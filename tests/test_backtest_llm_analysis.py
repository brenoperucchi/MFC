"""
Testes da análise por LLM (perfil `backtest-analysis` do llm-gateway,
~/Devs/llm-gateway) anexada ao journal — scripts/_backtest_results_log.py
(attach_llm_analysis) + scripts/run_isolated_backtest.py (construção do
texto, chamada HTTP, e a integração dentro de _run_and_record()).

O gateway roda numa máquina separada (Omarchy), alcançado via túnel SSH
local (systemd --user mfc-llm-gateway-tunnel.service, porta 18080 na
Ryzen9) — nada disso está disponível neste checkout de desenvolvimento;
todo teste aqui mocka a chamada HTTP.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import scripts._backtest_results_log as bl
import scripts.run_isolated_backtest as rib

# httpx só é importado DENTRO de _call_backtest_analysis() (import tardio,
# de propósito — ver a função) — este checkout Linux de desenvolvimento não
# tem o pacote instalado por padrão (mesma situação de fastapi, ver
# CLAUDE.md). Só a classe que exercita a chamada HTTP de verdade (mockando
# httpx.post) precisa pular; as outras não dependem disso.
try:
    import httpx  # noqa: F401
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False


class TestAttachLlmAnalysis(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._path = os.path.join(self._tmpdir.name, "journal.json")
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump([
                {"journal_seq": 1, "note": "outra execução", "window": {}},
                {"journal_seq": 2, "note": "a que eu quero", "window": {}},
            ], f)
        patcher = patch.object(bl, "RESULTS_LOG_PATH", self._path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _read(self):
        with open(self._path, "r", encoding="utf-8") as f:
            return json.load(f)

    def test_attaches_to_the_correct_entry_by_journal_seq(self):
        analysis = {"summary": "5tf_port_a venceu por margem pequena.", "confidence": "media",
                    "caveats": [], "recommendation": "nada a fazer"}
        bl.attach_llm_analysis(2, analysis)
        log_after = self._read()
        self.assertIsNone(log_after[0].get("llm_analysis"))
        self.assertEqual(log_after[1]["llm_analysis"], analysis)

    def test_does_not_touch_other_fields(self):
        bl.attach_llm_analysis(2, {"summary": "x", "confidence": "baixa", "caveats": [], "recommendation": "y"})
        log_after = self._read()
        self.assertEqual(log_after[1]["note"], "a que eu quero")
        self.assertEqual(log_after[1]["journal_seq"], 2)

    def test_raises_when_journal_seq_does_not_exist(self):
        with self.assertRaises(ValueError):
            bl.attach_llm_analysis(999, {"summary": "x", "confidence": "baixa", "caveats": [], "recommendation": "y"})

    def test_llm_analysis_is_excluded_from_the_content_digest(self):
        """achado do Breno registrando o perfil não muda isto, mas é a
        garantia estrutural: uma anotação anexada DEPOIS do append não pode
        entrar num digest de integridade de conteúdo — ver
        _RESULT_DIGEST_EXCLUDED_FIELDS."""
        entry_before = {"journal_seq": 2, "note": "a que eu quero", "window": {}}
        digest_before = bl.result_snapshot_digest(entry_before)
        entry_after = dict(entry_before)
        entry_after["llm_analysis"] = {"summary": "x", "confidence": "alta", "caveats": [], "recommendation": "y"}
        digest_after = bl.result_snapshot_digest(entry_after)
        self.assertEqual(digest_before, digest_after)


class TestBuildLlmAnalysisText(unittest.TestCase):
    def test_includes_window_market_and_per_engine_metrics(self):
        entry = {
            "window": {"days": 45, "start_brt": "2026-07-16T21:00:00-03:00",
                       "end_brt": "2026-08-30T21:00:00-03:00", "nights_evaluated": 31},
            "engines": {
                "3tf_baseline": {"baskets": 186, "bruto": -61.39, "custo": 414.98,
                                  "liquido": -476.37, "noite_pct": 43.5, "cesta_pct": 43.5,
                                  "quality_status": "clean"},
                "5tf_port_a": {"baskets": 209, "bruto": 96.0, "custo": 476.51,
                               "liquido": -380.51, "noite_pct": 41.9, "cesta_pct": 43.5,
                               "quality_status": "clean"},
            },
            "paired_net_delta_per_night": {"mean": 3.092, "stderr": 6.006, "n": 31},
        }
        text = rib._build_llm_analysis_text(entry, market_open=True)
        self.assertIn("45 dias", text)
        self.assertIn("31 noites avaliadas", text)
        self.assertIn("aberto", text)
        self.assertIn("3tf_baseline", text)
        self.assertIn("5tf_port_a", text)
        self.assertIn("liquido=-476.37", text)
        self.assertIn("media=3.092", text)
        # nunca instrução nenhuma pro modelo aqui — isso mora no
        # system_prompt do perfil, no gateway (achado do Breno).
        self.assertNotIn("Você", text)
        self.assertNotIn("confidence", text.lower())

    def test_ranks_engines_by_liquido_so_the_model_never_compares(self):
        """Achado do llm-exec, confirmado independentemente pelo dre-exec:
        modelos de 14B erram comparação entre líquidos negativos ("qual é
        menos negativo"). A ordenação é feita AQUI, em Python — o texto já
        chega pronto, o modelo só redige a partir da ordem, nunca compara."""
        entry = {
            "window": {},
            "engines": {
                "3tf_baseline": {"liquido": -604.27},
                "5tf_port_a": {"liquido": -528.36},
                "5tf_upstream": {"liquido": -557.65},
                "3tf_vector": {"liquido": -251.64},
            },
            "paired_net_delta_per_night": {},
        }
        text = rib._build_llm_analysis_text(entry, market_open=True)
        ranked_line = next(line for line in text.splitlines() if line.startswith("Ordem por líquido"))
        # menos negativo (mais próximo de zero) primeiro -- é o "melhor"
        # entre líquidos todos negativos, e é justamente onde o modelo
        # errou nos dois smoke tests reais desta sessão.
        order = [chunk.split()[0] for chunk in ranked_line.split(": ", 1)[1].rstrip(".").split(", ")]
        self.assertEqual(order, ["3tf_vector", "5tf_port_a", "5tf_upstream", "3tf_baseline"])
        self.assertIn("3tf_vector -251.64", ranked_line)
        self.assertIn("3tf_baseline -604.27", ranked_line)

    def test_ranking_handles_a_mix_of_positive_and_negative_liquido(self):
        entry = {
            "window": {},
            "engines": {
                "mn1_v2": {"liquido": 30.0},
                "legacy": {"liquido": -290.0},
                "3tf_baseline": {"liquido": -1160.0},
            },
            "paired_net_delta_per_night": {},
        }
        text = rib._build_llm_analysis_text(entry, market_open=True)
        ranked_line = next(line for line in text.splitlines() if line.startswith("Ordem por líquido"))
        self.assertIn("mn1_v2 +30.00", ranked_line)
        order = [chunk.split()[0] for chunk in ranked_line.split(": ", 1)[1].rstrip(".").split(", ")]
        self.assertEqual(order, ["mn1_v2", "legacy", "3tf_baseline"])

    def test_omits_ranking_line_when_no_engine_has_a_numeric_liquido(self):
        text = rib._build_llm_analysis_text({"window": {}, "engines": {}}, market_open=True)
        self.assertNotIn("Ordem por líquido", text)

    def test_reports_closed_market(self):
        entry = {"window": {}, "engines": {}, "paired_net_delta_per_night": {}}
        text = rib._build_llm_analysis_text(entry, market_open=False)
        self.assertIn("fechado", text)

    def test_handles_missing_fields_without_raising(self):
        text = rib._build_llm_analysis_text({}, market_open=True)
        self.assertIsInstance(text, str)


@unittest.skipUnless(HTTPX_AVAILABLE, "httpx não instalado neste ambiente")
class TestCallBacktestAnalysis(unittest.TestCase):
    """NUNCA lança — a análise é sempre opcional/best-effort (ver docstring
    de _call_backtest_analysis)."""

    def test_returns_result_dict_on_success(self):
        fake_response = MagicMock()
        fake_response.raise_for_status = MagicMock()
        fake_response.json.return_value = {
            "result": {"summary": "x", "confidence": "alta", "caveats": [], "recommendation": "y"}
        }
        with patch("httpx.post", return_value=fake_response) as mocked_post:
            result = rib._call_backtest_analysis({"window": {}, "engines": {}}, market_open=True)
        self.assertEqual(result["summary"], "x")
        call = mocked_post.call_args
        self.assertEqual(call.args[0], f"{rib.LLM_GATEWAY_URL}/v1/tasks/backtest-analysis")
        self.assertEqual(call.kwargs["json"]["project"], "mfc")
        self.assertIn("text", call.kwargs["json"])
        self.assertNotIn("model", call.kwargs["json"])  # perfil decide o modelo, não o MFC
        self.assertGreaterEqual(call.kwargs["timeout"], 180.0)

    def test_returns_none_on_network_failure(self):
        with patch("httpx.post", side_effect=OSError("conexão recusada")):
            result = rib._call_backtest_analysis({"window": {}, "engines": {}}, market_open=True)
        self.assertIsNone(result)

    def test_returns_none_on_timeout(self):
        import httpx
        with patch("httpx.post", side_effect=httpx.TimeoutException("timeout")):
            result = rib._call_backtest_analysis({"window": {}, "engines": {}}, market_open=True)
        self.assertIsNone(result)

    def test_returns_none_when_http_status_is_an_error(self):
        import httpx
        fake_response = MagicMock()
        fake_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "502", request=MagicMock(), response=MagicMock()
        )
        with patch("httpx.post", return_value=fake_response):
            result = rib._call_backtest_analysis({"window": {}, "engines": {}}, market_open=True)
        self.assertIsNone(result)

    def test_returns_none_when_result_is_not_a_dict(self):
        fake_response = MagicMock()
        fake_response.raise_for_status = MagicMock()
        fake_response.json.return_value = {"result": "texto solto, não deveria acontecer"}
        with patch("httpx.post", return_value=fake_response):
            result = rib._call_backtest_analysis({"window": {}, "engines": {}}, market_open=True)
        self.assertIsNone(result)


class TestRunAndRecordLlmIntegration(unittest.TestCase):
    """A análise roda DEPOIS de status="done" já ter sido gravado, fora do
    lock dedicado (achado: não precisa da exclusividade, e segurar o lock
    aqui só estenderia sem necessidade a espera de outro disparo)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        for name, value in (
            ("TRIGGER_STATE_DIR", self._tmpdir.name),
            ("STATUS_PATH", os.path.join(self._tmpdir.name, "status.json")),
            ("OWNER_PID_PATH", os.path.join(self._tmpdir.name, "owner.pid")),
        ):
            p = patch.object(rib, name, value)
            p.start()
            self.addCleanup(p.stop)

        self._journal_path = os.path.join(self._tmpdir.name, "journal.json")
        with open(self._journal_path, "w", encoding="utf-8") as f:
            json.dump([], f)
        journal_patch = patch.object(bl, "RESULTS_LOG_PATH", self._journal_path)
        journal_patch.start()
        self.addCleanup(journal_patch.stop)

        env_patch = patch.dict(os.environ, {}, clear=False)
        env_patch.start()
        self.addCleanup(env_patch.stop)

        critical_patch = patch.object(rib, "in_critical_window", return_value=False)
        critical_patch.start()
        self.addCleanup(critical_patch.stop)
        market_patch = patch.object(rib, "market_is_open", return_value=True)
        market_patch.start()
        self.addCleanup(market_patch.stop)

    def _append_fake_journal_entry(self, note):
        with open(self._journal_path, "r", encoding="utf-8") as f:
            log_data = json.load(f)
        log_data.append({"journal_seq": len(log_data) + 1, "note": note, "window": {}, "engines": {}})
        with open(self._journal_path, "w", encoding="utf-8") as f:
            json.dump(log_data, f)

    def test_success_path_calls_analysis_and_attaches_it(self):
        import scripts.backtest_engine_compare as bec

        def fake_compare(**kwargs):
            self._append_fake_journal_entry(kwargs["log_note"])
            return 0

        analysis = {"summary": "x", "confidence": "alta", "caveats": [], "recommendation": "y"}
        with patch.object(bec, "compare", side_effect=fake_compare), \
             patch.object(rib, "_call_backtest_analysis", return_value=analysis) as mocked_call:
            ret = rib._run_and_record("desc", 1, "runid-llm-ok")

        self.assertEqual(ret, 0)
        mocked_call.assert_called_once()
        with open(self._journal_path, "r", encoding="utf-8") as f:
            log_data = json.load(f)
        self.assertEqual(log_data[-1]["llm_analysis"], analysis)
        status = rib.read_status()
        self.assertEqual(status["status"], "done")

    def test_failed_analysis_does_not_affect_done_status(self):
        """Achado central: um backtest bem-sucedido não pode falhar por
        causa de uma anotação opcional."""
        import scripts.backtest_engine_compare as bec

        def fake_compare(**kwargs):
            self._append_fake_journal_entry(kwargs["log_note"])
            return 0

        with patch.object(bec, "compare", side_effect=fake_compare), \
             patch.object(rib, "_call_backtest_analysis", return_value=None) as mocked_call:
            ret = rib._run_and_record("desc", 1, "runid-llm-fail")

        self.assertEqual(ret, 0)
        mocked_call.assert_called_once()
        status = rib.read_status()
        self.assertEqual(status["status"], "done")
        with open(self._journal_path, "r", encoding="utf-8") as f:
            log_data = json.load(f)
        self.assertNotIn("llm_analysis", log_data[-1])

    def test_analysis_is_never_called_for_skipped_or_failed_runs(self):
        import scripts.backtest_engine_compare as bec

        with patch.object(rib, "in_critical_window", return_value=True), \
             patch.object(bec, "compare") as mocked_compare, \
             patch.object(rib, "_call_backtest_analysis") as mocked_call:
            rib._run_and_record("desc", 1, "runid-skip")
        mocked_compare.assert_not_called()
        mocked_call.assert_not_called()

        with patch.object(bec, "compare", return_value=1), \
             patch.object(rib, "_call_backtest_analysis") as mocked_call2:
            rib._run_and_record("desc", 1, "runid-failed-compare")
        mocked_call2.assert_not_called()


if __name__ == "__main__":
    unittest.main()
