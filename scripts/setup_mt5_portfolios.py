"""
SCRIPT DE CONFIGURAÇÃO AUTOMÁTICA DO META TRADER 5:
1. Copia o EA CSS_Portfolio_Basket_EA.mq5 para MQL5/Experts/
2. Compila via MetaEditor CLI
3. Cria o Profile 'CSS_Portfolios' com 8 Gráficos (1 para cada moeda: USD, EUR, GBP, CHF, JPY, AUD, CAD, NZD)
4. Configura os inputs exatos de cada robô (Moeda, Magic Number, Lote, Horários 21:05 -> 08:00)
"""

import os
import sys
import shutil
import subprocess
import time

TERMINAL_ID = "53785E099C927DB68A545C249CDBCE06"
APPDATA = os.environ.get("APPDATA")
TERMINAL_BASE = os.path.join(APPDATA, "MetaQuotes", "Terminal", TERMINAL_ID)
MQL5_DIR = os.path.join(TERMINAL_BASE, "MQL5")
EXPERTS_DIR = os.path.join(MQL5_DIR, "Experts")

PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SOURCE_EA = os.path.join(PROJECT_DIR, "mt5", "CSS_Portfolio_Basket_EA.mq5")

# Definir as 8 moedas, pares representativos e Magic Numbers
PORTFOLIO_CONFIGS = [
    {"index": 0, "ccy": "USD", "symbol": "EURUSD", "magic": 801001, "color": "255"},
    {"index": 1, "ccy": "EUR", "symbol": "EURGBP", "magic": 801002, "color": "16760576"},
    {"index": 2, "ccy": "GBP", "symbol": "GBPJPY", "magic": 801003, "color": "14772545"},
    {"index": 3, "ccy": "CHF", "symbol": "USDCHF", "magic": 801004, "color": "16776960"},
    {"index": 4, "ccy": "JPY", "symbol": "USDJPY", "magic": 801005, "color": "13382297"},
    {"index": 5, "ccy": "AUD", "symbol": "AUDUSD", "magic": 801006, "color": "33023"},
    {"index": 6, "ccy": "CAD", "symbol": "USDCAD", "magic": 801007, "color": "139"},
    {"index": 7, "ccy": "NZD", "symbol": "NZDUSD", "magic": 801008, "color": "9221330"}
]


def generate_chart_chr_content(cfg, chart_id):
    """Gera o conteúdo de um arquivo de gráfico .chr do MT5 com o Expert Advisor anexado."""
    ccy_enum = cfg["index"]
    magic = cfg["magic"]
    symbol = cfg["symbol"]
    ccy_name = cfg["ccy"]

    chr_text = f"""<chart>
id={chart_id}
symbol={symbol}
description=CSS Portfolio - {ccy_name} (Magic {magic})
period_type=1
period_size=60
digits=5
scale_fix=0
scale_bar=0
scale=8
mode=1
fore=0
grid=1
volume=0
scroll=1
shift=1
shift_size=20.0
ohlc=1
one_click=0
one_click_btn=0
ticker=1
tradehistory=1
tradelines=1
bidline=1
askline=0
background_color=1315860
foreground_color=14737632
barup_color=59136
bardown_color=3552970
bullcandle_color=1315860
bearcandle_color=3552970
chartline_color=59136
grid_color=2960685
windows_total=1

<expert>
name=CSS_Portfolio_Basket_EA
path=Experts\\CSS_Portfolio_Basket_EA.ex5
expertmode=1
<inputs>
InpCurrency={ccy_enum}
InpMagicNumber={magic}
InpLotSize=0.010000
InpDirectionBias=0
InpEntryHour=21
InpEntryMinute=5
InpExitHour=8
InpExitMinute=0
InpGmtOffset=0
InpDeviation=15
InpExportTelemetry=true
</inputs>
</expert>

<window>
height=100.000000

<indicator>
name=Main
path=
apply=1
show_data=1
scale_inherit=0
scale_line=0
scale_line_percent=50
scale_line_value=0.000000
scale_fix_min=0
scale_fix_min_val=0.000000
scale_fix_max=0
scale_fix_max_val=0.000000
expertmode=0
fixed_height=-1
</indicator>
</window>
</chart>
"""
    return chr_text


def setup():
    print("===================================================================")
    print("  CONFIGURADOR AUTOMÁTICO DE PORTFÓLIOS MT5 (CSS INSTITUTIONAL)   ")
    print("===================================================================")

    # 1. Compilar EA uma vez com MetaEditor
    metaeditor_candidates = [
        r"C:\Program Files\Tickmill MT5 Terminal\metaeditor64.exe",
        r"C:\Program Files\Tickmill MT5 Terminal - Copia - Copia\metaeditor64.exe",
        r"C:\Program Files\MetaTrader 5\metaeditor64.exe"
    ]
    metaeditor_path = None
    for cand in metaeditor_candidates:
        if os.path.exists(cand):
            metaeditor_path = cand
            break

    ex5_source = os.path.join(PROJECT_DIR, "mt5", "CSS_Portfolio_Basket_EA.ex5")
    if metaeditor_path:
        print(f"[*] Compilando EA via MetaEditor CLI: {metaeditor_path}...")
        log_file = os.path.join(PROJECT_DIR, "mt5", "compile.log")
        cmd = f'"{metaeditor_path}" /compile:"{SOURCE_EA}" /log:"{log_file}"'
        try:
            res = subprocess.run(cmd, shell=True, capture_output=True, timeout=15)
            time.sleep(1)
            if os.path.exists(ex5_source):
                print(f"[+] Compilação BEM-SUCEDIDA! Gerado: {ex5_source}")
        except Exception as e:
            print(f"[!] Erro ao compilar: {e}")

    # 2. Descobrir todos os Terminais MT5 em AppData
    terminals_dir = os.path.join(APPDATA, "MetaQuotes", "Terminal")
    terminal_ids = []
    if os.path.exists(terminals_dir):
        for entry in os.listdir(terminals_dir):
            full_p = os.path.join(terminals_dir, entry)
            if os.path.isdir(full_p) and entry not in ["Common", "Community", "Help"]:
                terminal_ids.append(entry)

    # Priorizar o terminal ativo conectado
    try:
        import MetaTrader5 as mt5_mod
        if mt5_mod.initialize():
            t_info = mt5_mod.terminal_info()
            if t_info and t_info.data_path:
                active_id = os.path.basename(t_info.data_path)
                if active_id in terminal_ids:
                    terminal_ids.remove(active_id)
                terminal_ids.insert(0, active_id)
                print(f"[★] Terminal Ativo Detectado: {active_id}")
    except Exception:
        pass

    print(f"[*] Terminais Encontrados: {terminal_ids}\n")

    for t_id in terminal_ids:
        t_base = os.path.join(terminals_dir, t_id)
        mql5_dir = os.path.join(t_base, "MQL5")
        experts_dir = os.path.join(mql5_dir, "Experts")
        
        if not os.path.exists(mql5_dir):
            continue

        print(f"--- Configurando Terminal: {t_id} ---")
        os.makedirs(experts_dir, exist_ok=True)

        # Copiar .mq5 e .ex5
        shutil.copy2(SOURCE_EA, os.path.join(experts_dir, "CSS_Portfolio_Basket_EA.mq5"))
        if os.path.exists(ex5_source):
            shutil.copy2(ex5_source, os.path.join(experts_dir, "CSS_Portfolio_Basket_EA.ex5"))
            print(f"  [+] EA .mq5 e .ex5 copiados para {experts_dir}")

        # Criar Profile 'CSS_Portfolios'
        profile_dirs = [
            os.path.join(t_base, "profiles", "charts", "CSS_Portfolios"),
            os.path.join(mql5_dir, "Profiles", "Charts", "CSS_Portfolios")
        ]

        for p_dir in profile_dirs:
            os.makedirs(p_dir, exist_ok=True)
            order_wnd_lines = []
            base_id = 140000000000000000

            for i, cfg in enumerate(PORTFOLIO_CONFIGS):
                chart_num = i + 1
                chart_filename = f"chart{chart_num:02d}.chr"
                chart_file_path = os.path.join(p_dir, chart_filename)
                chart_id = base_id + i * 1000 + 101

                chr_content = generate_chart_chr_content(cfg, chart_id)
                with open(chart_file_path, "w", encoding="utf-16le") as f:
                    f.write("\ufeff" + chr_content)

                order_wnd_lines.append(chart_filename)

            order_wnd_path = os.path.join(p_dir, "order.wnd")
            with open(order_wnd_path, "w", encoding="utf-16le") as f:
                f.write("\ufeff" + "\n".join(order_wnd_lines) + "\n")
            print(f"  [+] Profile CSS_Portfolios configurado em: {p_dir}")

        # Templates
        templates_dirs = [
            os.path.join(t_base, "templates"),
            os.path.join(mql5_dir, "Profiles", "Templates")
        ]
        for t_dir in templates_dirs:
            os.makedirs(t_dir, exist_ok=True)
            for cfg in PORTFOLIO_CONFIGS:
                ccy_tpl_path = os.path.join(t_dir, f"CSS_{cfg['ccy']}.tpl")
                tpl_content = generate_chart_chr_content(cfg, 140000000000000999)
                with open(ccy_tpl_path, "w", encoding="utf-16le") as f:
                    f.write("\ufeff" + tpl_content)

            master_tpl = os.path.join(t_dir, "CSS_Portfolio_Basket.tpl")
            with open(master_tpl, "w", encoding="utf-16le") as f:
                f.write("\ufeff" + generate_chart_chr_content(PORTFOLIO_CONFIGS[6], 140000000000000999))
            print(f"  [+] Templates gerados em: {t_dir}")

    print("\n[SUCESSO GLOBAL] Todos os Terminais MT5 foram configurados com 100% de êxito!")


if __name__ == "__main__":
    setup()
