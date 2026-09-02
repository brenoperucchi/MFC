"""
SERVIDOR FASTAPI — PLATAFORMA WEB CSS (LOCAL HOST : 8050)
Fornece API REST de alta performance e serve a aplicação web frontend SPA.
"""

import os
import sys
import asyncio
import hmac
import json
import base64
import threading
import time
from datetime import datetime
import webbrowser
import uvicorn
from pydantic import BaseModel, ConfigDict, Field
from fastapi import FastAPI, HTTPException, Header
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# Garantir que a pasta MFC esteja no sys.path
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from web.css_service import css_engine
from web.history_tracker import history_engine
from scripts import run_isolated_backtest
from scripts._backtest_results_log import RESULTS_LOG_PATH

app = FastAPI(
    title="CSS Institutional Multi-Timeframe Platform",
    description="Plataforma Profissional de Análise Cíclica CSS & Confluência Multi-Timeframe",
    version="2.0.0"
)

# Habilitar CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(STATIC_DIR, exist_ok=True)


@app.get("/api/status")
async def get_status():
    """Retorna o status da conexão com MT5 e timestamp da última atualização."""
    data = css_engine.update_data(force=False)
    return {
        "status": "online",
        "mt5_connected": data.get("mt5_connected", False),
        "last_update": data.get("timestamp"),
        "error": css_engine.last_error
    }


@app.get("/api/css/all")
async def get_css_all(mode: str = "standard"):
    """Retorna todos os dados de moedas, gráficos, tríades e screener dos 28 pares (Modo Padrão TMA ou Modo Gauss NWE)."""
    data = css_engine.update_data(force=False, mode=mode)
    return JSONResponse(content=data)


@app.get("/api/css/chart/{tf}")
async def get_chart_by_tf(tf: str, mode: str = "standard"):
    """Retorna séries temporais específicas para um timeframe (MN1, W1, D1, H4, H1)."""
    tf_upper = tf.upper()
    data = css_engine.update_data(force=False, mode=mode)
    charts = data.get("charts", {})
    if tf_upper not in charts:
        raise HTTPException(status_code=404, detail=f"Timeframe '{tf_upper}' não encontrado.")
    return {
        "tf": tf_upper,
        "mode": mode,
        "times": charts[tf_upper]["times"],
        "series": charts[tf_upper]["series"],
        "colors": data.get("colors", {}),
        "flags": data.get("flags", {})
    }


@app.get("/api/pairs")
async def get_pairs(mode: str = "standard"):
    """Retorna o ranking e diagnóstico dos 28 pares Forex."""
    data = css_engine.update_data(force=False, mode=mode)
    return {"pairs": data.get("pairs", []), "mode": mode}


@app.get("/api/crossovers")
async def get_crossovers(mode: str = "standard"):
    """Retorna os cruzamentos de scores detectados entre as moedas dos 28 pares Forex."""
    data = css_engine.update_data(force=False, mode=mode)
    return JSONResponse(content=data.get("crossovers", {}))


@app.get("/api/crossovers/{tf}")
async def get_crossovers_by_tf(tf: str, mode: str = "standard"):
    """Retorna cruzamentos específicos para um timeframe (H1, H4, D1, etc.)."""
    tf_upper = tf.upper()
    data = css_engine.update_data(force=False, mode=mode)
    crossovers = data.get("crossovers", {}).get("timeframes", {})
    if tf_upper not in crossovers:
        raise HTTPException(status_code=404, detail=f"Timeframe '{tf_upper}' não encontrado nos cruzamentos.")
    return JSONResponse(content=crossovers[tf_upper])


@app.get("/api/history/dates")
async def get_history_dates():
    """Retorna as datas de análises diárias disponíveis no arquivo."""
    dates = css_engine.get_history_dates()
    return {"dates": dates}


@app.get("/api/history/{date_str}")
async def get_history_report(date_str: str):
    """Retorna o conteúdo do relatório diário de uma data específica."""
    content = css_engine.get_history_report(date_str)
    if not content:
        raise HTTPException(status_code=404, detail=f"Relatório para {date_str} não encontrado.")
    return {"date": date_str, "content": content}


@app.post("/api/refresh")
async def force_refresh(mode: str = "all"):
    """Força o recálculo dos dois bancos de dados (Standard e Gauss) a partir do MT5."""
    if mode == "gauss":
        data = css_engine.update_data(force=True, mode="gauss")
    elif mode == "standard":
        data = css_engine.update_data(force=True, mode="standard")
    else:
        # Atualiza ambos os bancos
        css_engine.update_data(force=True, mode="standard")
        data = css_engine.update_data(force=True, mode="gauss")

    return {
        "success": True,
        "timestamp": data.get("timestamp"),
        "mt5_connected": data.get("mt5_connected", False)
    }


@app.get("/api/track-record/summary")
async def get_track_record_summary(currency: str = "ALL"):
    """Retorna métricas consolidadas, curva de capital e sessões com múltiplos portfólios."""
    data = history_engine.get_filtered_data(currency)
    return JSONResponse(content=data)


@app.get("/api/track-record/live")
async def get_track_record_live():
    """Retorna a sessão em andamento com cotações tick-a-tick em tempo real do MT5."""
    data = history_engine.get_live_session()
    return JSONResponse(content={"session": data})


@app.post("/api/track-record/recalculate")
async def recalculate_track_record(days: int = 60):
    """Sincroniza e audita as deals reais executadas no MT5 pelos 8 robôs com trava de segurança (1 a 180 dias)."""
    clamped_days = min(max(1, int(days)), 180)
    res = history_engine.sync_mt5_deals(days_back=clamped_days)
    return JSONResponse(content={"success": True, "summary": res.get("summary")})


# ---------------------------------------------------------------------------
# Acompanhamento de backtest via web (regression tracking) — ver
# docs/plans/eventual-stargazing-bear.md pro desenho completo e o porquê de
# cada decisão (consulta herdr-ask mfc-13). Leitura do journal
# (reports/backtest_history.json) não exige chave; disparar uma execução
# nova exige CSS_BACKTEST_API_KEY — uma chave DEDICADA, nunca
# CSS_PORTFOLIO_API_KEY (que abre posição real), pra não aumentar o raio de
# exposição dessa última.
# ---------------------------------------------------------------------------

BACKTEST_API_KEY = os.environ.get("CSS_BACKTEST_API_KEY")


def _backtest_api_key_matches(x_css_api_key: str) -> bool:
    if not BACKTEST_API_KEY:
        return False
    provided = (x_css_api_key or "").encode("utf-8", "surrogateescape")
    expected = str(BACKTEST_API_KEY).encode("utf-8", "surrogateescape")
    return hmac.compare_digest(provided, expected)


def _require_backtest_api_key(x_css_api_key: str = Header(default=None)):
    """Fail-closed: sem CSS_BACKTEST_API_KEY configurada, tanto disparar
    quanto ler o status (que pode conter log_tail com proveniência sensível
    — conta, servidor, caminho do terminal) ficam recusados."""
    if not BACKTEST_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="CSS_BACKTEST_API_KEY não configurada no servidor — disparo de backtest "
                   "via web desabilitado por padrão (fail closed).",
        )
    if not _backtest_api_key_matches(x_css_api_key):
        raise HTTPException(status_code=401, detail="Chave de API inválida ou ausente.")


class BacktestTriggerPayload(BaseModel):
    """Deliberadamente SEM sample_role/days/end_brt/engines — oos_disjoint e
    janela arbitrária são estruturalmente impossíveis por esta rota (ver
    scripts/run_isolated_backtest.py, que hardcoda os dois server-side, e o
    veto redundante em compare() via MFC_BACKTEST_WEB_TRIGGER=1)."""

    description: str = Field(
        min_length=run_isolated_backtest.MIN_DESCRIPTION_LEN,
        max_length=run_isolated_backtest.MAX_DESCRIPTION_LEN,
    )
    runs: int = Field(
        default=run_isolated_backtest.DEFAULT_RUNS,
        ge=run_isolated_backtest.MIN_RUNS,
        le=run_isolated_backtest.MAX_RUNS,
    )

    model_config = ConfigDict(extra="forbid")


def _summarize_backtest_entry(entry):
    """Linha resumida pra tabela — ver docs/plans/eventual-stargazing-bear.md,
    seção "Colunas da tabela". `market_open_at_run` é DERIVADO de
    recorded_at_utc (não persistido) — vale pra qualquer entrada, inclusive
    execuções CLI antigas, já que é função pura do timestamp."""
    if not isinstance(entry, dict):
        return None
    window = entry.get("window") if isinstance(entry.get("window"), dict) else {}
    provenance = entry.get("provenance") if isinstance(entry.get("provenance"), dict) else {}
    quality = entry.get("quality") if isinstance(entry.get("quality"), dict) else {}
    engines = entry.get("engines") if isinstance(entry.get("engines"), dict) else {}
    note = entry.get("note") if isinstance(entry.get("note"), str) else None

    recorded_at = entry.get("recorded_at_utc")
    market_open_at_run = None
    if isinstance(recorded_at, str):
        try:
            market_open_at_run = run_isolated_backtest.market_is_open(
                datetime.fromisoformat(recorded_at)
            )
        except ValueError:
            market_open_at_run = None

    return {
        "journal_seq": entry.get("journal_seq"),
        "recorded_at_utc": recorded_at,
        "market_open_at_run": market_open_at_run,
        "sample_role": window.get("sample_role"),
        "engines_compared": entry.get("engines_compared"),
        "window": {
            "days": window.get("days"),
            "start_brt": window.get("start_brt"),
            "end_brt": window.get("end_brt"),
            "nights_evaluated": window.get("nights_evaluated"),
        },
        "code_commit": provenance.get("code_commit"),
        "worktree_dirty": provenance.get("worktree_dirty"),
        "note": note,
        "is_web_trigger": bool(note and note.startswith("[web-trigger")),
        "engines": {
            name: {
                "baskets": metrics.get("baskets"),
                "bruto": metrics.get("bruto"),
                "liquido": metrics.get("liquido"),
                "quality_status": metrics.get("quality_status"),
            }
            for name, metrics in engines.items()
            if isinstance(metrics, dict)
        },
        "paired_net_delta_per_night": entry.get("paired_net_delta_per_night"),
        "runs": entry.get("runs"),
        "quality_status": quality.get("status"),
        # Anotação opcional pós-hoc (scripts/run_isolated_backtest.py, via
        # llm-gateway) — pode não existir ainda (gateway indisponível na
        # hora, ou anexada um pouco depois de "done") nem nunca vir a
        # existir (achado do Breno registrando o perfil: best_engine/
        # worst_engine saem omitidos de propósito em empate/inconclusivo —
        # não é erro). Nada sensível aqui, sem redação necessária.
        "llm_analysis": entry.get("llm_analysis") if isinstance(entry.get("llm_analysis"), dict) else None,
    }


@app.get("/api/backtest-history")
async def get_backtest_history(limit: int = 100, sample_role: str = None):
    """Histórico resumido pra tabela, mais recente primeiro. Só leitura —
    não exige chave. `reports/backtest_history.json` ausente devolve lista
    vazia (não erro); JSON corrompido/permissão negada devolve erro claro."""
    clamped_limit = min(max(1, int(limit)), 500)
    try:
        with open(RESULTS_LOG_PATH, "r", encoding="utf-8") as f:
            log = json.load(f)
    except FileNotFoundError:
        return {"entries": []}
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(status_code=500, detail=f"Journal ilegível: {exc}")
    if not isinstance(log, list):
        raise HTTPException(status_code=500, detail="Journal não contém uma lista JSON.")
    entries = [_summarize_backtest_entry(entry) for entry in log]
    entries = [entry for entry in entries if entry is not None]
    if sample_role:
        entries = [entry for entry in entries if entry.get("sample_role") == sample_role]
    entries.sort(key=lambda entry: entry.get("journal_seq") or 0, reverse=True)
    return {"entries": entries[:clamped_limit]}


# Chaves de identidade de conta/host/terminal a redigir, por NOME — não por
# caminho fixo dentro do dict. Achado P2-1 (herdr-review mfc-66,
# `mfc-rev-2`, com evidência real do journal_seq=28): a mesma identidade
# aparece em TRÊS envelopes com nomes de campo diferentes —
# entry["provenance"]["terminal"] (configured_path/mt5_path),
# entry["producer_provenance"]["terminal"] (path/observed_path), e
# entry["execution"] (host/terminal_path), este último uma chave de TOPO,
# não aninhada em nenhum dos dois. Uma primeira versão redigia só
# producer_provenance e deixava os outros dois vazarem login/servidor/
# hostname/caminhos sem chave nenhuma. Redigir por nome de campo (percorrendo
# o dict inteiro) em vez de por caminho fixo evita repetir esse erro se um
# quarto envelope aparecer no futuro.
_SENSITIVE_PROVENANCE_KEYS = {
    "login", "server", "configured_path", "mt5_path", "path",
    "observed_path", "host", "terminal_path",
}


def _redact_provenance(entry):
    """Remove recursivamente qualquer campo de identidade de conta/host/
    terminal do registro antes de devolver pelo endpoint SEM chave — ver
    _SENSITIVE_PROVENANCE_KEYS. O restante do registro (engines, qualidade,
    cobertura, janela, digest, currency, trade_mode) continua público."""
    if isinstance(entry, dict):
        return {
            key: ("[redacted]" if key in _SENSITIVE_PROVENANCE_KEYS else _redact_provenance(value))
            for key, value in entry.items()
        }
    if isinstance(entry, list):
        return [_redact_provenance(item) for item in entry]
    return entry


@app.get("/api/backtest-history/{journal_seq}")
async def get_backtest_history_entry(journal_seq: int):
    """Registro completo (pro painel de detalhe), com identidade de
    conta/terminal/host REDIGIDA — só leitura, sem chave. Diferente do
    endpoint de status do trigger (que exige CSS_BACKTEST_API_KEY porque o
    log_tail pode conter texto livre não redigido), este é público, então a
    proveniência sensível nunca sai daqui, mesmo que o servidor escute fora
    de localhost."""
    try:
        with open(RESULTS_LOG_PATH, "r", encoding="utf-8") as f:
            log = json.load(f)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Journal ainda não existe.")
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(status_code=500, detail=f"Journal ilegível: {exc}")
    for entry in log if isinstance(log, list) else []:
        if isinstance(entry, dict) and entry.get("journal_seq") == journal_seq:
            return JSONResponse(content=_redact_provenance(entry))
    raise HTTPException(status_code=404, detail=f"journal_seq={journal_seq} não encontrado.")


_backtest_trigger_lock = threading.Lock()


@app.post("/api/backtest-history/trigger", status_code=202)
async def trigger_backtest_history(
    payload: BacktestTriggerPayload, x_css_api_key: str = Header(default=None)
):
    """Dispara uma execução de acompanhamento (sample_role="exploratory",
    janela fixa) em processo separado, contra o terminal MT5 ISOLADO. Ver
    docs/plans/eventual-stargazing-bear.md."""
    _require_backtest_api_key(x_css_api_key)
    if run_isolated_backtest.in_critical_window():
        raise HTTPException(
            status_code=409,
            detail="Disparo recusado: horário dentro da janela crítica de abertura/fechamento "
                   "de cesta (20:55-22:00 ou 07:55-08:20 BRT).",
        )
    if not run_isolated_backtest.market_is_open():
        raise HTTPException(
            status_code=409,
            detail="Disparo recusado: mercado FX fechado agora — medição de custo por tick "
                   "ao vivo não teria sentido (ver achado 5 na consulta de design do plano).",
        )
    # Lock em memória guarda só a checagem+disparo instantâneos (nunca um
    # await dentro do `with` — threading.Lock não coopera com o event loop,
    # travá-lo durante uma espera travaria o servidor inteiro).
    with _backtest_trigger_lock:
        if run_isolated_backtest.is_trigger_running():
            raise HTTPException(
                status_code=409,
                detail="Já existe uma execução de acompanhamento em andamento.",
            )
        process, run_id = run_isolated_backtest.spawn_isolated_backtest(
            payload.description, payload.runs
        )
    # Fora do lock em memória: espera (não-bloqueante pro event loop, outras
    # requisições continuam servidas) o filho conquistar o lock de arquivo
    # dedicado, reduzindo — sem eliminar — a janela em que duas requisições
    # quase simultâneas veriam as duas "não está rodando" e cada uma
    # lançaria seu próprio processo (achado P2/P2-2, herdr-review mfc-65).
    # A correção estrutural pro caso residual é current_running_owner_pid():
    # mesmo que dois filhos cheguem a existir, só um segura o lock por vez, e
    # é sempre esse que o watchdog/status tratam como "o" dono.
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and not run_isolated_backtest.is_trigger_running():
        await asyncio.sleep(0.05)
    return {
        "status": "started",
        "run_id": run_id,
        "pid": process.pid,
        "description": payload.description,
        "runs": payload.runs,
    }


@app.get("/api/backtest-history/trigger/status")
async def get_backtest_trigger_status(x_css_api_key: str = Header(default=None)):
    """Estado do disparo mais recente — exige a chave (log_tail pode conter
    proveniência sensível do processo filho, que carrega o .env inteiro na
    importação; ver docs/plans/eventual-stargazing-bear.md).

    `status.json` NUNCA é reescrito por um kill do watchdog (SIGTERM não
    passa pelos caminhos normais de saída de _run_and_record) nem reflete
    instantaneamente um segundo dono assumindo o lock — então reconcilia
    nas DUAS direções contra is_trigger_running() (a autoridade real, ver
    scripts/run_isolated_backtest.py::read_status) antes de responder
    (achado P2-1, herdr-review mfc-65, `mfc-rev-2`).

    A reconciliação corrige só o campo `status` (e, no caso de um dono
    novo, `pid`) — `run_id`/`description`/`log_tail` podem continuar sendo
    da execução ANTERIOR nesse caso (achado P2, herdr-review mfc-66,
    `mfc-rev`: sem um status por `run_id`, não há como saber a descrição do
    dono novo até ele mesmo escrever seu próprio status.json). Sinalizado
    explicitamente via `stale_metadata=True` — não fingir precisão que o
    desenho atual não tem."""
    _require_backtest_api_key(x_css_api_key)
    status = run_isolated_backtest.read_status()
    lock_running = run_isolated_backtest.is_trigger_running()
    json_running = status.get("status") == "running"
    if lock_running and not json_running:
        # outro processo já assumiu o lock, mas ainda não sobrescreveu
        # status.json com sua própria descrição — reflete a realidade
        # (running + o PID de fato atual), não o snapshot defasado da
        # execução anterior. run_id/description continuam podendo ser da
        # execução anterior (ver docstring acima).
        status = {
            **status,
            "status": "running",
            "pid": run_isolated_backtest.current_running_owner_pid(),
            "stale_metadata": True,
        }
    elif json_running and not lock_running:
        # o dono anterior morreu sem passar pelos caminhos normais de saída
        # (ex.: SIGTERM do watchdog) — status.json nunca é reescrito nesse
        # caso, então "running" aqui seria uma mentira permanente.
        status = {**status, "status": "interrupted"}
    status["log_tail"] = run_isolated_backtest.read_log_tail()
    return JSONResponse(content=status)


_BACKTEST_WATCHDOG_INTERVAL_SEC = 30


def _backtest_critical_window_watchdog():
    """Termina o subprocesso de backtest se ele ainda estiver rodando
    quando o host entrar na janela crítica — mais seguro que tentar prever
    duração, porque matar O PROCESSO CERTO (o filho de backtest) é sempre
    seguro (nunca envia ordem, append_result() só escreve no fim, de forma
    atômica; pior caso é perder uma execução de diagnóstico). Roda pra
    qualquer disparo, inclusive um smoke test manual (`python
    scripts/run_isolated_backtest.py ...`) executado enquanto este servidor
    está de pé, já que a checagem lê o lock de arquivo compartilhado, não um
    Popen em memória.

    Usa current_running_owner_pid() (nunca status.json["pid"] bruto) — o PID
    gravado ali é imune especificamente ao caso em que um segundo disparo
    enfileirado sobrescreveu status.json com o próprio PID antes de bloquear
    esperando o primeiro terminar (achado P2/P2-2, herdr-review mfc-65). Não
    é imune a TUDO: current_running_owner_pid() ainda sonda o lock e lê
    owner.pid em dois passos não-atômicos (achado P1, herdr-review
    mfc-66/mfc-67/mfc-68, ainda residual — ver a própria docstring da
    função) — a garantia real é "identifica corretamente o dono na imensa
    maioria dos casos", não "nunca erra o PID"."""
    while True:
        time.sleep(_BACKTEST_WATCHDOG_INTERVAL_SEC)
        try:
            if not run_isolated_backtest.in_critical_window():
                continue
            pid = run_isolated_backtest.current_running_owner_pid()
            if pid is None:
                continue
            run_isolated_backtest.terminate_owner(pid)
        except Exception as exc:
            # Nunca engolir em silêncio (achado P2, herdr-review mfc-66,
            # `mfc-rev`) — uma falha aqui significa a proteção de janela
            # crítica parou de funcionar sem que ninguém saiba.
            print(f"[!] watchdog de janela crítica do backtest falhou: {exc}")
            continue


threading.Thread(target=_backtest_critical_window_watchdog, daemon=True).start()


class PortfolioOpenPayload(BaseModel):
    currency: str
    bias: str # "BUY" (Força) ou "SELL" (Fraqueza)
    lot: float = 0.01


class PortfolioClosePayload(BaseModel):
    currency: str = "ALL"


# Trava de autenticação pro endpoint que ABRE ordem real. Sem chave configurada
# (variável de ambiente CSS_PORTFOLIO_API_KEY), o endpoint recusa TODA
# requisição — falha fechado, não aberto. CORS "*" hoje deixa qualquer aba de
# navegador na mesma máquina chamar isso; essa chave é a única barreira até
# uma solução melhor (rede isolada, mTLS, etc.) entrar em cena.
PORTFOLIO_API_KEY = os.environ.get("CSS_PORTFOLIO_API_KEY")


def _portfolio_api_key_matches(x_css_api_key: str) -> bool:
    """True se a chave provida bate com a configurada. Sempre False se
    nenhuma chave estiver configurada (nada pra comparar contra).

    Compara em BYTES: compare_digest com str lança TypeError se houver
    caractere não-ASCII (o Starlette decodifica headers em latin-1, então um
    byte alto no header viraria 500 em vez de 401)."""
    if not PORTFOLIO_API_KEY:
        return False
    provided = (x_css_api_key or "").encode("utf-8", "surrogateescape")
    expected = str(PORTFOLIO_API_KEY).encode("utf-8", "surrogateescape")
    return hmac.compare_digest(provided, expected)


def _require_portfolio_api_key(x_css_api_key: str = Header(default=None)):
    """Pro endpoint que ABRE ordem real: fail-closed completo. Sem chave
    configurada, recusa TUDO — nunca abre cesta sem barreira nenhuma."""
    if not PORTFOLIO_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="CSS_PORTFOLIO_API_KEY não configurada no servidor — endpoint de execução "
                   "real desabilitado por padrão (fail closed). Configure a variável de ambiente "
                   "pra habilitar."
        )
    if not _portfolio_api_key_matches(x_css_api_key):
        raise HTTPException(status_code=401, detail="Chave de API inválida ou ausente.")


def _require_portfolio_api_key_for_close(x_css_api_key: str = Header(default=None)):
    """Pro endpoint que FECHA posição: NUNCA bloqueia por falta de chave
    configurada (achado em revisão, F10) — fechar reduz risco, mesma regra
    que já vale pro kill switch em agents/portfolio_executor.py ("fechar
    nunca é bloqueado"). Uma CSS_PORTFOLIO_API_KEY esquecida/vazia não pode
    trancar o operador fora numa emergência. Se uma chave ESTIVER
    configurada, ainda exige a chave certa: sem isso, qualquer aba na rede
    local poderia fechar cestas à força e interromper a estratégia antes da
    hora — dano real, só que menor que ficar sem conseguir fechar."""
    if not PORTFOLIO_API_KEY:
        return
    if not _portfolio_api_key_matches(x_css_api_key):
        raise HTTPException(status_code=401, detail="Chave de API inválida ou ausente.")


@app.get("/api/portfolio-robots/telemetry")
async def get_portfolio_robots_telemetry():
    """Retorna a telemetria ao vivo das posições abertas no MT5 agrupadas pelos 8 Magic Numbers dos portfólios.
    Só leitura — não exige a chave de API."""
    from agents.portfolio_executor import get_live_portfolio_telemetry
    data = get_live_portfolio_telemetry()
    return JSONResponse(content=data)


@app.post("/api/portfolio-robots/open")
async def open_portfolio_robot(payload: PortfolioOpenPayload, x_css_api_key: str = Header(default=None)):
    """Abre a cesta de 7 pares de uma moeda no MT5 com seu Magic Number exclusivo.
    Exige X-Css-Api-Key. As travas de segurança de verdade (kill switch,
    validade da configuração de execução, conta demo, idempotência, tetos
    de exposição, colisão de símbolo — ordem completa em CLAUDE.md, seção
    "Live MT5 execution") ficam em agents/portfolio_executor.py — essa
    chave é só a porta de entrada, não substitui aquelas checagens."""
    _require_portfolio_api_key(x_css_api_key)
    from agents.portfolio_executor import open_portfolio_basket
    res = open_portfolio_basket(payload.currency, payload.bias, payload.lot)
    return JSONResponse(content=res)


@app.post("/api/portfolio-robots/close")
async def close_portfolio_robot(payload: PortfolioClosePayload, x_css_api_key: str = Header(default=None)):
    """Fecha posições de um portfólio específico ou de todos os 8 portfólios no MT5.
    Exige a chave só se uma estiver configurada — sem chave configurada, o
    endpoint fica aberto (fechar reduz risco, nunca é bloqueado por
    configuração ausente; ver _require_portfolio_api_key_for_close)."""
    _require_portfolio_api_key_for_close(x_css_api_key)
    from agents.portfolio_executor import close_portfolio_basket, close_all_portfolios
    if payload.currency.upper() == "ALL":
        res = close_all_portfolios()
    else:
        res = close_portfolio_basket(payload.currency)
    return JSONResponse(content=res)


class TelegramRaioXPayload(BaseModel):
    target: str
    image_base64: str
    bias: str = ""
    confluence_state: str = ""
    timestamp: str = ""


@app.post("/api/telegram/send-raio-x")
async def send_raio_x_telegram(payload: TelegramRaioXPayload):
    """Recebe a imagem do Raio-X em Base64 e despacha diretamente para o Telegram."""
    try:
        from web.telegram_service import send_telegram_photo
        img_str = payload.image_base64
        if "," in img_str:
            img_str = img_str.split(",", 1)[1]
        img_bytes = base64.b64decode(img_str)

        caption = (
            f"📊 <b>Raio-X Institucional: {payload.target}</b>\n"
            f"🎯 <b>Estado:</b> {payload.confluence_state}\n"
            f"🧭 <b>Viés:</b> {payload.bias}\n"
            f"🕒 <i>{payload.timestamp or datetime.now().strftime('%Y-%m-%d %H:%M:%S')} — CSS Institutional</i>"
        )

        res = send_telegram_photo(img_bytes, filename=f"Raio-X_{payload.target}.png", caption=caption)
        if not res.get("success"):
            raise HTTPException(status_code=500, detail=res.get("error", "Erro ao disparar Telegram"))
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/telegram/trigger-daily-routine")
async def trigger_daily_routine_endpoint(x_css_api_key: str = Header(default=None)):
    """Dispara a rotina diária das 21h em background gerando os 8 Raio-X e enviando para o Telegram.

    Exige a chave de API porque run_daily_routine() gera 9 gráficos e
    despacha várias mensagens/fotos reais pro Telegram configurado — sem a
    trava, qualquer aba aberta na máquina (CORS é "*") dispararia isso à
    vontade. NÃO grava mais o sinal oficial de execução (data/
    portfolio_signals_live.json): essa gravação foi removida daqui (achado
    em revisão — corria com scripts/scheduler_daemon.py::execute_phase_2102,
    que já grava esse sinal de forma independente às 21:02 BRT). Disparar
    este endpoint manualmente NÃO substitui nem reforça o sinal do dia."""
    _require_portfolio_api_key(x_css_api_key)
    try:
        import threading
        from daily_css_routine import run_daily_routine
        thread = threading.Thread(target=run_daily_routine, daemon=True)
        thread.start()
        return {"success": True, "message": "Rotina diária das 21h iniciada em background."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Montar arquivos estáticos
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/{full_path:path}", response_class=HTMLResponse)
async def serve_index(full_path: str = ""):
    """Serve o SPA pra qualquer rota de navegação do cliente (convenção
    Rails, ex.: /track_record/backtest — ver MODAL_ROUTES em app.js). O
    path em si não importa pro servidor, só o JS decide o que abrir a
    partir de window.location.pathname — deixa uma URL desconhecida cair
    no dashboard padrão (estado inicial), nunca 404 pro cliente.

    Registrado por ÚLTIMO (depois do mount de /static e de todo /api/*,
    que casam primeiro por terem sido registrados antes — Starlette tenta
    rotas na ordem de registro), então esta captura só o que sobra.

    Isso NÃO reserva o prefixo /api/ — um /api/rota-que-nao-existe também
    casaria aqui e devolveria 200+HTML em vez de 404 (achado P3-1,
    herdr-review mfc-67, `mfc-rev-2`: o front faz `if (!res.ok) throw`, que
    nunca dispara com 200, e o erro vira um SyntaxError genérico de
    `res.json()` sobre HTML). Recusa explicitamente esse prefixo aqui —
    `full_path == "api"` cobre o path SEM barra final (`/api`), que o
    conversor do Starlette entrega sem o `/` que `.startswith("api/")`
    sozinho exigia (achado P3, herdr-review mfc-68, `mfc-rev`, verify
    mode)."""
    if full_path == "api" or full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail=f"Rota de API não encontrada: /{full_path}")
    index_file = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_file):
        with open(index_file, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>CSS Web Platform</h1><p>Frontend em carregamento...</p>")


def run_server(host="127.0.0.1", port=8050, open_browser=False):
    print(f"[*] Iniciando CSS Web Platform em http://{host}:{port}")
    if open_browser:
        try:
            webbrowser.open(f"http://localhost:{port}")
        except Exception:
            pass
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    # Configurável via env, com o mesmo default de sempre (localhost:8050) —
    # nada muda pra quem já roda "python web/server.py" sem nenhuma variável.
    run_server(
        host=os.environ.get("CSS_WEB_HOST", "127.0.0.1"),
        port=int(os.environ.get("CSS_WEB_PORT", "8050")),
    )

