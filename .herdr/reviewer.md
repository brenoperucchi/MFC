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
   CSS_CATASTROPHIC_SL_PIPS, os dois CSS_AMBIGUOUS_CONFIRM_*) → identidade da
   conta (`CSS_MT5_EXPECTED_LOGIN`) → trava de demo (`CSS_LIVE_TRADING`) →
   idempotência → tetos de exposição → colisão de símbolo em conta netting →
   preflight de símbolo/tick (tudo-ou-nada) → stop-loss catastrófico do lado
   do broker. Exposição e colisão netting são as duas exceções documentadas:
   ambas são recusas puras sobre o mesmo snapshot de `open_magics`, sem
   efeito colateral, então a ordem relativa ENTRE ESSAS DUAS não muda a
   decisão de abrir/recusar — só qual mensagem de erro sai quando as duas
   se aplicariam (ver `CLAUDE.md`, seção "Live MT5 execution"). Reordenar
   qualquer OUTRO par de gates, pular um gate ou torná-lo condicional
   continua P0. "Ausente" e "inválido" em `.env` NÃO são o mesmo caso
   (decisão do usuário, F06-2, 2026-08-27 — ver `CLAUDE.md`, seção "Live
   MT5 execution", pra não reabrir isto): gates de identidade/permissão
   (`CSS_MT5_EXPECTED_LOGIN`, `CSS_LIVE_TRADING`) não têm default seguro —
   ausência é ambígua e tem que recusar. As cinco variáveis de
   `check_execution_config()` são margens de segurança tunáveis com default
   documentado e seguro (150 pips, 0.01 lote, 8 cestas, 3 tentativas, 1.0s)
   — ausência usa o default e ABRE normalmente; só um valor EXPLICITAMENTE
   fornecido e inválido recusa. Isso não é fail-open: é a mesma regra
   "usado ≠ escrito" aplicada só a quem escreveu algo.

3. **Kill switch é assimétrico.** `data/CSS_KILL.flag` bloqueia abrir cesta
   nova. Nunca bloqueia fechamento — reduzir risco sempre prossegue. Qualquer
   caminho que faça o flag impedir um fechamento é P0.

4. **Sufixo de símbolo do broker.** Toda fronteira com o MT5 passa por
   `to_broker_symbol()` / `from_broker_symbol()` em `web/css_service.py`.
   Símbolo literal (`"EURUSD"`) cruzando pro MT5 faz o track record cair
   silenciosamente em histórico simulado — falha sem barulho, P1 no mínimo.

5. **`public/` é artefato gerado.** Fonte é `web/static/`; `public/` sai de
   `scripts/build_firebase_bundle.py`. Edição manual em `public/` é achado.

6. **`agents/` é camada pura.** Sem I/O, sem MT5, sem FastAPI. Cada estágio
   recebe séries numpy e devolve dict. Importar MT5 ou tocar disco dentro de
   `agents/` (exceto `portfolio_executor.py`, camada de execução) quebra o
   contrato.

7. **Paridade dos dois chamadores.** `web/css_service.py` e
   `daily_css_routine.py` precisam se comportar de forma idêntica ao rodar o
   pipeline de `agents/`. Divergência entre o que o dashboard mostra e o que o
   relatório diário escreve é achado.

8. **Skills espelham o código.** `.agents/skills/css-macro-analyzer/SKILL.md`
   e `.agents/skills/css-operational-analyzer/SKILL.md` reescrevem em
   português as regras de `agents/macro_analyzer.py` e
   `agents/operational_analyzer.py`. Nenhum código as importa — se a lógica
   mudou e a skill não, aponte.

9. **Journal indexado.** Todo `log_conhecimento/YYYYMMDD.md` novo exige linha
   correspondente em `log_conhecimento/INDEX.md`.

10. **Bug reportado exige spec de regressão.** Correção de bug sem teste em
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
