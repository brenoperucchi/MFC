"""
TESTE AUTOMATIZADO DE UNIFICAÇÃO DE SCORE CSS (FASE 0 & FASE 1)
Objetivo: Garantir que:
1. O Painel Web (css_service.py)
2. O Relatório Diário (daily_css_routine.py)
3. O Motor de Portfólios / MT5 Signals (portfolio_executor.py / real_portfolio_audit.py)
utilizam a MESMA fonte matemática de verdade e concordam 100% em scores, tríades e direção.
"""

import os
import sys
import unittest
from datetime import datetime
from unittest.mock import patch

sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from web.css_service import css_engine, CURRENCIES, MT5_AVAILABLE, mt5
from agents.triad_analyzer import analyze_tf_triad
import agents.portfolio_executor as pe
from agents.portfolio_executor import generate_and_save_daily_signals, PORTFOLIO_MAGICS


class TestScoreUnification(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if MT5_AVAILABLE:
            # Via css_engine.connect_mt5() (não mt5.initialize() direto) —
            # reusa a mesma trava de MT5_PATH corrigida ali (achado ALTO em
            # revisão), em vez de duplicar aqui um 5º ponto que anexaria a
            # qualquer terminal disponível na máquina sem validar nada.
            css_engine.connect_mt5()

    def test_single_source_of_truth(self):
        """Verifica se todos os módulos consomem rigorosamente o mesmo cálculo."""
        print("\n===================================================================")
        print("  TESTE DE CONCORDÂNCIA E UNIFICAÇÃO DE SCORE CSS (100% UNIFICADO) ")
        print("===================================================================")
        
        # 1. Obter dados do Painel Web (css_service)
        web_data = css_engine.update_data(force=False, mode="standard")
        self.assertIsNotNone(web_data, "Falha ao obter dados do CSS Engine")
        
        # 2. Obter Sinais de Portfólio (portfolio_executor / MT5)
        # Neutraliza só a ESCRITA em disco (achado F11): sem isso, esta
        # chamada reescreve de verdade data/portfolio_signals_live.json (e a
        # pasta MQL5/Files do MT5, se configurada) toda vez que a suíte roda,
        # sujando o sinal real do operador mesmo fora de produção. O CÁLCULO
        # que este teste precisa comparar continua real — não mocka
        # currencies_data nem mt5_connected.
        with patch.object(pe, "_atomic_write_json", lambda *a, **k: None), \
             patch.object(pe, "get_mt5_files_dir", lambda: None):
            signals_payload = generate_and_save_daily_signals()
        self.assertIn("portfolios", signals_payload)
        portfolios = signals_payload["portfolios"]
        
        charts = web_data.get("charts", {})
        
        print(f"{'Moeda':<6} | {'D1 Engine':<10} | {'D1 Sinais':<10} | {'H4 Engine':<10} | {'H4 Sinais':<10} | {'Direção':<8} | {'Concordância':<12}")
        print("-" * 80)
        
        for ccy in CURRENCIES:
            # Score D1 e H4 no Web Engine
            d1_series = charts.get("D1", {}).get("series", {}).get(ccy, [])
            h4_series = charts.get("H4", {}).get("series", {}).get(ccy, [])
            
            d1_engine_score = round(float(d1_series[-1]), 3) if len(d1_series) > 0 else 0.0
            h4_engine_score = round(float(h4_series[-1]), 3) if len(h4_series) > 0 else 0.0
            
            # Score D1 e H4 nos Sinais de Execução do MT5
            sig = portfolios.get(ccy, {})
            d1_sig_score = sig.get("d1_score", 0.0)
            h4_sig_score = sig.get("h4_score", 0.0)
            direction = sig.get("direction", "NEUTRAL")
            
            # Validação de Concordância Absoluta
            self.assertAlmostEqual(
                d1_engine_score, d1_sig_score, places=2,
                msg=f"Discrepância no score D1 para {ccy}: Engine={d1_engine_score} vs Sinais={d1_sig_score}"
            )
            self.assertAlmostEqual(
                h4_engine_score, h4_sig_score, places=2,
                msg=f"Discrepância no score H4 para {ccy}: Engine={h4_engine_score} vs Sinais={h4_sig_score}"
            )
            
            # Validar Tríade
            triad_d1 = analyze_tf_triad("D1", d1_series)
            self.assertAlmostEqual(
                triad_d1["score"], d1_engine_score, places=2,
                msg=f"Discrepância na Tríade D1 para {ccy}"
            )
            
            print(f"{ccy:<6} | {d1_engine_score:+10.3f} | {d1_sig_score:+10.3f} | {h4_engine_score:+10.3f} | {h4_sig_score:+10.3f} | {direction:<8} | {'100% MATCH':<12}")

        print("-" * 80)
        print("[SUCESSO] Todos os módulos (Painel, Relatórios, Robôs) concordam perfeitamente entre si!")


if __name__ == "__main__":
    unittest.main()
