# 🗄️ Arquitetura de Dados, Fluxo de Informação e Mapeamento de Caches

Este documento descreve as estruturas de dados, schemas de armazenamento, persistência JSON, camadas de cache em memória e o fluxo de dados entre o **MetaTrader 5 (MT5)**, os **Agentes Especialistas**, o **Serviço FastAPI** e a **Interface Web**.

---

## 1. Diagrama de Fluxo de Dados Global

```
┌────────────────────────────────────────────────────────────────────────────────┐
│                           METATRADER 5 TERMINAL                                │
│                     (28 Pares Forex @ MN1, W1, D1, H4, H1)                     │
└──────────────────────────────────────┬─────────────────────────────────────────┘
                                       │ MetaTrader5 Python API (copy_rates)
                                       ▼
┌────────────────────────────────────────────────────────────────────────────────┐
│                         CAMADA DE SERVIÇO (BACKEND)                            │
│  ┌─────────────────────────────────┐   ┌────────────────────────────────────┐  │
│  │   web/css_service.py            │   │   web/history_tracker.py           │  │
│  │   - Cache em Memória (TTL 60s)  │   │   - Motor de Backtest 21h-08h      │  │
│  │   - Algoritmo LWMA 21 + ATR     │   │   - Cálculo Intraday MAE / MFE     │  │
│  │   - Matriz dos 28 Pares         │   │   - Evolução CSS H1/H4             │  │
│  └────────────────┬────────────────┘   └─────────────────┬──────────────────┘  │
└───────────────────┼──────────────────────────────────────┼─────────────────────┘
                    │                                      │
                    ▼                                      ▼
┌──────────────────────────────────────┐   ┌─────────────────────────────────────┐
│      AGENTES ESPECIALISTAS (AI)      │   │     PERSISTÊNCIA EM DISCO (DATA)    │
│  - agents/triad_analyzer.py          │   │  - data/simulated_trades_history.json│
│  - agents/macro_analyzer.py          │   │  - YYYYMMDD/analise_diaria.md       │
│  - agents/operational_analyzer.py    │   │  - log_conhecimento/                │
│  - agents/confluence_engine.py       │   └─────────────────────────────────────┘
└───────────────────┬──────────────────┘
                    │ REST JSON Endpoints
                    ▼
┌────────────────────────────────────────────────────────────────────────────────┐
│                      INTERFACE WEB INTERATIVA (FRONTEND)                       │
│  - Dashboard Multi-Timeframe com Curvas CSS (Canvas 2D)                        │
│  - Radar de Confluência & Ranking dos 28 Pares                                 │
│  - Modal Matriz de Pares Isolados por Moeda                                    │
│  - Terminal Agregador de Track Record com MAE/MFE e Curvas Intraday            │
└────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Estrutura e Schemas de Armazenamento

### 2.1 Schema: `data/simulated_trades_history.json`

O arquivo principal de auditoria do Track Record armazena a árvore completa de sessões diárias, cestas operadas e os 7 pares individuais:

```json
{
  "last_update": "2026-08-19 19:00:49",
  "summary": {
    "total_pnl_usd": 143.23,
    "total_pips": 2126.4,
    "total_sessions": 57,
    "active_sessions": 52,
    "neutral_sessions": 5,
    "total_portfolios": 110,
    "win_sessions": 32,
    "loss_sessions": 20,
    "win_rate": 61.5,
    "profit_factor": 1.57,
    "best_currency": "JPY",
    "worst_currency": "GBP",
    "currency_pnl": {
      "USD": -5.92,
      "EUR": 26.71,
      "GBP": -7.86,
      "CHF": 12.23,
      "JPY": 51.56,
      "AUD": 27.02,
      "CAD": 32.99,
      "NZD": 6.50
    }
  },
  "equity_curve": [
    { "date": "Início", "equity": 0.0, "pnl": 0.0 },
    { "date": "2026-06-25", "equity": 18.40, "pnl": 18.40 }
  ],
  "sessions": [
    {
      "date": "2026-08-18",
      "entry_time_br": "2026-08-18 21:00",
      "exit_time_br": "2026-08-19 08:00",
      "status": "WIN",
      "status_label": "✅ GANHO",
      "portfolios_count": 2,
      "total_pnl_usd": 35.80,
      "total_pips": 240.5,
      "mfe_usd": 48.20,
      "mae_usd": -4.10,
      "equity_after": 143.23,
      "intraday_hours": ["21h", "22h", "23h", "00h", "01h", "02h", "03h", "04h", "05h", "06h", "07h", "08h"],
      "intraday_pnl_curve": [0.0, 5.2, 12.8, 18.4, 25.0, 31.2, 48.2, 42.0, 38.5, 36.0, 35.8],
      "portfolios": [
        {
          "currency": "JPY",
          "flag": "🇯🇵",
          "color": "#9C27B0",
          "bias": "BUY",
          "bias_label": "Cesta JPY (COMPRA)",
          "reason": "Confluência de Alta em 5/5 Timeframes (Devendo Força +0.20)",
          "leds": { "MN1": "green", "W1": "green", "D1": "green", "H4": "green", "H1": "green" },
          "pnl_usd": 28.50,
          "pips": 180.0,
          "mfe_usd": 32.40,
          "mae_usd": -1.20,
          "intraday_pnl": [0.0, 4.0, 9.5, 14.0, 20.1, 25.0, 32.4, 30.0, 29.1, 28.5],
          "css_h1_curve": [-0.18, -0.12, -0.05, 0.02, 0.08, 0.14, 0.22, 0.25],
          "css_h4_curve": [-0.10, -0.08, -0.05, -0.02, 0.03, 0.07, 0.11, 0.15],
          "status": "WIN",
          "pairs": [
            {
              "pair": "USDJPY",
              "base": "USD",
              "quote": "JPY",
              "action": "SELL",
              "lot": 0.01,
              "entry_price": 157.587,
              "exit_price": 156.827,
              "pnl_usd": 4.85,
              "pips": 76.0,
              "mfe_usd": 9.39,
              "mae_usd": 0.0,
              "hourly_pnl": [0.0, 1.2, 2.5, 4.8, 6.2, 8.1, 9.39, 7.5, 6.0, 4.85],
              "status": "WIN"
            }
          ]
        }
      ]
    }
  ]
}
```

---

## 3. Estrutura de Diretórios de Relatórios e Conhecimento

* `reports/YYYYMMDD/analise_diaria.md`: Relatórios diários e gráficos gerados pela rotina automática das 21h00 BRT (`daily_css_routine.py`). Contém os scores de cada timeframe, os 8 dashboards por moeda, a Tríade Analítica das 8 moedas e os 28 pares ranqueados.
* `log_conhecimento/YYYYMMDD.md`: Diário de aprendizado do sistema institucional, registrando padrões de mercado observados, falsos rompimentos no box e divergências contra o Mensal.

---

## 4. Camada de Cache em Memória (`web/css_service.py`)

Para manter a interface ultrarrápida (resposta $< 15\text{ms}$) e evitar sobrecarregar o MetaTrader 5:

```python
_css_cache = {
    "data": None,       # Payload completo com 8 moedas, 5 TFs e 28 pares
    "timestamp": 0.0    # Unix timestamp da última leitura
}
CACHE_TTL = 60.0        # Validade de 60 segundos por ciclo
```

* **Invalidação Automática:** A cada 60s, a chamada à rota `/api/css/all` refaz a extração dos 28 pares e 5 timeframes no MT5 em lote (`copy_rates_from_pos`).
* **Fallback Inteligente:** Se o MT5 estiver fechado ou offline, o sistema gera séries harmônicas estáticas para manter a interface web 100% funcional.
