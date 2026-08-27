"""
TESTE UNITÁRIO DAS TRAVAS DE SEGURANÇA DA EXECUÇÃO (FASE 1)
Objetivo:
1. Validar que o kill switch bloqueia abertura de cesta sem tocar em MT5 nenhum.
2. Validar que a trava de conta demo recusa abrir em conta que não seja demo.
3. Validar a idempotência: não reabre cesta já aberta pro mesmo magic number.
4. Validar a recusa por colisão de símbolo em conta netting.
5. Validar o cálculo do stop-loss catastrófico (pip size JPY vs. não-JPY).
6. Validar que a escrita de sinais é atômica (sem arquivo temporário sobrando).

Este projeto roda em ambiente sem o pacote MetaTrader5 instalado (Linux) — os
testes que dependem de "conexão MT5" usam um objeto fake em lugar do módulo
real, via unittest.mock.patch, para exercitar a lógica de decisão sem precisar
do terminal rodando.
"""

import os
import sys
import subprocess
import io
from contextlib import redirect_stdout
import json
import tempfile
import threading
import time
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch, MagicMock, mock_open

sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import agents.portfolio_executor as pe
import web.css_service as cs
import web.real_portfolio_audit as rpa
import web.history_tracker as ht


def make_fake_mt5(**overrides):
    """Fake mínimo do módulo MetaTrader5 — só as constantes e funções que
    agents/portfolio_executor.py realmente usa."""
    fake = MagicMock()
    fake.ACCOUNT_TRADE_MODE_DEMO = "DEMO"
    fake.ACCOUNT_MARGIN_MODE_RETAIL_NETTING = "NETTING"
    fake.ACCOUNT_MARGIN_MODE_RETAIL_HEDGING = "HEDGING"
    fake.TRADE_ACTION_DEAL = 1
    fake.ORDER_TYPE_BUY = 0
    fake.ORDER_TYPE_SELL = 1
    fake.ORDER_TIME_GTC = 0
    fake.ORDER_FILLING_IOC = 1
    fake.ORDER_FILLING_RETURN = 2
    fake.TRADE_RETCODE_DONE = 10009
    # Achado 2 (revisão de ad44e12/c24a44c): sem isso, um MagicMock sem essa
    # constante configurada devolve outro Mock (nunca o default de getattr,
    # já que MagicMock nunca lança AttributeError) — todo teste que não
    # setasse isso ficaria com full_mode == algum Mock arbitrário. O valor
    # aqui é o mesmo "FULL" que todo teste desta suíte já usa quando define
    # a constante manualmente — só uniformiza o padrão.
    fake.SYMBOL_TRADE_MODE_FULL = "FULL"
    fake.terminal_info.return_value = SimpleNamespace(connected=True)
    fake.positions_get.return_value = []
    for k, v in overrides.items():
        setattr(fake, k, v)
    return fake


# Conta demo "esperada" padrão usada nos testes que precisam passar da trava
# de identidade (check_account_gate) pra exercitar o que vem depois dela.
# CSS_MT5_EXPECTED_LOGIN=999 casa com o login=999 usado nesses testes.
DEMO_GATE_ENV = {"CSS_MT5_EXPECTED_LOGIN": "999", "CSS_LIVE_TRADING": ""}


class TestKillSwitch(unittest.TestCase):
    """NUNCA tocar no KILL_SWITCH_FILE real: se o operador armou o kill switch
    e alguém roda a suíte, apagá-lo aqui desarmaria a única trava que funciona
    com o resto do sistema fora do ar. Todo teste usa um caminho temporário."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._flag = os.path.join(self._tmp.name, "CSS_KILL.flag")
        # get_mt5_files_dir também é neutralizado — senão a checagem do segundo
        # local poderia encontrar (ou criar) algo fora do sandbox do teste.
        self._patches = [
            patch.object(pe, "KILL_SWITCH_FILE", self._flag),
            patch.object(pe, "get_mt5_files_dir", lambda: None),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()

    def test_kill_switch_off_by_default(self):
        self.assertFalse(pe.is_kill_switch_active())

    def test_real_kill_switch_file_is_never_touched_by_tests(self):
        """Regressão: a suíte apagava data/CSS_KILL.flag do operador.

        A versão anterior deste teste era falso-verde estrutural: amostrava
        `existed_before` DEPOIS do setUp, então se o setUp voltasse a apagar o
        flag real, o teste ainda passaria. Aqui o arquivo real é criado dentro
        do próprio teste, e o que se exige é que ele SOBREVIVA — o que falha
        de verdade se alguém reintroduzir o os.remove no caminho real."""
        real_flag = os.path.join(pe.DATA_DIR, "CSS_KILL.flag")
        self.assertNotEqual(pe.KILL_SWITCH_FILE, real_flag,
                            "KILL_SWITCH_FILE deve estar patcheado pra um tmpdir nos testes")
        created_here = not os.path.exists(real_flag)
        if created_here:
            with open(real_flag, "w") as f:
                f.write("sentinela do teste")
        try:
            with open(self._flag, "w") as f:
                f.write("stop")
            pe.is_kill_switch_active()
            pe.open_portfolio_basket("CAD", "BUY")
            self.assertTrue(os.path.exists(real_flag),
                            "a suíte apagou o kill switch real do operador")
        finally:
            if created_here and os.path.exists(real_flag):
                os.remove(real_flag)
        print("[✓] O kill switch real sobrevive à suíte (teste capaz de falhar de verdade)")

    def test_kill_switch_blocks_open_without_touching_mt5(self):
        with open(self._flag, "w") as f:
            f.write("stop")
        self.assertTrue(pe.is_kill_switch_active())

        # MT5_AVAILABLE segue False neste ambiente — se o kill switch não
        # travar ANTES de qualquer tentativa de uso do mt5, o teste falharia
        # com um erro de atributo/None em vez de um "success": False limpo.
        result = pe.open_portfolio_basket("CAD", "BUY")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "kill_switch_active")
        print("[✓] Kill switch bloqueia abertura sem tocar em MT5")


class TestMt5FilesDirValidation(unittest.TestCase):
    """Regressão (achado F2): get_mt5_files_dir() criava MQL5/Files embaixo
    de QUALQUER caminho em MT5_PATH, mesmo um typo/instância errada — kill
    switch e sinais eram gravados numa pasta fantasma que reportava sucesso
    mas que o EA (lendo o terminal real) nunca via."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmp.cleanup()

    def test_returns_none_and_creates_nothing_when_terminal_exe_does_not_exist(self):
        ghost_dir = os.path.join(self._tmp.name, "instancia-errada")
        ghost_exe = os.path.join(ghost_dir, "terminal64.exe")
        with patch.object(pe, "MT5_PATH", ghost_exe):
            result = pe.get_mt5_files_dir()
        self.assertIsNone(result)
        self.assertFalse(os.path.exists(ghost_dir),
                          "criou diretório fantasma embaixo de um terminal64.exe que não existe")
        print("[✓] MT5_PATH apontando pra terminal inexistente: None, nenhum diretório criado")

    def test_returns_files_dir_when_terminal_exe_really_exists(self):
        real_dir = os.path.join(self._tmp.name, "mfc-portable")
        os.makedirs(real_dir)
        real_exe = os.path.join(real_dir, "terminal64.exe")
        open(real_exe, "w").close()
        with patch.object(pe, "MT5_PATH", real_exe):
            result = pe.get_mt5_files_dir()
        self.assertEqual(result, os.path.join(real_dir, "MQL5", "Files"))
        self.assertTrue(os.path.isdir(result))
        print("[✓] MT5_PATH apontando pro terminal real: cria e devolve MQL5/Files")


class TestEnsureMt5NeverAttachesToWrongTerminal(unittest.TestCase):
    """Regressão ALTO (achado em revisão sobre o F2): ensure_mt5() caía pra
    mt5.initialize() SEM path quando MT5_PATH não existia — nesta máquina,
    que roda vários terminais MT5 pra estratégias/contas diferentes, isso
    podia anexar SILENCIOSAMENTE a um terminal errado em vez de falhar. Falha
    fechada: sem MT5_PATH resolvendo pro terminal certo, ensure_mt5() nunca
    tenta se conectar a nenhum outro."""

    def test_refuses_to_initialize_without_path_when_mt5_path_does_not_exist(self):
        fake_mt5 = make_fake_mt5()
        fake_mt5.terminal_info.return_value = None  # nenhum terminal já anexado
        with patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5), \
             patch.object(pe, "MT5_PATH", "/caminho/que/nao/existe/terminal64.exe"):
            result = pe.ensure_mt5()
        self.assertFalse(result)
        fake_mt5.initialize.assert_not_called()
        print("[✓] MT5_PATH inexistente: ensure_mt5() falha fechado, NUNCA chama initialize() sem path")

    def test_initializes_with_path_when_mt5_path_really_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            real_exe = os.path.join(tmp, "terminal64.exe")
            open(real_exe, "w").close()
            fake_mt5 = make_fake_mt5()
            fake_mt5.terminal_info.return_value = None
            fake_mt5.initialize.return_value = True
            with patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5), \
                 patch.object(pe, "MT5_PATH", real_exe):
                result = pe.ensure_mt5()
        self.assertTrue(result)
        fake_mt5.initialize.assert_called_once_with(path=real_exe)
        print("[✓] MT5_PATH apontando pro terminal real: inicializa normalmente")


class TestConnectMt5NeverAttachesToWrongTerminal(unittest.TestCase):
    """Regressão ALTO (achado em revisão): web/css_service.py::connect_mt5()
    tinha o MESMO bug que ensure_mt5() em agents/portfolio_executor.py —
    caía pra mt5.initialize() SEM path quando MT5_PATH não existia, podendo
    anexar silenciosamente a QUALQUER outro terminal MT5 já rodando na
    máquina. Importa em dobro: é o connect_mt5() (não o ensure_mt5()) que
    roda de verdade na rotina das 21:00/21:02, via css_engine.update_data —
    corrigir só o ensure_mt5() deixava esse caminho real intocado."""

    def test_refuses_to_initialize_without_path_when_mt5_path_does_not_exist(self):
        fake_mt5 = MagicMock()
        with patch.object(cs, "MT5_AVAILABLE", True), patch.object(cs, "mt5", fake_mt5), \
             patch.object(cs, "MT5_PATH", "/caminho/que/nao/existe/terminal64.exe"):
            result = cs.css_engine.connect_mt5()
        self.assertFalse(result)
        fake_mt5.initialize.assert_not_called()
        print("[✓] MT5_PATH inexistente: connect_mt5() falha fechado, NUNCA chama initialize() sem path")

    def test_initializes_with_path_when_mt5_path_really_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            real_exe = os.path.join(tmp, "terminal64.exe")
            open(real_exe, "w").close()
            fake_mt5 = MagicMock()
            fake_mt5.initialize.return_value = True
            with patch.object(cs, "MT5_AVAILABLE", True), patch.object(cs, "mt5", fake_mt5), \
                 patch.object(cs, "MT5_PATH", real_exe):
                result = cs.css_engine.connect_mt5()
        self.assertTrue(result)
        fake_mt5.initialize.assert_called_once_with(path=real_exe)
        print("[✓] MT5_PATH apontando pro terminal real: connect_mt5() inicializa normalmente")


class TestRealAuditEnsureMt5NeverAttachesToWrongTerminal(unittest.TestCase):
    """Regressão ALTO (achado em revisão): web/real_portfolio_audit.py tinha
    uma 3ª cópia do MESMO bug (mt5.initialize() sem path). Crítica em
    particular: real_audit_engine é instanciado no IMPORT do módulo e chama
    isso antes de qualquer outra checagem no processo — corrigir só
    portfolio_executor.py e css_service.py não bastava, porque esta rodava
    PRIMEIRO e deixava os outros dois acharem 'já conectado'."""

    def test_refuses_to_initialize_without_path_when_mt5_path_does_not_exist(self):
        fake_mt5 = MagicMock()
        fake_mt5.terminal_info.return_value = None
        with patch.object(rpa, "MT5_AVAILABLE", True), patch.object(rpa, "mt5", fake_mt5), \
             patch.object(rpa, "MT5_PATH", "/caminho/que/nao/existe/terminal64.exe"):
            result = rpa.ensure_mt5()
        self.assertFalse(result)
        fake_mt5.initialize.assert_not_called()
        print("[✓] real_portfolio_audit.ensure_mt5(): falha fechada, NUNCA chama initialize() sem path")

    def test_initializes_with_path_when_mt5_path_really_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            real_exe = os.path.join(tmp, "terminal64.exe")
            open(real_exe, "w").close()
            fake_mt5 = MagicMock()
            fake_mt5.terminal_info.return_value = None
            fake_mt5.initialize.return_value = True
            with patch.object(rpa, "MT5_AVAILABLE", True), patch.object(rpa, "mt5", fake_mt5), \
                 patch.object(rpa, "MT5_PATH", real_exe):
                result = rpa.ensure_mt5()
        self.assertTrue(result)
        fake_mt5.initialize.assert_called_once_with(path=real_exe)
        print("[✓] real_portfolio_audit.ensure_mt5(): MT5_PATH real, inicializa normalmente")


class TestHistoryTrackerEnsureMt5NeverAttachesToWrongTerminal(unittest.TestCase):
    """Mesmo bug, 4ª cópia: web/history_tracker.py::ensure_mt5_connected(),
    usada por TrackRecordEngine (scripts/backtest_selection_rules.py) — não
    é o caminho ao vivo, mas o mesmo risco de anexar num terminal errado."""

    def test_refuses_to_initialize_without_path_when_mt5_path_does_not_exist(self):
        fake_mt5 = MagicMock()
        fake_mt5.terminal_info.return_value = None
        with patch.object(ht, "MT5_AVAILABLE", True), patch.object(ht, "mt5", fake_mt5), \
             patch.object(ht, "MT5_PATH", "/caminho/que/nao/existe/terminal64.exe"):
            result = ht.ensure_mt5_connected()
        self.assertFalse(result)
        fake_mt5.initialize.assert_not_called()
        print("[✓] history_tracker.ensure_mt5_connected(): falha fechada, NUNCA chama initialize() sem path")

    def test_initializes_with_path_when_mt5_path_really_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            real_exe = os.path.join(tmp, "terminal64.exe")
            open(real_exe, "w").close()
            fake_mt5 = MagicMock()
            fake_mt5.terminal_info.return_value = None
            fake_mt5.initialize.return_value = True
            with patch.object(ht, "MT5_AVAILABLE", True), patch.object(ht, "mt5", fake_mt5), \
                 patch.object(ht, "MT5_PATH", real_exe):
                result = ht.ensure_mt5_connected()
        self.assertTrue(result)
        fake_mt5.initialize.assert_called_once_with(path=real_exe)
        print("[✓] history_tracker.ensure_mt5_connected(): MT5_PATH real, inicializa normalmente")


class TestAccountSafety(unittest.TestCase):
    def test_get_account_safety_info_fails_closed_without_mt5(self):
        """Sem MT5 disponível, is_demo deve ser False (fail closed), não True."""
        with patch.object(pe, "MT5_AVAILABLE", False):
            info = pe.get_account_safety_info()
        self.assertFalse(info["is_demo"])
        print("[✓] get_account_safety_info() falha fechado quando MT5 indisponível")

    def test_open_refused_when_account_not_demo(self):
        """Login batendo com o esperado, mas conta real sem CSS_LIVE_TRADING —
        recusa especificamente pela checagem de demo, não pela de identidade."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.account_info.return_value = SimpleNamespace(
            login=12345, server="Broker-Real", trade_mode="REAL",
            trade_allowed=True, margin_mode="HEDGING"
        )
        with patch.dict(os.environ, {"CSS_MT5_EXPECTED_LOGIN": "12345", "CSS_LIVE_TRADING": ""}), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe.open_portfolio_basket("CAD", "BUY")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "not_demo_account")
        fake_mt5.order_send.assert_not_called()
        print("[✓] Abertura recusada quando a conta não é demo (mesmo com login esperado batendo)")

    def test_open_proceeds_to_orders_when_account_is_demo(self):
        """Conta demo + identidade batendo + sem colisão + sem posição prévia
        deve chegar a tentar enviar ordem (não necessariamente com sucesso, só
        confirma que passou das travas de segurança)."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.account_info.return_value = SimpleNamespace(
            login=999, server="Broker-Demo", trade_mode="DEMO",
            trade_allowed=True, margin_mode="HEDGING"
        )
        fake_mt5.symbol_info.return_value = SimpleNamespace(visible=True, trade_mode="FULL")
        fake_mt5.symbol_info_tick.return_value = SimpleNamespace(ask=1.1000, bid=1.0998)
        fake_mt5.order_send.return_value = SimpleNamespace(
            retcode=fake_mt5.TRADE_RETCODE_DONE, order=1, price=1.1000, comment="ok"
        )
        with patch.dict(os.environ, DEMO_GATE_ENV), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe.open_portfolio_basket("CAD", "BUY")
        self.assertTrue(fake_mt5.order_send.called)
        self.assertEqual(result["opened_count"], 7)
        # Toda ordem enviada deve ter carregado um SL != 0 (rede de segurança).
        for call in fake_mt5.order_send.call_args_list:
            request = call.args[0]
            self.assertNotEqual(request["sl"], 0.0)
        print("[✓] Conta demo sem colisão chega a enviar ordem, com SL catastrófico em todas")


class TestAccountGate(unittest.TestCase):
    """check_account_gate: identidade da conta (essa máquina roda vários
    terminais MT5 com contas diferentes ao mesmo tempo) + liberação
    explícita pra conta real, nunca uma sozinha basta."""

    def test_refuses_when_expected_login_not_configured(self):
        fake_mt5 = make_fake_mt5()
        fake_mt5.account_info.return_value = SimpleNamespace(
            login=999, server="Broker-Demo", trade_mode="DEMO",
            trade_allowed=True, margin_mode="HEDGING"
        )
        with patch.dict(os.environ, {"CSS_MT5_EXPECTED_LOGIN": "", "CSS_LIVE_TRADING": ""}), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe.open_portfolio_basket("CAD", "BUY")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "no_expected_login_configured")
        fake_mt5.order_send.assert_not_called()
        print("[✓] Sem CSS_MT5_EXPECTED_LOGIN configurado, recusa mesmo em conta demo")

    def test_refuses_when_login_does_not_match_expected(self):
        """Conta demo, mas NÃO a esperada — ex.: um dos outros terminais
        (irai, ira_ticks, pairtrading...) que rodam na mesma máquina."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.account_info.return_value = SimpleNamespace(
            login=111, server="Broker-Demo-Outra-Conta", trade_mode="DEMO",
            trade_allowed=True, margin_mode="HEDGING"
        )
        with patch.dict(os.environ, {"CSS_MT5_EXPECTED_LOGIN": "999", "CSS_LIVE_TRADING": ""}), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe.open_portfolio_basket("CAD", "BUY")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "wrong_account")
        fake_mt5.order_send.assert_not_called()
        print("[✓] Login diferente do esperado recusa mesmo em conta demo (conta/terminal errado)")

    def test_refuses_real_account_without_live_flag_even_with_matching_login(self):
        fake_mt5 = make_fake_mt5()
        fake_mt5.account_info.return_value = SimpleNamespace(
            login=555, server="Broker-Real", trade_mode="REAL",
            trade_allowed=True, margin_mode="HEDGING"
        )
        with patch.dict(os.environ, {"CSS_MT5_EXPECTED_LOGIN": "555", "CSS_LIVE_TRADING": ""}), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe.open_portfolio_basket("CAD", "BUY")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "not_demo_account")
        fake_mt5.order_send.assert_not_called()
        print("[✓] Conta real recusada mesmo com login esperado batendo, sem CSS_LIVE_TRADING=true")

    def test_allows_real_account_only_with_live_flag_and_matching_login(self):
        """A única combinação que libera conta real: login batendo E
        CSS_LIVE_TRADING=true explícito ao mesmo tempo."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.account_info.return_value = SimpleNamespace(
            login=555, server="Broker-Real", trade_mode="REAL",
            trade_allowed=True, margin_mode="HEDGING"
        )
        fake_mt5.symbol_info.return_value = SimpleNamespace(visible=True, trade_mode="FULL")
        fake_mt5.symbol_info_tick.return_value = SimpleNamespace(ask=1.1000, bid=1.0998)
        fake_mt5.order_send.return_value = SimpleNamespace(
            retcode=fake_mt5.TRADE_RETCODE_DONE, order=1, price=1.1000, comment="ok"
        )
        with patch.dict(os.environ, {"CSS_MT5_EXPECTED_LOGIN": "555", "CSS_LIVE_TRADING": "true"}), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe.open_portfolio_basket("CAD", "BUY")
        self.assertTrue(fake_mt5.order_send.called)
        self.assertEqual(result["opened_count"], 7)
        print("[✓] Conta real só é aceita com CSS_LIVE_TRADING=true E login esperado batendo")


class TestExecutionConfigGate(unittest.TestCase):
    """check_execution_config(): achado em revisão (Codex, herdr-review
    rodada 6, F-06; design consultado via herdr-ask, mfc-rev + mfc-rev-2,
    2026-08-27). _env_number() nunca derruba o import (deliberado), mas
    isso deixava "valor explicitamente fornecido e inválido" abrir cesta
    com um default que pode ser o OPOSTO da intenção do operador. Este
    gate roda no MOMENTO DO USO, logo depois do kill switch, e recusa a
    abertura (não o processo) quando qualquer uma das variáveis de
    segurança está presente mas inválida."""

    def test_open_refused_when_max_lot_is_not_a_number(self):
        fake_mt5 = make_fake_mt5()
        with patch.dict(os.environ, {**DEMO_GATE_ENV, "CSS_MAX_LOT": "o.01"}), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe.open_portfolio_basket("CAD", "BUY")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "invalid_execution_config")
        self.assertIn("CSS_MAX_LOT", result["message"])
        fake_mt5.order_send.assert_not_called()
        fake_mt5.account_info.assert_not_called()
        print("[✓] CSS_MAX_LOT com typo recusa a abertura, não cai silenciosamente no "
              "default — e nem chega a consultar a conta MT5")

    def test_open_refused_when_catastrophic_sl_is_zero(self):
        """A REGRESSÃO CENTRAL do achado (mfc-rev-2, herdr-ask): SL <= 0
        CASTA sem erro nenhum (_env_number nem imprime aviso) e
        _compute_catastrophic_sl() devolve sl=0.0 — a rede de segurança
        desarmada em silêncio, sem nenhum sinal de que algo está errado.
        Um "0" copiado sem querer de um .env de teste não pode abrir cesta
        real sem stop nenhum."""
        fake_mt5 = make_fake_mt5()
        with patch.dict(os.environ, {**DEMO_GATE_ENV, "CSS_CATASTROPHIC_SL_PIPS": "0"}), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe.open_portfolio_basket("CAD", "BUY")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "invalid_execution_config")
        self.assertIn("CSS_CATASTROPHIC_SL_PIPS", result["message"])
        fake_mt5.order_send.assert_not_called()
        print("[✓] CSS_CATASTROPHIC_SL_PIPS=0 recusa a abertura em vez de abrir sem stop")

    def test_open_refused_when_catastrophic_sl_is_negative(self):
        fake_mt5 = make_fake_mt5()
        with patch.dict(os.environ, {**DEMO_GATE_ENV, "CSS_CATASTROPHIC_SL_PIPS": "-50"}), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe.open_portfolio_basket("CAD", "BUY")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "invalid_execution_config")
        fake_mt5.order_send.assert_not_called()
        print("[✓] CSS_CATASTROPHIC_SL_PIPS negativo também recusa, não só zero")

    def test_open_refused_when_catastrophic_sl_is_nan_or_infinite(self):
        for raw in ("nan", "inf"):
            with self.subTest(raw=raw):
                fake_mt5 = make_fake_mt5()
                with patch.dict(os.environ, {**DEMO_GATE_ENV, "CSS_CATASTROPHIC_SL_PIPS": raw}), \
                     patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
                    result = pe.open_portfolio_basket("CAD", "BUY")
                self.assertFalse(result["success"])
                self.assertEqual(result["error"], "invalid_execution_config")
                fake_mt5.order_send.assert_not_called()
        print("[✓] CSS_CATASTROPHIC_SL_PIPS=nan/inf recusa — o cast real é int() "
              "(_EXECUTION_CONFIG_SPEC), que já rejeita as duas strings por "
              "ValueError direto, sem precisar de math.isfinite() aqui")

    def test_open_refused_when_max_concurrent_baskets_is_negative(self):
        fake_mt5 = make_fake_mt5()
        with patch.dict(os.environ, {**DEMO_GATE_ENV, "CSS_MAX_CONCURRENT_BASKETS": "-1"}), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe.open_portfolio_basket("CAD", "BUY")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "invalid_execution_config")
        fake_mt5.order_send.assert_not_called()
        print("[✓] CSS_MAX_CONCURRENT_BASKETS negativo recusa a abertura")

    def test_open_refused_when_ambiguous_confirm_attempts_is_out_of_range(self):
        """CSS_AMBIGUOUS_CONFIRM_ATTEMPTS já era silenciosamente CLAMPADO
        pra [1, 10] sem nenhum aviso (_clamp não imprime nada, ao contrário
        de _env_number) — o comentário do próprio arquivo reconhece isso
        como o mesmo problema do F-06 com um canal pior."""
        fake_mt5 = make_fake_mt5()
        with patch.dict(os.environ, {**DEMO_GATE_ENV, "CSS_AMBIGUOUS_CONFIRM_ATTEMPTS": "300000"}), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe.open_portfolio_basket("CAD", "BUY")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "invalid_execution_config")
        self.assertIn("CSS_AMBIGUOUS_CONFIRM_ATTEMPTS", result["message"])
        fake_mt5.order_send.assert_not_called()
        print("[✓] CSS_AMBIGUOUS_CONFIRM_ATTEMPTS fora da faixa clampada recusa a "
              "abertura em vez de ser silenciosamente reescrito pra 10")

    def test_open_reports_multiple_invalid_vars_in_one_message(self):
        fake_mt5 = make_fake_mt5()
        with patch.dict(os.environ, {**DEMO_GATE_ENV, "CSS_MAX_LOT": "xyz",
                                      "CSS_CATASTROPHIC_SL_PIPS": "0"}), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe.open_portfolio_basket("CAD", "BUY")
        self.assertFalse(result["success"])
        self.assertIn("CSS_MAX_LOT", result["message"])
        self.assertIn("CSS_CATASTROPHIC_SL_PIPS", result["message"])
        fake_mt5.order_send.assert_not_called()
        print("[✓] Duas variáveis inválidas ao mesmo tempo saem juntas na mesma mensagem, "
              "nenhuma mascara a outra, e nenhuma ordem sai")

    def test_open_refused_when_ambiguous_confirm_delay_is_out_of_range(self):
        fake_mt5 = make_fake_mt5()
        with patch.dict(os.environ, {**DEMO_GATE_ENV, "CSS_AMBIGUOUS_CONFIRM_DELAY_SEC": "50"}), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe.open_portfolio_basket("CAD", "BUY")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "invalid_execution_config")
        self.assertIn("CSS_AMBIGUOUS_CONFIRM_DELAY_SEC", result["message"])
        fake_mt5.order_send.assert_not_called()
        print("[✓] CSS_AMBIGUOUS_CONFIRM_DELAY_SEC fora da faixa clampada também recusa "
              "a abertura")

    def test_open_refused_when_catastrophic_sl_is_a_decimal_string(self):
        """A REGRESSÃO CENTRAL do F06-1 (mfc-rev-2 + mfc-rev, herdr-review
        rodada 7, confirmado pelos dois independentemente): a primeira
        versão de check_execution_config() validava CSS_CATASTROPHIC_SL_PIPS
        com float(), mas o cast REAL (_env_number) usa int(). "50.0" passava
        no gate antigo (float aceita) e o cast real falhava, caindo em
        silêncio no default 150 — o operador reduz o SL de 150 pra 50 depois
        de um incidente, escreve "50.0" (hábito natural num .env onde
        CSS_MAX_LOT já usa decimal), e a cesta abre com SL de 150 mesmo
        assim. Isso não pode mais passar: agora o gate usa o MESMO cast
        (int) que o valor real, via _EXECUTION_CONFIG_SPEC."""
        fake_mt5 = make_fake_mt5()
        with patch.dict(os.environ, {**DEMO_GATE_ENV, "CSS_CATASTROPHIC_SL_PIPS": "50.0"}), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe.open_portfolio_basket("CAD", "BUY")
        self.assertFalse(result["success"],
                         "CSS_CATASTROPHIC_SL_PIPS='50.0' precisa ser recusado pelo gate, "
                         "não cair silenciosamente no default 150 no cast real")
        self.assertEqual(result["error"], "invalid_execution_config")
        fake_mt5.order_send.assert_not_called()
        print("[✓] CSS_CATASTROPHIC_SL_PIPS='50.0' recusa a abertura — o gate usa o "
              "mesmo cast (int) que o valor realmente usado, não mais float()")

    def test_open_refused_when_ambiguous_confirm_attempts_is_a_decimal_string(self):
        """Mesma classe do teste acima, pra CSS_AMBIGUOUS_CONFIRM_ATTEMPTS
        (também castado com int() de verdade, validado com float() na
        primeira versão do gate)."""
        fake_mt5 = make_fake_mt5()
        with patch.dict(os.environ, {**DEMO_GATE_ENV, "CSS_AMBIGUOUS_CONFIRM_ATTEMPTS": "5.0"}), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe.open_portfolio_basket("CAD", "BUY")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "invalid_execution_config")
        fake_mt5.order_send.assert_not_called()
        print("[✓] CSS_AMBIGUOUS_CONFIRM_ATTEMPTS='5.0' também recusa — mesmo cast do "
              "valor real (int), não float()")

    def test_kill_switch_takes_priority_over_invalid_execution_config(self):
        """Achado em revisão (mfc-rev-2, herdr-review rodada 7, P3-1): a
        ordem kill switch → config gate não tinha teste nenhum fixando ela.
        Com os dois motivos de recusa presentes ao mesmo tempo, o kill
        switch tem que aparecer na mensagem — senão um operador com o kill
        switch armado E o .env quebrado vê "config inválida", "conserta" o
        .env, e não percebe que a própria alavanca de emergência continua
        puxada."""
        fake_mt5 = make_fake_mt5()
        tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(tmp_dir.cleanup)
        flag_path = os.path.join(tmp_dir.name, "CSS_KILL.flag")
        with open(flag_path, "w") as f:
            f.write("")
        with patch.dict(os.environ, {**DEMO_GATE_ENV, "CSS_MAX_LOT": "xyz"}), \
             patch.object(pe, "KILL_SWITCH_FILE", flag_path), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe.open_portfolio_basket("CAD", "BUY")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "kill_switch_active",
                         "kill switch precisa vencer a checagem de config — senão um "
                         "operador pode 'consertar' o .env sem perceber que o kill "
                         "switch continua armado")
        fake_mt5.order_send.assert_not_called()
        print("[✓] Kill switch tem prioridade sobre config inválida — a ordem "
              "documentada no CLAUDE.md está fixada por teste")

    def test_open_proceeds_normally_with_explicit_valid_execution_config(self):
        """Controle negativo: valores EXPLICITAMENTE fornecidos mas válidos
        não podem disparar o gate — prova que os testes acima não passam só
        porque qualquer env var setada recusa."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.account_info.return_value = SimpleNamespace(
            login=999, server="Broker-Demo", trade_mode="DEMO",
            trade_allowed=True, margin_mode="HEDGING")
        fake_mt5.symbol_info.return_value = SimpleNamespace(visible=True, trade_mode="FULL")
        fake_mt5.symbol_info_tick.return_value = SimpleNamespace(ask=1.1000, bid=1.0998)
        fake_mt5.order_send.return_value = SimpleNamespace(
            retcode=fake_mt5.TRADE_RETCODE_DONE, order=1, price=1.1000, comment="ok")
        with patch.dict(os.environ, {**DEMO_GATE_ENV, "CSS_MAX_LOT": "0.05",
                                      "CSS_MAX_CONCURRENT_BASKETS": "4",
                                      "CSS_CATASTROPHIC_SL_PIPS": "80"}), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            # MAX_LOT (a constante já lida no import) não muda por setar a
            # env var aqui — só o gate revalida o valor bruto. lot=0.01
            # bate com o default de MAX_LOT, então a checagem de teto
            # antiga (independente desta correção) não interfere.
            result = pe.open_portfolio_basket("CAD", "BUY", lot=0.01)
        self.assertTrue(fake_mt5.order_send.called)
        self.assertEqual(result["opened_count"], 7)
        print("[✓] Configuração explícita e válida abre normalmente — o gate não é "
              "falso positivo pra qualquer env var setada")

    def test_close_portfolio_basket_ignores_invalid_execution_config(self):
        """Assimetria deliberada (mesmo padrão do kill switch): reduzir
        risco nunca pode ser bloqueado por config de ABERTURA quebrada."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.account_info.return_value = SimpleNamespace(
            login=999, server="Broker-Demo", trade_mode="DEMO",
            trade_allowed=True, margin_mode="HEDGING")
        fake_mt5.positions_get.return_value = [
            SimpleNamespace(ticket=1, symbol="USDCAD", volume=0.01,
                            type=fake_mt5.ORDER_TYPE_SELL, magic=pe.PORTFOLIO_MAGICS["CAD"],
                            price_open=1.35, price_current=1.36, profit=1.0)
        ]
        fake_mt5.symbol_info_tick.return_value = SimpleNamespace(ask=1.3600, bid=1.3598)
        fake_mt5.order_send.return_value = SimpleNamespace(
            retcode=fake_mt5.TRADE_RETCODE_DONE, order=2, price=1.3598, comment="ok")
        with patch.dict(os.environ, {"CSS_MT5_EXPECTED_LOGIN": "999",
                                      "CSS_CATASTROPHIC_SL_PIPS": "0"}), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe.close_portfolio_basket("CAD")
        self.assertTrue(result["success"])
        fake_mt5.order_send.assert_called()
        print("[✓] Fechamento continua liberado mesmo com config de execução inválida — "
              "só a ABERTURA é bloqueada")


class TestGmtOffsetEnvSafety(unittest.TestCase):
    """Achado em revisão (mfc-rev-2, herdr-review rodada 7, P2-1): GMT_OFFSET
    em web/history_tracker.py usava int() cru, sem try/except — um typo em
    CSS_MT5_GMT_OFFSET derrubava o IMPORT deste módulo e, por tabela, o de
    agents/portfolio_executor.py (que importa daqui) — servidor web E daemon
    inteiros, exatamente o problema que _env_number() existe pra evitar."""

    def test_env_int_safe_never_raises_on_garbage(self):
        for raw in ("abc", "3.5", "", "  ", "inf", "nan"):
            with self.subTest(raw=raw):
                with patch.dict(os.environ, {"CSS_MT5_GMT_OFFSET_TESTE": raw}):
                    valor = ht._env_int_safe("CSS_MT5_GMT_OFFSET_TESTE", -3)
                self.assertEqual(valor, -3)
        print("[✓] _env_int_safe() nunca levanta exceção — qualquer lixo cai no default, "
              "sem derrubar o import do módulo")

    def test_env_int_safe_accepts_valid_int_string(self):
        with patch.dict(os.environ, {"CSS_MT5_GMT_OFFSET_TESTE": "0"}):
            valor = ht._env_int_safe("CSS_MT5_GMT_OFFSET_TESTE", -3)
        self.assertEqual(valor, 0)
        print("[✓] _env_int_safe() aceita um inteiro válido normalmente")

    def test_env_int_safe_warns_but_accepts_value_outside_range(self):
        """Achado em revisão (mfc-rev-2 + Codex, herdr-review rodadas 8/9,
        P3-2/F09-02): um valor que CASTA mas é absurdo pra fuso horário real
        (ex.: 99) passava sem nenhum aviso, deslocando ENTRY_SERVER_HOUR em
        silêncio. Não é caminho de execução ao vivo — avisa, não recusa."""
        buf = io.StringIO()
        with patch.dict(os.environ, {"CSS_MT5_GMT_OFFSET_TESTE": "99"}), \
             redirect_stdout(buf):
            valor = ht._env_int_safe("CSS_MT5_GMT_OFFSET_TESTE", -3, lo=-12, hi=14)
        self.assertEqual(valor, 99, "fora do caminho de execução, o valor é usado mesmo assim")
        self.assertIn("fora da faixa esperada", buf.getvalue())
        print("[✓] _env_int_safe() avisa quando o valor castado está fora da faixa "
              "esperada, mas continua usando (não é um gate fail-closed)")

    def test_gmt_offset_module_constant_uses_the_real_timezone_range(self):
        """A ligação real (`GMT_OFFSET = _env_int_safe(...)`) precisa passar
        lo=-12, hi=14 — senão a checagem de faixa acima nunca dispara na
        prática. Mesmo padrão de subprocesso do teste de crash-no-import
        acima, pra não repetir o erro de só testar a função isolada."""
        env = dict(os.environ)
        env["CSS_MT5_GMT_OFFSET"] = "99"
        result = subprocess.run(
            [sys.executable, "-c", "import web.history_tracker as ht; print(ht.GMT_OFFSET)"],
            cwd=pe.BASE_DIR, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0)
        self.assertIn("fora da faixa esperada", result.stdout)
        self.assertIn("99", result.stdout)
        print("[✓] A ligação real de GMT_OFFSET usa a faixa de fuso horário, não só a função")

    def test_env_int_safe_falls_back_when_absent(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CSS_MT5_GMT_OFFSET_TESTE", None)
            valor = ht._env_int_safe("CSS_MT5_GMT_OFFSET_TESTE", -3)
        self.assertEqual(valor, -3)
        print("[✓] Variável ausente usa o default, sem erro")

    def test_gmt_offset_import_survives_poisoned_env_var(self):
        """A REGRESSÃO CENTRAL do P2-1 (mfc-rev-2, herdr-review rodada 8) e
        F08-03 (Codex): os três testes acima chamam _env_int_safe() direto —
        provam a FUNÇÃO, não a LIGAÇÃO `GMT_OFFSET = _env_int_safe(...)`, que
        é onde o bug original morava. Revertendo só essa linha pro int() cru,
        a suíte inteira continuava verde (185 passed) porque nenhum teste
        importava o módulo de verdade com a env var envenenada. Um
        subprocesso, sem mock nenhum, prova a ligação real: import de
        web.history_tracker com CSS_MT5_GMT_OFFSET='abc' não pode derrubar o
        processo — mesmo padrão de bug que já voltou duas vezes neste
        trabalho (backtest rodada 3→4, _tick_valido rodada 6→7): função
        extraída ganha teste, ligação não."""
        env = dict(os.environ)
        env["CSS_MT5_GMT_OFFSET"] = "abc"
        result = subprocess.run(
            [sys.executable, "-c", "import web.history_tracker as ht; "
                                    "print(ht.GMT_OFFSET, ht.ENTRY_SERVER_HOUR)"],
            cwd=pe.BASE_DIR, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0,
                         f"import de web.history_tracker não pode falhar com "
                         f"CSS_MT5_GMT_OFFSET inválido — stderr: {result.stderr}")
        self.assertIn("-3 0", result.stdout,
                      "GMT_OFFSET precisa cair no default (-3) quando o valor é inválido")
        print("[✓] Import real de web.history_tracker sobrevive a CSS_MT5_GMT_OFFSET "
              "envenenado — a ligação está protegida, não só a função")


class TestDotenvLoader(unittest.TestCase):
    """_load_dotenv_if_present vive em web/css_service.py (módulo mais cedo
    importado — cobre MT5_PATH e qualquer variável lida por
    agents/portfolio_executor.py, que importa css_service antes de ler as
    suas próprias). Nunca toca no .env real do projeto nos testes — sempre
    recebe um caminho explícito de arquivo temporário."""

    def test_dotenv_does_not_override_existing_env_var(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = os.path.join(tmp, ".env")
            with open(env_path, "w", encoding="utf-8") as f:
                f.write("# comentário\n\nCSS_TEST_DOTENV_KEY=from_file\n")
            with patch.dict(os.environ, {"CSS_TEST_DOTENV_KEY": "from_real_env"}):
                cs._load_dotenv_if_present(env_path)
                self.assertEqual(os.environ["CSS_TEST_DOTENV_KEY"], "from_real_env")
        print("[✓] .env nunca sobrescreve variável já definida no ambiente real")

    def test_dotenv_sets_unset_var_and_strips_quotes(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = os.path.join(tmp, ".env")
            with open(env_path, "w", encoding="utf-8") as f:
                f.write('CSS_TEST_DOTENV_KEY2="quoted value"\n')
            os.environ.pop("CSS_TEST_DOTENV_KEY2", None)
            try:
                cs._load_dotenv_if_present(env_path)
                self.assertEqual(os.environ["CSS_TEST_DOTENV_KEY2"], "quoted value")
            finally:
                os.environ.pop("CSS_TEST_DOTENV_KEY2", None)
        print("[✓] .env preenche variável ausente e remove aspas")


class TestIdempotency(unittest.TestCase):
    def test_refuses_to_reopen_basket_already_open(self):
        fake_mt5 = make_fake_mt5()
        fake_mt5.account_info.return_value = SimpleNamespace(
            login=999, server="Broker-Demo", trade_mode="DEMO",
            trade_allowed=True, margin_mode="HEDGING"
        )
        # Já existe posição sob o magic do CAD (801007).
        fake_mt5.positions_get.return_value = [
            SimpleNamespace(magic=pe.PORTFOLIO_MAGICS["CAD"], symbol="CADCHF")
        ]
        with patch.dict(os.environ, DEMO_GATE_ENV), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe.open_portfolio_basket("CAD", "BUY")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "already_open")
        fake_mt5.order_send.assert_not_called()
        print("[✓] Idempotência: não reabre cesta já aberta pro mesmo magic")


class TestNettingCollision(unittest.TestCase):
    def setUp(self):
        # Isola _FAMILY_STATE_FILE num tmpdir — dois testes desta classe
        # exercitam _detect_mt5_symbol_family() (o aquecimento antes da
        # checagem de colisão), e sem isso vazariam
        # data/mt5_symbol_family.json DE VERDADE no repositório (bug real,
        # encontrado rodando esta suíte antes deste isolamento existir).
        self._tmp_family_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp_family_dir.cleanup)
        patcher = patch.object(
            cs, "_FAMILY_STATE_FILE",
            os.path.join(self._tmp_family_dir.name, "mt5_symbol_family.json"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_refuses_open_on_symbol_collision_in_netting_mode(self):
        fake_mt5 = make_fake_mt5()
        fake_mt5.account_info.return_value = SimpleNamespace(
            login=999, server="Broker-Demo", trade_mode="DEMO",
            trade_allowed=True, margin_mode="NETTING"
        )
        # Cesta USD (magic diferente do CAD) já tem USDCAD aberto — colide
        # com um dos 7 pares que a cesta CAD abriria.
        fake_mt5.positions_get.return_value = [
            SimpleNamespace(magic=pe.PORTFOLIO_MAGICS["USD"], symbol="USDCAD")
        ]
        with patch.dict(os.environ, DEMO_GATE_ENV), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe.open_portfolio_basket("CAD", "BUY")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "netting_symbol_collision")
        fake_mt5.order_send.assert_not_called()
        print("[✓] Conta netting recusa cesta que colide em símbolo com outra já aberta")

    def test_allows_open_on_symbol_collision_in_hedging_mode(self):
        """A mesma colisão de símbolo, em conta hedging, não deve bloquear —
        hedging permite posições independentes por magic no mesmo símbolo."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.account_info.return_value = SimpleNamespace(
            login=999, server="Broker-Demo", trade_mode="DEMO",
            trade_allowed=True, margin_mode="HEDGING"
        )
        fake_mt5.positions_get.return_value = [
            SimpleNamespace(magic=pe.PORTFOLIO_MAGICS["USD"], symbol="USDCAD")
        ]
        fake_mt5.symbol_info.return_value = SimpleNamespace(visible=True, trade_mode="FULL")
        fake_mt5.symbol_info_tick.return_value = SimpleNamespace(ask=1.1000, bid=1.0998)
        fake_mt5.order_send.return_value = SimpleNamespace(
            retcode=fake_mt5.TRADE_RETCODE_DONE, order=1, price=1.1000, comment="ok"
        )
        with patch.dict(os.environ, DEMO_GATE_ENV), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe.open_portfolio_basket("CAD", "BUY")
        self.assertTrue(fake_mt5.order_send.called)
        print("[✓] Conta hedging permite colisão de símbolo entre cestas diferentes")

    def test_refuses_open_on_symbol_collision_when_margin_mode_is_unknown(self):
        """A REGRESSÃO CENTRAL do achado em revisão (Codex, achado 2/4
        rodada 4, decisão do usuário): antes, a checagem de colisão só
        rodava com margin_mode == "netting" — um valor "desconhecido" (nem
        NETTING nem HEDGING; get_account_safety_info() cai nesse estado
        quando o campo falta, uma exceção ocorre, ou o MT5 devolve um
        terceiro valor real nunca mapeado, ex.: ACCOUNT_MARGIN_MODE_EXCHANGE)
        pulava a checagem em SILÊNCIO, tratado como se fosse hedging. Se a
        conta FOSSE netting de verdade mas classificada errado, a cesta
        podia se fundir com uma posição já aberta sem essa proteção nunca
        rodar. Simula exatamente esse estado: margin_mode que não bate com
        nenhuma das duas constantes conhecidas."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.account_info.return_value = SimpleNamespace(
            login=999, server="Broker-Demo", trade_mode="DEMO",
            trade_allowed=True, margin_mode="ALGUM_MODO_NAO_MAPEADO"
        )
        fake_mt5.positions_get.return_value = [
            SimpleNamespace(magic=pe.PORTFOLIO_MAGICS["USD"], symbol="USDCAD")
        ]
        with patch.dict(os.environ, DEMO_GATE_ENV), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe.open_portfolio_basket("CAD", "BUY")
        self.assertFalse(result["success"],
                         "margin_mode desconhecido não pode pular a checagem de colisão")
        self.assertEqual(result["error"], "netting_symbol_collision")
        fake_mt5.order_send.assert_not_called()
        print("[✓] margin_mode desconhecido roda a checagem de colisão — não pula em silêncio, "
              "só pula quando SABE que é hedging")

    def test_collision_refusal_message_reflects_real_margin_mode_not_hardcoded_netting(self):
        """Achado em revisão (mfc-rev-2, achado 2/4 rodada 5): a mensagem de
        recusa dizia literalmente "Conta em modo netting: ..." mesmo quando
        margin_mode era "desconhecido" (ou qualquer outro valor não-hedging) —
        um operador via 7 recusas afirmando um modo de conta que não é o
        dela, sem pista de que a causa raiz é a classificação do
        margin_mode. A mensagem agora deve citar o valor REAL de
        margin_mode, não a string fixa "netting"."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.account_info.return_value = SimpleNamespace(
            login=999, server="Broker-Demo", trade_mode="DEMO",
            trade_allowed=True, margin_mode="ALGUM_MODO_NAO_MAPEADO"
        )
        fake_mt5.positions_get.return_value = [
            SimpleNamespace(magic=pe.PORTFOLIO_MAGICS["USD"], symbol="USDCAD")
        ]
        with patch.dict(os.environ, DEMO_GATE_ENV), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe.open_portfolio_basket("CAD", "BUY")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "netting_symbol_collision")
        self.assertNotIn("modo netting:", result["message"],
                          "mensagem não pode afirmar 'netting' quando o modo real é outro")
        self.assertIn("desconhecido", result["message"],
                       "mensagem deve citar o margin_mode REAL classificado ('desconhecido'), "
                       "não um valor fixo 'netting'")
        print("[✓] mensagem de recusa cita o margin_mode real, não 'netting' hardcoded")

    def test_symbol_suffix_auto_detection_warms_up_before_netting_check(self):
        """Regressão (achado MÉDIO em revisão): a auto-detecção de sufixo só
        disparava dentro do preflight, DEPOIS da checagem de colisão em
        netting — sem CSS_MT5_SYMBOL_SUFFIX configurado, a comparação de
        símbolo pra colisão rodava com o sufixo ainda não descoberto (posição
        existente "USDCADm" nunca seria reconhecida como colidindo com o par
        lógico "USDCAD"). Agora resolve um par de referência ANTES da
        checagem de colisão, garantindo que a detecção já rodou a tempo."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.account_info.return_value = SimpleNamespace(
            login=999, server="Broker-Demo", trade_mode="DEMO",
            trade_allowed=True, margin_mode="NETTING")
        # Posição existente vem com sufixo "m" — só a auto-detecção
        # descobre isso, já que não há sufixo configurado neste teste.
        fake_mt5.positions_get.return_value = [
            SimpleNamespace(magic=pe.PORTFOLIO_MAGICS["USD"], symbol="USDCADm")
        ]
        fake_mt5.SYMBOL_TRADE_MODE_FULL = "FULL"
        fake_mt5.symbols_get.return_value = [
            SimpleNamespace(name="EURUSDm", visible=True, trade_mode="FULL")
        ]
        # Só o nome COM sufixo resolve — sem isso, symbol_info(nome puro)
        # "confirmaria" antes de precisar da auto-detecção, e o teste não
        # provaria nada de verdade. trade_contract_size PRECISA ser
        # consistente entre os 28: a auto-detecção agora valida família
        # inteira (achado 1), não só o par-sonda — um dublê sem esse campo
        # reprovaria "m" e o teste pararia de provar o que promete.
        fake_mt5.symbol_info.side_effect = (
            lambda sym: SimpleNamespace(visible=True, trade_mode="FULL", trade_contract_size=100000)
            if sym.endswith("m") else None)
        fake_mt5.symbol_info_tick.return_value = SimpleNamespace(ask=1.1, bid=1.0998)

        with patch.dict(os.environ, DEMO_GATE_ENV), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5), \
             patch.object(cs, "MT5_AVAILABLE", True), patch.object(cs, "mt5", fake_mt5), \
             patch.object(cs, "MT5_SYMBOL_SUFFIX", ""), \
             patch.object(cs, "_AUTO_DETECTED_SUFFIX", None), \
             patch.object(cs, "_LAST_FAILED_FAMILY_DETECTION_AT", None), \
             patch.dict(cs._SYMBOL_RESOLUTION_CACHE, {}, clear=True):
            result = pe.open_portfolio_basket("CAD", "BUY")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "netting_symbol_collision")
        print("[✓] Colisão em netting é detectada mesmo sem sufixo configurado — "
              "auto-detecção já rodou a tempo")

    def test_warmup_forces_fresh_detection_even_if_a_previous_attempt_just_failed(self):
        """Achado em revisão (mfc-rev-2, achado 1 rodada 3, medido): o
        cooldown de 15s existe pro caminho quente do dashboard (recalcula a
        cada 3s) — mas a fase de abertura inteira roda em segundos, bem
        dentro dessa janela. Sem forçar uma tentativa fresca no aquecimento,
        uma falha transitória (ex.: MT5 terminando de conectar bem às
        21:05) fica presa no cooldown pelo resto da mesma execução — 0/8
        cestas em vez de até 8/8. Simula exatamente isso: uma falha de
        detecção "recente" (_LAST_FAILED_FAMILY_DETECTION_AT = agora) que,
        sem o reset, bloquearia esta tentativa também."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.account_info.return_value = SimpleNamespace(
            login=999, server="Broker-Demo", trade_mode="DEMO",
            trade_allowed=True, margin_mode="HEDGING")
        fake_mt5.positions_get.return_value = ()
        fake_mt5.SYMBOL_TRADE_MODE_FULL = "FULL"
        fake_mt5.symbols_get.return_value = [
            SimpleNamespace(name="EURUSDm", visible=True, trade_mode="FULL")
        ]
        fake_mt5.symbol_info.side_effect = (
            lambda sym: SimpleNamespace(visible=True, trade_mode="FULL", trade_contract_size=100000)
            if sym.endswith("m") else None)
        fake_mt5.symbol_info_tick.return_value = SimpleNamespace(ask=1.1, bid=1.0998)
        fake_mt5.order_send.return_value = SimpleNamespace(
            retcode=fake_mt5.TRADE_RETCODE_DONE, order=1, price=1.1, comment="ok")

        with patch.dict(os.environ, DEMO_GATE_ENV), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5), \
             patch.object(cs, "MT5_AVAILABLE", True), patch.object(cs, "mt5", fake_mt5), \
             patch.object(cs, "MT5_SYMBOL_SUFFIX", ""), \
             patch.object(cs, "_AUTO_DETECTED_SUFFIX", None), \
             patch.object(cs, "_LAST_FAILED_FAMILY_DETECTION_AT", cs.time.monotonic()), \
             patch.dict(cs._SYMBOL_RESOLUTION_CACHE, {}, clear=True):
            result = pe.open_portfolio_basket("CAD", "BUY")
            # DENTRO do with: patch.dict/patch.object restauram o valor
            # original ao sair — checar fora provaria o estado anterior ao
            # teste, não o que aconteceu durante (o mesmo bug de escopo que
            # os revisores encontraram duas vezes nesta rodada).
            self.assertEqual(cs._AUTO_DETECTED_SUFFIX, "m")
        self.assertTrue(result["success"],
                        f"aquecimento não forçou detecção fresca — resultado: {result}")
        print("[✓] O aquecimento força tentativa fresca mesmo com o cooldown ainda ativo — "
              "uma falha transitória não condena a noite inteira")

    def test_refuses_open_when_position_symbol_cannot_be_normalized(self):
        """Regressão (achado MÉDIO em revisão, rodada 3): o aquecimento acima
        reduz mas não elimina a janela onde a auto-detecção ainda não
        rodou — se a consulta ao servidor falhar bem na hora do aquecimento
        (ex.: falha transitória de IPC), from_broker_symbol() não normaliza a
        posição existente, e a checagem de colisão comparava um símbolo com
        sufixo ("USDCADm") contra um sem sufixo ("USDCAD") sem nunca bater —
        colisão real passaria batido. Agora a checagem valida o dado em si:
        se um símbolo aberto não bate com nenhum dos 28 pares conhecidos,
        recusa por segurança em vez de seguir com uma comparação que sabe
        estar quebrada."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.account_info.return_value = SimpleNamespace(
            login=999, server="Broker-Demo", trade_mode="DEMO",
            trade_allowed=True, margin_mode="NETTING")
        fake_mt5.positions_get.return_value = [
            SimpleNamespace(magic=pe.PORTFOLIO_MAGICS["USD"], symbol="USDCADm")
        ]
        # Falha transitória exatamente na consulta de auto-detecção (dispara
        # dentro do aquecimento) — sem sufixo configurado e sem conseguir
        # descobrir um, from_broker_symbol não tem como normalizar.
        fake_mt5.symbols_get.side_effect = RuntimeError("IPC timeout transitório")
        fake_mt5.symbol_info.return_value = None  # nome puro nunca resolve

        with patch.dict(os.environ, DEMO_GATE_ENV), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5), \
             patch.object(cs, "MT5_AVAILABLE", True), patch.object(cs, "mt5", fake_mt5), \
             patch.object(cs, "MT5_SYMBOL_SUFFIX", ""), \
             patch.object(cs, "_AUTO_DETECTED_SUFFIX", None), \
             patch.object(cs, "_LAST_FAILED_FAMILY_DETECTION_AT", None), \
             patch.dict(cs._SYMBOL_RESOLUTION_CACHE, {}, clear=True):
            result = pe.open_portfolio_basket("CAD", "BUY")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "symbol_resolution_unreliable")
        fake_mt5.order_send.assert_not_called()
        print("[✓] Símbolo aberto não reconhecido (resolução falhou) recusa por segurança, "
              "em vez de arriscar colisão não detectada")


class TestPositionQueryFailsClosed(unittest.TestCase):
    """positions_get() devolve None tanto pra 'nenhuma posição' quanto pra
    ERRO de consulta. Tratar erro como 'nada aberto' derruba a idempotência e
    a checagem de colisão justamente quando o terminal está instável — que é
    quando elas mais importam."""

    def _demo_mt5(self):
        fake = make_fake_mt5()
        fake.account_info.return_value = SimpleNamespace(
            login=999, server="Broker-Demo", trade_mode="DEMO",
            trade_allowed=True, margin_mode="HEDGING"
        )
        return fake

    def test_open_refused_when_positions_get_returns_none(self):
        fake_mt5 = self._demo_mt5()
        fake_mt5.positions_get.return_value = None
        fake_mt5.last_error.return_value = (-10004, "IPC timeout")
        with patch.dict(os.environ, DEMO_GATE_ENV), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe.open_portfolio_basket("CAD", "BUY")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "position_query_failed")
        fake_mt5.order_send.assert_not_called()
        print("[✓] positions_get()->None recusa abertura (não reabre cesta por cima)")

    def test_open_refused_when_positions_get_raises(self):
        fake_mt5 = self._demo_mt5()
        fake_mt5.positions_get.side_effect = RuntimeError("conexão perdida")
        with patch.dict(os.environ, DEMO_GATE_ENV), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe.open_portfolio_basket("CAD", "BUY")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "position_query_failed")
        fake_mt5.order_send.assert_not_called()
        print("[✓] positions_get() lançando exceção recusa abertura")

    def test_empty_tuple_is_not_an_error(self):
        """Tupla vazia é 'nenhuma posição' de verdade — deve seguir normal."""
        fake_mt5 = self._demo_mt5()
        fake_mt5.positions_get.return_value = ()
        fake_mt5.symbol_info.return_value = SimpleNamespace(visible=True, trade_mode="FULL")
        fake_mt5.symbol_info_tick.return_value = SimpleNamespace(ask=1.1, bid=1.0998)
        fake_mt5.order_send.return_value = SimpleNamespace(
            retcode=fake_mt5.TRADE_RETCODE_DONE, order=1, price=1.1, comment="ok")
        with patch.dict(os.environ, DEMO_GATE_ENV), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe.open_portfolio_basket("CAD", "BUY")
        self.assertTrue(result["success"])
        print("[✓] Tupla vazia (sem posições) segue normalmente — só None/exceção recusa")

    def test_close_all_reports_failure_when_query_fails(self):
        """O pior modo de falha do fechamento: reportar 'fechou tudo' com a
        cesta viva."""
        fake_mt5 = self._demo_mt5()
        fake_mt5.positions_get.return_value = None
        fake_mt5.last_error.return_value = (-10004, "IPC timeout")
        with patch.dict(os.environ, DEMO_GATE_ENV), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5), \
             patch.object(pe, "ensure_mt5", lambda: True):
            result = pe.close_all_portfolios()
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "position_query_failed")
        print("[✓] Fechamento com consulta falhando reporta ERRO, não sucesso falso")


class TestSymbolResolution(unittest.TestCase):
    """Medido na conta real (Exness): 0 dos 28 pares existem com o nome puro,
    28 existem como 'EURUSDm' etc. Sem resolução de sufixo o sistema não abre
    uma perna sequer."""

    def setUp(self):
        # _FAMILY_STATE_FILE isolado num tmpdir por teste — sem isso, os
        # testes leriam/escreveriam data/mt5_symbol_family.json DE VERDADE
        # (bug real encontrado rodando esta suíte: vazou um arquivo real no
        # repositório antes deste isolamento existir).
        self._tmp_family_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp_family_dir.cleanup)
        patcher = patch.object(
            cs, "_FAMILY_STATE_FILE",
            os.path.join(self._tmp_family_dir.name, "mt5_symbol_family.json"))
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _mixed_family_symbol_info(mixed_pairs, mixed_size=1000, standard_size=100000):
        """Fake de symbol_info() pra uma corretora que lista DUAS séries: os
        pares em `mixed_pairs` só existem com sufixo "m" (contrato micro);
        os demais dos 28 só existem SEM sufixo (contrato padrão). Nenhum par
        existe nas duas séries ao mesmo tempo — reproduz o cenário real do
        achado 1: um sufixo cobre uma FATIA dos 28, não todos."""
        def info_for(sym):
            for pair in mixed_pairs:
                if sym == pair + "m":
                    return SimpleNamespace(trade_mode="FULL", trade_contract_size=mixed_size)
                if sym == pair:
                    return None
            for pair in cs.ALL_28_PAIRS:
                if pair in mixed_pairs:
                    continue
                if sym == pair:
                    return SimpleNamespace(trade_mode="FULL", trade_contract_size=standard_size)
                if sym == pair + "m":
                    return None
            return None
        return info_for

    @staticmethod
    def _uniform_family_symbol_info(suffix, contract_size=1000):
        """Fake de symbol_info() pra uma corretora onde os 28 pares existem
        TODOS com o mesmo sufixo (ou sem sufixo, se suffix="") e o MESMO
        trade_contract_size — a família boa, que deve ser aceita."""
        def info_for(sym):
            for pair in cs.ALL_28_PAIRS:
                if sym == pair + suffix:
                    return SimpleNamespace(trade_mode="FULL", trade_contract_size=contract_size)
            return None
        return info_for

    def test_to_broker_symbol_applies_suffix(self):
        with patch.object(cs, "MT5_SYMBOL_SUFFIX", "m"), \
             patch.object(cs, "MT5_AVAILABLE", False), \
             patch.dict(cs._SYMBOL_RESOLUTION_CACHE, {}, clear=True):
            self.assertEqual(cs.to_broker_symbol("EURUSD"), "EURUSDm")

    def test_configured_suffix_does_not_fall_back_to_bare_per_pair(self):
        """Mudança de escopo desta rodada: o sufixo CONFIGURADO tinha o
        MESMO defeito que o auto-detectado — se um par específico não
        existisse com ele, caía pro nome puro só PRA ESSE par, o que é a
        mesma classe de mistura de família que o achado 1 fecha do lado
        automático. Configuração explícita do operador continua tendo
        precedência absoluta (não é revalidada contra os 28 pares — isso é
        confiança no operador, não bug), mas não muda de família por par."""
        fake_mt5 = make_fake_mt5()

        def info_for(sym):
            if sym == "EURUSDm":
                return None                      # não existe com o sufixo configurado
            if sym == "EURUSD":
                return SimpleNamespace(trade_mode="FULL", trade_contract_size=100000)
            return None

        fake_mt5.symbol_info.side_effect = info_for
        with patch.object(cs, "MT5_SYMBOL_SUFFIX", "m"), \
             patch.object(cs, "MT5_AVAILABLE", True), patch.object(cs, "mt5", fake_mt5), \
             patch.dict(cs._SYMBOL_RESOLUTION_CACHE, {}, clear=True):
            result = cs.to_broker_symbol("EURUSD")
        self.assertEqual(result, "EURUSDm",
                         "não pode cair pro nome puro só porque EURUSDm não resolveu")
        self.assertNotIn("EURUSD", cs._SYMBOL_RESOLUTION_CACHE,
                         "resolução não confirmada não pode ser cacheada")
        print("[✓] Sufixo configurado nunca cai pro nome puro por par — fica não confirmado")

    def test_from_broker_symbol_strips_suffix(self):
        with patch.object(cs, "MT5_SYMBOL_SUFFIX", "m"):
            self.assertEqual(cs.from_broker_symbol("EURUSDm"), "EURUSD")
            self.assertEqual(cs.from_broker_symbol("EURUSD"), "EURUSD")

    def test_mixed_family_is_rejected_even_though_every_leg_individually_resolves(self):
        """A REGRESSÃO CENTRAL do achado 1 (reproduzida por mfc-rev-2 numa
        cópia fora do repo, achado real em c24a44c): sem CSS_MT5_SYMBOL_SUFFIX
        configurado, EURUSD (que existe com sufixo "m", contract_size=1000,
        junto com 26 outros pares) resolvia pra "m" via auto-detecção,
        enquanto CADCHF (que só existe SEM sufixo, contract_size=100000)
        resolvia pro nome puro — mesma chamada, duas séries com nocional
        100x diferente, ambas FULL. Fixture fiel ao cenário: "m" APARECE
        como candidato (symbols_get devolve EURUSDm), mas falha a validação
        de família porque CADCHFm não existe — então nem "m" nem o nome puro
        (que nunca chega a ser tentado pra CADCHF, ver o teste de baixo)
        ficam confirmados."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.SYMBOL_TRADE_MODE_FULL = "FULL"
        # "m" é o único candidato que a busca descobre — CADCHF bare nunca
        # aparece aqui, porque a busca só varre "*EURUSD*".
        fake_mt5.symbols_get.return_value = [
            SimpleNamespace(name="EURUSDm", trade_mode="FULL"),
        ]
        fake_mt5.symbol_info.side_effect = self._mixed_family_symbol_info(
            mixed_pairs=[p for p in cs.ALL_28_PAIRS if p != "CADCHF"])
        with patch.object(cs, "MT5_SYMBOL_SUFFIX", ""), \
             patch.object(cs, "MT5_AVAILABLE", True), patch.object(cs, "mt5", fake_mt5), \
             patch.object(cs, "_AUTO_DETECTED_SUFFIX", None), \
             patch.object(cs, "_LAST_FAILED_FAMILY_DETECTION_AT", None), \
             patch.dict(cs._SYMBOL_RESOLUTION_CACHE, {}, clear=True):
            eurusd = cs.to_broker_symbol("EURUSD")
            cadchf = cs.to_broker_symbol("CADCHF")
            gbpusd = cs.to_broker_symbol("GBPUSD")
            self.assertIsNone(cs._AUTO_DETECTED_SUFFIX,
                              "'m' cobre 27 dos 28 — não pode virar família só por isso")
            # Achado em revisão (mfc-rev-2, achado 1 rodada 2): as três
            # asserções de cache abaixo ficavam FORA deste "with" na versão
            # anterior — patch.dict(..., clear=True) restaura o dict ORIGINAL
            # ao sair do bloco, então checar depois provava o estado
            # anterior ao teste, não o que aconteceu durante. O teste
            # continuava passando contra o código com o defeito, porque não
            # olhava pro lugar certo. Movido pra dentro.
            self.assertNotIn("EURUSD", cs._SYMBOL_RESOLUTION_CACHE)
            self.assertNotIn("CADCHF", cs._SYMBOL_RESOLUTION_CACHE)
            self.assertNotIn("GBPUSD", cs._SYMBOL_RESOLUTION_CACHE)
        # Igualdade exata, não assertNotIn: com o marcador de família não
        # resolvida (_UNRESOLVED_FAMILY_MARKER), o valor certo é conhecido —
        # checar o valor exato é mais forte que só excluir os dois errados.
        self.assertEqual(eurusd, "EURUSD" + cs._UNRESOLVED_FAMILY_MARKER)
        self.assertEqual(cadchf, "CADCHF" + cs._UNRESOLVED_FAMILY_MARKER)
        self.assertEqual(gbpusd, "GBPUSD" + cs._UNRESOLVED_FAMILY_MARKER)
        print("[✓] Família com nocional inconsistente entre pares é rejeitada por inteiro — "
              "nenhuma perna resolve, mesmo as que existem sozinhas")

    def test_unresolved_family_fallback_cannot_be_mistaken_for_a_real_symbol(self):
        """A LACUNA que a rodada anterior de revisão achou (Codex, achado 1):
        o teste acima prova que to_broker_symbol() não CONFIRMA nada quando a
        família falha — mas o preflight em portfolio_executor.py não confia
        no cache desta função, ele CHAMA mt5.symbol_info() de novo por conta
        própria. Se o valor devolvido aqui por falta de família (o nome
        puro) acontecer de EXISTIR no servidor — que é exatamente o caso de
        CADCHF neste fixture —, essa segunda chamada do preflight aceitava
        mesmo assim, sem saber que era só um palpite não confirmado.

        Prova as duas pontas: (1) to_broker_symbol NÃO devolve o nome puro
        quando o MT5 está disponível pra checar; (2) o valor que ELE devolve,
        re-consultado exatamente como o preflight faz, continua None — a
        cesta seria recusada pelo caminho já testado, não aceita por
        coincidência."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.SYMBOL_TRADE_MODE_FULL = "FULL"
        fake_mt5.symbols_get.return_value = [SimpleNamespace(name="EURUSDm", trade_mode="FULL")]
        fake_mt5.symbol_info.side_effect = self._mixed_family_symbol_info(
            mixed_pairs=[p for p in cs.ALL_28_PAIRS if p != "CADCHF"])
        with patch.object(cs, "MT5_SYMBOL_SUFFIX", ""), \
             patch.object(cs, "MT5_AVAILABLE", True), patch.object(cs, "mt5", fake_mt5), \
             patch.object(cs, "_AUTO_DETECTED_SUFFIX", None), \
             patch.object(cs, "_LAST_FAILED_FAMILY_DETECTION_AT", None), \
             patch.dict(cs._SYMBOL_RESOLUTION_CACHE, {}, clear=True):
            cadchf = cs.to_broker_symbol("CADCHF")
            self.assertNotEqual(cadchf, "CADCHF",
                                "não pode devolver o nome puro quando dá pra confirmar no MT5 — "
                                "CADCHF bare EXISTE de verdade neste fixture, e aceitá-lo é a "
                                "lacuna que este teste existe pra fechar")
            # Exatamente o que o preflight faz: revalida o retorno por conta
            # própria, sem olhar pro cache desta função.
            reconfirmado = fake_mt5.symbol_info(cadchf)
        self.assertIsNone(reconfirmado,
                          "o nome devolvido pra família indeterminada tem que continuar "
                          "'não resolvido' quando o preflight o revalida — nunca pode ser um "
                          "símbolo que por acaso existe numa família diferente da decidida")
        print("[✓] Fallback de família indeterminada nunca é confundível com um símbolo real — "
              "o preflight não consegue aceitá-lo por coincidência")

    def test_uniform_bare_family_is_accepted_and_shared_by_all_pairs(self):
        """O caso simples continua funcionando: se os 28 pares existirem
        TODOS sem sufixo e com o mesmo contrato, essa família bare (suffix
        "") é aceita — e usada por igual pra qualquer par, não descoberta de
        novo par a par."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.SYMBOL_TRADE_MODE_FULL = "FULL"
        fake_mt5.symbols_get.return_value = [SimpleNamespace(name="EURUSD", trade_mode="FULL")]
        fake_mt5.symbol_info.side_effect = self._uniform_family_symbol_info("", contract_size=100000)
        with patch.object(cs, "MT5_SYMBOL_SUFFIX", ""), \
             patch.object(cs, "MT5_AVAILABLE", True), patch.object(cs, "mt5", fake_mt5), \
             patch.object(cs, "_AUTO_DETECTED_SUFFIX", None), \
             patch.object(cs, "_LAST_FAILED_FAMILY_DETECTION_AT", None), \
             patch.dict(cs._SYMBOL_RESOLUTION_CACHE, {}, clear=True):
            eurusd = cs.to_broker_symbol("EURUSD")
            cadjpy = cs.to_broker_symbol("CADJPY")
            self.assertEqual(cs._AUTO_DETECTED_SUFFIX, "",
                             "família bare precisa ficar memorizada como '' (não None)")
        self.assertEqual((eurusd, cadjpy), ("EURUSD", "CADJPY"))
        print("[✓] Família bare consistente nos 28 pares é aceita e compartilhada por todos")

    def test_reads_family_persisted_by_another_process_without_requerying(self):
        """A REGRESSÃO CENTRAL da coordenação entre processos (achado em
        revisão: Codex, achado 1 rodada 3; decisão do usuário). Daemon e web
        server são processos separados, cada um com seu próprio
        _AUTO_DETECTED_SUFFIX em memória — sem persistência, cada um podia
        validar uma família diferente se o servidor devolvesse candidatos em
        ordens diferentes pra cada conexão (o dict.fromkeys resolve
        determinismo DENTRO de um processo, não coordenação ENTRE
        processos). Simula: outro processo já validou e gravou 'm'; ESTE
        processo, sem família em memória, lê o arquivo em vez de
        redescobrir do zero."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.SYMBOL_TRADE_MODE_FULL = "FULL"
        fake_mt5.symbol_info.side_effect = self._uniform_family_symbol_info("m")
        cs._persist_family("m")  # simula outro processo já tendo validado
        with patch.object(cs, "MT5_SYMBOL_SUFFIX", ""), \
             patch.object(cs, "MT5_AVAILABLE", True), patch.object(cs, "mt5", fake_mt5), \
             patch.object(cs, "_AUTO_DETECTED_SUFFIX", None), \
             patch.object(cs, "_LAST_FAILED_FAMILY_DETECTION_AT", None):
            result = cs._detect_mt5_symbol_family()
        self.assertEqual(result, "m")
        fake_mt5.symbols_get.assert_not_called()
        print("[✓] Família persistida por outro processo é lida e revalidada, sem redescobrir")

    def test_ignores_persisted_family_that_no_longer_validates(self):
        """A persistência nunca é confiada cegamente — sempre revalidada
        contra os 28 pares ANTES de ser adotada. Um arquivo de uma corretora
        antiga (ou editado à mão) que não bate mais com o servidor tem que
        ser ignorado, não travar a detecção nem virar família adotada por
        engano."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.SYMBOL_TRADE_MODE_FULL = "FULL"
        fake_mt5.symbols_get.return_value = [SimpleNamespace(name="EURUSDpro", trade_mode="FULL")]
        fake_mt5.symbol_info.side_effect = self._uniform_family_symbol_info("pro")
        cs._persist_family("m")  # "m" não existe mais nesta corretora (só "pro")
        with patch.object(cs, "MT5_SYMBOL_SUFFIX", ""), \
             patch.object(cs, "MT5_AVAILABLE", True), patch.object(cs, "mt5", fake_mt5), \
             patch.object(cs, "_AUTO_DETECTED_SUFFIX", None), \
             patch.object(cs, "_LAST_FAILED_FAMILY_DETECTION_AT", None):
            result = cs._detect_mt5_symbol_family()
        self.assertEqual(result, "pro",
                         "família persistida obsoleta tem que ser ignorada, não travar a detecção")
        fake_mt5.symbols_get.assert_called_once()
        print("[✓] Família persistida que não valida mais é ignorada — descobre de novo, não trava")

    def test_persisted_family_failure_arms_the_cooldown(self):
        """Achado em revisão (mfc-rev-2, rodada 4, medido): a versão anterior
        lia e revalidava o arquivo persistido ANTES do cooldown — um arquivo
        presente mas obsoleto reintroduzia a MESMA tempestade de IPC que o
        cooldown existe pra evitar (~3948 chamadas MT5/ciclo medidas). Aqui,
        nem o arquivo persistido nem a descoberta normal encontram família
        válida — família continua None depois da 1ª chamada, então uma
        segunda chamada logo em seguida precisa respeitar o cooldown em vez
        de revalidar o arquivo obsoleto de novo."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.symbol_info.return_value = None  # "m" não existe mais em pair nenhum
        fake_mt5.symbols_get.return_value = []  # descoberta normal também não acha nada
        with patch.object(cs, "mt5", fake_mt5):
            cs._persist_family("m")  # não existe mais nesta corretora
        with patch.object(cs, "MT5_SYMBOL_SUFFIX", ""), \
             patch.object(cs, "MT5_AVAILABLE", True), patch.object(cs, "mt5", fake_mt5), \
             patch.object(cs, "_AUTO_DETECTED_SUFFIX", None), \
             patch.object(cs, "_LAST_FAILED_FAMILY_DETECTION_AT", None):
            first = cs._detect_mt5_symbol_family()
            second = cs._detect_mt5_symbol_family()
        self.assertIsNone(first, "arquivo obsoleto não pode virar família sozinho")
        self.assertIsNone(second)
        self.assertEqual(fake_mt5.symbols_get.call_count, 1,
                         "segunda chamada dentro do cooldown não pode reconsultar, mesmo com "
                         "um arquivo persistido (e obsoleto) no caminho")
        print("[✓] Arquivo persistido que reprova arma o cooldown — não vira brecha pra "
              "reconsultar a cada chamada")

    def test_persisted_family_from_a_different_account_is_rejected(self):
        """Achado em revisão (Codex/mfc-rev-2, rodada 4): validar só o
        nocional não basta — se o MESMO checkout (mesmo data/) for
        reaproveitado entre contas/corretoras diferentes, e a família antiga
        por coincidência também for válida (mas não a pretendida) na conta
        nova, ela passaria despercebida. Identidade de conta CONHECIDA e
        DIFERENTE tem que reprovar, mesmo que o nocional bateria."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.SYMBOL_TRADE_MODE_FULL = "FULL"
        fake_mt5.account_info.return_value = SimpleNamespace(login=111, server="BrokerA-Demo")
        fake_mt5.symbols_get.return_value = [SimpleNamespace(name="EURUSDm", trade_mode="FULL")]
        fake_mt5.symbol_info.side_effect = self._uniform_family_symbol_info("m")
        with patch.object(cs, "mt5", fake_mt5):
            cs._persist_family("m")  # grava com login=111 (conta atual do fake acima)

        # Troca de conta: mesmo sufixo 'm' AINDA validaria (coincidência),
        # mas a conta agora é outra.
        fake_mt5.account_info.return_value = SimpleNamespace(login=222, server="BrokerB-Live")
        with patch.object(cs, "MT5_SYMBOL_SUFFIX", ""), \
             patch.object(cs, "MT5_AVAILABLE", True), patch.object(cs, "mt5", fake_mt5), \
             patch.object(cs, "_AUTO_DETECTED_SUFFIX", None), \
             patch.object(cs, "_LAST_FAILED_FAMILY_DETECTION_AT", None):
            result = cs._detect_mt5_symbol_family()
        self.assertEqual(result, "m",
                         "descobre de novo (não herda o arquivo da conta antiga) e chega no "
                         "mesmo valor por conta própria — o que prova a rejeição é o "
                         "symbols_get sendo chamado, não o resultado final")
        fake_mt5.symbols_get.assert_called_once()
        print("[✓] Família persistida por outra conta é rejeitada mesmo quando o nocional "
              "coincidentemente bateria — descobre de novo em vez de herdar")

    def test_reread_after_persist_adopts_the_winner_of_a_concurrent_write(self):
        """Achado em revisão (mfc-rev-2, rodada 4, reproduzido com
        subprocessos reais: 3 de 5 execuções divergiram sem esta mitigação).
        Simula a corrida: ESTE processo valida 'm' sozinho, mas entre a
        escrita e a releitura, outro processo já escreveu 'z' por cima —
        este processo tem que adotar 'z' (o que está no disco), não 'm' (o
        que ele mesmo calculou), pra convergir com quem quer que tenha
        vencido a corrida."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.SYMBOL_TRADE_MODE_FULL = "FULL"
        fake_mt5.symbols_get.return_value = [SimpleNamespace(name="EURUSDm", trade_mode="FULL")]

        def info_for(sym):
            for pair in cs.ALL_28_PAIRS:
                if sym == pair + "m":
                    return SimpleNamespace(trade_mode="FULL", trade_contract_size=1000)
                if sym == pair + "z":
                    return SimpleNamespace(trade_mode="FULL", trade_contract_size=999999)
            return None
        fake_mt5.symbol_info.side_effect = info_for

        real_persist = cs._persist_family

        def persist_then_let_other_process_win(suffix):
            real_persist(suffix)
            real_persist("z")  # "outro processo" escreve por cima, bem aqui —
            # chama a função REAL, não cs._persist_family (que está mockada
            # neste teste): usar a mockada aqui recursaria nela mesma.

        with patch.object(cs, "MT5_SYMBOL_SUFFIX", ""), \
             patch.object(cs, "MT5_AVAILABLE", True), patch.object(cs, "mt5", fake_mt5), \
             patch.object(cs, "_AUTO_DETECTED_SUFFIX", None), \
             patch.object(cs, "_LAST_FAILED_FAMILY_DETECTION_AT", None), \
             patch.object(cs, "_persist_family", side_effect=persist_then_let_other_process_win):
            result = cs._detect_mt5_symbol_family()
        self.assertEqual(result, "z",
                         "tem que adotar o que está no disco (o vencedor da corrida), não o "
                         "que este processo mesmo calculou")
        print("[✓] Reread-after-write adota o vencedor de uma escrita concorrente, não o "
              "próprio valor calculado")

    def test_reading_a_corrupted_persisted_file_never_raises(self):
        """Achado em revisão (mfc-rev-2, rodada 4, reproduzido): a versão
        anterior só capturava (OSError, json.JSONDecodeError). Um arquivo
        com bytes que não decodificam como UTF-8 lança UnicodeDecodeError no
        open() ANTES do json.load — não é um JSONDecodeError (não herda
        dele) — e isso propagava até to_broker_symbol(), derrubando o
        cálculo inteiro numa corretora saudável só por causa de um arquivo
        de CACHE corrompido."""
        with open(cs._FAMILY_STATE_FILE, "wb") as f:
            f.write(b"\xff\xfe\x00\x01garbage-not-utf8")
        try:
            result = cs._read_persisted_family()
        except Exception as e:
            self.fail(f"_read_persisted_family() lançou {type(e).__name__}: {e} — "
                      f"um arquivo de cache corrompido não pode derrubar o chamador")
        self.assertIsNone(result)
        print("[✓] Arquivo persistido com bytes inválidos nunca lança — trata como ausente")

    def test_persisted_family_survives_as_json_readable_by_another_process(self):
        """Prova de ponta a ponta do arquivo em si — não só da função que o
        lê internamente: grava, e um leitor de JSON puro (sem passar por
        nenhuma função deste módulo) consegue ler o mesmo valor."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.SYMBOL_TRADE_MODE_FULL = "FULL"
        fake_mt5.symbols_get.return_value = [SimpleNamespace(name="EURUSDm", trade_mode="FULL")]
        fake_mt5.symbol_info.side_effect = self._uniform_family_symbol_info("m")
        with patch.object(cs, "MT5_AVAILABLE", True), patch.object(cs, "mt5", fake_mt5), \
             patch.object(cs, "_AUTO_DETECTED_SUFFIX", None):
            cs._detect_mt5_symbol_family()
        with open(cs._FAMILY_STATE_FILE, "r", encoding="utf-8") as f:
            on_disk = json.load(f)
        self.assertEqual(on_disk["suffix"], "m")
        print("[✓] Família validada é persistida em disco como JSON legível por outro processo")

    def test_auto_detects_suffix_by_querying_the_server(self):
        """Regressão (pedido do Breno): em vez de exigir CSS_MT5_SYMBOL_SUFFIX
        configurado manualmente pra cada corretora nova, consulta o servidor
        direto. Agora a família também precisa ser válida nos 28 pares —
        "pro" existe pro par-sonda mas não forma família (rejeitada); "m"
        cobre os 28 com contrato consistente."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.SYMBOL_TRADE_MODE_FULL = "FULL"
        fake_mt5.symbols_get.return_value = [
            SimpleNamespace(name="EURUSDpro", trade_mode="FULL"),  # série alternativa, mais longa
            SimpleNamespace(name="EURUSDm", trade_mode="FULL"),    # série padrão, mais curta — essa vence
        ]

        def info_for(sym):
            if sym.endswith("pro"):
                return None if sym != "EURUSDpro" else SimpleNamespace(
                    trade_mode="FULL", trade_contract_size=100000)
            if sym.endswith("m") and sym[:-1] in cs.ALL_28_PAIRS:
                return SimpleNamespace(trade_mode="FULL", trade_contract_size=1000)
            return None

        fake_mt5.symbol_info.side_effect = info_for
        with patch.object(cs, "MT5_AVAILABLE", True), patch.object(cs, "mt5", fake_mt5), \
             patch.object(cs, "_AUTO_DETECTED_SUFFIX", None), \
             patch.object(cs, "_LAST_FAILED_FAMILY_DETECTION_AT", None):
            result = cs._detect_mt5_symbol_family()
        self.assertEqual(result, "m")
        fake_mt5.symbols_get.assert_called_once_with("*EURUSD*")
        print("[✓] Detecção consulta o servidor e adota o candidato mais curto que forma "
              "família válida nos 28 pares — não só o par-sonda")

    def test_auto_detection_excludes_non_full_trade_modes(self):
        """Achado em revisão /dual-r: filtrar só "!= DISABLED" ainda deixava
        passar CLOSEONLY/LONGONLY/SHORTONLY como candidato a sufixo padrão —
        um desses pode rejeitar abertura de alguma perna só depois de outras
        já terem aberto. Agora só aceita trade_mode == FULL no par-sonda."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.SYMBOL_TRADE_MODE_FULL = "FULL"
        fake_mt5.symbols_get.return_value = [
            # Todos mais curtos que o certo, mas nenhum negociável nos dois
            # sentidos — nenhum pode vencer.
            SimpleNamespace(name="EURUSDx", visible=True, trade_mode="DISABLED"),
            SimpleNamespace(name="EURUSDy", visible=True, trade_mode="CLOSEONLY"),
            SimpleNamespace(name="EURUSDw", visible=True, trade_mode="LONGONLY"),
            SimpleNamespace(name="EURUSDv", visible=True, trade_mode="SHORTONLY"),
            # Mais longo, mas negociável nos dois sentidos — este é o certo.
            SimpleNamespace(name="EURUSDpro", visible=True, trade_mode="FULL"),
        ]
        fake_mt5.symbol_info.side_effect = self._uniform_family_symbol_info("pro")
        with patch.object(cs, "MT5_AVAILABLE", True), patch.object(cs, "mt5", fake_mt5), \
             patch.object(cs, "_AUTO_DETECTED_SUFFIX", None), \
             patch.object(cs, "_LAST_FAILED_FAMILY_DETECTION_AT", None):
            result = cs._detect_mt5_symbol_family()
        self.assertEqual(result, "pro")
        print("[✓] Detecção só aceita trade_mode FULL no par-sonda — ignora DISABLED, CLOSEONLY, "
              "LONGONLY e SHORTONLY")

    def test_auto_detection_ignores_market_watch_visibility(self):
        """Achado em revisão /dual-r: "visible" é só estado de UI (Market
        Watch, mutável por symbol_select), não direito de negociar — um
        símbolo FULL mas nunca aberto no Market Watch ainda é negociável.
        Filtrar por "visible" podia REJEITAR o instrumento certo numa conta
        nova só porque ninguém tinha aberto o gráfico dele ainda."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.SYMBOL_TRADE_MODE_FULL = "FULL"
        fake_mt5.symbols_get.return_value = [
            # Mais curto E invisível, mas só fecha posição — uma implementação
            # que aceitasse "qualquer invisível" (em vez de continuar exigindo
            # FULL) escolheria este por engano; ele tem que perder mesmo assim.
            SimpleNamespace(name="EURUSDx", visible=False, trade_mode="CLOSEONLY"),
            # Mais longo e também invisível, mas FULL — este é o certo.
            SimpleNamespace(name="EURUSDm", visible=False, trade_mode="FULL"),
        ]
        fake_mt5.symbol_info.side_effect = self._uniform_family_symbol_info("m")
        with patch.object(cs, "MT5_AVAILABLE", True), patch.object(cs, "mt5", fake_mt5), \
             patch.object(cs, "_AUTO_DETECTED_SUFFIX", None), \
             patch.object(cs, "_LAST_FAILED_FAMILY_DETECTION_AT", None):
            result = cs._detect_mt5_symbol_family()
        self.assertEqual(result, "m")
        print("[✓] Detecção não descarta candidato do par-sonda só por estar invisível no "
              "Market Watch — mas continua exigindo trade_mode FULL mesmo assim")

    def test_auto_detection_result_is_cached_not_requeried(self):
        fake_mt5 = make_fake_mt5()
        fake_mt5.SYMBOL_TRADE_MODE_FULL = "FULL"
        fake_mt5.symbols_get.return_value = [SimpleNamespace(name="EURUSDm", trade_mode="FULL")]
        fake_mt5.symbol_info.side_effect = self._uniform_family_symbol_info("m")
        with patch.object(cs, "MT5_AVAILABLE", True), patch.object(cs, "mt5", fake_mt5), \
             patch.object(cs, "_AUTO_DETECTED_SUFFIX", None), \
             patch.object(cs, "_LAST_FAILED_FAMILY_DETECTION_AT", None):
            cs._detect_mt5_symbol_family()
            cs._detect_mt5_symbol_family()
        self.assertEqual(fake_mt5.symbols_get.call_count, 1)
        print("[✓] Família detectada é memorizada — não reconsulta o servidor de novo")

    def test_auto_detection_does_not_retry_immediately_after_failure(self):
        """Achado em revisão (mfc-rev-2, achado 1 rodada 2, medido): sem
        cooldown, cada chamada de to_broker_symbol() com família
        indeterminada refazia symbols_get() + a validação inteira do zero —
        e to_broker_symbol() é chamado ~140 vezes por ciclo de update_data().
        Numa corretora onde a família nunca fecha, isso virava uma
        tempestade de IPC contra o mesmo canal que envia ordem real. Uma
        segunda chamada imediata (mesmo instante) não pode reconsultar."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.symbols_get.return_value = []  # servidor ainda não respondeu nada útil
        with patch.object(cs, "MT5_AVAILABLE", True), patch.object(cs, "mt5", fake_mt5), \
             patch.object(cs, "_AUTO_DETECTED_SUFFIX", None), \
             patch.object(cs, "_LAST_FAILED_FAMILY_DETECTION_AT", None):
            first = cs._detect_mt5_symbol_family()
            second = cs._detect_mt5_symbol_family()
        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(fake_mt5.symbols_get.call_count, 1,
                         "segunda chamada dentro do cooldown não pode reconsultar o servidor")
        print("[✓] Falha em detectar não dispara reconsulta imediata — respeita o cooldown")

    def test_auto_detection_retries_after_the_cooldown_expires(self):
        """A outra metade da mesma garantia: o cooldown NÃO pode virar
        desistência permanente — depois que o intervalo passa (ex.: o MT5
        finalmente conectou), a próxima chamada tenta de novo."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.symbols_get.return_value = []
        t = [1000.0]
        with patch.object(cs, "MT5_AVAILABLE", True), patch.object(cs, "mt5", fake_mt5), \
             patch.object(cs, "_AUTO_DETECTED_SUFFIX", None), \
             patch.object(cs, "_LAST_FAILED_FAMILY_DETECTION_AT", None), \
             patch.object(cs.time, "monotonic", lambda: t[0]):
            first = cs._detect_mt5_symbol_family()
            t[0] += cs._FAMILY_DETECTION_COOLDOWN_SECONDS + 0.01
            second = cs._detect_mt5_symbol_family()
        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(fake_mt5.symbols_get.call_count, 2,
                         "depois do cooldown expirar, a próxima chamada tem que reconsultar")
        print("[✓] Depois do cooldown expirar, a detecção tenta de novo — não desiste pra sempre")

    def test_auto_detection_does_not_cache_when_no_candidate_forms_a_valid_family(self):
        """Achado 1: mesmo com candidatos existindo pro par-sonda, se NENHUM
        deles cobrir os 28 pares com contrato consistente, a detecção falha
        — e falha SEM memorizar, senão a família ruim ficaria congelada."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.SYMBOL_TRADE_MODE_FULL = "FULL"
        fake_mt5.symbols_get.return_value = [SimpleNamespace(name="EURUSDm", trade_mode="FULL")]
        # "m" resolve o par-sonda, mas não os outros 27 — família inválida.
        fake_mt5.symbol_info.side_effect = (
            lambda sym: SimpleNamespace(trade_mode="FULL", trade_contract_size=1000)
            if sym == "EURUSDm" else None)
        with patch.object(cs, "MT5_AVAILABLE", True), patch.object(cs, "mt5", fake_mt5), \
             patch.object(cs, "_AUTO_DETECTED_SUFFIX", None), \
             patch.object(cs, "_LAST_FAILED_FAMILY_DETECTION_AT", None):
            first = cs._detect_mt5_symbol_family()
            second = cs._detect_mt5_symbol_family()
        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(fake_mt5.symbols_get.call_count, 1,
                         "segunda chamada dentro do cooldown não pode reconsultar")
        print("[✓] Candidato que só resolve o par-sonda (não os 28) é rejeitado, sem memorizar "
              "— e sem reconsultar de novo dentro do cooldown")

    def test_symbol_family_consistency_treats_missing_contract_size_as_inconsistent(self):
        """Objeto MT5 sem trade_contract_size (só ocorre em dublê de teste
        mínimo — o objeto real do MT5 sempre expõe) não pode ser tratado como
        'compatível por padrão' — precisa reprovar o candidato."""
        fake_mt5 = make_fake_mt5()

        def info_for(sym):
            if sym == "EURUSDm":
                return SimpleNamespace(trade_mode="FULL")  # sem trade_contract_size
            return None

        fake_mt5.symbol_info.side_effect = info_for
        with patch.object(cs, "MT5_AVAILABLE", True), patch.object(cs, "mt5", fake_mt5):
            self.assertFalse(cs._symbol_family_is_consistent("m"))
        print("[✓] trade_contract_size ausente reprova o candidato — não presume compatível")

    def test_symbol_family_consistency_treats_non_positive_contract_size_as_inconsistent(self):
        """Achado em revisão (Codex, achado 1 rodada 3): trade_contract_size
        zerado ou negativo é inválido, mas 'todos zerados' bateria em
        len(sizes) == 1 se a checagem fosse só 'is None'."""
        fake_mt5 = make_fake_mt5()

        def info_for(sym):
            for pair in cs.ALL_28_PAIRS:
                if sym == pair + "m":
                    return SimpleNamespace(trade_mode="FULL", trade_contract_size=0)
            return None

        fake_mt5.symbol_info.side_effect = info_for
        with patch.object(cs, "MT5_AVAILABLE", True), patch.object(cs, "mt5", fake_mt5):
            self.assertFalse(cs._symbol_family_is_consistent("m"))
        print("[✓] trade_contract_size zerado (mesmo consistente entre os 28) reprova o candidato")

    def test_to_broker_symbol_falls_back_to_auto_detection_as_last_resort(self):
        """Sem CSS_MT5_SYMBOL_SUFFIX configurado, a resolução descobre a
        família sozinha antes de devolver um nome não confirmado."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.SYMBOL_TRADE_MODE_FULL = "FULL"
        fake_mt5.symbols_get.return_value = [SimpleNamespace(name="EURUSDm", trade_mode="FULL")]
        fake_mt5.symbol_info.side_effect = self._uniform_family_symbol_info("m")

        with patch.object(cs, "MT5_SYMBOL_SUFFIX", ""), \
             patch.object(cs, "MT5_AVAILABLE", True), patch.object(cs, "mt5", fake_mt5), \
             patch.object(cs, "_AUTO_DETECTED_SUFFIX", None), \
             patch.object(cs, "_LAST_FAILED_FAMILY_DETECTION_AT", None), \
             patch.dict(cs._SYMBOL_RESOLUTION_CACHE, {}, clear=True):
            result = cs.to_broker_symbol("EURUSD")
        self.assertEqual(result, "EURUSDm")
        print("[✓] to_broker_symbol() resolve via família auto-detectada quando configuração "
              "manual não existe")

    def test_to_broker_symbol_does_not_query_server_when_configured_suffix_already_works(self):
        """A configuração explícita continua tendo precedência — não faz uma
        consulta mais cara (symbols_get) quando o sufixo configurado já
        resolve, e nem tenta validar família nesse caso."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.symbol_info.return_value = SimpleNamespace(
            trade_mode="FULL", trade_contract_size=1000)
        with patch.object(cs, "MT5_SYMBOL_SUFFIX", "m"), \
             patch.object(cs, "MT5_AVAILABLE", True), patch.object(cs, "mt5", fake_mt5), \
             patch.object(cs, "_AUTO_DETECTED_SUFFIX", None), \
             patch.object(cs, "_LAST_FAILED_FAMILY_DETECTION_AT", None), \
             patch.dict(cs._SYMBOL_RESOLUTION_CACHE, {}, clear=True):
            cs.to_broker_symbol("EURUSD")
        fake_mt5.symbols_get.assert_not_called()
        print("[✓] Sufixo configurado que já funciona nunca aciona a auto-detecção")

    def test_from_broker_symbol_strips_auto_detected_suffix_too(self):
        with patch.object(cs, "MT5_SYMBOL_SUFFIX", ""), \
             patch.object(cs, "_AUTO_DETECTED_SUFFIX", "m"):
            self.assertEqual(cs.from_broker_symbol("EURUSDm"), "EURUSD")
        print("[✓] from_broker_symbol() também reconhece o sufixo auto-detectado")

    def test_open_refused_entirely_when_symbols_unresolved(self):
        """Recusa a cesta INTEIRA em vez de abrir parcial: 3 de 7 pernas é uma
        aposta direcional nua, não a cesta diversificada da estratégia."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.account_info.return_value = SimpleNamespace(
            login=999, server="Broker-Demo", trade_mode="DEMO",
            trade_allowed=True, margin_mode="HEDGING")
        fake_mt5.positions_get.return_value = ()
        fake_mt5.symbol_info.return_value = None   # nenhum símbolo resolve
        with patch.dict(os.environ, DEMO_GATE_ENV), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe.open_portfolio_basket("CAD", "BUY")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "preflight_failed")
        self.assertEqual(len(result["unresolved"]), 7)
        fake_mt5.order_send.assert_not_called()
        print("[✓] Símbolo não resolvido recusa a cesta inteira, sem enviar ordem")

    def test_open_refused_entirely_when_a_single_pair_has_no_tick(self):
        """Regressão do preflight: antes ele só checava symbol_info, e um par
        sem cotação só era descoberto no laço de envio — depois das pernas
        anteriores já terem sido abertas, deixando a cesta parcial."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.account_info.return_value = SimpleNamespace(
            login=999, server="Broker-Demo", trade_mode="DEMO",
            trade_allowed=True, margin_mode="HEDGING")
        fake_mt5.positions_get.return_value = ()
        fake_mt5.symbol_info.return_value = SimpleNamespace(visible=True, trade_mode="FULL")

        def tick_for(sym):
            # 6 dos 7 pares cotam; CADJPYm (o último da cesta) não.
            if sym.startswith("CADJPY"):
                return None
            return SimpleNamespace(ask=1.1000, bid=1.0998)

        fake_mt5.symbol_info_tick.side_effect = tick_for
        with patch.dict(os.environ, DEMO_GATE_ENV), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe.open_portfolio_basket("CAD", "BUY")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "preflight_failed")
        self.assertEqual(result["no_tick"], ["CADJPY"])
        fake_mt5.order_send.assert_not_called()
        print("[✓] Um único par sem cotação recusa a cesta inteira — zero perna aberta")

    def test_open_refused_entirely_when_a_single_pair_has_a_crossed_market_tick(self):
        """A REGRESSÃO CENTRAL do achado 4 rodada 4 (Claude, medido): o
        CostModel já rejeitava mercado cruzado (ask < bid) como dado
        inválido, mas o PREFLIGHT — o gate que REALMENTE decide se a ordem
        sai — não tinha a mesma checagem. Um tick cruzado era aceito aqui e
        abriria as 7 pernas com um price potencialmente do lado errado do
        book, que também alimenta o stop catastrófico. Antes desta
        correção, o diagnóstico era mais rigoroso que o gate de execução —
        exatamente o oposto do que deveria ser."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.account_info.return_value = SimpleNamespace(
            login=999, server="Broker-Demo", trade_mode="DEMO",
            trade_allowed=True, margin_mode="HEDGING")
        fake_mt5.positions_get.return_value = ()
        fake_mt5.symbol_info.return_value = SimpleNamespace(visible=True, trade_mode="FULL")

        def tick_for(sym):
            # 6 dos 7 pares cotam normal; CADJPYm vem com mercado cruzado
            # (ask < bid) — dado de tick claramente inválido, não "sem tick".
            if sym.startswith("CADJPY"):
                return SimpleNamespace(ask=1.0998, bid=1.1000)
            return SimpleNamespace(ask=1.1000, bid=1.0998)

        fake_mt5.symbol_info_tick.side_effect = tick_for
        with patch.dict(os.environ, DEMO_GATE_ENV), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe.open_portfolio_basket("CAD", "BUY")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "preflight_failed")
        self.assertEqual(result["no_tick"], ["CADJPY"],
                         "mercado cruzado tem que ser tratado como tick inválido, não aceito")
        fake_mt5.order_send.assert_not_called()
        print("[✓] Tick com mercado cruzado recusa a cesta inteira — mesma régua do CostModel")

    def test_open_refused_when_one_side_of_the_tick_is_zero_in_a_mono_direction_basket(self):
        """Achado em revisão (mfc-rev-2, achado 2/4 rodada 5, P2-2, medido):
        a versão anterior do preflight só exigia "não cruzado" (ask >= bid)
        e o preço do lado USADO positivo — não exigia o OUTRO lado positivo.
        Numa cesta que mistura BUY e SELL isso ficava mascarado, porque a
        perna do lado oposto costuma cair em "sem tick" primeiro. EUR/BUY é
        uma cesta MONO-DIREÇÃO de verdade (EUR é base nos 7 pares da sua
        cesta, então toda perna é BUY, usa só o ask) — o cenário exato em
        que a proteção acidental não existe. ask positivo + bid=0 (não é
        "cruzado": 150.02 >= 0) tinha que passar reto antes desta correção;
        agora _tick_valido() exige os dois lados positivos."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.account_info.return_value = SimpleNamespace(
            login=999, server="Broker-Demo", trade_mode="DEMO",
            trade_allowed=True, margin_mode="HEDGING"
        )
        fake_mt5.positions_get.return_value = ()
        fake_mt5.symbol_info.return_value = SimpleNamespace(visible=True, trade_mode="FULL")

        def tick_for(sym):
            if sym.startswith("EURJPY"):
                return SimpleNamespace(ask=150.02, bid=0.0)  # ask>0, bid=0 — não "cruzado"
            return SimpleNamespace(ask=1.1002, bid=1.1000)

        fake_mt5.symbol_info_tick.side_effect = tick_for
        with patch.dict(os.environ, DEMO_GATE_ENV), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe.open_portfolio_basket("EUR", "BUY")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "preflight_failed")
        self.assertEqual(result["no_tick"], ["EURJPY"],
                         "bid=0 com ask positivo tem que ser recusado mesmo numa cesta "
                         "mono-direção, sem a proteção acidental da perna do lado oposto")
        fake_mt5.order_send.assert_not_called()
        print("[✓] preflight recusa tick com um lado zerado mesmo numa cesta só-BUY "
              "(EUR), onde não existe perna do lado oposto pra mascarar o bug")

    def test_order_send_falls_back_to_preflight_price_when_tick_degrades_before_send(self):
        """Achado em revisão (mfc-rev-2, achado 2/4 rodada 5, P2-2, medido):
        o laço de envio reconsulta o tick antes de cada order_send(); se
        esse tick vier cruzado bem entre o preflight e o envio (janela real,
        ainda que estreita), a versão anterior só checava "price <= 0" —
        aceitava o lado escolhido mesmo com mercado cruzado. Agora usa
        _tick_valido() também aqui: tick inválido na 2ª consulta cai pro
        preço JÁ VALIDADO no preflight, não é aceito cru."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.account_info.return_value = SimpleNamespace(
            login=999, server="Broker-Demo", trade_mode="DEMO",
            trade_allowed=True, margin_mode="HEDGING"
        )
        fake_mt5.positions_get.return_value = ()
        fake_mt5.symbol_info.return_value = SimpleNamespace(visible=True, trade_mode="FULL")
        fake_mt5.order_send.return_value = SimpleNamespace(
            retcode=fake_mt5.TRADE_RETCODE_DONE, order=1, price=1.1002, comment="ok")

        calls_per_symbol = {}

        def tick_for(sym):
            n = calls_per_symbol.get(sym, 0)
            calls_per_symbol[sym] = n + 1
            if sym.startswith("CADJPY") and n == 1:
                # 2ª consulta (laço de envio, depois do preflight já ter
                # validado este par): mercado ficou cruzado nesse meio-tempo.
                return SimpleNamespace(ask=1.0998, bid=1.1000)
            return SimpleNamespace(ask=1.1002, bid=1.1000)

        fake_mt5.symbol_info_tick.side_effect = tick_for
        with patch.dict(os.environ, DEMO_GATE_ENV), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe.open_portfolio_basket("CAD", "BUY")
        self.assertEqual(result["opened_count"], 7,
                         "a cesta inteira já passou pelo preflight — um tick que degrada "
                         "só depois não pode abortar a perna nem travar a cesta")
        cadjpy_calls = [c for c in fake_mt5.order_send.call_args_list
                        if c.args[0]["symbol"].startswith("CADJPY")]
        self.assertEqual(len(cadjpy_calls), 1)
        self.assertEqual(cadjpy_calls[0].args[0]["price"], 1.1002,
                         "com tick cruzado só na 2ª consulta, o price enviado tem que ser "
                         "o do preflight (já validado), não o do lado errado do book")
        print("[✓] tick que fica cruzado entre preflight e envio cai no preço já validado "
              "do preflight, não é aceito cru na 2ª consulta")

    def test_open_refused_entirely_when_a_single_pair_has_restricted_trade_mode(self):
        """Achado ALTO em revisão (/codex-r sobre o commit ad44e12): a
        auto-detecção de sufixo em web/css_service.py só valida
        trade_mode==FULL no par-sonda (EURUSD) — não garante nada sobre as
        outras 27 pernas possíveis da mesma família de símbolos. Uma perna
        individual pode ser CLOSEONLY/LONGONLY/SHORTONLY mesmo com o sufixo
        certo, e sem esta checagem passaria batido no preflight até falhar
        em order_send() — tarde demais, com pernas anteriores já abertas."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.SYMBOL_TRADE_MODE_FULL = "FULL"
        fake_mt5.account_info.return_value = SimpleNamespace(
            login=999, server="Broker-Demo", trade_mode="DEMO",
            trade_allowed=True, margin_mode="HEDGING")
        fake_mt5.positions_get.return_value = ()

        def info_for(sym):
            # 6 dos 7 pares negociam livremente; CADJPYm só fecha posição.
            mode = "CLOSEONLY" if sym.startswith("CADJPY") else "FULL"
            return SimpleNamespace(visible=True, trade_mode=mode)

        fake_mt5.symbol_info.side_effect = info_for
        fake_mt5.symbol_info_tick.return_value = SimpleNamespace(ask=1.1000, bid=1.0998)
        with patch.dict(os.environ, DEMO_GATE_ENV), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe.open_portfolio_basket("CAD", "BUY")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "preflight_failed")
        self.assertEqual(result["restricted"], ["CADJPY"])
        fake_mt5.order_send.assert_not_called()
        print("[✓] Um único par com trade_mode restrito recusa a cesta inteira — zero perna aberta")

    def test_open_refused_when_trade_mode_is_missing_from_symbol_info(self):
        """Achado 2 (revisão de ad44e12/c24a44c, codex-r + mfc-rev-2): o
        preflight usava getattr(info, "trade_mode", full_mode) — campo
        AUSENTE era presumido FULL (fail-open). As duas rodadas de revisão
        confirmaram, via documentação oficial do binding e por inspeção do
        próprio código (que já acessa trade_contract_size, swap_long, point
        e visible SEM getattr em outros lugares), que o objeto real do MT5
        NUNCA vem incompleto — symbol_info() devolve o namedtuple inteiro ou
        None, nunca um meio-termo. Ou seja: campo ausente só pode acontecer
        aqui dentro de um dublê de teste, nunca em produção. Mesmo assim,
        presumir o caso mais permissivo (FULL) quando a garantia é teórica
        é a escolha errada num preflight que existe pra decidir se dinheiro
        real sai. Corrigido pra fail-closed: ausência de trade_mode agora
        é tratada como restrição, não como negociável."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.SYMBOL_TRADE_MODE_FULL = "FULL"
        fake_mt5.account_info.return_value = SimpleNamespace(
            login=999, server="Broker-Demo", trade_mode="DEMO",
            trade_allowed=True, margin_mode="HEDGING")
        fake_mt5.positions_get.return_value = ()
        # CADJPY sem trade_mode nenhum (não CLOSEONLY — AUSENTE); os outros
        # 6 pares vêm com FULL explícito.
        fake_mt5.symbol_info.side_effect = (
            lambda sym: SimpleNamespace(visible=True) if sym.startswith("CADJPY")
            else SimpleNamespace(visible=True, trade_mode="FULL"))
        fake_mt5.symbol_info_tick.return_value = SimpleNamespace(ask=1.1000, bid=1.0998)
        with patch.dict(os.environ, DEMO_GATE_ENV), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe.open_portfolio_basket("CAD", "BUY")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "preflight_failed")
        self.assertEqual(result["restricted"], ["CADJPY"],
                         "trade_mode AUSENTE tem que ser tratado como restrito, não como FULL")
        fake_mt5.order_send.assert_not_called()
        print("[✓] trade_mode ausente é tratado como restrito (fail-closed) — não presume FULL")


class TestReconciler(unittest.TestCase):
    """Reconciliador das 08:10 — a rede que transforma 'ninguém percebeu' em
    alarme. O valor dele está em ALERTAR, não em fechar."""

    def _run(self, magics_abertos, close_result=None, raise_query=False):
        """Isola os arquivos de alerta num tmpdir: o reconciliador grava
        RECONCILE_ALERT.json/reconcile_alerts.log de verdade, e sem isso a
        suíte sujava data/ e um teste contaminava o outro."""
        import scripts.scheduler_daemon as daemon
        enviados = []

        def fake_get():
            if raise_query:
                raise pe.MT5QueryError("consulta falhou")
            return magics_abertos

        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(daemon, "RECONCILE_ALERT", os.path.join(tmp, "RECONCILE_ALERT.json")), \
             patch.object(daemon, "RECONCILE_LOG", os.path.join(tmp, "reconcile_alerts.log")), \
             patch.object(pe, "get_open_magics_and_symbols", fake_get), \
             patch.object(daemon, "close_all_portfolios",
                          lambda: close_result or {"success": True, "total_closed": 7}), \
             patch("web.telegram_service.send_telegram_message",
                   lambda text, **kw: enviados.append(text)):
            daemon.execute_phase_0810()
        return enviados

    def test_alert_file_written_even_without_telegram(self):
        """O Telegram é decisão em aberto: o alerta não pode depender dele."""
        import scripts.scheduler_daemon as daemon
        with tempfile.TemporaryDirectory() as tmp:
            alert = os.path.join(tmp, "RECONCILE_ALERT.json")
            with patch.object(daemon, "RECONCILE_ALERT", alert), \
                 patch.object(daemon, "RECONCILE_LOG", os.path.join(tmp, "r.log")), \
                 patch.object(pe, "get_open_magics_and_symbols",
                              lambda: {801007: {"USDCAD"}}), \
                 patch.object(daemon, "close_all_portfolios",
                              lambda: {"success": False, "error": "x", "message": "y"}), \
                 patch("web.telegram_service.send_telegram_message",
                       lambda *a, **k: (_ for _ in ()).throw(RuntimeError("telegram off"))):
                daemon.execute_phase_0810()
            self.assertTrue(os.path.exists(alert),
                            "alerta precisa existir em arquivo mesmo com o Telegram fora")
            with open(alert, encoding="utf-8") as f:
                self.assertIn("INTERVENÇÃO MANUAL", json.load(f)["message"])
        print("[✓] Alerta persiste em arquivo mesmo com o Telegram indisponível")

    def test_silent_when_nothing_open(self):
        self.assertEqual(self._run({}), [])
        print("[✓] Reconciliação limpa não gera alarme falso")

    def test_alerts_when_orphan_positions_found(self):
        enviados = self._run({801007: {"USDCAD"}})
        self.assertTrue(any("ainda ABERTA" in t for t in enviados))
        print("[✓] Posição órfã às 08:10 dispara alerta")

    def test_escalates_when_close_fails(self):
        enviados = self._run({801007: {"USDCAD"}},
                             close_result={"success": False, "error": "partial_close",
                                           "message": "não fechou"})
        self.assertTrue(any("INTERVENÇÃO MANUAL" in t for t in enviados))
        print("[✓] Falha no fechamento da reconciliação escala pra intervenção manual")

    def test_alerts_when_broker_query_itself_fails(self):
        """Não conseguir CONSULTAR é tão grave quanto achar posição aberta:
        não dá pra afirmar que está tudo fechado."""
        enviados = self._run({}, raise_query=True)
        self.assertTrue(any("não foi possível CONSULTAR" in t for t in enviados))
        print("[✓] Consulta falhando às 08:10 também alerta (não assume 'tudo fechado')")

    def test_notices_when_telegram_reports_failure_without_raising(self):
        """Regressão (achado em revisão, rodada 8): send_telegram_message()
        não lança em falha — devolve {"success": False, ...} silenciosamente
        (token/chat_id ausente, erro de rede tratado internamente). Sem
        checar o retorno, um envio que falhou passava como se tivesse dado
        certo, sem nenhum aviso nos logs."""
        import scripts.scheduler_daemon as daemon
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(daemon, "RECONCILE_ALERT", os.path.join(tmp, "RECONCILE_ALERT.json")), \
             patch.object(daemon, "RECONCILE_LOG", os.path.join(tmp, "r.log")), \
             patch.object(pe, "get_open_magics_and_symbols", lambda: {801007: {"USDCAD"}}), \
             patch.object(daemon, "close_all_portfolios",
                          lambda: {"success": True, "total_closed": 7}), \
             patch("web.telegram_service.send_telegram_message",
                   lambda *a, **k: {"success": False, "error": "Bot Token ausente"}), \
             patch("builtins.print") as mock_print:
            daemon.execute_phase_0810()
        printed = " ".join(str(c.args[0]) for c in mock_print.call_args_list if c.args)
        self.assertIn("não confirmou o envio", printed)
        self.assertIn("Bot Token ausente", printed)
        print("[✓] Telegram devolvendo success=False (sem lançar) agora aparece no log")


class TestExposureCaps(unittest.TestCase):
    """Travas de tamanho. Nos valores padrão não mudam a estratégia (lote
    fixo 0.01, até 8 cestas): existem contra erro de chamada e payload de
    API malformado."""

    def _demo(self, positions=()):
        fake = make_fake_mt5()
        fake.account_info.return_value = SimpleNamespace(
            login=999, server="Broker-Demo", trade_mode="DEMO",
            trade_allowed=True, margin_mode="HEDGING")
        fake.positions_get.return_value = positions
        fake.symbol_info.return_value = SimpleNamespace(visible=True, trade_mode="FULL")
        fake.symbol_info_tick.return_value = SimpleNamespace(ask=1.1, bid=1.0998)
        fake.order_send.return_value = SimpleNamespace(
            retcode=fake.TRADE_RETCODE_DONE, order=1, price=1.1, comment="ok")
        return fake

    def test_invalid_bias_refused_instead_of_inverting_basket(self):
        """Regressão: get_portfolio_pairs trata qualquer valor != 'BUY' como
        fraqueza, então bias='LONG' montava a cesta INVERTIDA em silêncio."""
        fake_mt5 = self._demo()
        with patch.dict(os.environ, DEMO_GATE_ENV), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe.open_portfolio_basket("CAD", "LONG")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "invalid_bias")
        fake_mt5.order_send.assert_not_called()
        print("[✓] bias inválido ('LONG') recusa em vez de abrir a cesta invertida")

    def test_lot_above_cap_refused(self):
        fake_mt5 = self._demo()
        with patch.dict(os.environ, DEMO_GATE_ENV), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe.open_portfolio_basket("CAD", "BUY", lot=10.0)
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "lot_above_cap")
        fake_mt5.order_send.assert_not_called()
        print("[✓] Lote acima do teto recusado (o 0.01 era só um default, não trava)")

    def test_default_lot_still_allowed(self):
        """O teto não pode quebrar a operação normal."""
        fake_mt5 = self._demo()
        with patch.dict(os.environ, DEMO_GATE_ENV), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe.open_portfolio_basket("CAD", "BUY")
        self.assertTrue(result["success"])
        self.assertEqual(result["opened_count"], 7)
        print("[✓] Lote padrão 0.01 continua passando (teto não muda a estratégia)")

    def test_basket_cap_refuses_beyond_limit(self):
        magics = list(pe.PORTFOLIO_MAGICS.values())[:3]
        positions = [SimpleNamespace(magic=m, symbol="EURUSDm") for m in magics]
        fake_mt5 = self._demo(positions=positions)
        with patch.dict(os.environ, DEMO_GATE_ENV), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5), \
             patch.object(pe, "MAX_CONCURRENT_BASKETS", 3):
            result = pe.open_portfolio_basket("NZD", "BUY")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "basket_cap_reached")
        fake_mt5.order_send.assert_not_called()
        print("[✓] Teto de cestas simultâneas recusa a abertura seguinte")


class TestAmbiguousRetcodeConfirmation(unittest.TestCase):
    """Regressão de bug real (achado em revisão cruzada com o upstream, F4):
    depois de um retcode ambíguo (TIMEOUT/CONNECTION/DONE_PARTIAL/PLACED),
    uma checagem ÚNICA e IMEDIATA que não encontra a posição não prova que
    ela nunca abriu — só que ainda não propagou no broker. O código antigo
    tratava 'não achei agora' como 'confirmado que não abriu' e reenviava,
    o que pode dobrar a perna."""

    def setUp(self):
        self._sleep_patch = patch.object(pe.time, "sleep", lambda *_: None)
        self._sleep_patch.start()

    def tearDown(self):
        self._sleep_patch.stop()

    def test_confirms_found_on_a_later_attempt_not_only_the_first(self):
        calls = {"n": 0}

        def fake_volume(symbol, magic):
            calls["n"] += 1
            return 0.01 if calls["n"] >= 3 else None  # só "aparece" na 3ª tentativa

        with patch.object(pe, "_confirmed_position_volume", side_effect=fake_volume), \
             patch.object(pe, "_AMBIGUOUS_CONFIRM_ATTEMPTS", 3):
            result = pe._confirm_position_after_ambiguous_retcode("EURUSDm", 801001)
        self.assertEqual(result, 0.01)
        self.assertEqual(calls["n"], 3)
        print("[✓] confirmação após ambíguo espera a posição propagar antes de desistir")

    def test_gives_up_as_none_after_exhausting_attempts_without_finding(self):
        with patch.object(pe, "_confirmed_position_volume", return_value=None), \
             patch.object(pe, "_AMBIGUOUS_CONFIRM_ATTEMPTS", 3):
            result = pe._confirm_position_after_ambiguous_retcode("EURUSDm", 801001)
        self.assertIsNone(result)
        print("[✓] esgotadas as tentativas sem achar, devolve None — quem chama NÃO reenvia")

    def test_gives_up_as_none_when_query_never_succeeds(self):
        """Consulta indisponível em TODAS as tentativas (None sempre) também
        desiste como None no fim — nunca finge certeza, mas também nunca
        trava esperando uma resposta que não vai vir."""
        with patch.object(pe, "_confirmed_position_volume", return_value=None), \
             patch.object(pe, "_AMBIGUOUS_CONFIRM_ATTEMPTS", 3):
            result = pe._confirm_position_after_ambiguous_retcode("EURUSDm", 801001)
        self.assertIsNone(result)
        print("[✓] consulta sempre indisponível: desiste como None, não trava nem finge certeza")

    def test_retries_through_a_transient_query_failure_instead_of_giving_up_early(self):
        """Regressão sobre a própria correção (achado em revisão): a versão
        anterior desta função desistia IMEDIATAMENTE (retornava None) se a
        1ª tentativa falhasse na consulta, mesmo que a 2ª tentativa fosse
        confirmar a posição de verdade. Uma falha de consulta transitória não
        pode custar a chance de confirmar nas tentativas seguintes."""
        calls = {"n": 0}

        def fake_volume(symbol, magic):
            calls["n"] += 1
            if calls["n"] == 1:
                return None  # falha transitória de consulta na 1ª tentativa
            return 0.01 if calls["n"] >= 2 else None  # confirma na 2ª

        with patch.object(pe, "_confirmed_position_volume", side_effect=fake_volume), \
             patch.object(pe, "_AMBIGUOUS_CONFIRM_ATTEMPTS", 3):
            result = pe._confirm_position_after_ambiguous_retcode("EURUSDm", 801001)
        self.assertEqual(result, 0.01)
        self.assertEqual(calls["n"], 2)
        print("[✓] falha de consulta na 1ª tentativa não impede confirmar na 2ª")

    def test_confirm_attempts_and_delay_are_clamped_against_misconfiguration(self):
        """Regressão (achado ALTO em revisão): CSS_AMBIGUOUS_CONFIRM_ATTEMPTS
        e CSS_AMBIGUOUS_CONFIRM_DELAY_SEC só passavam por _env_number, que
        garante o CAST pro tipo certo mas não que o valor faz sentido. Um
        typo tipo ATTEMPTS=300000 travaria a abertura da cesta por horas;
        DELAY_SEC negativo ou NaN faz time.sleep() lançar ValueError no meio
        do laço de envio, abortando pernas restantes sem rollback algum."""
        self.assertEqual(pe._clamp(300000, 1, 10), 10)
        self.assertEqual(pe._clamp(-5, 1, 10), 1)
        self.assertEqual(pe._clamp(-1.0, 0.0, 10.0), 0.0)
        self.assertEqual(pe._clamp(float("inf"), 0.0, 10.0), 10.0)
        self.assertEqual(pe._clamp(float("nan"), 0.0, 10.0), 0.0)
        print("[✓] _clamp neutraliza tentativas/delay absurdos vindos de env var")

    def test_confirmed_position_volume_sums_matching_positions_and_ignores_others(self):
        fake_mt5 = make_fake_mt5()
        fake_mt5.positions_get.return_value = [
            SimpleNamespace(magic=801001, volume=0.006),
            SimpleNamespace(magic=801001, volume=0.004),  # a mesma perna em 2 posições
            SimpleNamespace(magic=999999, volume=5.0),    # outro magic — nunca soma
        ]
        with patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe._confirmed_position_volume("EURUSDm", 801001)
        self.assertAlmostEqual(result, 0.01)
        print("[✓] soma só o volume do magic pedido, ignora posições de outros magics")

    def test_confirmed_position_volume_none_when_nothing_matches(self):
        fake_mt5 = make_fake_mt5()
        fake_mt5.positions_get.return_value = []
        with patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe._confirmed_position_volume("EURUSDm", 801001)
        self.assertIsNone(result)
        print("[✓] nenhuma posição do magic: None, não zero (distinção importa pro chamador)")

    def test_done_partial_reports_real_volume_not_the_requested_lot(self):
        """DONE_PARTIAL é ambíguo: o broker aceitou mas só preencheu uma
        fração do lote pedido. Antes da correção (achado em revisão), a
        confirmação só checava 'existe alguma posição' — contava a perna
        como os 0.01 pedidos mesmo com só 0.006 executado de verdade,
        escondendo a exposição real da cesta."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.account_info.return_value = SimpleNamespace(
            login=999, server="Broker-Demo", trade_mode="DEMO",
            trade_allowed=True, margin_mode="HEDGING")
        fake_mt5.symbol_info.return_value = SimpleNamespace(visible=True, trade_mode="FULL")
        fake_mt5.symbol_info_tick.return_value = SimpleNamespace(ask=1.1, bid=1.0998)
        fake_mt5.TRADE_RETCODE_DONE_PARTIAL = 10010

        pairs = pe.get_portfolio_pairs("CAD", "BUY")
        first_symbol = pairs[0]["pair"]
        first_broker_symbol = pe.to_broker_symbol(first_symbol)
        first_magic = pe.PORTFOLIO_MAGICS["CAD"]

        def fake_positions_get(*args, **kwargs):
            symbol = kwargs.get("symbol")
            if symbol == first_broker_symbol:
                return [SimpleNamespace(magic=first_magic, volume=0.006)]
            return []
        fake_mt5.positions_get.side_effect = fake_positions_get

        def fake_order_send(request):
            if request["symbol"] == first_broker_symbol:
                return SimpleNamespace(retcode=fake_mt5.TRADE_RETCODE_DONE_PARTIAL, order=1,
                                        price=1.1, comment="partial")
            return SimpleNamespace(retcode=fake_mt5.TRADE_RETCODE_DONE, order=2,
                                    price=1.1, comment="ok")
        fake_mt5.order_send.side_effect = fake_order_send

        with patch.dict(os.environ, DEMO_GATE_ENV), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5), \
             patch.object(pe, "_AMBIGUOUS_CONFIRM_ATTEMPTS", 1):
            result = pe.open_portfolio_basket("CAD", "BUY", lot=0.01)

        first_leg_result = next(r for r in result["results"] if r["pair"] == first_symbol)
        self.assertEqual(first_leg_result["status"], "OPENED")
        self.assertAlmostEqual(first_leg_result["lot"], 0.006,
                                msg="reportou o lote PEDIDO (0.01) em vez do volume REAL confirmado (0.006)")
        self.assertIn("0.01", first_leg_result["message"])  # menciona o lote pedido pra contraste
        print("[✓] DONE_PARTIAL reporta o volume real confirmado, não o lote pedido")

    def test_open_basket_never_resends_after_unconfirmed_ambiguous_retcode(self):
        """Ponta a ponta: antes da correção, essa sequência levava a um SEGUNDO
        order_send (o comentário do código dizia 'confirmado que não abriu —
        seguro reenviar', o que é falso pra PLACED/TIMEOUT/CONNECTION)."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.account_info.return_value = SimpleNamespace(
            login=999, server="Broker-Demo", trade_mode="DEMO",
            trade_allowed=True, margin_mode="HEDGING")
        fake_mt5.positions_get.return_value = []
        fake_mt5.symbol_info.return_value = SimpleNamespace(visible=True, trade_mode="FULL")
        fake_mt5.symbol_info_tick.return_value = SimpleNamespace(ask=1.1, bid=1.0998)
        fake_mt5.TRADE_RETCODE_PLACED = 10008

        pairs = pe.get_portfolio_pairs("CAD", "BUY")
        first_symbol = pairs[0]["pair"]
        first_broker_symbol = pe.to_broker_symbol(first_symbol)

        def fake_order_send(request):
            if request["symbol"] == first_broker_symbol:
                return SimpleNamespace(retcode=fake_mt5.TRADE_RETCODE_PLACED, order=None,
                                        price=None, comment="placed")
            return SimpleNamespace(retcode=fake_mt5.TRADE_RETCODE_DONE, order=1,
                                    price=1.1, comment="ok")
        fake_mt5.order_send.side_effect = fake_order_send

        with patch.dict(os.environ, DEMO_GATE_ENV), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5), \
             patch.object(pe, "_AMBIGUOUS_CONFIRM_ATTEMPTS", 2):
            result = pe.open_portfolio_basket("CAD", "BUY")

        calls_for_first = [c for c in fake_mt5.order_send.call_args_list
                            if c.args[0]["symbol"] == first_broker_symbol]
        self.assertEqual(len(calls_for_first), 1,
                          "reenviou depois de retcode ambíguo não confirmado — pode ter dobrado a perna")
        first_leg_result = next(r for r in result["results"] if r["pair"] == first_symbol)
        self.assertEqual(first_leg_result["status"], "ERROR")
        self.assertEqual(result["opened_count"], 6)
        print("[✓] retcode ambíguo não confirmado NÃO reenvia — fica ERROR pra revisão manual")

    def test_order_send_returning_none_is_treated_as_ambiguous_not_resent(self):
        """res is None (ex.: exceção de conexão dentro do próprio order_send,
        MT5 devolve None em vez de lançar) também é ambíguo — mesmo caminho
        de confirmação, mesma proibição de reenvio."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.account_info.return_value = SimpleNamespace(
            login=999, server="Broker-Demo", trade_mode="DEMO",
            trade_allowed=True, margin_mode="HEDGING")
        fake_mt5.positions_get.return_value = []
        fake_mt5.symbol_info.return_value = SimpleNamespace(visible=True, trade_mode="FULL")
        fake_mt5.symbol_info_tick.return_value = SimpleNamespace(ask=1.1, bid=1.0998)

        pairs = pe.get_portfolio_pairs("CAD", "BUY")
        first_symbol = pairs[0]["pair"]
        first_broker_symbol = pe.to_broker_symbol(first_symbol)

        def fake_order_send(request):
            if request["symbol"] == first_broker_symbol:
                return None
            return SimpleNamespace(retcode=fake_mt5.TRADE_RETCODE_DONE, order=1,
                                    price=1.1, comment="ok")
        fake_mt5.order_send.side_effect = fake_order_send

        with patch.dict(os.environ, DEMO_GATE_ENV), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5), \
             patch.object(pe, "_AMBIGUOUS_CONFIRM_ATTEMPTS", 2):
            result = pe.open_portfolio_basket("CAD", "BUY")

        calls_for_first = [c for c in fake_mt5.order_send.call_args_list
                            if c.args[0]["symbol"] == first_broker_symbol]
        self.assertEqual(len(calls_for_first), 1)
        first_leg_result = next(r for r in result["results"] if r["pair"] == first_symbol)
        self.assertEqual(first_leg_result["status"], "ERROR")
        print("[✓] order_send retornando None (não uma resposta) também não reenvia às cegas")

    def test_fallback_error_reports_the_resend_response_not_the_original(self):
        """Regressão (achado em revisão): a mensagem de erro do fallback com
        ORDER_FILLING_RETURN usava os campos da resposta ORIGINAL (res), não
        do próprio reenvio (res2) — reportava o erro errado quando quem
        falhou de fato foi a 2ª tentativa."""
        fake_mt5 = make_fake_mt5()
        fake_mt5.account_info.return_value = SimpleNamespace(
            login=999, server="Broker-Demo", trade_mode="DEMO",
            trade_allowed=True, margin_mode="HEDGING")
        fake_mt5.positions_get.return_value = []
        fake_mt5.symbol_info.return_value = SimpleNamespace(visible=True, trade_mode="FULL")
        fake_mt5.symbol_info_tick.return_value = SimpleNamespace(ask=1.1, bid=1.0998)
        fake_mt5.TRADE_RETCODE_REQUOTE = 10004  # não ambíguo — rejeição pura

        pairs = pe.get_portfolio_pairs("CAD", "BUY")
        first_symbol = pairs[0]["pair"]
        first_broker_symbol = pe.to_broker_symbol(first_symbol)
        calls = {"n": 0}

        def fake_order_send(request):
            if request["symbol"] != first_broker_symbol:
                return SimpleNamespace(retcode=fake_mt5.TRADE_RETCODE_DONE, order=1,
                                        price=1.1, comment="ok")
            calls["n"] += 1
            if calls["n"] == 1:
                return SimpleNamespace(retcode=fake_mt5.TRADE_RETCODE_REQUOTE, order=None,
                                        price=None, comment="requote original")
            return SimpleNamespace(retcode=fake_mt5.TRADE_RETCODE_REQUOTE, order=None,
                                    price=None, comment="requote do reenvio")
        fake_mt5.order_send.side_effect = fake_order_send

        with patch.dict(os.environ, DEMO_GATE_ENV), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe.open_portfolio_basket("CAD", "BUY")

        first_leg_result = next(r for r in result["results"] if r["pair"] == first_symbol)
        self.assertEqual(first_leg_result["status"], "ERROR")
        self.assertEqual(first_leg_result["message"], "requote do reenvio")
        print("[✓] erro do fallback reporta a resposta do REENVIO, não a original")


class TestCloseFailsClosed(unittest.TestCase):
    """O pior modo de falha do sistema: anunciar 'encerramento concluído' com
    a cesta viva atravessando o dia sem stop."""

    def _mt5_with_open_basket(self, order_ok=True, tick_ok=True):
        fake = make_fake_mt5()
        fake.account_info.return_value = SimpleNamespace(
            login=999, server="Broker-Demo", trade_mode="DEMO",
            trade_allowed=True, margin_mode="HEDGING")
        magic = pe.PORTFOLIO_MAGICS["CAD"]
        fake.positions_get.return_value = [
            SimpleNamespace(magic=magic, symbol=f"CAD{i}m", ticket=1000 + i,
                            volume=0.01, type=0)
            for i in range(7)
        ]
        fake.symbol_info_tick.return_value = (
            SimpleNamespace(ask=1.1, bid=1.0998) if tick_ok else None)
        fake.order_send.return_value = SimpleNamespace(
            retcode=fake.TRADE_RETCODE_DONE if order_ok else 10004,
            order=1, price=1.1, comment="ok" if order_ok else "Requote")
        return fake

    def test_close_reports_failure_when_every_order_rejected(self):
        fake_mt5 = self._mt5_with_open_basket(order_ok=False)
        with patch.dict(os.environ, DEMO_GATE_ENV), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5), \
             patch.object(pe, "ensure_mt5", lambda: True):
            res = pe.close_portfolio_basket("CAD")
        self.assertFalse(res["success"])
        self.assertEqual(res["closed_count"], 0)
        self.assertEqual(res["target_count"], 7)
        print("[✓] 7 posições vivas com ordem rejeitada => success False (não sucesso falso)")

    def test_close_reports_failure_when_tick_missing(self):
        fake_mt5 = self._mt5_with_open_basket(tick_ok=False)
        with patch.dict(os.environ, DEMO_GATE_ENV), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5), \
             patch.object(pe, "ensure_mt5", lambda: True):
            res = pe.close_portfolio_basket("CAD")
        self.assertFalse(res["success"])
        self.assertEqual(res["closed_count"], 0)
        print("[✓] Tick ausente no fechamento => posição contabilizada como NÃO fechada")

    def test_close_all_propagates_failures(self):
        fake_mt5 = self._mt5_with_open_basket(order_ok=False)
        with patch.dict(os.environ, DEMO_GATE_ENV), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5), \
             patch.object(pe, "ensure_mt5", lambda: True):
            res = pe.close_all_portfolios()
        self.assertFalse(res["success"])
        self.assertTrue(res["failures"], "failures não pode ficar vazio com pernas vivas")
        print("[✓] close_all_portfolios propaga a falha em vez de reportar sucesso")

    def test_close_refuses_wrong_account(self):
        """Esta máquina roda 5 terminais MT5 em contas diferentes: fechar na
        conta errada é tão ruim quanto não fechar."""
        fake_mt5 = self._mt5_with_open_basket()
        fake_mt5.account_info.return_value = SimpleNamespace(
            login=111, server="Outro-Terminal", trade_mode="DEMO",
            trade_allowed=True, margin_mode="HEDGING")
        with patch.dict(os.environ, {"CSS_MT5_EXPECTED_LOGIN": "999", "CSS_LIVE_TRADING": ""}), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5), \
             patch.object(pe, "ensure_mt5", lambda: True):
            res = pe.close_portfolio_basket("CAD")
        self.assertFalse(res["success"])
        self.assertEqual(res["error"], "wrong_account")
        fake_mt5.order_send.assert_not_called()
        print("[✓] Fechamento recusa conta diferente da esperada (5 terminais na máquina)")


class TestProvenanceStamping(unittest.TestCase):
    """O snapshot data/css_standard.json é versionado no git e carrega
    `mt5_connected: true` de quando foi gravado. Servido verbatim com o MT5
    fora, ele fazia um sinal de ontem passar como dado ao vivo."""

    def test_disk_snapshot_never_reports_itself_as_live(self):
        """Regressão do buraco real: o css_standard.json versionado tem
        `mt5_connected: true`; o __new__ o carrega no cache E carimba o
        last_update_*, então a chamada seguinte caía no throttle de 3s e
        devolvia esse `true` verbatim — 8/8 portfólios ACTIVE numa máquina
        sem MT5. Selar no _load_from_disk é o que fecha todos os caminhos."""
        with tempfile.TemporaryDirectory() as tmp:
            snap = os.path.join(tmp, "snapshot.json")
            with open(snap, "w", encoding="utf-8") as f:
                json.dump({"mt5_connected": True, "currencies": [], "timestamp": "2020-01-01"}, f)
            loaded = cs.CSSDataEngine._load_from_disk(snap)
        self.assertFalse(loaded["mt5_connected"])
        print("[✓] Snapshot em disco nunca se declara live, mesmo com true gravado")

    def test_stamp_overrides_inherited_flag_without_mutating_original(self):
        original = {"mt5_connected": True, "currencies": [1, 2, 3]}
        stamped = cs._stamp_provenance(original, False)
        self.assertFalse(stamped["mt5_connected"])
        self.assertTrue(original["mt5_connected"], "não pode mutar o cache compartilhado")
        print("[✓] _stamp_provenance sobrescreve a procedência sem mutar o original")

    def _gen(self, **kwargs):
        """Gera o payload SEM tocar em data/portfolio_signals_live.json nem na
        pasta do MT5 — o teste não pode reescrever o sinal real do operador."""
        with patch.object(pe, "_atomic_write_json", lambda *a, **k: None), \
             patch.object(pe, "get_mt5_files_dir", lambda: None):
            return pe.generate_and_save_daily_signals(
                currencies_data=[{"symbol": "CAD", "trade_bias": "COMPRA", "triads": {}}],
                **kwargs)

    def test_signals_blocked_when_caller_omits_provenance(self):
        """FAIL-CLOSED: quem passa currencies_data pronto (daily_css_routine)
        precisa declarar a procedência; omitir vira NÃO-live."""
        payload = self._gen()
        self.assertFalse(payload["mt5_connected"])
        self.assertEqual(payload["portfolios"]["CAD"]["status"], "BLOCKED")
        print("[✓] currencies_data sem mt5_connected declarado => BLOCKED (fail closed)")

    def test_signals_active_when_caller_declares_live(self):
        """Caso positivo — sem ele, os testes de procedência passariam mesmo
        se a checagem bloqueasse tudo indiscriminadamente."""
        payload = self._gen(mt5_connected=True)
        self.assertTrue(payload["mt5_connected"])
        self.assertEqual(payload["portfolios"]["CAD"]["status"], "ACTIVE")
        self.assertEqual(payload["portfolios"]["CAD"]["direction"], "BUY")
        print("[✓] Procedência declarada live => ACTIVE (a trava distingue os dois casos)")


class TestSignalProvenance(unittest.TestCase):
    """O carimbo 'date' atesta a hora da escrita, não a origem dos dados —
    sem 'mt5_connected' uma série SIMULADA vira ordem real."""

    def test_signals_blocked_when_data_not_live(self):
        fake_engine = SimpleNamespace(update_data=lambda force=False: {
            "mt5_connected": False,
            "currencies": [{"symbol": "CAD", "trade_bias": "COMPRA", "triads": {}}],
        })
        with patch.dict("sys.modules", {}), \
             patch.object(pe, "_atomic_write_json", lambda *a, **k: None), \
             patch.object(pe, "get_mt5_files_dir", lambda: None), \
             patch.object(cs, "css_engine", fake_engine):
            payload = pe.generate_and_save_daily_signals()
        self.assertFalse(payload["mt5_connected"])
        self.assertEqual(payload["portfolios"]["CAD"]["status"], "BLOCKED")
        self.assertEqual(payload["portfolios"]["CAD"]["direction"], "NEUTRAL")
        print("[✓] Dados sem conexão MT5 saem BLOCKED/NEUTRAL, não viram ordem")


class TestCatastrophicStopLoss(unittest.TestCase):
    def test_pip_size_jpy_vs_non_jpy(self):
        self.assertEqual(pe._pip_size("USDJPY"), 0.01)
        self.assertEqual(pe._pip_size("EURUSD"), 0.0001)

    def test_sl_below_entry_for_buy(self):
        sl = pe._compute_catastrophic_sl("EURUSD", True, 1.1000)
        self.assertLess(sl, 1.1000)

    def test_sl_above_entry_for_sell(self):
        sl = pe._compute_catastrophic_sl("EURUSD", False, 1.1000)
        self.assertGreater(sl, 1.1000)

    def test_sl_disabled_returns_zero(self):
        with patch.object(pe, "CATASTROPHIC_SL_PIPS", 0):
            sl = pe._compute_catastrophic_sl("EURUSD", True, 1.1000)
        self.assertEqual(sl, 0.0)
        print("[✓] Stop-loss catastrófico: direção correta pro lado e desligável só via config explícita")


class TestAtomicWrite(unittest.TestCase):
    def test_atomic_write_round_trips_and_leaves_no_temp_file(self):
        target = os.path.join(pe.DATA_DIR, "_test_atomic_write.json")
        try:
            pe._atomic_write_json(target, {"a": 1, "b": [1, 2, 3]})
            with open(target, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["a"], 1)
            leftovers = [f for f in os.listdir(pe.DATA_DIR) if f.startswith(".tmp_")]
            self.assertEqual(leftovers, [])
            print("[✓] Escrita atômica: conteúdo correto, sem arquivo temporário sobrando")
        finally:
            if os.path.exists(target):
                os.remove(target)


class TestCostModel(unittest.TestCase):
    """CostModel — spread+swap reais em USD, medidos no broker conectado.
    Movida de scripts/backtest_canonical.py pra agents/portfolio_executor.py
    (pedido do Breno: medir custo de verdade em vez de perguntar 'valor
    típico' pro Miquéias) — mesma classe, agora usada pelo executor ao vivo
    E pelo backtest, uma cópia só."""

    def test_leg_computes_spread_and_swap_in_usd(self):
        """Achado em revisão (Codex, achado 4 rodada 4): este teste dizia no
        nome "spread e swap", mas o dublê não tinha swap_mode (dependia do
        default antigo, que presumia PONTOS quando ausente — o mesmo
        fail-open que a rodada 4 fechou) e a asserção só checava spread.
        Agora o dublê declara swap_mode=PONTOS explicitamente e o teste
        também verifica swap_usd — passa a testar o que o nome promete."""
        fake_mt5 = MagicMock()
        fake_mt5.SYMBOL_SWAP_MODE_POINTS = 1

        def fake_symbol_info(sym):
            if sym == "USDJPYm":
                return SimpleNamespace(trade_contract_size=100000, point=0.01,
                                       swap_long=-5.0, swap_short=2.0, swap_mode=1)
            if sym == "USDCADm":  # usado pra converter CAD -> USD (par de referência)
                return SimpleNamespace(trade_contract_size=100000, point=0.0001,
                                       swap_long=0.0, swap_short=0.0, swap_mode=1)
            return None

        def fake_symbol_info_tick(sym):
            if sym == "USDJPYm":
                return SimpleNamespace(ask=150.02, bid=150.00)
            if sym == "USDCADm":
                return SimpleNamespace(ask=1.3502, bid=1.3500)
            return None

        fake_mt5.symbol_info.side_effect = fake_symbol_info
        fake_mt5.symbol_info_tick.side_effect = fake_symbol_info_tick

        with patch.object(pe, "mt5", fake_mt5), \
             patch.object(pe, "to_broker_symbol", lambda p: p + "m"):
            model = pe.CostModel(0.01)
            spread_usd, swap_usd = model.leg("USDJPY", "BUY")

        # USDJPY: quote=JPY, mas a conversão pro cálculo do par em si usa a
        # cotação do próprio par (spread em pontos de JPY * contrato * lote);
        # o que importa aqui é confirmar que saiu um número > 0 coerente com
        # spread de 2 pips num contrato padrão de 0.01 lote.
        self.assertGreater(spread_usd, 0)
        # BUY em USDJPY usa swap_long=-5.0 pontos — negativo, custo real.
        self.assertLess(swap_usd, 0, "swap_mode=PONTOS deveria calcular um swap não-zero")
        print(f"[✓] CostModel.leg() calcula spread E swap reais em USD: "
              f"${spread_usd:.4f} / ${swap_usd:.4f}")

    def test_leg_treats_missing_swap_mode_as_unmodeled_not_points(self):
        """A REGRESSÃO CENTRAL do achado em revisão (Codex, achado 4 rodada
        4): getattr(si, "swap_mode", swap_mode_points) tinha o MESMO
        fail-open que o achado 2 fechou pro trade_mode — campo ausente
        virava "presumir PONTOS", calculando um swap como se o modo
        estivesse confirmado. Objeto real do MT5 sempre expõe o campo
        (mesmo argumento do achado 2), mas o preflight já mostrou que
        vale a mesma disciplina mesmo sendo teórico em produção."""
        fake_mt5 = MagicMock()
        fake_mt5.SYMBOL_SWAP_MODE_POINTS = 1
        fake_mt5.symbol_info.return_value = SimpleNamespace(
            trade_contract_size=100000, point=0.0001,
            swap_long=-2.5, swap_short=1.0)  # sem swap_mode
        fake_mt5.symbol_info_tick.return_value = SimpleNamespace(ask=1.1002, bid=1.1000)
        with patch.object(pe, "mt5", fake_mt5), \
             patch.object(pe, "to_broker_symbol", lambda p: p + "m"):
            model = pe.CostModel(0.01)
            spread_usd, swap_usd = model.leg("EURUSD", "BUY")
        self.assertGreater(spread_usd, 0.0, "spread continua real")
        self.assertEqual(swap_usd, 0.0, "swap_mode ausente não pode calcular swap como PONTOS")
        self.assertIn(("EURUSD", "BUY", 0.01), model._swap_unmodeled,
                      "swap_mode ausente tem que cair em swap não modelado, não em PONTOS")
        print("[✓] swap_mode ausente é tratado como não modelado — não presume PONTOS")

    def test_leg_returns_zero_when_symbol_unavailable(self):
        fake_mt5 = MagicMock()
        fake_mt5.symbol_info.return_value = None
        fake_mt5.symbol_info_tick.return_value = None
        with patch.object(pe, "mt5", fake_mt5), \
             patch.object(pe, "to_broker_symbol", lambda p: p + "m"):
            model = pe.CostModel(0.01)
            spread_usd, swap_usd = model.leg("EURUSD", "BUY")
        self.assertEqual((spread_usd, swap_usd), (0.0, 0.0))
        print("[✓] CostModel.leg() nunca quebra — devolve zero quando o símbolo não resolve")

    def test_leg_retries_symbol_select_on_its_own_pair_before_giving_up(self):
        """Achado 4 rodada 2 (Codex + mfc-rev-2, achado confirmado pelos
        DOIS revisores independentemente): _usd_rate() já tentava
        symbol_select() nos pares de CONVERSÃO, mas leg() nunca tentava na
        PRÓPRIA perna — o comentário original assumia "o preflight já
        selecionou as 7 pernas", verdade no caminho ao vivo, falso no
        backtest canônico (scripts/backtest_canonical.py), que nunca roda
        open_portfolio_basket() e é o consumidor que decide se a estratégia
        é lucrativa líquida. Sem isto, medido: Market Watch vazio zerava
        7/7 pernas sempre no backtest."""
        fake_mt5 = MagicMock()
        fake_mt5.SYMBOL_SWAP_MODE_POINTS = 1

        def info_for(sym):
            # Só resolve DEPOIS do symbol_select — simula "existe no
            # servidor, mas não está selecionado no Market Watch".
            if sym == "EURUSDm" and fake_mt5.symbol_select.called:
                return SimpleNamespace(trade_contract_size=100000, point=0.0001,
                                       swap_long=0.0, swap_short=0.0, swap_mode=1)
            return None

        def tick_for(sym):
            if sym == "EURUSDm" and fake_mt5.symbol_select.called:
                return SimpleNamespace(ask=1.1002, bid=1.1000)
            return None

        fake_mt5.symbol_info.side_effect = info_for
        fake_mt5.symbol_info_tick.side_effect = tick_for
        with patch.object(pe, "mt5", fake_mt5), \
             patch.object(pe, "to_broker_symbol", lambda p: p + "m"):
            model = pe.CostModel(0.01)
            spread_usd, _ = model.leg("EURUSD", "BUY")
        fake_mt5.symbol_select.assert_called_once_with("EURUSDm", True)
        self.assertGreater(spread_usd, 0.0,
                           "symbol_select deveria ter destravado o símbolo da própria perna")
        print("[✓] leg() tenta symbol_select() na própria perna antes de desistir")

    def test_leg_treats_zero_price_tick_as_degraded_not_zero_cost(self):
        """Achado em revisão (Codex, achado 4 rodada 3): leg() só checava
        `tick is None` — um tick com ask==0 ou bid==0 (dado de mercado
        obviamente inválido) passava como "resolvido", gerando custo ZERO
        sem cair em _degraded. O preflight já trata isso como inválido
        (agents/portfolio_executor.py, no_tick); CostModel tinha que
        espelhar a mesma regra."""
        fake_mt5 = MagicMock()
        fake_mt5.SYMBOL_SWAP_MODE_POINTS = 1
        fake_mt5.symbol_info.return_value = SimpleNamespace(
            trade_contract_size=100000, point=0.0001,
            swap_long=0.0, swap_short=0.0, swap_mode=1)
        fake_mt5.symbol_info_tick.return_value = SimpleNamespace(ask=0.0, bid=0.0)
        with patch.object(pe, "mt5", fake_mt5), \
             patch.object(pe, "to_broker_symbol", lambda p: p + "m"):
            model = pe.CostModel(0.01)
            spread_usd, swap_usd = model.leg("EURUSD", "BUY")
        self.assertEqual((spread_usd, swap_usd), (0.0, 0.0))
        self.assertIn(("EURUSD", "BUY", 0.01), model._degraded,
                      "tick com preço zero não pode passar como medição completa")
        print("[✓] Tick com ask/bid zerado é tratado como degradado, não como custo real zero")

    def test_leg_treats_infinite_tick_as_degraded_not_valid(self):
        """Achado em revisão (Codex, rodada 6, medido): `ask > 0` sozinho
        não barra `float("inf")` — um tick com ask=inf passava por
        _tick_valido() antes desta correção, e um custo/preço infinito
        podia se propagar pro cálculo (e, no preflight/laço de envio, até
        pro order_send() e pro stop catastrófico). math.isfinite() nos dois
        lados fecha isso."""
        fake_mt5 = MagicMock()
        fake_mt5.SYMBOL_SWAP_MODE_POINTS = 1
        fake_mt5.symbol_info.return_value = SimpleNamespace(
            trade_contract_size=100000, point=0.0001,
            swap_long=0.0, swap_short=0.0, swap_mode=1)
        fake_mt5.symbol_info_tick.return_value = SimpleNamespace(ask=float("inf"), bid=1.1000)
        with patch.object(pe, "mt5", fake_mt5), \
             patch.object(pe, "to_broker_symbol", lambda p: p + "m"):
            model = pe.CostModel(0.01)
            spread_usd, _ = model.leg("EURUSD", "BUY")
        self.assertEqual(spread_usd, 0.0, "tick infinito nunca pode virar custo/preço")
        self.assertIn(("EURUSD", "BUY", 0.01), model._degraded)
        print("[✓] Tick com lado infinito (ask=inf) é tratado como degradado, não aceito")

    def test_leg_treats_crossed_market_tick_as_degraded(self):
        """Achado em revisão (Codex, achado 4 rodada 3): ask < bid (mercado
        cruzado — dado de tick claramente ruim) gerava spread NEGATIVO,
        subtraindo do custo total em vez de somar."""
        fake_mt5 = MagicMock()
        fake_mt5.SYMBOL_SWAP_MODE_POINTS = 1
        fake_mt5.symbol_info.return_value = SimpleNamespace(
            trade_contract_size=100000, point=0.0001,
            swap_long=0.0, swap_short=0.0, swap_mode=1)
        fake_mt5.symbol_info_tick.return_value = SimpleNamespace(ask=1.0998, bid=1.1000)
        with patch.object(pe, "mt5", fake_mt5), \
             patch.object(pe, "to_broker_symbol", lambda p: p + "m"):
            model = pe.CostModel(0.01)
            spread_usd, _ = model.leg("EURUSD", "BUY")
        self.assertEqual(spread_usd, 0.0, "spread negativo nunca pode sair de leg()")
        self.assertIn(("EURUSD", "BUY", 0.01), model._degraded)
        print("[✓] Tick com mercado cruzado (ask < bid) é tratado como degradado")

    def test_leg_retries_symbol_select_when_tick_is_invalid_not_just_none(self):
        """Achado em revisão (Claude, achado 4 rodada 4, medido): o retry de
        symbol_select só disparava com tick is None — um tick INVÁLIDO mas
        não nulo (ask=bid=0, sintoma clássico de símbolo ainda não presente
        no Market Watch, a MESMA causa que o retry existe pra curar) nunca
        acionava o retry. Simula: antes do symbol_select, tick zerado;
        depois, tick real."""
        fake_mt5 = MagicMock()
        fake_mt5.SYMBOL_SWAP_MODE_POINTS = 1
        fake_mt5.symbol_info.return_value = SimpleNamespace(
            trade_contract_size=100000, point=0.0001,
            swap_long=0.0, swap_short=0.0, swap_mode=1)

        def tick_for(sym):
            if fake_mt5.symbol_select.called:
                return SimpleNamespace(ask=1.1002, bid=1.1000)
            return SimpleNamespace(ask=0.0, bid=0.0)  # tick zerado, não None

        fake_mt5.symbol_info_tick.side_effect = tick_for
        with patch.object(pe, "mt5", fake_mt5), \
             patch.object(pe, "to_broker_symbol", lambda p: p + "m"):
            model = pe.CostModel(0.01)
            spread_usd, _ = model.leg("EURUSD", "BUY")
        fake_mt5.symbol_select.assert_called_once_with("EURUSDm", True)
        self.assertGreater(spread_usd, 0.0,
                           "symbol_select deveria ter destravado o tick zerado")
        print("[✓] Retry de symbol_select cobre tick inválido (zerado), não só tick None")

    def test_usd_rate_retries_symbol_select_before_giving_up(self):
        """Achado 4 (revisão de ad44e12/c24a44c, mfc-rev-2): o gatilho mais
        provável de degradação — o par de CONVERSÃO (não uma das 7 pernas
        reais da cesta, essas o preflight já seleciona) pode não estar
        "selecionado" no Market Watch, e symbol_info_tick() devolve None
        mesmo o símbolo existindo. _usd_rate() agora tenta symbol_select()
        e reconsulta antes de desistir — mesma correção que o preflight já
        tem pros 7 pares reais (agents/portfolio_executor.py, preflight de
        open_portfolio_basket)."""
        fake_mt5 = MagicMock()

        def tick_for(sym):
            # GBPUSDm existe, mas só devolve tick depois do symbol_select.
            if sym == "GBPUSDm" and fake_mt5.symbol_select.called:
                return SimpleNamespace(ask=1.2502, bid=1.2500)
            return None

        fake_mt5.symbol_info_tick.side_effect = tick_for
        with patch.object(pe, "mt5", fake_mt5), \
             patch.object(pe, "to_broker_symbol", lambda p: p + "m"):
            model = pe.CostModel(0.01)
            rate = model._usd_rate("GBP")
        fake_mt5.symbol_select.assert_called_once_with("GBPUSDm", True)
        self.assertEqual(rate, 1.2500)
        print("[✓] _usd_rate() tenta symbol_select() antes de desistir do par de conversão")

    def test_usd_rate_rejects_conversion_tick_with_one_side_zeroed(self):
        """Achado em revisão (mfc-rev-2, achado 2/4 rodada 5, P2-2, medido):
        antes só checava "tick is None e tick.bid > 0" — um tick de
        CONVERSÃO com ask=0 (campo não usado por este par, já que o par
        direto usa só bid) nunca era checado, e podia gerar uma taxa a
        partir de dado inválido sem marcar degradação. Simula GBPUSD com
        ask=0, bid positivo: o par direto (invert=False) usa só bid, mas
        _tick_valido() agora exige os dois lados."""
        fake_mt5 = MagicMock()
        fake_mt5.symbol_info_tick.return_value = SimpleNamespace(ask=0.0, bid=1.2500)
        with patch.object(pe, "mt5", fake_mt5), \
             patch.object(pe, "to_broker_symbol", lambda p: p + "m"):
            model = pe.CostModel(0.01)
            rate = model._usd_rate("GBP")
        self.assertIsNone(rate,
                          "ask=0 no par de conversão tem que recusar a taxa, mesmo o par "
                          "direto usando só o bid pra este cálculo")
        print("[✓] _usd_rate() recusa tick de conversão com um lado zerado, não só quando "
              "o lado USADO está inválido")

    def test_leg_computes_swap_when_mode_is_points(self):
        fake_mt5 = MagicMock()
        fake_mt5.SYMBOL_SWAP_MODE_POINTS = 1
        fake_mt5.symbol_info.return_value = SimpleNamespace(
            trade_contract_size=100000, point=0.0001,
            swap_long=-2.5, swap_short=1.0, swap_mode=1)
        fake_mt5.symbol_info_tick.return_value = SimpleNamespace(ask=1.1002, bid=1.1000)
        with patch.object(pe, "mt5", fake_mt5), \
             patch.object(pe, "to_broker_symbol", lambda p: p + "m"):
            model = pe.CostModel(0.01)
            _, swap_usd = model.leg("EURUSD", "BUY")
        self.assertNotEqual(swap_usd, 0.0)
        print(f"[✓] swap_mode=PONTOS: swap calculado normalmente (${swap_usd:.4f})")

    def test_leg_skips_swap_when_mode_is_not_points(self):
        """Regressão (achado ALTO em revisão): MT5 também aceita swap em
        moeda base/margem/depósito e percentual — a fórmula em pontos dá
        número ERRADO nesses casos. Reporta 0.0 em vez de fingir uma
        precisão que não existe. Achado em revisão (Codex, recorrente
        desde a rodada 2 do achado 4): "subestima, nunca infla" (frase
        antiga deste docstring) só vale se o swap real for sempre débito —
        MT5 também aceita swap CRÉDITO (positivo), caso em que zerar na
        verdade INFLA o custo reportado (deixa de contar um alívio real).
        O efeito correto é "produz um número diferente do real, sinal
        desconhecido", não uma direção garantida."""
        fake_mt5 = MagicMock()
        fake_mt5.SYMBOL_SWAP_MODE_POINTS = 1
        fake_mt5.symbol_info.return_value = SimpleNamespace(
            trade_contract_size=100000, point=0.0001,
            swap_long=-2.5, swap_short=1.0, swap_mode=5)  # 5 = não é pontos
        fake_mt5.symbol_info_tick.return_value = SimpleNamespace(ask=1.1002, bid=1.1000)
        with patch.object(pe, "mt5", fake_mt5), \
             patch.object(pe, "to_broker_symbol", lambda p: p + "m"):
            model = pe.CostModel(0.01)
            spread_usd, swap_usd = model.leg("EURUSD", "BUY")
        self.assertEqual(swap_usd, 0.0)
        self.assertGreater(spread_usd, 0.0, "spread continua calculado normalmente")
        print("[✓] swap_mode != PONTOS: swap reportado como 0.0, spread não é afetado")

    def test_swap_mode_not_points_is_not_reported_as_degraded(self):
        """A REGRESSÃO CENTRAL do achado 4 rodada 2 (mfc-rev-2, medido): antes
        desta correção, swap_mode fora de PONTOS usava a MESMA bandeira que
        "sem símbolo/tick/taxa" — uma cesta com spread REAL medido e contado
        aparecia com 7/7 pernas "degradadas", disparando o alarme de PnL
        otimista em TODA cesta, toda noite, pra sempre, em qualquer corretora
        que use swap fora de pontos (comum: swap em moeda de depósito). Um
        alarme que nunca desliga é ignorado — o oposto do propósito do
        mecanismo. last_basket_degraded tem que ficar vazio aqui; a
        informação vai pra last_basket_swap_unmodeled, categoria separada."""
        fake_mt5 = MagicMock()
        fake_mt5.SYMBOL_SWAP_MODE_POINTS = 1
        fake_mt5.symbol_info.return_value = SimpleNamespace(
            trade_contract_size=100000, point=0.0001,
            swap_long=-2.5, swap_short=1.0, swap_mode=5)
        fake_mt5.symbol_info_tick.return_value = SimpleNamespace(ask=1.1002, bid=1.1000)
        with patch.object(pe, "mt5", fake_mt5), \
             patch.object(pe, "to_broker_symbol", lambda p: p + "m"):
            model = pe.CostModel(0.01)
            cost = model.basket("CAD", "BUY")
        all_pairs = {p["pair"] for p in pe.get_portfolio_pairs("CAD", "BUY")}
        self.assertGreater(cost, 0.0, "spread real de todas as 7 pernas tem que estar no custo")
        self.assertEqual(model.last_basket_degraded, set(),
                         "swap fora de pontos não é 'dado perdido' — spread é real")
        self.assertEqual(model.last_basket_swap_unmodeled, all_pairs,
                         "todas as 7 pernas têm swap_mode 5 — todas devem aparecer aqui")
        print("[✓] swap_mode != PONTOS não aparece como degradado — categoria separada")

    def test_basket_applies_leg_lots_when_provided(self):
        """Achado em revisão (/codex-r sobre o commit ad44e12, GAPS): o teste
        de scheduler_daemon.py que cobre lote-por-perna só prova que o
        MAPA é repassado pra uma função mockada — nunca prova que
        CostModel.basket() de fato USA lotes diferentes por perna no
        cálculo. Mede a cesta CAD duas vezes: uma com o mesmo lote em
        todas as pernas, outra com UMA perna 10x maior — o custo tem que
        ser maior na segunda (contradiz uma implementação que ignore
        leg_lots e sempre use o self.lot escalar)."""
        fake_mt5 = MagicMock()
        fake_mt5.SYMBOL_SWAP_MODE_POINTS = 1
        fake_mt5.symbol_info.return_value = SimpleNamespace(
            trade_contract_size=100000, point=0.0001,
            swap_long=0.0, swap_short=0.0, swap_mode=1)
        fake_mt5.symbol_info_tick.return_value = SimpleNamespace(ask=1.1002, bid=1.1000)
        with patch.object(pe, "mt5", fake_mt5), \
             patch.object(pe, "to_broker_symbol", lambda p: p + "m"):
            pairs = pe.get_portfolio_pairs("CAD", "BUY")
            uniform_lots = {p["pair"]: 0.01 for p in pairs}
            skewed_lots = dict(uniform_lots)
            skewed_lots[pairs[0]["pair"]] = 0.10  # uma perna 10x maior

            cost_uniform = pe.CostModel(0.01).basket("CAD", "BUY", uniform_lots)
            cost_skewed = pe.CostModel(0.01).basket("CAD", "BUY", skewed_lots)

        self.assertGreater(cost_skewed, cost_uniform,
                            "custo não mudou ao aumentar o lote de uma perna — "
                            "leg_lots está sendo ignorado no cálculo")
        print("[✓] CostModel.basket() usa o lote de cada perna do mapa, não só o escalar")

    def test_leg_cache_distinguishes_by_lot(self):
        """Achado em revisão (/codex-r sobre o commit ad44e12, GAPS): a
        chave do cache de leg() agora inclui o lote — prova que a MESMA
        perna calculada de novo com um lote DIFERENTE não devolve o valor
        velho (o que aconteceria se o cache ainda fosse só por
        (pair, action), ignorando o lote)."""
        fake_mt5 = MagicMock()
        fake_mt5.SYMBOL_SWAP_MODE_POINTS = 1
        fake_mt5.symbol_info.return_value = SimpleNamespace(
            trade_contract_size=100000, point=0.0001,
            swap_long=0.0, swap_short=0.0, swap_mode=1)
        fake_mt5.symbol_info_tick.return_value = SimpleNamespace(ask=1.1002, bid=1.1000)
        with patch.object(pe, "mt5", fake_mt5), \
             patch.object(pe, "to_broker_symbol", lambda p: p + "m"):
            model = pe.CostModel(0.01)
            spread_a, _ = model.leg("EURUSD", "BUY", lot=0.01)
            spread_b, _ = model.leg("EURUSD", "BUY", lot=0.05)
        self.assertNotAlmostEqual(spread_a, spread_b,
                                   msg="mesmo par/ação com lote diferente devolveu o mesmo "
                                       "spread — cache não está diferenciando por lote")
        self.assertAlmostEqual(spread_b, spread_a * 5,
                                msg="spread não escalou linearmente com o lote")
        print("[✓] Cache de leg() diferencia corretamente por lote — não devolve valor velho")

    def test_basket_reports_which_legs_degraded_to_zero(self):
        """Achado 4 (revisão de ad44e12/c24a44c, mfc-rev-2): quando um
        símbolo/tick/taxa de conversão falta, leg() devolve (0.0, 0.0) e o
        cálculo segue em frente — sem isso, "falha transitória de UM par"
        e "cesta com custo genuinamente zero" ficam indistinguíveis pra
        quem lê o log ou o backtest depois. basket() agora expõe, após a
        chamada, QUAIS pernas desta cesta específica caíram no zero."""
        fake_mt5 = MagicMock()
        fake_mt5.SYMBOL_SWAP_MODE_POINTS = 1

        def tick_for(sym):
            # Uma perna sem cotação (símbolo "sumido" bem na hora da
            # medição) — as outras 6 cotam normal.
            if sym.startswith("CADJPY"):
                return None
            return SimpleNamespace(ask=1.1002, bid=1.1000)

        fake_mt5.symbol_info.return_value = SimpleNamespace(
            trade_contract_size=100000, point=0.0001,
            swap_long=0.0, swap_short=0.0, swap_mode=1)
        fake_mt5.symbol_info_tick.side_effect = tick_for
        with patch.object(pe, "mt5", fake_mt5), \
             patch.object(pe, "to_broker_symbol", lambda p: p + "m"):
            model = pe.CostModel(0.01)
            model.basket("CAD", "BUY")
        self.assertEqual(model.last_basket_degraded, {"CADJPY"},
                         "CADJPY não teve tick — tem que aparecer como degradada")
        print("[✓] basket() expõe quais pernas caíram no zero por falta de dado, não só o "
              "número final")

    def test_basket_reports_no_degradation_when_every_leg_has_real_data(self):
        """O caminho feliz não pode acusar degradação nenhuma — sem isso, o
        alerta do achado 4 vira ruído em toda medição normal."""
        fake_mt5 = MagicMock()
        fake_mt5.SYMBOL_SWAP_MODE_POINTS = 1
        fake_mt5.symbol_info.return_value = SimpleNamespace(
            trade_contract_size=100000, point=0.0001,
            swap_long=0.0, swap_short=0.0, swap_mode=1)
        fake_mt5.symbol_info_tick.return_value = SimpleNamespace(ask=1.1002, bid=1.1000)
        with patch.object(pe, "mt5", fake_mt5), \
             patch.object(pe, "to_broker_symbol", lambda p: p + "m"):
            model = pe.CostModel(0.01)
            model.basket("CAD", "BUY")
        self.assertEqual(model.last_basket_degraded, set())
        print("[✓] Nenhuma perna degradada no caminho feliz — sem falso positivo")


class TestMeasureAndLogBasketCost(unittest.TestCase):
    """measure_and_log_basket_cost() — dado empírico próprio, acrescentado a
    cada cesta aberta com sucesso, sem depender de ninguém informar 'valor
    típico'. Roda DEPOIS da cesta aberta, nunca antes/durante — não pode
    atrasar nem arriscar o envio de ordem real."""

    def test_appends_entry_to_new_log_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "execution_cost_log.json")
            with patch.object(pe, "COST_LOG_FILE", log_path), \
                 patch.object(pe, "CostModel") as MockModel:
                MockModel.return_value.basket.return_value = 12.34
                MockModel.return_value.last_basket_degraded = set()
                MockModel.return_value.last_basket_swap_unmodeled = set()
                pe.measure_and_log_basket_cost("cad", "BUY", 0.01)
            with open(log_path, encoding="utf-8") as f:
                log = json.load(f)
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["currency"], "CAD")
        self.assertEqual(log[0]["bias"], "BUY")
        self.assertEqual(log[0]["cost_usd"], 12.34)
        print("[✓] Primeira medição cria o log com a entrada certa")

    def test_logs_degraded_legs_when_cost_model_reports_them(self):
        """Achado 4 (revisão de ad44e12/c24a44c, mfc-rev-2): a entrada
        precisa registrar QUAIS pernas ficaram sem dado real, não só o
        número final — sem isso "cesta cara mas medida direito" e "cesta
        com uma perna sem cotação" são a mesma entrada no log."""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "execution_cost_log.json")
            with patch.object(pe, "COST_LOG_FILE", log_path), \
                 patch.object(pe, "CostModel") as MockModel:
                MockModel.return_value.basket.return_value = 3.0
                MockModel.return_value.last_basket_degraded = {"CADJPY"}
                MockModel.return_value.last_basket_swap_unmodeled = set()
                pe.measure_and_log_basket_cost("cad", "BUY", 0.01)
            with open(log_path, encoding="utf-8") as f:
                log = json.load(f)
        self.assertEqual(log[0]["degraded"], ["CADJPY"])
        print("[✓] Entrada do log registra quais pernas ficaram sem dado real")

    def test_does_not_add_degraded_field_when_nothing_degraded(self):
        """O caminho feliz não pode ganhar um campo "degraded": [] em toda
        entrada — poluiria o log inteiro por uma checagem que quase nunca
        dispara."""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "execution_cost_log.json")
            with patch.object(pe, "COST_LOG_FILE", log_path), \
                 patch.object(pe, "CostModel") as MockModel:
                MockModel.return_value.basket.return_value = 12.34
                MockModel.return_value.last_basket_degraded = set()
                MockModel.return_value.last_basket_swap_unmodeled = set()
                pe.measure_and_log_basket_cost("cad", "BUY", 0.01)
            with open(log_path, encoding="utf-8") as f:
                log = json.load(f)
        self.assertNotIn("degraded", log[0])
        print("[✓] Sem degradação, o campo \"degraded\" nem aparece na entrada")

    def test_malformed_cost_model_attributes_are_treated_as_unreliable_not_complete(self):
        """A REGRESSÃO CENTRAL do fail-closed (achado em revisão: Codex +
        mfc-rev-2, achado 4 rodada 3, confirmado pelos dois). Remover o
        isinstance da rodada anterior sem validar nada deixava um
        MagicMock cru (truthy, mas itera vazio) produzir "[!] Custo
        PARCIAL ... 0 perna(s) sem dado real ()" — mensagem que se
        contradiz — e gravar "degraded": [] no log, o campo vazio que o
        teste de caminho feliz existe pra impedir. Um formato inesperado
        tem que ser tratado como cesta INTEIRA não confiável, nunca como
        cesta completa."""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "execution_cost_log.json")
            with patch.object(pe, "COST_LOG_FILE", log_path), \
                 patch.object(pe, "CostModel") as MockModel:
                MockModel.return_value.basket.return_value = 3.0
                # NÃO configura last_basket_degraded/last_basket_swap_unmodeled
                # de propósito — simula um CostModel mal formado.
                pe.measure_and_log_basket_cost("cad", "BUY", 0.01)
            with open(log_path, encoding="utf-8") as f:
                log = json.load(f)
        self.assertIn("degraded", log[0])
        self.assertNotEqual(log[0]["degraded"], [],
                           "formato inesperado não pode virar 'degraded': [] — "
                           "isso é indistinguível do caminho feliz")
        print("[✓] CostModel mal formado é tratado como cesta não confiável, não como "
              "medição completa")

    def test_appends_without_overwriting_previous_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "execution_cost_log.json")
            with patch.object(pe, "COST_LOG_FILE", log_path), \
                 patch.object(pe, "CostModel") as MockModel:
                MockModel.return_value.last_basket_degraded = set()
                MockModel.return_value.last_basket_swap_unmodeled = set()
                MockModel.return_value.basket.return_value = 5.0
                pe.measure_and_log_basket_cost("EUR", "SELL", 0.01)
                MockModel.return_value.basket.return_value = 8.0
                pe.measure_and_log_basket_cost("USD", "BUY", 0.01)
            with open(log_path, encoding="utf-8") as f:
                log = json.load(f)
        self.assertEqual(len(log), 2)
        self.assertEqual([e["currency"] for e in log], ["EUR", "USD"])
        print("[✓] Medições seguintes acrescentam ao histórico, não sobrescrevem")

    def test_concurrent_measurements_from_multiple_threads_lose_nothing(self):
        """Regressão (achado MÉDIO em revisão). O scheduler hoje mede as
        cestas de uma noite em LOTE, numa thread só (achado 3) — não é mais
        de onde vem a concorrência real. Mas measure_and_log_basket_cost()
        é função PÚBLICA (achado em revisão, Codex rodada 2 do achado 4): o
        endpoint manual de abertura e o daemon podem chamá-la ao mesmo
        tempo. Sem serializar o ciclo ler-modificar-gravar, duas chamadas
        concorrentes podiam ler o mesmo histórico e uma gravação apagar a
        outra silenciosamente (lost update). Um CostModel.basket()
        artificialmente lento alarga a janela de corrida — sem o lock, este
        teste perderia entradas."""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "execution_cost_log.json")
            n = 8

            def slow_model(lot):
                m = MagicMock()
                m.basket.side_effect = lambda ccy, bias, leg_lots=None: (time.sleep(0.02), 1.0)[1]
                m.last_basket_degraded = set()
                m.last_basket_swap_unmodeled = set()
                return m

            with patch.object(pe, "COST_LOG_FILE", log_path), \
                 patch.object(pe, "CostModel", side_effect=slow_model):
                threads = [
                    threading.Thread(target=pe.measure_and_log_basket_cost,
                                      args=(f"CCY{i}", "BUY", 0.01))
                    for i in range(n)
                ]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(timeout=5)

            with open(log_path, encoding="utf-8") as f:
                log = json.load(f)
        self.assertEqual(len(log), n, "entradas perdidas — gravação concorrente sem lock")
        self.assertEqual({e["currency"] for e in log}, {f"CCY{i}" for i in range(n)})
        print(f"[✓] {n} medições concorrentes: todas as {n} entradas sobrevivem, nenhuma perdida")

    def test_lock_is_actually_held_during_the_read_modify_write_section(self):
        """O teste acima (várias threads + time.sleep) é probabilístico: passa
        mesmo sem o lock, porque a corrida pode não se manifestar numa única
        execução (confirmado manualmente removendo o lock — o teste continuou
        passando). Este prova o lock de forma determinística.

        Achado em revisão (rodada 3): uma primeira versão pausava só dentro
        de _atomic_write_json (a GRAVAÇÃO final) — isso provaria o lock
        retido durante a escrita, mas não provaria nada sobre a LEITURA
        anterior (_read_cost_log + append). Uma implementação insegura que lê
        o arquivo e monta a entrada FORA do lock, e só adquire o lock pra
        gravar no fim, ainda passaria naquele teste (duas threads podiam ler
        o mesmo snapshot antes de qualquer uma escrever). Agora a pausa fica
        no PRIMEIRO passo da seção (_read_cost_log) — se o lock só for
        adquirido depois disso, acquire(blocking=False) consegue pegar o
        lock livre aqui, e o teste pega a falha. Intercepta `pe._read_cost_log`
        (atributo do próprio módulo) em vez de `os.path.exists` global — um
        patch global travou o pytest inteiro (a suíte usa os.path.exists nos
        próprios bastidores); patchear só o que `pe` chama é seguro."""
        entered_critical_section = threading.Event()
        release_writer = threading.Event()
        real_read_cost_log = pe._read_cost_log

        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "execution_cost_log.json")

            def blocking_read(path):
                entered_critical_section.set()
                release_writer.wait(timeout=5)
                return real_read_cost_log(path)

            with patch.object(pe, "COST_LOG_FILE", log_path), \
                 patch.object(pe, "CostModel") as MockModel, \
                 patch.object(pe, "_read_cost_log", side_effect=blocking_read):
                MockModel.return_value.basket.return_value = 1.0
                MockModel.return_value.last_basket_degraded = set()
                MockModel.return_value.last_basket_swap_unmodeled = set()
                writer = threading.Thread(
                    target=pe.measure_and_log_basket_cost, args=("CAD", "BUY", 0.01),
                    daemon=True)  # defesa extra: um bug neste teste não deve travar o processo
                writer.start()
                try:
                    self.assertTrue(entered_critical_section.wait(timeout=5),
                                     "thread nunca entrou na seção crítica")
                    got_lock = pe._COST_LOG_LOCK.acquire(blocking=False)
                    # Libera ANTES de assertar (não depois, e não só no ramo
                    # "if got_lock"): se a asserção abaixo falhar — exatamente
                    # o caso que este teste existe pra pegar —, uma exceção
                    # pularia a liberação e a thread writer, ainda presa
                    # esperando esse lock em with _COST_LOG_LOCK:, travaria
                    # pra sempre (não é daemon: travaria até o processo inteiro).
                    if got_lock:
                        pe._COST_LOG_LOCK.release()
                    self.assertFalse(
                        got_lock,
                        "lock NÃO estava retido logo no início da seção (leitura) — "
                        "duas threads podiam ler o mesmo snapshot e perder uma entrada")
                finally:
                    release_writer.set()
                    writer.join(timeout=5)

            self.assertTrue(pe._COST_LOG_LOCK.acquire(blocking=False),
                             "lock ficou retido depois que a thread terminou")
            pe._COST_LOG_LOCK.release()
        print("[✓] Lock comprovadamente retido desde o início da leitura até o fim da "
              "gravação (não por sorte de timing, e não só durante a escrita)")

    def test_second_measurement_for_same_stuck_currency_is_skipped(self):
        """Achado em revisão /dual-r: sem timeout, uma chamada MT5 travada
        (IPC do terminal preso) deixa a thread de medição presa pra sempre —
        Python não tem como cancelá-la por dentro. Isto não cura a chamada
        travada, só evita empilhar mais uma pra a MESMA moeda: uma segunda
        medição de CAD enquanto a primeira ainda está presa desiste na hora
        em vez de abrir outra thread condenada a nunca voltar."""
        entered = threading.Event()
        release = threading.Event()

        def stuck_model(lot):
            m = MagicMock()

            def stuck_basket(ccy, bias, leg_lots=None):
                entered.set()
                release.wait(timeout=5)
                return 1.0
            m.basket.side_effect = stuck_basket
            m.last_basket_degraded = set()
            m.last_basket_swap_unmodeled = set()
            return m

        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "execution_cost_log.json")
            with patch.object(pe, "COST_LOG_FILE", log_path), \
                 patch.object(pe, "CostModel", side_effect=stuck_model):
                stuck_thread = threading.Thread(
                    target=pe.measure_and_log_basket_cost, args=("CAD", "BUY", 0.01),
                    daemon=True)
                stuck_thread.start()
                self.assertTrue(entered.wait(timeout=5), "thread nunca entrou na medição")

                # Segunda medição da MESMA moeda enquanto a primeira está presa:
                # tem que desistir NA HORA — se ela também tentasse medir,
                # cairia no mesmo stuck_basket() e só voltaria depois do
                # release.wait(timeout=5) abaixo (ele só é setado DEPOIS desta
                # chamada síncrona retornar), então o tempo decorrido aqui é o
                # que realmente prova a diferença: ~0s com o guard, ~5s sem.
                start = time.monotonic()
                pe.measure_and_log_basket_cost("CAD", "SELL", 0.02)
                elapsed = time.monotonic() - start
                self.assertLess(
                    elapsed, 1.0,
                    f"segunda medição da mesma moeda presa levou {elapsed:.2f}s — não desistiu "
                    f"na hora, esperou a primeira liberar (abriu outra thread condenada)")

                release.set()
                stuck_thread.join(timeout=5)

                # Achado em revisão (/codex-r sobre o commit ad44e12, GAPS):
                # provar só que a SEGUNDA medição foi pulada não prova que o
                # guard é removido depois — uma implementação que nunca tira
                # a moeda do registro (bug de "trava permanente") passaria
                # pelas asserções acima do mesmo jeito. Uma TERCEIRA medição,
                # já com a primeira thread terminada, tem que funcionar
                # normalmente.
                with patch.object(pe, "CostModel") as free_model:
                    free_model.return_value.basket.return_value = 2.0
                    free_model.return_value.last_basket_degraded = set()
                    free_model.return_value.last_basket_swap_unmodeled = set()
                    pe.measure_and_log_basket_cost("CAD", "BUY", 0.01)

            with open(log_path, encoding="utf-8") as f:
                log = json.load(f)
        self.assertFalse(stuck_thread.is_alive(), "thread travada nunca terminou")
        self.assertEqual(len(log), 2,
                          "a segunda medição (pulada) gravou algo, ou a terceira "
                          "(depois de liberar) NÃO gravou — guard preso pra sempre")
        print("[✓] Segunda medição da mesma moeda presa desiste na hora, não empilha outra "
              "thread, e uma terceira medição depois de liberar volta a funcionar normalmente")

    def test_stuck_currency_never_blocks_a_different_currency(self):
        """A trava é POR MOEDA, não um teto global — moedas diferentes nunca
        competem entre si, só a mesma moeda com ela mesma. Usa DUAS moedas
        presas (CAD e USD) e verifica que uma TERCEIRA (EUR) ainda mede
        normalmente (achado em revisão /codex-r sobre o commit ad44e12,
        GAPS): com só uma moeda presa, um teto global tipo
        threading.Semaphore(2) passaria por engano — sobraria 1 vaga livre.
        Com DUAS presas, um semáforo(2) já estaria esgotado e bloquearia a
        terceira; o registro por moeda não bloqueia."""
        entered = {"CAD": threading.Event(), "USD": threading.Event()}
        release = threading.Event()

        def stuck_model(lot):
            m = MagicMock()

            def stuck_basket(ccy, bias, leg_lots=None):
                entered[ccy].set()
                release.wait(timeout=5)
                return 1.0
            m.basket.side_effect = stuck_basket
            m.last_basket_degraded = set()
            m.last_basket_swap_unmodeled = set()
            return m

        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "execution_cost_log.json")
            with patch.object(pe, "COST_LOG_FILE", log_path), \
                 patch.object(pe, "CostModel", side_effect=stuck_model):
                stuck_threads = [
                    threading.Thread(target=pe.measure_and_log_basket_cost,
                                      args=(ccy, "BUY", 0.01), daemon=True)
                    for ccy in ("CAD", "USD")
                ]
                for t in stuck_threads:
                    t.start()
                for ev in entered.values():
                    self.assertTrue(ev.wait(timeout=5), "thread nunca entrou na medição")

                # Terceira moeda, DIFERENTE das duas presas: não pode ser afetada.
                with patch.object(pe, "CostModel") as free_model:
                    free_model.return_value.basket.return_value = 5.0
                    free_model.return_value.last_basket_degraded = set()
                    free_model.return_value.last_basket_swap_unmodeled = set()
                    pe.measure_and_log_basket_cost("EUR", "BUY", 0.01)
                with open(log_path, encoding="utf-8") as f:
                    log = json.load(f)
                self.assertEqual([e["currency"] for e in log], ["EUR"])

                release.set()
                for t in stuck_threads:
                    t.join(timeout=5)
        print("[✓] Terceira moeda mede normalmente mesmo com DUAS outras presas ao mesmo tempo")

    def test_never_raises_even_when_measurement_fails_completely(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "execution_cost_log.json")
            with patch.object(pe, "COST_LOG_FILE", log_path), \
                 patch.object(pe, "CostModel", side_effect=RuntimeError("MT5 fora do ar")):
                pe.measure_and_log_basket_cost("CAD", "BUY", 0.01)  # não pode lançar
        self.assertFalse(os.path.exists(log_path))
        print("[✓] Falha na medição nunca propaga — é só observação")

    def test_never_overwrites_history_when_existing_log_is_corrupted(self):
        """Regressão (achado em revisão): antes, um log ilegível (JSON
        corrompido, formato inesperado) virava silenciosamente um log NOVO
        de 1 entrada só — apagando meses de histórico acumulado. Agora
        recusa gravar em vez de sobrescrever."""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "execution_cost_log.json")
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("{isso não é json válido")
            original_content = open(log_path, encoding="utf-8").read()

            with patch.object(pe, "COST_LOG_FILE", log_path), \
                 patch.object(pe, "CostModel") as MockModel:
                MockModel.return_value.basket.return_value = 9.99
                MockModel.return_value.last_basket_degraded = set()
                MockModel.return_value.last_basket_swap_unmodeled = set()
                pe.measure_and_log_basket_cost("EUR", "SELL", 0.01)

            with open(log_path, encoding="utf-8") as f:
                after_content = f.read()
        self.assertEqual(after_content, original_content,
                          "log corrompido foi sobrescrito — histórico anterior teria sido perdido")
        print("[✓] Log corrompido/ilegível NÃO é sobrescrito — histórico anterior preservado")


class TestSignalsFileWriteFailurePropagates(unittest.TestCase):
    """Regressão ALTO (achado em revisão, rodada 7): generate_and_save_daily_signals()
    engolia a exceção da escrita em SIGNALS_FILE (só imprimia e continuava),
    então mesmo com o disco cheio/sem permissão/qualquer falha real de
    escrita, a função devolvia o payload normalmente. Isso fazia
    execute_phase_2102() 'ter sucesso' (nenhuma exceção chegava até ele) e
    liberar 21:05 pra abrir com o que já estivesse em disco — possivelmente
    um sinal de mais cedo hoje, ou de ontem. Agora a falha na escrita LOCAL
    propaga; só a escrita best-effort na pasta MQL5/Files continua capturada."""

    def test_local_write_failure_propagates_instead_of_being_swallowed(self):
        def boom(*a, **k):
            raise OSError("disco cheio (simulado)")

        with patch.object(pe, "_atomic_write_json", side_effect=boom), \
             patch.object(pe, "get_mt5_files_dir", lambda: None):
            with self.assertRaises(OSError):
                pe.generate_and_save_daily_signals(
                    currencies_data=[{"symbol": "CAD", "trade_bias": "COMPRA", "triads": {}}],
                    mt5_connected=True)
        print("[✓] Falha na escrita de SIGNALS_FILE propaga — não fica escondida num print")

    def test_mql5_files_write_failure_still_does_not_propagate(self):
        """A escrita local (a que importa pra execute_phase_2105) continua
        best-effort na cópia MQL5/Files — só o EA em modo legado lê aquilo."""
        calls = {"n": 0}

        def local_ok_mt5_boom(path, payload):
            calls["n"] += 1
            if calls["n"] == 2:  # 2ª chamada é a cópia em MQL5/Files
                raise OSError("pasta MQL5/Files inacessível (simulado)")

        with patch.object(pe, "_atomic_write_json", side_effect=local_ok_mt5_boom), \
             patch.object(pe, "get_mt5_files_dir", lambda: "/tmp/fake-mql5-files"):
            payload = pe.generate_and_save_daily_signals(
                currencies_data=[{"symbol": "CAD", "trade_bias": "COMPRA", "triads": {}}],
                mt5_connected=True)
        self.assertIn("portfolios", payload)
        self.assertEqual(calls["n"], 2)
        print("[✓] Falha só na cópia MQL5/Files não derruba a função — sinal local já foi gravado")

    def test_execute_phase_2102_returns_false_on_real_local_write_failure(self):
        """Ponta a ponta: a mesma falha real de escrita, vista por
        execute_phase_2102() — precisa devolver False, não True."""
        import scripts.scheduler_daemon as daemon

        def boom(*a, **k):
            raise OSError("disco cheio (simulado)")

        with patch.object(pe, "_atomic_write_json", side_effect=boom), \
             patch.object(pe, "get_mt5_files_dir", lambda: None), \
             patch.object(cs, "css_engine",
                          SimpleNamespace(update_data=lambda force=False: {
                              "mt5_connected": True,
                              "currencies": [{"symbol": "CAD", "trade_bias": "COMPRA", "triads": {}}],
                          })):
            result = daemon.execute_phase_2102()
        self.assertFalse(result)
        print("[✓] execute_phase_2102() devolve False numa falha REAL de escrita (não só quando mockado direto)")


class TestScheduledOpenTrigger(unittest.TestCase):
    """Fase 1b: quem abre por padrão agora é o Python, disparado às 21:05 BRT
    pelo scheduler_daemon.py (execute_phase_2105), lendo o sinal gravado às
    21:02. O EA vira guardião de fechamento (InpEaOpensBasket=false por
    padrão no .mq5 — não testável aqui, é MQL5, mas o lado Python que
    substitui a abertura dele precisa estar coberto)."""

    @staticmethod
    def _signals(date_str=None, mt5_connected=True):
        from datetime import datetime as _dt
        return {
            "date": date_str or _dt.now().strftime("%Y-%m-%d"),
            "mt5_connected": mt5_connected,
            "portfolios": {
                "CAD": {"direction": "BUY", "status": "ACTIVE"},
                "USD": {"direction": "SELL", "status": "ACTIVE"},
                "EUR": {"direction": "NEUTRAL", "status": "BLOCKED"},
            },
        }

    def _run_phase(self, payload):
        import scripts.scheduler_daemon as daemon
        with patch.object(daemon, "SIGNALS_FILE", "/dev/null"), \
             patch("builtins.open", mock_open(read_data=json.dumps(payload))), \
             patch.object(daemon, "open_portfolio_basket") as mock_open_basket:
            mock_open_basket.return_value = {"success": True, "opened_count": 7, "total_pairs": 7}
            daemon.execute_phase_2105()
        return mock_open_basket

    def test_execute_phase_2105_opens_only_active_currencies_with_right_direction(self):
        mock_open_basket = self._run_phase(self._signals())
        # Assere moeda E direção: um bug que invertesse BUY/SELL é a classe de
        # erro mais cara aqui, e passava verde quando só a moeda era checada.
        called = {(c.args[0], c.args[1]) for c in mock_open_basket.call_args_list}
        self.assertEqual(called, {("CAD", "BUY"), ("USD", "SELL")})
        print("[✓] execute_phase_2105 abre só as ACTIVE, com a direção correta de cada uma")

    def test_execute_phase_2105_measures_cost_only_on_full_success(self):
        """Custo medido só faz sentido pra cesta COMPLETA — uma parcial não é
        a cesta diversificada que o custo pretende caracterizar. A medição
        roda numa thread de lote (cost_batch, ver
        TestCostMeasurementNeverConcurrentWithOrders), não síncrona — o
        measured.wait(timeout=2) abaixo não é vestígio documental, é o que
        de fato impede este teste de correr contra essa thread de fundo."""
        import scripts.scheduler_daemon as daemon
        payload = self._signals()
        measured = threading.Event()
        calls = []

        def fake_measure(*args):
            calls.append(args)
            measured.set()

        with patch.object(daemon, "SIGNALS_FILE", "/dev/null"), \
             patch("builtins.open", mock_open(read_data=json.dumps(payload))), \
             patch.object(daemon, "open_portfolio_basket") as mock_open_basket, \
             patch.object(daemon, "measure_and_log_basket_cost", side_effect=fake_measure):
            mock_open_basket.side_effect = [
                {"success": True, "opened_count": 7, "total_pairs": 7,
                 "results": [{"pair": "EURCAD", "lot": 0.01}]},  # CAD: completa
                {"success": True, "opened_count": 5, "total_pairs": 7,
                 "results": [{"pair": "EURUSD", "lot": 0.01}]},  # USD: parcial
            ]
            daemon.execute_phase_2105()
            self.assertTrue(measured.wait(timeout=2), "custo nunca foi medido (thread de fundo)")
        self.assertEqual(calls, [("CAD", "BUY", 0.01, {"EURCAD": 0.01})])
        print("[✓] Custo só é medido pra cesta completa, nunca pra parcial")

    def test_execute_phase_2105_uses_average_lot_across_confirmed_legs(self):
        """Achado em revisão: uma perna com preenchimento parcial (volume
        diferente das outras) não pode ser representada pelo lote de UMA
        perna só. O campo "lot" gravado no log continua sendo a média
        (arredondada) das pernas confirmadas — só como resumo —, mas
        (achado em revisão /dual-r) o CÁLCULO do custo agora usa o mapa
        {pair: lote real} de cada perna, passado à parte."""
        import scripts.scheduler_daemon as daemon
        payload = self._signals()
        measured = threading.Event()
        calls = []

        def fake_measure(*args):
            calls.append(args)
            measured.set()

        cad_legs = [{"pair": f"PAIR{i}", "lot": 0.01} for i in range(6)] + \
                   [{"pair": "PAIR6", "lot": 0.006}]  # 1 perna parcial
        with patch.object(daemon, "SIGNALS_FILE", "/dev/null"), \
             patch("builtins.open", mock_open(read_data=json.dumps(payload))), \
             patch.object(daemon, "open_portfolio_basket") as mock_open_basket, \
             patch.object(daemon, "measure_and_log_basket_cost", side_effect=fake_measure):
            mock_open_basket.side_effect = [
                {"success": True, "opened_count": 7, "total_pairs": 7, "results": cad_legs},
                {"success": True, "opened_count": 7, "total_pairs": 7,
                 "results": [{"pair": f"PAIR{i}", "lot": 0.01} for i in range(7)]},
            ]
            daemon.execute_phase_2105()
            self.assertTrue(measured.wait(timeout=2))
            # Espera as duas medições (CAD e USD). Hoje a thread de lote as
            # faz em sequência, então o laço sai na primeira iteração; fica
            # como rede caso a medição volte a ser assíncrona por moeda.
            for _ in range(20):
                if len(calls) >= 2:
                    break
                time.sleep(0.05)
        cad_call = next(c for c in calls if c[0] == "CAD")
        self.assertAlmostEqual(cad_call[2], round((0.01 * 6 + 0.006) / 7, 4))
        expected_leg_lots = {leg["pair"]: leg["lot"] for leg in cad_legs}
        self.assertEqual(cad_call[3], expected_leg_lots)
        print("[✓] Campo \"lot\" é a média das pernas confirmadas, e o mapa por perna "
              "(usado no cálculo real do custo) chega intacto até measure_and_log_basket_cost")

    def test_execute_phase_2105_refuses_stale_signal(self):
        mock_open_basket = self._run_phase(self._signals(date_str="2020-01-01"))
        mock_open_basket.assert_not_called()
        print("[✓] Sinal com data de outro dia não abre nada (não opera direção de ontem)")

    def test_execute_phase_2105_refuses_non_live_signal(self):
        """Sinal derivado de cache/série simulada nunca vira ordem real."""
        mock_open_basket = self._run_phase(self._signals(mt5_connected=False))
        mock_open_basket.assert_not_called()
        print("[✓] Sinal com mt5_connected=false (cache/simulado) não abre nada")

    def test_execute_phase_2105_isolates_exception_per_currency(self):
        import scripts.scheduler_daemon as daemon
        payload = self._signals()
        with patch.object(daemon, "SIGNALS_FILE", "/dev/null"), \
             patch("builtins.open", mock_open(read_data=json.dumps(payload))), \
             patch.object(daemon, "open_portfolio_basket") as mock_open_basket:
            mock_open_basket.side_effect = [
                RuntimeError("MT5 caiu"),
                {"success": True, "opened_count": 7, "total_pairs": 7},
            ]
            daemon.execute_phase_2105()  # não pode propagar exceção
        self.assertEqual(mock_open_basket.call_count, 2)
        print("[✓] Exceção numa moeda não aborta as demais nem derruba o daemon")

    def test_test_mode_never_sends_real_orders(self):
        """Regressão: --test chamava execute_phase_2105, abrindo até 56
        posições reais em qualquer hora do dia."""
        import scripts.scheduler_daemon as daemon
        with patch.object(daemon, "execute_phase_2102") as m2102, \
             patch.object(daemon, "execute_phase_2105") as m2105, \
             patch.object(daemon, "execute_phase_0800") as m0800, \
             patch.object(daemon, "execute_phase_0805") as m0805:
            daemon.run_daemon_loop(test_mode=True)
        m2105.assert_not_called()
        m0800.assert_not_called()
        self.assertTrue(m2102.called and m0805.called)
        print("[✓] --test não dispara nenhuma fase que envia ordem real")


class TestCostMeasurementNeverConcurrentWithOrders(unittest.TestCase):
    """Regressão: a medição de custo era disparada numa thread DENTRO do laço
    de aberturas (`threading.Thread(...).start()` logo após cada cesta), e o
    laço seguia imediatamente para a moeda seguinte. As duas coisas usam o
    MESMO binding global do MetaTrader5: a medição chama symbol_info/
    symbol_info_tick enquanto a cesta seguinte chama order_send.

    O binding Python do MT5 não documenta thread-safety em lugar nenhum — a
    integração é descrita só como IPC com o terminal, e projetos do
    ecossistema divergem (uns serializam tudo num executor de thread única,
    outros não). Para código que envia ordem real, ausência de garantia é
    motivo suficiente para não depender dela.

    A correção não sincroniza os dois atores DENTRO de uma execução da fase:
    remove o segundo. A medição sai de dentro do laço e roda numa thread só,
    depois que todas as aberturas terminaram — sem lock e sem concorrência
    ENTRE MEDIÇÕES. A pergunta sobre thread-safety não fica irrelevante
    (por isso o guard por moeda em portfolio_executor.py permanece): uma
    medição presa numa noite pode, em tese, ainda estar rodando quando a
    fase da noite seguinte envia ordem — risco residual P3, aceito e descrito
    em scheduler_daemon.py."""

    @staticmethod
    def _signals():
        from datetime import datetime as _dt
        return {
            "date": _dt.now().strftime("%Y-%m-%d"),
            "mt5_connected": True,
            "portfolios": {
                "CAD": {"direction": "BUY", "status": "ACTIVE"},
                "USD": {"direction": "SELL", "status": "ACTIVE"},
            },
        }

    def _full_basket(self, pair):
        return {"success": True, "opened_count": 7, "total_pairs": 7,
                "results": [{"pair": pair, "lot": 0.01}]}

    def test_no_thread_is_spawned_while_baskets_are_opening(self):
        """A invariante é ausência de SOBREPOSIÇÃO, não ausência de thread:
        nada pode nascer ENTRE o primeiro e o último order_send. Um
        assert_not_called() na fase inteira mediria demais e vetaria o desenho
        correto, que dispara UMA thread depois do laço para não deixar o
        relógio do daemon refém de uma medição travada."""
        import scripts.scheduler_daemon as daemon
        payload = self._signals()
        threads_vivas_ao_abrir = []

        with patch.object(daemon, "SIGNALS_FILE", "/dev/null"), \
             patch("builtins.open", mock_open(read_data=json.dumps(payload))), \
             patch.object(daemon, "measure_and_log_basket_cost"), \
             patch.object(daemon.threading, "Thread") as mock_thread:

            def fake_open(ccy, direction):
                # Quantas threads já haviam nascido quando ESTA abertura
                # começou. Com a medição de volta pra dentro do laço, a
                # segunda abertura veria 1.
                threads_vivas_ao_abrir.append(mock_thread.call_count)
                return self._full_basket("EURCAD" if ccy == "CAD" else "EURUSD")

            with patch.object(daemon, "open_portfolio_basket", side_effect=fake_open):
                daemon.execute_phase_2105()

        self.assertEqual(threads_vivas_ao_abrir, [0, 0],
                         "nasceu thread no meio do laço de aberturas")
        self.assertEqual(mock_thread.call_count, 1,
                         "as medições devem rodar numa thread só, criada após o laço")
        # daemon=True é parte contratual da correção, não detalhe: sem ele o
        # processo não encerra se a medição travar (ver test_phase_returns_...
        # e o comentário em portfolio_executor.py sobre "processo que não
        # encerra"). Sem esta asserção, trocar pra daemon=False passa nos três
        # testes desta classe e reintroduz exatamente esse risco.
        self.assertIs(mock_thread.call_args.kwargs.get("daemon"), True,
                      "a thread de medição precisa ser daemon=True")
        print("[✓] Nenhuma thread nasce durante as aberturas; a medição usa uma só, depois, daemon=True")

    def test_every_basket_opens_before_any_cost_is_measured(self):
        """Ordem observável: todas as aberturas primeiro, todas as medições
        depois. A Thread falsa executa o alvo no próprio .start(), no ponto
        exato onde a real seria criada — o que torna a asserção determinística
        em vez de correr contra uma thread de fundo. Se a criação voltar pra
        dentro do laço, ("mede", "CAD") aparece antes de ("abre", "USD")."""
        import scripts.scheduler_daemon as daemon
        payload = self._signals()
        eventos = []

        class ThreadSincrona:
            def __init__(self, target=None, args=(), **kwargs):
                self._target, self._args = target, args

            def start(self):
                self._target(*self._args)

        def fake_open(ccy, direction):
            eventos.append(("abre", ccy))
            return self._full_basket("EURCAD" if ccy == "CAD" else "EURUSD")

        def fake_measure(ccy, *args):
            eventos.append(("mede", ccy))

        with patch.object(daemon, "SIGNALS_FILE", "/dev/null"), \
             patch("builtins.open", mock_open(read_data=json.dumps(payload))), \
             patch.object(daemon, "open_portfolio_basket", side_effect=fake_open), \
             patch.object(daemon, "measure_and_log_basket_cost", side_effect=fake_measure), \
             patch.object(daemon.threading, "Thread", ThreadSincrona):
            daemon.execute_phase_2105()

        self.assertEqual(eventos, [("abre", "CAD"), ("abre", "USD"),
                                   ("mede", "CAD"), ("mede", "USD")])
        print("[✓] Todas as cestas abrem antes de qualquer medição de custo")

    def test_phase_returns_even_if_a_cost_measurement_hangs(self):
        """O ponto da thread: uma medição presa (IPC do MT5 travado) não pode
        impedir execute_phase_2105 de retornar. run_daemon_loop a chama direto
        no seu while, então uma fase que não retorna nunca alcança o
        encerramento compulsório das 08:00 nem a reconciliação das 08:10 —
        perder o fechamento é pior que perder a medição.

        A fase roda numa thread do próprio teste e o que se assere é que ELA
        termina enquanto a medição segue presa. Esperar a medição destravar
        sozinha não serviria: o desenho síncrono também "passaria", só que
        depois de esperar o travamento inteiro."""
        import scripts.scheduler_daemon as daemon
        payload = {"date": self._signals()["date"], "mt5_connected": True,
                   "portfolios": {"CAD": {"direction": "BUY", "status": "ACTIVE"}}}
        travou = threading.Event()
        liberar = threading.Event()
        fase_retornou = threading.Event()

        def measure_que_trava(*args):
            travou.set()
            liberar.wait(timeout=60)     # alto de propósito: se a fase for
                                         # síncrona, ela fica presa aqui e a
                                         # asserção abaixo estoura antes

        with patch.object(daemon, "SIGNALS_FILE", "/dev/null"), \
             patch("builtins.open", mock_open(read_data=json.dumps(payload))), \
             patch.object(daemon, "open_portfolio_basket") as mock_open_basket, \
             patch.object(daemon, "measure_and_log_basket_cost",
                          side_effect=measure_que_trava):
            mock_open_basket.side_effect = [self._full_basket("EURCAD")]

            def roda_fase():
                daemon.execute_phase_2105()
                fase_retornou.set()

            fase = threading.Thread(target=roda_fase, name="fase_2105", daemon=True)
            fase.start()
            try:
                self.assertTrue(travou.wait(timeout=5),
                                "a medição nem chegou a rodar")
                self.assertTrue(
                    fase_retornou.wait(timeout=3),
                    "execute_phase_2105 ficou presa numa medição travada — o "
                    "laço do daemon não alcançaria o fechamento das 08:00")
            finally:
                liberar.set()
                fase.join(timeout=5)
        print("[✓] A fase 21:05 retorna mesmo com uma medição de custo travada")


class TestPhase2100Nonblocking(unittest.TestCase):
    """Regressão (achado F3): execute_phase_2100 rodava SÍNCRONO dentro do
    laço do scheduler_daemon (run_command com timeout=600 — pode levar até
    10 min). Se passasse de uns poucos minutos, os gatilhos exatos de 21:02 e
    21:05 (cur_min == X) eram perdidos POR INTEIRO naquela noite, porque o
    relógio do loop só é reavaliado depois da chamada síncrona terminar —
    sem nenhuma cesta aberta e sem nenhum aviso."""

    def test_loop_keeps_checking_later_triggers_while_phase_2100_still_running(self):
        """run_daemon_loop() roda numa thread de TESTE separada (não a
        principal), com um teto curto de espera pelo gatilho de 21:02 — se o
        código voltasse a chamar execute_phase_2100() de forma síncrona, essa
        thread ficaria bloqueada pra sempre em release.wait() (sem timeout,
        de propósito) e o gatilho de 21:02 nunca dispararia dentro do teto,
        falhando o teste de verdade. Um timeout no wait() mascararia
        exatamente esse bug (o código antigo acabaria destravando sozinho
        depois do timeout e o teste passaria mesmo estando quebrado)."""
        import scripts.scheduler_daemon as daemon

        started = threading.Event()
        release = threading.Event()
        phase_2102_called = threading.Event()

        def slow_phase_2100():
            started.set()
            release.wait()  # sem timeout — só quem já provou o resultado libera

        class _StopLoop(Exception):
            pass

        now_values = [
            datetime(2026, 8, 24, 20, 59, 0),  # consumido pelo print de banner
            datetime(2026, 8, 24, 21, 0, 0),
            datetime(2026, 8, 24, 21, 2, 0),
        ]

        def fake_sleep(_secs):
            if not now_values:
                raise _StopLoop()

        def runner():
            try:
                daemon.run_daemon_loop()
            except _StopLoop:
                pass

        try:
            with patch.object(daemon, "execute_phase_2100", side_effect=slow_phase_2100), \
                 patch.object(daemon, "execute_phase_2102", side_effect=phase_2102_called.set), \
                 patch.object(daemon, "datetime") as fake_dt, \
                 patch.object(daemon, "time") as fake_time:
                fake_dt.now.side_effect = lambda: now_values.pop(0)
                fake_time.sleep.side_effect = fake_sleep

                thread = threading.Thread(target=runner, daemon=True)
                thread.start()

                self.assertTrue(started.wait(timeout=2),
                                 "execute_phase_2100 nunca chegou a rodar")
                self.assertTrue(
                    phase_2102_called.wait(timeout=2),
                    "21:02 NÃO disparou enquanto a rotina das 21:00 ainda rodava — "
                    "o loop está bloqueado nela (regressão do achado F3)")
                print("[✓] 21:02 é checado e disparado mesmo com a rotina das 21:00 ainda em andamento")
        finally:
            release.set()  # nunca deixa a thread de fundo pendurada


class TestDailyRoutineDoesNotDuplicateSignalWrite(unittest.TestCase):
    """Regressão de condição de corrida (achado em revisão SOBRE a própria
    correção do F3): antes, execute_phase_2100 rodava SÍNCRONO, então a
    escrita de sinal dentro de daily_css_routine.py (passo 3.1, com dados
    capturados minutos antes de gráficos/Telegram) sempre terminava ANTES do
    scheduler sequer checar 21:02 — ordem garantida por acidente. Depois de
    passar a rodar em thread separada (subprocess concorrente de verdade), a
    escrita atrasada e OBSOLETA do passo 3.1 podia sobrescrever o sinal
    FRESCO gravado por execute_phase_2102, sem ordem garantida entre as
    duas. A correção removeu a escrita duplicada de daily_css_routine.py —
    só scheduler_daemon.py::execute_phase_2102 grava o sinal oficial agora."""

    def test_daily_routine_never_calls_generate_and_save_daily_signals(self):
        daily_routine_path = os.path.join(BASE_DIR, "daily_css_routine.py")
        with open(daily_routine_path, encoding="utf-8") as f:
            source = f.read()
        self.assertNotIn(
            "generate_and_save_daily_signals", source,
            "daily_css_routine.py voltou a gravar o sinal oficial — reintroduz a corrida "
            "com execute_phase_2102 (scripts/scheduler_daemon.py), que agora roda concorrente "
            "porque execute_phase_2100 é despachado numa thread em segundo plano")
        print("[✓] daily_css_routine.py não grava mais o sinal oficial (só execute_phase_2102 faz)")


class TestPhase2102GatesPhase2105ByCompletion(unittest.TestCase):
    """Regressão (achado em revisão, rodada 5): uma janela de relógio fixa
    pra 21:05 (era 5-9) não cobre com garantia o caso em que
    execute_phase_2102 demora mais que o previsto sob contenção de MT5 com
    o subprocesso da 21:00. A dependência agora é por EVENTO de conclusão
    real, não por coincidência de minuto: 21:05 só abre depois que
    phase_2102_done confirma que o sinal de HOJE terminou de gravar, não
    importa em que minuto isso aconteça (contanto que seja antes de 21:59)."""

    def test_phase_2105_never_opens_before_phase_2102_actually_finishes(self):
        import scripts.scheduler_daemon as daemon

        started = threading.Event()
        release = threading.Event()

        def slow_phase_2102():
            started.set()
            release.wait()  # sem timeout — só o teste libera, depois de provar o resultado

        class _StopLoop(Exception):
            pass

        # 21:02 dispara 2102 (fica bloqueada); 21:07 e 21:08 caem bem dentro
        # do que ERA a janela antiga de 21:05 (5-9) — não pode abrir, porque
        # 2102 ainda não terminou de verdade.
        now_values = [
            datetime(2026, 8, 24, 20, 59, 0),
            datetime(2026, 8, 24, 21, 2, 0),
            datetime(2026, 8, 24, 21, 7, 0),
            datetime(2026, 8, 24, 21, 8, 0),
        ]

        def fake_sleep(_secs):
            if not now_values:
                raise _StopLoop()

        def runner():
            try:
                daemon.run_daemon_loop()
            except _StopLoop:
                pass

        try:
            with patch.object(daemon, "execute_phase_2102", side_effect=slow_phase_2102), \
                 patch.object(daemon, "execute_phase_2105") as m2105, \
                 patch.object(daemon, "datetime") as fake_dt, \
                 patch.object(daemon, "time") as fake_time:
                fake_dt.now.side_effect = lambda: now_values.pop(0)
                fake_time.sleep.side_effect = fake_sleep

                thread = threading.Thread(target=runner, daemon=True)
                thread.start()

                self.assertTrue(started.wait(timeout=2), "execute_phase_2102 nunca chegou a rodar")
                thread.join(timeout=2)
                m2105.assert_not_called()
                print("[✓] 21:05 NÃO abre enquanto 21:02 ainda grava o sinal, mesmo passado o minuto 7-8")
        finally:
            release.set()  # nunca deixa a thread de fundo pendurada

    def test_phase_2105_fires_as_soon_as_confirmed_even_well_past_the_old_window(self):
        import scripts.scheduler_daemon as daemon

        phase_2105_called = threading.Event()

        class _StopLoop(Exception):
            pass

        # 2102 conclui rápido (mock sem bloqueio); só conseguimos checar o
        # relógio de novo às 21:12 — bem depois da antiga janela 5-9. Tem
        # que abrir mesmo assim, porque o sinal já foi confirmado.
        now_values = [
            datetime(2026, 8, 24, 20, 59, 0),
            datetime(2026, 8, 24, 21, 2, 0),
            datetime(2026, 8, 24, 21, 12, 0),
        ]

        def fake_sleep(_secs):
            if not now_values:
                raise _StopLoop()

        def runner():
            try:
                daemon.run_daemon_loop()
            except _StopLoop:
                pass

        with patch.object(daemon, "execute_phase_2102"), \
             patch.object(daemon, "execute_phase_2105", side_effect=phase_2105_called.set), \
             patch.object(daemon, "datetime") as fake_dt, \
             patch.object(daemon, "time") as fake_time:
            fake_dt.now.side_effect = lambda: now_values.pop(0)
            fake_time.sleep.side_effect = fake_sleep

            thread = threading.Thread(target=runner, daemon=True)
            thread.start()
            self.assertTrue(phase_2105_called.wait(timeout=2),
                             "21:05 nunca disparou, mesmo com o sinal das 21:02 já confirmado")
            thread.join(timeout=2)
        print("[✓] 21:05 abre em 21:12 (bem depois da janela antiga), pois 2102 já tinha confirmado conclusão")

    def test_gives_up_and_alerts_at_21_59_if_phase_2102_never_confirms(self):
        import scripts.scheduler_daemon as daemon

        class _StopLoop(Exception):
            pass

        now_values = [
            datetime(2026, 8, 24, 20, 59, 0),
            datetime(2026, 8, 24, 21, 59, 0),  # nunca passou por 21:02 — sinal nunca confirmado
        ]

        def fake_sleep(_secs):
            if not now_values:
                raise _StopLoop()

        with patch.object(daemon, "execute_phase_2105") as m2105, \
             patch.object(daemon, "datetime") as fake_dt, \
             patch.object(daemon, "time") as fake_time:
            fake_dt.now.side_effect = lambda: now_values.pop(0)
            fake_time.sleep.side_effect = fake_sleep
            with self.assertRaises(_StopLoop):
                daemon.run_daemon_loop()
        m2105.assert_not_called()
        print("[✓] 21:59 sem confirmação: desiste (não abre cesta com sinal nunca confirmado)")

    def test_phase_2105_never_fires_if_phase_2102_ran_but_failed(self):
        """Regressão (achado ALTO em revisão, rodada 6): antes, o Event era
        setado incondicionalmente no finally — 'a thread terminou' virava
        sinônimo de 'pode abrir', mesmo que execute_phase_2102 tivesse
        capturado uma exceção internamente e devolvido sem gravar nada.
        Agora execute_phase_2102() retorna False em falha, e o Event só é
        setado em sucesso."""
        import scripts.scheduler_daemon as daemon

        class _StopLoop(Exception):
            pass

        now_values = [
            datetime(2026, 8, 24, 20, 59, 0),
            datetime(2026, 8, 24, 21, 2, 0),
            datetime(2026, 8, 24, 21, 6, 0),
            datetime(2026, 8, 24, 21, 59, 0),
        ]

        def fake_sleep(_secs):
            if not now_values:
                raise _StopLoop()

        with patch.object(daemon, "execute_phase_2102", return_value=False), \
             patch.object(daemon, "execute_phase_2105") as m2105, \
             patch.object(daemon, "datetime") as fake_dt, \
             patch.object(daemon, "time") as fake_time:
            fake_dt.now.side_effect = lambda: now_values.pop(0)
            fake_time.sleep.side_effect = fake_sleep
            with self.assertRaises(_StopLoop):
                daemon.run_daemon_loop()
        m2105.assert_not_called()
        print("[✓] execute_phase_2102 retornando False (falhou) NUNCA libera a abertura das 21:05")

    def test_phase_2102_done_is_cleared_on_a_new_calendar_day_even_without_hitting_minute_2(self):
        """Regressão (achado MÉDIO em revisão, rodada 6): se o Event ficasse
        True de ONTEM e o minuto exato de 21:02 de HOJE fosse perdido por
        qualquer motivo, 21:05 poderia abrir com a confirmação de ontem
        ainda 'presa' no Event. A limpeza agora acontece na virada de dia,
        antes de qualquer checagem de gatilho — não só no disparo das 21:02."""
        import scripts.scheduler_daemon as daemon

        class _StopLoop(Exception):
            pass

        # Nunca passa pelo minuto 2 de hoje — só chega direto em 21:06.
        now_values = [
            datetime(2026, 8, 24, 20, 59, 0),
            datetime(2026, 8, 24, 21, 6, 0),
        ]

        def fake_sleep(_secs):
            if not now_values:
                raise _StopLoop()

        with patch.object(daemon, "execute_phase_2105") as m2105, \
             patch.object(daemon, "datetime") as fake_dt, \
             patch.object(daemon, "time") as fake_time:
            fake_dt.now.side_effect = lambda: now_values.pop(0)
            fake_time.sleep.side_effect = fake_sleep
            with self.assertRaises(_StopLoop):
                daemon.run_daemon_loop()
        m2105.assert_not_called()
        print("[✓] sem 21:02 hoje, 21:05 não abre — Event começa limpo a cada dia, não herda de ontem")


class TestSignalWriteWindowToleratesDelay(unittest.TestCase):
    """Regressão (achado em revisão): 08:10 é gatilho de MINUTO EXATO — se o
    loop só conseguisse checar o relógio de novo em 08:12 (por qualquer
    atraso), o reconciliador (rede de segurança contra posição órfã) nunca
    disparava naquela manhã, silenciosamente. Agora é uma janela tolerante
    (10-14)."""

    def test_phase_0810_fires_even_if_the_exact_minute_08_10_is_missed(self):
        import scripts.scheduler_daemon as daemon

        class _StopLoop(Exception):
            pass

        now_values = [
            datetime(2026, 8, 24, 7, 59, 0),   # banner
            datetime(2026, 8, 24, 8, 12, 0),   # loop só chega aqui às 08:12, não 08:10
        ]

        def fake_sleep(_secs):
            if not now_values:
                raise _StopLoop()

        with patch.object(daemon, "execute_phase_0800", return_value=True), \
             patch.object(daemon, "execute_phase_0805"), \
             patch.object(daemon, "execute_phase_0810") as m0810, \
             patch.object(daemon, "datetime") as fake_dt, \
             patch.object(daemon, "time") as fake_time:
            fake_dt.now.side_effect = lambda: now_values.pop(0)
            fake_time.sleep.side_effect = fake_sleep
            with self.assertRaises(_StopLoop):
                daemon.run_daemon_loop()
        m0810.assert_called_once()
        print("[✓] 08:10 (reconciliador) ainda dispara às 08:12 (dentro da janela)")

    def test_phase_0810_fires_even_very_late_in_the_hour(self):
        """Regressão (achado MÉDIO/ALTO em revisão, rodada 6): uma janela
        fechada em 5 min (10-14) podia ser inteiramente engolida se o
        fechamento das 08:00 retornasse tarde. A janela agora é ABERTA
        (10 até o fim da hora) — sem depender de nenhuma outra fase ter
        terminado, já que 0810 não lê nada que 0800/0805 produzam."""
        import scripts.scheduler_daemon as daemon

        class _StopLoop(Exception):
            pass

        now_values = [
            datetime(2026, 8, 24, 7, 59, 0),
            datetime(2026, 8, 24, 8, 45, 0),  # bem depois da antiga janela 10-14
        ]

        def fake_sleep(_secs):
            if not now_values:
                raise _StopLoop()

        with patch.object(daemon, "execute_phase_0800", return_value=True), \
             patch.object(daemon, "execute_phase_0805"), \
             patch.object(daemon, "execute_phase_0810") as m0810, \
             patch.object(daemon, "datetime") as fake_dt, \
             patch.object(daemon, "time") as fake_time:
            fake_dt.now.side_effect = lambda: now_values.pop(0)
            fake_time.sleep.side_effect = fake_sleep
            with self.assertRaises(_StopLoop):
                daemon.run_daemon_loop()
        m0810.assert_called_once()
        print("[✓] 08:10 (reconciliador) dispara mesmo às 08:45 — janela aberta até o fim da hora")


class TestPhase0805Nonblocking(unittest.TestCase):
    """Regressão (achado em revisão, mesmo padrão do F3 pra 21:00):
    execute_phase_0805 (sync_mt5_deals + build/deploy Firebase, dois
    run_command de até 120s cada) rodava SÍNCRONO dentro do loop — podia
    empurrar o relógio pra além do minuto exato de 08:10, fazendo o
    RECONCILIADOR (a rede de segurança real contra posição órfã) nunca
    disparar naquela manhã, silenciosamente."""

    def test_loop_keeps_checking_later_triggers_while_phase_0805_still_running(self):
        import scripts.scheduler_daemon as daemon

        started = threading.Event()
        release = threading.Event()
        phase_0810_called = threading.Event()

        def slow_phase_0805():
            started.set()
            release.wait()  # sem timeout — só o teste libera, depois de provar o resultado

        class _StopLoop(Exception):
            pass

        now_values = [
            datetime(2026, 8, 24, 7, 59, 0),  # banner
            datetime(2026, 8, 24, 8, 5, 0),
            datetime(2026, 8, 24, 8, 10, 0),
        ]

        def fake_sleep(_secs):
            if not now_values:
                raise _StopLoop()

        def runner():
            try:
                daemon.run_daemon_loop()
            except _StopLoop:
                pass

        try:
            with patch.object(daemon, "execute_phase_0805", side_effect=slow_phase_0805), \
                 patch.object(daemon, "execute_phase_0800", return_value=True), \
                 patch.object(daemon, "execute_phase_0810", side_effect=phase_0810_called.set), \
                 patch.object(daemon, "datetime") as fake_dt, \
                 patch.object(daemon, "time") as fake_time:
                fake_dt.now.side_effect = lambda: now_values.pop(0)
                fake_time.sleep.side_effect = fake_sleep

                thread = threading.Thread(target=runner, daemon=True)
                thread.start()

                self.assertTrue(started.wait(timeout=2),
                                 "execute_phase_0805 nunca chegou a rodar")
                self.assertTrue(
                    phase_0810_called.wait(timeout=2),
                    "08:10 (reconciliador) NÃO disparou enquanto 08:05 ainda rodava — "
                    "o loop está bloqueado nele")
                print("[✓] 08:10 é checado e disparado mesmo com 08:05 ainda em andamento")
        finally:
            release.set()  # nunca deixa a thread de fundo pendurada


if __name__ == "__main__":
    unittest.main()
