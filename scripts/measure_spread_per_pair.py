"""
FERRAMENTA DE DIAGNÓSTICO, NÃO PARTE DO PIPELINE DE PRODUÇÃO.

Passo seguinte depois do achado "custo é ~95% spread, não swap"
(herdr-ask mfc-5 + árbitro efêmero, 2026-08-28): medir spread por PAR
individual, não só o agregado — pra responder "cortar pernas ajudaria, e
quais?" com dado, não achismo.

Duas medições, combinadas na mesma tabela:
1. Spread ida+volta ATUAL de cada um dos 28 pares, a 0,01 lote (via
   CostModel.leg(), o mesmo cálculo que o backtest usa) — snapshot do
   tick corrente, mesma ressalva de sempre (varia com o momento em que
   roda, não é custo histórico).
2. Frequência de SINAL ativo (não necessariamente cesta REALMENTE aberta —
   ver MFC22-01 abaixo) de cada par nas decisões do motor CONFIGURADO
   (`CSS_CONFLUENCE_ENGINE`, resolvido uma vez em `main()` — achado
   MFC76-03, herdr-review mfc-76: era rotulado "Port A 5-TF" incondicional
   mesmo já rodando com o default 3-TF desde a flag existir), na mesma
   janela/máscara de `backtest_engine_compare.py`.

Correções da herdr-review rodada 22 (mfc-rev):
- MFC22-02: `ensure_mt5()` agora roda ANTES de qualquer medição de spread —
  antes, `measure_static_spread()` podia rodar num processo onde o MT5
  ainda não tinha sido inicializado, e `CostModel.leg()` devolve
  silenciosamente (0.0, 0.0) pra símbolo/tick indisponível. Pares
  degradados agora são detectados e avisados, não contados como custo zero.
- MFC22-01: a frequência medida é de SINAL ativo (trade_bias != NEUTRO),
  não de cesta efetivamente aberta — não passa pelos gates de margem,
  idempotência, colisão netting, símbolo/tick ou filling que
  `open_portfolio_basket()` aplica de verdade. Rotulado explicitamente como
  estimativa, não frequência real de execução.
- MFC22-05: a leitura de razão_custo foi suavizada — é uma heurística de
  iliquidez (filtro exploratório), não prova de que nenhum motor pode
  lucrar naquele par.
- P3-2: `measure_cost_ratio()` agora monta um `rates_dict` a partir dos
  preços H1 já carregados (os 7 pares canônicos `*USD`/`USD*`) e passa pra
  `convert_pnl_to_usd()`, em vez de deixar pares cross caírem na tabela
  hardcoded de `web/history_tracker.py` — mesma fonte de conversão que
  `CostModel._usd_rate()` usa (tick ao vivo) e que o movimento histórico
  agora também usa (preço da própria noite), reduzindo a divergência entre
  numerador e denominador da razão.
"""

import os
import sys
from datetime import datetime, timedelta

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from web.css_service import MT5_AVAILABLE, MT5_PATH, mt5, to_broker_symbol, ALL_28_PAIRS
from agents.portfolio_executor import get_portfolio_pairs, CostModel, ensure_mt5
from web.history_tracker import convert_pnl_to_usd
from scripts.backtest_canonical import (
    BRT, LOT, ENTRY_HOUR_BRT, TFS, CURRENCIES, load_series, load_h1_prices, h1_bars_for_days,
    usd_cross_rates_dict, _brt_to_server, evaluate_at, is_market_session_valid,
    check_contract_size_consistency,
)
from confluence_config import resolve_confluence_engine


def measure_static_spread(lot=0.01):
    """Spread ida+volta atual de cada um dos 28 pares, via o mesmo cálculo
    que CostModel.basket() usa internamente (leg() * 2.0). Retorna
    {pair: (spread_roundtrip, degraded_bool)} — MFC22-02: degradado
    (símbolo/tick/taxa indisponível) fica marcado, não silenciosamente
    zero."""
    costs = CostModel(lot)
    out = {}
    for pair in ALL_28_PAIRS:
        spread, swap = costs.leg(pair, "BUY")  # spread não depende de BUY/SELL
        degraded = (spread == 0.0 and swap == 0.0)
        out[pair] = (spread * 2.0, degraded)
    return out


def measure_pair_frequency(days=45, engine=None):
    """Quantas vezes cada um dos 28 pares apareceu numa decisão de SINAL
    ATIVO (trade_bias != NEUTRO) do motor configurado (`engine`), na mesma
    janela/máscara de `backtest_engine_compare.py`.

    MFC22-01 (herdr-review rodada 22, mfc-rev): isto NÃO é frequência de
    cesta efetivamente aberta — não reproduz os gates de margem,
    idempotência, colisão netting/símbolo/tick/filling que
    `open_portfolio_basket()` aplica de verdade. É uma estimativa
    contrafactual de "quantas vezes o sinal apontaria pra essa perna", útil
    pra ranquear pares por exposição típica, não pra contar execução real.

    `engine` (achados MFC76-02/P3-2, herdr-review mfc-76): OBRIGATÓRIO —
    resolvido uma única vez em main() antes de ensure_mt5(), nunca aqui
    dentro. Reler por chamador violaria o contrato de
    confluence_config.py ("uma vez por rodada"), e resolver DEPOIS de
    ensure_mt5() gastaria a conexão MT5 antes de uma config inválida
    conseguir abortar."""
    if engine is None:
        raise ValueError("measure_pair_frequency requer engine= resolvido pelo chamador")
    if not ensure_mt5():
        print("[-] MT5 não conectado.")
        return None

    series = load_series(window_start_brt=datetime.now(BRT) - timedelta(days=days))
    if not series:
        return None

    freq = {pair: 0 for pair in ALL_28_PAIRS}
    nights_evaluated = 0
    for d in range(days, 0, -1):
        brt_day = (datetime.now(BRT) - timedelta(days=d)).replace(
            hour=ENTRY_HOUR_BRT, minute=0, second=0, microsecond=0, tzinfo=None)
        srv_dt = _brt_to_server(brt_day)
        exit_srv = srv_dt + timedelta(hours=11)
        verdicts = evaluate_at(series, srv_dt, brt_day.replace(tzinfo=BRT), engine)
        if verdicts is None:
            continue
        if not is_market_session_valid(series["H1"]["times"], exit_srv):
            continue
        nights_evaluated += 1
        for ccy in CURRENCIES:
            bias_word = verdicts[ccy]["trade_bias"]
            if bias_word not in ("COMPRA", "VENDA"):
                continue
            bias = "BUY" if bias_word == "COMPRA" else "SELL"
            for leg in get_portfolio_pairs(ccy, bias):
                freq[leg["pair"]] += 1

    return freq, nights_evaluated


def measure_valid_nights(days=45, engine=None):
    """Lista de (srv_dt, exit_srv) das noites válidas (mesma máscara do
    backtest_canonical.py) — usado como referência temporal comum pra medir
    movimento de TODOS os 28 pares, não só os que alguma cesta abriu.

    `engine` (achados MFC76-02/P3-2): OBRIGATÓRIO, resolvido uma única vez
    em main() — ver docstring de measure_pair_frequency acima."""
    if engine is None:
        raise ValueError("measure_valid_nights requer engine= resolvido pelo chamador")
    series = load_series(window_start_brt=datetime.now(BRT) - timedelta(days=days))
    if not series:
        return None, None
    nights = []
    for d in range(days, 0, -1):
        brt_day = (datetime.now(BRT) - timedelta(days=d)).replace(
            hour=ENTRY_HOUR_BRT, minute=0, second=0, microsecond=0, tzinfo=None)
        srv_dt = _brt_to_server(brt_day)
        exit_srv = srv_dt + timedelta(hours=11)
        if evaluate_at(series, srv_dt, brt_day.replace(tzinfo=BRT), engine) is None:
            continue
        if not is_market_session_valid(series["H1"]["times"], exit_srv):
            continue
        nights.append((srv_dt, exit_srv))
    return nights, series


def measure_cost_ratio(nights, prices, lot=LOT):
    """razão_custo(par) = spread_ida_volta / mediana(|movimento em USD|,
    mesma janela de 11h, mesmo lote) — separa 'caro porque é volátil'
    (mediana de movimento também alta, razão baixa) de 'caro porque é
    ilíquido' (mediana de movimento baixa, razão alta). Ver herdr-ask mfc-6
    (mfc-rev-2): critério medido sobre o que o par se move, não sobre o
    ranking de spread isolado.

    Corte deliberadamente NÃO decidido aqui — só a tabela. O critério de
    onde cortar deve vir da FORMA da distribuição, não do PnL (overfitting:
    escolher o corte que dá o resultado que se quer ver)."""
    costs = CostModel(lot)
    out = {}
    for pair in ALL_28_PAIRS:
        spread, swap = costs.leg(pair, "BUY")
        degraded_spread = (spread == 0.0 and swap == 0.0)
        spread_roundtrip = spread * 2.0
        ser = prices.get(pair)
        moves = []
        if ser is not None:
            for srv_dt, exit_srv in nights:
                try:
                    p_in = float(ser.asof(srv_dt))
                    p_out = float(ser.asof(exit_srv))
                except Exception:
                    continue
                if not (p_in > 0 and p_out > 0):
                    continue
                rates = usd_cross_rates_dict(prices, exit_srv)
                pnl, _ = convert_pnl_to_usd(pair, "BUY", p_in, p_out, lot, rates_dict=rates)
                moves.append(abs(pnl))
        moves.sort()
        median_move = moves[len(moves) // 2] if moves else 0.0
        ratio = (spread_roundtrip / median_move) if median_move > 0 else float("inf")
        out[pair] = (spread_roundtrip, median_move, ratio, len(moves), degraded_spread)
    return out


def main():
    # Achados MFC76-02/P3-2 (herdr-review mfc-76): resolvido UMA VEZ aqui,
    # ANTES de ensure_mt5() (uma config inválida não deve gastar a conexão
    # MT5 pra abortar depois), e passado explícito pras duas funções
    # abaixo — antes cada uma resolvia a própria cópia, violando o
    # contrato de confluence_config.py ("uma vez por rodada").
    try:
        signal_engine = resolve_confluence_engine()
    except ValueError as exc:
        print(f"[-] {exc}")
        return 1

    if not ensure_mt5():
        print("[-] MT5 não conectado — abortando (MFC22-02: medição de spread exige "
              "conexão confirmada, não só o import do binding).")
        return 1

    check_contract_size_consistency()

    print("[*] Medindo spread estático (tick atual, 0.01 lote)...")
    spread = measure_static_spread()
    degraded_pairs = [p for p, (_, deg) in spread.items() if deg]
    if degraded_pairs:
        print(f"[!] {len(degraded_pairs)} par(es) sem símbolo/tick/taxa disponível "
              f"(EXCLUÍDOS da tabela, não contados como custo zero): {degraded_pairs}")

    print(f"[*] Medindo frequência de sinal ativo do motor {signal_engine!r}...")
    result = measure_pair_frequency(days=45, engine=signal_engine)
    if result is None:
        return 1
    freq, nights_evaluated = result
    print(f"[+] {nights_evaluated} noites válidas.\n")

    rows = []
    for pair in ALL_28_PAIRS:
        s, degraded = spread[pair]
        if degraded:
            continue
        f = freq[pair]
        rows.append((pair, s, f, s * f))

    rows.sort(key=lambda r: r[3], reverse=True)

    print("=" * 70)
    print(f"  SPREAD POR PAR — estático x frequência de SINAL (motor {signal_engine!r}, 45 dias)")
    print("=" * 70)
    hdr = f"{'par':<10} {'spread ida+volta':>18} {'vezes SINAL ativo':>18} {'contribuição total':>20}"
    print(hdr)
    print("-" * len(hdr))
    total_contrib = sum(r[3] for r in rows)
    for pair, s, f, contrib in rows:
        pct = (contrib / total_contrib * 100) if total_contrib else 0.0
        print(f"{pair:<10} {s:>18.2f} {f:>18} {contrib:>20.2f}  ({pct:.1f}%)")

    print(f"\ncontribuição total (soma): ${total_contrib:.2f}")
    print("\nnota (MFC22-01/02, herdr-review rodada 22): 'vezes SINAL ativo' é frequência "
          "de trade_bias != NEUTRO, NÃO de cesta efetivamente aberta — não passa pelos "
          "gates de margem/idempotência/colisão/filling reais. 'contribuição total' é "
          "frequência x spread ATUAL — o spread real de cada ocorrência histórica variava "
          "com o tick daquele momento; isto é uma estimativa de ORDEM DE GRANDEZA de qual "
          "par pesa mais, não custo de execução reconstruído.")

    print("\n[*] Calculando razão_custo (spread / mediana do movimento em 11h)...")
    nights, series = measure_valid_nights(days=45, engine=signal_engine)
    if not nights:
        print("[-] Sem noites válidas — pulando razão_custo.")
        return 0
    prices = load_h1_prices(count=h1_bars_for_days(45))
    ratios = measure_cost_ratio(nights, prices)

    rows2 = sorted(
        ((p, v) for p, v in ratios.items() if not v[4]),
        key=lambda kv: kv[1][2], reverse=True)
    degraded_ratio_pairs = [p for p, v in ratios.items() if v[4]]
    if degraded_ratio_pairs:
        print(f"[!] {len(degraded_ratio_pairs)} par(es) sem spread disponível, "
              f"excluído(s) da razão_custo: {degraded_ratio_pairs}")

    print("\n" + "=" * 78)
    print(f"  RAZÃO_CUSTO POR PAR — spread / mediana(|movimento USD| em 11h), {len(nights)} noites")
    print("=" * 78)
    hdr2 = f"{'par':<10} {'spread':>10} {'mediana mov.':>14} {'razão_custo':>12} {'n obs':>7}"
    print(hdr2)
    print("-" * len(hdr2))
    for pair, (spread_rt, median_move, ratio, n, _deg) in rows2:
        ratio_str = f"{ratio:.2f}x" if ratio != float("inf") else "inf"
        print(f"{pair:<10} {spread_rt:>10.2f} {median_move:>14.2f} {ratio_str:>12} {n:>7}")

    print("\nleitura (suavizada — MFC22-05, herdr-review rodada 22): razão_custo > 1.0 é "
          "uma HEURÍSTICA de iliquidez (o spread sozinho já supera a MEDIANA do movimento "
          "de 11h), não uma prova de que nenhum motor de direção pode lucrar nesse par — "
          "um motor que selecionasse corretamente a cauda de movimentos maiores que a "
          "mediana ainda poderia ter expectativa positiva. Tratar como filtro exploratório "
          "de onde investigar primeiro, não como corte definitivo por si só.\n"
          "Corte NÃO escolhido aqui de propósito (herdr-ask mfc-6, mfc-rev-2): decidir o "
          "limiar depois de ver o efeito no PnL é overfitting — o corte deve vir da forma "
          "desta distribuição, não do resultado que ele produz.")


if __name__ == "__main__":
    sys.exit(main() or 0)
