# Log de Conhecimento Diário — Análise Cíclica e Avaliação de Trade (CSS / MFC)

Este diretório armazena o histórico diário de análises do indicador **CSS (Currency Slope Strength)**, registrando o diagnóstico de força/fraqueza de cada sessão, as oportunidades identificadas, a expectativa de resultado e o resultado real verificado a posteriori.

---

## 🎯 Objetivo
1. **Acumular Histórico Auditável**: Registrar diariamente o estado das matrizes macro e operacionais.
2. **Validar a Tese Cíclica**: Comparar as previsões de movimento (ex: Distribuição/Venda no topo, Acumulação/Compra no fundo) com o resultado real do mercado.
3. **Refinar a Tomada de Decisão**: Identificar padrões recorrentes de sucesso e ajuste fino de timing para os dias seguintes.

---

## 📝 Modelo de Registro Diário (`YYYYMMDD.md`)

Cada arquivo diário deve seguir esta estrutura padronizada:

```markdown
# Análise Diária CSS — YYYY-MM-DD

## 1. Referência dos Dados
* **Data da Análise**: YYYY-MM-DD
* **Pasta de Imagens**: `c:/Users/ryzen/Downloads/Antigravity/MFC/YYYYMMDD`

## 2. Matriz de Força Relativa (Dashboard Data)
### Macro Dashboard (MN / W1 / D1)
| Ranking | Moeda | MN | W1 | D1 | Nota Macro | Direção Cíclica |
| :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| 1 | USD | ... | ... | ... | ... | Expansão |
| ... | ... | ... | ... | ... | ... | ... |

### Operational Dashboard (H4 / H1)
| Moeda | H4 | H1 | Território Operacional |
| :---: | :---: | :---: | :--- |
| NZD | -0.10 | -0.04 | Negativo (Confirmado) |

---

## 3. Oportunidades Identificadas (Setup do Dia)
* **Ativo Primário**: ex: NZDUSD
* **Direção**: [COMPRA / VENDA]
* **Moeda Fraca (Sinal)**: NZD (Virada no D1=-1, H4=-0.10, H1=-0.04)
* **Moeda Forte (Contra-parte)**: USD (Nota Macro +0.67, D1=+2, H1=+0.16)
* **Tese Cíclica**: *Falling from positive region* (Distribuição de topo no NZD contra expansão de alta do USD).

---

## 4. Expectativa de Resultado
* **Movimento Esperado**: Desvalorização do par NZDUSD nas sessões seguintes.
* **Nível de Confirmação**: Rompimento e sustentação abaixo das médias operacionais no H1.

---

## 5. Resultado Real Verificado (Pós-Sessão)
* **Status**: [EM ACOMPANHAMENTO / SUCESSO / FALHA]
* **Movimento Real observado**: ...
* **Lições / Aprendizados**: ...
```
