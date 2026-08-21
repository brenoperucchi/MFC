"""
SERVIÇO DE DADOS E CÁLCULO DO CSS (Currency Slope Strength)
Gerencia a conexão com o MetaTrader 5, cálculo em tempo real de múltiplos timeframes,
cache inteligente e orquestração dos motores de confluência e Tríade Analítica.
"""

import os
import sys
import json
import time
from datetime import datetime
import numpy as np
import pandas as pd

# Assegurar imports do diretório raiz
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from agents.confluence_engine import evaluate_currency_confluence, evaluate_28_pairs_confluence
from agents.triad_analyzer import analyze_tf_triad

# Tentar importar MetaTrader5
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False
    mt5 = None

MT5_PATH = r"C:\Program Files\Tickmill MT5 Terminal - Copia - Copia\terminal64.exe"

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

CCY_FLAGS = {
    "USD": "🇺🇸",
    "EUR": "🇪🇺",
    "GBP": "🇬🇧",
    "CHF": "🇨🇭",
    "JPY": "🇯🇵",
    "AUD": "🇦🇺",
    "CAD": "🇨🇦",
    "NZD": "🇳🇿"
}

CCY_COLORS = {
    "USD": "#FF3B30", # Red
    "EUR": "#00BFFF", # Vivid Sky Blue
    "GBP": "#3872FF", # Royal Blue
    "CHF": "#00E5FF", # Cyan / Bright Blue
    "JPY": "#FFD700", # Yellow / Gold
    "AUD": "#FF8C00", # Deep Orange
    "CAD": "#E0245E", # Deep Pink / Crimson
    "NZD": "#D2B48C"  # Sand / Khaki
}

TIMEFRAMES_CONFIG = [
    ("MN1", getattr(mt5, "TIMEFRAME_MN1", 43200) if mt5 else 43200, 70),
    ("W1",  getattr(mt5, "TIMEFRAME_W1", 10080) if mt5 else 10080, 100),
    ("D1",  getattr(mt5, "TIMEFRAME_D1", 1440) if mt5 else 1440, 120),
    ("H4",  getattr(mt5, "TIMEFRAME_H4", 240) if mt5 else 240, 120),
    ("H1",  getattr(mt5, "TIMEFRAME_H1", 60) if mt5 else 60, 200)
]

def calc_atr_sma(high, low, close, period=100):
    tr = np.zeros(len(close))
    tr[0] = high[0] - low[0]
    for i in range(1, len(close)):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i-1]), abs(low[i] - close[i-1]))
    tr_series = pd.Series(tr)
    atr = tr_series.rolling(window=period, min_periods=1).mean().values
    return atr

def calc_lwma(series_values, period=21):
    weights = np.arange(1, period + 1)
    
    def lwma(prices):
        return np.dot(prices, weights) / weights.sum()
        
    s = pd.Series(series_values)
    res = s.rolling(window=period, min_periods=period).apply(lwma, raw=True)
    res = res.bfill().values 
    return res

def calculate_full_css(tf_val, count=120):
    if not MT5_AVAILABLE:
        return None, None, None
        
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
        return None, None, None
        
    common_index = None
    for sym, df in pair_dfs.items():
        if common_index is None:
            common_index = df.index
        else:
            common_index = common_index.intersection(df.index)
            
    if common_index is None or len(common_index) == 0:
        return None, None, None
        
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
        
        atr_arr = calc_atr_sma(highs, lows, closes, 100)
        lwma_arr = calc_lwma(closes, 21)
        idx_map = {t: i for i, t in enumerate(df.index)}
        
        slopes = []
        for t in common_index:
            pos = idx_map.get(t, -1)
            if pos <= 0:
                slopes.append(0.0)
                continue
            atr_val = atr_arr[pos - 10] if (pos - 10) >= 0 else atr_arr[pos]
            atr = atr_val / 10.0
            
            ma0 = lwma_arr[pos]
            ma1 = lwma_arr[pos - 1]
            close0 = closes[pos]
            
            dblTma = ma0
            dblPrev = (ma1 * 231.0 + close0 * 20.0) / 251.0
            
            sl = (dblTma - dblPrev) / atr if atr > 0 else 0.0
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
            
    time_strs = [t.strftime("%Y-%m-%d %H:%M") for t in common_index]
    return css_res, time_strs, pair_slopes


def detect_currency_crossovers(charts_dict):
    """
    Detecta cruzamentos de scores entre a Moeda Base e a Moeda Cotada para os 28 pares Forex.
    Retorna cruzamentos recentes, recência em barras, direção (BUY/SELL) e ranking de spread.
    """
    result = {}
    all_fresh = []
    
    tfs = ["H1", "H4", "D1", "W1", "MN1"]
    for tf in tfs:
        if tf not in charts_dict or "series" not in charts_dict[tf] or "times" not in charts_dict[tf]:
            continue
            
        series_map = charts_dict[tf]["series"]
        times = charts_dict[tf]["times"]
        num_bars = len(times)
        if num_bars < 2:
            continue
            
        tf_crossovers = []
        tf_spreads = []
        
        for pair in ALL_28_PAIRS:
            base = pair[:3]
            quote = pair[3:6]
            if base not in series_map or quote not in series_map:
                continue
                
            base_curve = series_map[base]
            quote_curve = series_map[quote]
            if len(base_curve) < 2 or len(quote_curve) < 2:
                continue
                
            curr_base = base_curve[-1]
            curr_quote = quote_curve[-1]
            curr_spread = round(curr_base - curr_quote, 3)
            
            # Buscar o cruzamento mais recente (varrendo do fim para o começo)
            latest_cross = None
            for i in range(num_bars - 1, 0, -1):
                prev_base = base_curve[i - 1]
                prev_quote = quote_curve[i - 1]
                b = base_curve[i]
                q = quote_curve[i]
                
                # Cruzamento de Alta (Base cruza Quote para cima -> BUY no par)
                if prev_base <= prev_quote and b > q:
                    bars_ago = num_bars - 1 - i
                    cross_region = (
                        "Zona de Sobreforça (+0.20)" if b >= 0.20 else
                        "Zona de Sobrefraqueza (-0.20)" if b <= -0.20 else
                        "Zona de Equilíbrio (0.00)"
                    )
                    latest_cross = {
                        "pair": pair,
                        "base": base,
                        "quote": quote,
                        "base_flag": CCY_FLAGS.get(base, ""),
                        "quote_flag": CCY_FLAGS.get(quote, ""),
                        "timeframe": tf,
                        "direction": "BUY",
                        "direction_label": "🟢 COMPRA",
                        "action_thesis": f"{base} superou {quote} em força relativa ({base} > {quote})",
                        "timestamp": times[i],
                        "bars_ago": bars_ago,
                        "is_fresh": bars_ago <= 3,
                        "base_score_cross": round(b, 2),
                        "quote_score_cross": round(q, 2),
                        "current_base_score": round(curr_base, 2),
                        "current_quote_score": round(curr_quote, 2),
                        "current_spread": curr_spread,
                        "abs_spread": abs(curr_spread),
                        "region": cross_region
                    }
                    break
                    
                # Cruzamento de Baixa (Quote cruza Base para cima / Base cruza para baixo -> SELL no par)
                elif prev_base >= prev_quote and b < q:
                    bars_ago = num_bars - 1 - i
                    cross_region = (
                        "Zona de Sobreforça (+0.20)" if q >= 0.20 else
                        "Zona de Sobrefraqueza (-0.20)" if q <= -0.20 else
                        "Zona de Equilíbrio (0.00)"
                    )
                    latest_cross = {
                        "pair": pair,
                        "base": base,
                        "quote": quote,
                        "base_flag": CCY_FLAGS.get(base, ""),
                        "quote_flag": CCY_FLAGS.get(quote, ""),
                        "timeframe": tf,
                        "direction": "SELL",
                        "direction_label": "🔴 VENDA",
                        "action_thesis": f"{quote} superou {base} em força relativa ({quote} > {base})",
                        "timestamp": times[i],
                        "bars_ago": bars_ago,
                        "is_fresh": bars_ago <= 3,
                        "base_score_cross": round(b, 2),
                        "quote_score_cross": round(q, 2),
                        "current_base_score": round(curr_base, 2),
                        "current_quote_score": round(curr_quote, 2),
                        "current_spread": curr_spread,
                        "abs_spread": abs(curr_spread),
                        "region": cross_region
                    }
                    break
            
            if latest_cross and latest_cross["bars_ago"] <= 8:
                tf_crossovers.append(latest_cross)
                if latest_cross["is_fresh"]:
                    all_fresh.append(latest_cross)
            
            tf_spreads.append({
                "pair": pair,
                "base": base,
                "quote": quote,
                "base_flag": CCY_FLAGS.get(base, ""),
                "quote_flag": CCY_FLAGS.get(quote, ""),
                "timeframe": tf,
                "current_base_score": round(curr_base, 2),
                "current_quote_score": round(curr_quote, 2),
                "spread": curr_spread,
                "abs_spread": abs(curr_spread),
                "leader": base if curr_base > curr_quote else quote,
                "bias": "BUY" if curr_base > curr_quote else "SELL"
            })
            
        # Ordenar cruzamentos por recência (os mais recentes primeiro)
        tf_crossovers.sort(key=lambda x: x["bars_ago"])
        tf_spreads.sort(key=lambda x: x["abs_spread"], reverse=True)
        
        result[tf] = {
            "crossovers": tf_crossovers,
            "spread_ranking": tf_spreads
        }
        
    return {
        "timeframes": result,
        "fresh_crossovers": all_fresh,
        "fresh_count": len(all_fresh)
    }


class CSSDataEngine:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(CSSDataEngine, cls).__new__(cls)
            cls._instance.cache = {}
            cls._instance.last_update = None
            cls._instance.is_mt5_connected = False
            cls._instance.last_error = None
        return cls._instance

    def connect_mt5(self):
        if not MT5_AVAILABLE:
            self.last_error = "MetaTrader5 Python module not installed."
            return False
        if os.path.exists(MT5_PATH):
            connected = mt5.initialize(path=MT5_PATH)
        else:
            connected = mt5.initialize()
        self.is_mt5_connected = connected
        if not connected:
            self.last_error = str(mt5.last_error())
        else:
            self.last_error = None
        return connected

    def update_data(self, force=False):
        # Throttle recalculations to at most once every 3 seconds if not forced
        now_ts = time.time()
        if not force and self.last_update and (now_ts - self.last_update) < 3.0:
            return self.cache

        connected = self.connect_mt5()
        if not connected and not self.cache:
            # Generate simulated / fallback baseline if MT5 is closed
            return self._generate_fallback_data()

        tf_data_raw = {}
        tf_charts = {}
        tf_pair_charts = {}
        for tf_name, tf_val, count in TIMEFRAMES_CONFIG:
            res, times, pair_slopes = calculate_full_css(tf_val, count)
            if res is not None:
                tf_data_raw[tf_name] = (res, times)
                # Formatar para frontend
                tf_charts[tf_name] = {
                    "times": times,
                    "series": {c: [round(float(v), 3) for v in res[c]] for c in CURRENCIES}
                }
                
                # Formatar pares (para matriz)
                formatted_pair_slopes = {}
                for sym, (base, quote, sl) in pair_slopes.items():
                    formatted_pair_slopes[sym] = [round(float(v), 3) for v in sl]
                tf_pair_charts[tf_name] = formatted_pair_slopes

        if not tf_data_raw:
            if not self.cache:
                return self._generate_fallback_data()
            return self.cache

        # Confluence and Triad per currency
        ccy_confluence_results = {}
        currency_cards = []
        for c in CURRENCIES:
            mn_s = tf_data_raw["MN1"][0][c]
            w1_s = tf_data_raw["W1"][0][c]
            d1_s = tf_data_raw["D1"][0][c]
            h4_s = tf_data_raw["H4"][0][c]
            h1_s = tf_data_raw["H1"][0][c]
            
            conf = evaluate_currency_confluence(c, mn_s, w1_s, d1_s, h4_s, h1_s)
            ccy_confluence_results[c] = conf
            
            # Triade for each timeframe
            triads = {
                "MN1": analyze_tf_triad("MN1", mn_s),
                "W1": analyze_tf_triad("W1", w1_s),
                "D1": analyze_tf_triad("D1", d1_s),
                "H4": analyze_tf_triad("H4", h4_s),
                "H1": analyze_tf_triad("H1", h1_s)
            }
            
            # Status LEDs (Green=UP, Red=DN, Yellow=Flat)
            leds = {
                tf: ("green" if "UP" in triads[tf]["dir"] else "red" if "DN" in triads[tf]["dir"] else "yellow")
                for tf in ["MN1", "W1", "D1", "H4", "H1"]
            }
            
            # Score no H1 (para exibição rápida) e H4
            h1_val = round(float(h1_s[-1]), 2)
            h4_val = round(float(h4_s[-1]), 2)
            d1_val = round(float(d1_s[-1]), 2)
            
            # Sinal Badge (BUY, SELL, NEUTRAL)
            bias = conf["trade_bias"]
            if "COMPRA" in bias:
                signal_badge = "BUY"
            elif "VENDA" in bias:
                signal_badge = "SELL"
            else:
                signal_badge = "NEUTRAL"

            currency_cards.append({
                "symbol": c,
                "flag": CCY_FLAGS.get(c, "🏳️"),
                "color": CCY_COLORS.get(c, "#888888"),
                "h1_score": h1_val,
                "h4_score": h4_val,
                "d1_score": d1_val,
                "total_score": round(conf["score_total"], 2),
                "signal_badge": signal_badge,
                "trade_bias": bias,
                "confluence_state": conf["confluence_state"],
                "final_verdict": conf["final_verdict"],
                "has_divergence": conf["has_divergence"],
                "divergence_alert": conf["divergence_alert"],
                "triads": triads,
                "leds": leds,
                "active_h1_triad": triads["H1"],
                "active_h4_triad": triads["H4"]
            })

        # Screener 28 Pares
        pair_rankings = evaluate_28_pairs_confluence(ALL_28_PAIRS, ccy_confluence_results, tf_data_raw)
        
        crossovers_data = detect_currency_crossovers(tf_charts)
        h1_cross_map = {c["pair"]: c for c in crossovers_data.get("timeframes", {}).get("H1", {}).get("crossovers", [])}

        # Formatar 28 pares
        formatted_pairs = []
        for item in pair_rankings:
            pair = item["pair"]
            base = item["base"]
            quote = item["quote"]
            
            # Sinal visual
            rec = item["rec"]
            if "STRONG BUY" in rec:
                badge_type = "STRONG_BUY"
            elif "BUY" in rec:
                badge_type = "BUY"
            elif "STRONG SELL" in rec:
                badge_type = "STRONG_SELL"
            elif "SELL" in rec:
                badge_type = "SELL"
            else:
                badge_type = "NEUTRAL"

            cross_info = h1_cross_map.get(pair)
            default_t = tf_charts.get("H1", {}).get("times", [""])[-1] if tf_charts.get("H1") else ""
            signal_time = cross_info["timestamp"] if cross_info else default_t
            bars_ago = cross_info["bars_ago"] if cross_info else 0

            formatted_pairs.append({
                "pair": pair,
                "base": base,
                "quote": quote,
                "base_flag": CCY_FLAGS.get(base, ""),
                "quote_flag": CCY_FLAGS.get(quote, ""),
                "total_score": round(item["total_score"], 2),
                "macro_diff": round(item["macro_diff"], 2),
                "op_diff": round(item["op_diff"], 2),
                "recommendation": rec,
                "badge_type": badge_type,
                "conviction": item["conviction"],
                "thesis": item["thesis"],
                "signal_time": signal_time,
                "bars_ago": bars_ago
            })

        self.cache = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "mt5_connected": self.is_mt5_connected,
            "currencies": currency_cards,
            "charts": tf_charts,
            "pair_charts": tf_pair_charts,
            "pairs": formatted_pairs,
            "crossovers": crossovers_data,
            "colors": CCY_COLORS,
            "flags": CCY_FLAGS
        }
        self.last_update = now_ts
        return self.cache

    def _generate_fallback_data(self):
        """Dados de demonstração robustos baseados na análise do dia anterior se MT5 estiver offline"""
        # Criar tempos
        now = datetime.now()
        dates = [f"18:00", "19:00", "20:00", "21:00", "22:00", "23:00", "00:00", "01:00", "02:00", "03:00", "04:00", "05:00", "06:00", "07:00", "08:00", "09:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00", "16:00", "17:00"]
        
        # Últimos scores conhecidos da rotina de 18/08/2026
        last_h1 = {
            "AUD": -0.70, "USD": 0.24, "EUR": 0.34, "GBP": 0.35,
            "CHF": -0.03, "JPY": 0.39, "CAD": -0.45, "NZD": -0.15
        }
        
        charts = {}
        pair_charts = {}
        for tf in ["MN1", "W1", "D1", "H4", "H1"]:
            series_dict = {}
            for c in CURRENCIES:
                val = last_h1.get(c, 0.0)
                # curva suave
                curve = [round(val + 0.1 * np.sin(i * 0.3), 3) for i in range(len(dates))]
                series_dict[c] = curve
            charts[tf] = {
                "times": dates,
                "series": series_dict
            }
            
            pair_charts_dict = {}
            for sym in ALL_28_PAIRS:
                base, quote = sym[:3], sym[3:6]
                val = last_h1.get(base, 0.0) - last_h1.get(quote, 0.0)
                curve = [round(val + 0.1 * np.sin(i * 0.3), 3) for i in range(len(dates))]
                pair_charts_dict[sym] = curve
            pair_charts[tf] = pair_charts_dict
            
        currency_cards = []
        for c in CURRENCIES:
            val = last_h1.get(c, 0.0)
            bias = "COMPRA FORTE" if val < -0.20 or c == "USD" else "VENDA FORTE" if val > 0.20 or c == "EUR" else "COMPRA" if c == "AUD" else "NEUTRO"
            badge = "BUY" if "COMPRA" in bias else "SELL" if "VENDA" in bias else "NEUTRAL"
            
            currency_cards.append({
                "symbol": c,
                "flag": CCY_FLAGS.get(c, "🏳️"),
                "color": CCY_COLORS.get(c, "#888888"),
                "h1_score": val,
                "h4_score": round(val * 0.8, 2),
                "d1_score": round(val * 0.5, 2),
                "total_score": round(val, 2),
                "signal_badge": badge,
                "trade_bias": bias,
                "confluence_state": "MODO SIMULADO / OFFLINE (CACHE LOCAL)",
                "final_verdict": f"{bias} (BASEADO NO ÚLTIMO FECHAMENTO)",
                "has_divergence": False,
                "divergence_alert": "Conexão com MT5 em espera (usando cache local)",
                "triads": {
                    tf: {
                        "tf": tf, "score": val, "score_str": f"{val:+.2f}", "diff": 0.05,
                        "dir": "▲ UP" if val < 0 else "▼ DN",
                        "region": f"Box {'Superior' if val > 0 else 'Inferior'}",
                        "current_cycle": "Ciclo Ativo", "owing_cycle": "Devendo Rompimento",
                        "angle": "▲ Inclinado para Força" if val < 0 else "▼ Inclinado para Fraqueza",
                        "is_retomada_forca": False, "is_retomada_fraqueza": False
                    } for tf in ["MN1", "W1", "D1", "H4", "H1"]
                },
                "leds": {tf: ("green" if val < 0 else "red") for tf in ["MN1", "W1", "D1", "H4", "H1"]},
                "active_h1_triad": {
                    "tf": "H1", "score": val, "score_str": f"{val:+.2f}", "dir": "▲ UP" if val < 0 else "▼ DN",
                    "region": "Zona de Parada", "current_cycle": "Ciclo Ativo", "owing_cycle": "Devendo Força/Fraqueza",
                    "angle": "🚀 Foguete" if abs(val) > 0.5 else "Moderado"
                },
                "active_h4_triad": {
                    "tf": "H4", "score": round(val*0.8, 2), "score_str": f"{val*0.8:+.2f}", "dir": "▲ UP" if val < 0 else "▼ DN",
                    "region": "Box", "current_cycle": "Ciclo Ativo", "owing_cycle": "Devendo Força/Fraqueza",
                    "angle": "Moderado"
                }
            })

        self.cache = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "mt5_connected": False,
            "currencies": currency_cards,
            "charts": charts,
            "pair_charts": pair_charts,
            "pairs": [
                {"pair": "EURAUD", "base": "EUR", "quote": "AUD", "base_flag": "🇪🇺", "quote_flag": "🇦🇺", "total_score": 0.38, "macro_diff": 0.30, "op_diff": 0.52, "recommendation": "COMPRA FORTE (STRONG BUY)", "badge_type": "STRONG_BUY", "conviction": "MÁXIMA (CONFLUÊNCIA DUPLA)", "thesis": "EUR forte vs AUD fraco devendo fraqueza macro."},
                {"pair": "GBPAUD", "base": "GBP", "quote": "AUD", "base_flag": "🇬🇧", "quote_flag": "🇦🇺", "total_score": 0.35, "macro_diff": 0.28, "op_diff": 0.47, "recommendation": "COMPRA FORTE (STRONG BUY)", "badge_type": "STRONG_BUY", "conviction": "MÁXIMA (CONFLUÊNCIA DUPLA)", "thesis": "GBP forte vs AUD fraco devendo fraqueza macro."},
                {"pair": "EURCHF", "base": "EUR", "quote": "CHF", "base_flag": "🇪🇺", "quote_flag": "🇨🇭", "total_score": 0.32, "macro_diff": 0.33, "op_diff": 0.31, "recommendation": "COMPRA (BUY)", "badge_type": "BUY", "conviction": "ALTA", "thesis": "Vantagem expressiva de fluxo para EUR sobre CHF."},
                {"pair": "AUDJPY", "base": "AUD", "quote": "JPY", "base_flag": "🇦🇺", "quote_flag": "🇯🇵", "total_score": -0.22, "macro_diff": -0.15, "op_diff": -0.28, "recommendation": "VENDA FORTE (STRONG SELL)", "badge_type": "STRONG_SELL", "conviction": "ALTA", "thesis": "AUD fraquejando frente ao JPY."},
                {"pair": "USDJPY", "base": "USD", "quote": "JPY", "base_flag": "🇺🇸", "quote_flag": "🇯🇵", "total_score": -0.19, "macro_diff": -0.31, "op_diff": -0.01, "recommendation": "VENDA (SELL)", "badge_type": "SELL", "conviction": "ALTA", "thesis": "Pressão de venda em USD frente ao JPY."}
            ],
            "crossovers": detect_currency_crossovers(charts),
            "colors": CCY_COLORS,
            "flags": CCY_FLAGS
        }
        return self.cache

    def get_history_dates(self):
        dates = []
        reports_dir = os.path.join(BASE_DIR, "reports")
        if os.path.exists(reports_dir):
            for item in sorted(os.listdir(reports_dir), reverse=True):
                if len(item) == 8 and item.isdigit():
                    rep_path = os.path.join(reports_dir, item, "analise_diaria.md")
                    if os.path.exists(rep_path):
                        dates.append(item)
        for item in sorted(os.listdir(BASE_DIR), reverse=True):
            if len(item) == 8 and item.isdigit() and item not in dates:
                rep_path = os.path.join(BASE_DIR, item, "analise_diaria.md")
                if os.path.exists(rep_path):
                    dates.append(item)
        return dates

    def get_history_report(self, date_str):
        reports_dir = os.path.join(BASE_DIR, "reports")
        rep_path = os.path.join(reports_dir, date_str, "analise_diaria.md")
        if not os.path.exists(rep_path):
            rep_path = os.path.join(BASE_DIR, date_str, "analise_diaria.md")
        if not os.path.exists(rep_path):
            rep_path = os.path.join(BASE_DIR, "log_conhecimento", f"{date_str}.md")
        if os.path.exists(rep_path):
            with open(rep_path, "r", encoding="utf-8") as f:
                return f.read()
        return None


# Instância Singleton
css_engine = CSSDataEngine()
