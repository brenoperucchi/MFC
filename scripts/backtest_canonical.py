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
  - A ESTIMATIVA de custo (spread ida+volta + swap da noite, via tick
    corrente da conta conectada — não custo realizado, ver docstring de
    CostModel em agents/portfolio_executor.py) é DESCONTADA. O backtest
    herdado ignora custo por completo.

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
# o sistema ao vivo usa pra ESTIMAR o custo de cada cesta aberta (achado em
# revisão: Codex, achado 2/4 rodada 5 — "custo real" aqui contradizia a
# própria docstring de CostModel), em vez de manter uma segunda cópia da
# lógica.
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


def _tally_cost_quality(costs, degraded_baskets, swap_unmodeled_baskets):
    """Incrementa os dois contadores a partir de um CostModel (ou qualquer
    objeto com os mesmos dois atributos) depois de uma chamada a
    .basket() — função pura e testável, separada do laço principal DE
    PROPÓSITO (achado em revisão: Codex + mfc-rev-2, achado 4 rodada 4,
    confirmado pelos dois: a rodada 3 testou só _cost_quality_summary_lines,
    a FORMATAÇÃO — não a LIGAÇÃO onde o bug da rodada 3 realmente morava,
    que era o laço não ler last_basket_swap_unmodeled. Reproduzido pelo
    mfc-rev-2: apagar essas duas linhas do laço original deixava a suíte
    inteira verde). Só precisa de um objeto com os dois atributos — não
    precisa mockar MT5, séries ou o motor de confluência."""
    if costs.last_basket_degraded:
        degraded_baskets += 1
    if costs.last_basket_swap_unmodeled:
        swap_unmodeled_baskets += 1
    return degraded_baskets, swap_unmodeled_baskets


def _cost_quality_summary_lines(baskets, degraded_baskets, swap_unmodeled_baskets):
    """Linhas de aviso sobre a qualidade do custo medido no backtest —
    função pura e testável, separada do laço principal de propósito
    (achado em revisão: Codex + mfc-rev-2, achado 4 rodada 3, confirmado
    pelos dois independentemente: a rodada anterior só ligou
    last_basket_swap_unmodeled no logger ao vivo, não aqui. Numa corretora
    com swap fora de pontos, last_basket_degraded ficava vazio, o aviso
    antigo nunca disparava, e ~45% do custo real desaparecia do PnL
    LÍQUIDO em silêncio — medido pelo mfc-rev-2. Achado 4 original de
    novo, reintroduzido por outra porta).

    Duas categorias, DUAS mensagens — não a mesma bandeira nem o mesmo
    tom: `degraded` é dado perdido de verdade (PnL líquido otimista, vale
    alarme); `swap_unmodeled` é limitação conhecida e documentada do
    modelo (spread real contado, só o swap ficou de fora — informativo,
    não é a mesma classe de problema que gerava alarme permanente na
    rodada anterior)."""
    lines = []
    if degraded_baskets:
        lines.append(
            f"⚠ {degraded_baskets}/{baskets} cesta(s) tiveram ao menos uma perna sem símbolo/"
            f"tick/taxa de conversão disponível na hora da medição — o custo contado pra "
            f"elas ficou parcialmente ZERADO, não é medição completa. PnL LÍQUIDO acima está "
            f"OTIMISTA na proporção dessas cestas.")
    if swap_unmodeled_baskets:
        lines.append(
            f"ℹ {swap_unmodeled_baskets}/{baskets} cesta(s) tiveram ao menos uma perna com "
            f"swap_mode fora de PONTOS — o spread dessas pernas está no custo normalmente, "
            f"só o swap não tem fórmula fiel pra esse modo e entrou como 0.0. Não é dado "
            f"perdido (ao contrário do aviso acima); o PnL LÍQUIDO dessas cestas difere do "
            f"custo de swap real em direção desconhecida (achado em revisão: Codex — swap "
            f"pode ser débito OU crédito; zerar um crédito real deixa o PnL mais pessimista, "
            f"não mais otimista).")
    return lines


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
    # CostModel é criado POR CESTA logo abaixo, não aqui fora do laço (achado
    # em revisão: Codex, achado 4 rodada 2, e decisão do usuário sobre um
    # conflito entre revisores). Um CostModel único reutilizado por todo o
    # backtest (dezenas/centenas de noites) faz seu cache (_leg, _rate,
    # _degraded) persistir pro período inteiro: uma falha transitória de MT5
    # na primeira noite tocada fica congelada em (0.0, 0.0) e é reaplicada
    # em TODA noite seguinte que toque aquele par, mesmo que o dado real
    # estivesse disponível de novo minutos depois — sem nunca re-tentar. O
    # caminho ao vivo (measure_and_log_basket_cost) já cria um CostModel
    # novo por chamada; aqui replica a mesma granularidade. Ganho de graça
    # (mfc-rev-2, rodada 3): também reseta CostModel._rate por cesta — com a
    # instância única, uma taxa de conversão que falhasse uma vez ficava
    # None pro backtest inteiro.
    #
    # Custo medido (mfc-rev-2, rodada 3, 90 dias × 8 cestas/noite = 720
    # cestas, Market Watch começando vazio): ~62× mais chamadas MT5 que a
    # instância única (12.870 vs. 208) — NÃO é "algumas chamadas" como uma
    # versão anterior deste comentário afirmava sem medir. A ~0,5ms por
    # chamada IPC local, ≈6,4s no total — aceitável pra ferramenta de
    # diagnóstico offline, mas registrado aqui pra não repetir a mesma
    # subestimativa. Ressalva de semântica (Codex, rodada 3): como o custo
    # de cada cesta histórica consulta o tick CORRENTE do MT5 (não um preço
    # histórico daquela noite), o número pode variar entre duas execuções
    # do mesmo backtest, ou entre a primeira e a última cesta de uma
    # execução longa — é uma estimativa amostrada no momento da execução,
    # não um custo histórico reconstruído. Isso já era verdade ANTES desta
    # mudança (a instância única também usava o tick corrente, só que
    # cacheado); esta mudança não piora a propriedade, só a torna mais
    # visível ao reconsultar mais vezes.

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
    # Achado 4 (revisão de ad44e12/c24a44c, mfc-rev-2): CostModel.leg()
    # devolve (0.0, 0.0) quando falta símbolo/tick/taxa, e o cálculo segue
    # em frente — sem contar isso, cada custo contribui pro PnL LÍQUIDO
    # abaixo como se fosse medição completa, mesmo quando não é. Não
    # aborta (o backtest é ferramenta de diagnóstico, não gate de
    # execução): acumula e avisa alto no resumo final.
    degraded_baskets = 0
    swap_unmodeled_baskets = 0

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
            costs = CostModel(LOT)  # novo por cesta — ver comentário acima
            b_cost = costs.basket(ccy, bias)
            degraded_baskets, swap_unmodeled_baskets = _tally_cost_quality(
                costs, degraded_baskets, swap_unmodeled_baskets)
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
    for line in _cost_quality_summary_lines(baskets, degraded_baskets, swap_unmodeled_baskets):
        print()
        print(line)
    print()
    print(f"{'moeda':<7} {'cestas':>7} {'bruto':>10} {'líquido':>10}")
    print("-" * 37)
    for c in sorted(per_ccy, key=lambda x: per_ccy[x]["net"], reverse=True):
        d = per_ccy[c]
        if d["n"]:
            print(f"{c:<7} {d['n']:>7} {d['gross']:>10.2f} {d['net']:>10.2f}")


if __name__ == "__main__":
    run(days=int(sys.argv[1]) if len(sys.argv) > 1 else 45)
