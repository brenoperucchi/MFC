# 📐 Especificação Matemática e Fórmulas do Sistema CSS Institutional

Este documento detalha rigorosamente todas as fórmulas matemáticas, algoritmos e modelos estatísticos implementados no sistema **CSS Institutional Platform & Multi-Portfolio Engine**.

---

## 1. Algoritmo de Cálculo do Indicador CSS (Currency Slope Strength)

O indicador CSS calcula a inclinação (derivada de força relativa) de cada uma das 8 principais moedas (`USD`, `EUR`, `GBP`, `CHF`, `JPY`, `AUD`, `CAD`, `NZD`) a partir da desagregação dos 28 pares cambiais do Forex.

### 1.1 Suavização de Preço: Linear Weighted Moving Average (LWMA 21)

A média móvel ponderada linearmente de período $N = 21$ atribui pesos linearmente decrescentes aos preços de fechamento passados:

$$\text{LWMA}_t = \frac{\sum_{i=0}^{N-1} (N - i) \cdot P_{t-i}}{\sum_{i=0}^{N-1} (N - i)} = \frac{\sum_{i=0}^{20} (21 - i) \cdot P_{t-i}}{\frac{21 \times 22}{2}} = \frac{\sum_{i=0}^{20} (21 - i) \cdot P_{t-i}}{231}$$

### 1.2 Normalização de Volatilidade: Average True Range (ATR SMA 100)

O True Range ($TR$) de cada barra é calculado por:
$$TR_t = \max \left( High_t - Low_t, \; |High_t - Close_{t-1}|, \; |Low_t - Close_{t-1}| \right)$$

O ATR suavizado por Média Móvel Simples de 100 períodos:
$$\text{ATR}_{100, t} = \frac{1}{100} \sum_{i=0}^{99} TR_{t-i}$$

Para a normalização da inclinação, utiliza-se a escala de 10 pips da volatilidade histórica medida 10 períodos atrás ($pos - 10$):
$$\text{ATR\_Val} = \frac{\text{ATR}_{100, t-10}}{10.0}$$

### 1.3 Estimativa de Inclinação Não-Repintante (Slope) do Par

A inclinação instantânea do par cambial $S_{\text{pair}}$ utiliza a interpolação exata do código MQL5 (`css.mql5`):

$$\text{Prev} = \frac{\text{LWMA}_{t-1} \times 231.0 + Close_t \times 20.0}{251.0}$$

$$S_{\text{pair}} = \frac{\text{LWMA}_t - \text{Prev}}{\text{ATR\_Val}}$$

### 1.4 Decomposição por Moeda

Para cada uma das 8 moedas $C \in \{USD, EUR, GBP, CHF, JPY, AUD, CAD, NZD\}$:
* Para cada par $P = \text{Base}\text{Quote}$ onde $C$ está presente:
  * Se $C = \text{Base}$: $S_{C, P} = +S_{\text{pair}}$
  * Se $C = \text{Quote}$: $S_{C, P} = -S_{\text{pair}}$
* O score final do CSS para a moeda $C$ no timeframe $TF$ é a média aritmética dos 7 pares:

$$\text{CSS}_{C, TF} = \frac{1}{7} \sum_{P \in \text{Pares}(C)} S_{C, P}$$

---

## 2. Modelo da Tríade Analítica Institucional

Para cada moeda em cada um dos 5 timeframes ($\text{MN1}, \text{W1}, \text{D1}, \text{H4}, \text{H1}$), o sistema classifica o estado operacional em 4 dimensões:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          TRÍADE ANALÍTICA DO CSS                            │
├───────────────────────┬──────────────────────┬──────────────────────────────┤
│ 1. REGIÃO INSTITUCIONAL│ 2. CICLO ATUAL       │ 3. CICLO DEVENDO (DIREÇÃO)   │
├───────────────────────┼──────────────────────┼──────────────────────────────┤
│ Superior: Score > +0.2│ Alta (Força)         │ Fraqueza (Rumo a 0.00/-0.20) │
│ Box Neutro: -0.2 a +0.2│ Rompimento / Reteste │ Continuidade até o extremo   │
│ Inferior: Score < -0.2│ Baixa (Fraqueza)     │ Força (Rumo a 0.00/+0.20)    │
└───────────────────────┴──────────────────────┴──────────────────────────────┘
```

### 2.1 Zonas de Parada e Níveis Críticos
* **Zona de Parada Superior (Linha Verde):** $+0.20$
* **Linha de Equilíbrio (Zero):** $0.00$
* **Zona de Parada Inferior (Linha Vermelha):** $-0.20$

### 2.2 Ciclo Devendo e Exaustão Cíclica

$$\text{Ciclo Devendo}(C) = \begin{cases} 
\text{Queda / Venda (Rumo a } -0.20\text{)}, & \text{se } \text{CSS}_C \ge +0.20 \text{ (Exaustão de Topo)} \\
\text{Alta / Compra (Rumo a } +0.20\text{)}, & \text{se } \text{CSS}_C \le -0.20 \text{ (Exaustão de Fundo)} \\
\text{Continuidade de Alta}, & \text{se } \text{CSS}_C > 0.00 \text{ e } \Delta\text{CSS} > 0 \\
\text{Continuidade de Baixa}, & \text{se } \text{CSS}_C < 0.00 \text{ e } \Delta\text{CSS} < 0
\end{cases}$$

---

## 3. Matriz de Confluência Multi-Timeframe e Pontuação dos 28 Pares

### 3.1 Ponderação Macro vs Operacional por Moeda

$$\text{Poder Macro}(C) = 0.20 \cdot \text{Score}_{\text{MN1}} + 0.30 \cdot \text{Score}_{\text{W1}} + 0.50 \cdot \text{Score}_{\text{D1}}$$

$$\text{Poder Operacional}(C) = 0.50 \cdot \text{Score}_{\text{H4}} + 0.50 \cdot \text{Score}_{\text{H1}}$$

$$\text{Poder Cíclico Total}(C) = 0.60 \cdot \text{Poder Macro}(C) + 0.40 \cdot \text{Poder Operacional}(C)$$

### 3.2 Score de Oportunidade Cíclica do Par (Base/Quote)

Para evitar comprar topos ou vender fundos, o ranking de pares é calculado pelo **Diferencial de Poder Cíclico**:

$$\text{Score Cíclico}(P) = \frac{\text{Poder Cíclico}(\text{Base}) - \text{Poder Cíclico}(\text{Quote})}{2.0}$$

* Se $\text{Score Cíclico}(P) \ge +0.40$: **COMPRA FORTE (STRONG BUY)**
* Se $\text{Score Cíclico}(P) \le -0.40$: **VENDA FORTE (STRONG SELL)**
* Se $|\text{Score Cíclico}(P)| < 0.15$: **NEUTRO / LATERAL (BOX)**

---

## 4. Motor Quantitativo de Portfólio (21h00 ➔ 08h00 BRT)

### 4.1 Regra de Confluência Mínima de Entrada (21h00 BRT / 03h00 MT5)
Uma moeda $C$ é qualificada para trading se e somente se:
$$\sum_{TF \in \{\text{MN1, W1, D1, H4, H1}\}} \mathbb{I}(\text{Direção}(C, TF) = \text{Sinal}) \ge 4$$

### 4.2 Cesta de 7 Pares Isolados
Se $C$ for qualificada com viés de **COMPRA (BUY)**:
* Para cada um dos 7 pares que contém $C$:
  * Se $C$ é Base $\implies$ Executa **BUY 0.01 lotes**
  * Se $C$ é Quote $\implies$ Executa **SELL 0.01 lotes** (vendendo a outra para comprar $C$)

Se $C$ for qualificada com viés de **VENDA (SELL)**:
* Para cada um dos 7 pares que contém $C$:
  * Se $C$ é Base $\implies$ Executa **SELL 0.01 lotes**
  * Se $C$ é Quote $\implies$ Executa **BUY 0.01 lotes**

---

## 5. Fórmulas de Conversão Financeira, MAE e MFE

### 5.1 Cálculo de PnL em USD por Par

Dado um lote padrão $L = 0.01$ (1.000 unidades da moeda base):
$$\text{Preço Diferencial} (\Delta P) = \begin{cases} P_{\text{exit}} - P_{\text{entry}}, & \text{se BUY} \\ P_{\text{entry}} - P_{\text{exit}}, & \text{se SELL} \end{cases}$$

$$\text{Pips} = \frac{\Delta P}{\text{PipSize}}, \quad \text{onde } \text{PipSize} = \begin{cases} 0.01, & \text{se par com JPY} \\ 0.0001, & \text{demais pares} \end{cases}$$

$$\text{PnL}_{\text{Quote}} = \Delta P \times 1000$$

Conversão para USD:
$$\text{PnL}_{\text{USD}} = \begin{cases} 
\text{PnL}_{\text{Quote}}, & \text{se } \text{Quote} = \text{USD} \\
\frac{\text{PnL}_{\text{Quote}}}{P_{\text{exit}}}, & \text{se } \text{Base} = \text{USD} \\
\text{PnL}_{\text{Quote}} \times \text{Rate}(\text{Quote}\to\text{USD}), & \text{pares cruzados}
\end{cases}$$

### 5.2 Excursão Máxima Favorável (MFE) e Adversa (MAE)

Para cada hora $h \in \{0, 1, 2, \dots, 11\}$ das 21h00 às 08h00, seja $\text{PnL}(h)$ o lucro flutuante acumulado:

$$\text{MFE} = \max_{0 \le h \le 11} \text{PnL}(h) \quad (\text{Pico Máximo de Lucro atingido})$$

$$\text{MAE} = \min_{0 \le h \le 11} \text{PnL}(h) \quad (\text{Rebaixamento / Drawdown Máximo sofrido})$$
