#!/usr/bin/env bash
# Wrapper do scheduler_daemon.py para systemd no WSL.
#
# Roda com o Python do WINDOWS, não o do WSL: o pacote MetaTrader5 só existe
# em Windows, e é ele quem fala com o terminal. Efeito colateral bom: MT5_PATH
# e get_mt5_files_dir() manipulam caminhos Windows nativamente — sob Python
# POSIX, os.path.dirname(r"D:\...\terminal64.exe") devolveria string vazia e a
# ponte de arquivos com o EA quebraria em silêncio.
#
# O cwd é um caminho WSL; o interop expõe isso ao processo Windows via
# \\wsl.localhost\... — mesmo padrão já usado pelos outros serviços da máquina.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

PYTHON_BIN="${CSS_PYTHON:-/mnt/c/WINDOWS/py.exe}"
# 3.12 explícito: o default do py.exe pode subir de versão e nem todo pacote
# tem wheel pronto na mais nova.
PYTHON_VERSION_FLAG="${CSS_PYTHON_VERSION_FLAG:--3.12}"

cd "${PROJECT_DIR}"
export PYTHONPATH="${PROJECT_DIR}${PYTHONPATH:+:${PYTHONPATH}}"
export PYTHONUNBUFFERED=1

exec "${PYTHON_BIN}" ${PYTHON_VERSION_FLAG} scripts/scheduler_daemon.py
