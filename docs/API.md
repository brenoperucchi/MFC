# 🌐 Referência Completa da API REST (FastAPI)

A plataforma disponibiliza uma API REST síncrona/assíncrona de alta performance rodando por padrão em `http://localhost:8050`.

---

## 1. Endpoints de Dados do CSS e Indicadores

### `GET /api/css/all`
Retorna todos os dados calculados do CSS para as 8 moedas em todos os 5 timeframes, além das séries históricas dos 28 pares cambiais.

* **Cache:** recálculo limitado a uma vez a cada 3 segundos por modo
  (`standard`/`gauss`); respostas de cache/fallback carregam
  `mt5_connected: false`.
* **Exemplo de Resposta:**
```json
{
  "timestamp": "2026-08-19 19:15:00",
  "mt5_connected": true,
  "engine_mode": "standard",
  "confluence_engine": "3tf",
  "currencies": [
    {
      "symbol": "USD",
      "h1_score": -0.15,
      "h4_score": -0.20,
      "d1_score": -0.27,
      "total_score": -1.42,
      "trade_bias": "VENDA"
    }
  ],
  "charts": {
    "MN1": {
      "times": ["2026-08-18 00:00"],
      "series": {"USD": [-0.15], "EUR": [0.10]}
    }
  },
  "pair_charts": {
    "MN1": {
      "EURUSD": [0.25],
      "GBPUSD": [0.40]
    }
  },
  "pairs": [],
  "crossovers": {},
  "colors": {},
  "flags": {}
}
```

**`confluence_engine` (achado MFC74-03, herdr-review mfc-74) muda a escala e a
semântica de `total_score` de cada item em `currencies`** — leia esse campo
antes de interpretar `total_score`:
- `"3tf"` (default desde 2026-09-05, ver `CLAUDE.md`/`.env.example`
  `CSS_CONFLUENCE_ENGINE`): `total_score` é o score BRUTO
  `D1*0.40 + H4*0.35 + H1*0.25`, sem normalização — mesma escala aproximada
  de um score de tríade isolado.
- `"5tf"` (Port A, matriz institucional com soberania macro MN1/W1):
  `total_score` é o score normalizado `weighted_score / 13.5 * 10`, sem
  clamp nesta etapa — escala aproximada -10 a +10.

Um consumidor que compare `total_score` entre duas chamadas precisa checar
se `confluence_engine` mudou entre elas antes de tratar a diferença como
sinal — os dois motores não estão na mesma escala. Esse campo não deve ser
comparado diretamente ao `total_score` dos itens de `pairs`, que continua
sendo o score do ranking de pares em escala própria (não afetado por
`CSS_CONFLUENCE_ENGINE`).

---

### `GET /api/css/triad`
Retorna a análise da Tríade Institucional (Região, Ciclo Atual, Ciclo Devendo, Angulação e Score) processada pelos agentes para todas as 8 moedas.

* **Exemplo de Resposta:**
```json
{
  "timestamp": "2026-08-19 19:15:00",
  "triad": {
    "USD": {
      "MN1": {
        "region": "ZONA DE PARADA INFERIOR (-0.20)",
        "current_cycle": "BAIXA (CUMPRIDO)",
        "owing_cycle": "FORÇA (DEVENDO RETORNO AO 0 / +0.20)",
        "slope": "FLAT / VIRANDO PARA CIMA",
        "current_score": -0.27
      }
    }
  }
}
```

---

### `GET /api/pairs`
Retorna o Radar Institucional dos 28 pares cambiais ranqueados por Potencial Cíclico ($\text{Base Power} - \text{Quote Power}$).

* **Exemplo de Resposta:**
```json
{
  "timestamp": "2026-08-19 19:15:00",
  "pairs": [
    {
      "pair": "EURUSD",
      "base": "EUR",
      "quote": "USD",
      "total_score": -0.35,
      "recommendation": "VENDA (SELL)",
      "conviction": "ALTA",
      "thesis": "Base (EUR) em Exaustão de Topo vs Cotada (USD) em Exaustão de Fundo."
    }
  ]
}
```

---

## 2. Endpoints do Track Record e Simulação Multi-Portfólio

### `GET /api/track-record/summary`
Retorna o sumário quantitativo auditado, curva de capital global/filtrada e histórico de sessões com MAE/MFE e curvas horárias de CSS.

* **Query Parameters:**
  * `currency` *(opcional, default="ALL")*: `ALL`, `USD`, `EUR`, `GBP`, `CHF`, `JPY`, `AUD`, `CAD`, `NZD`.
* **Exemplo de Requisição:** `GET /api/track-record/summary?currency=JPY`
* **Exemplo de Resposta:**
```json
{
  "last_update": "2026-08-19 19:00:49",
  "filter": "JPY",
  "summary": {
    "total_pnl_usd": 51.56,
    "total_pips": 792.0,
    "total_sessions": 11,
    "win_rate": 72.7,
    "profit_factor": 2.15
  },
  "equity_curve": [...],
  "sessions": [...]
}
```

---

### `POST /api/track-record/recalculate`
Força o reprocessamento de todo o histórico do MetaTrader 5 (45 dias de dados M1/H1/H4).

* **Query Parameters:**
  * `days` *(opcional, default=45)*: Número de dias para trás a auditar.
* **Exemplo de Resposta:**
```json
{
  "success": true,
  "summary": {
    "total_pnl_usd": 143.23,
    "win_rate": 61.5,
    "profit_factor": 1.57
  }
}
```

---

## 3. Endpoints de Relatórios Diários e Diagnóstico

### `GET /api/history/dates`
Retorna a lista de todas as datas disponíveis com relatórios salvos no diretório `reports/` gerados pela rotina das 21h00.

* **Exemplo de Resposta:**
```json
{
  "dates": ["20260818", "20260817", "20260816", "20260815", "20260814", "20260813"]
}
```

---

### `GET /api/history/{date_str}`
Retorna o conteúdo Markdown compilado da análise diária daquela data (ex: `/api/history/20260818`).

---

### `POST /api/refresh`
Força a leitura e recálculo instantâneo de dados no MT5.

---

### `GET /api/health`
Retorna o status de conexão com o MetaTrader 5 e a integridade da plataforma.
