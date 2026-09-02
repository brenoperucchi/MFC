"""
Roteamento por URL do SPA (convenção Rails, ex.: /track_record/backtest —
ver MODAL_ROUTES em web/static/app.js). O backend não conhece rota
nenhuma de verdade: web/server.py::serve_index é um catch-all que serve o
MESMO index.html pra qualquer path que não seja /static/* (mount) ou um
/api/* já REGISTRADO acima dele — um /api/* inexistente recusa 404
explicitamente (achado P3-1, herdr-review mfc-67) em vez de cair no
catch-all. Só o JS decide o que abrir a partir de window.location.pathname
pras rotas de fato conhecidas.

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


def test_catch_all_refuses_unregistered_api_path_with_404_not_200_html(client):
    """Achado P3-1 (herdr-review mfc-67, mfc-rev-2): sem reserva de prefixo,
    um /api/* com typo ou removido cairia no catch-all e devolveria
    200+HTML — o front trata isso como sucesso (`if (!res.ok) throw` nunca
    dispara), e o erro real vira um SyntaxError genérico de res.json() sobre
    HTML em vez de um 404 legível."""
    resp = client.get("/api/rota-que-nao-existe")
    assert resp.status_code == 404
    content_type = resp.headers.get("content-type", "")
    assert "html" not in content_type.lower()


def test_catch_all_refuses_bare_api_path_without_trailing_slash(client):
    """Achado P3 (herdr-review mfc-68, mfc-rev, verify mode): a checagem
    original só testava `startswith("api/")` — /api SEM barra final (que o
    conversor {full_path:path} do Starlette entrega como full_path="api",
    sem o "/" que o startswith exigia) escapava e caía no catch-all,
    devolvendo 200+HTML em vez de 404."""
    resp = client.get("/api")
    assert resp.status_code == 404
    content_type = resp.headers.get("content-type", "")
    assert "html" not in content_type.lower()


def test_catch_all_never_shadows_the_static_mount(client):
    resp = client.get("/static/app.js")
    assert resp.status_code == 200
    content_type = resp.headers.get("content-type", "")
    assert "html" not in content_type.lower()
