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
import tempfile
from datetime import datetime, timedelta
import threading

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# O .env é carregado em web/css_service.py (módulo mais cedo importado,
# direta ou transitivamente por todo entry point) — a importação abaixo já
# garante que os.environ está populado antes de qualquer leitura própria
# deste módulo (ex.: CATASTROPHIC_SL_PIPS logo adiante).
from web.css_service import (
    ALL_28_PAIRS, CURRENCIES, CCY_FLAGS, CCY_COLORS,
    MT5_AVAILABLE, mt5, MT5_PATH, MT5_SYMBOL_SUFFIX,
    to_broker_symbol, from_broker_symbol
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

# ======================================================================
# TRAVAS DE SEGURANÇA (Fase 1 — pontos independentes das decisões em
# aberto com o Miquéias; ver whatsapp-tools/PLANO_IMPLEMENTACAO_MFC.md)
# ======================================================================

# Kill switch: se este arquivo existir, nenhuma cesta NOVA é aberta.
# Fechar posições continua sempre permitido (reduzir risco nunca é bloqueado).
KILL_SWITCH_FILE = os.path.join(DATA_DIR, "CSS_KILL.flag")

# Stop-loss catastrófico: não é parâmetro de estratégia (a estratégia continua
# sem stop/take-profit na operação normal), é rede de segurança pro cenário
# "Python morto e terminal morto ao mesmo tempo". As três lentes de revisão
# (deep-reasoner, fable-reasoner, codex) concordaram que isso é mais importante
# que a escolha de arquitetura em si. Valor de partida amplo — recalibrar com
# a pior excursão histórica real por perna antes de qualquer conta real.
CATASTROPHIC_SL_PIPS = int(os.environ.get("CSS_CATASTROPHIC_SL_PIPS", "150"))

# Contas em modo netting não têm posição isolada por magic number — duas
# cestas que compartilham um par (ex.: USD e EUR podem operar EURUSD) se
# fundem numa posição líquida só. Sem uma regra de consolidação desenhada,
# a resposta segura é recusar abrir uma segunda cesta que colida em símbolo
# com uma cesta já aberta, em vez de assumir isolamento que a conta não dá.

# Trava de identidade de conta: essa máquina roda vários terminais MT5 ao
# mesmo tempo, cada um logado numa conta diferente (confirmado — 5
# terminal64.exe simultâneos pra estratégias distintas). "a conta é demo"
# sozinho não garante que é A conta certa. Sem CSS_MT5_EXPECTED_LOGIN
# configurado, a abertura é recusada mesmo em conta demo — falha fechado por
# ambiguidade, não só por risco de conta real.
#
# Contas reais só são aceitas com CSS_LIVE_TRADING=true/1 explícito E o login
# batendo com CSS_MT5_EXPECTED_LOGIN — as duas condições, não uma ou outra.
# Configurável via variável de ambiente real ou BASE_DIR/.env (ver
# .env.example); o .env nunca é a fonte de verdade se o sistema real já
# define a variável.


def check_account_identity(safety: dict = None) -> dict:
    """Confirma SÓ a identidade da conta conectada (login esperado), sem a
    trava de demo. Usado no fechamento: reduzir risco nunca pode ser bloqueado
    por a conta ser real — mas precisa ser a conta CERTA, porque esta máquina
    roda vários terminais MT5 com contas diferentes ao mesmo tempo e o binding
    global do pacote MetaTrader5 aponta pra um deles só."""
    if safety is None:
        safety = get_account_safety_info()

    expected_raw = os.environ.get("CSS_MT5_EXPECTED_LOGIN", "").strip()
    if not expected_raw:
        return {
            "allowed": False,
            "error": "no_expected_login_configured",
            "message": "CSS_MT5_EXPECTED_LOGIN não está configurado — não dá pra confirmar "
                       "em qual conta as ordens seriam enviadas.",
        }
    try:
        expected_login = int(expected_raw)
    except ValueError:
        return {
            "allowed": False,
            "error": "invalid_expected_login",
            "message": f"CSS_MT5_EXPECTED_LOGIN={expected_raw!r} não é um número de conta válido.",
        }
    if safety.get("login") != expected_login:
        return {
            "allowed": False,
            "error": "wrong_account",
            "message": f"Conta conectada ({safety.get('login')}) é diferente da esperada "
                       f"({expected_login}) — operação recusada.",
        }
    return {"allowed": True, "error": None, "message": None}


def check_account_gate(safety: dict) -> dict:
    """Confirma identidade e permissão da conta conectada antes de qualquer
    ordem de ABERTURA. Falha fechado em qualquer ambiguidade — ver comentário
    acima. Identidade é delegada a check_account_identity (mesma regra usada
    no fechamento); aqui se soma a trava de demo/live."""
    ident = check_account_identity(safety)
    if not ident["allowed"]:
        return ident

    expected_login = safety.get("login")
    live_allowed = os.environ.get("CSS_LIVE_TRADING", "").strip().lower() in ("1", "true", "yes")
    if not safety.get("is_demo") and not live_allowed:
        return {
            "allowed": False,
            "error": "not_demo_account",
            "message": f"Conta {expected_login} @ {safety.get('server')} não é demo e "
                       f"CSS_LIVE_TRADING não está ligado — abertura recusada.",
        }

    return {"allowed": True, "error": None, "message": None}


class MT5QueryError(RuntimeError):
    """Consulta ao MT5 falhou de forma que não dá pra distinguir de um estado
    válido — sempre tratada como motivo pra RECUSAR operação, nunca pra
    seguir com um resultado vazio."""


def is_kill_switch_active() -> bool:
    """Kill switch por arquivo — funciona mesmo com o resto do sistema fora do ar.
    Verifica dois lugares: a pasta local data/ do projeto (KILL_SWITCH_FILE) e a
    pasta MQL5/Files da instância MT5 portable dedicada ao MFC (onde o EA
    guardião também lê o mesmo arquivo, sem FILE_COMMON — instância exclusiva,
    não compartilhada com outras estratégias, então não há necessidade de sair
    do diretório local dela)."""
    if os.path.exists(KILL_SWITCH_FILE):
        return True
    files_dir = get_mt5_files_dir()
    if files_dir and os.path.exists(os.path.join(files_dir, "CSS_KILL.flag")):
        return True
    return False


def get_account_safety_info():
    """Consulta a conta MT5 conectada e retorna um resumo pra decisão de segurança.
    Nunca lança exceção — se não der pra consultar, retorna is_demo=False
    (fail closed: sem confirmação de que é demo, trata como inseguro)."""
    info = {
        "is_demo": False,
        "login": None,
        "server": None,
        "margin_mode": None,
        "trade_allowed": False,
        "error": None,
    }
    if not MT5_AVAILABLE or mt5 is None:
        info["error"] = "MetaTrader5 indisponível neste ambiente"
        return info
    try:
        acc = mt5.account_info()
        if acc is None:
            info["error"] = "account_info() retornou None (sem conexão ativa)"
            return info
        info["login"] = acc.login
        info["server"] = acc.server
        info["trade_allowed"] = bool(getattr(acc, "trade_allowed", False))
        info["is_demo"] = (acc.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO)
        margin_mode = getattr(acc, "margin_mode", None)
        if margin_mode == getattr(mt5, "ACCOUNT_MARGIN_MODE_RETAIL_NETTING", -1):
            info["margin_mode"] = "netting"
        elif margin_mode == getattr(mt5, "ACCOUNT_MARGIN_MODE_RETAIL_HEDGING", -2):
            info["margin_mode"] = "hedging"
        else:
            info["margin_mode"] = "desconhecido"
    except Exception as e:
        info["error"] = str(e)
    return info


def get_open_magics_and_symbols():
    """Retorna {magic: set(símbolos abertos)} pra todas as posições sob os
    magic numbers dos portfólios — base pra checagem de idempotência e de
    colisão de símbolo entre cestas em conta netting.

    Levanta MT5QueryError se a consulta falhar. É deliberado: a API do MT5
    devolve None tanto pra "nenhuma posição" quanto pra ERRO de consulta, e
    tratar erro como "nada aberto" derrubaria justamente as travas de
    idempotência e de colisão que dependem desta função — reabrindo a cesta
    inteira por cima da existente. Quem chama precisa recusar a operação,
    não seguir em frente com um resultado vazio.
    """
    result = {}
    if not MT5_AVAILABLE or mt5 is None:
        raise MT5QueryError("MetaTrader5 indisponível — não dá pra confirmar posições abertas")
    try:
        positions = mt5.positions_get()
    except Exception as e:
        raise MT5QueryError(f"positions_get() lançou exceção: {e}")
    if positions is None:
        err = None
        try:
            err = mt5.last_error()
        except Exception:
            pass
        raise MT5QueryError(f"positions_get() retornou None (erro de consulta, não 'sem posições'): {err}")
    for pos in positions:
        if pos.magic in ALL_PORTFOLIO_MAGICS:
            # positions_get devolve o nome da corretora (com sufixo); normaliza
            # pro nome lógico pra poder comparar com as listas internas.
            result.setdefault(pos.magic, set()).add(from_broker_symbol(pos.symbol))
    return result


def _pip_size(symbol: str) -> float:
    return 0.01 if "JPY" in symbol else 0.0001


def _compute_catastrophic_sl(symbol: str, order_type_is_buy: bool, entry_price: float):
    """SL amplo em pips fixos — rede de segurança, não parâmetro de estratégia.
    Retorna 0.0 (sem SL) se CATASTROPHIC_SL_PIPS <= 0, permitindo desligar via env
    var só em ambiente de teste; em produção deve ficar sempre > 0."""
    if CATASTROPHIC_SL_PIPS <= 0:
        return 0.0
    distance = CATASTROPHIC_SL_PIPS * _pip_size(symbol)
    return round(entry_price - distance, 5) if order_type_is_buy else round(entry_price + distance, 5)


def _atomic_write_json(path: str, payload: dict):
    """Escreve JSON de forma atômica (tempfile no mesmo diretório + os.replace) —
    evita que um leitor concorrente (o EA lendo a cada 3s, por exemplo) veja um
    arquivo pela metade."""
    directory = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


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

    Antes de qualquer ordem, checa (nessa ordem): kill switch, identidade e
    permissão da conta (check_account_gate — CSS_MT5_EXPECTED_LOGIN e
    CSS_LIVE_TRADING), idempotência (cesta desse magic já aberta hoje?) e
    colisão de símbolo com outra cesta já aberta em conta netting. Qualquer
    uma dessas recusa a abertura inteira sem enviar nenhuma ordem.
    """
    if is_kill_switch_active():
        msg = f"Kill switch ativo ({KILL_SWITCH_FILE}) — nenhuma cesta nova será aberta."
        print(f"[PORTFOLIO ROBOT] {msg}")
        return {"success": False, "error": "kill_switch_active", "message": msg}

    if not ensure_mt5():
        return {"success": False, "error": "MT5 não inicializado"}

    safety = get_account_safety_info()
    gate = check_account_gate(safety)
    if not gate["allowed"]:
        print(f"[PORTFOLIO ROBOT] {gate['message']}")
        return {"success": False, "error": gate["error"], "message": gate["message"], "account": safety}

    ccy = currency.upper()
    magic = PORTFOLIO_MAGICS.get(ccy, 801000)
    pairs = get_portfolio_pairs(ccy, bias)

    try:
        open_magics = get_open_magics_and_symbols()
    except MT5QueryError as e:
        msg = (f"Não foi possível confirmar as posições abertas ({e}) — abertura recusada. "
               f"Sem essa confirmação, idempotência e colisão de símbolo não valem nada.")
        print(f"[PORTFOLIO ROBOT {ccy}] {msg}")
        return {"success": False, "error": "position_query_failed", "message": msg}

    if magic in open_magics:
        msg = f"Cesta {ccy} (magic {magic}) já tem posição aberta — recusando reabrir (idempotência)."
        print(f"[PORTFOLIO ROBOT {ccy}] {msg}")
        return {"success": False, "error": "already_open", "message": msg}

    if safety.get("margin_mode") == "netting":
        target_symbols = {p["pair"] for p in pairs}
        for other_magic, other_symbols in open_magics.items():
            collision = target_symbols & other_symbols
            if collision:
                msg = (f"Conta em modo netting: cesta {ccy} colidiria com o magic {other_magic} "
                       f"já aberto nos símbolos {sorted(collision)} — sem regra de consolidação "
                       f"definida ainda, abertura recusada por segurança.")
                print(f"[PORTFOLIO ROBOT {ccy}] {msg}")
                return {"success": False, "error": "netting_symbol_collision", "message": msg}

    # Preflight de símbolos: resolve os 7 pares ANTES de enviar qualquer ordem.
    # Se algum não resolver, recusa a cesta inteira em vez de abrir uma cesta
    # parcial (uma cesta de 3 pernas é uma aposta direcional nua, não a cesta
    # diversificada que a estratégia pede).
    broker_symbols = {}
    ticks = {}
    unresolved = []
    no_tick = []
    for p in pairs:
        b_sym = to_broker_symbol(p["pair"])
        info = mt5.symbol_info(b_sym)
        if info is None:
            mt5.symbol_select(b_sym, True)
            info = mt5.symbol_info(b_sym)
        if info is None:
            unresolved.append(p["pair"])
            continue
        if not info.visible:
            mt5.symbol_select(b_sym, True)
        broker_symbols[p["pair"]] = b_sym

        # Tick tem que entrar no preflight, não só o símbolo existir: um par
        # recém-adicionado ao Market Watch pelo symbol_select acima pode não
        # ter tick no instante seguinte. Se isso só fosse descoberto no laço
        # de envio, as pernas anteriores já teriam sido abertas e a cesta
        # ficaria parcial — uma aposta direcional nua, sem rollback.
        tick = mt5.symbol_info_tick(b_sym)
        price = None
        if tick is not None:
            price = tick.ask if p["action"] == "BUY" else tick.bid
        if not tick or not price or price <= 0:
            no_tick.append(p["pair"])
            continue
        ticks[p["pair"]] = price

    if unresolved or no_tick:
        partes = []
        if unresolved:
            partes.append(f"não encontrados no servidor: {sorted(unresolved)} "
                          f"(sufixo configurado: {MT5_SYMBOL_SUFFIX!r} — confira CSS_MT5_SYMBOL_SUFFIX)")
        if no_tick:
            partes.append(f"sem cotação válida agora: {sorted(no_tick)}")
        msg = (f"Cesta {ccy} recusada por inteiro antes de qualquer ordem — " + "; ".join(partes) +
               ". Melhor nenhuma perna do que uma cesta parcial.")
        print(f"[PORTFOLIO ROBOT {ccy}] {msg}")
        return {"success": False, "error": "preflight_failed", "message": msg,
                "unresolved": sorted(unresolved), "no_tick": sorted(no_tick)}

    results = []
    success_count = 0

    print(f"\n[PORTFOLIO ROBOT {ccy}] Iniciando abertura da cesta ({bias}) | Magic: {magic} | {len(pairs)} pares...")

    for p in pairs:
        pair_sym = p["pair"]
        broker_sym = broker_symbols[pair_sym]
        action = p["action"]

        order_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL
        # Repuxa a cotação mais recente; se sumiu entre o preflight e agora,
        # cai no preço do preflight (já validado > 0) e deixa o `deviation`
        # do request absorver a diferença — melhor que abandonar a perna e
        # deixar a cesta parcial.
        tick = mt5.symbol_info_tick(broker_sym)
        price = None
        if tick is not None:
            price = tick.ask if action == "BUY" else tick.bid
        if not price or price <= 0:
            price = ticks[pair_sym]

        sl_price = _compute_catastrophic_sl(pair_sym, action == "BUY", price)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": broker_sym,
            "volume": float(lot),
            "type": order_type,
            "price": float(price),
            "sl": float(sl_price),
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

    # Identidade da conta também no FECHAMENTO. ensure_mt5() aceita qualquer
    # terminal já inicializado, e esta máquina roda 5 terminais MT5 logados em
    # contas diferentes — sem esta checagem, o gatilho automático das 08:00
    # pode fechar posições da conta errada, ou não achar as da conta certa e
    # reportar "nada aberto" com a cesta viva em outro terminal.
    ident = check_account_identity()
    if not ident["allowed"]:
        print(f"[PORTFOLIO ROBOT] {ident['message']}")
        return {"success": False, "error": ident["error"], "message": ident["message"]}

    ccy = currency.upper()
    magic = PORTFOLIO_MAGICS.get(ccy)
    if not magic:
        return {"success": False, "error": f"Moeda {ccy} inválida"}

    # positions_get() devolve None em ERRO e tupla vazia em "nenhuma posição".
    # Reportar sucesso no caso de erro seria a pior falha possível aqui: o
    # operador (ou o gatilho das 08:00) veria "fechou tudo" com a cesta viva.
    positions = mt5.positions_get()
    if positions is None:
        err = None
        try:
            err = mt5.last_error()
        except Exception:
            pass
        msg = (f"positions_get() retornou None (erro de consulta, não 'sem posições'): {err} — "
               f"NÃO foi possível confirmar nem fechar as posições de {ccy}.")
        print(f"[PORTFOLIO ROBOT {ccy}] {msg}")
        return {"success": False, "error": "position_query_failed", "message": msg}

    closed_count = 0
    results = []
    # Alvo: quantas posições DESTE magic existiam quando começamos. É contra
    # esse número que o sucesso é medido — não contra "fechou pelo menos uma".
    target_count = sum(1 for p in positions if p.magic == magic)

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
                # Antes era `continue` silencioso: a posição ficava aberta e
                # nem aparecia no resultado, que ainda dizia sucesso.
                results.append({"ticket": ticket, "symbol": symbol, "status": "ERROR",
                                "comment": "tick indisponível — posição NÃO fechada"})
                print(f"  [-] {symbol} ticket {ticket}: tick indisponível, NÃO fechada")
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
                    
    # Sucesso = fechou TUDO que existia deste magic. "Fechou pelo menos uma"
    # (ou "não fechou nenhuma") reportado como sucesso é o pior modo de falha
    # possível aqui: o gatilho das 08:00 anuncia encerramento concluído e a
    # cesta atravessa o dia inteiro sem stop.
    all_closed = (closed_count == target_count)
    if not all_closed:
        print(f"[PORTFOLIO ROBOT {ccy}] ATENÇÃO: fechadas {closed_count} de {target_count} "
              f"posições — {target_count - closed_count} SEGUEM ABERTAS.")
    return {
        "success": all_closed,
        "error": None if all_closed else "partial_close",
        "message": None if all_closed else (
            f"{target_count - closed_count} de {target_count} posições de {ccy} não fecharam."),
        "currency": ccy,
        "magic": magic,
        "closed_count": closed_count,
        "target_count": target_count,
        "results": results
    }


def close_all_portfolios(deviation: int = 15):
    """
    Encerra todos os portfólios gerenciados (Magic Numbers de 801001 a 801008) pontualmente às 08:00 BRT.
    """
    if not ensure_mt5():
        return {"success": False, "error": "MT5 não conectado"}

    # Identidade da conta (ver close_portfolio_basket) — checada aqui também
    # pra falhar cedo e com mensagem clara, antes de iterar as 8 moedas.
    ident = check_account_identity()
    if not ident["allowed"]:
        print(f"[PORTFOLIO ROBOT] {ident['message']}")
        return {"success": False, "error": ident["error"], "message": ident["message"]}

    # Mesma regra do close_portfolio_basket: None é ERRO, não "nada aberto".
    positions = mt5.positions_get()
    if positions is None:
        err = None
        try:
            err = mt5.last_error()
        except Exception:
            pass
        msg = f"positions_get() retornou None (erro de consulta): {err} — fechamento NÃO confirmado."
        print(f"[PORTFOLIO ROBOT] {msg}")
        return {"success": False, "error": "position_query_failed", "message": msg}

    total_closed = 0
    summary_by_ccy = {}
    failures = []

    for ccy in PORTFOLIO_MAGICS:
        # Isolamento por moeda: uma falha não pode impedir o fechamento das
        # outras 7 cestas — fechar é redutor de risco, sempre segue adiante.
        try:
            res = close_portfolio_basket(ccy, deviation=deviation)
        except Exception as e:
            failures.append({"currency": ccy, "error": str(e)})
            print(f"[PORTFOLIO ROBOT {ccy}] Exceção ao fechar: {e}")
            continue
        if not res.get("success", False) and res.get("error"):
            failures.append({"currency": ccy, "error": res.get("error"), "message": res.get("message")})
        if res.get("closed_count", 0) > 0:
            summary_by_ccy[ccy] = res
            total_closed += res["closed_count"]
            
    print(f"\n[ENCERRAMENTO 08:00 BRT] Total de posições fechadas: {total_closed}")
    if failures:
        print(f"[ENCERRAMENTO 08:00 BRT] ATENÇÃO — falhas por moeda: {failures}")
    return {
        "success": not failures,
        "total_closed": total_closed,
        "currencies_closed": summary_by_ccy,
        "failures": failures,
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


def get_mt5_files_dir():
    """Retorna a pasta MQL5/Files da instância MT5 portable dedicada ao MFC,
    derivada de MT5_PATH (CSS_MT5_TERMINAL_PATH). Em modo /portable cada
    instância mantém seus próprios arquivos localmente — como essa instância é
    exclusiva do MFC (não compartilhada com outras estratégias/contas na mesma
    máquina), não há necessidade de FILE_COMMON pra Python e o EA guardião
    enxergarem o mesmo arquivo; o diretório local já basta."""
    if not MT5_PATH:
        return None
    terminal_dir = os.path.dirname(MT5_PATH)
    if not terminal_dir:
        return None
    files_path = os.path.join(terminal_dir, "MQL5", "Files")
    try:
        os.makedirs(files_path, exist_ok=True)
    except OSError:
        return None
    return files_path


def generate_and_save_daily_signals(currencies_data=None, mt5_connected=None):
    """
    Grava pontualmente às 21:02 BRT o arquivo oficial de sinais dos 8 portfólios
    (BUY, SELL, NEUTRAL) no diretório do projeto e na pasta MQL5/Files da
    instância MT5 dedicada.
    """
    now_dt = datetime.now()
    date_str = now_dt.strftime("%Y-%m-%d")
    weekday = now_dt.weekday() # 0=Seg, 4=Sex, 5=Sab, 6=Dom
    
    # Trava de Segurança: Não operar no final de semana (Sexta após 20h / Sábado)
    is_weekend = (weekday == 4 and now_dt.hour >= 20) or (weekday == 5)
    
    portfolios_signals = {}

    # Origem dos dados: quando o MT5 está fora, css_engine.update_data() cai
    # em cache antigo / css_standard.json versionado / série SIMULADA. Só é
    # tratado como operável o que veio de conexão MT5 confirmada.
    #
    # FAIL-CLOSED: quem passa `currencies_data` pronto DEVE declarar a
    # procedência em `mt5_connected`. Sem declaração explícita, o sinal sai
    # bloqueado — antes o default era "live", e o caminho da rotina das 21:00
    # (daily_css_routine.py, que descarta o flag do update_data) entrava por
    # aí carimbando tudo como live incondicionalmente.
    if not currencies_data:
        from web.css_service import css_engine
        raw_res = css_engine.update_data(force=False)
        currencies_data = raw_res.get("currencies", [])
        data_is_live = bool(raw_res.get("mt5_connected", False))
    else:
        data_is_live = bool(mt5_connected)
        if mt5_connected is None:
            print("[!] generate_and_save_daily_signals recebeu currencies_data sem declarar "
                  "mt5_connected — tratando como NÃO-live (fail closed).")

    if not data_is_live:
        print("[!] SINAL NÃO-OPERÁVEL: dados sem conexão MT5 confirmada "
              "(cache/simulado/procedência não declarada). Todos os portfólios saem BLOCKED.")

    for c in currencies_data:
        sym = c.get("symbol", "")
        if sym not in PORTFOLIO_MAGICS:
            continue
            
        magic = PORTFOLIO_MAGICS[sym]
        trade_bias = c.get("trade_bias", "NEUTRO").upper()
        
        if not data_is_live:
            direction = "NEUTRAL"
            status = "BLOCKED"
            reason = "Dados sem conexão MT5 confirmada (cache/simulado) — não operável"
        elif is_weekend:
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
        # Atesta a ORIGEM dos dados, não só a hora da escrita: quem consome
        # precisa distinguir "analisado com o MT5 conectado" de "cache/simulado".
        "mt5_connected": data_is_live,
        "portfolios": portfolios_signals
    }
    
    # 1. Salvar no projeto local (data/) — escrita atômica: quem lê nunca vê
    #    um arquivo pela metade (relevante pro EA, que lê a cada 3s).
    try:
        _atomic_write_json(SIGNALS_FILE, signals_payload)
        print(f"[+] Sinais de Portfólio gravados localmente em: {SIGNALS_FILE}")
    except Exception as e:
        print(f"[-] Erro ao gravar sinais locais: {e}")

    # 2. Salvar na pasta MQL5/Files da instância MT5 dedicada — mesma garantia atômica.
    files_dir = get_mt5_files_dir()
    if files_dir:
        mt5_signals_path = os.path.join(files_dir, "CSS_Portfolio_Signals.json")
        try:
            _atomic_write_json(mt5_signals_path, signals_payload)
            print(f"[+] Sinais de Portfólio sincronizados com MT5: {mt5_signals_path}")
        except Exception as e:
            print(f"[-] Erro ao gravar sinais na pasta MQL5/Files do MT5: {e}")
    else:
        # Antes isso era um `if` sem `else`: a ponte pro EA não era escrita e
        # nada avisava. Se o EA for usado como leitor (modo legado), ele fica
        # com o sinal de ontem sem ninguém perceber.
        print(f"[-] ATENÇÃO: não foi possível resolver a pasta MQL5/Files a partir de "
              f"MT5_PATH={MT5_PATH!r} — o sinal NÃO foi entregue ao terminal MT5.")

    return signals_payload
