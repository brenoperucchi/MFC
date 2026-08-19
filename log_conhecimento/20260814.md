# Relatório Diário de Confluência Multi-Agente CSS — 14/08/2026

## 1. Referência dos Dados
* **Data da Execução**: 14/08/2026 às 22:22:13
* **Diretório das Imagens**: [`c:\Users\ryzen\Downloads\Antigravity\MFC\20260814`](file:///c:/Users/ryzen/Downloads/Antigravity/MFC/20260814)
* **Dashboards Salvos (Moedas Puras)**:
  * [`USD_5TF_Dashboard.png`](file:///c:/Users/ryzen/Downloads/Antigravity/MFC/20260814/USD_5TF_Dashboard.png)
  * [`EUR_5TF_Dashboard.png`](file:///c:/Users/ryzen/Downloads/Antigravity/MFC/20260814/EUR_5TF_Dashboard.png)
  * [`GBP_5TF_Dashboard.png`](file:///c:/Users/ryzen/Downloads/Antigravity/MFC/20260814/GBP_5TF_Dashboard.png)
  * [`AUD_5TF_Dashboard.png`](file:///c:/Users/ryzen/Downloads/Antigravity/MFC/20260814/AUD_5TF_Dashboard.png)
  * [`NZD_5TF_Dashboard.png`](file:///c:/Users/ryzen/Downloads/Antigravity/MFC/20260814/NZD_5TF_Dashboard.png)
  * [`CAD_5TF_Dashboard.png`](file:///c:/Users/ryzen/Downloads/Antigravity/MFC/20260814/CAD_5TF_Dashboard.png)
  * [`CHF_5TF_Dashboard.png`](file:///c:/Users/ryzen/Downloads/Antigravity/MFC/20260814/CHF_5TF_Dashboard.png)
  * [`JPY_5TF_Dashboard.png`](file:///c:/Users/ryzen/Downloads/Antigravity/MFC/20260814/JPY_5TF_Dashboard.png)
  * [`CSS_AllCurrencies_H1.png`](file:///c:/Users/ryzen/Downloads/Antigravity/MFC/20260814/CSS_AllCurrencies_H1.png) *(Visão Geral 8 Moedas no H1)*

---

## 2. Relatório do Agente Macro (MN1, W1, D1)
*Foco: Contexto institucional, zonas de parada (+/- 0.20), equilíbrio (0.00) e permissões de ciclo.*

| Moeda | MN1 (Score / Dir) | W1 (Score / Dir) | D1 (Score / Dir) | Fase do Ciclo Macro | Viés Macro |
| :---: | :---: | :---: | :---: | :--- | :--- |
| **USD** | -0.20 (DN) | +0.12 (UP) | -0.44 (DN) | CUMPRIU CICLO DE BAIXA (ALERTA DE FUNDO / LINHA VERMELHA) | **ATENÇÃO A EXAUSTÃO DE QUEDA** |
| **EUR** | +0.27 (UP) | -0.08 (DN) | +0.15 (DN) | ABERTO SOBRE O 0 (PERMISSÃO VÁLIDA PARA CAIR) | **VENDA EM DESENVOLVIMENTO (TESTE DO 0)** |
| **GBP** | +0.07 (DN) | +0.23 (DN) | -0.05 (UP) | ABERTO SOBRE O 0 (PERMISSÃO VÁLIDA PARA SUBIR) | **COMPRA EM DESENVOLVIMENTO (TESTE DO 0)** |
| **CHF** | +0.33 (UP) | -0.36 (UP) | -0.43 (UP) | ZONA DE PARADA VERMELHA (DEVENDO CICLO DE FORÇA) | **COMPRA DE FUNDO (RUMO À LINHA VERDE)** |
| **JPY** | -0.68 (DN) | +0.03 (DN) | +0.59 (DN) | ZONA DE PARADA VERDE (DEVENDO CICLO DE FRAQUEZA) | **VENDA DE TOPO (RUMO À LINHA VERMELHA)** |
| **AUD** | +0.49 (DN) | +0.06 (DN) | +0.07 (DN) | ABERTO SOBRE O 0 (PERMISSÃO VÁLIDA PARA CAIR) | **VENDA EM DESENVOLVIMENTO (TESTE DO 0)** |
| **CAD** | -0.12 (UP) | -0.16 (UP) | +0.10 (DN) | ABERTO SOBRE O 0 (PERMISSÃO VÁLIDA PARA CAIR) | **VENDA EM DESENVOLVIMENTO (TESTE DO 0)** |
| **NZD** | -0.17 (UP) | +0.17 (DN) | +0.02 (UP) | ABERTO SOBRE O 0 (PERMISSÃO VÁLIDA PARA SUBIR) | **COMPRA EM DESENVOLVIMENTO (TESTE DO 0)** |

---

## 3. Relatório do Agente Operacional (H4, H1)
*Foco: Timing intraday, acúmulos sobre zonas de parada e cumprimento de ciclos de força/fraqueza.*

| Moeda | H4 (Score / Dir) | H1 (Score / Dir) | Status Operacional | Timing de Execução |
| :---: | :---: | :---: | :--- | :--- |
| **USD** | -0.15 (UP) | -0.74 (UP) | ZONA DE PARADA VERMELHA (DEVENDO CICLO DE FORÇA) | **GATILHO DE COMPRA ATIVO (RUMO À LINHA VERDE)** |
| **EUR** | +0.14 (DN) | +0.18 (DN) | ZONA DE PARADA VERDE (DEVENDO CICLO DE FRAQUEZA) | **GATILHO DE VENDA ATIVO (RUMO À LINHA VERMELHA)** |
| **GBP** | +0.04 (DN) | +0.23 (DN) | ZONA DE PARADA VERDE (DEVENDO CICLO DE FRAQUEZA) | **GATILHO DE VENDA ATIVO (RUMO À LINHA VERMELHA)** |
| **CHF** | -0.38 (UP) | -0.32 (UP) | ZONA DE PARADA VERMELHA (DEVENDO CICLO DE FORÇA) | **GATILHO DE COMPRA ATIVO (RUMO À LINHA VERDE)** |
| **JPY** | -0.09 (UP) | -0.47 (UP) | ZONA DE PARADA VERMELHA (DEVENDO CICLO DE FORÇA) | **GATILHO DE COMPRA ATIVO (RUMO À LINHA VERDE)** |
| **AUD** | +0.14 (DN) | +0.20 (UP) | H4 EM QUEDA COM REPIQUE DE H1 (ACÚMULO / TESTE DO 0) | **AGUARDAR H1 VIRAR PARA BAIXA (VENDA)** |
| **CAD** | +0.29 (DN) | +0.34 (DN) | ZONA DE PARADA VERDE (DEVENDO CICLO DE FRAQUEZA) | **GATILHO DE VENDA ATIVO (RUMO À LINHA VERMELHA)** |
| **NZD** | +0.00 (DN) | +0.58 (DN) | ZONA DE PARADA VERDE (DEVENDO CICLO DE FRAQUEZA) | **GATILHO DE VENDA ATIVO (RUMO À LINHA VERMELHA)** |

---

## 4. Alertas de Divergência entre Timeframes (Mensal vs H4/H1)
*Identificação de divergências estruturais entre a inércia do Mensal e os ciclos operacionais imediatos de H4 e H1.*

| Moeda | Alerta de Divergência | Impacto no Trade |
| :---: | :--- | :--- |
| **USD** | ⚠️ DIVERGÊNCIA: MN1 inclinado para fraqueza (▼), mas H4 e H1 em alta devendo ciclo de força (▲) | ⚠️ Ciclo operacional (H4/H1) em contra-fluxo à inércia do Mensal |
| **EUR** | ⚠️ DIVERGÊNCIA: MN1 inclinado para força (▲), mas H4 e H1 em queda devendo ciclo de fraqueza (▼) | ⚠️ Ciclo operacional (H4/H1) em contra-fluxo à inércia do Mensal |
| **GBP** | NENHUMA (TIMEFRAMES ALINHADOS) | ✅ Timeframes em pleno alinhamento harmônico |
| **CHF** | NENHUMA (TIMEFRAMES ALINHADOS) | ✅ Timeframes em pleno alinhamento harmônico |
| **JPY** | ⚠️ DIVERGÊNCIA: MN1 inclinado para fraqueza (▼), mas H4 e H1 em alta devendo ciclo de força (▲) | ⚠️ Ciclo operacional (H4/H1) em contra-fluxo à inércia do Mensal |
| **AUD** | NENHUMA (TIMEFRAMES ALINHADOS) | ✅ Timeframes em pleno alinhamento harmônico |
| **CAD** | ⚠️ DIVERGÊNCIA: MN1 inclinado para força (▲), mas H4 e H1 em queda devendo ciclo de fraqueza (▼) | ⚠️ Ciclo operacional (H4/H1) em contra-fluxo à inércia do Mensal |
| **NZD** | ⚠️ DIVERGÊNCIA: MN1 inclinado para força (▲), mas H4 e H1 em queda devendo ciclo de fraqueza (▼) | ⚠️ Ciclo operacional (H4/H1) em contra-fluxo à inércia do Mensal |

---

## 5. Diagnóstico Unificado de Confluência das 8 Moedas

| Moeda | Estado de Confluência | Veredito Final | Potencial Cíclico |
| :---: | :--- | :--- | :--- |
| **USD** | OPERACIONAL EM ALTA (CICLO DE FORÇA H4/H1) | **COMPRA OPERACIONAL (BUSCANDO LINHA VERDE)** | `COMPRA` |
| **EUR** | CONFLUÊNCIA DE QUEDA (DEVENDO CICLO DE FRAQUEZA) | **VENDA FORTE (RUMO À LINHA VERMELHA -0.20)** | `VENDA FORTE` |
| **GBP** | OPERACIONAL EM QUEDA (CICLO DE FRAQUEZA H4/H1) | **VENDA OPERACIONAL (BUSCANDO LINHA VERMELHA)** | `VENDA` |
| **CHF** | CONFLUÊNCIA DE ALTA (DEVENDO CICLO DE FORÇA) | **COMPRA FORTE (RUMO À LINHA VERDE +0.20)** | `COMPRA FORTE` |
| **JPY** | OPERACIONAL EM ALTA (CICLO DE FORÇA H4/H1) | **COMPRA OPERACIONAL (BUSCANDO LINHA VERDE)** | `COMPRA` |
| **AUD** | CONFLUÊNCIA DE QUEDA (DEVENDO CICLO DE FRAQUEZA) | **VENDA FORTE (RUMO À LINHA VERMELHA -0.20)** | `VENDA FORTE` |
| **CAD** | CONFLUÊNCIA DE QUEDA (DEVENDO CICLO DE FRAQUEZA) | **VENDA FORTE (RUMO À LINHA VERMELHA -0.20)** | `VENDA FORTE` |
| **NZD** | OPERACIONAL EM QUEDA (CICLO DE FRAQUEZA H4/H1) | **VENDA OPERACIONAL (BUSCANDO LINHA VERMELHA)** | `VENDA` |

---

## 6. Ranking Oficial de Confluência dos 28 Pares de Moedas

| # | Par | Ação Recomendada | Convicção | Total Score | Macro Diff | Op Diff | Tese Cíclica de Confluência |
| :-: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| 1 | **AUDUSD** | **COMPRA (BUY)** | ALTA | `+0.47` | +0.37 | +0.62 | Vantagem expressiva de fluxo para AUD sobre USD. |
| 2 | **AUDCHF** | **COMPRA (BUY)** | ALTA | `+0.45` | +0.41 | +0.52 | Vantagem expressiva de fluxo para AUD sobre CHF. |
| 3 | **NZDUSD** | **COMPRA (BUY)** | ALTA | `+0.44` | +0.25 | +0.73 | Vantagem expressiva de fluxo para NZD sobre USD. |
| 4 | **EURUSD** | **COMPRA (BUY)** | ALTA | `+0.44` | +0.33 | +0.60 | Vantagem expressiva de fluxo para EUR sobre USD. |
| 5 | **NZDCHF** | **COMPRA (BUY)** | ALTA | `+0.43` | +0.28 | +0.64 | Vantagem expressiva de fluxo para NZD sobre CHF. |
| 6 | **EURCHF** | **COMPRA (BUY)** | ALTA | `+0.42` | +0.36 | +0.51 | Vantagem expressiva de fluxo para EUR sobre CHF. |
| 7 | **CADCHF** | **COMPRA (BUY)** | ALTA | `+0.40` | +0.23 | +0.67 | Vantagem expressiva de fluxo para CAD sobre CHF. |
| 8 | **GBPUSD** | **COMPRA (BUY)** | ALTA | `+0.40` | +0.28 | +0.58 | Vantagem expressiva de fluxo para GBP sobre USD. |
| 9 | **GBPCHF** | **COMPRA (BUY)** | ALTA | `+0.38` | +0.31 | +0.49 | Vantagem expressiva de fluxo para GBP sobre CHF. |
| 10 | **AUDJPY** | **COMPRA (BUY)** | ALTA | `+0.17` | -0.02 | +0.45 | Vantagem expressiva de fluxo para AUD sobre JPY. |
| 11 | **NZDJPY** | **COMPRA (BUY)** | ALTA | `+0.14` | -0.14 | +0.57 | Vantagem expressiva de fluxo para NZD sobre JPY. |
| 12 | **EURJPY** | **COMPRA (BUY)** | ALTA | `+0.14` | -0.06 | +0.44 | Vantagem expressiva de fluxo para EUR sobre JPY. |
| 13 | **CADJPY** | **COMPRA (BUY)** | ALTA | `+0.12` | -0.19 | +0.59 | Vantagem expressiva de fluxo para CAD sobre JPY. |
| 14 | **GBPJPY** | **COMPRA (BUY)** | ALTA | `+0.10` | -0.11 | +0.42 | Vantagem expressiva de fluxo para GBP sobre JPY. |
| 15 | **AUDCAD** | **COMPRA MODERADA** | MODERADA | `+0.05` | +0.18 | -0.14 | Leve predominância compradora para AUD. |
| 16 | **EURGBP** | **COMPRA MODERADA** | MODERADA | `+0.04` | +0.05 | +0.02 | Leve predominância compradora para EUR. |
| 17 | **AUDNZD** | **NEUTRO / LATERAL (BOX)** | NEUTRA | `+0.03` | +0.12 | -0.12 | Forças equilibradas ou ambas no Box. Evitar operar. |
| 18 | **NZDCAD** | **NEUTRO / LATERAL (BOX)** | NEUTRA | `+0.02` | +0.05 | -0.03 | Forças equilibradas ou ambas no Box. Evitar operar. |
| 19 | **EURCAD** | **NEUTRO / LATERAL (BOX)** | NEUTRA | `+0.02` | +0.13 | -0.15 | Forças equilibradas ou ambas no Box. Evitar operar. |
| 20 | **EURNZD** | **NEUTRO / LATERAL (BOX)** | NEUTRA | `-0.00` | +0.08 | -0.13 | Forças equilibradas ou ambas no Box. Evitar operar. |
| 21 | **USDCHF** | **NEUTRO / LATERAL (BOX)** | NEUTRA | `-0.02` | +0.03 | -0.09 | Forças equilibradas ou ambas no Box. Evitar operar. |
| 22 | **GBPCAD** | **NEUTRO / LATERAL (BOX)** | NEUTRA | `-0.02` | +0.08 | -0.18 | Forças equilibradas ou ambas no Box. Evitar operar. |
| 23 | **EURAUD** | **VENDA MODERADA** | MODERADA | `-0.03` | -0.04 | -0.01 | Leve predominância vendedora em EUR. |
| 24 | **GBPNZD** | **VENDA MODERADA** | MODERADA | `-0.04` | +0.03 | -0.15 | Leve predominância vendedora em GBP. |
| 25 | **GBPAUD** | **VENDA MODERADA** | MODERADA | `-0.07` | -0.09 | -0.04 | Leve predominância vendedora em GBP. |
| 26 | **CHFJPY** | **VENDA (SELL)** | ALTA | `-0.28` | -0.42 | -0.07 | Pressão expressiva de venda em CHF frente ao JPY. |
| 27 | **USDJPY** | **VENDA (SELL)** | ALTA | `-0.30` | -0.39 | -0.16 | Pressão expressiva de venda em USD frente ao JPY. |
| 28 | **USDCAD** | **VENDA (SELL)** | ALTA | `-0.42` | -0.20 | -0.76 | Pressão expressiva de venda em USD frente ao CAD. |

---

## 7. Oportunidades Principais de Alta Convicção
* 🥇 **Melhor Par para COMPRA**: **AUDUSD** (COMPRA (BUY))
  * *Tese*: Vantagem expressiva de fluxo para AUD sobre USD.
* 🥇 **Melhor Par para VENDA**: **USDCAD** (VENDA (SELL))
  * *Tese*: Pressão expressiva de venda em USD frente ao CAD.
