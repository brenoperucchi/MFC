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


def _load_dotenv_if_present(env_path=None):
    """Carrega BASE_DIR/.env pra dentro de os.environ, sem lib externa nem
    dependência nova. Variável já definida no ambiente real do sistema sempre
    vence o arquivo. Fica aqui (o módulo mais cedo importado, direta ou
    transitivamente por praticamente todo entry point) pra cobrir MT5_PATH
    logo abaixo e qualquer variável lida por agents/portfolio_executor.py,
    que importa este módulo antes de ler as suas próprias."""
    env_path = env_path or os.path.join(BASE_DIR, ".env")
    if not os.path.exists(env_path):
        return
    try:
        # utf-8-sig remove BOM (que corromperia o nome da primeira chave);
        # errors="replace" evita que um .env salvo em ANSI/Windows-1252
        # derrube o IMPORT deste módulo — e com ele o servidor e o daemon.
        with open(env_path, "r", encoding="utf-8-sig", errors="replace") as f:
            lines = f.readlines()
    except OSError as e:
        print(f"[!] Não foi possível ler {env_path}: {e} — seguindo sem ele.")
        return

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Comentário no fim da linha: só fora de aspas. Sem isto,
        # "CSS_CATASTROPHIC_SL_PIPS=150 # nota" virava o valor literal
        # "150 # nota" e o int() dele explodia no import.
        if value[:1] in ("'", '"'):
            quote = value[0]
            end = value.find(quote, 1)
            value = value[1:end] if end > 0 else value[1:]
        elif "#" in value:
            value = value.split("#", 1)[0].strip()
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv_if_present()

from agents.confluence_engine import evaluate_currency_confluence, evaluate_28_pairs_confluence
from agents.triad_analyzer import analyze_tf_triad

# Tentar importar MetaTrader5
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False
    mt5 = None

# Caminho do terminal MT5 que o motor conecta. Configurável via
# CSS_MT5_TERMINAL_PATH (variável real ou .env) — pensado pra apontar pra uma
# instância /portable dedicada (ver agents/portfolio_executor.py). Valor
# hardcoded abaixo é só fallback histórico, não aponta mais pra nenhuma
# instância em uso.
MT5_PATH = os.environ.get(
    "CSS_MT5_TERMINAL_PATH",
    r"C:\Program Files\Tickmill MT5 Terminal - Copia - Copia\terminal64.exe",
)

# Sufixo de símbolo da corretora. Medido na conta real (Exness-MT5Trial11,
# login 198819543): NENHUM dos 28 pares existe com o nome puro — todos os 28
# existem como "EURUSDm", "GBPUSDm", etc. Sem isso, toda consulta de símbolo
# e toda ordem falham com "símbolo não encontrado". Configurável porque o
# sufixo varia por corretora e por tipo de conta.
MT5_SYMBOL_SUFFIX = os.environ.get("CSS_MT5_SYMBOL_SUFFIX", "")

# Cache de resolução lógico -> corretora, preenchido sob demanda.
_SYMBOL_RESOLUTION_CACHE = {}


def to_broker_symbol(pair: str) -> str:
    """Nome lógico do par (ex.: 'EURUSD') -> nome no servidor da corretora
    (ex.: 'EURUSDm'). Tenta o nome exato primeiro, depois com o sufixo
    configurado; devolve o nome com sufixo mesmo sem MT5 disponível, pra que
    a intenção fique explícita em log/erro em vez de falhar em silêncio."""
    if pair in _SYMBOL_RESOLUTION_CACHE:
        return _SYMBOL_RESOLUTION_CACHE[pair]

    resolved = pair + MT5_SYMBOL_SUFFIX
    confirmed = False
    if MT5_AVAILABLE and mt5 is not None:
        try:
            # Sufixo configurado tem PRECEDÊNCIA sobre o nome puro: corretoras
            # que listam as duas séries (padrão e micro/cent) fariam a cesta
            # misturar contratos de nocional diferente por perna.
            if MT5_SYMBOL_SUFFIX and mt5.symbol_info(pair + MT5_SYMBOL_SUFFIX) is not None:
                resolved, confirmed = pair + MT5_SYMBOL_SUFFIX, True
            elif mt5.symbol_info(pair) is not None:
                resolved, confirmed = pair, True
        except Exception:
            pass
    # Só memoriza resolução CONFIRMADA contra o servidor. Cachear o palpite
    # feito antes do MT5 conectar congelaria um nome possivelmente errado pelo
    # resto da vida do processo.
    if confirmed:
        _SYMBOL_RESOLUTION_CACHE[pair] = resolved
    return resolved


def _stamp_provenance(payload, is_live: bool):
    """Sobrescreve o campo `mt5_connected` do payload com a procedência REAL,
    devolvendo uma cópia rasa (nunca muta o cache em memória compartilhado).

    Existe porque o campo, sozinho, era herdado de onde o dado veio: um
    snapshot em disco gravado ontem com `mt5_connected: true` era servido
    verbatim quando o MT5 estava fora, e a trava que decide se um sinal pode
    virar ordem real lia esse `true` como "dado ao vivo"."""
    if not isinstance(payload, dict):
        return payload
    stamped = dict(payload)
    stamped["mt5_connected"] = bool(is_live)
    return stamped


def from_broker_symbol(symbol: str) -> str:
    """Nome no servidor da corretora -> nome lógico do par. Usado pra comparar
    posições abertas (que vêm com o nome da corretora) contra as listas
    internas de pares."""
    if MT5_SYMBOL_SUFFIX and symbol.endswith(MT5_SYMBOL_SUFFIX):
        return symbol[: -len(MT5_SYMBOL_SUFFIX)]
    return symbol

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
    "EUR": "#2ECC71", # ForestGreen (Verde)
    "GBP": "#3872FF", # Royal Blue
    "CHF": "#00E5FF", # PaleTurquoise / Cyan
    "JPY": "#9932CC", # DarkOrchid (Roxo)
    "AUD": "#FF8C00", # Orange
    "CAD": "#8B0000", # Maroon
    "NZD": "#D2B48C"  # Tan
}

def get_tf_constant(tf_name):
    if not MT5_AVAILABLE or mt5 is None:
        return 0
    tf_map = {
        "MN1": getattr(mt5, "TIMEFRAME_MN1", 49153),
        "W1": getattr(mt5, "TIMEFRAME_W1", 32769),
        "D1": getattr(mt5, "TIMEFRAME_D1", 16408),
        "H4": getattr(mt5, "TIMEFRAME_H4", 16388),
        "H1": getattr(mt5, "TIMEFRAME_H1", 16385)
    }
    return tf_map.get(tf_name, 16385)

TIMEFRAMES_CONFIG = [
    ("MN1", 70),
    ("W1", 100),
    ("D1", 120),
    ("H4", 120),
    ("H1", 200)
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

def calc_nwe_gaussian(closes, lookback=95, bandwidth=8.0):
    """
    Cálculo do Valor Central do Nadaraya-Watson Envelope (NWE) com Kernel Gaussiano.
    Conforme especificação MQL5 CurrencySlopeStrength_NWE.mq5.
    """
    k = np.arange(lookback)
    w = np.exp(-(k**2) / (2.0 * (bandwidth**2)))
    n = len(closes)
    nwe = np.zeros(n)
    for i in range(n):
        avail = min(lookback, i + 1)
        sub_c = closes[i - avail + 1 : i + 1][::-1]
        sub_w = w[:avail]
        nwe[i] = np.dot(sub_c, sub_w) / np.sum(sub_w)
    return nwe

def normalize_score_tanh(value, sensitivity=1.0, max_bound=2.0, use_tanh=True):
    """
    Compressão Sigmoidal Suave (Tangente Hiperbólica - Tanh) com Retorno à Média.
    Preserva o 0.00 exato e satura suavemente em ±max_bound.
    """
    if not use_tanh or max_bound <= 0.0:
        return value
    if isinstance(value, np.ndarray):
        x = (value * sensitivity) / max_bound
        return np.tanh(x) * max_bound
    else:
        x = (value * sensitivity) / max_bound
        return float(np.tanh(x) * max_bound)

def calculate_full_css(tf_val, count=120, mode="standard"):
    if not MT5_AVAILABLE:
        return None, None, None
        
    pair_dfs = {}
    for sym in ALL_28_PAIRS:
        # Consulta pelo nome da corretora (pode ter sufixo, ex.: EURUSDm),
        # mas indexa pelo nome lógico — todo o resto do sistema usa o lógico.
        rates = mt5.copy_rates_from_pos(to_broker_symbol(sym), tf_val, 0, count + 150)
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
        
        idx_map = {t: i for i, t in enumerate(df.index)}
        slopes = []

        if mode == "gauss":
            # MODO GAUSS: Nadaraya-Watson Envelope + Raw ATR(100)
            atr_arr = calc_atr_sma(highs, lows, closes, 100)
            nwe_arr = calc_nwe_gaussian(closes, lookback=95, bandwidth=8.0)
            
            for t in common_index:
                pos = idx_map.get(t, -1)
                if pos <= 0:
                    slopes.append(0.0)
                    continue
                atr = atr_arr[pos] if pos < len(atr_arr) and atr_arr[pos] > 0 else 0.0001
                nwe0 = nwe_arr[pos]
                nwe1 = nwe_arr[pos - 1] if pos > 0 else nwe0
                sl = (nwe0 - nwe1) / atr
                slopes.append(sl)
        else:
            # MODO PADRÃO: TMA / LWMA + ATR(100)/10
            atr_arr = calc_atr_sma(highs, lows, closes, 100)
            lwma_arr = calc_lwma(closes, 21)
            
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
        if mode == "gauss":
            css_res[c] = normalize_score_tanh(css_res[c], sensitivity=1.0, max_bound=2.0, use_tanh=True)
            
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


DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_STANDARD_FILE = os.path.join(DATA_DIR, "css_standard.json")
DB_GAUSS_FILE = os.path.join(DATA_DIR, "css_gauss.json")


class CSSDataEngine:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(CSSDataEngine, cls).__new__(cls)
            cls._instance.cache_standard = cls._instance._load_from_disk(DB_STANDARD_FILE)
            cls._instance.cache_gauss = cls._instance._load_from_disk(DB_GAUSS_FILE)
            cls._instance.last_update_standard = time.time() if cls._instance.cache_standard else None
            cls._instance.last_update_gauss = time.time() if cls._instance.cache_gauss else None
            cls._instance.is_mt5_connected = False
            cls._instance.last_error = None
        return cls._instance

    @staticmethod
    def _load_from_disk(filepath):
        """Ponto único de entrada de dado vindo do disco — e por isso o lugar
        certo pra derrubar a procedência. O snapshot gravado carrega o
        `mt5_connected` de QUANDO foi gravado (o css_standard.json versionado
        tem `true`), e nada que sai de um arquivo é dado ao vivo. Selar aqui
        cobre também o __new__, que popula o cache e carimba o
        last_update_* — fazendo a chamada seguinte cair no throttle de 3s e
        devolver o cache sem passar por nenhuma outra checagem."""
        try:
            if os.path.exists(filepath):
                with open(filepath, "r", encoding="utf-8") as f:
                    return _stamp_provenance(json.load(f), False)
        except Exception as e:
            print(f"[!] Erro ao carregar banco {filepath}: {e}")
        return {}

    @staticmethod
    def _save_to_disk(filepath, data):
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[!] Erro ao salvar banco {filepath}: {e}")

    def connect_mt5(self):
        """Nunca inicializa 'com o que estiver disponível' (mt5.initialize()
        sem path) quando MT5_PATH não resolve pra um terminal64.exe real —
        essa máquina roda vários terminais MT5 pra estratégias/contas
        diferentes (achado ALTO em revisão), e anexar silenciosamente a
        QUALQUER outro terminal já em execução é pior que simplesmente
        falhar. Sem terminal certo, cai no fallback simulado/cache já
        existente (ver update_data) em vez de mostrar dado de conta errada
        como se fosse ao vivo. Mesma trava em
        agents/portfolio_executor.py::ensure_mt5() — MT5_PATH é a mesma
        variável nos dois módulos."""
        if not MT5_AVAILABLE:
            self.last_error = "MetaTrader5 Python module not installed."
            return False
        if not MT5_PATH or not os.path.isfile(MT5_PATH):
            self.is_mt5_connected = False
            self.last_error = f"MT5_PATH inválido ou inexistente: {MT5_PATH!r}"
            return False
        connected = mt5.initialize(path=MT5_PATH)
        self.is_mt5_connected = connected
        if not connected:
            self.last_error = str(mt5.last_error())
        else:
            self.last_error = None
        return connected

    def update_data(self, force=False, mode="standard"):
        mode = "gauss" if mode == "gauss" else "standard"
        now_ts = time.time()
        last_up = self.last_update_gauss if mode == "gauss" else self.last_update_standard
        cached = self.cache_gauss if mode == "gauss" else self.cache_standard

        # Throttle recalculation se dentro de 3s e já temos dados em memória
        if not force and last_up and (now_ts - last_up) < 3.0 and cached:
            return cached

        connected = self.connect_mt5()
        if not connected:
            if not cached:
                # Tentar carregar do disco
                db_file = DB_GAUSS_FILE if mode == "gauss" else DB_STANDARD_FILE
                disk_data = self._load_from_disk(db_file)
                if disk_data:
                    # O snapshot em disco (data/css_standard.json, versionado)
                    # carrega o mt5_connected de QUANDO foi gravado — que pode
                    # ser true de ontem. Sem sobrescrever aqui, um sinal
                    # derivado desse snapshot passa como "dado live" e vira
                    # ordem real. Nunca confie no flag que veio do disco.
                    disk_data = _stamp_provenance(disk_data, False)
                    if mode == "gauss":
                        self.cache_gauss = disk_data
                        self.last_update_gauss = now_ts
                    else:
                        self.cache_standard = disk_data
                        self.last_update_standard = now_ts
                    return disk_data

                # Gerar fallback
                res = _stamp_provenance(self._generate_fallback_data(mode=mode), False)
                if mode == "gauss":
                    self.cache_gauss = res
                    self.last_update_gauss = now_ts
                    self._save_to_disk(DB_GAUSS_FILE, res)
                else:
                    self.cache_standard = res
                    self.last_update_standard = now_ts
                    self._save_to_disk(DB_STANDARD_FILE, res)
                return res
            # Cache em memória servido com a conexão CAÍDA agora: seja qual for
            # a origem dele, não é dado live neste instante.
            return _stamp_provenance(cached, False)

        tf_data_raw = {}
        tf_charts = {}
        tf_pair_charts = {}
        for tf_name, count in TIMEFRAMES_CONFIG:
            tf_val = get_tf_constant(tf_name)
            res, times, pair_slopes = calculate_full_css(tf_val, count, mode=mode)
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
            # Conectou, mas nenhum copy_rates voltou — os dados servidos aqui
            # são de outro momento, não do agora. Mesmo tratamento.
            if not cached:
                return _stamp_provenance(self._generate_fallback_data(mode=mode), False)
            return _stamp_provenance(cached, False)

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
            
            # Status LEDs Institucionais
            leds = {
                tf: triads[tf].get("led", "yellow")
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
            
            # Sinal visual (preserva o badge_type definido no confluence engine se existir)
            badge_type = item.get("badge_type")
            if not badge_type:
                rec = item["rec"]
                badge_type = "STRONG_BUY" if "STRONG BUY" in rec else "BUY" if "BUY" in rec else "STRONG_SELL" if "STRONG SELL" in rec else "SELL" if "SELL" in rec else "NEUTRAL"

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
                "recommendation": item["rec"],
                "badge_type": badge_type,
                "conviction": item["conviction"],
                "is_alicate": item.get("is_alicate", False),
                "alicate_status": item.get("alicate_status", "NONE"),
                "alicate_tfs": item.get("alicate_tfs", []),
                "thesis": item["thesis"],
                "signal_time": signal_time,
                "bars_ago": bars_ago
            })

        result_payload = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "mt5_connected": self.is_mt5_connected,
            "engine_mode": mode,
            "engine_mode_label": "MODO GAUSS (Nadaraya-Watson Kernel)" if mode == "gauss" else "MODO PADRÃO (TMA / LWMA)",
            "currencies": currency_cards,
            "charts": tf_charts,
            "pair_charts": tf_pair_charts,
            "pairs": formatted_pairs,
            "crossovers": crossovers_data,
            "colors": CCY_COLORS,
            "flags": CCY_FLAGS
        }
        
        if mode == "gauss":
            self.cache_gauss = result_payload
            self.last_update_gauss = now_ts
            self._save_to_disk(DB_GAUSS_FILE, result_payload)
        else:
            self.cache_standard = result_payload
            self.last_update_standard = now_ts
            self._save_to_disk(DB_STANDARD_FILE, result_payload)

        return result_payload

    def _generate_fallback_data(self, mode="standard"):
        """Dados de demonstração robustos baseados na análise do dia anterior se MT5 estiver offline"""
        # Criar tempos
        now = datetime.now()
        dates = [f"18:00", "19:00", "20:00", "21:00", "22:00", "23:00", "00:00", "01:00", "02:00", "03:00", "04:00", "05:00", "06:00", "07:00", "08:00", "09:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00", "16:00", "17:00"]
        
        # Últimos scores conhecidos e curvas estruturais
        base_curves = {
            "USD": [-0.65, -0.70, -0.75, -0.80, -0.85, -0.75, -0.60, -0.45, -0.30, -0.15, -0.05, -0.01, -0.02],
            "EUR": [0.35, 0.40, 0.45, 0.42, 0.38, 0.30, 0.25, 0.18, 0.12, 0.08, 0.04, 0.00, 0.01],
            "GBP": [0.20, 0.22, 0.25, 0.28, 0.30, 0.32, 0.35, 0.36, 0.38, 0.40, 0.40, 0.41, 0.41],
            "AUD": [-0.20, -0.15, -0.05, 0.05, 0.12, 0.18, 0.22, 0.25, 0.28, 0.30, 0.31, 0.32, 0.33],
            "NZD": [-0.10, -0.05, 0.02, 0.08, 0.14, 0.18, 0.20, 0.22, 0.24, 0.25, 0.26, 0.27, 0.28],
            "CAD": [-0.15, -0.10, -0.05, 0.00, 0.04, 0.07, 0.09, 0.10, 0.11, 0.12, 0.12, 0.13, 0.13],
            "CHF": [0.10, 0.05, 0.00, -0.08, -0.14, -0.20, -0.25, -0.28, -0.30, -0.32, -0.33, -0.34, -0.34],
            "JPY": [0.15, 0.08, -0.02, -0.15, -0.30, -0.45, -0.58, -0.68, -0.74, -0.78, -0.80, -0.81, -0.81]
        }
        
        last_h1 = {c: base_curves[c][-1] for c in CURRENCIES}
        
        charts = {}
        pair_charts = {}
        for tf in ["MN1", "W1", "D1", "H4", "H1"]:
            series_dict = {}
            for c in CURRENCIES:
                curve = base_curves.get(c, [0.0]*len(dates))
                if len(curve) < len(dates):
                    # Interpolar para o tamanho de dates
                    curve = list(np.interp(np.linspace(0, len(curve)-1, len(dates)), np.arange(len(curve)), curve))
                    curve = [round(float(x), 3) for x in curve]
                if mode == "gauss":
                    curve = [round(float(normalize_score_tanh(v)), 3) for v in curve]
                series_dict[c] = curve
            charts[tf] = {
                "times": dates,
                "series": series_dict
            }
            
            pair_charts_dict = {}
            for sym in ALL_28_PAIRS:
                base, quote = sym[:3], sym[3:6]
                b_curve = series_dict.get(base, [0.0]*len(dates))
                q_curve = series_dict.get(quote, [0.0]*len(dates))
                pair_charts_dict[sym] = [round(b - q, 3) for b, q in zip(b_curve, q_curve)]
            pair_charts[tf] = pair_charts_dict
            
        currency_cards = []
        for c in CURRENCIES:
            val = last_h1.get(c, 0.0)
            if mode == "gauss":
                val = round(float(normalize_score_tanh(val)), 2)
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
                "confluence_state": f"MODO SIMULADO {'GAUSS (NWE)' if mode == 'gauss' else 'PADRÃO'} (CACHE LOCAL)",
                "final_verdict": f"{bias} (BASEADO NO ÚLTIMO FECHAMENTO)",
                "has_divergence": False,
                "divergence_alert": "Conexão com MT5 em espera (usando cache local)",
                "triads": {
                    tf: analyze_tf_triad(tf, charts[tf]["series"][c])
                    for tf in ["MN1", "W1", "D1", "H4", "H1"]
                },
                "leds": {
                    tf: analyze_tf_triad(tf, charts[tf]["series"][c])["led"]
                    for tf in ["MN1", "W1", "D1", "H4", "H1"]
                },
                "active_h1_triad": analyze_tf_triad("H1", charts["H1"]["series"][c]),
                "active_h4_triad": analyze_tf_triad("H4", charts["H4"]["series"][c])
            })

        crossovers_data = detect_currency_crossovers(charts)

        return {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "mt5_connected": False,
            "engine_mode": mode,
            "engine_mode_label": "MODO GAUSS (Nadaraya-Watson Kernel)" if mode == "gauss" else "MODO PADRÃO (TMA / LWMA)",
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
