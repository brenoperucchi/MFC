"""Regressões de integridade do snapshot 5-TF no adaptador web."""

import ast
from unittest.mock import MagicMock, patch
from pathlib import Path

import numpy as np

import web.css_service as css


def _rates(count=120, start=0):
    dtype = [
        ("time", "i8"),
        ("open", "f8"),
        ("high", "f8"),
        ("low", "f8"),
        ("close", "f8"),
    ]
    rows = np.zeros(count, dtype=dtype)
    rows["time"] = (start + np.arange(count)) * 3600
    rows["open"] = 1.0
    rows["high"] = 1.01
    rows["low"] = 0.99
    rows["close"] = 1.0
    return rows


def test_calculate_full_css_rejects_partial_pair_universe():
    fake_mt5 = MagicMock()
    fake_mt5.copy_rates_from_pos.side_effect = (
        lambda symbol, *_args: _rates() if symbol == "EURUSD" else None
    )
    with patch.object(css, "MT5_AVAILABLE", True), \
         patch.object(css, "mt5", fake_mt5), \
         patch.object(css, "ALL_28_PAIRS", ["EURUSD", "GBPUSD"]), \
         patch.object(css, "to_broker_symbol", side_effect=lambda pair: pair):
        result = css.calculate_full_css(1, count=2)

    assert result == (None, None, None)


def test_calculate_full_css_rejects_short_history_before_deriving_slope():
    fake_mt5 = MagicMock()
    fake_mt5.copy_rates_from_pos.return_value = _rates(150)
    with patch.object(css, "MT5_AVAILABLE", True), \
         patch.object(css, "mt5", fake_mt5), \
         patch.object(css, "ALL_28_PAIRS", ["EURUSD"]), \
         patch.object(css, "to_broker_symbol", side_effect=lambda pair: pair):
        result = css.calculate_full_css(1, count=70, return_quality=True)

    assert result[0] is not None
    assert result[3]["status"] == "degraded"
    assert result[3]["short_history_pairs"] == ["EURUSD"]
    assert result[3]["required_full_history_bars"] == 179


def test_calc_atr_sma_requires_full_window_before_returning_values():
    high = np.full(120, 1.01)
    low = np.full(120, 0.99)
    close = np.full(120, 1.0)

    atr = css.calc_atr_sma(high, low, close, period=100, min_periods=100)

    assert np.isnan(atr[:99]).all()
    assert np.isfinite(atr[99:]).all()


def test_short_standard_history_zeroes_slope_until_atr_warmup():
    rates = _rates(150)
    rates["close"] = 1.0 + np.arange(150) * 0.001
    rates["open"] = rates["close"]
    rates["high"] = rates["close"] + 0.01
    rates["low"] = rates["close"] - 0.01
    fake_mt5 = MagicMock()
    fake_mt5.copy_rates_from_pos.return_value = rates
    with patch.object(css, "MT5_AVAILABLE", True), \
         patch.object(css, "mt5", fake_mt5), \
         patch.object(css, "ALL_28_PAIRS", ["EURUSD"]), \
         patch.object(css, "to_broker_symbol", side_effect=lambda pair: pair):
        result = css.calculate_full_css(1, count=70, return_quality=True)

    slopes = result[2]["EURUSD"][2]
    assert np.allclose(slopes[:29], 0.0)
    assert np.any(np.abs(slopes[29:]) > 0.0)


def test_calculate_full_css_rejects_short_common_intersection():
    fake_mt5 = MagicMock()
    fake_mt5.copy_rates_from_pos.side_effect = lambda symbol, *_args: (
        _rates(120) if symbol == "EURUSD" else _rates(120, start=91)
    )
    with patch.object(css, "MT5_AVAILABLE", True), \
         patch.object(css, "mt5", fake_mt5), \
         patch.object(css, "ALL_28_PAIRS", ["EURUSD", "GBPUSD"]), \
         patch.object(css, "to_broker_symbol", side_effect=lambda pair: pair):
        result = css.calculate_full_css(1, count=2)

    assert result == (None, None, None)


def test_calculate_full_css_marks_warmed_but_short_common_intersection_degraded():
    # Cada par tem 109 barras próprias antes da interseção. Assim, o primeiro
    # timestamp comum já está aquecido para count=60, mas há somente 50 barras
    # comuns — o caso que a checagem de posição inicial não alcança.
    common = np.arange(200, 250)
    first = _rates(159)
    first["time"] = np.r_[np.arange(109), common] * 3600
    second = _rates(159)
    second["time"] = np.r_[np.arange(1000, 1109), common] * 3600

    fake_mt5 = MagicMock()
    fake_mt5.copy_rates_from_pos.side_effect = (
        lambda symbol, *_args: first if symbol == "EURUSD" else second
    )
    with patch.object(css, "MT5_AVAILABLE", True), \
         patch.object(css, "mt5", fake_mt5), \
         patch.object(css, "ALL_28_PAIRS", ["EURUSD", "GBPUSD"]), \
         patch.object(css, "to_broker_symbol", side_effect=lambda pair: pair):
        result = css.calculate_full_css(1, count=60, return_quality=True)

    quality = result[3]
    assert result[0] is not None
    assert quality["common_history_bars"] == 50
    assert quality["returned_history_bars"] == 50
    assert quality["requested_history_bars"] == 60
    assert quality["short_history_pairs"] == []
    assert quality["status"] == "degraded"


def test_partial_snapshot_serves_safe_cache_and_updates_throttle_clock():
    engine = object.__new__(css.CSSDataEngine)
    engine.cache_standard = {"mt5_connected": True, "sentinel": "old"}
    engine.cache_gauss = {}
    engine.last_update_standard = 10.0
    engine.last_update_gauss = None
    engine.last_error = None

    with patch.object(engine, "connect_mt5", return_value=True), \
         patch.object(css, "TIMEFRAMES_CONFIG", [("H1", 1), ("H4", 1)]), \
         patch.object(css, "calculate_full_css", return_value=(None, None, None)), \
         patch.object(css.time, "time", return_value=123.0):
        result = engine.update_data(force=True)

    assert result["mt5_connected"] is False
    assert result["sentinel"] == "old"
    assert engine.cache_standard["mt5_connected"] is False
    assert engine.last_update_standard == 123.0
    assert "timeframes ausentes" in engine.last_error


def test_partial_timeframe_snapshot_uses_controlled_fallback(tmp_path):
    engine = object.__new__(css.CSSDataEngine)
    engine.cache_standard = {}
    engine.cache_gauss = {}
    engine.last_update_standard = None
    engine.last_update_gauss = None
    engine.last_error = None
    fallback = {"mt5_connected": False, "source": "fallback"}
    real_snapshot = Path(css.DB_STANDARD_FILE).read_bytes()

    def result_for_tf(tf, *_args, **_kwargs):
        if tf == 1:
            return ({c: np.array([0.0]) for c in css.CURRENCIES}, [1], {})
        return (None, None, None)

    with patch.object(engine, "connect_mt5", return_value=True), \
         patch.object(css, "TIMEFRAMES_CONFIG", [("H1", 1), ("H4", 1)]), \
         patch.object(
             css, "get_tf_constant", side_effect=lambda name: 1 if name == "H1" else 2
         ), \
         patch.object(css, "DB_STANDARD_FILE", str(tmp_path / "css_standard.json")), \
         patch.object(css, "calculate_full_css", side_effect=result_for_tf), \
         patch.object(engine, "_generate_fallback_data", return_value=fallback), \
         patch.object(css.time, "time", return_value=456.0):
        result = engine.update_data(force=True)

    assert result["mt5_connected"] is False
    assert result["source"] == "fallback"
    assert result["snapshot_quality"]["status"] == "incomplete"
    assert result["snapshot_quality"]["missing_timeframes"] == ["H4"]
    assert "timeframes ausentes: H4" in engine.last_error
    assert Path(css.__file__).resolve().parent.parent.joinpath("data", "css_standard.json").read_bytes() == real_snapshot


def test_api_css_all_public_schema_separates_currency_and_pair_scores(tmp_path):
    """Regressão nomeada do payload público (plano Port A, Fase C): o payload
    servido em /api/css/all precisa manter `currencies[].total_score` como o
    único alias público de `score_total`, sem vazar diagnósticos internos do
    motor (`macro_bias`, `vectors`, `base_vectors`, `maturities`, `penalties`,
    `weighted_score`, `macro`/`operational` completos), e `pairs[].total_score`
    precisa continuar sendo um campo público separado, com escala própria."""
    engine = object.__new__(css.CSSDataEngine)
    engine.cache_standard = {}
    engine.cache_gauss = {}
    engine.last_update_standard = None
    engine.last_update_gauss = None
    engine.last_error = None
    engine.is_mt5_connected = True

    fake_confluence = {
        "ccy": "EUR",
        "macro": {"mn_triad": {}, "w1_triad": {}, "d1_triad": {}},
        "operational": {"h4_triad": {}, "h1_triad": {}},
        "confluence_state": "BOX DE EQUILÍBRIO (TESTE DO 0)",
        "final_verdict": "AGUARDAR DEFINIÇÃO",
        "trade_bias": "COMPRA",
        "has_divergence": False,
        "divergence_alert": "Nenhuma divergência estrutural",
        "score_total": 4.2,
        "weighted_score": 56.7,
        "macro_bias": 0.35,
        "base_vectors": {"D1": 1.0},
        "vectors": {"D1": 1.0},
        "maturities": {"D1": 1.0},
        "penalties": {"D1": 1.0},
        "aligned_up_count": 3,
        "aligned_dn_count": 0,
        "aligned_flat_count": 2,
        "ref_dt_brt": "2026-08-30T21:00:00-03:00",
    }
    fake_triad = {
        "led": "green", "region": "-", "current_cycle": "-",
        "owing_cycle": "-", "score_str": "0.00", "angle": "-",
    }
    fake_pairs = [{
        "pair": "EURUSD", "base": "EUR", "quote": "USD",
        "total_score": 9.9, "macro_diff": 1.0, "op_diff": 1.0,
        "rec": "COMPRA (BUY)", "conviction": "ALTA", "thesis": "...",
    }]
    series = {c: np.array([0.0, 0.15]) for c in css.CURRENCIES}

    with patch.object(engine, "connect_mt5", return_value=True), \
         patch.object(css, "DB_STANDARD_FILE", str(tmp_path / "css_standard.json")), \
         patch.object(
             css, "calculate_full_css",
             return_value=(series, [0, 1], {}, {"status": "clean"}),
         ), \
         patch.object(css, "evaluate_currency_confluence", return_value=fake_confluence), \
         patch.object(css, "analyze_tf_triad", return_value=fake_triad), \
         patch.object(css, "evaluate_28_pairs_confluence", return_value=fake_pairs), \
         patch.object(css, "detect_currency_crossovers", return_value={"timeframes": {}}):
        result = engine.update_data(force=True)

    assert result["currencies"], "payload sem moedas — mocks não cobriram o caminho live"
    currency = result["currencies"][0]
    assert currency["total_score"] == 4.2

    leaked_diagnostics = {
        "score_total", "macro_bias", "vectors", "base_vectors", "maturities",
        "penalties", "weighted_score", "ref_dt_brt", "macro", "operational",
        "aligned_up_count", "aligned_dn_count", "aligned_flat_count",
    }
    assert not leaked_diagnostics & currency.keys(), (
        "diagnóstico interno do motor vazou para o payload público de moeda: "
        f"{leaked_diagnostics & currency.keys()}"
    )

    assert result["pairs"], "payload sem pares — mocks não cobriram o caminho live"
    pair = result["pairs"][0]
    assert pair["total_score"] == 9.9
    assert pair["total_score"] != currency["total_score"], (
        "pairs[].total_score não pode ser confundido com currencies[].total_score"
        " — são escalas e fontes diferentes (ranking de pares vs. score_total"
        " da matriz por moeda)"
    )


def _dict_key_accesses(source):
    """(var_name, key) para todo `var.get("key", ...)` e `var["key"]` do
    módulo, sem depender do estilo de aspas ou de indexação por atributo vs.
    colchete — achado conjunto herdr-review mfc-56 (mfc-rev MFC56-04,
    mfc-rev-2 MFC56-02): uma checagem por substring literal (`"c.get('x'"`)
    não pega uma regressão que troca o estilo de aspas ou usa `c["x"]`."""
    tree = ast.parse(source)
    accesses = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            accesses.append((node.func.value.id, node.args[0].value))
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            accesses.append((node.value.id, node.slice.value))
    return accesses


def test_daily_report_format_separates_currency_trade_bias_from_pair_total_score():
    """Asserção do formato do relatório diário (plano Port A, Fase C): a seção
    por moeda usa `trade_bias` como rótulo e não deve exibir `total_score` da
    moeda (diagnóstico interno não impresso), enquanto a seção dos 28 pares
    deve continuar exibindo `total_score` do ranking de pares. Regressão por
    inspeção de fonte (via AST, não substring — ver `_dict_key_accesses`), no
    mesmo espírito de TestDailyRoutineDoesNotDuplicateSignalWrite
    (tests/test_portfolio_safety.py), porque `run_daily_routine` dispara
    matplotlib/Telegram/MT5 e não deve ser executada em teste; e não
    modificamos `daily_css_routine.py`, que é uma dependência do Port A
    protegida como byte-idêntica ao `HEAD` (ver plano, seção de
    rastreabilidade)."""
    daily_routine_path = (
        Path(css.__file__).resolve().parent.parent / "daily_css_routine.py"
    )
    source = daily_routine_path.read_text(encoding="utf-8")
    accesses = _dict_key_accesses(source)
    currency_keys = {key for var, key in accesses if var == "c"}
    pair_keys = {key for var, key in accesses if var == "p"}

    assert "trade_bias" in currency_keys, (
        "a seção por moeda do relatório diário parou de usar trade_bias como "
        "rótulo de decisão"
    )
    assert "total_score" not in currency_keys, (
        "o relatório diário passou a expor total_score da moeda — esse campo "
        "é o alias público de score_total e não deve aparecer na seção "
        "estruturada por moeda"
    )
    assert "total_score" in pair_keys, (
        "a tabela dos 28 pares parou de exibir total_score do ranking de "
        "pares"
    )


def test_degraded_timeframe_snapshot_is_not_served_as_live(tmp_path):
    engine = object.__new__(css.CSSDataEngine)
    engine.cache_standard = {}
    engine.cache_gauss = {}
    engine.last_update_standard = None
    engine.last_update_gauss = None
    engine.last_error = None
    fallback = {"mt5_connected": False, "source": "fallback"}
    real_snapshot = Path(css.DB_STANDARD_FILE).read_bytes()
    quality = {
        "status": "degraded",
        "required_full_history_bars": 120,
        "short_history_pairs": ["EURUSD"],
        "common_history_bars": 30,
    }

    def degraded_result(*_args, **_kwargs):
        return (
            {c: np.array([0.0]) for c in css.CURRENCIES},
            [1],
            {},
            quality,
        )

    with patch.object(engine, "connect_mt5", return_value=True), \
         patch.object(css, "TIMEFRAMES_CONFIG", [("H1", 1)]), \
         patch.object(css, "DB_STANDARD_FILE", str(tmp_path / "css_standard.json")), \
         patch.object(css, "calculate_full_css", side_effect=degraded_result), \
         patch.object(engine, "_generate_fallback_data", return_value=fallback), \
         patch.object(css.time, "time", return_value=789.0):
        result = engine.update_data(force=True)

    assert result["mt5_connected"] is False
    assert result["snapshot_quality"]["status"] == "incomplete"
    assert result["snapshot_quality"]["missing_timeframes"] == ["H1"]
    assert Path(css.__file__).resolve().parent.parent.joinpath("data", "css_standard.json").read_bytes() == real_snapshot
