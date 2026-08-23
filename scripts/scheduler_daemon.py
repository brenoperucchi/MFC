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

from agents.portfolio_executor import generate_and_save_daily_signals
from web.real_portfolio_audit import real_audit_engine


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
    print("[*] Monitorando horários: 21:00:00 | 21:02:00 | 08:05:00 BRT\n")

    if test_mode:
        print("[*] MODO DE TESTE ATIVO: Executando todas as rotinas em sequência...")
        execute_phase_2102()
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
