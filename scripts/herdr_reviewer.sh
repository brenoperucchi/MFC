#!/usr/bin/env bash
# Sobe o revisor Codex do MFC como um agent do Herdr, num pane dedicado.
#
# A fonte de verdade do papel é .codex/reviewer.md, versionado neste repositório.
# O profile do Codex (~/.codex/mfc-reviewer.config.toml) é um artefato regerado a
# cada execução — editar o .md aqui é o único jeito de mudar o revisor.
#
# Uso:
#   scripts/herdr_reviewer.sh [nome-do-agent]      # default: revisor-mfc
#   scripts/herdr_reviewer.sh revisor-mfc --tab    # em aba nova em vez de split
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROLE_FILE="$REPO/.codex/reviewer.md"
PROFILE_NAME="mfc-reviewer"
PROFILE_FILE="${CODEX_HOME:-$HOME/.codex}/${PROFILE_NAME}.config.toml"

AGENT_NAME="revisor-mfc"
USE_TAB=0
for arg in "$@"; do
  case "$arg" in
    --tab) USE_TAB=1 ;;
    -*)    echo "flag desconhecida: $arg" >&2; exit 2 ;;
    *)     AGENT_NAME="$arg" ;;
  esac
done

die() { echo "herdr_reviewer: $*" >&2; exit 1; }

[[ -f "$ROLE_FILE" ]] || die "não achei $ROLE_FILE — este revisor só existe dentro do MFC."
[[ "${HERDR_ENV:-}" == "1" ]] || die "rode de dentro do Herdr (HERDR_ENV=1)."
command -v codex >/dev/null || die "codex não está no PATH."

# TOML literal multi-linha ('''): não interpreta escapes, então backslash e aspas do
# markdown passam intactos. O único caractere que quebraria é a própria fence.
if grep -q "'''" "$ROLE_FILE"; then
  die "$ROLE_FILE contém ''' — isso quebra a fence do TOML gerado."
fi

# 1. Regenera o profile a partir do papel versionado no repo.
{
  echo "# GERADO por scripts/herdr_reviewer.sh — não edite aqui."
  echo "# Fonte: <repo>/.codex/reviewer.md"
  echo 'model = "gpt-5.6-sol"'
  echo 'model_reasoning_effort = "xhigh"'
  echo 'sandbox_mode = "read-only"'
  echo 'approval_policy = "never"'
  echo "developer_instructions = '''"
  cat "$ROLE_FILE"
  echo "'''"
} > "$PROFILE_FILE"

python3 -c "import tomllib,sys; tomllib.load(open(sys.argv[1],'rb'))" "$PROFILE_FILE" \
  || die "o profile gerado não é TOML válido: $PROFILE_FILE"

# 2. Abre o pane (ou aba) já com o cwd travado no MFC.
extract_pane_id() {
  # python3 -c (e nao heredoc) para que o stdin fique livre pro pipe do herdr
  python3 -c '
import json, sys
def walk(node):
    if isinstance(node, dict):
        if isinstance(node.get("pane_id"), str):
            yield node["pane_id"]
        for v in node.values():
            yield from walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk(v)
raw = sys.stdin.read()
try:
    doc = json.loads(raw)
except json.JSONDecodeError:
    sys.exit("resposta do herdr nao e JSON: " + raw[:200])
ids = list(walk(doc))
if not ids:
    sys.exit("sem pane_id na resposta do herdr: " + raw[:200])
print(ids[-1])
'
}

if (( USE_TAB )); then
  PANE_ID=$(herdr tab create --cwd "$REPO" --label "$AGENT_NAME" --focus | extract_pane_id)
else
  PANE_ID=$(herdr pane split --current --direction right --cwd "$REPO" | extract_pane_id)
fi
[[ -n "$PANE_ID" ]] || die "não consegui criar o pane."

# 3. Espera o pane chegar ao prompt de shell ANTES de tentar iniciar o agent.
#
# Sem essa espera, herdr agent start devolve agent_pane_busy; e se o retry for cego,
# cada tentativa re-executa o comando dentro do Codex que ja estava subindo.
shell_ready=0
for _ in $(seq 1 40); do
  title=$(herdr pane get "$PANE_ID" 2>/dev/null \
          | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["pane"].get("terminal_title",""))' 2>/dev/null || true)
  if [[ "$title" == *"@"* ]]; then shell_ready=1; break; fi
  sleep 0.25
done
(( shell_ready )) || die "o pane $PANE_ID nao chegou a um prompt de shell."

# 4. Sobe o Codex — uma unica vez, com o profile do revisor e a raiz travada no MFC.
#
# agent_not_ready nao e falha: o Codex pode estourar o timeout de readiness subindo
# os MCP servers e ainda assim estar rodando. Confirmamos via agent get.
if ! start_out=$(herdr agent start "$AGENT_NAME" --kind codex --pane "$PANE_ID" \
                   --timeout 120000 -- --profile "$PROFILE_NAME" -C "$REPO" 2>&1); then
  case "$start_out" in
    *agent_not_ready*) : ;;
    *) die "herdr agent start falhou: $start_out" ;;
  esac
fi

herdr agent get "$AGENT_NAME" >/dev/null 2>&1 \
  || die "o agent '$AGENT_NAME' nao foi registrado. Ultima resposta: ${start_out:-<vazia>}"

echo "revisor '$AGENT_NAME' pronto em $PANE_ID (profile $PROFILE_NAME, read-only, cwd $REPO)"
echo "prompt:  herdr agent prompt $AGENT_NAME 'revise o diff de HEAD' --wait"
