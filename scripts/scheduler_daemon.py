"""
DAEMON DE AGENDAMENTO AUTOMÁTICO INSTITUCIONAL (CSS PORTFOLIOS & TRACK RECORD)
Ciclo Diário Rigoroso:
- 21:00:00 BRT: Rotina de Análise CSS Multi-Timeframe e Raio-X
- 21:02:00 BRT: Gravação dos Sinais Oficiais dos 8 Portfólios em FILE_COMMON
- 21:05:00 BRT: Disparo das ordens pelo MT5 (Executado pelos robôs locais)
- 08:00:00 BRT: Encerramento compulsório a mercado
- 08:05:00 BRT: Sincronização da Auditoria de Deals Reais e Auto-Deploy no Firebase Hosting
"""

import os
import sys
import time
import subprocess
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from agents.portfolio_executor import (
    generate_and_save_daily_signals, open_portfolio_basket, SIGNALS_FILE
)
from web.real_portfolio_audit import real_audit_engine
import json


def run_command(cmd, desc=""):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Iniciando: {desc} -> {cmd}")
    try:
        res = subprocess.run(cmd, shell=True, cwd=BASE_DIR, capture_output=True, text=True, timeout=120)
        if res.returncode == 0:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [OK] {desc}")
            return True
        else:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [ERRO] {desc}: {res.stderr[:300]}")
            return False
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] [EXCEÇÃO] {desc}: {e}")
        return False


def execute_phase_2100():
    """21:00 BRT - Executa Análise CSS Diária e Geração de Relatórios."""
    print("\n" + "="*70)
    print(f"  [ROUTINE 21:00 BRT] EXECUTANDO ANÁLISE CSS MULTI-TIMEFRAME")
    print("="*70)
    cmd = f'"{sys.executable}" "{os.path.join(BASE_DIR, "daily_css_routine.py")}"'
    run_command(cmd, "Rotina Diária CSS 21h")


def execute_phase_2102():
    """21:02 BRT - Grava os Sinais dos 8 Portfólios para o MT5."""
    print("\n" + "="*70)
    print(f"  [ROUTINE 21:02 BRT] GRAVAÇÃO DOS SINAIS DE PORTFÓLIO (FILE_COMMON)")
    print("="*70)
    try:
        signals = generate_and_save_daily_signals()
        print(f"[+] Sinais gerados e sincronizados com MT5! Portfólios: {len(signals.get('portfolios', {}))}")
    except Exception as e:
        print(f"[-] Erro ao gravar sinais: {e}")


def execute_phase_2105():
    """21:05 BRT - Abre as cestas no MT5 pelo lado Python, lendo o sinal
    gravado às 21:02 (arquitetura recomendada pela revisão de 23/08: Python
    decide e abre, o EA vira guardião do fechamento — ver
    whatsapp-tools/PLANO_IMPLEMENTACAO_MFC.md, Seção 1). Todas as travas de
    segurança (kill switch, conta demo, idempotência, colisão de símbolo em
    conta netting, stop-loss catastrófico) vivem dentro de
    open_portfolio_basket() e são checadas por cesta, individualmente."""
    print("\n" + "="*70)
    print(f"  [ROUTINE 21:05 BRT] ABERTURA DAS CESTAS DE PORTFÓLIO (PYTHON)")
    print("="*70)
    try:
        with open(SIGNALS_FILE, "r", encoding="utf-8") as f:
            signals_payload = json.load(f)
    except Exception as e:
        print(f"[-] Não foi possível ler o sinal de {SIGNALS_FILE}: {e}. Nenhuma cesta será aberta.")
        return

    portfolios = signals_payload.get("portfolios", {})
    opened, refused, neutral = [], [], []

    for ccy, sig in portfolios.items():
        direction = sig.get("direction", "NEUTRAL")
        status = sig.get("status", "BLOCKED")
        if status != "ACTIVE" or direction not in ("BUY", "SELL"):
            neutral.append(ccy)
            continue

        res = open_portfolio_basket(ccy, direction)
        if res.get("success"):
            opened.append(ccy)
            print(f"[+] {ccy}: cesta aberta ({res.get('opened_count')}/{res.get('total_pairs')} pares).")
        else:
            refused.append((ccy, res.get("error"), res.get("message")))
            print(f"[!] {ccy}: abertura recusada — {res.get('error')}: {res.get('message')}")

    print(f"\n[RESUMO 21:05] Abertas: {opened} | Recusadas: {[c for c, *_ in refused]} | Neutras: {neutral}")


def execute_phase_0805():
    """08:05 BRT - Auditoria Noturna dos Deals Reais e Auto-Deploy no Firebase."""
    print("\n" + "="*70)
    print(f"  [ROUTINE 08:05 BRT] AUDITORIA DE DEALS MT5 & AUTO-DEPLOY NO FIREBASE")
    print("="*70)
    try:
        # 1. Sincronizar deals reais do MT5
        print("[*] Sincronizando deals reais fechadas pelo MT5...")
        real_audit_engine.sync_mt5_deals(days_back=60)
        
        # 2. Gerar Bundle do Firebase
        build_cmd = f'"{sys.executable}" "{os.path.join(BASE_DIR, "scripts", "build_firebase_bundle.py")}"'
        if run_command(build_cmd, "Gerar Bundle Firebase"):
            # 3. Publicar no Firebase Hosting
            run_command("firebase deploy --only hosting", "Deploy no Firebase Hosting")
    except Exception as e:
        print(f"[-] Erro na rotina 08:05: {e}")


def run_daemon_loop(test_mode=False):
    print("===================================================================")
    print("  DAEMON DE AGENDAMENTO INSTITUCIONAL ATIVO (CSS PORTFOLIOS)      ")
    print("===================================================================")
    print(f"[*] Horário Local Atual: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("[*] Monitorando horários: 21:00:00 | 21:02:00 | 21:05:00 | 08:05:00 BRT\n")

    if test_mode:
        print("[*] MODO DE TESTE ATIVO: Executando todas as rotinas em sequência...")
        execute_phase_2102()
        execute_phase_2105()
        execute_phase_0805()
        return

    last_trigger = {}

    while True:
        now = datetime.now()
        cur_date = now.strftime("%Y-%m-%d")
        cur_hour = now.hour
        cur_min = now.minute

        # 1. Gatilho 21:00
        if cur_hour == 21 and cur_min == 0:
            key = f"{cur_date}_2100"
            if key not in last_trigger:
                execute_phase_2100()
                last_trigger[key] = True

        # 2. Gatilho 21:02
        if cur_hour == 21 and cur_min == 2:
            key = f"{cur_date}_2102"
            if key not in last_trigger:
                execute_phase_2102()
                last_trigger[key] = True

        # 2b. Gatilho 21:05 — abertura das cestas pelo lado Python (ver
        # execute_phase_2105 e Seção 1 de PLANO_IMPLEMENTACAO_MFC.md). Com o
        # EA no modo padrão (InpEaOpensBasket=false), este é o único caminho
        # que efetivamente abre posição — se o EA estiver com
        # InpEaOpensBasket=true em algum gráfico, os dois tentariam abrir; a
        # idempotência dentro de open_portfolio_basket() e o
        # CountOpenPositions()==0 do lado EA evitam duplicar, mas os dois
        # caminhos não devem ficar ativos ao mesmo tempo por design.
        if cur_hour == 21 and cur_min == 5:
            key = f"{cur_date}_2105"
            if key not in last_trigger:
                execute_phase_2105()
                last_trigger[key] = True

        # 3. Gatilho 08:05
        if cur_hour == 8 and cur_min == 5:
            key = f"{cur_date}_0805"
            if key not in last_trigger:
                execute_phase_0805()
                last_trigger[key] = True

        # Dormir 25 segundos
        time.sleep(25)


if __name__ == "__main__":
    is_test = "--test" in sys.argv
    run_daemon_loop(test_mode=is_test)
