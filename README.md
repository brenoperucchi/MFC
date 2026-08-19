# 📊 CSS Institutional — Multi-Timeframe Currency Strength Platform & Multi-Portfolio Engine

> Plataforma institucional de análise de força relativa cambial (**CSS - Currency Slope Strength**), confluência multi-timeframe ($\text{MN1}, \text{W1}, \text{D1}, \text{H4}, \text{H1}$), radar cíclico de 28 pares e simulador/auditor de portfólios descorrelacionados com cálculo de **MAE (Maximum Adverse Excursion)** e **MFE (Maximum Favorable Excursion)**.

---

## 🌟 Principais Recursos da Plataforma

### 1. 📈 Dashboard Multi-Timeframe Interativo
* Renderização em tempo real (Canvas 2D de alta definição) das 8 moedas mundiais (`USD`, `EUR`, `GBP`, `CHF`, `JPY`, `AUD`, `CAD`, `NZD`).
* Alternância instantânea entre os timeframes: **Mensal (MN1)**, **Semanal (W1)**, **Diário (D1)**, **4 Horas (H4)** e **1 Hora (H1)**.
* Cálculo rigoroso **LWMA 21 (Non-Repainting) + SMA 100 ATR** calibrado com a fórmula exata do código MetaTrader 5 (`css.mql5`).

### 2. 🏛️ Tríade Analítica Institucional
* **Região:** Zona de Parada Superior ($+0.20$), Box de Equilíbrio ($-0.20$ a $+0.20$) e Zona de Parada Inferior ($-0.20$).
* **Ciclo Atual:** Identificação de ciclos de alta/baixa cumpridos e fases de rompimento.
* **Ciclo Devendo:** Determinação do fluxo futuro institucional (evitando compra em topos ou venda em fundos).
* **Score & Angulação:** Força direcional e velocidade da curva.

### 3. ⚡ Radar Cíclico dos 28 Pares Cambiais
* Classificação dos 28 pares por **Potencial Cíclico** ($\text{Base Power} - \text{Quote Power}$).
* Emissão de sinais institucionais: `COMPRA FORTE`, `VENDA FORTE`, `COMPRA`, `VENDA` e `NEUTRO (BOX)`.
* Alertas automáticos de exaustão de ciclo e reversão de fluxo.

### 4. 🔲 Matriz de Pares Isolados por Moeda
* Modal interativo que isola uma moeda base (ex: **USD**) e desenha as 7 linhas dos pares contrapostos (`EURUSD`, `GBPUSD`, `USDJPY`, `USDCHF`, `AUDUSD`, `USDCAD`, `NZDUSD`).
* Sistema de **Badges Flutuantes com Algoritmo Anti-Colisão** e linhas-guia curvas Bezier conectadas ao final das séries.

### 5. 🏆 Terminal de Track Record & Auditoria de Portfólios (21h ➔ 08h Brasília)
* Backtest quantitativo completo conectado aos dados históricos do MT5:
  * **Regra de Entrada:** Apenas moedas com confluência direcional em $\ge 4$ timeframes simultâneos.
  * **Execução:** Cestas de 7 pares em lote 0.01 por par (abertura às 21h00 BRT e encerramento às 08h00 BRT).
  * **Filtro por Moeda:** Seletor de pílulas para isolar a performance de moedas específicas (`JPY`, `AUD`, `CAD`, etc.) ou visualizar o consolidado geral.
  * **Curva Intraday de Capital (21h-08h):** Gráfico hora a hora da oscilação do saldo com pontos de **MFE (Pico de Lucro 🟢)** e **MAE (Drawdown Máximo 🔴)**.
  * **Evolução do Ciclo CSS (H1 & H4):** Gráfico que acompanha a trajetória da moeda contra as linhas de $+0.20$, $0.00$ e $-0.20$ durante a operação.
  * **Tabela em Cascata:** Auditoria completa dos preços de entrada, saída, MFE, MAE e PnL de cada um dos 7 pares.

---

## 🚀 Como Executar o Projeto

### Pré-requisitos
* **Python 3.10+** instalado.
* **MetaTrader 5** instalado e logado na sua corretora (ex: IC Markets, Tickmill, etc.).

### Instalação das Dependências
```bash
pip install fastapi uvicorn MetaTrader5 pandas numpy
```

### Iniciar a Plataforma Web
Basta executar o script de inicialização rápida:
```bash
start_web.bat
```
Ou via linha de comando:
```bash
python web/server.py
```
Acesse no seu navegador: **`http://localhost:8050`**

### Executar a Rotina Diária das 21h00 BRT
Para gerar o relatório de análise diária e os arquivos de snapshot:
```bash
run_daily_css.bat
```
Ou:
```bash
python daily_css_routine.py
```

---

## 📁 Estrutura do Repositório

```
MFC/
├── agents/                           # Camada de Inteligência e Agentes Especialistas
│   ├── triad_analyzer.py             # Tríade Analítica: Região, Ciclos e Angulação
│   ├── macro_analyzer.py             # Regime Macroinstitucional (MN1, W1, D1)
│   ├── operational_analyzer.py       # Timing Operacional e Gatilhos (H4, H1)
│   └── confluence_engine.py          # Confluência Multi-TF e Ranking dos 28 Pares
├── data/                             # Armazenamento e Históricos
│   └── simulated_trades_history.json # Base de dados das sessões, portfólios, MAE/MFE
├── docs/                             # Documentação Técnica e Especificações
│   ├── SPEC.md                       # Especificação do Sistema e Arquitetura
│   ├── MATHEMATICAL_MODELS.md        # Fórmulas Matemáticas (LWMA, ATR, MAE, MFE)
│   ├── DATABASE_DATA_FLOW.md         # Mapeamento de Schemas, Caches e Fluxos
│   └── API.md                        # Referência Completa dos Endpoints REST
├── reports/                          # Relatórios e Imagens Diárias Organizadas
│   ├── 20260818/                     # Snapshots dos Dashboards e analise_diaria.md
│   └── ...                           # Histórico diário por data (YYYYMMDD)
├── log_conhecimento/                 # Diário Institucional e Aprendizados
├── mt5/                              # Código-Fonte MQL5 Original
│   └── css.mql5                      # Indicador oficial MetaTrader 5 (LWMA 21)
├── web/                              # Servidor Web & Interface
│   ├── server.py                     # API FastAPI na porta 8050
│   ├── css_service.py                # Bridge MT5, Motor LWMA 21 e Cache
│   ├── history_tracker.py            # Motor de Backtest 21h-08h, MAE/MFE e Intraday
│   └── static/                       # Frontend SPA (Vanilla JS + CSS Dark Mode)
│       ├── index.html                # Estrutura e Modais da Aplicação
│       ├── styles.css                # Design System Institucional Dark Mode
│       └── app.js                    # Motor Canvas 2D, Gráficos e Interações
├── daily_css_routine.py              # Script da Rotina Noturna das 21h00 BRT
├── start_web.bat                     # Inicializador do Servidor Web
├── run_daily_css.bat                 # Inicializador da Rotina Diária
└── README.md                         # Manual do Projeto
```

---

## 📚 Documentação Técnica Detalhada

* 🏛️ [**Especificação Técnica da Arquitetura (SPEC.md)**](file:///c:/Users/ryzen/Downloads/Antigravity/MFC/docs/SPEC.md)
* 📐 [**Fórmulas Matemáticas e Modelos Quantitativos (MATHEMATICAL_MODELS.md)**](file:///c:/Users/ryzen/Downloads/Antigravity/MFC/docs/MATHEMATICAL_MODELS.md)
* 🗄️ [**Mapeamento de Dados, Fluxos e Caches (DATABASE_DATA_FLOW.md)**](file:///c:/Users/ryzen/Downloads/Antigravity/MFC/docs/DATABASE_DATA_FLOW.md)
* 🌐 [**Referência da API REST (API.md)**](file:///c:/Users/ryzen/Downloads/Antigravity/MFC/docs/API.md)

---

## 🎯 Regras de Negócio e Metodologia Cíclica (Resumo)

| Região do CSS | Score | Ciclo Atual | Ciclo Devendo | Ação / Viés |
| :--- | :--- | :--- | :--- | :--- |
| **Zona Superior (+0.20)** | $\ge +0.20$ | Alta Cumprida | Fraqueza (Queda) | **VENDA (Exaustão de Topo)** |
| **Cruzando Zero (0.00 $\uparrow$)** | $-0.15$ a $+0.05$ | Rompimento de Alta | Continuidade de Alta | **COMPRA (Rumo a +0.20)** |
| **Box Neutro** | $-0.15$ a $+0.15$ | Lateral / Equilíbrio | Definir Rompimento | **AGUARDAR / EVITAR OPERAR** |
| **Cruzando Zero (0.00 $\downarrow$)** | $+0.05$ a $-0.15$ | Rompimento de Baixa| Continuidade de Queda| **VENDA (Rumo a -0.20)** |
| **Zona Inferior (-0.20)** | $\le -0.20$ | Baixa Cumprida | Força (Alta) | **COMPRA (Exaustão de Fundo)** |
