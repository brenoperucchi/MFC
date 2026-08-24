#!/usr/bin/env bash
# Wrapper da dashboard (web/server.py) para systemd no WSL. Mesmo padrão de
# css-scheduler-mfc: roda com o Python do WINDOWS, porque é lá que o pacote
# MetaTrader5 existe — sem isso a dashboard cai no modo fallback/simulado.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

PYTHON_BIN="${CSS_PYTHON:-/mnt/c/WINDOWS/py.exe}"
PYTHON_VERSION_FLAG="${CSS_PYTHON_VERSION_FLAG:--3.12}"

cd "${PROJECT_DIR}"
export PYTHONPATH="${PROJECT_DIR}${PYTHONPATH:+:${PYTHONPATH}}"
export PYTHONUNBUFFERED=1

exec "${PYTHON_BIN}" ${PYTHON_VERSION_FLAG} web/server.py
