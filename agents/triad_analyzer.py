"""
MÓDULO DE ANÁLISE EM TRÍADE INSTITUCIONAL (CSS)
Executa a análise rigorosa dos 4 Pilares por Timeframe:
1. Região (Box, Zonas de Parada +/-0.20, Linha de Equilíbrio 0.00, Extremos)
2. Ciclo Atual (Alta, Baixa, Lateralizado, Retomada de Força/Fraqueza, Divergência Cíclica)
3. Ciclo Devendo (Rastreamento estrutural de destino entre Zonas de Parada +/-0.20)
4. Score & Angulação (Valor numérico exato e intensidade: Foguete, Montanha-Russa, Moderado, Sutil)
5. Status LED (Verde=UP Alinhado, Vermelho=DN Alinhado, Amarelo=Divergência / Transição)
"""

import numpy as np

def analyze_tf_triad(tf_name, series):
    """
    Analisa uma série temporal de CSS sob os pilares institucionais da Tríade Analítica.
    Rastreia a origem do ciclo estrutural entre as Zonas de Parada (+0.20 e -0.20)
    para determinar com exatidão o Ciclo Devendo e detectar Divergências de inclinação (LED Amarelo).
    """
    if len(series) < 2:
        val_curr = float(series[-1]) if len(series) > 0 else 0.0
        val_prev = val_curr
    else:
        val_curr = float(series[-1])
        val_prev = float(series[-2])
        
    diff = round(val_curr - val_prev, 4)
    abs_diff = abs(diff)
    
    # 1. REGIÃO NO BOX
    if val_curr >= 0.50:
        region = f"Extremo Superior (+{val_curr:.2f} >= +0.50)"
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

    # 2. RASTREAMENTO DO ÚLTIMO EXTREMO HISTÓRICO (Origem do Ciclo)
    last_extreme = None # "GREEN" (>= +0.20) ou "RED" (<= -0.20)
    for i in range(len(series) - 1, -1, -1):
        v = float(series[i])
        if v >= 0.20:
            last_extreme = "GREEN"
            break
        elif v <= -0.20:
            last_extreme = "RED"
            break

    # Detecção de Retomadas no Box (Ciclos Inválidos sem tocar os extremos)
    is_retomada_forca = False
    is_retomada_fraqueza = False
    if len(series) >= 5:
        hist = [float(x) for x in series[-5:]]
        # Retomada de Força: vinha caindo no box, mas repicou para cima sem cruzar -0.20
        if diff > 0 and val_curr > -0.20 and min(hist) > -0.20 and hist[-4] > hist[-3] and hist[-3] < val_curr:
            is_retomada_forca = True
        # Retomada de Fraqueza: vinha subindo no box, mas curvou para baixo sem cruzar +0.20
        elif diff < 0 and val_curr < 0.20 and max(hist) < 0.20 and hist[-4] < hist[-3] and hist[-3] > val_curr:
            is_retomada_fraqueza = True

    # 3. DETERMINAÇÃO ESTRUTURAL DO CICLO ATUAL, CICLO DEVENDO E COR DO LED
    if val_curr >= 0.20:
        # Ativo em Zona Verde / Extremo de Alta
        if diff < 0:
            current_cycle = "Início de Ciclo de Baixa (Virada no Topo / Linha Verde)"
            owing_cycle = "Devendo Ciclo de Fraqueza rumo à Linha Vermelha (-0.20)"
            led_color = "red"
        else:
            current_cycle = "Ciclo de Alta em Exaustão (Sobreforça Acima de +0.20)"
            owing_cycle = "Devendo Exaustão e Início de Ciclo de Fraqueza"
            led_color = "green" if abs_diff >= 0.02 else "yellow"

    elif val_curr <= -0.20:
        # Ativo em Zona Vermelha / Extremo de Baixa
        if diff > 0:
            current_cycle = "Início de Ciclo de Alta (Virada no Fundo / Linha Vermelha)"
            owing_cycle = "Devendo Ciclo de Força rumo à Linha Verde (+0.20)"
            led_color = "green"
        else:
            current_cycle = "Ciclo de Baixa em Exaustão (Sobrefraqueza Abaixo de -0.20)"
            owing_cycle = "Devendo Exaustão e Início de Ciclo de Força"
            led_color = "red" if abs_diff >= 0.02 else "yellow"

    else:
        # Ativo Dentro do Box (-0.20 a +0.20)
        if is_retomada_forca:
            current_cycle = "Retomada de Força no Box (Ciclo Inválido de Baixa)"
            owing_cycle = "Devendo Expansão de Alta rumo ao Topo"
            led_color = "green" if diff > 0 else "yellow"
        elif is_retomada_fraqueza:
            current_cycle = "Retomada de Fraqueza no Box (Ciclo Inválido de Alta)"
            owing_cycle = "Devendo Expansão de Baixa rumo ao Fundo"
            led_color = "red" if diff < 0 else "yellow"
        elif last_extreme == "RED":
            # Veio do fundo (<= -0.20): Está devendo Ciclo de Alta rumo à Linha Verde (+0.20)
            owing_cycle = "Devendo Ciclo de Alta rumo à Linha Verde (+0.20)"
            if diff > 0:
                current_cycle = "Ciclo de Alta em Andamento (Cruzando o Box)"
                led_color = "green"
            else:
                # DIVERGÊNCIA CÍCLICA: Deve alta, mas inclinou para baixo
                current_cycle = "Recuo / Divergência de Baixa (Devendo Alta rumo a +0.20)"
                led_color = "yellow"
        elif last_extreme == "GREEN":
            # Veio do topo (>= +0.20): Está devendo Ciclo de Baixa rumo à Linha Vermelha (-0.20)
            owing_cycle = "Devendo Ciclo de Baixa rumo à Linha Vermelha (-0.20)"
            if diff < 0:
                current_cycle = "Ciclo de Baixa em Andamento (Cruzando o Box)"
                led_color = "red"
            else:
                # DIVERGÊNCIA CÍCLICA: Deve baixa, mas inclinou para cima
                current_cycle = "Repique / Divergência de Alta (Devendo Baixa rumo a -0.20)"
                led_color = "yellow"
        else:
            # Não tocou extremos no histórico recente
            if val_curr >= 0:
                if diff > 0:
                    current_cycle = "Ciclo de Alta em Andamento"
                    owing_cycle = "Devendo Ciclo de Força rumo à Linha Verde (+0.20)"
                    led_color = "green"
                else:
                    current_cycle = "Recuo no Box Superior"
                    owing_cycle = "Devendo Ciclo de Fraqueza rumo à Linha Vermelha (-0.20)"
                    led_color = "yellow"
            else:
                if diff < 0:
                    current_cycle = "Ciclo de Baixa em Andamento"
                    owing_cycle = "Devendo Ciclo de Fraqueza rumo à Linha Vermelha (-0.20)"
                    led_color = "red"
                else:
                    current_cycle = "Repique no Box Inferior"
                    owing_cycle = "Devendo Ciclo de Força rumo à Linha Verde (+0.20)"
                    led_color = "yellow"

    # 4. ANGULAÇÃO / ROC
    if diff > 0:
        dir_str = "▲ UP"
        if abs_diff >= 0.08 or (val_curr < -0.20 and abs_diff >= 0.05):
            angle = "🚀 Foguete (Forte Aceleração de Alta ▲▲)"
            angle_type = "FOGUETE"
        elif abs_diff >= 0.02:
            angle = "▲ Inclinado para Força (UP)"
            angle_type = "FORCA_MODERADA"
        else:
            angle = "▲ Sutilmente Inclinado para Força"
            angle_type = "FORCA_SUTIL"
    elif diff < 0:
        dir_str = "▼ DN"
        if abs_diff >= 0.08 or (val_curr > 0.20 and abs_diff >= 0.05):
            angle = "🎢 Montanha-Russa (Forte Aceleração de Baixa ▼▼)"
            angle_type = "MONTANHA_RUSSA"
        elif abs_diff >= 0.02:
            angle = "▼ Inclinado para Fraqueza (DN)"
            angle_type = "FRAQUEZA_MODERADA"
        else:
            angle = "▼ Sutilmente Inclinado para Fraqueza"
            angle_type = "FRAQUEZA_SUTIL"
    else:
        dir_str = "→ FLAT"
        angle = "→ Sem Inclinação / Neutro"
        angle_type = "FLAT"
        if led_color == "green" or led_color == "red":
            led_color = "yellow"

    return {
        "tf": tf_name,
        "score": val_curr,
        "score_str": f"{val_curr:+5.2f}",
        "diff": diff,
        "dir": dir_str,
        "led": led_color,
        "region": region,
        "region_type": region_type,
        "current_cycle": current_cycle,
        "owing_cycle": owing_cycle,
        "angle": angle,
        "angle_type": angle_type,
        "is_retomada_forca": is_retomada_forca,
        "is_retomada_fraqueza": is_retomada_fraqueza
    }
