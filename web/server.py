"""
SERVIDOR FASTAPI — PLATAFORMA WEB CSS (LOCAL HOST : 8050)
Fornece API REST de alta performance e serve a aplicação web frontend SPA.
"""

import os
import sys
import hmac
import base64
from datetime import datetime
import webbrowser
import uvicorn
from pydantic import BaseModel
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


@app.get("/", response_class=HTMLResponse)
async def serve_index():
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

