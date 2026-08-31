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

**RESSALVA ABERTA (P2-2/MFC22-03, herdr-review rodada 22, achada
independentemente pelos dois revisores — decisão de arquitetura pendente do
Breno, não bug corrigível aqui):** desde a correção do look-ahead (MFC21-03,
`_closed_bar_index`), este backtest decide com a última barra JÁ FECHADA
(índice i-1) em cada TF. A produção (`web/css_service.py::calculate_full_css`,
via `copy_rates_from_pos(..., 0, ...)`) decide com a barra MAIS RECENTE, que
às 21:05 ainda está em formação (H1 das 21:00 com 5 minutos de vida, D1 de
hoje parcial). Ou seja: **desde essa correção, backtest e produção não veem
mais a mesma informação temporal** — a frase acima ("se divergirem, é bug")
deixou de ser garantida por construção nesse ponto específico. Não dá pra
"consertar" isso escolhendo um lado sozinho: usar a barra fechada no
backtest é o que evita look-ahead (o valor intrabar histórico não é
reconstruível a partir do OHLC final — foi por isso que o look-ahead
existia), mas mudar a produção pra decidir só com barra fechada é MUDANÇA DE
ESTRATÉGIA (não de ferramenta), e precisa de decisão explícita do Breno.
Até essa decisão: todo resultado deste backtest desde MFC21-03 (comparações
3-TF vs 5-TF, varredura do R1, efeito de composição/GBPNZD) descreve um
motor com informação mais "atrasada" que o que roda de verdade — não invalida
as comparações ENTRE variantes testadas aqui (todas sofrem da mesma
defasagem, igualmente), mas invalida projetar esses números como previsão
do que a produção faria.

Uso:
    python scripts/backtest_canonical.py [dias]      # default 45

Somente leitura: não envia ordem, não grava em data/.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from web.css_service import (
    ALL_28_PAIRS, ATR_PERIOD, CURRENCIES, MIN_COMMON_HISTORY_BARS,
    MT5_AVAILABLE, mt5, MT5_PATH, calc_atr_sma, calc_lwma, calculate_full_css,
    get_tf_constant, required_full_history_bars, to_broker_symbol,
)
import scripts.fetch_histdata_mn1_warmup as fetch_histdata_mn1_warmup
from agents.confluence_engine import BRT, evaluate_currency_confluence
# CostModel mora em agents/portfolio_executor.py (o executor de verdade) —
# era duplicada aqui até 24/08; agora o backtest importa a mesma classe que
# o sistema ao vivo usa pra ESTIMAR o custo de cada cesta aberta (achado em
# revisão: Codex, achado 2/4 rodada 5 — "custo real" aqui contradizia a
# própria docstring de CostModel), em vez de manter uma segunda cópia da
# lógica.
from agents.portfolio_executor import get_portfolio_pairs, ensure_mt5, CostModel
from web.history_tracker import GMT_OFFSET, convert_pnl_to_usd
from scripts._backtest_results_log import append_result

LOT = 0.01
ENTRY_HOUR_BRT = 21
EXIT_HOUR_BRT = 8
TFS = ("MN1", "W1", "D1", "H4", "H1")

# Barras a puxar por TF. Precisa cobrir a janela do backtest + aquecimento
# do ATR(100)/LWMA(21). calculate_full_css já soma +150 internamente.
TF_COUNTS = {"MN1": 60, "W1": 120, "D1": 200, "H4": 600, "H1": 1600}

# Cache de aquecimento MN1 de terceiro (HistData.com), só para o prefixo
# antigo da série que a Exness-MT5Trial11 não tem — nunca para as barras
# recentes/decisórias. Ver scripts/fetch_histdata_mn1_warmup.py (como foi
# gerado e validado) e docs/plans/port-upstream-institutional-matrix.md
# (decisão e validação cruzada contra a Exness, 2026-08-31).
HISTDATA_WARMUP_DIR = os.path.join(BASE_DIR, "data", "histdata_mn1_warmup")


def _brt_to_server(dt_brt):
    """BRT -> hora do servidor do broker (o inverso do GMT_OFFSET do EA)."""
    return dt_brt - timedelta(hours=GMT_OFFSET)


def _load_histdata_warmup_months(pair):
    """Barras MN1 de aquecimento da HistData.com pra um par, se houver cache
    (scripts/fetch_histdata_mn1_warmup.py) — GBPJPY não tem, não precisa.
    Retorna (rows, gaps): `rows` é uma lista de (timestamp UTC do início do
    mês, open, high, low, close), ordenada; `gaps` é a lista de "YYYY-MM"
    ausentes DENTRO do intervalo coberto pelo cache (ver
    scripts.fetch_histdata_mn1_warmup.find_gaps — achado herdr-review
    mfc-61, P2-1: um buraco no meio do cache, sem essa checagem, produzia
    `status=clean` do mesmo jeito que um cache contíguo)."""
    path = os.path.join(HISTDATA_WARMUP_DIR, f"{pair.lower()}.json")
    if not os.path.isfile(path):
        return [], []
    with open(path, "r", encoding="utf-8") as f:
        months = json.load(f)
    gaps = fetch_histdata_mn1_warmup.find_gaps(months)
    rows = []
    for key, bar in months.items():
        year, month = (int(part) for part in key.split("-"))
        ts = datetime(year, month, 1, tzinfo=timezone.utc)
        rows.append((ts, bar["open"], bar["high"], bar["low"], bar["close"]))
    rows.sort(key=lambda row: row[0])
    return rows, gaps


def load_mn1_series_with_warmup(count):
    """Variante de web/css_service.py::calculate_full_css() só para MN1, que
    estende o histórico curto da Exness com o cache de aquecimento da
    HistData.com quando disponível — só no PREFIXO necessário pro ATR(100)
    convergir. Os OHLC das barras mais recentes/decisórias são 100% Exness
    (nenhuma barra HistData jamais substitui ou se mistura com uma barra
    Exness — ver o filtro `row[0] < earliest_exness` abaixo); mas os SCORES
    dessas barras dependem do prefixo através do lookback de ATR(100)/LWMA(21)
    — é o propósito do adaptador, não um efeito colateral (achado
    herdr-review mfc-61, P3-1/`mfc-rev-2`: a formulação anterior, "as barras
    recentes continuam 100% Exness", podia ser lida como "o resultado não
    depende de dado de terceiro", o oposto do que acontece — por isso existe
    a validação cruzada documentada no plano, 0,17% de diferença média).

    Reimplementada aqui (mesma matemática, calc_atr_sma/calc_lwma
    reaproveitados de web/css_service.py) em vez de modificar aquela função
    porque ela é compartilhada com o caminho web AO VIVO — dado de terceiro
    nunca deve entrar na dashboard nem no sinal real, só neste backtest
    isolado e somente-leitura.

    Retorna (res, times, quality, warmup_months_used); `warmup_months_used`
    é {par: nº de meses da HistData usados} para registro de proveniência —
    vazio quando nenhum par precisou de aquecimento externo.
    `quality["histdata_warmup_gaps"]` (achado herdr-review mfc-61, P2-1,
    confirmado por `mfc-rev` e `mfc-rev-2`) registra, por par, os meses
    ausentes DENTRO do intervalo do cache usado — antes não havia nenhuma
    checagem de contiguidade, então um cache com buraco no meio (caso real:
    AUDJPY, 11 meses de 2012 genuinamente ausentes na HistData.com, não um
    erro de fetch) produzia `status=clean` do mesmo jeito que um cache
    íntegro; agora o buraco fica visível na proveniência mesmo quando não é
    grave o bastante pra derrubar `first_pos` abaixo do requisito.
    """
    if not MT5_AVAILABLE or mt5 is None:
        # Mesmo comportamento fail-closed de calculate_full_css() nesse
        # estado (achado herdr-review mfc-61, MFC61-02/`mfc-rev`): sem isso,
        # a chamada direta a esta API (fora de compare(), que já garante
        # ensure_mt5() antes) levantava AttributeError em vez de devolver
        # indisponibilidade controlada.
        return None, None, {"status": "unavailable"}, {}
    tf_val = get_tf_constant("MN1")
    required_history = required_full_history_bars(count, mode="standard")
    quality = {
        "status": "clean",
        "requested_history_bars": int(count),
        "required_full_history_bars": required_history,
        "short_history_pairs": [],
        "common_history_bars": 0,
        "returned_history_bars": 0,
        "histdata_warmup_gaps": {},
    }
    warmup_months_used = {}
    pair_dfs = {}
    for sym in ALL_28_PAIRS:
        rates = mt5.copy_rates_from_pos(to_broker_symbol(sym), tf_val, 0, count + 150)
        if rates is None or len(rates) < MIN_COMMON_HISTORY_BARS:
            continue
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        earliest_exness = df["time"].min()

        all_warmup_rows, warmup_gaps = _load_histdata_warmup_months(sym)
        warmup_rows = [row for row in all_warmup_rows if row[0] < earliest_exness]
        if warmup_gaps:
            quality["histdata_warmup_gaps"][sym] = warmup_gaps
        if warmup_rows:
            warmup_df = pd.DataFrame(
                warmup_rows, columns=["time", "open", "high", "low", "close"],
            )
            df = pd.concat(
                [warmup_df, df[["time", "open", "high", "low", "close"]]],
                ignore_index=True,
            )
            warmup_months_used[sym] = len(warmup_rows)
        df = df.drop_duplicates(subset="time", keep="last").sort_values("time")
        df.set_index("time", inplace=True)
        pair_dfs[sym] = df

    def _result(res, times):
        return res, times, quality, warmup_months_used

    if not pair_dfs:
        quality["status"] = "incomplete"
        return _result(None, None)

    missing_pairs = sorted(set(ALL_28_PAIRS) - set(pair_dfs))
    if missing_pairs:
        quality["status"] = "incomplete"
        quality["missing_pairs"] = missing_pairs
        return _result(None, None)

    common_index = None
    for df in pair_dfs.values():
        common_index = df.index if common_index is None else common_index.intersection(df.index)

    if common_index is None or len(common_index) < MIN_COMMON_HISTORY_BARS:
        quality["status"] = "incomplete"
        quality["common_history_bars"] = len(common_index) if common_index is not None else 0
        return _result(None, None)

    quality["common_history_bars"] = len(common_index)
    common_index = common_index[-count:]
    quality["returned_history_bars"] = len(common_index)
    if len(common_index) < count:
        quality["status"] = "degraded"
    for sym, df in pair_dfs.items():
        idx_map = {t: i for i, t in enumerate(df.index)}
        first_pos = idx_map.get(common_index[0]) if len(common_index) else None
        if first_pos is None or first_pos < required_history - count:
            quality["short_history_pairs"].append(sym)
    quality["short_history_pairs"] = sorted(quality["short_history_pairs"])
    if quality["short_history_pairs"]:
        quality["status"] = "degraded"

    pair_slopes = {}
    occurrences = {c: 0 for c in CURRENCIES}
    for sym in ALL_28_PAIRS:
        if sym not in pair_dfs:
            continue
        df = pair_dfs[sym]
        closes = df["close"].values
        highs = df["high"].values
        lows = df["low"].values
        idx_map = {t: i for i, t in enumerate(df.index)}
        atr_arr = calc_atr_sma(highs, lows, closes, ATR_PERIOD, min_periods=ATR_PERIOD)
        lwma_arr = calc_lwma(closes, 21)
        slopes = []
        for t in common_index:
            pos = idx_map.get(t, -1)
            if pos <= 0:
                slopes.append(0.0)
                continue
            atr_val = atr_arr[pos - 10] if (pos - 10) >= 0 else atr_arr[pos]
            atr = atr_val / 10.0
            ma0 = lwma_arr[pos]
            ma1 = lwma_arr[pos - 1]
            close0 = closes[pos]
            dbl_tma = ma0
            dbl_prev = (ma1 * 231.0 + close0 * 20.0) / 251.0
            sl = (dbl_tma - dbl_prev) / atr if np.isfinite(atr) and atr > 0 else 0.0
            slopes.append(sl)
        base, quote = sym[:3], sym[3:6]
        pair_slopes[sym] = (base, quote, np.array(slopes))
        if base in occurrences:
            occurrences[base] += 1
        if quote in occurrences:
            occurrences[quote] += 1

    css_res = {c: np.zeros(len(common_index)) for c in CURRENCIES}
    for sym, (base, quote, sl) in pair_slopes.items():
        if base in css_res:
            css_res[base] += sl
        if quote in css_res:
            css_res[quote] -= sl
    for c in CURRENCIES:
        if occurrences[c] > 0:
            css_res[c] /= occurrences[c]

    time_strs = [t.strftime("%Y-%m-%d %H:%M") for t in common_index]
    return _result(css_res, time_strs)


def load_series(require_clean=False, use_histdata_mn1_warmup=False):
    """Carrega scores e qualidade histórica pela função canônica.

    ``require_clean`` é usado pelo caminho OOS: uma série que não tem ATR
    cheio em todas as posições retornadas pode continuar útil para diagnóstico
    exploratório, mas não pode virar evidência OOS elegível.

    ``use_histdata_mn1_warmup``: quando True, a MN1 usa
    ``load_mn1_series_with_warmup`` em vez de ``calculate_full_css`` — só
    estende o prefixo de aquecimento com dado da HistData.com quando a
    Exness não tem histórico suficiente (ver módulo). Desligado por padrão:
    o caminho normal continua 100% Exness, sem qualquer dependência de
    terceiro.
    """
    series = {}
    for tf in TFS:
        if tf == "MN1" and use_histdata_mn1_warmup:
            res, times, quality, warmup_months_used = load_mn1_series_with_warmup(
                TF_COUNTS[tf]
            )
            if warmup_months_used:
                quality = dict(quality or {})
                quality["histdata_warmup_months_used"] = warmup_months_used
        else:
            calculated = calculate_full_css(
                get_tf_constant(tf), count=TF_COUNTS[tf], mode="standard",
                return_quality=True,
            )
            if len(calculated) == 4:
                res, times, _, quality = calculated
            else:  # compatibilidade com adaptadores/fixtures legados
                res, times, _ = calculated
                quality = {"status": "clean"}
        if res is None:
            print(f"[-] Sem dados para {tf} — abortando.")
            return None
        quality = dict(quality or {})
        quality.setdefault("status", "unknown")
        if quality["status"] != "clean":
            print(
                f"[!] {tf}: qualidade histórica CSS={quality['status']} "
                f"({quality.get('short_history_pairs', [])})"
            )
            if require_clean:
                print("[-] Histórico CSS degradado — abortando; não usar como OOS.")
                return None
        idx = pd.to_datetime(pd.Series(times))
        series[tf] = {"scores": res, "times": idx, "quality": quality}
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


def contract_size_snapshot():
    """Captura a escala dos 28 símbolos usada pelo PnL reconstruído."""
    expected = 100000
    if not MT5_AVAILABLE or mt5 is None:
        return {
            "observed_by_pair": {},
            "expected_for_pnl": expected,
            "missing_pairs": list(ALL_28_PAIRS),
            "invalid_pairs": [],
            "coverage_complete": False,
            "all_finite_positive": False,
            "valid_for_pnl": False,
        }

    observed = {}
    missing_pairs = []
    invalid_pairs = []
    for pair in ALL_28_PAIRS:
        info = mt5.symbol_info(to_broker_symbol(pair))
        if info is None:
            missing_pairs.append(pair)
            continue
        value = getattr(info, "trade_contract_size", None)
        observed[pair] = value
        if (not isinstance(value, (int, float)) or isinstance(value, bool)
                or not np.isfinite(float(value)) or value <= 0):
            invalid_pairs.append(pair)

    coverage_complete = not missing_pairs
    all_finite_positive = not invalid_pairs and len(observed) == len(ALL_28_PAIRS)
    valid_for_pnl = (
        coverage_complete and all_finite_positive
        and all(float(value) == float(expected) for value in observed.values())
    )
    return {
        "observed_by_pair": observed,
        "expected_for_pnl": expected,
        "missing_pairs": missing_pairs,
        "invalid_pairs": invalid_pairs,
        "coverage_complete": coverage_complete,
        "all_finite_positive": all_finite_positive,
        "valid_for_pnl": valid_for_pnl,
    }


def check_contract_size_consistency(strict=False):
    """Achado MFC22-07 (herdr-review rodada 22, mfc-rev): o PnL histórico
    (`convert_pnl_to_usd`, `web/history_tracker.py`) assume `units =
    lot_size * 100000` fixo, enquanto o custo novo
    (`CostModel.leg()`/`agents/portfolio_executor.py`) usa
    `lot * si.trade_contract_size` — o tamanho REAL do símbolo no broker.
    Se a conta/família de símbolos não for 100.000 (ex.: micro/cent),
    bruto e custo ficam em escalas diferentes sem nenhum aviso. Roda uma
    vez, no início de qualquer script de diagnóstico que compare bruto
    (via convert_pnl_to_usd) com custo (via CostModel) — não aborta (é
    ferramenta de diagnóstico), só avisa alto se detectar divergência."""
    snapshot = contract_size_snapshot()
    if not MT5_AVAILABLE or mt5 is None:
        if strict:
            raise RuntimeError("contrato MT5 não observável para janela OOS")
        return snapshot
    mismatched = []
    mismatched.extend(
        (pair, value) for pair, value in snapshot["observed_by_pair"].items()
        if value != 100000
    )
    mismatched.extend((pair, "missing") for pair in snapshot["missing_pairs"])
    if mismatched:
        print(f"[!] ATENÇÃO (MFC22-07): {len(mismatched)} par(es) com trade_contract_size "
              f"!= 100000 — bruto (convert_pnl_to_usd, assume 100000) e custo "
              f"(CostModel, usa o valor real) ficam em escalas DIFERENTES pra esses pares: "
              f"{mismatched}")
    if strict and not snapshot["valid_for_pnl"]:
        raise RuntimeError(
            "contrato MT5 incompatível ou incompleto para janela OOS: "
            f"missing={snapshot['missing_pairs']}, invalid={snapshot['invalid_pairs']}"
        )
    return snapshot


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


def _closed_bar_index(times, target):
    """Índice da última barra JÁ FECHADA em `target`.

    A barra achada por `_idx_at_or_before` (índice i) satisfaz
    times[i] <= target < times[i+1] por construção — ou seja, ela ainda está
    EM FORMAÇÃO em `target`, seu fechamento (times[i+1]) é estritamente
    posterior. A última barra realmente fechada é sempre i-1, cujo
    fechamento é times[i] <= target.

    Achado MFC21-03 (herdr-review rodada 21, mfc-rev): sem este shift, o
    backtest usava o valor FINAL de uma barra (OHLC e CSS já fechados) como
    se estivesse disponível no instante de entrada — informação que não
    existia ainda naquele ponto do histórico (look-ahead)."""
    i = _idx_at_or_before(times, target)
    if i is None or i == 0:
        return None
    return i - 1


# Gap máximo tolerado (em horas) entre a última barra H1 fechada e o
# instante alvo antes de considerar a sessão "sem mercado" (fim de
# semana/feriado). H1 é o TF mais rápido usado pelo pipeline: em pregão
# normal o gap fica em [0, 1) por construção; um gap muito maior só
# acontece com o mercado fechado. Achado MFC21-02 (herdr-review rodada 21,
# mfc-rev): sem esta checagem, Series.asof() reaproveitava silenciosamente
# o último score/preço de sexta-feira pra uma "entrada" de sábado/domingo,
# contando uma cesta que o mercado real nunca abriria.
MAX_MARKET_GAP_HOURS = 3.0


def _market_gap_hours(h1_times, target):
    """Horas decorridas entre a última barra H1 FECHADA e `target`. None se
    não houver histórico suficiente ainda."""
    i = _closed_bar_index(h1_times, target)
    if i is None:
        return None
    return (target - h1_times.iloc[i]).total_seconds() / 3600.0


def is_market_session_valid(h1_times, target):
    """True se `target` cai dentro de uma sessão com dado H1 fresco (mercado
    aberto), False se o gap sugere mercado fechado (fim de semana/feriado)
    ou se ainda não há histórico suficiente."""
    gap = _market_gap_hours(h1_times, target)
    return gap is not None and gap <= MAX_MARKET_GAP_HOURS


def evaluate_at(series, entry_server_dt, ref_dt):
    """Roda o pipeline canônico como se fosse `entry_server_dt`.
    Devolve {ccy: veredito} — mesmo formato que a dashboard usa.

    ``ref_dt`` é o instante explícito da decisão em BRT; ele é separado do
    horário do servidor usado para localizar as barras históricas.

    Usa a última barra FECHADA de cada TF (`_closed_bar_index`, não
    `_idx_at_or_before` puro) — ver docstring de `_closed_bar_index` sobre
    o look-ahead que isso evita (MFC21-03)."""
    slices = {}
    for tf in TFS:
        i = _closed_bar_index(series[tf]["times"], entry_server_dt)
        if i is None or i < 30:
            return None
        slices[tf] = i
    if not is_market_session_valid(series["H1"]["times"], entry_server_dt):
        return None
    out = {}
    for ccy in CURRENCIES:
        args = [series[tf]["scores"][ccy][: slices[tf] + 1] for tf in TFS]
        out[ccy] = evaluate_currency_confluence(ccy, *args, ref_dt=ref_dt)
    return out


def run(days=45, log_note=None):
    if not ensure_mt5():
        print("[-] MT5 não conectado.")
        return 1

    check_contract_size_consistency()

    print(f"[*] Carregando séries canônicas (mesmo motor da dashboard)...")
    series = load_series()
    if not series:
        print("[-] Séries canônicas indisponíveis.")
        return 1
    print(f"[*] Carregando preços H1 de {len(ALL_28_PAIRS)} pares...")
    prices = load_h1_prices()
    if not prices:
        print("[-] Preços H1 indisponíveis.")
        return 1
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
        brt_day = (datetime.now(BRT) - timedelta(days=d)).replace(
            hour=ENTRY_HOUR_BRT, minute=0, second=0, microsecond=0, tzinfo=None)
        srv = _brt_to_server(brt_day)
        if srv <= last_dt:
            entries.append((brt_day, srv))

    print(f"[*] {len(entries)} noites candidatas.\n")

    nights = baskets = 0
    gross = cost_total = 0.0
    # Decomposição do custo total (achado do árbitro efêmero, herdr-ask
    # mfc-5, 2026-08-28): spread e swap respondem a alavancas OPOSTAS —
    # spread aponta pra número de pernas/escolha de par, swap aponta pra
    # horário de saída/exposição ao rollover — e sem separar os dois o
    # "pedágio de custo" observado não diz qual mudar.
    spread_total = swap_total = 0.0
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
        verdicts = evaluate_at(series, srv_dt, brt_dt.replace(tzinfo=BRT))
        if verdicts is None:
            continue
        exit_srv = srv_dt + timedelta(hours=11)
        # Mesma checagem de sessão válida (MFC21-02) aplicada na SAÍDA: uma
        # entrada legítima pode ainda assim fechar num instante sem mercado
        # (ex.: swap de horário no fim de semana). Sem isso, o preço de
        # saída reaproveitaria silenciosamente a última cotação disponível.
        if not is_market_session_valid(series["H1"]["times"], exit_srv):
            continue
        actives = [(c, v) for c, v in verdicts.items() if v["trade_bias"] in ("COMPRA", "VENDA")]
        if not actives:
            continue

        night_gross = night_cost = 0.0
        night_spread = night_swap = 0.0
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
            night_spread += costs.last_basket_spread
            night_swap += costs.last_basket_swap
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
        spread_total += night_spread
        swap_total += night_swap
        if night_gross - night_cost >= 0:
            wins += 1

    if nights == 0:
        print("[-] Nenhuma noite operável reconstruída.")
        return 1

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
    print(f"  dos quais spread    : ${-spread_total:>9.2f}   (${-spread_total/nights:>6.2f}/noite, "
          f"{spread_total/cost_total*100 if cost_total else 0:.0f}% do custo)")
    print(f"  dos quais swap      : ${-swap_total:>9.2f}   (${-swap_total/nights:>6.2f}/noite, "
          f"{swap_total/cost_total*100 if cost_total else 0:.0f}% do custo)")
    print(f"PnL LÍQUIDO           : ${net:>9.2f}   (${net/nights:>6.2f}/noite)")
    print()
    print(f"custo médio por cesta : ${cost_total/baskets:.2f}"
          f"  (spread ${spread_total/baskets:.2f}, swap ${swap_total/baskets:.2f})")
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

    if log_note is not None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "script": "backtest_canonical.py",
            "days": days,
            "nights": nights,
            "note": log_note,
            "hypothesis": "Port A 5-TF deve substituir a decisão local 3-TF preservando o contrato de trade_bias.",
            "implementation": {
                "port": "A",
                "upstream_commit": "544d660",
                "thresholds": {"stop_zone": 0.20, "equilibrium": 0.05},
            },
            "baseline": {
                "name": "pre_port_3tf",
                "known_net_usd": -801.67,
                "known_window_days": 45,
                "known_nights": 32,
                "is_same_execution": False,
            },
            "window": {
                "days": days,
                "entry_hour_brt": ENTRY_HOUR_BRT,
                "exit_hour_brt": EXIT_HOUR_BRT,
                "mask": "closed_bar_and_valid_market_session",
            },
            "limitations": [
                "custo usa tick atual do MT5, não spread/swap histórico",
                "sem slippage, latência, requotes, fills parciais ou margem",
                "backtest usa barra fechada; produção ainda pode usar barra em formação",
            ],
            "parameters": {
                "lot": LOT,
                "entry_hour_brt": ENTRY_HOUR_BRT,
                "exit_hour_brt": EXIT_HOUR_BRT,
                "timeframes": list(TFS),
            },
            "rates_source": "historical_h1_prices; live CostModel tick for spread/swap",
            "cost_snapshot": "live tick sampled per reconstructed basket",
            "baskets": baskets,
            "bruto": round(gross, 2),
            "custo": round(cost_total, 2),
            "spread": round(spread_total, 2),
            "swap": round(swap_total, 2),
            "liquido": round(net, 2),
            "noites_lucrativas_pct": round(wins / nights * 100, 1) if nights else None,
        }
        path = append_result(record)
        print(f"\n[+] Resultado registrado em {path}")
    return 0


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 45
    note = sys.argv[2] if len(sys.argv) > 2 else None
    sys.exit(run(days=days, log_note=note))
