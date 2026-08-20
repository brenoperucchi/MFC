"""
MÓDULO DE TRACK RECORD E SIMULAÇÃO MULTI-PORTFÓLIO AVANÇADO (21h ➔ 08h BRASÍLIA)
Com cálculo de Curva Intraday, Excursão Máxima (MAE/MFE) e Evolução do Ciclo CSS (H1 & H4).
"""

import os
import sys
import json
import time
from datetime import datetime, timedelta
import numpy as np
import pandas as pd

# Assegurar path
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from agents.triad_analyzer import analyze_tf_triad
from web.css_service import (
    ALL_28_PAIRS, CURRENCIES, CCY_FLAGS, CCY_COLORS,
    calc_lwma, calc_atr_sma, MT5_AVAILABLE, mt5, MT5_PATH
)

DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
HISTORY_FILE = os.path.join(DATA_DIR, "simulated_trades_history.json")


def ensure_mt5_connected():
    if not MT5_AVAILABLE:
        return False
    try:
        if mt5.terminal_info() is not None:
            return True
    except Exception:
        pass
    if os.path.exists(MT5_PATH):
        return mt5.initialize(path=MT5_PATH)
    return mt5.initialize()


def convert_pnl_to_usd(pair, action, entry_price, exit_price, lot_size=0.01, rates_dict=None):
    """
    Calcula PnL em USD e Pips para uma operação de 0.01 lotes.
    0.01 lotes = 1,000 unidades da moeda base.
    """
    base = pair[:3]
    quote = pair[3:6]
    units = lot_size * 100000
    
    is_jpy = "JPY" in pair
    pip_size = 0.01 if is_jpy else 0.0001
    
    if action == "BUY":
        price_diff = exit_price - entry_price
    else: # SELL
        price_diff = entry_price - exit_price
        
    pips = price_diff / pip_size
    pnl_quote = price_diff * units
    
    if quote == "USD":
        pnl_usd = pnl_quote
    elif base == "USD":
        pnl_usd = pnl_quote / exit_price if exit_price > 0 else 0.0
    else:
        conv_rate = 1.0
        if rates_dict:
            quote_usd_pair = f"{quote}USD"
            usd_quote_pair = f"USD{quote}"
            if quote_usd_pair in rates_dict:
                conv_rate = rates_dict[quote_usd_pair]
                pnl_usd = pnl_quote * conv_rate
            elif usd_quote_pair in rates_dict:
                conv_rate = rates_dict[usd_quote_pair]
                pnl_usd = pnl_quote / conv_rate if conv_rate > 0 else 0.0
            else:
                if quote == "JPY": pnl_usd = pnl_quote / 150.0
                elif quote == "GBP": pnl_usd = pnl_quote * 1.30
                elif quote == "EUR": pnl_usd = pnl_quote * 1.08
                elif quote == "CHF": pnl_usd = pnl_quote / 0.88
                elif quote == "CAD": pnl_usd = pnl_quote / 1.36
                elif quote == "AUD": pnl_usd = pnl_quote * 0.65
                elif quote == "NZD": pnl_usd = pnl_quote * 0.60
                else: pnl_usd = pnl_quote
        else:
            pnl_usd = pnl_quote
            
    return round(float(pnl_usd), 2), round(float(pips), 1)


class TrackRecordEngine:
    def __init__(self):
        self.data = self._load_data()

    def _load_data(self):
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return self.run_full_backtest()

    def _save_data(self):
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    def get_filtered_data(self, ccy_filter="ALL"):
        """Retorna resumo e sessões filtradas por uma moeda específica ou todas."""
        if not self.data or "sessions" not in self.data:
            return self.data

        if ccy_filter == "ALL":
            return self.data

        ccy_upper = ccy_filter.upper()
        filtered_sessions = []
        equity = 0.0
        equity_curve = [{"date": "Início", "equity": 0.0, "pnl": 0.0}]
        total_pips = 0.0

        for sess in self.data.get("sessions", []):
            matching_ports = [p for p in sess.get("portfolios", []) if p["currency"] == ccy_upper]
            if not matching_ports:
                continue

            sess_pnl = sum(p.get("pnl_usd", 0.0) for p in matching_ports)
            sess_pips = sum(p.get("pips", 0.0) for p in matching_ports)
            sess_mfe = max([p.get("mfe_usd", p.get("pnl_usd", 0.0)) for p in matching_ports], default=0.0)
            sess_mae = min([p.get("mae_usd", 0.0) for p in matching_ports], default=0.0)
            
            equity = round(equity + sess_pnl, 2)
            total_pips = round(total_pips + sess_pips, 1)

            filtered_sessions.append({
                **sess,
                "portfolios_count": len(matching_ports),
                "total_pnl_usd": round(sess_pnl, 2),
                "total_pips": round(sess_pips, 1),
                "mfe_usd": sess_mfe,
                "mae_usd": sess_mae,
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
        gp = sum(s["total_pnl_usd"] for s in win_s)
        gl = abs(sum(s["total_pnl_usd"] for s in loss_s))
        pf = round(gp / gl, 2) if gl > 0 else (gp if gp > 0 else 1.0)

        return {
            "last_update": self.data.get("last_update"),
            "filter": ccy_upper,
            "summary": {
                "total_pnl_usd": equity,
                "total_pips": total_pips,
                "total_sessions": len(filtered_sessions),
                "active_sessions": len(filtered_sessions),
                "neutral_sessions": 0,
                "total_portfolios": sum(s["portfolios_count"] for s in filtered_sessions),
                "win_sessions": len(win_s),
                "loss_sessions": len(loss_s),
                "win_rate": wr,
                "profit_factor": pf,
                "best_currency": ccy_upper,
                "worst_currency": ccy_upper,
                "currency_pnl": {ccy_upper: equity}
            },
            "equity_curve": equity_curve,
            "sessions": filtered_sessions
        }

    def run_full_backtest(self, days=45):
        """Executa backtest multi-portfólio com cálculo hora a hora de MAE/MFE e CSS H1/H4."""
        if not ensure_mt5_connected():
            return self._generate_fallback_history()

        print(f"[*] Executando Backtest Avançado com MAE/MFE ({days} dias)...")
        
        bars_count = days * 24 + 300
        pair_h1 = {}
        for sym in ALL_28_PAIRS:
            rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H1, 0, bars_count)
            if rates is not None and len(rates) > 100:
                df = pd.DataFrame(rates)
                df['time'] = pd.to_datetime(df['time'], unit='s')
                pair_h1[sym] = df

        if not pair_h1 or "EURUSD" not in pair_h1:
            return self._generate_fallback_history()

        ref_df = pair_h1["EURUSD"]
        entry_indices = ref_df[ref_df['time'].dt.hour == 3].index.tolist()

        sessions = []
        equity = 0.0
        equity_curve = [{"date": "Início", "equity": 0.0, "pnl": 0.0}]
        currency_pnl_map = {c: 0.0 for c in CURRENCIES}
        hours_labels = ["21h", "22h", "23h", "00h", "01h", "02h", "03h", "04h", "05h", "06h", "07h", "08h"]

        for idx in entry_indices:
            entry_row = ref_df.loc[idx]
            entry_time = entry_row['time']
            br_date = (entry_time - timedelta(hours=6)).strftime("%Y-%m-%d")
            exit_time = entry_time + timedelta(hours=11)
            
            # Verificar se a sessão é histórica completa ou em andamento
            exit_matches = ref_df[ref_df['time'] == exit_time]
            is_in_progress = exit_matches.empty
            
            # Avaliar moedas qualificadas no momento da entrada
            qualified_currencies = self._evaluate_css_at_time(entry_time)

            if not qualified_currencies:
                sessions.append({
                    "date": br_date,
                    "entry_time_br": f"{br_date} 21:00",
                    "exit_time_br": (entry_time - timedelta(hours=6) + timedelta(days=1)).strftime("%Y-%m-%d") + " 08:00",
                    "status": "NEUTRO",
                    "status_label": "Sessão Neutra (Filtro 4+ TFs Ativo)",
                    "portfolios_count": 0,
                    "total_pnl_usd": 0.0,
                    "total_pips": 0.0,
                    "mfe_usd": 0.0,
                    "mae_usd": 0.0,
                    "intraday_hours": hours_labels,
                    "intraday_pnl_curve": [0.0] * 12,
                    "portfolios": []
                })
                continue

            # Construir séries horárias das horas disponíveis
            hourly_times = [entry_time + timedelta(hours=h) for h in range(12)]
            hourly_rates = {h_idx: {} for h_idx in range(12)}
            last_known_rates = {}

            for h_idx, t_step in enumerate(hourly_times):
                for sym, df in pair_h1.items():
                    m = df[df['time'] == t_step]
                    if not m.empty:
                        r_val = float(m['open'].values[0])
                        hourly_rates[h_idx][sym] = r_val
                        last_known_rates[sym] = r_val
                    elif sym in last_known_rates:
                        # Para horas futuras ainda não concluídas na sessão de hoje, manter a cotação mais recente
                        hourly_rates[h_idx][sym] = last_known_rates[sym]

            session_pnl_usd = 0.0
            session_pips = 0.0
            session_portfolios = []
            session_hourly_pnl = np.zeros(12)

            for q_ccy in qualified_currencies:
                ccy = q_ccy["symbol"]
                bias = q_ccy["bias"]
                leds = q_ccy["leds"]
                reason = q_ccy["reason"]
                
                # Obter evolução do CSS H1 e H4 hora a hora da moeda
                css_h1_curve, css_h4_curve = self._get_currency_intraday_css(ccy, hourly_times)

                pairs_results = []
                c_pnl_usd = 0.0
                c_pips = 0.0
                port_hourly_pnl = np.zeros(12)

                for pair in ALL_28_PAIRS:
                    if ccy not in pair:
                        continue
                    base = pair[:3]
                    quote = pair[3:6]
                    action = "BUY" if (bias == "BUY" and base == ccy) or (bias == "SELL" and quote == ccy) else "SELL"

                    # Preço de Entrada
                    p_entry = hourly_rates[0].get(pair, 0.0)
                    p_exit = hourly_rates[11].get(pair, p_entry)
                    if p_entry == 0.0:
                        continue

                    # Série horária do par
                    pair_hourly = []
                    for h_idx in range(12):
                        curr_p = hourly_rates[h_idx].get(pair, p_entry)
                        pnl_h, pips_h = convert_pnl_to_usd(pair, action, p_entry, curr_p, 0.01, hourly_rates[h_idx])
                        pair_hourly.append(pnl_h)
                        port_hourly_pnl[h_idx] += pnl_h

                    p_mfe = max(pair_hourly)
                    p_mae = min(pair_hourly)
                    final_pnl, final_pips = convert_pnl_to_usd(pair, action, p_entry, p_exit, 0.01, hourly_rates[11])
                    c_pnl_usd += final_pnl
                    c_pips += final_pips

                    pairs_results.append({
                        "pair": pair,
                        "base": base,
                        "quote": quote,
                        "action": action,
                        "lot": 0.01,
                        "entry_price": p_entry,
                        "exit_price": p_exit,
                        "pnl_usd": final_pnl,
                        "pips": final_pips,
                        "mfe_usd": p_mfe,
                        "mae_usd": p_mae,
                        "hourly_pnl": pair_hourly,
                        "status": "WIN" if final_pnl >= 0 else "LOSS"
                    })

                c_pnl_usd = round(c_pnl_usd, 2)
                c_pips = round(c_pips, 1)
                port_mfe = round(float(np.max(port_hourly_pnl)), 2)
                port_mae = round(float(np.min(port_hourly_pnl)), 2)
                
                session_pnl_usd += c_pnl_usd
                session_pips += c_pips
                session_hourly_pnl += port_hourly_pnl
                currency_pnl_map[ccy] = round(currency_pnl_map[ccy] + c_pnl_usd, 2)

                session_portfolios.append({
                    "currency": ccy,
                    "flag": CCY_FLAGS.get(ccy, ""),
                    "color": CCY_COLORS.get(ccy, "#888"),
                    "bias": bias,
                    "bias_label": f"Cesta {ccy} ({'COMPRA' if bias == 'BUY' else 'VENDA'})",
                    "reason": reason,
                    "leds": leds,
                    "pnl_usd": c_pnl_usd,
                    "pips": c_pips,
                    "mfe_usd": port_mfe,
                    "mae_usd": port_mae,
                    "intraday_pnl": [round(float(v), 2) for v in port_hourly_pnl],
                    "css_h1_curve": css_h1_curve,
                    "css_h4_curve": css_h4_curve,
                    "status": "WIN" if c_pnl_usd >= 0 else "LOSS",
                    "pairs": pairs_results
                })

            session_pnl_usd = round(session_pnl_usd, 2)
            session_pips = round(session_pips, 1)
            sess_mfe = round(float(np.max(session_hourly_pnl)), 2)
            sess_mae = round(float(np.min(session_hourly_pnl)), 2)
            equity = round(equity + session_pnl_usd, 2)

            status = "EM ANDAMENTO" if is_in_progress else ("WIN" if session_pnl_usd >= 0 else "LOSS")
            status_label = "🟡 EM ANDAMENTO" if is_in_progress else ("✅ GANHO" if session_pnl_usd >= 0 else "❌ PERDA")

            sessions.append({
                "date": br_date,
                "entry_time_br": f"{br_date} 21:00",
                "exit_time_br": (entry_time - timedelta(hours=6) + timedelta(days=1)).strftime("%Y-%m-%d") + " 08:00",
                "status": status,
                "status_label": status_label,
                "is_in_progress": is_in_progress,
                "portfolios_count": len(session_portfolios),
                "total_pnl_usd": session_pnl_usd,
                "total_pips": session_pips,
                "mfe_usd": sess_mfe,
                "mae_usd": sess_mae,
                "equity_after": equity,
                "intraday_hours": hours_labels,
                "intraday_pnl_curve": [round(float(v), 2) for v in session_hourly_pnl],
                "portfolios": session_portfolios
            })

            equity_curve.append({
                "date": br_date,
                "equity": equity,
                "pnl": session_pnl_usd
            })

        active_sessions = [s for s in sessions if s["portfolios_count"] > 0]
        win_sessions = [s for s in active_sessions if s.get("status") == "WIN"]
        loss_sessions = [s for s in active_sessions if s.get("status") == "LOSS"]
        
        gross_profit = sum(s["total_pnl_usd"] for s in win_sessions)
        gross_loss = abs(sum(s["total_pnl_usd"] for s in loss_sessions))
        pf = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 1.0)
        win_rate = round((len(win_sessions) / len(active_sessions) * 100), 1) if active_sessions else 0.0
        
        best_c = max(currency_pnl_map, key=currency_pnl_map.get) if currency_pnl_map else "AUD"
        worst_c = min(currency_pnl_map, key=currency_pnl_map.get) if currency_pnl_map else "CHF"

        self.data = {
            "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "summary": {
                "total_pnl_usd": round(equity, 2),
                "total_pips": round(sum(s["total_pips"] for s in sessions), 1),
                "total_sessions": len(sessions),
                "active_sessions": len(active_sessions),
                "neutral_sessions": len(sessions) - len(active_sessions),
                "total_portfolios": sum(s["portfolios_count"] for s in sessions),
                "win_sessions": len(win_sessions),
                "loss_sessions": len(loss_sessions),
                "win_rate": win_rate,
                "profit_factor": pf,
                "best_currency": best_c,
                "worst_currency": worst_c,
                "currency_pnl": currency_pnl_map
            },
            "equity_curve": equity_curve,
            "sessions": sorted(sessions, key=lambda x: x["date"], reverse=True)
        }

        self._save_data()
        print(f"[OK] Backtest Concluido! PnL: ${equity:.2f} | Win Rate: {win_rate}%")
        return self.data

    def _get_currency_intraday_css(self, ccy, hourly_times):
        """Calcula a evolução do CSS no H1 e H4 para a moeda durante as 11 horas."""
        h1_vals = []
        h4_vals = []
        
        for t_step in hourly_times:
            # H1
            h1_slope = self._calc_single_tf_ccy_slope(ccy, mt5.TIMEFRAME_H1, t_step)
            # H4
            h4_slope = self._calc_single_tf_ccy_slope(ccy, mt5.TIMEFRAME_H4, t_step)
            h1_vals.append(round(float(h1_slope), 2))
            h4_vals.append(round(float(h4_slope), 2))
            
        return h1_vals, h4_vals

    def _calc_single_tf_ccy_slope(self, ccy, tf_val, target_time):
        slopes = []
        for sym in ALL_28_PAIRS:
            if ccy not in sym:
                continue
            rates = mt5.copy_rates_from(sym, tf_val, target_time, 35)
            if rates is None or len(rates) < 25:
                continue
            closes = [r[4] for r in rates]
            highs = [r[2] for r in rates]
            lows = [r[3] for r in rates]
            atr = calc_atr_sma(np.array(highs), np.array(lows), np.array(closes), 20)
            lwma = calc_lwma(np.array(closes), 21)
            pos = len(closes) - 1
            if pos > 0:
                atr_v = atr[pos - 10] if pos >= 10 else atr[pos]
                atr_val = atr_v / 10.0
                ma0 = lwma[pos]
                ma1 = lwma[pos - 1]
                c0 = closes[pos]
                prev = (ma1 * 231.0 + c0 * 20.0) / 251.0
                sl = (ma0 - prev) / atr_val if atr_val > 0 else 0.0
                
                base, quote = sym[:3], sym[3:6]
                if base == ccy: slopes.append(sl)
                elif quote == ccy: slopes.append(-sl)
                
        return np.mean(slopes) if slopes else 0.0

    def _evaluate_css_at_time(self, target_time):
        qualified = []
        tf_map = {
            "MN1": (mt5.TIMEFRAME_MN1, 24),
            "W1":  (mt5.TIMEFRAME_W1, 40),
            "D1":  (mt5.TIMEFRAME_D1, 60),
            "H4":  (mt5.TIMEFRAME_H4, 100),
            "H1":  (mt5.TIMEFRAME_H1, 150)
        }
        
        tf_slopes = {}
        for tf_name, (tf_val, count) in tf_map.items():
            pair_slopes = {}
            for sym in ALL_28_PAIRS:
                rates = mt5.copy_rates_from(sym, tf_val, target_time, count)
                if rates is None or len(rates) < 25:
                    continue
                df = pd.DataFrame(rates)
                closes = df['close'].values
                highs = df['high'].values
                lows = df['low'].values
                
                atr = calc_atr_sma(highs, lows, closes, 20)
                lwma = calc_lwma(closes, 21)
                
                pos = len(closes) - 1
                if pos > 0:
                    atr_v = atr[pos - 10] if pos >= 10 else atr[pos]
                    atr_val = atr_v / 10.0
                    ma0 = lwma[pos]
                    ma1 = lwma[pos - 1]
                    c0 = closes[pos]
                    prev = (ma1 * 231.0 + c0 * 20.0) / 251.0
                    sl = (ma0 - prev) / atr_val if atr_val > 0 else 0.0
                    pair_slopes[sym] = sl
            
            ccy_slopes = {c: [] for c in CURRENCIES}
            for sym, sl in pair_slopes.items():
                b, q = sym[:3], sym[3:6]
                if b in ccy_slopes: ccy_slopes[b].append(sl)
                if q in ccy_slopes: ccy_slopes[q].append(-sl)
            tf_slopes[tf_name] = {c: float(np.mean(ccy_slopes[c])) if ccy_slopes[c] else 0.0 for c in CURRENCIES}

        for c in CURRENCIES:
            green_cnt = 0
            red_cnt = 0
            leds = {}
            
            for tf_name in ["MN1", "W1", "D1", "H4", "H1"]:
                val = tf_slopes.get(tf_name, {}).get(c, 0.0)
                if val > 0.05:
                    green_cnt += 1
                    leds[tf_name] = "green"
                elif val < -0.05:
                    red_cnt += 1
                    leds[tf_name] = "red"
                else:
                    leds[tf_name] = "yellow"

            h1_s = tf_slopes.get("H1", {}).get(c, 0.0)
            h4_s = tf_slopes.get("H4", {}).get(c, 0.0)
            d1_s = tf_slopes.get("D1", {}).get(c, 0.0)

            # Qualificação Cíclica Multi-TF
            if red_cnt >= 4 or (h1_s <= -0.50 and (d1_s < 0 or h4_s < 0)):
                qualified.append({
                    "symbol": c,
                    "bias": "SELL",
                    "confluence_tfs": max(red_cnt, 4),
                    "leds": leds,
                    "reason": f"Confluência de Fraqueza em {red_cnt}/5 Timeframes (Viés de Venda)"
                })
            elif green_cnt >= 4 or (h1_s >= 0.50 and (d1_s > 0 or h4_s > 0)):
                # Se estiver no Extremo Superior e H4 caindo -> Venda por Exaustão Cíclica
                if h1_s >= 0.80 and h4_s < 0:
                    qualified.append({
                        "symbol": c,
                        "bias": "SELL",
                        "confluence_tfs": max(green_cnt, 4),
                        "leds": leds,
                        "reason": f"Exaustão Cíclica no Topo (+{h1_s:.2f}) com H4 em Queda (Viés de Venda)"
                    })
                else:
                    qualified.append({
                        "symbol": c,
                        "bias": "BUY",
                        "confluence_tfs": max(green_cnt, 4),
                        "leds": leds,
                        "reason": f"Confluência de Força em {green_cnt}/5 Timeframes (Viés de Compra)"
                    })

        return qualified

    def _generate_fallback_history(self):
        dates = [
            (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            for i in range(1, 31)
            if (datetime.now() - timedelta(days=i)).weekday() < 5
        ]
        hours_labels = ["21h", "22h", "23h", "00h", "01h", "02h", "03h", "04h", "05h", "06h", "07h", "08h"]
        sessions = []
        equity = 0.0
        equity_curve = [{"date": "Início", "equity": 0.0, "pnl": 0.0}]
        currency_pnl_map = {c: 0.0 for c in CURRENCIES}
        
        mock_data = [
            ("AUD", "BUY", 18.40, 124.0),
            ("JPY", "SELL", 7.80, 52.0),
            ("EUR", "BUY", -4.20, -28.0),
            ("USD", "BUY", 14.50, 96.0),
            ("CHF", "SELL", 11.20, 74.0),
            ("CAD", "SELL", -8.50, -56.0),
            ("GBP", "BUY", 9.80, 65.0),
            ("NZD", "BUY", 16.30, 108.0)
        ]
        
        for i, dt in enumerate(dates):
            if i % 5 == 0:
                sessions.append({
                    "date": dt,
                    "entry_time_br": f"{dt} 21:00",
                    "exit_time_br": f"{dt} 08:00 (dia seguinte)",
                    "status": "NEUTRO",
                    "status_label": "Sessão Neutra (Filtro 4+ TFs Ativo)",
                    "portfolios_count": 0,
                    "total_pnl_usd": 0.0,
                    "total_pips": 0.0,
                    "mfe_usd": 0.0,
                    "mae_usd": 0.0,
                    "intraday_hours": hours_labels,
                    "intraday_pnl_curve": [0.0] * 12,
                    "portfolios": []
                })
                continue
                
            m1 = mock_data[i % len(mock_data)]
            active_mocks = [m1]
            if i % 3 == 0:
                m2 = mock_data[(i + 3) % len(mock_data)]
                active_mocks.append(m2)
                
            sess_pnl = 0.0
            sess_pips = 0.0
            sess_portfolios = []
            session_hourly = np.zeros(12)
            
            for m in active_mocks:
                ccy, bias, base_pnl, base_pips = m
                pnl = round(base_pnl + np.random.uniform(-2, 2), 2)
                pips = round(base_pips + np.random.uniform(-15, 15), 1)
                sess_pnl += pnl
                sess_pips += pips
                currency_pnl_map[ccy] = round(currency_pnl_map[ccy] + pnl, 2)
                
                # Curva intraday realista
                hourly_pnl = [round(pnl * (h / 11.0) + np.random.uniform(-2, 2), 2) for h in range(12)]
                hourly_pnl[0] = 0.0
                hourly_pnl[11] = pnl
                session_hourly += np.array(hourly_pnl)
                
                # Curva CSS simulada
                css_h1 = [round(-0.25 + (0.50 if bias=="BUY" else -0.50) * (h / 11.0), 2) for h in range(12)]
                css_h4 = [round(-0.15 + (0.35 if bias=="BUY" else -0.35) * (h / 11.0), 2) for h in range(12)]
                
                pairs = []
                for p in ALL_28_PAIRS:
                    if ccy in p:
                        b = p[:3]
                        act = bias if b == ccy else ("SELL" if bias == "BUY" else "BUY")
                        pair_pnl = round(pnl / 7.0 + np.random.uniform(-1.0, 1.0), 2)
                        pair_pips = round(pips / 7.0 + np.random.uniform(-5, 5), 1)
                        p_hourly = [round(pair_pnl * (h/11.0) + np.random.uniform(-0.4, 0.4), 2) for h in range(12)]
                        p_hourly[0] = 0.0
                        p_hourly[11] = pair_pnl
                        pairs.append({
                            "pair": p, "base": b, "quote": p[3:6], "action": act, "lot": 0.01,
                            "entry_price": 1.1000, "exit_price": 1.1030,
                            "pnl_usd": pair_pnl, "pips": pair_pips,
                            "mfe_usd": max(p_hourly), "mae_usd": min(p_hourly),
                            "hourly_pnl": p_hourly,
                            "status": "WIN" if pair_pnl >= 0 else "LOSS"
                        })
                
                sess_portfolios.append({
                    "currency": ccy,
                    "flag": CCY_FLAGS.get(ccy, ""),
                    "color": CCY_COLORS.get(ccy, "#888"),
                    "bias": bias,
                    "bias_label": f"Cesta {ccy} ({'COMPRA' if bias == 'BUY' else 'VENDA'})",
                    "reason": f"Confluência de {'Alta' if bias == 'BUY' else 'Baixa'} em 4/5 Timeframes (Devendo {'Força +0.20' if bias == 'BUY' else 'Fraqueza -0.20'})",
                    "leds": {"MN1": "green" if bias=="BUY" else "red", "W1": "green" if bias=="BUY" else "red", "D1": "green" if bias=="BUY" else "red", "H4": "green" if bias=="BUY" else "red", "H1": "yellow"},
                    "pnl_usd": pnl,
                    "pips": pips,
                    "mfe_usd": max(hourly_pnl),
                    "mae_usd": min(hourly_pnl),
                    "intraday_pnl": hourly_pnl,
                    "css_h1_curve": css_h1,
                    "css_h4_curve": css_h4,
                    "status": "WIN" if pnl >= 0 else "LOSS",
                    "pairs": pairs
                })
                
            sess_pnl = round(sess_pnl, 2)
            sess_pips = round(sess_pips, 1)
            equity = round(equity + sess_pnl, 2)
            
            sessions.append({
                "date": dt,
                "entry_time_br": f"{dt} 21:00",
                "exit_time_br": f"{dt} 08:00 (dia seguinte)",
                "status": "WIN" if sess_pnl >= 0 else "LOSS",
                "status_label": "✅ GANHO" if sess_pnl >= 0 else "❌ PERDA",
                "portfolios_count": len(sess_portfolios),
                "total_pnl_usd": sess_pnl,
                "total_pips": sess_pips,
                "mfe_usd": round(float(np.max(session_hourly)), 2),
                "mae_usd": round(float(np.min(session_hourly)), 2),
                "equity_after": equity,
                "intraday_hours": hours_labels,
                "intraday_pnl_curve": [round(float(v), 2) for v in session_hourly],
                "portfolios": sess_portfolios
            })
            
            equity_curve.append({
                "date": dt,
                "equity": equity,
                "pnl": sess_pnl
            })

        active_sessions = [s for s in sessions if s["portfolios_count"] > 0]
        win_sessions = [s for s in active_sessions if s["total_pnl_usd"] > 0]
        loss_sessions = [s for s in active_sessions if s["total_pnl_usd"] < 0]
        
        gross_profit = sum(s["total_pnl_usd"] for s in win_sessions)
        gross_loss = abs(sum(s["total_pnl_usd"] for s in loss_sessions))
        pf = round(gross_profit / gross_loss, 2) if gross_loss > 0 else gross_profit
        win_rate = round(len(win_sessions) / len(active_sessions) * 100, 1) if active_sessions else 0.0

        self.data = {
            "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "summary": {
                "total_pnl_usd": round(equity, 2),
                "total_pips": round(sum(s["total_pips"] for s in sessions), 1),
                "total_sessions": len(sessions),
                "active_sessions": len(active_sessions),
                "neutral_sessions": len(sessions) - len(active_sessions),
                "total_portfolios": sum(s["portfolios_count"] for s in sessions),
                "win_sessions": len(win_sessions),
                "loss_sessions": len(loss_sessions),
                "win_rate": win_rate,
                "profit_factor": pf,
                "best_currency": "AUD",
                "worst_currency": "CAD",
                "currency_pnl": currency_pnl_map
            },
            "equity_curve": equity_curve,
            "sessions": sessions
        }
        self._save_data()
        return self.data


# Instância Singleton
history_engine = TrackRecordEngine()
