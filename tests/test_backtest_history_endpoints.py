"""
TESTES DOS ENDPOINTS /api/backtest-history/* (acompanhamento de backtest via
web) — ver docs/plans/eventual-stargazing-bear.md pro desenho completo
(consulta herdr-ask mfc-13).

Pulado automaticamente se fastapi/uvicorn/httpx não estiverem instalados
neste ambiente — mesmo padrão de tests/test_portfolio_api_auth.py. Rodar de
verdade requer `pip install fastapi uvicorn` (ver CLAUDE.md); TestClient
usa httpx por baixo dos panos nas versões recentes de fastapi.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import pytest

try:
    from fastapi import HTTPException
    from fastapi.testclient import TestClient
    from pydantic import ValidationError
    import web.server as server
except ImportError as e:
    pytest.skip(f"stack web (fastapi/uvicorn/httpx) não instalado neste ambiente: {e}",
                 allow_module_level=True)


class TestRequireBacktestApiKey(unittest.TestCase):
    """Mesmo padrão fail-closed de _require_portfolio_api_key
    (tests/test_portfolio_api_auth.py) — chave DEDICADA
    (CSS_BACKTEST_API_KEY), nunca CSS_PORTFOLIO_API_KEY."""

    def test_fails_closed_when_no_key_configured(self):
        with patch.object(server, "BACKTEST_API_KEY", None):
            with self.assertRaises(HTTPException) as ctx:
                server._require_backtest_api_key(None)
        self.assertEqual(ctx.exception.status_code, 503)

    def test_rejects_wrong_key_when_one_is_configured(self):
        with patch.object(server, "BACKTEST_API_KEY", "segredo-backtest"):
            with self.assertRaises(HTTPException) as ctx:
                server._require_backtest_api_key("chave-errada")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_accepts_the_correct_key(self):
        with patch.object(server, "BACKTEST_API_KEY", "segredo-backtest"):
            server._require_backtest_api_key("segredo-backtest")  # não deve lançar

    def test_never_reuses_the_portfolio_key(self):
        """A chave de portfólio (que abre ordem real) nunca deve autenticar
        o endpoint de backtest, mesmo se coincidentemente configurada com o
        mesmo valor por engano do operador — são variáveis de ambiente
        DIFERENTES (CSS_PORTFOLIO_API_KEY vs CSS_BACKTEST_API_KEY)."""
        with patch.object(server, "BACKTEST_API_KEY", None), \
             patch.object(server, "PORTFOLIO_API_KEY", "mesma-chave"):
            with self.assertRaises(HTTPException) as ctx:
                server._require_backtest_api_key("mesma-chave")
        self.assertEqual(ctx.exception.status_code, 503)  # continua fail-closed


class TestBacktestTriggerPayloadShape(unittest.TestCase):
    """Achado 2 (herdr-ask mfc-13): sample_role/days/end_brt/engines
    estruturalmente impossíveis — o modelo rejeita QUALQUER campo extra."""

    def test_accepts_only_description(self):
        payload = server.BacktestTriggerPayload(description="mudança X no motor")
        self.assertEqual(payload.runs, server.run_isolated_backtest.DEFAULT_RUNS)

    def test_rejects_sample_role_field(self):
        with self.assertRaises(ValidationError):
            server.BacktestTriggerPayload(description="teste", sample_role="oos_disjoint")

    def test_rejects_days_field(self):
        with self.assertRaises(ValidationError):
            server.BacktestTriggerPayload(description="teste", days=688)

    def test_rejects_end_brt_field(self):
        with self.assertRaises(ValidationError):
            server.BacktestTriggerPayload(description="teste", end_brt="2099-01-01")

    def test_rejects_engines_field(self):
        with self.assertRaises(ValidationError):
            server.BacktestTriggerPayload(description="teste", engines=["3tf_baseline"])

    def test_rejects_description_too_short(self):
        with self.assertRaises(ValidationError):
            server.BacktestTriggerPayload(description="ab")

    def test_rejects_description_too_long(self):
        with self.assertRaises(ValidationError):
            server.BacktestTriggerPayload(description="x" * 501)

    def test_rejects_runs_outside_the_clamped_range(self):
        with self.assertRaises(ValidationError):
            server.BacktestTriggerPayload(description="teste válido", runs=6)
        with self.assertRaises(ValidationError):
            server.BacktestTriggerPayload(description="teste válido", runs=0)


class TestSummarizeBacktestEntry(unittest.TestCase):
    def test_trims_and_derives_expected_fields(self):
        entry = {
            "journal_seq": 42,
            "recorded_at_utc": "2026-08-31T12:00:00+00:00",  # quarta-feira, mercado aberto
            "note": "[web-trigger:abc123] mudança de threshold",
            "engines_compared": ["3tf_baseline", "5tf_port_a"],
            "window": {
                "sample_role": "exploratory", "days": 45,
                "start_brt": "a", "end_brt": "b", "nights_evaluated": 30,
            },
            "provenance": {"code_commit": "deadbeef" * 5, "worktree_dirty": False},
            "quality": {"status": "clean"},
            "engines": {
                "3tf_baseline": {"baskets": 10, "bruto": 100.0, "custo": 5.0,
                                  "liquido": 95.0, "quality_status": "clean"},
            },
            "paired_net_delta_per_night": {"mean": 1.2, "stderr": 0.5, "n": 20},
            "runs": 2,
        }
        summary = server._summarize_backtest_entry(entry)
        self.assertEqual(summary["journal_seq"], 42)
        self.assertTrue(summary["is_web_trigger"])
        self.assertTrue(summary["market_open_at_run"])
        self.assertEqual(summary["sample_role"], "exploratory")
        self.assertEqual(summary["code_commit"], "deadbeef" * 5)
        self.assertIn("3tf_baseline", summary["engines"])
        self.assertNotIn("custo", summary["engines"]["3tf_baseline"])  # trimmed

    def test_a_plain_cli_note_is_not_flagged_as_web_trigger(self):
        entry = {"journal_seq": 1, "note": "reprodutibilidade mfc-32", "window": {}}
        summary = server._summarize_backtest_entry(entry)
        self.assertFalse(summary["is_web_trigger"])

    def test_a_weekend_run_is_flagged_as_market_closed(self):
        entry = {
            "journal_seq": 2,
            "recorded_at_utc": "2026-08-29T12:00:00+00:00",  # sábado
            "note": None, "window": {},
        }
        summary = server._summarize_backtest_entry(entry)
        self.assertFalse(summary["market_open_at_run"])


class TestBacktestHistoryEndpointsIntegration(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._journal_path = os.path.join(self._tmpdir.name, "journal.json")
        patch_path = patch.object(server, "RESULTS_LOG_PATH", self._journal_path)
        patch_path.start()
        self.addCleanup(patch_path.stop)
        key_patch = patch.object(server, "BACKTEST_API_KEY", "test-key")
        key_patch.start()
        self.addCleanup(key_patch.stop)

    def _write_journal(self, entries):
        with open(self._journal_path, "w", encoding="utf-8") as f:
            json.dump(entries, f)

    def test_get_history_returns_empty_list_when_journal_missing(self):
        resp = self.client.get("/api/backtest-history")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"entries": []})

    def test_get_history_returns_most_recent_first(self):
        self._write_journal([
            {"journal_seq": 1, "window": {}},
            {"journal_seq": 3, "window": {}},
            {"journal_seq": 2, "window": {}},
        ])
        resp = self.client.get("/api/backtest-history")
        self.assertEqual(resp.status_code, 200)
        seqs = [e["journal_seq"] for e in resp.json()["entries"]]
        self.assertEqual(seqs, [3, 2, 1])

    def test_get_history_filters_by_sample_role(self):
        self._write_journal([
            {"journal_seq": 1, "window": {"sample_role": "exploratory"}},
            {"journal_seq": 2, "window": {"sample_role": "oos_disjoint"}},
        ])
        resp = self.client.get("/api/backtest-history?sample_role=oos_disjoint")
        self.assertEqual(resp.status_code, 200)
        entries = resp.json()["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["journal_seq"], 2)

    def test_get_history_entry_by_seq(self):
        self._write_journal([{"journal_seq": 7, "note": "achado x", "window": {}}])
        resp = self.client.get("/api/backtest-history/7")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["note"], "achado x")

    def test_get_history_entry_redacts_sensitive_provenance(self):
        """Achado P2 (`mfc-rev`) + P2-1 (`mfc-rev-2`, rodada mfc-66, com
        evidência real do journal_seq=28 do próprio repo): identidade de
        conta/servidor/host/caminho de terminal aparece em TRÊS envelopes
        com nomes de campo diferentes — entry["provenance"]["terminal"]
        (configured_path/mt5_path), entry["producer_provenance"]["terminal"]
        (path/observed_path), e entry["execution"] (host/terminal_path,
        chave de TOPO). Uma primeira versão redigia só producer_provenance e
        deixava os outros dois vazarem — o fixture abaixo usa exatamente o
        shape real (conferido lendo reports/backtest_history.json,
        journal_seq=28 real, com `sorted(entry.keys())` por envelope), não
        um shape reconstruído a partir da própria função (mesma classe de
        erro já registrada no projeto como MFC62-01)."""
        self._write_journal([{
            "journal_seq": 3,
            "window": {},
            "provenance": {
                "account": {"login": 198819543, "server": "Exness-MT5Trial11", "currency": "USD"},
                "terminal": {"configured_path": r"D:\MetaTradersWSL\mfc-backtest\terminal64.exe",
                             "mt5_path": r"D:\MetaTradersWSL\mfc-backtest\terminal64.exe"},
            },
            "producer_provenance": {
                "account": {"login": 198819543, "server": "Exness-MT5Trial11",
                             "currency": "USD", "trade_mode": 0, "trade_allowed": True},
                "terminal": {"path": r"D:\MetaTradersWSL\mfc-backtest\terminal64.exe",
                             "observed_path": r"D:\MetaTradersWSL\mfc-backtest"},
            },
            "execution": {
                "host": "Ryzen9",
                "terminal_path": r"D:\MetaTradersWSL\mfc-backtest\terminal64.exe",
                "is_production_terminal": False,
                "terminal_isolation_asserted": True,
                "orders_sent": False,
                "orders_sent_basis": "comparison harness contains no order_send call",
            },
        }])
        resp = self.client.get("/api/backtest-history/3")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        rendered = json.dumps(body)
        for leaked in ("198819543", "Exness-MT5Trial11", "Ryzen9", "MetaTradersWSL"):
            self.assertNotIn(leaked, rendered, f"{leaked!r} vazou sem chave no endpoint público")
        self.assertEqual(body["provenance"]["account"]["login"], "[redacted]")
        self.assertEqual(body["provenance"]["terminal"]["configured_path"], "[redacted]")
        self.assertEqual(body["producer_provenance"]["account"]["login"], "[redacted]")
        self.assertEqual(body["producer_provenance"]["account"]["currency"], "USD")  # não sensível
        self.assertEqual(body["producer_provenance"]["account"]["trade_mode"], 0)  # não sensível
        self.assertEqual(body["producer_provenance"]["terminal"]["path"], "[redacted]")
        self.assertEqual(body["execution"]["host"], "[redacted]")
        self.assertEqual(body["execution"]["terminal_path"], "[redacted]")
        self.assertEqual(body["execution"]["orders_sent"], False)  # não sensível, preservado

    def test_get_history_entry_404_when_missing(self):
        self._write_journal([{"journal_seq": 1, "window": {}}])
        resp = self.client.get("/api/backtest-history/999")
        self.assertEqual(resp.status_code, 404)

    def test_trigger_requires_api_key(self):
        resp = self.client.post("/api/backtest-history/trigger", json={"description": "teste válido"})
        self.assertEqual(resp.status_code, 401)

    def test_trigger_rejects_extra_fields_with_422(self):
        resp = self.client.post(
            "/api/backtest-history/trigger",
            json={"description": "teste válido", "sample_role": "oos_disjoint"},
            headers={"X-Css-Api-Key": "test-key"},
        )
        self.assertEqual(resp.status_code, 422)

    def test_trigger_refused_inside_critical_window(self):
        with patch.object(server.run_isolated_backtest, "in_critical_window", return_value=True):
            resp = self.client.post(
                "/api/backtest-history/trigger",
                json={"description": "teste válido"},
                headers={"X-Css-Api-Key": "test-key"},
            )
        self.assertEqual(resp.status_code, 409)

    def test_trigger_refused_when_market_closed(self):
        with patch.object(server.run_isolated_backtest, "in_critical_window", return_value=False), \
             patch.object(server.run_isolated_backtest, "market_is_open", return_value=False):
            resp = self.client.post(
                "/api/backtest-history/trigger",
                json={"description": "teste válido"},
                headers={"X-Css-Api-Key": "test-key"},
            )
        self.assertEqual(resp.status_code, 409)

    def test_trigger_refused_when_already_running(self):
        with patch.object(server.run_isolated_backtest, "in_critical_window", return_value=False), \
             patch.object(server.run_isolated_backtest, "market_is_open", return_value=True), \
             patch.object(server.run_isolated_backtest, "is_trigger_running", return_value=True):
            resp = self.client.post(
                "/api/backtest-history/trigger",
                json={"description": "teste válido"},
                headers={"X-Css-Api-Key": "test-key"},
            )
        self.assertEqual(resp.status_code, 409)

    def test_trigger_spawns_and_returns_202(self):
        """`is_trigger_running` devolve False na checagem inicial (sob o
        lock em memória) e True logo depois (simula o filho conquistando o
        lock de arquivo quase imediatamente) — evita que o teste espere os
        3s inteiros do loop pós-spawn (achado P2/P2-2, herdr-review mfc-65)."""
        fake_process = type("P", (), {"pid": 4242})()
        with patch.object(server.run_isolated_backtest, "in_critical_window", return_value=False), \
             patch.object(server.run_isolated_backtest, "market_is_open", return_value=True), \
             patch.object(server.run_isolated_backtest, "is_trigger_running",
                           side_effect=[False, True]), \
             patch.object(server.run_isolated_backtest, "spawn_isolated_backtest",
                           return_value=(fake_process, "run-id-xyz")) as mocked_spawn:
            resp = self.client.post(
                "/api/backtest-history/trigger",
                json={"description": "teste válido", "runs": 3},
                headers={"X-Css-Api-Key": "test-key"},
            )
        self.assertEqual(resp.status_code, 202)
        body = resp.json()
        self.assertEqual(body["run_id"], "run-id-xyz")
        self.assertEqual(body["pid"], 4242)
        mocked_spawn.assert_called_once_with("teste válido", 3)

    def test_trigger_waits_briefly_for_the_child_to_own_the_lock(self):
        """Achado P2/P2-2 (herdr-review mfc-65): o endpoint espera (fora do
        lock em memória, sem travar o event loop) até is_trigger_running()
        confirmar que o filho assumiu o lock dedicado, reduzindo a janela
        em que um segundo disparo veria erroneamente "não está rodando"."""
        fake_process = type("P", (), {"pid": 4242})()
        calls = {"n": 0}

        def fake_is_running():
            calls["n"] += 1
            return calls["n"] >= 3  # só "confirma" na 3ª sondagem

        with patch.object(server.run_isolated_backtest, "in_critical_window", return_value=False), \
             patch.object(server.run_isolated_backtest, "market_is_open", return_value=True), \
             patch.object(server.run_isolated_backtest, "is_trigger_running", side_effect=fake_is_running), \
             patch.object(server.run_isolated_backtest, "spawn_isolated_backtest",
                           return_value=(fake_process, "run-id-wait")):
            resp = self.client.post(
                "/api/backtest-history/trigger",
                json={"description": "teste válido"},
                headers={"X-Css-Api-Key": "test-key"},
            )
        self.assertEqual(resp.status_code, 202)
        self.assertGreaterEqual(calls["n"], 3)

    def test_status_requires_api_key(self):
        resp = self.client.get("/api/backtest-history/trigger/status")
        self.assertEqual(resp.status_code, 401)

    def test_status_returns_log_tail_with_correct_key(self):
        with patch.object(server.run_isolated_backtest, "read_status",
                           return_value={"status": "done", "new_journal_seq": 9}), \
             patch.object(server.run_isolated_backtest, "is_trigger_running", return_value=False), \
             patch.object(server.run_isolated_backtest, "read_log_tail", return_value="log content"):
            resp = self.client.get(
                "/api/backtest-history/trigger/status",
                headers={"X-Css-Api-Key": "test-key"},
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "done")
        self.assertEqual(body["log_tail"], "log content")

    def test_status_reconciles_stuck_running_after_a_kill_as_interrupted(self):
        """Achado P2-1 (herdr-review mfc-65, `mfc-rev-2`): um SIGTERM do
        watchdog nunca passa pelos caminhos normais de saída de
        _run_and_record(), então status.json fica preso em "running" pra
        sempre — o endpoint precisa reconciliar contra is_trigger_running()
        (a autoridade real)."""
        with patch.object(server.run_isolated_backtest, "read_status",
                           return_value={"status": "running", "pid": 4242}), \
             patch.object(server.run_isolated_backtest, "is_trigger_running", return_value=False), \
             patch.object(server.run_isolated_backtest, "read_log_tail", return_value=""):
            resp = self.client.get(
                "/api/backtest-history/trigger/status",
                headers={"X-Css-Api-Key": "test-key"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "interrupted")

    def test_status_reconciles_stale_done_while_a_second_run_is_actually_running(self):
        """Direção inversa do mesmo problema: status.json ainda mostra o
        "done" da execução anterior, mas o lock já foi conquistado por uma
        execução nova — o endpoint não pode mentir "done" enquanto algo
        roda de verdade. `pid` também é corrigido pro dono ATUAL (achado
        P2, herdr-review mfc-66, `mfc-rev`: sem isto, o `pid` ficaria sendo
        o da execução anterior, que já terminou)."""
        with patch.object(server.run_isolated_backtest, "read_status",
                           return_value={"status": "done", "new_journal_seq": 5, "pid": 111}), \
             patch.object(server.run_isolated_backtest, "is_trigger_running", return_value=True), \
             patch.object(server.run_isolated_backtest, "current_running_owner_pid", return_value=222), \
             patch.object(server.run_isolated_backtest, "read_log_tail", return_value=""):
            resp = self.client.get(
                "/api/backtest-history/trigger/status",
                headers={"X-Css-Api-Key": "test-key"},
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "running")
        self.assertEqual(body["pid"], 222)
        self.assertTrue(body["stale_metadata"])

    def test_status_leaves_a_consistent_running_state_untouched(self):
        with patch.object(server.run_isolated_backtest, "read_status",
                           return_value={"status": "running", "pid": 111}), \
             patch.object(server.run_isolated_backtest, "is_trigger_running", return_value=True), \
             patch.object(server.run_isolated_backtest, "read_log_tail", return_value="tail"):
            resp = self.client.get(
                "/api/backtest-history/trigger/status",
                headers={"X-Css-Api-Key": "test-key"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "running")


if __name__ == "__main__":
    unittest.main()
