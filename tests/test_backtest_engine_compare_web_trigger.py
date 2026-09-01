"""
Testes das duas mudanças em scripts/backtest_engine_compare.py::compare()
feitas pro acompanhamento de backtest via web (achados 1 e 2 da consulta de
design herdr-ask mfc-13, ver docs/plans/eventual-stargazing-bear.md):

1. compare() recusa sample_role="oos_disjoint" quando
   MFC_BACKTEST_WEB_TRIGGER=1 estiver setado (veto do lado do executor).
2. A asserção de terminal isolado (_assert_oos_terminal_configuration)
   passa a rodar pra QUALQUER execução com
   MFC_BACKTEST_TERMINAL_ISOLATED=1, não só sample_role=="oos_disjoint".

Os dois pontos são verificados ANTES de qualquer conexão MT5 real (a ordem
de compare() garante isso — ver o corpo da função), então nenhum destes
testes precisa mockar o pipeline inteiro de dados.
"""

import os
import sys
import unittest
from unittest.mock import patch

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import scripts.backtest_engine_compare as bec


def _clear_backtest_env():
    for key in ("MFC_BACKTEST_WEB_TRIGGER", "MFC_BACKTEST_TERMINAL_ISOLATED"):
        os.environ.pop(key, None)


class TestOosDisjointVetoedForWebTrigger(unittest.TestCase):
    """Achado 2 (herdr-ask mfc-13, ambos os revisores): segunda linha de
    defesa contra oos_disjoint vazar pela web, independente do endpoint."""

    def setUp(self):
        _clear_backtest_env()

    def tearDown(self):
        _clear_backtest_env()

    def test_web_trigger_env_blocks_oos_disjoint_before_end_brt_check(self):
        os.environ["MFC_BACKTEST_WEB_TRIGGER"] = "1"
        # Sem end_brt: se o veto NÃO disparasse primeiro, cairia no
        # ValueError de "end_brt explícito" em vez do RuntimeError do veto —
        # a mensagem esperada aqui prova qual checagem disparou primeiro.
        with self.assertRaises(RuntimeError) as ctx:
            bec.compare(sample_role="oos_disjoint")
        self.assertIn("MFC_BACKTEST_WEB_TRIGGER", str(ctx.exception))

    def test_without_web_trigger_env_oos_disjoint_reaches_preexisting_end_brt_check(self):
        """Guarda de regressão: o veto novo não pode bloquear um disparo
        oos_disjoint legítimo pela CLI (sem a variável do trigger web)."""
        with self.assertRaises(ValueError) as ctx:
            bec.compare(sample_role="oos_disjoint")
        self.assertIn("end_brt", str(ctx.exception))

    def test_exploratory_is_never_affected_by_the_veto(self):
        os.environ["MFC_BACKTEST_WEB_TRIGGER"] = "1"
        with patch.object(bec, "ensure_mt5", return_value=False):
            # exploratory nunca dispara o veto (só existe pra oos_disjoint);
            # deve seguir até a checagem normal de MT5 e devolver 1 (não
            # levantar RuntimeError nenhum).
            result = bec.compare(engine_names=["3tf_baseline"], sample_role="exploratory")
        self.assertEqual(result, 1)


class TestTerminalIsolationAssertionScopeExpanded(unittest.TestCase):
    """Achado 1 (herdr-ask mfc-13, ambos os revisores): antes só rodava pra
    sample_role=="oos_disjoint" — o disparo web usa "exploratory" e nunca
    era verificado."""

    def setUp(self):
        _clear_backtest_env()

    def tearDown(self):
        _clear_backtest_env()

    def test_assertion_still_requires_the_isolated_env_var_directly(self):
        with self.assertRaises(RuntimeError) as ctx:
            bec._assert_oos_terminal_configuration()
        self.assertIn("MFC_BACKTEST_TERMINAL_ISOLATED", str(ctx.exception))

    def test_assertion_rejects_a_non_isolated_terminal_path(self):
        os.environ["MFC_BACKTEST_TERMINAL_ISOLATED"] = "1"
        with patch.object(bec, "MT5_PATH", r"D:\MetaTradersWSL\mfc\terminal64.exe"):
            with self.assertRaises(RuntimeError) as ctx:
                bec._assert_oos_terminal_configuration()
        self.assertIn("mfc-backtest", str(ctx.exception))

    def test_assertion_accepts_the_isolated_terminal_path(self):
        os.environ["MFC_BACKTEST_TERMINAL_ISOLATED"] = "1"
        with patch.object(bec, "MT5_PATH", r"D:\MetaTradersWSL\mfc-backtest\terminal64.exe"):
            bec._assert_oos_terminal_configuration()  # não deve levantar

    def test_compare_calls_the_assertion_for_exploratory_when_isolated_env_set(self):
        os.environ["MFC_BACKTEST_TERMINAL_ISOLATED"] = "1"
        sentinel = RuntimeError("sentinel-called")
        with patch.object(bec, "_assert_oos_terminal_configuration", side_effect=sentinel) as mocked:
            with self.assertRaises(RuntimeError) as ctx:
                bec.compare(engine_names=["3tf_baseline"], sample_role="exploratory")
        mocked.assert_called_once()
        self.assertIs(ctx.exception, sentinel)

    def test_compare_does_not_call_the_assertion_for_plain_exploratory(self):
        """Regressão: uma execução exploratória comum (sem a variável de
        isolamento) continua não sendo verificada — comportamento pré-mudança
        preservado pra quem chama compare() sem nenhuma intenção de
        isolamento."""
        with patch.object(bec, "_assert_oos_terminal_configuration") as mocked, \
             patch.object(bec, "ensure_mt5", return_value=False):
            result = bec.compare(engine_names=["3tf_baseline"], sample_role="exploratory")
        mocked.assert_not_called()
        self.assertEqual(result, 1)


if __name__ == "__main__":
    unittest.main()
