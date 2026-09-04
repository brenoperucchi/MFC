"""
Testes de scripts/backtest_engine_compare.py::walk_forward() — retomada do
item 6 do plano de reconciliação Miqueias (matriz 5-TF em shadow mode),
pedido explícito do Breno: "pode montar a automação do walk-forward".

walk_forward() nunca conecta MT5 sozinho — ele chama compare() N vezes, que
sim conecta. Os testes aqui cobrem o que É testável sem MT5: a validação de
janela (nunca deixar uma janela cruzar DEVELOPMENT_START_BRT, protegendo o
holdout OOS), a recusa na janela crítica de abertura/fechamento, a busca de
entrada por marcador no journal (_find_walk_forward_entry), e o fluxo de
orquestração inteiro com compare() mockado (nunca chamando MT5 de verdade).

in_critical_window() é mockado como False por padrão em toda classe que
chama walk_forward() — sem isso, a suíte ficaria dependente do horário real
em que roda (achado ao escrever os testes: rodar por acidente entre 20:55 e
22:00 BRT faria TUDO aqui falhar por um motivo sem relação nenhuma com o que
está sendo testado).
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import scripts.backtest_engine_compare as engine_compare
from scripts.backtest_engine_compare import (
    BRT,
    DEVELOPMENT_START_BRT,
    RESULTS_LOG_PATH,
    _find_walk_forward_entry,
    walk_forward,
)
from scripts.run_isolated_backtest import (
    REGRESSION_WINDOW_DAYS,
    REGRESSION_WINDOW_END_BRT,
    _assert_regression_window_after_holdout,
)


class TestDevelopmentStartBrtIsNotStale(unittest.TestCase):
    """Achado original (herdr-review mfc-72, mfc-rev P1 + mfc-rev-2 P2-1,
    convergindo no mesmo ponto): DEVELOPMENT_START_BRT era uma cópia
    duplicada de scripts/run_isolated_backtest.py::REGRESSION_WINDOW_END_BRT
    - REGRESSION_WINDOW_DAYS, sem nenhuma checagem em runtime. A verificação
    mfc-73 (mfc-rev) reclassificou a correção original (só um teste de
    igualdade) como PARCIAL — resolvido de vez pela consulta herdr-ask
    mfc-14 (2026-09-04): DEVELOPMENT_START_BRT agora mora só em
    scripts/_backtest_results_log.py (fonte única, módulo folha, já dono da
    semântica de holdout via validate_oos_window()), NUNCA derivada de
    REGRESSION_WINDOW_END_BRT/DAYS — mfc-rev-2 mostrou que uma derivação
    tornaria a checagem de igualdade abaixo tautológica, cega justamente à
    edição perigosa (aumentar REGRESSION_WINDOW_DAYS sem revisar o holdout).
    O guard real agora é a desigualdade em
    scripts.run_isolated_backtest::_assert_regression_window_after_holdout().

    Os outros testes deste arquivo constroem suas datas RELATIVAS à própria
    constante, então nenhum deles pegaria a constante tendo "deslizado" pra
    um valor errado (mfc-rev-2, achado medido) — os dois testes abaixo
    existem só pra fechar esse buraco, ancorados fora da própria constante."""

    def test_regression_window_guard_passes_for_the_real_constants(self):
        """Positivo: com os valores reais do projeto hoje, o guard não
        recusa nada — REGRESSION_WINDOW_END_BRT menos REGRESSION_WINDOW_DAYS
        cai exatamente em DEVELOPMENT_START_BRT (fronteira independente)."""
        _assert_regression_window_after_holdout()  # não deve levantar

    def test_regression_window_guard_rejects_a_window_that_would_cross_the_holdout(self):
        """Discriminância (achado MFC72-01, herdr-ask mfc-14, `mfc-rev-2`):
        se REGRESSION_WINDOW_DAYS crescer no futuro (ex.: 45→90, pra reduzir
        ruído) sem que ninguém revise a fronteira do holdout, a janela de
        regressão passaria a começar DENTRO do holdout. Uma checagem de
        igualdade contra um valor DERIVADO da mesma fórmula nunca pegaria
        isso (seria tautológica por construção); esta desigualdade, contra a
        fronteira declarada de forma independente, pega."""
        with patch("scripts.run_isolated_backtest.REGRESSION_WINDOW_DAYS", 90):
            with self.assertRaises(ValueError) as ctx:
                _assert_regression_window_after_holdout()
        self.assertIn("holdout", str(ctx.exception))

    def test_matches_the_development_start_brt_recorded_in_real_oos_journal_entries(self):
        """Ancorado na fonte de verdade de verdade (mfc-rev-2, "melhor
        ainda"): não as duas constantes concordando entre si (as duas
        podem ter deslizado juntas, pro mesmo valor errado) — o
        `development_start_brt` de verdade GRAVADO nas entradas
        `oos_disjoint` reais do journal, que é o que qualquer análise
        futura vai efetivamente ler pra saber o que era holdout."""
        try:
            with open(RESULTS_LOG_PATH, "r", encoding="utf-8") as f:
                log = json.load(f)
        except FileNotFoundError:
            self.skipTest(f"{RESULTS_LOG_PATH} não existe neste checkout")
        oos_values = {
            entry["window"]["development_start_brt"]
            for entry in log
            if isinstance(entry, dict)
            and entry.get("window", {}).get("sample_role") == "oos_disjoint"
            and entry.get("window", {}).get("development_start_brt")
        }
        if not oos_values:
            self.skipTest("nenhuma entrada oos_disjoint com development_start_brt no journal")
        self.assertEqual(
            oos_values, {DEVELOPMENT_START_BRT},
            f"DEVELOPMENT_START_BRT={DEVELOPMENT_START_BRT!r} não bate com o(s) "
            f"development_start_brt real(is) gravado(s) nas entradas oos_disjoint "
            f"do journal: {oos_values!r}",
        )


def _fixed_end_brt(days_after_development_start=50):
    """`DEVELOPMENT_START_BRT` + N dias, SEM depender do relógio real — o
    mesmo "hoje" usado na sessão real (2026-09-04, ~50 dias depois), fixado
    explicitamente pra nenhum teste depender de quando a suíte roda."""
    base = datetime.fromisoformat(DEVELOPMENT_START_BRT).astimezone(BRT).replace(tzinfo=None)
    return base + timedelta(days=days_after_development_start)


class TestWalkForwardValidation(unittest.TestCase):
    """Validação acontece ANTES de qualquer chamada a compare() — testável
    sem MT5, sem mockar nada além do relógio (end_brt explícito) e da
    janela crítica (sempre False aqui, não é o que estes testes cobrem)."""

    def setUp(self):
        patcher = patch.object(engine_compare, "in_critical_window", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_rejects_n_windows_below_one(self):
        with self.assertRaises(ValueError):
            walk_forward(n_windows=0, log_note_prefix="teste")

    def test_rejects_non_positive_window_days(self):
        with self.assertRaises(ValueError):
            walk_forward(window_days=0, log_note_prefix="teste")

    def test_rejects_non_positive_step_days(self):
        with self.assertRaises(ValueError):
            walk_forward(step_days=0, log_note_prefix="teste")

    def test_requires_log_note_prefix(self):
        with self.assertRaises(ValueError):
            walk_forward(log_note_prefix=None)
        with self.assertRaises(ValueError):
            walk_forward(log_note_prefix="")

    def test_refuses_to_start_inside_the_critical_window(self):
        """Achado P3-2 (herdr-review mfc-72, mfc-rev-2): N janelas em
        sequência multiplicam a duração de um comando de CLI que nunca teve
        watchdog nenhum — recusa no início em vez de arriscar atravessar a
        janela de abertura/fechamento de cesta."""
        with patch.object(engine_compare, "in_critical_window", return_value=True):
            with self.assertRaises(ValueError) as ctx:
                walk_forward(n_windows=1, end_brt=_fixed_end_brt(), log_note_prefix="teste")
        self.assertIn("janela crítica", str(ctx.exception))

    def test_rejects_disjoint_windows_that_would_cross_development_start(self):
        """Hoje (poucos dias depois de DEVELOPMENT_START_BRT) não cabe nem
        uma SEGUNDA janela disjunta de 45 dias — pedir n_windows=2 com
        step_days=window_days=45 deve recusar, nunca silenciosamente
        cruzar o limite do holdout OOS."""
        with self.assertRaises(ValueError) as ctx:
            walk_forward(n_windows=2, window_days=45, step_days=45,
                         end_brt=_fixed_end_brt(), log_note_prefix="teste")
        message = str(ctx.exception)
        self.assertIn("DEVELOPMENT_START_BRT", message)
        # a mensagem precisa dizer quantas janelas disjuntas CABEM de verdade
        self.assertIn("1", message)

    def test_accepts_a_single_disjoint_window_that_fits(self):
        """n_windows=1 nunca cruza DEVELOPMENT_START_BRT se window_days<=50
        (o que já decorreu) — a validação deve deixar passar (falha depois,
        em compare(), por falta de MT5 neste checkout — não na validação)."""
        with patch.object(engine_compare, "compare", return_value=1) as mocked_compare:
            result = walk_forward(n_windows=1, window_days=45, end_brt=_fixed_end_brt(),
                                  log_note_prefix="teste")
        # validação passou (chegou a chamar compare()); compare() mockado
        # devolve 1 (falha), então walk_forward propaga 1 também.
        mocked_compare.assert_called_once()
        self.assertEqual(result, 1)

    def test_rejects_overlapping_windows_that_still_cross_development_start(self):
        """step_days < window_days reduz quantos dias por janela são NOVOS,
        mas a janela MAIS ANTIGA ainda pode cruzar o limite se n_windows for
        grande demais pro tempo decorrido."""
        with self.assertRaises(ValueError):
            walk_forward(n_windows=10, window_days=45, step_days=5,
                         end_brt=_fixed_end_brt(), log_note_prefix="teste")


class TestFindWalkForwardEntry(unittest.TestCase):
    """Função pura — sem MT5, só leitura de um journal sintético."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._journal_path = os.path.join(self._tmpdir.name, "journal.json")
        patch_path = patch.object(engine_compare, "RESULTS_LOG_PATH", self._journal_path)
        patch_path.start()
        self.addCleanup(patch_path.stop)

    def _write_journal(self, entries):
        with open(self._journal_path, "w", encoding="utf-8") as f:
            json.dump(entries, f)

    def test_finds_the_entry_with_the_exact_marker(self):
        self._write_journal([
            {"journal_seq": 1, "note": "[walk-forward:abc123:1/2] teste"},
            {"journal_seq": 2, "note": "[walk-forward:abc123:2/2] teste"},
        ])
        entry = _find_walk_forward_entry("abc123", 2, 2)
        self.assertEqual(entry["journal_seq"], 2)

    def test_returns_none_when_marker_is_absent(self):
        self._write_journal([{"journal_seq": 1, "note": "algo sem relação"}])
        self.assertIsNone(_find_walk_forward_entry("abc123", 1, 1))

    def test_returns_none_when_journal_file_is_missing(self):
        self.assertIsNone(_find_walk_forward_entry("abc123", 1, 1))

    def test_does_not_confuse_similar_window_index_prefixes(self):
        """Achado testando a própria função: "[...:1/2]" não pode casar
        acidentalmente com uma nota que começa "[...:1/20]" — o "]" no fim
        do marcador é o que evita isso (startswith exige o caractere exato
        logo depois do índice, não só o prefixo numérico)."""
        self._write_journal([
            {"journal_seq": 1, "note": "[walk-forward:abc123:1/20] nota errada"},
        ])
        self.assertIsNone(_find_walk_forward_entry("abc123", 1, 2))

    def test_ignores_entries_from_a_different_batch_id(self):
        self._write_journal([
            {"journal_seq": 1, "note": "[walk-forward:outro-lote:1/2] teste"},
        ])
        self.assertIsNone(_find_walk_forward_entry("abc123", 1, 2))


class TestWalkForwardOrchestration(unittest.TestCase):
    """Fluxo completo com compare() mockado — nunca chama MT5 de verdade.
    compare() aqui só grava a entrada no journal sintético (mesmo efeito que
    a versão real teria, sem a parte cara)."""

    def setUp(self):
        patcher = patch.object(engine_compare, "in_critical_window", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._journal_path = os.path.join(self._tmpdir.name, "journal.json")
        with open(self._journal_path, "w", encoding="utf-8") as f:
            json.dump([], f)
        patch_path = patch.object(engine_compare, "RESULTS_LOG_PATH", self._journal_path)
        patch_path.start()
        self.addCleanup(patch_path.stop)
        self._end_brt = _fixed_end_brt()

    def _append(self, note, liquido_by_engine, flip_rate_by_engine, window_end_brt):
        with open(self._journal_path, "r", encoding="utf-8") as f:
            log = json.load(f)
        log.append({
            "journal_seq": len(log) + 1,
            "note": note,
            "window": {"end_brt": window_end_brt},
            "engines": {name: {"liquido": v} for name, v in liquido_by_engine.items()},
            "turnover": {name: {"flip_rate": v} for name, v in flip_rate_by_engine.items()},
            "runs_summary": {
                "aggregate": {
                    "by_engine": {
                        name: {"liquido": {"mean": v, "min": v, "max": v}}
                        for name, v in liquido_by_engine.items()
                    }
                }
            },
        })
        with open(self._journal_path, "w", encoding="utf-8") as f:
            json.dump(log, f)

    def test_single_window_writes_one_entry_and_returns_zero(self):
        def fake_compare(days, engine_names, runs, log_note, end_brt, sample_role,
                          development_start_brt=None):
            self._append(log_note, {"engine_a": -10.0}, {"engine_a": 0.3}, "2026-08-30")
            return 0

        with patch.object(engine_compare, "compare", side_effect=fake_compare):
            result = walk_forward(n_windows=1, window_days=45, end_brt=self._end_brt,
                                  engine_names=["engine_a"], log_note_prefix="teste")
        self.assertEqual(result, 0)

    def test_passes_development_start_brt_through_to_compare(self):
        """Achado MFC72-01 (herdr-review mfc-72, mfc-rev): registrar a
        fronteira efetivamente usada em cada entrada — antes,
        window.development_start_brt ficava ausente nas entradas do
        walk-forward, mesmo a função respeitando o limite."""
        received = {}

        def fake_compare(days, engine_names, runs, log_note, end_brt, sample_role,
                          development_start_brt=None):
            received["development_start_brt"] = development_start_brt
            self._append(log_note, {"engine_a": -10.0}, {"engine_a": 0.3}, "2026-08-30")
            return 0

        with patch.object(engine_compare, "compare", side_effect=fake_compare):
            walk_forward(n_windows=1, window_days=45, end_brt=self._end_brt,
                        engine_names=["engine_a"], log_note_prefix="teste")
        self.assertEqual(
            received["development_start_brt"],
            datetime.fromisoformat(DEVELOPMENT_START_BRT).astimezone(BRT).replace(tzinfo=None),
        )

    def test_two_overlapping_windows_are_assembled_in_order(self):
        calls = []

        def fake_compare(days, engine_names, runs, log_note, end_brt, sample_role,
                          development_start_brt=None):
            calls.append(end_brt)
            liquido = -10.0 if len(calls) == 1 else -5.0
            self._append(log_note, {"engine_a": liquido}, {"engine_a": 0.3}, str(end_brt))
            return 0

        with patch.object(engine_compare, "compare", side_effect=fake_compare):
            result = walk_forward(n_windows=2, window_days=45, step_days=5,
                                  end_brt=self._end_brt, engine_names=["engine_a"],
                                  log_note_prefix="teste")
        self.assertEqual(result, 0)
        # a janela mais antiga (índice 0) foi chamada primeiro, a mais
        # recente (== end_brt pedido) por último
        self.assertEqual(calls[-1], self._end_brt)
        self.assertLess(calls[0], calls[-1])

    def test_aborts_without_crashing_when_a_window_fails(self):
        def fake_compare(days, engine_names, runs, log_note, end_brt, sample_role,
                          development_start_brt=None):
            return 1  # simula MT5 fora do ar na primeira janela

        with patch.object(engine_compare, "compare", side_effect=fake_compare):
            result = walk_forward(n_windows=2, window_days=45, step_days=5,
                                  end_brt=self._end_brt, engine_names=["engine_a"],
                                  log_note_prefix="teste")
        self.assertEqual(result, 1)

    def test_aborts_without_crashing_when_compare_raises(self):
        """Achado MFC72-02 (herdr-review mfc-72, mfc-rev): uma exceção de
        compare() (MT5, reconstrução, append_result()) precisa virar o
        retorno 1 documentado, não escapar como traceback cru."""
        def fake_compare(days, engine_names, runs, log_note, end_brt, sample_role,
                          development_start_brt=None):
            raise RuntimeError("MT5 caiu no meio da reconstrução")

        with patch.object(engine_compare, "compare", side_effect=fake_compare):
            result = walk_forward(n_windows=1, window_days=45, end_brt=self._end_brt,
                                  engine_names=["engine_a"], log_note_prefix="teste")
        self.assertEqual(result, 1)

    def test_does_not_swallow_keyboard_interrupt(self):
        def fake_compare(days, engine_names, runs, log_note, end_brt, sample_role,
                          development_start_brt=None):
            raise KeyboardInterrupt()

        with patch.object(engine_compare, "compare", side_effect=fake_compare):
            with self.assertRaises(KeyboardInterrupt):
                walk_forward(n_windows=1, window_days=45, end_brt=self._end_brt,
                            engine_names=["engine_a"], log_note_prefix="teste")

    def test_aborts_when_compare_succeeds_but_entry_is_not_found(self):
        """compare() devolve 0 mas não grava nada rastreável (bug
        hipotético do lado de compare()) — walk_forward() não pode fingir
        que montou um resumo sem dado nenhum."""
        def fake_compare(days, engine_names, runs, log_note, end_brt, sample_role,
                          development_start_brt=None):
            return 0  # não grava nada no journal

        with patch.object(engine_compare, "compare", side_effect=fake_compare):
            result = walk_forward(n_windows=1, window_days=45, end_brt=self._end_brt,
                                  engine_names=["engine_a"], log_note_prefix="teste")
        self.assertEqual(result, 1)

    def test_summary_ranks_by_mean_liquido_across_runs_not_a_single_pass(self):
        """Achado MFC72-03 (herdr-review mfc-72, `mfc-rev`) + achado sobre
        o PRÓPRIO teste (MFC73-02, herdr-review mfc-73, `mfc-rev`, verify
        mode: a primeira versão deste teste mockava
        _print_walk_forward_summary e só chamava o helper _mean_liquido()
        direto nas asserções — se o RESUMO REAL voltasse a usar
        entry["engines"][name]["liquido"] (a última passada, não a média),
        este teste continuaria verde, porque o código do resumo nem
        chegava a rodar. Corrigido: chama walk_forward() de ponta a ponta
        SEM mockar _print_walk_forward_summary, captura a saída impressa de
        verdade, e confirma que o motor apontado como vencedor é o que
        vence pela MÉDIA (engine_a), não pelo líquido cru da última
        passada (que apontaria engine_b)."""
        import contextlib
        import io

        def fake_compare(days, engine_names, runs, log_note, end_brt, sample_role,
                          development_start_brt=None):
            with open(self._journal_path, "r", encoding="utf-8") as f:
                log = json.load(f)
            log.append({
                "journal_seq": 1,
                "note": log_note,
                "window": {"end_brt": "2026-08-30"},
                # "líquido" cru (última passada) diz que engine_b venceu —
                "engines": {"engine_a": {"liquido": -5.0}, "engine_b": {"liquido": 10.0}},
                "turnover": {"engine_a": {"flip_rate": 0.3}, "engine_b": {"flip_rate": 0.3}},
                # mas a MÉDIA entre as passadas diz que engine_a venceu —
                "runs_summary": {"aggregate": {"by_engine": {
                    "engine_a": {"liquido": {"mean": 20.0, "min": -5.0, "max": 45.0}},
                    "engine_b": {"liquido": {"mean": -8.0, "min": -20.0, "max": 10.0}},
                }}},
            })
            with open(self._journal_path, "w", encoding="utf-8") as f:
                json.dump(log, f)
            return 0

        captured = io.StringIO()
        with patch.object(engine_compare, "compare", side_effect=fake_compare):
            with contextlib.redirect_stdout(captured):
                result = walk_forward(n_windows=1, window_days=45, end_brt=self._end_brt,
                                      engine_names=["engine_a", "engine_b"],
                                      log_note_prefix="teste")
        self.assertEqual(result, 0)
        output = captured.getvalue()
        winner_line = next(
            line for line in output.splitlines() if line.strip().startswith("janela 1:")
        )
        self.assertIn("engine_a", winner_line)
        self.assertNotIn("engine_b", winner_line)


if __name__ == "__main__":
    unittest.main()
