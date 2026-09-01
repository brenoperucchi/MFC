"""
Lançador de acompanhamento de backtest via web — roda
scripts/backtest_engine_compare.py::compare() num processo SEPARADO do
web/server.py, com ambiente construído do zero (nunca herdado), contra o
terminal MT5 ISOLADO (mfc-backtest), sempre sample_role="exploratory" numa
janela FIXA. Ver docs/plans/eventual-stargazing-bear.md pro desenho completo
e o porquê de cada decisão (consulta herdr-ask mfc-13).

Achado central da consulta: a ÚNICA coisa que impede este backtest de rodar
contra o terminal AO VIVO é ser um processo separado — `ensure_mt5()`
(agents/portfolio_executor.py) reaproveita sem checagem qualquer conexão MT5
que o CHAMADOR já tenha aberta, então `CSS_MT5_TERMINAL_PATH` isolado só
protege porque este módulo nasce sem nenhum binding MT5 prévio. Nunca virar
chamada in-process/thread a partir de web/server.py (que mantém conexão
aberta com o terminal AO VIVO em produção) — isso quebraria essa garantia
silenciosamente.

Dois jeitos de usar:
  - Importado por web/server.py: spawn_isolated_backtest() dispara este
    mesmo arquivo via `python -m scripts.run_isolated_backtest ...` num
    subprocess.Popen com build_isolated_env().
  - Executado diretamente (`python scripts/run_isolated_backtest.py
    --description "smoke test"`) pra smoke test manual — usa o ambiente já
    presente no shell que chamou, não build_isolated_env() (ver Verificação
    no plano, item 4: o caminho os.name=="posix" fica sem verificação
    automatizada).
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

ISOLATED_TERMINAL_PATH = r"D:\MetaTradersWSL\mfc-backtest\terminal64.exe"

# Janela fixa, nunca relativa a "agora" — mesma usada nas rodadas de
# reprodutibilidade já registradas no plano (mfc-32/34/35), começando
# exatamente onde o holdout OOS atual termina (development_start_brt), logo
# estruturalmente disjunta do holdout sem lógica extra de verificação.
REGRESSION_WINDOW_DAYS = 45
REGRESSION_WINDOW_END_BRT = "2026-08-30T21:00:00-03:00"

DEFAULT_RUNS = 2
MIN_RUNS = 1
MAX_RUNS = 5
MIN_DESCRIPTION_LEN = 3
MAX_DESCRIPTION_LEN = 500

TRIGGER_STATE_DIR = os.path.join(BASE_DIR, "data", "backtest_trigger")
STATUS_PATH = os.path.join(TRIGGER_STATE_DIR, "status.json")
OWNER_PID_PATH = os.path.join(TRIGGER_STATE_DIR, "owner.pid")

_NOTE_MARKER_PREFIX = "[web-trigger:"
_TRIGGER_LOCK_KEY = "mfc-backtest-trigger-lock"

BRT = timezone(timedelta(hours=-3))

# Whitelist de variáveis estruturais do SO que um processo Python no Windows
# precisa pra subir — nunca os.environ.copy() (ver build_isolated_env).
_SO_ENV_WHITELIST = (
    "SystemRoot", "PATH", "TEMP", "TMP", "USERPROFILE", "windir", "COMSPEC", "PATHEXT",
)


def _trigger_lock_path():
    from scripts._backtest_results_log import _lock_path
    return _lock_path(key=_TRIGGER_LOCK_KEY)


def build_isolated_env(base_dir):
    """Ambiente do subprocesso construído do zero — nunca os.environ.copy().

    Copia individualmente só o whitelist estrutural do SO, mais as variáveis
    de domínio setadas explicitamente. CSS_MT5_TERMINAL_PATH nunca é lido do
    processo pai — essa é a garantia que a variável dá; a garantia de fundo
    é ser um processo separado (ver docstring do módulo)."""
    env = {}
    for key in _SO_ENV_WHITELIST:
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    env["CSS_MT5_TERMINAL_PATH"] = ISOLATED_TERMINAL_PATH
    env["MFC_BACKTEST_TERMINAL_ISOLATED"] = "1"
    env["MFC_BACKTEST_WEB_TRIGGER"] = "1"
    env["PYTHONPATH"] = base_dir
    env["PYTHONUNBUFFERED"] = "1"
    # Sem isto, o stdout do filho no Windows usa o codepage ativo do console
    # (não UTF-8) — qualquer acento vira mojibake no log/log_tail, já que o
    # arquivo é lido de volta assumindo UTF-8 (achado do smoke test real,
    # 2026-09-01: "s�ries can�nicas" em vez de "séries canônicas").
    env["PYTHONIOENCODING"] = "utf-8"
    if os.name == "posix":
        # Caminho de dev local (servidor rodando via WSL, não o py.exe
        # nativo de produção) — WSLENV não propaga variável nenhuma pro lado
        # Windows por padrão, precisa ser listada explicitamente (mesmo
        # padrão documentado em CLAUDE.md). Não verificado automaticamente;
        # ver Verificação item 4 no plano.
        env["WSLENV"] = ":".join([
            "CSS_MT5_TERMINAL_PATH", "PYTHONPATH",
            "MFC_BACKTEST_TERMINAL_ISOLATED", "MFC_BACKTEST_WEB_TRIGGER",
        ])
    return env


def _python_command():
    """(python_bin, args_extras) do subprocesso, dependendo de onde ESTE
    processo pai está rodando. Em produção, web/server.py já roda como
    processo nativo do Windows (scripts/systemd/web-server-wsl.sh faz `exec`
    do py.exe do Windows via interop WSL — o exec substitui o processo) —
    um Popen disparado de dentro dele é spawn Windows->Windows normal, sem
    precisar de WSLENV (que só importa na travessia WSL->Windows, já feita
    uma vez no boot do próprio servidor)."""
    if os.name == "nt":
        return sys.executable, []
    return "/mnt/c/WINDOWS/py.exe", ["-3.12"]


def _write_status(payload):
    os.makedirs(TRIGGER_STATE_DIR, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", dir=TRIGGER_STATE_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, STATUS_PATH)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def read_status():
    """Estado estrutural (status/pid/run_id/...) pra UI — NÃO é a
    autoridade de "está rodando": is_trigger_running() (lock não-bloqueante)
    é quem decide isso de fato. Este arquivo pode ficar defasado (ex.: o
    servidor reiniciou antes de o filho escrever "done")."""
    try:
        with open(STATUS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"status": "idle"}


def _log_path_for(run_id):
    """Um arquivo de log POR run_id — nunca um único LOG_PATH global (achado
    P2, herdr-review mfc-65/66, `mfc-rev`): dois disparos quase simultâneos
    cada um abrindo o MESMO arquivo em modo "w" truncaria/corromperia o log
    um do outro, mesmo que só um deles de fato conquiste o lock dedicado."""
    return os.path.join(TRIGGER_STATE_DIR, f"run-{run_id}.log")


def read_log_tail(max_chars=4000, run_id=None):
    """Lê o log da execução `run_id` (default: a do `status.json` atual)."""
    if run_id is None:
        run_id = read_status().get("run_id")
    if not run_id:
        return ""
    try:
        with open(_log_path_for(run_id), "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except FileNotFoundError:
        return ""
    return content[-max_chars:]


def is_trigger_running():
    """Sonda NÃO-BLOQUEANTE: True se outro processo já segura o lock
    dedicado do trigger. Autoridade real de concorrência — sobrevive a
    restart do servidor e a `kill -9` do filho, sem constante de tempo
    mágica (diferente de um status.json com validade heurística).

    NUNCA usar os.kill(pid, 0) pra isto — no Windows, CPython chama
    TerminateProcess, matando o processo em vez de só verificar (achado da
    consulta herdr-ask mfc-13)."""
    try:
        import fcntl
    except ImportError:
        fcntl = None
    try:
        import msvcrt
    except ImportError:
        msvcrt = None

    lock_fd = os.open(_trigger_lock_path(), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if fcntl is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            return False
        if msvcrt is not None:
            os.lseek(lock_fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(lock_fd, msvcrt.LK_NBLCK, 1)
            except OSError:
                return True
            os.lseek(lock_fd, 0, os.SEEK_SET)
            msvcrt.locking(lock_fd, msvcrt.LK_UNLCK, 1)
            return False
        return False
    finally:
        os.close(lock_fd)


def _write_owner_pid():
    """Grava o PID de quem ACABOU de conquistar o lock dedicado — chamado
    de dentro da seção crítica do lock, pelo próprio dono (nunca pelo pai
    via Popen). É a fonte de verdade de "quem está rodando de fato" pro
    watchdog e pro endpoint de status — nunca `status.json["pid"]`, que é
    só o PID que o PAI viu ao criar o Popen e pode já ter sido sobrescrito
    por um segundo disparo que só ficou na FILA do lock (achado
    P2/P2-2, herdr-review mfc-65)."""
    os.makedirs(TRIGGER_STATE_DIR, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", dir=TRIGGER_STATE_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        os.replace(tmp_path, OWNER_PID_PATH)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _remove_owner_pid():
    """Apaga owner.pid quando o dono libera o lock — chamado de dentro da
    própria seção crítica, como a ÚLTIMA ação antes de sair do `with`
    (achado P3-2, herdr-review mfc-66, `mfc-rev-2`): sem isto, o arquivo
    fica com o PID do dono ANTERIOR entre uma execução e a seguinte; a
    janela em que current_running_owner_pid() poderia ler esse valor obsoleto
    (lock recém-conquistado, owner.pid ainda não reescrito) é sub-milissegundo
    e nunca observada em prática nesta sessão, mas apagar transforma essa
    janela em "ninguém rodando" (inofensivo) em vez de "PID errado"
    (potencialmente perigoso, no mesmo host que roda produção ao vivo)."""
    try:
        os.remove(OWNER_PID_PATH)
    except FileNotFoundError:
        pass


def _read_owner_pid():
    try:
        with open(OWNER_PID_PATH, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return None


def current_running_owner_pid():
    """Melhor estimativa de quem segura o lock dedicado AGORA, ou None se
    ninguém segura — combina a sonda não-bloqueante com o pid gravado por
    dentro da seção crítica (ver _write_owner_pid/_remove_owner_pid).

    NÃO é uma observação atômica (achado P1, herdr-review mfc-66,
    `mfc-rev`): há uma leitura do lock seguida de uma leitura separada do
    arquivo, sem seção crítica única cobrindo as duas — em teoria um dono
    pode soltar o lock e outro assumir entre as duas leituras. Avaliado
    (herdr-review mfc-66, `mfc-rev-2`) como praticamente inalcançável dado
    o tamanho real da janela (sub-milissegundo) e o intervalo do watchdog
    (30s) — mas é uma garantia best-effort, não uma identidade de processo
    verificada (sem token/handle de criação por SO). Fonte única, ainda
    assim, usada pelo watchdog de janela crítica e pelo endpoint de status
    pra reconciliar um status.json defasado — é estritamente melhor que
    confiar cegamente em status.json["pid"] (que pode ser de um segundo
    disparo só enfileirado), não uma garantia perfeita."""
    if not is_trigger_running():
        return None
    return _read_owner_pid()


def in_critical_window(now_brt=None):
    """20:55-22:00 e 07:55-08:20 BRT (achado 3, consulta herdr-ask mfc-13):
    margens largas o bastante pra cobrir a janela real de abertura tolerante
    (scripts/scheduler_daemon.py mantém a abertura real até 21:59, não
    21:05) — usada tanto pra recusar um disparo novo quanto pelo watchdog
    que termina uma execução em andamento (ver web/server.py)."""
    now = now_brt or datetime.now(BRT)
    minutes = now.hour * 60 + now.minute
    evening = (20 * 60 + 55, 22 * 60)
    morning = (7 * 60 + 55, 8 * 60 + 20)
    return evening[0] <= minutes < evening[1] or morning[0] <= minutes < morning[1]


def market_is_open(now_utc=None):
    """Aproximação SEM dependência de tzdata (zoneinfo com fusos IANA como
    'America/New_York' pede o pacote tzdata no Windows, que este projeto não
    instala por padrão — ver CLAUDE.md, seção Commands). Não é o dado real
    de sessão de is_market_session_valid() (scripts/backtest_canonical.py,
    que exige H1 já carregado) — é um filtro barato no INSTANTE do disparo,
    antes de qualquer carga de dado, propositalmente CONSERVADOR: usa
    [sexta 21:00 UTC, domingo 22:00 UTC) como fechado, um superconjunto do
    fechamento real em qualquer lado do horário de verão dos EUA (nunca um
    subconjunto — prefere recusar minutos extra de dia útil a deixar passar
    minutos de fim de semana de verdade).

    Achado 5 da consulta (mfc-rev-2, medido): custo/líquido variam ~2x
    dependendo de o mercado estar aberto no instante da medição — esta
    checagem existe pra que o disparo web nunca produza uma medição de custo
    sem sentido.

    Limitação conhecida (achado P3-3, herdr-review mfc-65, `mfc-rev-2`): não
    cobre feriado em dia útil (25/12, 01/01, Sexta-feira Santa etc.) — um
    disparo nesses dias é aceito e `market_open_at_run` fica `True` mesmo
    com o mercado de fato fechado. Impacto limitado a um ponto ruim na
    tabela de acompanhamento (não a uma decisão de execução); sem calendário
    de feriados implementado ainda."""
    now = now_utc or datetime.now(timezone.utc)
    weekday = now.weekday()  # Monday=0 ... Sunday=6
    minutes = now.hour * 60 + now.minute
    if weekday == 5:  # sábado inteiro
        return False
    if weekday == 4 and minutes >= 21 * 60:  # sexta a partir de 21:00 UTC
        return False
    if weekday == 6 and minutes < 22 * 60:  # domingo antes de 22:00 UTC
        return False
    return True


def spawn_isolated_backtest(description, runs, base_dir=BASE_DIR):
    """Dispara uma execução de acompanhamento em processo separado, env
    isolado construído do zero. Devolve (Popen, run_id). O PRÓPRIO filho
    segura o lock exclusivo dedicado durante toda a vida (ver
    _run_and_record) — não é responsabilidade de quem chama isto; a checagem
    de "já tem um rodando" é is_trigger_running(), não o Popen devolvido
    aqui (que não sobrevive a um restart do servidor)."""
    os.makedirs(TRIGGER_STATE_DIR, exist_ok=True)
    run_id = uuid.uuid4().hex[:12]
    python_bin, base_args = _python_command()
    argv = [
        python_bin, *base_args, "-m", "scripts.run_isolated_backtest",
        "--description", description,
        "--runs", str(runs),
        "--run-id", run_id,
    ]
    log_fh = open(_log_path_for(run_id), "w", encoding="utf-8")
    try:
        process = subprocess.Popen(
            argv,
            cwd=base_dir,
            env=build_isolated_env(base_dir),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
        )
    finally:
        log_fh.close()
    _write_status({
        "status": "running",
        "pid": process.pid,
        "run_id": run_id,
        "description": description,
        "runs": runs,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
    })
    return process, run_id


def terminate_owner(pid, timeout=10):
    """Envia SIGTERM (depois SIGKILL se necessário) pro PID recebido — usado
    pelo watchdog de janela crítica em web/server.py, sempre com `pid` vindo
    de current_running_owner_pid() (nunca de status.json bruto). NÃO revalida
    a identidade do processo antes de sinalizar (achado P1, herdr-review
    mfc-66, `mfc-rev`) — confia no `pid` recebido; a garantia de que ele é
    o dono certo vem inteira de current_running_owner_pid(), não desta
    função. O impacto de terminar o caminho de backtest é sempre contido: o
    caminho nunca envia ordem, e append_result() só escreve no fim, de
    forma atômica, então matar no meio no máximo perde uma execução de
    diagnóstico, nunca corrompe o journal.

    Sem percurso de árvore de processos: o caminho de backtest nunca
    SPAWNA um filho próprio — `mt5.initialize()` se CONECTA a um terminal
    MT5 já rodando via IPC (ver CLAUDE.md, seção da instância isolada
    dedicada), não o inicia. Terminar este único processo Python já é
    suficiente; o terminal MT5 continua de pé, só perde este cliente.

    Espera `timeout` segundos checando is_trigger_running() (nunca
    os.kill(pid, 0), que mata no Windows) antes de escalar pra um kill
    forçado se o processo ainda não soltou o lock."""
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_trigger_running():
            return
        time.sleep(0.5)
    try:
        os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    except OSError:
        pass


def _find_journal_entry(run_id):
    """Identifica a entrada alocada por ESTA execução, por conteúdo
    (marcador único de run_id no note) — não por "maior seq antes/depois",
    que colidiria com um append CLI independente e concorrente (achado da
    consulta herdr-ask mfc-13, mfc-rev). Devolve a entrada completa (ou
    None) — _find_journal_seq() abaixo é só o atalho pro caso comum de só
    precisar do número."""
    from scripts._backtest_results_log import RESULTS_LOG_PATH
    try:
        with open(RESULTS_LOG_PATH, "r", encoding="utf-8") as f:
            log = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    marker = f"{_NOTE_MARKER_PREFIX}{run_id}]"
    for entry in log:
        note = entry.get("note") if isinstance(entry, dict) else None
        if isinstance(note, str) and note.startswith(marker):
            return entry
    return None


def _find_journal_seq(run_id):
    entry = _find_journal_entry(run_id)
    return entry.get("journal_seq") if isinstance(entry, dict) else None


# Perfil dedicado no llm-gateway (repo separado, ~/Devs/llm-gateway) —
# system_prompt/schema/modelo/timeout são definidos LÁ (profiles.py), não
# aqui; este módulo só manda o resumo estruturado dos dados, nunca
# instrução nenhuma (achado do Breno registrando o perfil: "não repita
# essas instruções no text do MFC... se o critério mudar, muda no
# gateway"). Alcançado via túnel SSH local dedicado (systemd --user
# mfc-llm-gateway-tunnel.service, ssh -L da Ryzen9 pro Omarchy) — a porta
# LOCAL é 18080, não 8080 (que já pertence a outro sistema de trading ao
# vivo nesta mesma máquina, pairtrading-server.service).
LLM_GATEWAY_URL = os.environ.get("CSS_LLM_GATEWAY_URL", "http://127.0.0.1:18080")
LLM_ANALYSIS_PROFILE = "backtest-analysis"
# >= timeout_seconds do próprio perfil no gateway (180s, medido: ~2s com
# modelo carregado, ~28s frio — o Ryzen9 roda OLLAMA_MAX_LOADED_MODELS=1,
# então alternar de cliente recarrega). Cliente nunca pode desistir antes
# do gateway.
LLM_ANALYSIS_TIMEOUT_SEC = 190.0


def _build_llm_analysis_text(entry, market_open):
    """Resumo estruturado em texto simples da entrada — nunca o registro
    bruto inteiro (sem digest de proveniência, sem caminho de terminal) e
    nunca instrução nenhuma pro modelo (isso mora no system_prompt do
    perfil, no gateway)."""
    window = entry.get("window") if isinstance(entry.get("window"), dict) else {}
    engines = entry.get("engines") if isinstance(entry.get("engines"), dict) else {}
    paired = entry.get("paired_net_delta_per_night") if isinstance(entry.get("paired_net_delta_per_night"), dict) else {}

    lines = [
        f"Janela: {window.get('days')} dias, {window.get('start_brt')} a "
        f"{window.get('end_brt')} BRT, {window.get('nights_evaluated')} noites avaliadas.",
        f"Mercado no instante da medição: {'aberto' if market_open else 'fechado'}.",
        f"Motores comparados: {', '.join(engines.keys()) or '-'}.",
        "",
    ]
    for name, metrics in engines.items():
        if not isinstance(metrics, dict):
            continue
        lines.append(
            f"{name}: cestas={metrics.get('baskets')} bruto={metrics.get('bruto')} "
            f"custo={metrics.get('custo')} liquido={metrics.get('liquido')} "
            f"noite%={metrics.get('noite_pct')} cesta%={metrics.get('cesta_pct')} "
            f"qualidade={metrics.get('quality_status')}"
        )
    if paired.get("mean") is not None:
        lines.append("")
        lines.append(
            f"Delta pareado por noite: media={paired.get('mean')} "
            f"erro_padrao={paired.get('stderr')} n={paired.get('n')}"
        )
    return "\n".join(lines)


def _call_backtest_analysis(entry, market_open):
    """Chama o perfil backtest-analysis do llm-gateway pra uma leitura
    padronizada do resultado. NUNCA lança: um backtest bem-sucedido não
    pode falhar por causa de uma anotação opcional (gateway fora do ar,
    túnel caído, timeout, resposta fora do schema — tudo vira None em vez
    de exceção). Devolve o dict já validado contra o schema do perfil, ou
    None em qualquer falha."""
    try:
        import httpx
        response = httpx.post(
            f"{LLM_GATEWAY_URL}/v1/tasks/{LLM_ANALYSIS_PROFILE}",
            json={
                "project": "mfc",
                "text": _build_llm_analysis_text(entry, market_open),
            },
            timeout=LLM_ANALYSIS_TIMEOUT_SEC,
        )
        response.raise_for_status()
        result = response.json().get("result")
        return result if isinstance(result, dict) else None
    except Exception:
        return None


def _run_and_record(description, runs, run_id):
    """Corpo real da execução — chamado pelo processo filho, seja disparado
    pela web (spawn_isolated_backtest(), com build_isolated_env() já
    aplicado ao seu ambiente) ou invocado diretamente por um humano
    (`python scripts/run_isolated_backtest.py ...`, smoke test manual).

    Força MFC_BACKTEST_TERMINAL_ISOLATED=1 e MFC_BACKTEST_WEB_TRIGGER=1 no
    PRÓPRIO ambiente deste processo, incondicionalmente — nunca confia que
    quem chamou já os setou (achado P1, herdr-review mfc-65, `mfc-rev`): a
    entrada manual via __main__ não passava por build_isolated_env()
    nenhum, então um operador que esquecesse de exportar essas variáveis
    (ou rodasse num shell com CSS_MT5_TERMINAL_PATH ainda apontando pro
    terminal AO VIVO) faria compare() pular a asserção de isolamento
    inteira, silenciosamente. Setar aqui não muda CSS_MT5_TERMINAL_PATH em
    si — só GARANTE que a checagem de compare() (que compara o caminho
    configurado E o observado após conectar contra a instância
    mfc-backtest) sempre roda, recusando fechado se o terminal configurado
    não for o isolado, em vez de depender do ambiente de quem chamou.

    Segura o lock dedicado do trigger durante toda a vida, com o PID do
    dono gravado por dentro da seção crítica (_write_owner_pid, removido de
    novo em _remove_owner_pid ao sair) e as transições de status TERMINAIS
    ("done"/"failed"/"skipped") também dentro do `with` — nunca depois de
    liberar o lock, senão um segundo disparo enfileirado pode escrever seu
    próprio "running" por cima antes desta execução conseguir gravar seu
    "done" (achado P2/P2-1, herdr-review mfc-65). sample_role="exploratory"
    e a janela fixa ficam HARDCODED aqui (nunca vindas de fora) — nem o
    endpoint nem este módulo aceitam sample_role/janela como parâmetro
    externo, então oos_disjoint é estruturalmente impossível por este
    caminho, além do veto redundante em compare() via
    MFC_BACKTEST_WEB_TRIGGER=1.

    Reavalia in_critical_window()/market_is_open() logo depois de conquistar
    o lock, saindo com status "skipped" (sem chamar compare()) se qualquer
    um tiver virado — o endpoint já checou os dois no INSTANTE do disparo,
    mas um segundo disparo pode ficar enfileirado atrás do primeiro por
    minutos (achado P3-3, herdr-review mfc-66, `mfc-rev-2`): sem isto, o
    enfileirado rodaria de qualquer forma quando finalmente conquistasse o
    lock, produzindo exatamente a medição de custo sem sentido que os
    portões existem pra impedir."""
    os.environ["MFC_BACKTEST_TERMINAL_ISOLATED"] = "1"
    os.environ["MFC_BACKTEST_WEB_TRIGGER"] = "1"

    from scripts._backtest_results_log import _exclusive_lock
    from scripts.backtest_engine_compare import compare

    _write_status({
        "status": "running",
        "pid": os.getpid(),
        "run_id": run_id,
        "description": description,
        "runs": runs,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
    })
    end_brt_dt = datetime.fromisoformat(REGRESSION_WINDOW_END_BRT)
    note = f"{_NOTE_MARKER_PREFIX}{run_id}] {description}"

    with _exclusive_lock(_trigger_lock_path()):
        _write_owner_pid()
        try:
            for gate_name, gate_failed in (
                ("janela crítica de abertura/fechamento", in_critical_window()),
                ("mercado fechado", not market_is_open()),
            ):
                if gate_failed:
                    _write_status({
                        "status": "skipped",
                        "pid": os.getpid(),
                        "run_id": run_id,
                        "description": description,
                        "runs": runs,
                        "reason": f"{gate_name} — condição mudou enquanto este "
                                  "disparo esperava o lock dedicado",
                        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
                    })
                    return 0
            try:
                ret = compare(
                    days=REGRESSION_WINDOW_DAYS,
                    runs=runs,
                    log_note=note,
                    end_brt=end_brt_dt,
                    sample_role="exploratory",
                )
            except Exception as exc:
                _write_status({
                    "status": "failed",
                    "pid": os.getpid(),
                    "run_id": run_id,
                    "description": description,
                    "runs": runs,
                    "error": str(exc),
                    "finished_at_utc": datetime.now(timezone.utc).isoformat(),
                })
                raise
            if ret != 0:
                _write_status({
                    "status": "failed",
                    "pid": os.getpid(),
                    "run_id": run_id,
                    "description": description,
                    "runs": runs,
                    "returncode": ret,
                    "finished_at_utc": datetime.now(timezone.utc).isoformat(),
                })
                return ret
            entry = _find_journal_entry(run_id)
            new_journal_seq = entry.get("journal_seq") if isinstance(entry, dict) else None
            _write_status({
                "status": "done",
                "pid": os.getpid(),
                "run_id": run_id,
                "description": description,
                "runs": runs,
                "new_journal_seq": new_journal_seq,
                "finished_at_utc": datetime.now(timezone.utc).isoformat(),
            })
        finally:
            _remove_owner_pid()

    # Fora do lock dedicado: a análise por LLM (até ~190s no pior caso,
    # modelo frio no Ryzen9) nunca toca o terminal isolado, então não
    # precisa da exclusividade — segurar o lock aqui só estenderia sem
    # necessidade a janela em que outro disparo fica bloqueado esperando.
    # Melhor esforço puro: "done" já foi gravado acima, uma falha aqui
    # (gateway fora do ar, túnel caído, resposta fora do schema) nunca
    # muda isso — ver docstring de _call_backtest_analysis.
    if entry is not None and new_journal_seq is not None:
        analysis = _call_backtest_analysis(entry, market_is_open())
        if analysis is not None:
            try:
                from scripts._backtest_results_log import attach_llm_analysis
                attach_llm_analysis(new_journal_seq, analysis)
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Roda um backtest de acompanhamento (exploratory, janela fixa) "
                     "contra o terminal MT5 isolado. Ver docs/plans/eventual-stargazing-bear.md."
    )
    parser.add_argument("--description", required=True)
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--run-id", default=None, help="Auto-gerado se omitido (smoke test manual).")
    cli_args = parser.parse_args()

    if not (MIN_DESCRIPTION_LEN <= len(cli_args.description) <= MAX_DESCRIPTION_LEN):
        print(f"[-] description deve ter entre {MIN_DESCRIPTION_LEN} e {MAX_DESCRIPTION_LEN} caracteres.")
        sys.exit(1)
    if not (MIN_RUNS <= cli_args.runs <= MAX_RUNS):
        print(f"[-] runs deve estar entre {MIN_RUNS} e {MAX_RUNS}.")
        sys.exit(1)

    sys.exit(_run_and_record(
        cli_args.description,
        cli_args.runs,
        cli_args.run_id or uuid.uuid4().hex[:12],
    ))
