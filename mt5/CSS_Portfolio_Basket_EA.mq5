//+------------------------------------------------------------------+
//|                                     CSS_Portfolio_Basket_EA.mq5   |
//|                                  CSS Institutional Pro Platform  |
//|               Robô de Gestão de Cestas de Portfólio Multi-Moeda  |
//+------------------------------------------------------------------+
#property copyright "CSS Institutional Pro"
#property link      "https://css-pro-mfc.web.app/"
#property version   "1.00"
#property description "Opera a cesta de 7 pares de uma moeda institucional (USD, EUR, GBP, CHF, JPY, AUD, CAD, NZD)"
#property description "Abertura automática às 21:05 e encerramento às 08:00 com Magic Number exclusivo."

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\SymbolInfo.mqh>

//--- ENUMS
enum ENUM_PORTFOLIO_CCY
{
   CCY_USD = 0, // USD (Magic: 801001)
   CCY_EUR = 1, // EUR (Magic: 801002)
   CCY_GBP = 2, // GBP (Magic: 801003)
   CCY_CHF = 3, // CHF (Magic: 801004)
   CCY_JPY = 4, // JPY (Magic: 801005)
   CCY_AUD = 5, // AUD (Magic: 801006)
   CCY_CAD = 6, // CAD (Magic: 801007)
   CCY_NZD = 7  // NZD (Magic: 801008)
};

enum ENUM_EXEC_BIAS
{
   BIAS_AUTO = 0,      // Automático (Lê decisão CSS via arquivo JSON)
   BIAS_FORCA_BUY = 1,  // Força da Moeda (Comprar Base / Vender Cotada)
   BIAS_FRAQUEZA_SELL = 2 // Fraqueza da Moeda (Vender Base / Comprar Cotada)
};

//--- INPUT PARAMETERS
input group "=== CONFIGURAÇÃO DO PORTFÓLIO ==="
input ENUM_PORTFOLIO_CCY  InpCurrency       = CCY_CAD;        // Moeda Alvo do Portfólio
input ulong               InpMagicNumber     = 0;              // Magic Number (0 = Automático por Moeda)
input double              InpLotSize         = 0.01;           // Lote por Par (ex: 0.01 = 0.07 total)
input ENUM_EXEC_BIAS      InpDirectionBias   = BIAS_AUTO;      // Direção Operacional

input group "=== JANELA OPERACIONAL (HORÁRIO BRASÍLIA) ==="
input int                 InpEntryHour       = 21;             // Hora de Entrada (BRT)
input int                 InpEntryMinute     = 5;              // Minuto de Entrada (BRT)
input int                 InpExitHour        = 8;              // Hora de Encerramento (BRT)
input int                 InpExitMinute      = 0;              // Minuto de Encerramento (BRT)
input int                 InpGmtOffset       = 0;              // GMT Offset do MT5 vs Horário Local

input group "=== EXECUÇÃO E TELEMETRIA ==="
input ulong               InpDeviation       = 15;             // Slippage Máximo (pontos)
input bool                InpExportTelemetry = true;           // Exportar Telemetria JSON para Web Dashboard

input group "=== SEGURANÇA ==="
input int                 InpCatastrophicSLPips = 150;          // Stop-loss catastrófico em pips (0 = desligado; NÃO deixar 0 em conta real)

input group "=== ARQUITETURA DE EXECUÇÃO (revisão 3 lentes independentes, 23/08) ==="
input bool                InpEaOpensBasket   = false;           // EA abre a cesta? (false = Python abre, EA só garante o fechamento — recomendado. true = comportamento original)

//--- OBJETOS DE NEGOCIAÇÃO
CTrade         m_trade;
CPositionInfo  m_position;
CSymbolInfo    m_symbol;

//--- VARIÁVEIS GLOBAIS
ulong          g_magic = 801001;
string         g_ccy_str = "CAD";
string         g_pairs[7];
bool           g_session_opened_today = false;

// Variáveis de Auditoria e HUD
double         g_floating_pnl = 0.0;
double         g_floating_pips = 0.0;
double         g_mfe_today = 0.0;
double         g_mae_today = 0.0;
int            g_open_orders_count = 0;
string         g_decision_str = "Aguardando 21:02";
string         g_session_status_str = "AGUARDANDO ABERTURA";

#define HUD_PREFIX "CSS_PORT_HUD_"

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   // Definir Moeda e Magic Number
   switch(InpCurrency)
   {
      case CCY_USD: g_ccy_str = "USD"; g_magic = 801001; break;
      case CCY_EUR: g_ccy_str = "EUR"; g_magic = 801002; break;
      case CCY_GBP: g_ccy_str = "GBP"; g_magic = 801003; break;
      case CCY_CHF: g_ccy_str = "CHF"; g_magic = 801004; break;
      case CCY_JPY: g_ccy_str = "JPY"; g_magic = 801005; break;
      case CCY_AUD: g_ccy_str = "AUD"; g_magic = 801006; break;
      case CCY_CAD: g_ccy_str = "CAD"; g_magic = 801007; break;
      case CCY_NZD: g_ccy_str = "NZD"; g_magic = 801008; break;
   }
   
   if(InpMagicNumber > 0)
      g_magic = InpMagicNumber;
      
   m_trade.SetExpertMagicNumber(g_magic);
   m_trade.SetDeviationInPoints(InpDeviation);
   m_trade.SetTypeFilling(ORDER_FILLING_IOC);
   
   // Montar lista dos 7 pares da moeda
   InitPortfolioPairs(g_ccy_str);
   
   EventSetTimer(3); // Checagem e atualização a cada 3 segundos
   
   // Desenhar Painel On-Chart Inicial
   UpdateChartDashboard();
   
   PrintFormat("[CSS ROBOT %s] Inicializado com Sucesso! Magic: %d | Lote: %.2f | Janela: %02d:%02d ➔ %02d:%02d | Modo: %s | SL catastrófico: %d pips",
               g_ccy_str, g_magic, InpLotSize, InpEntryHour, InpEntryMinute, InpExitHour, InpExitMinute,
               (InpEaOpensBasket ? "EA ABRE E FECHA (legado)" : "GUARDIÃO — só fecha, Python abre"),
               InpCatastrophicSLPips);

   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   DeleteChartDashboard();
   PrintFormat("[CSS ROBOT %s] Descarregado. Motivo: %d", g_ccy_str, reason);
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   UpdateAuditMetrics();
   UpdateChartDashboard();
}

//+------------------------------------------------------------------+
//| Retorna a hora atual já ajustada de servidor do broker para BRT, |
//| usando InpGmtOffset (horas a somar ao horário do servidor).      |
//| Correção: antes o InpGmtOffset era declarado e nunca usado — o   |
//| timer comparava a hora do SERVIDOR direto contra 21/08, então em |
//| brokers fora de BRT a cesta abria e fechava no horário errado.   |
//+------------------------------------------------------------------+
void GetBrtAdjustedTime(MqlDateTime &out_dt)
{
   datetime brt_now = TimeCurrent() + InpGmtOffset * 3600;
   TimeToStruct(brt_now, out_dt);
}

//+------------------------------------------------------------------+
//| Kill switch por arquivo — mesma convenção do lado Python         |
//| (agents/portfolio_executor.py::is_kill_switch_active). Bloqueia  |
//| só ABERTURA de posição nova; fechar nunca é bloqueado. Sem       |
//| FILE_COMMON — instância /portable dedicada ao MFC, Python grava  |
//| direto na MQL5/Files desta mesma instância.                      |
//+------------------------------------------------------------------+
bool IsKillSwitchActiveEA()
{
   int handle = FileOpen("CSS_KILL.flag", FILE_READ | FILE_TXT);
   if(handle != INVALID_HANDLE)
   {
      FileClose(handle);
      return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| Timer function para controle rigoroso de horários                |
//+------------------------------------------------------------------+
void OnTimer()
{
   MqlDateTime dt;
   GetBrtAdjustedTime(dt);

   int now_min   = dt.hour * 60 + dt.min;
   int entry_min = InpEntryHour * 60 + InpEntryMinute;
   int exit_min  = InpExitHour * 60 + InpExitMinute;

   // Janela operacional atravessa a meia-noite no caso normal (21:05 -> 08:00).
   // Suporta também o caso não-overnight, se algum dia mudar.
   bool in_operational_window;
   if(entry_min <= exit_min)
      in_operational_window = (now_min >= entry_min && now_min < exit_min);
   else
      in_operational_window = (now_min >= entry_min || now_min < exit_min);

   int open_positions = CountOpenPositions();

   // Reset da flag de "já tentei abrir" ao sair da janela — permite nova
   // tentativa na próxima noite sem depender de comparar "dia" (que muda de
   // sentido numa janela que atravessa a meia-noite).
   if(!in_operational_window)
      g_session_opened_today = false;

   // 1. ABERTURA — só roda se InpEaOpensBasket = true (comportamento original
   //    do robô). Por padrão (false), quem abre é o Python
   //    (agents/portfolio_executor.py::open_portfolio_basket, disparado pelo
   //    scheduler_daemon.py às 21:05 BRT) — arquitetura recomendada pelas três
   //    lentes de revisão: a lógica de decisão/idempotência/trava de conta
   //    fica testável em Python, o EA vira guardião só do fechamento (item 2
   //    abaixo), que é onde uma segunda implementação independente do resto
   //    do sistema vale mais (sobrevive ao Python cair). Reversível na hora,
   //    sem recompilar: é só marcar este input como true de novo.
   if(InpEaOpensBasket && in_operational_window && !g_session_opened_today && open_positions == 0)
   {
      if(IsKillSwitchActiveEA())
      {
         PrintFormat("[CSS ROBOT %s] Kill switch ativo (CSS_KILL.flag) — abertura bloqueada.", g_ccy_str);
      }
      else
      {
         ENUM_EXEC_BIAS bias_to_trade = InpDirectionBias;

         if(bias_to_trade == BIAS_AUTO)
            bias_to_trade = ReadAutoBiasFromJson(g_ccy_str, dt);

         if(bias_to_trade != BIAS_AUTO)
         {
            PrintFormat("[CSS ROBOT %s] Disparo de Entrada às %02d:%02d BRT | Direção: %s",
                        g_ccy_str, dt.hour, dt.min, (bias_to_trade == BIAS_FORCA_BUY ? "FORÇA (BUY)" : "FRAQUEZA (SELL)"));
            OpenBasket(bias_to_trade);
         }
      }
      // Marca a tentativa mesmo se ficou neutro/bloqueado — não fica batendo
      // no arquivo de sinal a cada 3s pelo resto da janela à toa.
      g_session_opened_today = true;
   }

   // 2. FECHAMENTO — monotônico: sempre que estivermos FORA da janela
   //    operacional e ainda houver posição nossa aberta, tenta fechar. Não
   //    depende de acertar a hora exata — se falhar (ou o terminal ficou
   //    fora do ar durante o horário certo), tenta de novo no próximo tick
   //    de 3s, pra sempre, até CountOpenPositions() voltar a zero.
   if(!in_operational_window && open_positions > 0)
   {
      PrintFormat("[CSS ROBOT %s] Fora da janela operacional (%02d:%02d BRT) com %d posições abertas. Fechando...",
                  g_ccy_str, dt.hour, dt.min, open_positions);
      CloseBasket();
   }

   // 3. EXPORTAÇÃO DE TELEMETRIA
   if(InpExportTelemetry)
   {
      ExportTelemetryJson();
   }
}

//+------------------------------------------------------------------+
//| Monta a lista dos 7 pares da moeda correspondente               |
//+------------------------------------------------------------------+
void InitPortfolioPairs(string ccy)
{
   string all_28[28] = {
      "EURUSD","GBPUSD","AUDUSD","NZDUSD","USDCAD","USDCHF","USDJPY",
      "EURGBP","EURAUD","EURCAD","EURCHF","EURJPY","EURNZD",
      "GBPAUD","GBPCAD","GBPCHF","GBPJPY","GBPNZD",
      "AUDCAD","AUDCHF","AUDJPY","AUDNZD",
      "CADCHF","CADJPY",
      "CHFJPY",
      "NZDCAD","NZDCHF","NZDJPY"
   };
   
   int idx = 0;
   for(int i = 0; i < 28 && idx < 7; i++)
   {
      if(StringFind(all_28[i], ccy) >= 0)
      {
         g_pairs[idx] = all_28[i];
         SymbolSelect(g_pairs[idx], true);
         idx++;
      }
   }
}

//+------------------------------------------------------------------+
//| Calcula o stop-loss catastrófico (pips fixos) — rede de segurança|
//| pro cenário "Python morto e terminal morto ao mesmo tempo", não  |
//| parâmetro de estratégia (a operação normal continua sem SL).     |
//+------------------------------------------------------------------+
double ComputeCatastrophicSL(string sym, ENUM_ORDER_TYPE order_type, double entry_price)
{
   if(InpCatastrophicSLPips <= 0)
      return 0.0;

   double pip_size = (StringFind(sym, "JPY") >= 0) ? 0.01 : 0.0001;
   double distance = InpCatastrophicSLPips * pip_size;

   if(order_type == ORDER_TYPE_BUY)
      return NormalizeDouble(entry_price - distance, m_symbol.Digits());
   else
      return NormalizeDouble(entry_price + distance, m_symbol.Digits());
}

//+------------------------------------------------------------------+
//| Abre a cesta de 7 pares no MT5                                  |
//+------------------------------------------------------------------+
void OpenBasket(ENUM_EXEC_BIAS bias)
{
   int opened = 0;

   for(int i = 0; i < 7; i++)
   {
      string sym = g_pairs[i];
      string base = StringSubstr(sym, 0, 3);
      string quote = StringSubstr(sym, 3, 3);

      ENUM_ORDER_TYPE order_type;

      if(bias == BIAS_FORCA_BUY)
      {
         order_type = (base == g_ccy_str) ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
      }
      else // FRAQUEZA
      {
         order_type = (base == g_ccy_str) ? ORDER_TYPE_SELL : ORDER_TYPE_BUY;
      }

      m_symbol.Name(sym);
      m_symbol.RefreshRates();

      double price = (order_type == ORDER_TYPE_BUY) ? m_symbol.Ask() : m_symbol.Bid();
      double sl_price = ComputeCatastrophicSL(sym, order_type, price);
      string comment = StringFormat("CSS_%s_%s", g_ccy_str, (order_type == ORDER_TYPE_BUY ? "BUY" : "SELL"));

      bool sent;
      if(order_type == ORDER_TYPE_BUY)
         sent = m_trade.Buy(InpLotSize, sym, price, sl_price, 0, comment);
      else
         sent = m_trade.Sell(InpLotSize, sym, price, sl_price, 0, comment);

      // Correção: antes o retorno de Buy/Sell (e o retcode real do servidor
      // de negociação) não era checado — uma cesta parcial (ex.: 3 de 7
      // pares) virava posição direcional nua sem ninguém saber.
      if(sent && m_trade.ResultRetcode() == TRADE_RETCODE_DONE)
      {
         opened++;
         PrintFormat("  [+] %s %s %.2f @ %.5f | SL: %.5f | Magic: %d | Ticket: %d",
                     sym, (order_type == ORDER_TYPE_BUY ? "BUY" : "SELL"), InpLotSize, price, sl_price, g_magic, m_trade.ResultOrder());
      }
      else
      {
         PrintFormat("  [!] FALHA ao abrir %s %s | Retcode: %d (%s)",
                     sym, (order_type == ORDER_TYPE_BUY ? "BUY" : "SELL"), m_trade.ResultRetcode(), m_trade.ResultComment());
      }
   }

   if(opened < 7)
      PrintFormat("[CSS ROBOT %s] ATENÇÃO: cesta PARCIAL — apenas %d de 7 pares abertos. Revisar manualmente.", g_ccy_str, opened);
}

//+------------------------------------------------------------------+
//| Fecha todas as ordens abertas pertencentes a este robô          |
//+------------------------------------------------------------------+
void CloseBasket()
{
   int closed = 0;
   int failed = 0;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(m_position.SelectByIndex(i))
      {
         if(m_position.Magic() == g_magic)
         {
            ulong ticket = m_position.Ticket();
            string sym = m_position.Symbol();
            double profit = m_position.Profit();

            // Correção: retorno de PositionClose não era checado — falha de
            // fechamento ficava silenciosa.
            if(m_trade.PositionClose(ticket) && m_trade.ResultRetcode() == TRADE_RETCODE_DONE)
            {
               closed++;
               PrintFormat("  [x] Fechado Ticket #%d (%s) | Profit: $%.2f", ticket, sym, profit);
            }
            else
            {
               failed++;
               PrintFormat("  [!] FALHA ao fechar Ticket #%d (%s) | Retcode: %d (%s) — será retentado no próximo tick.",
                           ticket, sym, m_trade.ResultRetcode(), m_trade.ResultComment());
            }
         }
      }
   }

   if(failed > 0)
      PrintFormat("[CSS ROBOT %s] ATENÇÃO: %d posições não fecharam nesta tentativa.", g_ccy_str, failed);
}

//+------------------------------------------------------------------+
//| Conta posições abertas pertencentes a este Magic Number          |
//+------------------------------------------------------------------+
int CountOpenPositions()
{
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(m_position.SelectByIndex(i))
      {
         if(m_position.Magic() == g_magic)
            count++;
      }
   }
   return count;
}

//+------------------------------------------------------------------+
//| Extrai o valor de um campo string simples de um JSON "achatado", |
//| ex.: ExtractJsonStringField(content, "date") em {"date":"2026-08-23"} |
//+------------------------------------------------------------------+
string ExtractJsonStringField(string &content, string field)
{
   string needle = "\"" + field + "\"";
   int key_pos = StringFind(content, needle);
   if(key_pos < 0) return "";
   int colon_pos = StringFind(content, ":", key_pos);
   if(colon_pos < 0) return "";
   int quote1 = StringFind(content, "\"", colon_pos + 1);
   if(quote1 < 0) return "";
   int quote2 = StringFind(content, "\"", quote1 + 1);
   if(quote2 < 0) return "";
   return StringSubstr(content, quote1 + 1, quote2 - quote1 - 1);
}

//+------------------------------------------------------------------+
//| Lê decisão automática do CSS via arquivo JSON gravado às 21:02 na |
//| MQL5/Files desta mesma instância /portable (Python grava direto  |
//| aqui — instância dedicada ao MFC, sem FILE_COMMON). brt_now é    |
//| usado só pra checar o frescor do sinal (campo "date") contra o   |
//| dia de hoje em BRT — evita operar a direção de ontem se o lado   |
//| Python não rodou às 21:02 por qualquer motivo.                   |
//+------------------------------------------------------------------+
ENUM_EXEC_BIAS ReadAutoBiasFromJson(string ccy, MqlDateTime &brt_now)
{
   int handle = FileOpen("CSS_Portfolio_Signals.json", FILE_READ | FILE_TXT);
   if(handle == INVALID_HANDLE)
   {
      PrintFormat("[CSS ROBOT %s] Arquivo CSS_Portfolio_Signals.json não encontrado em MQL5/Files.", ccy);
   }

   if(handle != INVALID_HANDLE)
   {
      string content = "";
      while(!FileIsEnding(handle))
      {
         content += FileReadString(handle);
      }
      FileClose(handle);

      // Correção: o sinal grava "date"/"session_id"/"timestamp", mas isso
      // nunca era conferido — se o lado Python não rodasse às 21:02, o EA
      // podia operar tranquilamente a direção do dia anterior.
      string signal_date = ExtractJsonStringField(content, "date");
      string today_str = StringFormat("%04d-%02d-%02d", brt_now.year, brt_now.mon, brt_now.day);
      if(signal_date == "" || signal_date != today_str)
      {
         PrintFormat("[CSS ROBOT %s] Sinal ausente ou desatualizado (data do sinal: '%s', hoje BRT: '%s') — tratando como NEUTRO.",
                     ccy, signal_date, today_str);
         return BIAS_AUTO;
      }

      // Procurar pela moeda no JSON: "USD": { ... "direction": "BUY" ... }
      string ccy_key = "\"" + ccy + "\"";
      int ccy_pos = StringFind(content, ccy_key);
      if(ccy_pos >= 0)
      {
         int dir_pos = StringFind(content, "\"direction\"", ccy_pos);
         if(dir_pos >= 0 && dir_pos < ccy_pos + 300)
         {
            int buy_pos = StringFind(content, "\"BUY\"", dir_pos);
            int sell_pos = StringFind(content, "\"SELL\"", dir_pos);
            int neutral_pos = StringFind(content, "\"NEUTRAL\"", dir_pos);
            
            if(buy_pos >= 0 && (sell_pos < 0 || buy_pos < sell_pos) && (neutral_pos < 0 || buy_pos < neutral_pos) && buy_pos < dir_pos + 40)
            {
               PrintFormat("[CSS ROBOT %s] Sinal Detectado no JSON: FORÇA (BUY)", ccy);
               return BIAS_FORCA_BUY;
            }
            else if(sell_pos >= 0 && (buy_pos < 0 || sell_pos < buy_pos) && (neutral_pos < 0 || sell_pos < neutral_pos) && sell_pos < dir_pos + 40)
            {
               PrintFormat("[CSS ROBOT %s] Sinal Detectado no JSON: FRAQUEZA (SELL)", ccy);
               return BIAS_FRAQUEZA_SELL;
            }
            else
            {
               PrintFormat("[CSS ROBOT %s] Moeda NEUTRA no JSON. Nenhuma ordem será aberta.", ccy);
               return BIAS_AUTO;
            }
         }
      }
   }
   
   PrintFormat("[CSS ROBOT %s] Aviso: Não foi possível ler sinal para %s. Mantendo NEUTRO.", ccy, ccy);
   return BIAS_AUTO;
}

//+------------------------------------------------------------------+
//| Atualiza métricas flutuantes e excursão (MFE/MAE)                |
//+------------------------------------------------------------------+
void UpdateAuditMetrics()
{
   double total_profit = 0;
   double total_pips = 0;
   int open_count = 0;
   
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(m_position.SelectByIndex(i) && m_position.Magic() == g_magic)
      {
         total_profit += m_position.Profit() + m_position.Swap();
         open_count++;
         
         string sym = m_position.Symbol();
         double p_open = m_position.PriceOpen();
         double p_curr = m_position.PriceCurrent();
         bool is_jpy = (StringFind(sym, "JPY") >= 0);
         double pip_mult = is_jpy ? 100.0 : 10000.0;
         
         double pips = (m_position.PositionType() == POSITION_TYPE_BUY) ? (p_curr - p_open) * pip_mult : (p_open - p_curr) * pip_mult;
         total_pips += pips;
      }
   }
   
   g_floating_pnl = total_profit;
   g_floating_pips = total_pips;
   g_open_orders_count = open_count;
   
   if(open_count > 0)
   {
      if(total_profit > g_mfe_today) g_mfe_today = total_profit;
      if(total_profit < g_mae_today) g_mae_today = total_profit;
   }
}

//+------------------------------------------------------------------+
//| Cria ou atualiza o painel visual HUD no gráfico do MT5          |
//+------------------------------------------------------------------+
void UpdateChartDashboard()
{
   string bg_name = HUD_PREFIX + "BG";
   if(ObjectFind(0, bg_name) < 0)
   {
      ObjectCreate(0, bg_name, OBJ_RECTANGLE_LABEL, 0, 0, 0);
      ObjectSetInteger(0, bg_name, OBJPROP_XDISTANCE, 20);
      ObjectSetInteger(0, bg_name, OBJPROP_YDISTANCE, 25);
      ObjectSetInteger(0, bg_name, OBJPROP_XSIZE, 330);
      ObjectSetInteger(0, bg_name, OBJPROP_YSIZE, 220);
      ObjectSetInteger(0, bg_name, OBJPROP_BGCOLOR, C'14,20,34');
      ObjectSetInteger(0, bg_name, OBJPROP_BORDER_COLOR, C'0,229,255');
      ObjectSetInteger(0, bg_name, OBJPROP_BORDER_TYPE, BORDER_FLAT);
      ObjectSetInteger(0, bg_name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, bg_name, OBJPROP_BACK, false);
      ObjectSetInteger(0, bg_name, OBJPROP_SELECTABLE, false);
   }

   color pnl_color = (g_floating_pnl >= 0) ? C'0,230,118' : C'255,51,75';
   string pnl_str = StringFormat("%s$%.2f USD  (%+.1f pips)", (g_floating_pnl >= 0 ? "+" : ""), g_floating_pnl, g_floating_pips);
   string mfe_str = StringFormat("+$%.2f USD", g_mfe_today);
   string mae_str = StringFormat("-$%.2f USD", MathAbs(g_mae_today));

   MqlDateTime dt;
   GetBrtAdjustedTime(dt);
   bool is_overnight = (dt.hour >= 21 || dt.hour < 8);
   string status_str = is_overnight ? "🔴 SESSÃO AO VIVO (21:05 ➔ 08:00)" : "🟢 SESSÃO ENCERRADA (AGUARDANDO 21:05)";
   color status_col = is_overnight ? C'255,51,75' : C'0,230,118';

   CreateOrUpdateLabel("TITLE", StringFormat("⚡ PORTFÓLIO CSS — %s [#%d]", g_ccy_str, g_magic), 35, 38, C'0,229,255', 10, true);
   CreateOrUpdateLabel("STATUS", status_str, 35, 62, status_col, 8, false);
   CreateOrUpdateLabel("DECISION", StringFormat("Decisão das 21:02:  %s", g_decision_str), 35, 84, C'255,214,0', 9, true);
   CreateOrUpdateLabel("ORDERS", StringFormat("Ordens no MT5:   %d / 7 pares", g_open_orders_count), 35, 108, C'241,245,249', 9, false);
   CreateOrUpdateLabel("PNL", StringFormat("PnL Flutuante:     %s", pnl_str), 35, 132, pnl_color, 10, true);
   CreateOrUpdateLabel("MFE", StringFormat("MFE (Pico Favorável):   %s", mfe_str), 35, 156, C'0,230,118', 8, false);
   CreateOrUpdateLabel("MAE", StringFormat("MAE (Drawdown Máx):   %s", mae_str), 35, 176, C'255,82,102', 8, false);
   CreateOrUpdateLabel("FOOTER", "🌐 Sincronizado com CSS Web Platform", 35, 204, C'148,163,184', 7, false);

   ChartRedraw(0);
}

//+------------------------------------------------------------------+
//| Helper para criar/atualizar labels de texto no HUD               |
//+------------------------------------------------------------------+
void CreateOrUpdateLabel(string tag, string text, int x, int y, color col, int font_size=9, bool is_bold=false)
{
   string name = HUD_PREFIX + tag;
   if(ObjectFind(0, name) < 0)
   {
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetString(0, name, OBJPROP_FONT, "Segoe UI");
   }
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, name, OBJPROP_COLOR, col);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, font_size);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
}

//+------------------------------------------------------------------+
//| Exporta telemetria em tempo real para JSON, na MQL5/Files desta  |
//| instância /portable (sem FILE_COMMON — dedicada ao MFC)          |
//+------------------------------------------------------------------+
void ExportTelemetryJson()
{
   int handle = FileOpen("CSS_Live_Portfolio_" + g_ccy_str + ".json", FILE_WRITE | FILE_TXT);
   if(handle != INVALID_HANDLE)
   {
      string json = StringFormat("{\"currency\":\"%s\",\"magic\":%d,\"open_positions\":%d,\"floating_pnl_usd\":%.2f,\"floating_pips\":%.1f,\"mfe_usd\":%.2f,\"mae_usd\":%.2f,\"timestamp\":\"%s\"}",
                                 g_ccy_str, g_magic, g_open_orders_count, g_floating_pnl, g_floating_pips, g_mfe_today, g_mae_today, TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS));
      FileWriteString(handle, json);
      FileClose(handle);
   }
}

//+------------------------------------------------------------------+
//| Remove todos os objetos do HUD do gráfico                       |
//+------------------------------------------------------------------+
void DeleteChartDashboard()
{
   ObjectsDeleteAll(0, HUD_PREFIX);
   ChartRedraw(0);
}
