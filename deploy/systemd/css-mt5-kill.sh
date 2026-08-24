#!/usr/bin/env bash
# Encerra o processo Windows do terminal MT5 DEDICADO AO MFC.
#
# Necessario porque terminal64.exe roda no lado Windows via interop do WSL: o
# processo que o systemd rastreia e' o wrapper /init, nao o processo Windows.
# Matar o wrapper (systemctl stop/restart) deixa o terminal vivo e orfao, e o
# relancamento seguinte so foca a janela existente e sai com codigo 0 — o que
# com Restart=always vira loop ate o StartLimit.
#
# O filtro por caminho garante que os OUTROS terminais MT5 desta maquina
# (irai, ira_ticks, irai_forex_axi, pairtrading — contas diferentes) nunca
# sejam tocados.
PS='/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe'
[ -x "$PS" ] || exit 0
"$PS" -NoProfile -Command \
  "Get-Process terminal64 -ErrorAction SilentlyContinue | Where-Object { \$_.Path -like '*MetaTradersWSL\mfc*' } | Stop-Process -Force" \
  >/dev/null 2>&1
exit 0
