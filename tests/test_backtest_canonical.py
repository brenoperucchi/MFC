"""
Testa scripts/backtest_canonical.py — que nunca teve suíte própria (achado
em revisão: Codex + mfc-rev-2, achado 4 rodada 3, confirmado pelos dois
independentemente).

O laço principal (run()) depende de MT5, séries em disco e o motor de
confluência inteiro — não vale a pena mocká-los só pra testar a lógica de
aviso. Por isso as duas peças que importam foram extraídas pra funções
puras, testáveis sem nenhum desses mocks:

  - _tally_cost_quality: LÊ o CostModel e incrementa os contadores — é onde
    o bug da rodada 3 morava de verdade (o laço não lia
    last_basket_swap_unmodeled).
  - _cost_quality_summary_lines: FORMATA os contadores em mensagens.

Achado em revisão (Codex + mfc-rev-2, achado 4 rodada 4, confirmado pelos
dois): a rodada 3 só testou a segunda função. O Claude reproduziu apagando
a leitura de last_basket_swap_unmodeled do laço original — a suíte
continuava toda verde, porque nenhum teste tocava a LIGAÇÃO. Isto testa as
duas separadamente.
"""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

import scripts.backtest_canonical as bc
import scripts.fetch_histdata_mn1_warmup as fetch_histdata_mn1_warmup


def _h1_times_covering_one_night(days=1):
    """Série H1 horária que cobre com folga a entrada E a saída que
    `bc.run(days=days)` vai calcular sozinho — usada pelos smoke tests de
    `run()` que mockam `evaluate_at` mas NÃO mockam `is_market_session_valid`
    (ela roda de verdade dentro de `run()`, ver MFC21-02).

    Achado (herdr-review rodada 22, mfc-rev-2, P2-1): a versão anterior
    construía `h1_times` só com `pd.date_range(end=pd.Timestamp.now(),
    periods=48, freq='h')` — terminando EXATAMENTE agora. Como `exit_srv`
    pode cair depois de "agora" dependendo da hora do dia em que a suíte
    roda (a conversão BRT->servidor soma horas), a checagem de sessão válida
    via `_market_gap_hours` via de vez em quando não achava nenhuma barra
    perto o suficiente de `exit_srv` e `run()` descartava a única noite da
    fixture — os dois testes que dependem disso ficavam vermelhos
    especificamente conforme a hora do relógio, não o dia da semana. Aqui a
    série é ancorada nos mesmos cálculos que `run()` faz internamente
    (`_brt_to_server`, `ENTRY_HOUR_BRT`), com folga de warmup antes da
    entrada e depois da saída — determinístico, não depende de que horas são
    agora."""
    now = datetime.now()
    brt_day = (now - timedelta(days=days)).replace(
        hour=bc.ENTRY_HOUR_BRT, minute=0, second=0, microsecond=0)
    srv_dt = bc._brt_to_server(brt_day)
    exit_srv = srv_dt + timedelta(hours=11)
    start = srv_dt - timedelta(hours=40)
    end = exit_srv + timedelta(hours=2)
    return pd.Series(pd.date_range(start=start, end=end, freq="h"))


class TestTallyCostQuality(unittest.TestCase):
    """A REGRESSÃO CENTRAL do achado 4 rodada 4: onde o bug da rodada 3
    realmente morava — não na formatação da mensagem, na LEITURA do
    CostModel. Um fake com os dois atributos, sem precisar de MT5/séries/
    confluência nenhuma."""

    @staticmethod
    def _fake_costs(degraded, swap_unmodeled):
        return SimpleNamespace(last_basket_degraded=degraded,
                               last_basket_swap_unmodeled=swap_unmodeled)

    def test_increments_degraded_when_present(self):
        d, s = bc._tally_cost_quality(self._fake_costs({"CADJPY"}, set()), 0, 0)
        self.assertEqual((d, s), (1, 0))
        print("[✓] Cesta com dado perdido incrementa degraded_baskets")

    def test_increments_swap_unmodeled_even_when_degraded_is_empty(self):
        """Este é EXATAMENTE o caso que a rodada 3 deixava mudo: degraded
        vazio, swap_unmodeled preenchido. Se alguém remover a leitura de
        last_basket_swap_unmodeled (o bug real da rodada 3), este teste
        falha — ao contrário dos testes de formatação, que continuavam
        verdes com esse bug presente."""
        d, s = bc._tally_cost_quality(self._fake_costs(set(), {"CADJPY"}), 0, 0)
        self.assertEqual((d, s), (0, 1),
                         "swap_unmodeled não incrementou — é o bug exato da rodada 3")
        print("[✓] Cesta com swap não modelado (degraded vazio) incrementa "
              "swap_unmodeled_baskets — o bug da rodada 3 não volta")

    def test_does_not_increment_on_clean_basket(self):
        d, s = bc._tally_cost_quality(self._fake_costs(set(), set()), 0, 0)
        self.assertEqual((d, s), (0, 0))
        print("[✓] Cesta limpa não incrementa nenhum contador")

    def test_accumulates_across_multiple_calls(self):
        """Simula o laço real: várias cestas, contadores acumulando."""
        d, s = 0, 0
        for degraded, unmodeled in [(set(), set()), ({"CADJPY"}, set()),
                                    (set(), {"EURCAD"}), ({"USDCAD"}, {"GBPCAD"})]:
            d, s = bc._tally_cost_quality(self._fake_costs(degraded, unmodeled), d, s)
        self.assertEqual((d, s), (2, 2))
        print("[✓] Contadores acumulam corretamente ao longo de várias cestas")


class TestCostQualitySummaryLines(unittest.TestCase):
    """A REGRESSÃO CENTRAL do achado 4 rodada 3: a rodada anterior ligou
    last_basket_swap_unmodeled no logger ao vivo (measure_and_log_basket_cost)
    mas esqueceu o backtest — que nunca leu essa bandeira. Numa corretora com
    swap fora de PONTOS, last_basket_degraded ficava vazio, o único aviso
    que existia nunca disparava, e ~45% do custo real desaparecia do PnL
    LÍQUIDO em silêncio (medido por mfc-rev-2)."""

    def test_no_warning_when_nothing_degraded_or_unmodeled(self):
        lines = bc._cost_quality_summary_lines(baskets=10, degraded_baskets=0,
                                                swap_unmodeled_baskets=0)
        self.assertEqual(lines, [])
        print("[✓] Caminho feliz não gera aviso nenhum")

    def test_warns_about_degraded_data(self):
        lines = bc._cost_quality_summary_lines(baskets=10, degraded_baskets=3,
                                                swap_unmodeled_baskets=0)
        self.assertEqual(len(lines), 1)
        self.assertIn("3/10", lines[0])
        self.assertIn("OTIMISTA", lines[0])
        print("[✓] Dado perdido gera o aviso de PnL otimista")

    def test_warns_about_swap_unmodeled_even_with_zero_degraded(self):
        """Este é o caso exato que a rodada anterior deixava mudo: nenhuma
        perna com dado perdido (degraded=0), mas todas as cestas com swap
        fora de pontos. Sem esta correção, nenhuma linha seria devolvida —
        e o operador nunca saberia que ~45% do custo de swap ficou de fora."""
        lines = bc._cost_quality_summary_lines(baskets=10, degraded_baskets=0,
                                                swap_unmodeled_baskets=10)
        self.assertEqual(len(lines), 1,
                         "swap_unmodeled sozinho tem que gerar aviso, mesmo com degraded=0")
        self.assertIn("10/10", lines[0])
        self.assertNotIn("OTIMISTA", lines[0],
                         "swap não modelado não é a mesma classe de problema que dado "
                         "perdido — não pode usar o mesmo tom de alarme")
        print("[✓] swap_unmodeled sozinho (degraded=0) ainda gera aviso — o bug da rodada "
              "anterior não volta")

    def test_both_categories_produce_two_separate_lines(self):
        """As duas categorias não podem se fundir numa mensagem só — cada
        uma tem uma causa e uma gravidade diferente."""
        lines = bc._cost_quality_summary_lines(baskets=10, degraded_baskets=2,
                                                swap_unmodeled_baskets=5)
        self.assertEqual(len(lines), 2)
        degraded_line = next(l for l in lines if "OTIMISTA" in l)
        swap_line = next(l for l in lines if "OTIMISTA" not in l)
        self.assertIn("2/10", degraded_line)
        self.assertIn("5/10", swap_line)
        print("[✓] Dado perdido e swap não modelado geram duas linhas separadas, não uma só")


class TestRunWiresCostQualityIntoSummary(unittest.TestCase):
    """Achado em revisão (Codex P3 + mfc-rev-2 P2-3, rodada 5, confirmado
    pelos dois independentemente): as classes acima cobrem
    _tally_cost_quality e _cost_quality_summary_lines ISOLADAS, mas nada
    provava que run() de fato chama as duas — a rodada 3 já teve esse
    bug exato (apagar as duas linhas do laço deixava a suíte inteira
    verde) e a rodada 4 provou que o mesmo bug ainda é reintroduzível
    apagando qualquer uma das DUAS chamadas (a de dentro do laço ou a do
    resumo final), não só as duas linhas originais. Fecha a fronteira com
    um smoke test de run() — uma noite, uma cesta — injetando as seis
    dependências externas (ensure_mt5, load_series, load_h1_prices,
    evaluate_at, convert_pnl_to_usd, CostModel), nenhuma delas MT5 real."""

    class _FakeCostModel:
        """Substitui CostModel inteiro: .basket() devolve um custo fixo e
        marca a cesta como tendo uma perna degradada — o suficiente pra
        provar que run() LÊ last_basket_degraded via _tally_cost_quality
        e IMPRIME o aviso via _cost_quality_summary_lines, não só que a
        chamada não quebra."""

        def __init__(self, lot):
            self.lot = lot
            self.last_basket_degraded = set()
            self.last_basket_swap_unmodeled = set()
            self.last_basket_spread = 0.0
            self.last_basket_swap = 0.0

        def basket(self, ccy, bias, leg_lots=None):
            self.last_basket_degraded = {"USDCAD"}
            self.last_basket_swap_unmodeled = set()
            self.last_basket_spread = 2.0
            self.last_basket_swap = 0.5
            return 2.5

    def _run_one_night_one_basket(self):
        h1_times = _h1_times_covering_one_night(days=1)
        fake_series = {tf: {"times": h1_times, "scores": {}} for tf in bc.TFS}

        class _FakePriceSeries:
            def asof(self, _dt):
                return 1.1000

        fake_prices = {pair: _FakePriceSeries() for pair in bc.ALL_28_PAIRS}

        def fake_evaluate_at(series, entry_server_dt, ref_dt):
            return {"USD": {"trade_bias": "COMPRA"}}

        buf = io.StringIO()
        with patch.object(bc, "ensure_mt5", return_value=True), \
             patch.object(bc, "load_series", return_value=fake_series), \
             patch.object(bc, "load_h1_prices", return_value=fake_prices), \
             patch.object(bc, "evaluate_at", side_effect=fake_evaluate_at), \
             patch.object(bc, "convert_pnl_to_usd", return_value=(10.0, 5.0)), \
             patch.object(bc, "CostModel", self._FakeCostModel), \
             redirect_stdout(buf):
            bc.run(days=1)
        return buf.getvalue()

    def test_run_prints_degraded_warning_from_real_cost_model_state(self):
        output = self._run_one_night_one_basket()
        self.assertIn("cesta(s) tiveram ao menos uma perna sem símbolo/", output,
                       "run() não imprimiu o aviso de custo degradado — a ligação entre "
                       "o laço, _tally_cost_quality e _cost_quality_summary_lines quebrou")
        self.assertIn("OTIMISTA", output)
        print("[✓] run() lê last_basket_degraded do CostModel de cada cesta e imprime "
              "o aviso no resumo final — a fronteira que só a extração não cobria")

    def test_run_prints_swap_unmodeled_note_from_real_cost_model_state(self):
        """Achado em revisão (Codex, rodada 6, F-07): o smoke test acima só
        exercitava last_basket_degraded — o fake sempre fixava
        last_basket_swap_unmodeled=set(), e a asserção só procurava o aviso
        de degradação. Apagar a leitura/contagem da bandeira de SWAP no
        laço de run() (o bug histórico original do achado 4, rodada 2→3)
        ainda deixava esse smoke verde. Fecha com a variante inversa:
        degraded vazio, swap_unmodeled preenchido."""

        class _SwapUnmodeledFakeCostModel(self._FakeCostModel):
            def basket(self, ccy, bias, leg_lots=None):
                self.last_basket_degraded = set()
                self.last_basket_swap_unmodeled = {"USDCAD"}
                return 2.5

        h1_times = _h1_times_covering_one_night(days=1)
        fake_series = {tf: {"times": h1_times, "scores": {}} for tf in bc.TFS}

        class _FakePriceSeries:
            def asof(self, _dt):
                return 1.1000

        fake_prices = {pair: _FakePriceSeries() for pair in bc.ALL_28_PAIRS}

        buf = io.StringIO()
        with patch.object(bc, "ensure_mt5", return_value=True), \
             patch.object(bc, "load_series", return_value=fake_series), \
             patch.object(bc, "load_h1_prices", return_value=fake_prices), \
             patch.object(bc, "evaluate_at", return_value={"USD": {"trade_bias": "COMPRA"}}), \
             patch.object(bc, "convert_pnl_to_usd", return_value=(10.0, 5.0)), \
             patch.object(bc, "CostModel", _SwapUnmodeledFakeCostModel), \
             redirect_stdout(buf):
            bc.run(days=1)
        output = buf.getvalue()
        self.assertNotIn("cesta(s) tiveram ao menos uma perna sem símbolo/", output,
                         "swap_unmodeled sozinho não pode disparar o aviso de dado perdido")
        self.assertIn("swap_mode fora de PONTOS", output,
                       "run() não imprimiu a nota de swap não modelado — a ligação entre "
                       "o laço e last_basket_swap_unmodeled quebrou")
        print("[✓] run() lê last_basket_swap_unmodeled do CostModel e imprime a nota "
              "correspondente, independente da bandeira de degradação")

    def test_run_does_not_warn_when_no_basket_is_degraded(self):
        """Controle negativo: sem degradação, o aviso não pode aparecer —
        prova que o teste acima não passa por outro motivo qualquer."""

        class _CleanFakeCostModel(self._FakeCostModel):
            def basket(self, ccy, bias, leg_lots=None):
                self.last_basket_degraded = set()
                self.last_basket_swap_unmodeled = set()
                return 2.5

        h1_times = _h1_times_covering_one_night(days=1)
        fake_series = {tf: {"times": h1_times, "scores": {}} for tf in bc.TFS}

        class _FakePriceSeries:
            def asof(self, _dt):
                return 1.1000

        fake_prices = {pair: _FakePriceSeries() for pair in bc.ALL_28_PAIRS}

        buf = io.StringIO()
        with patch.object(bc, "ensure_mt5", return_value=True), \
             patch.object(bc, "load_series", return_value=fake_series), \
             patch.object(bc, "load_h1_prices", return_value=fake_prices), \
             patch.object(bc, "evaluate_at", return_value={"USD": {"trade_bias": "COMPRA"}}), \
             patch.object(bc, "convert_pnl_to_usd", return_value=(10.0, 5.0)), \
             patch.object(bc, "CostModel", _CleanFakeCostModel), \
             redirect_stdout(buf):
            bc.run(days=1)
        output = buf.getvalue()
        self.assertNotIn("cesta(s) tiveram ao menos uma perna sem símbolo/", output)
        print("[✓] sem cesta degradada, o aviso não aparece — controle negativo do teste acima")

    def test_run_wires_days_into_the_h1_bars_requested(self):
        """Achado do probe manual de 2026-09-01 (usuário + exec, contra a
        instância mfc-backtest): load_h1_prices() sempre pediu 1800 barras
        fixas, mesmo quando `days` pede uma janela maior — só parametrizar
        h1_bars_for_days() não bastava, precisava provar que run() de fato
        REPASSA `days` pra ela (mesmo padrão 'wiring não testado' já visto
        neste arquivo pra _tally_cost_quality)."""

        class _CleanFakeCostModel(self._FakeCostModel):
            def basket(self, ccy, bias, leg_lots=None):
                self.last_basket_degraded = set()
                self.last_basket_swap_unmodeled = set()
                return 2.5

        h1_times = _h1_times_covering_one_night(days=1)
        fake_series = {tf: {"times": h1_times, "scores": {}} for tf in bc.TFS}

        class _FakePriceSeries:
            def asof(self, _dt):
                return 1.1000

        fake_prices = {pair: _FakePriceSeries() for pair in bc.ALL_28_PAIRS}
        h1_mock = MagicMock(return_value=fake_prices)
        series_mock = MagicMock(return_value=fake_series)

        with patch.object(bc, "ensure_mt5", return_value=True), \
             patch.object(bc, "load_series", series_mock), \
             patch.object(bc, "load_h1_prices", h1_mock), \
             patch.object(bc, "evaluate_at", return_value={"USD": {"trade_bias": "COMPRA"}}), \
             patch.object(bc, "convert_pnl_to_usd", return_value=(10.0, 5.0)), \
             patch.object(bc, "CostModel", _CleanFakeCostModel):
            bc.run(days=1000)
        h1_mock.assert_called_once_with(count=bc.h1_bars_for_days(1000))
        series_call_kwargs = series_mock.call_args.kwargs
        self.assertIn("window_start_brt", series_call_kwargs,
                      "run() não repassou window_start_brt pra load_series() -- volta a "
                      "travar em TF_COUNTS fixo (~67 dias pro H1) pra qualquer `days` grande")
        print("[✓] run() repassa days pro count de load_h1_prices() E window_start_brt pra "
              "load_series(), não fica preso em TF_COUNTS/1800 fixos")


class TestBarsNeededSince(unittest.TestCase):
    """bars_needed_since()/tf_counts_for_window(): achado do probe manual de
    2026-09-01 (usuário + exec, contra a instância mfc-backtest) — a
    primeira versão deste helper (h1_bars_for_days, só `days`) sub-pedia
    barra quando `end_brt` já estava no passado: copy_rates_from_pos()
    sempre conta pra trás a partir de AGORA, não do fim da janela pedida,
    então uma janela de span pequeno mas terminando há 90 dias já ficava
    fora do alcance do TF_COUNTS fixo (~67 dias pro H1). O que importa é a
    distância de AGORA até o INÍCIO da janela (`window_start_brt`), não o
    span sozinho — replicado com execução real na instância mfc-backtest
    depois desta correção."""

    def test_window_start_none_returns_the_floor(self):
        self.assertEqual(bc.bars_needed_since(None, 24.0, 1800), 1800)

    def test_scales_with_distance_from_now_to_window_start(self):
        start = datetime.now(bc.BRT) - timedelta(days=1000)
        self.assertEqual(
            bc.bars_needed_since(start, 24.0, 1800), 1000 * 24 + 40,
        )

    def test_never_returns_below_the_floor_for_a_recent_window_start(self):
        start = datetime.now(bc.BRT) - timedelta(days=1)
        self.assertEqual(bc.bars_needed_since(start, 24.0, 1800), 1800)

    def test_margin_clears_the_i_less_than_30_rejection_at_the_first_candidate(self):
        """Achado medido numa rodada real (usuário + exec, 2026-09-01, janela
        estendida OOS): evaluate_at_all() recusa qualquer noite com índice
        de barra fechada i<30 (backtest_engine_compare.py:689), por TF,
        independente de quanto dado exista antes. Uma margem de exatamente
        30 períodos deixa a primeira noite candidata bem na borda (i≈30) --
        arredondamento de calendário empurrou ~10 noites iniciais pra
        i<30 mesmo com o resto da série saudável. A margem (60) precisa
        cobrir isso com folga real, não só o mínimo teórico."""
        self.assertGreater(
            bc.bars_needed_since(datetime.now(bc.BRT) - timedelta(days=1), 1.0, 0) - 0,
            30,
            "margem <= 30 deixa a primeira noite candidata na borda de i<30",
        )

    def test_naive_window_start_is_interpreted_as_brt_not_utc(self):
        """Achado explícito na correção: `_normalize_window_end()`
        (backtest_engine_compare.py) devolve datetime INGÊNUO interpretado
        como BRT, não UTC — misturar os dois introduziria um erro de fuso
        de 3h no cálculo (pequeno o bastante pra passar despercebido nos
        testes de `days` inteiros, mas errado)."""
        naive_start = (datetime.now(bc.BRT) - timedelta(days=1000)).replace(tzinfo=None)
        aware_start = datetime.now(bc.BRT) - timedelta(days=1000)
        self.assertEqual(
            bc.bars_needed_since(naive_start, 24.0, 1800),
            bc.bars_needed_since(aware_start, 24.0, 1800),
        )

    def test_tf_counts_for_window_scales_every_timeframe(self):
        start = datetime.now(bc.BRT) - timedelta(days=3000)
        counts = bc.tf_counts_for_window(start)
        self.assertEqual(set(counts), set(bc.TFS))
        for tf in bc.TFS:
            self.assertGreater(counts[tf], bc.TF_COUNTS[tf],
                               f"{tf} não escalou pra uma janela de 3000 dias")

    def test_tf_counts_for_window_none_preserves_the_historical_default(self):
        self.assertEqual(bc.tf_counts_for_window(None), bc.TF_COUNTS)

    def test_h1_bars_for_days_preserves_the_historical_default_for_45_days(self):
        self.assertEqual(bc.h1_bars_for_days(45), 1800)


def _month_start_ts(year, month):
    return int(datetime(year, month, 1, tzinfo=timezone.utc).timestamp())


def _monthly_rates(n_months, start_year, start_month, close=1.0):
    """Barras MN1 sintéticas, uma por mês, começando em start_year/start_month."""
    dtype = [("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"), ("close", "f8")]
    rows = np.zeros(n_months, dtype=dtype)
    year, month = start_year, start_month
    for i in range(n_months):
        rows[i] = (_month_start_ts(year, month), close, close + 0.01, close - 0.01, close)
        month += 1
        if month > 12:
            month = 1
            year += 1
    return rows


def _sequential_months(start_year, start_month, count):
    year, month = start_year, start_month
    keys = []
    for _ in range(count):
        keys.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            month = 1
            year += 1
    return keys


class TestHistdataMn1WarmupCache(unittest.TestCase):
    """_load_histdata_warmup_months: leitura pura do cache gerado por
    scripts/fetch_histdata_mn1_warmup.py, sem MT5."""

    def test_returns_empty_list_without_cache_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(bc, "HISTDATA_WARMUP_DIR", tmp):
                rows, gaps = bc._load_histdata_warmup_months("gbpjpy")
        self.assertEqual(rows, [])
        self.assertEqual(gaps, [])

    def test_parses_and_sorts_chronologically(self):
        months = {
            "2013-05": {"open": 1.10, "high": 1.20, "low": 1.00, "close": 1.15, "n": 10},
            "2012-01": {"open": 2.00, "high": 2.10, "low": 1.90, "close": 2.05, "n": 5},
        }
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "eurgbp.json"), "w", encoding="utf-8") as f:
                json.dump(months, f)
            with patch.object(bc, "HISTDATA_WARMUP_DIR", tmp):
                rows, gaps = bc._load_histdata_warmup_months("EURGBP")  # maiúsculas -> minúsculas
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], datetime(2012, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(rows[1][0], datetime(2013, 5, 1, tzinfo=timezone.utc))
        self.assertEqual(rows[0][4], 2.05)  # close de 2012-01
        # 2012-02..2013-04 ausentes entre os dois meses do fixture (15 buracos)
        self.assertEqual(len(gaps), 15)
        self.assertEqual(gaps[0], "2012-02")
        self.assertEqual(gaps[-1], "2013-04")

    def test_returns_no_gaps_for_a_contiguous_cache(self):
        months = {f"2012-{m:02d}": {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0} for m in range(1, 13)}
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "eurgbp.json"), "w", encoding="utf-8") as f:
                json.dump(months, f)
            with patch.object(bc, "HISTDATA_WARMUP_DIR", tmp):
                rows, gaps = bc._load_histdata_warmup_months("eurgbp")
        self.assertEqual(len(rows), 12)
        self.assertEqual(gaps, [])


class TestFindGaps(unittest.TestCase):
    """fetch_histdata_mn1_warmup.find_gaps: achado herdr-review mfc-61
    (P2-1) — não existia nenhuma checagem de contiguidade em lugar nenhum do
    pipeline; a "correção" do gap real do AUDJPY (2010-2021) só adicionou
    meses na PONTA, sem fechar o buraco no meio, e nada detectou."""

    def test_empty_dict_has_no_gaps(self):
        self.assertEqual(fetch_histdata_mn1_warmup.find_gaps({}), [])

    def test_contiguous_range_has_no_gaps(self):
        months = {f"2020-{m:02d}": {} for m in range(1, 13)}
        self.assertEqual(fetch_histdata_mn1_warmup.find_gaps(months), [])

    def test_detects_gap_in_the_middle(self):
        months = {"2020-01": {}, "2020-02": {}, "2020-05": {}, "2020-06": {}}
        self.assertEqual(
            fetch_histdata_mn1_warmup.find_gaps(months), ["2020-03", "2020-04"],
        )

    def test_detects_the_shape_of_the_original_audjpy_gap(self):
        """Regressão do formato do achado original (achado herdr-review mfc-62,
        MFC62-01/`mfc-rev`: a versão anterior deste teste afirmava testar 'o
        cache versionado', mas montava meses sintéticos — não lia
        data/histdata_mn1_warmup/audjpy.json, então uma reversão do arquivo
        real não seria pega aqui). Isto continua sendo só um teste de
        find_gaps() em isolamento, com o formato do buraco original (11 meses
        de 2012 ausentes, confirmado rebaixando o zip de 2012 -- só tinha
        outubro). A proteção contra REGRESSÃO do arquivo real está em
        TestAudjpyWarmupCacheIsGapFree, abaixo."""
        months = {
            "2011-11": {}, "2011-12": {}, "2012-10": {}, "2013-01": {}, "2013-02": {},
        }
        gaps = fetch_histdata_mn1_warmup.find_gaps(months)
        self.assertIn("2012-01", gaps)
        self.assertIn("2012-09", gaps)
        self.assertIn("2012-11", gaps)
        self.assertIn("2012-12", gaps)
        self.assertEqual(len(gaps), 11)


class TestAudjpyWarmupCacheIsGapFree(unittest.TestCase):
    """Regressão direta sobre o ARTEFATO versionado (achado herdr-review
    mfc-62, MFC62-01/`mfc-rev`) — carrega data/histdata_mn1_warmup/audjpy.json
    de verdade, não uma reprodução sintética. Se alguém reintroduzir o buraco
    de 2012 (ou qualquer outro) nesse arquivo, isto tem que ficar vermelho."""

    def _load(self):
        path = os.path.join(bc.HISTDATA_WARMUP_DIR, "audjpy.json")
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def test_no_internal_gaps(self):
        months = self._load()
        self.assertEqual(fetch_histdata_mn1_warmup.find_gaps(months), [])

    def test_covers_2010_through_2021_with_144_consecutive_months(self):
        months = self._load()
        self.assertEqual(len(months), 144)
        self.assertEqual(min(months), "2010-01")
        self.assertEqual(max(months), "2021-12")

    def test_the_2012_months_filled_via_dukascopy_are_present_and_valid(self):
        months = self._load()
        for m in range(1, 13):
            key = f"2012-{m:02d}"
            self.assertIn(key, months)
            bar = months[key]
            self.assertTrue(bar["low"] <= bar["open"] <= bar["high"])
            self.assertTrue(bar["low"] <= bar["close"] <= bar["high"])
            self.assertGreater(bar["low"], 0)


class TestLoadMn1SeriesWithWarmup(unittest.TestCase):
    """load_mn1_series_with_warmup: só o prefixo antigo pode vir da
    HistData.com — nunca as barras recentes/decisórias, nunca sobrepondo o
    que a Exness já tem."""

    def _run(self, exness_rates, warmup_months=None, count=60):
        with tempfile.TemporaryDirectory() as tmp:
            if warmup_months is not None:
                with open(os.path.join(tmp, "eurusd.json"), "w", encoding="utf-8") as f:
                    json.dump(warmup_months, f)
            fake_mt5 = MagicMock()
            fake_mt5.copy_rates_from_pos.return_value = exness_rates
            with patch.object(bc, "HISTDATA_WARMUP_DIR", tmp), \
                 patch.object(bc, "ALL_28_PAIRS", ["EURUSD"]), \
                 patch.object(bc, "CURRENCIES", ["EUR", "USD"]), \
                 patch.object(bc, "to_broker_symbol", side_effect=lambda p: p), \
                 patch.object(bc, "MT5_AVAILABLE", True), \
                 patch.object(bc, "mt5", fake_mt5), \
                 patch.object(bc, "get_tf_constant", return_value=1):
                return bc.load_mn1_series_with_warmup(count=count)

    def test_stays_degraded_when_exness_alone_is_short_and_no_cache_exists(self):
        """Controle negativo: sem cache de aquecimento, o comportamento é
        idêntico ao calculate_full_css original — 59 barras continuam
        degradadas."""
        exness_rates = _monthly_rates(59, 2021, 9)
        res, times, quality, warmup_used = self._run(exness_rates, warmup_months=None)
        self.assertEqual(quality["status"], "degraded")
        self.assertEqual(quality["short_history_pairs"], ["EURUSD"])
        self.assertEqual(warmup_used, {})

    def test_returns_unavailable_without_raising_when_mt5_is_not_available(self):
        """Achado herdr-review mfc-61 (MFC61-02/`mfc-rev`): calculate_full_css()
        devolve quality=unavailable sem desreferenciar mt5 quando
        MT5_AVAILABLE é falso; a API nova precisa do mesmo comportamento
        fail-closed em vez de AttributeError."""
        with patch.object(bc, "MT5_AVAILABLE", False), \
             patch.object(bc, "mt5", None):
            res, times, quality, warmup_used = bc.load_mn1_series_with_warmup(count=60)
        self.assertIsNone(res)
        self.assertIsNone(times)
        self.assertEqual(quality["status"], "unavailable")
        self.assertEqual(warmup_used, {})

    def test_reports_histdata_warmup_gaps_in_quality_for_audit(self):
        """Achado herdr-review mfc-62 (P2-2, `mfc-rev-2`): um buraco real no
        meio do cache de aquecimento precisa ficar visível na proveniência
        E degradar o status — antes (mfc-61) só ficava registrado sem
        afetar `require_clean`, o que deixava o caso real do AUDJPY
        (11 meses de 2012 ausentes) passar como `status=clean`."""
        exness_rates = _monthly_rates(59, 2021, 9)
        warmup_keys = _sequential_months(2012, 1, 116)
        warmup_months = {
            key: {"open": 1.0, "high": 1.01, "low": 0.99, "close": 1.0, "n": 100}
            for key in warmup_keys
        }
        # buraco no meio, longe da ponta usada pela Exness
        del warmup_months["2015-06"]
        del warmup_months["2015-07"]
        res, times, quality, warmup_used = self._run(exness_rates, warmup_months)
        self.assertEqual(quality["histdata_warmup_gaps"], {"EURUSD": ["2015-06", "2015-07"]})
        self.assertEqual(quality["warmup_gap_pairs"], ["EURUSD"])
        self.assertEqual(quality["status"], "degraded")

    def test_seam_gap_between_warmup_cache_end_and_exness_start_is_detected(self):
        """Achado herdr-review mfc-62 (P2-1, `mfc-rev-2`): um cache
        internamente contíguo mas que TERMINA antes do mês imediatamente
        anterior ao início da Exness deixa um buraco exatamente na emenda —
        invisível pro find_gaps() interno do cache (que só olha min..max
        dele) e pras contagens de posição (`short_history_pairs`).
        Hoje isso não é alcançável com a profundidade fixa atual da Exness,
        mas protege contra uma Exness com janela rolante (achado explícito
        do revisor: "não consigo distinguir de data de início fixa neste
        checkout")."""
        exness_rates = _monthly_rates(59, 2021, 9)
        # contíguo, mas termina em 2021-06 -- faltam 2021-07 e 2021-08 antes
        # do início real da Exness (2021-09)
        warmup_keys = _sequential_months(2012, 1, 114)
        warmup_months = {
            key: {"open": 1.0, "high": 1.01, "low": 0.99, "close": 1.0, "n": 100}
            for key in warmup_keys
        }
        res, times, quality, warmup_used = self._run(exness_rates, warmup_months)
        self.assertEqual(
            quality["histdata_warmup_gaps"], {"EURUSD": ["2021-07", "2021-08"]},
        )
        self.assertEqual(quality["warmup_gap_pairs"], ["EURUSD"])
        self.assertEqual(quality["status"], "degraded")

    def test_gap_outside_the_used_slice_does_not_degrade_status(self):
        """Achado herdr-review mfc-63 (P3-3/`mfc-rev-2`): a correção do
        P2-1/P2-2 mudou a medição de gaps do cache INTEIRO pra fatia
        efetivamente concatenada (`warmup_rows`, filtrada por
        `row[0] < earliest_exness`). Os testes anteriores só cobrem a
        direção "detectar e degradar" — nenhum prova que um buraco em meses
        do cache que o filtro DESCARTA (por serem >= earliest_exness) não
        aciona nada. Sem este teste, uma regressão que voltasse a medir
        gaps sobre o cache inteiro (ou medisse sobre os dois ao mesmo
        tempo) passaria despercebida e reintroduziria o falso positivo que
        a própria correção existe pra eliminar."""
        exness_rates = _monthly_rates(59, 2021, 9)
        # cache vai até 2022-12 (bem além do que a Exness precisa, que
        # começa em 2021-09) -- 2022-03/2022-04 removidos, mas esses meses
        # nunca entram em warmup_rows (row[0] >= earliest_exness).
        warmup_keys = _sequential_months(2012, 1, 132)  # 2012-01..2022-12
        warmup_months = {
            key: {"open": 1.0, "high": 1.01, "low": 0.99, "close": 1.0, "n": 100}
            for key in warmup_keys
        }
        del warmup_months["2022-03"]
        del warmup_months["2022-04"]
        res, times, quality, warmup_used = self._run(exness_rates, warmup_months)
        self.assertEqual(quality["histdata_warmup_gaps"], {})
        self.assertEqual(quality["warmup_gap_pairs"], [])
        self.assertEqual(quality["status"], "clean")
        # confirma que o filtro realmente descartou o rabo pós-Exness --
        # se warmup_used incluísse os meses de 2022, o buraco estaria
        # DENTRO da fatia usada e este teste não provaria nada.
        self.assertEqual(warmup_used, {"EURUSD": 116})  # só 2012-01..2021-08

    def test_reaches_clean_status_when_warmup_fills_the_deficit(self):
        """59 barras da Exness (2021-09..2026-08) precisam de 169 pro
        aquecimento do ATR(100)+offset-10 (count=60) — faltam 110. O cache
        cobre 2012-01..2021-08 (116 meses, folga de 6), estritamente ANTES
        do primeiro mês da Exness."""
        exness_rates = _monthly_rates(59, 2021, 9)
        warmup_keys = _sequential_months(2012, 1, 116)
        warmup_months = {
            key: {"open": 1.0, "high": 1.01, "low": 0.99, "close": 1.0, "n": 100}
            for key in warmup_keys
        }
        res, times, quality, warmup_used = self._run(exness_rates, warmup_months)
        self.assertIsNotNone(res)
        self.assertEqual(quality["status"], "clean")
        self.assertEqual(quality["short_history_pairs"], [])
        self.assertEqual(warmup_used, {"EURUSD": 116})
        self.assertEqual(len(times), 60)

    def test_warmup_month_coinciding_with_exness_start_is_never_used(self):
        """Um mês de aquecimento que coincide com (ou é posterior a) o
        primeiro mês real da Exness precisa ser descartado — só o prefixo
        estritamente ANTERIOR pode vir de fora. (30 meses de Exness, não 3 —
        abaixo de MIN_COMMON_HISTORY_BARS=30 o par nem entra em pair_dfs, o
        que mascararia o que este teste quer provar.)"""
        exness_rates = _monthly_rates(30, 2021, 9, close=1.0)
        warmup_months = {
            "2021-09": {"open": 9.0, "high": 9.1, "low": 8.9, "close": 9.0, "n": 1},  # coincide, deve ser ignorado
            "2021-08": {"open": 2.0, "high": 2.1, "low": 1.9, "close": 2.0, "n": 1},  # anterior, deve entrar
        }
        res, times, quality, warmup_used = self._run(exness_rates, warmup_months)
        self.assertEqual(warmup_used, {"EURUSD": 1})  # só 2021-08 entrou

    def test_default_load_series_never_touches_histdata_warmup(self):
        """load_series() sem o flag continua 100% calculate_full_css — o
        caminho novo só existe quando use_histdata_mn1_warmup=True."""
        fake_quality = {"status": "clean"}
        with patch.object(bc, "load_mn1_series_with_warmup") as warmup_fn, \
             patch.object(
                 bc, "calculate_full_css",
                 return_value=({"EUR": [0.1]}, ["2026-01-01 00:00"], None, fake_quality),
             ), \
             patch.object(bc, "get_tf_constant", return_value=1):
            series = bc.load_series()
        warmup_fn.assert_not_called()
        self.assertIn("MN1", series)

    def test_load_series_uses_warmup_path_only_for_mn1_when_flag_enabled(self):
        fake_quality = {"status": "clean"}
        with patch.object(
                 bc, "load_mn1_series_with_warmup",
                 return_value=({"EUR": [0.1]}, ["2026-01-01 00:00"], dict(fake_quality), {"EURUSD": 5}),
             ) as warmup_fn, \
             patch.object(
                 bc, "calculate_full_css",
                 return_value=({"EUR": [0.1]}, ["2026-01-01 00:00"], None, dict(fake_quality)),
             ) as calc_fn, \
             patch.object(bc, "get_tf_constant", return_value=1):
            series = bc.load_series(use_histdata_mn1_warmup=True)
        warmup_fn.assert_called_once_with(bc.TF_COUNTS["MN1"])
        # calculate_full_css só pros outros 4 TFs (W1, D1, H4, H1), nunca MN1
        self.assertEqual(calc_fn.call_count, len(bc.TFS) - 1)
        self.assertEqual(series["MN1"]["quality"]["histdata_warmup_months_used"], {"EURUSD": 5})


if __name__ == "__main__":
    unittest.main()
