"""
ROTINA DIÁRIA AUTOMATIZADA DE ANÁLISE CSS MULTI-TIMEFRAME (21:01)
Sistema Multi-Agente: Agente Macro (MN, W1, D1) + Agente Operacional (H4, H1)
"""

import os
import sys
from datetime import datetime
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import MetaTrader5 as mt5

# Garantir que a pasta MFC esteja no sys.path para importar agents
BASE_DIR = r"c:\Users\ryzen\Downloads\Antigravity\MFC"
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from agents.confluence_engine import evaluate_currency_confluence, evaluate_28_pairs_confluence

# Setup dark styling
plt.style.use('dark_background')
plt.rcParams['font.family'] = 'sans-serif'
plt.rcParams['font.size'] = 9

LOG_DIR = os.path.join(BASE_DIR, "log_conhecimento")
os.makedirs(LOG_DIR, exist_ok=True)

MT5_PATH = r"C:\Program Files\Tickmill MT5 Terminal - Copia - Copia\terminal64.exe"

SYMBOLS = [
    "AUDCAD", "AUDCHF", "AUDJPY", "AUDNZD", "AUDUSD",
    "CADJPY", "CHFJPY",
    "EURAUD", "EURCAD", "EURJPY", "EURNZD", "EURUSD",
    "GBPAUD", "GBPCAD", "GBPCHF", "GBPJPY", "GBPNZD", "GBPUSD",
    "NZDCHF", "NZDJPY", "NZDUSD",
    "USDCAD", "USDCHF", "USDJPY"
]

ALL_28_PAIRS = [
    "EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "USDJPY",
    "EURGBP", "EURAUD", "EURCAD", "EURCHF", "EURJPY", "EURNZD",
    "GBPAUD", "GBPCAD", "GBPCHF", "GBPJPY", "GBPNZD",
    "AUDCAD", "AUDCHF", "AUDJPY", "AUDNZD",
    "CADCHF", "CADJPY",
    "CHFJPY",
    "NZDCAD", "NZDCHF", "NZDJPY"
]

CURRENCIES = ["USD", "EUR", "GBP", "CHF", "JPY", "AUD", "CAD", "NZD"]

CCY_COLORS = {
    "USD": "#FF3333",
    "EUR": "#00BFFF",
    "GBP": "#4169E1",
    "CHF": "#00E5FF",
    "JPY": "#FFD700",
    "AUD": "#FF8C00",
    "CAD": "#8B0000",
    "NZD": "#D2B48C"
}

TIMEFRAMES_CONFIG = [
    ("MN1", mt5.TIMEFRAME_MN1, 70),
    ("W1",  mt5.TIMEFRAME_W1, 100),
    ("D1",  mt5.TIMEFRAME_D1, 120),
    ("H4",  mt5.TIMEFRAME_H4, 120),
    ("H1",  mt5.TIMEFRAME_H1, 500)
]

def calculate_atr_wilder(high, low, close, period=100):
    tr = np.zeros(len(close))
    tr[0] = high[0] - low[0]
    for i in range(1, len(close)):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i-1]), abs(low[i] - close[i-1]))
    atr = np.zeros(len(close))
    if len(close) < period:
        return atr
    atr[period-1] = np.mean(tr[:period])
    alpha = 1.0 / period
    for i in range(period, len(close)):
        atr[i] = atr[i-1] * (1.0 - alpha) + tr[i] * alpha
    return atr

def calc_tma_series(closes):
    tma = np.zeros(len(closes))
    n = len(closes)
    for pos in range(n):
        c0 = closes[pos]
        dblSum = c0 * 21.0
        dblSumw = 21.0
        for jnx in range(1, 21):
            knx = 21 - jnx
            past_pos = pos - jnx
            if past_pos >= 0:
                dblSum += closes[past_pos] * knx
                dblSumw += knx
            future_pos = pos + jnx
            if future_pos < n:
                dblSum += closes[future_pos] * knx
                dblSumw += knx
        tma[pos] = dblSum / dblSumw if dblSumw > 0 else c0
    return tma

def calculate_full_css(tf_val, count=120):
    pair_dfs = {}
    for sym in ALL_28_PAIRS:
        rates = mt5.copy_rates_from_pos(sym, tf_val, 0, count + 150)
        if rates is None or len(rates) < 120:
            continue
        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        df.set_index('time', inplace=True)
        pair_dfs[sym] = df
        
    if not pair_dfs:
        return None, None
        
    common_index = None
    for sym, df in pair_dfs.items():
        if common_index is None:
            common_index = df.index
        else:
            common_index = common_index.intersection(df.index)
            
    if common_index is None or len(common_index) == 0:
        return None, None
        
    common_index = common_index[-count:]
    pair_slopes = {}
    occurrences = {c: 0 for c in CURRENCIES}
    
    for sym in ALL_28_PAIRS:
        if sym not in pair_dfs:
            continue
        df = pair_dfs[sym]
        closes = df['close'].values
        highs = df['high'].values
        lows = df['low'].values
        
        atr_arr = calculate_atr_wilder(highs, lows, closes, 100)
        tma_arr = calc_tma_series(closes)
        idx_map = {t: i for i, t in enumerate(df.index)}
        
        slopes = []
        for t in common_index:
            pos = idx_map.get(t, -1)
            if pos <= 0:
                slopes.append(0.0)
                continue
            atr_val = atr_arr[pos - 10] if (pos - 10) >= 0 else atr_arr[pos]
            atr = atr_val / 10.0
            sl = (tma_arr[pos] - tma_arr[pos - 1]) / atr if atr > 0 else 0.0
            slopes.append(sl)
            
        base, quote = sym[:3], sym[3:6]
        pair_slopes[sym] = (base, quote, np.array(slopes))
        if base in occurrences: occurrences[base] += 1
        if quote in occurrences: occurrences[quote] += 1
        
    css_res = {c: np.zeros(len(common_index)) for c in CURRENCIES}
    for sym, (base, quote, sl) in pair_slopes.items():
        if base in css_res: css_res[base] += sl
        if quote in css_res: css_res[quote] -= sl
        
    for c in CURRENCIES:
        if occurrences[c] > 0:
            css_res[c] /= occurrences[c]
            
    return css_res, [t.to_pydatetime() for t in common_index]

def run_daily_routine():
    now = datetime.now()
    date_str = now.strftime("%Y%m%d")
    date_formatted = now.strftime("%d/%m/%Y")
    
    reports_dir = os.path.join(BASE_DIR, "reports")
    os.makedirs(reports_dir, exist_ok=True)
    daily_folder = os.path.join(reports_dir, date_str)
    os.makedirs(daily_folder, exist_ok=True)
    
    print(f"[{now.strftime('%H:%M:%S')}] Iniciando Rotina Diaria Multi-Agente CSS: {date_formatted}")
    
    if not mt5.initialize(path=MT5_PATH):
        print("Falha ao inicializar MT5:", mt5.last_error())
        return False
        
    # 1. Obter dados CSS para todos os timeframes
    tf_data = {}
    for tf_name, tf_val, count in TIMEFRAMES_CONFIG:
        res, times = calculate_full_css(tf_val, count)
        if res is not None:
            tf_data[tf_name] = (res, times)
            
    # 2. Execucao dos Agentes Especializados (Macro + Operacional + Confluencia)
    ccy_confluence_results = {}
    for c in CURRENCIES:
        mn_s = tf_data["MN1"][0][c]
        w1_s = tf_data["W1"][0][c]
        d1_s = tf_data["D1"][0][c]
        h4_s = tf_data["H4"][0][c]
        h1_s = tf_data["H1"][0][c]
        
        conf = evaluate_currency_confluence(c, mn_s, w1_s, d1_s, h4_s, h1_s)
        ccy_confluence_results[c] = conf
        
    # 3. Avaliacao dos 28 Pares pelo Motor de Confluencia
    pair_rankings = evaluate_28_pairs_confluence(ALL_28_PAIRS, ccy_confluence_results, tf_data)
    
    mt5.shutdown()

    # 4. Gerar Prints dos 8 Dashboards das Moedas Puras (AUD, EUR, GBP, NZD, CAD, CHF, JPY, USD)
    for ccy in CURRENCIES:
        fig, axes = plt.subplots(2, 3, figsize=(18, 9), facecolor="#141414")
        title_hdr = f"CSS Cyclic Macro Dashboard — Moeda: {ccy} | {date_formatted}"
        fig.suptitle(title_hdr, fontsize=14, fontweight='bold', color='white', y=0.98)
        
        grid_map = [
            ("MN1", axes[0, 0]),
            ("D1",  axes[0, 1]),
            ("H1",  axes[0, 2]),
            ("W1",  axes[1, 0]),
            ("H4",  axes[1, 1])
        ]
        
        ccy_color = CCY_COLORS.get(ccy, "#FF8C00")
        
        for tf_name, ax in grid_map:
            ax.set_facecolor("#181818")
            ax.grid(True, linestyle=":", alpha=0.3, color="#555555")
            if tf_name in tf_data:
                css_dict, times = tf_data[tf_name]
                series = css_dict[ccy]
                
                # Auto-escala vertical dinamica
                d_min = series.min()
                d_max = series.max()
                y_min = min(d_min - 0.15, -1.2)
                y_max = max(d_max + 0.15, 1.2)
                ax.set_ylim(y_min, y_max)
                
                # Linhas do Box Principal (+0.20 Verde, -0.20 Vermelha) e Linhas Pontilhadas Sutis
                if y_min <= 0.0 <= y_max:
                    ax.axhline(0.0, color="#777777", linestyle=":", linewidth=1.0, alpha=0.7)
                if y_min <= 0.20 <= y_max:
                    ax.axhline(0.20, color="#00FF00", linestyle="--", linewidth=1.5, alpha=0.9)
                if y_min <= -0.20 <= y_max:
                    ax.axhline(-0.20, color="#FF3333", linestyle="--", linewidth=1.5, alpha=0.9)
                
                other_lvls = [0.50, -0.50, 1.00, -1.00, 1.50, -1.50, 2.00, -2.00, 2.50, -2.50, 3.00, -3.00]
                for lvl in other_lvls:
                    if y_min <= lvl <= y_max:
                        ax.axhline(lvl, color="#444444", linestyle=":", linewidth=0.8, alpha=0.45)
                
                x_idx = np.arange(len(times))
                ax.plot(x_idx, series, color=ccy_color, linewidth=2.2, label=ccy)
                last_val = series[-1]
                diff = series[-1] - series[-2]
                dir_txt = "▲ UP" if diff > 0 else "▼ DN"
                
                ax.text(0.97, 0.92, f"TF: {tf_name}\n{ccy}: {last_val:+5.2f} ({dir_txt})", 
                        transform=ax.transAxes, fontsize=10, fontweight='bold',
                        color=ccy_color, horizontalalignment='right', verticalalignment='top',
                        bbox=dict(boxstyle="round,pad=0.3", facecolor="#222222", edgecolor=ccy_color, alpha=0.85))
                        
                ax.set_title(f"{ccy}, {tf_name}", fontsize=11, color="#E0E0E0", pad=6)
                
                tick_pos = np.linspace(0, len(times)-1, 6, dtype=int)
                ax.set_xticks(tick_pos)
                fmt_str = '%b %y' if tf_name in ['MN1', 'W1'] else '%d/%m %H:%M' if tf_name in ['H4', 'H1'] else '%d %b'
                ax.set_xticklabels([times[p].strftime(fmt_str) for p in tick_pos], color='#AAAAAA', fontsize=8)
                 # Painel 6: Resumo Institucional e Diagnostico da Moeda Pura
        ax_card = axes[1, 2]
        ax_card.set_facecolor("#181818")
        ax_card.axis('off')
        
        ax_card.text(0.05, 0.95, f"RESUMO MULTI-TIMEFRAME — {ccy}", fontsize=11.5, fontweight='bold', color='white', transform=ax_card.transAxes)
        ax_card.plot([0.05, 0.95], [0.91, 0.91], color=ccy_color, linewidth=1.5, transform=ax_card.transAxes)
        
        tfs_order = [("MN1", "Macro Mensal"), ("W1", "Macro Semanal"), ("D1", "Gatilho Diário"), ("H4", "Swing H4"), ("H1", "Operacional H1")]
        y_c = 0.83
        for tf_k, tf_lbl in tfs_order:
            if tf_k in tf_data:
                s_dict, _ = tf_data[tf_k]
                val = s_dict[ccy][-1]
                diff = s_dict[ccy][-1] - s_dict[ccy][-2]
                dir_t = "▲ UP" if diff > 0 else "▼ DN"
                ax_card.text(0.08, y_c, f"{tf_k:<4} ({tf_lbl}):", fontsize=9.0, color="#CCCCCC", transform=ax_card.transAxes)
                ax_card.text(0.65, y_c, f"{val:+5.2f} ({dir_t})", fontsize=9.5, fontweight='bold', color=ccy_color, transform=ax_card.transAxes)
                y_c -= 0.075
                
        ax_card.plot([0.05, 0.95], [0.43, 0.43], color="#555555", transform=ax_card.transAxes)
        ax_card.text(0.05, 0.38, "DIAGNÓSTICO E PERMISSÕES", fontsize=10.5, fontweight='bold', color='white', transform=ax_card.transAxes)
        
        conf_info = ccy_confluence_results.get(ccy, None)
        if conf_info:
            m_phase = conf_info["macro"]["cyclic_phase"]
            op_stat = conf_info["operational"]["op_status"]
            v_bias = conf_info["final_verdict"]
            div_alert = conf_info["divergence_alert"]
            has_div = conf_info["has_divergence"]
            
            ax_card.text(0.08, 0.29, f"Macro: {m_phase[:44]}", fontsize=8.0, color="#E0E0E0", transform=ax_card.transAxes)
            ax_card.text(0.08, 0.21, f"Operacional: {op_stat[:44]}", fontsize=8.0, color="#E0E0E0", transform=ax_card.transAxes)
            
            if has_div:
                ax_card.text(0.08, 0.12, f"{div_alert[:46]}", fontsize=7.8, fontweight='bold', color="#FFD700", transform=ax_card.transAxes)
            else:
                ax_card.text(0.08, 0.12, "Alinhamento: Sem divergência estrutural", fontsize=8.0, color="#88FF88", transform=ax_card.transAxes)
                
            ax_card.text(0.08, 0.02, f"Veredito: {v_bias[:42]}", fontsize=9.0, fontweight='bold', color=ccy_color, transform=ax_card.transAxes, bbox=dict(boxstyle="round,pad=0.25", facecolor="#222222", edgecolor=ccy_color, alpha=0.85))
            
        plt.tight_layout(rect=[0, 0.03, 1, 0.95])
        img_filename = f"{ccy}_5TF_Dashboard.png"
        img_full_path = os.path.join(daily_folder, img_filename)
        plt.savefig(img_full_path, dpi=120)
        plt.close()

    # 4.1 Gerar Painel Geral CSS H1 com Todas as 8 Moedas (Alinhado com Template MT5)
    fig_all, (ax_main, ax_rank) = plt.subplots(1, 2, figsize=(18, 8), gridspec_kw={'width_ratios': [4, 1]}, facecolor="#141414")
    fig_all.suptitle(f"CSS Indicator — Todas as 8 Moedas (H1) | {date_formatted} ({now.strftime('%H:%M')})", fontsize=14, fontweight='bold', color='white', y=0.98)
    
    ax_main.set_facecolor("#181818")
    ax_main.grid(True, linestyle=":", alpha=0.3, color="#555555")
    
    h1_css, h1_times = tf_data["H1"]
    all_min = min(h1_css[c].min() for c in CURRENCIES)
    all_max = max(h1_css[c].max() for c in CURRENCIES)
    y_min = min(all_min - 0.25, -1.5)
    y_max = max(all_max + 0.25, 1.5)
    ax_main.set_ylim(y_min, y_max)
    
    # Niveis no grafico principal: +0.20 Verde, -0.20 Vermelho, 0.0 Pontilhado, outros pontilhados sutis
    if y_min <= 0.0 <= y_max:
        ax_main.axhline(0.0, color="#777777", linestyle=":", linewidth=1.0, alpha=0.7)
    if y_min <= 0.20 <= y_max:
        ax_main.axhline(0.20, color="#00FF00", linestyle="--", linewidth=1.5, alpha=0.9)
    if y_min <= -0.20 <= y_max:
        ax_main.axhline(-0.20, color="#FF3333", linestyle="--", linewidth=1.5, alpha=0.9)
    
    other_lvls = [0.50, -0.50, 1.00, -1.00, 1.50, -1.50, 2.00, -2.00, 2.50, -2.50, 3.00, -3.00]
    for lvl in other_lvls:
        if y_min <= lvl <= y_max:
            ax_main.axhline(lvl, color="#444444", linestyle=":", linewidth=0.8, alpha=0.45)
    
    x_indices = np.arange(len(h1_times))
    for c in CURRENCIES:
        ax_main.plot(x_indices, h1_css[c], color=CCY_COLORS[c], linewidth=2.0, label=c)
        
    ax_main.set_title("Currency Slope Strength — H1 (Últimas 500 barras / ~1 Mês - Auto-Escala)", fontsize=11, color="#E0E0E0", pad=6)
    tick_pos = np.linspace(0, len(h1_times)-1, 10, dtype=int)
    ax_main.set_xticks(tick_pos)
    ax_main.set_xticklabels([h1_times[p].strftime('%d/%m %H:%M') for p in tick_pos], color='#AAAAAA', fontsize=8.5)
    ax_main.legend(loc="upper left", ncol=4, framealpha=0.3, facecolor="#222222", edgecolor="#555555", labelcolor="white")
    
    # Painel Lateral de Ranking H1 e MN1
    ax_rank.set_facecolor("#181818")
    ax_rank.axis('off')
    
    sorted_ccys = sorted(CURRENCIES, key=lambda c: h1_css[c][-1], reverse=True)
    
    ax_rank.text(0.05, 0.96, "RANKING H1 (Operacional)", fontsize=11, fontweight='bold', color='white', transform=ax_rank.transAxes)
    ax_rank.text(0.05, 0.92, "Moeda    Score    Dir", fontsize=9, color='#AAAAAA', transform=ax_rank.transAxes)
    ax_rank.plot([0.05, 0.95], [0.905, 0.905], color='#555555', transform=ax_rank.transAxes)
    
    y_pos = 0.865
    for c in sorted_ccys:
        val = h1_css[c][-1]
        diff = h1_css[c][-1] - h1_css[c][-2]
        dir_t = "▲ UP" if diff > 0 else "▼ DN"
        c_color = CCY_COLORS[c]
        row_txt = f"{c:<4}  {val:+5.2f}  ({dir_t})"
        ax_rank.text(0.05, y_pos, row_txt, fontsize=10, fontweight='bold', color=c_color, transform=ax_rank.transAxes)
        y_pos -= 0.046
        
    # Adicionar também Ranking MN1 no painel lateral
    if "MN1" in tf_data:
        mn_css, _ = tf_data["MN1"]
        sorted_mn = sorted(CURRENCIES, key=lambda c: mn_css[c][-1], reverse=True)
        ax_rank.text(0.05, 0.46, "RANKING MN1 (Macro)", fontsize=11, fontweight='bold', color='white', transform=ax_rank.transAxes)
        ax_rank.text(0.05, 0.42, "Moeda    Score    Dir", fontsize=9, color='#AAAAAA', transform=ax_rank.transAxes)
        ax_rank.plot([0.05, 0.95], [0.405, 0.405], color='#555555', transform=ax_rank.transAxes)
        y_pos = 0.365
        for c in sorted_mn:
            val = mn_css[c][-1]
            diff = mn_css[c][-1] - mn_css[c][-2]
            dir_t = "▲ UP" if diff > 0 else "▼ DN"
            c_color = CCY_COLORS[c]
            row_txt = f"{c:<4}  {val:+5.2f}  ({dir_t})"
            ax_rank.text(0.05, y_pos, row_txt, fontsize=9.5, fontweight='bold', color=c_color, transform=ax_rank.transAxes)
            y_pos -= 0.044
            
    plt.tight_layout(rect=[0, 0.03, 1, 0.95])
    all_h1_path = os.path.join(daily_folder, "CSS_AllCurrencies_H1.png")
    plt.savefig(all_h1_path, dpi=120)
    plt.close()
    
    print(f"[{now.strftime('%H:%M:%S')}] 8 Dashboards das Moedas Puras + Painel Geral H1 salvos em: {daily_folder}")
    
    # 5. Montar Relatorio Diario em Markdown com Separacao dos Agentes e Tríade Analítica
    md_content = f"""# Relatório Diário de Confluência Multi-Agente CSS — {date_formatted}

## 1. Referência dos Dados
* **Data da Execução**: {now.strftime('%d/%m/%Y às %H:%M:%S')}
* **Diretório das Imagens**: [`c:\\Users\\ryzen\\Downloads\\Antigravity\\MFC\\reports\\{date_str}`](file:///c:/Users/ryzen/Downloads/Antigravity/MFC/reports/{date_str})
* **Dashboards Salvos (Moedas Puras)**:
  * [`USD_5TF_Dashboard.png`](file:///c:/Users/ryzen/Downloads/Antigravity/MFC/reports/{date_str}/USD_5TF_Dashboard.png)
  * [`EUR_5TF_Dashboard.png`](file:///c:/Users/ryzen/Downloads/Antigravity/MFC/reports/{date_str}/EUR_5TF_Dashboard.png)
  * [`GBP_5TF_Dashboard.png`](file:///c:/Users/ryzen/Downloads/Antigravity/MFC/reports/{date_str}/GBP_5TF_Dashboard.png)
  * [`AUD_5TF_Dashboard.png`](file:///c:/Users/ryzen/Downloads/Antigravity/MFC/reports/{date_str}/AUD_5TF_Dashboard.png)
  * [`NZD_5TF_Dashboard.png`](file:///c:/Users/ryzen/Downloads/Antigravity/MFC/reports/{date_str}/NZD_5TF_Dashboard.png)
  * [`CAD_5TF_Dashboard.png`](file:///c:/Users/ryzen/Downloads/Antigravity/MFC/reports/{date_str}/CAD_5TF_Dashboard.png)
  * [`CHF_5TF_Dashboard.png`](file:///c:/Users/ryzen/Downloads/Antigravity/MFC/reports/{date_str}/CHF_5TF_Dashboard.png)
  * [`JPY_5TF_Dashboard.png`](file:///c:/Users/ryzen/Downloads/Antigravity/MFC/reports/{date_str}/JPY_5TF_Dashboard.png)
  * [`CSS_AllCurrencies_H1.png`](file:///c:/Users/ryzen/Downloads/Antigravity/MFC/reports/{date_str}/CSS_AllCurrencies_H1.png) *(Visão Geral 8 Moedas no H1)*

---

## 2. Análise Estruturada por Moeda (Tríade Analítica: Região $\\rightarrow$ Ciclo Atual $\\rightarrow$ Ciclo Devendo $\\rightarrow$ Score & Angulação)
"""
    for c in CURRENCIES:
        conf = ccy_confluence_results[c]
        macro = conf["macro"]
        op = conf["operational"]
        
        md_content += f"""
### 🔹 Moeda: **{c}**
* **Veredito Unificado**: `{conf['confluence_state']}` $\\rightarrow$ **{conf['final_verdict']}**
* **Alerta de Divergência**: {conf['divergence_alert']}

| Timeframe | 1. Região no Box | 2. Ciclo Atual | 3. Ciclo Devendo | 4. Score & Angulação |
| :---: | :--- | :--- | :--- | :--- |
| **MN1 (Mensal)** | {macro['mn_triad']['region']} | {macro['mn_triad']['current_cycle']} | {macro['mn_triad']['owing_cycle']} | `{macro['mn_triad']['score_str']}` ({macro['mn_triad']['angle']}) |
| **W1 (Semanal)** | {macro['w1_triad']['region']} | {macro['w1_triad']['current_cycle']} | {macro['w1_triad']['owing_cycle']} | `{macro['w1_triad']['score_str']}` ({macro['w1_triad']['angle']}) |
| **D1 (Diário)**  | {macro['d1_triad']['region']} | {macro['d1_triad']['current_cycle']} | {macro['d1_triad']['owing_cycle']} | `{macro['d1_triad']['score_str']}` ({macro['d1_triad']['angle']}) |
| **H4 (Swing)**   | {op['h4_triad']['region']} | {op['h4_triad']['current_cycle']} | {op['h4_triad']['owing_cycle']} | `{op['h4_triad']['score_str']}` ({op['h4_triad']['angle']}) |
| **H1 (Intraday)**| {op['h1_triad']['region']} | {op['h1_triad']['current_cycle']} | {op['h1_triad']['owing_cycle']} | `{op['h1_triad']['score_str']}` ({op['h1_triad']['angle']}) |
"""

    md_content += """
---

## 3. Síntese do Agente Macro (MN1, W1, D1)
*Foco: Contexto institucional, zonas de parada (+/- 0.20), equilíbrio (0.00) e permissões de ciclo.*

| Moeda | MN1 (Score / Dir) | W1 (Score / Dir) | D1 (Score / Dir) | Fase do Ciclo Macro | Viés Macro |
| :---: | :---: | :---: | :---: | :--- | :--- |
"""
    for c in CURRENCIES:
        m = ccy_confluence_results[c]["macro"]
        md_content += f"| **{c}** | {m['mn_score']:+5.2f} ({m['mn_dir']}) | {m['w1_score']:+5.2f} ({m['w1_dir']}) | {m['d1_score']:+5.2f} ({m['d1_dir']}) | {m['cyclic_phase']} | **{m['macro_bias']}** |\n"
        
    md_content += """
---

## 4. Síntese do Agente Operacional (H4, H1)
*Foco: Timing intraday, acúmulos sobre zonas de parada, retomadas no Box e cumprimento de ciclos.*

| Moeda | H4 (Score / Dir) | H1 (Score / Dir) | Status Operacional | Timing de Execução |
| :---: | :---: | :---: | :--- | :--- |
"""
    for c in CURRENCIES:
        o = ccy_confluence_results[c]["operational"]
        md_content += f"| **{c}** | {o['h4_score']:+5.2f} ({o['h4_dir']}) | {o['h1_score']:+5.2f} ({o['h1_dir']}) | {o['op_status']} | **{o['timing_type']}** |\n"
        
    md_content += """
---

## 5. Alertas de Divergência entre Timeframes (Mensal vs H4/H1)
*Identificação de divergências estruturais entre a inércia do Mensal e os ciclos operacionais imediatos de H4 e H1.*

| Moeda | Alerta de Divergência | Impacto no Trade |
| :---: | :--- | :--- |
"""
    for c in CURRENCIES:
        res = ccy_confluence_results[c]
        alert_txt = res["divergence_alert"]
        impact = "⚠️ Ciclo operacional (H4/H1) em contra-fluxo à inércia do Mensal" if res["has_divergence"] else "✅ Timeframes em pleno alinhamento harmônico"
        md_content += f"| **{c}** | {alert_txt} | {impact} |\n"
        
    md_content += """
---

## 6. Diagnóstico Unificado de Confluência das 8 Moedas

| Moeda | Estado de Confluência | Veredito Final | Potencial Cíclico |
| :---: | :--- | :--- | :--- |
"""
    for c in CURRENCIES:
        res = ccy_confluence_results[c]
        md_content += f"| **{c}** | {res['confluence_state']} | **{res['final_verdict']}** | `{res['trade_bias']}` |\n"
        
    md_content += """
---

## 7. Ranking Oficial de Confluência dos 28 Pares de Moedas

| # | Par | Ação Recomendada | Convicção | Total Score | Macro Diff | Op Diff | Tese Cíclica de Confluência |
| :-: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
"""
    for idx, p in enumerate(pair_rankings):
        md_content += f"| {idx+1} | **{p['pair']}** | **{p['rec']}** | {p['conviction']} | `{p['total_score']:+5.2f}` | {p['macro_diff']:+5.2f} | {p['op_diff']:+5.2f} | {p['thesis']} |\n"
        
    top_buy = pair_rankings[0]
    top_sell = pair_rankings[-1]
    
    md_content += f"""
---

## 8. Oportunidades Principais de Alta Convicção
* 🥇 **Melhor Par para COMPRA**: **{top_buy['pair']}** ({top_buy['rec']})
  * *Tese*: {top_buy['thesis']}
* 🥇 **Melhor Par para VENDA**: **{top_sell['pair']}** ({top_sell['rec']})
  * *Tese*: {top_sell['thesis']}
"""

    # Salvar relatorios
    daily_report_path = os.path.join(daily_folder, "analise_diaria.md")
    with open(daily_report_path, "w", encoding="utf-8") as f:
        f.write(md_content)
        
    central_report_path = os.path.join(LOG_DIR, f"{date_str}.md")
    with open(central_report_path, "w", encoding="utf-8") as f:
        f.write(md_content)
        
    # Atualizar INDEX.md
    index_path = os.path.join(LOG_DIR, "INDEX.md")
    index_entry = f"| {date_formatted} | `{date_str}` | **{top_buy['pair']} / {top_sell['pair']}** | COMPRA / VENDA | Sistema Multi-Agente (Macro + Operacional) | 🟢 Concluído | [{date_str}.md](file:///c:/Users/ryzen/Downloads/Antigravity/MFC/log_conhecimento/{date_str}.md) |\n"
    
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            idx_text = f.read()
        if date_str not in idx_text:
            idx_text += index_entry
            with open(index_path, "w", encoding="utf-8") as f:
                f.write(idx_text)
    else:
        with open(index_path, "w", encoding="utf-8") as f:
            f.write("# Índice de Análises Diárias e Avaliação de Resultados\n\n| Data | Pasta de Imagens | Ativo Principal | Direção | Tese Cíclica | Status do Resultado | Link da Análise |\n| :---: | :---: | :---: | :---: | :--- | :---: | :---: |\n" + index_entry)
            
    print(f"[{now.strftime('%H:%M:%S')}] Rotina Multi-Agente concluida com sucesso!")
    print(f"Relatório gerado em: {daily_report_path}")
    return True

if __name__ == "__main__":
    run_daily_routine()
