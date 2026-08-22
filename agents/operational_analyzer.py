"""
AGENTE DE ANÁLISE OPERACIONAL (H4, H1)
Especialista em validação de timing, momentum intraday, acúmulos em zonas de parada,
retomadas de força/fraqueza no Box e a Tríade Analítica.
"""

from agents.triad_analyzer import analyze_tf_triad

def analyze_operational_currency(ccy, h4_series, h1_series, macro_info=None):
    h4_triad = analyze_tf_triad("H4", h4_series)
    h1_triad = analyze_tf_triad("H1", h1_series)
    
    h4_curr = h4_triad["score"]
    h1_curr = h1_triad["score"]
    
    h4_dir = "UP" if h4_triad["diff"] > 0 else "DN"
    h1_dir = "UP" if h1_triad["diff"] > 0 else "DN"
    
    op_status = "EQUILÍBRIO / CONSOLIDAÇÃO"
    timing_type = "AGUARDAR"
    op_power = 0.0
    
    # Classificação Operacional na Linguagem do Usuário
    if h1_triad["is_retomada_forca"]:
        op_status = "RETOMADA DE FORÇA NO BOX (CICLO INVÁLIDO DE BAIXA)"
        timing_type = "COMPRA DE DESCONTO NO BOX (GATILHO DE ALTA ATIVO)"
        op_power = +2.5
    elif h1_triad["is_retomada_fraqueza"]:
        op_status = "RETOMADA DE FRAQUEZA NO BOX (CICLO INVÁLIDO DE ALTA)"
        timing_type = "VENDA DE REPIQUE NO BOX (GATILHO DE BAIXA ATIVO)"
        op_power = -2.5
    elif h4_dir == "DN" and h1_dir == "DN":
        if h1_curr >= 0.15 or h4_curr >= 0.15:
            op_status = "ZONA DE PARADA VERDE (DEVENDO CICLO DE FRAQUEZA)"
            timing_type = "GATILHO DE VENDA ATIVO (RUMO À LINHA VERMELHA)"
            op_power = -2.0
        elif -0.05 <= h1_curr <= 0.15:
            op_status = "ABERTO SOBRE O 0 (PERMISSÃO VÁLIDA PARA CAIR)"
            timing_type = "VENDA EM ANDAMENTO (TESTE DO 0)"
            op_power = -1.5
        elif h1_curr <= -0.20:
            op_status = "EXTREMO INFERIOR / SOBREFRAQUEZA (DEVENDO EXAUSTÃO)"
            timing_type = "QUEDA EM ANDAMENTO (DEVENDO VIRADA DE ALTA)"
            op_power = -1.0
        else:
            op_status = "CICLO DE FRAQUEZA EM ANDAMENTO (BUSCANDO -0.20)"
            timing_type = "VENDA ATIVA"
            op_power = -1.5
    elif h4_dir == "UP" and h1_dir == "UP":
        if h1_curr <= -0.15 or h4_curr <= -0.15:
            op_status = "ZONA DE PARADA VERMELHA (DEVENDO CICLO DE FORÇA)"
            timing_type = "GATILHO DE COMPRA ATIVO (RUMO À LINHA VERDE)"
            op_power = +2.0
        elif -0.15 <= h1_curr <= 0.05:
            op_status = "ABERTO SOBRE O 0 (PERMISSÃO VÁLIDA PARA SUBIR)"
            timing_type = "COMPRA EM ANDAMENTO (TESTE DO 0)"
            op_power = +1.5
        elif h1_curr >= 0.20:
            op_status = "EXTREMO SUPERIOR / SOBREFORÇA (DEVENDO EXAUSTÃO)"
            timing_type = "ALTA EM ANDAMENTO (DEVENDO VIRADA DE BAIXA)"
            op_power = +1.0
        else:
            op_status = "CICLO DE FORÇA EM ANDAMENTO (BUSCANDO +0.20)"
            timing_type = "COMPRA ATIVA"
            op_power = +1.5
    elif h4_dir == "DN" and h1_dir == "UP":
        op_status = "H4 EM QUEDA COM REPIQUE DE H1 (ACÚMULO / TESTE DO 0)"
        timing_type = "AGUARDAR H1 VIRAR PARA BAIXA (VENDA)"
        op_power = -0.5
    elif h4_dir == "UP" and h1_dir == "DN":
        op_status = "H4 EM ALTA COM PULLBACK DE H1 (ACÚMULO / TESTE DO 0)"
        timing_type = "AGUARDAR H1 VIRAR PARA ALTA (COMPRA)"
        op_power = +0.5
        
    # Detecção Abrangente de Divergência entre Timeframes (Região, Ciclo Devendo, Diário, Mensal, H4/H1)
    divergence_alerts = []
    has_divergence = False
    
    if macro_info:
        mn_dir = macro_info.get("mn_dir", "UP")
        w1_dir = macro_info.get("w1_dir", "UP")
        d1_dir = macro_info.get("d1_dir", "UP")
        mn_score = macro_info.get("mn_score", 0.0)
        w1_score = macro_info.get("w1_score", 0.0)
        d1_score = macro_info.get("d1_score", 0.0)
        
        # 1. Divergência Estrutural de Região / Ciclo Devendo (ex: CHF no topo do Mensal vs fundo no restante)
        if mn_score >= 0.20 and (w1_score <= -0.20 or d1_score <= -0.20 or h4_curr <= -0.20):
            divergence_alerts.append(f"⚠️ DIVERGÊNCIA ESTRUTURAL: MN1 no Topo ({mn_score:+5.2f}) devendo ciclo de baixa, enquanto W1/D1/H4/H1 estão no Fundo devendo ciclo de alta")
        elif mn_score <= -0.20 and (w1_score >= 0.20 or d1_score >= 0.20 or h4_curr >= 0.20):
            divergence_alerts.append(f"⚠️ DIVERGÊNCIA ESTRUTURAL: MN1 no Fundo ({mn_score:+5.2f}) devendo ciclo de alta, enquanto W1/D1/H4/H1 estão no Topo devendo ciclo de baixa")
        
        # 2. Divergência do Diário (D1 em contra-fluxo com W1, H4 ou H1)
        if d1_dir == "UP" and (w1_dir == "DN" or h4_dir == "DN" or h1_dir == "DN"):
            if w1_dir == "DN" and (h4_dir == "DN" or h1_dir == "DN"):
                divergence_alerts.append("⚠️ DIVERGÊNCIA NO DIÁRIO: D1 inclinado para força (▲), em contra-fluxo ao Semanal e Operacional em fraqueza (▼)")
            elif h4_dir == "DN" and h1_dir == "DN":
                divergence_alerts.append("⚠️ DIVERGÊNCIA NO DIÁRIO: D1 inclinado para força (▲), mas H4 e H1 em queda devendo ciclo de fraqueza (▼)")
        elif d1_dir == "DN" and (w1_dir == "UP" or h4_dir == "UP" or h1_dir == "UP"):
            if w1_dir == "UP" and (h4_dir == "UP" or h1_dir == "UP"):
                divergence_alerts.append("⚠️ DIVERGÊNCIA NO DIÁRIO: D1 inclinado para fraqueza (▼), em contra-fluxo ao Semanal e Operacional em força (▲)")
            elif h4_dir == "UP" and h1_dir == "UP":
                divergence_alerts.append("⚠️ DIVERGÊNCIA NO DIÁRIO: D1 inclinado para fraqueza (▼), mas H4 e H1 em alta devendo ciclo de força (▲)")
                
        # 3. Divergência de Inclinação do Mensal (MN1 oposto ao H4 e H1)
        if mn_dir == "UP" and h4_dir == "DN" and h1_dir == "DN":
            if not any("MENSAL" in a or "ESTRUTURAL" in a for a in divergence_alerts):
                divergence_alerts.append("⚠️ DIVERGÊNCIA NO MENSAL: MN1 inclinado para força (▲), mas H4 e H1 em queda devendo ciclo de fraqueza (▼)")
        elif mn_dir == "DN" and h4_dir == "UP" and h1_dir == "UP":
            if not any("MENSAL" in a or "ESTRUTURAL" in a for a in divergence_alerts):
                divergence_alerts.append("⚠️ DIVERGÊNCIA NO MENSAL: MN1 inclinado para fraqueza (▼), mas H4 e H1 em alta devendo ciclo de força (▲)")
                
    has_divergence = len(divergence_alerts) > 0
    divergence_alert = "<br>".join(divergence_alerts) if has_divergence else "NENHUMA (TIMEFRAMES ALINHADOS)"
            
    weighted_op_score = (h4_curr * 0.5) + (h1_curr * 0.5)
    
    return {
        "ccy": ccy,
        "h4_score": h4_curr,
        "h4_dir": h4_dir,
        "h1_score": h1_curr,
        "h1_dir": h1_dir,
        "op_status": op_status,
        "timing_type": timing_type,
        "op_power": op_power,
        "weighted_op_score": weighted_op_score,
        "has_divergence": has_divergence,
        "divergence_alert": divergence_alert,
        "h4_triad": h4_triad,
        "h1_triad": h1_triad
    }
