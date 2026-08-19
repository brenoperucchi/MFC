"""
MÓDULO DE ANÁLISE EM TRÍADE INSTITUCIONAL (CSS)
Executa a análise rigorosa dos 4 Pilares por Timeframe:
1. Região (Box, Zonas de Parada +/-0.20, Linha de Equilíbrio 0.00, Extremos)
2. Ciclo Atual (Alta, Baixa, Lateralizado, Retomada de Força/Fraqueza / Ciclo Inválido)
3. Ciclo Devendo (Devendo Fraqueza até -0.20, Devendo Força até +0.20, Expansão)
4. Score & Angulação (Valor numérico exato e intensidade: Foguete, Montanha-Russa, Moderado, Sutil)
"""

import numpy as np

def analyze_tf_triad(tf_name, series):
    """
    Analisa uma série temporal de CSS sob os 4 pilares institucionais.
    """
    if len(series) < 2:
        val_curr = float(series[-1]) if len(series) > 0 else 0.0
        val_prev = val_curr
    else:
        val_curr = float(series[-1])
        val_prev = float(series[-2])
        
    diff = val_curr - val_prev
    abs_diff = abs(diff)
    
    # 1. REGIÃO
    if val_curr >= 0.50:
        region = f"Extremo Superior (+{val_curr:.2f} > +0.50)"
        region_type = "EXTREMO_SUPERIOR"
    elif val_curr >= 0.20:
        region = f"Zona de Parada Verde (+{val_curr:.2f} >= +0.20)"
        region_type = "ZONA_PARADA_VERDE"
    elif 0.05 < val_curr < 0.20:
        region = f"Dentro do Box Superior (+{val_curr:.2f} entre 0.00 e +0.20)"
        region_type = "BOX_SUPERIOR"
    elif -0.05 <= val_curr <= 0.05:
        region = f"Linha de Equilíbrio 0.00 ({val_curr:+.2f})"
        region_type = "EQUILIBRIO_0"
    elif -0.20 < val_curr < -0.05:
        region = f"Dentro do Box Inferior ({val_curr:.2f} entre -0.20 e 0.00)"
        region_type = "BOX_INFERIOR"
    elif val_curr <= -0.50:
        region = f"Extremo Inferior ({val_curr:.2f} <= -0.50)"
        region_type = "EXTREMO_INFERIOR"
    else: # -0.50 < val_curr <= -0.20
        region = f"Zona de Parada Vermelha ({val_curr:.2f} <= -0.20)"
        region_type = "ZONA_PARADA_VERMELHA"

    # 2 & 3. CICLO ATUAL E CICLO DEVENDO
    # Detecção de Retomada de Força / Fraqueza (Ciclo Inválido no Box)
    is_retomada_forca = False
    is_retomada_fraqueza = False
    
    if len(series) >= 5:
        hist = series[-5:]
        # Retomada de Força: vinha caindo no box, mas repicou para cima sem cruzar -0.20
        if diff > 0 and val_curr > -0.20 and min(hist) > -0.30 and hist[-4] > hist[-3] and hist[-3] < val_curr:
            is_retomada_forca = True
        # Retomada de Fraqueza: vinha subindo no box, mas curvou para baixo sem cruzar +0.20
        elif diff < 0 and val_curr < 0.20 and max(hist) < 0.30 and hist[-4] < hist[-3] and hist[-3] > val_curr:
            is_retomada_fraqueza = True

    if diff > 0:
        dir_str = "▲ UP"
        if is_retomada_forca:
            current_cycle = "Retomada de Força no Box (Ciclo Inválido de Baixa)"
            owing_cycle = "Devendo Expansão de Alta rumo ao Topo"
        elif val_curr <= -0.20:
            current_cycle = "Início de Ciclo de Alta (Acúmulo sobre Zona Vermelha)"
            owing_cycle = "Devendo Ciclo de Força rumo à Linha Verde (+0.20)"
        elif -0.05 <= val_curr <= 0.05:
            current_cycle = "Aberto sobre o 0 (Permissão Válida para Subir)"
            owing_cycle = "Devendo Ciclo de Força rumo à Linha Verde (+0.20)"
        elif val_curr >= 0.20:
            current_cycle = "Ciclo de Alta Concluído (Alerta de Topo / Linha Verde)"
            owing_cycle = "Devendo Exaustão e Início de Ciclo de Fraqueza"
        else:
            current_cycle = "Ciclo de Alta em Andamento (Cruzando o Box)"
            owing_cycle = "Devendo Rompimento da Linha Verde (+0.20)"
    else: # diff <= 0
        dir_str = "▼ DN"
        if is_retomada_fraqueza:
            current_cycle = "Retomada de Fraqueza no Box (Ciclo Inválido de Alta)"
            owing_cycle = "Devendo Expansão de Baixa rumo ao Fundo"
        elif val_curr >= 0.20:
            current_cycle = "Início de Ciclo de Baixa (Acúmulo sobre Zona Verde)"
            owing_cycle = "Devendo Ciclo de Fraqueza rumo à Linha Vermelha (-0.20)"
        elif -0.05 <= val_curr <= 0.05:
            current_cycle = "Aberto sobre o 0 (Permissão Válida para Cair)"
            owing_cycle = "Devendo Ciclo de Fraqueza rumo à Linha Vermelha (-0.20)"
        elif val_curr <= -0.20:
            current_cycle = "Ciclo de Baixa Concluído (Alerta de Fundo / Linha Vermelha)"
            owing_cycle = "Devendo Exaustão e Início de Ciclo de Força"
        else:
            current_cycle = "Ciclo de Baixa em Andamento (Cruzando o Box)"
            owing_cycle = "Devendo Rompimento da Linha Vermelha (-0.20)"

    # 4. SCORE & ANGULAÇÃO
    if diff > 0:
        if abs_diff >= 0.08 or (val_curr < -0.20 and abs_diff >= 0.05):
            angle = "🚀 Foguete (Forte Aceleração de Alta ▲▲)"
            angle_type = "FOGUETE"
        elif abs_diff >= 0.02:
            angle = "▲ Inclinado para Força (UP)"
            angle_type = "FORCA_MODERADA"
        else:
            angle = "▲ Sutilmente Inclinado para Força"
            angle_type = "FORCA_SUTIL"
    else:
        if abs_diff >= 0.08 or (val_curr > 0.20 and abs_diff >= 0.05):
            angle = "🎢 Montanha-Russa (Forte Aceleração de Baixa ▼▼)"
            angle_type = "MONTANHA_RUSSA"
        elif abs_diff >= 0.02:
            angle = "▼ Inclinado para Fraqueza (DN)"
            angle_type = "FRAQUEZA_MODERADA"
        else:
            angle = "▼ Sutilmente Inclinado para Fraqueza"
            angle_type = "FRAQUEZA_SUTIL"

    return {
        "tf": tf_name,
        "score": val_curr,
        "score_str": f"{val_curr:+5.2f}",
        "diff": diff,
        "dir": dir_str,
        "region": region,
        "region_type": region_type,
        "current_cycle": current_cycle,
        "owing_cycle": owing_cycle,
        "angle": angle,
        "angle_type": angle_type,
        "is_retomada_forca": is_retomada_forca,
        "is_retomada_fraqueza": is_retomada_fraqueza
    }
