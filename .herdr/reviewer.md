# MFC — CSS Institutional

Indicador Currency Slope Strength + motor de track-record + execução ao vivo em
MetaTrader 5. Este é código que envia ordens reais a mercado — na dúvida entre
"provavelmente ok" e "pode abrir posição errada", reporte.

## Isolamento

Você é a única lente nesta rodada (a outra roda em paralelo, cega, num diretório
irmão — ver `request.md`). Não invoque skills de revisão multi-agente
(`dual-r`, `tri-r`, `pair-i`, `codex-r`, `codex-i`) nem delegue via
subagente/`agentrelay`/`dispatch`. Se faltar informação, diga o que faltou —
não busque segunda opinião pra preencher a lacuna.

## Invariantes do projeto

Violação de qualquer item abaixo é achado P0 ou P1, mesmo que os testes passem.

1. **Paridade tri-implementação.** A matemática do CSS existe três vezes:
   `web/css_service.py` (`calc_lwma`, `calc_atr_sma`, `calculate_full_css`),
   `mt5/css.mql5` e `CSS.pine`. As três precisam produzir o mesmo número.
   Mudança em uma sem as outras duas é regressão silenciosa. Fórmula canônica
   em `docs/MATHEMATICAL_MODELS.md` §1.

2. **Ordem dos gates de execução.** `agents/portfolio_executor.py::open_portfolio_basket()`
   checa, nessa ordem: kill switch → validade da configuração de execução
   (`check_execution_config()` — CSS_MAX_LOT, CSS_MAX_CONCURRENT_BASKETS,
   CSS_CATASTROPHIC_SL_PIPS, os dois CSS_AMBIGUOUS_CONFIRM_*, CSS_MIN_MARGIN_FREE)
   → identidade da conta (`CSS_MT5_EXPECTED_LOGIN`) → trava de demo
   (`CSS_LIVE_TRADING`) → margem livre mínima, piso fixo (`check_account_gate()`,
   contra o `margin_free` AO VIVO da conta) → idempotência → tetos de exposição →
   colisão de símbolo em conta netting → preflight de símbolo/tick
   (tudo-ou-nada) → **margem agregada** (soma `order_calc_margin()` das 7
   pernas já resolvidas contra uma releitura FRESCA de `margin_free`, recusa
   se não cobrir o total mais a reserva de `CSS_MIN_MARGIN_FREE` — erros
   `margin_calc_failed`/`insufficient_aggregate_margin`) → stop-loss
   catastrófico do lado do broker. O piso fixo e a margem agregada são DUAS
   checagens deliberadamente separadas, não uma redundância a simplificar: o
   piso é barato e cedo (antes da cesta existir); a agregada é a resposta real
   a "as 7 pernas cabem?", só possível depois do preflight resolver os
   símbolos — decisão registrada em `.herdr/ask/mfc-3/` (herdr-ask + árbitro
   `gpt-5.6-sol`, 27/08, porque os dois revisores da rodada 15 concordaram no
   que fazer mas divergiram em QUANDO). `order_calc_margin()` é Windows-only,
   não testável neste checkout — qualquer achado que dependa de rodar essa
   chamada de verdade (em vez da lógica de agregação/fail-closed em torno
   dela) é esperado ficar sem verificação até validação manual no terminal
   real. Exposição e
   colisão netting são as duas exceções documentadas: ambas são recusas
   puras sobre o mesmo snapshot de `open_magics`, sem efeito colateral,
   então a ordem relativa ENTRE ESSAS DUAS não muda a decisão de
   abrir/recusar — só qual mensagem de erro sai quando as duas se
   aplicariam (ver `CLAUDE.md`, seção "Live MT5 execution"). Reordenar
   qualquer OUTRO par de gates, pular um gate ou torná-lo condicional
   continua P0. A margem livre foi item 2 do plano de reconciliação com o
   upstream (Miquéias): a versão dele fica fail-open quando `account_info()`
   é `None` ou lança exceção (pula a checagem inteira) e assume USD na
   mensagem — aqui `margin_free` ausente ou não-finito recusa (fail-closed,
   igual às outras checagens desta função), e a mensagem cita a moeda real
   da conta. "Ausente" e "inválido" em `.env` NÃO são o mesmo caso (decisão
   do usuário, F06-2, 2026-08-27 — ver `CLAUDE.md`, seção "Live MT5
   execution", pra não reabrir isto): gates de identidade/permissão
   (`CSS_MT5_EXPECTED_LOGIN`, `CSS_LIVE_TRADING`) não têm default seguro —
   ausência é ambígua e tem que recusar. As SEIS variáveis de
   `check_execution_config()` são margens de segurança tunáveis com default
   documentado (150 pips, 0.01 lote, 8 cestas, 3 tentativas, 1.0s, 50 de
   margem livre) — ausência usa o default e ABRE normalmente; só um
   valor EXPLICITAMENTE fornecido e inválido recusa. Isso não é fail-open: é
   a mesma regra "usado ≠ escrito" aplicada só a quem escreveu algo (nota:
   isso vale só pro LIMIAR `CSS_MIN_MARGIN_FREE` em si — o `margin_free` AO
   VIVO da conta, lido em `check_account_gate()`, segue a regra oposta:
   ausente/não-finito SEMPRE recusa, não tem "default" possível pra um dado
   que só o broker sabe). Existe uma SÉTIMA variável tunável,
   `CSS_CLOSE_WATCHDOG_DEADLINE_SEC` (orçamento de tempo do watchdog de
   fechamento em `close_all_portfolios()`) — DELIBERADAMENTE fora de
   `check_execution_config()`/das seis acima: essa variável só afeta
   FECHAMENTO, que nunca pode ser recusado por config ruim (reduzir risco
   nunca é bloqueado), então um valor ausente ou inválido aqui cai no
   default (90s) via `_env_number()`/`_clamp()` comuns, sem passar pelo gate
   de abertura. Não é omissão nem achado novo se um revisor encontrar essa
   variável fora da lista das seis.

3. **Kill switch é assimétrico.** `data/CSS_KILL.flag` bloqueia abrir cesta
   nova. Nunca bloqueia fechamento — reduzir risco sempre prossegue. Qualquer
   caminho que faça o flag impedir um fechamento é P0.

4. **Cesta parcial às 21:05 alerta externamente, mas não se auto-fecha.**
   Achado mfc-rev-2 (herdr-ask consulta 3, 27/08): antes disso, uma cesta
   PARCIAL (margem, requote, símbolo indisponível, queda de conexão — qualquer
   causa) só virava um `print()`, e a reconciliação das 08:10 não pega o caso
   (cesta parcial fecha limpa por magic, sem posição órfã pra detectar).
   `execute_phase_2105()` em `scripts/scheduler_daemon.py` agora grava em
   `PARTIAL_BASKET_LOG` e tenta Telegram (melhor esforço, nunca derruba a
   fase) pra toda cesta parcial. Isso é só ALERTA — o fechamento automático da
   cesta parcial foi deliberadamente deixado como decisão em aberto do Breno
   (julgamento de tolerância a risco, não fato técnico); não é achado uma
   cesta parcial continuar aberta até o encerramento das 08:00 de hoje.
   Achado MFC18-01 (Codex, herdr-review rodada 18): `success=False` (nenhuma
   perna CONFIRMADA aberta) não bastava pra decidir "recusada" — cada perna
   agora tem três estados possíveis (`OPENED`/confirmado aberto,
   `ERROR`/confirmado que não abriu, `UNCERTAIN`/nem uma coisa nem outra —
   ordem enviada ou resposta ambígua sem confirmação), e `uncertain_count`
   no retorno de `open_portfolio_basket()` avisa o daemon quando "recusada"
   seria enganoso (pode haver exposição real sem confirmação); esse caso
   também vira alerta de cesta PARCIAL, não recusa silenciosa. Achado
   P1-1/rodada 19 (Codex + mfc-rev-2, confirmado pelos dois
   independentemente): a arquitetura de três estados da rodada 18 cobriu a
   1ª tentativa de `order_send()` mas deixou de fora o REENVIO com
   `ORDER_FILLING_RETURN` — um `res2` ambíguo/`None` caía direto em `ERROR`
   sem confirmação. Corrigido aplicando o MESMO padrão de confirmação
   (`_confirm_position_after_ambiguous_retcode`) também ao resultado do
   reenvio; mfc-rev-2 mediu que num broker que só aceita filling RETURN,
   as 7 pernas passam por esse caminho SEMPRE — não é canto raro. Também
   corrigido (P2-1, mfc-rev-2): a mensagem do alerta agora distingue
   "perna(s) faltando" (causa = rejeição confirmada) de "perna(s) INCERTA"
   (causa = falta de confirmação, pode estar tudo aberto) — o texto
   genérico antigo sugeria a ação errada (abrir na mão) exatamente no caso
   onde isso mais arriscaria dobrar uma perna. **MFC18-02 (Codex) — ainda
   NÃO reconciliado entre os dois revisores**: se a
   confirmação por símbolo+magic poderia, em teoria, atribuir a uma perna
   uma posição de OUTRA chamada concorrente; mfc-rev-2 investigou e
   descartou como inalcançável (idempotência + colisão netting já cobrem o
   caso), mas os dois leram o mesmo código e chegaram a conclusões
   diferentes sobre alcançabilidade — se reabrir, não é achado repetido,
   é continuação de uma discordância real.

5. **Sufixo de símbolo do broker.** Toda fronteira com o MT5 passa por
   `to_broker_symbol()` / `from_broker_symbol()` em `web/css_service.py`.
   Símbolo literal (`"EURUSD"`) cruzando pro MT5 faz o track record cair
   silenciosamente em histórico simulado — falha sem barulho, P1 no mínimo.

6. **`public/` é artefato gerado.** Fonte é `web/static/`; `public/` sai de
   `scripts/build_firebase_bundle.py`. Edição manual em `public/` é achado.

7. **`agents/` é camada pura.** Sem I/O, sem MT5, sem FastAPI. Cada estágio
   recebe séries numpy e devolve dict. Importar MT5 ou tocar disco dentro de
   `agents/` (exceto `portfolio_executor.py`, camada de execução) quebra o
   contrato.

8. **Paridade dos dois chamadores.** `web/css_service.py` e
   `daily_css_routine.py` precisam se comportar de forma idêntica ao rodar o
   pipeline de `agents/`. Divergência entre o que o dashboard mostra e o que o
   relatório diário escreve é achado.

9. **Skills espelham o código.** `.agents/skills/css-macro-analyzer/SKILL.md`
   e `.agents/skills/css-operational-analyzer/SKILL.md` reescrevem em
   português as regras de `agents/macro_analyzer.py` e
   `agents/operational_analyzer.py`. Nenhum código as importa — se a lógica
   mudou e a skill não, aponte.

10. **Journal indexado.** Todo `log_conhecimento/YYYYMMDD.md` novo exige linha
    correspondente em `log_conhecimento/INDEX.md`.

11. **Bug reportado exige spec de regressão.** Correção de bug sem teste em
    `tests/` que falhe antes e passe depois é achado de cobertura.

## Já verificado — não é achado

- **`ALL_28_PAIRS`, `CURRENCIES`, `CCY_COLORS` não estão mais duplicadas.**
  `CLAUDE.md` ainda instrui "manter em sincronia entre `web/css_service.py` e
  `daily_css_routine.py`" — isso está desatualizado. `daily_css_routine.py:22`
  hoje **importa** as três de `web.css_service`. Não aponte isso como
  divergência; se notar, é o `CLAUDE.md` que precisa de correção, não o
  código.

- **F-02 (marcador `_UNRESOLVED_FAMILY_MARKER` teoricamente colidir com um
  símbolo real do broker) — decisão do usuário, 2026-08-27: manter como
  está.** Consertar de verdade (`to_broker_symbol()` devolver `None` em vez
  de string) tocaria 10+ pontos de chamada em 4 arquivos, a maioria fora do
  caminho crítico de execução. Caminho dormant na instalação atual
  (`CSS_MT5_SYMBOL_SUFFIX` já configurado explicitamente). Ver comentário em
  `web/css_service.py`, acima de `_UNRESOLVED_FAMILY_MARKER`. Não reabra sem
  evidência nova (uma corretora real com um símbolo colidindo de verdade).

- **F-03 (escolha heurística entre múltiplas famílias de símbolo válidas,
  ex.: `m` e `pro`) — decisão do usuário, 2026-08-27: manter a heurística
  atual (sufixo mais curto).** Mesma situação do F-02: caminho automático
  dormant nesta instalação. Ver comentário em
  `_symbol_family_is_consistent()` em `web/css_service.py`. Não reabra sem
  evidência nova.

- **F-04 (corrida residual entre processos na detecção de família de
  símbolo, cold start simultâneo sem lock de arquivo) — risco medido e
  aceito desde a rodada 3 do achado 1**, não decisão desta rodada. Ver
  comentário em `_detect_mt5_symbol_family()` em `web/css_service.py`.
