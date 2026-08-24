#!/usr/bin/env bash
# Envia um .mq5 pra instância MT5 portable dedicada ao MFC (WSL do Breno,
# systemd --user css-mt5-mfc.service) e compila via MetaEditor64.exe em modo
# batch (interop do WSL, sem precisar abrir GUI). Imprime o log de compilação.
#
# Uso: scripts/compile_ea_remote.sh [caminho-do-.mq5-local]
# Default: mt5/CSS_Portfolio_Basket_EA.mq5
#
# Variáveis de ambiente opcionais:
#   CSS_MT5_SSH_HOST            (default: 192.168.0.125)
#   CSS_MT5_REMOTE_PORTABLE_DIR (default: /mnt/d/MetaTradersWSL/mfc)

set -euo pipefail

SSH_HOST="${CSS_MT5_SSH_HOST:-192.168.0.125}"
REMOTE_PORTABLE_DIR="${CSS_MT5_REMOTE_PORTABLE_DIR:-/mnt/d/MetaTradersWSL/mfc}"
REMOTE_EXPERTS_SUBDIR="MQL5/Experts/MFC"
LOCAL_EA="${1:-mt5/CSS_Portfolio_Basket_EA.mq5}"
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=10)

if [ ! -f "$LOCAL_EA" ]; then
  echo "Arquivo não encontrado: $LOCAL_EA" >&2
  exit 1
fi

EA_FILENAME="$(basename "$LOCAL_EA")"
REMOTE_EA_DIR="$REMOTE_PORTABLE_DIR/$REMOTE_EXPERTS_SUBDIR"

# Caminhos em formato Windows (o MetaEditor não entende /mnt/d/...).
to_win_path() {
  printf '%s' "$1" | sed -E 's#^/mnt/([a-zA-Z])#\1:#; s#/#\\#g'
}
WIN_EA_PATH="$(to_win_path "$REMOTE_EA_DIR/$EA_FILENAME")"
WIN_LOG_PATH="$(to_win_path "$REMOTE_EA_DIR/compile.log")"

echo "[1/3] Enviando $LOCAL_EA -> $SSH_HOST:$REMOTE_EA_DIR/"
ssh "${SSH_OPTS[@]}" "$SSH_HOST" "mkdir -p '$REMOTE_EA_DIR'"
rsync -avz -e "ssh ${SSH_OPTS[*]}" "$LOCAL_EA" "$SSH_HOST:$REMOTE_EA_DIR/"

echo "[2/3] Compilando via MetaEditor64.exe (interop WSL)..."
# compile.log antigo fora do caminho, senão um log vazio de uma falha de
# lançamento do MetaEditor pode ser confundido com um log real desatualizado.
ssh "${SSH_OPTS[@]}" "$SSH_HOST" "rm -f '$REMOTE_EA_DIR/compile.log'"
ssh -o BatchMode=yes -o ConnectTimeout=60 "$SSH_HOST" bash -s <<REMOTE_SCRIPT
"$REMOTE_PORTABLE_DIR/MetaEditor64.exe" /portable /compile:"$WIN_EA_PATH" /log:"$WIN_LOG_PATH" || true
REMOTE_SCRIPT

echo "[3/3] Aguardando compile.log e imprimindo (o MetaEditor grava em UTF-16LE):"
for i in 1 2 3 4 5 6 7 8 9 10; do
  if ssh "${SSH_OPTS[@]}" "$SSH_HOST" "test -s '$REMOTE_EA_DIR/compile.log'"; then
    break
  fi
  sleep 1
done
ssh "${SSH_OPTS[@]}" "$SSH_HOST" \
  "iconv -f UTF-16LE -t UTF-8 '$REMOTE_EA_DIR/compile.log' 2>/dev/null || cat '$REMOTE_EA_DIR/compile.log' 2>/dev/null || echo '(compile.log não apareceu — MetaEditor pode ter falhado ao iniciar)'"
