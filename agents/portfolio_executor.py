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
import math
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
    to_broker_symbol, from_broker_symbol, reset_family_detection_cooldown
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
def _env_number(name, default, cast=int):
    """Lê variável numérica sem derrubar o IMPORT do módulo se vier lixo.
    Um valor inválido aqui tirava do ar o servidor e o daemon inteiros, de
    forma silenciosa quando rodando sob o Task Scheduler."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return cast(raw.strip())
    except (TypeError, ValueError):
        print(f"[!] {name}={raw!r} inválido — usando o padrão {default}.")
        return default


# Fonte única de (nome, default, cast, validador, descrição de domínio) pras
# cinco variáveis de segurança tunáveis — achado em revisão (mfc-rev-2,
# herdr-review rodada 7, P1-1, confiança alta, confirmado por mfc-rev
# independentemente): a primeira versão de check_execution_config() (mais
# abaixo) validava CSS_CATASTROPHIC_SL_PIPS e CSS_AMBIGUOUS_CONFIRM_ATTEMPTS
# com float(), mas o cast REAL que produz o valor usado é int() — um
# "50.0" passava no gate (float aceita) e falhava no cast real (a linha
# abaixo cai no default 150/3 em silêncio, exatamente o fail-open que o
# gate existe pra fechar). Medido: CSS_CATASTROPHIC_SL_PIPS="50.0" abria a
# cesta com SL de 150 pips, não os 50 que o operador escreveu. Uma tabela
# só, usada tanto pra calcular a constante quanto pra validar no gate, torna
# essa divergência de cast estruturalmente impossível — as duas usam o
# mesmo cast por construção, não por disciplina de manter dois lugares
# sincronizados (que já falhou uma vez).
_EXECUTION_CONFIG_SPEC = (
    # (env_name, default, cast, validador_do_valor_já_castado, descrição_do_domínio)
    ("CSS_MAX_LOT", 0.01, float,
     lambda v: math.isfinite(v) and v > 0,
     "precisa ser um número finito > 0"),
    ("CSS_MAX_CONCURRENT_BASKETS", 8, int,
     lambda v: v >= 0,
     "não pode ser negativo"),
    ("CSS_CATASTROPHIC_SL_PIPS", 150, int,
     lambda v: v > 0,
     "precisa ser um inteiro > 0 — a rede de segurança não pode ser desligada implicitamente"),
    ("CSS_AMBIGUOUS_CONFIRM_ATTEMPTS", 3, int,
     lambda v: 1 <= v <= 10,
     "precisa ser um inteiro entre 1 e 10"),
    ("CSS_AMBIGUOUS_CONFIRM_DELAY_SEC", 1.0, float,
     lambda v: math.isfinite(v) and 0.0 <= v <= 10.0,
     "precisa ser um número finito entre 0.0 e 10.0"),
)


def _spec_env_number(name):
    """_env_number() castando pelo tipo declarado em _EXECUTION_CONFIG_SPEC —
    nunca um cast solto duplicado à mão, pra check_execution_config() não
    poder divergir de qual tipo é realmente usado."""
    for env_name, default, cast, _validador, _descricao in _EXECUTION_CONFIG_SPEC:
        if env_name == name:
            return _env_number(env_name, default, cast)
    raise KeyError(f"{name} não está em _EXECUTION_CONFIG_SPEC")


CATASTROPHIC_SL_PIPS = _spec_env_number("CSS_CATASTROPHIC_SL_PIPS")

# Tetos de exposição. Nenhum deles altera a estratégia nos valores padrão
# (lote fixo 0.01, até 8 cestas — uma por moeda): existem pra impedir que um
# erro de chamada, um payload de API malformado ou uma mudança acidental de
# parâmetro vire uma posição muito maior que a pretendida.
MAX_LOT = _spec_env_number("CSS_MAX_LOT")
MAX_CONCURRENT_BASKETS = _spec_env_number("CSS_MAX_CONCURRENT_BASKETS")

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


def check_execution_config() -> dict:
    """Valida, no momento do USO (não no import), que os limites de segurança
    configurados via `.env` valem o que o operador de fato escreveu — não um
    default silencioso. Achado em revisão (Codex, herdr-review rodada 6,
    F-06; design consultado via herdr-ask, mfc-rev + mfc-rev-2, 2026-08-27):
    `_env_number()` nunca derruba o IMPORT (deliberado — um valor inválido
    crashava servidor E daemon inteiros sob o Task Scheduler antes desta
    função existir, ver comentário de `_env_number` acima), mas isso também
    deixava "valor explicitamente fornecido e inválido" abrir cesta com um
    default que pode ser o OPOSTO da intenção do operador — ex.: reduzir
    CSS_MAX_CONCURRENT_BASKETS depois de um incidente, e um typo silenciosamente
    volta pro 8 de sempre.

    Validado aqui, em separado do import, e não num registro global paralelo
    (mesmo padrão de `check_account_identity`): `os.environ` não muda durante
    a vida do processo — o `.env` é lido uma vez em `web/css_service.py` — então
    validar no uso pega exatamente os mesmos casos que um registro no import
    pegaria, sem precisar manter um segundo mecanismo em sincronia (e sem o
    risco de alguém adicionar uma variável nova esquecendo de populá-lo — foi
    assim que CSS_AMBIGUOUS_CONFIRM_ATTEMPTS/_DELAY_SEC abaixo escapavam de
    qualquer validação até esta função existir).

    Cobre as três formas de "valor usado ≠ valor escrito" que já existem no
    arquivo: cast que falha (`_env_number` cai no default), clamp que altera
    sem avisar (`_clamp` nos dois CSS_AMBIGUOUS_CONFIRM_*), e faixa proibida
    que CASTA mas não devia passar — `CSS_CATASTROPHIC_SL_PIPS <= 0` desarma
    a rede de segurança (`_compute_catastrophic_sl` devolve sl=0.0) sem erro
    de cast nenhum, então `_env_number` nem chega a imprimir aviso.

    Percorre `_EXECUTION_CONFIG_SPEC` — a MESMA tabela que define o cast das
    constantes de módulo (achado em revisão: mfc-rev-2, herdr-review rodada
    7, P1-1) — em vez de repetir `float`/`int` soltos aqui: a primeira versão
    desta função validava CSS_CATASTROPHIC_SL_PIPS e
    CSS_AMBIGUOUS_CONFIRM_ATTEMPTS com `float()`, mas o cast real dessas duas
    é `int()` — um "50.0" passava aqui e caía no default 150/3 em silêncio no
    cast real, o fail-open exato que este gate existe pra fechar."""
    erros = []
    for name, _default, cast, validador, descricao in _EXECUTION_CONFIG_SPEC:
        raw = os.environ.get(name, "").strip()
        if not raw:
            continue
        try:
            valor = cast(raw)
        except (TypeError, ValueError):
            erros.append(f"{name}={raw!r} não é um valor válido ({descricao})")
            continue
        if not validador(valor):
            erros.append(f"{name}={raw!r} {descricao}")

    if erros:
        return {
            "allowed": False,
            "error": "invalid_execution_config",
            "message": "Configuração de execução inválida no .env, abertura recusada: "
                       + "; ".join(erros),
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
    Retorna 0.0 (sem SL) se CATASTROPHIC_SL_PIPS <= 0 — usado só em teste
    unitário desta função isolada (`patch.object(pe, "CATASTROPHIC_SL_PIPS", 0)`).
    Em qualquer abertura REAL via `open_portfolio_basket()`, `<= 0`
    explicitamente configurado no `.env` já é recusado antes de chegar aqui
    por `check_execution_config()` (achado F-06/F06-1) — esta função nunca
    vê CATASTROPHIC_SL_PIPS <= 0 vindo de uma abertura de verdade."""
    if CATASTROPHIC_SL_PIPS <= 0:
        return 0.0
    distance = CATASTROPHIC_SL_PIPS * _pip_size(symbol)
    return round(entry_price - distance, 5) if order_type_is_buy else round(entry_price + distance, 5)


# Retcodes em que NÃO dá pra saber se a ordem executou: a resposta se perdeu,
# mas o servidor pode ter preenchido. Reenviar às cegas dobraria a perna.
_AMBIGUOUS_RETCODE_NAMES = (
    "TRADE_RETCODE_TIMEOUT",
    "TRADE_RETCODE_CONNECTION",
    "TRADE_RETCODE_DONE_PARTIAL",
    "TRADE_RETCODE_PLACED",
)


def _ambiguous_retcodes():
    codes = set()
    if MT5_AVAILABLE and mt5 is not None:
        for name in _AMBIGUOUS_RETCODE_NAMES:
            v = getattr(mt5, name, None)
            if v is not None:
                codes.add(v)
    return codes


def _confirmed_position_volume(broker_symbol: str, magic: int):
    """Soma o volume das posições abertas com esse magic+símbolo, ou None se
    não deu pra consultar ou não achou nenhuma. Volume, não só existência
    (achado em revisão): uma checagem que só confirma 'existe alguma posição'
    contava um DONE_PARTIAL como perna CHEIA, mesmo quando só uma fração do
    lote pedido de fato executou — quem chama usa o volume real pra reportar
    o que realmente abriu, não o que foi pedido."""
    if not MT5_AVAILABLE or mt5 is None:
        return None
    try:
        positions = mt5.positions_get(symbol=broker_symbol)
    except Exception:
        return None
    if positions is None:
        return None
    total = sum(p.volume for p in positions if p.magic == magic)
    return total if total > 0 else None


# Tentativas de confirmar a posição depois de um retcode ambíguo, e o
# intervalo entre elas. Existem porque uma checagem ÚNICA e IMEDIATA pode não
# encontrar a posição só porque ela ainda não propagou no lado do broker —
# isso NÃO é o mesmo que "confirmado que não abriu". TRADE_RETCODE_PLACED em
# particular significa "aceita, ainda processando": consultar milissegundos
# depois e não achar nada é "ainda não", não "nunca".
#
# Clamp deliberado (achado em revisão): _env_number só garante que o valor
# CASTOU pro tipo certo, não que faz sentido. Sem limite, um typo tipo
# CSS_AMBIGUOUS_CONFIRM_ATTEMPTS=300000 travaria a abertura da cesta inteira
# por horas dentro do laço de envio, e um delay negativo/NaN faz time.sleep()
# lançar ValueError no meio da cesta, abortando as pernas restantes sem
# nenhum rollback. Faixa escolhida: generosa o bastante pra absorver
# propagação normal do broker, pequena o bastante pra nunca travar por muito
# tempo um caminho que já é síncrono e envia dinheiro real.
def _clamp(value, lo, hi):
    if value != value:  # NaN nunca é igual a si mesmo
        return lo
    return max(lo, min(hi, value))


_AMBIGUOUS_CONFIRM_ATTEMPTS = _clamp(_spec_env_number("CSS_AMBIGUOUS_CONFIRM_ATTEMPTS"), 1, 10)
_AMBIGUOUS_CONFIRM_DELAY_SEC = _clamp(_spec_env_number("CSS_AMBIGUOUS_CONFIRM_DELAY_SEC"), 0.0, 10.0)

# Deadline do watchdog de FECHAMENTO — achado em revisão (mfc-rev + mfc-rev-2,
# herdr-review rodada 10, P2-1/F10-3, confirmado pelos dois independentemente):
# a primeira versão do watchdog de close_all_portfolios() reusava
# CSS_AMBIGUOUS_CONFIRM_ATTEMPTS/_DELAY_SEC — pensados pra confirmar um
# order_send AMBÍGUO na abertura (milissegundos, sem prazo externo) — pro
# fechamento, que tem um prazo externo rígido de verdade: a janela 08:00-08:04
# do scheduler (240s, scripts/scheduler_daemon.py). Medido (mfc-rev-2): o
# valor MÁXIMO válido das duas (10 tentativas × 10s de espera) combinado com
# latência real de broker (7 ordens × N moedas, não uma consulta só) projeta
# até ~426s — estoura a janela, e um operador que aumente essas duas variáveis
# por um motivo isolado do lado da ABERTURA (broker lento pra confirmar ordem
# ambígua) muda, sem saber, o orçamento de tempo do FECHAMENTO também.
# Deadline PRÓPRIO, medido em tempo DECORRIDO (não contagem de tentativas) —
# imune a qualquer combinação de CSS_AMBIGUOUS_CONFIRM_*, porque o teto é
# sobre o relógio, não sobre quantas rodadas cabem nele. Ressalva medida
# (mfc-rev-2, rodada 11, P3-3): o deadline é conferido ENTRE rodadas, nunca
# interrompe um order_send em voo (interromper seria pior — posição
# parcialmente fechada sem confirmação) — o teto REAL de uma chamada é
# "deadline + a duração da última rodada iniciada antes dele expirar", não o
# deadline sozinho. Por isso o teto da faixa abaixo é 120s, não mais: mesmo
# com uma rodada patológica (8 cestas × 7 pernas a ~2s/ordem ≈ 112s), o pior
# caso projetado fica dentro dos 240s da janela 08:00-08:04. Limitação
# estrutural que ISTO NÃO RESOLVE (Codex, F11-1): nenhuma chamada MT5
# individual (positions_get/order_send/symbol_info_tick/account_info) tem
# timeout ou cancelamento — se uma travar de verdade (sem devolver erro nem
# sucesso), nenhum deadline em Python consegue interromper, porque a checagem
# só roda ENTRE chamadas, nunca durante uma. Isso é uma propriedade do
# binding MT5 usada em TODO o arquivo, não algo introduzido por este
# watchdog — corrigir isso de verdade exigiria timeout/cancelamento real
# (ex.: rodar a chamada bloqueável numa thread separada e desistir dela sem
# esperar), o que a própria Sol já observou não poder assumir thread-safety
# do binding sem medir — fora do escopo desta correção pontual.
# Nunca pode BLOQUEAR o fechamento (fechar é redutor de risco, nunca é
# gate-ado como abrir é): por isso não passa por
# _EXECUTION_CONFIG_SPEC/check_execution_config() — que só guarda a ABERTURA —
# um valor explícito inválido aqui cai no default via _env_number(), como
# CATASTROPHIC_SL_PIPS caía antes do achado F-06, mas sem nunca recusar fechar.
# Documentada em .env.example e na invariante 2 de .herdr/reviewer.md — ela é
# a SEXTA variável tunável do sistema, deliberadamente FORA das cinco
# gate-adas por check_execution_config() (achado P3-2, mfc-rev-2, rodada 11).
#
# Segunda limitação estrutural, reconhecida e deliberadamente NÃO resolvida
# aqui (Codex, F13-1, rodada 13): este deadline é POR CHAMADA de
# close_all_portfolios(), não um orçamento absoluto da janela 08:00-08:04 do
# scheduler como um todo. scripts/scheduler_daemon.py chama
# execute_phase_0800() de novo a cada ciclo enquanto `cur_min < 5` e a
# tentativa anterior não confirmou fechamento total — cada nova chamada
# ganha um deadline FRESCO de até 120s, então, em tese, uma sequência de
# chamadas mal-sucedidas em cadeia pode consumir mais que os 240s nominais
# da janela antes que o scheduler pare de tentar. Isso NÃO quebra a garantia
# de segurança real: a reconciliação das 08:10 (`execute_phase_0810`) é o
# backstop deliberadamente incondicional pra exatamente este cenário — ela
# dispara quando `cur_min >= 10` independente de quantas tentativas
# aconteceram antes, reconsulta o broker do zero, e alerta alto (3 canais)
# se ainda houver posição órfã. Construir um orçamento ancorado
# entre-chamadas (ex.: um timestamp de início de janela persistido no
# scheduler) fecharia a janela NOMINAL de 4 minutos com mais precisão, mas
# não muda o resultado final garantido pelo 08:10 — considerado fora de
# escopo desta correção pontual do watchdog em si.
_CLOSE_WATCHDOG_DEADLINE_SEC = _clamp(_env_number("CSS_CLOSE_WATCHDOG_DEADLINE_SEC", 90.0, float), 10.0, 120.0)
# Granularidade fixa entre reconsultas — não é tunável via .env de propósito:
# o que protege o orçamento de tempo é o deadline acima, não o intervalo de
# poll. Curto o bastante pra não desperdiçar o deadline em espera ociosa,
# longo o bastante pra não martelar o broker.
_CLOSE_WATCHDOG_POLL_INTERVAL_SEC = 2.0


def _confirm_position_after_ambiguous_retcode(broker_symbol: str, magic: int):
    """Confirma se a perna abriu depois de uma resposta ambígua, tentando
    algumas vezes com espera entre elas em vez de uma única checagem
    imediata. Uma falha de consulta (None) numa tentativa NÃO encerra a
    confirmação — conta como 'ainda não confirmado' e tenta de novo, porque
    uma falha de consulta costuma ser tão transitória quanto a própria
    ambiguidade que estamos tentando resolver; só desiste (None) depois de
    esgotar todas as tentativas. Esta função nunca reenvia ordem — só quem
    chama decide o que fazer com o resultado.

    Retorna o VOLUME real confirmado (float > 0), não um bool — quem chama
    usa isso pra reportar o que de fato abriu (pode ser menos que o lote
    pedido, num DONE_PARTIAL) em vez de assumir que qualquer posição
    encontrada é a perna inteira.

    Limitação conhecida (ainda não resolvida): correlaciona só por
    símbolo+magic, sem ticket/deal — mas como open_portfolio_basket() já
    recusa a cesta inteira de saída se o magic tiver QUALQUER posição prévia
    (idempotência), uma posição encontrada aqui só pode ser a que ESTA
    própria chamada acabou de enviar, não uma sobra de outra sessão."""
    for attempt in range(_AMBIGUOUS_CONFIRM_ATTEMPTS):
        volume = _confirmed_position_volume(broker_symbol, magic)
        if volume:
            return volume
        if attempt < _AMBIGUOUS_CONFIRM_ATTEMPTS - 1:
            time.sleep(_AMBIGUOUS_CONFIRM_DELAY_SEC)
    return None


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
    """Nunca inicializa 'com o que estiver disponível' (mt5.initialize() sem
    path) quando MT5_PATH não resolve pra um terminal64.exe real — essa
    máquina roda vários terminais MT5 pra estratégias/contas diferentes, e um
    CSS_MT5_TERMINAL_PATH mal configurado silenciosamente anexando a
    QUALQUER outro terminal já rodando seria pior que simplesmente falhar
    (achado ALTO em revisão: combinado com get_mt5_files_dir() também
    falhando fechado no mesmo cenário, a 2ª localização do kill switch fica
    inatingível — mas com esta trava, nenhuma ordem chega a ser considerada
    de qualquer forma, porque MT5 nunca inicializa)."""
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


COST_LOG_FILE = os.path.join(DATA_DIR, "execution_cost_log.json")

# Serializa o ciclo ler-modificar-gravar de COST_LOG_FILE (achado em revisão).
# Achado em revisão (Codex, achado 4 rodada 2): este comentário dizia "medir
# custo agora roda numa thread por moeda" — desatualizado desde o achado 3
# (ver o comentário do guard logo abaixo): hoje mede as cestas de uma noite
# em LOTE, numa thread só (scheduler_daemon._measure_pending_costs), então
# dentro de UMA execução da fase 21:05 não há concorrência nenhuma pro log.
# A trava continua necessária porque measure_and_log_basket_cost() é função
# PÚBLICA — o endpoint manual (POST /api/portfolio-robots/open) pode chamar
# open_portfolio_basket() e, por tabela, medir custo, ao mesmo tempo em que
# o daemon mede o lote da noite. Sem esta trava, dois chamadores podiam ler
# o mesmo histórico, cada um acrescentar sua entrada e gravar por cima,
# perdendo a entrada do outro silenciosamente (lost update). A escrita
# atômica já existente evita JSON pela metade; esta trava evita perder uma
# gravação inteira.
_COST_LOG_LOCK = threading.Lock()

# Registro de medições de custo em andamento, por MOEDA (achado em revisão
# /dual-r). O scheduler já não dispara uma thread por moeda: hoje mede as
# cestas do dia em lote, numa thread só, DEPOIS de todas as aberturas (ver
# scheduler_daemon._measure_pending_costs) — então DENTRO de uma execução da
# fase 21:05 este guard não chega a disparar. Ele continua necessário por uma
# razão que só aparece olhando MAIS de uma noite: se a cost_batch de uma
# noite travar numa chamada MT5 (sem timeout) e não destravar a tempo, a
# fase seguinte cria uma cost_batch NOVA sobre o mesmo pending_cost — sem
# este guard, cada noite travada empilha mais uma thread presa (sem teto), e
# pior, a moeda que travou primeiro nunca mais seria medida (a thread nova
# trava exatamente no mesmo ponto). Com o guard, o teto continua sendo 8 —
# uma por moeda que já está travada — e as OUTRAS moedas do lote seguem
# sendo medidas normalmente na noite seguinte. Python não tem como cancelar uma
# chamada C-extension travada por dentro (nem ThreadPoolExecutor ajuda: seus
# workers não são daemon desde a 3.9 e são join()ados no encerramento do
# processo — trocaria "threads penduradas inofensivas" por "processo que não
# encerra"). Isto não cura uma chamada travada, só limita o estrago: no
# máximo 1 thread presa POR MOEDA (teto real de 8, o total de moedas),
# mesmo depois de muitas noites — uma medição nova pra uma moeda que já tem
# outra presa desiste na hora e avisa, em vez de empilhar mais uma. Note que
# a UNIDADE de perda mudou com o lote único do scheduler: uma moeda travada
# não perde só a si mesma, arrasta as moedas seguintes do MESMO lote (que
# nunca chegam a rodar, presas atrás dela na mesma thread) — o guard limita
# quantas THREADS acumulam, não quantas medições um travamento custa numa
# única noite. Um
# semáforo com teto baixo (ex.: 2) foi cogitado e descartado: numa noite
# normal com 3+ moedas qualificando ao mesmo tempo (nada incomum — até 8
# podem qualificar juntas), a maioria seria pulada só por concorrência
# passageira entre medições saudáveis, não por travamento de verdade. Por
# moeda não tem esse efeito colateral: moedas DIFERENTES nunca competem
# entre si, só a MESMA moeda com ela mesma.
_COST_MEASUREMENT_IN_PROGRESS = set()
_COST_MEASUREMENT_IN_PROGRESS_LOCK = threading.Lock()

# Sentinela pra distinguir "swap_mode ausente" de "swap_mode presente com um
# valor real" em CostModel.leg() — object() só é igual a si mesmo, nunca a
# um valor de constante MT5 (achado em revisão: Codex, achado 4 rodada 4).
_SWAP_MODE_MISSING = object()


def _tick_valido(tick):
    """Um tick só é confiável se os DOIS lados forem positivos, FINITOS e o
    mercado não estiver cruzado (ask >= bid) — não só o lado que vai ser
    usado numa ordem específica. Função de MÓDULO, não mais closure interna
    de CostModel.leg() (achado em revisão: Codex + mfc-rev-2, achado 2/4
    rodada 5, confirmado pelos dois independentemente): a versão anterior
    só existia dentro de leg(), então o preflight real (open_portfolio_basket)
    e _usd_rate() tinham suas PRÓPRIAS checagens, mais fracas — só o preço
    do lado escolhido, sem exigir o outro lado positivo. Medido (Claude):
    um tick com ask=1.1002/bid=0.0 (BUY) passava reto pelo preflight e as
    7 ordens saíam, enquanto o CostModel (só diagnóstico) já rejeitava a
    mesma perna. Uma função só, usada nos quatro lugares que fazem a
    mesma pergunta ao mesmo binding, fecha a divergência estruturalmente
    em vez de precisar lembrar de sincronizar quatro cópias.

    Achado em revisão (Codex, rodada 6): `ask > 0` sozinho não barra
    `float("inf")` — um tick `ask=inf, bid=1.0` passava, o preflight
    gravava `inf` em `ticks[...]`, e a reconsulta no laço de envio aceitava
    de novo, entregando `price=inf` a `order_send()` e ao cálculo do stop
    catastrófico. `math.isfinite()` nos dois lados fecha isso."""
    return (tick is not None
            and math.isfinite(tick.ask) and math.isfinite(tick.bid)
            and tick.ask > 0 and tick.bid > 0 and tick.ask >= tick.bid)


class CostModel:
    """ESTIMATIVA de spread e swap no broker conectado, em USD, pro lote dado
    — não custo realizado (achado em revisão, Codex, recorrente desde a
    rodada 2 do achado 4): usa o TICK CORRENTE (ask/bid de agora), não o
    preço de preenchimento real da ordem, e não conta comissão nem
    slippage; o swap usa os campos do símbolo, não o valor efetivamente
    debitado/creditado nos deals de fechamento (esse dado mais fiel já é
    lido em web/real_portfolio_audit.py, campo d.swap — ver o comentário
    de leg() sobre rollover triplo pra mais contexto). "Real" aqui, nos
    logs e no backtest, significa "medido no broker de verdade agora", em
    contraste com um valor típico hardcoded — não "idêntico ao que a
    corretora efetivamente cobrou". Movida de scripts/backtest_canonical.py
    (que tinha sua própria cópia hardcoded em LOT=0.01) pra cá — este é o
    executor de verdade, e o backtest agora importa esta classe em vez de
    duplicá-la (pedido do Breno: medir spread de verdade em vez de
    perguntar 'valor típico' pro Miquéias — a mesma lógica serve pra
    qualquer corretora que o processo estiver conectado no momento, sem
    precisar saber de antemão o custo típico de nenhuma conta
    específica)."""

    def __init__(self, lot: float):
        self.lot = lot
        self._rate = {}
        self._leg = {}
        # Achado 4 rodada 2 (mfc-rev-2, medido): as duas categorias abaixo
        # NÃO podem compartilhar a mesma bandeira. _degraded é "perdi o
        # dado" — spread E swap se foram, custo real da perna é
        # desconhecido. _swap_unmodeled é "o spread é real, só o swap não
        # tem fórmula fiel pra esse modo" — propriedade ESTÁTICA do
        # símbolo, não falha transitória. Antes de separar, uma corretora
        # com swap fora de pontos (comum: swap em moeda de depósito)
        # marcava 100% das cestas como "degradadas" em toda medição, pra
        # sempre — um alarme que nunca desliga é ignorado, o oposto do
        # propósito deste mecanismo.
        self._degraded = {}          # (pair, action, lot) -> motivo: dado perdido
        self._swap_unmodeled = {}    # (pair, action, lot) -> motivo: swap fora de pontos
        self.last_basket_degraded = set()        # pernas SEM dado na ÚLTIMA basket()
        self.last_basket_swap_unmodeled = set()  # pernas com swap não modelado na ÚLTIMA

    def _usd_rate(self, quote):
        if quote in self._rate:
            return self._rate[quote]
        if quote == "USD":
            self._rate[quote] = 1.0
            return 1.0
        for cand, invert in ((f"{quote}USD", False), (f"USD{quote}", True)):
            bsym = to_broker_symbol(cand)
            tick = mt5.symbol_info_tick(bsym)
            if not _tick_valido(tick):
                # Achado 4 (revisão de ad44e12/c24a44c, mfc-rev-2): o gatilho
                # mais provável de degradação era este — pares de conversão
                # (ex.: GBPUSD pra medir custo de uma cesta CAD) não fazem
                # parte das 7 pernas que o preflight já seleciona; sem estar
                # "selecionado" no Market Watch, symbol_info_tick() devolve
                # None mesmo o símbolo existindo. Mesma correção que o
                # preflight já usa pros 7 pares reais. Achado em revisão
                # (Codex, achado 2/4 rodada 5): antes só checava "tick is
                # None e tick.bid > 0" — um tick com ask<=0, ask<bid, ou
                # bid<=0 no campo NÃO usado por esta conversão (ex.: par
                # invertido usa só bid, mas ask=0 nunca era checado) podia
                # gerar uma taxa a partir de dado inválido. Mesma régua de
                # _tick_valido() usada em leg() — os dois lados, não só o
                # usado.
                mt5.symbol_select(bsym, True)
                tick = mt5.symbol_info_tick(bsym)
            if _tick_valido(tick):
                r = (1.0 / tick.bid) if invert else tick.bid
                self._rate[quote] = r
                return r
        self._rate[quote] = None
        return None

    def leg(self, pair, action, lot=None):
        """(spread_ida_usd, swap_noite_usd). Swap negativo = custo.
        `lot` opcional substitui self.lot pra essa perna (achado em revisão
        /dual-r: cesta com preenchimento parcial numa perna tem lote
        diferente das outras — usar sempre o mesmo lote pra todas subestima
        ou superestima o custo de quem divergiu). A chave do cache inclui o
        lote usado: sem isso, a MESMA perna calculada de novo com outro lote
        devolveria o valor velho em vez de recalcular."""
        lot = self.lot if lot is None else lot
        key = (pair, action, lot)
        if key in self._leg:
            return self._leg[key]
        bsym = to_broker_symbol(pair)
        si, tick = mt5.symbol_info(bsym), mt5.symbol_info_tick(bsym)
        if si is None or tick is None or not _tick_valido(tick):
            # Achado 4 rodada 2 (Codex + mfc-rev-2, achado confirmado pelos
            # dois independentemente): o comentário original assumia que as
            # 7 pernas "o preflight já seleciona" — verdade no caminho AO
            # VIVO, falso no BACKTEST (scripts/backtest_canonical.py), que
            # nunca roda open_portfolio_basket() e é justamente o
            # consumidor que decide se a estratégia é lucrativa líquida.
            # Sem isto, medido: backtest com Market Watch vazio zerava
            # 7/7 pernas sempre. Mesma correção que _usd_rate() já tem
            # pros pares de CONVERSÃO — agora a perna real também tenta.
            #
            # Achado em revisão (mfc-rev-2, achado 4 rodada 4, medido): a
            # versão anterior só entrava aqui com tick is None — um tick
            # ZERADO nunca disparava o retry, mesmo sendo o sintoma
            # clássico de "símbolo ainda não presente no Market Watch"
            # (mesma causa, mesma cura). Ressalva (mfc-rev-2, rodada 5,
            # medido): pra tick CRUZADO (ask < bid) especificamente, NÃO é
            # a mesma causa — symbol_select() não corrige um mercado
            # cruzado, então o retry aqui é uma chamada garantidamente
            # inútil nesse caso específico (inofensiva, só ruído de IPC;
            # a segunda leitura vai reprovar de novo em _tick_valido logo
            # abaixo, entrando em _degraded do mesmo jeito).
            mt5.symbol_select(bsym, True)
            si, tick = mt5.symbol_info(bsym), mt5.symbol_info_tick(bsym)
        rate = self._usd_rate(pair[3:6])
        # "is None" (não "not"/"falsy") pra si e tick, igual ao retry acima
        # — achado em revisão (mfc-rev-2, rodada 3): as duas checagens
        # perguntavam a mesma coisa com rigor diferente (um objeto falsy
        # que não seja None é inalcançável no MT5 real, mas convidava
        # dúvida de qual delas era a intencional).
        tick_valido = _tick_valido(tick)
        if si is None or tick is None or not tick_valido or rate is None:
            # Achado 4 (revisão de ad44e12/c24a44c, mfc-rev-2): (0.0, 0.0)
            # aqui é indistinguível de "cesta com custo genuinamente zero"
            # pra quem lê o log ou o backtest depois. Registra o motivo pra
            # basket() poder sinalizar — quem soma os números continua
            # recebendo 0.0 (nunca lança, nunca atrasa a cesta), só quem
            # PERGUNTA (basket().last_basket_degraded) fica sabendo.
            if si is None or tick is None:
                motivo = "símbolo/tick indisponível"
            elif not tick_valido:
                motivo = f"tick inválido (ask={getattr(tick, 'ask', None)}, " \
                         f"bid={getattr(tick, 'bid', None)})"
            else:
                motivo = f"taxa de conversão {pair[3:6]}→USD indisponível"
            self._degraded[key] = motivo
            self._leg[key] = (0.0, 0.0)
            return self._leg[key]
        units = lot * si.trade_contract_size
        spread = (tick.ask - tick.bid) * units * rate
        # Swap só é calculado certo pro modo PONTOS (não o único — MT5
        # também aceita swap em moeda base, moeda de margem, moeda de
        # depósito e percentual). Fora desse modo, reporta 0.0 em vez de
        # fingir uma precisão que não existe. Achado em revisão (mfc-rev-2,
        # achado 4 rodada 2, medido): isto NÃO é a mesma categoria do "sem
        # símbolo/tick/taxa" acima — ali a perna inteira (spread E swap)
        # se perde; aqui o SPREAD é real e contou pro custo, só o swap
        # ficou de fora, por escopo deliberado do modelo (não é erro
        # transitório de dado, é uma propriedade ESTÁTICA do símbolo).
        # Marcar as duas com a mesma bandeira "degraded" faz um alarme que
        # NUNCA desliga em qualquer corretora que use swap fora de pontos
        # (medido: 7/7 pernas "degradadas" numa cesta cujo custo real foi
        # medido e contado) — o oposto do propósito do achado 4. Categoria
        # separada, tratada como aviso silencioso do modelo, não como dado
        # perdido.
        swap_mode_points = getattr(mt5, "SYMBOL_SWAP_MODE_POINTS", 1)
        # Achado em revisão (Codex, achado 4 rodada 4): o default do getattr
        # abaixo era swap_mode_points — ausência do campo virava "presumir
        # PONTOS" (fail-open), mesmo padrão que o achado 2 já fechou pro
        # trade_mode. _SWAP_MODE_MISSING (um objeto único, nunca igual a
        # nada além de si mesmo) garante que campo ausente NUNCA bate com
        # swap_mode_points — cai no ramo de swap não modelado, como deveria.
        swap_mode_atual = getattr(si, "swap_mode", _SWAP_MODE_MISSING)
        if swap_mode_atual == swap_mode_points:
            swap_pts = si.swap_long if action == "BUY" else si.swap_short
            swap = swap_pts * si.point * units * rate
        else:
            swap = 0.0
            motivo_swap = ("swap_mode ausente" if swap_mode_atual is _SWAP_MODE_MISSING
                           else f"swap_mode {swap_mode_atual!r} não é PONTOS")
            self._swap_unmodeled[key] = motivo_swap
        # Não contabiliza rollover triplo (si.swap_rollover3days) —
        # decisão deliberada, não descuido (achado em revisão /dual-r).
        # Corrigir isso exigiria saber o dia-da-semana do SERVIDOR do
        # broker, e este repositório já foi mordido por essa exata classe
        # de bug (duas implementações de offset de fuso divergentes — ver
        # web/history_tracker.py:30-37 vs. web/real_portfolio_audit.py
        # get_broker_gmt_offset()). Pior: pra esta conta (GMT_OFFSET=-3,
        # servidor UTC+0), a janela operacional 21:05→08:00 BRT cai em
        # ~00:05→11:00 do servidor — dentro do MESMO dia-servidor — logo
        # muito provavelmente não atravessa rollover nenhum, e aplicar um
        # multiplicador ×3 aqui pioraria o dado em vez de melhorar. Se um
        # dia for preciso um swap fiel, o caminho mais seguro não é estimar
        # melhor — é ler o swap REALIZADO dos deals de fechamento (já lido
        # em web/real_portfolio_audit.py, campo d.swap), que captura
        # rollover triplo, modo de swap fora de pontos e tudo o mais sem
        # nenhum cálculo de fuso novo.
        self._leg[key] = (spread, swap)
        return self._leg[key]

    def basket(self, ccy, bias, leg_lots: dict = None):
        """Custo total de uma cesta: spread ida+volta + swap de uma noite.
        Retorna valor POSITIVO representando quanto a cesta custa.
        `leg_lots` opcional: {pair: lote confirmado} pra usar o lote real de
        cada perna em vez do escalar único de __init__; perna ausente do
        mapa (ou mapa não informado) cai pro escalar.

        Depois de chamar, duas bandeiras SEPARADAS (achado 4, rodada 2 —
        conflar as duas fazia um alarme que nunca desliga em qualquer
        corretora com swap fora de pontos):
        - self.last_basket_degraded: pares DESTA cesta sem símbolo/tick/taxa
          — spread E swap perdidos, custo real da perna é desconhecido.
        - self.last_basket_swap_unmodeled: pares com spread real, mas swap
          zerado por modo não suportado (propriedade estática do símbolo,
          não falha transitória).
        Nenhuma das duas muda o número devolvido — continua somando 0.0
        pras pernas afetadas, nunca lança, nunca atrasa a cesta. Só expõe
        quem quiser saber se o custo é medição completa, parcial, ou
        completa-mas-sem-swap-modelado."""
        spread = swap = 0.0
        degraded = set()
        swap_unmodeled = set()
        for p in get_portfolio_pairs(ccy, bias):
            raw_lot = (leg_lots or {}).get(p["pair"])
            s, w = self.leg(p["pair"], p["action"], raw_lot)
            spread += s
            swap += w
            resolved_lot = self.lot if raw_lot is None else raw_lot
            key = (p["pair"], p["action"], resolved_lot)
            if key in self._degraded:
                degraded.add(p["pair"])
            if key in self._swap_unmodeled:
                swap_unmodeled.add(p["pair"])
        self.last_basket_degraded = degraded
        self.last_basket_swap_unmodeled = swap_unmodeled
        return spread * 2.0 - swap  # swap negativo vira custo positivo


def _read_cost_log(path: str) -> list:
    """Lê o log existente em `path`, ou [] se o arquivo ainda não existe.
    Levanta se o conteúdo for ilegível/corrompido — quem chama decide
    (measure_and_log_basket_cost recusa sobrescrever nesse caso)."""
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        log = json.load(f)
    if not isinstance(log, list):
        raise ValueError("formato inesperado (não é uma lista)")
    return log


def measure_and_log_basket_cost(currency: str, bias: str, lot: float, leg_lots: dict = None):
    """Mede a ESTIMATIVA de custo (spread+swap) da cesta recém-aberta, via
    CostModel — não custo realizado (achado em revisão: Codex, achado 2/4
    rodada 5: esta docstring ainda dizia "custo real", contradizendo a
    própria docstring de CostModel logo acima, que já deixa claro que usa o
    tick corrente, não o preço de preenchimento, e não inclui comissão nem
    slippage). Acrescenta ao histórico em COST_LOG_FILE — dado empírico
    próprio, sem depender de ninguém informar 'valor típico'. Deliberadamente
    chamada DEPOIS de a cesta já ter aberto (nunca antes, nunca durante) e
    nunca por
    open_portfolio_basket() diretamente: dentro de UMA execução da fase
    21:05, isso garante que não atrasa nem arrisca o envio de ordem real.
    Entre noites diferentes essa garantia é só P3 (ver o guard acima e
    scheduler_daemon.py): uma medição presa numa noite ainda pode, em teoria,
    estar rodando quando a fase seguinte envia ordem. Qualquer falha aqui
    fica só no log, nunca propaga.

    `lot` continua sendo o escalar (média, ou o lote pedido) gravado no
    campo "lot" pra compatibilidade com quem já lê este log. `leg_lots`
    opcional (achado em revisão /dual-r) — {pair: lote confirmado da perna}
    — dá o custo exato quando alguma perna teve preenchimento parcial; sem
    ele, CostModel.basket() cai pro escalar único pra todas as pernas."""
    ccy_key = currency.upper()
    with _COST_MEASUREMENT_IN_PROGRESS_LOCK:
        if ccy_key in _COST_MEASUREMENT_IN_PROGRESS:
            print(f"[-] Medição de custo da cesta {ccy_key} pulada — uma medição anterior pra "
                  f"esta MESMA moeda ainda está presa (possível IPC do MT5 travado). Log fica "
                  f"sem esta entrada; a cesta em si não é afetada.")
            return
        _COST_MEASUREMENT_IN_PROGRESS.add(ccy_key)
    try:
        # A medição em si (chamadas MT5) fica FORA do lock — só o ciclo
        # ler-modificar-gravar do arquivo compartilhado precisa ser
        # serializado, pra não travar a medição de uma moeda esperando a
        # de outra.
        model = CostModel(lot)
        cost_usd = model.basket(currency.upper(), bias, leg_lots)
        # Achado 4 (revisão de ad44e12/c24a44c, mfc-rev-2): sem isto,
        # "cesta com custo genuinamente zero" e "uma perna sem dado bem na
        # hora da medição" eram gravadas de forma idêntica no log — e o
        # backtest canônico (scripts/backtest_canonical.py) desconta este
        # modelo do resultado líquido sem saber qual dos dois casos é este.
        degraded = model.last_basket_degraded
        swap_unmodeled = model.last_basket_swap_unmodeled
        # Fail-closed de verdade (achado em revisão: Codex + mfc-rev-2,
        # achado 4 rodada 3, confirmado pelos dois — remover o isinstance
        # da rodada 2 sem validar nada não bastou). Reproduzido: um
        # CostModel mal configurado (MagicMock cru, sem os dois atributos)
        # é truthy E itera vazio — produzia "[!] Custo PARCIAL ... 0
        # perna(s) sem dado real ()", uma mensagem que se contradiz, e
        # gravava "degraded": [] no log (o campo vazio que o teste de
        # caminho feliz existe pra impedir). Em vez de confiar cegamente
        # ou silenciar, trata um formato inesperado como cesta INTEIRA não
        # confiável — nunca como cesta completa.
        if not isinstance(degraded, (set, frozenset)) or not isinstance(swap_unmodeled, (set, frozenset)):
            print(f"[-] CostModel devolveu last_basket_degraded/last_basket_swap_unmodeled "
                  f"num formato inesperado pra {currency.upper()} (tipos: "
                  f"{type(degraded).__name__}, {type(swap_unmodeled).__name__}) — tratando "
                  f"a cesta inteira como não confiável, não como medição completa.")
            degraded, swap_unmodeled = {"<formato_inesperado_do_CostModel>"}, set()
        entry = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "currency": currency.upper(),
            "bias": bias,
            "lot": round(lot, 4),
            "cost_usd": round(cost_usd, 4),
        }
        if degraded:
            entry["degraded"] = sorted(degraded)
        if swap_unmodeled:
            entry["swap_not_modeled"] = sorted(swap_unmodeled)
        if leg_lots:
            entry["leg_lots"] = {pair: round(l, 4) for pair, l in leg_lots.items()}
        with _COST_LOG_LOCK:
            try:
                log = _read_cost_log(COST_LOG_FILE)
            except Exception as e:
                # NÃO sobrescreve (achado em revisão): sem isso, um arquivo
                # corrompido/ilegível virava um log novo de 1 entrada só,
                # apagando silenciosamente todo o histórico acumulado. Perder
                # ESTA medição é aceitável; apagar meses de histórico não.
                print(f"[-] Falha ao ler {COST_LOG_FILE} ({e}) — NÃO sobrescrevendo "
                      f"(evita apagar o histórico). Esta medição fica de fora do log.")
                return
            log.append(entry)
            _atomic_write_json(COST_LOG_FILE, log)
        # Achado em revisão (mfc-rev-2, achado 4 rodada 3): as duas
        # mensagens abaixo eram if/elif — quando degraded E swap_unmodeled
        # coexistem na MESMA cesta (reproduzido: 2 pernas sem tick + 5 com
        # swap fora de pontos), só a de degraded saía no stdout, mesmo o
        # log gravando os dois campos corretamente. Agora são duas
        # condições independentes — cada categoria fala por si.
        if degraded:
            print(f"[!] Custo PARCIAL da cesta {currency.upper()} ({bias}): ${cost_usd:.2f} "
                  f"(gravado em {COST_LOG_FILE}) — {len(degraded)} perna(s) sem dado real na "
                  f"hora da medição ({', '.join(sorted(degraded))}), contadas como custo zero. "
                  f"Não é a mesma coisa que uma cesta com custo genuinamente baixo.")
        if swap_unmodeled:
            # Achado 4 rodada 2 (mfc-rev-2, medido): NÃO é a mesma classe de
            # "[!] PARCIAL" — o spread de todas as pernas é real e já está
            # no $cost_usd; só o swap de algumas ficou sem fórmula fiel
            # (modo do símbolo, não falha de dado). Alertar como se fosse a
            # mesma coisa que `degraded` é o que fazia este alarme nunca
            # desligar em corretoras com swap fora de pontos.
            print(f"[+] Custo medido da cesta {currency.upper()} ({bias}): ${cost_usd:.2f} "
                  f"(gravado em {COST_LOG_FILE}) — swap não modelado em "
                  f"{len(swap_unmodeled)} perna(s) ({', '.join(sorted(swap_unmodeled))}: modo "
                  f"de swap do símbolo não é PONTOS); spread de todas as pernas está no valor.")
        if not degraded and not swap_unmodeled:
            print(f"[+] Custo medido da cesta {currency.upper()} ({bias}): ${cost_usd:.2f} "
                  f"(gravado em {COST_LOG_FILE})")
    except Exception as e:
        print(f"[-] Falha ao medir/gravar custo da cesta {currency}: {e}")
    finally:
        with _COST_MEASUREMENT_IN_PROGRESS_LOCK:
            _COST_MEASUREMENT_IN_PROGRESS.discard(ccy_key)


def open_portfolio_basket(currency: str, bias: str, lot: float = 0.01, deviation: int = 15):
    """
    Envia ordens a mercado no MT5 para os 7 pares do portfólio especificado
    com o Magic Number exclusivo da moeda.

    Antes de qualquer ordem, checa (nessa ordem): kill switch, validade da
    configuração de execução (check_execution_config — CSS_MAX_LOT,
    CSS_MAX_CONCURRENT_BASKETS, CSS_CATASTROPHIC_SL_PIPS e os dois
    CSS_AMBIGUOUS_CONFIRM_*), identidade e permissão da conta
    (check_account_gate — CSS_MT5_EXPECTED_LOGIN e CSS_LIVE_TRADING),
    idempotência (cesta desse magic já aberta hoje?), tetos de exposição e
    colisão de símbolo com outra cesta já aberta em conta netting. Qualquer
    uma dessas recusa a abertura inteira sem enviar nenhuma ordem.
    """
    if is_kill_switch_active():
        msg = f"Kill switch ativo ({KILL_SWITCH_FILE}) — nenhuma cesta nova será aberta."
        print(f"[PORTFOLIO ROBOT] {msg}")
        return {"success": False, "error": "kill_switch_active", "message": msg}

    # Logo depois do kill switch, antes de qualquer coisa que consuma os
    # limites de segurança (inclusive o teto de lote logo abaixo, que já lê
    # MAX_LOT): config inválida invalida todo gate que vier depois dela, e
    # não custa MT5 nenhum (achado em revisão: Codex, F-06 rodada 6; design
    # via herdr-ask — ver check_execution_config()).
    config_gate = check_execution_config()
    if not config_gate["allowed"]:
        print(f"[PORTFOLIO ROBOT] {config_gate['message']}")
        return {"success": False, "error": config_gate["error"], "message": config_gate["message"]}

    if not ensure_mt5():
        return {"success": False, "error": "MT5 não inicializado"}

    safety = get_account_safety_info()
    gate = check_account_gate(safety)
    if not gate["allowed"]:
        print(f"[PORTFOLIO ROBOT] {gate['message']}")
        return {"success": False, "error": gate["error"], "message": gate["message"], "account": safety}

    ccy = currency.upper()

    # Direção precisa ser exatamente BUY ou SELL. get_portfolio_pairs trata
    # QUALQUER valor diferente de "BUY" como fraqueza, então um "LONG" vindo
    # do endpoint HTTP montava a cesta INVERTIDA sem erro nenhum.
    bias_norm = (bias or "").strip().upper()
    if bias_norm not in ("BUY", "SELL"):
        msg = (f"Direção inválida: {bias!r}. Só BUY ou SELL são aceitos — "
               f"qualquer outro valor montaria a cesta invertida em silêncio.")
        print(f"[PORTFOLIO ROBOT {ccy}] {msg}")
        return {"success": False, "error": "invalid_bias", "message": msg}
    bias = bias_norm

    # Teto de lote: o "lote fixo 0.01" da estratégia era só um default de
    # parâmetro, não uma trava — o endpoint HTTP aceitava qualquer valor.
    try:
        lot = float(lot)
    except (TypeError, ValueError):
        msg = f"Lote inválido: {lot!r}."
        print(f"[PORTFOLIO ROBOT {ccy}] {msg}")
        return {"success": False, "error": "invalid_lot", "message": msg}
    if lot <= 0 or lot > MAX_LOT:
        msg = (f"Lote {lot} fora do teto permitido (0 < lote <= {MAX_LOT}). "
               f"Ajuste CSS_MAX_LOT se a mudança for deliberada.")
        print(f"[PORTFOLIO ROBOT {ccy}] {msg}")
        return {"success": False, "error": "lot_above_cap", "message": msg}

    magic = PORTFOLIO_MAGICS.get(ccy, 801000)
    pairs = get_portfolio_pairs(ccy, bias)

    # Aquece a resolução de símbolo ANTES da checagem de colisão em netting
    # logo abaixo (achado em revisão): a auto-detecção de sufixo só dispara
    # dentro de to_broker_symbol(), que só era chamada mais adiante no
    # preflight — na primeira chamada do processo (sufixo manual ainda não
    # configurado), a comparação de símbolo pra colisão em netting rodaria
    # antes do sufixo estar descoberto.
    #
    # Desde o achado 1 (validação de família nos 28 pares, não só o
    # par-sonda — achado em revisão: mfc-rev-2), este aquecimento ficou mais
    # caro no caminho feliz (1 symbols_get + até 28 symbol_info por
    # candidato, uma vez por processo, cacheado depois — não mais "um
    # symbol_info a mais") e, no caso raro em que NENHUMA família valida,
    # tem um cooldown (_FAMILY_DETECTION_COOLDOWN_SECONDS em css_service.py)
    # em vez de reconsultar a cada chamada — sem isso, o preflight logo
    # abaixo (7 pernas) reconsultaria o servidor do zero a cada perna.
    #
    # Esse cooldown (15s, pensado pro dashboard que recalcula a cada 3s) é
    # LONGO DEMAIS pra esta fase — ela inteira roda em segundos. Sem forçar
    # uma tentativa fresca aqui (achado em revisão, mfc-rev-2 rodada 3,
    # medido: 0/8 cestas vs. 7/8 numa falha transitória), uma reconexão
    # lenta do MT5 bem às 21:05 condenaria a noite inteira — nenhuma
    # tentativa dentro da mesma execução chegaria a reconsultar o servidor.
    if pairs:
        reset_family_detection_cooldown()
        to_broker_symbol(pairs[0]["pair"])

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

    # Ordem: tetos de exposição ANTES da colisão netting (achado em revisão:
    # Codex, achado 2/4 rodada 4 — divergência com uma versão anterior do
    # CLAUDE.md, que documentava a ordem inversa). Confirmado que essa ordem
    # já existia assim no commit c24a44c, antes de qualquer correção desta
    # sessão — não é regressão dela. Análise (Codex + mfc-rev-2, rodadas 4 e
    # 5, ambos concordam): os dois gates são condições independentes, sem
    # efeito colateral e sem dependência entre si — cada um só RECUSA; se
    # qualquer um recusaria, a função retorna cedo nas duas ordens
    # possíveis, então a decisão final (abre ou não abre) não muda com a
    # ordem, só qual mensagem de erro sai quando os dois seriam verdade ao
    # mesmo tempo. Decisão do usuário (rodada 5): em vez de reordenar código
    # de execução real já testado, o CLAUDE.md foi corrigido pra documentar
    # esta ordem (e o porquê dela ser segura) — ver "Live MT5 execution" lá.
    #
    # Teto de cestas simultâneas: no padrão (8) não muda nada, mas permite
    # limitar a exposição total numa primeira sessão real sem mexer no código.
    if len(open_magics) >= MAX_CONCURRENT_BASKETS:
        msg = (f"Já há {len(open_magics)} cestas abertas (teto: {MAX_CONCURRENT_BASKETS}) — "
               f"abertura de {ccy} recusada. Ajuste CSS_MAX_CONCURRENT_BASKETS se for deliberado.")
        print(f"[PORTFOLIO ROBOT {ccy}] {msg}")
        return {"success": False, "error": "basket_cap_reached", "message": msg}

    # Achado em revisão (Codex, achado 2/4 rodada 4, decisão do usuário):
    # era "== netting" — margin_mode "desconhecido" (campo ausente,
    # exceção na consulta, ou um terceiro valor real do MT5 nunca mapeado
    # aqui, ex.: ACCOUNT_MARGIN_MODE_EXCHANGE) pulava esta checagem em
    # SILÊNCIO, tratado como se fosse hedging. Se a conta FOSSE netting de
    # verdade mas tivesse sido classificada errado, uma cesta nova podia
    # se fundir com uma posição já aberta de outro magic sem essa proteção
    # nunca rodar. Fail-closed: só pula quando SABEMOS que é hedging — "!=
    # hedging" cobre netting E desconhecido com a mesma checagem.
    #
    # Achado em revisão (mfc-rev-2, achado 2/4 rodada 5): o custo de aplicar
    # esta checagem "à toa" numa conta hedging mal classificada NÃO é zero —
    # medido numa simulação de 8 cestas/noite: conta hedging classificada
    # como "desconhecido" abre 1/8 cestas (as outras 7 recusam por colisão de
    # símbolo com a primeira, já que todo par de cestas do CSS compartilha
    # pelo menos 1 símbolo por desenho). O trade-off continua favorável ao
    # fail-closed (1/8 de exposição é recuperável; posições fundidas numa
    # conta netting mal classificada não são), mas fica registrado pelo que
    # é: se margin_mode nunca resolver para "hedging" numa conta que É
    # hedging, a estratégia roda a 1/8 da exposição pretendida todo noite,
    # indefinidamente, até alguém investigar por que só 1 cesta abre.
    margin_mode_real = safety.get("margin_mode")
    if margin_mode_real != "hedging":
        if margin_mode_real != "netting":
            print(f"[PORTFOLIO ROBOT {ccy}] margin_mode não identificado como hedging "
                  f"nem netting (valor: {margin_mode_real!r}) — aplicando a checagem de "
                  f"colisão de símbolo por precaução. Se esta conta for hedging, cestas "
                  f"seguintes serão recusadas até a classificação ser corrigida.")
        target_symbols = {p["pair"] for p in pairs}
        for other_magic, other_symbols in open_magics.items():
            # Acha em revisão (rodada 3): o aquecimento acima reduz mas não
            # elimina a janela onde from_broker_symbol() ainda não sabe o
            # sufixo (ex.: falha transitória bem na hora do aquecimento, numa
            # corretora nova sem CSS_MT5_SYMBOL_SUFFIX configurado) — nesse
            # caso o símbolo da corretora não seria normalizado e a colisão
            # abaixo passaria batido. Em vez de confiar que o aquecimento deu
            # certo, valida o DADO que a decisão realmente usa: se algum
            # símbolo não bate com nenhum dos 28 pares conhecidos, a
            # normalização falhou pra essa consulta — recusa por segurança.
            unresolved = other_symbols - set(ALL_28_PAIRS)
            if unresolved:
                msg = (f"Conta em modo {margin_mode_real!r} (tratada como não-hedging): "
                       f"símbolo(s) {sorted(unresolved)} da cesta "
                       f"(magic {other_magic}) não bateram com nenhum par conhecido depois de "
                       f"tentar remover o sufixo da corretora — resolução de símbolo pode ter "
                       f"falhado nesta consulta. Abertura de {ccy} recusada por segurança: sem "
                       f"essa normalização confiável, colisão de símbolo não pode ser descartada.")
                print(f"[PORTFOLIO ROBOT {ccy}] {msg}")
                return {"success": False, "error": "symbol_resolution_unreliable", "message": msg}
            collision = target_symbols & other_symbols
            if collision:
                msg = (f"Conta em modo {margin_mode_real!r} (tratada como não-hedging): "
                       f"cesta {ccy} colidiria com o magic {other_magic} "
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
    restricted = []
    # Achado ALTO em revisão (/codex-r sobre o commit ad44e12): a
    # auto-detecção de sufixo em web/css_service.py só valida
    # trade_mode==FULL no par-sonda (EURUSD) — não garante NADA sobre as
    # outras 27 pernas possíveis daquela mesma família de símbolos. Uma
    # perna individual pode ser CLOSEONLY/LONGONLY/SHORTONLY mesmo com o
    # sufixo "certo" escolhido, e antes disso passava batido aqui (só
    # existência+tick eram checados) até falhar em order_send() — tarde
    # demais, com pernas anteriores já abertas (exatamente a cesta parcial
    # que este preflight all-or-nothing existe pra evitar).
    full_mode = getattr(mt5, "SYMBOL_TRADE_MODE_FULL", 4)
    for p in pairs:
        b_sym = to_broker_symbol(p["pair"])
        info = mt5.symbol_info(b_sym)
        if info is None:
            mt5.symbol_select(b_sym, True)
            info = mt5.symbol_info(b_sym)
        if info is None:
            unresolved.append(p["pair"])
            continue
        # Achado 2 (revisão de ad44e12/c24a44c): getattr(..., full_mode)
        # presumia FULL quando trade_mode estava AUSENTE — fail-open. Duas
        # rodadas de revisão (codex-r + mfc-rev-2) confirmaram, via
        # documentação oficial e por este mesmo código já acessar
        # trade_contract_size/swap_long/point/visible SEM getattr em outros
        # lugares, que o objeto real do MT5 nunca vem incompleto —
        # symbol_info() devolve tudo ou None. Ausência só ocorre em dublê de
        # teste mínimo. Mesmo sendo teórico em produção, um preflight que
        # decide se ordem real sai não presume o cenário mais permissivo:
        # ausência de trade_mode agora é tratada como restrição.
        if getattr(info, "trade_mode", None) != full_mode:
            restricted.append(p["pair"])
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
        # Achado em revisão (mfc-rev-2, achado 4 rodada 4, medido): faltava
        # aqui a MESMA checagem que o CostModel já exige pra não contar
        # como medição completa. Sem isso, o diagnóstico (CostModel) era
        # mais rigoroso que o gate que REALMENTE decide se a ordem sai.
        #
        # Achado em revisão (Codex + mfc-rev-2, achado 2/4 rodada 5,
        # confirmado pelos dois independentemente): a primeira versão
        # desta correção só checava "não cruzado" (ask < bid) e o preço do
        # lado USADO — não exigia o OUTRO lado positivo. Medido (Claude):
        # tick com ask=1.1002 (usado, positivo) e bid=0.0 (não usado)
        # passava reto — não é "cruzado" (1.1002 >= 0), e price>0. Agora
        # usa _tick_valido(), a MESMA função de módulo que o CostModel usa
        # — os dois lados, não só o escolhido. _tick_valido já garante
        # ask>0 e bid>0, então "price" (o lado escolhido) é necessariamente
        # positivo quando ela passa — sem checagem redundante de price.
        if not _tick_valido(tick):
            no_tick.append(p["pair"])
            continue
        ticks[p["pair"]] = tick.ask if p["action"] == "BUY" else tick.bid

    if unresolved or no_tick or restricted:
        partes = []
        if unresolved:
            partes.append(f"não encontrados no servidor: {sorted(unresolved)} "
                          f"(sufixo configurado: {MT5_SYMBOL_SUFFIX!r} — confira CSS_MT5_SYMBOL_SUFFIX)")
        if restricted:
            partes.append(f"modo de negociação restrito, não FULL (CLOSEONLY/LONGONLY/SHORTONLY): "
                          f"{sorted(restricted)}")
        if no_tick:
            partes.append(f"sem cotação válida agora: {sorted(no_tick)}")
        msg = (f"Cesta {ccy} recusada por inteiro antes de qualquer ordem — " + "; ".join(partes) +
               ". Melhor nenhuma perna do que uma cesta parcial.")
        print(f"[PORTFOLIO ROBOT {ccy}] {msg}")
        return {"success": False, "error": "preflight_failed", "message": msg,
                "unresolved": sorted(unresolved), "no_tick": sorted(no_tick),
                "restricted": sorted(restricted)}

    results = []
    success_count = 0

    print(f"\n[PORTFOLIO ROBOT {ccy}] Iniciando abertura da cesta ({bias}) | Magic: {magic} | {len(pairs)} pares...")

    for p in pairs:
        pair_sym = p["pair"]
        broker_sym = broker_symbols[pair_sym]
        action = p["action"]

        order_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL
        # Repuxa a cotação mais recente; se sumiu ou ficou inválida entre o
        # preflight e agora, cai no preço do preflight (já validado por
        # _tick_valido) e deixa o `deviation` do request absorver a
        # diferença — melhor que abandonar a perna e deixar a cesta
        # parcial. Achado em revisão (Codex, achado 2/4 rodada 5): a
        # versão anterior só checava "price <= 0" nesta segunda consulta —
        # não revalidava mercado cruzado. Um tick que ficasse cruzado
        # bem entre o preflight e este laço (janela real, ainda que
        # estreita) tinha o preço do lado escolhido aceito mesmo assim,
        # alimentando order_send() e o stop catastrófico com um preço do
        # lado errado do book. Agora usa _tick_valido() aqui também.
        tick = mt5.symbol_info_tick(broker_sym)
        price = (tick.ask if action == "BUY" else tick.bid) if _tick_valido(tick) else None
        if not price:
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
            # Retry SÓ quando a falha é inequivocamente "não executou".
            # Um timeout/erro de conexão é AMBÍGUO: a ordem pode ter sido
            # preenchida no servidor e só a resposta ter se perdido — reenviar
            # nesse caso dobra o volume da perna. Nesses casos, confirma no
            # broker antes de decidir.
            retcode = res.retcode if res else None
            ambiguous = (res is None or retcode in _ambiguous_retcodes())
            if ambiguous:
                confirmed_volume = _confirm_position_after_ambiguous_retcode(broker_sym, magic)
                if confirmed_volume:
                    success_count += 1
                    # Reporta o volume REAL confirmado no broker, não o lote
                    # pedido — um DONE_PARTIAL abre menos do que foi pedido, e
                    # reportar o pedido como se fosse o executado escondia a
                    # exposição real da cesta (achado em revisão).
                    partial_note = ""
                    if abs(confirmed_volume - lot) > 1e-9:
                        partial_note = (f" — ATENÇÃO: volume confirmado ({confirmed_volume}) "
                                         f"é diferente do lote pedido ({lot}); possível preenchimento parcial")
                    results.append({
                        "pair": pair_sym, "action": action, "lot": confirmed_volume,
                        "entry_price": price, "status": "OPENED",
                        "message": f"Resposta ambígua ({retcode}), mas posição confirmada no broker "
                                   f"(volume real: {confirmed_volume}){partial_note}"})
                    print(f"  [+] {pair_sym} {action}: resposta ambígua, posição CONFIRMADA no broker "
                          f"(volume {confirmed_volume}){partial_note}")
                    continue
                # Não confirmada aberta mesmo após várias tentativas — NUNCA
                # reenviar. "Não vista após N tentativas" não é o mesmo que
                # "confirmado que não abriu", e reenviar às cegas pode dobrar
                # uma perna que só ainda não propagou no broker (bug real,
                # achado em revisão — ver docs da reconciliação com o upstream).
                results.append({
                    "pair": pair_sym, "action": action, "status": "ERROR",
                    "error_code": retcode,
                    "message": "Resposta ambígua e não confirmada aberta — "
                               "NÃO reenviado (evita dobrar a perna). Revisar manualmente."})
                print(f"  [!] {pair_sym} {action}: resposta ambígua ({retcode}), não confirmada "
                      f"— não reenviado. REVISAR MANUALMENTE.")
                continue

            # Fallback de filling: alcançado pra qualquer rejeição NÃO
            # ambígua (retcode ambíguo já foi resolvido/recusado acima, sem
            # chegar aqui) — não filtra por retcode específico de modo de
            # preenchimento, tenta de novo com ORDER_FILLING_RETURN pra
            # qualquer rejeição não ambígua.
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
                # Usa a resposta do REENVIO (res2), não da tentativa original
                # (res) — reportar o erro da 1ª tentativa quando quem falhou
                # foi a 2ª confunde o diagnóstico. Nota: se res2 também vier
                # com um retcode ambíguo, ele NÃO passa por confirmação (só a
                # 1ª tentativa passa) — fica ERROR mesmo que possa ter
                # executado; não há um 3º envio automático, então não há
                # risco de dobrar, só de um falso ERROR. Ver docs da
                # reconciliação com o upstream.
                err_code = res2.retcode if res2 else "N/A"
                err_desc = res2.comment if res2 else "Falha de envio"
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

    Watchdog de confirmação (achado em revisão: consulta de design via
    herdr-ask, plano de reconciliação 27/08, mfc-rev + mfc-rev-2 — "watchdog
    de fechamento"): o upstream (Miquéias) tem uma ideia parecida
    (`d2eb1d3`, até 3 tentativas com espera), mas com dois furos fail-open —
    `positions_get() is None` vira lista vazia (declara zero posições sem
    ter confirmado nada) e `close_all_portfolios()` dele pode terminar
    declarando sucesso mesmo com posições reais sobrando depois das
    tentativas. Esta versão NÃO confia só no `closed_count` que cada
    `close_portfolio_basket()` reporta (que já é fail-closed por si só, mas
    é medido no MOMENTO do order_send, não depois) — reconsulta o broker via
    `get_open_magics_and_symbols()` (que já levanta `MT5QueryError` fail-closed
    em vez de confundir erro de consulta com "nada aberto") pra CONFIRMAR de
    verdade que zerou, e só retenta fechar as moedas que a consulta fresca
    ainda mostra abertas — nunca declara "flat" sem essa leitura confirmando.

    O orçamento de tempo é um DEADLINE (`_CLOSE_WATCHDOG_DEADLINE_SEC`,
    tempo decorrido via `time.monotonic()`, checado no TOPO do loop antes de
    iniciar qualquer rodada nova), não contagem de tentativas — achado em
    revisão (mfc-rev + mfc-rev-2, rodada 10, P2-1/F10-3): a primeira versão
    reusava CSS_AMBIGUOUS_CONFIRM_ATTEMPTS/_DELAY_SEC (do lado da abertura,
    sem prazo externo), e a combinação MÁXIMA válida dessas duas projetava
    até ~426s de espera — estourando a janela 08:00-08:04 real do scheduler
    (240s). Deadline próprio garante o teto independente de qualquer config
    de OUTRA parte do sistema — não é imune à latência do próprio broker
    (ver ressalvas medidas na definição de `_CLOSE_WATCHDOG_DEADLINE_SEC`
    acima: o teto real é "deadline + a rodada em andamento quando ele
    expira", e nenhuma chamada MT5 individual tem timeout próprio).

    A cada rodada, `pendentes` é recalculado do zero contra TODOS os 8
    magics (não só interseção com a lista anterior) — achado F10-2 (Codex,
    confiança média): uma cesta aberta por um caminho concorrente (endpoint
    HTTP manual, por exemplo) enquanto o fechamento está em andamento
    também entra no watchdog, não só as que já estavam na lista inicial.
    Resíduo aceito e não coberto por isto (F11-2, Codex, rodada 11): entre a
    ÚLTIMA consulta de confirmação e o retorno da função ainda existe uma
    janela sem lock — uma abertura concorrente bem nesse intervalo pode não
    ser vista. Não há mecanismo de exclusão mútua entre abrir e fechar neste
    código; fechar sempre confia na consulta mais recente que conseguiu
    fazer. A rede de segurança pra ambos os resíduos (consulta inicial vazia
    E esta última janela) é a reconciliação das 08:10
    (`execute_phase_0810`), que reconsulta de forma totalmente independente,
    minutos depois.
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

    # Só itera as moedas que REALMENTE têm posição: antes chamava
    # close_portfolio_basket pras 8 sempre, cada uma refazendo positions_get.
    magics_abertos = {p.magic for p in positions}
    pendentes = [c for c, m in PORTFOLIO_MAGICS.items() if m in magics_abertos]
    if not pendentes:
        print("[ENCERRAMENTO 08:00 BRT] Nenhuma posição dos portfólios aberta.")
        return {"success": True, "total_closed": 0, "currencies_closed": {},
                "failures": [], "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

    total_closed = 0
    # {ccy: {"closed_count": N, "currency": ccy}} — só o total acumulado por
    # moeda, deliberadamente sem `target_count`/`results` de uma rodada
    # específica (achado F11-4, Codex, rodada 11): guardar o `target_count`
    # da ÚLTIMA rodada ao lado de um `closed_count` ACUMULADO de várias
    # rodadas produzia um resumo internamente incoerente (ex.: closed_count=7
    # ao lado de target_count=1, porque a última rodada só tinha 1 perna
    # pendente). Cada rodada é isolada por natureza; só o total faz sentido
    # agregado.
    summary_by_ccy = {}
    # Contagem por (moeda, erro, estágio) em vez de uma entrada por rodada —
    # achado P2-1 (mfc-rev-2, rodada 11, medido em produção sintética): um
    # broker que rejeita sistematicamente gerava uma entrada IDÊNTICA por
    # rodada (2024 entradas medidas com deadline curto, ~16 MB projetado pro
    # deadline máximo, despejadas inteiras num único print) — o operador
    # precisa saber "quais erros apareceram antes de ceder", não uma cópia
    # por rodada do mesmo erro. Bounded por natureza: no máximo (moedas ×
    # tipos de erro distintos) chaves, nunca por número de rodadas.
    transient_counts = {}
    deadline = time.monotonic() + _CLOSE_WATCHDOG_DEADLINE_SEC
    # None enquanto a última consulta de confirmação tentada deu certo (ou
    # nenhuma rodou ainda); vira a mensagem de erro assim que uma falhar, e
    # só volta a None quando uma consulta seguinte tiver sucesso. Existe pra
    # um único motivo: com a checagem de deadline agora no TOPO do loop
    # (ver abaixo), o loop pode sair sem NUNCA chegar a tentar uma nova
    # confirmação na última iteração — sem isto, esse caso perderia a
    # distinção entre "confirmamos que ainda tem posição aberta" e "não
    # conseguimos nem confirmar", colapsando os dois no mesmo
    # "still_open_after_deadline" e escondendo que o broker parou de
    # responder, não que as posições resistiram.
    ultimo_erro_confirmacao = None

    while True:
        # Checagem no TOPO do loop — achado F11-1 (Codex, rodada 11): a
        # versão anterior só conferia o deadline DEPOIS de uma rodada
        # inteira (fechamento de todas as pendentes + reconsulta), então
        # podia iniciar mais uma rodada completa já fora do prazo depois de
        # um sleep que consumisse o resto do orçamento. Checar aqui garante
        # que nenhuma rodada NOVA começa após o deadline — o residual
        # "deadline + duração da rodada já em andamento quando ele expira"
        # continua existindo (não dá pra interromper um order_send em voo
        # sem arriscar coisa pior), mas não se soma mais uma rodada inteira
        # de propósito.
        if time.monotonic() >= deadline:
            break
        for ccy in pendentes:
            # Isolamento por moeda: uma falha não pode impedir o fechamento
            # das outras cestas — fechar é redutor de risco, sempre segue
            # adiante.
            try:
                res = close_portfolio_basket(ccy, deviation=deviation)
            except Exception as e:
                # Achado P3-2 (mfc-rev-2, rodada 13, medido pelos dois
                # independentemente): str(e) completo pode conter
                # ticket/retcode/timestamp — cada valor distinto vira uma
                # chave nova, quebrando o bound "moedas × tipos de erro"
                # que o dedup promete (medido: 8 chaves com mensagem
                # estável, 2.376 chaves/213KB com mensagem variável). O
                # ramo close_attempt já usa um código curto e fixo
                # (res.get("error")); aqui, type(e).__name__ dá a mesma
                # propriedade sem exigir que o binding MT5 real garanta
                # mensagens estáveis.
                key = (ccy, type(e).__name__, "exception")
                transient_counts[key] = transient_counts.get(key, 0) + 1
                print(f"[PORTFOLIO ROBOT {ccy}] Exceção ao fechar: {e}")
                continue
            closed_now = res.get("closed_count", 0)
            if closed_now > 0:
                total_closed += closed_now
                prev = summary_by_ccy.get(ccy, {}).get("closed_count", 0)
                summary_by_ccy[ccy] = {"currency": ccy, "closed_count": prev + closed_now}
            elif not res.get("success", False) and res.get("error"):
                key = (ccy, res.get("error"), "close_attempt")
                transient_counts[key] = transient_counts.get(key, 0) + 1
                summary_by_ccy.setdefault(ccy, {"currency": ccy, "closed_count": 0})

        # Confirmação de verdade — não confia no closed_count reportado
        # acima sozinho. get_open_magics_and_symbols() levanta MT5QueryError
        # (nunca devolve "nada aberto" por engano) se a consulta falhar.
        try:
            ainda_abertos = get_open_magics_and_symbols()
            ultimo_erro_confirmacao = None
        except MT5QueryError as e:
            ultimo_erro_confirmacao = str(e)
            # Achado F13-2 (Codex, rodada 13): duas leituras de time.monotonic()
            # pra decidir "ainda dá tempo?" e "quanto dormir?" tinham uma janela
            # onde a 2ª leitura passava do deadline entre uma chamada e outra —
            # time.sleep(negativo) levanta ValueError, que escapava desta função
            # e virava "exceção no encerramento" sem relação com a causa real.
            # Uma leitura só, comparada contra zero, fecha a janela.
            tempo_restante = deadline - time.monotonic()
            if tempo_restante > 0:
                time.sleep(min(_CLOSE_WATCHDOG_POLL_INTERVAL_SEC, tempo_restante))
                continue
            break

        # Recalcula do zero contra TODOS os magics — acha F10-2: uma cesta
        # aberta por um caminho concorrente durante o fechamento também
        # precisa entrar aqui, não só o que já estava em `pendentes`.
        pendentes = [c for c, m in PORTFOLIO_MAGICS.items() if m in ainda_abertos]
        if not pendentes:
            break
        tempo_restante = deadline - time.monotonic()
        if tempo_restante <= 0:
            break
        time.sleep(min(_CLOSE_WATCHDOG_POLL_INTERVAL_SEC, tempo_restante))

    transient_errors = [{"currency": c, "error": err, "stage": stage, "count": n}
                        for (c, err, stage), n in transient_counts.items()]

    # Saída única — acha F11-1 (Codex, rodada 11): mover a checagem de
    # deadline pro topo do loop podia fazer o loop sair SEM nunca chegar a
    # tentar confirmar de novo na última iteração, o que colapsava "não
    # conseguimos confirmar" em "confirmamos que ainda tem posição aberta"
    # se o retorno continuasse dividido entre um `return` no meio do loop e
    # outro no final. `ultimo_erro_confirmacao` carrega esse estado através
    # de qualquer jeito de sair do loop.
    if ultimo_erro_confirmacao is not None:
        msg = (f"Fechamento tentado, mas o resultado NÃO pôde ser confirmado "
               f"(consulta ao broker falhou): {ultimo_erro_confirmacao}")
        print(f"[ENCERRAMENTO 08:00 BRT] {msg}")
        return {
            "success": False,
            "error": "position_query_failed",
            "message": msg,
            "total_closed": total_closed,
            "currencies_closed": summary_by_ccy,
            "failures": [{"currency": c, "error": "position_query_failed"} for c in pendentes],
            "transient_errors": transient_errors,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    # O que sobrar em `pendentes` aqui é o que uma consulta FRESCA ao broker
    # ainda mostra aberto, depois do deadline — nunca "declarado" fechado
    # sem essa confirmação.
    failures = [
        {"currency": c, "error": "still_open_after_deadline",
         "message": f"{c} continua com posição aberta depois de "
                    f"{_CLOSE_WATCHDOG_DEADLINE_SEC:.0f}s de tentativas de fechamento."}
        for c in pendentes
    ]

    print(f"\n[ENCERRAMENTO 08:00 BRT] Total de posições fechadas: {total_closed}")
    if failures:
        print(f"[ENCERRAMENTO 08:00 BRT] ATENÇÃO — falhas por moeda: {failures}")
    if transient_errors:
        print(f"[ENCERRAMENTO 08:00 BRT] Erros superados em rodadas anteriores (moeda/erro/vezes): {transient_errors}")
    return {
        "success": not failures,
        "total_closed": total_closed,
        "currencies_closed": summary_by_ccy,
        "failures": failures,
        "transient_errors": transient_errors,
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


def _save_telemetry_file(telemetry: dict):
    """Persiste o snapshot de telemetria pro dashboard web.

    Estava sendo CHAMADA em dois pontos e não existia em lugar nenhum do
    repositório — NameError garantido, ou seja: o acompanhamento da cesta
    durante a madrugada nunca funcionou. Escrita atômica porque o dashboard
    lê este arquivo em paralelo. Nunca propaga exceção: falha de telemetria
    não pode derrubar quem estava tratando de posição real."""
    try:
        _atomic_write_json(TELEMETRY_FILE, telemetry)
    except Exception as e:
        print(f"[-] Erro ao gravar telemetria em {TELEMETRY_FILE}: {e}")


def get_mt5_files_dir():
    """Retorna a pasta MQL5/Files da instância MT5 portable dedicada ao MFC,
    derivada de MT5_PATH (CSS_MT5_TERMINAL_PATH). Em modo /portable cada
    instância mantém seus próprios arquivos localmente — como essa instância é
    exclusiva do MFC (não compartilhada com outras estratégias/contas na mesma
    máquina), não há necessidade de FILE_COMMON pra Python e o EA guardião
    enxergarem o mesmo arquivo; o diretório local já basta.

    Exige que MT5_PATH aponte pra um terminal64.exe que REALMENTE existe em
    disco antes de criar qualquer diretório. Sem essa checagem, um
    CSS_MT5_TERMINAL_PATH mal configurado (typo, instância errada) fazia
    os.makedirs criar uma árvore MQL5/Files "fantasma" embaixo de um caminho
    que não é o terminal de verdade — kill switch e sinais eram gravados lá,
    reportando sucesso, e o EA (que lê a pasta do terminal real) nunca via
    nada. Falhar fechado aqui (None) é melhor que sincronizar com o lugar
    errado em silêncio."""
    if not MT5_PATH or not os.path.isfile(MT5_PATH):
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
    #
    # NÃO engole exceção aqui (achado ALTO em revisão): SIGNALS_FILE é a
    # fonte que execute_phase_2105 realmente lê pra decidir se abre a cesta.
    # Engolir e devolver o payload normalmente mesmo com a escrita falhada
    # fazia execute_phase_2102() "ter sucesso" e liberar 21:05 com o que já
    # estivesse em disco antes (sinal de mais cedo hoje, ou de ontem) —
    # quem chama precisa saber que isso falhou pra NÃO liberar a abertura.
    _atomic_write_json(SIGNALS_FILE, signals_payload)
    print(f"[+] Sinais de Portfólio gravados localmente em: {SIGNALS_FILE}")

    # 2. Salvar na pasta MQL5/Files da instância MT5 dedicada — mesma
    #    garantia atômica. Best-effort (só o EA em modo legado,
    #    InpEaOpensBasket=true, lê isso diretamente — o caminho Python já
    #    tem o payload em memória): uma falha aqui não invalida o sinal
    #    que 21:05 usa, por isso continua sendo capturada, não propagada.
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
