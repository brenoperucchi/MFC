"""
Testes de scripts/backtest_engine_compare.py::walk_forward() — retomada do
item 6 do plano de reconciliação Miqueias (matriz 5-TF em shadow mode),
pedido explícito do Breno: "pode montar a automação do walk-forward".

walk_forward() nunca conecta MT5 sozinho — ele chama compare() N vezes, que
sim conecta. Os testes aqui cobrem o que É testável sem MT5: a validação de
janela (nunca deixar uma janela cruzar DEVELOPMENT_START_BRT, protegendo o
holdout OOS), a busca de entrada por marcador no journal
(_find_walk_forward_entry), e o fluxo de orquestração inteiro com compare()
mockado (nunca chamando MT5 de verdade).
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import scripts.backtest_engine_compare as engine_compare
from scripts.backtest_engine_compare import (
    BRT,
    DEVELOPMENT_START_BRT,
    _find_walk_forward_entry,
    walk_forward,
)


class TestWalkForwardValidation(unittest.TestCase):
    """Validação acontece ANTES de qualquer chamada a compare() — testável
    sem MT5, sem mockar nada além do relógio (end_brt explícito)."""

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

    def test_rejects_disjoint_windows_that_would_cross_development_start(self):
        """Achado do próprio desenho: hoje (poucos dias depois de
        DEVELOPMENT_START_BRT) não cabe nem uma SEGUNDA janela disjunta de
        45 dias — pedir n_windows=2 com step_days=window_days=45 deve
        recusar, nunca silenciosamente cruzar o limite do holdout OOS."""
        end_brt = datetime.fromisoformat(DEVELOPMENT_START_BRT).astimezone(BRT).replace(
            tzinfo=None
        )
        end_brt = end_brt.replace(year=end_brt.year, month=end_brt.month, day=end_brt.day)
        # 50 dias depois de DEVELOPMENT_START_BRT — o mesmo "hoje" usado na
        # sessão real (2026-09-04), só que fixado explicitamente pro teste
        # não depender do relógio de verdade.
        from datetime import timedelta
        end_brt = end_brt + timedelta(days=50)
        with self.assertRaises(ValueError) as ctx:
            walk_forward(n_windows=2, window_days=45, step_days=45,
                         end_brt=end_brt, log_note_prefix="teste")
        message = str(ctx.exception)
        self.assertIn("DEVELOPMENT_START_BRT", message)
        # a mensagem precisa dizer quantas janelas disjuntas CABEM de verdade
        self.assertIn("1", message)

    def test_accepts_a_single_disjoint_window_that_fits(self):
        """n_windows=1 nunca cruza DEVELOPMENT_START_BRT se window_days<=50
        (o que já decorreu) — a validação deve deixar passar (falha depois,
        em compare(), por falta de MT5 neste checkout — não na validação)."""
        end_brt = datetime.fromisoformat(DEVELOPMENT_START_BRT).astimezone(BRT).replace(tzinfo=None)
        from datetime import timedelta
        end_brt = end_brt + timedelta(days=50)
        with patch.object(engine_compare, "compare", return_value=1) as mocked_compare:
            result = walk_forward(n_windows=1, window_days=45, end_brt=end_brt,
                                  log_note_prefix="teste")
        # validação passou (chegou a chamar compare()); compare() mockado
        # devolve 1 (falha), então walk_forward propaga 1 também.
        mocked_compare.assert_called_once()
        self.assertEqual(result, 1)

    def test_rejects_overlapping_windows_that_still_cross_development_start(self):
        """step_days < window_days reduz quantos dias por janela são NOVOS,
        mas a janela MAIS ANTIGA ainda pode cruzar o limite se n_windows for
        grande demais pro tempo decorrido."""
        end_brt = datetime.fromisoformat(DEVELOPMENT_START_BRT).astimezone(BRT).replace(tzinfo=None)
        from datetime import timedelta
        end_brt = end_brt + timedelta(days=50)
        with self.assertRaises(ValueError):
            walk_forward(n_windows=10, window_days=45, step_days=5,
                         end_brt=end_brt, log_note_prefix="teste")


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
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._journal_path = os.path.join(self._tmpdir.name, "journal.json")
        with open(self._journal_path, "w", encoding="utf-8") as f:
            json.dump([], f)
        patch_path = patch.object(engine_compare, "RESULTS_LOG_PATH", self._journal_path)
        patch_path.start()
        self.addCleanup(patch_path.stop)
        self._end_brt = datetime.fromisoformat(DEVELOPMENT_START_BRT).astimezone(BRT).replace(tzinfo=None)
        from datetime import timedelta
        self._end_brt = self._end_brt + timedelta(days=50)

    def _append(self, note, liquido_by_engine, flip_rate_by_engine, window_end_brt):
        with open(self._journal_path, "r", encoding="utf-8") as f:
            log = json.load(f)
        log.append({
            "journal_seq": len(log) + 1,
            "note": note,
            "window": {"end_brt": window_end_brt},
            "engines": {name: {"liquido": v} for name, v in liquido_by_engine.items()},
            "turnover": {name: {"flip_rate": v} for name, v in flip_rate_by_engine.items()},
        })
        with open(self._journal_path, "w", encoding="utf-8") as f:
            json.dump(log, f)

    def test_single_window_writes_one_entry_and_returns_zero(self):
        def fake_compare(days, engine_names, runs, log_note, end_brt, sample_role):
            self._append(log_note, {"engine_a": -10.0}, {"engine_a": 0.3}, "2026-08-30")
            return 0

        with patch.object(engine_compare, "compare", side_effect=fake_compare):
            result = walk_forward(n_windows=1, window_days=45, end_brt=self._end_brt,
                                  engine_names=["engine_a"], log_note_prefix="teste")
        self.assertEqual(result, 0)

    def test_two_overlapping_windows_are_assembled_in_order(self):
        calls = []

        def fake_compare(days, engine_names, runs, log_note, end_brt, sample_role):
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
        def fake_compare(days, engine_names, runs, log_note, end_brt, sample_role):
            return 1  # simula MT5 fora do ar na primeira janela

        with patch.object(engine_compare, "compare", side_effect=fake_compare):
            result = walk_forward(n_windows=2, window_days=45, step_days=5,
                                  end_brt=self._end_brt, engine_names=["engine_a"],
                                  log_note_prefix="teste")
        self.assertEqual(result, 1)

    def test_aborts_when_compare_succeeds_but_entry_is_not_found(self):
        """compare() devolve 0 mas não grava nada rastreável (bug
        hipotético do lado de compare()) — walk_forward() não pode fingir
        que montou um resumo sem dado nenhum."""
        def fake_compare(days, engine_names, runs, log_note, end_brt, sample_role):
            return 0  # não grava nada no journal

        with patch.object(engine_compare, "compare", side_effect=fake_compare):
            result = walk_forward(n_windows=1, window_days=45, end_brt=self._end_brt,
                                  engine_names=["engine_a"], log_note_prefix="teste")
        self.assertEqual(result, 1)


if __name__ == "__main__":
    unittest.main()
