"""
Script de exportação e preparação de arquivos para Firebase Hosting
Copia os arquivos de frontend e gera snapshots das APIs em formato JSON.
"""

import os
import shutil
import json
import urllib.request

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "web", "static")
PUBLIC_DIR = os.path.join(BASE_DIR, "public")
API_DIR = os.path.join(PUBLIC_DIR, "api")

def build():
    print("[*] Preparando diretório 'public' para o Firebase...")
    if os.path.exists(PUBLIC_DIR):
        shutil.rmtree(PUBLIC_DIR)
    os.makedirs(PUBLIC_DIR, exist_ok=True)
    os.makedirs(os.path.join(API_DIR, "css"), exist_ok=True)
    os.makedirs(os.path.join(API_DIR, "track-record"), exist_ok=True)

    # 1. Copiar index.html, styles.css, app.js
    for f in ["index.html", "styles.css", "app.js"]:
        src = os.path.join(STATIC_DIR, f)
        dst = os.path.join(PUBLIC_DIR, f)
        if os.path.exists(src):
            shutil.copy2(src, dst)
            print(f"  [+] Copiado: {f}")

    # Ajustar referências de /static/ em index.html para caminhos raiz
    index_dst = os.path.join(PUBLIC_DIR, "index.html")
    with open(index_dst, "r", encoding="utf-8") as f:
        html = f.read()
    html = html.replace('href="/static/styles.css"', 'href="/styles.css"')
    html = html.replace('src="/static/app.js"', 'src="/app.js"')
    with open(index_dst, "w", encoding="utf-8") as f:
        f.write(html)
    print("  [+] Ajustadas referências estáticas em index.html")

    # 2. Gerar Snapshots de API a partir do servidor local ou geradores
    print("[*] Gerando snapshots de API...")
    
    # CSS All
    try:
        req = urllib.request.urlopen("http://127.0.0.1:8050/api/css/all", timeout=5)
        css_data = json.loads(req.read().decode("utf-8"))
        with open(os.path.join(API_DIR, "css", "all.json"), "w", encoding="utf-8") as f:
            json.dump(css_data, f, indent=2, ensure_ascii=False)
        print("  [+] Gerado: /api/css/all.json")
    except Exception as e:
        print(f"  [!] Fallback para CSS local: {e}")
        from web.css_service import get_all_css_data
        css_data = get_all_css_data()
        with open(os.path.join(API_DIR, "css", "all.json"), "w", encoding="utf-8") as f:
            json.dump(css_data, f, indent=2, ensure_ascii=False)

    # Track Record Summary
    try:
        req = urllib.request.urlopen("http://127.0.0.1:8050/api/track-record/summary?currency=ALL", timeout=5)
        tr_data = json.loads(req.read().decode("utf-8"))
        with open(os.path.join(API_DIR, "track-record", "summary.json"), "w", encoding="utf-8") as f:
            json.dump(tr_data, f, indent=2, ensure_ascii=False)
        print("  [+] Gerado: /api/track-record/summary.json")
    except Exception as e:
        print(f"  [!] Fallback para Track Record local: {e}")
        from web.history_tracker import history_engine
        tr_data = history_engine.get_filtered_data("ALL")
        with open(os.path.join(API_DIR, "track-record", "summary.json"), "w", encoding="utf-8") as f:
            json.dump(tr_data, f, indent=2, ensure_ascii=False)

    # Track Record Live
    try:
        req = urllib.request.urlopen("http://127.0.0.1:8050/api/track-record/live", timeout=5)
        live_data = json.loads(req.read().decode("utf-8"))
        with open(os.path.join(API_DIR, "track-record", "live.json"), "w", encoding="utf-8") as f:
            json.dump(live_data, f, indent=2, ensure_ascii=False)
        print("  [+] Gerado: /api/track-record/live.json")
    except Exception as e:
        print(f"  [!] Fallback para Live Session local: {e}")
        from web.history_tracker import history_engine
        live_data = {"session": history_engine.get_live_session()}
        with open(os.path.join(API_DIR, "track-record", "live.json"), "w", encoding="utf-8") as f:
            json.dump(live_data, f, indent=2, ensure_ascii=False)

    # Matrix Summary
    os.makedirs(os.path.join(API_DIR, "matrix"), exist_ok=True)
    try:
        req = urllib.request.urlopen("http://127.0.0.1:8050/api/matrix/summary", timeout=5)
        matrix_data = json.loads(req.read().decode("utf-8"))
        with open(os.path.join(API_DIR, "matrix", "summary.json"), "w", encoding="utf-8") as f:
            json.dump(matrix_data, f, indent=2, ensure_ascii=False)
        print("  [+] Gerado: /api/matrix/summary.json")
    except Exception as e:
        print(f"  [!] Fallback para Matrix local: {e}")

    print("\n[OK] Bundle do Firebase gerado com sucesso em 'public/'!")

if __name__ == "__main__":
    build()
