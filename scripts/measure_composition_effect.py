"""
FERRAMENTA DE DIAGNÓSTICO, NÃO PARTE DO PIPELINE DE PRODUÇÃO. NÃO ALTERA
`agents/portfolio_executor.py::get_portfolio_pairs()` NEM QUALQUER CÓDIGO DE
EXECUÇÃO — só filtra a lista de pernas DEPOIS de calculada, pra medir o
efeito de composição num relatório, sem tocar produção.

Passo seguinte depois de razão_custo isolar GBPNZD (1.81x, único par acima
do corte pré-registrado ≥1.0 — herdr-ask mfc-6): medir o efeito real de
REMOVER essa perna das cestas de GBP e NZD, decompondo custo (esperado
cair, determinístico) de bruto (efeito econômico real da perna removida,
não necessariamente ruído — ver ressalva abaixo).

Mesmos sinais do Port A 5-TF, mesma máscara de noites válidas, MESMO backtest —
a única diferença entre as duas colunas é a composição da cesta.

Correções da herdr-review rodada 22 (MFC22-06, mfc-rev; P3-3, mfc-rev-2):
- Antes, cada variante podia descartar uma cesta independentemente (se
  faltasse preço de QUALQUER perna daquela variante) — as duas colunas
  podiam acabar comparando conjuntos de cestas DIFERENTES, o que invalida
  isolar composição como única variável. Agora as duas compartilham o
  mesmo `CostModel` por noite e são descartadas JUNTAS se qualquer perna
  (das 7) não tiver preço.
- Antes, cada variante criava seu próprio `CostModel` (consultas de tick
  em momentos ligeiramente diferentes) — agora um `CostModel` por NOITE é
  compartilhado pelas duas variantes, então a diferença de custo entre
  elas é exatamente o spread/swap da perna removida, não ruído de tick.
- Antes, "delta bruto perto de zero = sem overfitting" estava com a framing
  errada: se a perna removida tinha PnL sistematicamente positivo ou
  negativo, o delta bruto DEVE refletir isso — é efeito econômico real, não
  necessariamente overfitting. Agora reporta o PnL da perna removida
  separadamente, e o delta bruto do resto da cesta (que sim deveria ficar
  perto de zero, por não ter mudado nada além da perna removida).
- Pernas degradadas (`CostModel.leg()` sem símbolo/tick/taxa, retorna
  (0.0, 0.0)) agora são contadas e avisadas, não silenciosamente tratadas
  como "sem custo".
"""

import os
import sys
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from agents.portfolio_executor import get_portfolio_pairs, CostModel, ensure_mt5
from web.history_tracker import convert_pnl_to_usd
from scripts.backtest_canonical import (
    BRT, LOT, ENTRY_HOUR_BRT, CURRENCIES, load_series, load_h1_prices, h1_bars_for_days,
    _brt_to_server, evaluate_at, is_market_session_valid, check_contract_size_consistency,
)
from scripts._backtest_results_log import append_result

EXCLUDED_PAIR = "GBPNZD"  # corte pré-registrado: razão_custo >= 1.0 (herdr-ask mfc-6)

# Pares canônicos usados pra montar rates_dict (achado herdr-review mfc-62,
# P3-1/`mfc-rev-2`) — os mesmos 7 que CostModel._usd_rate() tenta pra
# conversão de qualquer moeda não-USD. Mesmo padrão de
# scripts/measure_spread_per_pair.py::_build_rates_dict (P3-2 da rodada 22).
_USD_CROSS_PAIRS = ("EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "USDJPY")


def _build_rates_dict(prices, at_dt):
    """rates_dict pra convert_pnl_to_usd(): preço dos 7 pares canônicos
    *USD/USD* no instante `at_dt`, lido da MESMA série histórica H1 que mede
    o movimento — sem isso, convert_pnl_to_usd() cai na tabela hardcoded de
    web/history_tracker.py pra qualquer par de cotação não-USD (achado
    herdr-review mfc-62, P3-1/`mfc-rev-2`: o `rates_source` gravado no
    journal afirmava 'historical_h1_prices' pra essa conversão, mas o
    código não passava rates_dict — divergência entre metadado e
    comportamento real, não erro de valor)."""
    rates = {}
    for pair in _USD_CROSS_PAIRS:
        ser = prices.get(pair)
        if ser is None:
            continue
        try:
            p = float(ser.asof(at_dt))
        except Exception:
            continue
        if p > 0:
            rates[pair] = p
    return rates


def _needs_hardcoded_rate_fallback(pair, rates):
    """Replica a MESMA condição que web/history_tracker.py::convert_pnl_to_usd()
    usa pra decidir se cai na tabela hardcoded (achado herdr-review mfc-63,
    P3-2/`mfc-rev-2` + MFC63-02/`mfc-rev`, CONFIRMADO pelos dois): mesmo com
    rates_dict passado, uma moeda de cotação não-USD cujo cross não esteja
    em `rates` (par ausente de `prices`, ou tick inválido) ainda cai no
    fallback — silenciosamente, sem que rates_source deixasse de afirmar
    'historical_h1_prices'. Não reimplementa a conversão, só a checagem de
    "vai precisar do fallback?" pra poder avisar ANTES de calcular."""
    base, quote = pair[:3], pair[3:6]
    if quote == "USD" or base == "USD":
        return False
    quote_usd_pair, usd_quote_pair = f"{quote}USD", f"USD{quote}"
    if rates.get(quote_usd_pair, 0) > 0 or rates.get(usd_quote_pair, 0) > 0:
        return False
    return True


def _legs_pnl_and_cost(legs, prices, srv_dt, exit_srv, costs, lot=LOT):
    """PnL bruto + custo por perna, usando um `CostModel` COMPARTILHADO
    (passado de fora) — garante que as duas variantes comparadas na mesma
    noite usem exatamente o mesmo tick pras pernas em comum. Retorna None
    se QUALQUER perna não tiver preço, pra descartar a noite JUNTA nas duas
    variantes (MFC22-06/P3-3)."""
    per_leg = {}
    rates = _build_rates_dict(prices, exit_srv)
    for leg in legs:
        ser = prices.get(leg["pair"])
        if ser is None:
            return None
        try:
            p_in = float(ser.asof(srv_dt))
            p_out = float(ser.asof(exit_srv))
        except Exception:
            return None
        if not (p_in > 0 and p_out > 0):
            return None
        rate_fallback = _needs_hardcoded_rate_fallback(leg["pair"], rates)
        pnl, _ = convert_pnl_to_usd(leg["pair"], leg["action"], p_in, p_out, lot, rates_dict=rates)
        spread, swap = costs.leg(leg["pair"], leg["action"], lot)
        cost = spread * 2.0 - swap
        # Proxy de degradação: CostModel.leg() devolve exatamente (0.0, 0.0)
        # quando símbolo/tick/taxa está indisponível (agents/portfolio_executor.py) —
        # zero genuíno de mercado é praticamente impossível pra um par real.
        degraded = (spread == 0.0 and swap == 0.0)
        per_leg[leg["pair"]] = (pnl, cost, degraded, rate_fallback)
    return per_leg


def compare_composition(days=45, log_note=None):
    if not ensure_mt5():
        print("[-] MT5 não conectado — abortando comparação; não usar dados degradados.")
        return 1
    check_contract_size_consistency()
    print("[*] Carregando séries canônicas...")
    series = load_series()
    if not series:
        print("[-] Séries canônicas indisponíveis.")
        return 1
    print("[*] Carregando preços H1...")
    prices = load_h1_prices(count=h1_bars_for_days(days))
    if not prices:
        print("[-] Preços H1 indisponíveis.")
        return 1
    print(f"[+] {len(prices)} pares com preço disponível.\n")

    variants = ("7_pernas", "6_pernas_sem_" + EXCLUDED_PAIR)
    stats = {v: {"pnl": 0.0, "cost": 0.0, "baskets": 0, "nights_with_baskets": 0, "wins": 0}
             for v in variants}
    removed_leg_gross_total = 0.0
    removed_leg_cost_total = 0.0
    removed_leg_gross_per_basket = []  # pra média ± erro padrão (P3-3, mfc-rev-2)
    rest_of_basket_deltas = []  # PnL bruto das 6 pernas mantidas: cut - (full - removida) — deveria ser ~0
    affected_baskets = 0
    degraded_baskets = 0
    rate_fallback_baskets = 0  # achado herdr-review mfc-63 (P3-2/MFC63-02, CONFIRMADO)
    skipped_missing_price = 0
    nights_evaluated = 0

    for d in range(days, 0, -1):
        brt_day = (datetime.now(BRT) - timedelta(days=d)).replace(
            hour=ENTRY_HOUR_BRT, minute=0, second=0, microsecond=0, tzinfo=None)
        srv_dt = _brt_to_server(brt_day)
        exit_srv = srv_dt + timedelta(hours=11)
        verdicts = evaluate_at(series, srv_dt, brt_day.replace(tzinfo=BRT))
        if verdicts is None:
            continue
        if not is_market_session_valid(series["H1"]["times"], exit_srv):
            continue
        nights_evaluated += 1

        # Um CostModel POR NOITE, compartilhado pelas duas variantes — a
        # diferença de custo entre elas passa a ser exatamente o spread/swap
        # da perna removida, não ruído de tick entre chamadas separadas.
        costs = CostModel(LOT)

        night_pnl = {v: 0.0 for v in variants}
        night_cost = {v: 0.0 for v in variants}
        night_baskets = {v: 0 for v in variants}

        for ccy in CURRENCIES:
            bias_word = verdicts[ccy]["trade_bias"]
            if bias_word not in ("COMPRA", "VENDA"):
                continue
            bias = "BUY" if bias_word == "COMPRA" else "SELL"
            legs_full = get_portfolio_pairs(ccy, bias)
            has_excluded = any(leg["pair"] == EXCLUDED_PAIR for leg in legs_full)
            if has_excluded:
                affected_baskets += 1

            per_leg = _legs_pnl_and_cost(legs_full, prices, srv_dt, exit_srv, costs)
            if per_leg is None:
                skipped_missing_price += 1
                continue  # descarta a cesta nas DUAS variantes (MFC22-06/P3-3)

            if any(deg for _, _, deg, _ in per_leg.values()):
                degraded_baskets += 1
            if any(fb for _, _, _, fb in per_leg.values()):
                rate_fallback_baskets += 1

            gross_full = sum(pnl for pnl, _, _, _ in per_leg.values())
            cost_full = sum(cost for _, cost, _, _ in per_leg.values())
            removed = per_leg.get(EXCLUDED_PAIR)
            if removed is not None:
                gross_removed, cost_removed, _, _ = removed
                legs_cut = [leg for leg in legs_full if leg["pair"] != EXCLUDED_PAIR]
                per_leg_cut = _legs_pnl_and_cost(
                    legs_cut, prices, srv_dt, exit_srv, costs
                )
                if per_leg_cut is None:
                    skipped_missing_price += 1
                    continue
                # Só acumula as estatísticas da perna removida DEPOIS de
                # confirmar que a variante reduzida também tem preço pras 6
                # pernas — achado herdr-review mfc-62 (P3-3/`mfc-rev-2`): a
                # ordem anterior incrementava esses totais ANTES desta
                # checagem, então uma cesta descartada (preço faltando na
                # variante reduzida) ainda contribuía pros totais/média da
                # perna removida, mesmo saindo das duas colunas comparadas.
                removed_leg_gross_total += gross_removed
                removed_leg_cost_total += cost_removed
                removed_leg_gross_per_basket.append(gross_removed)
                # Recalcula a variante reduzida de forma independente. A
                # igualdade esperada abaixo é uma checagem de sanidade real,
                # não a identidade algébrica gross_full - removed.
                gross_cut = sum(pnl for pnl, _, _, _ in per_leg_cut.values())
                cost_cut = sum(cost for _, cost, _, _ in per_leg_cut.values())
                rest_of_basket_deltas.append(
                    gross_cut - (gross_full - gross_removed)
                )
            else:
                gross_cut, cost_cut = gross_full, cost_full

            for v, gross, cost in ((variants[0], gross_full, cost_full),
                                    (variants[1], gross_cut, cost_cut)):
                night_pnl[v] += gross
                night_cost[v] += cost
                night_baskets[v] += 1

        for v in variants:
            stats[v]["pnl"] += night_pnl[v]
            stats[v]["cost"] += night_cost[v]
            stats[v]["baskets"] += night_baskets[v]
            if night_baskets[v] > 0:
                stats[v]["nights_with_baskets"] += 1
                if night_pnl[v] - night_cost[v] >= 0:
                    stats[v]["wins"] += 1

    print("=" * 78)
    print(f"  EFEITO DE COMPOSIÇÃO — excluir {EXCLUDED_PAIR} (razão_custo=1.81x), {days} dias")
    print("=" * 78)
    print(f"noites avaliadas       : {nights_evaluated}")
    print(f"cestas afetadas (GBP/NZD com {EXCLUDED_PAIR}): {affected_baskets}")
    print(f"cestas com perna degradada (sem símbolo/tick/taxa): {degraded_baskets}")
    if rate_fallback_baskets:
        print(f"[!] cestas com pelo menos uma perna convertida pela tabela "
              f"hardcoded (cross USD ausente de prices): {rate_fallback_baskets} "
              f"— rates_source abaixo reflete isso")
    if skipped_missing_price:
        print(f"cestas descartadas por preço faltando (nas DUAS variantes): {skipped_missing_price}")
    print()
    hdr = f"{'variante':<24} {'cestas':>8} {'noite%':>7} {'bruto':>10} {'custo':>10} {'líquido':>10}"
    print(hdr)
    print("-" * len(hdr))
    for v in variants:
        s = stats[v]
        net = s["pnl"] - s["cost"]
        winrate = (s["wins"] / s["nights_with_baskets"] * 100) if s["nights_with_baskets"] else 0.0
        print(f"{v:<24} {s['baskets']:>8} {winrate:>6.1f}% {s['pnl']:>10.2f} {s['cost']:>10.2f} {net:>10.2f}")

    d_pnl = stats[variants[1]]["pnl"] - stats[variants[0]]["pnl"]
    d_cost = stats[variants[1]]["cost"] - stats[variants[0]]["cost"]
    d_net = (stats[variants[1]]["pnl"] - stats[variants[1]]["cost"]) - \
            (stats[variants[0]]["pnl"] - stats[variants[0]]["cost"])
    print(f"\ndelta bruto (6p - 7p)   : {d_pnl:+.2f}  (= -1 × PnL bruto da perna removida, abaixo)")
    print(f"delta custo (6p - 7p)   : {d_cost:+.2f}  (= -1 × custo da perna removida — determinístico)")
    print(f"delta líquido (6p - 7p) : {d_net:+.2f}")
    n_removed = len(removed_leg_gross_per_basket)
    if n_removed > 1:
        mean_g = removed_leg_gross_total / n_removed
        variance = sum((x - mean_g) ** 2 for x in removed_leg_gross_per_basket) / (n_removed - 1)
        stderr_g = (variance / n_removed) ** 0.5
    else:
        mean_g = removed_leg_gross_per_basket[0] if removed_leg_gross_per_basket else 0.0
        stderr_g = float("nan")
    print(f"\nPnL bruto da perna {EXCLUDED_PAIR} removida: {removed_leg_gross_total:+.2f} total "
          f"(média {mean_g:+.3f} +/- {stderr_g:.3f} erro padrão, n={n_removed} cestas)")
    print(f"custo da perna {EXCLUDED_PAIR} removida    : {removed_leg_cost_total:+.2f}")
    max_rest_delta = max((abs(x) for x in rest_of_basket_deltas), default=0.0)
    print(f"\nchecagem independente do restante da cesta: n={len(rest_of_basket_deltas)}, "
          f"máximo |delta|={max_rest_delta:.10f}")
    print("\nleitura (correção MFC22-06/P3-3, herdr-review rodada 22): o delta bruto É o "
          "PnL da própria perna removida — se ela ajudava, removê-la piora o bruto; se "
          "atrapalhava, melhora. Isso é efeito econômico REAL da perna, não overfitting. "
          "O que DEVERIA ficar perto de zero é o resto da cesta (as 6 pernas mantidas, "
          "idênticas nas duas variantes) — não há mecanismo pelo qual removê-la mudasse o "
          "PnL delas, e a checagem independente acima deve permanecer em zero dentro da "
          "precisão de cálculo.")

    if log_note is not None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "script": "measure_composition_effect.py",
            "days": days,
            "nights_evaluated": nights_evaluated,
            "excluded_pair": EXCLUDED_PAIR,
            "note": log_note,
            "hypothesis": "Remover GBPNZD reduz o custo da cesta sem alterar o PnL bruto das seis pernas mantidas.",
            "implementation": {
                "diagnostic": "composition_effect",
                "signal_engine": "5tf_port_a",
                "port": "A",
                "upstream_commit": "544d660",
            },
            "baseline": variants[0],
            "window": {
                "days": days,
                "nights_evaluated": nights_evaluated,
                "mask": "same_closed_bar_and_valid_market_session",
            },
            "limitations": [
                "custo usa tick atual do MT5, não custo histórico da noite",
                "sem slippage, latência, requotes, fills parciais ou margem",
                "frequência e composição são diagnóstico, não execução real",
                "resultado não é validação fora da amostra",
            ],
            "comparison_to_baseline": {
                variants[1]: {
                    "gross_delta": round(d_pnl, 2),
                    "cost_delta": round(d_cost, 2),
                    "net_delta": round(d_net, 2),
                    "rest_of_basket_delta_max_abs": round(max_rest_delta, 10),
                },
            },
            "parameters": {
                "lot": LOT,
                "excluded_pair": EXCLUDED_PAIR,
                "variants": list(variants),
            },
            # Achado herdr-review mfc-63 (P3-2/`mfc-rev-2`, MFC63-02/`mfc-rev`,
            # CONFIRMADO pelos dois): o fix do P3-1 (mfc-62) monta rates_dict
            # a partir da série H1, mas se um dos 7 pares de cross USD faltar
            # de `prices` (ex.: NZDUSD ausente — exatamente o cross que
            # GBPNZD, o par que este script existe pra avaliar, precisa),
            # convert_pnl_to_usd() ainda cai na tabela hardcoded pra essa
            # perna especificamente, e o rótulo abaixo era incondicional —
            # não distinguia esse caso do caminho totalmente coberto por H1.
            "rates_source": (
                "historical_h1_prices; live CostModel tick for spread/swap"
                if not rate_fallback_baskets else
                f"historical_h1_prices; live CostModel tick for spread/swap; "
                f"WARNING: {rate_fallback_baskets} basket(s) had at least one "
                f"leg fall back to web/history_tracker.py's hardcoded USD "
                f"cross table (a required rates_dict cross pair was missing "
                f"from H1 prices)"
            ),
            "rate_fallback_baskets": rate_fallback_baskets,
            # Achado herdr-review mfc-62 (MFC62-03/`mfc-rev`): CostModel é
            # compartilhado por NOITE (garante que as duas variantes usem o
            # mesmo tick pras pernas em comum), mas CostModel.leg() consulta
            # o MT5 na primeira ocorrência de cada chave (pair, action, lot)
            # e cacheia daí em diante — não é uma fotografia simultânea de
            # todos os pares, é uma leitura sequencial por chave distinta ao
            # longo da noite, compartilhada entre as duas colunas.
            "cost_snapshot": "live tick sampled once per distinct (pair, action, lot) key, "
                              "shared across both variants within the same night",
            "affected_baskets": affected_baskets,
            "degraded_baskets": degraded_baskets,
            "skipped_missing_price": skipped_missing_price,
            "variants": {
                v: {
                    "baskets": stats[v]["baskets"],
                    "bruto": round(stats[v]["pnl"], 2),
                    "custo": round(stats[v]["cost"], 2),
                    "liquido": round(stats[v]["pnl"] - stats[v]["cost"], 2),
                    "noite_pct": round(
                        (stats[v]["wins"] / stats[v]["nights_with_baskets"] * 100)
                        if stats[v]["nights_with_baskets"] else 0.0, 1),
                } for v in variants
            },
            "removed_leg_gross": round(removed_leg_gross_total, 2),
            "removed_leg_cost": round(removed_leg_cost_total, 2),
            "removed_leg_gross_mean": round(mean_g, 3),
            "removed_leg_gross_stderr": None if stderr_g != stderr_g else round(stderr_g, 3),
            "removed_leg_n": n_removed,
            "rest_of_basket_delta_max_abs": round(max_rest_delta, 10),
            "rest_of_basket_delta_n": len(rest_of_basket_deltas),
        }
        path = append_result(record)
        print(f"\n[+] Resultado registrado em {path}")
    return 0


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 45
    note = sys.argv[2] if len(sys.argv) > 2 else None
    sys.exit(compare_composition(days=days, log_note=note))
