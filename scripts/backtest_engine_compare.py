"""
FERRAMENTA DE ANÁLISE, NÃO PARTE DO PIPELINE DE PRODUÇÃO.

Harness genérico de comparação de motores de confluência — "rodar o mesmo
backtest de 45 noites pra qualquer mudança de decisão, não só uma vez pro
item 6" (decisão do Breno, 2026-08-28: toda mudança que altere qual cesta
abre — não refactor comportamento-preservado — passa por isto antes de
adotar).

Nasceu do item 6 do plano de reconciliação com o upstream (Miquéias): validar
se a matriz 5-TF (`544d660`, com soberania macro e maturação temporal) decide
melhor do que o baseline 3-TF que estava em produção antes do Port A. O
baseline fica reproduzido aqui para que a comparação antes/depois não mude
de significado quando `agents/confluence_engine.py` passa a ser 5-TF.

`evaluate_currency_confluence_5tf()` abaixo é uma cópia (com atribuição) do
`agents/confluence_engine.py::evaluate_currency_confluence` do upstream no
commit `544d660` — NÃO uma adaptação. Importa `analyze_macro_currency`/
`analyze_operational_currency`/`analyze_tf_triad` dos NOSSOS módulos (não dos
dele): a "inversão de tese" (item 5, ainda não decidida) mudou macro_power/
op_power em `agents/macro_analyzer.py`/`operational_analyzer.py` do lado dele,
mas `_calculate_tf_vector` (abaixo) não lê esses campos — recalcula o vetor
direto do score/diff BRUTO da tríade, que é idêntico nos dois lados (mesma
matemática de CSS, mesma invariante 1). Ou seja, esta comparação isola
genuinamente o item 6 (motor de decisão 5-TF vs 3-TF) do item 5 (tese
textual) — não estão acoplados como o item 5 antigo temia.

Reusa scripts/backtest_canonical.py::evaluate_at()/load_series()/load_h1_prices()
tal como estão — mesmos dados, mesmo motor de custo (CostModel), e o MESMO
filtro de entrada que o sistema ao vivo usa (trade_bias != NEUTRO via
agents/confluence_engine.py — confirmado 2026-08-28 que NÃO existe filtro
adicional de "N de 5 timeframes concordam" no caminho real; esse filtro
existe só em web/history_tracker.py::TrackRecordEngine, um motor de
auditoria separado que este script não usa) — só troca QUAL função de
confluência decide trade_bias por noite, currency por currency.

Ruído conhecido, documentado aqui pra quem for interpretar o resultado: o
CostModel de cada cesta consulta o TICK ATUAL do MT5 (spread/swap do momento
da execução), não um custo histórico reconstruído — dois runs do "mesmo"
backtest em horários diferentes dão custo total diferente (visto na prática:
mesma cesta/PnL bruto exatos, custo ~2x diferente rodando de manhã vs. perto
do fechamento de sexta). `runs=N` faz várias passadas e reporta a faixa de
custo observada, em vez de um número único que parece mais preciso do que é.

A janela relativa padrão é exploratória. Uma janela OOS só é considerada
disjunta quando recebe um endpoint absoluto e isso é registrado no histórico;
em ambos os casos o resultado é evidência pra decisão do Breno, não uma
decisão já tomada.
"""

import os
import sys
import argparse
import hashlib
import json
import ntpath
import socket
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from agents.macro_analyzer import analyze_macro_currency
from agents.operational_analyzer import analyze_operational_currency
from agents.triad_analyzer import analyze_tf_triad
from agents.confluence_engine import BRT, evaluate_currency_confluence as evaluate_currency_confluence_port_a
from agents.portfolio_executor import get_portfolio_pairs, CostModel, ensure_mt5
from web.history_tracker import convert_pnl_to_usd
from scripts._backtest_results_log import (
    append_result,
    result_snapshot_digest,
    _code_provenance,
    _PROVENANCE_SOURCE_FILES,
    _runtime_provenance,
    validate_oos_window,
)
from scripts.backtest_canonical import (
    LOT, ENTRY_HOUR_BRT, TFS, TF_COUNTS, CURRENCIES, ALL_28_PAIRS,
    load_series, load_h1_prices, _idx_at_or_before, _closed_bar_index,
    is_market_session_valid, check_contract_size_consistency,
    _brt_to_server, MT5_AVAILABLE, mt5, MT5_PATH,
)


# ---------------------------------------------------------------------------
# Cópia atribuída da lógica de agents/confluence_engine.py do upstream,
# commit 544d660 (miqueiasa1/MFC). O núcleo vetorial é mantido para medir o
# comportamento do commit; os desvios de contrato abaixo são deliberados:
# ref_dt é obrigatório e qualquer instante aware é normalizado para BRT. Isso
# mantém o braço histórico reproduzível e deixa a diferença explícita.
# ---------------------------------------------------------------------------

def _get_tf_maturity(tf_name, ref_dt):
    """
    Calcula a Maturação Temporal Progressiva (M_TF) da barra.
    - Barras FECHADAS (D1, H4, H1 na rotina diária das 21h) = 1.00 (100%)
    - Barras FLUTUANTES / EM FORMAÇÃO (W1 e MN1) = Ponderadas pelo tempo decorrido.
    """
    if not isinstance(ref_dt, datetime):
        raise TypeError("ref_dt obrigatório e deve ser datetime")
    if ref_dt.tzinfo is None:
        ref_dt = ref_dt.replace(tzinfo=BRT)
    else:
        ref_dt = ref_dt.astimezone(BRT)

    if tf_name in ("D1", "H4", "H1"):
        return 1.00

    if tf_name == "W1":
        wday = ref_dt.weekday()
        if wday == 0:
            return 0.20
        elif wday == 1:
            return 0.40
        elif wday == 2:
            return 0.60
        elif wday == 3:
            return 0.80
        else:
            return 1.00

    if tf_name == "MN1":
        day = ref_dt.day
        return round(min(1.00, max(0.20, day / 30.0)), 2)

    return 1.00


def _calculate_tf_vector(tf_name, triad):
    """
    Calcula o vetor direcional base (-2.0 a +2.0) de um timeframe com base na
    Tríade Analítica — usa score/diff BRUTOS da tríade, não macro_power/op_power.
    """
    score = triad.get("score", 0.0)
    diff = triad.get("diff", 0.0)
    abs_diff = abs(diff)

    if score >= 0.16:
        if diff <= -0.03:
            return -2.0
        elif diff < 0:
            return -1.5
        elif diff > 0:
            return +1.5
        else:
            return -0.5

    if score <= -0.16:
        if diff >= +0.03:
            return +2.0
        elif diff > 0:
            return +1.5
        elif diff < 0:
            return -1.5
        else:
            return +0.5

    if -0.04 <= score <= 0.04:
        if diff > 0.002:
            return +0.40
        elif diff < -0.002:
            return -0.40
        return 0.0

    if abs_diff >= 0.05:
        return +1.0 if diff > 0 else -1.0
    elif diff > 0.002:
        return +0.5
    elif diff < -0.002:
        return -0.5

    return 0.0


def evaluate_currency_confluence_5tf(ccy, mn_s, w1_s, d1_s, h4_s, h1_s, ref_dt):
    """Motor 5-TF atribuído ao upstream (544d660), com contrato BRT explícito."""
    macro = analyze_macro_currency(ccy, mn_s, w1_s, d1_s)
    op = analyze_operational_currency(ccy, h4_s, h1_s, macro)

    triads = {
        "MN1": macro["mn_triad"],
        "W1": macro["w1_triad"],
        "D1": macro["d1_triad"],
        "H4": op["h4_triad"],
        "H1": op["h1_triad"],
    }
    weights = {"D1": 3.0, "H4": 2.0, "W1": 1.5, "MN1": 1.5, "H1": 1.0}

    base_vectors = {tf: _calculate_tf_vector(tf, triads[tf]) for tf in triads}
    maturities = {tf: _get_tf_maturity(tf, ref_dt) for tf in triads}

    mn_mature_vec = base_vectors["MN1"] * maturities["MN1"]
    w1_mature_vec = base_vectors["W1"] * maturities["W1"]
    macro_bias = round(mn_mature_vec + w1_mature_vec, 3)

    penalties = {"MN1": 1.0, "W1": 1.0, "D1": 1.0, "H4": 1.0, "H1": 1.0}
    is_counter_flow_d1 = False
    is_counter_flow_h4 = False

    if macro_bias > 0.30:
        for tf in ("D1", "H4", "H1"):
            if base_vectors[tf] < 0:
                penalties[tf] = 0.40
                if tf == "D1":
                    is_counter_flow_d1 = True
                if tf == "H4":
                    is_counter_flow_h4 = True
    elif macro_bias < -0.30:
        for tf in ("D1", "H4", "H1"):
            if base_vectors[tf] > 0:
                penalties[tf] = 0.40
                if tf == "D1":
                    is_counter_flow_d1 = True
                if tf == "H4":
                    is_counter_flow_h4 = True

    vectors = {tf: round(base_vectors[tf] * maturities[tf] * penalties[tf], 3) for tf in triads}
    weighted_score = sum(vectors[tf] * weights[tf] for tf in weights)
    norm_score = round((weighted_score / 13.5) * 10.0, 2)

    up_tfs = [tf for tf, v in vectors.items() if v > 0]
    dn_tfs = [tf for tf, v in vectors.items() if v < 0]

    d1_vec = vectors["D1"]
    h4_vec = vectors["H4"]
    h1_vec = vectors["H1"]

    confluence_state = "EQUILÍBRIO"
    final_verdict = "AGUARDAR DEFINIÇÃO"
    trade_bias = "NEUTRO"

    if macro_bias > 0.30 and (is_counter_flow_d1 or is_counter_flow_h4) and h1_vec > 0:
        trade_bias = "COMPRA"
        confluence_state = "RETOMADA DE FORÇA NO SUPORTE (PULLBACK ENCERRADO)"
        final_verdict = "COMPRA NA RETOMADA (ALINHADO COM MACRO)"
    elif macro_bias < -0.30 and (is_counter_flow_d1 or is_counter_flow_h4) and h1_vec < 0:
        trade_bias = "VENDA"
        confluence_state = "RETOMADA DE FRAQUEZA NA RESISTÊNCIA (REPIQUE ENCERRADO)"
        final_verdict = "VENDA NA RETOMADA (ALINHADO COM MACRO)"
    elif norm_score <= -1.5 or (d1_vec < 0 and (h4_vec < 0 or macro_bias < -0.30) and len(dn_tfs) >= 3):
        trade_bias = "VENDA"
        if len(dn_tfs) == 5:
            confluence_state = "CONFLUÊNCIA TOTAL DE QUEDA (5-TF ALINHADOS)"
            final_verdict = "VENDA FORTE (FLUXO INSTITUCIONAL COMPLETO)"
        elif len(dn_tfs) >= 3:
            confluence_state = f"CONFLUÊNCIA DE QUEDA ({len(dn_tfs)}/5 TIMEFRAMES)"
            final_verdict = "VENDA (BUSCANDO FUNDO DO BOX)"
        else:
            confluence_state = "QUEDA ANCORADA PELO DIÁRIO (D1/H4)"
            final_verdict = "VENDA (PRESSÃO VENDEDORA)"
    elif norm_score >= +1.5 or (d1_vec > 0 and (h4_vec > 0 or macro_bias > 0.30) and len(up_tfs) >= 3):
        trade_bias = "COMPRA"
        if len(up_tfs) == 5:
            confluence_state = "CONFLUÊNCIA TOTAL DE ALTA (5-TF ALINHADOS)"
            final_verdict = "COMPRA FORTE (FLUXO INSTITUCIONAL COMPLETO)"
        elif len(up_tfs) >= 3:
            confluence_state = f"CONFLUÊNCIA DE ALTA ({len(up_tfs)}/5 TIMEFRAMES)"
            final_verdict = "COMPRA (BUSCANDO TOPO DO BOX)"
        else:
            confluence_state = "ALTA ANCORADA PELO DIÁRIO (D1/H4)"
            final_verdict = "COMPRA (PRESSÃO COMPRADORA)"
    else:
        confluence_state = "BOX DE EQUILÍBRIO (TESTE DO 0)"
        final_verdict = "AGUARDAR DEFINIÇÃO"
        trade_bias = "NEUTRO"

    return {
        "ccy": ccy,
        "trade_bias": trade_bias,
        "confluence_state": confluence_state,
        "final_verdict": final_verdict,
        "score_total": norm_score,
        "macro_bias": macro_bias,
        "vectors": vectors,
        "aligned_up_count": len(up_tfs),
        "aligned_dn_count": len(dn_tfs),
    }


# ---------------------------------------------------------------------------
# Harness de comparação — mesmo padrão de scripts/backtest_selection_rules.py
# ---------------------------------------------------------------------------

# Registro de motores comparáveis. Cada função recebe (ccy, mn, w1, d1, h4,
# h1, ref_dt) e devolve um veredito dict com pelo menos "trade_bias"
# (COMPRA/VENDA/NEUTRO) e "score_total" (só usado pra imprimir exemplos de
# discordância — pode ser 0.0 se o motor não tiver um score comparável).
# Adicionar uma comparação nova (ex.: ATR diferenciado, item 7) é registrar
# outra entrada aqui, não escrever um script novo do zero.
def _run_3tf(ccy, mn, w1, d1, h4, h1, ref_dt=None):
    """Baseline congelado do motor local anterior ao Port A.

    Mantém exatamente os pesos e o limiar da decisão 3-TF pré-port para que
    o backtest compare a mesma implementação antes e depois da mudança.
    """
    macro = analyze_macro_currency(ccy, mn, w1, d1)
    op = analyze_operational_currency(ccy, h4, h1, macro)
    d1_curr = float(d1[-1]) if len(d1) > 0 else 0.0
    h4_curr = float(h4[-1]) if len(h4) > 0 else 0.0
    h1_curr = float(h1[-1]) if len(h1) > 0 else 0.0
    score_3tf = (d1_curr * 0.40) + (h4_curr * 0.35) + (h1_curr * 0.25)
    if score_3tf >= 0.10:
        state = "CONFLUÊNCIA DE ALTA (FLUXO COMPRADOR 3-TF)"
        verdict = "COMPRA (BUSCANDO TOPO DO BOX)"
        bias = "COMPRA"
    elif score_3tf <= -0.10:
        state = "CONFLUÊNCIA DE QUEDA (FLUXO VENDEDOR 3-TF)"
        verdict = "VENDA (BUSCANDO FUNDO DO BOX)"
        bias = "VENDA"
    else:
        state = "BOX DE EQUILÍBRIO (TESTE DO 0)"
        verdict = "AGUARDAR DEFINIÇÃO"
        bias = "NEUTRO"
    return {
        "ccy": ccy,
        "macro": macro,
        "operational": op,
        "confluence_state": state,
        "final_verdict": verdict,
        "trade_bias": bias,
        "has_divergence": op["has_divergence"],
        "divergence_alert": op["divergence_alert"],
        "score_total": round(score_3tf, 2),
    }


def _run_port_a(ccy, mn, w1, d1, h4, h1, ref_dt=None):
    """Implementação local efetivamente portada do Port A (544d660)."""
    return evaluate_currency_confluence_port_a(
        ccy, mn, w1, d1, h4, h1, ref_dt=ref_dt
    )


def _run_5tf_upstream(ccy, mn, w1, d1, h4, h1, ref_dt=None):
    # ref_dt explícito (item 4 dobrado aqui): sem isso, o 5-TF cairia em
    # datetime.now() e o resultado deixaria de ser reproduzível pra uma
    # noite histórica.
    return evaluate_currency_confluence_5tf(ccy, mn, w1, d1, h4, h1, ref_dt=ref_dt)


# R1 (herdr-review rodada 21, mfc-rev-2): o nosso motor decide por NÍVEL do
# score (score_3tf = D1*0.40+H4*0.35+H1*0.25) — medido como estatisticamente
# independente da direção do próximo movimento (corr(nível, derivada) =
# -0.019 na amostra dela). Esta variante troca nível por INCLINAÇÃO
# (_calculate_tf_vector, já escrito/testado pro motor 5-TF acima), mantendo
# NOSSOS 3 TFs e NOSSOS pesos — isola essa ÚNICA variável, em vez de adotar
# o motor 5-TF inteiro (que muda TFs, pesos, vetor E penalidade de
# contrafluxo de uma vez, sem dizer qual mudança pagou o resultado).
def make_3tf_vector_engine(threshold):
    """Fábrica de engine — o limiar é parâmetro explícito (R2), nunca
    escolhido ajustando na mesma amostra que mede o resultado. Cada valor
    testado ganha seu próprio nome em ENGINES/threshold_sweep."""
    def _run(ccy, mn, w1, d1, h4, h1, ref_dt=None):
        triads = {
            "D1": analyze_tf_triad("D1", d1),
            "H4": analyze_tf_triad("H4", h4),
            "H1": analyze_tf_triad("H1", h1),
        }
        vec = {tf: _calculate_tf_vector(tf, triads[tf]) for tf in triads}
        score = vec["D1"] * 0.40 + vec["H4"] * 0.35 + vec["H1"] * 0.25
        if score >= threshold:
            trade_bias = "COMPRA"
        elif score <= -threshold:
            trade_bias = "VENDA"
        else:
            trade_bias = "NEUTRO"
        return {"ccy": ccy, "trade_bias": trade_bias, "score_total": round(score, 3)}
    return _run


# Grade de limiares candidatos pra threshold_sweep() — não é uma escolha,
# é a faixa que o vetor por TF pode assumir (múltiplos de 0.5 dentro de
# +/-2.0, ver _calculate_tf_vector) pra decidir T com dado, não com chute.
VECTOR_THRESHOLDS = (0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0)


def _mean_stderr(values):
    """Média e erro padrão amostral de uma série de PnLs por cesta."""
    n = len(values)
    if not n:
        return None, None
    mean = sum(values) / n
    if n == 1:
        return mean, None
    variance = sum((value - mean) ** 2 for value in values) / (n - 1)
    return mean, (variance / n) ** 0.5


def _normalize_window_end(end_brt=None):
    """Normaliza o fim da janela para um datetime ingênuo em BRT.

    O harness trabalha com datas de entrada na hora operacional BRT. Uma
    janela explícita pode ser aware (convertida para BRT) ou ingênua (já
    interpretada como BRT); quando omitida, usa a data de hoje na hora de
    entrada. O intervalo medido é [end - days, end), portanto a data de
    ``end_brt`` não entra na amostra.
    """
    if end_brt is None:
        return datetime.now(BRT).replace(
            hour=ENTRY_HOUR_BRT, minute=0, second=0, microsecond=0, tzinfo=None
        )
    if not isinstance(end_brt, datetime):
        raise TypeError("end_brt deve ser datetime ou None")
    if end_brt.tzinfo is not None:
        end_brt = end_brt.astimezone(BRT).replace(tzinfo=None)
    return end_brt


def _brt_iso(value):
    return value.replace(tzinfo=BRT).isoformat()


def _stable_digest(value):
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _data_snapshot_digest(series, prices):
    """Identifica exatamente as séries e preços carregados nesta execução."""
    snapshot = {"series": {}, "prices": {}}
    for tf in TFS:
        snapshot["series"][tf] = {
            "times": [str(value) for value in series[tf]["times"]],
            "scores": {
                ccy: [float(value) for value in series[tf]["scores"][ccy]]
                for ccy in CURRENCIES
            },
            "quality": series[tf].get("quality", {"status": "unknown"}),
        }
    for pair in sorted(prices):
        frame = prices[pair]
        snapshot["prices"][pair] = {
            "times": [str(value) for value in frame.index],
            "opens": [float(value) for value in frame.tolist()],
        }
    return _stable_digest(snapshot)


def _canonical_windows_path(value):
    return ntpath.normcase(str(value or "").replace("/", "\\")).rstrip("\\")


def _assert_oos_terminal_configuration():
    """Recusa OOS antes de qualquer conexão se o terminal não é o dedicado."""
    if os.environ.get("MFC_BACKTEST_TERMINAL_ISOLATED") != "1":
        raise RuntimeError("janela OOS exige MFC_BACKTEST_TERMINAL_ISOLATED=1")
    expected_path = _canonical_windows_path(MT5_PATH)
    if (ntpath.basename(expected_path) != "terminal64.exe"
            or ntpath.basename(ntpath.dirname(expected_path)) != "mfc-backtest"):
        raise RuntimeError(
            "OOS exige CSS_MT5_TERMINAL_PATH apontando para a instância mfc-backtest"
        )


def _assert_oos_terminal_runtime():
    """Confere caminho observado e conta demo depois do initialize do MT5."""
    if not MT5_AVAILABLE or mt5 is None:
        raise RuntimeError("OOS exige MT5 disponível")
    terminal_info = mt5.terminal_info()
    observed_path = getattr(terminal_info, "path", None) if terminal_info else None
    expected_dir = _canonical_windows_path(ntpath.dirname(MT5_PATH))
    if _canonical_windows_path(observed_path) != expected_dir:
        raise RuntimeError(
            "OOS conectado a terminal diferente do caminho dedicado: "
            f"observado={observed_path!r}, esperado={ntpath.dirname(MT5_PATH)!r}"
        )
    account = mt5.account_info()
    demo_mode = getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", None)
    if account is None or demo_mode is None or getattr(account, "trade_mode", None) != demo_mode:
        raise RuntimeError("OOS exige conta demo observada no terminal dedicado")
    return terminal_info, account


def _execution_provenance(contract_snapshot, data_digest, cost_digests,
                          require_isolated=False):
    """Registra no processo produtor o envelope da evidência."""
    isolated = os.environ.get("MFC_BACKTEST_TERMINAL_ISOLATED") == "1"
    account = terminal = None
    if MT5_AVAILABLE and mt5 is not None:
        terminal_info = mt5.terminal_info()
        info = mt5.account_info()
        if info is not None:
            account = {
                "login": getattr(info, "login", None),
                "server": getattr(info, "server", None),
                "currency": getattr(info, "currency", None),
                "trade_mode": getattr(info, "trade_mode", None),
                "trade_allowed": getattr(info, "trade_allowed", None),
            }
        terminal = {
            "path": MT5_PATH,
            "observed_path": getattr(terminal_info, "path", None),
        }
    commit, dirty, source_digest = _code_provenance()
    commit = commit or os.environ.get("MFC_BACKTEST_SOURCE_COMMIT") or None
    runtime_account, runtime_terminal, runtime_contract = _runtime_provenance()
    if account is None:
        account = runtime_account
    if terminal is None:
        terminal = runtime_terminal
    return {
        "status": "complete" if (
            isinstance(source_digest, str)
            and isinstance(account, dict) and isinstance(terminal, dict)
            and contract_snapshot.get("valid_for_pnl")
            and isinstance(data_digest, str)
            and isolated
        ) else "partial",
        "code_commit": commit,
        "worktree_dirty": dirty,
        "code_source_digest": source_digest,
        "code_source_files": list(_PROVENANCE_SOURCE_FILES),
        "account": account,
        "terminal": terminal,
        "contract_size": contract_snapshot,
        "data_snapshot": {"series_and_h1_prices_digest": data_digest},
        "cost_snapshot": {
            "source": "current MT5 ticks sampled by CostModel",
            "per_run_observation_digests": cost_digests,
        },
        "execution": {
            "host": socket.gethostname(),
            "terminal_path": MT5_PATH,
            "is_production_terminal": False if isolated else None,
            "terminal_isolation_asserted": isolated,
            "orders_sent": False,
            "orders_sent_basis": "comparison harness contains no order_send call",
            "runtime_contract_snapshot": runtime_contract or contract_snapshot,
        },
    }


def _quality_status(stats):
    """Classifica integridade de uma passada sem esconder custo não modelado."""
    if stats["degraded_baskets"] or stats["skipped_missing_price"]:
        return "degraded"
    if stats["swap_unmodeled_baskets"]:
        return "partial_model"
    return "clean"


def _overall_quality_status(css_history_status, stats, engine_names):
    """Combina qualidade do histórico CSS e qualidade do custo reconstruído."""
    if css_history_status != "clean" or any(
        _quality_status(stats[name]) == "degraded" for name in engine_names
    ):
        return "degraded"
    if any(_quality_status(stats[name]) == "partial_model" for name in engine_names):
        return "partial_model"
    return "clean"


def _pass_summary(pass_result, engine_names):
    """Resumo persistível de uma passada individual do CostModel."""
    stats, _agree, _disagree, _examples, active, nights, paired, coverage = pass_result
    engines = {}
    for name in engine_names:
        current = stats[name]
        engines[name] = {
            "baskets": current["baskets"],
            "bruto": round(current["pnl"], 2),
            "custo": round(current["cost"], 2),
            "liquido": round(current["pnl"] - current["cost"], 2),
            "active_signals": sum(active[name].values()),
            "degraded_baskets": current["degraded_baskets"],
            "swap_unmodeled_baskets": current["swap_unmodeled_baskets"],
            "skipped_missing_price": current["skipped_missing_price"],
            "quality_status": _quality_status(current),
        }
    paired_mean, paired_stderr = _mean_stderr(paired)
    return {
        "nights_evaluated": nights,
        "coverage": coverage,
        "engines": engines,
        "cost_observation_digests": {
            name: _stable_digest(stats[name].get("cost_observations", []))
            for name in engine_names
        },
        "paired_net_delta_per_night": {
            "mean": round(paired_mean, 3) if paired_mean is not None else None,
            "stderr": round(paired_stderr, 3) if paired_stderr is not None else None,
            "n": len(paired),
        },
    }


def _aggregate_pass_summaries(pass_summaries, engine_names):
    """Agrega min/máx/média para que runs=N seja auditável no JSON."""
    aggregate = {}
    for name in engine_names:
        aggregate[name] = {}
        for metric in ("bruto", "custo", "liquido", "baskets",
                       "degraded_baskets", "swap_unmodeled_baskets",
                       "skipped_missing_price"):
            values = [summary["engines"][name][metric] for summary in pass_summaries]
            aggregate[name][metric] = {
                "min": min(values),
                "max": max(values),
                "mean": round(sum(values) / len(values), 3),
            }
    paired = [
        summary["paired_net_delta_per_night"]["mean"]
        for summary in pass_summaries
        if summary["paired_net_delta_per_night"]["mean"] is not None
    ]
    return {
        "by_engine": aggregate,
        "paired_net_delta_per_night_mean": {
            "min": min(paired) if paired else None,
            "max": max(paired) if paired else None,
            "mean": round(sum(paired) / len(paired), 3) if paired else None,
            "n_runs": len(paired),
        },
    }

ENGINES = {
    "3tf_baseline": _run_3tf,
    "5tf_port_a": _run_port_a,
    "5tf_upstream": _run_5tf_upstream,
    "3tf_vector": make_3tf_vector_engine(1.0),  # T inicial pra comparação lado a lado — ver threshold_sweep() pra escolher de verdade
}


def evaluate_at_all(series, entry_server_dt, ref_dt, engine_names):
    """Roda os motores pedidos sobre a MESMA janela histórica. Devolve
    {nome_motor: {ccy: veredito}} ou None se não houver barra suficiente ou
    a sessão não for válida.

    Usa a MESMA máscara que scripts/backtest_canonical.py::evaluate_at()
    (barra fechada + sessão de mercado válida) — achados MFC21-02/03,
    herdr-review rodada 21 (mfc-rev): sem isso, os dois motores comparados
    veriam look-ahead da barra em formação e cestas em fim de semana/
    feriado, com máscaras potencialmente diferentes já que os dois
    harnesses tinham cada um sua própria cópia do critério de warmup."""
    slices = {}
    for tf in TFS:
        i = _closed_bar_index(series[tf]["times"], entry_server_dt)
        if i is None or i < 30:
            return None
        slices[tf] = i
    if not is_market_session_valid(series["H1"]["times"], entry_server_dt):
        return None

    out = {name: {} for name in engine_names}
    for ccy in CURRENCIES:
        args = [series[tf]["scores"][ccy][: slices[tf] + 1] for tf in TFS]
        for name in engine_names:
            out[name][ccy] = ENGINES[name](ccy, *args, ref_dt=ref_dt)
    return out


def _basket_pnl(ccy, bias_word, prices, srv_dt, exit_srv):
    """Reproduz o cálculo de PnL bruto de uma cesta de scripts/backtest_canonical.py::run(),
    isolado numa função pra ser chamado pelos dois motores sem duplicar o laço inteiro."""
    bias = "BUY" if bias_word == "COMPRA" else "SELL"
    legs = get_portfolio_pairs(ccy, bias)
    gross = 0.0
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
        pnl, _ = convert_pnl_to_usd(leg["pair"], leg["action"], p_in, p_out, LOT)
        gross += pnl
    costs = CostModel(LOT)
    cost = costs.basket(ccy, bias)
    return (
        gross,
        cost,
        costs.last_basket_spread,
        costs.last_basket_swap,
        bool(costs.last_basket_degraded),
        bool(costs.last_basket_swap_unmodeled),
    )


def _one_pass(series, prices, days, engine_names, end_brt=None):
    """Uma passada completa pelos `days` dias, pros motores pedidos. Separado
    de compare() pra permitir `runs=N` (custo varia por passada, decisão
    (trade_bias) não — reevaluate_at_all é determinístico dado o histórico,
    só o tick de custo consultado ao vivo muda entre chamadas)."""
    # "wins"/"basket_wins" — achado MFC21-04 (herdr-review rodada 21,
    # mfc-rev): o "win%" da versão anterior era taxa de NOITE lucrativa
    # (soma de todas as cestas da noite >= 0), facilmente lido como taxa de
    # acerto de cesta individual. Rastreia os dois separadamente agora.
    # "spread"/"swap" — achado do árbitro efêmero (herdr-ask mfc-5,
    # 2026-08-28): sem decompor o custo total nos dois termos, não dá pra
    # saber se o "pedágio" observado aponta pra número de pernas/par
    # (spread) ou pra horário de saída/exposição a rollover (swap) — cada
    # resposta pede uma ação diferente.
    stats = {name: {"pnl": 0.0, "cost": 0.0, "spread": 0.0, "swap": 0.0,
                     "baskets": 0, "nights_with_baskets": 0, "wins": 0,
                     "basket_wins": 0, "net_per_basket": [],
                     "degraded_baskets": 0, "swap_unmodeled_baskets": 0,
                     "skipped_missing_price": 0, "cost_observations": []}
             for name in engine_names}
    agree = 0
    disagree = 0
    disagreement_examples = []
    active_signal_counts = {name: {c: 0 for c in CURRENCIES} for name in engine_names}
    nights_evaluated = 0
    candidate_nights = 0
    skipped_no_verdict = 0
    skipped_invalid_exit = 0
    evaluated_brt_days = []
    paired_net_deltas = []
    window_end = _normalize_window_end(end_brt)

    for d in range(days, 0, -1):
        brt_day = (window_end - timedelta(days=d)).replace(
            hour=ENTRY_HOUR_BRT, minute=0, second=0, microsecond=0, tzinfo=None)
        srv_dt = _brt_to_server(brt_day)
        exit_srv = srv_dt + timedelta(hours=11)

        if not is_market_session_valid(series["H1"]["times"], srv_dt):
            continue
        candidate_nights += 1
        verdicts = evaluate_at_all(
            series, srv_dt, brt_day.replace(tzinfo=BRT), engine_names
        )
        if verdicts is None:
            skipped_no_verdict += 1
            continue
        # Mesma checagem de sessão válida na SAÍDA que
        # backtest_canonical.py::run() aplica (MFC21-02) — mantém a máscara
        # idêntica entre os dois harnesses também no lado do fechamento.
        if not is_market_session_valid(series["H1"]["times"], exit_srv):
            skipped_invalid_exit += 1
            continue
        nights_evaluated += 1
        evaluated_brt_days.append(brt_day.replace(tzinfo=BRT).isoformat())

        night_pnl = {name: 0.0 for name in engine_names}
        night_cost = {name: 0.0 for name in engine_names}
        night_spread = {name: 0.0 for name in engine_names}
        night_swap = {name: 0.0 for name in engine_names}
        night_baskets = {name: 0 for name in engine_names}

        for ccy in CURRENCIES:
            biases = {name: verdicts[name][ccy]["trade_bias"] for name in engine_names}
            for name in engine_names:
                if biases[name] in ("COMPRA", "VENDA"):
                    active_signal_counts[name][ccy] += 1

            distinct = set(biases.values())
            if len(distinct) == 1:
                agree += 1
            else:
                disagree += 1
                if len(disagreement_examples) < 12:
                    disagreement_examples.append((
                        brt_day.strftime("%Y-%m-%d"), ccy,
                        {name: (biases[name], verdicts[name][ccy].get("score_total", 0.0))
                         for name in engine_names},
                    ))

            for name in engine_names:
                bias = biases[name]
                if bias not in ("COMPRA", "VENDA"):
                    continue
                result = _basket_pnl(ccy, bias, prices, srv_dt, exit_srv)
                if result is None:
                    stats[name]["skipped_missing_price"] += 1
                    continue
                gross, cost, spread, swap, degraded, swap_unmodeled = result
                night_pnl[name] += gross
                night_cost[name] += cost
                night_spread[name] += spread
                night_swap[name] += swap
                night_baskets[name] += 1
                stats[name]["net_per_basket"].append(gross - cost)
                if degraded:
                    stats[name]["degraded_baskets"] += 1
                if swap_unmodeled:
                    stats[name]["swap_unmodeled_baskets"] += 1
                if gross - cost >= 0:
                    stats[name]["basket_wins"] += 1
                stats[name]["cost_observations"].append({
                    "currency": ccy,
                    "bias": bias,
                    "spread": spread,
                    "swap": swap,
                    "degraded": degraded,
                    "swap_unmodeled": swap_unmodeled,
                })

        for name in engine_names:
            stats[name]["pnl"] += night_pnl[name]
            stats[name]["cost"] += night_cost[name]
            stats[name]["spread"] += night_spread[name]
            stats[name]["swap"] += night_swap[name]
            stats[name]["baskets"] += night_baskets[name]
            if night_baskets[name] > 0:
                stats[name]["nights_with_baskets"] += 1
                if night_pnl[name] - night_cost[name] >= 0:
                    stats[name]["wins"] += 1

        # Comparação pareada por noite: só inclui noites em que ambos os
        # motores reconstruíram pelo menos uma cesta. O pareamento é temporal
        # (não implica que as cestas/pares escolhidos sejam idênticos).
        if "3tf_baseline" in engine_names and "5tf_port_a" in engine_names:
            if (night_baskets["3tf_baseline"] > 0
                    and night_baskets["5tf_port_a"] > 0):
                paired_net_deltas.append(
                    (night_pnl["5tf_port_a"] - night_cost["5tf_port_a"])
                    - (night_pnl["3tf_baseline"] - night_cost["3tf_baseline"])
                )

    price_missing_points = []
    for brt_iso in evaluated_brt_days:
        brt_day = datetime.fromisoformat(brt_iso).astimezone(BRT).replace(tzinfo=None)
        srv_dt = _brt_to_server(brt_day)
        exit_srv = srv_dt + timedelta(hours=11)
        for pair in ALL_28_PAIRS:
            price_series = prices.get(pair)
            if price_series is None:
                price_missing_points.extend([f"{brt_iso}:{pair}:entry", f"{brt_iso}:{pair}:exit"])
                continue
            for label, target in (("entry", srv_dt), ("exit", exit_srv)):
                try:
                    value = float(price_series.asof(target))
                except Exception:
                    value = float("nan")
                if not value > 0:
                    price_missing_points.append(f"{brt_iso}:{pair}:{label}")

    coverage = {
        "candidate_nights": candidate_nights,
        "evaluated_nights": nights_evaluated,
        "skipped_no_verdict": skipped_no_verdict,
        "skipped_invalid_exit": skipped_invalid_exit,
        "evaluated_dates_brt": evaluated_brt_days,
        "price_missing_points": price_missing_points,
    }
    return (
        stats,
        agree,
        disagree,
        disagreement_examples,
        active_signal_counts,
        nights_evaluated,
        paired_net_deltas,
        coverage,
    )


def compare(days=45, engine_names=None, runs=1, log_note=None, end_brt=None,
            sample_role="exploratory", development_start_brt=None,
            use_histdata_mn1_warmup=False):
    """Compara N motores registrados em ENGINES sobre `days` noites.
    `runs>1` repete a passada inteira e reporta a faixa de custo/líquido
    observada, pra não esconder o ruído do CostModel (tick ao vivo) atrás de
    um número único — ver docstring do módulo. `log_note` (opcional):
    registra o resultado em reports/backtest_history.json — passe uma
    descrição curta do que mudou desde o último run, pra rastrear
    melhora/piora conforme o código/parâmetros evoluem. `end_brt` fixa o fim
    da janela em BRT para permitir amostra OOS disjunta; sem ele, a janela é
    relativa ao relógio atual e `sample_role` deve permanecer exploratório.
    Um papel `oos_disjoint` exige endpoint explícito e um cutoff explícito da
    amostra de desenvolvimento; a janela precisa terminar no máximo nesse
    cutoff. `use_histdata_mn1_warmup` (opt-in explícito, desligado por
    padrão): estende o prefixo de aquecimento MN1 curto da Exness com o
    cache validado da HistData.com (scripts/fetch_histdata_mn1_warmup.py) —
    só o prefixo antigo necessário pro ATR(100) convergir, nunca as barras
    recentes/decisórias; ver load_mn1_series_with_warmup em
    scripts/backtest_canonical.py."""
    if not isinstance(days, int) or days <= 0:
        raise ValueError("days deve ser inteiro positivo")
    if not isinstance(runs, int) or runs <= 0:
        raise ValueError("runs deve ser inteiro positivo")
    if sample_role not in {"exploratory", "oos_disjoint"}:
        raise ValueError("sample_role deve ser exploratory ou oos_disjoint")
    if sample_role == "oos_disjoint" and end_brt is None:
        raise ValueError("oos_disjoint exige end_brt explícito")
    window_end = _normalize_window_end(end_brt)
    window_start = window_end - timedelta(days=days)
    development_start = None
    if development_start_brt is not None:
        development_start = _normalize_window_end(development_start_brt)
    if sample_role == "oos_disjoint":
        if development_start is None:
            raise ValueError("oos_disjoint exige development_start_brt explícito")
        validate_oos_window({
            "days": days,
            "start_brt": _brt_iso(window_start),
            "end_brt": _brt_iso(window_end),
            "development_start_brt": _brt_iso(development_start),
        })
        if not log_note:
            raise ValueError("oos_disjoint exige log_note explícita")
    engine_names = engine_names or list(ENGINES.keys())
    for name in engine_names:
        if name not in ENGINES:
            print(f"[-] Motor desconhecido: {name!r}. Disponíveis: {list(ENGINES)}")
            return 1

    if sample_role == "oos_disjoint":
        _assert_oos_terminal_configuration()
    if not ensure_mt5():
        print("[-] MT5 não conectado — abortando comparação; não usar dados degradados.")
        return 1
    if sample_role == "oos_disjoint":
        _assert_oos_terminal_runtime()
    contract_snapshot = check_contract_size_consistency(
        strict=sample_role == "oos_disjoint"
    )

    print(f"[*] Carregando séries canônicas (mesmo motor da dashboard)...")
    series = load_series(
        require_clean=sample_role == "oos_disjoint",
        use_histdata_mn1_warmup=use_histdata_mn1_warmup,
    )
    if not series:
        print("[-] Séries canônicas indisponíveis.")
        return 1
    css_history_quality = {
        tf: dict(
            series.get(tf, {}).get("quality", {"status": "unknown"})
            if isinstance(series, dict) and isinstance(series.get(tf, {}), dict)
            else {"status": "unknown"}
        )
        for tf in TFS
    }
    css_history_status = (
        "clean" if all(item.get("status") == "clean" for item in css_history_quality.values())
        else "degraded"
    )
    print(f"[+] Qualidade histórica CSS: {css_history_status}")
    print(f"[*] Carregando preços H1 de 28 pares...")
    prices = load_h1_prices()
    if not prices:
        print("[-] Preços H1 indisponíveis.")
        return 1
    print(f"[+] {len(prices)} pares com preço disponível.\n")
    if sample_role == "oos_disjoint" and set(prices) != set(ALL_28_PAIRS):
        raise RuntimeError(
            "OOS exige preços H1 para os 28 pares: "
            f"faltantes={sorted(set(ALL_28_PAIRS) - set(prices))}"
        )
    data_digest = _data_snapshot_digest(series, prices)

    passes = []
    for r in range(runs):
        if runs > 1:
            print(f"[*] Passada {r + 1}/{runs}...")
        passes.append(_one_pass(series, prices, days, engine_names, end_brt=window_end))

    # Decisão (trade_bias, agree/disagree, contagem de sinais) é idêntica em toda
    # passada — só custo/líquido variam. Usa a última passada pra tudo que
    # não é custo, e agrega net líquido de todas as passadas pra reportar a
    # faixa.
    (stats, agree, disagree, disagreement_examples, active_signal_counts,
     nights_evaluated, paired_net_deltas, coverage) = passes[-1]
    if any(pass_result[-1] != coverage for pass_result in passes):
        raise RuntimeError("cobertura divergente entre passadas do mesmo backtest")
    if sample_role == "oos_disjoint":
        if (coverage["candidate_nights"] < 30
                or coverage["evaluated_nights"] != coverage["candidate_nights"]
                or coverage["skipped_no_verdict"]
                or coverage["skipped_invalid_exit"]
                or coverage["price_missing_points"]):
            raise RuntimeError("OOS sem cobertura temporal/preço completa")
        for name in engine_names:
            if (stats[name]["baskets"] < 1
                    or stats[name]["skipped_missing_price"]
                    or stats[name]["degraded_baskets"]
                    or stats[name]["swap_unmodeled_baskets"]):
                raise RuntimeError(f"OOS sem reconstrução limpa para {name}")
    pass_summaries = [
        _pass_summary(pass_result, engine_names) for pass_result in passes
    ]
    runs_summary = {
        "reported_pass": runs,
        "per_run": [
            {"run": index + 1, **summary}
            for index, summary in enumerate(pass_summaries)
        ],
        "aggregate": _aggregate_pass_summaries(pass_summaries, engine_names),
        "coverage": coverage,
    }
    producer_provenance = _execution_provenance(
        contract_snapshot,
        data_digest,
        [summary["cost_observation_digests"] for summary in pass_summaries],
        require_isolated=sample_role == "oos_disjoint",
    )
    net_by_run = {name: [p[0][name]["pnl"] - p[0][name]["cost"] for p in passes] for name in engine_names}

    total_calls = agree + disagree
    print("=" * 70)
    print(f"  COMPARAÇÃO DE MOTORES: {', '.join(engine_names)} — {days} dias"
          + (f", {runs} passadas de custo" if runs > 1 else ""))
    print("=" * 70)
    print(f"noites avaliadas       : {nights_evaluated}")
    print(f"decisões comparadas    : {total_calls} (moeda x noite)")
    agree_pct = (agree / total_calls * 100) if total_calls else None
    disagree_pct = (disagree / total_calls * 100) if total_calls else None
    print(f"concordam (todos iguais): {agree} ({agree_pct:.1f}%)" if agree_pct is not None
          else "concordam (todos iguais): 0 (n/a)")
    print(f"discordam              : {disagree} ({disagree_pct:.1f}%)" if disagree_pct is not None
          else "discordam              : 0 (n/a)")
    print()
    # MFC21-04 (herdr-review rodada 21, mfc-rev): "win%" sozinho era
    # ambíguo — noite lucrativa (soma de todas as cestas) e cesta vencedora
    # (perna a perna) são taxas DIFERENTES; a versão anterior só reportava
    # a primeira com um nome que soa como a segunda. Reporta as duas.
    hdr = (f"{'motor':<14} {'noites c/ reconstrução':>22} {'cestas recon.':>13} {'noite%':>7} "
           f"{'cesta%':>7} {'bruto':>10} {'custo':>10} {'líquido':>10}")
    print(hdr)
    print("-" * len(hdr))
    for name in engine_names:
        s = stats[name]
        net = s["pnl"] - s["cost"]
        night_winrate = (s["wins"] / s["nights_with_baskets"] * 100) if s["nights_with_baskets"] else 0.0
        basket_winrate = (s["basket_wins"] / s["baskets"] * 100) if s["baskets"] else 0.0
        print(f"{name:<14} {s['nights_with_baskets']:>16} {s['baskets']:>8} "
              f"{night_winrate:>6.1f}% {basket_winrate:>6.1f}% "
              f"{s['pnl']:>10.2f} {s['cost']:>10.2f} {net:>10.2f}")

    print("\nqualidade da reconstrução:")
    for name in engine_names:
        s = stats[name]
        status = _quality_status(s)
        print(f"  {name:<14} status={status:<8} "
              f"degraded={s['degraded_baskets']} "
              f"swap_unmodeled={s['swap_unmodeled_baskets']} "
              f"missing_price={s['skipped_missing_price']}")

    print("\nPnL líquido por cesta (média +/- erro padrão):")
    for name in engine_names:
        mean, stderr = _mean_stderr(stats[name]["net_per_basket"])
        if mean is None:
            print(f"  {name:<14} n=0 (n/a)")
        elif stderr is None:
            print(f"  {name:<14} média={mean:>9.3f}  erro padrão=n/a  n=1")
        else:
            print(f"  {name:<14} média={mean:>9.3f}  erro padrão={stderr:>9.3f}  n={len(stats[name]['net_per_basket'])}")

    # Decomposição spread/swap (achado do árbitro efêmero, herdr-ask mfc-5):
    # spread e swap apontam pra alavancas DIFERENTES (número de pernas/par
    # vs. horário de saída/rollover) — sem separar, "reduzir custo" não diz
    # o que mudar.
    print(f"\ndecomposição do custo (spread vs swap, por cesta):")
    hdr2 = f"{'motor':<14} {'spread':>10} {'swap':>10} {'spread/cesta':>13} {'swap/cesta':>11} {'bruto/cesta':>12}"
    print(hdr2)
    print("-" * len(hdr2))
    for name in engine_names:
        s = stats[name]
        n = s["baskets"] or 1
        print(f"{name:<14} {s['spread']:>10.2f} {s['swap']:>10.2f} "
              f"{s['spread']/n:>13.3f} {s['swap']/n:>11.3f} {s['pnl']/n:>12.3f}")

    if runs > 1:
        print(f"\nfaixa de líquido observada em {runs} passadas (ruído do CostModel — tick ao vivo):")
        for name in engine_names:
            vals = net_by_run[name]
            print(f"  {name:<14} min={min(vals):>10.2f}  max={max(vals):>10.2f}  "
                  f"média={sum(vals)/len(vals):>10.2f}")

    print(f"\nsinais ativos (não cestas abertas; {nights_evaluated} noites possíveis):")
    print(f"{'moeda':<6} " + " ".join(f"{name:>10}" for name in engine_names))
    for c in CURRENCIES:
        print(f"{c:<6} " + " ".join(f"{active_signal_counts[name][c]:>10}" for name in engine_names))

    if disagreement_examples:
        print(f"\nexemplos de discordância (até 12, de {disagree} totais):")
        for date_str, ccy, per_engine in disagreement_examples:
            parts = ", ".join(f"{name}={bias}({score:.2f})" for name, (bias, score) in per_engine.items())
            print(f"  {date_str} {ccy:<4} {parts}")

    paired_mean, paired_stderr = _mean_stderr(paired_net_deltas)
    if "3tf_baseline" in engine_names and "5tf_port_a" in engine_names:
        print("\nDelta líquido pareado por noite (somente noites com cesta nos dois):")
        if paired_mean is None:
            print("  n=0 (n/a)")
        elif paired_stderr is None:
            print(f"  média={paired_mean:.3f}  erro padrão=n/a  n=1")
        else:
            print(f"  média={paired_mean:.3f}  erro padrão={paired_stderr:.3f} "
                  f"n={len(paired_net_deltas)}")

    if log_note is not None:
        baseline_name = "3tf_baseline" if "3tf_baseline" in engine_names else None
        is_port_a_compare = baseline_name is not None and "5tf_port_a" in engine_names
        comparison_to_baseline = {}
        if baseline_name:
            baseline_net = stats[baseline_name]["pnl"] - stats[baseline_name]["cost"]
            baseline_baskets = stats[baseline_name]["baskets"]
            baseline_per_basket = (
                baseline_net / baseline_baskets if baseline_baskets else None
            )
            for name in engine_names:
                net = stats[name]["pnl"] - stats[name]["cost"]
                baskets = stats[name]["baskets"]
                per_basket = net / baskets if baskets else None
                comparison_to_baseline[name] = {
                    "net_delta_vs_3tf_baseline": round(net - baseline_net, 2),
                    "net_delta_per_basket_vs_3tf_baseline": (
                        round(per_basket - baseline_per_basket, 3)
                        if per_basket is not None and baseline_per_basket is not None else None
                    ),
                }
        hypothesis = (
            "A matriz institucional 5-TF do Port A melhora a decisão líquida contra o baseline 3-TF pré-port."
            if is_port_a_compare else
            f"Comparação exploratória dos motores: {', '.join(engine_names)}."
        )
        implementation = (
            {
                "port": "A",
                "upstream_commit": "544d660",
                "local_engine": "5tf_port_a",
                "thresholds": {"stop_zone": 0.20, "equilibrium": 0.05},
            }
            if is_port_a_compare else
            {"engines": list(engine_names), "port_a_executed": False}
        )
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "script": "backtest_engine_compare.py",
            "days": days,
            "runs": runs,
            "nights_evaluated": nights_evaluated,
            "engines_compared": engine_names,
            "note": log_note,
            "hypothesis": hypothesis,
            "implementation": implementation,
            "baseline": baseline_name,
            "window": {
                "days": days,
                "start_brt": _brt_iso(window_start),
                "end_brt": _brt_iso(window_end),
                "sample_role": sample_role,
                "development_start_brt": (
                    _brt_iso(development_start) if development_start is not None else None
                ),
                "nights_evaluated": nights_evaluated,
                "mask": "same_closed_bar_and_valid_market_session_for_all_engines",
            },
            "coverage": coverage,
            "limitations": [
                "custo usa tick atual do MT5, não custo histórico da noite",
                "sem slippage, latência, requotes, fills parciais ou margem",
                "backtest usa barra fechada; produção pode usar barra em formação",
                (
                    "janela fixa e declarada OOS disjunta da janela de desenvolvimento; "
                    "isso não prova generalização estatística"
                    if sample_role == "oos_disjoint" else
                    "janela relativa, sem garantia de separação OOS"
                ),
                f"poder amostral limitado: {nights_evaluated} noites e "
                f"{sum(stats[name]['baskets'] for name in engine_names)} cestas agregadas",
            ],
            "quality": {
                "status": _overall_quality_status(
                    css_history_status, stats, engine_names
                ),
                "css_history": css_history_quality,
                "by_engine": {
                    name: {
                        "degraded_baskets": stats[name]["degraded_baskets"],
                        "swap_unmodeled_baskets": stats[name]["swap_unmodeled_baskets"],
                        "skipped_missing_price": stats[name]["skipped_missing_price"],
                    }
                    for name in engine_names
                },
            },
            "degraded_baskets": sum(stats[name]["degraded_baskets"] for name in engine_names),
            "skipped_missing_price": sum(stats[name]["skipped_missing_price"] for name in engine_names),
            "paired_net_delta_per_night": {
                "mean": round(paired_mean, 3) if paired_mean is not None else None,
                "stderr": round(paired_stderr, 3) if paired_stderr is not None else None,
                "n": len(paired_net_deltas),
                "definition": "Port A minus baseline on nights with reconstructed baskets in both",
            },
            "comparison_to_baseline": comparison_to_baseline,
            "parameters": {
                "lot": LOT,
                "runs": runs,
                "engines": list(engine_names),
                "window_end_brt": _brt_iso(window_end),
                "sample_role": sample_role,
                "development_start_brt": (
                    _brt_iso(development_start) if development_start is not None else None
                ),
            },
            "rates_source": "historical_h1_prices; live CostModel tick for spread/swap",
            "cost_snapshot": "live tick sampled per reconstructed basket",
            "data_snapshot_digest": data_digest,
            "producer_provenance": producer_provenance,
            "execution": producer_provenance["execution"],
            "agree_pct": round(agree / total_calls * 100, 1) if total_calls else None,
            "engines": {
                name: {
                    "baskets": stats[name]["baskets"],
                    "reconstructed_baskets": stats[name]["baskets"],
                    "active_signals": sum(active_signal_counts[name].values()),
                    "bruto": round(stats[name]["pnl"], 2),
                    "custo": round(stats[name]["cost"], 2),
                    "liquido": round(stats[name]["pnl"] - stats[name]["cost"], 2),
                    "noite_pct": round(
                        (stats[name]["wins"] / stats[name]["nights_with_baskets"] * 100)
                        if stats[name]["nights_with_baskets"] else 0.0, 1),
                    "cesta_pct": round(
                        (stats[name]["basket_wins"] / stats[name]["baskets"] * 100)
                        if stats[name]["baskets"] else 0.0, 1),
                    "net_per_basket_mean": round(_mean_stderr(stats[name]["net_per_basket"])[0], 3)
                    if _mean_stderr(stats[name]["net_per_basket"])[0] is not None else None,
                    "net_per_basket_stderr": round(_mean_stderr(stats[name]["net_per_basket"])[1], 3)
                    if _mean_stderr(stats[name]["net_per_basket"])[1] is not None else None,
                    "net_per_basket_n": len(stats[name]["net_per_basket"]),
                    "degraded_baskets": stats[name]["degraded_baskets"],
                    "swap_unmodeled_baskets": stats[name]["swap_unmodeled_baskets"],
                    "skipped_missing_price": stats[name]["skipped_missing_price"],
                    "quality_status": _quality_status(stats[name]),
                } for name in engine_names
            },
            "runs_summary": runs_summary,
        }
        producer_provenance["result_snapshot_digest"] = result_snapshot_digest(record)
        path = append_result(record)
        print(f"\n[+] Resultado registrado em {path}")
    return 0


def threshold_sweep(days=45, thresholds=VECTOR_THRESHOLDS, end_brt=None):
    """R2 (herdr-review rodada 21, mfc-rev-2): varre o limiar do motor
    `3tf_vector` (R1) e reporta cestas/noite, win rate e PnL pra cada valor
    — decide T com dado, não escolhendo um número e testando só ele. Não
    recomenda um valor sozinho; é insumo pra decisão do Breno."""
    if not ensure_mt5():
        print("[-] MT5 não conectado — abortando varredura; não usar dados degradados.")
        return 1
    check_contract_size_consistency()
    print(f"[*] Carregando séries canônicas (mesmo motor da dashboard)...")
    series = load_series()
    if not series:
        print("[-] Séries canônicas indisponíveis.")
        return 1
    print(f"[*] Carregando preços H1 de 28 pares...")
    prices = load_h1_prices()
    if not prices:
        print("[-] Preços H1 indisponíveis.")
        return 1
    print(f"[+] {len(prices)} pares com preço disponível.\n")

    print("=" * 90)
    print(f"  VARREDURA DE LIMIAR — motor 3tf_vector (R1), {days} dias")
    print("=" * 90)
    hdr = (f"{'T':>6} {'noites c/ reconstrução':>22} {'cestas recon.':>13} {'cestas/noite':>13} "
           f"{'noite%':>7} {'cesta%':>7} {'bruto':>10} {'custo':>10} {'líquido':>10}")
    print(hdr)
    print("-" * len(hdr))
    for t in thresholds:
        name = f"T{t}"
        original = ENGINES.get(name)
        ENGINES[name] = make_3tf_vector_engine(t)
        try:
            stats, agree, disagree, _, _, nights_evaluated, _, _coverage = _one_pass(
                series, prices, days, [name], end_brt=end_brt
            )
        finally:
            if original is None:
                del ENGINES[name]
            else:
                ENGINES[name] = original
        s = stats[name]
        net = s["pnl"] - s["cost"]
        night_winrate = (s["wins"] / s["nights_with_baskets"] * 100) if s["nights_with_baskets"] else 0.0
        basket_winrate = (s["basket_wins"] / s["baskets"] * 100) if s["baskets"] else 0.0
        cestas_noite = s["baskets"] / nights_evaluated if nights_evaluated else 0.0
        print(f"{t:>6} {s['nights_with_baskets']:>16} {s['baskets']:>8} {cestas_noite:>13.2f} "
              f"{night_winrate:>6.1f}% {basket_winrate:>6.1f}% "
              f"{s['pnl']:>10.2f} {s['cost']:>10.2f} {net:>10.2f}")
    return 0


if __name__ == "__main__":
    def _parse_iso_datetime(value):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(str(exc)) from exc
        if len(value) == 10:
            parsed = parsed.replace(hour=ENTRY_HOUR_BRT)
        return parsed

    if len(sys.argv) > 1 and sys.argv[1] == "sweep":
        parser = argparse.ArgumentParser(
            description="Varre limiares do motor 3tf_vector"
        )
        parser.add_argument("days", nargs="?", type=int, default=45)
        parser.add_argument("--end-brt", type=_parse_iso_datetime)
        args = parser.parse_args(sys.argv[2:])
        sys.exit(threshold_sweep(days=args.days, end_brt=args.end_brt))
    else:
        parser = argparse.ArgumentParser(description="Compara motores de confluência")
        parser.add_argument("days", nargs="?", type=int, default=45)
        parser.add_argument("runs", nargs="?", type=int, default=1)
        parser.add_argument("note", nargs="?")
        parser.add_argument("--end-brt", type=_parse_iso_datetime)
        parser.add_argument("--development-start-brt", type=_parse_iso_datetime)
        parser.add_argument(
            "--sample-role",
            choices=("exploratory", "oos_disjoint"),
            default="exploratory",
        )
        parser.add_argument(
            "--use-histdata-mn1-warmup",
            action="store_true",
            help=(
                "Estende o prefixo de aquecimento MN1 curto da Exness com o "
                "cache validado da HistData.com (data/histdata_mn1_warmup/, "
                "gerado por scripts/fetch_histdata_mn1_warmup.py) -- só o "
                "prefixo antigo necessário pro ATR(100) convergir, nunca as "
                "barras recentes/decisórias. Desligado por padrão."
            ),
        )
        args = parser.parse_args()
        sys.exit(compare(
            days=args.days,
            runs=args.runs,
            log_note=args.note,
            end_brt=args.end_brt,
            sample_role=args.sample_role,
            development_start_brt=args.development_start_brt,
            use_histdata_mn1_warmup=args.use_histdata_mn1_warmup,
        ))
