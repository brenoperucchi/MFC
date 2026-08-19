# 🏛️ Especificação Técnica do Sistema (System Specification)

## 1. Visão Geral do Sistema

O **CSS Institutional Platform & Multi-Portfolio Engine** é uma plataforma institucional de análise quantitativa e execução de cestas cambiais descorrelacionadas no mercado de câmbio (Forex).

O sistema resolve o principal problema dos indicadores de força relativa convencionais: **a repintura de dados, a falta de sincronização multi-timeframe e a compra em topos/venda em fundos provocada pela confusão entre momentum e exaustão cíclica**.

```
┌────────────────────────────────────────────────────────────────────────────────┐
│                   ECOSSISTEMA CSS INSTITUTIONAL & MULTI-PORTFOLIO              │
├────────────────────────────────────────────────────────────────────────────────┤
│                                                                                │
│  [MetaTrader 5] ──► [CSS Engine LWMA 21] ──► [Tríade Analítica dos 5 TFs]      │
│                            │                                                   │
│                            ├──────────────► [Radar dos 28 Pares Cíclicos]     │
│                            │                                                   │
│                            └──────────────► [Terminal de Auditoria 21h-08h]    │
│                                                (MAE / MFE / Intraday)          │
│                                                                                │
└────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Componentes e Responsabilidades

### 2.1 Camada de Inteligência e Agentes (`agents/`)

* **`agents/triad_analyzer.py`:** Módulo mestre da Tríade. Avalia a posição geométrica do score em relação às Zonas de Parada ($\pm 0.20$), o ciclo atual cumprido e o ciclo devendo.
* **`agents/macro_analyzer.py`:** Especialista em regime macroinstitucional ($\text{MN1}, \text{W1}, \text{D1}$). Define a direção estratégica e identifica topos/fundos estruturais.
* **`agents/operational_analyzer.py`:** Especialista em timing fino ($\text{H4}, \text{H1}$). Monitora retestes da Linha Zero ($0.00$), rompimentos de box e gatilhos de reversão.
* **`agents/confluence_engine.py`:** Motor de confluência multi-timeframe. Converte as análises dos agentes em pontuação de **Poder Cíclico** para os 28 pares e define o ranking de oportunidades.

---

### 2.2 Camada de Serviços e Backend (`web/`)

* **`web/css_service.py`:**
  * Comunicação direta com a DLL/API do MetaTrader 5 em Python (`MetaTrader5`).
  * Implementação exata do algoritmo **LWMA 21 + SMA 100 ATR** idêntico ao indicador oficial MQL5 (`css.mql5`).
  * Cache em memória com TTL de 60 segundos e suporte a fallback de dados offline.
  * Extração e interpolação de séries históricas dos 28 pares para visualização em matriz.
* **`web/history_tracker.py`:**
  * Motor de simulação e track record histórico da estratégia institucional das **21h00 às 08h00 Brasília** (03h00 às 14h00 servidor MT5).
  * Avalia moedas com confluência direcional em $\ge 4$ timeframes simultâneos.
  * Executa cestas de 7 pares em lote 0.01 por par.
  * Calcula hora a hora (12 etapas) a flutuação do capital, **MFE** (Pico de Lucro), **MAE** (Rebaixamento Máximo) e evolução do CSS **H1/H4**.
* **`web/server.py`:**
  * Servidor web assíncrono construído em FastAPI e Uvicorn na porta `8050`.
  * Roteamento de dados estáticos, endpoints JSON REST e integração com o motor de histórico.

---

### 2.3 Interface Web Institucional (`web/static/`)

* **`index.html`:** Layout responsivo em Dark Mode profissional, com cartões de Tríade, matriz de timeframes, radar de operações, modal da Matriz de Pares e modal do Track Record com gráficos duplos.
* **`styles.css`:** Design system moderno com variáveis CSS, tipografia Google Fonts (*Inter*, *Outfit*, *JetBrains Mono*), efeito glassmorphism e micro-animações.
* **`app.js`:** Motor frontend de renderização em Canvas 2D de alta taxa de quadros (HiDPI / Retina):
  * Gráfico Multi-Timeframe com 8 linhas coloridas simultâneas.
  * Modal da Matriz de 7 Pares com badges flutuantes anti-colisão e linhas-guia curvas Bezier.
  * Gráfico duplo de Curva de Capital (Global / Intraday com MAE/MFE) e Ciclo CSS H1/H4.
  * Tabela em cascata com acordeão e auditoria dos pares individuais.

---

## 3. Protocolo de Operação de Cestas (21h00 ➔ 08h00 BRT)

```
[21h00 BRT] (Abertura Ásia / 03h00 MT5)
    │
    ├─► 1. Extração do CSS nos 5 Timeframes (MN1, W1, D1, H4, H1)
    │
    ├─► 2. Filtro Rígido de Confluência: Moeda possui >= 4 TFs apontando na mesma direção?
    │       ├─► NÃO ──► Sessão Neutra (0 trades / Preservação Total de Capital)
    │       └─► SIM ──► Qualifica a Cesta da Moeda (BUY ou SELL)
    │
    ├─► 3. Abertura Simultânea dos 7 Pares da Moeda (Lote 0.01 por par)
    │
    ├─► 4. Monitoramento Intraday Hora a Hora (Registro de MAE, MFE e CSS H1/H4)
    │
[08h00 BRT] (Pré-Londres/NY / 14h00 MT5)
    │
    └─► 5. Encerramento a Mercado de Todos os Pares e Apuração do PnL em USD/Pips
```
