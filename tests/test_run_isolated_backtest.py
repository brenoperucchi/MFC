"""
Testes de scripts/run_isolated_backtest.py — lançador do acompanhamento de
backtest via web. Ver docs/plans/eventual-stargazing-bear.md pro desenho
completo (consulta herdr-ask mfc-13).
"""

import datetime
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import scripts.run_isolated_backtest as rib
import scripts._backtest_results_log as bl


class TestBuildIsolatedEnv(unittest.TestCase):
    """Achado 1 (herdr-ask mfc-13): env construído do zero, nunca
    os.environ.copy() — nenhum segredo do processo pai (CSS_PORTFOLIO_API_KEY
    etc.) pode reaparecer no dicionário passado ao Popen, e
    CSS_MT5_TERMINAL_PATH é sempre o caminho isolado, nunca herdado."""

    def test_never_copies_the_surrounding_environment_wholesale(self):
        with patch.dict(os.environ, {
            "CSS_PORTFOLIO_API_KEY": "super-secret",
            "CSS_MT5_TERMINAL_PATH": r"D:\MetaTradersWSL\mfc\terminal64.exe",
            "SOME_RANDOM_VAR": "should-not-appear",
        }):
            env = rib.build_isolated_env("/tmp/base")
        self.assertNotIn("CSS_PORTFOLIO_API_KEY", env)
        self.assertNotIn("SOME_RANDOM_VAR", env)

    def test_terminal_path_is_always_the_isolated_instance(self):
        with patch.dict(os.environ, {
            "CSS_MT5_TERMINAL_PATH": r"D:\MetaTradersWSL\mfc\terminal64.exe",
        }):
            env = rib.build_isolated_env("/tmp/base")
        self.assertEqual(env["CSS_MT5_TERMINAL_PATH"], rib.ISOLATED_TERMINAL_PATH)

    def test_sets_isolation_and_web_trigger_markers(self):
        env = rib.build_isolated_env("/tmp/base")
        self.assertEqual(env["MFC_BACKTEST_TERMINAL_ISOLATED"], "1")
        self.assertEqual(env["MFC_BACKTEST_WEB_TRIGGER"], "1")
        self.assertEqual(env["PYTHONPATH"], "/tmp/base")

    def test_forces_utf8_stdout_on_the_child(self):
        """Achado do smoke test real (2026-09-01, produção): sem
        PYTHONIOENCODING=utf-8, o stdout do filho no Windows usa o codepage
        ativo do console — qualquer acento em log_note/prints vira mojibake
        no arquivo de log, já que read_log_tail() sempre lê como UTF-8."""
        env = rib.build_isolated_env("/tmp/base")
        self.assertEqual(env["PYTHONIOENCODING"], "utf-8")


class TestSpawnIsolatedBacktestArgv(unittest.TestCase):
    """Achado 2 (herdr-ask mfc-13, ambos os revisores): congela o argv
    construído — deliberadamente nenhum elemento com "oos", sample_role
    fixo em "exploratory" hardcoded dentro de _run_and_record (não passado
    aqui pelo argv), argumentos como LISTA (nunca shell=True)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._patches = [
            patch.object(rib, "TRIGGER_STATE_DIR", self._tmpdir.name),
            patch.object(rib, "STATUS_PATH", os.path.join(self._tmpdir.name, "status.json")),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._tmpdir.cleanup)

    def test_argv_never_mentions_oos_and_uses_module_invocation(self):
        fake_process = MagicMock(pid=12345)
        with patch.object(rib.subprocess, "Popen", return_value=fake_process) as mocked_popen:
            process, run_id = rib.spawn_isolated_backtest("minha descrição de teste", 3)

        self.assertIs(process, fake_process)
        mocked_popen.assert_called_once()
        call = mocked_popen.call_args
        argv = call.args[0]
        self.assertIsInstance(argv, list)
        self.assertNotIn("shell", call.kwargs)  # nunca shell=True (kwarg ausente == default False)
        joined = " ".join(argv)
        self.assertNotIn("oos", joined.lower())
        self.assertIn("-m", argv)
        self.assertIn("scripts.run_isolated_backtest", argv)
        self.assertIn("--description", argv)
        self.assertIn("minha descrição de teste", argv)
        self.assertIn("--runs", argv)
        self.assertIn("3", argv)
        self.assertIn("--run-id", argv)
        self.assertIn(run_id, argv)

    def test_a_description_that_looks_like_a_flag_stays_a_single_argv_value(self):
        """Uma descrição maliciosa/acidental parecida com um argumento CLI
        (ex.: "--sample-role oos_disjoint") não pode virar um argumento
        SEPARADO — argumentos como lista impede isso estruturalmente."""
        fake_process = MagicMock(pid=1)
        malicious = "--sample-role oos_disjoint --end-brt 2099-01-01"
        with patch.object(rib.subprocess, "Popen", return_value=fake_process) as mocked_popen:
            rib.spawn_isolated_backtest(malicious, 1)
        argv = mocked_popen.call_args.args[0]
        # A string inteira aparece como UM ÚNICO elemento do argv, logo após
        # "--description" — nunca fatiada em múltiplos argumentos.
        idx = argv.index("--description")
        self.assertEqual(argv[idx + 1], malicious)

    def test_writes_running_status_after_spawn(self):
        fake_process = MagicMock(pid=999)
        with patch.object(rib.subprocess, "Popen", return_value=fake_process):
            _, run_id = rib.spawn_isolated_backtest("desc", 2)
        status = rib.read_status()
        self.assertEqual(status["status"], "running")
        self.assertEqual(status["pid"], 999)
        self.assertEqual(status["run_id"], run_id)
        self.assertEqual(status["runs"], 2)

    def test_two_spawns_use_independent_log_files(self):
        """Achado P2 (herdr-review mfc-65/66, `mfc-rev`): um LOG_PATH global
        único faria o segundo disparo truncar/corromper o log do primeiro,
        mesmo que só um dos dois conquiste o lock dedicado de verdade."""
        fake_process_a = MagicMock(pid=1)
        fake_process_b = MagicMock(pid=2)
        with patch.object(rib.subprocess, "Popen", return_value=fake_process_a):
            _, run_id_a = rib.spawn_isolated_backtest("primeiro disparo", 1)
        with open(rib._log_path_for(run_id_a), "a", encoding="utf-8") as f:
            f.write("saida do primeiro disparo\n")
        with patch.object(rib.subprocess, "Popen", return_value=fake_process_b):
            _, run_id_b = rib.spawn_isolated_backtest("segundo disparo", 1)
        # o log do primeiro continua intacto — o segundo nunca tocou o
        # mesmo arquivo.
        self.assertIn("saida do primeiro disparo", rib.read_log_tail(run_id=run_id_a))
        self.assertNotEqual(rib._log_path_for(run_id_a), rib._log_path_for(run_id_b))


class TestIsTriggerRunning(unittest.TestCase):
    """Achado 3 (herdr-ask mfc-13, mfc-rev): lock não-bloqueante, cross-
    processo, morre com o processo — NUNCA os.kill(pid, 0) (mata no
    Windows)."""

    def test_false_when_nobody_holds_the_dedicated_lock(self):
        self.assertFalse(rib.is_trigger_running())

    def test_true_while_another_file_descriptor_holds_the_lock(self):
        import fcntl
        lock_path = bl._lock_path(key=rib._TRIGGER_LOCK_KEY)
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            self.assertTrue(rib.is_trigger_running())
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        self.assertFalse(rib.is_trigger_running())

    def test_trigger_lock_is_independent_from_the_journal_append_lock(self):
        """O lock do trigger não pode ser o MESMO do journal (append_result)
        — reusar o mesmo bloquearia qualquer escritor CLI concorrente pela
        duração inteira do backtest via web (ver docstring de
        scripts._backtest_results_log._exclusive_lock)."""
        self.assertNotEqual(
            bl._lock_path(key=rib._TRIGGER_LOCK_KEY),
            bl._lock_path(),
        )


class TestInCriticalWindow(unittest.TestCase):
    """Achado 4 (herdr-ask mfc-13, mfc-rev-2): margens largas o bastante
    pra cobrir a janela real de abertura tolerante (scheduler_daemon.py
    mantém até 21:59, não 21:05)."""

    def test_2054_is_not_critical(self):
        self.assertFalse(rib.in_critical_window(_brt(20, 54)))

    def test_2055_is_critical(self):
        self.assertTrue(rib.in_critical_window(_brt(20, 55)))

    def test_2159_is_critical(self):
        self.assertTrue(rib.in_critical_window(_brt(21, 59)))

    def test_2200_is_no_longer_critical(self):
        self.assertFalse(rib.in_critical_window(_brt(22, 0)))

    def test_0754_is_not_critical(self):
        self.assertFalse(rib.in_critical_window(_brt(7, 54)))

    def test_0755_is_critical(self):
        self.assertTrue(rib.in_critical_window(_brt(7, 55)))

    def test_0819_is_critical(self):
        self.assertTrue(rib.in_critical_window(_brt(8, 19)))

    def test_0820_is_no_longer_critical(self):
        self.assertFalse(rib.in_critical_window(_brt(8, 20)))

    def test_midday_is_never_critical(self):
        self.assertFalse(rib.in_critical_window(_brt(14, 0)))


def _brt(hour, minute):
    return datetime.datetime(2026, 8, 31, hour, minute, tzinfo=rib.BRT)


class TestMarketIsOpen(unittest.TestCase):
    """Achado 5 (herdr-ask mfc-13, mfc-rev-2, medido: custo/líquido variam
    ~2x dependendo de o mercado estar aberto no instante da medição) —
    aproximação conservadora sem depender de tzdata."""

    def test_saturday_is_closed(self):
        self.assertFalse(rib.market_is_open(_utc(2026, 8, 29, 12, 0)))

    def test_friday_before_2100_utc_is_open(self):
        self.assertTrue(rib.market_is_open(_utc(2026, 8, 28, 20, 59)))

    def test_friday_from_2100_utc_is_closed(self):
        self.assertFalse(rib.market_is_open(_utc(2026, 8, 28, 21, 0)))

    def test_sunday_before_2200_utc_is_closed(self):
        self.assertFalse(rib.market_is_open(_utc(2026, 8, 30, 21, 59)))

    def test_sunday_from_2200_utc_is_open(self):
        self.assertTrue(rib.market_is_open(_utc(2026, 8, 30, 22, 0)))

    def test_wednesday_noon_is_open(self):
        self.assertTrue(rib.market_is_open(_utc(2026, 9, 2, 12, 0)))


def _utc(year, month, day, hour, minute):
    return datetime.datetime(year, month, day, hour, minute, tzinfo=datetime.timezone.utc)


class TestFindJournalSeq(unittest.TestCase):
    """O journal_seq da execução web é identificado por CONTEÚDO (marcador
    único de run_id no note), não por "maior seq antes/depois" — evita a
    corrida com um append CLI independente e concorrente apontada na
    consulta herdr-ask mfc-13 (mfc-rev)."""

    def setUp(self):
        self._tmpfile = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        self.addCleanup(self._safe_unlink)
        self._patch = patch.object(bl, "RESULTS_LOG_PATH", self._tmpfile.name)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def _safe_unlink(self):
        try:
            os.unlink(self._tmpfile.name)
        except FileNotFoundError:
            pass

    def _write_journal(self, entries):
        with open(self._tmpfile.name, "w", encoding="utf-8") as f:
            json.dump(entries, f)

    def test_finds_the_entry_matching_this_run_id_marker(self):
        self._write_journal([
            {"journal_seq": 1, "note": "[web-trigger:aaa111] outra descrição"},
            {"journal_seq": 2, "note": "[web-trigger:bbb222] a que eu quero"},
            {"journal_seq": 3, "note": "execução CLI manual, sem marcador nenhum"},
        ])
        self.assertEqual(rib._find_journal_seq("bbb222"), 2)

    def test_returns_none_when_no_entry_matches(self):
        self._write_journal([{"journal_seq": 1, "note": "sem marcador"}])
        self.assertIsNone(rib._find_journal_seq("nao-existe"))

    def test_returns_none_when_journal_file_is_missing(self):
        os.unlink(self._tmpfile.name)
        self.assertIsNone(rib._find_journal_seq("qualquer"))

    def test_does_not_confuse_a_concurrent_cli_appends_higher_seq(self):
        """Cenário do achado mfc-rev: um append CLI independente concorrente
        cria um journal_seq MAIOR depois do nosso — "maior depois" pegaria o
        errado; a busca por marcador continua correta."""
        self._write_journal([
            {"journal_seq": 5, "note": "[web-trigger:ccc333] nosso run"},
            {"journal_seq": 6, "note": "outro processo CLI, apendado depois, sem relação"},
        ])
        self.assertEqual(rib._find_journal_seq("ccc333"), 5)


class TestRunAndRecord(unittest.TestCase):
    """Corpo real da execução — sample_role/janela HARDCODED aqui, nunca
    vindos de fora; status.json reflete o resultado real de compare()."""

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

        # _run_and_record() seta MFC_BACKTEST_TERMINAL_ISOLATED/WEB_TRIGGER
        # em os.environ de propósito, de forma PERMANENTE (correto — cada
        # invocação real é um processo OS dedicado que morre depois; ver
        # achado P1, herdr-review mfc-65). Em testes, isso vazaria pros
        # próximos arquivos rodando no MESMO processo pytest — restaura o
        # ambiente ao original depois de cada teste desta classe.
        env_patch = patch.dict(os.environ, {}, clear=False)
        env_patch.start()
        self.addCleanup(env_patch.stop)

        # _run_and_record() reavalia os dois portões DEPOIS de conquistar o
        # lock (achado P3-3, herdr-review mfc-66) — sem mockar isto, os
        # testes ficariam reféns do horário/dia real em que rodam (ex.:
        # rodar num fim de semana faria todo teste "success" virar
        # "skipped"). Default: os dois portões sempre abertos; testes
        # dedicados sobrescrevem pra exercer o "skipped".
        critical_patch = patch.object(rib, "in_critical_window", return_value=False)
        critical_patch.start()
        self.addCleanup(critical_patch.stop)
        market_patch = patch.object(rib, "market_is_open", return_value=True)
        market_patch.start()
        self.addCleanup(market_patch.stop)

    def _append_fake_journal_entry(self, note):
        with open(self._journal_path, "r", encoding="utf-8") as f:
            log = json.load(f)
        log.append({"journal_seq": len(log) + 1, "note": note})
        with open(self._journal_path, "w", encoding="utf-8") as f:
            json.dump(log, f)

    def test_success_path_hardcodes_exploratory_and_the_fixed_window(self):
        import scripts.backtest_engine_compare as bec

        captured = {}

        def fake_compare(**kwargs):
            captured.update(kwargs)
            self._append_fake_journal_entry(kwargs["log_note"])
            return 0

        with patch.object(bec, "compare", side_effect=fake_compare):
            ret = rib._run_and_record("minha descrição", 2, "runid123")

        self.assertEqual(ret, 0)
        self.assertEqual(captured["sample_role"], "exploratory")
        self.assertEqual(captured["days"], rib.REGRESSION_WINDOW_DAYS)
        self.assertEqual(captured["runs"], 2)
        self.assertNotIn("oos", captured["log_note"].lower())
        self.assertTrue(captured["log_note"].startswith("[web-trigger:runid123]"))

        status = rib.read_status()
        self.assertEqual(status["status"], "done")
        self.assertEqual(status["new_journal_seq"], 1)

    def test_failure_returncode_is_recorded_as_failed_status(self):
        import scripts.backtest_engine_compare as bec

        with patch.object(bec, "compare", return_value=1):
            ret = rib._run_and_record("desc", 1, "runidfail")

        self.assertEqual(ret, 1)
        status = rib.read_status()
        self.assertEqual(status["status"], "failed")
        self.assertEqual(status["returncode"], 1)

    def test_exception_is_recorded_as_failed_status_and_reraised(self):
        import scripts.backtest_engine_compare as bec

        with patch.object(bec, "compare", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                rib._run_and_record("desc", 1, "runidexc")

        status = rib.read_status()
        self.assertEqual(status["status"], "failed")
        self.assertIn("boom", status["error"])

    def test_forces_isolation_env_vars_regardless_of_invocation(self):
        """Achado P1 (herdr-review mfc-65, `mfc-rev`): a entrada manual
        (__main__) não passava por build_isolated_env() nenhum — sem esta
        garantia, um operador que esquecesse de exportar
        MFC_BACKTEST_TERMINAL_ISOLATED=1 faria compare() pular a asserção de
        isolamento inteira, silenciosamente."""
        import scripts.backtest_engine_compare as bec

        os.environ.pop("MFC_BACKTEST_TERMINAL_ISOLATED", None)
        os.environ.pop("MFC_BACKTEST_WEB_TRIGGER", None)

        with patch.object(bec, "compare", return_value=0) as mocked:
            rib._run_and_record("desc", 1, "runid-force")

        self.assertEqual(os.environ.get("MFC_BACKTEST_TERMINAL_ISOLATED"), "1")
        self.assertEqual(os.environ.get("MFC_BACKTEST_WEB_TRIGGER"), "1")
        mocked.assert_called_once()

    def test_manual_invocation_with_live_terminal_path_is_refused(self):
        """Cenário concreto do achado P1: um operador roda o script direto
        num shell cujo CSS_MT5_TERMINAL_PATH ainda aponta pro terminal AO
        VIVO (esqueceu de exportar o isolado) — a asserção real de
        compare() (não mockada aqui) deve recusar, não silenciosamente
        pular a checagem."""
        import scripts.backtest_engine_compare as bec

        os.environ.pop("MFC_BACKTEST_TERMINAL_ISOLATED", None)
        with patch.object(bec, "MT5_PATH", r"D:\MetaTradersWSL\mfc\terminal64.exe"):
            with self.assertRaises(RuntimeError) as ctx:
                rib._run_and_record("desc", 1, "runid-live-refused")
        self.assertIn("mfc-backtest", str(ctx.exception))
        status = rib.read_status()
        self.assertEqual(status["status"], "failed")

    def test_owner_pid_file_is_removed_when_the_lock_is_released(self):
        """Achado P3-2 (herdr-review mfc-66, `mfc-rev-2`): sem apagar, o
        arquivo guardaria o PID do dono ANTERIOR entre uma execução e a
        seguinte — janela pequena, mas potencialmente perigosa no mesmo
        host que roda produção ao vivo."""
        import scripts.backtest_engine_compare as bec

        with patch.object(bec, "compare", return_value=0):
            rib._run_and_record("desc", 1, "runid-cleanup")
        self.assertFalse(os.path.exists(rib.OWNER_PID_PATH))

    def test_reevaluates_critical_window_after_acquiring_the_lock(self):
        """Achado P3-3 (herdr-review mfc-66, `mfc-rev-2`): um disparo
        enfileirado atrás de outro pode esperar minutos — se a janela
        crítica começou nesse meio tempo, não deve rodar compare()."""
        import scripts.backtest_engine_compare as bec

        with patch.object(rib, "in_critical_window", return_value=True), \
             patch.object(bec, "compare") as mocked_compare:
            ret = rib._run_and_record("desc", 1, "runid-critical-skip")
        mocked_compare.assert_not_called()
        self.assertEqual(ret, 0)
        status = rib.read_status()
        self.assertEqual(status["status"], "skipped")
        self.assertIn("janela crítica", status["reason"])

    def test_reevaluates_market_open_after_acquiring_the_lock(self):
        """Mesmo achado P3-3, portão de mercado fechado."""
        import scripts.backtest_engine_compare as bec

        with patch.object(rib, "market_is_open", return_value=False), \
             patch.object(bec, "compare") as mocked_compare:
            ret = rib._run_and_record("desc", 1, "runid-market-skip")
        mocked_compare.assert_not_called()
        self.assertEqual(ret, 0)
        status = rib.read_status()
        self.assertEqual(status["status"], "skipped")
        self.assertIn("mercado fechado", status["reason"])


class TestOwnerPidAndTerminateOwner(unittest.TestCase):
    """Achado P2/P2-2 (herdr-review mfc-65, ambos os revisores): o pid
    gravado por dentro da seção crítica do lock é a fonte de verdade —
    nunca status.json["pid"], que pode pertencer a um segundo disparo
    enfileirado."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        for name, value in (
            ("TRIGGER_STATE_DIR", self._tmpdir.name),
            ("OWNER_PID_PATH", os.path.join(self._tmpdir.name, "owner.pid")),
        ):
            p = patch.object(rib, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_current_running_owner_pid_is_none_when_nobody_holds_the_lock(self):
        self.assertIsNone(rib.current_running_owner_pid())

    def test_current_running_owner_pid_reads_the_pid_written_under_the_lock(self):
        import fcntl
        lock_path = bl._lock_path(key=rib._TRIGGER_LOCK_KEY)
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            rib._write_owner_pid()
            self.assertEqual(rib.current_running_owner_pid(), os.getpid())
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        # ninguém mais segura o lock — mesmo com owner.pid ainda no disco
        # (conteúdo obsoleto), a autoridade é o lock, não o arquivo sozinho.
        self.assertIsNone(rib.current_running_owner_pid())

    def test_terminate_owner_sends_sigterm_and_returns_once_lock_is_free(self):
        import subprocess as sp
        import fcntl
        lock_path = bl._lock_path(key=rib._TRIGGER_LOCK_KEY)
        # Processo real que segura o lock e sai assim que receber SIGTERM.
        script = (
            "import fcntl, os, signal, sys, time; "
            f"fd = os.open({lock_path!r}, os.O_RDWR | os.O_CREAT, 0o600); "
            "fcntl.flock(fd, fcntl.LOCK_EX); "
            "signal.signal(signal.SIGTERM, lambda *a: sys.exit(0)); "
            "time.sleep(30)"
        )
        proc = sp.Popen([sys.executable, "-c", script])
        try:
            deadline = __import__("time").monotonic() + 5
            while __import__("time").monotonic() < deadline and not rib.is_trigger_running():
                __import__("time").sleep(0.05)
            self.assertTrue(rib.is_trigger_running())
            rib.terminate_owner(proc.pid, timeout=5)
            self.assertFalse(rib.is_trigger_running())
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
