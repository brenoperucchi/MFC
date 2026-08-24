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

## Live MT5 execution (branch `fase-1-hibrido`)

Real order sending lives in `agents/portfolio_executor.py`. Python decides and opens
the basket (21:05 BRT, via `scripts/scheduler_daemon.py`); the MQL5 EA
(`mt5/CSS_Portfolio_Basket_EA.mq5`) is the closing guardian — `InpEaOpensBasket=true`
reverts to the EA doing both, without recompiling.

Every safety gate is checked inside `open_portfolio_basket()`, in order: kill switch →
account identity (`CSS_MT5_EXPECTED_LOGIN`) → demo lock (`CSS_LIVE_TRADING`) →
idempotency → netting symbol collision → exposure caps → symbol/tick preflight
(all-or-nothing) → broker-side catastrophic stop-loss. All configured via `.env`
(see `.env.example`); a missing/invalid value fails closed, never open.

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
