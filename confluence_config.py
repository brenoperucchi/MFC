"""
Config de FRONTEIRA pra qual motor de confluência decide `trade_bias` —
FORA de `agents/` de propósito.

Achado MFC74-01 (herdr-review mfc-74) ficou PARCIAL na verificação mfc-75:
mfc-rev leu a invariante 7 do `.herdr/reviewer.md` como "a camada `agents/`
é pura, sem I/O, sem exceção pra um helper de configuração" — mesmo um
`resolve_confluence_engine()` claramente documentado como "não é estágio do
pipeline" ainda contava como I/O DENTRO do pacote `agents/`. Este módulo
existe só pra fechar essa leitura: o resolver (e a leitura de
`os.environ`) mora aqui, fora de `agents/` de verdade, não só
"conceitualmente separado" dentro do mesmo arquivo.

`agents/confluence_engine.py` importa só as DUAS CONSTANTES de nome
(`CONFLUENCE_ENGINE_3TF`/`_5TF`, strings puras, sem I/O) pra validar o
argumento `engine=` do seu dispatcher — nunca importa `resolve_confluence_engine`
nem lê `os.environ` diretamente. Cada consumidor real
(`web/css_service.py::update_data()`, `scripts/backtest_canonical.py::run()`,
`scripts/measure_composition_effect.py`, `scripts/measure_spread_per_pair.py`)
chama `resolve_confluence_engine()` daqui, UMA VEZ por rodada/snapshot, e
passa o resultado como `engine=` pra `agents.confluence_engine.evaluate_currency_confluence()`.
"""

import os

CSS_CONFLUENCE_ENGINE_ENV_VAR = "CSS_CONFLUENCE_ENGINE"
CONFLUENCE_ENGINE_3TF = "3tf"
CONFLUENCE_ENGINE_5TF = "5tf"
DEFAULT_CONFLUENCE_ENGINE = CONFLUENCE_ENGINE_3TF
VALID_CONFLUENCE_ENGINES = (CONFLUENCE_ENGINE_3TF, CONFLUENCE_ENGINE_5TF)


def resolve_confluence_engine():
    """Lê CSS_CONFLUENCE_ENGINE do ambiente do processo. Troca só qual
    motor decide `trade_bias` — nenhuma das duas opções manda ordem
    sozinha nem é "mais perigosa" que a outra (diferente de
    CSS_MIN_MARGIN_FREE e companhia em agents/portfolio_executor.py).

    - AUSENTE (chave não existe em os.environ): cai no default
      documentado (3tf, compatibilidade deliberada com o comportamento
      pré-Port-A).
    - PRESENTE e EXPLICITAMENTE INVÁLIDO (typo, ou string vazia): RECUSA
      (`ValueError`), nunca avisa-e-cai-no-default — achado MFC74-02,
      herdr-ask mfc-17: a invariante deste projeto pra `.env` é "usado ≠
      escrito", não "qual lado é mais perigoso", e essa regra se aplica
      aqui mesmo sem um motor objetivamente mais arriscado que o outro.
    - Nunca imprime nada — quem chama decide como reportar o erro (ex.:
      web/css_service.py serve cache/fallback em vez de derrubar o
      processo; scripts/backtest_canonical.py aborta antes de tocar MT5).

    Chame isto UMA VEZ por rodada/snapshot, nunca por avaliação individual
    dentro de um loop — reler por chamada reproduziria o mesmo padrão do
    achado MFC74-04 (proveniência/decisão como releituras independentes em
    vez do mesmo valor resolvido)."""
    if CSS_CONFLUENCE_ENGINE_ENV_VAR not in os.environ:
        return DEFAULT_CONFLUENCE_ENGINE
    raw_original = os.environ[CSS_CONFLUENCE_ENGINE_ENV_VAR]
    raw = raw_original.strip().lower()
    if raw not in VALID_CONFLUENCE_ENGINES:
        raise ValueError(
            f"{CSS_CONFLUENCE_ENGINE_ENV_VAR}={raw_original!r} inválido — use "
            f"{CONFLUENCE_ENGINE_3TF!r} ou {CONFLUENCE_ENGINE_5TF!r}, ou remova "
            f"a variável pra usar o default ({DEFAULT_CONFLUENCE_ENGINE!r})."
        )
    return raw
