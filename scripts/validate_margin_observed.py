"""
FERRAMENTA DE VALIDAÇÃO MANUAL, NÃO PARTE DO PIPELINE DE PRODUÇÃO.

Valida `order_calc_margin()` na instância MT5 ISOLADA (`mfc-backtest`), usando
uma cesta de teste com stop-loss catastrófico e cleanup confirmado pelo broker.
Este arquivo envia ordens reais quando executado: leia as travas e exporte
`CSS_MT5_TERMINAL_PATH` explicitamente antes de qualquer execução.

O fluxo é deliberadamente conservador: todas as sete pernas passam por
símbolo, tick, modo de negociação, filling, SL e margem antes de qualquer
`order_send()`; somente `TRADE_RETCODE_INVALID_FILL` permite uma segunda
tentativa com `ORDER_FILLING_RETURN`; respostas ambíguas nunca são reenviadas
às cegas; e o cleanup só declara sucesso após confirmar zero posições.

Não executar este script como parte de testes ou deploy automático. A suíte
testa apenas as funções dependentes de um fake MT5; nenhuma ordem real é
enviada pelos testes.
"""

import math
import ntpath
import os
import sys
import time

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from web.css_service import MT5_AVAILABLE, MT5_PATH, mt5, to_broker_symbol
import agents.portfolio_executor as portfolio_executor
from agents.portfolio_executor import get_portfolio_pairs, check_account_identity


RUN_MAGIC_BASE = 900_000_000
LEGACY_TEST_MAGIC = 900099
LOT = 0.01
DEVIATION = 15
REQUIRED_PATH_MARKER = "mfc-backtest"
CLEANUP_DEADLINE_SEC = 30.0
CLEANUP_POLL_INTERVAL_SEC = 1.0
AMBIGUOUS_CONFIRM_ATTEMPTS = 3
AMBIGUOUS_CONFIRM_DELAY_SEC = 1.0

_AMBIGUOUS_RETCODE_NAMES = (
    "TRADE_RETCODE_TIMEOUT",
    "TRADE_RETCODE_CONNECTION",
    "TRADE_RETCODE_DONE_PARTIAL",
    "TRADE_RETCODE_PLACED",
)


def _canonical_windows_path(value):
    """Normaliza um caminho Windows pra comparação exata (mesmo helper de
    scripts/backtest_engine_compare.py::_canonical_windows_path)."""
    return ntpath.normcase(str(value or "").replace("/", "\\")).rstrip("\\")


def _terminal_path_is_isolated(configured_path):
    """Recusa por ESTRUTURA de caminho (pasta REQUIRED_PATH_MARKER, arquivo
    terminal64.exe), não por substring — achado herdr-review mfc-56
    (MFC56-01): 'mfc-backtest-prod' contém a substring 'mfc-backtest' mas não
    é a instância dedicada, e a checagem antiga (`REQUIRED_PATH_MARKER not in
    configured_path`) deixava passar."""
    canon = _canonical_windows_path(configured_path)
    return (
        ntpath.basename(canon) == "terminal64.exe"
        and ntpath.basename(ntpath.dirname(canon)) == REQUIRED_PATH_MARKER
    )


def _terminal_identity_matches(configured_path):
    """Confere, DEPOIS de mt5.initialize(), que o terminal REALMENTE
    conectado é o caminho configurado. mt5.initialize(path=X) pode anexar a
    um terminal já em execução em vez de abrir X (mesmo risco documentado em
    web/css_service.py::connect_mt5 e agents/portfolio_executor.py::
    ensure_mt5); sem esta checagem pós-conexão, o guard estrutural acima só
    prova o que foi PEDIDO, não o que foi de fato usado — achado herdr-review
    mfc-56 (MFC56-01)."""
    if mt5 is None:
        return False
    terminal_info = mt5.terminal_info()
    if terminal_info is None:
        return False
    observed_dir = _canonical_windows_path(getattr(terminal_info, "path", None))
    expected_dir = _canonical_windows_path(ntpath.dirname(configured_path))
    return observed_dir == expected_dir


def _run_magic():
    """Magic exclusivo desta execução, sem compartilhar o 900099 histórico."""
    return RUN_MAGIC_BASE + (os.getpid() % 100_000_000)


def _is_test_magic(value):
    """Identifica magics deste validador, inclusive o legado fixo."""
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and (
            value == LEGACY_TEST_MAGIC
            or RUN_MAGIC_BASE <= value < RUN_MAGIC_BASE + 100_000_000
        )
    )


def _parse_cli():
    """Lê os argumentos mínimos sem permitir cleanup de magic arbitrário."""
    args = list(sys.argv[1:])
    cleanup_magic = None
    if "--cleanup-magic" in args:
        index = args.index("--cleanup-magic")
        if index + 1 >= len(args):
            return None, None, None, "--cleanup-magic exige um valor inteiro"
        try:
            cleanup_magic = int(args[index + 1])
        except ValueError:
            return None, None, None, "--cleanup-magic exige um valor inteiro"
        del args[index:index + 2]
        if not _is_test_magic(cleanup_magic):
            return None, None, None, (
                f"magic {cleanup_magic} não pertence à faixa de teste "
                f"({LEGACY_TEST_MAGIC} ou {RUN_MAGIC_BASE}+pid)"
            )
    if len(args) > 2:
        return None, None, None, (
            "uso: validate_margin_observed.py [CCY] [BUY|SELL] "
            "[--cleanup-magic MAGIC]"
        )
    ccy = args[0].upper() if args else "CAD"
    bias = args[1].upper() if len(args) > 1 else "BUY"
    return ccy, bias, cleanup_magic, None


def _retcode(response):
    return getattr(response, "retcode", None) if response is not None else None


def _ambiguous_retcodes():
    if mt5 is None:
        return set()
    return {
        value for name in _AMBIGUOUS_RETCODE_NAMES
        if (value := getattr(mt5, name, None)) is not None
    }


def _is_ambiguous_response(response):
    return response is None or _retcode(response) in _ambiguous_retcodes()


def _is_done(response):
    return response is not None and _retcode(response) == getattr(mt5, "TRADE_RETCODE_DONE", None)


def _is_invalid_fill(response):
    invalid_fill = getattr(mt5, "TRADE_RETCODE_INVALID_FILL", None) if mt5 is not None else None
    return invalid_fill is not None and _retcode(response) == invalid_fill


def send_with_fallback(request):
    """Só tenta RETURN após rejeição explícita de filling.

    `None`, `PLACED`, timeout, conexão perdida e preenchimento parcial não são
    rejeições de filling: a ordem pode ter sido aceita, então não há reenvio.
    """
    response = mt5.order_send(dict(request))
    if not _is_invalid_fill(response):
        return response
    return_filling = getattr(mt5, "ORDER_FILLING_RETURN", None)
    if return_filling is None or request.get("type_filling") == return_filling:
        return response
    retry = dict(request)
    retry["type_filling"] = return_filling
    return mt5.order_send(retry)


def _query_positions():
    """Retorna `(known, positions, error)`; None nunca significa lista vazia."""
    if not MT5_AVAILABLE or mt5 is None:
        return False, (), "MetaTrader5 indisponível"
    try:
        positions = mt5.positions_get()
    except Exception as exc:
        return False, (), f"positions_get() lançou exceção: {exc}"
    if positions is None:
        try:
            broker_error = mt5.last_error()
        except Exception:
            broker_error = None
        return False, (), f"positions_get() retornou None: {broker_error}"
    return True, tuple(positions), None


def _positions_for_magic(magic):
    known, positions, error = _query_positions()
    if not known:
        return False, (), error
    return True, tuple(
        pos for pos in positions if getattr(pos, "magic", None) == magic
    ), None


def _query_orders():
    """Retorna `(known, orders, error)` para ordens pendentes."""
    if not MT5_AVAILABLE or mt5 is None:
        return False, (), "MetaTrader5 indisponível"
    query = getattr(mt5, "orders_get", None)
    if not callable(query):
        return False, (), "orders_get() indisponível"
    try:
        orders = query()
    except Exception as exc:
        return False, (), f"orders_get() lançou exceção: {exc}"
    if orders is None:
        try:
            broker_error = mt5.last_error()
        except Exception:
            broker_error = None
        return False, (), f"orders_get() retornou None: {broker_error}"
    return True, tuple(orders), None


def _orders_for_magic(magic):
    known, orders, error = _query_orders()
    if not known:
        return False, (), error
    return True, tuple(
        order for order in orders if getattr(order, "magic", None) == magic
    ), None


def _orphan_test_magics(positions, current_magic, orders=()):
    """Retorna magics de teste anteriores em posições ou ordens pendentes."""
    exposures = tuple(positions) + tuple(orders)
    return sorted({
        getattr(position, "magic", None)
        for position in exposures
        if _is_test_magic(getattr(position, "magic", None))
        and getattr(position, "magic", None) != current_magic
    })


def _finite_positive(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def _valid_tick(tick):
    if tick is None:
        return False
    bid = getattr(tick, "bid", None)
    ask = getattr(tick, "ask", None)
    return _finite_positive(bid) and _finite_positive(ask) and ask >= bid


def _valid_margin(value):
    return _finite_positive(value)


def _return_allowed(info):
    market_execution = getattr(mt5, "SYMBOL_TRADE_EXECUTION_MARKET", None)
    execution = getattr(info, "trade_exemode", None)
    return execution is not None and (market_execution is None or execution != market_execution)


def _select_filling(info):
    """Escolhe um filling permitido pelo símbolo, fail-closed."""
    mask = getattr(info, "filling_mode", None)
    if not isinstance(mask, int) or isinstance(mask, bool) or mask < 0:
        return None
    symbol_ioc = getattr(mt5, "SYMBOL_FILLING_IOC", 2)
    symbol_fok = getattr(mt5, "SYMBOL_FILLING_FOK", 1)
    if mask & symbol_ioc:
        return getattr(mt5, "ORDER_FILLING_IOC", None)
    if mask & symbol_fok:
        return getattr(mt5, "ORDER_FILLING_FOK", None)
    if _return_allowed(info):
        return getattr(mt5, "ORDER_FILLING_RETURN", None)
    return None


def _valid_stop_loss(action, price, sl):
    if not _finite_positive(sl):
        return False
    return sl < price if action == "BUY" else sl > price


def _prepare_orders(legs, margin_free):
    """Resolve e valida todas as pernas sem enviar nenhuma ordem."""
    prepared = []
    errors = []
    for leg in legs:
        pair = leg["pair"]
        action = leg["action"]
        broker_symbol = to_broker_symbol(pair)
        info = mt5.symbol_info(broker_symbol)
        if info is None:
            try:
                selected = mt5.symbol_select(broker_symbol, True)
            except Exception:
                selected = False
            info = mt5.symbol_info(broker_symbol) if selected is not False else None
        if info is None:
            errors.append(f"{pair}: símbolo indisponível")
            continue

        if getattr(info, "trade_mode", None) != getattr(mt5, "SYMBOL_TRADE_MODE_FULL", None):
            errors.append(f"{pair}: modo de negociação não FULL")
            continue
        if not getattr(info, "visible", False):
            try:
                if not mt5.symbol_select(broker_symbol, True):
                    errors.append(f"{pair}: symbol_select() falhou")
                    continue
            except Exception as exc:
                errors.append(f"{pair}: symbol_select() lançou exceção: {exc}")
                continue

        tick = mt5.symbol_info_tick(broker_symbol)
        if not _valid_tick(tick):
            errors.append(f"{pair}: tick inválido (bid/ask ausente, não-finito ou cruzado)")
            continue
        price = tick.ask if action == "BUY" else tick.bid
        if not _finite_positive(price):
            errors.append(f"{pair}: preço de entrada inválido")
            continue

        filling = _select_filling(info)
        if filling is None:
            errors.append(f"{pair}: nenhum modo de filling permitido pelo símbolo")
            continue

        try:
            sl = portfolio_executor._compute_catastrophic_sl(
                broker_symbol, action == "BUY", price
            )
        except Exception as exc:
            errors.append(f"{pair}: cálculo do SL falhou: {exc}")
            continue
        if not _valid_stop_loss(action, price, sl):
            errors.append(f"{pair}: SL catastrófico inválido/ausente")
            continue

        order_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL
        try:
            margin = mt5.order_calc_margin(order_type, broker_symbol, LOT, price)
        except Exception as exc:
            errors.append(f"{pair}: order_calc_margin() lançou exceção: {exc}")
            continue
        if not _valid_margin(margin):
            errors.append(f"{pair}: order_calc_margin() retornou valor inválido: {margin!r}")
            continue

        prepared.append({
            "leg": leg,
            "broker_symbol": broker_symbol,
            "price": price,
            "sl": sl,
            "margin": margin,
            "filling": filling,
        })

    if errors:
        return None, errors
    if len(prepared) != len(legs):
        return None, ["pré-flight incompleto: nem todas as pernas foram resolvidas"]
    if not _finite_positive(margin_free):
        return None, [f"margin_free inválido: {margin_free!r}"]
    margin_total = sum(item["margin"] for item in prepared)
    if not math.isfinite(margin_total) or margin_total <= 0:
        return None, [f"margem agregada inválida: {margin_total!r}"]
    if margin_total >= margin_free:
        return None, [
            f"margem insuficiente: cesta exige {margin_total:.2f}, "
            f"margin_free disponível {margin_free:.2f}"
        ]
    return prepared, []


def _confirmed_execution(symbol, magic):
    """Reconcilia resposta ambígua sem reenviar e preserva o tipo do estado."""
    for attempt in range(AMBIGUOUS_CONFIRM_ATTEMPTS):
        known, positions, error = _query_positions()
        if known:
            volume = sum(
                float(getattr(pos, "volume", 0.0))
                for pos in positions
                if getattr(pos, "magic", None) == magic
                and getattr(pos, "symbol", None) == symbol
                and _finite_positive(getattr(pos, "volume", None))
            )
            if volume > 0:
                return {"kind": "POSITION_CONFIRMED", "volume": volume}
            orders_known, orders, orders_error = _orders_for_magic(magic)
            if orders_known:
                pending_volume = sum(
                    float(getattr(order, "volume_current", 0.0)
                          or getattr(order, "volume_initial", 0.0))
                    for order in orders
                    if getattr(order, "symbol", None) == symbol
                    and _finite_positive(
                        getattr(order, "volume_current", 0.0)
                        or getattr(order, "volume_initial", 0.0)
                    )
                )
                if pending_volume > 0:
                    return {"kind": "PENDING_ORDER_CONFIRMED", "volume": pending_volume}
            elif attempt == AMBIGUOUS_CONFIRM_ATTEMPTS - 1:
                print(f"[-] Não foi possível reconciliar {symbol}: {orders_error}")
        elif attempt == AMBIGUOUS_CONFIRM_ATTEMPTS - 1:
            print(f"[-] Não foi possível reconciliar {symbol}: {error}")
        if attempt < AMBIGUOUS_CONFIRM_ATTEMPTS - 1:
            time.sleep(AMBIGUOUS_CONFIRM_DELAY_SEC)
    return None


def _account_trade_allowed(account):
    """Permissão de trading deve ser um booleano True confirmado."""
    return type(getattr(account, "trade_allowed", None)) is bool \
        and account.trade_allowed is True


def _close_test_magic_positions(magic, deadline_sec=CLEANUP_DEADLINE_SEC):
    """Fecha/cancela e confirma zero posições/ordens; estado desconhecido nunca vira sucesso."""
    deadline = time.monotonic() + deadline_sec
    closed_tickets = set()
    while True:
        positions_known, positions, positions_error = _positions_for_magic(magic)
        orders_known, orders, orders_error = _orders_for_magic(magic)
        if not positions_known or not orders_known:
            error = positions_error if not positions_known else orders_error
            print(f"[-] Cleanup sem estado confirmado: {error}")
            if time.monotonic() >= deadline:
                return {"confirmed": False, "closed": len(closed_tickets), "remaining": None}
            time.sleep(CLEANUP_POLL_INTERVAL_SEC)
            continue
        if not positions and not orders:
            return {"confirmed": True, "closed": len(closed_tickets), "remaining": 0}
        if time.monotonic() >= deadline:
            return {
                "confirmed": False,
                "closed": len(closed_tickets),
                "remaining": len(positions) + len(orders),
            }

        for order in orders:
            if time.monotonic() >= deadline:
                break
            ticket = getattr(order, "ticket", None)
            symbol = getattr(order, "symbol", None)
            if ticket is None:
                print(f"  ordem pendente ({symbol}): ticket ausente — ainda aberta")
                continue
            request = {
                "action": mt5.TRADE_ACTION_REMOVE,
                "order": ticket,
                "symbol": symbol,
                "magic": magic,
                "comment": "CSS_TEST_CANCEL",
            }
            try:
                response = mt5.order_send(request)
            except Exception as exc:
                print(f"  ordem {ticket} ({symbol}): exceção ao cancelar: {exc}")
                continue
            if _is_done(response):
                closed_tickets.add(ticket)
                print(f"  ordem {ticket} ({symbol}) cancelamento aceito; aguardando confirmação.")
            elif _is_ambiguous_response(response):
                print(f"  ordem {ticket} ({symbol}): cancelamento ambíguo — sem reenvio; reconsultando.")
            else:
                print(f"  ordem {ticket} ({symbol}): falha ao cancelar ({_retcode(response)})")

        for position in positions:
            if time.monotonic() >= deadline:
                break
            symbol = getattr(position, "symbol", None)
            ticket = getattr(position, "ticket", None)
            try:
                tick = mt5.symbol_info_tick(symbol)
            except Exception as exc:
                print(f"  ticket {ticket} ({symbol}): exceção ao consultar tick — ainda aberto ({exc})")
                continue
            if not _valid_tick(tick):
                print(f"  ticket {ticket} ({symbol}): tick inválido — ainda aberto")
                continue
            close_type = (
                mt5.ORDER_TYPE_SELL
                if position.type == mt5.ORDER_TYPE_BUY
                else mt5.ORDER_TYPE_BUY
            )
            price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask
            if not _finite_positive(price):
                print(f"  ticket {ticket} ({symbol}): preço de fechamento inválido")
                continue
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": position.volume,
                "type": close_type,
                "position": ticket,
                "price": price,
                "deviation": DEVIATION,
                "magic": magic,
                "comment": "CSS_TEST_CLOSE",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            try:
                response = send_with_fallback(request)
            except Exception as exc:
                print(f"  ticket {ticket} ({symbol}): exceção ao fechar: {exc}")
                continue
            if _is_done(response):
                closed_tickets.add(ticket)
                print(f"  ticket {ticket} ({symbol}) fechamento aceito; aguardando confirmação.")
            elif _is_ambiguous_response(response):
                print(f"  ticket {ticket} ({symbol}): fechamento ambíguo — sem reenvio; reconsultando.")
            else:
                print(f"  ticket {ticket} ({symbol}): falha ao fechar ({_retcode(response)})")

        if time.monotonic() < deadline:
            time.sleep(CLEANUP_POLL_INTERVAL_SEC)


def _account_margin_free(account):
    value = getattr(account, "margin_free", None)
    return value if _finite_positive(value) else None


def _margin_ratio_message(observed, margin_total, opened_count, total_count, incomplete):
    if incomplete:
        return (
            "Razão observado/previsto não calculada: experimento "
            f"incompleto ({opened_count}/{total_count} pernas)."
        )
    return (
        f"Razão observado/previsto: {observed / margin_total:.2f}x "
        "(sanidade, não validação — inclui PnL, comissão e netting/hedging)"
    )


def _experiment_exit_code(failed, ambiguous, measurement_incomplete):
    return 1 if failed or ambiguous or measurement_incomplete else 0


def main():
    ccy, bias, cleanup_magic, cli_error = _parse_cli()
    if cli_error:
        print(f"[-] RECUSADO: {cli_error}")
        return 2
    magic = _run_magic()
    cleanup_target = cleanup_magic if cleanup_magic is not None else magic
    cleanup_armed = False

    configured_path = os.environ.get("CSS_MT5_TERMINAL_PATH", "")
    if not _terminal_path_is_isolated(configured_path):
        print(f"[-] RECUSADO: CSS_MT5_TERMINAL_PATH={configured_path!r} não aponta para "
              f".../{REQUIRED_PATH_MARKER}/terminal64.exe. Este script só roda contra a "
              "instância isolada.")
        return 1
    if not MT5_AVAILABLE or mt5 is None or not mt5.initialize(path=MT5_PATH):
        print(f"[-] initialize() falhou: {mt5.last_error() if mt5 else 'MT5 indisponível'}")
        return 1

    try:
        if not _terminal_identity_matches(configured_path):
            observed = getattr(mt5.terminal_info(), "path", None)
            print(f"[-] RECUSADO: terminal conectado não é o isolado configurado "
                  f"(observado={observed!r}, esperado=.../{REQUIRED_PATH_MARKER}) — "
                  "mt5.initialize() pode ter anexado a outro terminal já em execução.")
            return 1

        identity = check_account_identity()
        if not identity["allowed"]:
            print(f"[-] RECUSADO (identidade de conta): {identity['message']}")
            return 1

        account = mt5.account_info()
        margin_free = _account_margin_free(account) if account is not None else None
        if account is None or margin_free is None:
            print("[-] RECUSADO: account_info()/margin_free inválido antes do teste.")
            return 1
        if account.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
            print(f"[-] RECUSADO: conta {account.login} não é demo — este script nunca envia ordem numa conta real.")
            return 1
        if not _account_trade_allowed(account):
            print("[-] RECUSADO: não foi possível confirmar que a conta permite trading.")
            return 1

        positions_known, all_positions, positions_error = _query_positions()
        orders_known, all_orders, orders_error = _query_orders()
        if not positions_known or not orders_known:
            error = positions_error if not positions_known else orders_error
            print(f"[-] RECUSADO: não foi possível confirmar exposição prévia: {error}")
            return 1
        orphan_magics = _orphan_test_magics(all_positions, magic, all_orders)
        if orphan_magics:
            listed = ", ".join(str(item) for item in orphan_magics)
            print(
                f"[!] Há exposição de teste de execução(ões) anterior(es) "
                f"com magic {listed}."
            )
        existing = tuple(
            position for position in all_positions
            if getattr(position, "magic", None) == magic
        )
        existing_orders = tuple(
            order for order in all_orders
            if getattr(order, "magic", None) == magic
        )
        if existing or existing_orders:
            print(f"[-] RECUSADO: magic desta execução ({magic}) já possui "
                  f"{len(existing)} posição(ões) e {len(existing_orders)} ordem(ns); "
                  "nenhuma será tocada.")
            return 1
        unresolved_orphans = [
            item for item in orphan_magics if item != cleanup_magic
        ]
        if unresolved_orphans:
            listed = ", ".join(str(item) for item in unresolved_orphans)
            print(
                f"[-] RECUSADO: exposição antiga não coberta pelo cleanup explícito "
                f"({listed}). Limpe com --cleanup-magic MAGIC antes de continuar."
            )
            return 1
        cleanup_armed = True

        if cleanup_magic is not None:
            print(f"Cleanup explícito solicitado para o magic {cleanup_magic}.")
            return 0

        legs = get_portfolio_pairs(ccy, bias)
        if len(legs) != 7:
            print(f"[-] RECUSADO: cesta esperava 7 pernas, encontrou {len(legs)}.")
            return 1
        print(f"Cesta de TESTE {ccy} ({bias}), magic {magic}, lote {LOT}")
        print(f"margin_free ANTES: {margin_free:.2f} {account.currency}\n")

        prepared, errors = _prepare_orders(legs, margin_free)
        if errors:
            print("[-] RECUSADO antes de qualquer ordem — pré-flight falhou:")
            for item in errors:
                print(f"    - {item}")
            return 1
        margin_total = sum(item["margin"] for item in prepared)

        account_fresh = mt5.account_info()
        fresh_margin_free = _account_margin_free(account_fresh) if account_fresh is not None else None
        if fresh_margin_free is None or fresh_margin_free <= margin_total:
            print("[-] RECUSADO antes de qualquer ordem — margin_free fresco não cobre a margem agregada.")
            return 1

        print(f"Pré-flight OK: margem agregada {margin_total:.2f}; "
              f"margin_free fresco {fresh_margin_free:.2f}.")
        failed = False
        ambiguous = False
        measurement_incomplete = False
        opened = []
        pending_confirmed = []
        for item in prepared:
            leg = item["leg"]
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": item["broker_symbol"],
                "volume": LOT,
                "type": mt5.ORDER_TYPE_BUY if leg["action"] == "BUY" else mt5.ORDER_TYPE_SELL,
                "price": item["price"],
                "sl": item["sl"],
                "deviation": DEVIATION,
                "magic": magic,
                "comment": f"CSS_TEST_{ccy}_{bias}",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": item["filling"],
            }
            try:
                response = send_with_fallback(request)
            except Exception as exc:
                print(f"  {leg['pair']:<8} {leg['action']:<5} exceção no envio: {exc}")
                failed = True
                break
            if _is_done(response):
                opened.append(response)
                print(f"  {leg['pair']:<8} {leg['action']:<5} preço={item['price']:<10.5f} "
                      f"previsto={item['margin']:<8.2f} sl={item['sl']} ticket={getattr(response, 'order', None)}")
                continue
            if _is_ambiguous_response(response):
                execution = _confirmed_execution(item["broker_symbol"], magic)
                if execution is not None and execution["kind"] == "POSITION_CONFIRMED":
                    opened.append(response)
                    print(f"  {leg['pair']:<8} {leg['action']:<5} resposta ambígua, "
                          f"posição confirmada (volume {execution['volume']}); sem reenvio.")
                elif execution is not None:
                    pending_confirmed.append(response)
                    measurement_incomplete = True
                    print(f"  {leg['pair']:<8} {leg['action']:<5} ordem pendente confirmada "
                          f"(volume {execution['volume']}); sem reenvio, sem fill confirmado.")
                else:
                    print(f"  {leg['pair']:<8} {leg['action']:<5} resposta ambígua não confirmada; "
                          "experimento abortado, sem reenvio.")
                    ambiguous = True
                    break
            else:
                print(f"  {leg['pair']:<8} {leg['action']:<5} falha confirmada no envio: {_retcode(response)}")
                failed = True
                break

        if failed or ambiguous:
            print("[!] Experimento incompleto; não serão enviadas mais pernas.")
        if opened:
            time.sleep(1.0)
            account_after = mt5.account_info()
            after_margin_free = _account_margin_free(account_after) if account_after is not None else None
            if after_margin_free is None:
                print("[-] margin_free DEPOIS indisponível — validação incompleta; cleanup continuará.")
                measurement_incomplete = True
            else:
                observed = margin_free - after_margin_free
                print(f"\nmargin_free DEPOIS de abrir: {after_margin_free:.2f} {account.currency}")
                print(f"Previsto (soma order_calc_margin): {margin_total:.2f} {account.currency}")
                print(f"Observado (queda real em margin_free): {observed:.2f} {account.currency}")
                print(_margin_ratio_message(
                    observed, margin_total, len(opened), len(prepared),
                    failed or ambiguous or measurement_incomplete,
                ))
        return _experiment_exit_code(failed, ambiguous, measurement_incomplete)
    finally:
        cleanup_failed = False
        if cleanup_armed:
            print(f"\nFechando e confirmando posições do magic {cleanup_target}...")
            result = _close_test_magic_positions(cleanup_target)
            if result["confirmed"]:
                print(f"Cleanup confirmado: {result['closed']} posição(ões) fechada(s), zero restantes.")
            else:
                print("[!] CLEANUP NÃO CONFIRMADO: estado final desconhecido ou posição restante. "
                      "Verificar manualmente na instância isolada.")
                cleanup_failed = True
        else:
            print("[-] Cleanup não armado: identidade/demo/posição prévia não foram validados; nenhuma posição será tocada.")
        try:
            mt5.shutdown()
        except Exception:
            pass
        # Um `return 0` dentro do try já foi avaliado quando o finally começa.
        # Só substitui sucesso pendente; durante outra exceção, preserva o
        # traceback original. (P1 MFC23-02 / borda de cleanup.)
        if cleanup_failed and sys.exc_info()[0] is None:
            raise SystemExit(1)


if __name__ == "__main__":
    sys.exit(main())
