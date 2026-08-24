"""
FERRAMENTA DE ANÁLISE, NÃO PARTE DO PIPELINE DE PRODUÇÃO.

Compara "abrir todas as moedas que qualificam" (comportamento atual) contra
3 regras candidatas de "escolher uma moeda só" — propostas por 3 lentes de
revisão independentes (Opus, Fable, codex/Sol) em resposta à divergência
achada entre o que o Miquéias descreveu ("escolhe uma moeda") e o que o
código faz (abre cesta pra toda moeda qualificada).

Reusa web/history_tracker.py::run_full_backtest() tal como está — mesmo
motor de score próprio dele (LWMA21 + ATR·SMA20, diferente do canônico
ATR·SMA100), sem re-simular PnL: cada regra só RE-SELECIONA, entre as
moedas que já qualificaram numa sessão, qual teria sido escolhida — e olha
o PnL que essa moeda específica já teve naquela noite (session["portfolios"]).

Nenhuma das 3 regras foi validada pelo Miquéias. Isto é evidência pra levar
a ele, não uma decisão já tomada.
"""

import os
import sys

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# NOTA: web/history_tracker.py::history_engine aponta pra real_audit_engine
# (web/real_portfolio_audit.py) — audita deals REAIS já executados, não
# reconstrói histórico sintético de preço. TrackRecordEngine (com
# run_full_backtest) não é instanciada em lugar nenhum do repositório hoje;
# funciona standalone contra preço histórico real independente disso.
from web.history_tracker import TrackRecordEngine, GMT_OFFSET, ENTRY_SERVER_HOUR

history_engine = TrackRecordEngine()


def _sign(bias):
    return 1 if bias == "BUY" else -1


def _canonical_total_score(scores):
    """Mesma fórmula do motor AO VIVO (agents/confluence_engine.py), pra ter
    um "total_score" comparável mesmo não sendo o que qualificou aqui."""
    return scores["D1"] * 0.40 + scores["H4"] * 0.35 + scores["H1"] * 0.25


def rule_opus(candidates):
    """Portão do Diário + portão de extremo no H4 + portão de dissenso (≤1
    entre MN1/W1/H4/H1), rankeado por 60% consenso + 40% espaço até a zona
    de parada do H4, -15% se tiver divergência aproximada.

    Substituição declarada: o desempate original pedia triads[tf]["diff"]
    (aceleração), que este motor de backtest não calcula — usa o próprio
    score como proxy de "quanto falta" no desempate."""
    survivors = []
    for c in candidates:
        s = _sign(c["bias"])
        sc = c["scores"]
        if s * sc["D1"] <= 0.05:
            continue
        if s * sc["H4"] >= 0.50:
            continue
        others = ("MN1", "W1", "H4", "H1")
        dissent = sum(1 for tf in others if s * sc[tf] < -0.05)
        if dissent > 1:
            continue
        votes = sum(1 for tf in others if s * sc[tf] > 0.05)
        room = min((0.20 - s * sc["H4"]) / 0.40, 1.0)
        score = 0.60 * (votes / 4.0) + 0.40 * room
        if c.get("has_divergence_approx"):
            score -= 0.15
        survivors.append((score, c))
    if not survivors:
        return None
    survivors.sort(key=lambda x: (
        x[0],
        min((0.20 - _sign(x[1]["bias"]) * x[1]["scores"]["H1"]) / 0.40, 1.0),
        _sign(x[1]["bias"]) * x[1]["scores"]["D1"],
    ), reverse=True)
    return survivors[0][1]


def rule_fable(candidates):
    """LED do D1 alinhado como filtro; contagem de LEDs alinhados nos 5 TFs
    como métrica principal (mínimo 4/5 pra operar); sem-divergência e score
    ponderado 5-TF (pesos MN1 .12/W1 .18/D1 .30/H4 .20/H1 .20) como
    desempates."""
    led_alvo = {"BUY": "green", "SELL": "red"}
    W = {"MN1": .12, "W1": .18, "D1": .30, "H4": .20, "H1": .20}
    eligible = []
    for c in candidates:
        alvo = led_alvo[c["bias"]]
        if c["leds"].get("D1") != alvo:
            continue
        align5 = sum(1 for tf in W if c["leds"].get(tf) == alvo)
        if align5 < 4:
            continue
        s = _sign(c["bias"])
        wscore = s * sum(W[tf] * c["scores"][tf] for tf in W)
        eligible.append(((align5, not c.get("has_divergence_approx"), wscore), c))
    if not eligible:
        return None
    eligible.sort(key=lambda x: x[0], reverse=True)
    return eligible[0][1]


def rule_codex(candidates):
    """Índice de Consenso Prospectivo: 40% D1 + 15% cada um dos outros 4,
    usando direção discreta (+1/0/-1) por TF em vez do score contínuo.

    Substituição declarada: o "p_tf" original vinha do ciclo devendo/
    retomada da Tríade Analítica (region_type/ciclo), que este motor de
    backtest não calcula — usa o sinal do score bruto (mesmo limiar ±0.05
    do LED) como direção prospectiva aproximada."""
    W = {"D1": .40, "MN1": .15, "W1": .15, "H4": .15, "H1": .15}
    scored = []
    for c in candidates:
        s = _sign(c["bias"])
        sc = c["scores"]
        icp = 0.0
        for tf, w in W.items():
            p_tf = 1 if sc[tf] > 0.05 else (-1 if sc[tf] < -0.05 else 0)
            icp += w * (s * p_tf)
        a_d1 = s * (1 if sc["D1"] > 0.05 else (-1 if sc["D1"] < -0.05 else 0))
        total = s * _canonical_total_score(sc)
        scored.append(((icp, a_d1, not c.get("has_divergence_approx"), total, c["symbol"]), c))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    winner_key, winner = scored[0]
    if winner_key[0] <= 0:
        return None  # ICP <=0 não é consenso a favor, não deveria operar
    return winner


RULES = {
    "abrir_todas": None,  # tratado à parte — soma tudo, é o baseline atual
    "opus": rule_opus,
    "fable": rule_fable,
    "codex": rule_codex,
}


def compare(days=45):
    print(f"[*] Rodando run_full_backtest(days={days})...")
    print(f"[*] GMT_OFFSET={GMT_OFFSET} | ENTRY_SERVER_HOUR={ENTRY_SERVER_HOUR}")
    data = history_engine.run_full_backtest(days=days)
    sessions = data.get("sessions", data) if isinstance(data, dict) else data
    if not sessions:
        print("[-] Nenhuma sessão reconstruída — MT5 indisponível ou histórico curto demais.")
        return

    stats = {name: {"pnl": 0.0, "trades": 0, "wins": 0, "nights": 0} for name in RULES}

    for sess in sessions:
        qualified = sess.get("qualified_full", [])
        portfolios_by_ccy = {p["currency"]: p for p in sess.get("portfolios", [])}
        night_had_candidates = bool(qualified)

        for name in RULES:
            stats[name]["nights"] += 1

        # baseline: exatamente o que o sistema faria hoje (abrir todas)
        stats["abrir_todas"]["pnl"] += sess.get("total_pnl_usd", 0.0)
        if qualified:
            stats["abrir_todas"]["trades"] += 1
            if sess.get("total_pnl_usd", 0.0) >= 0:
                stats["abrir_todas"]["wins"] += 1

        if not night_had_candidates:
            continue

        for name, fn in RULES.items():
            if fn is None:
                continue
            winner = fn(qualified)
            if winner is None:
                continue
            port = portfolios_by_ccy.get(winner["symbol"])
            pnl = port["pnl_usd"] if port else 0.0
            stats[name]["pnl"] += pnl
            stats[name]["trades"] += 1
            if pnl >= 0:
                stats[name]["wins"] += 1

    print()
    hdr = f"{'regra':<14} {'noites':>7} {'operou':>7} {'wins':>6} {'win%':>7} {'pnl_total':>11} {'pnl/trade':>10}"
    print(hdr)
    print("-" * len(hdr))
    for name, s in stats.items():
        winrate = (s["wins"] / s["trades"] * 100) if s["trades"] else 0.0
        avg = (s["pnl"] / s["trades"]) if s["trades"] else 0.0
        print(f"{name:<14} {s['nights']:>7} {s['trades']:>7} {s['wins']:>6} "
              f"{winrate:>6.1f}% {s['pnl']:>11.2f} {avg:>10.3f}")


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 45
    compare(days=days)
