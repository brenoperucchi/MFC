"""
SERVIDOR FASTAPI — PLATAFORMA WEB CSS (LOCAL HOST : 8050)
Fornece API REST de alta performance e serve a aplicação web frontend SPA.
"""

import os
import sys
import webbrowser
import uvicorn
from fastapi import FastAPI, HTTPException
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
async def get_css_all():
    """Retorna todos os dados de moedas, gráficos, tríades e screener dos 28 pares."""
    data = css_engine.update_data(force=False)
    return JSONResponse(content=data)


@app.get("/api/css/chart/{tf}")
async def get_chart_by_tf(tf: str):
    """Retorna séries temporais específicas para um timeframe (MN1, W1, D1, H4, H1)."""
    tf_upper = tf.upper()
    data = css_engine.update_data(force=False)
    charts = data.get("charts", {})
    if tf_upper not in charts:
        raise HTTPException(status_code=404, detail=f"Timeframe '{tf_upper}' não encontrado.")
    return {
        "tf": tf_upper,
        "times": charts[tf_upper]["times"],
        "series": charts[tf_upper]["series"],
        "colors": data.get("colors", {}),
        "flags": data.get("flags", {})
    }


@app.get("/api/pairs")
async def get_pairs():
    """Retorna o ranking e diagnóstico dos 28 pares Forex."""
    data = css_engine.update_data(force=False)
    return {"pairs": data.get("pairs", [])}


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
async def force_refresh():
    """Força o recálculo dos dados a partir do MT5."""
    data = css_engine.update_data(force=True)
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


@app.post("/api/track-record/recalculate")
async def recalculate_track_record(days: int = 45):
    """Força o recálculo do backtest multi-portfólio a partir do MT5."""
    res = history_engine.run_full_backtest(days=days)
    return JSONResponse(content={"success": True, "summary": res.get("summary")})


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
    run_server()

