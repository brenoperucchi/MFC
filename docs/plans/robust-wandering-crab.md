# ACHADO HISTÓRICO — snapshot pré-Port A (2026-08-29)

Este documento registra o diagnóstico do motor antes da implementação do Port
A `544d660`. Ele não descreve o comportamento atual e não deve ser usado como
especificação vigente sem ler `docs/plans/port-upstream-institutional-matrix.md`.

## Descoberta do agente 2 (pipeline Python completo)

`evaluate_currency_confluence()` (agents/confluence_engine.py), que é a função
que o backtest_canonical.py e o sistema ao vivo realmente usam pra decidir
`trade_bias` (COMPRA/VENDA/NEUTRO), **NÃO usa `macro_power` nem `op_power`**:

- `score_3tf = d1_curr*0.40 + h4_curr*0.35 + h1_curr*0.25` — calculado direto
  dos scores BRUTOS de D1/H4/H1 (`d1_s[-1]`, `h4_s[-1]`, `h1_s[-1]`), não dos
  valores compostos `macro_power`/`op_power` que `macro_analyzer.py`/
  `operational_analyzer.py` calculam.
- `trade_bias`: `score_3tf >= 0.10` → COMPRA, `<= -0.10` → VENDA, senão NEUTRO.
- `evaluate_28_pairs_confluence` (ranking dos 28 pares) também usa
  `power_3tf = D1*0.40+H4*0.35+H1*0.25` — os mesmos brutos, não macro/op power.

**`macro_power`/`op_power` (os campos que a "inversão de tese" do item 5
muda) são só DIAGNÓSTICO/TEXTO** — aparecem no dict retornado
(`macro`/`operational` sub-dicts) mas não entram em `score_3tf`,
`trade_bias`, nem `cyclic_score`. Mudar a tese hoje muda o TEXTO exibido
("ALTA EM EXAUSTÃO" vs o que a tese invertida diria) — não muda NENHUMA
decisão de abrir/recusar cesta, nem o PnL de nenhum backtest.

## Implicação

Um backtest comparando as duas teses (atual vs. invertida) — seja via
`backtest_canonical.py` ou via um EA no Strategy Tester — **produziria PnL
IDÊNTICO nas duas rodadas**, porque nenhum dos dois caminhos de decisão real
consulta `macro_power`/`op_power`. Todo o esforço de portar a lógica pra
MQL5 e rodar no Strategy Tester não responderia a pergunta "qual tese é
melhor" — porque hoje NENHUMA tese afeta o resultado.

## Pausado para perguntar ao usuário antes de continuar

Ver próxima mensagem — preciso saber como o usuário quer proceder antes de
montar mais plano de infraestrutura de backtest.
