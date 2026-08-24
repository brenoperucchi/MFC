# Serviço do terminal MT5 dedicado ao MFC

Mantém de pé a instância MT5 `/portable` da conta demo do MFC
(`D:\MetaTradersWSL\mfc`), no WSL. Instalação:

```bash
mkdir -p ~/bin ~/.config/systemd/user
cp css-mt5-kill.sh ~/bin/ && chmod +x ~/bin/css-mt5-kill.sh
cp css-mt5-mfc.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now css-mt5-mfc.service
```

## Por que existe o `css-mt5-kill.sh`

`terminal64.exe` roda no lado Windows via interop do WSL. O processo que o
systemd rastreia é o wrapper `/init`, **não** o processo Windows. Sem os
ganchos `ExecStartPre`/`ExecStop`:

- `systemctl stop`/`restart` mata só o wrapper e deixa o terminal **órfão**;
- o relançamento seguinte vê a instância já rodando, foca a janela e sai com
  código 0;
- com `Restart=always`, isso vira **loop de relançamento** até estourar o
  `StartLimitBurst`.

Comprovado no log do próprio MT5 (`logs/YYYYMMDD.log`), com cinco linhas
seguidas de `terminal process already started` / `exit with code 0`.

O filtro por caminho no script garante que os outros terminais MT5 desta
máquina (outras estratégias, outras contas) nunca sejam tocados.

## Boot

Não é este serviço que sobe o WSL. Isso vem da tarefa `WSL Autostart` do
Task Scheduler do Windows (`C:\scripts\startup.vbs`), que roda no **logon**.
Reboot sem logon não traz nada de volta — risco já existente para as outras
estratégias da máquina, não introduzido aqui.

## Gráficos

Os 8 gráficos (um EA por moeda) vêm de `scripts/setup_mt5_portfolios.py`, que
escreve em `MQL5/Profiles/Charts/Default/`. Atenção: o caminho legado
`<base>/profiles/charts/` existe mas **não é lido** pelo build 6140 —
verificado na instância real (o terminal ignorou por completo).


---

# Serviço do scheduler (`css-scheduler-mfc`)

Roda `scripts/scheduler_daemon.py`, que é quem decide e dispara: 21:00 análise,
21:02 sinais, 21:05 **abertura de cesta real**, 08:00 fechamento, 08:05
auditoria, 08:10 reconciliação.

```bash
# o repo precisa estar em ~/Devs/miqueias/MFC nesta máquina
cp css-scheduler-mfc.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now css-scheduler-mfc.service
journalctl --user -u css-scheduler-mfc -f
```

## Roda com o Python do Windows, não o do WSL

O pacote `MetaTrader5` só existe em Windows. Além disso, sob Python POSIX
`os.path.dirname(r"D:\...\terminal64.exe")` devolve string vazia — a ponte de
arquivos com o EA (`get_mt5_files_dir()`) quebraria em silêncio. O wrapper
`scripts/systemd/scheduler-daemon-wsl.sh` cuida disso.

## Kill switch

```bash
# bloqueia QUALQUER cesta nova (fechar nunca é bloqueado)
touch ~/Devs/miqueias/MFC/data/CSS_KILL.flag
# libera
rm ~/Devs/miqueias/MFC/data/CSS_KILL.flag
```

**O serviço foi instalado com o kill switch ARMADO de propósito**, para que a
primeira ordem real seja uma decisão explícita e não efeito colateral da
instalação. Enquanto o arquivo existir, o 21:05 recusa abrir e diz o motivo no
log.

## Log

`sys.stdout.reconfigure(..., line_buffering=True)` no daemon não é cosmético:
sem isso o buffer de bloco engole a saída e o journal não mostra nada do que
aconteceu às 21:05.
