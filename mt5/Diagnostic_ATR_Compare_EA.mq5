//+------------------------------------------------------------------+
//| Diagnostic_ATR_Compare_EA.mq5                                    |
//|                                                                   |
//| Item 7 (tri-implementacao ATR) — herdr-ask mfc-16: compara, bar a |
//| bar, sobre a MESMA serie real de candles (so barras FECHADAS):    |
//|   (a) SMA(TR,100) — igual a calc_atr_sma() em web/css_service.py  |
//|   (b) buffer CRU do iATR() nativo do MetaTrader (handle)          |
//|   (c) Wilder/RMA textbook (semente = SMA da 1a janela), desempate |
//|                                                                   |
//| A medicao roda uma unica vez dentro de OnInit() (chart-attach). Se |
//| a cobertura sair completa, o EA fica anexado ao grafico sem fazer |
//| mais nada depois (OnTick vazio, nao se autorremove); se a         |
//| cobertura sair incompleta, OnInit() devolve INIT_FAILED e o MT5   |
//| REMOVE o EA do grafico sozinho (nao e falha de compilar/anexar —  |
//| o CSV ja foi escrito e a linha "COBERTURA:" ja saiu no log antes  |
//| do return — herdr-review mfc-78/achado NOVO-2). Nao abre ordem    |
//| nenhuma em nenhum dos dois casos.                                 |
//|                                                                   |
//| SO PARA A INSTANCIA ISOLADA mfc-backtest — nunca a live mfc. O    |
//| helper padrao do projeto (scripts/compile_ea_remote.sh) aponta pro|
//| terminal AO VIVO por default — pra compilar este arquivo contra a |
//| instancia isolada, sempre com o override explicito:               |
//|   CSS_MT5_REMOTE_PORTABLE_DIR=/mnt/d/MetaTradersWSL/mfc-backtest \|
//|     scripts/compile_ea_remote.sh mt5/Diagnostic_ATR_Compare_EA.mq5|
//| (herdr-review mfc-77/achado P3-3: sem o override, o comando obvio |
//| instalaria isto no terminal ao vivo — sem risco de ordem real,    |
//| ja que OnTick e vazio, mas nao e o pretendido.)                   |
//+------------------------------------------------------------------+
#property strict

input string InpSymbols    = "EURUSDm,GBPUSDm,USDJPYm,AUDUSDm"; // CSV, ja com sufixo da corretora
input ENUM_TIMEFRAMES InpTimeframe = PERIOD_D1;
input int    InpBars       = 400;
input int    InpAtrPeriod  = 100;
input string InpOutputFile = "atr_diagnostic_output.csv";

int OnInit()
{
   Print("MFC ATR DIAGNOSTIC INICIO");
   bool ok = RunDiagnostic();
   Print("MFC ATR DIAGNOSTIC FIM — cobertura completa: ", ok ? "SIM" : "NAO (ver avisos acima)");
   return(ok ? INIT_SUCCEEDED : INIT_FAILED);
}

void OnTick() {}
void OnDeinit(const int reason) {}

// Devolve true só se TODOS os símbolos pedidos produziram a cobertura
// esperada (nenhum pulado, nenhuma linha faltando) — herdr-review mfc-77
// achado P1: antes disto, uma falha total (ex.: SymbolSelect falhando pra
// todo mundo) ainda imprimia "Diagnostico concluido" com máximos em zero,
// indistinguível de um match real.
bool RunDiagnostic()
{
   string symbols[];
   int nSym = StringSplit(InpSymbols, ',', symbols);

   int fh = FileOpen(InpOutputFile, FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
   if(fh == INVALID_HANDLE)
   {
      Print("ERRO: FileOpen falhou pra ", InpOutputFile, " code=", GetLastError());
      return false;
   }
   FileWrite(fh, "symbol", "bar_index", "time",
             "sma_atr", "native_iatr", "wilder_atr",
             "abs_diff_sma_vs_iatr", "abs_diff_wilder_vs_iatr", "rel_diff_sma_vs_iatr_pct");

   int symbolsOk = 0, symbolsSkipped = 0, symbolsIncomplete = 0;
   long totalExpectedRows = 0, totalWrittenRows = 0;

   for(int s = 0; s < nSym; s++)
   {
      string sym = symbols[s];
      StringTrimLeft(sym);
      StringTrimRight(sym);
      if(sym == "") continue;

      if(!SymbolSelect(sym, true))
      {
         Print("AVISO: SymbolSelect falhou pra ", sym, " code=", GetLastError());
         symbolsSkipped++;
         continue;
      }

      // start_pos=1 (nao 0): pula deliberadamente a barra CORRENTE, que pode
      // ainda estar se formando — herdr-review mfc-77 achado P2. Sem isso,
      // o high/low/close dessa barra podem mudar entre esta captura e a
      // leitura do iATR mais abaixo, mesmo com o casamento por timestamp
      // (iBarShift) ja fechando a corrida de INDICE que a v1 tinha. So
      // barras fechadas entram na comparacao.
      MqlRates rates[];
      ArraySetAsSeries(rates, false); // index 0 = barra mais antiga
      int copied = CopyRates(sym, InpTimeframe, 1, InpBars, rates);
      if(copied <= InpAtrPeriod)
      {
         Print("AVISO: poucas barras pra ", sym, " (", copied, " copiadas, precisa > ", InpAtrPeriod, ")");
         symbolsSkipped++;
         continue;
      }

      // TR + SMA(TR, period) — espelha calc_atr_sma() (web/css_service.py:727)
      double tr[];
      ArrayResize(tr, copied);
      tr[0] = rates[0].high - rates[0].low;
      for(int i = 1; i < copied; i++)
      {
         double hl = rates[i].high - rates[i].low;
         double hc = MathAbs(rates[i].high - rates[i - 1].close);
         double lc = MathAbs(rates[i].low - rates[i - 1].close);
         tr[i] = MathMax(hl, MathMax(hc, lc));
      }

      double smaAtr[];
      ArrayResize(smaAtr, copied);
      double windowSum = 0.0;
      for(int i = 0; i < copied; i++)
      {
         windowSum += tr[i];
         int windowLen = i + 1;
         if(windowLen > InpAtrPeriod)
         {
            windowSum -= tr[i - InpAtrPeriod];
            windowLen = InpAtrPeriod;
         }
         smaAtr[i] = windowSum / windowLen;
      }

      // Wilder/RMA textbook, so como desempate — NAO e o que calc_atr_sma()
      // faz. Semente = SMA dos primeiros InpAtrPeriod TRs (herdr-review
      // mfc-77 achado P2-3/P3-2: semear com tr[0] sozinho injeta um erro
      // que so esquece exponencialmente — com period=100, ~37% do erro de
      // semente ainda presente depois de 100 barras, ~5% depois de 300).
      // Indices antes do seed ficam com 0.0 e nunca sao lidos (o loop de
      // comparacao comeca em i=InpAtrPeriod, apos o seed em InpAtrPeriod-1).
      double wilderAtr[];
      ArrayResize(wilderAtr, copied);
      ArrayInitialize(wilderAtr, 0.0);
      double seedSum = 0.0;
      for(int k = 0; k < InpAtrPeriod; k++) seedSum += tr[k];
      wilderAtr[InpAtrPeriod - 1] = seedSum / InpAtrPeriod;
      for(int i = InpAtrPeriod; i < copied; i++)
         wilderAtr[i] = (wilderAtr[i - 1] * (InpAtrPeriod - 1) + tr[i]) / InpAtrPeriod;

      // iATR nativo — mesma convencao de handle de mt5/css.mql5 (g_hATR[i] = iATR(...))
      int hATR = iATR(sym, InpTimeframe, InpAtrPeriod);
      if(hATR == INVALID_HANDLE)
      {
         Print("AVISO: iATR handle invalido pra ", sym, " code=", GetLastError());
         symbolsSkipped++;
         continue;
      }

      int tries = 0;
      while(BarsCalculated(hATR) < copied && tries < 50)
      {
         Sleep(100);
         tries++;
      }
      if(BarsCalculated(hATR) < copied)
         Print("AVISO: iATR de ", sym, " so calculou ", BarsCalculated(hATR), " de ", copied, " barras pedidas");

      // NAO usar um unico CopyBuffer(...,0,copied,...) casado por indice com
      // 'rates': entre o CopyRates() la em cima e este ponto, o "shift 0"
      // (barra atual) do simbolo pode ter avancado (feed ainda sincronizando
      // no boot do terminal) — isso desalinha os dois arrays em N barras sem
      // erro nenhum reportado, e SIMULA uma diferenca de calculo que na
      // verdade e so desalinhamento de coleta (visto empiricamente: pra 3 de
      // 4 simbolos numa rodada anterior, sma_atr[i] batia com
      // native_iatr[i-1] quase perfeitamente). Fix: casar CADA barra pelo
      // proprio timestamp via iBarShift, nunca por indice bruto.
      int expectedForSymbol = copied - InpAtrPeriod;
      int writtenForSymbol = 0;
      double maxAbsDiffSma = 0.0, maxRelDiffSma = 0.0;
      double maxAbsDiffWilder = 0.0, maxRelDiffWilder = 0.0;
      for(int i = InpAtrPeriod; i < copied; i++) // so barras com janela cheia do SMA e do Wilder
      {
         int shift = iBarShift(sym, InpTimeframe, rates[i].time, true);
         if(shift < 0)
         {
            Print("AVISO: iBarShift nao achou a barra ", TimeToString(rates[i].time, TIME_DATE | TIME_MINUTES), " de ", sym);
            continue;
         }
         double nativeBuf[];
         if(CopyBuffer(hATR, 0, shift, 1, nativeBuf) <= 0)
         {
            Print("AVISO: CopyBuffer(shift=", shift, ") falhou pra ", sym, " code=", GetLastError());
            continue;
         }
         double nativeVal = nativeBuf[0];

         double absDiffSma = MathAbs(smaAtr[i] - nativeVal);
         double absDiffWilder = MathAbs(wilderAtr[i] - nativeVal);
         double relDiffSmaPct = (nativeVal != 0.0) ? (absDiffSma / nativeVal) * 100.0 : 0.0;
         double relDiffWilderPct = (nativeVal != 0.0) ? (absDiffWilder / nativeVal) * 100.0 : 0.0;

         // 12 casas decimais NAO expoe ruido de ponto flutuante (que aqui e
         // ~1e-14/1e-16 — imprimiria 0.000000000000 de qualquer jeito); o
         // que isso faz e' colocar o TETO do que o CSV consegue afirmar em
         // 1e-12, ordens de grandeza abaixo de qualquer divergencia real de
         // metodo — "concorda dentro de 1e-12", nunca "bate bit a bit". Pra
         // enxergar o ruido de verdade seria preciso notacao cientifica ou
         // mais casas — herdr-review mfc-77 achado P3-1/P2-4, mfc-78
         // achado NOVO-1 (o comentario anterior superafirmava o que estas
         // 12 casas provam).
         FileWrite(fh, sym, i, TimeToString(rates[i].time, TIME_DATE | TIME_MINUTES),
                   DoubleToString(smaAtr[i], 12), DoubleToString(nativeVal, 12), DoubleToString(wilderAtr[i], 12),
                   DoubleToString(absDiffSma, 12), DoubleToString(absDiffWilder, 12), DoubleToString(relDiffSmaPct, 8));
         writtenForSymbol++;

         if(relDiffSmaPct > maxRelDiffSma) maxRelDiffSma = relDiffSmaPct;
         if(absDiffSma > maxAbsDiffSma) maxAbsDiffSma = absDiffSma;
         if(relDiffWilderPct > maxRelDiffWilder) maxRelDiffWilder = relDiffWilderPct;
         if(absDiffWilder > maxAbsDiffWilder) maxAbsDiffWilder = absDiffWilder;
      }

      // Unidades consistentes nos dois lados do print (herdr-review mfc-77
      // achado P3-1, sub-item): antes misturava relativo (SMA) com absoluto
      // (Wilder), impossivel comparar a olho sem abrir o CSV.
      Print(sym, ": SMA-vs-iATR max_abs=", DoubleToString(maxAbsDiffSma, 12),
            " max_rel=", DoubleToString(maxRelDiffSma, 8), "%",
            "  |  Wilder-vs-iATR max_abs=", DoubleToString(maxAbsDiffWilder, 12),
            " max_rel=", DoubleToString(maxRelDiffWilder, 8), "%",
            "  (", writtenForSymbol, "/", expectedForSymbol, " linhas)");

      totalExpectedRows += expectedForSymbol;
      totalWrittenRows += writtenForSymbol;
      if(writtenForSymbol < expectedForSymbol)
         symbolsIncomplete++;
      else
         symbolsOk++;

      IndicatorRelease(hATR);
   }

   FileClose(fh);
   Print("Diagnostico concluido, ver arquivo: ", InpOutputFile,
         "  |  COBERTURA: ok=", symbolsOk, " incompletos=", symbolsIncomplete, " pulados=", symbolsSkipped,
         "  linhas ", totalWrittenRows, "/", totalExpectedRows);

   return (symbolsSkipped == 0 && symbolsIncomplete == 0 && totalWrittenRows > 0 && totalWrittenRows == totalExpectedRows);
}
