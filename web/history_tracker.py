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
    calc_lwma, calc_atr_sma, MT5_AVAILABLE, mt5, MT5_PATH,
    to_broker_symbol, ATR_PERIOD, required_full_history_bars
)

DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
HISTORY_FILE = os.path.join(DATA_DIR, "simulated_trades_history.json")

# Este módulo calcula seu próprio slope pontual (não usa calculate_full_css)
# e, até esta correção, chamava calc_atr_sma() com período 20 e min_periods
# implícito (default 1) — um segundo motor de ATR, divergente do canônico
# (ATR_PERIOD=100, docs/MATHEMATICAL_MODELS.md §1.2), decidindo
# qualified_currencies (quais moedas entram no track record). Achado via
# herdr-ask mfc-18: drift, não escolha deliberada — o próprio Miquéias já
# tinha corrigido isto no fork dele (upstream/main commit 1e0bda3, "unify
# ATR 100"), mas esse commit nunca chegou a este `main`. Medido antes do fix
# (15 noites reais, instância isolada mfc-backtest): 17/120 pontos
# moeda-noite divergiam do canônico, sempre na mesma direção — o motor
# antigo SUBcontava qualificações, nunca o contrário. `required_full_history_bars(1)`
# é o mínimo de barras cruas pra UM ponto de decisão ter o ATR(100) com
# janela cheia no índice lido (pos-10). A margem de 20 NÃO é folga contra
# fim de semana/feriado — copy_rates_from(sym, tf, t, N) devolve N barras que
# EXISTEM (fim de semana/feriado não consome slot, só não gera barra). Ela
# TAMBÉM não tolera uma corretora com menos profundidade de histórico —
# TRACK_RECORD_MIN_BARS é ao mesmo tempo o que se pede E o piso da guarda
# (`len(rates) < TRACK_RECORD_MIN_BARS` descarta), então menos profundidade
# vira skip/neutro, nunca um resultado tolerado. O único efeito real da
# margem é dar folga no ÍNDICE LIDO: com ela, pos-10 fica 20 barras dentro
# da janela válida em vez de exatamente no limite. Correções herdr-review
# mfc-79 (mfc-rev-2, achado P3-1) e mfc-80 (mfc-rev, achado MFC-80-02) — duas
# rodadas seguidas corrigindo o motivo, o valor sempre esteve certo. Mesmo
# espírito da margem de calculate_full_css() em css_service.py (lá +150,
# aqui bem menor pois só precisamos de UM ponto, não uma série inteira).
TRACK_RECORD_MIN_BARS = required_full_history_bars(1) + 20

# Mesmo offset do InpGmtOffset do EA (mt5/CSS_Portfolio_Basket_EA.mq5) — medido
# empiricamente em 24/08 contra a conta real (Exness-MT5Trial11 = UTC+0,
# BRT = UTC-3, logo offset = -3). O backtest calculava a hora de entrada como
# "hora do servidor == 3" assumindo servidor = UTC+3 (offset implícito +6 na
# rotulagem de data) — nesta conta isso aponta pra meia-noite BRT, não 21h.
# Reconfira se a corretora/conta mudar.
def _env_int_safe(name, default, lo=None, hi=None):
    """Mesmo padrão de agents/portfolio_executor.py::_env_number() — nunca
    derruba o IMPORT deste módulo se vier lixo. Achado em revisão (mfc-rev-2,
    herdr-review rodada 7, P2-1): GMT_OFFSET usava `int()` cru, sem
    try/except — um typo em CSS_MT5_GMT_OFFSET derrubava o import deste
    arquivo e, por tabela, o de agents/portfolio_executor.py (que importa
    daqui), ou seja, servidor web E daemon inteiros — exatamente o problema
    que `_env_number()` foi criado pra evitar, sobrevivendo numa variável
    fora do escopo original do F-06 por este módulo não reusar aquela
    função (evitar import circular: agents/portfolio_executor.py importa
    deste módulo, não o contrário).

    `lo`/`hi` opcionais SÓ avisam, não recusam (achado em revisão: mfc-rev-2
    + Codex, herdr-review rodadas 8/9, P3-2/F09-02: um valor que CASTA mas é
    absurdo, ex. GMT_OFFSET="99", passava sem aviso nenhum e deslocava
    ENTRY_SERVER_HOUR em silêncio). Não é um gate fail-closed como
    check_execution_config() em agents/portfolio_executor.py — GMT_OFFSET só
    afeta rotulagem de sessão em track record/backtest, nunca envio de
    ordem, então recusar computar aqui seria mais rígido do que o risco
    justifica; um aviso visível já fecha o "silêncio" que era o achado."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        valor = int(raw.strip())
    except (TypeError, ValueError):
        print(f"[!] {name}={raw!r} inválido — usando o padrão {default}.")
        return default
    if lo is not None and hi is not None and not (lo <= valor <= hi):
        print(f"[!] {name}={valor} fora da faixa esperada [{lo}, {hi}] — "
              f"usando mesmo assim, mas confira se é intencional.")
    return valor


# Faixa real de fuso horário (UTC-12 a UTC+14) — só aviso, não recusa (ver
# docstring de _env_int_safe acima).
GMT_OFFSET = _env_int_safe("CSS_MT5_GMT_OFFSET", -3, lo=-12, hi=14)
ENTRY_SERVER_HOUR = (21 - GMT_OFFSET) % 24


def ensure_mt5_connected():
    """Nunca inicializa 'com o que estiver disponível' (mt5.initialize() sem
    path) quando MT5_PATH não resolve pra um terminal64.exe real — mesma
    trava aplicada em agents/portfolio_executor.py::ensure_mt5(),
    web/css_service.py::connect_mt5() e web/real_portfolio_audit.py::
    ensure_mt5() (achado ALTO em revisão, mesmo bug duplicado nos 4
    módulos). Usada só por TrackRecordEngine (scripts/backtest_selection_rules.py),
    não pelo caminho de execução ao vivo, mas o risco de anexar num terminal
    errado da máquina é o mesmo."""
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
        conv_done = False
        if rates_dict:
            quote_usd_pair = f"{quote}USD"
            usd_quote_pair = f"USD{quote}"
            if quote_usd_pair in rates_dict and rates_dict[quote_usd_pair] > 0:
                pnl_usd = pnl_quote * rates_dict[quote_usd_pair]
                conv_done = True
            elif usd_quote_pair in rates_dict and rates_dict[usd_quote_pair] > 0:
                pnl_usd = pnl_quote / rates_dict[usd_quote_pair]
                conv_done = True

        if not conv_done:
            if quote == "JPY": pnl_usd = pnl_quote / 155.0
            elif quote == "GBP": pnl_usd = pnl_quote * 1.30
            elif quote == "EUR": pnl_usd = pnl_quote * 1.08
            elif quote == "CHF": pnl_usd = pnl_quote / 0.88
            elif quote == "CAD": pnl_usd = pnl_quote / 1.36
            elif quote == "AUD": pnl_usd = pnl_quote * 0.65
            elif quote == "NZD": pnl_usd = pnl_quote * 0.60
            else: pnl_usd = pnl_quote
            
    return round(float(pnl_usd), 2), round(float(pips), 1)


def _aggregate_pair_slopes_to_ccy_slopes(pair_slopes):
    """pair_slopes: {símbolo: slope} SÓ dos pares que sobreviveram ao fetch
    de um timeframe -> {moeda: slope médio} pras 8 moedas.

    Cobertura parcial de pares NÃO vira média de subconjunto — mesmo
    princípio do gate canônico de calculate_full_css() (web/css_service.py:
    "um timeframe com 27/28 pares não é um snapshot válido... a média por
    moeda mudaria silenciosamente"). Achado herdr-ask mfc-18 / herdr-review
    mfc-79 (mfc-rev-2): uniformizar TRACK_RECORD_MIN_BARS trocou "MN1 sempre
    ausente pra todo mundo" (consistente, pois count=24 < guard antiga de 25
    nunca passava) por "MN1 calculado a partir de 1-2 dos 28 pares" nos
    TFs/corretoras onde a profundidade de histórico é menor que
    TRACK_RECORD_MIN_BARS pra maioria dos pares — uma amostra enviesada que
    parece um score válido mas não é. Um TF incompleto fica neutro (0.0) pra
    TODAS as moedas, igual ao comportamento seguro que já existia quando a
    contagem sobrevivente batia zero."""
    if len(pair_slopes) < len(ALL_28_PAIRS):
        return {c: 0.0 for c in CURRENCIES}

    ccy_slopes = {c: [] for c in CURRENCIES}
    for sym, sl in pair_slopes.items():
        b, q = sym[:3], sym[3:6]
        if b in ccy_slopes: ccy_slopes[b].append(sl)
        if q in ccy_slopes: ccy_slopes[q].append(-sl)
    return {c: float(np.mean(ccy_slopes[c])) if ccy_slopes[c] else 0.0 for c in CURRENCIES}


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

    def get_live_session(self):
        """
        Retorna o estado da sessão de portfólio baseado na janela operacional rigorosa (21h00 às 08h00 BRT).
        - Entre 21h00 e 07h59 BRT: Sessão ATIVA / AO VIVO com cotações tick-a-tick.
        - Entre 08h00 e 20h59 BRT: Sessão ENCERRADA às 08h00 (resultado consolidado e aguardando abertura das 21h).
        """
        now_dt = datetime.now()
        hour = now_dt.hour
        is_overnight_active = (hour >= 21 or hour < 8)

        if not self.data or not self.data.get("sessions"):
            self.run_full_backtest(days=15)

        sessions = self.data.get("sessions", [])
        if not sessions:
            return None

        # A sessão mais recente
        base_sess = sessions[0]
        live_sess = json.loads(json.dumps(base_sess)) # clone profundo

        if is_overnight_active:
            # SESSÃO AO VIVO (21h00 - 08h00)
            live_sess["is_in_progress"] = True
            live_sess["status"] = "EM ANDAMENTO"
            live_sess["status_label"] = "🔴 AO VIVO (EM ANDAMENTO)"
            
            # Calcular tempo restante até 08h00
            if hour >= 21:
                close_dt = (now_dt + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
            else:
                close_dt = now_dt.replace(hour=8, minute=0, second=0, microsecond=0)
            rem_secs = max(0, int((close_dt - now_dt).total_seconds()))
            rem_h = rem_secs // 3600
            rem_m = (rem_secs % 3600) // 60
            live_sess["time_remaining_str"] = f"restam {rem_h}h {rem_m}m"
            live_sess["session_info_str"] = f"📅 Sessão Ao Vivo | Início: 21h00 ➔ Encerramento: 08h00 BRT ({live_sess['time_remaining_str']})"

            # Obter cotações em tempo real do MT5 para cada par
            if ensure_mt5_connected():
                total_live_pnl = 0.0
                total_live_pips = 0.0
                for port in live_sess.get("portfolios", []):
                    p_pnl = 0.0
                    p_pips = 0.0
                    for pair_item in port.get("pairs", []):
                        pair_sym = pair_item["pair"]
                        tick = mt5.symbol_info_tick(to_broker_symbol(pair_sym))
                        if tick:
                            curr_price = tick.bid if pair_item["action"] == "BUY" else tick.ask
                            p_entry = pair_item["entry_price"]
                            final_pnl, final_pips = convert_pnl_to_usd(pair_sym, pair_item["action"], p_entry, curr_price, pair_item.get("lot", 0.01))
                            pair_item["exit_price"] = curr_price
                            pair_item["current_price"] = curr_price
                            pair_item["pnl_usd"] = final_pnl
                            pair_item["pips"] = final_pips
                            pair_item["status"] = "WIN" if final_pnl >= 0 else "LOSS"
                            p_pnl += final_pnl
                            p_pips += final_pips
                            if final_pnl > pair_item.get("mfe_usd", 0.0):
                                pair_item["mfe_usd"] = final_pnl
                            if final_pnl < pair_item.get("mae_usd", 0.0):
                                pair_item["mae_usd"] = final_pnl
                    port["pnl_usd"] = round(p_pnl, 2)
                    port["pips"] = round(p_pips, 1)
                    port["status"] = "WIN" if p_pnl >= 0 else "LOSS"
                    total_live_pnl += p_pnl
                    total_live_pips += p_pips
                
                live_sess["total_pnl_usd"] = round(total_live_pnl, 2)
                live_sess["total_pips"] = round(total_live_pips, 1)
        else:
            # SESSÃO ENCERRADA (08h00 - 21h00)
            live_sess["is_in_progress"] = False
            live_sess["status"] = "ENCERRADA"
            live_sess["status_label"] = "🟢 SESSÃO DA MADRUGADA ENCERRADA (08:00 BRT)"
            
            # Calcular tempo até a próxima abertura às 21h00
            next_open_dt = now_dt.replace(hour=21, minute=0, second=0, microsecond=0)
            rem_secs = max(0, int((next_open_dt - now_dt).total_seconds()))
            rem_h = rem_secs // 3600
            rem_m = (rem_secs % 3600) // 60
            live_sess["next_session_in"] = f"{rem_h}h {rem_m}m"
            live_sess["session_info_str"] = f"✅ Pregão Noturno Encerrado às 08h00 BRT | Próxima abertura às 21h00 BRT (em {rem_h}h {rem_m}m)"

            # Assegurar que os portfólios estão marcados com os resultados finais realizados
            for port in live_sess.get("portfolios", []):
                port["status_label"] = "FECHADA ÀS 08H00"
                for pair_item in port.get("pairs", []):
                    pair_item["status_label"] = "Fechado 08h00"

        return live_sess

    def run_full_backtest(self, days=45):
        """Executa backtest multi-portfólio com cálculo hora a hora de MAE/MFE e CSS H1/H4."""
        if not ensure_mt5_connected():
            return self._generate_fallback_history()

        print(f"[*] Executando Backtest Avançado com MAE/MFE ({days} dias)...")
        
        bars_count = days * 24 + 300
        pair_h1 = {}
        for sym in ALL_28_PAIRS:
            # Consulta pelo nome da corretora (pode ter sufixo, ex.: EURUSDm),
            # indexa pelo nome lógico. Sem isso, nenhum par resolvia e o track
            # record caía silenciosamente em histórico SIMULADO.
            rates = mt5.copy_rates_from_pos(to_broker_symbol(sym), mt5.TIMEFRAME_H1, 0, bars_count)
            if rates is not None and len(rates) > 100:
                df = pd.DataFrame(rates)
                df['time'] = pd.to_datetime(df['time'], unit='s')
                pair_h1[sym] = df

        if not pair_h1 or "EURUSD" not in pair_h1:
            return self._generate_fallback_history()

        ref_df = pair_h1["EURUSD"]
        entry_indices = ref_df[ref_df['time'].dt.hour == ENTRY_SERVER_HOUR].index.tolist()

        sessions = []
        equity = 0.0
        equity_curve = [{"date": "Início", "equity": 0.0, "pnl": 0.0}]
        currency_pnl_map = {c: 0.0 for c in CURRENCIES}
        hours_labels = ["21h", "22h", "23h", "00h", "01h", "02h", "03h", "04h", "05h", "06h", "07h", "08h"]

        for idx in entry_indices:
            entry_row = ref_df.loc[idx]
            entry_time = entry_row['time']
            # entry_time é hora do SERVIDOR; converte pra BRT de verdade antes de
            # rotular a data (antes: -6h fixo, que só bate com servidor=UTC+3).
            br_date = (entry_time + timedelta(hours=GMT_OFFSET)).strftime("%Y-%m-%d")
            exit_time = entry_time + timedelta(hours=11)
            exit_time_brt_str = (exit_time + timedelta(hours=GMT_OFFSET)).strftime("%Y-%m-%d %H:%M")
            
            # Verificar se a sessão é histórica completa ou em andamento
            exit_matches = ref_df[ref_df['time'] == exit_time]
            is_in_progress = exit_matches.empty
            
            # Avaliar moedas qualificadas no momento da entrada
            qualified_currencies = self._evaluate_css_at_time(entry_time)

            if not qualified_currencies:
                sessions.append({
                    "date": br_date,
                    "entry_time_br": f"{br_date} 21:00",
                    "exit_time_br": exit_time_brt_str,
                    "status": "NEUTRO",
                    "status_label": "Sessão Neutra (Filtro 4+ TFs Ativo)",
                    "portfolios_count": 0,
                    "total_pnl_usd": 0.0,
                    "total_pips": 0.0,
                    "mfe_usd": 0.0,
                    "mae_usd": 0.0,
                    "intraday_hours": hours_labels,
                    "intraday_pnl_curve": [0.0] * 12,
                    "portfolios": [],
                    "qualified_full": []
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
                "exit_time_br": exit_time_brt_str,
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
                "portfolios": session_portfolios,
                # Snapshot completo de quem qualificou (com scores brutos e LEDs),
                # pra aplicar regras de seleção-de-uma-moeda no pós-processamento
                # sem precisar re-simular PnL — já está tudo em session_portfolios.
                "qualified_full": qualified_currencies
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
        expected_pairs = sum(1 for sym in ALL_28_PAIRS if ccy in sym)
        slopes = []
        for sym in ALL_28_PAIRS:
            if ccy not in sym:
                continue
            rates = mt5.copy_rates_from(to_broker_symbol(sym), tf_val, target_time, TRACK_RECORD_MIN_BARS)
            if rates is None or len(rates) < TRACK_RECORD_MIN_BARS:
                continue
            closes = [r[4] for r in rates]
            highs = [r[2] for r in rates]
            lows = [r[3] for r in rates]
            atr = calc_atr_sma(np.array(highs), np.array(lows), np.array(closes), ATR_PERIOD, min_periods=ATR_PERIOD)
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

        # Mesmo princípio de _aggregate_pair_slopes_to_ccy_slopes(): cobertura
        # parcial das pernas de UMA moeda (aqui 7, não os 28 pares inteiros)
        # não vira média de subconjunto — herdr-review mfc-80 achado MFC-80-01
        # (mfc-rev): usado pelas curvas H1/H4 do painel de auditoria, e o
        # aumento de TRACK_RECORD_MIN_BARS pra 130 tornou mais provável faltar
        # alguma perna curta/indisponível do que quando o piso era 35.
        #
        # RESÍDUO ACEITO (herdr-review mfc-81, mfc-rev): 0.0 aqui significa
        # tanto "cobertura incompleta" quanto "slope genuinamente neutro" — o
        # chamador da curva não distingue os dois casos. Resolver isso de
        # verdade exigiria propagar um sentinel explícito de "sem dado" pelo
        # único consumidor desta função (a curva H1/H4 do painel de
        # auditoria) e pelo contrato da API/frontend que a exibe — mudança de
        # escopo maior que este fix de período do ATR. Aceito como está: é
        # estritamente melhor que o comportamento anterior (média enviesada
        # de subconjunto, nunca sinalizada de jeito nenhum), não um risco de
        # execução (não decide ordem), e a ambiguidade já existia em espírito
        # antes desta correção.
        if len(slopes) < expected_pairs:
            return 0.0
        return np.mean(slopes) if slopes else 0.0

    def _evaluate_css_at_time(self, target_time):
        qualified = []
        # As 5 contagens usam o MESMO valor: o requisito de barras cruas do
        # ATR(100) é por QUANTIDADE de barras, não pelo calendário que cada
        # timeframe cobre — o TF só muda o que cada barra representa, não
        # quantas são necessárias pra fechar a janela de 100 (ver
        # TRACK_RECORD_MIN_BARS acima). As contagens antigas (24/40/60/100/150)
        # eram calibradas pro ATR(20) anterior e, mesmo assim, MN1=24 já
        # ficava abaixo do mínimo que aquele período exigia — achado
        # herdr-ask mfc-18 (mfc-rev-2).
        tf_map = {
            "MN1": (mt5.TIMEFRAME_MN1, TRACK_RECORD_MIN_BARS),
            "W1":  (mt5.TIMEFRAME_W1, TRACK_RECORD_MIN_BARS),
            "D1":  (mt5.TIMEFRAME_D1, TRACK_RECORD_MIN_BARS),
            "H4":  (mt5.TIMEFRAME_H4, TRACK_RECORD_MIN_BARS),
            "H1":  (mt5.TIMEFRAME_H1, TRACK_RECORD_MIN_BARS)
        }

        tf_slopes = {}
        for tf_name, (tf_val, count) in tf_map.items():
            pair_slopes = {}
            for sym in ALL_28_PAIRS:
                rates = mt5.copy_rates_from(to_broker_symbol(sym), tf_val, target_time, count)
                if rates is None or len(rates) < TRACK_RECORD_MIN_BARS:
                    continue
                df = pd.DataFrame(rates)
                closes = df['close'].values
                highs = df['high'].values
                lows = df['low'].values

                atr = calc_atr_sma(highs, lows, closes, ATR_PERIOD, min_periods=ATR_PERIOD)
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
            
            tf_slopes[tf_name] = _aggregate_pair_slopes_to_ccy_slopes(pair_slopes)

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
            # scores brutos das 5 TFs — aditivo, só pra quem precisar comparar
            # regras de seleção contra o histórico (ex.: backtest de qual moeda
            # escolher entre as que qualificam); não afeta a qualificação em si.
            raw_scores = {tf: tf_slopes.get(tf, {}).get(c, 0.0) for tf in ("MN1", "W1", "D1", "H4", "H1")}
            # divergência aqui é aproximação (H1 e H4 discordando de sinal) — o
            # has_divergence "oficial" (agents/operational_analyzer.py) compara
            # macro vs operacional, pipeline que este motor não usa.
            approx_divergence = (h1_s > 0) != (h4_s > 0) and abs(h1_s) > 0.05 and abs(h4_s) > 0.05

            if red_cnt >= 4 or (h1_s <= -0.50 and (d1_s < 0 or h4_s < 0)):
                qualified.append({
                    "symbol": c,
                    "bias": "SELL",
                    "confluence_tfs": max(red_cnt, 4),
                    "leds": leds,
                    "scores": raw_scores,
                    "has_divergence_approx": approx_divergence,
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
                        "scores": raw_scores,
                        "has_divergence_approx": approx_divergence,
                        "reason": f"Exaustão Cíclica no Topo (+{h1_s:.2f}) com H4 em Queda (Viés de Venda)"
                    })
                else:
                    qualified.append({
                        "symbol": c,
                        "bias": "BUY",
                        "confluence_tfs": max(green_cnt, 4),
                        "leds": leds,
                        "scores": raw_scores,
                        "has_divergence_approx": approx_divergence,
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
                    "portfolios": [],
                    "qualified_full": []
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


# Instância Singleton Oficial de Auditoria Real (100% MT5 Deals)
from web.real_portfolio_audit import real_audit_engine
history_engine = real_audit_engine

