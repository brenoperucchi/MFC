"""
MOTOR DE CONFLUÊNCIA MULTI-AGENTE CSS
Conecta os diagnósticos do Agente Macro (MN1, W1, D1) com o Agente Operacional (H4, H1)
para gerar o veredito unificado de confluência para cada moeda e para os 28 pares Forex.
"""

from agents.macro_analyzer import analyze_macro_currency
from agents.operational_analyzer import analyze_operational_currency

def evaluate_currency_confluence(ccy, mn_s, w1_s, d1_s, h4_s, h1_s):
    macro = analyze_macro_currency(ccy, mn_s, w1_s, d1_s)
    op = analyze_operational_currency(ccy, h4_s, h1_s, macro)
    
    m_power = macro["macro_power"]
    o_power = op["op_power"]
    
    confluence_state = "EQUILÍBRIO"
    final_verdict = "AGUARDAR ALINHAMENTO"
    trade_bias = "NEUTRO"
    score_total = (macro["weighted_macro_score"] * 0.6) + (op["weighted_op_score"] * 0.4)
    
    if m_power < 0 and o_power < 0:
        confluence_state = "CONFLUÊNCIA DE QUEDA (DEVENDO CICLO DE FRAQUEZA)"
        final_verdict = "VENDA FORTE (RUMO À LINHA VERMELHA -0.20)"
        trade_bias = "VENDA FORTE"
    elif m_power > 0 and o_power > 0:
        confluence_state = "CONFLUÊNCIA DE ALTA (DEVENDO CICLO DE FORÇA)"
        final_verdict = "COMPRA FORTE (RUMO À LINHA VERDE +0.20)"
        trade_bias = "COMPRA FORTE"
    elif o_power < 0:
        confluence_state = "OPERACIONAL EM QUEDA (CICLO DE FRAQUEZA H4/H1)"
        final_verdict = "VENDA OPERACIONAL (BUSCANDO LINHA VERMELHA)"
        trade_bias = "VENDA"
    elif o_power > 0:
        confluence_state = "OPERACIONAL EM ALTA (CICLO DE FORÇA H4/H1)"
        final_verdict = "COMPRA OPERACIONAL (BUSCANDO LINHA VERDE)"
        trade_bias = "COMPRA"
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
        "score_total": score_total
    }

def evaluate_28_pairs_confluence(all_pairs, ccy_results, tf_data):
    pair_rankings = []
    
    for pair in all_pairs:
        base = pair[:3]
        quote = pair[3:6]
        
        b_res = ccy_results[base]
        q_res = ccy_results[quote]
        
        b_power = (b_res["macro"]["macro_power"] * 0.6) + (b_res["operational"]["op_power"] * 0.4)
        q_power = (q_res["macro"]["macro_power"] * 0.6) + (q_res["operational"]["op_power"] * 0.4)
        
        # Potencial Cíclico do Par: Base Power - Quote Power
        # Se Base devendo alta (+2) e Cotada devendo queda (-2) -> cyclic_score = +2.0 (COMPRA)
        # Se Base devendo queda (-2) e Cotada devendo alta (+2) -> cyclic_score = -2.0 (VENDA)
        cyclic_score = (b_power - q_power) / 2.0
        
        diffs = {}
        for tf_name in ["MN1", "W1", "D1", "H4", "H1"]:
            b_v = tf_data[tf_name][0][base][-1]
            q_v = tf_data[tf_name][0][quote][-1]
            diffs[tf_name] = b_v - q_v
            
        macro_diff = (diffs["MN1"] * 0.2) + (diffs["W1"] * 0.3) + (diffs["D1"] * 0.5)
        op_diff = (diffs["H4"] * 0.5) + (diffs["H1"] * 0.5)
        
        b_bias = b_res["trade_bias"]
        q_bias = q_res["trade_bias"]
        
        rec = "NEUTRO / LATERAL (BOX)"
        conviction = "NEUTRA"
        thesis = "Forças equilibradas ou ambas no Box. Evitar operar."
        
        if cyclic_score >= 0.4:
            if "COMPRA" in b_bias and "VENDA" in q_bias:
                rec = "COMPRA FORTE (STRONG BUY)"
                conviction = "MÁXIMA (CONFLUÊNCIA DUPLA)"
                thesis = f"Base ({base}) {b_res['macro']['cyclic_phase']} vs Cotada ({quote}) {q_res['macro']['cyclic_phase']}."
            else:
                rec = "COMPRA (BUY)"
                conviction = "ALTA"
                thesis = f"Potencial cíclico de fluxo comprador para {base} frente a {quote}."
        elif cyclic_score <= -0.4:
            if "VENDA" in b_bias and "COMPRA" in q_bias:
                rec = "VENDA FORTE (STRONG SELL)"
                conviction = "MÁXIMA (CONFLUÊNCIA DUPLA)"
                thesis = f"Base ({base}) {b_res['macro']['cyclic_phase']} vs Cotada ({quote}) {q_res['macro']['cyclic_phase']}."
            else:
                rec = "VENDA (SELL)"
                conviction = "ALTA"
                thesis = f"Pressão cíclica vendedora em {base} frente ao {quote} (Exaustão de Topo/Fundo)."
        elif cyclic_score >= 0.15:
            rec = "COMPRA MODERADA"
            conviction = "MODERADA"
            thesis = f"Leve predominância compradora para {base}."
        elif cyclic_score <= -0.15:
            rec = "VENDA MODERADA"
            conviction = "MODERADA"
            thesis = f"Leve predominância vendedora em {base}."
            
        pair_rankings.append({
            "pair": pair,
            "base": base,
            "quote": quote,
            "total_score": cyclic_score,
            "macro_diff": macro_diff,
            "op_diff": op_diff,
            "diffs": diffs,
            "rec": rec,
            "conviction": conviction,
            "thesis": thesis
        })
        
    # Ordenar por maior convicção cíclica absoluta
    pair_rankings = sorted(pair_rankings, key=lambda x: abs(x["total_score"]), reverse=True)
    return pair_rankings
