"""
SERVIÇO DE DADOS E CÁLCULO DO CSS (Currency Slope Strength)
Gerencia a conexão com o MetaTrader 5, cálculo em tempo real de múltiplos timeframes,
cache inteligente e orquestração dos motores de confluência e Tríade Analítica.
"""

import os
import sys
import json
import tempfile
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

from agents.confluence_engine import (
    BRT,
    evaluate_currency_confluence,
    evaluate_28_pairs_confluence,
)
from confluence_config import resolve_confluence_engine
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

# Sufixo descoberto sozinho no servidor (achado em revisão: CSS_MT5_SYMBOL_SUFFIX
# exige alguém medir manualmente e configurar antes de qualquer ordem funcionar
# — cada corretora nova, como a do Miquéias, repetiria esse trabalho). None =
# ainda não tentou OU tentou e falhou (ver _LAST_FAILED_FAMILY_DETECTION_AT
# pra distinguir os dois — achado em revisão, Codex rodada 3: este comentário
# só descrevia dois estados quando na verdade já existem três); string (mesmo
# vazia) = já tentou, resultado VALIDADO e memorizado pro resto do processo.
_AUTO_DETECTED_SUFFIX = None
_SUFFIX_PROBE_PAIR = "EURUSD"  # existe em toda corretora forex, referência estável

# Cooldown entre tentativas de detecção que FALHAM (achado em revisão:
# mfc-rev-2, achado 1 rodada 2, medido). "Não memorizar falha" (pra não
# travar num "nunca resolve" antes do MT5 acabar de conectar) tem um custo
# que a versão anterior não pagava: to_broker_symbol() é chamado por
# calculate_full_css() 28 vezes por timeframe — ~140 vezes por ciclo de
# update_data(), que recalcula a cada 3s sob uso ativo do dashboard (ver
# CLAUDE.md, "throttled to at most one recompute per 3s"). Numa corretora
# onde a família nunca fecha, cada uma dessas 140 chamadas refaria o
# symbols_get + a validação inteira (até 28 symbol_info por candidato) — a
# mesma sonda que mediu isso encontrou ~3920 chamadas MT5 por ciclo,
# repetindo a cada 3s, contra o MESMO IPC do terminal que envia ordem real.
# O cooldown limita a NOVA tentativa a, no máximo, uma vez a cada intervalo
# — preserva "não desiste pra sempre" sem virar tempestade de IPC.
_LAST_FAILED_FAMILY_DETECTION_AT = None
_FAMILY_DETECTION_COOLDOWN_SECONDS = 15

# Persistência da família entre PROCESSOS (achado em revisão: Codex, achado 1
# rodada 3). O daemon (scripts/scheduler_daemon.py) e o web server
# (web/server.py, que também expõe /api/portfolio-robots/open — ordem real,
# não só leitura) são processos SEPARADOS, cada um com seu próprio
# _AUTO_DETECTED_SUFFIX em memória. dict.fromkeys() garante que os DOIS
# cheguem à mesma escolha SE o servidor devolver os candidatos na MESMA
# ordem pros dois — mas isso é propriedade do servidor, não garantida pelo
# código. Sem isso, um cenário raro mas real: corretora nova, sufixo ainda
# não configurado, e o daemon abrindo às 21:05 enquanto alguém aciona a API
# manual ao mesmo tempo — os dois podem validar famílias diferentes, cada
# um internamente consistente, mas divergentes entre si.
#
# Resolvido pelo mesmo padrão que o resto do projeto já usa pra estado
# compartilhado entre processos (CSS_KILL.flag, os vários JSON em data/):
# um arquivo simples. O PRIMEIRO processo a validar uma família grava aqui;
# qualquer processo (o mesmo ou outro) que ainda não tem uma família em
# memória LÊ o arquivo antes de reconsultar o servidor do zero — mas
# revalida contra os 28 pares antes de confiar (nunca herda cegamente um
# arquivo de uma corretora antiga ou de uma versão de teste).
_FAMILY_STATE_FILE = os.path.join(BASE_DIR, "data", "mt5_symbol_family.json")


def _current_account_identity():
    """(login, server) da conta MT5 atualmente conectada, ou (None, None) se
    indisponível — nunca lança. Usado só pra rejeitar um arquivo de família
    persistido por uma CONTA/CORRETORA diferente (ver _read_persisted_family);
    não afeta nenhuma outra decisão.

    Filtra pra (str, int, float, None): um dublê de teste mínimo sem
    account_info configurado devolve um MagicMock encadeado — não é
    serializável em JSON, e escrever isso quebraria _persist_family. O MT5
    real nunca devolve outra coisa pra login/server."""
    try:
        acc = mt5.account_info()
    except Exception:
        return (None, None)
    if acc is None:
        return (None, None)
    login = getattr(acc, "login", None)
    server = getattr(acc, "server", None)
    login = login if isinstance(login, (str, int, float)) else None
    server = server if isinstance(server, (str, int, float)) else None
    return (login, server)


def _read_persisted_family():
    """Lê o arquivo de família persistida (dict completo, ou None). None se
    não existe, está corrompido, ou não tem o campo "suffix" — trata
    qualquer coisa que não seja isso como se não houvesse persistência
    nenhuma, nunca lança.

    Achado em revisão (mfc-rev-2, rodada 4, medido): a versão anterior só
    capturava (OSError, json.JSONDecodeError) — um arquivo com bytes que não
    decodificam como UTF-8 (disco corrompido, escrita interrompida a meio
    caractere) lança UnicodeDecodeError no open() ANTES do json.load, e isso
    não é um JSONDecodeError (não herda dele). Reproduzido: derrubava
    to_broker_symbol() inteiro, numa corretora saudável, só porque um
    arquivo de CACHE entre processos tinha bytes ruins. Um otimização entre
    processos não pode ter esse poder — captura tudo, deliberadamente."""
    try:
        with open(_FAMILY_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("suffix"), str):
        return None
    return data


def _persisted_family_is_trustworthy(persisted):
    """Um arquivo persistido só é adotado se (a) a família ainda for
    consistente nos 28 pares AGORA (broker pode ter mudado) e (b) a conta
    gravada bater com a conta ATUAL, quando as duas são conhecidas.

    Achado em revisão (mfc-rev-2/Codex, rodada 4): validar só o nocional não
    basta — se o mesmo `data/` for reaproveitado entre contas/corretoras
    diferentes, e a família antiga por coincidência também for válida (mas
    não a pretendida) na conta nova, ela passaria despercebida. Identidade
    conhecida e DIFERENTE reprova na hora, sem gastar symbol_info nenhum.
    Quando qualquer lado da identidade é desconhecido (conta ainda não
    conectou, arquivo antigo sem esse campo) cai pra validação só de
    nocional — permissivo de propósito: travar a otimização inteira porque
    a identidade não pôde ser confirmada custaria mais do que vale, e o
    cenário que isso deixa passar (mesmo checkout, conta trocada, família
    coincidentemente válida na nova) é mais raro que "conta ainda
    conectando"."""
    login, server = _current_account_identity()
    p_login, p_server = persisted.get("login"), persisted.get("server")
    if login is not None and p_login is not None and login != p_login:
        return False
    if server is not None and p_server is not None and server != p_server:
        return False
    return _symbol_family_is_consistent(persisted["suffix"])


def _persist_family(suffix):
    """Grava a família validada pra outros processos lerem, junto com a
    identidade da conta atual (ver _persisted_family_is_trustworthy).
    Escrita atômica (tempfile no mesmo diretório + os.replace, mesmo padrão
    de agents/portfolio_executor.py:_atomic_write_json) — um leitor
    concorrente nunca vê o arquivo pela metade. Falha aqui NUNCA propaga:
    persistir é otimização entre processos, não requisito pra este processo
    continuar funcionando com a família que ele mesmo já validou em
    memória."""
    try:
        login, server = _current_account_identity()
        directory = os.path.dirname(_FAMILY_STATE_FILE)
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"suffix": suffix, "probe_pair": _SUFFIX_PROBE_PAIR,
                           "login": login, "server": server}, f)
            os.replace(tmp_path, _FAMILY_STATE_FILE)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as e:
        print(f"[-] Falha ao persistir família de símbolos pra outros processos (não afeta "
              f"este processo, que já validou '{suffix}' em memória): {e}")


def _symbol_family_is_consistent(suffix):
    """Um sufixo só vira a família adotada se TODOS os 28 pares existirem com
    ele E tiverem o MESMO trade_contract_size entre si — nocional diferente
    por perna é o defeito que este fail-closed existe pra evitar.

    Achado em revisão (mfc-rev-2, achado 1 sobre o commit c24a44c): o sufixo
    auto-detectado perdia pro nome puro par a par — cada to_broker_symbol()
    decidia sozinho, olhando só AQUELE par, sem saber o que os outros 27
    tinham decidido. Numa corretora que lista duas séries (ex.: EURUSD e
    CADCHF de graça, sem sufixo, contrato padrão 100000; e EURUSDm, GBPUSDm
    etc., só com sufixo, contrato micro 1000), cada par resolvia pra série
    que existisse PRA ELE primeiro — a cesta de 7 pernas podia sair com
    nocional 100x diferente entre pernas, todas FULL, todas com tick, o
    preflight aprovando tudo. Corrigido não validando o sufixo sozinho, mas
    testando se ele forma uma família INTEIRA e coerente antes de confiar
    nele pra qualquer par.

    DELIBERADAMENTE não checa trade_mode aqui (proposto em revisão,
    mfc-rev-2 rodada 3, e revertido depois de medir a regressão — medição
    confirmada de novo na rodada seguinte com order_send real: 6/8 cestas
    abrem sem a checagem contra 0/8 com ela, no caso de família única). O
    motivo de fundo (mfc-rev-2, rodada 3, argumento mais forte que o de
    granularidade): trade_contract_size é uma propriedade ESTÁTICA do
    contrato — não muda sem o broker redefinir o instrumento. trade_mode é
    TRANSIENTE — muda por sessão, feriado, decisão pontual do broker. Esta
    função memoriza a escolha pro resto do processo; usar um dado transiente
    como veto de uma decisão permanente é incompatibilidade de tempo de
    vida, não só granularidade errada. O preflight em portfolio_executor.py
    já rejeita trade_mode restrito por perna, escopado à moeda certa — a
    checagem aqui, na granularidade errada (família inteira, permanente),
    trocaria uma falha pequena e correta por uma grande e desnecessária.
    NÃO IMPLEMENTADO, considerado e descartado (achado reaberto sem evidência
    nova: Codex, F-03, herdr-review rodada 6; decisão final do usuário,
    2026-08-27): preferir o candidato mais aberto SÓ quando existem MÚLTIPLOS
    candidatos igualmente consistentes em nocional, ou recusar em vez de
    escolher nesse caso — nenhuma das duas foi adotada. Mantida a heurística
    atual (sufixo mais curto, desempate pela ordem do servidor): o caminho
    automático só roda quando CSS_MT5_SYMBOL_SUFFIX não está configurado, e a
    orientação operacional deste projeto é configurar essa variável
    explicitamente em qualquer instalação real — o cenário de ambiguidade é
    dormant na prática, não vale a complexidade extra de recusar-quando-
    ambíguo agora."""
    sizes = set()
    for pair in ALL_28_PAIRS:
        try:
            info = mt5.symbol_info(pair + suffix)
        except Exception:
            return False
        if info is None:
            return False
        size = getattr(info, "trade_contract_size", None)
        # not size (não só "is None") pega também 0/negativo — achado em
        # revisão (Codex rodada 3): um contrato zerado ou negativo é
        # inválido, mas "todos zerados" passaria no teste de len(sizes)==1
        # sem esta checagem.
        if not size or size < 0:
            return False
        sizes.add(size)
    return len(sizes) == 1


def reset_family_detection_cooldown():
    """Zera o cooldown de detecção de família, forçando a PRÓXIMA chamada a
    tentar de novo mesmo que a última tentativa tenha falhado há pouco.

    Existe pra um único chamador: o aquecimento em
    agents/portfolio_executor.py, ANTES de abrir uma cesta. Achado em
    revisão (mfc-rev-2, achado 1 rodada 3, medido): o cooldown de 15s
    (pensado pro caminho quente do dashboard, que recalcula a cada 3s) é
    LONGO DEMAIS pra fase de abertura — ela inteira (8 moedas × 7 pernas)
    roda em segundos, bem dentro da janela do cooldown. Uma falha
    transitória bem na primeira tentativa da noite (ex.: MT5 ainda
    terminando de conectar às 21:05) condenava as 8 cestas daquela noite
    inteira, porque nenhuma tentativa seguinte, na mesma execução, chegava a
    reconsultar. Chamar isto antes do aquecimento custa no máximo 8
    detecções extras por noite (uma por moeda que tentar abrir) — irrelevante
    perto do que uma noite inteira sem cesta custaria."""
    global _LAST_FAILED_FAMILY_DETECTION_AT
    _LAST_FAILED_FAMILY_DETECTION_AT = None


def _detect_mt5_symbol_family():
    """Descobre e VALIDA um sufixo (possivelmente vazio, "família bare") que
    cubra os 28 pares com trade_contract_size consistente — não só o
    par-sonda. Só é chamada quando CSS_MT5_SYMBOL_SUFFIX não está
    configurado (a configuração manual continua tendo precedência absoluta,
    sem validação — é decisão explícita do operador).

    Candidatos vêm de UMA consulta ao servidor (mt5.symbols_get com padrão
    no par de referência); cada candidato é então validado contra os 28
    pares em _symbol_family_is_consistent(). O primeiro que passar (mais
    curto primeiro; empate desfeito pela ordem em que o SERVIDOR devolveu —
    não alfabética, não aleatória DENTRO deste processo) vira a família
    adotada, memorizada pro resto do processo E persistida em
    _FAMILY_STATE_FILE pra outros processos lerem.

    Coordenação entre processos (achado em revisão, Codex rodada 3, decisão
    do usuário): o daemon e o web server (que também expõe
    /api/portfolio-robots/open — ordem real, não só leitura) são processos
    separados; sem coordenação, cada um podia validar uma família diferente
    se o servidor devolvesse candidatos em ordens diferentes pra cada
    conexão. Fechado por arquivo, não por lock: o primeiro processo a
    validar grava (e relê depois de gravar — ver mais abaixo); qualquer
    processo (o mesmo depois de reiniciar, ou outro) tenta ler o arquivo
    ANTES de reconsultar o servidor do zero — mas revalida o que leu contra
    os 28 pares E contra a identidade da conta atual (nunca herda cegamente;
    ver _persisted_family_is_trustworthy).

    Residual medido e aceito (mfc-rev-2, rodada 4, reproduzido com
    subprocessos reais: 3 de 5 execuções divergiram sem esta mitigação):
    dois processos que iniciam EXATAMENTE juntos, com o arquivo ainda
    inexistente, podem cada um validar uma família diferente antes de
    qualquer um persistir — não há lock, só arquivo. O reread-after-write
    (mais abaixo) fecha a maioria das corridas reais (qualquer uma em que a
    escrita de um processo aconteça antes da releitura do outro), mas não
    a simultaneidade exata. Um processo que JÁ adotou uma família em
    memória (_AUTO_DETECTED_SUFFIX não-None) nunca mais olha pro arquivo —
    é o mesmo motivo pelo qual o cache normal não expira: a família é uma
    verdade de sessão, não algo que deva mudar sob os pés do processo.
    Fechar isso por completo exigiria um lock de verdade (com detecção de
    dono morto) — desproporcional ao risco: exige corretora sem sufixo
    configurado (produção atual tem) E arranque literalmente simultâneo de
    dois processos.

    Fail-closed: se NENHUM candidato cobrir os 28 pares com nocional
    consistente, devolve None — mas com um COOLDOWN antes de tentar de novo
    (ver _FAMILY_DETECTION_COOLDOWN_SECONDS), não sem memorizar nada.
    Medido em revisão (mfc-rev-2, achado 1 rodada 2): sem o cooldown, cada
    chamada de to_broker_symbol() com família indeterminada refazia a
    consulta E a validação inteira do zero — e to_broker_symbol() é chamado
    ~140 vezes por ciclo de update_data() (calculate_full_css, 28 pares ×
    5 timeframes), que recalcula a cada 3s sob uso ativo do dashboard (ver
    CLAUDE.md). Numa corretora onde a família nunca fecha, isso media ~3920
    chamadas MT5 por ciclo, repetindo a cada 3s, contra o MESMO IPC do
    terminal que também envia ordem real — tempestade, não fail-closed
    barato. O cooldown limita a nova tentativa a uma vez por janela,
    preservando "não desiste pra sempre" (ainda tenta de novo depois do
    MT5 acabar de conectar) sem virar tempestade.

    O CHECK DE COOLDOWN VEM ANTES DA LEITURA DO ARQUIVO PERSISTIDO — achado
    em revisão (mfc-rev-2, rodada 4, medido): a versão anterior lia e
    revalidava o arquivo ANTES do cooldown, então um arquivo presente mas
    OBSOLETO reintroduzia a mesma tempestade que o cooldown existe pra
    evitar — cada uma das ~140 chamadas por ciclo refazia a validação
    inteira contra os 28 pares (medido: ~3948 chamadas MT5/ciclo, o mesmo
    regime que motivou o cooldown na rodada 2). Corrigido: cooldown primeiro
    (path mais barato, sem tocar disco nem MT5); se o arquivo existir mas
    reprovar, ARMA o cooldown também — um arquivo ruim não pode custar mais
    que uma detecção do zero que também falha."""
    global _AUTO_DETECTED_SUFFIX, _LAST_FAILED_FAMILY_DETECTION_AT
    if _AUTO_DETECTED_SUFFIX is not None:
        return _AUTO_DETECTED_SUFFIX
    if not MT5_AVAILABLE or mt5 is None:
        return None
    now = time.monotonic()
    if (_LAST_FAILED_FAMILY_DETECTION_AT is not None
            and now - _LAST_FAILED_FAMILY_DETECTION_AT < _FAMILY_DETECTION_COOLDOWN_SECONDS):
        return None
    # Outro processo já pode ter validado — tenta a leitura ANTES de gastar
    # um symbols_get() + até 28×N symbol_info(). Revalida sempre (nocional E
    # identidade de conta — ver _persisted_family_is_trustworthy); se
    # reprovar, arma o cooldown igual a uma detecção do zero que falhou, e
    # segue pro caminho normal de descoberta.
    persisted = _read_persisted_family()
    if persisted is not None:
        if _persisted_family_is_trustworthy(persisted):
            _AUTO_DETECTED_SUFFIX = persisted["suffix"]
            _LAST_FAILED_FAMILY_DETECTION_AT = None
            print(f"[+] Família de símbolos lida de outro processo e revalidada nos 28 pares "
                  f"(sufixo {persisted['suffix']!r})")
            return _AUTO_DETECTED_SUFFIX
        _LAST_FAILED_FAMILY_DETECTION_AT = now
    try:
        matches = mt5.symbols_get(f"*{_SUFFIX_PROBE_PAIR}*")
    except Exception:
        matches = None
    if not matches:
        _LAST_FAILED_FAMILY_DETECTION_AT = now
        print(f"[-] Nenhum símbolo candidato encontrado pra {_SUFFIX_PROBE_PAIR!r} no servidor "
              f"— família de símbolos indeterminada (retenta em até "
              f"{_FAMILY_DETECTION_COOLDOWN_SECONDS}s). Verifique se o MT5 já conectou de "
              f"verdade e se o par de referência existe nesta corretora.")
        return None
    # Nomes que começam com o par de referência, REALMENTE negociáveis nos
    # dois sentidos (trade_mode == FULL) — achado em revisão /dual-r: filtrar
    # só "!= DISABLED" (versão anterior) ainda deixava passar CLOSEONLY/
    # LONGONLY/SHORTONLY como candidato a sufixo padrão. E filtrar por
    # "visible" era o filtro ERRADO: visible é só estado de UI (Market
    # Watch, mutável por symbol_select), não direito de negociar. Ausência
    # do campo trade_mode (só ocorre em dublês de teste mínimos, nunca no
    # MT5 real) é tratada como "presumir FULL". O nome puro (par-sonda sem
    # sufixo) entra nesta mesma lista como candidato "" quando ele próprio
    # aparecer no resultado — não é mais tratado à parte, o que ELIMINA a
    # inversão de precedência do achado 1 (bare deixou de ter um caminho
    # próprio que ignorava a validação de família).
    full_mode = getattr(mt5, "SYMBOL_TRADE_MODE_FULL", 4)
    # dict.fromkeys(...) em vez de um set: deduplica candidatos preservando
    # a ORDEM EM QUE O SERVIDOR OS DEVOLVEU. Um set aqui (achado em revisão,
    # medido por mfc-rev-2 com 12 processos e PYTHONHASHSEED variando: "m"
    # venceu em 4, "z" venceu em 8) faz o desempate entre candidatos do
    # MESMO comprimento depender da ordem de iteração do set — aleatória por
    # processo — em vez de qualquer coisa vinda do servidor. Dois processos
    # do mesmo sistema (o daemon e o web server) podiam decidir famílias
    # DIFERENTES a partir dos MESMOS dados, cada um determinístico consigo
    # mesmo mas divergente do outro.
    seen = dict.fromkeys(
        s.name[len(_SUFFIX_PROBE_PAIR):]
        for s in matches
        if s.name.startswith(_SUFFIX_PROBE_PAIR)
        and getattr(s, "trade_mode", full_mode) == full_mode
    )
    candidates = sorted(seen, key=len)
    for suffix in candidates:
        if _symbol_family_is_consistent(suffix):
            _persist_family(suffix)
            # Relê IMEDIATAMENTE depois de escrever (achado em revisão,
            # mfc-rev-2, rodada 4): estreita a janela de corrida entre
            # processos que cold-startam juntos. Se outro processo escreveu
            # por cima entre a nossa escrita e esta releitura, adotamos o
            # dele — não o nosso — desde que ainda seja íntegro; assim os
            # dois processos convergem pro MESMO valor sempre que suas
            # janelas de escrita se sobrepõem, mesmo que a ordem de quem
            # escreveu por último seja imprevisível. Não elimina a
            # simultaneidade EXATA (ver docstring da função) — reduz.
            winner = _read_persisted_family()
            if (winner is not None and winner["suffix"] != suffix
                    and _persisted_family_is_trustworthy(winner)):
                suffix = winner["suffix"]
            _AUTO_DETECTED_SUFFIX = suffix
            _LAST_FAILED_FAMILY_DETECTION_AT = None
            print(f"[+] Família de símbolos detectada e validada nos 28 pares "
                  f"(sufixo {suffix!r}, trade_contract_size consistente)")
            return _AUTO_DETECTED_SUFFIX
    _LAST_FAILED_FAMILY_DETECTION_AT = now
    # Achado em revisão (mfc-rev-2, achado 1 rodada 3): a versão anterior
    # falhava em SILÊNCIO absoluto — o preflight recusava a cesta apontando
    # pra "confira CSS_MT5_SYMBOL_SUFFIX", a variável ERRADA quando o
    # problema é a auto-detecção (que só roda quando essa variável está
    # justamente vazia). Numa corretora nova sem sufixo configurado — o caso
    # de uso que a auto-detecção existe pra servir — essa seria a primeira
    # falha que o operador veria, apontando pro lugar errado.
    print(f"[-] {len(candidates)} candidato(s) encontrado(s) pro par-sonda "
          f"({', '.join(repr(c) for c in candidates)}), mas nenhum cobre os 28 pares com "
          f"trade_contract_size consistente — família de símbolos indeterminada (retenta em "
          f"até {_FAMILY_DETECTION_COOLDOWN_SECONDS}s). Corretora provavelmente mistura séries "
          f"(ex.: padrão e micro/cent) sem um sufixo único que resolva tudo — considere fixar "
          f"CSS_MT5_SYMBOL_SUFFIX manualmente.")
    return None


# Devolvida por to_broker_symbol() quando a família ainda não está
# determinada E o MT5 está disponível pra confirmar. Achado em revisão
# (Codex, achado 1 rodada 2): devolver o nome PURO nesse caso era uma
# proteção só de fachada — o preflight (agents/portfolio_executor.py) não
# confia no cache desta função, ele revalida chamando mt5.symbol_info() por
# conta própria. Se o nome puro devolvido aqui por FALTA de família
# acontecesse de EXISTIR no servidor (ainda que numa série com nocional
# diferente das outras pernas — o próprio cenário que este fail-closed
# existe pra evitar), o preflight aceitava, porque pra ele "existe" já
# bastava. O marcador abaixo é reservado por convenção, NÃO por garantia
# formal do binding (achado em revisão, Codex rodada 3: "#" aparece em
# nomes reais de CFD de ações tipo "#AAPL", e símbolos customizados também
# permitem — a MetaQuotes não proíbe o caractere). A colisão exata com
# "<PAR>#unresolved-family" continua praticamente impossível — nenhuma
# corretora nomeia um instrumento assim — mas isto é convenção reservada,
# não impossibilidade comprovada. Enquanto isso, mt5.symbol_info(<isto>)
# devolve None — o preflight recusa a cesta inteira pelo caminho já testado
# de "símbolo não resolvido", em vez de arriscar aceitar um nome bare por
# coincidência.
#
# Achado reaberto sem evidência nova (Codex, F-02, herdr-review rodada 6);
# decisão final do usuário (2026-08-27): manter como está. Tornar a colisão
# PROVADAMENTE impossível (ex.: to_broker_symbol() devolver None em vez de
# string) exigiria atualizar 10+ pontos de chamada em 4 arquivos, a maioria
# fora do caminho crítico de execução (telemetria/backtest/auditoria) — mais
# superfície de regressão do que o risco teórico que resolve. Também é um
# caminho dormant na instalação atual: CSS_MT5_SYMBOL_SUFFIX já é configurado
# explicitamente, então _detect_mt5_symbol_family() (única chamadora deste
# marcador) nunca roda.
_UNRESOLVED_FAMILY_MARKER = "#unresolved-family"


def to_broker_symbol(pair: str) -> str:
    """Nome lógico do par (ex.: 'EURUSD') -> nome no servidor da corretora
    (ex.: 'EURUSDm'). TODO par usa a MESMA família — nunca uma decisão por
    par (ver _symbol_family_is_consistent para o porquê). Sufixo configurado
    (CSS_MT5_SYMBOL_SUFFIX) é aceito verbatim, sem validação de família —
    decisão explícita do operador, e por isso também não cai pro nome puro
    quando um par específico não existir com ele: cair mudaria de família
    pra ESSE par só, reabrindo o mesmo defeito por outra porta.

    Sem configuração, usa a família auto-detectada e validada nos 28 pares.
    Enquanto ela não resolve: se o MT5 nem está disponível pra checar nada
    (app ainda não conectou), devolve o nome puro só pra log ficar legível —
    nenhuma ordem pode sair sem MT5 de qualquer jeito. Se o MT5 ESTÁ
    disponível e a família genuinamente não validou, devolve um nome
    marcado que NUNCA existe no servidor (ver _UNRESOLVED_FAMILY_MARKER) —
    garante que quem revalidar este retorno (o preflight sempre revalida)
    veja "não resolvido" de verdade, em vez de por acaso aceitar um nome
    puro que existe numa família diferente da decidida."""
    if pair in _SYMBOL_RESOLUTION_CACHE:
        return _SYMBOL_RESOLUTION_CACHE[pair]

    family = MT5_SYMBOL_SUFFIX if MT5_SYMBOL_SUFFIX else _detect_mt5_symbol_family()
    mt5_ready = MT5_AVAILABLE and mt5 is not None
    if family is None:
        return pair if not mt5_ready else pair + _UNRESOLVED_FAMILY_MARKER
    candidate = pair + family

    if not mt5_ready:
        return candidate

    try:
        confirmed = mt5.symbol_info(candidate) is not None
    except Exception:
        confirmed = False
    # Só memoriza resolução CONFIRMADA contra o servidor. Cachear o palpite
    # feito antes do MT5 conectar congelaria um nome possivelmente errado pelo
    # resto da vida do processo.
    if confirmed:
        _SYMBOL_RESOLUTION_CACHE[pair] = candidate
    return candidate


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


# Achado P3-2, resíduo pego na verificação mfc-75 (herdr-review mfc-74):
# o print() original saiu de dentro de agents/confluence_engine.py (que
# gerava 8 linhas/noite), mas sem deduplicar aqui na FRONTEIRA, uma
# CSS_CONFLUENCE_ENGINE inválida persistente podia voltar a imprimir uma
# linha por atualização HTTP (o throttle de 3s em update_data() só se
# aplica quando já existe last_up/cached de uma atualização BOA anterior
# — cold start ou force=True continuam batendo aqui a cada chamada).
_last_warned_invalid_confluence_engine_value = None


def _warn_invalid_confluence_engine_once(exc):
    """Avisa só uma vez por valor inválido distinto — mesmo padrão já
    usado (e removido de dentro de agents/) na correção original do P3-2."""
    global _last_warned_invalid_confluence_engine_value
    raw = str(exc)
    if raw == _last_warned_invalid_confluence_engine_value:
        return
    _last_warned_invalid_confluence_engine_value = raw
    print(f"[!] {exc} — recusando gerar snapshot novo, servindo cache/fallback.")


# Achado MFC76-04 (herdr-review mfc-76, `mfc-rev`): sentinela de payload,
# NUNCA um valor válido de `engine=` pra evaluate_currency_confluence() —
# distingue "nenhum motor real calculou estes números" (dado 100%
# fabricado, sem qualquer conexão MT5 nesta chamada) de um motor de
# verdade tê-los produzido. Antes disso, _generate_fallback_data() dizia
# "3tf" (ou "5tf") mesmo quando os scores eram inteiramente inventados —
# um consumidor lendo o contrato de docs/API.md concluiria o oposto do
# que aconteceu.
CONFLUENCE_ENGINE_SIMULATED = "simulated"


def _fallback_confluence_engine_label():
    """Só pro payload 100% simulado de _generate_fallback_data() (nunca
    passa por evaluate_currency_confluence de verdade) — sempre
    CONFLUENCE_ENGINE_SIMULATED, nunca o motor configurado: os scores
    aqui não vieram de nenhum dos dois motores, então rotulá-los com
    "3tf"/"5tf" seria uma mentira de proveniência, mesmo que
    coincidentemente igual ao que CSS_CONFLUENCE_ENGINE diz. Não propaga
    o ValueError de resolve_confluence_engine() — informativo, não pode
    derrubar a última rede de segurança do serviço por causa de config
    inválida."""
    return CONFLUENCE_ENGINE_SIMULATED


def from_broker_symbol(symbol: str) -> str:
    """Nome no servidor da corretora -> nome lógico do par. Usado pra comparar
    posições abertas (que vêm com o nome da corretora) contra as listas
    internas de pares — em especial a checagem de colisão de símbolo em conta
    netting (agents/portfolio_executor.py), que compara nomes lógicos. Tenta
    remover o sufixo configurado primeiro, depois o auto-detectado (achado em
    revisão, Codex rodada 3: to_broker_symbol() já não tem "ordem de
    precedência" nenhuma — usa família única, sem fallback — então a
    referência antiga a essa ordem aqui ficou órfã; o comportamento em si
    continua correto, só a explicação estava desatualizada); sem isso, uma
    posição com sufixo só descoberto via auto-detecção nunca seria
    reconhecida na comparação (a idempotência em si é por MAGIC NUMBER, não
    por símbolo — essa função não afeta aquela checagem)."""
    for suf in (MT5_SYMBOL_SUFFIX, _AUTO_DETECTED_SUFFIX):
        if suf and symbol.endswith(suf):
            return symbol[: -len(suf)]
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

# O pipeline de decisão só aceita um ponto quando há uma interseção temporal
# comum mínima entre todos os 28 pares. Isso precisa ser igual ao aquecimento
# mínimo usado pelo backtest para não servir um snapshot parcial como live.
MIN_COMMON_HISTORY_BARS = 30
ATR_PERIOD = 100
STANDARD_ATR_OFFSET = 10


def required_full_history_bars(count, mode="standard"):
    """Retorna o histórico necessário para toda a série exibida ser válida.

    No modo padrão o ATR é lido em ``pos - 10``. Portanto, não basta ter a
    janela de 100 barras disponível na última observação: a primeira posição
    retornada também precisa ter ATR cheio. O modo gaussiano não aplica esse
    deslocamento, mas mantém a mesma regra geral com seu próprio aquecimento.
    """
    offset = STANDARD_ATR_OFFSET if mode != "gauss" else 0
    return int(count) + ATR_PERIOD + offset - 1


def calc_atr_sma(high, low, close, period=100, min_periods=1):
    tr = np.zeros(len(close))
    tr[0] = high[0] - low[0]
    for i in range(1, len(close)):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i-1]), abs(low[i] - close[i-1]))
    tr_series = pd.Series(tr)
    atr = tr_series.rolling(window=period, min_periods=min_periods).mean().values
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

def calculate_full_css(tf_val, count=120, mode="standard", return_quality=False):
    if not MT5_AVAILABLE:
        return (None, None, None, {"status": "unavailable"}) if return_quality else (None, None, None)

    required_history = required_full_history_bars(count, mode)
    quality = {
        "status": "clean",
        "requested_history_bars": int(count),
        "required_full_history_bars": required_history,
        "short_history_pairs": [],
        "common_history_bars": 0,
        "returned_history_bars": 0,
    }

    def _result(res, times, pair_slopes):
        if return_quality:
            return res, times, pair_slopes, quality
        return res, times, pair_slopes
        
    pair_dfs = {}
    for sym in ALL_28_PAIRS:
        # Consulta pelo nome da corretora (pode ter sufixo, ex.: EURUSDm),
        # mas indexa pelo nome lógico — todo o resto do sistema usa o lógico.
        rates = mt5.copy_rates_from_pos(to_broker_symbol(sym), tf_val, 0, count + 150)
        # A máscara mínima permite reconstruir o histórico, mas a qualidade
        # numérica só é "clean" quando o ATR(100) e o deslocamento de 10
        # posições têm janela cheia em TODAS as posições exibidas. O chamador
        # live recebe o marcador abaixo e cai no fallback/cache em vez de usar
        # um score truncado sem aviso.
        if rates is None or len(rates) < MIN_COMMON_HISTORY_BARS:
            continue
        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        df.set_index('time', inplace=True)
        pair_dfs[sym] = df
        
    if not pair_dfs:
        quality["status"] = "incomplete"
        return _result(None, None, None)

    # Um timeframe com 27/28 pares não é um snapshot válido: a média por
    # moeda e, portanto, o score normalizado mudariam silenciosamente. Exigir
    # a cobertura completa faz o chamador cair no cache/fallback controlado.
    missing_pairs = sorted(set(ALL_28_PAIRS) - set(pair_dfs))
    if missing_pairs:
        quality["status"] = "incomplete"
        quality["missing_pairs"] = missing_pairs
        return _result(None, None, None)
        
    common_index = None
    for sym, df in pair_dfs.items():
        if common_index is None:
            common_index = df.index
        else:
            common_index = common_index.intersection(df.index)
            
    if common_index is None or len(common_index) < MIN_COMMON_HISTORY_BARS:
        quality["status"] = "incomplete"
        quality["common_history_bars"] = len(common_index) if common_index is not None else 0
        return _result(None, None, None)

    quality["common_history_bars"] = len(common_index)
    common_index = common_index[-count:]
    quality["returned_history_bars"] = len(common_index)
    # ``count`` é a quantidade mínima declarada para o snapshot. A interseção
    # pode estar suficientemente aquecida para o ATR e ainda assim ter menos
    # barras que o consumidor solicitou; isso não é um snapshot completo e
    # deve permanecer utilizável apenas para diagnóstico exploratório.
    if len(common_index) < count:
        quality["status"] = "degraded"
    # A quantidade bruta de barras pode esconder um início desalinhado da
    # interseção comum. Medir a primeira posição efetivamente exibida fecha
    # tanto esse caso quanto a faixa cega ``120 <= len < count + 109`` do
    # modo padrão.
    for sym, df in pair_dfs.items():
        idx_map = {t: i for i, t in enumerate(df.index)}
        first_pos = idx_map.get(common_index[0]) if len(common_index) else None
        if first_pos is None or first_pos < required_history - count:
            quality["short_history_pairs"].append(sym)
    quality["short_history_pairs"] = sorted(quality["short_history_pairs"])
    if quality["short_history_pairs"]:
        quality["status"] = "degraded"

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
            atr_arr = calc_atr_sma(highs, lows, closes, ATR_PERIOD, min_periods=ATR_PERIOD)
            nwe_arr = calc_nwe_gaussian(closes, lookback=95, bandwidth=8.0)
            
            for t in common_index:
                pos = idx_map.get(t, -1)
                if pos <= 0:
                    slopes.append(0.0)
                    continue
                atr_value = atr_arr[pos] if pos < len(atr_arr) else np.nan
                atr = atr_value if np.isfinite(atr_value) and atr_value > 0 else 0.0
                if atr <= 0:
                    slopes.append(0.0)
                    continue
                nwe0 = nwe_arr[pos]
                nwe1 = nwe_arr[pos - 1] if pos > 0 else nwe0
                sl = (nwe0 - nwe1) / atr
                slopes.append(sl)
        else:
            # MODO PADRÃO: TMA / LWMA + ATR(100)/10
            atr_arr = calc_atr_sma(highs, lows, closes, ATR_PERIOD, min_periods=ATR_PERIOD)
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
                
                sl = (dblTma - dblPrev) / atr if np.isfinite(atr) and atr > 0 else 0.0
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
    return _result(css_res, time_strs, pair_slopes)


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

        # Achado MFC76-02 (herdr-review mfc-76): resolvido ANTES de
        # conectar ao MT5 e antes de qualquer calculate_full_css() (28
        # pares x 5 timeframes) — uma CSS_CONFLUENCE_ENGINE inválida não
        # deve custar uma rodada inteira de consultas MT5 só pra descobrir
        # isso depois. Resolvido UMA vez por snapshot (nunca recalculado
        # por moeda dentro do loop — achados MFC74-01/mfc-rev e a 3ª
        # releitura que o `mfc-rev-2` achou na consulta mfc-17) e passado
        # explícito pra evaluate_currency_confluence(), que agora NUNCA lê
        # os.environ (a leitura só existe aqui, na fronteira). Valor
        # AUSENTE cai no default (3tf); PRESENTE E INVÁLIDO recusa
        # (fail-closed, achado MFC74-02 — os dois revisores convergiram na
        # mfc-17: "usado ≠ escrito" da invariante 2 se aplica aqui mesmo
        # sem um motor objetivamente mais perigoso que o outro) — recusar
        # gerar um snapshot NOVO com um motor que ninguém pediu é mais
        # seguro que produzir um silenciosamente, mas um erro de config
        # não pode derrubar o processo inteiro (mesma regra de
        # agents/portfolio_executor.py pros seis tunáveis de execução):
        # serve o cache já existente, ou grava e serve um fallback
        # THROTTLED (achado MFC76-02, segunda metade: sem cachear o
        # fallback aqui, cada requisição HTTP repetiria a tentativa de
        # resolver — e, se algum dia a resolução voltasse a custar I/O,
        # repetiria isso também) — mesmo padrão já usado abaixo pra MT5
        # desconectado.
        try:
            active_confluence_engine = resolve_confluence_engine()
        except ValueError as exc:
            _warn_invalid_confluence_engine_once(exc)
            if cached:
                return _stamp_provenance(cached, False)
            res = _stamp_provenance(self._generate_fallback_data(mode=mode), False)
            if mode == "gauss":
                self.cache_gauss = res
                self.last_update_gauss = now_ts
            else:
                self.cache_standard = res
                self.last_update_standard = now_ts
            return res

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
                else:
                    self.cache_standard = res
                    self.last_update_standard = now_ts
                # Fallback é uma resposta de sessão, não um snapshot válido
                # para persistir. Nunca sobrescrever o último snapshot
                # durável com dados simulados/incompletos.
                return res
            # Cache em memória servido com a conexão CAÍDA agora: seja qual for
            # a origem dele, não é dado live neste instante.
            return _stamp_provenance(cached, False)

        tf_data_raw = {}
        tf_charts = {}
        tf_pair_charts = {}
        snapshot_quality = {
            "status": "clean",
            "required_full_history_bars": {
                tf_name: required_full_history_bars(count, mode)
                for tf_name, count in TIMEFRAMES_CONFIG
            },
            "timeframes": {},
        }
        for tf_name, count in TIMEFRAMES_CONFIG:
            tf_val = get_tf_constant(tf_name)
            calculated = calculate_full_css(
                tf_val, count, mode=mode, return_quality=True
            )
            if len(calculated) == 4:
                res, times, pair_slopes, quality = calculated
            else:  # compatibilidade com adaptadores de teste/integrações antigas
                res, times, pair_slopes = calculated
                quality = {"status": "clean"}
            if quality.get("status") != "clean":
                snapshot_quality["status"] = "degraded"
                snapshot_quality["timeframes"][tf_name] = quality
                continue
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

        required_timeframes = {tf_name for tf_name, _ in TIMEFRAMES_CONFIG}
        if set(tf_data_raw) != required_timeframes:
            # Conectou, mas o snapshot está incompleto — não misturar TFs de
            # momentos diferentes nem indexar um timeframe ausente. O mesmo
            # caminho controlado de cache/fallback vale para zero ou alguns
            # timeframes indisponíveis.
            missing_timeframes = sorted(required_timeframes - set(tf_data_raw))
            self.last_error = (
                "Snapshot CSS incompleto; timeframes ausentes: "
                + ", ".join(missing_timeframes)
            )
            snapshot_quality["status"] = "incomplete"
            snapshot_quality["missing_timeframes"] = missing_timeframes
            if not cached:
                result = _stamp_provenance(
                    self._generate_fallback_data(mode=mode), False
                )
                result["snapshot_quality"] = snapshot_quality
                if mode == "gauss":
                    self.cache_gauss = result
                    self.last_update_gauss = now_ts
                else:
                    self.cache_standard = result
                    self.last_update_standard = now_ts
                # Um snapshot incompleto não pode virar a fonte durável do
                # próximo processo; mantê-lo apenas no cache desta sessão
                # força a recuperação do snapshot anterior ou novo fallback.
                return result

            # Mesmo um cache antigo precisa atualizar o relógio: esta
            # tentativa já produziu uma resposta segura e, sem isso, cada
            # requisição ignorando o throttle repetiria cinco consultas MT5.
            result = _stamp_provenance(cached, False)
            result["snapshot_quality"] = snapshot_quality
            if mode == "gauss":
                self.cache_gauss = result
                self.last_update_gauss = now_ts
            else:
                self.cache_standard = result
                self.last_update_standard = now_ts
            return result

        # Confluence and Triad per currency
        ccy_confluence_results = {}
        currency_cards = []
        # Captura única por snapshot: MN1/W1 precisam usar a mesma maturação
        # para todas as moedas, e o motor recebe o instante explicitamente.
        reference_dt = datetime.now(BRT)
        for c in CURRENCIES:
            mn_s = tf_data_raw["MN1"][0][c]
            w1_s = tf_data_raw["W1"][0][c]
            d1_s = tf_data_raw["D1"][0][c]
            h4_s = tf_data_raw["H4"][0][c]
            h1_s = tf_data_raw["H1"][0][c]
            
            conf = evaluate_currency_confluence(
                c, mn_s, w1_s, d1_s, h4_s, h1_s, ref_dt=reference_dt, engine=active_confluence_engine
            )
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
            # Achado MFC74-03 (herdr-review mfc-74): total_score muda de
            # escala/semântica conforme este valor ("3tf" = score bruto
            # D1*0.40+H4*0.35+H1*0.25; "5tf" = normalizado -10..+10) — ver
            # docs/API.md.
            "confluence_engine": active_confluence_engine,
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
            # Dado 100% simulado (sem MT5) — trade_bias/scores aqui nunca
            # passaram por evaluate_currency_confluence(), então um valor
            # inválido de CSS_CONFLUENCE_ENGINE não deve derrubar nem esta
            # última rede de segurança; cai no default só informativamente.
            "confluence_engine": _fallback_confluence_engine_label(),
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
