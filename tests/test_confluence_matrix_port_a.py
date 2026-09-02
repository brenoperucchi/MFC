import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import pandas as pd

from agents.confluence_engine import (
    BRT,
    _calculate_tf_vector,
    _get_tf_maturity,
    evaluate_currency_confluence,
)
from scripts.backtest_engine_compare import (
    _get_tf_maturity as _get_upstream_tf_maturity,
    _normalize_window_end,
    _aggregate_pass_summaries,
    _data_snapshot_digest,
    _disagreement_by_currency_summary,
    _engine_summary,
    _exposure_summary,
    _overall_quality_status,
    _quality_status,
    _turnover_summary,
    compare,
    evaluate_currency_confluence_5tf,
)
import scripts.backtest_canonical as canonical
import scripts.backtest_engine_compare as engine_compare


def _triad(score, diff):
    return {"score": score, "diff": diff}


def _macro(mn, w1, d1):
    return {
        "mn_triad": mn,
        "w1_triad": w1,
        "d1_triad": d1,
    }


def _operational(h4, h1):
    return {
        "h4_triad": h4,
        "h1_triad": h1,
        "has_divergence": False,
        "divergence_alert": "NENHUMA",
    }


class TestPortAVectors(unittest.TestCase):
    def test_local_stop_and_equilibrium_boundaries_are_preserved(self):
        self.assertEqual(_calculate_tf_vector("D1", _triad(0.199, 0.10)), 1.0)
        self.assertEqual(_calculate_tf_vector("D1", _triad(0.20, 0.10)), 1.5)
        self.assertEqual(_calculate_tf_vector("D1", _triad(0.05, 0.01)), 0.4)
        self.assertEqual(_calculate_tf_vector("D1", _triad(0.051, 0.01)), 0.5)
        self.assertEqual(_calculate_tf_vector("D1", _triad(-0.20, 0.04)), 2.0)

    def test_maturity_uses_explicit_brt_reference(self):
        monday = datetime(2026, 8, 24, 21, tzinfo=BRT)
        tuesday_seen_as_monday_in_brt = datetime(
            2026, 8, 25, 2, tzinfo=timezone.utc
        )
        self.assertEqual(_get_tf_maturity("W1", monday), 0.20)
        self.assertEqual(_get_tf_maturity("W1", tuesday_seen_as_monday_in_brt), 0.20)
        self.assertEqual(_get_tf_maturity("MN1", datetime(2026, 8, 25, tzinfo=BRT)), 0.83)

    def test_missing_reference_is_rejected(self):
        with self.assertRaises(TypeError):
            evaluate_currency_confluence("AUD", [], [], [], [], [], ref_dt=None)
        with self.assertRaises(TypeError):
            evaluate_currency_confluence("AUD", [], [], [], [], [], ref_dt="2026-08-25")

        with self.assertRaises(TypeError):
            _get_upstream_tf_maturity("W1", None)
        with self.assertRaises(TypeError):
            evaluate_currency_confluence_5tf("AUD", [], [], [], [], [])

    def test_backtest_window_endpoint_is_normalized_to_brt(self):
        utc_endpoint = datetime(2026, 7, 16, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(
            _normalize_window_end(utc_endpoint),
            datetime(2026, 7, 15, 21, 0),
        )
        self.assertEqual(
            _get_upstream_tf_maturity("W1", utc_endpoint),
            _get_upstream_tf_maturity("W1", utc_endpoint.astimezone(BRT)),
        )

    def test_oos_role_requires_explicit_non_overlapping_cutoff(self):
        with pytest.raises(ValueError, match="end_brt"):
            compare(days=1, sample_role="oos_disjoint")
        with pytest.raises(ValueError, match="log_note"):
            compare(
                days=1,
                end_brt=datetime(2026, 7, 16, 21, tzinfo=BRT),
                development_start_brt=datetime(2026, 7, 16, 21, tzinfo=BRT),
                sample_role="oos_disjoint",
            )

    def test_backtest_loader_propagates_and_rejects_degraded_css_history(self):
        scores = {ccy: [0.0, 0.1] for ccy in canonical.CURRENCIES}

        def fake_calculate(tf_val, **_kwargs):
            quality = {
                "status": "degraded" if tf_val == "MN1" else "clean",
                "required_full_history_bars": 179 if tf_val == "MN1" else 229,
                "short_history_pairs": ["EURUSD"] if tf_val == "MN1" else [],
            }
            return scores, ["2026-08-01 00:00", "2026-08-02 00:00"], {}, quality

        with patch.object(canonical, "get_tf_constant", side_effect=lambda name: name), \
                patch.object(canonical, "calculate_full_css", side_effect=fake_calculate):
            self.assertIsNone(canonical.load_series(require_clean=True))
            loaded = canonical.load_series(require_clean=False)

        self.assertEqual(loaded["MN1"]["quality"]["status"], "degraded")
        self.assertEqual(loaded["MN1"]["quality"]["short_history_pairs"], ["EURUSD"])

    def test_oos_boundaries_must_be_canonical_21_brt_before_mt5(self):
        with patch.object(engine_compare, "ensure_mt5", side_effect=AssertionError("não deveria conectar")):
            with pytest.raises(ValueError, match="21:00 BRT"):
                compare(
                    days=1,
                    end_brt=datetime(2026, 7, 16, 21, 30, tzinfo=BRT),
                    development_start_brt=datetime(2026, 7, 16, 21, tzinfo=BRT),
                    sample_role="oos_disjoint",
                    log_note="boundary test",
                )

    def test_oos_configuration_guard_runs_before_mt5(self):
        with patch.dict(engine_compare.os.environ, {"MFC_BACKTEST_TERMINAL_ISOLATED": "0"}), \
                patch.object(engine_compare, "ensure_mt5") as ensure:
            with pytest.raises(RuntimeError, match="MFC_BACKTEST_TERMINAL_ISOLATED"):
                compare(
                    days=1,
                    end_brt=datetime(2026, 7, 16, 21, tzinfo=BRT),
                    development_start_brt=datetime(2026, 7, 16, 21, tzinfo=BRT),
                    sample_role="oos_disjoint",
                    log_note="guard order test",
                )
            ensure.assert_not_called()

    def test_oos_runtime_guard_rejects_wrong_terminal_or_non_demo_account(self):
        expected = r"D:\MetaTradersWSL\mfc-backtest\terminal64.exe"
        for observed_path, trade_mode, error in (
            (r"D:\MetaTradersWSL\other\terminal64.exe", 0, "terminal diferente"),
            (r"D:\MetaTradersWSL\mfc-backtest", 1, "conta demo"),
        ):
            fake_mt5 = SimpleNamespace(
                ACCOUNT_TRADE_MODE_DEMO=0,
                terminal_info=lambda path=observed_path: SimpleNamespace(path=path),
                account_info=lambda mode=trade_mode: SimpleNamespace(trade_mode=mode),
            )
            with patch.object(engine_compare, "MT5_PATH", expected), \
                    patch.object(engine_compare, "MT5_AVAILABLE", True), \
                    patch.object(engine_compare, "mt5", fake_mt5), \
                    patch.object(engine_compare, "_assert_oos_terminal_configuration"), \
                    patch.object(engine_compare, "ensure_mt5", return_value=True):
                with pytest.raises(RuntimeError, match=error):
                    compare(
                        days=1,
                        end_brt=datetime(2026, 7, 16, 21, tzinfo=BRT),
                        development_start_brt=datetime(2026, 7, 16, 21, tzinfo=BRT),
                        sample_role="oos_disjoint",
                        log_note="runtime guard test",
                    )

    def test_valid_oos_runtime_guard_advances_to_contract_gate(self):
        expected = r"D:\MetaTradersWSL\mfc-backtest\terminal64.exe"
        fake_mt5 = SimpleNamespace(
            ACCOUNT_TRADE_MODE_DEMO=0,
            terminal_info=lambda: SimpleNamespace(path=r"D:\MetaTradersWSL\mfc-backtest"),
            account_info=lambda: SimpleNamespace(trade_mode=0),
        )
        with patch.object(engine_compare, "MT5_PATH", expected), \
                patch.object(engine_compare, "MT5_AVAILABLE", True), \
                patch.object(engine_compare, "mt5", fake_mt5), \
                patch.object(engine_compare, "_assert_oos_terminal_configuration"), \
                patch.object(engine_compare, "ensure_mt5", return_value=True), \
                patch.object(engine_compare, "check_contract_size_consistency", return_value={"valid_for_pnl": True}) as contract, \
                patch.object(engine_compare, "load_series", return_value=None) as loader:
            assert compare(
                days=1,
                end_brt=datetime(2026, 7, 16, 21, tzinfo=BRT),
                development_start_brt=datetime(2026, 7, 16, 21, tzinfo=BRT),
                sample_role="oos_disjoint",
                log_note="runtime guard valid test",
            ) == 1
        contract.assert_called_once_with(strict=True)
        loader.assert_called_once_with(
            require_clean=True, use_histdata_mn1_warmup=False,
            window_start_brt=datetime(2026, 7, 15, 21, 0),
        )
        with pytest.raises(ValueError, match="sobrepõe"):
            compare(
                days=1,
                end_brt=datetime(2026, 7, 17, 21, tzinfo=BRT),
                development_start_brt=datetime(2026, 7, 16, 21, tzinfo=BRT),
                sample_role="oos_disjoint",
            )

    def test_data_snapshot_digest_binds_css_history_quality(self):
        times = pd.date_range(start="2026-08-01", periods=2, freq="h")
        scores = {ccy: [0.0, 0.1] for ccy in canonical.CURRENCIES}
        clean = {
            tf: {"times": times, "scores": scores, "quality": {"status": "clean"}}
            for tf in canonical.TFS
        }
        degraded = {
            tf: {"times": times, "scores": scores, "quality": {"status": "clean"}}
            for tf in canonical.TFS
        }
        degraded["MN1"]["quality"] = {
            "status": "degraded",
            "short_history_pairs": ["EURUSD"],
        }

        self.assertNotEqual(
            _data_snapshot_digest(clean, {}),
            _data_snapshot_digest(degraded, {}),
        )

    def test_overall_quality_status_keeps_degraded_css_history_visible(self):
        stats = {
            "baseline": {
                "degraded_baskets": 0,
                "skipped_missing_price": 0,
                "swap_unmodeled_baskets": 0,
            }
        }
        self.assertEqual(
            _overall_quality_status("degraded", stats, ["baseline"]),
            "degraded",
        )

    def test_oos_producer_coverage_gate_rejects_each_incomplete_case(self):
        names = list(engine_compare.ENGINES)
        active = {name: {ccy: 0 for ccy in engine_compare.CURRENCIES} for name in names}

        def fake_pass(coverage, dirty_name=None, dirty_metric=None):
            stats = {
                name: {
                    "baskets": 1,
                    "skipped_missing_price": 0,
                    "degraded_baskets": 0,
                    "swap_unmodeled_baskets": 0,
                }
                for name in names
            }
            if dirty_name:
                stats[dirty_name]["degraded_baskets"] = 1
            if dirty_metric == "baskets":
                stats[names[0]]["baskets"] = 0
            if dirty_metric == "skipped_missing_price":
                stats[names[0]]["skipped_missing_price"] = 1
            disagree_by_currency = {ccy: 0 for ccy in engine_compare.CURRENCIES}
            exposure_series = {name: [] for name in names}
            decision_matrix = {}
            return (stats, 0, 0, [], active, coverage["evaluated_nights"], [],
                    disagree_by_currency, exposure_series, decision_matrix, coverage)

        base = {
            "candidate_nights": 30,
            "evaluated_nights": 30,
            "skipped_no_verdict": 0,
            "skipped_invalid_exit": 0,
            "evaluated_dates_brt": [f"2026-06-{index + 1:02d}T21:00:00-03:00" for index in range(30)],
            "price_missing_points": [],
        }
        cases = []
        coverage = dict(base)
        coverage["candidate_nights"] = 29
        coverage["evaluated_nights"] = 29
        coverage["evaluated_dates_brt"] = base["evaluated_dates_brt"][:29]
        cases.append((coverage, None, None))
        coverage = dict(base)
        coverage["evaluated_nights"] = 29
        coverage["evaluated_dates_brt"] = base["evaluated_dates_brt"][:29]
        cases.append((coverage, None, None))
        for key, value in (("skipped_no_verdict", 1), ("skipped_invalid_exit", 1)):
            coverage = dict(base)
            coverage[key] = value
            cases.append((coverage, None, None))
        coverage = dict(base)
        coverage["price_missing_points"] = ["date:EURUSD:entry"]
        cases.append((coverage, None, None))
        cases.append((base, names[0], None))
        cases.append((base, None, "baskets"))
        cases.append((base, None, "skipped_missing_price"))

        for coverage, dirty_name, dirty_metric in cases:
            with self.subTest(coverage=coverage, dirty_name=dirty_name, dirty_metric=dirty_metric):
                with patch.object(engine_compare, "MT5_PATH", r"D:\MetaTradersWSL\mfc-backtest\terminal64.exe"), \
                        patch.object(engine_compare, "MT5_AVAILABLE", True), \
                        patch.object(engine_compare, "_assert_oos_terminal_configuration"), \
                        patch.object(engine_compare, "_assert_oos_terminal_runtime"), \
                        patch.object(engine_compare, "ensure_mt5", return_value=True), \
                        patch.object(engine_compare, "check_contract_size_consistency", return_value={"valid_for_pnl": True}), \
                        patch.object(engine_compare, "load_series", return_value={"loaded": True}), \
                        patch.object(engine_compare, "load_h1_prices", return_value={pair: object() for pair in canonical.ALL_28_PAIRS}), \
                        patch.object(engine_compare, "_data_snapshot_digest", return_value="a" * 64), \
                        patch.object(engine_compare, "_one_pass", return_value=fake_pass(coverage, dirty_name, dirty_metric)):
                    with pytest.raises(RuntimeError, match="cobertura|reconstrução limpa"):
                        compare(
                            days=1,
                            end_brt=datetime(2026, 7, 16, 21, tzinfo=BRT),
                            development_start_brt=datetime(2026, 7, 16, 21, tzinfo=BRT),
                            sample_role="oos_disjoint",
                            log_note="producer coverage gate test",
                        )

    def test_oos_producer_rejects_coverage_that_differs_between_passes(self):
        names = list(engine_compare.ENGINES)
        stats = {
            name: {
                "baskets": 1,
                "skipped_missing_price": 0,
                "degraded_baskets": 0,
                "swap_unmodeled_baskets": 0,
            }
            for name in names
        }
        active = {name: {ccy: 0 for ccy in engine_compare.CURRENCIES} for name in names}
        first = {
            "candidate_nights": 30, "evaluated_nights": 30,
            "skipped_no_verdict": 0, "skipped_invalid_exit": 0,
            "evaluated_dates_brt": [f"2026-06-{index + 1:02d}T21:00:00-03:00" for index in range(30)],
            "price_missing_points": [],
        }
        second = dict(first)
        second["evaluated_nights"] = 29
        disagree_by_currency = {ccy: 0 for ccy in engine_compare.CURRENCIES}
        exposure_series = {name: [] for name in names}
        result = lambda coverage: (
            stats, 0, 0, [], active, coverage["evaluated_nights"], [],
            disagree_by_currency, exposure_series, {}, coverage,
        )
        with patch.object(engine_compare, "MT5_PATH", r"D:\MetaTradersWSL\mfc-backtest\terminal64.exe"), \
                patch.object(engine_compare, "MT5_AVAILABLE", True), \
                patch.object(engine_compare, "_assert_oos_terminal_configuration"), \
                patch.object(engine_compare, "_assert_oos_terminal_runtime"), \
                patch.object(engine_compare, "ensure_mt5", return_value=True), \
                patch.object(engine_compare, "check_contract_size_consistency", return_value={"valid_for_pnl": True}), \
                patch.object(engine_compare, "load_series", return_value={"loaded": True}), \
                patch.object(engine_compare, "load_h1_prices", return_value={pair: object() for pair in canonical.ALL_28_PAIRS}), \
                patch.object(engine_compare, "_data_snapshot_digest", return_value="a" * 64), \
                patch.object(engine_compare, "_one_pass", side_effect=[result(first), result(second)]):
            with pytest.raises(RuntimeError, match="cobertura divergente"):
                compare(
                    days=1,
                    runs=2,
                    end_brt=datetime(2026, 7, 16, 21, tzinfo=BRT),
                    development_start_brt=datetime(2026, 7, 16, 21, tzinfo=BRT),
                    sample_role="oos_disjoint",
                    log_note="coverage consistency test",
                )

    def test_compare_rejects_non_positive_runs_before_mt5(self):
        with pytest.raises(ValueError, match="runs"):
            compare(days=1, runs=0)
        with pytest.raises(ValueError, match="runs"):
            compare(days=1, runs=-1)

    def test_run_summary_keeps_min_max_mean_when_cost_passes_differ(self):
        summaries = [
            {
                "engines": {"port": {
                    "bruto": 10.0, "custo": 2.0, "spread": 1.5, "swap": 0.5,
                    "liquido": 8.0,
                    "baskets": 1, "degraded_baskets": 0,
                    "swap_unmodeled_baskets": 0, "skipped_missing_price": 0,
                }},
                "paired_net_delta_per_night": {"mean": 8.0},
            },
            {
                "engines": {"port": {
                    "bruto": 10.0, "custo": 6.0, "spread": 1.5, "swap": 4.5,
                    "liquido": 4.0,
                    "baskets": 1, "degraded_baskets": 0,
                    "swap_unmodeled_baskets": 0, "skipped_missing_price": 0,
                }},
                "paired_net_delta_per_night": {"mean": 4.0},
            },
        ]
        aggregate = _aggregate_pass_summaries(summaries, ["port"])

        assert aggregate["by_engine"]["port"]["custo"] == {
            "min": 2.0, "max": 6.0, "mean": 4.0
        }
        # Achado herdr-review mfc-62 (MFC62-02/`mfc-rev`): a decomposição
        # spread/swap precisa sobreviver à agregação de runs=N igual às
        # outras métricas de custo — spread é constante entre as duas
        # passadas (mesmas pernas, mesmo lote), só swap varia.
        assert aggregate["by_engine"]["port"]["spread"] == {
            "min": 1.5, "max": 1.5, "mean": 1.5
        }
        assert aggregate["by_engine"]["port"]["swap"] == {
            "min": 0.5, "max": 4.5, "mean": 2.5
        }
        assert aggregate["by_engine"]["port"]["liquido"] == {
            "min": 4.0, "max": 8.0, "mean": 6.0
        }
        assert aggregate["paired_net_delta_per_night_mean"] == {
            "min": 4.0, "max": 8.0, "mean": 6.0, "n_runs": 2
        }

    def test_engine_summary_includes_spread_and_swap_in_the_main_record(self):
        """Achado herdr-review mfc-63 (MFC63-01/`mfc-rev`): a correção
        anterior (fe0f1ba) levou spread/swap até _pass_summary()/
        runs_summary, mas o registro PRINCIPAL de compare() (o objeto
        "engines" que qualquer consumidor comum lê, não o aninhado em
        runs_summary) era montado por um bloco inline SEPARADO que
        continuava sem os dois campos — o teste de _aggregate_pass_summaries
        acima nunca cobria esse segundo caminho. _engine_summary() foi
        extraída pra ficar testável do mesmo jeito que _pass_summary()."""
        stats = {
            "port": {
                "pnl": 10.0, "cost": 4.0, "spread": 3.0, "swap": 1.0,
                "baskets": 2, "nights_with_baskets": 1, "wins": 1,
                "basket_wins": 2, "net_per_basket": [3.0, 3.0],
                "degraded_baskets": 0, "swap_unmodeled_baskets": 0,
                "skipped_missing_price": 0,
            }
        }
        active_signal_counts = {"port": {"EUR": 2, "USD": 0}}
        summary = _engine_summary("port", stats, active_signal_counts)
        assert summary["spread"] == 3.0
        assert summary["swap"] == 1.0
        assert summary["custo"] == 4.0
        assert summary["liquido"] == 6.0
        assert summary["active_signals"] == 2

    def test_basket_pnl_uses_historical_rate_for_cross_pair_not_hardcoded_table(self):
        """Achado herdr-review mfc-64 (MFC64-01/`mfc-rev`, P1): _basket_pnl()
        é a função que produz o PnL que vira evidência OOS persistida
        (journal_seq=25 incluído) — sem rates_dict, convert_pnl_to_usd() caía
        na tabela hardcoded de web/history_tracker.py (NZD=0.60) pra
        qualquer perna cotada em NZD, mesmo com o preço histórico H1 do
        cross USD/NZD disponível em `prices`. Mesma classe de bug já
        corrigida em measure_composition_effect.py na mfc-62/63 — não
        bastou, porque é uma função diferente."""
        from web.history_tracker import convert_pnl_to_usd

        srv_dt = datetime(2026, 1, 5, 21, 0, 0)
        exit_srv = datetime(2026, 1, 6, 8, 0, 0)

        class _FakeSeries:
            def __init__(self, entry, exit_):
                self.entry, self.exit_ = entry, exit_

            def asof(self, dt):
                return self.entry if dt == srv_dt else self.exit_

        # GBPNZD BUY: entry=2.000, exit=2.010 -- cotado em NZD. Taxa
        # histórica NZDUSD=0.55, deliberadamente diferente do fallback
        # hardcoded (0.60), pra provar qual dos dois foi usado.
        prices = {
            "GBPNZD": _FakeSeries(2.000, 2.010),
            "NZDUSD": _FakeSeries(0.55, 0.55),
        }
        fake_legs = [{"pair": "GBPNZD", "action": "BUY"}]

        class _FakeCostModel:
            def __init__(self, lot):
                self.last_basket_spread = 0.0
                self.last_basket_swap = 0.0
                self.last_basket_degraded = set()
                self.last_basket_swap_unmodeled = set()

            def basket(self, ccy, bias, leg_lots=None):
                return 0.0

        with patch.object(engine_compare, "get_portfolio_pairs", return_value=fake_legs), \
             patch.object(engine_compare, "CostModel", _FakeCostModel):
            result = engine_compare._basket_pnl("GBP", "COMPRA", prices, srv_dt, exit_srv)

        gross = result[0]
        rate_correct_gross, _ = convert_pnl_to_usd(
            "GBPNZD", "BUY", 2.000, 2.010, canonical.LOT, rates_dict={"NZDUSD": 0.55})
        hardcoded_gross, _ = convert_pnl_to_usd(
            "GBPNZD", "BUY", 2.000, 2.010, canonical.LOT)  # sem rates_dict -> cai no 0.60
        assert gross == rate_correct_gross
        assert gross != hardcoded_gross, (
            "_basket_pnl() usou a tabela hardcoded em vez do preço histórico H1")

    def test_unmodeled_swap_is_not_reported_as_clean(self):
        stats = {
            "degraded_baskets": 0,
            "skipped_missing_price": 0,
            "swap_unmodeled_baskets": 1,
        }
        assert _quality_status(stats) == "partial_model"

    def test_evaluate_at_rejects_when_fewer_than_30_closed_bars(self):
        times = pd.date_range(
            start="2026-08-01", periods=30, freq="h"
        )
        series = {
            tf: {"times": times, "scores": {}}
            for tf in canonical.TFS
        }
        with patch.object(canonical, "_closed_bar_index", return_value=29), \
                patch.object(canonical, "is_market_session_valid", return_value=True):
            assert canonical.evaluate_at(
                series, times[-1].to_pydatetime(),
                datetime(2026, 8, 2, 21, tzinfo=BRT),
            ) is None

    def test_oos_contract_gate_fails_closed_without_mt5(self):
        with patch.object(canonical, "MT5_AVAILABLE", False), \
             patch.object(canonical, "mt5", None):
            with pytest.raises(RuntimeError, match="contrato MT5"):
                canonical.check_contract_size_consistency(strict=True)


class TestItem6VectorMetrics(unittest.TestCase):
    """Retomada do item 6 (matriz 5-TF em shadow mode, reconciliação
    Miqueias): "vetores/decisão/exposição/turnover", não só PnL agregado.
    _turnover_summary/_exposure_summary/_disagreement_by_currency_summary
    são funções puras (sem MT5), testáveis diretamente com uma matriz de
    decisão sintética."""

    def test_turnover_counts_zero_flips_for_a_stable_currency(self):
        names = ["engine_a"]
        decision_matrix = {
            "2026-07-16": {"USD": {"engine_a": "COMPRA"}},
            "2026-07-19": {"USD": {"engine_a": "COMPRA"}},
            "2026-07-20": {"USD": {"engine_a": "COMPRA"}},
        }
        # completa as outras 7 moedas com um valor constante — não devem
        # contribuir flip nenhum, só ruído se o código não filtrar direito.
        for date in decision_matrix:
            for ccy in engine_compare.CURRENCIES:
                decision_matrix[date].setdefault(ccy, {"engine_a": "NEUTRO"})
        summary = _turnover_summary(decision_matrix, names)
        self.assertEqual(summary["engine_a"]["by_currency"]["USD"]["flips"], 0)
        self.assertEqual(summary["engine_a"]["by_currency"]["USD"]["night_pairs"], 2)
        self.assertEqual(summary["engine_a"]["by_currency"]["USD"]["flip_rate"], 0.0)

    def test_turnover_counts_every_state_change_for_a_flip_flopping_currency(self):
        names = ["engine_a"]
        sequence = ["COMPRA", "VENDA", "VENDA", "NEUTRO", "COMPRA"]  # 3 mudanças
        decision_matrix = {}
        for index, bias in enumerate(sequence):
            date = f"2026-07-{16 + index:02d}"
            decision_matrix[date] = {ccy: {"engine_a": "NEUTRO"} for ccy in engine_compare.CURRENCIES}
            decision_matrix[date]["GBP"] = {"engine_a": bias}
        summary = _turnover_summary(decision_matrix, names)
        gbp = summary["engine_a"]["by_currency"]["GBP"]
        self.assertEqual(gbp["flips"], 3)
        self.assertEqual(gbp["night_pairs"], 4)
        self.assertEqual(gbp["flip_rate"], 0.75)

    def test_turnover_totals_sum_across_all_currencies(self):
        names = ["engine_a"]
        decision_matrix = {
            "2026-07-16": {ccy: {"engine_a": "NEUTRO"} for ccy in engine_compare.CURRENCIES},
            "2026-07-19": {ccy: {"engine_a": "NEUTRO"} for ccy in engine_compare.CURRENCIES},
        }
        decision_matrix["2026-07-19"]["USD"] = {"engine_a": "COMPRA"}  # 1 flip (USD)
        decision_matrix["2026-07-19"]["EUR"] = {"engine_a": "VENDA"}   # 1 flip (EUR)
        decision_matrix["2026-07-16"]["USD"] = {"engine_a": "NEUTRO"}
        decision_matrix["2026-07-16"]["EUR"] = {"engine_a": "NEUTRO"}
        summary = _turnover_summary(decision_matrix, names)
        # 8 moedas x 1 par de noites cada = 8 pares totais; 2 flips (USD, EUR)
        self.assertEqual(summary["engine_a"]["flips_total"], 2)
        self.assertEqual(summary["engine_a"]["night_pairs_total"], 8)
        self.assertEqual(summary["engine_a"]["flip_rate"], 0.25)

    def test_turnover_handles_empty_decision_matrix_without_dividing_by_zero(self):
        summary = _turnover_summary({}, ["engine_a"])
        self.assertEqual(summary["engine_a"]["flips_total"], 0)
        self.assertIsNone(summary["engine_a"]["flip_rate"])
        for ccy_summary in summary["engine_a"]["by_currency"].values():
            self.assertIsNone(ccy_summary["flip_rate"])

    def test_exposure_reports_mean_max_and_nights_with_any_activity(self):
        exposure_series = {"engine_a": [0, 2, 4, 0, 3]}
        summary = _exposure_summary(exposure_series, ["engine_a"])
        e = summary["engine_a"]
        self.assertEqual(e["max_open_currencies"], 4)
        self.assertEqual(e["nights_with_any_exposure"], 3)
        self.assertEqual(e["nights_total"], 5)
        self.assertAlmostEqual(e["mean_open_currencies"], 1.8)

    def test_exposure_handles_no_nights_without_dividing_by_zero(self):
        summary = _exposure_summary({"engine_a": []}, ["engine_a"])
        e = summary["engine_a"]
        self.assertIsNone(e["mean_open_currencies"])
        self.assertIsNone(e["max_open_currencies"])
        self.assertEqual(e["nights_with_any_exposure"], 0)

    def test_disagreement_by_currency_computes_rate_per_currency(self):
        disagree_by_currency = {ccy: 0 for ccy in engine_compare.CURRENCIES}
        disagree_by_currency["GBP"] = 15
        disagree_by_currency["JPY"] = 3
        summary = _disagreement_by_currency_summary(disagree_by_currency, nights_evaluated=30)
        self.assertEqual(summary["GBP"]["disagree_nights"], 15)
        self.assertAlmostEqual(summary["GBP"]["disagree_rate"], 0.5)
        self.assertAlmostEqual(summary["JPY"]["disagree_rate"], 0.1)
        self.assertEqual(summary["USD"]["disagree_nights"], 0)
        self.assertEqual(summary["USD"]["disagree_rate"], 0.0)

    def test_disagreement_by_currency_handles_zero_nights_without_dividing_by_zero(self):
        summary = _disagreement_by_currency_summary({"USD": 0}, nights_evaluated=0)
        self.assertIsNone(summary["USD"]["disagree_rate"])


class TestPortAMacroDecision(unittest.TestCase):
    def test_macro_context_changes_decision_while_operational_data_is_fixed(self):
        d1 = _triad(-0.10, -0.10)  # -1.0: contra-fluxo no cenário macro comprador
        h4 = _triad(0.10, 0.10)    # +1.0
        h1 = _triad(0.10, 0.10)    # +1.0: gatilho de retomada
        neutral_macro = _macro(_triad(0.0, 0.0), _triad(0.0, 0.0), d1)
        bullish_macro = _macro(_triad(0.30, 0.10), _triad(0.20, 0.10), d1)
        operational = _operational(h4, h1)
        ref_dt = datetime(2026, 8, 25, 21, tzinfo=BRT)

        with patch("agents.confluence_engine.analyze_macro_currency", return_value=neutral_macro), \
             patch("agents.confluence_engine.analyze_operational_currency", return_value=operational):
            neutral = evaluate_currency_confluence(
                "AUD", [], [], [], [], [], ref_dt=ref_dt
            )

        with patch("agents.confluence_engine.analyze_macro_currency", return_value=bullish_macro), \
             patch("agents.confluence_engine.analyze_operational_currency", return_value=operational):
            bullish = evaluate_currency_confluence(
                "AUD", [], [], [], [], [], ref_dt=ref_dt
            )

        self.assertEqual(neutral["trade_bias"], "NEUTRO")
        self.assertEqual(bullish["trade_bias"], "COMPRA")
        self.assertGreater(bullish["macro_bias"], 0.30)
        self.assertEqual(bullish["penalties"]["D1"], 0.40)
        self.assertEqual(bullish["penalties"]["H4"], 1.0)
        self.assertEqual(bullish["ref_dt_brt"], ref_dt.isoformat())
        self.assertIn(bullish["trade_bias"], {"COMPRA", "VENDA", "NEUTRO"})

    def test_score_uses_544d660_normalization_without_clamp(self):
        # O vetor +2.0 é possível na reversão explosiva de fundo. Cinco
        # vetores assim somam 18.0; o denominador normativo do 544d660 é 13.5
        # e não há clamp nesta etapa.
        macro = _macro(
            _triad(-0.30, 0.10),
            _triad(-0.30, 0.10),
            _triad(-0.30, 0.10),
        )
        operational = _operational(_triad(-0.30, 0.10), _triad(-0.30, 0.10))
        with patch("agents.confluence_engine.analyze_macro_currency", return_value=macro), \
             patch("agents.confluence_engine.analyze_operational_currency", return_value=operational):
            result = evaluate_currency_confluence(
                "AUD", [], [], [], [], [], ref_dt=datetime(2026, 8, 30, 21, tzinfo=BRT)
            )
        self.assertEqual(result["weighted_score"], 18.0)
        self.assertEqual(result["score_total"], 13.33)


if __name__ == "__main__":
    unittest.main()
