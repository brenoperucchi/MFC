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
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock, mock_open

sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import agents.portfolio_executor as pe
import web.css_service as cs


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
        import scripts.scheduler_daemon as daemon
        enviados = []

        def fake_get():
            if raise_query:
                raise pe.MT5QueryError("consulta falhou")
            return magics_abertos

        with patch.object(pe, "get_open_magics_and_symbols", fake_get), \
             patch.object(daemon, "close_all_portfolios",
                          lambda: close_result or {"success": True, "total_closed": 7}), \
             patch("web.telegram_service.send_telegram_message",
                   lambda text, **kw: enviados.append(text)):
            daemon.execute_phase_0810()
        return enviados

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


if __name__ == "__main__":
    unittest.main()
