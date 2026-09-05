"""
Regressão do achado herdr-ask mfc-18: web/history_tracker.py tinha sua própria
implementação de ATR (período 20, min_periods=1 implícito) — divergente do
canônico (ATR_PERIOD=100, min_periods=ATR_PERIOD, docs/MATHEMATICAL_MODELS.md
§1.2) — decidindo qualified_currencies. Drift confirmado via git (upstream/main
commit 1e0bda3 "unify ATR 100" nunca chegou a este `main`), medido numa
comparação real contra a instância isolada mfc-backtest (17/120 pontos
moeda-noite divergentes em 15 noites reais, sempre no mesmo sentido: o motor
antigo subcontava qualificações).

Estes testes são determinísticos (mockam mt5 inteiramente) e travam a
correção: se alguém reintroduzir o período 20, remover min_periods=ATR_PERIOD,
ou voltar as contagens de barra antigas (24/40/60/100/150), falham.

NÃO executável neste checkout Linux (numpy/pandas ausentes) — sintaxe
verificada via `ast.parse`; execução real pendente de um ambiente com as
dependências instaladas (ver CLAUDE.md).
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import web.css_service as cs
import web.history_tracker as ht


def _make_fake_rates(count, base_price=1.10000, daily_range=0.00120, spike_last_n=0, spike_range=0.02000):
    """Constrói um array estruturado igual ao que mt5.copy_rates_from devolve
    de verdade: campos ordenados (time, open, high, low, close, tick_volume,
    spread, real_volume) — a indexação posicional do código real (r[2]=high,
    r[3]=low, r[4]=close) e pd.DataFrame(rates)['close'] dependem dessa ordem.

    `spike_last_n`/`spike_range`: se > 0, as últimas `spike_last_n` barras têm
    range MUITO maior que o resto — usado pra provar que ATR(20) (janela curta)
    reage muito mais forte a um pico recente do que ATR(100) (janela longa).
    """
    dtype = [
        ("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"),
        ("close", "f8"), ("tick_volume", "i8"), ("spread", "i4"), ("real_volume", "i8"),
    ]
    rates = np.zeros(count, dtype=dtype)
    price = base_price
    for i in range(count):
        rng = spike_range if (spike_last_n and i >= count - spike_last_n) else daily_range
        o = price
        c = price + rng * 0.1  # leve tendência de alta, constante e pequena
        h = max(o, c) + rng * 0.5
        l = min(o, c) - rng * 0.5
        rates[i] = (1_700_000_000 + i * 86400, o, h, l, c, 100, 1, 0)
        price = c
    return rates


class TestCalcSingleTfCcySlopeUsesCanonicalAtr(unittest.TestCase):
    """_calc_single_tf_ccy_slope (usado pelas curvas H1/H4 do painel de auditoria)."""

    def setUp(self):
        self._patches = [
            patch.object(cs, "MT5_SYMBOL_SUFFIX", "m"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def test_requests_enough_bars_for_full_atr_100_window(self):
        fake_mt5 = MagicMock()
        fake_mt5.TIMEFRAME_H1 = 16385
        rates = _make_fake_rates(ht.TRACK_RECORD_MIN_BARS)
        fake_mt5.copy_rates_from.return_value = rates

        with patch.object(ht, "mt5", fake_mt5):
            ht.TrackRecordEngine._calc_single_tf_ccy_slope(None, "EUR", fake_mt5.TIMEFRAME_H1, 12345)

        # Todo par que contém "EUR" foi consultado — confere o COUNT pedido em
        # cada chamada, não só a última.
        self.assertGreater(fake_mt5.copy_rates_from.call_count, 0)
        for call in fake_mt5.copy_rates_from.call_args_list:
            requested_count = call.args[3]
            self.assertEqual(
                requested_count, ht.TRACK_RECORD_MIN_BARS,
                "count pedido a copy_rates_from deveria ser TRACK_RECORD_MIN_BARS "
                "(antes desta correção era um 35 fixo, insuficiente pra ATR(100))",
            )

    def test_uses_canonical_atr_period_and_min_periods(self):
        fake_mt5 = MagicMock()
        fake_mt5.TIMEFRAME_H1 = 16385
        fake_mt5.copy_rates_from.return_value = _make_fake_rates(ht.TRACK_RECORD_MIN_BARS)

        real_calc_atr_sma = cs.calc_atr_sma
        spy = MagicMock(side_effect=real_calc_atr_sma)

        with patch.object(ht, "mt5", fake_mt5), patch.object(ht, "calc_atr_sma", spy):
            ht.TrackRecordEngine._calc_single_tf_ccy_slope(None, "EUR", fake_mt5.TIMEFRAME_H1, 12345)

        self.assertGreater(spy.call_count, 0)
        for call in spy.call_args_list:
            period_arg = call.args[3]
            self.assertEqual(
                period_arg, cs.ATR_PERIOD,
                "período do ATR deveria ser o canônico (100), não o antigo 20",
            )
            self.assertEqual(
                call.kwargs.get("min_periods"), cs.ATR_PERIOD,
                "min_periods deveria ser ATR_PERIOD (janela cheia obrigatória), "
                "não o default fraco (1) que a assinatura de calc_atr_sma permite",
            )

    def test_partial_leg_coverage_returns_neutral_not_a_biased_subset_average(self):
        """Achado herdr-review mfc-80 (mfc-rev, MFC-80-01): usado pelas curvas
        H1/H4 do painel de auditoria. Antes desta correção, se UMA das 7
        pernas de uma moeda faltasse, a função ainda calculava a média das 6
        sobreviventes — um número que parece um slope válido mas não é o
        conjunto canônico. Com TRACK_RECORD_MIN_BARS=130 (vs. os 35 antigos),
        faltar uma perna ficou mais provável, não menos."""
        fake_mt5 = MagicMock()
        fake_mt5.TIMEFRAME_H1 = 16385
        missing_broker_symbol = cs.to_broker_symbol("EURJPY")
        full_rates = _make_fake_rates(ht.TRACK_RECORD_MIN_BARS, spike_last_n=15, spike_range=0.02000)

        def side_effect(broker_symbol, tf_val, target_time, count):
            if broker_symbol == missing_broker_symbol:
                return None
            return full_rates

        fake_mt5.copy_rates_from.side_effect = side_effect

        with patch.object(ht, "mt5", fake_mt5):
            result = ht.TrackRecordEngine._calc_single_tf_ccy_slope(None, "EUR", fake_mt5.TIMEFRAME_H1, 12345)

        self.assertEqual(
            result, 0.0,
            "com EURJPY faltando (1 de 7 pernas do EUR), o resultado deveria "
            "ser neutro (0.0), nunca a média enviesada das 6 pernas restantes",
        )


class TestEvaluateCssAtTimeUsesCanonicalAtr(unittest.TestCase):
    """_evaluate_css_at_time (decide qualified_currencies do track record)."""

    def setUp(self):
        self._patches = [patch.object(cs, "MT5_SYMBOL_SUFFIX", "m")]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def _fake_mt5_with_uniform_rates(self, count):
        fake_mt5 = MagicMock()
        fake_mt5.TIMEFRAME_MN1 = 1
        fake_mt5.TIMEFRAME_W1 = 2
        fake_mt5.TIMEFRAME_D1 = 3
        fake_mt5.TIMEFRAME_H4 = 4
        fake_mt5.TIMEFRAME_H1 = 5
        fake_mt5.copy_rates_from.return_value = _make_fake_rates(count)
        return fake_mt5

    def test_tf_map_requests_uniform_track_record_min_bars(self):
        """As 5 contagens (antes 24/40/60/100/150 — MN1 nem fechava ATR(20))
        agora usam o mesmo TRACK_RECORD_MIN_BARS: o requisito de barras do
        ATR(100) não depende de qual timeframe está sendo lido."""
        fake_mt5 = self._fake_mt5_with_uniform_rates(ht.TRACK_RECORD_MIN_BARS)

        with patch.object(ht, "mt5", fake_mt5):
            ht.TrackRecordEngine._evaluate_css_at_time(None, 12345)

        requested_counts = {call.args[3] for call in fake_mt5.copy_rates_from.call_args_list}
        self.assertEqual(
            requested_counts, {ht.TRACK_RECORD_MIN_BARS},
            "todas as chamadas (todos os 5 TFs x 28 pares) deveriam pedir a "
            "mesma contagem uniforme TRACK_RECORD_MIN_BARS",
        )
        # Achado herdr-review mfc-80 (mfc-rev, MFC-80-03): o teste anterior só
        # conferia o SET de counts, não a QUANTIDADE de chamadas — não provava
        # que os 5 TFs x 28 pares foram de fato processados.
        self.assertEqual(
            fake_mt5.copy_rates_from.call_count, 5 * len(ht.ALL_28_PAIRS),
            "esperado exatamente 5 timeframes x 28 pares chamadas a copy_rates_from",
        )

    def test_uses_canonical_atr_period_and_min_periods_for_all_timeframes(self):
        fake_mt5 = self._fake_mt5_with_uniform_rates(ht.TRACK_RECORD_MIN_BARS)
        real_calc_atr_sma = cs.calc_atr_sma
        spy = MagicMock(side_effect=real_calc_atr_sma)

        with patch.object(ht, "mt5", fake_mt5), patch.object(ht, "calc_atr_sma", spy):
            ht.TrackRecordEngine._evaluate_css_at_time(None, 12345)

        self.assertGreater(spy.call_count, 0)
        for call in spy.call_args_list:
            self.assertEqual(call.args[3], cs.ATR_PERIOD)
            self.assertEqual(call.kwargs.get("min_periods"), cs.ATR_PERIOD)

    def test_track_record_min_bars_matches_required_full_history_bars(self):
        """Pin da derivação: TRACK_RECORD_MIN_BARS = required_full_history_bars(1) + margem.
        Um ponto de decisão só (não uma série) precisa do mínimo pra UMA posição
        ter o ATR(100) com janela cheia no índice lido (pos-10)."""
        self.assertEqual(ht.TRACK_RECORD_MIN_BARS, cs.required_full_history_bars(1) + 20)

    def test_partial_pair_coverage_engages_the_guard_in_the_real_call_path(self):
        """Achado herdr-review mfc-80 (mfc-rev, MFC-80-03): os testes
        anteriores só provavam _aggregate_pair_slopes_to_ccy_slopes() isolada
        — se alguém revertesse a chamada dela em _evaluate_css_at_time() pra
        voltar à média inline antiga, nenhum teste existente pegaria isso.
        Este roda o fluxo REAL (side_effect por símbolo, um par faltando) e
        espiona a própria função de agregação pra provar que ela é chamada
        com a cobertura incompleta de verdade — não uma reprodução isolada."""
        fake_mt5 = MagicMock()
        fake_mt5.TIMEFRAME_MN1 = 1
        fake_mt5.TIMEFRAME_W1 = 2
        fake_mt5.TIMEFRAME_D1 = 3
        fake_mt5.TIMEFRAME_H4 = 4
        fake_mt5.TIMEFRAME_H1 = 5

        missing_broker_symbol = cs.to_broker_symbol("EURGBP")
        full_rates = _make_fake_rates(ht.TRACK_RECORD_MIN_BARS)

        def side_effect(broker_symbol, tf_val, target_time, count):
            if broker_symbol == missing_broker_symbol:
                return None
            return full_rates

        fake_mt5.copy_rates_from.side_effect = side_effect

        real_aggregate = ht._aggregate_pair_slopes_to_ccy_slopes
        spy = MagicMock(side_effect=real_aggregate)

        with patch.object(ht, "mt5", fake_mt5), patch.object(ht, "_aggregate_pair_slopes_to_ccy_slopes", spy):
            ht.TrackRecordEngine._evaluate_css_at_time(None, 12345)

        self.assertEqual(spy.call_count, 5, "a guarda deveria ser chamada uma vez por timeframe")
        for call in spy.call_args_list:
            pair_slopes_arg = call.args[0]
            self.assertEqual(
                len(pair_slopes_arg), len(ht.ALL_28_PAIRS) - 1,
                "com EURGBP faltando, cada timeframe deveria chegar na guarda "
                "com exatamente 27 pares sobreviventes — provando que a "
                "cobertura incompleta REALMENTE chega até a função de guarda "
                "no fluxo de _evaluate_css_at_time(), não só num teste isolado",
            )


class TestPartialPairCoverageNeverBecomesABiasedAverage(unittest.TestCase):
    """Achado herdr-review mfc-79 (mfc-rev-2): uniformizar TRACK_RECORD_MIN_BARS
    resolve o período do ATR mas expõe um segundo problema — em corretoras/TFs
    onde a maioria dos 28 pares não tem profundidade suficiente (ex.: MN1 na
    instância isolada mfc-backtest: só 3 de 28 pares chegam a 130 barras), a
    média por moeda passava a vir de 1-2 pares sobreviventes em vez de ficar
    ausente de forma consistente. _aggregate_pair_slopes_to_ccy_slopes()
    corrige isso: cobertura incompleta (< 28 pares) vira neutro (0.0) pra
    TODAS as moedas, nunca uma média enviesada."""

    def test_full_coverage_produces_real_per_currency_average(self):
        # EURUSD e GBPUSD sobrevivem, todos os outros 26 pares também —
        # cobertura completa: a média deve refletir os valores reais, não 0.0.
        pair_slopes = {sym: 0.10 for sym in ht.ALL_28_PAIRS}
        result = ht._aggregate_pair_slopes_to_ccy_slopes(pair_slopes)
        self.assertNotEqual(result["USD"], 0.0)
        self.assertEqual(len(result), len(ht.CURRENCIES))

    def test_partial_coverage_never_produces_a_biased_average(self):
        """Reproduz o cenário medido pela mfc-rev-2: só EURUSD, GBPUSD e
        GBPJPY sobrevivem (profundidade MN1 real da instância isolada) — sem
        o fix, USD sairia de 2 amostras, GBP de 2, EUR/JPY de 1, e
        AUD/NZD/CAD/CHF ficariam em 0.0 (mistura de "moeda sem dado" com
        "moeda com dado forte de 1 par só", ambos aparentando ser o mesmo
        0.0/não-0.0 por acaso). Com o fix, TODAS as 8 moedas devem ser 0.0 —
        cobertura incompleta é um estado, não uma média."""
        pair_slopes = {"EURUSD": 5.0, "GBPUSD": -5.0, "GBPJPY": 5.0}
        result = ht._aggregate_pair_slopes_to_ccy_slopes(pair_slopes)
        for ccy in ht.CURRENCIES:
            self.assertEqual(
                result[ccy], 0.0,
                f"{ccy} deveria ser neutro (0.0) com cobertura incompleta "
                f"(3/28 pares), não uma média calculada só a partir de quem sobrou",
            )

    def test_zero_surviving_pairs_also_produces_neutral(self):
        """Caso degenerado (era o comportamento acidentalmente seguro antes
        do fix, quando a guarda antiga fazia TODOS os pares falharem pro
        MN1): continua neutro."""
        result = ht._aggregate_pair_slopes_to_ccy_slopes({})
        self.assertTrue(all(v == 0.0 for v in result.values()))


class TestOldAtr20WeakerThanCanonical(unittest.TestCase):
    """Prova comportamental (não só de assinatura): um pico de volatilidade
    recente inflaciona ATR(20) muito mais que ATR(100), o que ANTES podia
    encolher o slope reportado o suficiente pra deixar de qualificar uma
    moeda que o motor canônico qualificaria — exatamente o padrão medido nos
    dados reais (17/120 pontos, sempre subcontagem, nunca sobrecontagem)."""

    def setUp(self):
        self._patches = [patch.object(cs, "MT5_SYMBOL_SUFFIX", "m")]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def test_recent_volatility_spike_shrinks_old_atr20_slope_more_than_canonical(self):
        count = ht.TRACK_RECORD_MIN_BARS
        rates = _make_fake_rates(count, spike_last_n=15, spike_range=0.02000)
        closes = rates["close"]
        highs = rates["high"]
        lows = rates["low"]

        # "Como era antes desta correção", calculado inline (sem tocar o
        # código-fonte) — não uma reprodução de calc_atr_sma legado, só o
        # mesmo cálculo com os parâmetros antigos, pra comparar magnitude.
        atr_old = cs.calc_atr_sma(highs, lows, closes, 20)  # min_periods default (1)
        atr_new = cs.calc_atr_sma(highs, lows, closes, cs.ATR_PERIOD, min_periods=cs.ATR_PERIOD)

        pos = count - 1
        atr_val_old = atr_old[pos - 10] / 10.0
        atr_val_new = atr_new[pos - 10] / 10.0

        lwma = cs.calc_lwma(closes, 21)
        ma0, ma1, c0 = lwma[pos], lwma[pos - 1], closes[pos]
        prev = (ma1 * 231.0 + c0 * 20.0) / 251.0
        sl_old = (ma0 - prev) / atr_val_old if atr_val_old > 0 else 0.0
        sl_new = (ma0 - prev) / atr_val_new if atr_val_new > 0 else 0.0

        self.assertGreater(
            atr_val_old, atr_val_new * 2,
            "o pico recente deveria inflacionar MUITO mais o ATR(20) de janela "
            "curta do que o ATR(100) de janela longa",
        )
        self.assertGreater(
            abs(sl_new), abs(sl_old),
            "com o ATR canônico (100), o slope reportado deveria ser MAIOR em "
            "módulo do que seria com o ATR(20) antigo — o motor antigo "
            "encolhia o sinal justamente quando um pico recente inflava seu "
            "denominador de janela curta, o mecanismo por trás da "
            "subcontagem medida nos dados reais (17/120 pontos, mfc-18)",
        )


if __name__ == "__main__":
    unittest.main()
