"""
MOTOR DE ALERTAS DE CRUZAMENTO DE SCORE CSS (SERVER-SIDE TELEGRAM DISPATCHER)
Funcionalidades:
1. Execução contínua no servidor em background (sem depender do navegador aberto).
2. Detecção precisa de cruzamentos nos timeframes H1 e H4 para os 28 pares Forex.
3. Filtro de Estado Anti-Spam: Garante que cada cruzamento inédito seja notificado exatamente 1 única vez.
4. Formatação Institucional em HTML com flags, scores, direção e tese analítica.
"""

import os
import sys
import json
import time
from datetime import datetime

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from web.css_service import (
    css_engine, detect_currency_crossovers, CCY_FLAGS, CCY_COLORS, ALL_28_PAIRS
)
from web.telegram_service import send_telegram_message, get_telegram_config

DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
SENT_EVENTS_FILE = os.path.join(DATA_DIR, "crossover_sent_events.json")


def load_sent_events():
    """Carrega o histórico de cruzamentos já notificados para evitar envios duplicados."""
    if os.path.exists(SENT_EVENTS_FILE):
        try:
            with open(SENT_EVENTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_sent_events(events_dict):
    """Persiste o histórico de eventos notificados com expiração de 48h."""
    now_ts = time.time()
    cutoff_ts = now_ts - (48 * 3600)
    cleaned = {k: v for k, v in events_dict.items() if v.get("sent_ts", now_ts) > cutoff_ts}
    try:
        with open(SENT_EVENTS_FILE, "w", encoding="utf-8") as f:
            json.dump(cleaned, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[-] Erro ao salvar eventos de cruzamento: {e}")


def format_crossover_message(cross):
    """Formata o card de alerta de cruzamento em HTML para o Telegram."""
    pair = cross["pair"]
    base = cross["base"]
    quote = cross["quote"]
    base_flag = CCY_FLAGS.get(base, "🏳️")
    quote_flag = CCY_FLAGS.get(quote, "🏳️")
    tf = cross["timeframe"]
    dir_label = "🟢 COMPRA INSTITUCIONAL" if cross["direction"] == "BUY" else "🔴 VENDA INSTITUCIONAL"
    spread = cross.get("spread", 0.0)
    
    msg = (
        f"⚡ <b>ALERTA DE CRUZAMENTO CSS — {tf}</b>\n\n"
        f"🎯 <b>Par:</b> {base_flag} <b>{pair}</b> {quote_flag}\n"
        f"🧭 <b>Direção:</b> <b>{dir_label}</b>\n"
        f"📊 <b>Spread de Força:</b> <code>{spread:+.3f}</code>\n"
        f"💡 <b>Tese:</b> {cross.get('action_thesis', 'Divergência de Força Relativa Confirmada')}\n"
        f"⏰ <b>Horário:</b> <code>{cross.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M'))}</code>\n\n"
        f"🌐 <i>Monitoramento Contínuo — CSS PRO Institutional</i>"
    )
    return msg


def scan_and_dispatch_crossovers(dry_run=False):
    """
    Varre os gráficos dos timeframes H1 e H4, identifica cruzamentos recentes (<= 2 barras),
    filtra os já enviados e despacha alertas inéditos para o Telegram.
    """
    cfg = get_telegram_config()
    if not cfg.get("enabled", True) and not dry_run:
        print("[*] Alertas Telegram desativados na configuração.")
        return []

    data = css_engine.update_data(force=False, mode="standard")
    if not data or "charts" not in data:
        return []

    crossovers_data = detect_currency_crossovers(data["charts"])
    sent_events = load_sent_events()
    dispatched_alerts = []

    for tf in ["H1", "H4"]:
        tf_info = crossovers_data.get("timeframes", {}).get(tf, {})
        crosses = tf_info.get("crossovers", [])

        for c in crosses:
            # Filtrar apenas cruzamentos recentes (<= 2 barras de idade)
            if c.get("bars_ago", 99) > 2:
                continue

            event_id = f"{c['pair']}_{tf}_{c['direction']}_{c.get('timestamp', '')}"
            if event_id in sent_events:
                continue # Já notificado anteriormente

            # Novo cruzamento confirmado!
            msg = format_crossover_message(c)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [NOVO CRUZAMENTO] {c['pair']} ({tf}) -> {c['direction']}")

            if not dry_run:
                success = send_telegram_message(msg)
                if success:
                    sent_events[event_id] = {
                        "pair": c["pair"],
                        "tf": tf,
                        "direction": c["direction"],
                        "timestamp": c.get("timestamp"),
                        "sent_ts": time.time()
                    }
                    dispatched_alerts.append(c)
            else:
                dispatched_alerts.append(c)

    if not dry_run and dispatched_alerts:
        save_sent_events(sent_events)

    return dispatched_alerts


if __name__ == "__main__":
    print("===================================================================")
    print("  SCANNER DE CRUZAMENTOS CSS — EXECUÇÃO DE TESTE                 ")
    print("===================================================================")
    alerts = scan_and_dispatch_crossovers(dry_run=True)
    print(f"[+] Total de Cruzamentos Recentes Detectados: {len(alerts)}")
    for a in alerts:
        print(f"  • {a['pair']} ({a['timeframe']}) -> {a['direction']} | {a['action_thesis']}")
