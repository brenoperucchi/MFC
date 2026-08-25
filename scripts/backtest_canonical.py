"""
BACKTEST CANÔNICO — usa o MESMO pipeline que roda ao vivo.

Diferença essencial pro web/history_tracker.py::run_full_backtest(), que é
código órfão herdado (não instanciado em lugar nenhum) e recalcula os scores
com matemática própria (LWMA21 + ATR·SMA20):

  - Aqui os scores vêm de web/css_service.py::calculate_full_css() — a
    mesma função que alimenta a dashboard (LWMA21 + ATR·SMA100/10, a
    canônica documentada em docs/MATHEMATICAL_MODELS.md).
  - A decisão de operar vem de agents/confluence_engine.py::
    evaluate_currency_confluence() — o mesmo motor que decide ao vivo.
  - O custo real (spread ida+volta + swap da noite) é DESCONTADO, medido
    na conta conectada. O backtest herdado ignora custo por completo.

Se este backtest e o sistema ao vivo divergirem, é bug — não diferença de
metodologia. É esse o ponto.

Uso:
    python scripts/backtest_canonical.py [dias]      # default 45

Somente leitura: não envia ordem, não grava em data/.
"""

import os
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from web.css_service import (
    ALL_28_PAIRS, CURRENCIES, MT5_AVAILABLE, mt5, MT5_PATH,
    calculate_full_css, get_tf_constant, to_broker_symbol,
)
from agents.confluence_engine import evaluate_currency_confluence
# CostModel mora em agents/portfolio_executor.py (o executor de verdade) —
# era duplicada aqui até 24/08; agora o backtest importa a mesma classe que
# o sistema ao vivo usa pra medir custo real de cada cesta aberta, em vez de
# manter uma segunda cópia da lógica.
from agents.portfolio_executor import get_portfolio_pairs, ensure_mt5, CostModel
from web.history_tracker import GMT_OFFSET, convert_pnl_to_usd

LOT = 0.01
ENTRY_HOUR_BRT = 21
EXIT_HOUR_BRT = 8
TFS = ("MN1", "W1", "D1", "H4", "H1")

# Barras a puxar por TF. Precisa cobrir a janela do backtest + aquecimento
# do ATR(100)/LWMA(21). calculate_full_css já soma +150 internamente.
TF_COUNTS = {"MN1": 60, "W1": 120, "D1": 200, "H4": 600, "H1": 1600}


def _brt_to_server(dt_brt):
    """BRT -> hora do servidor do broker (o inverso do GMT_OFFSET do EA)."""
    return dt_brt - timedelta(hours=GMT_OFFSET)


def load_series():
    """Séries de score por TF, via a função CANÔNICA (a mesma da dashboard)."""
    series = {}
    for tf in TFS:
        res, times, _ = calculate_full_css(get_tf_constant(tf), count=TF_COUNTS[tf], mode="standard")
        if res is None:
            print(f"[-] Sem dados para {tf} — abortando.")
            return None
        idx = pd.to_datetime(pd.Series(times))
        series[tf] = {"scores": res, "times": idx}
        print(f"[+] {tf}: {len(times)} barras  ({times[0]} -> {times[-1]})")
    return series


def load_h1_prices():
    """Preço de abertura H1 por par, indexado por tempo do servidor."""
    prices = {}
    for pair in ALL_28_PAIRS:
        rates = mt5.copy_rates_from_pos(to_broker_symbol(pair), mt5.TIMEFRAME_H1, 0, 1800)
        if rates is None or len(rates) < 100:
            continue
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        prices[pair] = df.set_index("time")["open"]
    return prices


def _idx_at_or_before(times, target):
    """Índice da última barra em ou antes de `target`; None se não houver."""
    pos = times.searchsorted(target, side="right") - 1
    return int(pos) if pos >= 0 else None


def evaluate_at(series, entry_server_dt):
    """Roda o pipeline canônico como se fosse `entry_server_dt`.
    Devolve {ccy: veredito} — mesmo formato que a dashboard usa."""
    slices = {}
    for tf in TFS:
        i = _idx_at_or_before(series[tf]["times"], entry_server_dt)
        if i is None or i < 30:
            return None
        slices[tf] = i
    out = {}
    for ccy in CURRENCIES:
        args = [series[tf]["scores"][ccy][: slices[tf] + 1] for tf in TFS]
        out[ccy] = evaluate_currency_confluence(ccy, *args)
    return out


def run(days=45):
    if not ensure_mt5():
        print("[-] MT5 não conectado.")
        return

    print(f"[*] Carregando séries canônicas (mesmo motor da dashboard)...")
    series = load_series()
    if not series:
        return
    print(f"[*] Carregando preços H1 de {len(ALL_28_PAIRS)} pares...")
    prices = load_h1_prices()
    print(f"[+] {len(prices)} pares com preço disponível.")
    costs = CostModel(LOT)

    # Pontos de entrada: 21:00 BRT de cada dia, convertidos pra hora do servidor
    h1_times = series["H1"]["times"]
    last_dt = h1_times.iloc[-1]
    entries = []
    for d in range(days, 0, -1):
        brt_day = (datetime.now() - timedelta(days=d)).replace(
            hour=ENTRY_HOUR_BRT, minute=0, second=0, microsecond=0)
        srv = _brt_to_server(brt_day)
        if srv <= last_dt:
            entries.append((brt_day, srv))

    print(f"[*] {len(entries)} noites candidatas.\n")

    nights = baskets = 0
    gross = cost_total = 0.0
    wins = 0
    per_ccy = {c: {"n": 0, "gross": 0.0, "net": 0.0} for c in CURRENCIES}

    for brt_dt, srv_dt in entries:
        verdicts = evaluate_at(series, srv_dt)
        if verdicts is None:
            continue
        actives = [(c, v) for c, v in verdicts.items() if v["trade_bias"] in ("COMPRA", "VENDA")]
        if not actives:
            continue

        exit_srv = srv_dt + timedelta(hours=11)
        night_gross = night_cost = 0.0
        opened = 0

        for ccy, v in actives:
            bias = "BUY" if v["trade_bias"] == "COMPRA" else "SELL"
            legs = get_portfolio_pairs(ccy, bias)
            b_gross = 0.0
            ok = True
            for leg in legs:
                ser = prices.get(leg["pair"])
                if ser is None:
                    ok = False
                    break
                try:
                    p_in = float(ser.asof(srv_dt))
                    p_out = float(ser.asof(exit_srv))
                except Exception:
                    ok = False
                    break
                if not (p_in > 0 and p_out > 0):
                    ok = False
                    break
                pnl, _ = convert_pnl_to_usd(leg["pair"], leg["action"], p_in, p_out, LOT)
                b_gross += pnl
            if not ok:
                continue
            b_cost = costs.basket(ccy, bias)
            night_gross += b_gross
            night_cost += b_cost
            opened += 1
            per_ccy[ccy]["n"] += 1
            per_ccy[ccy]["gross"] += b_gross
            per_ccy[ccy]["net"] += b_gross - b_cost

        if opened == 0:
            continue
        nights += 1
        baskets += opened
        gross += night_gross
        cost_total += night_cost
        if night_gross - night_cost >= 0:
            wins += 1

    if nights == 0:
        print("[-] Nenhuma noite operável reconstruída.")
        return

    net = gross - cost_total
    print("=" * 62)
    print(f"  BACKTEST CANÔNICO — {days} dias (pipeline idêntico ao ao vivo)")
    print("=" * 62)
    print(f"noites operadas       : {nights}")
    print(f"cestas totais         : {baskets}  (média {baskets/nights:.2f}/noite)")
    print(f"noites lucrativas     : {wins}  ({wins/nights*100:.1f}%)")
    print()
    print(f"PnL BRUTO             : ${gross:>9.2f}   (${gross/nights:>6.2f}/noite)")
    print(f"custo spread+swap     : ${-cost_total:>9.2f}   (${-cost_total/nights:>6.2f}/noite)")
    print(f"PnL LÍQUIDO           : ${net:>9.2f}   (${net/nights:>6.2f}/noite)")
    print()
    print(f"custo médio por cesta : ${cost_total/baskets:.2f}")
    print()
    print(f"{'moeda':<7} {'cestas':>7} {'bruto':>10} {'líquido':>10}")
    print("-" * 37)
    for c in sorted(per_ccy, key=lambda x: per_ccy[x]["net"], reverse=True):
        d = per_ccy[c]
        if d["n"]:
            print(f"{c:<7} {d['n']:>7} {d['gross']:>10.2f} {d['net']:>10.2f}")


if __name__ == "__main__":
    run(days=int(sys.argv[1]) if len(sys.argv) > 1 else 45)
