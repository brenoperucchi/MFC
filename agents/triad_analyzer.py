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

# Thresholds da Tríade Analítica — item 3 do plano de reconciliação com o
# upstream (Miquéias, 27/08): ele mexeu neste valor 3 vezes em 3 dias nos 42
# commits analisados, mas nem toda mudança era tuning de faixa (achado em
# revisão, mfc-rev-2, herdr-review rodada 20 — ela leu os 3 commits reais:
# `544d660`/`ef6a6f6` SÃO tuning de alvo móvel, +/-0.20 original -> banda
# +/-0.01 em torno de 0.20 -> banda alargada +/-0.04; `220f0a3` NÃO mexe no
# threshold — ele arredonda o score pra 2 casas ANTES de comparar, corrigindo
# um descasamento real entre o número exibido e a região classificada, que
# EXISTE também do nosso lado, não corrigido aqui — ver P3-2 registrado em
# .herdr/reviewer.md). A decisão sobre o VALOR não muda: congelado em +/-0.20
# (MANTER o valor já em uso, não perseguir o alvo móvel dos dois primeiros
# commits) — é o mesmo valor documentado em docs/MATHEMATICAL_MODELS.md
# ("Zona de Parada Superior/Inferior") e o default de input tanto em
# mt5/css.mql5 (`inp_levelCrossValue`) quanto em CSS.pine (`boxLevel`), então
# já era de fato o valor em uso nas três implementações — isto só torna
# esse fato estrutural (uma constante nomeada, não ~20 literais soltos
# repetidos pelo arquivo) em vez de só convencional. Consumido por
# agents/macro_analyzer.py e agents/operational_analyzer.py através do
# resultado desta função — mudar aqui muda a classificação de região em
# toda a pipeline de confluência.
REGION_ZONA_PARADA = 0.20
REGION_EQUILIBRIO = 0.05
REGION_EXTREMO = 0.50


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
    if val_curr >= REGION_EXTREMO:
        region = f"Extremo Superior (+{val_curr:.2f} >= +{REGION_EXTREMO:.2f})"
        region_type = "EXTREMO_SUPERIOR"
    elif val_curr >= REGION_ZONA_PARADA:
        region = f"Zona de Parada Verde (+{val_curr:.2f} >= +{REGION_ZONA_PARADA:.2f})"
        region_type = "ZONA_PARADA_VERDE"
    elif REGION_EQUILIBRIO < val_curr < REGION_ZONA_PARADA:
        region = f"Dentro do Box Superior (+{val_curr:.2f} entre 0.00 e +{REGION_ZONA_PARADA:.2f})"
        region_type = "BOX_SUPERIOR"
    elif -REGION_EQUILIBRIO <= val_curr <= REGION_EQUILIBRIO:
        region = f"Linha de Equilíbrio 0.00 ({val_curr:+.2f})"
        region_type = "EQUILIBRIO_0"
    elif -REGION_ZONA_PARADA < val_curr < -REGION_EQUILIBRIO:
        region = f"Dentro do Box Inferior ({val_curr:.2f} entre -{REGION_ZONA_PARADA:.2f} e 0.00)"
        region_type = "BOX_INFERIOR"
    elif val_curr <= -REGION_EXTREMO:
        region = f"Extremo Inferior ({val_curr:.2f} <= -{REGION_EXTREMO:.2f})"
        region_type = "EXTREMO_INFERIOR"
    else: # -REGION_EXTREMO < val_curr <= -REGION_ZONA_PARADA
        region = f"Zona de Parada Vermelha ({val_curr:.2f} <= -{REGION_ZONA_PARADA:.2f})"
        region_type = "ZONA_PARADA_VERMELHA"

    # 2. RASTREAMENTO DO ÚLTIMO EXTREMO HISTÓRICO (Origem do Ciclo)
    last_extreme = None # "GREEN" (>= +REGION_ZONA_PARADA) ou "RED" (<= -REGION_ZONA_PARADA)
    for i in range(len(series) - 1, -1, -1):
        v = float(series[i])
        if v >= REGION_ZONA_PARADA:
            last_extreme = "GREEN"
            break
        elif v <= -REGION_ZONA_PARADA:
            last_extreme = "RED"
            break

    # Detecção de Retomadas no Box (Ciclos Inválidos sem tocar os extremos)
    is_retomada_forca = False
    is_retomada_fraqueza = False
    if len(series) >= 5:
        hist = [float(x) for x in series[-5:]]
        # Retomada de Força: vinha caindo no box, mas repicou para cima sem cruzar -REGION_ZONA_PARADA
        if diff > 0 and val_curr > -REGION_ZONA_PARADA and min(hist) > -REGION_ZONA_PARADA and hist[-4] > hist[-3] and hist[-3] < val_curr:
            is_retomada_forca = True
        # Retomada de Fraqueza: vinha subindo no box, mas curvou para baixo sem cruzar +REGION_ZONA_PARADA
        elif diff < 0 and val_curr < REGION_ZONA_PARADA and max(hist) < REGION_ZONA_PARADA and hist[-4] < hist[-3] and hist[-3] > val_curr:
            is_retomada_fraqueza = True

    # 3. DETERMINAÇÃO ESTRUTURAL DO CICLO ATUAL, CICLO DEVENDO E COR DO LED
    if val_curr >= REGION_ZONA_PARADA:
        # Ativo em Zona Verde / Extremo de Alta
        if diff < 0:
            current_cycle = "Início de Ciclo de Baixa (Virada no Topo / Linha Verde)"
            owing_cycle = f"Devendo Ciclo de Fraqueza rumo à Linha Vermelha (-{REGION_ZONA_PARADA:.2f})"
            led_color = "red"
        else:
            current_cycle = f"Ciclo de Alta em Exaustão (Sobreforça Acima de +{REGION_ZONA_PARADA:.2f})"
            owing_cycle = "Devendo Exaustão e Início de Ciclo de Fraqueza"
            led_color = "green" if abs_diff >= 0.02 else "yellow"

    elif val_curr <= -REGION_ZONA_PARADA:
        # Ativo em Zona Vermelha / Extremo de Baixa
        if diff > 0:
            current_cycle = "Início de Ciclo de Alta (Virada no Fundo / Linha Vermelha)"
            owing_cycle = f"Devendo Ciclo de Força rumo à Linha Verde (+{REGION_ZONA_PARADA:.2f})"
            led_color = "green"
        else:
            current_cycle = f"Ciclo de Baixa em Exaustão (Sobrefraqueza Abaixo de -{REGION_ZONA_PARADA:.2f})"
            owing_cycle = "Devendo Exaustão e Início de Ciclo de Força"
            led_color = "red" if abs_diff >= 0.02 else "yellow"

    else:
        # Ativo Dentro do Box (-REGION_ZONA_PARADA a +REGION_ZONA_PARADA)
        if is_retomada_forca:
            current_cycle = "Retomada de Força no Box (Ciclo Inválido de Baixa)"
            owing_cycle = "Devendo Expansão de Alta rumo ao Topo"
            led_color = "green" if diff > 0 else "yellow"
        elif is_retomada_fraqueza:
            current_cycle = "Retomada de Fraqueza no Box (Ciclo Inválido de Alta)"
            owing_cycle = "Devendo Expansão de Baixa rumo ao Fundo"
            led_color = "red" if diff < 0 else "yellow"
        elif last_extreme == "RED":
            # Veio do fundo (<= -REGION_ZONA_PARADA): Está devendo Ciclo de Alta rumo à Linha Verde
            owing_cycle = f"Devendo Ciclo de Alta rumo à Linha Verde (+{REGION_ZONA_PARADA:.2f})"
            if diff > 0:
                current_cycle = "Ciclo de Alta em Andamento (Cruzando o Box)"
                led_color = "green"
            else:
                # DIVERGÊNCIA CÍCLICA: Deve alta, mas inclinou para baixo
                current_cycle = f"Recuo / Divergência de Baixa (Devendo Alta rumo a +{REGION_ZONA_PARADA:.2f})"
                led_color = "yellow"
        elif last_extreme == "GREEN":
            # Veio do topo (>= +REGION_ZONA_PARADA): Está devendo Ciclo de Baixa rumo à Linha Vermelha
            owing_cycle = f"Devendo Ciclo de Baixa rumo à Linha Vermelha (-{REGION_ZONA_PARADA:.2f})"
            if diff < 0:
                current_cycle = "Ciclo de Baixa em Andamento (Cruzando o Box)"
                led_color = "red"
            else:
                # DIVERGÊNCIA CÍCLICA: Deve baixa, mas inclinou para cima
                current_cycle = f"Repique / Divergência de Alta (Devendo Baixa rumo a -{REGION_ZONA_PARADA:.2f})"
                led_color = "yellow"
        else:
            # Não tocou extremos no histórico recente
            if val_curr >= 0:
                if diff > 0:
                    current_cycle = "Ciclo de Alta em Andamento"
                    owing_cycle = f"Devendo Ciclo de Força rumo à Linha Verde (+{REGION_ZONA_PARADA:.2f})"
                    led_color = "green"
                else:
                    current_cycle = "Recuo no Box Superior"
                    owing_cycle = f"Devendo Ciclo de Fraqueza rumo à Linha Vermelha (-{REGION_ZONA_PARADA:.2f})"
                    led_color = "yellow"
            else:
                if diff < 0:
                    current_cycle = "Ciclo de Baixa em Andamento"
                    owing_cycle = f"Devendo Ciclo de Fraqueza rumo à Linha Vermelha (-{REGION_ZONA_PARADA:.2f})"
                    led_color = "red"
                else:
                    current_cycle = "Repique no Box Inferior"
                    owing_cycle = f"Devendo Ciclo de Força rumo à Linha Verde (+{REGION_ZONA_PARADA:.2f})"
                    led_color = "yellow"

    # 4. ANGULAÇÃO / ROC — 0.02/0.08 são limiares de intensidade de VARIAÇÃO
    # (abs_diff), sem relação com REGION_ZONA_PARADA; o 0.05 aqui também é
    # limiar de abs_diff (coincide em valor com REGION_EQUILIBRIO, mas é
    # outro conceito — variação entre barras, não posição absoluta do
    # score). Não usar as constantes de região aqui evita acoplar as duas
    # escalas por engano numa futura recalibração de uma delas.
    if diff > 0:
        dir_str = "▲ UP"
        if abs_diff >= 0.08 or (val_curr < -REGION_ZONA_PARADA and abs_diff >= 0.05):
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
        if abs_diff >= 0.08 or (val_curr > REGION_ZONA_PARADA and abs_diff >= 0.05):
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
