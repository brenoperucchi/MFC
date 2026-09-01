# Acompanhamento de backtest via web (regression tracking)

## Contexto

Ao longo da sessão de 2026-08-31/09-01 (ver `docs/plans/port-upstream-institutional-matrix.md`,
seção "OOS estendido"), ficou claro que o projeto não tem hoje nenhum mecanismo sistemático
pra saber se uma mudança de código ou parâmetro melhorou ou piorou o desempenho dos motores —
cada comparação (`scripts/backtest_engine_compare.py::compare()`) precisa ser disparada
manualmente por CLI contra um terminal MT5 isolado, e o único registro é o
`reports/backtest_history.json` bruto, sem nenhuma visualização.

O pedido do usuário: (1) uma tabela visível na web com o histórico de execuções (data,
parâmetros, descrição/commit, resultado), (2) um jeito de disparar uma nova execução pela
própria web, (3) no futuro (fora de escopo aqui), otimização/varredura de parâmetros.

A tensão central, já mapeada nesta sessão: o motor `oos_disjoint` (evidência OOS protegida,
`journal_seq` mais alto vence) não pode virar "mais um botão que qualquer um aperta" — isso
contamina exatamente o holdout que o `development_start_brt` existe pra proteger. A solução é
separar os dois papéis explicitamente: acompanhamento de regressão usa sempre
`sample_role="exploratory"` numa janela FIXA e reutilizável; o holdout OOS continua CLI-only,
como hoje.

Um segundo risco real, achado nesta investigação: o processo do `web/server.py` já mantém
conexão MT5 aberta com o terminal **AO VIVO** (`.env` de produção,
`CSS_MT5_TERMINAL_PATH=D:\MetaTradersWSL\mfc\terminal64.exe`). Qualquer disparo de backtest
pela web precisa rodar num **processo separado**, com o ambiente do terminal isolado
(`mfc-backtest`) construído do zero — nunca herdado do processo do servidor.

## Decisões confirmadas com o usuário

1. Trigger roda em processo separado (`subprocess.Popen`), env isolado construído do zero.
2. Nova aba dentro do modal de track-record já existente (não um modal novo).
3. Disparo pela web só aceita `sample_role="exploratory"` — `oos_disjoint` continua CLI-only.
4. Chave de API separada (`CSS_BACKTEST_API_KEY`) — nunca reaproveita `CSS_PORTFOLIO_API_KEY`
   (que abre posição real), pra não aumentar o raio de exposição dessa chave.

## Consulta de design (herdr-ask mfc-13) — achados que mudaram o plano

Antes de implementar, o plano foi submetido a `herdr-ask` (`mfc-rev` + `mfc-rev-2`, cegos um
do outro). Os dois convergiram em quatro pontos que este plano já incorpora abaixo; um deles
(o item 1) é a descoberta mais importante da consulta:

1. **A única coisa que impede o backtest de rodar contra o terminal AO VIVO é ser um
   PROCESSO separado — não a variável de ambiente.** `ensure_mt5()`
   (`agents/portfolio_executor.py`) começa com `if mt5.terminal_info() is not None: return
   True` — ou seja, se o CHAMADOR já tiver uma conexão MT5 aberta (como o `web/server.py` em
   produção, conectado no terminal ao vivo), essa conexão seria reaproveitada
   **sem checar `CSS_MT5_TERMINAL_PATH` nenhum**. `Popen` com env isolado só funciona porque o
   FILHO nasce sem nenhum binding MT5 prévio. **Isto é uma invariante do desenho, não um
   detalhe de implementação: nunca virar thread/chamada in-process, nem "otimizar" isso num
   refactor futuro.**
2. **A checagem de isolamento (`_assert_oos_terminal_configuration()`) só roda pra
   `oos_disjoint`, nunca pra `exploratory`** — que é exatamente o papel que o disparo web usa.
   Fix: religar essa asserção (ou uma equivalente pós-`initialize()`, comparando
   `mt5.terminal_info().path` observado contra o esperado) por `MFC_BACKTEST_TERMINAL_ISOLATED=1`
   estar setado, não por `sample_role` — assim a propriedade fica **verificada**, não só
   arranjada. Mudança pequena em `scripts/backtest_engine_compare.py::compare()`.
3. **As margens de janela crítica (20:55–21:12) são curtas demais e checam o instante
   errado.** `scripts/scheduler_daemon.py` mantém a abertura real tolerante até **21:59** (não
   21:05) — um disparo às 21:15 cairia dentro dessa janela tolerante. Pior: a regra checava só
   o INSTANTE do disparo, não quanto tempo a execução leva — um disparo às 20:50 podia
   atravessar a fase crítica inteira sem violar regra nenhuma.
4. **Persistir a chave de API em `localStorage`, numa página com dezenas de `innerHTML` sem
   nenhum helper de escape, é arriscado** — vira alvo de XSS/extensão maliciosa, e o
   `note`/descrição (texto livre digitado por humano) seria o primeiro campo de texto livre já
   renderizado nessa UI.

Um achado **só do `mfc-rev-2`, com dado real medido**, que mudou o desenho da tabela em si:

5. **O `custo`/`líquido` — as colunas que a tabela mais destacaria — variam ~2x dependendo da
   HORA do disparo, não do código.** Comparando três execuções do MESMO commit
   (`7b22a4b`) na MESMA janela `[2026-06-01,2026-07-16)`: uma rodou num domingo (mercado
   fechado) e mediu custo quase o dobro das outras duas (dias úteis, que batem entre si dentro
   de 0,7%). `bruto`/contagem de cestas foram IDÊNTICOS nas três (parte determinística, não
   depende de tick ao vivo). Sem tratar isso, a tabela geraria alarme falso de "melhorou 64%"
   no primeiro disparo de fim de semana, com zero mudança de código.

## Mudanças no desenho por causa da consulta

- **`scripts/backtest_engine_compare.py::compare()`** ganha uma checagem de isolamento
  pós-conexão sempre que `MFC_BACKTEST_TERMINAL_ISOLATED=1` estiver setado (não só pra
  `oos_disjoint`), e recusa `sample_role="oos_disjoint"` quando uma nova var
  `MFC_BACKTEST_WEB_TRIGGER=1` estiver presente (veto do lado do executor, espelhando a
  asserção — assim nem um endpoint futuro que aceitasse `sample_role` do corpo conseguiria
  contaminar o holdout). Teste novo: congela o argv construído por
  `spawn_isolated_backtest()` (contém `--sample-role exploratory`, `--end-brt` fixo, nenhum
  elemento com `oos`) e o corpo do modelo Pydantic (`{description, runs}` só).
- **Concorrência**: em vez de um `status.json` com validade de 20 minutos como autoridade, o
  FILHO (`run_isolated_backtest.py`, executável standalone) segura o
  `_exclusive_lock()` já implementado em `scripts/_backtest_results_log.py`
  (`fcntl.flock`/`msvcrt.locking` conforme o SO) durante toda a sua vida — morre com o
  processo, inclusive `kill -9`, inclusive restart do servidor, sem constante mágica. O
  endpoint testa o lock em modo não-bloqueante pra responder `409` com certeza, não estimativa.
  `status.json` continua existindo só pra UI (log, PID, início) — deixa de ser autoridade.
  **Nunca usar `os.kill(pid, 0)` como teste de vida — no Windows isso mata o processo
  (`TerminateProcess`), não só verifica.**
- **Janela crítica**: margens ajustadas pra `20:55–22:00` (noite) e `07:55–08:20` (manhã), MAIS
  um watchdog no processo supervisor que **termina o subprocesso filho** se ele ainda estiver
  rodando quando o host entrar na janela crítica — mais seguro que tentar prever duração,
  porque matar o filho é sempre seguro (nunca envia ordem, `append_result()` só escreve no
  fim, de forma atômica; pior caso é perder uma execução de diagnóstico).
- **Mercado fechado**: o endpoint recusa disparo com o mercado fechado (sexta ~17h ET →
  domingo ~17h ET, mesma checagem que `is_market_session_valid()` já faz por noite) — a
  medição de custo por tick ao vivo não tem sentido nesse período.
- **Tabela**: `bruto` e contagem de cestas (`baskets`) viram as colunas PRIMÁRIAS de detecção
  de regressão (determinísticas, reproduzem perfeitamente entre execuções do mesmo commit);
  `custo`/`líquido` ficam marcados como dependentes do momento da medição (badge/tooltip "tick
  ao vivo no instante do disparo"), com uma coluna extra indicando se o mercado estava
  aberto/fechado na hora do run. `runs` passa a default **2** (não 1), expondo o
  `runs_summary` (min/max/mean) já calculado por `_aggregate_pass_summaries` — a tabela mostra
  o piso de ruído em vez de fingir que um número único é preciso.
- **Auth**: `GET /api/backtest-history/trigger/status` passa a exigir a mesma
  `CSS_BACKTEST_API_KEY` (o `log_tail` pode conter proveniência sensível — conta, servidor,
  caminho do terminal — já que o filho carrega o `.env` inteiro na importação, ver nota de
  segredos abaixo). `GET /api/backtest-history` e `GET /.../{journal_seq}` continuam sem auth
  (só leitura, sem `log_tail`).
- **Frontend**: qualquer string vinda do journal (`note`, nomes de engine, etc.) é renderizada
  com `textContent`, nunca interpolada em `innerHTML`. A chave de API vai em
  `sessionStorage` (não `localStorage`) — soma-se ao fato de já ser uma chave dedicada
  (decisão anterior), não a que abre posição real.
- **Nota de segredos, sem mudança de desenho**: o filho importa `agents.portfolio_executor` →
  `web.css_service`, que carrega o `.env` do repo inteiro (`CSS_PORTFOLIO_API_KEY` etc.) —
  `_load_dotenv_if_present()` só PREENCHE chaves ausentes, nunca sobrescreve o que o
  `build_isolated_env()` já setou explicitamente, então o caminho do terminal isolado
  continua vencendo. Mas o whitelist de env não é uma fronteira de contenção de segredo — os
  segredos ficam disponíveis no processo do filho (não usados, mas presentes). Isso é aceitável
  porque o filho nunca expõe esse ambiente pra fora, exceto via `log_tail` — daí a auth nova
  nesse endpoint específico.
- **Proveniência**: `_PROVENANCE_SOURCE_FILES` (`scripts/_backtest_results_log.py`) ganha
  `scripts/run_isolated_backtest.py` na lista, pra manter `code_source_digest` completo.
- **Limite de tamanho**: `description` ganha um máximo (proposto: 500 chars), não só o mínimo
  de 3 — ela vira `note`, que entra no `result_snapshot_digest` auditado.

## Janela de acompanhamento (fixa, nunca relativa a "agora")

`[2026-07-16T21:00:00-03:00, 2026-08-30T21:00:00-03:00)` BRT, 45 dias.

Não é uma janela nova: é a mesma usada nas rodadas de "reprodutibilidade" já registradas
no plano (`mfc-32`/`mfc-34`/`mfc-35`, ver `docs/plans/port-upstream-institutional-matrix.md`),
que começa exatamente onde o holdout OOS atual termina (`development_start_brt=2026-07-16`) —
portanto estruturalmente disjunta do holdout, sem precisar de nenhuma lógica extra de
verificação. `bars_needed_since()`/`tf_counts_for_window()`
(`scripts/backtest_canonical.py:419-478`) já escalam corretamente conforme "agora" avança
(pedem mais barras com o tempo, sem precisar de mudança de código) — confirmado lendo essas
duas funções, e confirmado na consulta que o TF mais restrito (W1) está longe do teto nessa
janela de 45 dias (diferente da janela OOS de 688 dias, onde sobra só 1 barra).

Constantes a fixar em `scripts/run_isolated_backtest.py` (novo arquivo):
```python
REGRESSION_WINDOW_DAYS = 45
REGRESSION_WINDOW_END_BRT = "2026-08-30T21:00:00-03:00"
```

**Primeiro uso recomendado, depois de implementado** (não é trabalho deste plano, é uma
sugestão operacional): rodar o MESMO commit duas vezes na janela fixa antes de confiar na
tabela pra decisão nenhuma — estabelece empiricamente o piso de ruído de custo/tick, já que
hoje não existe no journal nenhum par de execuções independentes do mesmo commit em dia útil
pra medir isso.

## O que já existe e não precisa ser criado

- **Rastreabilidade de commit**: `scripts/_backtest_results_log.py::_code_provenance()`
  (linha 91) já captura `code_commit` (SHA completo) e `worktree_dirty` em TODO registro,
  independente de `sample_role` — confirmado nas últimas 5 entradas reais do journal, e de
  novo na consulta. Nenhum campo novo é necessário; a tabela só precisa ler
  `provenance.code_commit`/`worktree_dirty`.
- **Motor de comparação**: `compare()` não muda em nada na sua lógica de comparação — só ganha
  a checagem de isolamento do item 2 acima, que é aditiva.

## Backend

### `scripts/run_isolated_backtest.py` (novo)

Módulo importável por `web/server.py` e também executável standalone (pra smoke test manual).

- `ISOLATED_TERMINAL_PATH = r"D:\MetaTradersWSL\mfc-backtest\terminal64.exe"`.
- `build_isolated_env(base_dir)`: env construído do zero, nunca `os.environ.copy()`. Copia
  individualmente só as variáveis estruturais do SO que um processo Python no Windows precisa
  pra subir (`SystemRoot`, `PATH`, `TEMP`/`TMP`, `USERPROFILE`, `windir`, `COMSPEC`,
  `PATHEXT`), mais as variáveis de domínio setadas explicitamente:
  `CSS_MT5_TERMINAL_PATH=ISOLATED_TERMINAL_PATH` (nunca lida do processo pai — essa é a
  propriedade de segurança que a env var garante; a propriedade de fundo é ser processo
  separado, ver achado 1 da consulta), `MFC_BACKTEST_TERMINAL_ISOLATED="1"`,
  `MFC_BACKTEST_WEB_TRIGGER="1"` (veto contra `oos_disjoint`, ver achado 2),
  `PYTHONPATH=base_dir`, `PYTHONUNBUFFERED="1"`.
- **Achado da consulta**: em produção, `web/server.py` já roda como processo NATIVO do Windows
  (`scripts/systemd/web-server-wsl.sh` faz `exec` do `py.exe` do Windows via interop WSL — o
  `exec` substitui o processo, então o que fica rodando é o `py.exe`, não bash). Um
  `subprocess.Popen` disparado de DENTRO desse processo já é um spawn Windows→Windows normal
  — não precisa de WSLENV (WSLENV só importa na travessia WSL→Windows, que já aconteceu uma
  vez, no boot do próprio servidor). Detectar via `os.name`:
  - `os.name == "nt"` (produção): `python_bin = sys.executable`, sem flag de versão.
  - `os.name == "posix"` (dev local, `python web/server.py` direto): `python_bin =
    "/mnt/c/WINDOWS/py.exe"`, `["-3.12"]`, e um `WSLENV` construído no env isolado (mesmo
    padrão do `CLAUDE.md`) — **esse caminho fica sem verificação automatizada; validar com um
    smoke test manual antes de confiar nele** (ver seção Verificação).
- `spawn_isolated_backtest(description, runs, log_path, base_dir)`: o próprio processo filho
  (não o endpoint) segura `_exclusive_lock()` de `scripts/_backtest_results_log.py` durante
  toda a execução (importado, não reimplementado). `subprocess.Popen([...], cwd=base_dir,
  env=build_isolated_env(base_dir), stdout=log_fh, stderr=subprocess.STDOUT)`, argumentos
  como LISTA (nunca `shell=True` — a `description` vem de input do usuário). Comando:
  `scripts/backtest_engine_compare.py {REGRESSION_WINDOW_DAYS} {runs} "[web-trigger]
  {description}" --end-brt {REGRESSION_WINDOW_END_BRT} --sample-role exploratory`. Devolve o
  `Popen`.
- **Watchdog de janela crítica**: uma checagem periódica (rodando no processo supervisor, ou
  no próprio `web/server.py` via um `threading.Timer`/loop leve) que termina a árvore de
  processo do filho se `datetime.now(BRT)` entrar em `20:55–22:00` ou `07:55–08:20` enquanto
  ele ainda está rodando.

### Novos endpoints em `web/server.py`

- **`GET /api/backtest-history`** — lê `reports/backtest_history.json` com `json.load` puro
  (sem dependência de MT5; escrita já é atômica via `tempfile.mkstemp`+`os.replace` em
  `append_result()`, então uma leitura concorrente nunca vê arquivo pela metade — sem lock
  extra necessário). `FileNotFoundError` → `{"entries": []}`; `json.JSONDecodeError`/erro de
  permissão → erro claro (não um 500 genérico indistinguível). Query params: `limit` (clamp
  1-500, default 100, mesmo padrão de `/api/track-record/recalculate?days=60`),
  `sample_role` (filtro opcional). Ordena por `journal_seq` decrescente. Devolve só o
  resumo por entrada (colunas da tabela, ver abaixo) — não o objeto bruto inteiro.
- **`GET /api/backtest-history/{journal_seq}`** — busca linear pela entrada, 404 se ausente,
  devolve o registro completo (pro painel de detalhe). Sem auth (só leitura).
- **`POST /api/backtest-history/trigger`** — protegido por uma nova
  `_require_backtest_api_key` (mesmo padrão de `_require_portfolio_api_key`, header
  `X-Css-Api-Key`, `hmac.compare_digest`, só que lendo `CSS_BACKTEST_API_KEY`). Corpo:
  `{"description": str (obrigatório, 3-500 chars), "runs": int = 2 (clamp 1-5)}` —
  **deliberadamente sem `sample_role`/`days`/`end_brt`/`engines`**, pra que `oos_disjoint` ou
  uma janela arbitrária sejam estruturalmente impossíveis por essa rota (e agora também
  vetados do lado do filho, achado 2). Recusa (`409`) se: (a) o horário atual em BRT cair em
  `20:55–22:00` ou `07:55–08:20`; (b) o mercado FX estiver fechado (sexta ~17h ET → domingo
  ~17h ET); (c) já existir uma execução em andamento (lock não-bloqueante do filho, não
  heurística de tempo). Devolve `202 Accepted` imediatamente (`{"status": "started", ...}`) —
  não bloqueia a requisição até terminar.
- **`GET /api/backtest-history/trigger/status`** — **agora exige `CSS_BACKTEST_API_KEY`**
  (pode conter `log_tail` com proveniência sensível). `"running"` (com `log_tail`,
  truncado), `"done"` (com `new_journal_seq`, obtido do filho reportando o `journal_seq` que
  ele mesmo alocou — não por "maior antes/depois", que teria corrida com execuções CLI
  concorrentes), `"failed"` (`returncode` + `log_tail`), ou `"idle"`.

### `.gitignore`

Adicionar `data/backtest_trigger/` (estado efêmero — `status.json` + log — mesmo padrão já
usado pra outros arquivos de runtime em `data/`).

## Colunas da tabela (linha-resumo)

| Coluna | Campo fonte | Nota |
|---|---|---|
| `#` | `journal_seq` | mais recente primeiro |
| Data/Hora | `recorded_at_utc` | quando RODOU, não a janela avaliada |
| Sessão | derivado do horário do run | 🟢 mercado aberto / 🌙 fechado — contextualiza custo/líquido |
| Papel | `window.sample_role` | badge — `exploratory` neutro, `oos_disjoint` destacado/dourado (raro) |
| Motores | lista de engines comparados | códigos curtos |
| Janela | `window.start_brt → end_brt` | + `(Ddias, Nnoites)` |
| Commit | `provenance.code_commit` (8 chars) | ⚠️ se `worktree_dirty=true` |
| Descrição | `note` | 🌐 se começar com `[web-trigger]`; renderizado com `textContent` |
| **Bruto/motor** | `engines.<nome>.bruto` + `baskets` | **coluna primária de regressão — determinística** |
| Δ pareado | `paired_net_delta_per_night` | `mean ± stderr (n=N)` |
| Líquido/motor | `engines.<nome>.liquido` | marcado como dependente de tick ao vivo (ver coluna Sessão) |
| Qualidade | `quality.status` | `clean`✅ / `partial_model`⚠️ / `degraded`❌ |

Painel de detalhe (ao clicar na linha, mesmo padrão de `renderAuditDetailPanel`): breakdown
completo por motor (`baskets`, `bruto`, `custo`, `spread`, `swap`, `liquido`, `noite_pct`,
`cesta_pct`), `limitations[]`, `coverage`, `quality.by_engine`, `producer_provenance`
(conta/terminal/`orders_sent=false`), `data_snapshot_digest`, `parameters` completo,
`runs_summary` (min/max/mean quando `runs>1`).

## Frontend

Reaproveita o modal de track-record (`setupTrackRecordModal()`, `app.js:2365`) e sua CSS
(`.track-table`, `.table-responsive`, `.audit-detail-panel`) — só uma 4ª aba nova.

- `index.html`: novo botão `#tabBtnBacktests` no `.track-modal-nav` (depois de
  `tabBtnAnalytics`), novo painel `#paneBacktests` (depois de `#paneAnalytics`) com: (a)
  formulário de disparo (`<textarea>` de descrição obrigatória, max 500 chars + `<input
  type="password">` pra `X-Css-Api-Key`, persistido em `sessionStorage`, não `localStorage`) +
  botão `btnTriggerBacktest`; (b) a tabela; (c) o painel de detalhe.
- `app.js`: branch nova no handler de clique de aba pra `"backtests"` chamando
  `loadBacktestHistory()`. Funções novas: `loadBacktestHistory()`,
  `renderBacktestHistoryTable(data)`, `renderBacktestDetailPanel(entry)` (busca
  `/api/backtest-history/{seq}`), `setupBacktestTriggerForm()` (dispara o POST, entra em
  polling de 3s do status via o mesmo padrão de `startLivePolling`/`stopLivePolling`, para
  quando `status !== "running"`, reabilita o botão e recarrega a tabela). **Toda string vinda
  do journal (note, nomes de engine, etc.) é setada via `textContent`, nunca interpolada em
  `innerHTML`** — diferente do padrão existente no resto do arquivo (que só renderiza número/
  moeda, nunca texto livre humano).

## Fora de escopo (confirmado)

- Otimização/varredura de parâmetros (fase futura — `threshold_sweep()` já existe como
  semente, não expor ainda).
- Expor `oos_disjoint` pela web, de qualquer forma.
- Qualquer mudança na lógica de comparação/estatística de `compare()`/`threshold_sweep()` em
  si (a checagem de isolamento do achado 2 é aditiva, não muda o cálculo).
- Sandbox de filesystem pro processo filho (ele roda no mesmo checkout, `cwd=base_dir`) — os
  scripts de backtest já foram extensivamente auditados nesta sessão como só-leitura (nunca
  escrevem em `data/`, só em `reports/backtest_history.json` via escrita atômica); aceito como
  risco residual, não bloqueador pra esta primeira versão.

## Verificação

1. `python -m pytest tests/ -q` continua verde.
2. Testes novos: `build_isolated_env()` nunca inclui `CSS_PORTFOLIO_API_KEY`/segredos no
   dicionário construído (mesmo que o processo filho depois os carregue via `.env` — o teste é
   sobre o que o `Popen` recebe, não sobre o ambiente efetivo do filho); argv congelado de
   `spawn_isolated_backtest()`; corpo Pydantic do trigger rejeita campos extras
   (`sample_role`, `days`, etc.); `compare()` recusa `oos_disjoint` quando
   `MFC_BACKTEST_WEB_TRIGGER=1`; checagem de isolamento dispara por
   `MFC_BACKTEST_TERMINAL_ISOLATED=1` independente de `sample_role`.
3. Smoke test manual do caminho `os.name=="nt"` (produção): via SSH na instância remota, com
   o servidor web rodando de verdade, chamar `POST /api/backtest-history/trigger` com a chave
   configurada, confirmar via `GET .../trigger/status` (autenticado) que termina `"done"` e
   que `reports/backtest_history.json` ganha uma entrada nova com `sample_role="exploratory"`
   e a janela fixa certa — igual ao que já foi feito manualmente essa sessão pras janelas OOS.
   Confirmar que o lock morre corretamente ao final (`_exclusive_lock` liberado).
4. Smoke test manual do caminho `os.name=="posix"` (dev local) — sinalizado como não
   verificado pelo desenho; rodar `python scripts/run_isolated_backtest.py --description
   "smoke test"` direto de um shell WSL antes de confiar nesse fallback.
5. Confirmar visualmente na UI: aba nova abre, tabela carrega mesmo com `reports/backtest_history.json`
   tendo 28+ entradas, clique numa linha abre o painel de detalhe, disparo com chave errada
   devolve 401, disparo sem chave configurada no servidor devolve 503, disparo dentro da
   janela crítica devolve 409, disparo com mercado fechado devolve 409, `note`/descrição com
   HTML/script embutido aparece como texto literal na tabela (não executa).
6. Depois de implementado: rodar o mesmo commit duas vezes na janela fixa (sugestão operacional
   da seção "Janela de acompanhamento" acima) pra estabelecer o piso de ruído real antes de
   usar a tabela pra qualquer decisão.

## Implementação e revisão de código (herdr-review mfc-65)

Implementado (backend + frontend + testes, 467 passando num venv com fastapi/httpx
instalados, 430+2 skipped no Python do sistema deste checkout — os 2 skips são os testes de
endpoint, mesmo padrão de `tests/test_portfolio_api_auth.py`). Antes de considerar pronto, uma
rodada `herdr-review` (`mfc-rev` + `mfc-rev-2`, cegos um do outro) revisou o diff completo.
Achados CONFIRMADOS por pelo menos um revisor, todos corrigidos no código antes deste commit:

- **P1 (`mfc-rev`) — a entrada manual (`__main__`) podia cair no terminal AO VIVO.**
  `_run_and_record()` não forçava `MFC_BACKTEST_TERMINAL_ISOLATED=1`/`MFC_BACKTEST_WEB_TRIGGER=1`
  no próprio ambiente — um operador rodando `python scripts/run_isolated_backtest.py` num shell
  que esquecesse de exportar essas variáveis (ou já tivesse `CSS_MT5_TERMINAL_PATH` apontando
  pro terminal ao vivo) faria `compare()` pular a asserção de isolamento inteira,
  silenciosamente. **Corrigido**: `_run_and_record()` agora seta as duas variáveis em
  `os.environ` incondicionalmente, ANTES de importar/chamar `compare()` — regardless de quem a
  invocou, a checagem de terminal isolado sempre roda e recusa fechado se
  `CSS_MT5_TERMINAL_PATH` não apontar pra instância `mfc-backtest`.
- **P2/P2-2 (`mfc-rev` + `mfc-rev-2`, medido) — o watchdog podia matar o processo ERRADO.**
  Entre o pai chamar `Popen` e o filho conquistar o lock dedicado (~0,18s de import só neste
  checkout, mais em produção com MT5 real) existe uma janela em que um segundo disparo pode ser
  aceito; o segundo filho sobrescrevia `status.json["pid"]` com o próprio PID antes de bloquear
  esperando o primeiro terminar — o watchdog matava o filho ENFILEIRADO, deixando o que estava
  de fato rodando atravessar a janela crítica sem proteção nenhuma. **Corrigido**: o dono do
  lock grava o próprio PID (`_write_owner_pid()`) POR DENTRO da seção crítica — nunca o pai via
  Popen; `current_running_owner_pid()` combina a sonda do lock com esse PID como fonte única de
  verdade; o watchdog e o endpoint de status usam só essa fonte. O endpoint de disparo também
  passou a esperar (fora do lock em memória, sem travar o event loop) até `is_trigger_running()`
  confirmar, reduzindo — não eliminando — a frequência do caso.
- **P2-1 (`mfc-rev-2`) — status preso em "running" pra sempre depois de um kill.** Um `SIGTERM`
  nunca passa pelos caminhos normais de saída de `_run_and_record()`, então `status.json` nunca
  era reescrito — a UI ficava em polling indefinido. **Corrigido**: as escritas de status
  TERMINAIS ("done"/"failed") passaram pra dentro do `with _exclusive_lock(...)`, e o endpoint
  de status reconcilia nas duas direções contra `is_trigger_running()` antes de responder
  (`running` sem lock → `interrupted`; `done` com lock ainda ocupado → `running`).
- **P2 (`mfc-rev`) — proveniência sensível exposta sem chave.** `GET
  /api/backtest-history/{journal_seq}` devolvia `producer_provenance` completo (login, servidor,
  host, caminhos de terminal) sem autenticação — confirmado com dado real do journal por
  `mfc-rev-2` (P3-1). **Corrigido**: o endpoint redige `account`/`terminal`/`execution.host`
  antes de responder; o resto do registro (engines, qualidade, janela, digest) continua público.
- **P2 (`mfc-rev`) — tabela não rotulava visivelmente que líquido depende de tick ao vivo.**
  **Corrigido**: legenda visível acima da tabela + `title` nos cabeçalhos de `Δ pareado`/
  `líquido`.
- **P3 (`mfc-rev`) — comentários desatualizados ("3 abas").** Corrigido pra refletir as 4 abas.
- **.gitignore (`mfc-rev-2`) — regra nova inserida entre `.herdr/*` e sua negação.** Movida pro
  fim do arquivo; `git check-ignore` reconfirma `.herdr/reviewer.md` rastreado e
  `data/backtest_trigger/status.json` ignorado.

Dois achados de `mfc-rev-2` ficam **documentados, sem mudança de código** (baixa severidade,
`market_open_at_run` já é só um contexto informativo, não um gate de execução):

- **P3-2 — mergear isto invalida toda a evidência OOS elegível até uma nova rodada no host
  Windows.** `scripts/run_isolated_backtest.py` entrou em `_PROVENANCE_SOURCE_FILES`
  (correto: é esse arquivo que fixa `sample_role`/janela pro disparo web) — mas o digest de
  proveniência é global, então essa mudança sozinha já invalida as 13 entradas `oos_disjoint`
  existentes no journal (nenhuma elegível hoje). **Ação necessária depois do merge**: rodar de
  novo a janela OOS de 688 dias no host Windows (mesmo procedimento já documentado em
  `docs/plans/port-upstream-institutional-matrix.md`, seção "OOS estendido") antes de confiar em
  `select_latest_oos_evidence()` pra qualquer decisão.
- **P3-3 — `market_is_open()` não cobre feriado em dia útil** (25/12, 01/01 etc.) — nesses dias
  raros, `market_open_at_run` fica `True` mesmo com o mercado de fato fechado. Documentado no
  docstring da função; sem calendário de feriados implementado (impacto limitado a um ponto ruim
  na tabela, não a uma decisão de execução).

Ainda pendente, fora do escopo desta rodada de correção (ambos os revisores documentaram como
não-verificável neste checkout Linux sem MT5): os smoke tests manuais dos itens 3 e 4 da seção
Verificação acima, incluindo a dúvida específica que `mfc-rev-2` levantou — se
`mt5.initialize()` alguma vez precisar LANÇAR o terminal isolado (em vez de anexar a um já
rodando, que é o estado hoje documentado em `CLAUDE.md`), `_SO_ENV_WHITELIST` não inclui
`APPDATA`/`LOCALAPPDATA`/`USERNAME` — vale testar essa hipótese explicitamente com o terminal
isolado FECHADO antes do disparo.

## Segunda rodada de revisão (herdr-review mfc-66) — correções sobre a rodada anterior

Depois das correções acima, uma segunda rodada `herdr-review` (mesmo par, cegos de novo)
revisou o diff atualizado. Os dois confirmaram o P1 anterior fechado corretamente e o
mecanismo de owner-pid como "fechado na parte que importava" (`mfc-rev-2`) — mas acharam um
achado P2/P2-1 real na correção de redação de proveniência (a mesma identidade aparece em TRÊS
envelopes com nomes de campo diferentes; a correção anterior só limpava um) e três achados
menores. Todos corrigidos:

- **P2 (`mfc-rev`) + P2-1 (`mfc-rev-2`, medido contra `journal_seq=28` real) — redação
  incompleta.** `_redact_provenance()` só tocava `producer_provenance`; `entry["provenance"]`
  (presente em TODA entrada) e `entry["execution"]` (chave de TOPO) vazavam login/servidor/
  host/caminhos sem chave. **Corrigido**: redação agora percorre o registro inteiro
  recursivamente, redigindo por NOME de campo (`login`, `server`, `configured_path`,
  `mt5_path`, `path`, `observed_path`, `host`, `terminal_path`) em vez de por caminho fixo —
  não repete o erro se um quarto envelope aparecer. Teste reescrito com o shape REAL (lido de
  `reports/backtest_history.json`, não reconstruído a partir da própria função — mesma classe
  de erro já registrada no projeto como MFC62-01).
- **P3-2 (`mfc-rev-2`) — `owner.pid` nunca era apagado ao soltar o lock.** Entre uma execução
  e a seguinte, o arquivo guardava o PID do dono ANTERIOR — janela pequena (avaliada como
  praticamente inalcançável na prática), mas potencialmente perigosa no mesmo host que roda
  produção ao vivo. **Corrigido**: `_remove_owner_pid()` apaga o arquivo como última ação
  dentro da própria seção crítica do lock.
- **P3-3 (`mfc-rev-2`) — o filho enfileirado não revalidava os portões.** Um segundo disparo
  podia esperar minutos atrás do primeiro e, ao finalmente conquistar o lock, rodar mesmo que
  a janela crítica tivesse começado ou o mercado tivesse fechado nesse meio-tempo. **Corrigido**:
  `_run_and_record()` reavalia `in_critical_window()`/`market_is_open()` logo depois de
  conquistar o lock, saindo com status `"skipped"` (sem chamar `compare()`) se qualquer um
  virou.
- **P2 (`mfc-rev`) — `LOG_PATH` global colidia entre disparos concorrentes.** Dois `Popen`
  abrindo o mesmo arquivo em modo `"w"` truncavam/corrompiam o log um do outro. **Corrigido**:
  um arquivo de log por `run_id` (`_log_path_for()`); `read_log_tail()` lê o log da execução
  atual (via `run_id` do `status.json`, ou explícito).
- **P2 (`mfc-rev`) — `status.json` reconciliado só no campo `status`.** `pid` podia continuar
  sendo de uma execução já finalizada. **Corrigido**: quando o lock revela um dono novo, `pid`
  é reescrito com `current_running_owner_pid()` e a resposta ganha `stale_metadata: true`
  (honesto sobre `run_id`/`description` ainda poderem ser da execução anterior — corrigir isso
  por completo exigiria status por `run_id`, redesenho maior, não feito aqui).
- **P3-1 (`mfc-rev-2`) — front não tinha branch pra `"interrupted"`.** Caía num `else` mudo — o
  operador via a tela limpar sem saber que a execução foi interrompida. **Corrigido**: branches
  explícitos pra `"interrupted"` e `"skipped"` em `pollBacktestStatus()`.
- **P3 (`mfc-rev`) — watchdog engolia exceção em silêncio.** **Corrigido**: `print` no `except`
  do loop do watchdog.
- **P3 (`mfc-rev`) — docstrings superafirmavam a segurança do mecanismo de posse.**
  `current_running_owner_pid()`/`terminate_owner()` reescritos pra descrever a garantia
  REAL (best-effort, não atômica) em vez de "nunca aponta pro processo errado".

**Residual aceito, não corrigido nesta rodada** (ambos os revisores convergem que é aceitável
dado o resto do mecanismo): o double-spawn em si (duas requisições quase simultâneas podendo
lançar dois processos) continua estruturalmente possível — o que mudou é que agora, sejam um
ou dois processos lançados, o watchdog sempre mata o dono CERTO (`current_running_owner_pid()`)
e o enfileirado nunca roda `compare()` fora de contexto (P3-3). Corrigir o double-spawn em si
exigiria uma reserva do lock de arquivo ANTES de responder ao cliente (não só depois, como hoje)
— redesenho maior, deixado como trabalho futuro se a frequência real em produção justificar.
Duas rodadas de correção já foram feitas (limite do protocolo `herdr-review`); o estado atual
foi levado ao usuário em vez de uma terceira rodada.

Suíte depois da segunda rodada: 471 passando (venv com fastapi/httpx), 434+2 skipped no Python
do sistema deste checkout (sem fastapi).
