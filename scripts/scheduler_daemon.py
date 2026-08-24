"""
DAEMON DE AGENDAMENTO AUTOMÁTICO INSTITUCIONAL (CSS PORTFOLIOS & TRACK RECORD)
Ciclo Diário Rigoroso:
- 21:00:00 BRT: Rotina de Análise CSS Multi-Timeframe e Raio-X
- 21:02:00 BRT: Gravação dos Sinais Oficiais dos 8 Portfólios (MQL5/Files da instância)
- 21:05:00 BRT: Abertura das cestas pelo lado Python (o EA é guardião do fechamento)
- 08:00:00 BRT: Encerramento compulsório a mercado (Python + EA, redundantes)
- 08:05:00 BRT: Sincronização da Auditoria de Deals Reais e Auto-Deploy no Firebase Hosting
- 08:10:00 BRT: Reconciliação — alerta no Telegram se sobrou posição órfã
"""

import os
import sys
import threading
import time
import subprocess
import re
from datetime import datetime, timedelta

# line_buffering é essencial, não cosmético: sem ele o reconfigure() reativa o
# buffer de bloco e anula o PYTHONUNBUFFERED do wrapper — rodando sob systemd,
# a saída fica presa no buffer e o journal não mostra NADA do que o daemon fez
# às 21:05. Um daemon que opera dinheiro sem deixar rastro legível é pior que
# um que não roda.
sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
sys.stderr.reconfigure(encoding='utf-8', line_buffering=True)

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from agents.portfolio_executor import (
    generate_and_save_daily_signals, open_portfolio_basket, close_all_portfolios,
    SIGNALS_FILE
)
from web.real_portfolio_audit import real_audit_engine

DATA_DIR = os.path.join(BASE_DIR, "data")
# Estado atual (some quando resolve) e histórico da reconciliação.
RECONCILE_ALERT = os.path.join(DATA_DIR, "RECONCILE_ALERT.json")
RECONCILE_LOG = os.path.join(DATA_DIR, "reconcile_alerts.log")
import json


def run_command(cmd, desc="", timeout=120):
    """timeout configurável: a rotina das 21h faz ~17 uploads de PNG com sleeps,
    140 séries do MT5 e 9 figuras matplotlib — 120s mata isso no meio.
    Chamadas com trabalho pesado devem passar um teto próprio."""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Iniciando: {desc} -> {cmd}")
    try:
        res = subprocess.run(cmd, shell=True, cwd=BASE_DIR, capture_output=True, text=True, timeout=timeout)
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
    # 10 min: teto generoso pra 17 uploads de PNG + 140 séries MT5 + 9
    # figuras matplotlib. Esta rotina só gera relatório/dashboard/Telegram —
    # não grava mais o sinal oficial (isso é execute_phase_2102, abaixo,
    # independente e mais rápido). Ver Trilha A, achado F3.
    run_command(cmd, "Rotina Diária CSS 21h", timeout=600)


def execute_phase_2102():
    """21:02 BRT - Grava os Sinais dos 8 Portfólios para o MT5.

    Retorna True só se a gravação teve êxito de ponta a ponta — usado por
    run_daemon_loop pra decidir se é seguro abrir a cesta das 21:05 com o
    sinal recém-gravado (achado em revisão: um Event que só sinalizava 'a
    thread terminou', sem checar sucesso, deixaria 21:05 seguir em frente
    mesmo que a gravação tivesse lançado exceção)."""
    print("\n" + "="*70)
    print(f"  [ROUTINE 21:02 BRT] GRAVAÇÃO DOS SINAIS DE PORTFÓLIO (MQL5/Files)")
    print("="*70)
    try:
        signals = generate_and_save_daily_signals()
        print(f"[+] Sinais gerados e sincronizados com MT5! Portfólios: {len(signals.get('portfolios', {}))}")
        return True
    except Exception as e:
        print(f"[-] Erro ao gravar sinais: {e}")
        return False


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

    # Frescor: o arquivo é persistente (e versionado). Se a fase 21:02 falhou,
    # ou se o daemon subiu depois dela, o que está em disco é o sinal de outro
    # dia — abrir com ele significa operar a direção de ontem.
    signal_date = signals_payload.get("date")
    today_str = datetime.now().strftime("%Y-%m-%d")
    if signal_date != today_str:
        print(f"[-] Sinal com data '{signal_date}', hoje é '{today_str}' — sinal desatualizado. "
              f"Nenhuma cesta será aberta.")
        return

    # Origem: sinal derivado de cache/série simulada nunca vira ordem real.
    if not signals_payload.get("mt5_connected", False):
        print("[-] Sinal marcado como não-operável (mt5_connected=false: cache ou dado simulado). "
              "Nenhuma cesta será aberta.")
        return

    portfolios = signals_payload.get("portfolios", {})
    opened, refused, neutral, partial = [], [], [], []

    for ccy, sig in portfolios.items():
        direction = sig.get("direction", "NEUTRAL")
        status = sig.get("status", "BLOCKED")
        if status != "ACTIVE" or direction not in ("BUY", "SELL"):
            neutral.append(ccy)
            continue

        # Isolamento por moeda: uma exceção numa cesta não pode abortar as
        # demais nem derrubar o daemon (que ainda precisa rodar o 08:00).
        try:
            res = open_portfolio_basket(ccy, direction)
        except Exception as e:
            refused.append((ccy, "exception", str(e)))
            print(f"[!] {ccy}: exceção na abertura — {e}")
            continue

        if res.get("success"):
            opened_count = res.get("opened_count", 0)
            total_pairs = res.get("total_pairs", 0)
            if opened_count < total_pairs:
                partial.append((ccy, opened_count, total_pairs))
                print(f"[!] {ccy}: cesta PARCIAL ({opened_count}/{total_pairs} pares) — "
                      f"exposição direcional não diversificada. REVISAR MANUALMENTE.")
            else:
                opened.append(ccy)
                print(f"[+] {ccy}: cesta aberta ({opened_count}/{total_pairs} pares).")
        else:
            refused.append((ccy, res.get("error"), res.get("message")))
            print(f"[!] {ccy}: abertura recusada — {res.get('error')}: {res.get('message')}")

    print(f"\n[RESUMO 21:05] Abertas: {opened} | Parciais: {partial} | "
          f"Recusadas: {[c for c, *_ in refused]} | Neutras: {neutral}")


def execute_phase_0800():
    """08:00 BRT - Encerramento compulsório a mercado, pelo lado Python.

    Segunda rede: o EA guardião continua sendo o fechador dentro do terminal
    (sobrevive ao Python morrer), mas ele depende de estar anexado, com
    AutoTrading ligado, no terminal certo — e sinaliza falha só por Print, que
    ninguém lê. Fechar é idempotente e redutor de risco, então ter os dois
    tentando é estritamente mais seguro que ter um só.

    Retorna True só quando o fechamento foi CONFIRMADO — o chamador usa isso
    pra decidir se retenta. O EA fecha de forma monotônica (retenta a cada 3s
    pra sempre); uma segunda rede que desiste na primeira falha não é rede."""
    print("\n" + "="*70)
    print(f"  [ROUTINE 08:00 BRT] ENCERRAMENTO COMPULSÓRIO A MERCADO (PYTHON)")
    print("="*70)
    try:
        res = close_all_portfolios()
    except Exception as e:
        print(f"[-] EXCEÇÃO no encerramento das 08:00: {e}")
        return False
    if res.get("success"):
        print(f"[+] Encerramento confirmado. Posições fechadas: {res.get('total_closed', 0)}")
        return True
    print(f"[-] ATENÇÃO — encerramento NÃO confirmado: {res.get('error')}: {res.get('message')}")
    if res.get("failures"):
        print(f"[-] Falhas por moeda: {res.get('failures')}")
    print("[-] Vai retentar no próximo ciclo (janela 08:00-08:04).")
    return False


def execute_phase_0810():
    """08:10 BRT - Reconciliador: última rede antes do dia começar.

    Às 08:00 o Python tenta fechar e o EA tenta fechar. Se as duas falharem
    (terminal fora do ar, AutoTrading desligado, conta errada, ordem rejeitada
    repetidamente), uma cesta direcional atravessa o dia inteiro protegida só
    pelo stop catastrófico — e ninguém fica sabendo, porque o único sinal era
    uma linha de print que ninguém lê às 08h.

    Este passo existe pra transformar esse silêncio em alarme: reconsulta o
    broker, tenta fechar o que sobrou e ALERTA no Telegram se ainda houver
    posição órfã. Alertar é o ponto — o fechamento aqui é a terceira
    tentativa, não a principal."""
    print("\n" + "="*70)
    print(f"  [ROUTINE 08:10 BRT] RECONCILIAÇÃO — POSIÇÕES ÓRFÃS")
    print("="*70)

    from agents.portfolio_executor import get_open_magics_and_symbols, MT5QueryError

    def alerta(texto, resolvido=False):
        """Alerta em três canais, do mais confiável pro mais conveniente.

        O arquivo vem PRIMEIRO e não depende de nada: o Telegram ainda é uma
        decisão em aberto e pode estar desligado, e um alerta que existe só
        como print no stdout de um daemon é o mesmo silêncio que este passo
        foi criado pra eliminar. RECONCILE_ALERT.json é o estado atual (some
        quando resolve); o .log guarda o histórico."""
        print(texto)
        limpo = re.sub(r"<[^>]+>", "", texto)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with open(RECONCILE_LOG, "a", encoding="utf-8") as f:
                f.write(f"[{stamp}] {limpo}\n")
        except OSError as e:
            print(f"[-] Falha ao gravar {RECONCILE_LOG}: {e}")
        try:
            if resolvido:
                if os.path.exists(RECONCILE_ALERT):
                    os.remove(RECONCILE_ALERT)
            else:
                with open(RECONCILE_ALERT, "w", encoding="utf-8") as f:
                    json.dump({"timestamp": stamp, "message": limpo}, f,
                              indent=2, ensure_ascii=False)
        except OSError as e:
            print(f"[-] Falha ao atualizar {RECONCILE_ALERT}: {e}")
        # Telegram é best-effort: decisão em aberto, e nunca pode ser o único
        # canal nem derrubar o reconciliador se estiver fora. Checa o retorno
        # (achado em revisão) — send_telegram_message() não lança em falha,
        # devolve {"success": False, ...} silenciosamente; sem checar isso,
        # um envio que falhou (token/chat_id ausente, erro de rede) parecia
        # ter dado certo nos logs.
        try:
            from web.telegram_service import send_telegram_message
            tg_res = send_telegram_message(texto)
            if not tg_res.get("success"):
                print(f"[-] Telegram não confirmou o envio ({tg_res.get('error')}) — "
                      f"alerta segue em {RECONCILE_ALERT} e {RECONCILE_LOG}.")
        except Exception as e:
            print(f"[-] Telegram indisponível ({e}) — alerta segue em {RECONCILE_ALERT}.")

    try:
        abertos = get_open_magics_and_symbols()
    except MT5QueryError as e:
        alerta(f"🚨 <b>MFC 08:10</b> — não foi possível CONSULTAR o broker ({e}). "
               f"Impossível confirmar se há posição aberta. Verificar manualmente.")
        return

    if not abertos:
        print("[+] Nenhuma posição órfã — reconciliação limpa.")
        # Limpa alerta de um dia anterior que tenha sido resolvido na mão.
        if os.path.exists(RECONCILE_ALERT):
            alerta("✅ <b>MFC 08:10</b> — reconciliação limpa; alerta anterior resolvido.",
                   resolvido=True)
        return

    total = sum(len(v) for v in abertos.values())
    alerta(f"⚠️ <b>MFC 08:10</b> — {total} posição(ões) ainda ABERTA(S) após o "
           f"encerramento das 08:00: { {m: sorted(v) for m, v in abertos.items()} }. "
           f"Tentando fechar de novo...")

    try:
        res = close_all_portfolios()
    except Exception as e:
        alerta(f"🚨 <b>MFC 08:10</b> — exceção ao tentar fechar: {e}. INTERVENÇÃO MANUAL NECESSÁRIA.")
        return

    if res.get("success"):
        alerta(f"✅ <b>MFC 08:10</b> — reconciliação fechou {res.get('total_closed', 0)} posição(ões). "
               f"Nada mais aberto.", resolvido=True)
        return

    try:
        restantes = get_open_magics_and_symbols()
    except MT5QueryError:
        restantes = {"?": ["consulta falhou"]}
    alerta(f"🚨 <b>MFC 08:10</b> — reconciliação NÃO conseguiu fechar tudo. "
           f"Restam: { {m: sorted(v) for m, v in restantes.items()} }. "
           f"Erro: {res.get('error')} — {res.get('message')}. "
           f"INTERVENÇÃO MANUAL NECESSÁRIA (posição sem fechador, só com stop catastrófico).")


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
    print("[*] Monitorando horários: 21:00:00 | 21:02:00 | 21:05:00 | 08:00:00 | 08:05:00 | 08:10:00 BRT\n")

    if test_mode:
        print("[*] MODO DE TESTE: rotinas que NÃO enviam ordem, em sequência.")
        print("[*] A fase 21:05 (abertura) e a 08:00 (encerramento) ficam de fora —")
        print("[*] elas mandam ordem real a mercado, em qualquer hora do dia.")
        execute_phase_2102()
        execute_phase_0805()
        return

    last_trigger = {}

    # Sinaliza quando execute_phase_2102 (gravação do sinal oficial) de HOJE
    # terminou COM ÊXITO — o gatilho de 21:05 espera nisso em vez de confiar
    # numa janela de relógio fixa (achado em revisão: uma janela larga o
    # suficiente pra absorver qualquer atraso não existe, porque
    # execute_phase_2102 pode demorar de forma imprevisível sob contenção de
    # MT5 com o subprocesso da 21:00). Só seta em SUCESSO (achado em revisão,
    # rodada 6): setar incondicionalmente no finally faria 21:05 seguir em
    # frente mesmo que a gravação tivesse lançado exceção — a checagem de
    # frescor dentro de execute_phase_2105 cobre "sinal de outro dia", não
    # "gravação de hoje que falhou". Limpo a cada novo disparo de 21:02 E a
    # cada virada de dia (abaixo), pra nunca herdar estado de ontem se o
    # minuto exato de 21:02 for perdido por algum motivo.
    phase_2102_done = threading.Event()
    last_seen_date = None

    def _run_phase_2102():
        ok = False
        try:
            ok = execute_phase_2102()
        finally:
            if ok:
                phase_2102_done.set()
            else:
                print("[!] 21:02: gravação do sinal FALHOU — 21:05 não vai abrir cesta "
                      "com sinal não confirmado hoje (vai esperar até 21:59 e alertar).")

    while True:
        now = datetime.now()
        cur_date = now.strftime("%Y-%m-%d")
        cur_hour = now.hour
        cur_min = now.minute

        if cur_date != last_seen_date:
            phase_2102_done.clear()
            last_seen_date = cur_date

        # 1. Gatilho 21:00 — despachado numa thread separada (achado F3): a
        # rotina inteira (~140 séries do MT5, matplotlib, uploads no
        # Telegram) pode passar de alguns minutos, e rodar isso SÍNCRONO
        # aqui travava o relógio do loop inteiro — perdendo os gatilhos
        # exatos de 21:02 e 21:05 sem nenhum aviso, se a rotina ainda
        # estivesse rodando quando esses minutos chegassem. O sinal que
        # 21:02/21:05 realmente usam vem de generate_and_save_daily_signals()
        # rodando de novo, de forma independente, dentro de execute_phase_2102
        # — não depende desta thread ter terminado.
        if cur_hour == 21 and cur_min == 0:
            key = f"{cur_date}_2100"
            if key not in last_trigger:
                threading.Thread(target=execute_phase_2100, name="phase_2100",
                                  daemon=True).start()
                last_trigger[key] = True

        # 2. Gatilho 21:02 — despachado em thread própria (mesmo padrão do
        # achado F3 pra 21:00), com um Event de conclusão em vez de só uma
        # janela de relógio (achado em revisão, rodada 5): execute_phase_2102
        # recalcula 140 séries via MT5 e PODE demorar de forma imprevisível,
        # principalmente sob contenção com o subprocesso da 21:00 batendo no
        # MESMO terminal MT5 ao mesmo tempo — nenhuma janela fixa cobre isso
        # com garantia. O gatilho de 21:05 abaixo só abre quando
        # phase_2102_done confirma que o sinal de HOJE terminou de gravar.
        if cur_hour == 21 and cur_min == 2:
            key = f"{cur_date}_2102"
            if key not in last_trigger:
                # (Não precisa de phase_2102_done.clear() aqui: a virada de
                # dia acima já garante que começa limpo todo santo dia.)
                threading.Thread(target=_run_phase_2102, name="phase_2102",
                                  daemon=True).start()
                last_trigger[key] = True

        # 2b. Gatilho 21:05 — abertura das cestas pelo lado Python (ver
        # execute_phase_2105 e Seção 1 de PLANO_IMPLEMENTACAO_MFC.md). Não é
        # minuto exato nem janela fixa: fica reavaliando a cada 25s, do
        # minuto 5 até o 59 (mesma janela tolerante que o upstream/Miquéias
        # já usa), até phase_2102_done confirmar que o sinal de hoje está
        # pronto. Se chegar em 21:59 sem confirmação, desiste e ALERTA — sinal
        # nunca confirmado é mais seguro que abrir com sinal desconhecido, e
        # a checagem de frescor dentro de execute_phase_2105 (compara a data
        # do sinal com hoje) é a rede secundária pro caso de o sinal em disco
        # ser de ontem. Com o EA no modo padrão (InpEaOpensBasket=false),
        # este é o único caminho que efetivamente abre posição — se o EA
        # estiver com InpEaOpensBasket=true em algum gráfico, os dois
        # tentariam abrir; a idempotência dentro de open_portfolio_basket() e
        # o CountOpenPositions()==0 do lado EA evitam duplicar, mas os dois
        # caminhos não devem ficar ativos ao mesmo tempo por design.
        if cur_hour == 21 and cur_min >= 5:
            key = f"{cur_date}_2105"
            if key not in last_trigger:
                if phase_2102_done.is_set():
                    execute_phase_2105()
                    last_trigger[key] = True
                elif cur_min >= 59:
                    print(f"[!] 21:59 BRT — sinal das 21:02 nunca confirmou conclusão "
                          f"({cur_date}). NENHUMA cesta será aberta hoje. Revisar manualmente.")
                    last_trigger[key] = True

        # 3. Gatilho 08:00 — encerramento compulsório pelo lado Python.
        # Segunda rede ao EA guardião (ver execute_phase_0800).
        # Só marca como concluído se REALMENTE fechou: o EA fecha de forma
        # monotônica (retenta a cada 3s pra sempre), e uma "segunda rede" que
        # desiste na primeira falha não é rede nenhuma. Retenta na janela
        # 08:00-08:04 até confirmar.
        if cur_hour == 8 and cur_min < 5:
            key = f"{cur_date}_0800"
            if key not in last_trigger:
                if execute_phase_0800():
                    last_trigger[key] = True

        # 4. Gatilho 08:10 — reconciliação (ver execute_phase_0810). Janela
        # ABERTA (10 até o fim da hora), não mais fechada em 5 min (achado
        # em revisão, rodada 6): esta é a REDE DE SEGURANÇA contra posição
        # órfã, e o fechamento das 08:00 pode retornar depois de 08:04 (seu
        # próprio retry só tenta até cur_min<5) — uma janela estreita aqui
        # podia ser inteiramente engolida por um fechamento lento, deixando
        # a rede de segurança sem disparar E sem alertar. Este gatilho não
        # depende de 08:00 nem de 08:05 terem terminado (nenhum dos dois
        # produz algo que a reconciliação precise ler) — só precisa que o
        # loop chegue a checar o relógio, o que ele faz a cada 25s desde que
        # nada mais o bloqueie synchronamente. Só marca como feito se rodou;
        # assim uma exceção não o cancela.
        if cur_hour == 8 and cur_min >= 10:
            key = f"{cur_date}_0810"
            if key not in last_trigger:
                execute_phase_0810()
                last_trigger[key] = True

        # 5. Gatilho 08:05 — despachado numa thread separada (mesmo padrão
        # do achado F3 pra 21:00, achado em revisão): sync_mt5_deals(60 dias)
        # + build do bundle Firebase + deploy (dois run_command com até 120s
        # cada, default) podiam levar minutos rodando SÍNCRONO aqui,
        # empurrando o relógio do loop pra além do minuto exato de 08:10 e
        # fazendo o reconciliador (a rede de segurança de verdade) nunca
        # disparar. 08:05 não produz nada que 08:10 precise ler — auditoria/
        # deploy e reconciliação de posição são independentes.
        if cur_hour == 8 and cur_min == 5:
            key = f"{cur_date}_0805"
            if key not in last_trigger:
                threading.Thread(target=execute_phase_0805, name="phase_0805",
                                  daemon=True).start()
                last_trigger[key] = True

        # Dormir 25 segundos
        time.sleep(25)


if __name__ == "__main__":
    is_test = "--test" in sys.argv
    run_daemon_loop(test_mode=is_test)
