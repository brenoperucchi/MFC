"""
ROTINA DIÁRIA AUTOMATIZADA DE ANÁLISE CSS MULTI-TIMEFRAME (21:00 BRT)
Emite o Raio-X Institucional dos 5 Timeframes (MN1, W1, D1, H4, H1) de todas as moedas,
gera os relatórios de confluência e despacha tudo automaticamente para o Telegram.
"""

import os
import sys
import time
import textwrap
from datetime import datetime
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# Assegurar imports do diretório raiz
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from web.css_service import css_engine, CCY_COLORS, CCY_FLAGS, CURRENCIES, ALL_28_PAIRS
from web.telegram_service import send_telegram_photo, send_telegram_message, get_telegram_config

import html

# Setup Matplotlib styling
plt.style.use('dark_background')
plt.rcParams['font.family'] = 'sans-serif'
plt.rcParams['font.sans-serif'] = ['DejaVu Sans', 'Arial', 'Helvetica']
plt.rcParams['font.size'] = 9

LOG_DIR = os.path.join(BASE_DIR, "log_conhecimento")
os.makedirs(LOG_DIR, exist_ok=True)
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)


def clean_text_for_plot(text):
    """Remove tags HTML e substitui emojis por símbolos ASCII/Unicode padrão para o Matplotlib."""
    if not text:
        return ""
    t = str(text).replace("<br>", " | ").replace("<br/>", " | ").replace("<br />", " ")
    t = t.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
    t = t.replace("🚀", "▲▲").replace("🎢", "▼▼").replace("⚡", "*").replace("🔥", "*")
    t = t.replace("🇦🇺", "").replace("🇪🇺", "").replace("🇬🇧", "").replace("🇨🇭", "")
    t = t.replace("🇯🇵", "").replace("🇺🇸", "").replace("🇨🇦", "").replace("🇳🇿", "")
    t = t.replace("⚠️", "(!)")
    return t.strip()


def tg_esc(text):
    """Escapa HTML com segurança para as captions do Telegram."""
    if not text:
        return ""
    t = str(text).replace("<br>", " ").replace("<br/>", " ").replace("<br />", " ")
    t = t.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
    return html.escape(t.strip())



def render_currency_raio_x_image(ccy_data, all_charts, output_path, date_str=""):
    """
    Gera imagem em altíssima resolução (Dark Premium) do Raio-X Institucional da Moeda
    contendo o Resumo dos 5 Timeframes (Bolinhas/Foguetes) e as Tríades Analíticas.
    """
    ccy = ccy_data["symbol"]
    color = CCY_COLORS.get(ccy, "#00E5FF")
    tfs = ["MN1", "W1", "D1", "H4", "H1"]
    
    # Criar Figura Dark Institucional
    fig = plt.figure(figsize=(11.5, 15.5), facecolor="#080B11", dpi=130)
    
    # 1. Cabeçalho
    plt.figtext(0.04, 0.968, f"Raio-X Institucional: {ccy}", 
                fontsize=17, fontweight='bold', color='white')
    plt.figtext(0.04, 0.950, "Diagnóstico Cíclico e Tríade Analítica nos 5 Timeframes (MN1, W1, D1, H4, H1) — CSS PRO", 
                fontsize=9.5, color='#8899A6')
    time_label = date_str or datetime.now().strftime("%d/%m/%Y às %H:%M")
    plt.figtext(0.96, 0.968, time_label, 
                fontsize=10, color='#8899A6', horizontalalignment='right')

    # Linha divisória ciano
    line_ax = fig.add_axes([0.04, 0.942, 0.92, 0.002])
    line_ax.set_facecolor("#00E5FF")
    line_ax.axis('off')

    # 2. Card com Resumo dos 5 Timeframes (Bolinhas e Foguetes)
    card_ax = fig.add_axes([0.04, 0.865, 0.92, 0.068])
    card_ax.set_facecolor("#0E131E")
    for spine in card_ax.spines.values():
        spine.set_color((1.0, 1.0, 1.0, 0.12))
        spine.set_linewidth(1.0)
    card_ax.set_xticks([])
    card_ax.set_yticks([])

    # 5 Caixas dos Timeframes (Distribuídas em 100% da largura)
    box_w = 0.188
    gap = 0.010
    box_start_x = 0.012
    box_y = 0.12
    box_h = 0.76

    for idx, tf in enumerate(tfs):
        bx = box_start_x + idx * (box_w + gap)
        triad = ccy_data.get("triads", {}).get(tf, {})
        led = triad.get("led", "yellow")
        angle = triad.get("angle", "")
        angle_type = triad.get("angle_type", "")
        score_val = triad.get("score", 0.0)
        score_str = triad.get("score_str", f"{score_val:+.2f}")

        # Determinar Símbolo, Cor e Texto de Status
        if angle_type == "FOGUETE" or "Foguete" in angle or "▲▲" in angle:
            icon_sym = "▲▲ FOGUETE"
            pill_color = "#00E676"
            pill_bg = "#0B2618"
            pill_border = "#00E676"
        elif angle_type == "MONTANHA_RUSSA" or "Montanha-Russa" in angle or "▼▼" in angle:
            icon_sym = "▼▼ QUEDA FORTE"
            pill_color = "#FF1744"
            pill_bg = "#2B0B11"
            pill_border = "#FF1744"
        elif led == "green":
            icon_sym = "● FORÇA (UP)"
            pill_color = "#00E676"
            pill_bg = "#071C12"
            pill_border = "#00E676"
        elif led == "red":
            icon_sym = "● FRAQUEZA (DN)"
            pill_color = "#FF1744"
            pill_bg = "#21080D"
            pill_border = "#FF1744"
        else:
            icon_sym = "● DIVERGÊNCIA"
            pill_color = "#FFD700"
            pill_bg = "#211D07"
            pill_border = "#FFD700"

        # Desenhar Pill do TF
        pill_rect = plt.Rectangle((bx, box_y), box_w, box_h, transform=card_ax.transAxes,
                                  facecolor=pill_bg, edgecolor=pill_border, linewidth=1.1, clip_on=False)
        card_ax.add_patch(pill_rect)

        # Texto dentro do Pill: TF + Score e Símbolo
        card_ax.text(bx + 0.015, box_y + box_h * 0.58, f"{tf}: {score_str}", 
                     fontsize=9.2, fontweight='bold', color='#FFFFFF', transform=card_ax.transAxes)
        card_ax.text(bx + 0.015, box_y + box_h * 0.16, icon_sym, 
                     fontsize=8.0, fontweight='bold', color=pill_color, transform=card_ax.transAxes)

    # 3. Desenhar os 5 Timeframes (MN1, W1, D1, H4, H1)
    row_height = 0.145
    row_gap = 0.015
    start_top = 0.845

    for i, tf in enumerate(tfs):
        top_y = start_top - (i + 1) * row_height - (i * row_gap)
        triad = ccy_data.get("triads", {}).get(tf, {})
        chart_data = all_charts.get(tf, {})
        series = np.array(chart_data.get("series", {}).get(ccy, []))
        
        # Sub-gráfico (64% largura)
        ax_chart = fig.add_axes([0.04, top_y, 0.63, row_height])
        ax_chart.set_facecolor("#05070A")
        for spine in ax_chart.spines.values():
            spine.set_color((1.0, 1.0, 1.0, 0.08))
        ax_chart.grid(True, linestyle=":", alpha=0.25, color="#555555")

        # Título do Timeframe e Score
        ax_chart.text(0.02, 0.88, f"{tf} {ccy}", fontsize=10.5, fontweight='bold', color='white', transform=ax_chart.transAxes)
        score_val = triad.get("score", 0.0)
        score_color = "#00E676" if score_val > 0 else "#FF1744"
        score_hdr = f"{triad.get('score_str', '')} {triad.get('dir', '')} - {clean_text_for_plot(triad.get('angle', ''))}"
        ax_chart.text(0.98, 0.88, score_hdr, fontsize=9.5, fontweight='bold', color=score_color,
                      horizontalalignment='right', transform=ax_chart.transAxes)

        if len(series) > 1:
            s_min = series.min()
            s_max = series.max()
            y_min = min(s_min - 0.08, -0.26)
            y_max = max(s_max + 0.08, 0.26)
            ax_chart.set_ylim(y_min, y_max)

            # Linhas de Parada e Nível 0.00
            ax_chart.axhline(0.20, color="#00E676", linestyle="--", linewidth=1.0, alpha=0.65)
            ax_chart.axhline(0.00, color="#00E5FF", linestyle=":", linewidth=1.0, alpha=0.55)
            ax_chart.axhline(-0.20, color="#FF3333", linestyle="--", linewidth=1.0, alpha=0.65)

            x = np.arange(len(series))
            ax_chart.plot(x, series, color=color, linewidth=2.3)
            
            # Ponto Final
            last_x = len(series) - 1
            last_y = series[-1]
            ax_chart.plot(last_x, last_y, 'o', color=color, markersize=5)
            
            # Indicadores de Níveis no eixo Y à direita
            ax_chart.text(last_x + 1, 0.20, "+0.20", color="#00E676", fontsize=7.5, verticalalignment='center')
            ax_chart.text(last_x + 1, 0.00, " 0.00", color="#00E5FF", fontsize=7.5, verticalalignment='center')
            ax_chart.text(last_x + 1, -0.20, "-0.20", color="#FF3333", fontsize=7.5, verticalalignment='center')
            ax_chart.set_xlim(0, len(series) + 8)
        
        ax_chart.set_xticks([])
        ax_chart.set_yticks([])

        # Sub-painel da Tríade Analítica (28% largura)
        ax_info = fig.add_axes([0.68, top_y, 0.28, row_height])
        ax_info.set_facecolor("#05070A")
        for spine in ax_info.spines.values():
            spine.set_color((1.0, 1.0, 1.0, 0.08))
        ax_info.set_xticks([])
        ax_info.set_yticks([])

        # Itens da Tríade
        y_text = 0.86
        steps = [
            ("1. Região no Box", clean_text_for_plot(triad.get("region", "-")), "#FFFFFF"),
            ("2. Ciclo Atual", clean_text_for_plot(triad.get("current_cycle", "-")), "#00E5FF"),
            ("3. Ciclo Devendo", clean_text_for_plot(triad.get("owing_cycle", "-")), "#FFD600"),
            ("4. Angulação / Veredito", clean_text_for_plot(triad.get("angle", "-")), "#FFFFFF"),
        ]
        for label, val, c_val in steps:
            ax_info.text(0.05, y_text, label.upper(), fontsize=6.8, fontweight='bold', color="#7F8C8D", transform=ax_info.transAxes)
            y_text -= 0.115
            wrapped_val = textwrap.shorten(val, width=38, placeholder="...")
            ax_info.text(0.05, y_text, wrapped_val, fontsize=7.8, fontweight='bold', color=c_val, transform=ax_info.transAxes)
            y_text -= 0.135

    # 4. Rodapé
    plt.figtext(0.5, 0.015, "CSS PRO INSTITUTIONAL PLATFORM — ROTINA DIÁRIA 21H", 
                fontsize=8.5, color='#555555', horizontalalignment='center')

    plt.savefig(output_path, dpi=130, facecolor=fig.get_facecolor(), bbox_inches='tight')
    plt.close()
    return output_path


def render_all_currencies_h1_image(h1_data, output_path, date_str=""):
    """Gera o painel geral das 8 moedas no H1 com ranking lateral."""
    fig, (ax_main, ax_rank) = plt.subplots(1, 2, figsize=(18, 8), gridspec_kw={'width_ratios': [4, 1]}, facecolor="#080B11", dpi=120)
    fig.suptitle(f"CSS Indicator — Todas as 8 Moedas (H1) | {date_str}", fontsize=14, fontweight='bold', color='white', y=0.98)
    
    ax_main.set_facecolor("#05070A")
    ax_main.grid(True, linestyle=":", alpha=0.3, color="#555555")
    
    series_dict = h1_data.get("series", {})
    times = h1_data.get("times", [])
    
    if not series_dict:
        plt.close()
        return output_path

    all_min = min(np.array(series_dict[c]).min() for c in CURRENCIES if c in series_dict)
    all_max = max(np.array(series_dict[c]).max() for c in CURRENCIES if c in series_dict)
    y_min = min(all_min - 0.25, -1.5)
    y_max = max(all_max + 0.25, 1.5)
    ax_main.set_ylim(y_min, y_max)
    
    ax_main.axhline(0.0, color="#00E5FF", linestyle=":", linewidth=1.0, alpha=0.6)
    ax_main.axhline(0.20, color="#00E676", linestyle="--", linewidth=1.4, alpha=0.85)
    ax_main.axhline(-0.20, color="#FF3333", linestyle="--", linewidth=1.4, alpha=0.85)
    
    x_indices = np.arange(len(times)) if times else np.arange(len(next(iter(series_dict.values()))))
    for c in CURRENCIES:
        if c in series_dict:
            ax_main.plot(x_indices, series_dict[c], color=CCY_COLORS.get(c, "#FFF"), linewidth=2.1, label=c)
        
    ax_main.set_title("Currency Slope Strength — H1 (Últimas Barras / Auto-Escala)", fontsize=11, color="#E0E0E0", pad=6)
    if times and len(times) > 5:
        tick_pos = np.linspace(0, len(times)-1, 10, dtype=int)
        ax_main.set_xticks(tick_pos)
        ax_main.set_xticklabels([times[p] if isinstance(times[p], str) else times[p].strftime('%d/%m %H:%M') for p in tick_pos], color='#AAAAAA', fontsize=8.5)
    
    ax_main.legend(loc="upper left", ncol=4, framealpha=0.3, facecolor="#222222", edgecolor="#555555", labelcolor="white")
    
    # Painel Lateral de Ranking H1
    ax_rank.set_facecolor("#0E131E")
    ax_rank.axis('off')
    
    sorted_ccys = sorted(CURRENCIES, key=lambda c: series_dict[c][-1] if c in series_dict else 0, reverse=True)
    
    ax_rank.text(0.05, 0.96, "RANKING H1 (Operacional)", fontsize=11, fontweight='bold', color='white', transform=ax_rank.transAxes)
    ax_rank.text(0.05, 0.92, "Moeda    Score    Dir", fontsize=9, color='#AAAAAA', transform=ax_rank.transAxes)
    ax_rank.plot([0.05, 0.95], [0.905, 0.905], color='#555555', transform=ax_rank.transAxes)
    
    y_pos = 0.865
    for c in sorted_ccys:
        s = series_dict[c]
        val = s[-1]
        diff = s[-1] - s[-2] if len(s) > 1 else 0
        dir_t = "▲ UP" if diff > 0 else "▼ DN"
        c_color = CCY_COLORS.get(c, "#FFF")
        row_txt = f"{c:<4}  {val:+5.2f}  ({dir_t})"
        ax_rank.text(0.05, y_pos, row_txt, fontsize=10, fontweight='bold', color=c_color, transform=ax_rank.transAxes)
        y_pos -= 0.05
        
    plt.tight_layout(rect=[0, 0.03, 1, 0.95])
    plt.savefig(output_path, dpi=120)
    plt.close()
    return output_path


def run_daily_routine():
    """
    Executa a rotina diária das 21h:
    1. Recalcula o CSS e Confluência Multi-Timeframe.
    2. Gera os 8 Raio-X dos 5 Timeframes em alta resolução.
    3. Despacha o Briefing Executivo e as fotos do Raio-X diretamente para o Telegram.
    4. Salva histórico local em Markdown e relatórios arquivados.
    """
    now = datetime.now()
    date_str = now.strftime("%Y%m%d")
    date_formatted = now.strftime("%d/%m/%Y")
    time_formatted = now.strftime("%H:%M:%S")
    
    daily_folder = os.path.join(REPORTS_DIR, date_str)
    os.makedirs(daily_folder, exist_ok=True)
    
    print(f"[{time_formatted}] ========================================================")
    print(f"[{time_formatted}] INICIANDO ROTINA DIÁRIA DAS 21H — RAIO-X 5-TF & TELEGRAM")
    print(f"[{time_formatted}] Data: {date_formatted}")
    print(f"[{time_formatted}] ========================================================")
    
    # 1. Obter Dados Oficiais do Motor CSS
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Atualizando dados de cálculo CSS do MT5...")
    data = css_engine.update_data(force=True, mode="standard")
    
    currencies = data.get("currencies", [])
    charts = data.get("charts", {})
    pairs = data.get("pairs", [])
    timestamp = data.get("timestamp", f"{date_formatted} às {time_formatted}")
    
    if not currencies:
        print("[!] Erro: Nenhum dado de moeda retornado pelo motor CSS.")
        return False

    # 2. Gerar as Imagens dos 8 Raio-X 5-TF
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Gerando imagens em alta definição dos 8 Raio-X 5-TF...")
    raio_x_images = {}
    for ccy_item in currencies:
        ccy = ccy_item["symbol"]
        out_path = os.path.join(daily_folder, f"{ccy}_RaioX_5TF.png")
        render_currency_raio_x_image(ccy_item, charts, out_path, date_str=timestamp)
        raio_x_images[ccy] = out_path

    # Gerar Painel Geral H1
    h1_chart_path = os.path.join(daily_folder, "CSS_AllCurrencies_H1.png")
    render_all_currencies_h1_image(charts.get("H1", {}), h1_chart_path, date_str=timestamp)

    # 3. Disparar Relatório Completo para o Telegram
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Despachando Raio-X e Briefing para o Telegram...")
    tg_cfg = get_telegram_config()
    
    if tg_cfg.get("enabled", True):
        # 3.1 Mensagem de Abertura Executiva (Briefing 21h)
        top_buys = [p for p in pairs if "BUY" in p.get("recommendation", "") or "COMPRA" in p.get("recommendation", "")][:3]
        top_sells = [p for p in pairs if "SELL" in p.get("recommendation", "") or "VENDA" in p.get("recommendation", "")][:3]
        
        # Resumo das Moedas
        ccy_summary_lines = []
        for c in currencies:
            flag = c.get("flag", "")
            sym = c.get("symbol", "")
            state = c.get("confluence_state", "")
            badge = c.get("signal_badge", "")
            div = " ⚠️ <i>(Divergência)</i>" if c.get("has_divergence") else ""
            ccy_summary_lines.append(f"• <b>{flag} {sym}:</b> {badge} — {state}{div}")

        ccy_summary_text = "\n".join(ccy_summary_lines)

        pairs_summary_lines = []
        alicate_pairs = [p for p in pairs if p.get('is_alicate')][:3]
        if alicate_pairs:
            pairs_summary_lines.append("✂️ <b>OPERAÇÕES ALICATE (TRANSIÇÃO & EXTREMOS):</b>")
            for p in alicate_pairs:
                pairs_summary_lines.append(f"  • <b>{p['pair']}:</b> {p.get('recommendation')} | {p.get('conviction')}")

        if top_buys:
            pairs_summary_lines.append("🟢 <b>Melhores Oportunidades de COMPRA:</b>")
            for p in top_buys:
                pairs_summary_lines.append(f"  • <b>{p['pair']}:</b> {p.get('recommendation')} | Convicção: {p.get('conviction')}")
        if top_sells:
            pairs_summary_lines.append("🔴 <b>Melhores Oportunidades de VENDA:</b>")
            for p in top_sells:
                pairs_summary_lines.append(f"  • <b>{p['pair']}:</b> {p.get('recommendation')} | Convicção: {p.get('conviction')}")

        pairs_summary_text = "\n".join(pairs_summary_lines) if pairs_summary_lines else "• Sem confluências duplas extremas no momento."

        briefing_msg = f"""🏛️ <b>RELATÓRIO INSTITUCIONAL RAIO-X 5-TF (21:00 BRT)</b>
📅 <b>Data:</b> {date_formatted} | ⏱️ <b>Sessão:</b> Madrugada (21h ➔ 08h)
━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 <b>ESTADO DE CONFLUÊNCIA DAS 8 MOEDAS:</b>
{ccy_summary_text}

🎯 <b>RADAR DOS 28 PARES FOREX:</b>
{pairs_summary_text}

━━━━━━━━━━━━━━━━━━━━━━━━━━
<i>Abaixo seguem os 8 Raio-X dos 5 Timeframes (MN1, W1, D1, H4, H1) com as Tríades Analíticas detalhadas.</i>"""

        send_telegram_message(briefing_msg)
        time.sleep(1.0)

        # 3.2 Enviar Foto do Gráfico Geral H1
        if os.path.exists(h1_chart_path):
            with open(h1_chart_path, "rb") as img_f:
                send_telegram_photo(
                    img_f.read(),
                    filename="CSS_AllCurrencies_H1.png",
                    caption=f"📈 <b>CSS PRO — Visão Geral das 8 Moedas no H1</b>\nRanking Operacional e Inversão de Fluxo Institucional ({date_formatted})"
                )
            time.sleep(1.2)

        # 3.3 Enviar as 8 Fotos de Raio-X com Captions Ricas
        for ccy_item in currencies:
            ccy = ccy_item["symbol"]
            flag = ccy_item.get("flag", "")
            img_file = raio_x_images.get(ccy)
            
            if img_file and os.path.exists(img_file):
                triads = ccy_item.get("triads", {})
                mn = triads.get("MN1", {})
                w1 = triads.get("W1", {})
                d1 = triads.get("D1", {})
                h4 = triads.get("H4", {})
                h1 = triads.get("H1", {})
                
                div_text = f"\n⚠️ <b>Divergência:</b> {tg_esc(ccy_item.get('divergence_alert'))}" if ccy_item.get("has_divergence") else ""
                
                caption = f"""📊 <b>RAIO-X INSTITUCIONAL: {flag} {ccy} ({tg_esc(ccy_item.get('trade_bias', 'NEUTRO'))})</b>
🎯 <b>Confluência:</b> {tg_esc(ccy_item.get('confluence_state', ''))}
🔮 <b>Veredito:</b> {tg_esc(ccy_item.get('final_verdict', ''))}{div_text}

📈 <b>Tríades Analíticas (5-TF):</b>
• <b>MN1:</b> {tg_esc(mn.get('score_str', ''))} | {tg_esc(mn.get('owing_cycle', ''))}
• <b>W1:</b> {tg_esc(w1.get('score_str', ''))} | {tg_esc(w1.get('owing_cycle', ''))}
• <b>D1:</b> {tg_esc(d1.get('score_str', ''))} | {tg_esc(d1.get('owing_cycle', ''))}
• <b>H4:</b> {tg_esc(h4.get('score_str', ''))} | {tg_esc(h4.get('owing_cycle', ''))}
• <b>H1:</b> {tg_esc(h1.get('score_str', ''))} | {tg_esc(h1.get('owing_cycle', ''))}

🌐 <i>https://css-pro-mfc.web.app/</i>"""

                with open(img_file, "rb") as img_f:
                    send_telegram_photo(img_f.read(), filename=f"{ccy}_RaioX_5TF.png", caption=caption)
                
                time.sleep(0.8)

        print(f"[{datetime.now().strftime('%H:%M:%S')}] Todos os Raio-X e Briefings foram entregues no Telegram com sucesso!")
    else:
        print("[*] Envio ao Telegram desativado em data/telegram_config.json.")

    # 3.1 REMOVIDO (achado em revisão, condição de corrida real): esta rotina
    # gravava aqui o mesmo arquivo de sinais que scripts/scheduler_daemon.py
    # ::execute_phase_2102() também grava, de forma independente, exatamente
    # às 21:02 BRT. Enquanto execute_phase_2100 (esta rotina) rodava SÍNCRONO
    # dentro do loop do scheduler, a ordem entre as duas escritas era
    # garantida (esta sempre terminava antes do scheduler sequer checar
    # 21:02). Depois da correção que passou a rodar esta rotina numa THREAD
    # em segundo plano (pra não travar o relógio do scheduler — ver
    # execute_phase_2100 em scripts/scheduler_daemon.py), as duas escritas
    # viraram genuinamente concorrentes: como esta rotina só chega até aqui
    # DEPOIS de gerar 9 gráficos e despachar Telegram (pode levar minutos), a
    # escrita ATRASADA e OBSOLETA daqui podia sobrescrever o sinal FRESCO das
    # 21:02, e o sistema abriria a cesta com dados de minutos atrás. A
    # gravação de sinal oficial agora vive só em execute_phase_2102 — esta
    # rotina fica só com relatório/dashboard/Telegram, que não decidem ordem.
    print(f"[{datetime.now().strftime('%H:%M:%S')}] (Gravação do sinal oficial fica a cargo de "
          f"scheduler_daemon.py::execute_phase_2102, às 21:02 BRT — não duplicada aqui.)")

    # 4. Salvar Relatório em Markdown e Histórico Local
    md_content = f"""# Relatório Diário de Confluência Multi-Agente CSS — {date_formatted}

## 1. Referência dos Dados
* **Data da Execução**: {timestamp}
* **Diretório das Imagens**: [`c:\\Users\\ryzen\\Downloads\\Antigravity\\MFC\\reports\\{date_str}`](file:///c:/Users/ryzen/Downloads/Antigravity/MFC/reports/{date_str})
* **Dashboards Salvos (Raio-X 5-TF)**:
"""
    for c in currencies:
        sym = c["symbol"]
        md_content += f"  * [`{sym}_RaioX_5TF.png`](file:///c:/Users/ryzen/Downloads/Antigravity/MFC/reports/{date_str}/{sym}_RaioX_5TF.png)\n"
    md_content += f"  * [`CSS_AllCurrencies_H1.png`](file:///c:/Users/ryzen/Downloads/Antigravity/MFC/reports/{date_str}/CSS_AllCurrencies_H1.png)\n\n---\n\n"

    md_content += "## 2. Análise Estruturada por Moeda (Tríade Analítica nos 5 Timeframes)\n"
    for c in currencies:
        sym = c["symbol"]
        triads = c.get("triads", {})
        md_content += f"""
### 🔹 Moeda: **{c.get('flag', '')} {sym}** ({c.get('trade_bias', 'NEUTRO')})
* **Estado de Confluência**: `{c.get('confluence_state', '')}` ➔ **{c.get('final_verdict', '')}**
* **Alerta de Divergência**: {c.get('divergence_alert', 'Nenhuma divergência estrutural')}

| Timeframe | 1. Região no Box | 2. Ciclo Atual | 3. Ciclo Devendo | 4. Score & Angulação |
| :---: | :--- | :--- | :--- | :--- |
"""
        for tf in ["MN1", "W1", "D1", "H4", "H1"]:
            tr = triads.get(tf, {})
            md_content += f"| **{tf}** | {tr.get('region', '-')} | {tr.get('current_cycle', '-')} | {tr.get('owing_cycle', '-')} | `{tr.get('score_str', '')}` ({tr.get('angle', '-')}) |\n"

    md_content += "\n---\n\n## 3. Radar dos 28 Pares Forex\n\n"
    md_content += "| # | Par | Ação Recomendada | Convicção | Total Score | Macro Diff | Op Diff | Tese Cíclica de Confluência |\n"
    md_content += "| :-: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |\n"
    for idx, p in enumerate(pairs):
        md_content += f"| {idx+1} | **{p.get('pair')}** | **{p.get('recommendation')}** | {p.get('conviction')} | `{p.get('total_score', 0):+5.2f}` | {p.get('macro_diff', 0):+5.2f} | {p.get('op_diff', 0):+5.2f} | {p.get('thesis', '')} |\n"

    daily_report_path = os.path.join(daily_folder, "analise_diaria.md")
    with open(daily_report_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    central_report_path = os.path.join(LOG_DIR, f"{date_str}.md")
    with open(central_report_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    # Atualizar INDEX.md
    index_path = os.path.join(LOG_DIR, "INDEX.md")
    best_buy = pairs[0].get("pair", "") if pairs else "EURUSD"
    best_sell = pairs[-1].get("pair", "") if pairs else "USDJPY"
    index_entry = f"| {date_formatted} | `{date_str}` | **{best_buy} / {best_sell}** | RAIO-X 5-TF | Raio-X dos 5 TFs & Despacho Telegram | 🟢 Concluído | [{date_str}.md](file:///c:/Users/ryzen/Downloads/Antigravity/MFC/log_conhecimento/{date_str}.md) |\n"

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

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Relatório arquivado com sucesso em: {daily_report_path}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Rotina Diária das 21h concluída com 100% de êxito!")
    return True


if __name__ == "__main__":
    run_daily_routine()
