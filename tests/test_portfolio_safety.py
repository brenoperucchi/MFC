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
    def setUp(self):
        if os.path.exists(pe.KILL_SWITCH_FILE):
            os.remove(pe.KILL_SWITCH_FILE)

    def tearDown(self):
        if os.path.exists(pe.KILL_SWITCH_FILE):
            os.remove(pe.KILL_SWITCH_FILE)

    def test_kill_switch_off_by_default(self):
        self.assertFalse(pe.is_kill_switch_active())

    def test_kill_switch_blocks_open_without_touching_mt5(self):
        with open(pe.KILL_SWITCH_FILE, "w") as f:
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

    def test_execute_phase_2105_opens_only_active_currencies(self):
        import scripts.scheduler_daemon as daemon

        fake_signals = {
            "date": "2026-08-23",
            "portfolios": {
                "CAD": {"direction": "BUY", "status": "ACTIVE"},
                "USD": {"direction": "SELL", "status": "ACTIVE"},
                "EUR": {"direction": "NEUTRAL", "status": "BLOCKED"},
            }
        }

        with patch.object(daemon, "SIGNALS_FILE", "/dev/null"), \
             patch("builtins.open", mock_open(read_data=json.dumps(fake_signals))), \
             patch.object(daemon, "open_portfolio_basket") as mock_open_basket:
            mock_open_basket.return_value = {"success": True, "opened_count": 7, "total_pairs": 7}
            daemon.execute_phase_2105()

        called_currencies = {call.args[0] for call in mock_open_basket.call_args_list}
        self.assertEqual(called_currencies, {"CAD", "USD"})
        print("[✓] execute_phase_2105 só abre as moedas com status ACTIVE, ignora NEUTRAL/BLOCKED")


if __name__ == "__main__":
    unittest.main()
