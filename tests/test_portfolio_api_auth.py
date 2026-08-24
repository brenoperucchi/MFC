"""
TESTE DE AUTENTICAÇÃO DOS ENDPOINTS /api/portfolio-robots/*

Pulado automaticamente se fastapi não estiver instalado neste ambiente —
este checkout Linux não tem o stack web completo, só o necessário pra
tests/test_portfolio_safety.py (que não importa web/server.py). Rodar de
verdade requer `pip install fastapi uvicorn` (ver CLAUDE.md).
"""

import os
import sys
import unittest
from unittest.mock import patch

import pytest

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# web/server.py importa fastapi E uvicorn (entre outras deps do stack web) —
# um importorskip só de "fastapi" não bastaria (uvicorn ausente ainda quebra
# a COLETA da suíte inteira, não só pula este arquivo). Pula por qualquer
# ImportError faltando, não só um nome específico.
try:
    from fastapi import HTTPException
    import web.server as server
except ImportError as e:
    pytest.skip(f"stack web (fastapi/uvicorn) não instalado neste ambiente: {e}",
                 allow_module_level=True)


class TestCloseEndpointNeverLocksOutWithoutConfiguredKey(unittest.TestCase):
    """Regressão (achado F10): /api/portfolio-robots/close exigia a mesma
    chave que /open — sem CSS_PORTFOLIO_API_KEY configurada, o endpoint
    devolvia 503 pra QUALQUER chamada de fechamento, trancando o operador de
    fora numa emergência. Contradiz a regra estabelecida em todo o resto do
    sistema (kill switch em agents/portfolio_executor.py,
    close_all_portfolios): fechar/reduzir risco nunca é bloqueado."""

    def test_close_never_blocked_when_no_key_configured(self):
        with patch.object(server, "PORTFOLIO_API_KEY", None):
            server._require_portfolio_api_key_for_close(None)  # não deve lançar
            server._require_portfolio_api_key_for_close("qualquer-coisa")  # também não
        print("[✓] /close nunca bloqueia por falta de CSS_PORTFOLIO_API_KEY configurada")

    def test_open_still_fails_closed_when_no_key_configured(self):
        """Guarda de regressão: a correção do /close não pode enfraquecer o
        /open, que precisa continuar recusando TUDO sem chave configurada."""
        with patch.object(server, "PORTFOLIO_API_KEY", None):
            with self.assertRaises(HTTPException) as ctx:
                server._require_portfolio_api_key(None)
        self.assertEqual(ctx.exception.status_code, 503)
        print("[✓] /open continua fail-closed (503) sem chave configurada")

    def test_close_still_requires_correct_key_when_one_is_configured(self):
        """Sem chave configurada, /close fica aberto — mas com uma chave
        configurada, ainda precisa ser a CERTA: senão qualquer aba na rede
        local fecharia cestas à força."""
        with patch.object(server, "PORTFOLIO_API_KEY", "segredo123"):
            with self.assertRaises(HTTPException) as ctx:
                server._require_portfolio_api_key_for_close("chave-errada")
        self.assertEqual(ctx.exception.status_code, 401)
        print("[✓] /close com chave configurada ainda exige a chave certa")

    def test_close_passes_with_correct_key_when_one_is_configured(self):
        with patch.object(server, "PORTFOLIO_API_KEY", "segredo123"):
            server._require_portfolio_api_key_for_close("segredo123")  # não deve lançar
        print("[✓] /close com a chave certa passa normalmente")


if __name__ == "__main__":
    unittest.main()
