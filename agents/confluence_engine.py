"""
MOTOR DE CONFLUÊNCIA MULTI-AGENTE CSS — OPERAÇÕES ALICATE 3-TF (D1, H4, H1)
Especialista em operações curtas e intraday baseadas no Fechamento de Alicate (Scissor / Pincer Convergence).
Hierarquia de pesos 3-TF:
  - D1 (40%): Contexto Direcional e Permissão do Dia
  - H4 (35%): Estrutura e Momentum da Sessão
  - H1 (25%): Gatilho Imediato e Ponto de Ignição
"""

from agents.macro_analyzer import analyze_macro_currency
from agents.operational_analyzer import analyze_operational_currency
from agents.triad_analyzer import analyze_tf_triad


def detect_tf_alicate(b_score, b_diff, q_score, q_diff):
    """
    Detecta se há Fechamento de Alicate (Scissor Convergence) entre a Moeda Base e a Moeda Cotada no timeframe.
    
    Alicate de COMPRA (BUY):
      - Base no Fundo / Acumulação (score <= -0.08 e diff >= 0.002 -> efeito foguete 🚀)
      - Cotada no Topo / Distribuição (score >= +0.08 e diff <= -0.002 -> efeito montanha-russa 🎢)
      
    Alicate de VENDA (SELL):
      - Base no Topo / Distribuição (score >= +0.08 e diff <= -0.002 -> efeito montanha-russa 🎢)
      - Cotada no Fundo / Acumulação (score <= -0.08 e diff >= 0.002 -> efeito foguete 🚀)
    """
    # BUY ALICATE
    if (b_score <= -0.08 and b_diff >= 0.002) and (q_score >= 0.08 and q_diff <= -0.002):
        spread = q_score - b_score
        return {
            "type": "BUY",
            "spread": round(spread, 3),
            "b_score": round(b_score, 3),
            "b_diff": round(b_diff, 4),
            "q_score": round(q_score, 3),
            "q_diff": round(q_diff, 4)
        }
        
    # SELL ALICATE
    if (b_score >= 0.08 and b_diff <= -0.002) and (q_score <= -0.08 and q_diff >= 0.002):
        spread = b_score - q_score
        return {
            "type": "SELL",
            "spread": round(spread, 3),
            "b_score": round(b_score, 3),
            "b_diff": round(b_diff, 4),
            "q_score": round(q_score, 3),
            "q_diff": round(q_diff, 4)
        }
        
    return None


def evaluate_currency_confluence(ccy, mn_s, w1_s, d1_s, h4_s, h1_s):
    macro = analyze_macro_currency(ccy, mn_s, w1_s, d1_s)
    op = analyze_operational_currency(ccy, h4_s, h1_s, macro)
    
    d1_curr = float(d1_s[-1]) if len(d1_s) > 0 else 0.0
    h4_curr = float(h4_s[-1]) if len(h4_s) > 0 else 0.0
    h1_curr = float(h1_s[-1]) if len(h1_s) > 0 else 0.0
    
    # Pontuação consolidada 3-TF: D1 (40%) + H4 (35%) + H1 (25%)
    score_3tf = (d1_curr * 0.40) + (h4_curr * 0.35) + (h1_curr * 0.25)
    
    d1_power = macro["d1_triad"].get("diff", 0.0)
    h4_power = op["h4_triad"].get("diff", 0.0)
    h1_power = op["h1_triad"].get("diff", 0.0)
    
    confluence_state = "EQUILÍBRIO"
    final_verdict = "AGUARDAR ALINHAMENTO"
    trade_bias = "NEUTRO"
    
    if score_3tf >= 0.10:
        confluence_state = "CONFLUÊNCIA DE ALTA (FLUXO COMPRADOR 3-TF)"
        final_verdict = "COMPRA (BUSCANDO TOPO DO BOX)"
        trade_bias = "COMPRA"
    elif score_3tf <= -0.10:
        confluence_state = "CONFLUÊNCIA DE QUEDA (FLUXO VENDEDOR 3-TF)"
        final_verdict = "VENDA (BUSCANDO FUNDO DO BOX)"
        trade_bias = "VENDA"
    else:
        confluence_state = "BOX DE EQUILÍBRIO (TESTE DO 0)"
        final_verdict = "AGUARDAR DEFINIÇÃO"
        trade_bias = "NEUTRO"
        
    return {
        "ccy": ccy,
        "macro": macro,
        "operational": op,
        "confluence_state": confluence_state,
        "final_verdict": final_verdict,
        "trade_bias": trade_bias,
        "has_divergence": op["has_divergence"],
        "divergence_alert": op["divergence_alert"],
        "score_total": round(score_3tf, 2)
    }


def _get_val_and_diff(series):
    if len(series) < 2:
        v = float(series[-1]) if len(series) > 0 else 0.0
        return v, 0.0
    return float(series[-1]), round(float(series[-1]) - float(series[-2]), 4)


def evaluate_28_pairs_confluence(all_pairs, ccy_results, tf_data):
    pair_rankings = []
    
    for pair in all_pairs:
        base = pair[:3]
        quote = pair[3:6]
        
        b_res = ccy_results[base]
        q_res = ccy_results[quote]
        
        # 1. Extrair valores e derivadas para os 3 timeframes intraday (D1, H4, H1)
        b_d1_v, b_d1_d = _get_val_and_diff(tf_data["D1"][0][base])
        b_h4_v, b_h4_d = _get_val_and_diff(tf_data["H4"][0][base])
        b_h1_v, b_h1_d = _get_val_and_diff(tf_data["H1"][0][base])
        
        q_d1_v, q_d1_d = _get_val_and_diff(tf_data["D1"][0][quote])
        q_h4_v, q_h4_d = _get_val_and_diff(tf_data["H4"][0][quote])
        q_h1_v, q_h1_d = _get_val_and_diff(tf_data["H1"][0][quote])
        
        # 2. Detecção de Fechamento de Alicate nos 3 Timeframes (D1, H4, H1)
        al_d1 = detect_tf_alicate(b_d1_v, b_d1_d, q_d1_v, q_d1_d)
        al_h4 = detect_tf_alicate(b_h4_v, b_h4_d, q_h4_v, q_h4_d)
        al_h1 = detect_tf_alicate(b_h1_v, b_h1_d, q_h1_v, q_h1_d)
        
        al_map = {"D1": al_d1, "H4": al_h4, "H1": al_h1}
        buy_alicate_tfs = [tf for tf, al in al_map.items() if al and al["type"] == "BUY"]
        sell_alicate_tfs = [tf for tf, al in al_map.items() if al and al["type"] == "SELL"]
        
        # 3. Diferenciais numéricos 3-TF
        diffs = {
            "D1": round(b_d1_v - q_d1_v, 3),
            "H4": round(b_h4_v - q_h4_v, 3),
            "H1": round(b_h1_v - q_h1_v, 3),
        }
        macro_diff = round(diffs["D1"], 2) # D1 é a bússola direcional do dia
        op_diff = round((diffs["H4"] * 0.55) + (diffs["H1"] * 0.45), 2)
        
        # 4. Cálculo do Total Score 3-TF Ponderado: D1 (40%) + H4 (35%) + H1 (25%)
        b_power_3tf = (b_d1_v * 0.40) + (b_h4_v * 0.35) + (b_h1_v * 0.25)
        q_power_3tf = (q_d1_v * 0.40) + (q_h4_v * 0.35) + (q_h1_v * 0.25)
        cyclic_score = (b_power_3tf - q_power_3tf) * 2.5 # Normalização de escala
        
        # Momentum e suporte de direção
        h4_buy_support = (b_h4_d > 0 and q_h4_d <= 0) or (diffs["H4"] > 0 and b_h4_d >= 0)
        h4_sell_support = (b_h4_d < 0 and q_h4_d >= 0) or (diffs["H4"] < 0 and b_h4_d <= 0)
        h1_buy_support = (b_h1_d > 0 and q_h1_d <= 0) or (diffs["H1"] > 0 and b_h1_d >= 0)
        h1_sell_support = (b_h1_d < 0 and q_h1_d >= 0) or (diffs["H1"] < 0 and b_h1_d <= 0)
        
        # Verificação de exaustão do Diário (+/- 0.18)
        b_d1_exhausted_top = (b_d1_v >= 0.18 and b_d1_d <= 0)
        b_d1_exhausted_bottom = (b_d1_v <= -0.18 and b_d1_d >= 0)
        q_d1_exhausted_top = (q_d1_v >= 0.18 and q_d1_d <= 0)
        q_d1_exhausted_bottom = (q_d1_v <= -0.18 and q_d1_d >= 0)
        
        rec = "NEUTRO / LATERAL (BOX)"
        conviction = "NEUTRA"
        badge_type = "NEUTRAL"
        is_alicate = False
        alicate_status = "NONE"
        thesis = "Forças equilibradas no intraday (D1, H4, H1). Aguardar definição."
        
        # =========================================================================
        # 1. HIERARQUIA 1: FECHAMENTO DE ALICATE DE COMPRA (BUY PINCER 3-TF)
        # =========================================================================
        if len(buy_alicate_tfs) > 0:
            is_alicate = True
            has_d1_alicate = "D1" in buy_alicate_tfs
            has_h4_alicate = "H4" in buy_alicate_tfs
            has_h1_alicate = "H1" in buy_alicate_tfs
            
            has_buy_dislocation = (
                b_d1_exhausted_top or
                (q_h4_v >= 0.30 and q_h4_d > 0.02) or
                (abs(q_h4_v - q_h1_v) >= 0.65)
            )

            # Caso 1.1: ALICATE TRIPLO / TOTAL (D1 + H4 + H1)
            if len(buy_alicate_tfs) >= 2 and (h4_buy_support and h1_buy_support) and not has_buy_dislocation:
                rec = "COMPRA FORTE (ALICATE TRIPLO 3-TF)"
                conviction = "🔥 MÁXIMA (ALICATE 3-TF SINCRONIZADO)"
                badge_type = "ALICATE_SYNC"
                alicate_status = "SYNC"
                cyclic_score = max(cyclic_score + 0.90, 2.10)
                thesis = f"✂️ ALICATE TRIPLO: Base ({base}) em Arrancada de Fundo (Foguete 🚀) vs Cotada ({quote}) em Queda de Topo (Montanha-Russa 🎢) sincronizado em {', '.join(buy_alicate_tfs)}."

            # Caso 1.2: ALICATE OPERACIONAL INTRADAY (H4 + H1)
            elif (has_h4_alicate or has_h1_alicate) and (h4_buy_support or h1_buy_support) and not has_buy_dislocation:
                rec = "COMPRA OPERACIONAL (ALICATE H4/H1)"
                conviction = "ALTA (ALICATE INTRADAY)"
                badge_type = "ALICATE_OP"
                alicate_status = "OP"
                cyclic_score = max(cyclic_score + 0.50, 1.65)
                thesis = f"⚡ ALICATE OPERACIONAL: Arrancada de extremos confirmada em {', '.join(buy_alicate_tfs)} ({base} saindo do fundo e {quote} caindo do topo)."

            # Caso 1.3: ALICATE D1 EM ESPERA (Aguardando gatilho de H1)
            elif has_d1_alicate:
                rec = "ALICATE D1 (AGUARDAR H1)"
                conviction = "DIÁRIO EM TRANSIÇÃO (AGUARDAR GATILHO)"
                badge_type = "ALICATE_WAIT"
                alicate_status = "WAIT_OP"
                cyclic_score = 0.85
                thesis = f"✂️ ALICATE D1 EM ESPERA: Fechamento de alicate ativo no Diário ({base} fundo vs {quote} topo), mas H1/H4 em correção. Aguardar virada de H1 para entrada ideal."
            
            else:
                rec = "COMPRA MODERADA (ALICATE)"
                conviction = "MODERADA"
                badge_type = "ALICATE_OP"
                alicate_status = "OP"
                cyclic_score = max(cyclic_score, 1.20)
                thesis = f"⚡ ALICATE INTRADAY: Alinhamento em {', '.join(buy_alicate_tfs)}."

        # =========================================================================
        # 2. HIERARQUIA 2: FECHAMENTO DE ALICATE DE VENDA (SELL PINCER 3-TF)
        # =========================================================================
        elif len(sell_alicate_tfs) > 0:
            is_alicate = True
            has_d1_alicate = "D1" in sell_alicate_tfs
            has_h4_alicate = "H4" in sell_alicate_tfs
            has_h1_alicate = "H1" in sell_alicate_tfs
            
            has_sell_dislocation = (
                b_d1_exhausted_bottom or
                (b_h4_v >= 0.30 and b_h4_d > 0.02) or
                (abs(b_h4_v - b_h1_v) >= 0.65)
            )

            # Caso 2.1: ALICATE TRIPLO / TOTAL (D1 + H4 + H1)
            if len(sell_alicate_tfs) >= 2 and (h4_sell_support and h1_sell_support) and not has_sell_dislocation:
                rec = "VENDA FORTE (ALICATE TRIPLO 3-TF)"
                conviction = "🔥 MÁXIMA (ALICATE 3-TF SINCRONIZADO)"
                badge_type = "ALICATE_SYNC"
                alicate_status = "SYNC"
                cyclic_score = min(cyclic_score - 0.90, -2.10)
                thesis = f"✂️ ALICATE TRIPLO: Base ({base}) em Queda de Topo (Montanha-Russa 🎢) vs Cotada ({quote}) em Arrancada de Fundo (Foguete 🚀) sincronizado em {', '.join(sell_alicate_tfs)}."

            # Caso 2.2: ALICATE OPERACIONAL INTRADAY (H4 + H1)
            elif (has_h4_alicate or has_h1_alicate) and (h4_sell_support or h1_sell_support) and not has_sell_dislocation:
                rec = "VENDA OPERACIONAL (ALICATE H4/H1)"
                conviction = "ALTA (ALICATE INTRADAY)"
                badge_type = "ALICATE_OP"
                alicate_status = "OP"
                cyclic_score = min(cyclic_score - 0.50, -1.65)
                thesis = f"⚡ ALICATE OPERACIONAL: Arrancada de extremos confirmada em {', '.join(sell_alicate_tfs)} ({base} caindo do topo e {quote} saindo do fundo)."

            # Caso 2.3: ALICATE D1 EM ESPERA
            elif has_d1_alicate:
                rec = "ALICATE D1 (AGUARDAR H1)"
                conviction = "DIÁRIO EM TRANSIÇÃO (AGUARDAR GATILHO)"
                badge_type = "ALICATE_WAIT"
                alicate_status = "WAIT_OP"
                cyclic_score = -0.85
                thesis = f"✂️ ALICATE D1 EM ESPERA: Fechamento de alicate ativo no Diário ({base} topo vs {quote} fundo), mas H1/H4 em correção. Aguardar virada de H1 para entrada ideal."
            
            else:
                rec = "VENDA MODERADA (ALICATE)"
                conviction = "MODERADA"
                badge_type = "ALICATE_OP"
                alicate_status = "OP"
                cyclic_score = min(cyclic_score, -1.20)
                thesis = f"⚡ ALICATE INTRADAY: Alinhamento em {', '.join(sell_alicate_tfs)}."

        # =========================================================================
        # 3. CASOS PADRÃO (SEM ALICATE) — FLUXO INTRADAY 3-TF
        # =========================================================================
        else:
            if cyclic_score >= 0.40:
                if b_d1_exhausted_top:
                    rec = "COMPRA MODERADA (EXAUSTÃO D1)"
                    conviction = "MODERADA"
                    badge_type = "BUY"
                    thesis = f"Vantagem compradora para {base}, porém Base em zona de chegada (+0.20) no Diário devendo exaustão."
                elif (b_h4_v > q_h4_v and b_h1_v > q_h1_v) and (b_d1_v > q_d1_v):
                    rec = "COMPRA FORTE (STRONG BUY)"
                    conviction = "ALTA (ALINHAMENTO 3-TF)"
                    badge_type = "STRONG_BUY"
                    thesis = f"Alinhamento positivo nos 3 horizontes: D1 ({macro_diff:+.2f}), H4 e H1 ({op_diff:+.2f})."
                else:
                    rec = "COMPRA (BUY)"
                    conviction = "ALTA"
                    badge_type = "BUY"
                    thesis = f"Potencial comprador para {base} frente a {quote} no intraday."
            
            elif cyclic_score <= -0.40:
                if b_d1_exhausted_bottom:
                    rec = "VENDA MODERADA (EXAUSTÃO D1)"
                    conviction = "MODERADA"
                    badge_type = "SELL"
                    thesis = f"Pressão vendedora em {base}, porém Base em zona de parada (-0.20) no Diário devendo exaustão."
                elif (b_h4_v < q_h4_v and b_h1_v < q_h1_v) and (b_d1_v < q_d1_v):
                    rec = "VENDA FORTE (STRONG SELL)"
                    conviction = "ALTA (ALINHAMENTO 3-TF)"
                    badge_type = "STRONG_SELL"
                    thesis = f"Alinhamento vendedor nos 3 horizontes: D1 ({macro_diff:+.2f}), H4 e H1 ({op_diff:+.2f})."
                else:
                    rec = "VENDA (SELL)"
                    conviction = "ALTA"
                    badge_type = "SELL"
                    thesis = f"Pressão vendedora para {base} frente a {quote} no intraday."
            
            elif cyclic_score >= 0.15:
                rec = "COMPRA MODERADA"
                conviction = "MODERADA"
                badge_type = "BUY"
                thesis = f"Leve predominância compradora para {base} no intraday."
            elif cyclic_score <= -0.15:
                rec = "VENDA MODERADA"
                conviction = "MODERADA"
                badge_type = "SELL"
                thesis = f"Leve predominância vendedora em {base} no intraday."

        pair_rankings.append({
            "pair": pair,
            "base": base,
            "quote": quote,
            "total_score": round(cyclic_score, 2),
            "macro_diff": macro_diff,
            "op_diff": op_diff,
            "diffs": diffs,
            "rec": rec,
            "badge_type": badge_type,
            "conviction": conviction,
            "is_alicate": is_alicate,
            "alicate_status": alicate_status,
            "alicate_tfs": buy_alicate_tfs if len(buy_alicate_tfs) > 0 else sell_alicate_tfs,
            "thesis": thesis
        })
        
    # Ordenar por maior pontuação absoluta (Total Score 3-TF)
    pair_rankings = sorted(pair_rankings, key=lambda x: abs(x["total_score"]), reverse=True)
    return pair_rankings
