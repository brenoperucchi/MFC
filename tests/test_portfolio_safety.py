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
import json
import tempfile
import threading
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
        fake_mt5.symbol_info.return_value = SimpleNamespace(visible=True)
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
        fake_mt5.symbol_info.return_value = SimpleNamespace(visible=True)
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
        fake_mt5.symbol_info.return_value = SimpleNamespace(visible=True)
        fake_mt5.symbol_info_tick.return_value = SimpleNamespace(ask=1.1000, bid=1.0998)
        fake_mt5.order_send.return_value = SimpleNamespace(
            retcode=fake_mt5.TRADE_RETCODE_DONE, order=1, price=1.1000, comment="ok"
        )
        with patch.dict(os.environ, DEMO_GATE_ENV), \
             patch.object(pe, "MT5_AVAILABLE", True), patch.object(pe, "mt5", fake_mt5):
            result = pe.open_portfolio_basket("CAD", "BUY")
        self.assertTrue(fake_mt5.order_send.called)
        print("[✓] Conta hedging permite colisão de símbolo entre cestas diferentes")


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
        fake_mt5.symbol_info.return_value = SimpleNamespace(visible=True)
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

    def test_to_broker_symbol_applies_suffix(self):
        with patch.object(cs, "MT5_SYMBOL_SUFFIX", "m"), \
             patch.object(cs, "MT5_AVAILABLE", False), \
             patch.dict(cs._SYMBOL_RESOLUTION_CACHE, {}, clear=True):
            self.assertEqual(cs.to_broker_symbol("EURUSD"), "EURUSDm")

    def test_from_broker_symbol_strips_suffix(self):
        with patch.object(cs, "MT5_SYMBOL_SUFFIX", "m"):
            self.assertEqual(cs.from_broker_symbol("EURUSDm"), "EURUSD")
            self.assertEqual(cs.from_broker_symbol("EURUSD"), "EURUSD")

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
        fake_mt5.symbol_info.return_value = SimpleNamespace(visible=True)

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
        fake.symbol_info.return_value = SimpleNamespace(visible=True)
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
        fake_mt5.symbol_info.return_value = SimpleNamespace(visible=True)
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
        fake_mt5.symbol_info.return_value = SimpleNamespace(visible=True)
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
        fake_mt5.symbol_info.return_value = SimpleNamespace(visible=True)
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
        fake_mt5.symbol_info.return_value = SimpleNamespace(visible=True)
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
