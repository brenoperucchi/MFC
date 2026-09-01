"""
Roteamento por URL do SPA (convenção Rails, ex.: /track_record/backtest —
ver MODAL_ROUTES em web/static/app.js). O backend não conhece rota
nenhuma de verdade: web/server.py::serve_index é um catch-all que serve o
MESMO index.html pra qualquer path que não seja /api/* ou /static/* — só
o JS decide o que abrir a partir de window.location.pathname.

Pulado automaticamente se fastapi/uvicorn/httpx não estiverem instalados
neste ambiente — mesmo padrão de tests/test_portfolio_api_auth.py.
"""

import os
import sys

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import pytest

try:
    from fastapi.testclient import TestClient
    import web.server as server
except ImportError as e:
    pytest.skip(f"stack web (fastapi/uvicorn/httpx) não instalado neste ambiente: {e}",
                 allow_module_level=True)


@pytest.fixture()
def client():
    return TestClient(server.app)


def test_root_serves_index_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "<html" in resp.text.lower()


def test_known_spa_route_serves_the_same_index_html(client):
    """/track_record/backtest — exatamente o exemplo dado pelo Breno."""
    root = client.get("/")
    route = client.get("/track_record/backtest")
    assert route.status_code == 200
    assert route.text == root.text


def test_unknown_path_falls_back_to_index_html_not_404(client):
    """Uma rota que o JS não reconhece cai no dashboard padrão — nunca 404
    pro cliente, já que a validade da rota é decidida no JS, não aqui."""
    resp = client.get("/rota/que/nao/existe")
    assert resp.status_code == 200
    assert "<html" in resp.text.lower()


def test_deep_dive_route_with_currency_segment_serves_index_html(client):
    resp = client.get("/deep_dive/AUD")
    assert resp.status_code == 200
    assert "<html" in resp.text.lower()


@pytest.mark.parametrize("path", [
    "/api/status", "/api/css/all", "/api/pairs", "/api/backtest-history",
])
def test_catch_all_never_shadows_api_routes(client, path):
    """A catch-all foi registrada por ÚLTIMO de propósito — rotas /api/*
    já registradas antes continuam casando primeiro (Starlette tenta rotas
    na ordem de registro). Uma regressão de ordem faria isto virar HTML."""
    resp = client.get(path)
    assert resp.status_code != 404
    content_type = resp.headers.get("content-type", "")
    assert "html" not in content_type.lower()


def test_catch_all_never_shadows_the_static_mount(client):
    resp = client.get("/static/app.js")
    assert resp.status_code == 200
    content_type = resp.headers.get("content-type", "")
    assert "html" not in content_type.lower()
