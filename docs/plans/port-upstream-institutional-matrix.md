# Plano — portar a matriz institucional do upstream

Status: Port A implementado; comparação antes/depois registrada; o gate OOS
estrito recusou a janela por histórico MN1 insuficiente e as execuções
exploratórias mfc-46/mfc-47 ficaram explicitamente degradadas; a revisão formal
final mfc-49 aprovou as correções sem P0/P1/P2/P3; adoção live permanece
fechada. O upstream foi verificado novamente em 2026-08-30 e aponta para
`4138998`; ele continua fora do escopo desta etapa.

Última aprovação formal da implementação do Port A: `mfc-49`, com `mfc-rev` e
`mfc-rev-2` aprovando sem P0/P1/P2/P3. A divergência da rodada `mfc-47` foi arbitrada
exclusivamente via `herdr-ask` (`mfc-12`), o gate de cardinalidade foi coberto
por regressão e o diagrama de pódio foi corrigido. O protocolo continua sem
`/tri-r`, AgentRelay, subagentes nativos ou novos agents.

As rodadas documentais `mfc-50`, `mfc-51`, `mfc-52` e `mfc-53` reabriram este
plano para corrigir rastreabilidade, rollback, inventário de consumidores,
schema e separação entre ranking de pares e baseline. Os achados e correções
dessas rodadas fazem parte desta versão; a rodada agregada seguinte é o gate
para qualquer nova execução do OOS ou mudança de código.

## Decisão que este plano resolve

O upstream confirma que o macro e o operacional devem participar da decisão do
motor, mas não por meio de uma leitura direta dos campos escalares
`macro_power` e `op_power`.

Na implementação do upstream, a decisão é formada por:

- vetores direcionais derivados da tríade de MN1, W1, D1, H4 e H1;
- `macro_bias` calculado a partir de MN1 + W1;
- maturação temporal em `544d660`;
- penalidade de contra-fluxo;
- score ponderado e contagem de timeframes alinhados.

A modulação contínua por inclinação só aparece em `4138998`, junto com pesos,
regras de maioria e clamp diferentes. Ela não faz parte do Port A normativo.

Os campos `macro_power` e `op_power` continuam sendo retornados pelos
analisadores, mas não são consumidos diretamente pelo `trade_bias`.

Os dois commits não são uma única fórmula equivalente: `4138998` é filho de
`544d660` e altera pesos, inclinação e clamp, embora ambos usem denominador
13,5. O port precisa escolher uma etapa normativa ou tratá-las como duas
etapas mensuráveis, nunca misturá-las implicitamente.

Evidência histórica local: antes do Port A, `HEAD` tinha o score bruto 3-TF.
Evidência atual: `agents/confluence_engine.py::evaluate_currency_confluence`
implementa a matriz 5-TF e usa `macro_bias`/`vectors` nas regras de decisão;
os adaptadores controlados passam `ref_dt` explicitamente.

## Objetivo

Portar de forma focada a matriz institucional do upstream para a branch local,
preservando as melhorias de segurança, histórico e validação já feitas, sem
fazer um merge amplo do upstream.

## Escopo

1. Fazer um inventário de compatibilidade entre a branch local e o upstream nos
   módulos de confluência, tríade, analisadores e seus consumidores.
2. Portar para `agents/confluence_engine.py` apenas a lógica necessária da
   matriz 5-TF de `544d660`:
   - cálculo de vetor por região/ciclo/derivada;
   - maturação de W1/MN1;
   - `macro_bias` e penalidade de contra-fluxo;
   - score normalizado, contagem de alinhamento e regras de `trade_bias`.
3. Portar mudanças mínimas de `agents/triad_analyzer.py`,
   `agents/macro_analyzer.py` e `agents/operational_analyzer.py` somente quando
   forem dependências reais da matriz, mantendo contratos usados pela branch.
   A taxonomia local congelada (`±0.20` para zona de parada e `±0.05` para
   equilíbrio) não pode ser substituída silenciosamente pelos limiares do
   upstream (`±0.16`/`±0.04`); a escolha deve ser registrada antes do código.
4. Separar explicitamente a decisão por moeda do ranking de pares. O upstream
   ainda contém lógica de alicate/ranking 3-TF em `evaluate_28_pairs_confluence`;
   essa parte será portada somente após confirmar seus consumidores e critérios,
   sem assumir que a matriz 5-TF a substitui automaticamente.
5. Atualizar testes e documentação para registrar que `macro_power`/`op_power`
   são diagnósticos e que a influência efetiva ocorre pelos vetores e
   `macro_bias`.

## Estratégia de implementação

### Fase A — baseline (concluída)

- Capturar os contratos atuais, consumidores e snapshots de comportamento.
- Comparar os testes locais com `tests/test_confluence_matrix.py` do upstream.
- Identificar mudanças de assinatura, pesos, limiares e nomenclatura antes de
  editar qualquer módulo.
- Escolher formalmente a etapa normativa: recomendação inicial é portar
  `544d660` como baseline e avaliar `4138998` como segunda etapa isolada.
  Registrar pesos, slope, clamp, limiares e tolerâncias de cada variante.
- Definir o instante de referência como dado de entrada explícito, com fuso
  canônico e normalização documentada. Live e backtest devem passar o mesmo
  `ref_dt`; não depender de `datetime.now()` implícito.
- Inventariar todos os consumidores, incluindo `web/css_service.py`,
  `scripts/backtest_canonical.py`, `web/history_tracker.py`, o frontend que
  exibe `total_score`, `daily_css_routine.py` e o executor que interpreta
  `trade_bias`.

### Snapshot pré-Port A e estado atual

- O snapshot pré-Port A era a branch local `main` no commit
  `7b22a4bce946e530795d9dbb083f99ac572731fb`: o motor usava apenas D1/H4/H1,
  os pesos eram `40%/35%/25%`, `ref_dt` não existia no contrato e os dois
  adaptadores chamavam a função sem instante explícito. Esse bloco é histórico,
  não descreve o código atual.
- No estado atual, `agents/confluence_engine.py::evaluate_currency_confluence`
  exige `ref_dt`, usa MN1/W1/D1/H4/H1 e produz o score normalizado da matriz.
  `web/css_service.py::update_data` captura um único `reference_dt` e o passa
  ao motor; `scripts/backtest_canonical.py::evaluate_at` também passa o
  `ref_dt` explícito. O ranking 3-TF restante está isolado em
  `evaluate_28_pairs_confluence` e no braço `3tf_baseline` do comparativo.
- `agents/portfolio_executor.py` continua consumindo apenas o contrato textual
  `COMPRA|VENDA|NEUTRO` por substring; ele não foi alterado para portar a
  matriz.

### Inventário de consumidores e contratos

- Chamadores diretos do motor Port A: `web/css_service.py::update_data`,
  `scripts/backtest_canonical.py::evaluate_at` e o alias usado por
  `scripts/backtest_engine_compare.py` no engine `5tf_port_a`. O engine
  `5tf_upstream` é uma cópia informativa da matriz do upstream dentro do
  harness, não chama o motor Port A.
- Consumidor indireto de produção: `daily_css_routine.py::run_daily_routine`
  chama `css_engine.update_data(force=True, mode="standard")`, portanto usa o
  mesmo caminho web e não mantém uma segunda chamada ao motor. O scheduler
  apenas dispara essa rotina; não calcula confluência por conta própria.
- Consumidor de apresentação: o payload contém `currencies[].total_score`, mas
  `web/static/app.js` não lê esse campo na tabela de moedas; usa principalmente
  scores por timeframe, LEDs e `trade_bias`, enquanto o screener lê
  `pairs[].total_score`. O relatório diário
  lê `trade_bias` da moeda e `total_score` dos pares, não serializa nem imprime
  o score da moeda.
- `web/history_tracker.py` não chama o motor Port A nem é chamado pelo
  comparativo; é um oracle de auditoria/seleção separado e fica fora deste
  gate. `agents/portfolio_executor.py` só interpreta o texto de `trade_bias`.
- `agents/triad_analyzer.py`, `agents/macro_analyzer.py`,
  `agents/operational_analyzer.py`, `daily_css_routine.py` e as duas skills
  CSS não foram modificados pelo Port A. Os blobs no `HEAD` são,
  respectivamente, `fd785eb912d8c2fa7b3f6869d8732168d00656d7`,
  `a88cd88e4f25ddea502c6f47076a754b8c21233a`,
  `f0b856a884f6f19b0ee75de02ba0da1240d5b970`,
  `136f2c123cc0113d142902a1b82576cf0bbe265e`,
  `7192e7db05413034206de52576d4b16dba26ab94` e
  `34bd20d772e6a4d18144924c30c7b1d8845edef4`. Por isso não há atualização
  correspondente nas skills; se qualquer um desses arquivos entrar numa
  etapa futura, a alteração e seus testes serão incluídos na allowlist.

### Upstream fixado de forma reprodutível — 2026-08-30 17:35:36 BRT

- `544d660` resolve para
  `544d660b423498cd41a594328ef05b0c4c6adff1`, tree
  `413faac9261ace70599a71305b5d0bca6a01daac`.
- `4138998` resolve para
  `413899804c7b920317d1db17b6ec91338ad6895a`, tree
  `916708c40c926ee42582a68d8bb8a9993a8ddec4`; é filho direto de `544d660`.
- A consulta read-only `git ls-remote upstream refs/heads/main` retornou
  `413899804c7b920317d1db17b6ec91338ad6895a refs/heads/main` nesse instante.
  O último fetch registrado localmente também resolveu `upstream/main` para
  esse SHA; a referência continua móvel e não substitui a pinagem acima.
- Comandos de reprodução da inspeção: `git ls-remote upstream
  refs/heads/main`; `git rev-parse 544d660^{commit} 544d660^{tree}
  upstream/main^{commit} upstream/main^{tree}`; `git show -s --format='%H%n%T%n%aI%n%s'
  544d660`; o mesmo comando para `upstream/main`; e
  `git diff --stat HEAD..upstream/main`.
- A diferença entre `HEAD` e `upstream/main` alcança 149 caminhos e inclui
  dados, relatórios, UI, EA, scheduler e deploy. Nenhum merge ou cherry-pick
  amplo foi feito; `4138998` fica reservado para uma etapa isolada posterior.
- O upstream modifica `agents/confluence_engine.py` e
  `agents/triad_analyzer.py`; o commit `4138998` também alcança 70 caminhos,
  incluindo dados, relatórios, UI, scripts e serviço web. Isso confirma que
  cherry-pick/merge do commit inteiro está fora do escopo.
- `agents/portfolio_executor.py:2396-2417` interpreta `trade_bias` por
  substring. A implementação deve preservar o contrato exato
  `COMPRA|VENDA|NEUTRO` e cobri-lo com teste, sem alterar o executor nesta
  etapa.
- O checkout local contém `tests/test_backtest_canonical.py` e
  `tests/test_triad_analyzer.py`; a árvore upstream diverge nesses testes.
  A comparação deve ser feita por comportamento e contratos, não por cópia
  cega da árvore de testes.

### Rastreabilidade do estado Port A revisado

- O estado local revisado é um working tree baseado em
  `7b22a4bce946e530795d9dbb083f99ac572731fb`; o Port A ainda não foi
  materializado em commit próprio. Alterações não relacionadas presentes no
  checkout, como segurança do executor, não fazem parte deste allowlist.
- Para reproduzir o fingerprint do estado Port A, executar na raiz:
  `sha256sum agents/confluence_engine.py web/css_service.py
  scripts/backtest_canonical.py scripts/backtest_engine_compare.py
  scripts/_backtest_results_log.py tests/test_confluence_matrix_port_a.py
  tests/test_css_service_port_a.py tests/test_backtest_canonical.py docs/API.md
  reports/backtest_history.json | sha256sum`; complementar com
  `git status --short --untracked-files=all`, `git diff --binary HEAD --` e
  `git diff --cached --binary` para identificar working tree e index.
- O fingerprint calculado para este estado é
  `658987baa76222890f57671f577ac02970164e9ecc595230e5c218808570bbd1`. Ele não inclui este plano nem aliases gerados do
  journal, evitando circularidade; os arquivos incluídos e o status Git são
  a parte auditável da revisão documental.
- Este fingerprint mudou após a regressão nomeada do payload público (item 1
  da retomada — ver Fase C): `tests/test_css_service_port_a.py` ganhou
  `test_api_css_all_public_schema_separates_currency_and_pair_scores` e
  `test_daily_report_format_separates_currency_trade_bias_from_pair_total_score`.
  Nenhum arquivo de runtime (`agents/confluence_engine.py`,
  `web/css_service.py`, `scripts/backtest_canonical.py`) foi tocado — apenas o
  arquivo de teste do allowlist, com o mesmo comando de reprodução acima. O
  fingerprint intermediário foi
  `efa5be61cfa82bb1d55459f0e48d6c1ef216a922d5e8afc390d1d05e202e081f`.
- Rodada de revisão `mfc-56` (blind, `mfc-rev` + `mfc-rev-2`) sobre este item:
  `mfc-rev-2` aprovou com dois P3 não bloqueantes; `mfc-rev` confirmou o mesmo
  P3 do teste do relatório diário (achado duplo — checagem por substring
  literal não era semanticamente discriminante) e rejeitou a rodada por três
  achados P1/P2 em arquivos fora do escopo declarado do item 1
  (`scripts/validate_margin_observed.py`, `scripts/validate_margin_calc.py`,
  `supersedes` em `scripts/_backtest_results_log.py`) — arquivos já presentes
  no working tree sujo antes desta etapa, não tocados por ela; ver handoff
  `.herdr/handoff/mfc-20260830-192441-3778992.md`. O P3 confirmado foi
  corrigido: `_dict_key_accesses` em `tests/test_css_service_port_a.py` agora
  usa AST para checar os acessos `c.get(...)`/`c[...]` e `p.get(...)`/`p[...]`
  do relatório diário, independente de estilo de aspas ou indexação por
  colchete. `docs/DECISION_MATRIX_CONFLUENCE_SYSTEM.md:193-199` também foi
  corrigido (achado único `mfc-rev-2`): não descreve mais `currencies[]` como
  expondo "diagnósticos internos da matriz 5-TF". O fingerprint após os dois
  fixes de payload foi
  `573b7b96ac43764edabbc814d8254d43a0bfc0e14a412c0861c716f431c9cd7a`.
- Breno decidiu (2026-08-30) tratar os três achados P1/P2 de `mfc-rev` nesta
  mesma sessão, antes do item 2 da retomada. Correções aplicadas, fora do
  escopo original do Port A mas dentro do mesmo working tree:
  - **MFC56-01** (`scripts/validate_margin_observed.py`): a checagem de
    `CSS_MT5_TERMINAL_PATH` era substring (`REQUIRED_PATH_MARKER in
    configured_path`), então `mfc-backtest-prod` passava sem ser a instância
    dedicada. Substituída por `_terminal_path_is_isolated` (checagem
    estrutural via `ntpath.basename`, mesmo padrão de
    `scripts/backtest_engine_compare.py::_assert_oos_terminal_configuration`),
    e adicionada `_terminal_identity_matches` — confere `mt5.terminal_info().path`
    DEPOIS de `mt5.initialize()`, porque `initialize(path=X)` pode anexar a um
    terminal já em execução em vez de abrir X. Duas novas checagens no fluxo
    de `main()`, uma pré-conexão e uma pós-conexão.
  - **MFC56-02** (`scripts/validate_margin_calc.py`): não tinha NENHUM guard de
    terminal ou identidade de conta antes desta correção — `order_calc_margin()`
    podia rodar contra qualquer terminal MT5 já aberto na máquina. Recebeu os
    mesmos dois guards de `validate_margin_observed.py` mais a chamada a
    `check_account_identity()` (ausente antes).
  - **MFC56-03** (`scripts/_backtest_results_log.py`): `supersedes` era
    metadado parcialmente confiado do chamador — `append_result()` só
    sobrescrevia o campo quando encontrava registro antigo da MESMA janela; sem
    isso, um valor forjado apontando pra um identifier de OUTRA janela
    sobrevivia intacto, sem cobertura do digest. Duas camadas de correção: (1)
    `append_result()` agora sempre descarta `entry["supersedes"]` do chamador
    antes de derivar (mesmo tratamento já dado a `provenance`); (2)
    `select_latest_oos_evidence()` só aceita uma supersessão quando o
    identifier alvo pertence à MESMA janela do entry que a declara — defesa em
    profundidade contra um entry legado/editado à mão no journal.
  - Oito testes novos em `tests/test_validate_margin_observed.py` (que já
    cobre os três módulos): dois de guard de terminal em cada validador
    (look-alike de diretório + identidade pós-conexão divergente), um de
    `check_account_identity()` ausente no cálculo, um de checagem estrutural
    pura, e dois de `supersedes` (persistência nunca aceita valor do chamador;
    seletor ignora supersessão cross-window). Cinco testes existentes que
    chamavam `main()` precisaram de `fake.terminal_info.return_value` — sem
    isso o `MagicMock()` default não bate com o path esperado e o guard novo
    recusaria toda execução de teste por um motivo não intencional.
  - Suíte completa: `332 passed, 1 skipped, 32 subtests` (era 324 antes desta
    rodada). `scripts/_backtest_results_log.py` está no allowlist do Port A;
    o fingerprint após estas três correções é
    `793e2f84f518869a68cb59368d398a87c74d2ef2d7d294a5e1541b6a0e026350`.
    `scripts/validate_margin_observed.py` e `scripts/validate_margin_calc.py`
    não estão no allowlist do Port A (não fazem parte da matriz institucional);
    a correção fica registrada aqui por rastreabilidade da sessão, não como
    mudança de escopo do Port A.
- Rodada de revisão `mfc-57` (blind, `mfc-rev` + `mfc-rev-2`) sobre as três
  correções acima: `mfc-rev-2` aprovou sem achado novo (confirmou os três
  fechados). `mfc-rev` achou **MFC57-01** (P2, único — só um dos dois
  revisores achou, avaliado e confirmado procedente por este exec, verificado
  reproduzindo o bug contra a lógica anterior antes de aceitar) e **MFC57-02**
  (P3, único, também confirmado procedente): a segunda camada de
  `select_latest_oos_evidence()` da rodada anterior não validava ordem
  temporal (um `entry` com `recorded_at_utc` mais antigo podia declarar
  supersessão de um registro mais recente e vencer) nem escopava
  `_record_identity()` por janela (duas janelas diferentes podem coincidir
  nesse valor, já que é só o timestamp normalizado) — e o teste
  `test_oos_selector_ignores_cross_window_supersedes_claim` da rodada anterior
  não exercitava de fato o código vulnerável, porque filtrava por
  `start_brt`/`end_brt` antes da lógica de `supersedes` rodar, então a entrada
  de outra janela nunca virava candidata.
  - Corrigido em `scripts/_backtest_results_log.py`: `append_result()` só
    deriva `supersedes` para registros ESTRITAMENTE anteriores ao novo
    (`_record_datetime(old) < entry_dt`); `select_latest_oos_evidence()` agora
    escopa `by_identity` por `(janela, identity)` em vez de só `identity`, e só
    aceita supersessão quando o alvo é da mesma janela E estritamente
    anterior.
  - O teste `test_oos_selector_uses_supersession_when_newer_record_supersedes_older`
    tinha os timestamps de `older`/`newer` invertidos (passava "por acidente"
    com o código antigo, que não validava ordem) — corrigido. Reescrito
    `test_oos_selector_ignores_cross_window_supersedes_claim` pra chamar
    `select_latest_oos_evidence` sem filtro de janela (onde o código
    vulnerável de fato roda) e construir o cenário de forma que a supersessão
    forjada, se aceita, mudaria qual entrada vence. Dois testes novos:
    `test_oos_selector_rejects_backdated_entry_superseding_a_later_record` e
    `test_oos_selector_does_not_confuse_identity_shared_by_different_windows`.
    Os quatro testes de regressão novos/reescritos foram verificados
    reproduzindo o bug contra uma cópia da lógica anterior antes de aceitar a
    correção como fechada (não apenas "os testes passam com o código novo").
  - Suíte completa: `334 passed, 1 skipped, 32 subtests`. `git diff --check`
    limpo. Fingerprint do allowlist Port A após esta correção:
    `fca1bb82cd1b07797144d52e560d8d9a77282df2c2ac1dd3275975882e0207c8`.
  - Esta foi a rodada 2 de no máximo 2 desta sessão (skill `herdr-review`).
    Nenhuma terceira rodada foi disparada; o estado final foi levado a Breno
    para decisão, não decidido unilateralmente por este exec.
- **Correção de registro:** a frase acima ("os quatro testes de regressão
  novos/reescritos foram verificados reproduzindo o bug... não apenas 'os
  testes passam'") está factualmente incorreta para 3 dos 4 testes — provado
  por `mfc-rev-2` na rodada `mfc-58` (abaixo), rodando cada teste contra
  cópias isoladas da lógica anterior. Só
  `test_oos_selector_rejects_backdated_entry_superseding_a_later_record`
  discrimina de fato; os outros três passam mesmo com a proteção removida.
  Mantida aqui, riscada, por rastreabilidade — não apagar o registro errado,
  documentar a correção.
- Breno pediu explicitamente uma rodada 3 (`mfc-58`, acima do teto normal de 2
  da skill `herdr-review`) depois de ver o estado da rodada 2, e depois pediu
  pra enviar o resultado pro `mfc-scout` de qualquer forma, sem nova rodada de
  correção antes. Vereditos: **os dois REJEITARAM.**
  - `mfc-rev`: **MFC58-01** (P2) — dois registros da mesma janela com
    `recorded_at_utc` idêntico ficam ambos ativos, sem sinalização de
    ambiguidade; a seleção final depende da ordem física do JSON, não de uma
    regra explícita. **MFC58-02** (P3) — o teste
    `test_oos_selector_does_not_confuse_identity_shared_by_different_windows`
    não discrimina de fato a proteção por `(janela, identity)` (mesmo padrão
    do MFC57-02 anterior, repetido).
  - `mfc-rev-2` (revisão com evidência empírica — rodou os módulos reais
    contra cópias isoladas da lógica anterior, fora do repositório):
    - **P2-1** — **regressão real introduzida na rodada `mfc-57`**: um
      registro com `recorded_at_utc` no futuro (relógio adiantado, fuso
      errado, ou valor fornecido pelo chamador) fica **permanentemente
      inamovível** como evidência selecionada — a checagem de ordem temporal
      em `append_result()` agora impede qualquer execução futura de
      supersedê-lo. Provado rodando `append_result()` real contra a lógica
      anterior lado a lado.
    - **P2-2** — 3 dos 4 testes desta linha do tempo (`mfc-57`/`mfc-58`) não
      discriminam código antigo de código novo (ver correção de registro
      acima); acha isso uma violação do princípio "correção sem teste que
      falhe antes e passe depois é achado de cobertura".
    - **P3-1** — como a supersessão só pode remover um registro estritamente
      anterior ao declarante, e a seleção final é `max()` por timestamp, o
      mecanismo de `supersedes` no seletor ficou matematicamente **inerte**
      (equivalente a `max()` puro — provado substituindo a função inteira e
      rodando a suíte sem diferença nenhuma); `oos_evidence_status()` nunca
      mais retorna `all_superseded`.
    - **P3-2** — resposta à pergunta feita nesta rodada sobre empate exato de
      `recorded_at_utc` na mesma janela: o **primeiro** registro appendado
      vence, não o mais recente — contradiz a semântica append-only assumida
      no resto do módulo. Baixa probabilidade prática hoje (nenhum chamador
      passa `recorded_at_utc` explícito), mas não documentada como decisão.
    - **P3-3** — achado sobre este próprio plano, não sobre código: editar
      `scripts/_backtest_results_log.py` muda `_source_digest()`, o que expira
      toda a evidência OOS do journal (comportamento desenhado). Hoje
      `select_latest_oos_evidence()` retorna `None` (8 registros, 0
      elegíveis) — este plano ainda descreve a entrada `mfc-40` como vigente
      e selecionável nas seções "Reexecução de reprodutibilidade" e
      "Verificação do gate de qualidade" acima; isso está desatualizado
      enquanto o digest não bater de novo (nova execução no terminal
      isolado), e não deve ser citado como evidência atual sem reconferir.
  - Nenhum desses achados foi corrigido ainda. O resultado (rejeitado, com
    uma regressão real não corrigida) foi enviado ao `mfc-scout` via
    `herdr agent prompt`, a pedido de Breno, para uma leitura independente —
    não é uma rodada formal da dupla fixa `mfc-rev`/`mfc-rev-2`, é um envio
    ad hoc pedido explicitamente.
  - **Leitura do `mfc-scout`** (codex, read-only, sem executar pytest —
    launcher de processo indisponível no sandbox dele; verificação por
    inspeção estática/lógica e recálculo manual do digest de proveniência):
    confirma todos os achados de `mfc-rev`/`mfc-rev-2` desta rodada,
    inclusive P3-3 (recalculou o digest atual — `ae608dd057afe475…` — contra
    os 8 registros OOS do journal e confirmou zero correspondência com
    `0aa901e8…`, o digest citado pelo plano para `mfc-40`). Funde o achado de
    empate exato de `mfc-rev` (MFC58-01) com o P3-2 de `mfc-rev-2` como o
    mesmo problema.
    - **Recomendação de design** (não implementada): não tentar mais uma
      correção pontual da ordenação por `recorded_at_utc` — esse timestamp
      não deveria ser autoridade de precedência, porque é aceito do chamador
      via `setdefault` e nunca coberto pelo digest. Proposta: (1)
      `append_result()` sempre gera `recorded_at_utc` internamente,
      ignorando qualquer valor do chamador; (2) a seleção passa a usar ordem
      de append (um `journal_seq` monotônico gerado sob o lock existente),
      não mais o timestamp; (3) `recorded_at_utc` vira só informativo; (4)
      `supersedes` sai da decisão e `all_superseded` é eliminado (já é
      inalcançável, per P3-1); (5) uma eventual revogação sem substituição
      no futuro seria modelada como tombstone por `record_id` explícito, não
      por timestamp. Antes de qualquer nova rodada de revisão, `mfc-scout`
      pede que os testes provem cada regressão falhando contra a
      implementação anterior primeiro (mesmo padrão que `mfc-rev-2` cobrou).
      Qualquer mudança nesse mecanismo muda `_source_digest()` de novo — a
      evidência OOS (já expirada, ver P3-3) precisaria de nova execução no
      terminal isolado de qualquer forma.
    - Levado a Breno para decisão antes de qualquer nova implementação.
      Decisão: implementar o redesenho do `mfc-scout` agora, nesta mesma
      sessão.
- **Item 2 da retomada resolvido nesta sessão (2026-08-31): primeira
  evidência OOS elegível desde o início do gate de qualidade histórica.**
  A causa raiz do bloqueio de dados (MN1 travado em 59 barras comuns pra 25
  dos 28 pares, precisando de 169) foi diagnosticada como teto do lado do
  servidor Exness-MT5Trial11 pras variantes de símbolo `m` — confirmado por
  duas evidências independentes: (a) dois terminais MT5 separados
  (`mfc-backtest` isolado e `mfc` ao vivo), mesma conta `198819543`, sem
  cache compartilhado, mostraram exatamente os mesmos números; (b) os
  arquivos de cache local por ano (`bases/.../history/<símbolo>/<ano>.hcc`)
  batem exatamente com os anos de barras disponíveis por símbolo. Não é
  configuração de cliente nem questão de esperar mais.
  - **Solução: aquecimento do ATR(100) da MN1 com dado gratuito da
    HistData.com** (via `pip install histdata`, mirror do repositório
    público `philipperemy/FX-1-Minute-Data`), só no PREFIXO antigo que a
    Exness não tem — nunca nas barras recentes/decisórias, que continuam
    100% Exness ao vivo. Escopo corrigido após intervenção do Breno (a
    primeira tentativa buscou 2010-2022 pros 28 pares uniformemente; a
    versão final busca só o déficit real de cada par — 27 dos 28, excluindo
    GBPJPY que já tem 400+ barras — e a maioria em 2012-2021, não
    2010-2022).
  - **Validação cruzada:** comparação HistData vs. Exness no período de
    sobreposição (set-dez/2021, os únicos meses em que os dois conjuntos
    coincidem) — 61 comparações, diferença média de 0,17%, máxima de 0,81%
    (AUDJPY, nov/2021), sem viés sistemático. Um par (`AUDJPY`) teve um gap
    de 2 meses no primeiro fetch (falha silenciosa, 109 de 120 meses
    esperados); completado com mais 2 anos (2010-2011) até fechar sem
    `short_history_pairs`.
  - **Implementação:** `scripts/fetch_histdata_mn1_warmup.py` (ferramenta
    manual, gera o cache), `data/histdata_mn1_warmup/*.json` (27 arquivos,
    ~570KB, versionados — decisão do Breno: dado de terceiro auditável,
    mesmo espírito de `reports/backtest_history.json`),
    `scripts/backtest_canonical.py::load_mn1_series_with_warmup` (mesma
    matemática de `web/css_service.py::calculate_full_css`, reimplementada
    em vez de modificar aquela função — que é compartilhada com o caminho
    web AO VIVO; dado de terceiro nunca deve entrar na dashboard/sinal
    real). `load_series()` ganhou o parâmetro opcional
    `use_histdata_mn1_warmup` (default `False` — o caminho normal continua
    100% Exness); `scripts/backtest_engine_compare.py::compare()` e o CLI
    (`--use-histdata-mn1-warmup`) propagam a mesma flag. `oos_evidence`
    registra `histdata_warmup_months_used` por par em `css_history` na
    proveniência, pra auditoria. 12 testes novos em
    `tests/test_backtest_canonical.py`, incluindo controles negativos (sem
    cache, comportamento idêntico ao anterior) e um caso de borda (mês de
    aquecimento coincidindo com o início real da Exness precisa ser
    descartado).
  - **Verificação contra MT5 real** (isolada, `mfc-backtest`,
    `198819543`/Exness-MT5Trial11, sem envio de ordens): MN1 passou de
    `degraded` (59/169) para **`clean`**, `short_history_pairs=[]`. As
    outras 4 timeframes (W1, D1, H4, H1) já eram `clean`.
  - **Execução OOS pré-registrada (`mfc-61`):** mesma janela e cutoff já
    fixados em `mfc-40`/`mfc-45`/`mfc-46`/`mfc-47`
    (`[2026-06-01 21:00, 2026-07-16 21:00)` BRT, `development_start_brt`
    igual ao fim da janela) — reexecutada agora que o bloqueio de dados foi
    resolvido, não uma janela nova escolhida depois de ver resultado. 33
    noites avaliadas, qualidade `clean` (0 degradado/swap não
    modelado/preço ausente) nos 4 motores:
    - `3tf_baseline`: 187 cestas, bruto `$75,61`, custo `$343,19`, líquido
      `-$267,58`;
    - `5tf_port_a`: 228 cestas, bruto `$178,81`, custo `$426,50`, líquido
      `-$247,69`;
    - `5tf_upstream`: 234 cestas, bruto `$178,20`, custo `$437,47`, líquido
      `-$259,27`;
    - `3tf_vector`: 53 cestas (26 noites), bruto `$131,34`, custo
      `$100,32`, líquido `+$31,02`;
    - delta líquido pareado Port A vs. baseline: `+0,603 ± 4,582` por
      noite, n=33 — erro padrão maior que a média, **não é uma melhora
      estatisticamente distinguível de zero**. O bruto bate exatamente com
      a reexecução anterior (mfc-40: mesma janela, mesmos preços); o custo
      difere bastante (`$343,19` agora vs. `$729,33` em mfc-40) porque
      `CostModel` amostra o tick ATUAL do MT5 em cada execução, não um
      custo histórico — variação já documentada e esperada, não bug.
  - **`select_latest_oos_evidence(...)` confirma `status=eligible` pela
    primeira vez** para esta janela — `producer_provenance.status=complete`,
    `orders_sent=false`, `journal_seq` corretamente sequenciado no journal.
  - **Achado operacional durante a execução:** `reports/backtest_history.json`
    nunca foi commitado (não está no `.gitignore`, mas também nunca entrou
    num commit) — o checkout local (23 registros, mfc-30 a mfc-47) e o do
    host remoto isolado (só 3 registros de sessões antigas, sem relação com
    a numeração `mfc-XX` deste plano) tinham DIVERGIDO silenciosamente. A
    entrada `mfc-61` foi extraída do arquivo do remoto (com os campos que
    `append_result()` recalcula removidos) e reanexada no journal canônico
    local via `append_result()` de verdade, preservando a sequência
    correta; o journal atualizado (24 registros) foi sincronizado de volta
    pro remoto pra não divergir de novo. Registrado aqui como risco
    conhecido do processo, não resolvido estruturalmente (o arquivo segue
    fora do git nos dois lados).
  - Suíte completa: `351 passed, 1 skipped, 32 subtests`. `git diff --check`
    limpo. Fingerprint do allowlist Port A após esta etapa:
    `c37d3d627b4bdfe20d264b0b54354d17492ad20966287050563a258e0f667113`.
  - **Não decide adoção:** o resultado é a primeira evidência OOS
    genuinamente elegível, mas o delta pareado não é estatisticamente
    distinguível de zero — consistente com o padrão de todas as execuções
    anteriores (mfc-30, mfc-40, mfc-45 exploratório). Esta etapa fecha o
    item 2 da retomada (recuperar dado suficiente); não fecha a decisão de
    adoção do Port A, que segue condicionada à validação real de
    `order_calc_margin()` e a mais janelas/decisão explícita posterior.
- Rodada de revisão `mfc-61` (blind, `mfc-rev` + `mfc-rev-2`) sobre o
  adaptador de aquecimento e a execução OOS: **os dois REJEITARAM**, com um
  achado P2 convergente e três P3.
  - **P2-1 (os dois, confirmado por medição direta):** o gap do `AUDJPY`
    que o registro anterior deste plano dizia "completado com mais 2 anos"
    **continuava lá** — os 2 anos extras (2010-2011) foram acrescentados na
    PONTA, não fecharam o buraco real no meio (jan-set e nov-dez/2012, 11
    meses). `mfc-rev-2` rebaixou o zip de 2012 pra confirmar: a HistData.com
    genuinamente só tem outubro desse ano pra esse par — não é falha de
    fetch, é uma lacuna real da fonte gratuita. Não existia checagem de
    contiguidade em lugar nenhum do pipeline; um cache com buraco no meio
    produzia `status=clean` do mesmo jeito que um cache íntegro.
    `mfc-rev-2` mediu o impacto diretamente: um buraco de 3 meses em 2015
    muda o score das barras decisórias de 2026 em `0,000000` (as 12 mais
    recentes) a `0,000599` (as mais antigas) — o gap real do AUDJPY, em
    2012, ainda mais distante, é indistinguível de zero pra esta janela. A
    evidência `mfc-61` **não precisou ser refeita**; o mecanismo de
    detecção que faltava, sim.
  - **P3-1 (`mfc-rev-2`):** a docstring dizia "as barras recentes
    continuam 100% Exness", que podia ser lida como "o resultado não
    depende de dado de terceiro" — o oposto do que acontece. Os OHLC das
    barras decisórias são de fato 100% Exness, mas os SCORES dependem do
    prefixo via lookback do ATR(100)/LWMA(21) — é o propósito do
    adaptador, e é exatamente por isso que a validação cruzada existe.
  - **MFC61-02 (`mfc-rev`, P3):** `load_mn1_series_with_warmup()` não tinha
    a mesma guarda de `calculate_full_css()` pra `MT5_AVAILABLE=False`/
    `mt5=None` — uma chamada direta à API nova (fora de `compare()`, que já
    garante `ensure_mt5()` antes) levantava `AttributeError` em vez de
    devolver indisponibilidade controlada.
  - **MFC61-03/MFC61-04 (`mfc-rev`, P3, não corrigidos nesta rodada —
    registrados como limitação conhecida):** não há garantia estrutural de
    que a barra-alvo de uma janela OOS escolhida perto da borda de
    cobertura Exness não consuma uma barra do prefixo externo (não se
    materializa pra `mfc-61`, cujas datas de decisão são todas de 2026); e
    a proveniência não registra um manifesto/hash agregado do cache
    HistData consumido, só a contagem de meses por par. Ficam para uma
    etapa futura se uma janela OOS mais próxima da borda de cobertura for
    proposta.
  - **Nota de processo (`mfc-rev-2`):** o pedido desta rodada não declarou
    que `scripts/_backtest_results_log.py` e
    `tests/test_validate_margin_observed.py` também mudaram no diff (eram
    as correções da rodada `mfc-60`, já fechadas antes desta rodada) —
    revisado mesmo assim, e confirmado que os quatro achados de `mfc-60`
    seguem fechados.
  - Corrigido: `scripts/fetch_histdata_mn1_warmup.py::find_gaps()` (função
    pura, compartilhada — importada por `backtest_canonical.py` sem
    precisar do pacote `histdata` instalado, que agora só é importado
    dentro de `fetch_pair()`) detecta meses ausentes dentro do intervalo do
    cache; o fetcher agora IMPRIME um aviso alto quando grava um cache com
    buraco (não bloqueia — a ausência pode ser real do lado da fonte, não
    um erro corrigível por retry). `load_mn1_series_with_warmup()` expõe
    `quality["histdata_warmup_gaps"]` (por par, lista de meses ausentes)
    pra auditoria, mesmo quando o gap não é grave o bastante pra derrubar o
    status. Guarda de `MT5_AVAILABLE`/`mt5 is None` adicionada, mesmo
    padrão de `calculate_full_css()`. Docstring corrigida pra não afirmar
    que o resultado independe do dado de terceiro.
  - 9 testes novos em `tests/test_backtest_canonical.py`: `find_gaps()`
    isolada (incluindo uma regressão que reproduz o gap real do AUDJPY a
    partir do cache versionado), detecção de gap sem derrubar o status,
    guarda de `MT5_AVAILABLE=False` sem levantar exceção.
  - Suíte completa: `358 passed, 1 skipped, 32 subtests` (era 351 antes
    desta rodada). `git diff --check` limpo. Fingerprint do allowlist Port
    A após estas correções:
    `a1cdb7ad801d0913c017a6a1e1c73181a99d23c31289edafc139347cfc629d71`.
  - Código sincronizado de volta pro host remoto isolado; a evidência
    `mfc-61` já registrada não foi refeita (impacto do gap medido como
    indistinguível de zero pra essa janela específica, confirmado por
    `mfc-rev-2`). Nenhuma rodada de revisão adicional foi disparada depois
    desta correção.
- **Gap do AUDJPY fechado de verdade (2026-08-31, depois da rodada
  `mfc-61`).** O Breno pediu pra checar mais duas fontes antes de aceitar o
  gap como limitação permanente: (1) um mirror alternativo do dataset no
  Google Drive — confirmado ser o MESMO arquivo da HistData.com, byte a
  byte idêntico (327KB, só outubro/2012), não uma fonte diferente; (2)
  `forexsb.com` (dado próprio, mas originado do Dukascopy) apontou pro
  caminho certo — o Dukascopy tem timeframe MN1 nativo, então não era
  preciso baixar M1 e agregar. Via `npx dukascopy-node -i audjpy -from
  2012-01-01 -to 2013-01-01 -t mn1 -f csv`, os 12 meses de 2012 vieram
  completos, direto, sem agregação. Cruzado contra o mês que já tinha
  (outubro/2012): close `82,354` (HistData, mês parcial a partir do dia 7)
  vs. `82,828` (Dukascopy, mês completo) — 0,57% de diferença, mesma ordem
  de grandeza da validação cruzada original contra a Exness. Os 11 meses
  novos foram mesclados no cache (`data/histdata_mn1_warmup/audjpy.json`,
  133 → 144 meses), com prioridade pro Dukascopy nos 12 meses de 2012 por
  ser mais completo. `find_gaps()` confirma **zero buracos** agora
  (2010-01..2021-12 contíguo); verificado contra o MT5 real, isolado, sem
  ordens: `histdata_warmup_gaps: {}` pros 27 pares. Suíte completa segue
  `358 passed`. Sincronizado de volta pro remoto. Não decidido ainda se
  vale a pena revisitar os outros 26 pares com Dukascopy nativo (deficit
  zero medido neles até aqui, então não é urgente) — registrado aqui pra
  quem retomar depois.
- **Redesenho implementado (2026-08-31)** em `scripts/_backtest_results_log.py`,
  seguindo a proposta do `mfc-scout`:
  - `journal_seq` — inteiro monotônico, 1-based, atribuído por
    `append_result()` sob o `_exclusive_lock()` existente — é agora a única
    autoridade de ordem/seleção. Nunca aceito do chamador. Registros
    anteriores a esta mudança (sem o campo) recebem backfill pela própria
    posição no array append-only na primeira escrita subsequente — o journal
    é append-only, então a posição já é a ordem histórica real; depois do
    backfill, `journal_seq` é sempre `1..len(log)` e a nova entrada recebe
    `len(log) + 1`.
  - `recorded_at_utc`/`timestamp_utc` agora são **sempre** gerados
    internamente por `append_result()` (`datetime.now(timezone.utc)`),
    igual a `provenance` — nunca aceitos do chamador, nem com `setdefault`.
    Fecha a raiz de P2-1: não há mais como um `record` chegar com timestamp
    forjado no futuro, porque o valor do chamador é descartado
    incondicionalmente. Os campos continuam existindo só como identidade
    temporal auditável/informativa (`oos_evidence_status()` ainda expõe
    `selected_recorded_at_utc`); não decidem mais nada.
  - `supersedes` não é mais derivado em `append_result()` nem lido em
    `select_latest_oos_evidence()` — o mecanismo inteiro (achado
    matematicamente inerte por P3-1) foi removido, não só corrigido.
    Registros históricos que já têm o campo mantêm-no como metadado legado
    inofensivo; `entry.pop("supersedes", None)` continua recusando qualquer
    valor fornecido pelo chamador, por defesa em profundidade.
  - `oos_evidence_eligible()` agora exige `journal_seq` válido (inteiro
    positivo, não-bool) além da identidade temporal interpretável — um
    registro legado/editado à mão sem `journal_seq` fica inelegível,
    fail-closed.
  - `oos_evidence_status()` perdeu o status `all_superseded` (P3-1: já era
    inalcançável por construção — se `eligible` é não-vazio,
    `select_latest_oos_evidence()` sempre retorna um candidato, porque não
    há mais nada que possa remover o argmax do conjunto).
  - `_record_identity()` foi removida (função inteiramente sem chamador após
    o redesenho).
  - `journal_seq` foi adicionado a `_RESULT_DIGEST_EXCLUDED_FIELDS` — é
    posição no journal compartilhado, não dado de conteúdo do backtest, e
    não pode fazer parte do digest reproduzível entre produtor e importador.
  - Testes: `tests/test_validate_margin_observed.py` teve o cluster inteiro
    de supersessão/seleção reescrito (11 testes afetados pela mudança de
    design; 2 testes obsoletos removidos — o conceito que testavam deixou de
    existir; 2 novos testes de `journal_seq` monotônico/backfill; 2 novos
    testes provando diretamente que `recorded_at_utc` manipulado não afeta
    mais a seleção nem sobrevive ao append; 1 novo teste de elegibilidade
    fail-closed sem `journal_seq`). Os dois testes de segurança mais
    diretamente ligados a P2-1 e P3-2 foram verificados reproduzindo o bug
    contra uma cópia isolada da lógica de seleção/append anterior antes de
    aceitar como fechados (mesmo padrão que `mfc-rev-2` cobrou nas rodadas
    anteriores — não repetir o erro de mfc-57/mfc-58 de declarar isso sem
    checar).
  - Suíte completa: `334 passed, 1 skipped, 32 subtests`. `git diff --check`
    limpo. Fingerprint do allowlist Port A após o redesenho:
    `c01a51aef3f576ad2862ed160f46389fc8e0456f6652426aa3fa2cdef5ec6782`.
  - Esta mudança altera `_source_digest()` de novo — a evidência OOS do
    journal (já expirada desde mfc-57/58, ver P3-3 acima) segue expirada;
    nenhuma nova execução foi disparada nesta etapa.
- Rodada de revisão `mfc-59` (blind, `mfc-rev` + `mfc-rev-2`) sobre o
  redesenho: **os dois REJEITARAM**, mas convergindo em que o essencial do
  redesenho está correto — a rejeição é sobre dois achados específicos, não
  sobre a direção da mudança.
  - `mfc-rev`: **MFC59-01** (P2) — `journal_seq` preexistente não era
    validado como único/positivo/coerente antes de calcular `next_seq`;
    `MFC59-02` (P2) — `oos_evidence_eligible()` exigia `journal_seq`, mas o
    backfill só existia no caminho de escrita (`append_result()`), então um
    consumidor puramente read-only via todo o journal em disco como
    inelegível até a próxima escrita.
  - `mfc-rev-2` (medido com probes reais fora do repo, incluindo 12
    processos e 16 threads sob carga): confirma que P2-1 (timestamp
    forjado), P2-2 (cobertura de teste) e P3-1/P3-2 da rodada anterior estão
    genuinamente fechados. Acha, de forma convergente com `mfc-rev` mas
    medida concretamente: **P2-1 desta rodada** — `next_seq = len(log) + 1`
    reintroduz a MESMA classe de bug que o redesenho existe pra eliminar, só
    que no campo novo — provado com dois cenários (T1: truncação normal do
    journal deixando `journal_seq` altos remanescentes; T2: edição manual
    com valor forjado alto) em que a execução nova nunca vira a evidência
    vigente, e no caso T1 o `journal_seq` chega a duplicar. **P3-1** — o
    degrau `timestamp_utc` de `_record_datetime()` ficou sem cobertura
    depois que o teste antigo que o exercitava foi removido. **P3-2** — o
    mesmo achado de `mfc-rev` (MFC59-02), medido: os 23 registros de
    `reports/backtest_history.json` não têm `journal_seq` nenhum ainda, e o
    seletor os trata como inelegíveis até o próximo append. Também aponta
    uma nota factual: este plano descrevia "2 testes obsoletos removidos" na
    rodada `mfc-58`→`mfc-59`, mas o delta real foi 9 nomes removidos e 9
    adicionados (a maioria renomeações/substituições do mesmo conceito, não
    perda de cobertura) — corrigido aqui.
  - Corrigido em `scripts/_backtest_results_log.py`: `next_seq` agora deriva
    do MAIOR `journal_seq` efetivo já presente no journal
    (`max(...) + 1`, via `_effective_journal_seq`), não do comprimento do
    array — fecha T1 e T2 com a mesma mudança. `append_result()` falha
    fechado (`ValueError`, nada é persistido) se o journal já tiver
    `journal_seq` declarado duplicado antes do append — o campo fica fora do
    digest, então essa adulteração não seria detectável de outra forma.
    `oos_evidence_eligible()` não exige mais `journal_seq` — a autoridade de
    ordem é uma propriedade do ARRAY inteiro (posição relativa), não de uma
    entrada isolada; `_effective_journal_seq()` (declarado, com fallback pela
    posição 1-based) agora é usada tanto por `select_latest_oos_evidence()`
    (na leitura, sem persistir nada) quanto por `append_result()` (na
    escrita) — as duas ficam consistentes entre si sem exigir uma escrita
    prévia. Novo status `ambiguous_journal_seq` em `oos_evidence_status()`
    para quando há candidatos elegíveis mas a seleção recusou por
    `journal_seq` duplicado entre eles.
  - Testes: 8 novos/reescritos em `tests/test_validate_margin_observed.py`
    — dois cobrindo T1/T2 via `append_result()` real (truncação e valor
    forjado alto, ambos verificados reproduzindo o bug contra a lógica
    `len(log)+1` anterior antes de aceitar como fechados), um de falha
    fechada em duplicata, um do novo status `ambiguous_journal_seq`, um
    provando que a leitura funciona em journal legado sem nenhum append
    prévio (fecha MFC59-02/P3-2), um substituindo o teste que exigia
    `journal_seq` (premissa invertida pela correção), e um cobrindo o degrau
    `timestamp_utc` de `_record_datetime()` (fecha P3-1).
  - Suíte completa: `340 passed, 1 skipped, 32 subtests`. `git diff --check`
    limpo. Fingerprint do allowlist Port A após estas correções:
    `50f886143fc27a5898bbbf804648b75c36bbecf9576a8775cd41f28ff4b21d72`.
  - Esta foi a rodada 4 desta sessão.
- Rodada de revisão `mfc-60` (blind, `mfc-rev` + `mfc-rev-2`), pedida por
  Breno: **os dois REJEITARAM de novo**, ambos apontando o MESMO defeito de
  raiz por ângulos diferentes: `_effective_journal_seq()` misturava duas
  escalas incomensuráveis — o fallback `index + 1` (pequeno, reinicia do
  zero) comparado diretamente com valores declarados vindos de
  `max(...) + 1` (potencialmente muito maior), assim que o journal deixa de
  ser contíguo a partir de 1. Isso produzia: (`mfc-rev`, MFC60-01/02)
  colisão silenciosa gravada em disco pelo backfill (checagem rodava ANTES
  do backfill, só sobre declarados) e `oos_evidence_status()` reindexando
  numa lista pré-filtrada, divergindo de `select_latest_oos_evidence()`
  sobre o mesmo `history`; (`mfc-rev-2`, medido ponta a ponta, P2-1/P2-2/
  P2-3/P3-1) o mesmo — colisão persistida que trava todo append futuro,
  entrada mais recente sem `journal_seq` classificada atrás de declarados
  antigos, as duas funções públicas discordando sobre o mesmo `history`, e
  empate declarado-vs-fallback escapando de `ambiguous_journal_seq`.
  `mfc-rev-2` deu a correção precisa: fallback monotônico
  (`max(maior_visto_até_aqui + 1, index + 1)`, numa única passada) e
  consistência checada sobre os valores EFETIVOS, depois do backfill.
  - Corrigido em `scripts/_backtest_results_log.py`: `_effective_journal_seq`
    (por entrada) virou `_effective_journal_seqs` (por array, numa única
    passada esquerda→direita, fallback monotônico); `_journal_seqs_are_consistent`
    virou `_effective_seqs_are_consistent` (compara efetivos, não só
    declarados); `select_latest_oos_evidence()` e `append_result()` chamam a
    mesma função sobre o array completo; `oos_evidence_status()` agora chama
    `select_latest_oos_evidence()` com o `history` ORIGINAL (não uma lista
    pré-filtrada), eliminando a divergência entre as duas funções públicas.
  - 4 testes novos, verificados um a um reproduzindo cada bug contra uma
    cópia isolada da lógica da rodada anterior antes de aceitar como
    fechados (mesmo padrão exigido desde mfc-59). Um teste existente
    (`test_append_result_backfill_never_collides_with_a_declared_value`) foi
    corrigido em seguida — sua expectativa original estava errada: o
    comportamento certo é falhar fechado nesse cenário específico, não
    resolver em silêncio; renomeado pra refletir isso.
  - Suíte completa: `344 passed, 1 skipped, 32 subtests` (era 340 antes desta
    rodada). `git diff --check` limpo. Fingerprint do allowlist Port A após
    estas correções: `fcc293fdab5b72f02156ce0a4d91e3c755603b69b5538d3e9b338bcc99ec7915`.
  - Esta é a rodada 5 desta sessão sobre o mecanismo de `journal_seq`. Nenhuma
    rodada 6 foi disparada ainda — levado a Breno antes de decidir se mais
    uma verificação é necessária ou se o esforço nesta frente para por aqui
    e volta pro item de dados (recuperação de histórico OOS), que é o que
    motivou originalmente pedir execução "ainda hoje".
- A prova independente de que as dependências declaradas intocadas continuam
  byte-idênticas ao `HEAD` é `git diff --quiet HEAD -- agents/triad_analyzer.py
  agents/macro_analyzer.py agents/operational_analyzer.py daily_css_routine.py
  .agents/skills/css-macro-analyzer/SKILL.md
  .agents/skills/css-operational-analyzer/SKILL.md` com saída 0, repetido com
  `git diff --cached --quiet --` sobre os mesmos seis caminhos com saída 0, e
  os hashes SHA-256 atuais: `agents/triad_analyzer.py`
  `e3121c0e51046d47214d9f6cabda2ca52ce5d9e4b82dd0ed65b2b3919e7d99be`,
  `agents/macro_analyzer.py`
  `6c9958050f2f1ee707ae2f20df6ffa95a8f2dbfb96cbaacd938dd6a0de12f990`,
  `agents/operational_analyzer.py`
  `4541924b964fc82224a60c1e9b6616aba18b2ecc85fbd670412fc5df80043a77`,
  `daily_css_routine.py`
  `7e817fe24584302127ce5dcfd3b34f454309205931728f7c5de4fdaf5a4fd071`,
  `.agents/skills/css-macro-analyzer/SKILL.md`
  `6e769c7a279fd61431612198cff5f2df91b0268272b8ed99a2cfd29c2a751c6e` e
  `.agents/skills/css-operational-analyzer/SKILL.md`
  `5ccfbc133f8e4fda0e39d73a7eada2ae584808aafa8cb14f2881b12844d01293`.

Decisões fixadas para o Port A: (a) `544d660` é a etapa normativa desta
implementação; `4138998` fica para uma etapa isolada posterior; (b) a
taxonomia local permanece congelada em `±0.20` para zona de parada e `±0.05`
para equilíbrio; (c) `ref_dt` é obrigatório no motor e representa um
instante normalizado para BRT; (d) os diagnósticos completos ficam no retorno
interno do motor e não são copiados para o payload público; e (e)
`score_total` interno passa a ser o score normalizado da matriz pela fórmula
`weighted_score / 13.5 * 10`; o alias público existente é
`currencies[].total_score`, com contrato documentado e ainda sujeito à
regressão de payload. A referência nominal é `[-10,+10]`, mas o
`544d660` não aplica clamp: estados excepcionais com vetor `±2.0` podem
alcançar aproximadamente `±13.33`; essa propriedade fica explícita e não é
alterada nesta etapa.

`544d660` não tem modulação contínua por slope: seus vetores dependem apenas
de `score`/`diff`, maturação e penalidade, e o score é a soma ponderada com
pesos D1 `3.0`, H4 `2.0`, W1 `1.5`, MN1 `1.5` e H1 `1.0`. A equação normativa
do Port A é `vector_final = base_vector * maturidade * penalidade` e
`score_total = round(weighted_score / 13.5 * 10, 2)`, sem clamp. A fórmula de
slope, `slope_mods` e o clamp de `4138998` ficam explicitamente excluídos;
nenhum trecho de `4138998` pode ser incorporado por esse item.

### Schema e visibilidade dos resultados

- O retorno interno de `evaluate_currency_confluence` usa `score_total`
  (`float`, score normalizado pela matriz) e pode conter `macro_bias`,
  `vectors`, `base_vectors`, `maturities` e `penalties` para auditoria do
  motor. Esses diagnósticos não são serializados no payload web nesta etapa.
- Somente o endpoint `/api/css/all` serializa `currencies[].total_score`
  (`float`), que é o alias público de `conf["score_total"]`. O relatório diário
  expõe `trade_bias` da moeda e `pairs[].total_score`, mas não imprime o score
  da moeda; o frontend usa `trade_bias` no raio-X e `pairs[].total_score` no
  screener.
- `pairs[].total_score` é outro campo público: pertence ao ranking de pares e
  mantém sua escala própria. Não é alias do score da moeda e não é medido pelo
  braço `3tf_baseline`.
- O canal de auditoria dos diagnósticos é o retorno interno do motor, os
  snapshots do comparativo e o journal de backtest; qualquer exposição web de
  `macro_bias` ou vetores exige uma decisão e um teste de schema novos.

O baseline 3-TF usado no backtest comparativo permanece reproduzido em
`scripts/backtest_engine_compare.py` como `3tf_baseline`; ele não depende mais
do módulo de produção, que agora implementa o Port A.

### Primeiro resultado do Port A — 2026-08-30 (registro histórico superseded)

Este é o registro da primeira execução, anterior à reexecução de
reprodutibilidade `mfc-30`. Ele permanece no journal append-only para preservar
a evolução do desenvolvimento, mas não é evidência decisória: os números de
custo e a proveniência foram substituídos pelos registros posteriores com
envelope do produtor completo.

Backtest executado em 45 dias, com 31 noites válidas, mesma máscara de barra
fechada/sessão válida para todos os motores e três passadas de custo na
instância isolada `D:\MetaTradersWSL\mfc-backtest\terminal64.exe`.

- `3tf_baseline`: bruto `-$55,48`, custo `$706,22`, líquido `-$761,70`;
- `5tf_port_a`: bruto `$106,82`, custo `$806,46`, líquido `-$699,64`;
- delta líquido registrado contra o baseline: `+$62,05`.

A conclusão favorável desta primeira rodada está superseded. Para a decisão,
usar somente a reexecução atualizada abaixo, que preserva a mesma máscara e
explicita o volume de cestas, custos, janela e incerteza. O registro histórico
completo continua em `reports/backtest_history.json`.

### Janela OOS disjunta — histórico mfc-40 (superseded)

Foi executada uma janela absoluta meio-aberta `[2026-06-01 21:00,
2026-07-16 21:00) BRT`, com `development_start_brt` exatamente em
`2026-07-16 21:00 BRT`. Ela não se sobrepõe à janela de desenvolvimento
recente. Os quatro motores usaram a mesma máscara de barra fechada e sessão
válida, em três passadas do custo, no terminal isolado
`D:\MetaTradersWSL\mfc-backtest\terminal64.exe`; nenhuma ordem foi enviada.

- `3tf_baseline`: 33 noites, 187 cestas, bruto `$75,61`, custo `$729,33`,
  líquido `-$653,72`, média por cesta `-$3,496 ± 0,747`;
- `5tf_port_a`: 33 noites, 228 cestas, bruto `$178,81`, custo `$871,53`,
  líquido `-$692,72`, média por cesta `-$3,038 ± 0,667`;
- delta líquido Port A contra o baseline: `-$39,00`;
- delta pareado por noite: `-$1,182 ± 4,634`, n=33.

As passadas repetidas produziram os mesmos custos nesta coleta. A qualidade da
reconstrução foi `clean` nos quatro motores (zero cestas degradadas, swaps não
modelados ou preços ausentes). O resultado é disjunto e auditável, mas não
autoriza adoção: o líquido agregado do Port A foi pior nessa janela, a média
por cesta não corrige a diferença de volume, e o custo usa tick atual, não
spread/swap histórico. O registro completo, incluindo faixa por passada,
proveniência e execução, está em `reports/backtest_history.json`.

### Reexecução de reprodutibilidade — 2026-08-30 (mfc-30; histórica)

As janelas de 90 dias e 45 dias foram reexecutadas com o mesmo checkout
isolado, digest de código do produtor, digest das séries/preços e snapshot de
contrato. A janela de 90 dias contém exatamente a janela de 45 dias de
desenvolvimento mais o OOS disjunto; por isso, decisões e PnL bruto devem
obedecer ao complemento `90d - 45d` (custos não são aditivos porque usam o tick
atual do MT5 em cada passada).

- 90 dias `[2026-06-01 21:00, 2026-08-30 21:00)` BRT: baseline `373`
  cestas / bruto `$20,13`; Port A `450` / `$182,88`;
- 45 dias `[2026-07-16 21:00, 2026-08-30 21:00)` BRT: baseline `186`
  cestas / bruto `-$55,48`, líquido `-$761,70`; Port A `222` / `$4,07`,
  líquido `-$849,86`;
- complemento OOS: baseline `187` cestas / bruto `$75,61`; Port A `228`
  / `$178,81`, reproduzindo exatamente as contagens e o bruto do OOS
  registrado.

Nos registros atuais, o delta líquido agregado é `-$127,17` em 90 dias e
`-$88,16` em 45 dias; os deltas pareados por noite são, respectivamente,
`-$1,987 ± 3,786`, n=64, e `-$2,844 ± 6,142`, n=31. No OOS disjunto, o delta
é `-$39,00` e o pareado é `-$1,182 ± 4,634`, n=33. Portanto, a reexecução não
mostra melhora líquida do Port A contra o baseline nessas janelas.

Os três registros mfc-30 foram produzidos no terminal isolado
`D:\\MetaTradersWSL\\mfc-backtest\\terminal64.exe`, com `orders_sent=false`,
contrato `100000` observado nos 28 pares e `producer_provenance.status=complete`.
O digest de dados é o mesmo nas três janelas; o digest de código é o mesmo
entre produtor e checkout importador. O OOS continua contido no agregado de
90 dias para fins de histórico, embora permaneça disjunto da amostra de
desenvolvimento e não seja contado como evidência independente adicional. O
consumidor deve selecionar a evidência por
`select_latest_oos_evidence(...)`; entradas anteriores da mesma janela são
mantidas, mas a nova entrada registra seus identificadores em `supersedes`.
O `producer_provenance` completo é a autoridade da execução; o campo
`provenance` local pode ficar `partial` quando o importador não possui MT5.
Uma evidência OOS só permanece elegível enquanto o digest de todos os arquivos
da allowlist de proveniência coincidir com o checkout que a consulta; qualquer
alteração nesses arquivos expira a evidência e exige nova execução antes de
ser citada. `oos_evidence_status(...)` diferencia ausência de registros de
registros expirados/inválidos. O journal é append-only por registro: a escrita
atômica regrava o array para preservar compatibilidade de schema, sem alterar
as métricas dos registros anteriores.

Após o hardening dos gates temporal, de cobertura e de identidade, a reprodução
`mfc-40` tornou-se o conjunto vigente do histórico. Ela repete os resultados
OOS da mfc-35 — 33 noites, 187 cestas e `-$653,72` no baseline contra 228
cestas e `-$692,72` no Port A, delta `-$39,00`, pareado `-$1,182 ± 4,634` — e
agora carrega o digest atualizado do checkout
`0aa901e8be0c94bd0b962fa3c1163f3a793cc8429775f36394c52cf074e491cb`. A entrada
foi produzida no terminal isolado, com contrato `100000` nos 28 pares,
`producer_provenance.status=complete`, cobertura limpa e
`orders_sent=false`. Os registros mfc-35 e mfc-39 continuam no journal
append-only, mas estão superseded e não são evidências independentes
adicionais; o consumidor deve selecionar a entrada mfc-40 por
`select_latest_oos_evidence(...)`.

> **Nota de expiração (achado herdr-review mfc-58/mfc-59, P3-3):** este
> parágrafo é histórico. Toda edição em `scripts/_backtest_results_log.py`
> (inclusive as rodadas `mfc-56`–`mfc-59` desta sessão) muda `_source_digest()`
> e expira a proveniência de TODOS os registros OOS do journal, `mfc-40`
> incluído — é o gate funcionando, não um bug. No checkout atual,
> `select_latest_oos_evidence(...)` retorna `None` para esta janela (8
> registros, 0 elegíveis). `mfc-40` não pode ser citado como evidência vigente
> sem uma nova execução no terminal isolado que reproduza o digest atual.

O hardening também tornou uma identidade temporal ilegível inelegível,
impediu que ela participasse de `supersedes` e tornou a chave de supersessão
canônica mesmo quando há fallback temporal legado. Os testes cobrem o filtro
de amostra exploratória, as quatro guardas estruturais do envelope e a
rejeição antes da persistência. O validador manual de margem agora retorna
código não zero quando alguma perna falha, e a documentação da matriz inclui
maturação, contra-fluxo, vetores excepcionais `±2.0` e limiares efetivos do
`544d660`.

### Execução histórica mfc-45 — não elegível após o gate de qualidade

Depois das correções de histórico comum e integridade do journal, a execução
`mfc-45` foi feita na mesma janela meio-aberta
`[2026-06-01 21:00, 2026-07-16 21:00) BRT`, com `development_start_brt` em
`2026-07-16 21:00 BRT`. O terminal observado foi a instância isolada
`D:\\MetaTradersWSL\\mfc-backtest\\terminal64.exe`, conta demo Exness
`198819543`, sem envio de ordens.

- `3tf_baseline`: 33 noites, 187 cestas, bruto `$75,61`, custo `$729,33`,
  líquido `-$653,72`, média por cesta `-$3,496 ± 0,747`;
- `5tf_port_a`: 33 noites, 222 cestas, bruto `$459,01`, custo `$851,41`,
  líquido `-$392,40`, média por cesta `-$1,768 ± 0,689`;
- `5tf_upstream`: 33 noites, 228 cestas, bruto `$440,61`, custo `$871,00`,
  líquido `-$430,39`;
- delta agregado do Port A contra o baseline: `+$261,32` (a comparação
  persistida registra `+$261,31` por arredondamento dos valores internos);
- delta pareado por noite: `+$7,919 ± 4,643`, n=33.

As três passadas reproduziram os mesmos números, com `quality.status=clean`,
zero cestas degradadas, zero swaps não modelados e zero pontos de preço
ausentes nos quatro motores. O registro está em
`reports/backtest_history.json`, com `producer_provenance.status=complete`,
digest de código e resultado conferidos no checkout importador. O `mfc-45` é
a entrada mais recente da janela, mas deixou de ser evidência OOS elegível
assim que o gate de qualidade histórica foi corrigido abaixo; o resultado
continua exploratório e não autoriza adoção live.

O `mfc-45` não é uma reprodução da mesma entrada usada em `mfc-40`: o
`data_snapshot_digest` mudou de `25a9fa56…` nas execuções mfc-30…mfc-40 para
`35f7c4e4…` no mfc-45. A causa provável é a correção de `min_periods=100` no
ATR: o baseline 3-TF, que não lê MN1/W1, permaneceu bit-idêntico (187 cestas,
`-$653,72`), enquanto o Port A, que lê esses TFs, mudou (de 228 para 222
cestas e de `-$692,72` para `-$392,40`). Essa atribuição é uma hipótese
fundamentada; o novo gate impede tratar a execução degradada como evidência
OOS até que exista histórico suficiente ou uma nova fonte validada.

O adaptador web exige ao menos 30 barras na interseção temporal comum dos 28
pares. Para cada timeframe, a série inteira retornada precisa também cobrir o
ATR(100) e o deslocamento padrão de 10 posições: o requisito efetivo é
`count + 109` barras no modo standard, isto é, `count` barras utilizáveis mais
109 de prefixo aquecido. No modo gauss, o total é `count + 99`, com prefixo
99. Se a primeira posição exibida não satisfaz esse requisito, o timeframe é marcado como
`degraded`; o `CSSDataEngine` não o serve como live e retorna fallback/cache
com `snapshot_quality`. O backtest OOS vigente foi produzido antes deste gate
de qualidade histórica; a próxima execução só poderá ser elegível se todos os
TFs passarem essa validação. O digest cobre engines, janela, cobertura,
métricas, custos, comparação e qualidade das séries; apenas
metadados/aliases gerados pelo journal ficam fora para evitar circularidade.

### Verificação do gate de qualidade — mfc-46 e mfc-47 (2026-08-30)

Com a validação propagada para o backtest, a mesma execução OOS foi tentada
novamente no terminal isolado e abortou antes de avaliar a janela: `MN1` tem
59 barras comuns, mas `count=60` exige 169 para que o ATR(100) e o
deslocamento de 10 posições estejam completos. Isso é uma recusa deliberada,
não uma falha de conexão nem uma evidência perdida.

Para preservar a evolução do desenvolvimento, a mesma janela foi registrada
como `mfc-46` com `sample_role=exploratory` e `quality.status=degraded`, e
repetida como `mfc-47` após a ligação dos testes de integridade do harness. O
registro mfc-47 identificava `MN1` como degradado e não era selecionado por
`select_latest_oos_evidence(...)`; após a correção final de cardinalidade, seu
`code_source_digest` ficou deliberadamente obsoleto e ele permanece apenas
como histórico. Nenhum registro atual é evidência OOS elegível. O resultado
exploratório mantém a comparação numérica de mfc-45 para diagnóstico, mas a
limitação de entrada está no próprio journal e no digest de dados.

Depois da revisão mfc-47, o gate também passou a distinguir
`requested_history_bars` de `returned_history_bars` e marca como `degraded`
qualquer interseção aquecida que entregue menos que `count`. O caso foi
reproduzido com 50 barras comuns para uma solicitação de 60 e coberto por
teste; snapshots com pelo menos `count` barras preservam o status `clean`.

### Próxima retomada — recuperar evidência OOS válida

Este é o próximo trabalho do plano; ele não reabre o Port A nem autoriza
adoção live. O bloqueio atual é de dados, não de implementação: no terminal
isolado observado (`D:\\MetaTradersWSL\\mfc-backtest\\terminal64.exe`), a
interseção comum de MN1 tem 59 barras, enquanto o contrato atual pede 169
(`count=60` mais aquecimento do ATR(100) e deslocamento de 10 posições).

Para produzir uma nova evidência OOS elegível:

1. Usar somente a instância demo isolada e carregar histórico suficiente para
   os 28 pares, preservando os mesmos símbolos, sufixo e configuração do
   backtest. Não enviar ordens.
2. Confirmar, antes da execução, que os cinco timeframes têm cobertura comum
   completa e passam o aquecimento; para a configuração OOS atual, MN1 precisa
   ter pelo menos 169 barras comuns. Qualquer falta mantém o resultado como
   `degraded`.
3. Pré-registrar uma janela meio-aberta disjunta, com pelo menos 30 noites
   candidatas e pontos de decisão às 21:00 BRT. Fixar o cutoff de
   desenvolvimento antes de olhar os resultados.
4. Executar o comparativo com `sample_role=oos_disjoint`, `ref_dt`/endpoint
   explícito em BRT e o mesmo conjunto de engines (`3tf_baseline`,
   `5tf_port_a` e, se desejado, `5tf_upstream` como variante informativa).
5. Validar no journal: cobertura completa de entrada e saída, zero skips,
   zero pontos ausentes, reconstrução limpa, digests de código/dados/resultados
   válidos e seleção não nula por `select_latest_oos_evidence(...)`.
6. Se qualquer gate falhar, manter `eligible=0` e registrar a tentativa como
   exploratória/degradada. Não reduzir `TF_COUNTS`, relaxar o gate, reutilizar
   mfc-45/mfc-46/mfc-47 ou chamar fonte alternativa sem validação independente
   de OHLC, timestamps, mapeamento de símbolos e custos.

Somente depois de uma janela OOS elegível faz sentido comparar a estabilidade
do Port A com o baseline e decidir se a etapa isolada de `4138998` merece ser
avaliada. Mesmo então, a adoção live continua condicionada à validação real de
`order_calc_margin()` e a uma decisão explícita posterior.

### Gate de adoção e rollback

As execuções mfc-45, mfc-46 e mfc-47 permanecem exploratórias: não autorizam afirmar
superioridade estatística nem fazer deploy. A comparação numérica registrada
em mfc-45 marcou o Port A em `-$392,40` líquido contra `-$653,72` do baseline,
delta agregado de aproximadamente `+$261,32`, com 222 contra 187 cestas; o
delta pareado foi `+$7,919 ± 4,643` por noite, n=33. Esses números foram
produzidos antes do gate histórico estrito e não são evidência OOS elegível.
A mfc-46 confirma que a janela não pode ser promovida enquanto o MN1 não
tiver histórico suficiente. A média por cesta e o delta agregado não
transformam o resultado em superioridade estatística; o custo usa tick atual,
não spread/swap histórico. Os agregados de 90 dias e as execuções anteriores
permanecem apenas como histórico, não como nova amostra OOS independente. O
resultado permanece exploratório e não altera o gate de adoção live. O
abortamento é obrigatório diante de qualquer
P0/P1/P2 não tratado, quebra de contrato, snapshot incompleto ou divergência
de `ref_dt`. Para uma evidência OOS ser elegível, o harness exige pelo menos
30 noites candidatas, cobertura completa de entrada/saída, zero datas sem
veredito, zero pontos de preço ausentes, reconstrução limpa e ao menos uma
cesta em cada motor comparado; os limites temporais precisam ser instantes
canônicos de 21:00 BRT.
A rotina compartilhada valida a semântica temporal nos dois caminhos: offset
BRT explícito, relação exata entre início/fim e duração, cutoff compatível e
datas únicas às 21:00 dentro do intervalo. Gates específicos do produtor e do
importador validam, respectivamente, e revalidam, a cobertura completa de
entrada/saída e a reconstrução limpa.

O rollback precisa ser atômico para uma revisão/manifesto coerente, não uma
remoção isolada de `ref_dt`. O manifesto do baseline pré-Port A é o commit
`7b22a4bce946e530795d9dbb083f99ac572731fb`, com estes blobs dos caminhos de
runtime:

- `agents/confluence_engine.py` —
  `5ecb4f05e97aac52220f8b68f90f796143773605`; assinatura sem `ref_dt`, decisão
  3-TF D1/H4/H1 e `score_total` bruto nessa escala;
- `web/css_service.py` —
  `5ea58fccbd33979895ff51ab4469b5e99e4b7a0d`; chamada do motor sem `ref_dt`;
- `scripts/backtest_canonical.py` —
  `846bf38e16c8861911b8b7b7901b6143ae2e9395`; `evaluate_at` chama o mesmo
  contrato sem `ref_dt`.

O allowlist real do Port A é formado por esses três caminhos de runtime e pelos
artefatos de validação/documentação: `tests/test_confluence_matrix_port_a.py`,
`tests/test_css_service_port_a.py`, `tests/test_backtest_canonical.py`,
`scripts/backtest_engine_compare.py`, `scripts/_backtest_results_log.py`,
`reports/backtest_history.json`, `docs/API.md` e este plano. Os analisadores,
skills, rotina diária e executor não foram alterados, como registrado no
inventário acima.

Para um rollback futuro, antes de qualquer adoção o estado operacional seguro
atual deve estar materializado em um commit/árvore própria, preservando as
correções de segurança e qualidade que não pertencem ao Port A. O Port A deve
estar em um commit separado, contendo apenas as mudanças de matriz/`ref_dt`.
Os blobs-base acima são referência histórica do contrato 3-TF; não são uma
receita para substituir `web/css_service.py` ou `scripts/backtest_canonical.py`
inteiros.

O rollback é a reversão do commit isolado do Port A sobre a árvore segura: em
`agents/confluence_engine.py`, restaurar a função de decisão 3-TF; em
`web/css_service.py`, reverter apenas a captura/passagem de `ref_dt` e a
seleção da matriz, preservando `MIN_COMMON_HISTORY_BARS`,
`required_full_history_bars`, ATR com `min_periods=ATR_PERIOD` e todos os gates
de snapshot; em `scripts/backtest_canonical.py`, reverter apenas a assinatura
e chamada do motor, preservando `load_series(..., require_clean=...)`, a
validação OOS e o journal. Nenhum arquivo inteiro desses dois adaptadores deve
ser restaurado pelo rollback.

O procedimento é: pausar e terminar os processos web/scheduler, preparar uma
árvore temporária a partir do commit seguro, aplicar o patch inverso do commit
isolado do Port A, conferir os blobs protegidos e os invariantes de qualidade,
executar os smoke tests sem MT5 e só então trocar a árvore ativa em uma
operação única; iniciar novos processos a partir dessa árvore e confirmar o
commit/caminho carregado antes de reabrir consumidores. Não se deve restaurar
arquivos um a um no worktree em uso nem usar uma worktree integral do
commit-base histórico.

O rollback preserva `reports/backtest_history.json` e os registros de auditoria,
e deixa `agents/portfolio_executor.py`, `scripts/scheduler_daemon.py`, dados,
EA e deploy intocados. O smoke test cobre importação/chamada do caminho web,
`evaluate_at` do backtest, `daily_css_routine` via `css_engine.update_data` e
importação do scheduler em modo sem MT5, além dos testes de segurança e
backtest; verificar também `required_full_history_bars`,
`min_periods=ATR_PERIOD`, `load_series(require_clean=...)` e o schema público.
Uma falha de assinatura, invariante, schema ou importação mantém o rollback
recusado. Como nenhum deploy foi feito nesta etapa, o gate de adoção live
continua fechado até uma decisão explícita posterior.

### Fase B — port focado (concluída para o Port A)

- Aplicar a matriz 5-TF em uma alteração isolada e revisável.
- Preservar APIs existentes sempre que possível; quando a matriz precisar de
  `ref_dt`, tornar o contrato explícito e atualizar todos os chamadores
  controlados, sem fallback temporal oculto.
- Usar allowlist de caminhos e confirmar com `git diff --name-only` que nenhum
  dado, UI ou arquivo de deploy entrou no port; o artefato exigido
  `reports/backtest_history.json` é a exceção explícita para registrar a
  evidência.
- Não importar dados, UI, Firebase, EA, systemd ou arquivos de deploy do
  upstream.
- Não ligar diretamente `macro_power` ou `op_power` ao `trade_bias` sem uma
  decisão nova e explícita; a fonte de verdade será a matriz vetorial.

### Fase C — validação (parcialmente concluída; OOS pendente)

| Item | Estado | Evidência ou decisão |
| --- | --- | --- |
| Vetores, zonas locais, maturação e contra-fluxo | concluído | `tests/test_confluence_matrix_port_a.py::TestPortAVectors` e `::TestPortAMacroDecision` |
| Slope | concluído por exclusão | `544d660` não tem modulação contínua; `slope_mods`, novos pesos e clamp de `4138998` estão fora do Port A |
| Decisão 5-TF contra contexto macro | concluído | `TestPortAMacroDecision::test_macro_context_changes_decision_while_operational_data_is_fixed` |
| Escala pública e contrato de `trade_bias` | concluído | `TestPortAMacroDecision::test_score_uses_544d660_normalization_without_clamp` e asserção para `COMPRA|VENDA|NEUTRO` |
| Chamadores web, backtest e diário | concluído por caminho compartilhado | `update_data` e `evaluate_at` passam `ref_dt`; `daily_css_routine::run_daily_routine` chama o mesmo `css_engine.update_data` |
| Paridade de assinatura e dependências | concluído por inventário | Não há chamada direta em `daily_css_routine`, scheduler, `history_tracker` ou frontend; analisadores e skills ficaram byte-idênticos ao `HEAD` |
| Schema público versus diagnóstico interno | concluído | `score_total` é interno; somente `/api/css/all` serializa `currencies[].total_score`; o diário expõe `trade_bias` da moeda e `pairs[].total_score`. Cobertura: `tests/test_css_service_port_a.py::test_api_css_all_public_schema_separates_currency_and_pair_scores` (payload de `update_data`, com o motor mockado, confirma que nenhum diagnóstico interno — `score_total`, `macro_bias`, `vectors`, `base_vectors`, `maturities`, `penalties`, `weighted_score`, `macro`/`operational`, `aligned_*_count`, `ref_dt_brt` — vaza para `currencies[]`, e que `pairs[].total_score` é um campo separado com escala própria) e `::test_daily_report_format_separates_currency_trade_bias_from_pair_total_score` (regressão por inspeção de fonte de `daily_css_routine.py`, mesmo padrão de `TestDailyRoutineDoesNotDuplicateSignalWrite` em `tests/test_portfolio_safety.py`, já que a rotina dispara matplotlib/Telegram/MT5 e não deve ser executada em teste) |
| Ranking de pares 3-TF | separado | `evaluate_28_pairs_confluence` só alimenta o screener de pares e não entra no harness |
| Baseline 3-TF por moeda | concluído no harness | O braço `3tf_baseline` em `scripts/backtest_engine_compare.py` mede a decisão pré-Port A por moeda; não mede o ranking de pares |
| `web/history_tracker.py` | fora do gate do Port A | `TrackRecordEngine` é um oracle de auditoria separado; `scripts/backtest_engine_compare.py` não o executa nem promete equivalência numérica. Mudança nessa regra abre item próprio |
| Gate temporal, qualidade e OOS | parcial | Testes de `ref_dt`, janela disjunta e qualidade passam; nenhuma janela atual é elegível por falta de MN1, conforme a seção de retomada OOS |
| Paridade nas viradas semanal/mensal | concluído | `TestPortAVectors::test_maturity_uses_explicit_brt_reference` e `test_backtest_window_endpoint_is_normalized_to_brt` |
| Suíte, diff e revisão | concluído para o Port A | `mfc-49`: `322 passed, 1 skipped, 32 subtests`; `git diff --check`; revisão fixa `mfc-rev` + `mfc-rev-2` |

A regressão do schema público foi executada e passou (`324 passed, 1 skipped,
32 subtests`, incluindo os dois testes citados na tabela acima). A validação
restante tem um gate: recuperar dados para a janela OOS descrita acima. O item
de `history_tracker` não é uma pendência oculta: ele fica fora do gate porque
não consome o motor Port A e possui regra de seleção própria.

### Fase D — revisão e integração (implementação concluída; gate documental pendente)

- Submeter o plano e depois a implementação a revisores independentes.
- Resolver divergências com evidência no checkout, sem escolher por votação
  quando houver conflito factual.
- Só considerar concluído com ausência de P0/P1/P2 não tratados.

## Resultado da arbitragem

A arbitragem anterior confirmou como obrigatórios: escolher entre `544d660` e
`4138998`;
preservar ou reabrir explicitamente a taxonomia local; definir e propagar
`ref_dt`/fuso; usar allowlist de caminhos; especificar escala/schema público e
contrato exato de `trade_bias`; tratar `history_tracker` e
`backtest_engine_compare.py` como regras/cópias que precisam de decisão
explícita; e adicionar gates empíricos, janela fora da amostra, tolerâncias e
rollback.

Foi refutada a hipótese de mudança de denominador entre os commits: ambos usam
13,5. A divergência matemática real está nos pesos, slope e clamp. Para este
Port A, `544d660` foi fixado como etapa normativa, os diagnósticos completos
ficam internos e o contrato público preserva as chaves existentes.

## Critérios de aceitação

- `trade_bias` usa a matriz institucional 5-TF e não o score bruto 3-TF como
  única fonte de decisão.
- `macro_bias`, vetores efetivos, maturação e penalidades ficam observáveis no
  retorno para auditoria.
- `macro_power` e `op_power` não são apresentados como se fossem usados
  diretamente quando não forem.
- Os testes cobrem comportamento novo e compatibilidade dos consumidores.
- O payload de `/api/css/all` e o relatório diário distinguem o alias público
  `currencies[].total_score`, o diagnóstico interno `score_total` e o
  `pairs[].total_score`, com regressão nomeada para esse contrato.
- A suíte passa sem executar MT5, enviar ordens ou alterar o executor de
  produção nesta fase.
- Nenhuma mudança ampla de dados, dashboard, deploy ou EA entra por acidente.

## Fora do escopo

- Merge integral de `upstream/main`.
- Alteração de `agents/portfolio_executor.py` ou
  `scripts/scheduler_daemon.py`.
- Envio de ordens ou deploy; o backtest dependente do terminal é somente
  leitura e serve como evidência registrada, sem ativar o executor.
- Exclusão do GBPNZD.
- Implementação de áudio/prints ou novos ajustes do motor de convicção além da
  matriz confirmada neste plano.

## Riscos e gates

- **Mudança de comportamento:** comparar snapshots antes/depois e exigir testes
  explícitos para cada limiar.
- **Quebra de consumidores:** mapear assinaturas e chaves retornadas antes do
  port; não remover chaves sem adaptador/teste.
- **Mistura de decisões:** manter separadas a matriz por moeda e a lógica de
  ranking/alicate de pares até haver evidência de que devem ser unificadas.
- **Drift do upstream:** fixar os commits usados por SHA completo
  (`544d660b423498cd41a594328ef05b0c4c6adff1` e
  `413899804c7b920317d1db17b6ec91338ad6895a`), conferir a tree antes de uma
  etapa futura e abortar se `upstream/main` não resolver exatamente ao SHA
  registrado. Qualquer avanço do upstream exige nova decisão/medição, nunca
  atualização silenciosa deste plano; dados gerados/UI não entram na
  portabilidade.

## Gate pós-implementação [HISTÓRICO — status de evidência OOS superado, ver seção "OOS estendido" abaixo]

**Achado herdr-review mfc-64 (MFC64-03/`mfc-rev`):** esta seção descreve o
estado em que nenhuma evidência OOS era elegível (histórico MN1
insuficiente). Isso deixou de ser verdade a partir do `mfc-61`
(evidência OOS elegível pela primeira vez) e principalmente da seção
**"OOS estendido"** abaixo (`journal_seq=26`, a evidência elegível atual —
ver `select_latest_oos_evidence`). O texto original permanece abaixo só
como registro histórico do que motivou as rodadas `mfc-50`–`mfc-61`; não
leia como descrição do estado atual.

O gate técnico foi concluído na rodada final `mfc-49`: `mfc-rev` e
`mfc-rev-2` confirmaram que não resta P0, P1 ou P2. O histórico registra
mfc-45, mfc-46 e mfc-47, mas nenhuma evidência OOS atual é elegível: a janela
estrita foi recusada por histórico MN1 insuficiente, e mfc-46/mfc-47 são
exploratórias/degradadas. A suíte local passou, o escopo foi auditado e nenhum
pedido/deploy foi executado. A adoção live continua fechada até decisão
explícita posterior e validação manual de margem no terminal MT5. O gate
documental deste plano foi reaberto nas rodadas `mfc-50`/`mfc-51`/`mfc-52`/
`mfc-53`; o `mfc-49` permanece apenas como aprovação histórica da
implementação. Nenhuma rodada intermediária autoriza continuar o OOS ou
alterar código sem a aprovação agregada da versão corrigida pela dupla fixa.

## OOS estendido (journal_seq 26) — janela 15x maior que o `mfc-61`, 2026-09-01

Depois das rodadas de revisão `mfc-62`/`mfc-63` (achados fechados, ver seções
acima), o usuário pediu uma janela OOS maior que os 33 noites do `mfc-61`
pra apertar o intervalo de confiança do delta 3TF-vs-5TF. A cadeia de
achados, cada um descoberto só depois de tentar rodar (não hipotéticos):

1. **H1-preço travado em 1800 barras (~75 dias)**, sem nunca ter sido
   justificado no código — `load_h1_prices()`. Probe manual (usuário + exec,
   contra a instância `mfc-backtest`) mediu a profundidade REAL da Exness:
   ~30.159-30.160 barras (~4,85 anos) nos 25 pares mais curtos,
   ~59.951-59.965 (~12,6 anos) em EURUSD/GBPUSD, 100.000+ em GBPJPY — ao
   contrário do MN1, aqui não é gargalo de dado, só de código pedindo pouco.
   Corrigido (`7cfbe53`): `h1_bars_for_days(days)`.
2. **As 5 séries de score (`TF_COUNTS`) também travadas**, independente de
   `load_h1_prices()` — `MN1:60 W1:120 D1:200 H4:600 H1:1600`, fixos, nunca
   escalavam com `days`. Pior: como `copy_rates_from_pos` conta pra trás a
   partir de AGORA (não do fim da janela pedida), mesmo o FIM de uma janela
   no passado já ficava fora do alcance. Corrigido (`5886559`):
   `bars_needed_since(window_start_brt, bars_per_day, floor)` substitui
   `h1_bars_for_days` como fórmula única, usada pras 5 séries via
   `tf_counts_for_window()`.
3. **MN1 warmup com déficit de ~4 meses** pra janela maior: o cache
   HistData cobria só 2012-2021 (120 meses) pros 25 pares "curtos" do MN1
   (só o AUDJPY tinha 2010-2011 extra, do fechamento do buraco); combinado
   com a Exness nativa dava ~179 meses contra 183 necessários. Resolvido
   baixando 2010-2011 pros 18 pares "cross" que ainda não tinham (os 7
   USD-cross + AUDJPY + CHFJPY já tinham) — mesma fonte/método do AUDJPY,
   `scripts/fetch_histdata_mn1_warmup.py` agora idempotente por ano (não só
   por par: completa anos faltantes num cache já existente, achado do
   resíduo "não reproduzível" que `mfc-rev-2` tinha apontado no merge
   Dukascopy do AUDJPY). Todos os 27 pares agora cobrem 2010-01, zero
   buracos.
4. **`evaluate_at_all()` recusa `i<30`** (índice de barra fechada), por TF,
   independente de profundidade de dado — a margem de `bars_needed_since`
   precisa exceder 30 com folga real. Primeira tentativa (margem=60)
   funcionava pro H1 (60h≈2,5 dias, barato) mas desperdiçava quase metade
   do orçamento do **W1** (60 semanas≈420 dias!) — o TF mais restrito
   (~254 semanas reais nos pares curtos, que pro W1 inclui
   AUDUSD/NZDUSD/USDCAD/USDCHF/USDJPY além dos 18 cross; só EURUSD/GBPUSD/
   GBPJPY são fundos aqui — composição diferente da do MN1). Calibrado pra
   40 (`1b4e9a0`), verificado empiricamente com `require_clean=True` (o
   gate real — um diagnóstico anterior tinha usado `require_clean=False`
   por engano e mascarou a degradação).

**Janela final, verificada e executada:** `[2024-08-27, 2026-07-16)` BRT,
688 dias, 488 noites — `evaluated_nights == candidate_nights == 488`,
`short_history_pairs=[]` nas 5 séries, `price_missing_points=0`.
`sample_role=oos_disjoint`, `development_start_brt=2026-07-16` (mesmo
boundary do `mfc-61` — nada depois dele foi usado em desenvolvimento; a
extensão foi só pra TRÁS, nunca reusando o período
`[2026-07-16,2026-08-30)` já usado como exploratório em rodadas
anteriores). **Achado P3-2/`mfc-rev-2` (rodada `mfc-64`):** o W1 é o TF
mais apertado — 254 semanas disponíveis contra 253 exigidas pela margem
atual, só **1 barra** de folga. A janela não pode esticar mais pra trás
sem alterar a margem/calibração; uma tentativa futura falharia com
`short_history_pairs=[...]` sem indicar que o teto é o W1.

### Rodada `mfc-64` (herdr-review) — achado P1 que invalidou o primeiro registro

A primeira execução desta janela registrou `journal_seq=25` e foi pra
revisão (`mfc-64`). `mfc-rev` achou **MFC64-01 (P1)**: `_basket_pnl()` —
a função que produz o PnL desta evidência — chamava `convert_pnl_to_usd()`
**sem `rates_dict`**, então qualquer perna de cotação não-USD caía na
tabela hardcoded de `web/history_tracker.py` (NZD=0,60, GBP=1,30 etc.),
contradizendo o `rates_source="historical_h1_prices"` declarado no
registro — a MESMA classe de bug já fechada em
`measure_composition_effect.py` na `mfc-62/63`, só que numa função
diferente (a que realmente produz a evidência OOS). `journal_seq=25` foi
**descartado como evidência numérica** (permanece no journal só como
registro histórico, nunca modificado — journal é append-only) e a janela
foi reexecutada como `journal_seq=26` depois do fix. Outros achados
fechados na mesma rodada: fetcher idempotente por MÊS, não por ano
(MFC64-02/`mfc-rev` + P3-3/`mfc-rev-2`, CONFIRMADO pelos dois — o
`any(...)` anterior teria deixado passar de novo o exato formato do
buraco real do AUDJPY); journal sempre grava LF, mesmo rodando no lado
Windows (MFC64-06); `getattr` no lugar de acesso direto pro fail-closed de
spread/swap (MFC64-05); `parameters.use_histdata_mn1_warmup` declarado
explicitamente, não só inferível de dentro de `quality` (P3-1/`mfc-rev-2`
— a validação cruzada do warmup mediu o *close* mensal contra a Exness,
não a amplitude high-low que alimenta o ATR diretamente; `mfc-rev-2`
checou depois, à parte, e não achou viés, mas a limitação passou a constar
explicitamente no registro).

**Resultado (`journal_seq=26`, número corrigido) — divergente em SINAL do
`mfc-61`, mas as duas amostras NÃO são independentes:**

| | `mfc-61` (33 noites) | `journal_seq=26` (488 noites) |
|---|---|---|
| janela | `[2026-06-01,2026-07-16)` | `[2024-08-27,2026-07-16)` |
| 3tf_baseline líquido | -$267,58 / 187 cestas | -$7.722,80 / 2806 cestas |
| 5tf_port_a líquido | -$247,69 / 228 cestas | -$8.040,92 / 3533 cestas |
| delta pareado/noite | **+0,603 ± 4,582** (n=33) | **-0,652 ± 2,579** (n=488) |

**Achado P3-4/`mfc-rev-2` (rodada `mfc-64`):** a janela do `mfc-61`
(`[2026-06-01,2026-07-16)`) está **inteiramente contida** na janela nova
(`[2024-08-27,2026-07-16)`, mesmo `end_brt`) — as 33 noites são um
SUBCONJUNTO das 488, não uma segunda amostra independente. Isolando só a
parte disjunta (455 noites que o `mfc-61` nunca viu):
`(488×(-0,652) − 33×0,603)/455 ≈ -0,743`/noite — mais negativo que o
total, ou seja, a sobreposição não é o que produz a inversão de sinal; o
delta segue negativo mesmo olhando só pro período genuinamente novo. O
erro padrão da amostra completa (2,579, contra 4,582 do `mfc-61`) ainda
cobre zero (IC95% ≈ -0,652 ± 5,06 → [-5,71, +4,41]). **Não há evidência
estatisticamente significativa de que o Port A bata ou perca do baseline
3TF nesta janela** — consistente com ruído de regime de mercado, não com
um efeito real e estável. Ambos os motores são líquido-negativos nas duas
janelas.

### Verificação do baseline `mfc-61` contra o fix do P1 (journal_seq 27/28)

Pedido do usuário: o `mfc-61` original (`journal_seq=24`, +0,603±4,582)
também foi calculado com a mesma `_basket_pnl()` sem `rates_dict` do
achado MFC64-01 — nunca tinha sido reexecutado, só a janela estendida
recebeu o fix. Reexecutar a janela original `[2026-06-01,2026-07-16)`
(`journal_seq=27`) deu **+0,631±4,511** — variação pequena, mesmo sinal;
o bug teve efeito quase nulo nesta janela específica (diferente do que se
temia antes de medir).

**Efeito colateral registrado, não um achado de revisão:**
`select_latest_oos_evidence()` escolhe por `journal_seq` mais alto, sem
considerar tamanho de amostra — rodar `journal_seq=27` (33 noites) DEPOIS
de `journal_seq=26` (488 noites) fez a evidência selecionada regredir pra
amostra menor, apesar de `journal_seq=26` ser estritamente mais robusta.
Corrigido reexecutando a janela estendida mais uma vez (`journal_seq=28`,
mesmos parâmetros do 26, -0,648±2,579 — dentro do ruído normal de tick da
mesma janela/código) só pra restaurar o ponteiro. **A evidência elegível
atual é `journal_seq=28`.** Isto não é um bug do mecanismo — é o
comportamento monotônico por design (decidido nas rodadas mfc-56–mfc-60,
"nunca `recorded_at_utc`, sempre `journal_seq`") — mas reexecutar uma
janela menor por cima de uma maior tem esse efeito prático, vale ter em
mente antes de rodar qualquer verificação pontual no futuro.

**Escopo real da mudança (achado MFC64-04/`mfc-rev` — a formulação anterior
desta seção era imprecisa):** nenhum GATE de execução foi alterado —
`open_portfolio_basket()`, `check_execution_config()`,
`check_account_gate()`, o kill switch, a idempotência, os tetos, a colisão
netting, o preflight e a margem agregada permanecem byte-idênticos. Mas
`agents/portfolio_executor.py` (código do executor ao vivo, não só
scripts/dados) FOI tocado nesta cadeia: `measure_and_log_basket_cost()`
ganhou observabilidade (grava `spread_usd`/`swap_usd` da cesta REAL,
commit `2d0aacc`, e o fix `getattr` desta rodada) — roda DEPOIS da cesta
aberta e nunca decide abrir/recusar nada, mas é uma mudança no módulo de
execução e precisa constar no inventário de escopo. Os demais arquivos
tocados: `scripts/backtest_canonical.py`, `scripts/backtest_engine_compare.py`,
`scripts/measure_composition_effect.py`, `scripts/measure_spread_per_pair.py`,
`scripts/fetch_histdata_mn1_warmup.py`, `scripts/_backtest_results_log.py`,
`data/histdata_mn1_warmup/*.json` e `reports/backtest_history.json`.

Rodada `herdr-review` disparada e fechada sobre esta cadeia inteira
(`mfc-64`, achados acima todos corrigidos e reexecutados).
