"""
FERRAMENTA DE VALIDAÇÃO MANUAL, NÃO PARTE DO PIPELINE DE PRODUÇÃO.

Passo 1 do runbook de deploy (herdr-ask consulta 4, 2026-08-28): validar
order_calc_margin() no terminal MT5 real ANTES de levar o gate de margem
agregada (item 2 do plano de reconciliação) pra produção — order_calc_margin()
é Windows-only, nunca rodou de verdade até este ponto (só mockado nos testes).

Só CALCULA margem pras 7 pernas de uma cesta (BUY, moeda escolhida via
argv[1], default CAD) — não envia NENHUMA ordem. Pergunta as 3 coisas que a
mfc-rev-2 pediu: (1) cada order_calc_margin() volta número finito e positivo?
(2) a soma bate com a ordem de grandeza esperada pra alavancagem da conta?
(3) a moeda usada é a mesma de account_info().currency?
"""

import math
import ntpath
import os
import sys

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from web.css_service import MT5_AVAILABLE, MT5_PATH, mt5, to_broker_symbol
from agents.portfolio_executor import get_portfolio_pairs, PORTFOLIO_MAGICS, check_account_identity


REQUIRED_PATH_MARKER = "mfc-backtest"


def _canonical_windows_path(value):
    """Normaliza um caminho Windows pra comparação exata (mesmo helper de
    scripts/backtest_engine_compare.py::_canonical_windows_path e
    scripts/validate_margin_observed.py)."""
    return ntpath.normcase(str(value or "").replace("/", "\\")).rstrip("\\")


def _terminal_path_is_isolated(configured_path):
    """Recusa por ESTRUTURA de caminho, não substring — mesmo achado
    herdr-review mfc-56 (MFC56-02): este script não tinha NENHUM guard de
    terminal antes desta correção, então order_calc_margin() podia rodar
    contra qualquer terminal MT5 já aberto na máquina."""
    canon = _canonical_windows_path(configured_path)
    return (
        ntpath.basename(canon) == "terminal64.exe"
        and ntpath.basename(ntpath.dirname(canon)) == REQUIRED_PATH_MARKER
    )


def _terminal_identity_matches(configured_path):
    """Confere, DEPOIS de mt5.initialize(), que o terminal REALMENTE
    conectado é o caminho configurado — mt5.initialize(path=X) pode anexar a
    um terminal já em execução em vez de abrir X."""
    if mt5 is None:
        return False
    terminal_info = mt5.terminal_info()
    if terminal_info is None:
        return False
    observed_dir = _canonical_windows_path(getattr(terminal_info, "path", None))
    expected_dir = _canonical_windows_path(ntpath.dirname(configured_path))
    return observed_dir == expected_dir


def main():
    ccy = sys.argv[1].upper() if len(sys.argv) > 1 else "CAD"
    bias = sys.argv[2].upper() if len(sys.argv) > 2 else "BUY"

    if not MT5_AVAILABLE or mt5 is None:
        print("[-] MetaTrader5 indisponível neste ambiente.")
        return 1

    configured_path = os.environ.get("CSS_MT5_TERMINAL_PATH", "")
    if not _terminal_path_is_isolated(configured_path):
        print(f"[-] RECUSADO: CSS_MT5_TERMINAL_PATH={configured_path!r} não aponta para "
              f".../{REQUIRED_PATH_MARKER}/terminal64.exe. Este script só roda contra a "
              "instância isolada.")
        return 1

    if not mt5.initialize(path=MT5_PATH):
        print(f"[-] mt5.initialize() falhou: {mt5.last_error()}")
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

        acc = mt5.account_info()
        if acc is None:
            print("[-] account_info() retornou None.")
            return 1
        print(f"Conta: {acc.login} @ {acc.server} | leverage=1:{acc.leverage} | "
              f"margin_free={acc.margin_free:.2f} {acc.currency} | balance={acc.balance:.2f}")

        magic = PORTFOLIO_MAGICS.get(ccy)
        if magic is None:
            print(f"[-] Moeda inválida: {ccy}")
            return 1

        legs = get_portfolio_pairs(ccy, bias)
        print(f"\nCesta {ccy} ({bias}), magic {magic}, {len(legs)} pernas, lote 0.01:\n")

        total = 0.0
        falhas = []
        hdr = f"{'par':<10} {'ação':<5} {'preço':>10} {'margem calculada':>18}"
        print(hdr)
        print("-" * len(hdr))
        for leg in legs:
            broker_sym = to_broker_symbol(leg["pair"])
            info = mt5.symbol_info(broker_sym)
            if info is None:
                mt5.symbol_select(broker_sym, True)
                info = mt5.symbol_info(broker_sym)
            if info is None:
                print(f"{leg['pair']:<10} {leg['action']:<5} {'--':>10} {'símbolo não resolveu':>18}")
                falhas.append(leg["pair"])
                continue
            tick = mt5.symbol_info_tick(broker_sym)
            if tick is None:
                print(f"{leg['pair']:<10} {leg['action']:<5} {'--':>10} {'sem tick':>18}")
                falhas.append(leg["pair"])
                continue
            order_type = mt5.ORDER_TYPE_BUY if leg["action"] == "BUY" else mt5.ORDER_TYPE_SELL
            price = tick.ask if leg["action"] == "BUY" else tick.bid
            try:
                margem = mt5.order_calc_margin(order_type, broker_sym, 0.01, price)
            except Exception as e:
                print(f"{leg['pair']:<10} {leg['action']:<5} {price:>10.5f} {'exceção: ' + str(e):>18}")
                falhas.append(leg["pair"])
                continue
            ok = isinstance(margem, (int, float)) and not isinstance(margem, bool) \
                and math.isfinite(margem) and margem > 0
            marcador = "" if ok else "  <-- FALHA (não-finito/negativo/None)"
            print(f"{leg['pair']:<10} {leg['action']:<5} {price:>10.5f} {margem!s:>18}{marcador}")
            if ok:
                total += margem
            else:
                falhas.append(leg["pair"])

        print(f"\nSoma das {len(legs)} pernas: {total:.2f} {acc.currency}")
        print(f"margin_free atual: {acc.margin_free:.2f} {acc.currency}")
        print(f"Alavancagem da conta: 1:{acc.leverage}")

        print("\n--- Perguntas de validação (herdr-ask consulta 4, mfc-rev-2) ---")
        print(f"1) Todas as 7 pernas deram número finito e positivo? "
              f"{'SIM' if not falhas else 'NÃO — falharam: ' + ', '.join(falhas)}")
        # Estimativa grosseira de referência: nocional_base / alavancagem, só
        # pra checar ORDEM DE GRANDEZA (não é o cálculo real do broker).
        estimativa = (1000.0 / acc.leverage) * len(legs) if acc.leverage else None
        if estimativa:
            razao = total / estimativa if estimativa > 0 else None
            print(f"2) Ordem de grandeza: soma real = {total:.2f}, estimativa nocional/alavancagem "
                  f"= {estimativa:.2f} (razão: {razao:.2f}x)" if razao else "2) não calculável")
        print(f"3) Moeda da margem calculada é a mesma de margin_free? "
              f"(order_calc_margin não expõe moeda própria — presumir moeda da conta, "
              f"{acc.currency}, é a suposição que o código de produção faz)")

    finally:
        mt5.shutdown()

    # A printed "NÃO" must also fail the machine-readable gate.  Otherwise a
    # wrapper/runbook can continue as if all seven legs were validated.
    return 1 if falhas or len(legs) != 7 else 0


if __name__ == "__main__":
    sys.exit(main())
