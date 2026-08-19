"""
AGENTE DE ANÁLISE MACRO INSTITUCIONAL (MN1, W1, D1)
Especialista em identificar fases cíclicas institucionais, zonas de parada (+/- 0.20),
permissões válidas e a Tríade Analítica (Região, Ciclo Atual, Ciclo Devendo, Score & Angulação).
"""

import numpy as np
from agents.triad_analyzer import analyze_tf_triad

def analyze_macro_currency(ccy, mn_series, w1_series, d1_series):
    mn_triad = analyze_tf_triad("MN1", mn_series)
    w1_triad = analyze_tf_triad("W1", w1_series)
    d1_triad = analyze_tf_triad("D1", d1_series)
    
    mn_curr = mn_triad["score"]
    w1_curr = w1_triad["score"]
    d1_curr = d1_triad["score"]
    
    mn_dir = "UP" if mn_triad["diff"] > 0 else "DN"
    w1_dir = "UP" if w1_triad["diff"] > 0 else "DN"
    d1_dir = "UP" if d1_triad["diff"] > 0 else "DN"
    
    # Classificação do Ciclo Macro na Linguagem Institucional
    macro_bias = "NEUTRO"
    cyclic_phase = d1_triad["current_cycle"]
    macro_power = 0.0
    
    if d1_dir == "UP":
        if d1_curr <= -0.15:
            cyclic_phase = "ZONA DE PARADA VERMELHA (DEVENDO CICLO DE FORÇA)"
            macro_bias = "COMPRA DE FUNDO (RUMO À LINHA VERDE)"
            macro_power = +2.0
        elif -0.15 < d1_curr <= 0.05:
            cyclic_phase = "ABERTO SOBRE O 0 (PERMISSÃO VÁLIDA PARA SUBIR)"
            macro_bias = "COMPRA EM DESENVOLVIMENTO (TESTE DO 0)"
            macro_power = +1.5
        elif d1_curr >= 0.20:
            cyclic_phase = "CUMPRIU CICLO DE ALTA (ALERTA DE TOPO / LINHA VERDE)"
            macro_bias = "EXAUSTÃO DE ALTA (DEVENDO CICLO DE QUEDA)"
            macro_power = -1.5
        else:
            cyclic_phase = "CICLO DE FORÇA EM ANDAMENTO (BUSCANDO +0.20)"
            macro_bias = "COMPRA ATIVA"
            macro_power = +1.0
    elif d1_dir == "DN":
        if d1_curr >= 0.15:
            cyclic_phase = "ZONA DE PARADA VERDE (DEVENDO CICLO DE FRAQUEZA)"
            macro_bias = "VENDA DE TOPO (RUMO À LINHA VERMELHA)"
            macro_power = -2.0
        elif -0.05 <= d1_curr < 0.15:
            cyclic_phase = "ABERTO SOBRE O 0 (PERMISSÃO VÁLIDA PARA CAIR)"
            macro_bias = "VENDA EM DESENVOLVIMENTO (TESTE DO 0)"
            macro_power = -1.5
        elif d1_curr <= -0.20:
            cyclic_phase = "CUMPRIU CICLO DE BAIXA (ALERTA DE FUNDO / LINHA VERMELHA)"
            macro_bias = "EXAUSTÃO DE BAIXA (DEVENDO CICLO DE ALTA)"
            macro_power = +1.5
        else:
            cyclic_phase = "CICLO DE FRAQUEZA EM ANDAMENTO (BUSCANDO -0.20)"
            macro_bias = "VENDA ATIVA"
            macro_power = -1.0
            
    # Pontuação Ponderada Macro
    weighted_macro_score = (mn_curr * 0.2) + (w1_curr * 0.3) + (d1_curr * 0.5)
    
    return {
        "ccy": ccy,
        "mn_score": mn_curr,
        "mn_dir": mn_dir,
        "w1_score": w1_curr,
        "w1_dir": w1_dir,
        "d1_score": d1_curr,
        "d1_dir": d1_dir,
        "macro_bias": macro_bias,
        "cyclic_phase": cyclic_phase,
        "macro_power": macro_power,
        "weighted_macro_score": weighted_macro_score,
        "mn_triad": mn_triad,
        "w1_triad": w1_triad,
        "d1_triad": d1_triad
    }
