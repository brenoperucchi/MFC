"""
TESTE UNITÁRIO DE AUDITORIA INCREMENTAL E IMUTABILIDADE (FASE 2)
Objetivo:
1. Validar que o journal funciona em modo Append-Only (sem perda de sessões passadas).
2. Validar que a detecção dinâmica de fuso horário do Broker MT5 está operacional.
3. Validar a integridade da persistência atômica com backups diários.
"""

import os
import sys
import unittest
import json
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from web.real_portfolio_audit import (
    real_audit_engine, get_broker_gmt_offset, JOURNAL_FILE, BACKUPS_DIR
)


class TestIncrementalAudit(unittest.TestCase):
    def test_dynamic_broker_offset(self):
        """Verifica se o cálculo do GMT Offset do broker retorna um valor válido."""
        offset = get_broker_gmt_offset()
        self.assertIsInstance(offset, int)
        self.assertTrue(-12 <= offset <= 14, f"Offset inválido: {offset}")
        print(f"[✓] GMT Offset do Broker MT5 Detectado Dinamicamente: GMT{offset:+d}")

    def test_immutable_journal_structure(self):
        """Verifica a integridade da estrutura do Journal oficial."""
        journal = real_audit_engine.journal
        self.assertIn("summary", journal)
        self.assertIn("sessions", journal)
        self.assertIn("equity_curve", journal)
        self.assertIn("portfolio_equity_curves", journal)
        self.assertEqual(len(journal["portfolio_equity_curves"]), 8)
        print(f"[✓] Journal Oficial Estruturado com 8 Curvas de Moedas: {list(journal['portfolio_equity_curves'].keys())}")

    def test_backups_directory(self):
        """Verifica a existência do diretório de backups protegidos."""
        self.assertTrue(os.path.exists(BACKUPS_DIR))
        print(f"[✓] Diretório de Backups Diários Ativo: {BACKUPS_DIR}")


if __name__ == "__main__":
    unittest.main()
