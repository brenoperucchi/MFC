"""
MOTOR DE AUDITORIA REAL DE PORTFÓLIOS MULTI-MOEDA MT5 (21h ➔ 08h)
100% Auditado — Leitura direta de Deals e Posições do MetaTrader 5 por Magic Number (801001 a 801008).
Sem dados sintéticos ou mocks aleatórios.
"""

import os
import sys
import json
import time
import tempfile
from datetime import datetime, timedelta
import numpy as np

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from web.css_service import (
    ALL_28_PAIRS, CURRENCIES, CCY_FLAGS, CCY_COLORS,
    MT5_AVAILABLE, mt5, MT5_PATH,
    to_broker_symbol
)

# MAGIC NUMBERS DEDICADOS PARA CADA PORTFÓLIO (801001 a 801008)
PORTFOLIO_MAGICS = {
    "USD": 801001,
    "EUR": 801002,
    "GBP": 801003,
    "CHF": 801004,
    "JPY": 801005,
    "AUD": 801006,
    "CAD": 801007,
    "NZD": 801008
}
ALL_PORTFOLIO_MAGICS = set(PORTFOLIO_MAGICS.values())

DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
JOURNAL_FILE = os.path.join(DATA_DIR, "portfolio_audit_journal.json")
SIGNALS_FILE = os.path.join(DATA_DIR, "portfolio_signals_live.json")
ARCHIVE_DIR = os.path.join(DATA_DIR, "signals_archive")
BACKUPS_DIR = os.path.join(DATA_DIR, "backups")
os.makedirs(ARCHIVE_DIR, exist_ok=True)
os.makedirs(BACKUPS_DIR, exist_ok=True)


def ensure_mt5():
    """Nunca inicializa 'com o que estiver disponível' (mt5.initialize() sem
    path) quando MT5_PATH não resolve pra um terminal64.exe real — essa
    máquina roda vários terminais MT5 pra estratégias/contas diferentes
    (achado ALTO em revisão). Crítico aqui em particular: real_audit_engine
    é instanciado no IMPORT do módulo (`real_audit_engine =
    RealPortfolioAuditEngine()` mais abaixo), então esta função roda antes
    de qualquer outra checagem de MT5 no processo — se ela anexar num
    terminal errado primeiro, ensure_mt5()/connect_mt5() dos outros módulos
    (já corrigidos) veem 'já conectado' e nunca chegam a validar nada.
    Mesma trava em agents/portfolio_executor.py::ensure_mt5() e
    web/css_service.py::connect_mt5() — MT5_PATH é a mesma variável nos
    três módulos."""
    if not MT5_AVAILABLE:
        return False
    try:
        if mt5.terminal_info() is not None:
            return True
    except Exception:
        pass
    if not MT5_PATH or not os.path.isfile(MT5_PATH):
        return False
    return mt5.initialize(path=MT5_PATH)


def get_broker_gmt_offset():
    """
    Detecta dinamicamente a diferença de fuso horário (GMT Offset) do servidor do broker em relação ao UTC.
    Suporta mercado ao vivo e análise da barra de fechamento de sexta-feira no final de semana.
    """
    if not ensure_mt5():
        return 3 # Tickmill / MetaQuotes padrão (GMT+3 no Verão / GMT+2 no Inverno)
    try:
        rates = mt5.copy_rates_from_pos(to_broker_symbol("EURUSD"), mt5.TIMEFRAME_M1, 0, 1)
        if rates is not None and len(rates) > 0:
            bar_ts = rates[0]['time']
            bar_dt = datetime.fromtimestamp(bar_ts)
            # Durante fechamento de final de semana
            if bar_dt.weekday() == 4:
                # O fechamento de sexta ocorre às 21:00 UTC (Verão) ou 22:00 UTC (Inverno)
                offset = bar_dt.hour - 21
                if 0 <= offset <= 5:
                    return int(offset)
            # Durante mercado aberto
            cur_utc_ts = datetime.utcnow().timestamp()
            diff_secs = bar_ts - cur_utc_ts
            if abs(diff_secs) < 3600 * 5:
                return int(round(diff_secs / 3600.0))
    except Exception:
        pass
    return 3


def atomic_json_save(filepath, data):
    """Gravação atômica segura via arquivo temporário para evitar corrupção de dados."""
    dirname = os.path.dirname(filepath)
    os.makedirs(dirname, exist_ok=True)
    prefix = os.path.basename(filepath) + ".tmp"
    with tempfile.NamedTemporaryFile("w", dir=dirname, prefix=prefix, delete=False, encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        temp_name = f.name
    os.replace(temp_name, filepath)


class RealPortfolioAuditEngine:
    def __init__(self):
        self.journal = self._load_journal()
        self.sync_mt5_deals()

    def _load_journal(self):
        """Carrega o journal de auditoria oficial."""
        if os.path.exists(JOURNAL_FILE):
            try:
                with open(JOURNAL_FILE, "r", encoding="utf-8") as f:
                    j_data = json.load(f)
                    if "portfolio_equity_curves" not in j_data:
                        j_data["portfolio_equity_curves"] = {
                            c: [{"date": "Início", "equity": 0.0, "pnl": 0.0}] for c in CURRENCIES
                        }
                    return j_data
            except Exception as e:
                print(f"[!] Erro ao carregar journal: {e}. Inicializando novo journal limpo.")
                
        # Estrutura inicial padrão 100% limpa (Sem mocks!)
        empty_journal = {
            "last_audit_sync": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "summary": {
                "total_pnl_usd": 0.0,
                "total_pips": 0.0,
                "total_sessions": 0,
                "active_sessions": 0,
                "neutral_sessions": 0,
                "win_sessions": 0,
                "loss_sessions": 0,
                "win_rate": 0.0,
                "profit_factor": 1.0,
                "best_currency": "-",
                "worst_currency": "-",
                "currency_pnl": {c: 0.0 for c in CURRENCIES},
                "portfolio_stats": {
                    c: {
                        "currency": c,
                        "flag": CCY_FLAGS.get(c, ""),
                        "magic": PORTFOLIO_MAGICS.get(c, 0),
                        "total_pnl_usd": 0.0,
                        "total_pips": 0.0,
                        "sessions_count": 0,
                        "win_sessions": 0,
                        "loss_sessions": 0,
                        "win_rate": 0.0
                    } for c in CURRENCIES
                }
            },
            "equity_curve": [{"date": "Início", "equity": 0.0, "pnl": 0.0}],
            "portfolio_equity_curves": {
                c: [{"date": "Início", "equity": 0.0, "pnl": 0.0}] for c in CURRENCIES
            },
            "sessions": []
        }
        self._save_journal(empty_journal)
        return empty_journal

    def _save_journal(self, data=None):
        if data is None:
            data = self.journal
        atomic_json_save(JOURNAL_FILE, data)
        # Backup diário automático
        today_str = datetime.now().strftime("%Y-%m-%d")
        backup_file = os.path.join(BACKUPS_DIR, f"journal_backup_{today_str}.json")
        try:
            if not os.path.exists(backup_file):
                atomic_json_save(backup_file, data)
        except Exception:
            pass

    def get_today_signals(self):
        """Retorna as decisões do dia gravadas pontualmente às 21:02 BRT."""
        if os.path.exists(SIGNALS_FILE):
            try:
                with open(SIGNALS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
                
        # Se não houver arquivo, calcular snapshot atual de decisões
        from agents.portfolio_executor import generate_and_save_daily_signals
        return generate_and_save_daily_signals()

    def sync_mt5_deals(self, days_back=60):
        """
        Consulta o histórico real de deals do MT5 filtrando pelos Magic Numbers (801001-801008),
        agrupa por sessões noturnas e consolida os resultados no journal de auditoria.
        Trava de segurança: Limite rígido entre 1 e 180 dias.
        """
        if not ensure_mt5():
            return self.journal

        safe_days = min(max(1, int(days_back)), 180)
        from_date = datetime.now() - timedelta(days=safe_days)
        to_date = datetime.now() + timedelta(days=1)
        
        deals = mt5.history_deals_get(from_date, to_date)
        if deals is None or len(deals) == 0:
            return self.journal

        # Filtrar apenas deals pertencentes aos nossos 8 Magic Numbers
        magic_to_ccy = {v: k for k, v in PORTFOLIO_MAGICS.items()}
        portfolio_deals = [d for d in deals if d.magic in magic_to_ccy and d.entry == mt5.DEAL_ENTRY_OUT]

        if not portfolio_deals:
            return self.journal

        # Agrupar deals por data da sessão noturna (Deals fechadas entre 21h e 09h pertencem à sessão daquela noite)
        sessions_map = {}
        for d in portfolio_deals:
            deal_time = datetime.fromtimestamp(d.time)
            # Se fechou antes das 12h, a abertura foi no dia anterior às 21h
            if deal_time.hour < 12:
                session_date = (deal_time - timedelta(days=1)).strftime("%Y-%m-%d")
            else:
                session_date = deal_time.strftime("%Y-%m-%d")

            if session_date not in sessions_map:
                sessions_map[session_date] = []
            sessions_map[session_date].append(d)

        # Construir sessões auditadas
        updated_sessions = []
        equity = 0.0
        equity_curve = [{"date": "Início", "equity": 0.0, "pnl": 0.0}]
        currency_pnl_map = {c: 0.0 for c in CURRENCIES}
        port_stats = {
            c: {
                "currency": c,
                "flag": CCY_FLAGS.get(c, ""),
                "magic": PORTFOLIO_MAGICS.get(c, 0),
                "total_pnl_usd": 0.0,
                "total_pips": 0.0,
                "sessions_count": 0,
                "win_sessions": 0,
                "loss_sessions": 0,
                "win_rate": 0.0
            } for c in CURRENCIES
        }

        # Carregar arquivos de sinais arquivados para vincular a decisão tomada às 21:02
        today_signals = self.get_today_signals()

        for sess_date in sorted(sessions_map.keys(), reverse=True):
            sess_deals = sessions_map[sess_date]
            
            # Agrupar por moeda
            ccy_deals_map = {}
            for d in sess_deals:
                ccy = magic_to_ccy.get(d.magic)
                if ccy not in ccy_deals_map:
                    ccy_deals_map[ccy] = []
                ccy_deals_map[ccy].append(d)

            sess_portfolios = []
            sess_total_pnl = 0.0
            sess_total_pips = 0.0

            for ccy, c_deals in ccy_deals_map.items():
                p_pnl = round(sum(d.profit + d.swap + d.commission for d in c_deals), 2)
                p_pairs = []
                p_pips_total = 0.0

                for d in c_deals:
                    sym = d.symbol
                    action = "BUY" if d.type == mt5.DEAL_TYPE_BUY else "SELL"
                    # Cálculo de pips aproximado
                    is_jpy = "JPY" in sym
                    pip_unit = 0.01 if is_jpy else 0.0001
                    p_pips = round(d.profit / (d.volume * 10) if d.volume > 0 else 0.0, 1)
                    p_pips_total += p_pips
                    
                    p_pairs.append({
                        "pair": sym,
                        "action": action,
                        "lot": d.volume,
                        "exit_price": d.price,
                        "pnl_usd": round(d.profit + d.swap + d.commission, 2),
                        "pips": p_pips,
                        "ticket_deal": d.ticket,
                        "order_ticket": d.order,
                        "close_time": datetime.fromtimestamp(d.time).strftime("%H:%M:%S")
                    })

                sess_total_pnl += p_pnl
                sess_total_pips += p_pips_total
                currency_pnl_map[ccy] = round(currency_pnl_map[ccy] + p_pnl, 2)
                
                port_stats[ccy]["total_pnl_usd"] = round(port_stats[ccy]["total_pnl_usd"] + p_pnl, 2)
                port_stats[ccy]["total_pips"] = round(port_stats[ccy]["total_pips"] + p_pips_total, 1)
                port_stats[ccy]["sessions_count"] += 1
                if p_pnl >= 0:
                    port_stats[ccy]["win_sessions"] += 1
                else:
                    port_stats[ccy]["loss_sessions"] += 1

                # Decisão vinculada às 21:02
                dec_info = today_signals.get("portfolios", {}).get(ccy, {}) if sess_date == today_signals.get("date") else {}
                
                sess_portfolios.append({
                    "currency": ccy,
                    "flag": CCY_FLAGS.get(ccy, ""),
                    "color": CCY_COLORS.get(ccy, "#FFF"),
                    "magic": PORTFOLIO_MAGICS.get(ccy, 0),
                    "bias": dec_info.get("direction", "BUY" if p_pnl >= 0 else "SELL"),
                    "bias_label": f"Cesta {ccy} ({dec_info.get('direction', 'BUY')})",
                    "reason": dec_info.get("reason", f"Operação Noturna MT5 Magic #{PORTFOLIO_MAGICS.get(ccy)}"),
                    "d1_score": dec_info.get("d1_score", 0.0),
                    "h4_score": dec_info.get("h4_score", 0.0),
                    "pnl_usd": p_pnl,
                    "pips": round(p_pips_total, 1),
                    "status": "WIN" if p_pnl >= 0 else "LOSS",
                    "pairs": p_pairs
                })

            sess_total_pnl = round(sess_total_pnl, 2)
            sess_total_pips = round(sess_total_pips, 1)
            equity = round(equity + sess_total_pnl, 2)

            updated_sessions.append({
                "date": sess_date,
                "entry_time_br": f"{sess_date} 21:05",
                "exit_time_br": (datetime.strptime(sess_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d") + " 08:00",
                "status": "WIN" if sess_total_pnl >= 0 else "LOSS",
                "status_label": "✅ GANHO" if sess_total_pnl >= 0 else "❌ PERDA",
                "portfolios_count": len(sess_portfolios),
                "total_pnl_usd": sess_total_pnl,
                "total_pips": sess_total_pips,
                "equity_after": equity,
                "portfolios": sess_portfolios
            })

            equity_curve.append({
                "date": sess_date,
                "equity": equity,
                "pnl": sess_total_pnl
            })

        # Construir curvas de capital individuais para cada uma das 8 moedas (ordem cronológica)
        portfolio_equity_curves = {
            c: [{"date": "Início", "equity": 0.0, "pnl": 0.0}] for c in CURRENCIES
        }
        ccy_running_equity = {c: 0.0 for c in CURRENCIES}

        # Sessões em ordem cronológica para as curvas
        for s in reversed(updated_sessions):
            s_date = s["date"]
            for c in CURRENCIES:
                p_item = next((p for p in s.get("portfolios", []) if p["currency"] == c), None)
                pnl = p_item.get("pnl_usd", 0.0) if p_item else 0.0
                ccy_running_equity[c] = round(ccy_running_equity[c] + pnl, 2)
                portfolio_equity_curves[c].append({
                    "date": s_date,
                    "equity": ccy_running_equity[c],
                    "pnl": pnl
                })

        # Recalcular estatísticas por portfólio
        for ccy, st in port_stats.items():
            if st["sessions_count"] > 0:
                st["win_rate"] = round(st["win_sessions"] / st["sessions_count"] * 100, 1)

        win_sess_count = len([s for s in updated_sessions if s["total_pnl_usd"] > 0])
        loss_sess_count = len([s for s in updated_sessions if s["total_pnl_usd"] < 0])
        total_active_sess = len(updated_sessions)
        
        gross_profit = sum(s["total_pnl_usd"] for s in updated_sessions if s["total_pnl_usd"] > 0)
        gross_loss = abs(sum(s["total_pnl_usd"] for s in updated_sessions if s["total_pnl_usd"] < 0))
        pf = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 1.0)
        wr = round(win_sess_count / total_active_sess * 100, 1) if total_active_sess > 0 else 0.0

        best_c = max(currency_pnl_map, key=currency_pnl_map.get) if any(currency_pnl_map.values()) else "-"
        worst_c = min(currency_pnl_map, key=currency_pnl_map.get) if any(currency_pnl_map.values()) else "-"

        self.journal = {
            "last_audit_sync": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "summary": {
                "total_pnl_usd": round(equity, 2),
                "total_pips": round(sum(s["total_pips"] for s in updated_sessions), 1),
                "total_sessions": total_active_sess,
                "active_sessions": total_active_sess,
                "neutral_sessions": 0,
                "win_sessions": win_sess_count,
                "loss_sessions": loss_sess_count,
                "win_rate": wr,
                "profit_factor": pf,
                "best_currency": best_c,
                "worst_currency": worst_c,
                "currency_pnl": currency_pnl_map,
                "portfolio_stats": port_stats
            },
            "equity_curve": equity_curve,
            "portfolio_equity_curves": portfolio_equity_curves,
            "sessions": updated_sessions
        }
        self._save_journal()
        return self.journal

    def get_filtered_data(self, ccy_filter="ALL"):
        """Retorna resumo filtrado por moeda ou consolidado de todas as moedas."""
        if ccy_filter == "ALL":
            return self.journal

        ccy_upper = ccy_filter.upper()
        filtered_sessions = []
        equity = 0.0
        equity_curve = [{"date": "Início", "equity": 0.0, "pnl": 0.0}]
        total_pips = 0.0

        for sess in self.journal.get("sessions", []):
            matching_ports = [p for p in sess.get("portfolios", []) if p["currency"] == ccy_upper]
            if not matching_ports:
                continue

            sess_pnl = sum(p.get("pnl_usd", 0.0) for p in matching_ports)
            sess_pips = sum(p.get("pips", 0.0) for p in matching_ports)
            equity = round(equity + sess_pnl, 2)
            total_pips = round(total_pips + sess_pips, 1)

            filtered_sessions.append({
                **sess,
                "portfolios_count": len(matching_ports),
                "total_pnl_usd": round(sess_pnl, 2),
                "total_pips": round(sess_pips, 1),
                "equity_after": equity,
                "portfolios": matching_ports
            })

            equity_curve.append({
                "date": sess["date"],
                "equity": equity,
                "pnl": round(sess_pnl, 2)
            })

        win_s = [s for s in filtered_sessions if s["total_pnl_usd"] > 0]
        loss_s = [s for s in filtered_sessions if s["total_pnl_usd"] < 0]
        wr = round(len(win_s) / len(filtered_sessions) * 100, 1) if filtered_sessions else 0.0

        return {
            "last_audit_sync": self.journal.get("last_audit_sync"),
            "filter": ccy_upper,
            "summary": {
                "total_pnl_usd": equity,
                "total_pips": total_pips,
                "total_sessions": len(filtered_sessions),
                "active_sessions": len(filtered_sessions),
                "neutral_sessions": 0,
                "win_sessions": len(win_s),
                "loss_sessions": len(loss_s),
                "win_rate": wr,
                "profit_factor": self.journal.get("summary", {}).get("profit_factor", 1.0),
                "best_currency": ccy_upper,
                "worst_currency": ccy_upper,
                "currency_pnl": {ccy_upper: equity},
                "portfolio_stats": self.journal.get("summary", {}).get("portfolio_stats", {})
            },
            "equity_curve": equity_curve,
            "sessions": filtered_sessions
        }

    def get_live_session(self):
        """
        Retorna o estado da sessão em tempo real:
        - Decisões gravadas hoje às 21:02 BRT.
        - Posições reais abertas no MT5 (filtradas por Magic Number).
        - Contagem regressiva até 08:00 BRT.
        """
        now_dt = datetime.now()
        hour = now_dt.hour
        is_session_active = (hour >= 21 or hour < 8)

        # Calcular contagem regressiva até 08:00
        if hour >= 21:
            close_dt = (now_dt + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
        else:
            close_dt = now_dt.replace(hour=8, minute=0, second=0, microsecond=0)
        rem_secs = max(0, int((close_dt - now_dt).total_seconds()))
        rem_h = rem_secs // 3600
        rem_m = (rem_secs % 3600) // 60

        # Carregar decisões oficiais de hoje
        today_signals = self.get_today_signals()
        today_portfolios = today_signals.get("portfolios", {})

        live_portfolios = []
        total_live_pnl = 0.0
        total_live_pips = 0.0
        total_open_pairs = 0

        # Consultar posições reais no MT5
        mt5_connected = ensure_mt5()
        open_positions = mt5.positions_get() if mt5_connected else None
        
        magic_to_positions = {}
        if open_positions:
            for pos in open_positions:
                if pos.magic in ALL_PORTFOLIO_MAGICS:
                    if pos.magic not in magic_to_positions:
                        magic_to_positions[pos.magic] = []
                    magic_to_positions[pos.magic].append(pos)

        # Montar a lista dos 8 portfólios com Decisão + Execução MT5
        for ccy in CURRENCIES:
            magic = PORTFOLIO_MAGICS.get(ccy, 0)
            sig = today_portfolios.get(ccy, {})
            direction = sig.get("direction", "NEUTRAL")
            reason = sig.get("reason", "Sem confluência")
            d1_score = sig.get("d1_score", 0.0)
            h4_score = sig.get("h4_score", 0.0)

            port_open_pos = magic_to_positions.get(magic, [])
            p_pnl = round(sum(p.profit for p in port_open_pos), 2)
            p_pairs = []

            for p in port_open_pos:
                sym = p.symbol
                action = "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL"
                is_jpy = "JPY" in sym
                pip_unit = 0.01 if is_jpy else 0.0001
                p_pips = round(p.profit / (p.volume * 10) if p.volume > 0 else 0.0, 1)
                
                p_pairs.append({
                    "pair": sym,
                    "action": action,
                    "lot": p.volume,
                    "entry_price": p.price_open,
                    "current_price": p.price_current,
                    "pnl_usd": round(p.profit, 2),
                    "pips": p_pips,
                    "ticket": p.ticket,
                    "open_time": datetime.fromtimestamp(p.time).strftime("%H:%M:%S") if p.time else "--:--"
                })

            total_live_pnl += p_pnl
            total_open_pairs += len(p_pairs)

            live_portfolios.append({
                "currency": ccy,
                "flag": CCY_FLAGS.get(ccy, ""),
                "color": CCY_COLORS.get(ccy, "#FFF"),
                "magic": magic,
                "decision": {
                    "direction": direction,
                    "status": sig.get("status", "BLOCKED"),
                    "d1_score": d1_score,
                    "h4_score": h4_score,
                    "confluence_state": sig.get("confluence_state", ""),
                    "reason": reason
                },
                "execution": {
                    "is_trading": len(p_pairs) > 0,
                    "open_pairs_count": len(p_pairs),
                    "pnl_usd": p_pnl,
                    "pips": 0.0,
                    "status": "WIN" if p_pnl >= 0 else "LOSS",
                    "pairs": p_pairs
                }
            })

        active_portfolios_trading = len([p for p in live_portfolios if p["execution"]["is_trading"]])

        return {
            "timestamp": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "date": today_signals.get("date", now_dt.strftime("%Y-%m-%d")),
            "is_in_progress": is_session_active,
            "status": "EM ANDAMENTO" if is_session_active else "ENCERRADA / AGUARDANDO",
            "status_label": "🔴 PREGÃO NOTURNO AO VIVO (21:05 ➔ 08:00 BRT)" if is_session_active else "🟢 SESSÃO ENCERRADA (AGUARDANDO 21:05 BRT)",
            "time_remaining_str": f"restam {rem_h}h {rem_m}m" if is_session_active else "Abertura às 21:05 BRT",
            "session_info_str": f"📅 Sessão Ao Vivo | Início: 21h05 ➔ Encerramento: 08h00 BRT (restam {rem_h}h {rem_m}m)" if is_session_active else "✅ Sessão Concluída às 08h00 BRT | Próxima Abertura às 21h05 BRT",
            "total_pnl_usd": round(total_live_pnl, 2),
            "total_pips": round(total_live_pips, 1),
            "total_open_pairs": total_open_pairs,
            "active_portfolios_count": active_portfolios_trading,
            "signals_date": today_signals.get("date"),
            "portfolios": live_portfolios
        }


# Instância Singleton do Motor de Auditoria Real
real_audit_engine = RealPortfolioAuditEngine()
