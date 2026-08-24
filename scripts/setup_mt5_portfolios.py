"""
CONFIGURAÇÃO DOS 8 GRÁFICOS DO MT5 (um robô de portfólio por moeda).

Gera o profile "CSS_Portfolios" com 8 arquivos .chr — cada um já com o EA
anexado e os inputs corretos —, mais os templates .tpl. O terminal carrega
esse profile no boot, o que torna o setup inteiro scriptável: não é preciso
arrastar o EA pra gráfico nenhum na mão.

MIRA UMA INSTÂNCIA SÓ, a portable dedicada ao MFC (derivada de
CSS_MT5_TERMINAL_PATH). A versão anterior deste script varria
%APPDATA%\\MetaQuotes\\Terminal\\ e instalava o EA em TODOS os terminais
encontrados — nesta máquina são 5, de outras estratégias e outras contas.
Nunca reintroduzir essa varredura.

O .ex5 precisa existir. Compile antes com scripts/compile_ea_remote.sh
(sync + MetaEditor /portable /compile via SSH).
"""

import os
import sys
import shutil

PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from web.css_service import MT5_PATH, MT5_SYMBOL_SUFFIX

# Conta esperada: alimenta o InpExpectedLogin do EA, que alerta no log se
# ele subir num terminal logado noutra conta (esta maquina roda varios).
EXPECTED_LOGIN = os.environ.get("CSS_MT5_EXPECTED_LOGIN", "0").strip() or "0"

# Onde o compile_ea_remote.sh deposita o EA compilado, relativo ao MQL5/.
EXPERTS_SUBDIR = os.path.join("Experts", "MFC")
EA_NAME = "CSS_Portfolio_Basket_EA"
BOM_CHAR = "\ufeff"

SOURCE_EA = os.path.join(PROJECT_DIR, "mt5", f"{EA_NAME}.mq5")

# Moeda, gráfico representativo e magic. O símbolo do gráfico é só o pano de
# fundo visual — o EA opera os 7 pares da moeda, não o símbolo do gráfico.
PORTFOLIO_CONFIGS = [
    {"index": 0, "ccy": "USD", "symbol": "EURUSD", "magic": 801001},
    {"index": 1, "ccy": "EUR", "symbol": "EURGBP", "magic": 801002},
    {"index": 2, "ccy": "GBP", "symbol": "GBPJPY", "magic": 801003},
    {"index": 3, "ccy": "CHF", "symbol": "USDCHF", "magic": 801004},
    {"index": 4, "ccy": "JPY", "symbol": "USDJPY", "magic": 801005},
    {"index": 5, "ccy": "AUD", "symbol": "AUDUSD", "magic": 801006},
    {"index": 6, "ccy": "CAD", "symbol": "USDCAD", "magic": 801007},
    {"index": 7, "ccy": "NZD", "symbol": "NZDUSD", "magic": 801008},
]


def terminal_base():
    """Diretório de dados da instância portable (mesma pasta do terminal64.exe)."""
    if not MT5_PATH:
        return None
    base = os.path.dirname(MT5_PATH)
    return base or None


def generate_chart_chr_content(cfg, chart_id):
    """Conteúdo de um .chr do MT5 com o EA anexado e os inputs desta moeda."""
    # O símbolo do gráfico também precisa do sufixo da corretora — sem ele o
    # gráfico abre vazio (EURUSD não existe; EURUSDm sim).
    symbol = cfg["symbol"] + MT5_SYMBOL_SUFFIX
    ea_rel_path = os.path.join(EXPERTS_SUBDIR, f"{EA_NAME}.ex5").replace(os.sep, "\\")

    return f"""<chart>
id={chart_id}
symbol={symbol}
description=CSS Portfolio - {cfg['ccy']} (Magic {cfg['magic']})
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
name={EA_NAME}
path={ea_rel_path}
expertmode=1
<inputs>
InpCurrency={cfg['index']}
InpMagicNumber={cfg['magic']}
InpLotSize=0.010000
InpDirectionBias=0
InpEntryHour=21
InpEntryMinute=5
InpExitHour=8
InpExitMinute=0
InpCloseGraceMin=15
InpGmtOffset=-3
InpExpectedLogin={EXPECTED_LOGIN}
InpSymbolSuffix={MT5_SYMBOL_SUFFIX}
InpDeviation=15
InpExportTelemetry=true
InpCatastrophicSLPips=150
InpEaOpensBasket=false
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


def _write_profile(profile_dir):
    """Escreve os 8 .chr + order.wnd. Os .chr do MT5 sao UTF-16LE com BOM."""
    os.makedirs(profile_dir, exist_ok=True)
    base_id = 140000000000000000
    chart_files = []
    for i, cfg in enumerate(PORTFOLIO_CONFIGS):
        chart_filename = f"chart{i + 1:02d}.chr"
        chr_content = generate_chart_chr_content(cfg, base_id + i * 1000 + 101)
        with open(os.path.join(profile_dir, chart_filename), "w", encoding="utf-16le") as f:
            f.write(BOM_CHAR + chr_content)
        chart_files.append(chart_filename)
        print(f"  [+] {chart_filename}  {cfg['ccy']} (magic {cfg['magic']}) "
              f"em {cfg['symbol']}{MT5_SYMBOL_SUFFIX}")
    with open(os.path.join(profile_dir, "order.wnd"), "w", encoding="utf-16le") as f:
        f.write(BOM_CHAR + "\n".join(chart_files) + "\n")


def setup(out_dir=None):
    """out_dir: gera o profile num diretorio qualquer em vez de dentro da
    instancia — usado pra montar os arquivos aqui e enviar por SSH pra maquina
    do MT5, sem precisar do repositorio inteiro la."""
    print("=" * 67)
    print("  CONFIGURADOR DOS 8 GRÁFICOS — INSTÂNCIA MT5 DEDICADA AO MFC")
    print("=" * 67)

    if out_dir:
        profile_dir = os.path.abspath(out_dir)
        print(f"[*] Modo geracao: escrevendo o profile em {profile_dir}")
        print(f"[*] Sufixo simbolo: {MT5_SYMBOL_SUFFIX!r}")
        _write_profile(profile_dir)
        return 0

    base = terminal_base()
    if not base or not os.path.isdir(base):
        print(f"[-] Instância MT5 não encontrada a partir de MT5_PATH={MT5_PATH!r}.")
        print("    Configure CSS_MT5_TERMINAL_PATH no .env (ver .env.example).")
        return 1

    mql5_dir = os.path.join(base, "MQL5")
    experts_dir = os.path.join(mql5_dir, EXPERTS_SUBDIR)
    ex5_path = os.path.join(experts_dir, f"{EA_NAME}.ex5")

    print(f"[*] Instância    : {base}")
    print(f"[*] Sufixo símbolo: {MT5_SYMBOL_SUFFIX!r}")

    if not os.path.exists(ex5_path):
        print(f"[-] EA compilado não encontrado: {ex5_path}")
        print("    Rode antes: scripts/compile_ea_remote.sh")
        return 1
    print(f"[+] EA compilado : {ex5_path}")

    # Mantém o .mq5 ao lado do .ex5 (conveniência pra abrir no MetaEditor).
    os.makedirs(experts_dir, exist_ok=True)
    if os.path.exists(SOURCE_EA):
        shutil.copy2(SOURCE_EA, os.path.join(experts_dir, f"{EA_NAME}.mq5"))

    # Build 6140 usa MQL5/Profiles/Charts/. O antigo <base>/profiles/charts/
    # existe mas nao e' lido — escrever la nao surte efeito nenhum
    # (verificado na instancia real: o terminal ignorou por completo).
    profile_dir = os.path.join(mql5_dir, "Profiles", "Charts", "Default")
    _write_profile(profile_dir)

    print(f"\n[+] Profile 'CSS_Portfolios' gravado em: {profile_dir}")
    print("[!] Para o terminal carregar: reinicie-o e selecione esse profile")
    print("    (Arquivo > Perfis > CSS_Portfolios), ou reinicie o serviço se")
    print("    o profile já estiver ativo: systemctl --user restart css-mt5-mfc")
    return 0


if __name__ == "__main__":
    # --out <dir>: gera o profile localmente pra enviar por SSH depois.
    out = None
    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out") + 1]
    sys.exit(setup(out_dir=out))
