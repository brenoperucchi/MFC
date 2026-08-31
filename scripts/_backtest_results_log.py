"""
Log compartilhado de resultados de backtest — usado por
scripts/backtest_engine_compare.py e scripts/measure_composition_effect.py
pra registrar cada rodada em reports/backtest_history.json, permitindo
comparar melhora/piora conforme parâmetros ou código mudam (pedido do
Breno, 2026-08-29: "esse backtest precisa ter json com resultados sendo
gravados pra podermos acompanhar melhoras ou pioras").

NÃO é parte do pipeline de produção — só os scripts de diagnóstico escrevem
aqui, nunca `agents/portfolio_executor.py` nem `scripts/scheduler_daemon.py`.
"""

import json
import hashlib
import math
import ntpath
import os
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RESULTS_LOG_PATH = os.path.join(BASE_DIR, "reports", "backtest_history.json")
SCHEMA_VERSION = 2
MIN_OOS_NIGHTS = 30
BRT = timezone(timedelta(hours=-3))
OOS_ENTRY_HOUR_BRT = 21
_RESULT_DIGEST_EXCLUDED_FIELDS = {
    "recorded_at_utc", "timestamp_utc", "timestamp", "producer_provenance",
    "provenance", "schema_version", "script", "supersedes", "result_semantics",
    "journal_seq",
}
_THREAD_LOCK = threading.Lock()
_PROVENANCE_SOURCE_FILES = (
    "agents/confluence_engine.py",
    "agents/triad_analyzer.py",
    "agents/macro_analyzer.py",
    "agents/operational_analyzer.py",
    "agents/portfolio_executor.py",
    "web/css_service.py",
    "web/history_tracker.py",
    "scripts/backtest_canonical.py",
    "scripts/backtest_engine_compare.py",
    "scripts/measure_composition_effect.py",
    "scripts/_backtest_results_log.py",
)


def _lock_path():
    digest = hashlib.sha256(RESULTS_LOG_PATH.encode("utf-8")).hexdigest()[:20]
    return os.path.join(tempfile.gettempdir(), f"mfc-backtest-history-{digest}.lock")


def _source_digest():
    """Resume os fontes que definem o motor/harness desta evidência."""
    digest = hashlib.sha256()
    try:
        for relative in _PROVENANCE_SOURCE_FILES:
            path = os.path.join(BASE_DIR, relative)
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            with open(path, "rb") as stream:
                digest.update(stream.read())
            digest.update(b"\0")
    except OSError:
        return None
    return digest.hexdigest()


def result_snapshot_digest(entry):
    """Vincula o envelope do produtor aos resultados observados.

    Identidades temporais, o próprio envelope de proveniência e aliases
    gerados pelo journal ficam fora do payload para evitar circularidade. Todo
    o restante do registro, incluindo engines, runs_summary, cobertura, janela,
    custos agregados e comparação, fica protegido pelo digest do harness.
    """
    payload = {
        key: value for key, value in entry.items()
        if key not in _RESULT_DIGEST_EXCLUDED_FIELDS
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _code_provenance():
    """Obtém identidade local do checkout sem fingir que dados de broker existem."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=BASE_DIR,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = bool(subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=BASE_DIR,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip())
    except (OSError, subprocess.SubprocessError):
        commit = None
        dirty = None
    return commit or None, dirty, _source_digest()


def _runtime_provenance():
    """Lê contexto já disponível, sem inicializar terminal nem abrir conexão."""
    account = terminal = contract_size = None
    try:
        from web.css_service import ALL_28_PAIRS, MT5_AVAILABLE, MT5_PATH, mt5, to_broker_symbol
        if MT5_AVAILABLE and mt5 is not None:
            info = mt5.account_info()
            if info is not None:
                account = {
                    "login": getattr(info, "login", None),
                    "server": getattr(info, "server", None),
                    "currency": getattr(info, "currency", None),
                }
            terminal = {
                "configured_path": os.environ.get("CSS_MT5_TERMINAL_PATH") or None,
                "mt5_path": MT5_PATH,
            }
            sizes = {}
            for pair in ALL_28_PAIRS:
                symbol_info = mt5.symbol_info(to_broker_symbol(pair))
                if symbol_info is not None:
                    sizes[pair] = getattr(symbol_info, "trade_contract_size", None)
            missing_pairs = [pair for pair in ALL_28_PAIRS if pair not in sizes]
            invalid_pairs = [
                pair for pair, value in sizes.items()
                if not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or value <= 0
            ]
            contract_size = {
                "observed_by_pair": sizes,
                "expected_for_pnl": 100000,
                "missing_pairs": missing_pairs,
                "invalid_pairs": invalid_pairs,
                "coverage_complete": not missing_pairs,
                "all_finite_positive": not invalid_pairs,
            }
    except Exception:
        # Provenance must never make a diagnostic result fail after its work;
        # the missing fields are recorded explicitly below.
        pass
    return account, terminal, contract_size


def _provenance_for(entry, *, legacy=False):
    if legacy:
        return {
            "status": "legacy_unavailable",
            "code_commit": None,
            "worktree_dirty": None,
            "data_window_days": entry.get("days"),
            "account": None,
            "terminal": None,
            "parameters": None,
            "contract_size": None,
            "rates_source": None,
            "cost_snapshot": None,
            "missing": [
                "recorded_at_utc", "code_commit", "account", "terminal",
                "parameters", "contract_size", "rates_source", "cost_snapshot",
            ],
        }
    commit, dirty, source_digest = _code_provenance()
    runtime_account, runtime_terminal, runtime_contract_size = _runtime_provenance()
    try:
        from web.css_service import ALL_28_PAIRS
    except Exception:
        ALL_28_PAIRS = ()

    # A contract snapshot is evidence only when it is internally coherent:
    # the flags are derived checks, not credentials that a caller may set to
    # True.  In particular, a 28-entry dict with one wrong/duplicate pair is
    # not equivalent to coverage of the canonical 28-pair universe.
    contract = runtime_contract_size
    observed = contract.get("observed_by_pair") if isinstance(contract, dict) else None
    expected = contract.get("expected_for_pnl") if isinstance(contract, dict) else None
    expected_valid = (
        isinstance(expected, (int, float))
        and not isinstance(expected, bool)
        and math.isfinite(float(expected))
        and float(expected) == 100000.0
    )
    contract_valid = (
        isinstance(contract, dict)
        and expected_valid
        and isinstance(observed, dict)
        and set(observed) == set(ALL_28_PAIRS)
        and contract.get("missing_pairs") == []
        and contract.get("invalid_pairs") == []
        and contract.get("coverage_complete") is True
        and contract.get("all_finite_positive") is True
        and all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and float(value) > 0
            for value in observed.values()
        )
        # O PnL reconstruído usa lot * 100000; um broker com outra escala não
        # pode ser rotulado como cobertura completa por ter só números válidos.
        and all(float(value) == float(expected) for value in observed.values())
    )
    values = {
        "code_commit": commit,
        "worktree_dirty": dirty,
        "code_source_digest": source_digest,
        "code_source_files": list(_PROVENANCE_SOURCE_FILES),
        "data_window_days": entry.get("days"),
        # Identity and runtime observations must come from this process, not
        # from fields copied into a diagnostic record by its caller.
        "account": runtime_account,
        "terminal": runtime_terminal,
        "parameters": entry.get("parameters"),
        "contract_size": contract if contract_valid else None,
        "rates_source": entry.get("rates_source"),
        "cost_snapshot": entry.get("cost_snapshot"),
    }
    missing = [key for key, value in values.items() if value is None]
    if source_digest is None:
        missing.append("code_source_digest")
    account = values["account"]
    if not isinstance(account, dict) or any(
        account.get(field) in (None, "") for field in ("login", "server", "currency")
    ):
        missing.append("account_fields")
    terminal = values["terminal"]
    if not isinstance(terminal, dict) or not (
        terminal.get("configured_path") or terminal.get("mt5_path")
    ):
        missing.append("terminal_path")
    if not contract_valid:
        missing.append("contract_size_coverage")
    return {
        "status": "complete" if not missing else "partial",
        **values,
        "coverage": {
            "nights_evaluated": entry.get("nights_evaluated", entry.get("nights")),
            "degraded_baskets": entry.get("degraded_baskets"),
            "skipped_missing_price": entry.get("skipped_missing_price"),
        },
        "exclusions": {
            "excluded_pair": entry.get("excluded_pair"),
            "missing_price_baskets": entry.get("skipped_missing_price"),
        },
        "missing": missing,
    }


def _complete_contract_snapshot(value):
    """Valida um snapshot de contrato produzido no terminal do backtest."""
    if not isinstance(value, dict):
        return False
    try:
        from web.css_service import ALL_28_PAIRS
    except Exception:
        return False
    observed = value.get("observed_by_pair")
    expected = value.get("expected_for_pnl")
    return bool(
        isinstance(observed, dict)
        and set(observed) == set(ALL_28_PAIRS)
        and expected == 100000
        and value.get("missing_pairs") == []
        and value.get("invalid_pairs") == []
        and value.get("coverage_complete") is True
        and value.get("all_finite_positive") is True
        and value.get("valid_for_pnl") is True
        and all(
            isinstance(item, (int, float)) and not isinstance(item, bool)
            and math.isfinite(float(item)) and float(item) == 100000.0
            for item in observed.values()
        )
    )


def validate_oos_window(window, *, evaluated_dates=None):
    """Valida a semântica temporal canônica de uma janela OOS.

    Os campos são serializados com offset BRT explícito. A mesma função é
    usada pelo produtor e pelo importador para que o selector não confie
    apenas no formato das strings.
    """
    if not isinstance(window, dict):
        raise ValueError("janela OOS inválida")
    days = window.get("days")
    if not isinstance(days, int) or isinstance(days, bool) or days <= 0:
        raise ValueError("janela OOS sem número de dias válido")

    def _parse_boundary(field):
        value = window.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{field} ausente na janela OOS")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{field} não é um instante ISO válido") from exc
        if parsed.tzinfo is None or parsed.utcoffset() != BRT.utcoffset(None):
            raise ValueError(f"{field} deve declarar offset BRT -03:00")
        parsed = parsed.astimezone(BRT)
        if (parsed.hour != OOS_ENTRY_HOUR_BRT or parsed.minute != 0
                or parsed.second != 0 or parsed.microsecond != 0):
            raise ValueError(
                f"{field} deve ser um instante canônico às {OOS_ENTRY_HOUR_BRT:02d}:00 BRT"
            )
        return parsed

    start = _parse_boundary("start_brt")
    end = _parse_boundary("end_brt")
    development_start = _parse_boundary("development_start_brt")
    if start != end - timedelta(days=days):
        raise ValueError("start_brt não corresponde a end_brt menos days")
    if end > development_start:
        raise ValueError("janela OOS sobrepõe a amostra de desenvolvimento")

    if evaluated_dates is not None:
        if not isinstance(evaluated_dates, list):
            raise ValueError("datas avaliadas da janela OOS não são uma lista")
        parsed_dates = []
        for value in evaluated_dates:
            try:
                parsed = datetime.fromisoformat(value)
            except (TypeError, ValueError) as exc:
                raise ValueError("data avaliada OOS não é ISO válida") from exc
            if parsed.tzinfo is None or parsed.utcoffset() != BRT.utcoffset(None):
                raise ValueError("data avaliada OOS deve declarar offset BRT -03:00")
            parsed = parsed.astimezone(BRT)
            if (parsed.hour != OOS_ENTRY_HOUR_BRT or parsed.minute != 0
                    or parsed.second != 0 or parsed.microsecond != 0):
                raise ValueError("data avaliada OOS fora do instante canônico")
            parsed_dates.append(parsed)
        if len(set(parsed_dates)) != len(parsed_dates):
            raise ValueError("datas avaliadas OOS repetidas")
        if any(parsed < start or parsed >= end for parsed in parsed_dates):
            raise ValueError("data avaliada OOS fora do intervalo declarado")
    return start, end, development_start


def _complete_oos_coverage(entry, engines, runs):
    """Valida que o envelope OOS representa uma amostra observada e limpa."""
    coverage = entry.get("coverage")
    if not isinstance(coverage, dict):
        return False

    def _nonnegative_int(value):
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    candidate = coverage.get("candidate_nights")
    evaluated = coverage.get("evaluated_nights")
    if (not _nonnegative_int(candidate) or candidate < MIN_OOS_NIGHTS
            or not _nonnegative_int(evaluated)
            or evaluated != candidate
            or not _nonnegative_int(coverage.get("skipped_no_verdict"))
            or coverage.get("skipped_no_verdict") != 0
            or not _nonnegative_int(coverage.get("skipped_invalid_exit"))
            or coverage.get("skipped_invalid_exit") != 0
            or not isinstance(coverage.get("evaluated_dates_brt"), list)
            or len(coverage["evaluated_dates_brt"]) != evaluated
            or any(not isinstance(item, str) or not item for item in coverage["evaluated_dates_brt"])
            or coverage.get("price_missing_points") != []
            or entry.get("nights_evaluated") != evaluated):
        return False

    quality = entry.get("quality")
    quality_by_engine = quality.get("by_engine") if isinstance(quality, dict) else None
    top_engines = entry.get("engines")
    if (not isinstance(quality, dict) or quality.get("status") != "clean"
            or not isinstance(quality_by_engine, dict)
            or set(quality_by_engine) != set(engines)
            or not isinstance(top_engines, dict)
            or set(top_engines) != set(engines)):
        return False
    for name in engines:
        metrics = quality_by_engine.get(name)
        if (not isinstance(metrics, dict)
                or any(metrics.get(field) != 0 for field in (
                    "degraded_baskets", "swap_unmodeled_baskets", "skipped_missing_price"))):
            return False
        top_metrics = top_engines.get(name)
        if (not isinstance(top_metrics, dict)
                or not isinstance(top_metrics.get("baskets"), int)
                or isinstance(top_metrics.get("baskets"), bool)
                or top_metrics["baskets"] < 1):
            return False

    runs_summary = entry.get("runs_summary")
    per_run = runs_summary.get("per_run") if isinstance(runs_summary, dict) else None
    if not isinstance(per_run, list) or len(per_run) != runs:
        return False
    for run in per_run:
        run_engines = run.get("engines") if isinstance(run, dict) else None
        if (not isinstance(run, dict) or run.get("coverage") != coverage
                or not isinstance(run_engines, dict)
                or set(run_engines) != set(engines)):
            return False
        for name in engines:
            metrics = run_engines[name]
            if (not isinstance(metrics, dict)
                    or not isinstance(metrics.get("baskets"), int)
                    or isinstance(metrics.get("baskets"), bool)
                    or metrics["baskets"] < 1
                    or any(metrics.get(field) != 0 for field in (
                        "degraded_baskets", "swap_unmodeled_baskets", "skipped_missing_price"))):
                return False
    return True


def _validate_producer_provenance(entry):
    """Confere metadados gerados no mesmo processo que produziu o backtest.

    O digest do checkout e o digest do resultado são comparados ao checkout e
    ao registro que importa a evidência; isso impede anexar uma execução ou
    métricas alteradas sem que a divergência seja detectada. O mecanismo não é
    uma assinatura criptográfica externa, mas vincula o envelope produzido ao
    conteúdo do resultado antes do append.
    """
    producer = entry.get("producer_provenance")
    window = entry.get("window")
    is_oos = isinstance(window, dict) and window.get("sample_role") == "oos_disjoint"
    if producer is None:
        if is_oos:
            raise ValueError("OOS exige producer_provenance completo")
        return
    if not isinstance(producer, dict):
        raise ValueError("producer_provenance inválido")
    digest = producer.get("code_source_digest")
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("producer_provenance sem digest de código")
    if digest != _source_digest():
        raise ValueError("digest do produtor não corresponde ao checkout importado")
    if producer.get("code_source_files") != list(_PROVENANCE_SOURCE_FILES):
        raise ValueError("lista de fontes do produtor diverge da allowlist de proveniência")
    account = producer.get("account")
    terminal = producer.get("terminal")
    if not isinstance(account, dict) or any(
        account.get(key) in (None, "") for key in ("login", "server", "currency")
    ):
        raise ValueError("producer_provenance sem identidade de conta completa")
    if not isinstance(terminal, dict) or not terminal.get("path"):
        raise ValueError("producer_provenance sem caminho de terminal")
    if not _complete_contract_snapshot(producer.get("contract_size")):
        raise ValueError("producer_provenance sem contrato completo de 28 pares")
    data_snapshot = producer.get("data_snapshot")
    data_digest = data_snapshot.get("series_and_h1_prices_digest") if isinstance(data_snapshot, dict) else None
    if not isinstance(data_digest, str) or len(data_digest) != 64:
        raise ValueError("producer_provenance sem digest dos dados históricos")
    if entry.get("data_snapshot_digest") != data_digest:
        raise ValueError("digest de dados do topo diverge do envelope do produtor")
    engines = entry.get("engines_compared")
    runs = entry.get("runs")
    if (not isinstance(engines, list) or not engines
            or any(not isinstance(name, str) or not name for name in engines)
            or not isinstance(runs, int) or isinstance(runs, bool) or runs < 1
            or len(set(engines)) != len(engines)):
        raise ValueError("envelope do produtor sem engines/runs válidos")
    cost_snapshot = producer.get("cost_snapshot")
    observed_cost_digests = (
        cost_snapshot.get("per_run_observation_digests")
        if isinstance(cost_snapshot, dict) else None
    )
    runs_summary = entry.get("runs_summary")
    per_run = runs_summary.get("per_run") if isinstance(runs_summary, dict) else None
    expected_cost_digests = (
        [run.get("cost_observation_digests") if isinstance(run, dict) else None for run in per_run]
        if isinstance(per_run, list) else None
    )
    def _valid_cost_digest_map(value):
        return (
            isinstance(value, dict)
            and set(value) == set(engines)
            and all(
                isinstance(digest, str) and len(digest) == 64
                and all(char in "0123456789abcdef" for char in digest.lower())
                for digest in value.values()
            )
        )

    cost_shape_valid = (
        isinstance(observed_cost_digests, list)
        and len(observed_cost_digests) == runs
        and all(_valid_cost_digest_map(item) for item in observed_cost_digests)
        and isinstance(per_run, list)
        and len(per_run) == runs
        and all(_valid_cost_digest_map(item) for item in expected_cost_digests)
    )
    if (not isinstance(cost_snapshot, dict)
            or cost_snapshot.get("source") != "current MT5 ticks sampled by CostModel"
            or not cost_shape_valid
            or observed_cost_digests != expected_cost_digests):
        raise ValueError("snapshot de custo do produtor ausente ou inconsistente")
    execution = producer.get("execution")
    if not isinstance(execution, dict) or execution.get("orders_sent") is not False:
        raise ValueError("producer_provenance não atesta ausência de ordens")
    if entry.get("execution") != execution:
        raise ValueError("execução do topo diverge do envelope do produtor")
    if is_oos:
        if (not isinstance(entry.get("note"), str) or not entry["note"].strip()
                or not isinstance(window, dict)
                or not isinstance(window.get("days"), int)
                or not all(isinstance(window.get(key), str) and window[key]
                           for key in ("start_brt", "end_brt", "development_start_brt"))):
            raise ValueError("OOS exige janela completa e nota explícita")
        observed_path = terminal.get("observed_path")
        expected_dir = ntpath.dirname(terminal.get("path", ""))
        if (not observed_path
                or ntpath.normcase(observed_path.rstrip("\\"))
                != ntpath.normcase(expected_dir.rstrip("\\"))):
            raise ValueError("OOS não comprova que o MT5 observado é o terminal configurado")
    if is_oos:
        try:
            if _record_datetime(entry) is None:
                raise ValueError("OOS exige identidade temporal interpretável")
            coverage = entry.get("coverage")
            validate_oos_window(
                window,
                evaluated_dates=(coverage or {}).get("evaluated_dates_brt")
                if isinstance(coverage, dict) else None,
            )
        except (TypeError, ValueError, OSError) as exc:
            raise ValueError(f"OOS com semântica temporal inválida: {exc}") from exc
    if is_oos and (
        producer.get("status") != "complete"
        or execution.get("terminal_isolation_asserted") is not True
        or execution.get("is_production_terminal") is not False
        or account.get("trade_mode") != 0
        or not _complete_oos_coverage(entry, engines, runs)
    ):
        raise ValueError("OOS exige proveniência completa em terminal isolado")
    if is_oos:
        result_digest = producer.get("result_snapshot_digest")
        if (not isinstance(result_digest, str) or len(result_digest) != 64
                or result_digest != result_snapshot_digest(entry)):
            raise ValueError("digest dos resultados diverge do registro OOS")


def _record_datetime(entry):
    """Normaliza identificadores legados para comparação cronológica segura."""
    for field in ("recorded_at_utc", "timestamp_utc", "timestamp"):
        value = entry.get(field)
        if not isinstance(value, str) or not value:
            continue
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc if field != "timestamp" else BRT)
        return parsed.astimezone(timezone.utc)
    return None


def _declared_journal_seq(entry):
    """`journal_seq` declarado em `entry`, só se for um inteiro positivo
    válido (não-bool) — sem olhar pra posição no array."""
    seq = entry.get("journal_seq") if isinstance(entry, dict) else None
    return seq if isinstance(seq, int) and not isinstance(seq, bool) and seq > 0 else None


def _effective_journal_seqs(entries):
    """`journal_seq` efetivo de cada entry em `entries`, numa única passada
    esquerda->direita: o valor declarado, se válido; senão um fallback
    MONOTÔNICO em relação a tudo que já foi visto na mesma passada — nunca a
    posição pura (`index + 1`).

    Achado herdr-review mfc-60 (P2-1/P2-2, `mfc-rev-2`, e MFC60-01/02,
    `mfc-rev`): a versão anterior comparava `index + 1` (escala pequena,
    reinicia do zero a cada checagem) diretamente com valores declarados
    (escala vinda de `max(...) + 1`, potencialmente muito maior) como se
    fossem comparáveis — não são, assim que o journal deixa de ser contíguo
    a partir de 1 (ex.: depois de qualquer truncamento/manutenção). Isso
    produzia duas falhas: uma entrada nova sem `journal_seq` podia cair
    classificada ATRÁS de um valor declarado antigo (perdendo a seleção que
    deveria vencer), e o backfill podia gravar em disco um `index + 1` que
    colide com um valor declarado existente, corrompendo o journal.

    `max(maior_visto_até_aqui + 1, index + 1)` resolve os dois: nunca gera
    um valor menor ou igual a qualquer coisa já vista nesta passada (nunca
    fica pra trás de um declarado alto), e ainda usa a posição como piso
    quando nada foi visto ainda.

    Retorna uma lista de `(entry, seq_efetivo)` na mesma ordem de `entries`.
    Usado tanto por `append_result()` (que persiste o resultado) quanto por
    `select_latest_oos_evidence()` (que só calcula, sem persistir) — chamado
    SEMPRE sobre o array completo original em ambos os casos (nunca uma
    lista pré-filtrada: um filtro antes desta função muda os índices e faz
    `select_latest_oos_evidence()` e `oos_evidence_status()` discordarem
    sobre o mesmo `history` — achado herdr-review mfc-60, MFC60-02/`mfc-rev`,
    P2-3/`mfc-rev-2`)."""
    result = []
    running_max = 0
    for index, entry in enumerate(entries):
        declared = _declared_journal_seq(entry)
        seq = declared if declared is not None else max(running_max + 1, index + 1)
        running_max = max(running_max, seq)
        result.append((entry, seq))
    return result


def _effective_seqs_are_consistent(pairs):
    """Falha fechada se dois `journal_seq` EFETIVOS colidirem — declarado
    contra declarado, declarado contra fallback, ou fallback contra
    fallback (achado herdr-review mfc-60, MFC60-01/`mfc-rev`, P2-1/P3-1
    `mfc-rev-2`: a checagem anterior só comparava valores declarados entre
    si, então uma colisão declarado-vs-fallback não era vista — e o
    fallback antigo, não-monotônico, podia de fato colidir).

    `journal_seq` fica fora do digest (`_RESULT_DIGEST_EXCLUDED_FIELDS`)
    porque é posição no journal compartilhado, não dado de conteúdo — mas
    isso também significa que um payload preservado com esse campo
    adulterado não é detectável pelo digest. Um journal com efetivos
    colidindo não tem uma ordem determinável com segurança; não adivinhar."""
    seqs = [seq for _, seq in pairs]
    return len(seqs) == len(set(seqs))


def oos_evidence_eligible(entry):
    """Retorna se uma entrada OOS satisfaz o envelope completo de evidência.

    Não inclui a checagem de `journal_seq`: a autoridade de ordem é uma
    propriedade do ARRAY inteiro (posição relativa), não de uma entrada
    isolada — ver `_effective_journal_seqs` e `select_latest_oos_evidence`."""
    if not isinstance(entry, dict):
        return False
    window = entry.get("window")
    if not isinstance(window, dict) or window.get("sample_role") != "oos_disjoint":
        return False
    try:
        _validate_producer_provenance(entry)
    except (AttributeError, KeyError, TypeError, ValueError, OSError):
        return False
    return _record_datetime(entry) is not None


def select_latest_oos_evidence(history, *, start_brt=None, end_brt=None):
    """Seleciona somente a entrada OOS completa mais recente.

    A entrada com maior `journal_seq` EFETIVO (ver `_effective_journal_seqs`)
    vence, não `recorded_at_utc`. `supersedes` não decide mais nada; o campo
    pode continuar aparecendo em registros históricos como metadado legado,
    mas não é lido aqui.

    Redesenho pós herdr-review mfc-56/57/58/59/60 + consulta mfc-scout
    (2026-08-31): `recorded_at_utc` era ao mesmo tempo metadado aceito do
    chamador (`setdefault`, nunca coberto pelo digest) E a autoridade de
    precedência da seleção — rodadas de correção sobre o mecanismo de
    `supersedes` baseado nesse timestamp continuaram achando furos. A decisão
    final foi parar de usar timestamp como autoridade inteiramente."""
    history = list(history or [])
    candidates = []
    # Efetivos SEMPRE sobre o `history` completo, nunca uma lista filtrada —
    # ver o aviso em `_effective_journal_seqs` (MFC60-02/P2-3).
    for entry, seq in _effective_journal_seqs(history):
        window = entry.get("window") if isinstance(entry, dict) else None
        if not isinstance(window, dict) or not oos_evidence_eligible(entry):
            continue
        if start_brt is not None and window.get("start_brt") != start_brt:
            continue
        if end_brt is not None and window.get("end_brt") != end_brt:
            continue
        candidates.append((entry, seq))
    if not candidates:
        return None
    # Consistência (sem efetivo duplicado) só precisa valer entre os
    # candidatos que de fato disputam esta seleção — um duplicado em
    # entradas irrelevantes (outra role, outra janela) não deveria bloquear
    # uma seleção válida (confirmado herdr-review mfc-60, `mfc-rev-2`).
    if not _effective_seqs_are_consistent(candidates):
        return None
    return max(candidates, key=lambda pair: pair[1])[0]


def oos_evidence_status(history, *, start_brt=None, end_brt=None):
    """Distingue ausência de OOS de evidência expirada/inválida/ambígua."""
    history = list(history or [])
    records = []
    for entry in history:
        window = entry.get("window") if isinstance(entry, dict) else None
        if not isinstance(window, dict) or window.get("sample_role") != "oos_disjoint":
            continue
        if start_brt is not None and window.get("start_brt") != start_brt:
            continue
        if end_brt is not None and window.get("end_brt") != end_brt:
            continue
        records.append(entry)
    eligible = [entry for entry in records if oos_evidence_eligible(entry)]
    # Chama com o `history` ORIGINAL (não `records`, uma lista já filtrada) —
    # select_latest_oos_evidence() já faz seu próprio filtro completo
    # (role via oos_evidence_eligible + start_brt/end_brt), e precisa do
    # array original pra computar os mesmos efetivos que uma chamada direta
    # computaria (achado herdr-review mfc-60, MFC60-02/`mfc-rev`,
    # P2-3/`mfc-rev-2`: as duas funções discordavam sobre o mesmo `history`
    # porque cada uma indexava um array diferente).
    selected = select_latest_oos_evidence(
        history, start_brt=start_brt, end_brt=end_brt
    )
    if not records:
        status = "no_records"
    elif not eligible:
        status = "expired_or_invalid"
    elif selected is None:
        # Há candidatos elegíveis, mas journal_seq efetivo é ambíguo
        # (duplicado) e a seleção recusou escolher — ver
        # _effective_seqs_are_consistent.
        status = "ambiguous_journal_seq"
    else:
        status = "eligible"
    return {
        "status": status,
        "records": len(records),
        "eligible": len(eligible),
        "selected_recorded_at_utc": (
            _record_datetime(selected).isoformat()
            if selected is not None and _record_datetime(selected) is not None else None
        ),
    }


def _normalize_semantics(entry):
    """Mantém nomes canônicos sem apagar campos antigos de consumidores."""
    script = entry.get("script")
    if script == "backtest_engine_compare.py":
        for metrics in (entry.get("engines") or {}).values():
            if isinstance(metrics, dict):
                if "reconstructed_baskets" not in metrics and "baskets" in metrics:
                    metrics["reconstructed_baskets"] = metrics["baskets"]
                if "baskets" not in metrics and "reconstructed_baskets" in metrics:
                    # Mantém o alias legado dentro do schema v2 para que o
                    # histórico append-only não tenha duas formas do mesmo
                    # campo dependendo da data do registro.
                    metrics["baskets"] = metrics["reconstructed_baskets"]
                metrics.setdefault("active_signals", None)
        entry.setdefault("result_semantics", "reconstructed_baskets_and_active_signals")
    elif script in {"backtest_canonical.py", "measure_composition_effect.py"}:
        entry.setdefault("result_semantics", "reconstructed_backtest_not_live_execution")
    return entry


def _normalize_legacy_entry(entry):
    if not isinstance(entry, dict):
        return entry
    normalized = dict(entry)
    normalized["schema_version"] = SCHEMA_VERSION
    normalized.setdefault("recorded_at_utc", None)
    normalized.setdefault("timestamp_utc", None)
    normalized.setdefault("provenance", _provenance_for(normalized, legacy=True))
    return _normalize_semantics(normalized)


@contextmanager
def _exclusive_lock():
    """Serializa append entre threads e processos, sem criar artefato no repo."""
    try:
        import fcntl
    except ImportError:  # pragma: no cover - caminho Windows
        fcntl = None
    try:
        import msvcrt
    except ImportError:  # pragma: no cover - caminho Unix
        msvcrt = None

    lock_fd = os.open(_lock_path(), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if fcntl is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
        elif msvcrt is not None:
            os.lseek(lock_fd, 0, os.SEEK_SET)
            msvcrt.locking(lock_fd, msvcrt.LK_LOCK, 1)
        yield
    finally:
        if fcntl is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        elif msvcrt is not None:
            os.lseek(lock_fd, 0, os.SEEK_SET)
            msvcrt.locking(lock_fd, msvcrt.LK_UNLCK, 1)
        os.close(lock_fd)


def append_result(record):
    """Acrescenta um registro a um array JSON em disco, escrita atômica
    (tempfile + os.replace) — mesmo padrão de
    agents/portfolio_executor.py::_atomic_write_json, reimplementado aqui
    pra não importar módulo de produção só por isso."""
    directory = os.path.dirname(RESULTS_LOG_PATH) or "."
    os.makedirs(directory, exist_ok=True)
    with _THREAD_LOCK, _exclusive_lock():
        try:
            with open(RESULTS_LOG_PATH, "r", encoding="utf-8") as f:
                log = json.load(f)
            if not isinstance(log, list):
                raise ValueError(f"{RESULTS_LOG_PATH} não contém uma lista JSON")
            log = [_normalize_legacy_entry(entry) for entry in log]
        except FileNotFoundError:
            log = []
        # journal_seq é a autoridade de ordem de append (redesenho pós
        # herdr-review mfc-56/57/58/59/60 + consulta mfc-scout, 2026-08-31;
        # ver oos_evidence_eligible/select_latest_oos_evidence). Registros
        # antigos sem o campo recebem backfill pela mesma passada monotônica
        # de `_effective_journal_seqs` usada na leitura. A consistência é
        # checada sobre os EFETIVOS, DEPOIS do backfill — checar só os
        # declarados ANTES do backfill (versão mfc-59) deixava o próprio
        # backfill gravar uma colisão em disco sem detectar (achado
        # herdr-review mfc-60, MFC60-01/`mfc-rev`, P2-1/`mfc-rev-2`). Falha
        # fechada (não persiste nada) se os efetivos colidirem — não há uma
        # próxima sequência segura de derivar nesse estado.
        effective_pairs = _effective_journal_seqs(log)
        if not _effective_seqs_are_consistent(effective_pairs):
            raise ValueError(
                "journal_seq duplicado no histórico existente — journal "
                "inconsistente, requer reparo manual antes de um novo append"
            )
        for old, seq in effective_pairs:
            if isinstance(old, dict):
                old["journal_seq"] = seq
        next_seq = max((seq for _, seq in effective_pairs), default=0) + 1

        entry = dict(record)
        entry["schema_version"] = SCHEMA_VERSION
        # recorded_at_utc/timestamp_utc nunca vêm do chamador (mesmo
        # tratamento de provenance/supersedes abaixo): antes, um valor
        # fornecido pelo chamador podia ficar no futuro e, combinado com a
        # ordenação por timestamp que existia até mfc-57, virava
        # permanentemente inamovível como evidência selecionada (achado
        # herdr-review mfc-58, P2-1). Continuam existindo só como identidade
        # temporal auditável/informativa — journal_seq decide a seleção.
        entry["recorded_at_utc"] = datetime.now(timezone.utc).isoformat()
        entry["timestamp_utc"] = entry["recorded_at_utc"]
        # Never persist caller-supplied provenance. It is metadata generated
        # from the current checkout/runtime, so even a forged "complete"
        # object is replaced before the append.
        entry.pop("provenance", None)
        # supersedes não decide mais nada (ver select_latest_oos_evidence) —
        # nunca aceito do chamador nem mais derivado aqui.
        entry.pop("supersedes", None)
        entry["journal_seq"] = next_seq
        _validate_producer_provenance(entry)
        entry["provenance"] = _provenance_for(entry)
        log.append(_normalize_semantics(entry))
        fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(log, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, RESULTS_LOG_PATH)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    return RESULTS_LOG_PATH
