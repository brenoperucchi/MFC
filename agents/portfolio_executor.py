"""
EXECUTOR DE PORTFÓLIOS MULTI-MOEDA MT5 & TELEMETRIA EM TEMPO REAL
Gerencia 8 Robôs de Portfólio (USD, EUR, GBP, CHF, JPY, AUD, CAD, NZD) com Magic Numbers independentes.
Regra de Negócio:
  - Às 21:05 BRT: Abre cestas de 7 pares para moedas qualificadas (Força/Fraqueza).
  - Durante a madrugada (21:05 às 07:59): Monitora e gera telemetria em tempo real para o site.
  - Às 08:00 BRT: Encerra todas as posições dos portfólios pontualmente a mercado.
"""

import os
import sys
import json
import time
from datetime import datetime, timedelta
import threading

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from web.css_service import (
    ALL_28_PAIRS, CURRENCIES, CCY_FLAGS, CCY_COLORS,
    MT5_AVAILABLE, mt5, MT5_PATH
)
from web.history_tracker import convert_pnl_to_usd

DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
TELEMETRY_FILE = os.path.join(DATA_DIR, "live_portfolio_telemetry.json")

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


def ensure_mt5():
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


def get_portfolio_pairs(currency: str, bias: str):
    """
    Retorna a lista dos 7 pares da moeda alvo com a respectiva ação (BUY/SELL).
    
    Exemplo para CAD com bias FORÇA (BUY):
      - Se Base for CAD (CADCHF, CADJPY) -> BUY
      - Se Cotada for CAD (USDCAD, EURCAD, GBPCAD, AUDCAD, NZDCAD) -> SELL
      
    Exemplo para CAD com bias FRAQUEZA (SELL):
      - Se Base for CAD -> SELL
      - Se Cotada for CAD -> BUY
    """
    ccy = currency.upper()
    bias = bias.upper()
    pairs_list = []
    
    for pair in ALL_28_PAIRS:
        if ccy not in pair:
            continue
        base = pair[:3]
        quote = pair[3:6]
        
        if bias == "BUY": # FORÇA
            action = "BUY" if base == ccy else "SELL"
        else: # FRAQUEZA
            action = "SELL" if base == ccy else "BUY"
            
        pairs_list.append({
            "pair": pair,
            "base": base,
            "quote": quote,
            "target_currency": ccy,
            "bias": bias,
            "action": action
        })
        
    return pairs_list


def open_portfolio_basket(currency: str, bias: str, lot: float = 0.01, deviation: int = 15):
    """
    Envia ordens a mercado no MT5 para os 7 pares do portfólio especificado
    com o Magic Number exclusivo da moeda.
    """
    if not ensure_mt5():
        return {"success": False, "error": "MT5 não inicializado"}

    ccy = currency.upper()
    magic = PORTFOLIO_MAGICS.get(ccy, 801000)
    pairs = get_portfolio_pairs(ccy, bias)
    
    results = []
    success_count = 0
    
    print(f"\n[PORTFOLIO ROBOT {ccy}] Iniciando abertura da cesta ({bias}) | Magic: {magic} | {len(pairs)} pares...")
    
    for p in pairs:
        pair_sym = p["pair"]
        action = p["action"]
        
        # Verificar se o par está habilitado no Market Watch do MT5
        symbol_info = mt5.symbol_info(pair_sym)
        if symbol_info is None:
            mt5.symbol_select(pair_sym, True)
            symbol_info = mt5.symbol_info(pair_sym)
            
        if symbol_info is None:
            results.append({"pair": pair_sym, "action": action, "status": "ERROR", "message": "Símbolo não encontrado"})
            continue
            
        if not symbol_info.visible:
            mt5.symbol_select(pair_sym, True)

        order_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL
        tick = mt5.symbol_info_tick(pair_sym)
        if not tick:
            results.append({"pair": pair_sym, "action": action, "status": "ERROR", "message": "Tick indisponível"})
            continue
            
        price = tick.ask if action == "BUY" else tick.bid
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pair_sym,
            "volume": float(lot),
            "type": order_type,
            "price": float(price),
            "deviation": deviation,
            "magic": int(magic),
            "comment": f"CSS_{ccy}_{bias}_{pair_sym}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        
        res = mt5.order_send(request)
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            success_count += 1
            results.append({
                "pair": pair_sym,
                "action": action,
                "lot": lot,
                "ticket": res.order,
                "entry_price": res.price or price,
                "status": "OPENED",
                "message": "Ordem executada com sucesso"
            })
            print(f"  [+] {pair_sym} {action} {lot} @ {res.price or price:.5f} | Ticket: {res.order}")
        else:
            # Tentar fallback com filling RETURN caso IOC falhe
            request["type_filling"] = mt5.ORDER_FILLING_RETURN
            res2 = mt5.order_send(request)
            if res2 and res2.retcode == mt5.TRADE_RETCODE_DONE:
                success_count += 1
                results.append({
                    "pair": pair_sym,
                    "action": action,
                    "lot": lot,
                    "ticket": res2.order,
                    "entry_price": res2.price or price,
                    "status": "OPENED",
                    "message": "Ordem executada com sucesso (RETURN)"
                })
                print(f"  [+] {pair_sym} {action} {lot} @ {res2.price or price:.5f} | Ticket: {res2.order}")
            else:
                err_code = res.retcode if res else "N/A"
                err_desc = res.comment if res else "Falha de envio"
                results.append({
                    "pair": pair_sym,
                    "action": action,
                    "status": "ERROR",
                    "error_code": err_code,
                    "message": err_desc
                })
                print(f"  [-] {pair_sym} {action} Falhou: {err_code} - {err_desc}")

    return {
        "success": success_count > 0,
        "currency": ccy,
        "bias": bias,
        "magic": magic,
        "opened_count": success_count,
        "total_pairs": len(pairs),
        "results": results
    }


def close_portfolio_basket(currency: str, deviation: int = 15):
    """
    Fecha todas as posições abertas pertencentes ao Magic Number do portfólio da moeda.
    """
    if not ensure_mt5():
        return {"success": False, "error": "MT5 não inicializado"}
        
    ccy = currency.upper()
    magic = PORTFOLIO_MAGICS.get(ccy)
    if not magic:
        return {"success": False, "error": f"Moeda {ccy} inválida"}
        
    positions = mt5.positions_get()
    if positions is None:
        return {"success": True, "closed_count": 0, "message": "Nenhuma posição aberta"}
        
    closed_count = 0
    results = []
    
    print(f"\n[PORTFOLIO ROBOT {ccy}] Fechando posições | Magic: {magic}...")
    
    for pos in positions:
        if pos.magic == magic:
            symbol = pos.symbol
            ticket = pos.ticket
            lot = pos.volume
            pos_type = pos.type
            
            close_type = mt5.ORDER_TYPE_SELL if pos_type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
            tick = mt5.symbol_info_tick(symbol)
            if not tick:
                continue
            close_price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask
            
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": lot,
                "type": close_type,
                "position": ticket,
                "price": close_price,
                "deviation": deviation,
                "magic": magic,
                "comment": f"CSS_{ccy}_CLOSE",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            
            res = mt5.order_send(request)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                closed_count += 1
                results.append({"ticket": ticket, "symbol": symbol, "status": "CLOSED", "price": close_price, "profit": pos.profit})
                print(f"  [x] Fechado {symbol} #{ticket} @ {close_price:.5f} | Profit: ${pos.profit:+.2f}")
            else:
                request["type_filling"] = mt5.ORDER_FILLING_RETURN
                res2 = mt5.order_send(request)
                if res2 and res2.retcode == mt5.TRADE_RETCODE_DONE:
                    closed_count += 1
                    results.append({"ticket": ticket, "symbol": symbol, "status": "CLOSED", "price": close_price, "profit": pos.profit})
                    print(f"  [x] Fechado {symbol} #{ticket} @ {close_price:.5f} | Profit: ${pos.profit:+.2f}")
                else:
                    results.append({"ticket": ticket, "symbol": symbol, "status": "ERROR", "comment": res.comment if res else ""})
                    
    return {
        "success": True,
        "currency": ccy,
        "magic": magic,
        "closed_count": closed_count,
        "results": results
    }


def close_all_portfolios(deviation: int = 15):
    """
    Encerra todos os portfólios gerenciados (Magic Numbers de 801001 a 801008) pontualmente às 08:00 BRT.
    """
    if not ensure_mt5():
        return {"success": False, "error": "MT5 não conectado"}
        
    positions = mt5.positions_get()
    if positions is None:
        return {"success": True, "total_closed": 0, "message": "Nenhuma posição aberta no MT5"}
        
    total_closed = 0
    summary_by_ccy = {}
    
    for ccy in PORTFOLIO_MAGICS:
        res = close_portfolio_basket(ccy, deviation=deviation)
        if res.get("closed_count", 0) > 0:
            summary_by_ccy[ccy] = res
            total_closed += res["closed_count"]
            
    print(f"\n[ENCERRAMENTO 08:00 BRT] Total de posições fechadas: {total_closed}")
    return {
        "success": True,
        "total_closed": total_closed,
        "currencies_closed": summary_by_ccy,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }


def get_live_portfolio_telemetry():
    """
    Lê todas as posições abertas reais no MT5 filtradas pelos Magic Numbers dos portfólios,
    calcula o PnL flutuante consolidado em USD, pips acumulados, tempo restante da sessão
    e gera o snapshot de telemetria para o Web Dashboard.
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
    
    telemetry = {
        "timestamp": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "is_session_active": is_session_active,
        "time_remaining_str": f"{rem_h}h {rem_m}m",
        "session_label": "🔴 PREGÃO NOTURNO AO VIVO (21:05 ➔ 08:00 BRT)" if is_session_active else "🟢 SESSÃO ENCERRADA (AGUARDANDO 21:05 BRT)",
        "total_floating_pnl_usd": 0.0,
        "total_floating_pips": 0.0,
        "total_open_positions": 0,
        "active_portfolios_count": 0,
        "portfolios": {}
    }
    
    if not ensure_mt5():
        telemetry["mt5_connected"] = False
        return telemetry
        
    telemetry["mt5_connected"] = True
    positions = mt5.positions_get()
    
    if positions is None or len(positions) == 0:
        # Salvar e retornar vazio
        _save_telemetry_file(telemetry)
        return telemetry
        
    # Agrupar por Moeda / Magic Number
    magic_to_ccy = {v: k for k, v in PORTFOLIO_MAGICS.items()}
    portfolios_data = {}
    
    for pos in positions:
        if pos.magic in magic_to_ccy:
            ccy = magic_to_ccy[pos.magic]
            if ccy not in portfolios_data:
                portfolios_data[ccy] = {
                    "currency": ccy,
                    "flag": CCY_FLAGS.get(ccy, ""),
                    "color": CCY_COLORS.get(ccy, "#FFF"),
                    "magic": pos.magic,
                    "total_pnl_usd": 0.0,
                    "total_pips": 0.0,
                    "positions_count": 0,
                    "pairs": []
                }
                
            pair_sym = pos.symbol
            action = "BUY" if pos.type == mt5.POSITION_TYPE_BUY else "SELL"
            p_entry = pos.price_open
            p_current = pos.price_current
            pos_profit = pos.profit
            
            pnl_usd, pips = convert_pnl_to_usd(pair_sym, action, p_entry, p_current, pos.volume)
            
            # Preferir o lucro real informado pelo MT5 se disponível em USD
            final_pnl = round(pos_profit, 2)
            
            portfolios_data[ccy]["total_pnl_usd"] += final_pnl
            portfolios_data[ccy]["total_pips"] += pips
            portfolios_data[ccy]["positions_count"] += 1
            telemetry["total_open_positions"] += 1
            telemetry["total_floating_pnl_usd"] += final_pnl
            telemetry["total_floating_pips"] += pips
            
            portfolios_data[ccy]["pairs"].append({
                "ticket": pos.ticket,
                "pair": pair_sym,
                "action": action,
                "lot": pos.volume,
                "entry_price": p_entry,
                "current_price": p_current,
                "pnl_usd": final_pnl,
                "pips": pips,
                "open_time": datetime.fromtimestamp(pos.time).strftime("%H:%M:%S") if pos.time else "--:--"
            })

    # Arredondar valores
    telemetry["total_floating_pnl_usd"] = round(telemetry["total_floating_pnl_usd"], 2)
    telemetry["total_floating_pips"] = round(telemetry["total_floating_pips"], 1)
    telemetry["active_portfolios_count"] = len(portfolios_data)
    
    for ccy, p_info in portfolios_data.items():
        p_info["total_pnl_usd"] = round(p_info["total_pnl_usd"], 2)
        p_info["total_pips"] = round(p_info["total_pips"], 1)
        p_info["status"] = "WIN" if p_info["total_pnl_usd"] >= 0 else "LOSS"
        
    telemetry["portfolios"] = portfolios_data
    _save_telemetry_file(telemetry)
    return telemetry


SIGNALS_FILE = os.path.join(DATA_DIR, "portfolio_signals_live.json")


def get_mt5_common_dir():
    """Retorna o caminho da pasta Comum do MetaTrader 5 (FILE_COMMON) no Windows."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        common_path = os.path.join(appdata, "MetaQuotes", "Terminal", "Common", "Files")
        os.makedirs(common_path, exist_ok=True)
        return common_path
    return None


def generate_and_save_daily_signals(currencies_data=None):
    """
    Grava pontualmente às 21:02 BRT o arquivo oficial de sinais dos 8 portfólios
    (BUY, SELL, NEUTRAL) no diretório do projeto e na pasta Common do MT5.
    """
    now_dt = datetime.now()
    date_str = now_dt.strftime("%Y-%m-%d")
    weekday = now_dt.weekday() # 0=Seg, 4=Sex, 5=Sab, 6=Dom
    
    # Trava de Segurança: Não operar no final de semana (Sexta após 20h / Sábado)
    is_weekend = (weekday == 4 and now_dt.hour >= 20) or (weekday == 5)
    
    portfolios_signals = {}
    
    # Se não receber lista pronta, calcular via css_engine
    if not currencies_data:
        from web.css_service import css_engine
        raw_res = css_engine.update_data(force=False)
        currencies_data = raw_res.get("currencies", [])
        
    for c in currencies_data:
        sym = c.get("symbol", "")
        if sym not in PORTFOLIO_MAGICS:
            continue
            
        magic = PORTFOLIO_MAGICS[sym]
        trade_bias = c.get("trade_bias", "NEUTRO").upper()
        
        if is_weekend:
            direction = "NEUTRAL"
            status = "BLOCKED"
            reason = "Mercado Fechado no Final de Semana (Preservação de Capital)"
        elif "COMPRA" in trade_bias or "BUY" in trade_bias or "FORÇA" in trade_bias:
            direction = "BUY"
            status = "ACTIVE"
            reason = c.get("final_verdict") or c.get("confluence_state") or "Força Relativa Confirmada"
        elif "VENDA" in trade_bias or "SELL" in trade_bias or "FRAQUEZA" in trade_bias:
            direction = "SELL"
            status = "ACTIVE"
            reason = c.get("final_verdict") or c.get("confluence_state") or "Fraqueza Relativa Confirmada"
        else:
            direction = "NEUTRAL"
            status = "BLOCKED"
            reason = c.get("final_verdict") or c.get("confluence_state") or "Sem Confluência Direcional Suficiente"
            
        triads = c.get("triads", {})
        d1_score = triads.get("D1", {}).get("score", 0.0)
        h4_score = triads.get("H4", {}).get("score", 0.0)
        
        portfolios_signals[sym] = {
            "magic": magic,
            "direction": direction,
            "status": status,
            "d1_score": round(float(d1_score), 3) if isinstance(d1_score, (int, float)) else 0.0,
            "h4_score": round(float(h4_score), 3) if isinstance(h4_score, (int, float)) else 0.0,
            "confluence_state": c.get("confluence_state", ""),
            "reason": reason
        }

    # Preencher moedas ausentes com NEUTRAL
    for sym, magic in PORTFOLIO_MAGICS.items():
        if sym not in portfolios_signals:
            portfolios_signals[sym] = {
                "magic": magic,
                "direction": "NEUTRAL",
                "status": "BLOCKED",
                "reason": "Sem dados suficientes"
            }

    signals_payload = {
        "timestamp": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "date": date_str,
        "session_id": f"{date_str}_NIGHT",
        "entry_time_brt": "21:05:00",
        "exit_time_brt": "08:00:00",
        "portfolios": portfolios_signals
    }
    
    # 1. Salvar no projeto local (data/)
    try:
        with open(SIGNALS_FILE, "w", encoding="utf-8") as f:
            json.dump(signals_payload, f, indent=2, ensure_ascii=False)
        print(f"[+] Sinais de Portfólio gravados localmente em: {SIGNALS_FILE}")
    except Exception as e:
        print(f"[-] Erro ao gravar sinais locais: {e}")
        
    # 2. Salvar na pasta FILE_COMMON do MetaTrader 5
    common_dir = get_mt5_common_dir()
    if common_dir:
        mt5_signals_path = os.path.join(common_dir, "CSS_Portfolio_Signals.json")
        try:
            with open(mt5_signals_path, "w", encoding="utf-8") as f:
                json.dump(signals_payload, f, indent=2, ensure_ascii=False)
            print(f"[+] Sinais de Portfólio sincronizados com MT5 (FILE_COMMON): {mt5_signals_path}")
        except Exception as e:
            print(f"[-] Erro ao gravar sinais no MT5 Common: {e}")
            
    return signals_payload
