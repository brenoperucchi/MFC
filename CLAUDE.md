# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

CSS Institutional — a Currency Slope Strength (CSS) platform: a multi-timeframe relative-strength
indicator for the 8 major FX currencies (USD, EUR, GBP, CHF, JPY, AUD, CAD, NZD) and the 28 pairs
they form, plus a track-record/backtest engine that audits a 21h→08h BRT basket-trading strategy
against live MetaTrader 5 (MT5) history. It ships as three parallel implementations of the *same*
indicator math (Python for the web platform, MQL5 for the MetaTrader terminal, Pine Script for
TradingView) plus a FastAPI backend + vanilla-JS/Canvas frontend, and a daily automated
report/knowledge-log routine.

Full docs live in `docs/`: `SPEC.md` (architecture), `API.md` (REST reference),
`MATHEMATICAL_MODELS.md` (exact formulas), `DATABASE_DATA_FLOW.md` (JSON schemas & caches).
Read those before making non-trivial changes — this file only summarizes what's needed to
navigate the code.

## Environment note

This checkout runs on Linux, but the project is developed and *executed* on Windows against a
locally-installed MetaTrader 5 terminal — the `MetaTrader5` Python package only works on Windows.
`daily_css_routine.py` hardcodes `BASE_DIR = r"c:\Users\ryzen\Downloads\Antigravity\MFC"`, and
`run_daily_css.bat` / `start_web.bat` hardcode the same Windows path. `web/css_service.py` and
`web/history_tracker.py` instead derive `BASE_DIR` from `__file__`, so the FastAPI app itself is
portable — only the `.bat` launchers and `daily_css_routine.py` are Windows-pinned. When
`MetaTrader5` isn't importable (e.g. on Linux) or the terminal isn't reachable, `css_service.py`
transparently falls back to simulated/cached data so the web UI keeps working.

## Commands

No `requirements.txt`/`pyproject.toml` exists — install manually:

```bash
pip install fastapi uvicorn MetaTrader5 pandas numpy matplotlib
```

Run the web platform (FastAPI + static SPA) on `http://localhost:8050`:

```bash
python web/server.py          # or start_web.bat on Windows
```

Run the 21:00 BRT daily report routine (writes `reports/YYYYMMDD/` and appends
`daily_routine.log`) — Windows-only due to hardcoded paths:

```bash
python daily_css_routine.py   # or run_daily_css.bat on Windows
```

Regenerate the static Firebase Hosting bundle in `public/` from `web/static/` plus live/cached
API snapshots (requires the web server running locally for full snapshot generation):

```bash
python scripts/build_firebase_bundle.py
firebase deploy               # separate, manual step — do not run without being asked
```

Run the test suite (no external test runner config — plain `unittest`/`pytest`):

```bash
python -m pytest tests/ -q          # all
python -m pytest tests/test_portfolio_safety.py -q   # execution safety gates
```

`tests/test_score_unification.py` calls `generate_and_save_daily_signals()` for real (to
compare its computed scores against the live dashboard engine), but patches the file-write
step so it never touches `data/portfolio_signals_live.json` on disk.

## Reviewer colleagues

If you're running as `mfc-exec` inside a Herdr workspace, `mfc-rev` (Codex)
and `mfc-rev-2` (Claude Opus) are two independent reviewers alive in sibling
panes of the same workspace right now — not hypothetical, nothing to set up.
Given this ships code that sends real orders to a live market, treat review
as load-bearing, not optional: when you finish a reviewable unit of work, use
the `herdr-review` skill to dispatch a blind, parallel review round to both
and resolve their findings (CONFIRMED/UNIQUE/CONFLICT) before considering the
work done. The same pair can also weigh in on an open design question
*before* you build something — given the safety stakes here, prefer asking
first over reviewing after; see the `herdr-ask` skill for that case.
Requires `HERDR_ENV=1`; if that's unset, you're not in a Herdr-managed pane
and none of this applies.

## Live MT5 execution (branch `fase-1-hibrido`)

Real order sending lives in `agents/portfolio_executor.py`. Python decides and opens
the basket (21:05 BRT, via `scripts/scheduler_daemon.py`); the MQL5 EA
(`mt5/CSS_Portfolio_Basket_EA.mq5`) is the closing guardian — `InpEaOpensBasket=true`
reverts to the EA doing both, without recompiling.

Every safety gate is checked inside `open_portfolio_basket()`, in order: kill switch →
execution config validity (`check_execution_config()` — `CSS_MAX_LOT`,
`CSS_MAX_CONCURRENT_BASKETS`, `CSS_CATASTROPHIC_SL_PIPS`, the two
`CSS_AMBIGUOUS_CONFIRM_*`, `CSS_MIN_MARGIN_FREE`) → account identity
(`CSS_MT5_EXPECTED_LOGIN`) → demo lock (`CSS_LIVE_TRADING`) → minimum free
margin, flat floor (`check_account_gate()`, against the account's live
`margin_free` vs. `CSS_MIN_MARGIN_FREE` — item 2 of the upstream/Miquéias
reconciliation plan, rewritten rather than ported: upstream fails open when
`account_info()` is `None` or raises, and hardcodes the currency in its
message; here a missing/non-finite `margin_free` also refuses, fail-closed,
and the message names the account's real currency) → idempotency → exposure
caps → netting symbol collision → symbol/tick preflight (all-or-nothing) →
**aggregate margin check** (sums `order_calc_margin()` over the resolved 7 legs
against a *fresh* `margin_free` read, refuses if it doesn't cover the sum plus
the `CSS_MIN_MARGIN_FREE` reserve — errors `margin_calc_failed`/
`insufficient_aggregate_margin`) → broker-side catastrophic stop-loss.

The margin gate is two layers, decided in two steps (herdr-review round 15
found the gap; herdr-ask consulta 3 + an ephemeral gpt-5.6-sol arbiter decided
the follow-up, both 2026-08-27, because the two reviewers disagreed on timing,
not on the fix itself): `CSS_MIN_MARGIN_FREE` alone (achado P2-1/F15-01) is a
flat, broker/leverage-agnostic floor, not a computed "do these 7 legs fit"
check — a 1:100-leverage account could pass it with less free margin than a
basket actually needs, with no rollback for a partially-filled basket. The
Breno's answer to "how soon is a real-money account" (weeks, not months) is
what decided implementing the aggregate check immediately instead of deferring
it to the `CSS_CATASTROPHIC_SL_PIPS` recalibration milestone. **`order_calc_margin()`
is Windows-only and untestable on a Linux checkout — validate it manually on
the real terminal, comparing predicted vs. observed margin drop across a
controlled demo execution, before ever setting `CSS_LIVE_TRADING=true`.** See
the comment on `_EXECUTION_CONFIG_SPEC` and on the aggregate-margin block (right
before the order-send loop) in `agents/portfolio_executor.py`.

Separately, `execute_phase_2105()` in `scripts/scheduler_daemon.py` now sends an
external alert (file log + best-effort Telegram) for any PARTIAL basket, whatever
the cause (margin, requote, disabled symbol, dropped connection) — before this,
a partial basket was only a `print()` nobody read, and the 08:10 reconciliation
doesn't catch it (a partial basket closes cleanly by magic, leaving no orphan
position to detect). This only alerts; it does not auto-close the partial basket
— that's a separate, deliberately undecided question (risk-tolerance judgment
call, not a technical one). Each leg in `open_portfolio_basket()`'s result ends
in one of three states — `OPENED` (confirmed open), `ERROR` (confirmed NOT
open), or `UNCERTAIN` (order sent, or an ambiguous broker response, with no
confirmation either way — achado MFC18-01, herdr-review round 18) — and the
top-level `uncertain_count` tells the scheduler when `success=False` still
might mean real, unconfirmed exposure rather than a clean refusal; that case is
alerted as PARTIAL too, not silently dropped into "refused". All
configured via `.env` (see `.env.example`); a missing/invalid value fails closed,
never open — an explicitly-set-but-invalid value (bad cast, out-of-range, or a value
that would be silently clamped) refuses to open rather than substituting a default
that may be more permissive than what the operator intended (reviewed 2026-08-27,
achado F-06). `_env_number()`/`_clamp()` themselves stay non-fatal at import time
(a bad `.env` value must never crash the whole web server or scheduler daemon
process, only refuse the specific open attempt) — `check_execution_config()`
re-validates the raw env values at the point of use instead of trusting the
already-substituted module constants. "Missing" means two different things by
design (raised as achado F06-2, decided by user 2026-08-27; refined 2026-08-27
after achado P3-1 caught an overly-broad first wording): identity/permission gates
(`CSS_MT5_EXPECTED_LOGIN`, `CSS_LIVE_TRADING`) have no safe default — missing is
genuinely ambiguous and must refuse. `CSS_MT5_SYMBOL_SUFFIX` is a different case:
missing is the *designed* normal path (`.env.example` ships it empty; that's what
drives the auto-detection in `web/css_service.py`) and opens normally — what must
refuse is *resolution failing* (the `#unresolved-family` marker reaching the
preflight), not the variable being absent. The six `check_execution_config()`
variables are tunable safety margins with documented, conservative defaults
(150 pips, 0.01 lot, 8 baskets, 3 attempts, 1.0s, 50 min free margin) — missing
there means "use the documented default" and is allowed to open, exactly like it
always has. Only an explicitly-provided-and-invalid value among those six
refuses — except `CSS_MIN_MARGIN_FREE`'s default is a documented *heuristic*
floor, not a proven-safe one (see the margin-gate paragraph above); the live
`margin_free` value it's compared against is a different case entirely and
always fails closed when missing/non-finite, with no default at all. Exposure
caps and netting symbol collision are both pure refusal checks reading the same
`open_magics` snapshot with no side effects, so their relative order doesn't change
the open/refuse decision — only which error/message comes back when both would have
refused (verified true since at least `c24a44c`, reviewed rounds 4–5). Reordering
either of THESE two specifically is not the P0 "gate ordering" case this invariant
exists to prevent; reordering any gate relative to kill switch, account identity,
demo lock, minimum free margin, or idempotency still is.

**Kill switch:** `touch data/CSS_KILL.flag` blocks any NEW basket. Closing is never
blocked — reducing risk always proceeds. The EA reads the same file name from the
instance's own `MQL5/Files`.

The broker symbols carry a suffix (`EURUSDm` on the Exness demo in use): set
`CSS_MT5_SYMBOL_SUFFIX`, and go through `to_broker_symbol()`/`from_broker_symbol()`
in `web/css_service.py` at every MT5 boundary — without it nothing resolves and the
track record silently falls back to simulated history.

Compile the EA against the dedicated terminal (over SSH, no GUI):

```bash
scripts/compile_ea_remote.sh                 # exits non-zero on compile errors
python scripts/setup_mt5_portfolios.py       # writes the 8 charts (MQL5/Profiles/Charts/Default)
```

`deploy/systemd/` holds the unit that keeps that terminal alive; read its README before
touching it — the WSL interop makes `systemctl stop` leave the Windows process orphaned.

## Architecture

### `agents/` — pure analysis layer (no I/O, no MT5, no FastAPI)

A 4-stage pipeline, each stage a plain function taking numpy-array score series in and returning a
dict verdict out. Called from both `web/css_service.py` (live web platform) and
`daily_css_routine.py` (daily batch report) — these two callers must stay behaviorally identical.

1. `agents/triad_analyzer.py::analyze_tf_triad(tf_name, series)` — the base primitive. Classifies
   one timeframe's score series into the "Tríade Analítica": Região (position vs. the ±0.20 stop
   zones and 0.00 equilibrium line), Ciclo Atual, Ciclo Devendo, and Score & Angulação. Everything
   else in `agents/` is built on top of this.
2. `agents/macro_analyzer.py::analyze_macro_currency` — MN1/W1/D1 structural bias per currency.
3. `agents/operational_analyzer.py::analyze_operational_currency` — H4/H1 timing/momentum,
   including "retomada de força/fraqueza" (box invalidation) triggers, checked against the macro
   verdict for divergence alerts.
4. `agents/confluence_engine.py` — `evaluate_currency_confluence` combines macro (60% weight) +
   operational (40% weight) into one per-currency verdict; `evaluate_28_pairs_confluence` derives
   the 28-pair ranking (`Base Power − Quote Power`) from the 8 per-currency verdicts.

`.agents/skills/css-macro-analyzer/` and `.agents/skills/css-operational-analyzer/` are Claude
Code agent-skill definitions that restate the macro/operational rules in natural language for
subagent use — keep them in sync with `agents/macro_analyzer.py` / `agents/operational_analyzer.py`
if that logic changes, but they are not imported by any Python code.

### `web/` — service + API layer

- **`web/css_service.py`** — the numerical core and a singleton (`css_engine`, actual
  Borg/singleton via `__new__`). `calc_lwma`/`calc_atr_sma`/`calculate_full_css` implement the
  exact LWMA(21) + ATR·SMA(100) slope formula from `mt5/css.mql5` (see
  `docs/MATHEMATICAL_MODELS.md` §1) — this must produce numerically identical output to the MQL5
  indicator and the Pine Script port (`CSS.pine`); don't change the math here without updating all
  three. `CSSDataEngine.update_data()` pulls all 28 pairs × 5 timeframes from MT5, decomposes pair
  slopes into per-currency CSS scores, runs the `agents/` pipeline per currency, ranks the 28
  pairs, and detects score crossovers (`detect_currency_crossovers`) — all cached in-memory with a
  60s TTL and throttled to at most one recompute per 3s. Falls back to
  `_generate_fallback_data()` (static/simulated series) when MT5 is unreachable.
- **`web/history_tracker.py`** — `TrackRecordEngine`, the 21h→08h BRT basket backtest/audit
  engine. Persists to `data/simulated_trades_history.json` (schema documented in
  `docs/DATABASE_DATA_FLOW.md` §2.1: sessions → portfolios (one per qualifying currency, 7 pairs
  each) → per-pair entry/exit/MAE/MFE/PnL). `convert_pnl_to_usd` handles quote-currency → USD
  conversion (via a live cross pair when available, else a hardcoded fallback rate table).
  Entry rule: a currency only gets a basket when ≥4 of 5 timeframes agree on direction.
- **`web/server.py`** — FastAPI app exposing the REST surface documented in `docs/API.md`
  (`/api/css/*`, `/api/pairs`, `/api/crossovers*`, `/api/track-record/*`, `/api/history/*`), and
  mounts `web/static/` at `/static` plus serving `index.html` at `/`.

### Frontend: `web/static/` is the source, `public/` is a generated build artifact

`web/static/{index.html,styles.css,app.js}` is the real vanilla-JS/Canvas SPA — edit it directly.
`public/` (plus `public/api/*.json`) is a static snapshot generated by
`scripts/build_firebase_bundle.py` for Firebase Hosting deploys, with `/static/` paths rewritten
to root and API responses baked into JSON files per `firebase.json`'s rewrite rules. **Never hand
edit files under `public/`** — regenerate them from `web/static/` via the build script instead.
`app.js` is organized around: canvas chart rendering (`renderChart`, `drawInstitutionalLevels`,
`drawTimeAxis`), the anti-collision floating badge layout (`resolveBadgePositions`), the
pair-matrix modal, and the track-record modal (live/audit/analytics tabs, equity-curve and
CSS-evolution canvases, crossovers modal).

### Other implementations of the same indicator

- `mt5/css.mql5` — the canonical MQL5 indicator for MetaTrader 5; the Python math in
  `web/css_service.py` is a port of this file.
- `CSS.pine` — Pine Script v6 (`indicator(..., dynamic_requests=true)`) port for TradingView.

### Daily reporting & knowledge log

- `daily_css_routine.py` runs at 21:00 BRT (via `run_daily_css.bat`, e.g. Windows Task
  Scheduler), duplicates the `agents/` pipeline call and the 28-pair/currency constant lists from
  `web/css_service.py` (keep these three lists — `ALL_28_PAIRS`, `CURRENCIES`, `CCY_COLORS` — in
  sync across `web/css_service.py` and `daily_css_routine.py`), and writes
  `reports/YYYYMMDD/analise_diaria.md` plus per-currency dashboard PNGs (matplotlib,
  dark-background style).
- `log_conhecimento/YYYYMMDD.md` is a hand/agent-maintained trading journal with a fixed template
  (see `log_conhecimento/README.md` for the required sections: matrices, setup thesis, expected
  vs. actual outcome). Every new entry must also be added as a row to `log_conhecimento/INDEX.md`.
