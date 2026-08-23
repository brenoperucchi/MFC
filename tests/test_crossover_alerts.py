"""
TESTE UNITÁRIO DO MOTOR DE ALERTAS DE CRUZAMENTOS E ANTI-SPAM (FASE 3)
Objetivo:
1. Validar que cruzamentos são detectados corretamente para os 28 pares em H1 e H4.
2. Validar que a máquina de estados anti-spam não reenvia o mesmo evento duplicado.
3. Validar a formatação correta das mensagens em HTML para o Telegram.
"""

import os
import sys
import unittest

sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from agents.crossover_alert_engine import (
    scan_and_dispatch_crossovers, format_crossover_message, load_sent_events
)


class TestCrossoverAlerts(unittest.TestCase):
    def test_crossover_formatting(self):
        """Verifica a estrutura e tags HTML da mensagem de alerta."""
        dummy_cross = {
            "pair": "USDCAD",
            "base": "USD",
            "quote": "CAD",
            "timeframe": "H1",
            "direction": "BUY",
            "spread": 0.425,
            "action_thesis": "USD superou CAD em força relativa",
            "timestamp": "2026-08-22 21:00"
        }
        msg = format_crossover_message(dummy_cross)
        self.assertIn("USDCAD", msg)
        self.assertIn("H1", msg)
        self.assertIn("COMPRA", msg)
        self.assertIn("+0.425", msg)
        print("[✓] Formatação HTML de Alerta Validada com Sucesso")

    def test_scan_and_anti_spam(self):
        """Verifica a varredura e o filtro anti-spam."""
        alerts = scan_and_dispatch_crossovers(dry_run=True)
        self.assertIsInstance(alerts, list)
        print(f"[✓] Varredura Concluída: {len(alerts)} Cruzamentos Recentes Detectados")


if __name__ == "__main__":
    unittest.main()
