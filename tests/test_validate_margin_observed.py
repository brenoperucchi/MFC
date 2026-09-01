"""Regressões do validador manual, sem conexão ou ordens MT5 reais."""

import importlib
import copy
import json
import re
import tempfile
import threading
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from io import StringIO
import pytest
from web.css_service import ALL_28_PAIRS
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


vmo = importlib.import_module("scripts.validate_margin_observed")
margin_calc = importlib.import_module("scripts.validate_margin_calc")
log = importlib.import_module("scripts._backtest_results_log")
canonical = importlib.import_module("scripts.backtest_canonical")
engine_compare = importlib.import_module("scripts.backtest_engine_compare")
composition = importlib.import_module("scripts.measure_composition_effect")


def fake_mt5():
    fake = MagicMock()
    fake.TRADE_RETCODE_DONE = 10009
    fake.TRADE_RETCODE_INVALID_FILL = 10030
    fake.TRADE_RETCODE_PLACED = 10008
    fake.TRADE_RETCODE_TIMEOUT = 10012
    fake.TRADE_RETCODE_CONNECTION = 10031
    fake.TRADE_RETCODE_DONE_PARTIAL = 10010
    fake.ORDER_FILLING_IOC = 1
    fake.ORDER_FILLING_FOK = 0
    fake.ORDER_FILLING_RETURN = 2
    fake.SYMBOL_FILLING_FOK = 1
    fake.SYMBOL_FILLING_IOC = 2
    fake.SYMBOL_TRADE_MODE_FULL = 4
    fake.SYMBOL_TRADE_EXECUTION_MARKET = 2
    fake.TRADE_ACTION_DEAL = 1
    fake.ORDER_TYPE_BUY = 0
    fake.ORDER_TYPE_SELL = 1
    fake.ORDER_TIME_GTC = 0
    fake.symbol_select.return_value = True
    return fake


def valid_info():
    return SimpleNamespace(
        trade_mode=4,
        visible=True,
        filling_mode=2,
        trade_exemode=0,
    )


def valid_tick():
    return SimpleNamespace(bid=1.1000, ask=1.1002)


def leg(pair="EURUSD", action="BUY"):
    return {"pair": pair, "action": action}


def test_ambiguous_first_response_is_never_resent():
    fake = fake_mt5()
    fake.order_send.return_value = SimpleNamespace(retcode=fake.TRADE_RETCODE_PLACED)
    with patch.object(vmo, "mt5", fake):
        result = vmo.send_with_fallback({"type_filling": fake.ORDER_FILLING_IOC})
    assert result.retcode == fake.TRADE_RETCODE_PLACED
    fake.order_send.assert_called_once()


def test_ambiguous_response_reconciles_pending_order_without_resend():
    fake = fake_mt5()
    fake.order_send.return_value = SimpleNamespace(retcode=fake.TRADE_RETCODE_PLACED)
    fake.positions_get.return_value = []
    fake.orders_get.return_value = [SimpleNamespace(
        magic=123,
        symbol="EURUSD",
        ticket=88,
        volume_current=0.01,
    )]
    with patch.object(vmo, "mt5", fake), patch.object(vmo, "MT5_AVAILABLE", True):
        execution = vmo._confirmed_execution("EURUSD", 123)
    assert execution == {"kind": "PENDING_ORDER_CONFIRMED", "volume": 0.01}
    fake.order_send.assert_not_called()


def test_orphan_test_magics_are_reported_but_explicit_cleanup_is_parseable():
    positions = [
        SimpleNamespace(magic=900099),
        SimpleNamespace(magic=vmo.RUN_MAGIC_BASE + 123),
        SimpleNamespace(magic=801001),
    ]
    assert vmo._orphan_test_magics(positions, vmo.RUN_MAGIC_BASE + 456) == [
        900099, vmo.RUN_MAGIC_BASE + 123,
    ]
    with patch.object(vmo.sys, "argv", [
        "validate_margin_observed.py", "--cleanup-magic", "900099",
    ]):
        ccy, bias, cleanup_magic, error = vmo._parse_cli()
    assert (ccy, bias, cleanup_magic, error) == ("CAD", "BUY", 900099, None)


def test_startup_refuses_old_pending_order_before_any_new_send():
    fake = fake_mt5()
    fake.ACCOUNT_TRADE_MODE_DEMO = 7
    fake.account_info.return_value = SimpleNamespace(
        margin_free=1000.0,
        trade_mode=7,
        trade_allowed=True,
        login=1,
        currency="USD",
    )
    fake.positions_get.return_value = []
    fake.orders_get.return_value = [SimpleNamespace(
        magic=vmo.RUN_MAGIC_BASE + 123,
        symbol="EURUSD",
        ticket=88,
        volume_current=0.01,
    )]
    legs = [leg(f"PAIR{i}") for i in range(7)]
    fake.symbol_info.return_value = valid_info()
    fake.symbol_info_tick.return_value = valid_tick()
    fake.order_calc_margin.return_value = 10.0
    fake.terminal_info.return_value = SimpleNamespace(path="C:/mfc-backtest")
    with patch.object(vmo, "mt5", fake), patch.object(vmo, "MT5_AVAILABLE", True), \
            patch.object(vmo, "MT5_PATH", "C:/mfc-backtest/terminal64.exe"), \
            patch.dict(vmo.os.environ, {"CSS_MT5_TERMINAL_PATH": "C:/mfc-backtest/terminal64.exe"}), \
            patch.object(vmo, "check_account_identity", return_value={"allowed": True}), \
            patch.object(vmo, "get_portfolio_pairs", return_value=legs), \
            patch.object(vmo, "to_broker_symbol", side_effect=lambda pair: pair), \
            patch.object(vmo.portfolio_executor, "_compute_catastrophic_sl", return_value=0.9), \
            patch.object(vmo, "_close_test_magic_positions", return_value={
                "confirmed": True, "closed": 0, "remaining": 0,
            }), \
            patch.object(vmo.sys, "argv", ["validate_margin_observed.py"]):
        result = vmo.main()
    assert result == 1
    fake.order_send.assert_not_called()
    fake.shutdown.assert_called_once()


def test_trade_allowed_is_fail_closed():
    assert vmo._account_trade_allowed(SimpleNamespace(trade_allowed=True)) is True
    assert vmo._account_trade_allowed(SimpleNamespace(trade_allowed=False)) is False
    assert vmo._account_trade_allowed(SimpleNamespace(trade_allowed=None)) is False
    assert vmo._account_trade_allowed(SimpleNamespace()) is False


def test_partial_experiment_never_reports_full_basket_ratio():
    message = vmo._margin_ratio_message(
        observed=10.0,
        margin_total=100.0,
        opened_count=2,
        total_count=7,
        incomplete=True,
    )
    assert "não calculada" in message
    assert "2/7" in message
    assert "0.10x" not in message


def test_invalid_post_send_measurement_returns_failure_status():
    assert vmo._experiment_exit_code(False, False, True) == 1
    assert vmo._experiment_exit_code(False, False, False) == 0


def test_pending_order_keeps_full_main_measurement_incomplete():
    fake = fake_mt5()
    fake.ACCOUNT_TRADE_MODE_DEMO = 7
    fake.TRADE_ACTION_REMOVE = 2
    account = SimpleNamespace(
        margin_free=1000.0,
        trade_mode=7,
        trade_allowed=True,
        login=1,
        currency="USD",
    )
    pending = SimpleNamespace(
        magic=123,
        symbol="PAIR0",
        ticket=88,
        volume_current=0.01,
    )
    fake.account_info.return_value = account
    fake.positions_get.return_value = []
    fake.orders_get.side_effect = [[], [pending], [pending], []]
    fake.symbol_info.return_value = valid_info()
    fake.symbol_info_tick.return_value = valid_tick()
    fake.order_calc_margin.return_value = 10.0
    fake.order_send.side_effect = [
        SimpleNamespace(retcode=fake.TRADE_RETCODE_PLACED),
        *[SimpleNamespace(retcode=fake.TRADE_RETCODE_DONE) for _ in range(6)],
        SimpleNamespace(retcode=fake.TRADE_RETCODE_DONE),
    ]
    legs = [leg(f"PAIR{i}") for i in range(7)]
    fake.terminal_info.return_value = SimpleNamespace(path="C:/mfc-backtest")
    output = StringIO()
    with patch.object(vmo, "mt5", fake), patch.object(vmo, "MT5_AVAILABLE", True), \
            patch.object(vmo, "MT5_PATH", "C:/mfc-backtest/terminal64.exe"), \
            patch.dict(vmo.os.environ, {"CSS_MT5_TERMINAL_PATH": "C:/mfc-backtest/terminal64.exe"}), \
            patch.object(vmo, "check_account_identity", return_value={"allowed": True}), \
            patch.object(vmo, "get_portfolio_pairs", return_value=legs), \
            patch.object(vmo, "to_broker_symbol", side_effect=lambda pair: pair), \
            patch.object(vmo.portfolio_executor, "_compute_catastrophic_sl", return_value=0.9), \
            patch.object(vmo.time, "sleep", return_value=None), \
            patch.object(vmo, "_run_magic", return_value=123), \
            patch.object(vmo.sys, "argv", ["validate_margin_observed.py"]), \
            redirect_stdout(output):
        result = vmo.main()
    assert result == 1
    text = output.getvalue()
    assert "ordem pendente confirmada" in text
    assert "Razão observado/previsto não calculada" in text
    assert "Razão observado/previsto: " not in text


def test_only_explicit_invalid_fill_allows_return_retry():
    fake = fake_mt5()
    fake.order_send.side_effect = [
        SimpleNamespace(retcode=fake.TRADE_RETCODE_INVALID_FILL),
        SimpleNamespace(retcode=fake.TRADE_RETCODE_DONE),
    ]
    request = {"type_filling": fake.ORDER_FILLING_IOC, "symbol": "EURUSD"}
    with patch.object(vmo, "mt5", fake):
        result = vmo.send_with_fallback(request)
    assert result.retcode == fake.TRADE_RETCODE_DONE
    assert fake.order_send.call_count == 2
    assert fake.order_send.call_args_list[1].args[0]["type_filling"] == fake.ORDER_FILLING_RETURN
    assert request["type_filling"] == fake.ORDER_FILLING_IOC


def test_preflight_is_all_or_nothing_and_never_sends():
    fake = fake_mt5()
    fake.symbol_info.side_effect = [valid_info(), valid_info()]
    fake.symbol_info_tick.side_effect = [valid_tick(), SimpleNamespace(bid=0.0, ask=1.2002)]
    fake.order_calc_margin.return_value = 10.0
    legs = [leg("EURUSD"), leg("GBPUSD")]
    with patch.object(vmo, "mt5", fake), patch.object(vmo, "to_broker_symbol", side_effect=lambda p: p):
        prepared, errors = vmo._prepare_orders(legs, margin_free=1000.0)
    assert prepared is None
    assert any("GBPUSD" in error for error in errors)
    fake.order_send.assert_not_called()


def test_invalid_margin_or_zero_sl_blocks_preflight():
    fake = fake_mt5()
    fake.symbol_info.return_value = valid_info()
    fake.symbol_info_tick.return_value = valid_tick()
    fake.order_calc_margin.return_value = None
    with patch.object(vmo, "mt5", fake), patch.object(vmo, "to_broker_symbol", lambda p: p):
        prepared, errors = vmo._prepare_orders([leg()], margin_free=1000.0)
    assert prepared is None
    assert any("order_calc_margin" in error for error in errors)
    fake.order_send.assert_not_called()

    fake.order_calc_margin.return_value = 10.0
    with patch.object(vmo, "mt5", fake), \
            patch.object(vmo, "to_broker_symbol", lambda p: p), \
            patch.object(vmo.portfolio_executor, "_compute_catastrophic_sl", return_value=0.0):
        prepared, errors = vmo._prepare_orders([leg()], margin_free=1000.0)
    assert prepared is None
    assert any("SL" in error for error in errors)


def test_validate_margin_calc_returns_failure_when_a_leg_cannot_be_measured():
    fake = fake_mt5()
    fake.account_info.return_value = SimpleNamespace(
        login=123,
        server="Exness-MT5Trial11",
        leverage=100,
        margin_free=1000.0,
        balance=1000.0,
        currency="USD",
    )
    fake.initialize.return_value = True
    fake.symbol_info.return_value = None
    fake.terminal_info.return_value = SimpleNamespace(path="C:/mfc-backtest")
    legs = [leg(f"PAIR{i}") for i in range(7)]
    with patch.object(margin_calc, "mt5", fake), \
            patch.object(margin_calc, "MT5_AVAILABLE", True), \
            patch.object(margin_calc, "MT5_PATH", "C:/mfc-backtest/terminal64.exe"), \
            patch.dict(margin_calc.os.environ, {"CSS_MT5_TERMINAL_PATH": "C:/mfc-backtest/terminal64.exe"}), \
            patch.object(margin_calc, "check_account_identity", return_value={"allowed": True}), \
            patch.object(margin_calc, "get_portfolio_pairs", return_value=legs), \
            patch.object(margin_calc, "to_broker_symbol", side_effect=lambda pair: pair), \
            patch.object(margin_calc.sys, "argv", ["validate_margin_calc.py"]):
        result = margin_calc.main()

    assert result == 1
    fake.shutdown.assert_called_once()


def test_canonical_windows_path_check_rejects_lookalike_directory():
    """Achado herdr-review mfc-56 (MFC56-01): a checagem antiga aceitava
    REQUIRED_PATH_MARKER como substring, então um diretório parecido como
    'mfc-backtest-prod' passava sem ser a instância isolada."""
    assert vmo._terminal_path_is_isolated("C:/mfc-backtest/terminal64.exe") is True
    assert vmo._terminal_path_is_isolated("C:/mfc-backtest-prod/terminal64.exe") is False
    assert vmo._terminal_path_is_isolated("C:/other/mfc-backtest/notterminal.exe") is False
    assert margin_calc._terminal_path_is_isolated("C:/mfc-backtest/terminal64.exe") is True
    assert margin_calc._terminal_path_is_isolated("C:/mfc-backtest-prod/terminal64.exe") is False


def test_validate_margin_observed_refuses_lookalike_terminal_before_connecting():
    fake = fake_mt5()
    with patch.object(vmo, "mt5", fake), patch.object(vmo, "MT5_AVAILABLE", True), \
            patch.object(vmo, "MT5_PATH", "C:/mfc-backtest-prod/terminal64.exe"), \
            patch.dict(vmo.os.environ, {"CSS_MT5_TERMINAL_PATH": "C:/mfc-backtest-prod/terminal64.exe"}), \
            patch.object(vmo.sys, "argv", ["validate_margin_observed.py"]):
        result = vmo.main()
    assert result == 1
    fake.initialize.assert_not_called()
    fake.shutdown.assert_not_called()


def test_validate_margin_observed_refuses_when_connected_terminal_differs_from_configured():
    """Mesmo com CSS_MT5_TERMINAL_PATH canônico, mt5.initialize(path=X) pode
    anexar a outro terminal já em execução — a checagem pós-conexão precisa
    recusar nesse caso (achado herdr-review mfc-56, MFC56-01)."""
    fake = fake_mt5()
    fake.ACCOUNT_TRADE_MODE_DEMO = 7
    fake.account_info.return_value = SimpleNamespace(
        margin_free=1000.0, trade_mode=7, trade_allowed=True, login=1, currency="USD",
    )
    fake.terminal_info.return_value = SimpleNamespace(path="C:/some-other-terminal")
    with patch.object(vmo, "mt5", fake), patch.object(vmo, "MT5_AVAILABLE", True), \
            patch.object(vmo, "MT5_PATH", "C:/mfc-backtest/terminal64.exe"), \
            patch.dict(vmo.os.environ, {"CSS_MT5_TERMINAL_PATH": "C:/mfc-backtest/terminal64.exe"}), \
            patch.object(vmo, "check_account_identity", return_value={"allowed": True}), \
            patch.object(vmo.sys, "argv", ["validate_margin_observed.py"]):
        result = vmo.main()
    assert result == 1
    fake.account_info.assert_not_called()
    fake.shutdown.assert_called_once()


def test_validate_margin_calc_refuses_lookalike_terminal_before_connecting():
    fake = fake_mt5()
    with patch.object(margin_calc, "mt5", fake), \
            patch.object(margin_calc, "MT5_AVAILABLE", True), \
            patch.object(margin_calc, "MT5_PATH", "C:/mfc-backtest-prod/terminal64.exe"), \
            patch.dict(margin_calc.os.environ, {"CSS_MT5_TERMINAL_PATH": "C:/mfc-backtest-prod/terminal64.exe"}), \
            patch.object(margin_calc.sys, "argv", ["validate_margin_calc.py"]):
        result = margin_calc.main()
    assert result == 1
    fake.initialize.assert_not_called()
    fake.shutdown.assert_not_called()


def test_validate_margin_calc_refuses_when_connected_terminal_differs_from_configured():
    fake = fake_mt5()
    fake.terminal_info.return_value = SimpleNamespace(path="C:/some-other-terminal")
    with patch.object(margin_calc, "mt5", fake), \
            patch.object(margin_calc, "MT5_AVAILABLE", True), \
            patch.object(margin_calc, "MT5_PATH", "C:/mfc-backtest/terminal64.exe"), \
            patch.dict(margin_calc.os.environ, {"CSS_MT5_TERMINAL_PATH": "C:/mfc-backtest/terminal64.exe"}), \
            patch.object(margin_calc, "check_account_identity", return_value={"allowed": True}), \
            patch.object(margin_calc.sys, "argv", ["validate_margin_calc.py"]):
        result = margin_calc.main()
    assert result == 1
    fake.account_info.assert_not_called()
    fake.shutdown.assert_called_once()


def test_validate_margin_calc_refuses_when_account_identity_check_fails():
    """Achado herdr-review mfc-56 (MFC56-02): este script não checava
    identidade de conta antes — só terminal isolado não garante que a conta
    logada é a esperada (mesma máquina roda vários terminais/contas)."""
    fake = fake_mt5()
    fake.terminal_info.return_value = SimpleNamespace(path="C:/mfc-backtest")
    with patch.object(margin_calc, "mt5", fake), \
            patch.object(margin_calc, "MT5_AVAILABLE", True), \
            patch.object(margin_calc, "MT5_PATH", "C:/mfc-backtest/terminal64.exe"), \
            patch.dict(margin_calc.os.environ, {"CSS_MT5_TERMINAL_PATH": "C:/mfc-backtest/terminal64.exe"}), \
            patch.object(margin_calc, "check_account_identity", return_value={
                "allowed": False, "message": "conta errada",
            }), \
            patch.object(margin_calc.sys, "argv", ["validate_margin_calc.py"]):
        result = margin_calc.main()
    assert result == 1
    fake.account_info.assert_not_called()
    fake.shutdown.assert_called_once()


def test_cleanup_does_not_call_empty_on_unknown_positions_state():
    fake = fake_mt5()
    fake.positions_get.return_value = None
    with patch.object(vmo, "mt5", fake), patch.object(vmo, "MT5_AVAILABLE", True), \
            patch.object(vmo.time, "sleep", return_value=None):
        result = vmo._close_test_magic_positions(123, deadline_sec=0.0)
    assert result["confirmed"] is False
    assert result["remaining"] is None
    fake.order_send.assert_not_called()


def test_cleanup_requeries_until_zero_is_confirmed():
    fake = fake_mt5()
    position = SimpleNamespace(
        magic=123,
        symbol="EURUSD",
        ticket=77,
        volume=0.01,
        type=fake.ORDER_TYPE_BUY,
    )
    fake.positions_get.side_effect = [[position], []]
    fake.symbol_info_tick.return_value = valid_tick()
    fake.order_send.return_value = SimpleNamespace(retcode=fake.TRADE_RETCODE_DONE)
    with patch.object(vmo, "mt5", fake), patch.object(vmo, "MT5_AVAILABLE", True), \
            patch.object(vmo.time, "sleep", return_value=None):
        result = vmo._close_test_magic_positions(123, deadline_sec=10.0)
    assert result == {"confirmed": True, "closed": 1, "remaining": 0}
    fake.order_send.assert_called_once()


def test_cleanup_cancels_pending_orders_and_confirms_zero():
    fake = fake_mt5()
    fake.TRADE_ACTION_REMOVE = 2
    order = SimpleNamespace(magic=123, symbol="EURUSD", ticket=88, volume_current=0.01)
    fake.positions_get.return_value = []
    fake.orders_get.side_effect = [[order], []]
    fake.order_send.return_value = SimpleNamespace(retcode=fake.TRADE_RETCODE_DONE)
    with patch.object(vmo, "mt5", fake), patch.object(vmo, "MT5_AVAILABLE", True), \
            patch.object(vmo.time, "sleep", return_value=None):
        result = vmo._close_test_magic_positions(123, deadline_sec=10.0)
    assert result == {"confirmed": True, "closed": 1, "remaining": 0}
    request = fake.order_send.call_args.args[0]
    assert request["action"] == fake.TRADE_ACTION_REMOVE
    assert request["order"] == 88


def test_cleanup_tick_exception_is_reported_as_unconfirmed_not_raised():
    fake = fake_mt5()
    position = SimpleNamespace(magic=123, symbol="EURUSD", ticket=77, volume=0.01, type=0)
    fake.positions_get.return_value = [position]
    fake.orders_get.return_value = []
    fake.symbol_info_tick.side_effect = RuntimeError("tick unavailable")
    with patch.object(vmo, "mt5", fake), patch.object(vmo, "MT5_AVAILABLE", True), \
            patch.object(vmo.time, "monotonic", side_effect=[0.0, 0.0, 0.0, 11.0, 11.0]):
        result = vmo._close_test_magic_positions(123, deadline_sec=10.0)
    assert result["confirmed"] is False
    assert result["remaining"] == 1


def test_cleanup_deadline_with_known_position_returns_unknown_without_looping():
    fake = fake_mt5()
    position = SimpleNamespace(magic=123, symbol="EURUSD", ticket=77, volume=0.01, type=0)
    fake.positions_get.return_value = [position]
    with patch.object(vmo, "mt5", fake), patch.object(vmo, "MT5_AVAILABLE", True):
        result = vmo._close_test_magic_positions(123, deadline_sec=0.0)
    assert result == {"confirmed": False, "closed": 0, "remaining": 1}
    fake.order_send.assert_not_called()


def test_missing_execution_mode_does_not_allow_return_filling():
    fake = fake_mt5()
    info = valid_info()
    del info.trade_exemode
    with patch.object(vmo, "mt5", fake):
        assert vmo._return_allowed(info) is False


def test_history_append_is_schema_versioned_and_process_serialized():
    with tempfile.TemporaryDirectory() as tmp:
        path = f"{tmp}/history.json"
        with open(path, "w", encoding="utf-8") as stream:
            json.dump([{"script": "old"}], stream)
        with patch.object(log, "RESULTS_LOG_PATH", path):
            threads = [
                threading.Thread(target=log.append_result, args=({"script": f"t{i}"},))
                for i in range(20)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        with open(path, encoding="utf-8") as stream:
            history = json.load(stream)
    assert len(history) == 21
    assert all(entry["schema_version"] == 2 for entry in history)
    assert all("recorded_at_utc" in entry for entry in history[1:])
    assert history[0]["recorded_at_utc"] is None
    assert history[0]["provenance"]["status"] == "legacy_unavailable"
    assert all(entry["provenance"]["status"] == "partial" for entry in history[1:])
    assert all("timestamp_utc" in entry for entry in history)


def test_history_migrates_engine_semantics_and_diagnostics_fail_closed():
    with tempfile.TemporaryDirectory() as tmp:
        path = f"{tmp}/history.json"
        with open(path, "w", encoding="utf-8") as stream:
            json.dump([{
                "script": "backtest_engine_compare.py",
                "engines": {"3tf": {"baskets": 4}},
            }], stream)
        with patch.object(log, "RESULTS_LOG_PATH", path):
            log.append_result({"script": "new"})
        with open(path, encoding="utf-8") as stream:
            history = json.load(stream)
    assert history[0]["schema_version"] == 2
    assert history[0]["engines"]["3tf"]["reconstructed_baskets"] == 4
    assert history[0]["result_semantics"] == "reconstructed_baskets_and_active_signals"

    with patch.object(canonical, "ensure_mt5", return_value=False):
        assert canonical.run(days=1) == 1
    with patch.object(engine_compare, "ensure_mt5", return_value=False):
        assert engine_compare.compare(days=1) == 1
        assert engine_compare.threshold_sweep(days=1) == 1
    with patch.object(composition, "ensure_mt5", return_value=False):
        assert composition.compare_composition(days=1) == 1


def test_needs_hardcoded_rate_fallback_matches_convert_pnl_to_usd_condition():
    """Achado herdr-review mfc-63 (P3-2/`mfc-rev-2`, MFC63-02/`mfc-rev`,
    CONFIRMADO pelos dois): mesmo com rates_dict, convert_pnl_to_usd() cai
    na tabela hardcoded quando o cross USD necessário não está no dict — o
    rótulo rates_source do journal era incondicional, então precisa de uma
    checagem que replique a MESMA condição pra poder avisar antes."""
    # USD de um lado (base ou quote) nunca precisa de cross -- nunca cai em fallback.
    assert composition._needs_hardcoded_rate_fallback("EURUSD", {}) is False
    assert composition._needs_hardcoded_rate_fallback("USDJPY", {}) is False
    # cross ausente do rates_dict -> precisa do fallback.
    assert composition._needs_hardcoded_rate_fallback("GBPNZD", {}) is True
    # cross presente via NZDUSD -> não precisa.
    assert composition._needs_hardcoded_rate_fallback(
        "GBPNZD", {"NZDUSD": 0.60}) is False
    # cross presente via USDNZD (o outro lado do par) -> não precisa.
    assert composition._needs_hardcoded_rate_fallback(
        "GBPNZD", {"USDNZD": 1.66}) is False
    # cross presente mas com valor não-positivo -> convert_pnl_to_usd() também
    # rejeita esse caso (rates_dict[...] > 0), então ainda precisa do fallback.
    assert composition._needs_hardcoded_rate_fallback(
        "GBPNZD", {"NZDUSD": 0.0}) is True


def test_history_recomputes_provenance_and_rejects_forged_contract_coverage():
    with tempfile.TemporaryDirectory() as tmp:
        path = f"{tmp}/history.json"
        forged_contract = {
            "observed_by_pair": {f"FAKE{i}": 100000 for i in range(28)},
            "expected_for_pnl": 100000,
            "missing_pairs": [],
            "invalid_pairs": [],
            "coverage_complete": True,
            "all_finite_positive": True,
        }
        with patch.object(log, "RESULTS_LOG_PATH", path), \
             patch.object(log, "_runtime_provenance", return_value=(None, None, forged_contract)):
            log.append_result({
                "script": "backtest_engine_compare.py",
                "parameters": {"days": 1},
                "provenance": {"status": "complete", "contract_size": forged_contract},
                "contract_size": forged_contract,
            })
        with open(path, encoding="utf-8") as stream:
            history = json.load(stream)

    provenance = history[0]["provenance"]
    assert provenance["status"] == "partial"
    assert provenance["contract_size"] is None
    assert "contract_size_coverage" in provenance["missing"]


def test_history_rejects_complete_pair_set_with_wrong_contract_scale():
    from web.css_service import ALL_28_PAIRS

    wrong_scale = {
        "observed_by_pair": {pair: 1000 for pair in ALL_28_PAIRS},
        "expected_for_pnl": 100000,
        "missing_pairs": [],
        "invalid_pairs": [],
        "coverage_complete": True,
        "all_finite_positive": True,
    }
    with patch.object(log, "_runtime_provenance", return_value=(None, None, wrong_scale)):
        provenance = log._provenance_for({"days": 1, "parameters": {}})

    assert provenance["status"] == "partial"
    assert provenance["contract_size"] is None
    assert "contract_size_coverage" in provenance["missing"]


def test_code_provenance_has_digest_for_dirty_checkout_sources():
    commit, dirty, source_digest = log._code_provenance()

    assert isinstance(source_digest, str)
    assert len(source_digest) == 64
    # A source export/container may not include .git; the source digest remains
    # the useful provenance invariant in that environment.
    assert commit is None or isinstance(commit, str)
    assert dirty is None or isinstance(dirty, bool)


def test_oos_rejects_missing_producer_provenance():
    with pytest.raises(ValueError, match="producer_provenance"):
        log._validate_producer_provenance({
            "window": {"sample_role": "oos_disjoint"},
        })


def _valid_producer_entry():
    execution = {
        "host": "test-host",
        "terminal_path": r"D:\MetaTradersWSL\mfc-backtest\terminal64.exe",
        "is_production_terminal": False,
        "terminal_isolation_asserted": True,
        "orders_sent": False,
    }
    cost_digests = {"3tf_baseline": "a" * 64, "5tf_port_a": "b" * 64}
    contract = {
        "observed_by_pair": {pair: 100000.0 for pair in ALL_28_PAIRS},
        "expected_for_pnl": 100000,
        "missing_pairs": [],
        "invalid_pairs": [],
        "coverage_complete": True,
        "all_finite_positive": True,
        "valid_for_pnl": True,
    }
    data_digest = "c" * 64
    coverage = {
        "candidate_nights": 30,
        "evaluated_nights": 30,
        "skipped_no_verdict": 0,
        "skipped_invalid_exit": 0,
        "evaluated_dates_brt": [f"2026-06-{index + 1:02d}T21:00:00-03:00" for index in range(30)],
        "price_missing_points": [],
    }
    engine_metrics = {
        name: {
            "baskets": 1,
            "degraded_baskets": 0,
            "swap_unmodeled_baskets": 0,
            "skipped_missing_price": 0,
        }
        for name in cost_digests
    }
    producer = {
        "status": "complete",
        "code_source_digest": log._source_digest(),
        "code_source_files": list(log._PROVENANCE_SOURCE_FILES),
        "account": {
            "login": 123,
            "server": "Exness-MT5Trial11",
            "currency": "USD",
            "trade_mode": 0,
        },
        "terminal": {
            "path": execution["terminal_path"],
            "observed_path": r"D:\MetaTradersWSL\mfc-backtest",
        },
        "contract_size": contract,
        "data_snapshot": {"series_and_h1_prices_digest": data_digest},
        "cost_snapshot": {
            "source": "current MT5 ticks sampled by CostModel",
            "per_run_observation_digests": [cost_digests],
        },
        "execution": execution,
    }
    entry = {
        "recorded_at_utc": "2026-08-30T01:00:00+00:00",
        "journal_seq": 1,
        "window": {
            "days": 45,
            "start_brt": "2026-06-01T21:00:00-03:00",
            "end_brt": "2026-07-16T21:00:00-03:00",
            "development_start_brt": "2026-07-16T21:00:00-03:00",
            "sample_role": "oos_disjoint",
        },
        "note": "fixture OOS completo",
        "runs": 1,
        "engines_compared": ["3tf_baseline", "5tf_port_a"],
        "data_snapshot_digest": data_digest,
        "nights_evaluated": 30,
        "coverage": coverage,
        "quality": {
            "status": "clean",
            "by_engine": {
                name: {
                    "degraded_baskets": 0,
                    "swap_unmodeled_baskets": 0,
                    "skipped_missing_price": 0,
                }
                for name in cost_digests
            },
        },
        "engines": engine_metrics,
        "runs_summary": {
            "per_run": [{
                "cost_observation_digests": cost_digests,
                "coverage": coverage,
                "engines": engine_metrics,
            }],
        },
        "execution": execution,
        "producer_provenance": producer,
    }
    producer["result_snapshot_digest"] = log.result_snapshot_digest(entry)
    return entry


def test_complete_producer_envelope_is_accepted_and_is_oos_eligible():
    entry = _valid_producer_entry()
    log._validate_producer_provenance(entry)
    assert log.oos_evidence_eligible(entry) is True


def test_oos_producer_envelope_rejects_incomplete_or_unclean_coverage():
    entry = _valid_producer_entry()
    entry["coverage"]["skipped_no_verdict"] = 1
    with pytest.raises(ValueError, match="proveniência completa"):
        log._validate_producer_provenance(entry)

    entry = _valid_producer_entry()
    entry["coverage"]["candidate_nights"] = 29
    entry["coverage"]["evaluated_nights"] = 29
    entry["coverage"]["evaluated_dates_brt"] = entry["coverage"]["evaluated_dates_brt"][:29]
    entry["nights_evaluated"] = 29
    with pytest.raises(ValueError, match="proveniência completa"):
        log._validate_producer_provenance(entry)

    entry = _valid_producer_entry()
    entry["coverage"]["evaluated_nights"] = 29
    entry["coverage"]["evaluated_dates_brt"] = entry["coverage"]["evaluated_dates_brt"][:29]
    entry["nights_evaluated"] = 29
    with pytest.raises(ValueError, match="proveniência completa"):
        log._validate_producer_provenance(entry)

    entry = _valid_producer_entry()
    entry["nights_evaluated"] = 29
    with pytest.raises(ValueError, match="proveniência completa"):
        log._validate_producer_provenance(entry)

    entry = _valid_producer_entry()
    entry["quality"]["status"] = "partial_model"
    with pytest.raises(ValueError, match="proveniência completa"):
        log._validate_producer_provenance(entry)

    # Cada uma destas guardas precisa continuar sendo carga útil do envelope
    # importado, não apenas uma consequência de outra validação.
    entry = _valid_producer_entry()
    entry["coverage"]["evaluated_dates_brt"] = entry["coverage"]["evaluated_dates_brt"][:-1]
    with pytest.raises(ValueError, match="proveniência completa"):
        log._validate_producer_provenance(entry)

    entry = _valid_producer_entry()
    entry["coverage"]["price_missing_points"] = ["EURUSD"]
    with pytest.raises(ValueError, match="proveniência completa"):
        log._validate_producer_provenance(entry)

    entry = _valid_producer_entry()
    entry["quality"]["by_engine"]["3tf_baseline"]["degraded_baskets"] = 1
    with pytest.raises(ValueError, match="proveniência completa"):
        log._validate_producer_provenance(entry)

    entry = _valid_producer_entry()
    entry["runs_summary"]["per_run"][0]["coverage"] = copy.deepcopy(entry["coverage"])
    entry["runs_summary"]["per_run"][0]["coverage"]["price_missing_points"] = ["EURUSD"]
    with pytest.raises(ValueError, match="proveniência completa"):
        log._validate_producer_provenance(entry)


def _mutate_coherent_orders_sent_true(entry):
    execution = copy.deepcopy(entry["execution"])
    execution["orders_sent"] = True
    entry["execution"] = execution
    entry["producer_provenance"]["execution"] = copy.deepcopy(execution)


def _mutate_observed_terminal_path(entry):
    entry["producer_provenance"]["terminal"]["observed_path"] = (
        r"D:\\MetaTradersWSL\\other-terminal"
    )


def _mutate_cost_source(entry):
    entry["producer_provenance"]["cost_snapshot"]["source"] = "forged source"


def _mutate_divergent_cost_digest(entry):
    snapshot = entry["producer_provenance"]["cost_snapshot"]
    observed = dict(snapshot["per_run_observation_digests"][0])
    observed["3tf_baseline"] = "d" * 64
    snapshot["per_run_observation_digests"] = [observed]


def _mutate_non_hex_cost_digest(entry):
    snapshot = entry["producer_provenance"]["cost_snapshot"]
    observed = dict(snapshot["per_run_observation_digests"][0])
    observed["3tf_baseline"] = "z" * 64
    snapshot["per_run_observation_digests"] = [observed]
    for run in entry["runs_summary"]["per_run"]:
        run["cost_observation_digests"]["3tf_baseline"] = "z" * 64


def _mutate_blank_oos_note(entry):
    entry["note"] = "   "


def _mutate_result_metric(entry):
    entry["engines"]["3tf_baseline"]["baskets"] = 2


@pytest.mark.parametrize("mutation, expected_error", [
    (
        lambda entry: entry["producer_provenance"].update({"code_source_digest": "0" * 64}),
        "digest do produtor não corresponde ao checkout importado",
    ),
    (
        lambda entry: entry.update({"data_snapshot_digest": "0" * 64}),
        "digest de dados do topo diverge do envelope do produtor",
    ),
    (
        lambda entry: entry["producer_provenance"].pop("cost_snapshot"),
        "snapshot de custo do produtor ausente ou inconsistente",
    ),
    (
        lambda entry: entry.update({"execution": {"orders_sent": True}}),
        "execução do topo diverge do envelope do produtor",
    ),
    (
        lambda entry: entry["runs_summary"]["per_run"][0]["cost_observation_digests"].pop("3tf_baseline"),
        "snapshot de custo do produtor ausente ou inconsistente",
    ),
    (_mutate_coherent_orders_sent_true, "producer_provenance não atesta ausência de ordens"),
    (_mutate_observed_terminal_path, "OOS não comprova que o MT5 observado é o terminal configurado"),
    (_mutate_cost_source, "snapshot de custo do produtor ausente ou inconsistente"),
    (_mutate_divergent_cost_digest, "snapshot de custo do produtor ausente ou inconsistente"),
    (_mutate_non_hex_cost_digest, "snapshot de custo do produtor ausente ou inconsistente"),
    (_mutate_blank_oos_note, "OOS exige janela completa e nota explícita"),
    (_mutate_result_metric, "digest dos resultados diverge do registro OOS"),
])
def test_producer_envelope_rejects_tampered_fields(mutation, expected_error):
    tampered = copy.deepcopy(_valid_producer_entry())
    mutation(tampered)
    with pytest.raises(ValueError, match=re.escape(expected_error)):
        log._validate_producer_provenance(tampered)


def test_oos_append_assigns_monotonically_increasing_journal_seq():
    """Redesenho pós herdr-review mfc-56/57/58 + consulta mfc-scout
    (2026-08-31): `journal_seq`, não `recorded_at_utc`, decide a ordem de
    seleção — um inteiro monotônico atribuído por append_result() sob o
    lock exclusivo, nunca aceito do chamador."""
    entry_1 = _valid_producer_entry()
    entry_2 = _valid_producer_entry()
    entry_3 = _valid_producer_entry()
    with tempfile.TemporaryDirectory() as tmp:
        path = f"{tmp}/history.json"
        with patch.object(log, "RESULTS_LOG_PATH", path):
            log.append_result(entry_1)
            log.append_result(entry_2)
            log.append_result(entry_3)
            with open(path, encoding="utf-8") as stream:
                history = json.load(stream)

    assert [record["journal_seq"] for record in history] == [1, 2, 3]
    assert log.select_latest_oos_evidence(history) == history[2]


def test_oos_append_backfills_journal_seq_for_legacy_entries_by_position():
    """Entradas do journal anteriores a esta mudança não têm `journal_seq` —
    o backfill usa a própria posição no array append-only como ordem
    histórica real, sem reescrever nada além do campo novo."""
    legacy_a = {"window": {"sample_role": "oos_disjoint"}, "script": "legacy-a"}
    legacy_b = {"window": {"sample_role": "oos_disjoint"}, "script": "legacy-b"}
    new_entry = _valid_producer_entry()
    with tempfile.TemporaryDirectory() as tmp:
        path = f"{tmp}/history.json"
        with open(path, "w", encoding="utf-8") as stream:
            json.dump([legacy_a, legacy_b], stream)
        with patch.object(log, "RESULTS_LOG_PATH", path):
            log.append_result(new_entry)
            with open(path, encoding="utf-8") as stream:
                history = json.load(stream)

    assert history[0]["journal_seq"] == 1
    assert history[1]["journal_seq"] == 2
    assert history[2]["journal_seq"] == 3


def test_oos_append_rejects_invalid_entry_before_persisting_it():
    entry = _valid_producer_entry()
    entry["coverage"]["price_missing_points"] = ["EURUSD"]
    with tempfile.TemporaryDirectory() as tmp:
        path = f"{tmp}/history.json"
        with patch.object(log, "RESULTS_LOG_PATH", path), pytest.raises(
            ValueError, match="proveniência completa"
        ):
            log.append_result(entry)
        with pytest.raises(FileNotFoundError):
            with open(path, encoding="utf-8"):
                pass


def test_oos_selector_is_fail_closed_for_malformed_records_and_reports_expiry():
    malformed = [
        {"window": []},
        {"window": {"sample_role": "oos_disjoint"}, "producer_provenance": {}},
    ]
    assert log.select_latest_oos_evidence(malformed) is None
    assert log.oos_evidence_status(malformed)["status"] == "expired_or_invalid"
    assert log.oos_evidence_status([])["status"] == "no_records"


def test_oos_validator_rejects_noncanonical_window_and_dates():
    mutations = [
        (lambda entry: entry["window"].update({"end_brt": "2026-07-16T21:30:00-03:00"}),
         "end_brt deve ser um instante canônico"),
        (lambda entry: entry["window"].update({"start_brt": "2026-06-02T21:00:00-03:00"}),
         "start_brt não corresponde"),
        (lambda entry: entry["window"].update({"start_brt": "2026-06-01T00:00:00+00:00"}),
         "start_brt deve declarar offset"),
        (lambda entry: entry["window"].update({"days": 0}),
         "sem número de dias válido"),
        (lambda entry: entry["window"].update({"development_start_brt": "2026-07-16T20:00:00-03:00"}),
         "development_start_brt deve ser um instante canônico"),
        (lambda entry: entry["coverage"].update({
            "evaluated_dates_brt": entry["coverage"]["evaluated_dates_brt"][:29]
            + ["2026-07-16T21:00:00-03:00"],
        }), "fora do intervalo declarado"),
        (lambda entry: entry["coverage"].update({
            "evaluated_dates_brt": ["2026-06-01T21:00:00+00:00"]
            + entry["coverage"]["evaluated_dates_brt"][1:],
        }), "deve declarar offset BRT"),
        (lambda entry: entry["coverage"].update({
            "evaluated_dates_brt": ["2026-06-01T20:00:00-03:00"]
            + entry["coverage"]["evaluated_dates_brt"][1:],
        }), "fora do instante canônico"),
        (lambda entry: entry["coverage"].update({
            "evaluated_dates_brt": entry["coverage"]["evaluated_dates_brt"][:1]
            * 2 + entry["coverage"]["evaluated_dates_brt"][2:],
        }), "datas avaliadas OOS repetidas"),
    ]
    for mutation, expected_error in mutations:
        entry = _valid_producer_entry()
        mutation(entry)
        with pytest.raises(ValueError, match=expected_error):
            log._validate_producer_provenance(entry)


def test_select_latest_oos_evidence_picks_highest_journal_seq():
    older = _valid_producer_entry()
    older["journal_seq"] = 1
    newer = copy.deepcopy(older)
    newer["journal_seq"] = 2
    assert log.select_latest_oos_evidence([older, newer]) == newer
    assert log.select_latest_oos_evidence([newer, older]) == newer  # ordem no array não importa


def test_select_latest_oos_evidence_uses_journal_seq_even_with_manipulated_timestamp():
    """Prova direta de que `recorded_at_utc` não decide mais nada (fecha
    P2-1/P3-2, achados herdr-review mfc-58 e consulta mfc-scout): mesmo que
    um entry com `journal_seq` MENOR (append mais antigo) tenha um
    `recorded_at_utc` manipulado pra parecer mais recente que um entry com
    `journal_seq` MAIOR, a seleção segue `journal_seq` — o timestamp forjado
    não muda nada."""
    older_by_seq_but_future_timestamp = _valid_producer_entry()
    older_by_seq_but_future_timestamp["journal_seq"] = 1
    older_by_seq_but_future_timestamp["recorded_at_utc"] = "2099-01-01T00:00:00+00:00"
    newer_by_seq = copy.deepcopy(older_by_seq_but_future_timestamp)
    newer_by_seq["journal_seq"] = 2
    newer_by_seq["recorded_at_utc"] = "2026-08-30T01:00:00+00:00"  # bem mais antigo, mas journal_seq maior

    assert log.select_latest_oos_evidence(
        [older_by_seq_but_future_timestamp, newer_by_seq]
    ) == newer_by_seq


def test_append_result_ignores_caller_supplied_recorded_at_utc():
    """Fecha a raiz de P2-1 (achado herdr-review mfc-58, `mfc-rev-2`):
    `recorded_at_utc` não é mais aceito do chamador em nenhuma hipótese — um
    valor no futuro (relógio adiantado, fuso errado, ou forjado) não
    consegue mais nem ser persistido, então não há como ele virar
    permanentemente inamovível."""
    entry = _valid_producer_entry()
    entry["recorded_at_utc"] = "2099-01-01T00:00:00+00:00"  # tentativa de forjar o futuro
    with tempfile.TemporaryDirectory() as tmp:
        path = f"{tmp}/history.json"
        with patch.object(log, "RESULTS_LOG_PATH", path):
            log.append_result(entry)
            with open(path, encoding="utf-8") as stream:
                history = json.load(stream)

    persisted_dt = datetime.fromisoformat(history[0]["recorded_at_utc"])
    assert persisted_dt.year < 2099
    assert (datetime.now(persisted_dt.tzinfo) - persisted_dt) < timedelta(minutes=5)


def test_oos_evidence_status_reports_selected_recorded_at_utc_as_informational():
    """`recorded_at_utc` continua exposto em `oos_evidence_status` para
    auditoria/exibição, mas não decide a seleção — quem decide é
    `journal_seq`, testado em separado."""
    older = _valid_producer_entry()
    older["journal_seq"] = 1
    older["recorded_at_utc"] = "2026-08-30T09:00:00+00:00"
    newer = copy.deepcopy(older)
    newer["journal_seq"] = 2
    newer["recorded_at_utc"] = None
    newer["timestamp"] = "2026-08-30T06:30:00"  # naive, normalizado como BRT

    assert log.select_latest_oos_evidence([older, newer]) == newer
    assert log.oos_evidence_status([older, newer])["selected_recorded_at_utc"] == "2026-08-30T09:30:00+00:00"


def test_oos_selector_ignores_an_eligible_record_with_unparseable_temporal_identity():
    valid = _valid_producer_entry()
    invalid = copy.deepcopy(valid)
    invalid["journal_seq"] = 2  # journal_seq maior, mas identidade temporal ilegível
    invalid["recorded_at_utc"] = "not-a-timestamp"
    invalid["timestamp_utc"] = "also-invalid"
    invalid["timestamp"] = "still-invalid"

    assert log.oos_evidence_eligible(invalid) is False
    assert log.oos_evidence_status([invalid])["status"] == "expired_or_invalid"
    assert log.select_latest_oos_evidence([invalid, valid]) == valid


def test_oos_evidence_eligible_does_not_require_journal_seq():
    """Corrigido após achado herdr-review mfc-59 (MFC59-02/P3-2, `mfc-rev` e
    `mfc-rev-2`): `journal_seq` é propriedade do ARRAY (posição relativa),
    não de uma entrada isolada — exigi-lo em `oos_evidence_eligible()`
    deixava todo o journal em disco inelegível até a próxima escrita, porque
    o backfill só existia no caminho de `append_result()`. Um entry sem
    `journal_seq` válido continua elegível; ganha um fallback por posição no
    array (`_effective_journal_seq`), calculado tanto na leitura quanto na
    escrita."""
    valid = _valid_producer_entry()
    missing = copy.deepcopy(valid)
    del missing["journal_seq"]
    zero = copy.deepcopy(valid)
    zero["journal_seq"] = 0
    non_int = copy.deepcopy(valid)
    non_int["journal_seq"] = "1"

    for entry in (missing, zero, non_int):
        assert log.oos_evidence_eligible(entry) is True

    # Sem journal_seq declarado válido em nenhum dos três, o fallback usa a
    # posição no array — o último da lista vence.
    assert log.select_latest_oos_evidence([missing, zero, non_int]) == non_int


def test_select_latest_oos_evidence_works_on_legacy_history_without_any_append():
    """Achado herdr-review mfc-59 (MFC59-02/P3-2): um consumidor read-only
    que nunca chamou `append_result()` precisa ver o mesmo resultado que
    veria depois do próximo append — backfill por posição também na
    leitura, não só na escrita."""
    older = _valid_producer_entry()
    del older["journal_seq"]
    newer = copy.deepcopy(older)
    newer["recorded_at_utc"] = "2026-08-30T02:00:00+00:00"  # irrelevante pra ordem agora
    history = [older, newer]  # nunca passou por append_result

    assert log.oos_evidence_eligible(older) is True
    assert log.select_latest_oos_evidence(history) == newer  # posição 2 > posição 1
    assert log.oos_evidence_status(history)["status"] == "eligible"


def test_append_result_derives_next_journal_seq_from_max_not_length_after_truncation():
    """Achado herdr-review mfc-59 (MFC59-01/P2-1, `mfc-rev-2`, medido): um
    journal truncado (remoção de entradas antigas — manutenção normal, não
    hostil) pode deixar `journal_seq` altos em poucas entradas restantes.
    `next_seq = len(log) + 1` reintroduzia a mesma classe de bug que este
    redesenho existe pra eliminar: a execução nova nunca superava o valor
    remanescente e podia até duplicar um `journal_seq` existente."""
    remaining_after_truncation = [
        {"window": {"sample_role": "exploratory"}, "script": "run4", "journal_seq": 4},
        {"window": {"sample_role": "exploratory"}, "script": "run5", "journal_seq": 5},
    ]
    new_entry = _valid_producer_entry()
    with tempfile.TemporaryDirectory() as tmp:
        path = f"{tmp}/history.json"
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(remaining_after_truncation, stream)
        with patch.object(log, "RESULTS_LOG_PATH", path):
            log.append_result(new_entry)
            with open(path, encoding="utf-8") as stream:
                history = json.load(stream)

    assert history[-1]["journal_seq"] == 6
    assert log.select_latest_oos_evidence(history) == history[-1]


def test_append_result_new_entry_always_outranks_forged_high_journal_seq():
    """Mesma classe de MFC59-01/P2-1, cenário de edição manual: um
    `journal_seq` forjado bem alto não fica mais permanentemente inamovível
    — a próxima execução real sempre supera qualquer valor existente,
    porque `next_seq` deriva do máximo observado, não do comprimento."""
    forged = _valid_producer_entry()
    forged["journal_seq"] = 99999
    new_entry = _valid_producer_entry()
    with tempfile.TemporaryDirectory() as tmp:
        path = f"{tmp}/history.json"
        with open(path, "w", encoding="utf-8") as stream:
            json.dump([forged], stream)
        with patch.object(log, "RESULTS_LOG_PATH", path):
            log.append_result(new_entry)
            with open(path, encoding="utf-8") as stream:
                history = json.load(stream)

    assert history[-1]["journal_seq"] == 100000
    assert log.select_latest_oos_evidence(history) == history[-1]


def test_append_result_refuses_when_existing_journal_seq_is_duplicated():
    """Falha fechada (achado herdr-review mfc-59, MFC59-01/P2-1 `mfc-rev`):
    `journal_seq` fica fora do digest, então um payload preservado com esse
    campo adulterado não é detectável — sem uma próxima sequência segura de
    derivar, `append_result()` recusa em vez de escolher em silêncio."""
    dup_a = {"window": {"sample_role": "exploratory"}, "script": "a", "journal_seq": 3}
    dup_b = {"window": {"sample_role": "exploratory"}, "script": "b", "journal_seq": 3}
    new_entry = _valid_producer_entry()
    with tempfile.TemporaryDirectory() as tmp:
        path = f"{tmp}/history.json"
        with open(path, "w", encoding="utf-8") as stream:
            json.dump([dup_a, dup_b], stream)
        with patch.object(log, "RESULTS_LOG_PATH", path):
            with pytest.raises(ValueError, match="journal_seq duplicado"):
                log.append_result(new_entry)
            with open(path, encoding="utf-8") as stream:
                history = json.load(stream)
    assert history == [dup_a, dup_b]  # nada foi escrito


def test_append_result_refuses_before_persisting_when_backfill_would_collide_with_a_declared_value():
    """Achado herdr-review mfc-60 (MFC60-01/`mfc-rev`, P2-1/`mfc-rev-2`,
    medido): a versão anterior (mfc-59) checava consistência ANTES do
    backfill e só olhava valores declarados — então `[sem-campo,
    declarado=1]` passava a checagem (só via `[1]`), e o backfill em
    seguida gravava `[1, 1]` em disco, corrompendo o journal (o travamento
    só aparecia no PRÓXIMO append). A checagem sobre efetivos, depois do
    backfill, recusa isso de uma vez — nada é persistido."""
    mixed = [
        {"window": {"sample_role": "exploratory"}, "script": "a"},  # sem journal_seq -> efetivo 1
        {"window": {"sample_role": "exploratory"}, "script": "b", "journal_seq": 1},  # colide
    ]
    new_entry = _valid_producer_entry()
    with tempfile.TemporaryDirectory() as tmp:
        path = f"{tmp}/history.json"
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(mixed, stream)
        with patch.object(log, "RESULTS_LOG_PATH", path):
            with pytest.raises(ValueError, match="journal_seq duplicado"):
                log.append_result(new_entry)
            with open(path, encoding="utf-8") as stream:
                history = json.load(stream)
    assert history == mixed  # nada foi escrito, journal original preservado intacto


def test_effective_journal_seq_fallback_is_monotonic_not_positional():
    """Achado herdr-review mfc-60 (P2-2/`mfc-rev-2`, medido): o fallback por
    posição pura (`index + 1`) e os valores declarados (vindos de
    `max(...) + 1`) vivem em escalas incomensuráveis assim que o journal
    deixa de ser contíguo a partir de 1 — um journal truncado deixando
    `journal_seq` altos remanescentes fazia a entrada MAIS RECENTE (sem o
    campo) ser classificada ATRÁS de entradas antigas. Os três candidatos
    precisam ser entradas OOS elegíveis de verdade (não `exploratory`),
    senão a comparação de journal_seq nunca chega a rodar entre elas."""
    d_declared = _valid_producer_entry()
    d_declared["journal_seq"] = 4
    e_declared = copy.deepcopy(d_declared)
    e_declared["journal_seq"] = 5
    f_most_recent_undeclared = copy.deepcopy(d_declared)
    del f_most_recent_undeclared["journal_seq"]
    truncated_with_new_undeclared = [d_declared, e_declared, f_most_recent_undeclared]

    selected = log.select_latest_oos_evidence(truncated_with_new_undeclared)
    assert selected is f_most_recent_undeclared


def test_select_latest_oos_evidence_and_oos_evidence_status_agree_on_the_same_history():
    """Achado herdr-review mfc-60 (MFC60-02/`mfc-rev`, P2-3/`mfc-rev-2`,
    medido): `oos_evidence_status()` filtrava `history` (removendo roles
    irrelevantes) ANTES de repassar pra `select_latest_oos_evidence()` — os
    índices, e portanto os `journal_seq` efetivos de entradas sem o campo,
    ficavam diferentes dependendo de qual das duas funções o consumidor
    chamasse sobre o MESMO `history`."""
    p_exploratory = {"window": {"sample_role": "exploratory"}, "script": "P"}
    q_exploratory = {"window": {"sample_role": "exploratory"}, "script": "Q"}
    r_oos_undeclared = _valid_producer_entry()
    del r_oos_undeclared["journal_seq"]
    r_oos_undeclared["recorded_at_utc"] = "2026-08-31T01:00:00+00:00"
    s_oos_declared = copy.deepcopy(r_oos_undeclared)
    s_oos_declared["journal_seq"] = 2
    s_oos_declared["recorded_at_utc"] = "2026-08-31T02:00:00+00:00"  # marcador distinto de r
    history = [p_exploratory, q_exploratory, r_oos_undeclared, s_oos_declared]

    direct = log.select_latest_oos_evidence(history)
    via_status = log.oos_evidence_status(history)
    assert direct is r_oos_undeclared  # posição 2 no array completo -> efetivo 3, vence s=2
    assert via_status["selected_recorded_at_utc"] == log._record_datetime(direct).isoformat()
    assert via_status["selected_recorded_at_utc"] != s_oos_declared["recorded_at_utc"]


def test_oos_evidence_status_reports_ambiguous_journal_seq_on_duplicate():
    a = _valid_producer_entry()
    a["journal_seq"] = 5
    b = copy.deepcopy(a)
    b["journal_seq"] = 5
    history = [a, b]

    assert log.select_latest_oos_evidence(history) is None
    status = log.oos_evidence_status(history)
    assert status["status"] == "ambiguous_journal_seq"
    assert status["eligible"] == 2


def test_oos_evidence_status_reports_ambiguous_when_declared_collides_with_fallback():
    """Achado herdr-review mfc-60 (P3-1/`mfc-rev-2`, medido): a checagem
    antiga só comparava valores DECLARADOS entre si — uma colisão entre um
    declarado e um fallback por posição não era vista, e `ambiguous_journal_seq`
    não disparava; o desempate voltava a ser silencioso."""
    x_undeclared = _valid_producer_entry()
    del x_undeclared["journal_seq"]  # posição 0 -> efetivo 1
    y_declared = copy.deepcopy(x_undeclared)
    y_declared["journal_seq"] = 1  # colide com o efetivo de x
    history = [x_undeclared, y_declared]

    assert log.select_latest_oos_evidence(history) is None
    status = log.oos_evidence_status(history)
    assert status["status"] == "ambiguous_journal_seq"


def test_record_datetime_falls_back_through_recorded_at_utc_timestamp_utc_timestamp():
    """Achado herdr-review mfc-59 (P3-1, `mfc-rev-2`): o degrau intermediário
    (`timestamp_utc`) de `_record_datetime()` tinha ficado sem nenhuma
    cobertura depois que o teste antigo de `_record_identity` (que o
    exercitava incidentalmente) foi removido no redesenho."""
    assert log._record_datetime({
        "recorded_at_utc": None, "timestamp_utc": "2026-08-30T08:00:00+00:00",
    }).isoformat() == "2026-08-30T08:00:00+00:00"
    assert log._record_datetime({
        "recorded_at_utc": "not-a-timestamp", "timestamp_utc": "2026-08-30T07:00:00+00:00",
    }).isoformat() == "2026-08-30T07:00:00+00:00"
    assert log._record_datetime({"recorded_at_utc": None, "timestamp_utc": None}) is None


def test_oos_append_rejects_explicitly_missing_temporal_identity():
    entry = _valid_producer_entry()
    entry["recorded_at_utc"] = None
    entry["timestamp_utc"] = None
    entry["timestamp"] = None
    with pytest.raises(ValueError, match="identidade temporal interpretável"):
        log._validate_producer_provenance(entry)


def test_oos_selector_excludes_newer_exploratory_sample_from_oos_evidence():
    oos = _valid_producer_entry()
    exploratory = copy.deepcopy(oos)
    exploratory["window"]["sample_role"] = "exploratory"
    exploratory["journal_seq"] = 2  # journal_seq maior, mas não é oos_disjoint

    assert log.select_latest_oos_evidence([oos, exploratory]) == oos
    status = log.oos_evidence_status([oos, exploratory])
    assert status["status"] == "eligible"
    assert status["records"] == 1
    assert status["eligible"] == 1


def test_oos_append_never_persists_caller_supplied_supersedes():
    """Achado herdr-review mfc-56 (MFC56-03): supersedes é metadado
    EXCLUSIVAMENTE derivado — antes desta correção, um valor forjado pelo
    chamador só era sobrescrito quando havia registro antigo da mesma janela;
    sem registro antigo (ex.: primeira entrada de uma janela nova), o valor
    forjado sobrevivia intacto no journal."""
    entry = _valid_producer_entry()
    entry["supersedes"] = ["2000-01-01T00:00:00+00:00"]  # identidade forjada
    with tempfile.TemporaryDirectory() as tmp:
        path = f"{tmp}/history.json"
        with patch.object(log, "RESULTS_LOG_PATH", path):
            log.append_result(entry)
            with open(path, encoding="utf-8") as stream:
                history = json.load(stream)
    assert "supersedes" not in history[0]


def _valid_producer_entry_for_window(*, start_brt, end_brt, days, journal_seq):
    """Variante de _valid_producer_entry() com janela/cobertura próprias e
    disjuntas, pra testar filtro por janela entre entradas de janelas
    diferentes coexistindo no mesmo journal."""
    entry = _valid_producer_entry()
    entry["window"] = {
        "days": days,
        "start_brt": start_brt,
        "end_brt": end_brt,
        "development_start_brt": end_brt,
        "sample_role": "oos_disjoint",
    }
    start_dt = datetime.fromisoformat(start_brt)
    dates = [(start_dt + timedelta(days=i)).isoformat() for i in range(days)]
    entry["coverage"]["evaluated_dates_brt"] = dates
    entry["coverage"]["candidate_nights"] = days
    entry["coverage"]["evaluated_nights"] = days
    entry["nights_evaluated"] = days
    entry["journal_seq"] = journal_seq
    entry["producer_provenance"]["result_snapshot_digest"] = log.result_snapshot_digest(entry)
    return entry


def test_select_latest_oos_evidence_filters_by_window_independent_of_journal_seq():
    """Com `supersedes` fora da decisão (redesenho pós mfc-58), uma entrada
    de uma janela não pode mais afetar a seleção de outra janela de forma
    alguma — não há mais campo pra forjar essa relação. Este teste confirma
    que o filtro por `start_brt`/`end_brt` continua funcionando corretamente
    quando janelas diferentes coexistem no mesmo journal, mesmo que a janela
    "errada" tenha o `journal_seq` mais alto (o candidato mais recente em
    termos absolutos)."""
    window_a = _valid_producer_entry()  # 2026-06-01..2026-07-16, 30 noites
    window_a["journal_seq"] = 1
    window_b = _valid_producer_entry_for_window(
        start_brt="2026-07-16T21:00:00-03:00",
        end_brt="2026-08-15T21:00:00-03:00",
        days=30,
        journal_seq=2,  # mais recente que window_a, mas de outra janela
    )
    history = [window_a, window_b]

    assert log.select_latest_oos_evidence(
        history, start_brt=window_a["window"]["start_brt"],
        end_brt=window_a["window"]["end_brt"],
    ) == window_a
    assert log.select_latest_oos_evidence(
        history, start_brt=window_b["window"]["start_brt"],
        end_brt=window_b["window"]["end_brt"],
    ) == window_b
    # Sem filtro, o candidato de journal_seq mais alto vence — comportamento
    # esperado ("dê-me a evidência mais recente, seja qual for a janela").
    assert log.select_latest_oos_evidence(history) == window_b


def test_cleanup_failure_overrides_pending_success_after_shutdown():
    fake = fake_mt5()
    fake.ACCOUNT_TRADE_MODE_DEMO = 7
    fake.account_info.return_value = SimpleNamespace(
        margin_free=1000.0,
        trade_mode=7,
        trade_allowed=True,
        login=1,
        currency="USD",
    )
    fake.positions_get.return_value = []
    fake.terminal_info.return_value = SimpleNamespace(path="C:/mfc-backtest")
    with patch.object(vmo, "mt5", fake), patch.object(vmo, "MT5_AVAILABLE", True), \
            patch.object(vmo, "MT5_PATH", "C:/mfc-backtest/terminal64.exe"), \
            patch.dict(vmo.os.environ, {"CSS_MT5_TERMINAL_PATH": "C:/mfc-backtest/terminal64.exe"}), \
            patch.object(vmo, "check_account_identity", return_value={"allowed": True}), \
            patch.object(vmo, "get_portfolio_pairs", return_value=[]), \
            patch.object(vmo, "_close_test_magic_positions", return_value={
                "confirmed": False, "closed": 0, "remaining": None,
            }):
        with patch.object(vmo.sys, "argv", ["validate_margin_observed.py"]):
            with pytest.raises(SystemExit) as raised:
                vmo.main()
    assert raised.value.code == 1
    fake.shutdown.assert_called_once()


def test_cleanup_failure_does_not_mask_an_exception_in_flight():
    fake = fake_mt5()
    fake.ACCOUNT_TRADE_MODE_DEMO = 7
    fake.account_info.return_value = SimpleNamespace(
        margin_free=1000.0,
        trade_mode=7,
        trade_allowed=True,
        login=1,
        currency="USD",
    )
    fake.positions_get.return_value = []
    fake.terminal_info.return_value = SimpleNamespace(path="C:/mfc-backtest")
    with patch.object(vmo, "mt5", fake), patch.object(vmo, "MT5_AVAILABLE", True), \
            patch.object(vmo, "MT5_PATH", "C:/mfc-backtest/terminal64.exe"), \
            patch.dict(vmo.os.environ, {"CSS_MT5_TERMINAL_PATH": "C:/mfc-backtest/terminal64.exe"}), \
            patch.object(vmo, "check_account_identity", return_value={"allowed": True}), \
            patch.object(vmo, "get_portfolio_pairs", side_effect=RuntimeError("boom")), \
            patch.object(vmo, "_close_test_magic_positions", return_value={
                "confirmed": False, "closed": 0, "remaining": None,
            }):
        with patch.object(vmo.sys, "argv", ["validate_margin_observed.py"]):
            with pytest.raises(RuntimeError, match="boom"):
                vmo.main()
    fake.shutdown.assert_called_once()
