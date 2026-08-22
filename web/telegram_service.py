"""
Módulo de Integração com o Telegram para a Plataforma CSS Institutional.
Envia relatórios, alertas e fotos em alta resolução do Raio-X Institucional.
"""
import os
import json
import logging
import urllib.request
import urllib.parse
import uuid

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
os.makedirs(DATA_DIR, exist_ok=True)
TELEGRAM_CONFIG_FILE = os.path.join(DATA_DIR, "telegram_config.json")

# Configurações Padrão
DEFAULT_BOT_TOKEN = "8661694016:AAHJ5RV7kJOnxXvYhcgllx-kYJSdHfbrBH8"
DEFAULT_CHAT_ID = "665651806"


def get_telegram_config():
    if os.path.exists(TELEGRAM_CONFIG_FILE):
        try:
            with open(TELEGRAM_CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Erro ao ler {TELEGRAM_CONFIG_FILE}: {e}")
    
    # Criar padrão se não existir
    cfg = {
        "bot_token": DEFAULT_BOT_TOKEN,
        "chat_id": DEFAULT_CHAT_ID,
        "enabled": True
    }
    try:
        with open(TELEGRAM_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass
    return cfg


def _build_photo_multipart(chat_id: str, image_bytes: bytes, filename: str, caption: str, parse_mode: str = "HTML") -> tuple[bytes, str]:
    boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"
    body = bytearray()
    
    # Campo chat_id
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(f'Content-Disposition: form-data; name="chat_id"\r\n\r\n'.encode("utf-8"))
    body.extend(f"{chat_id}\r\n".encode("utf-8"))

    # Campo caption
    if caption:
        # Truncar se exceder 1024 caracteres
        clean_caption = caption[:1020]
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="caption"\r\n\r\n'.encode("utf-8"))
        body.extend(clean_caption.encode("utf-8"))
        body.extend(b"\r\n")
        
        # Campo parse_mode se fornecido
        if parse_mode:
            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            body.extend(f'Content-Disposition: form-data; name="parse_mode"\r\n\r\n'.encode("utf-8"))
            body.extend(f"{parse_mode}\r\n".encode("utf-8"))

    # Campo photo (arquivo binário)
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(f'Content-Disposition: form-data; name="photo"; filename="{filename}"\r\n'.encode("utf-8"))
    body.extend(b"Content-Type: image/png\r\n\r\n")
    body.extend(image_bytes)
    body.extend(b"\r\n")

    # Fechamento do multipart
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    return bytes(body), boundary


def send_telegram_photo(image_bytes: bytes, filename: str = "raio_x.png", caption: str = "", parse_mode: str = "HTML") -> dict:
    """Envia uma foto (bytes PNG) para o canal/chat configurado no Telegram com fallback automático."""
    cfg = get_telegram_config()
    token = cfg.get("bot_token", DEFAULT_BOT_TOKEN)
    chat_id = cfg.get("chat_id", DEFAULT_CHAT_ID)

    if not token or not chat_id:
        return {"success": False, "error": "Bot Token ou Chat ID não configurados."}

    url = f"https://api.telegram.org/bot{token}/sendPhoto"

    # Tentativa 1: Com parse_mode solicitado (HTML)
    body, boundary = _build_photo_multipart(chat_id, image_bytes, filename, caption, parse_mode=parse_mode)
    req = urllib.request.Request(url, data=body)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("User-Agent", "CSS-PRO-Institutional-Bot/1.0")

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            if res_data.get("ok"):
                return {"success": True, "result": res_data.get("result")}
            return {"success": False, "error": res_data.get("description", "Erro desconhecido")}
    except Exception as e:
        logger.warning(f"Tentativa com parse_mode={parse_mode} falhou: {e}. Tentando fallback sem formatação...")

    # Tentativa 2: Fallback sem formatação HTML (texto puro)
    import re
    plain_caption = re.sub(r'<[^>]+>', '', caption) if caption else ""
    body, boundary = _build_photo_multipart(chat_id, image_bytes, filename, plain_caption, parse_mode=None)
    req = urllib.request.Request(url, data=body)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("User-Agent", "CSS-PRO-Institutional-Bot/1.0")

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            if res_data.get("ok"):
                return {"success": True, "result": res_data.get("result")}
            return {"success": False, "error": res_data.get("description", "Erro desconhecido")}
    except Exception as e:
        logger.error(f"Erro fatal ao enviar foto para o Telegram: {e}")
        return {"success": False, "error": str(e)}


def send_telegram_message(text: str, parse_mode: str = "HTML") -> dict:
    """Envia uma mensagem de texto formatada para o canal/chat configurado no Telegram."""
    cfg = get_telegram_config()
    token = cfg.get("bot_token", DEFAULT_BOT_TOKEN)
    chat_id = cfg.get("chat_id", DEFAULT_CHAT_ID)

    if not token or not chat_id:
        return {"success": False, "error": "Bot Token ou Chat ID não configurados."}

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text[:4090],
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    }).encode("utf-8")

    req = urllib.request.Request(url, data=payload, headers={
        "Content-Type": "application/json",
        "User-Agent": "CSS-PRO-Institutional-Bot/1.0"
    })

    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            if res_data.get("ok"):
                return {"success": True, "result": res_data.get("result")}
            return {"success": False, "error": res_data.get("description", "Erro desconhecido")}
    except Exception as e:
        logger.warning(f"Erro no envio de mensagem com {parse_mode}: {e}. Tentando fallback sem HTML...")

    # Fallback texto puro
    import re
    plain_text = re.sub(r'<[^>]+>', '', text)
    payload = json.dumps({
        "chat_id": chat_id,
        "text": plain_text[:4090],
        "disable_web_page_preview": True
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={
        "Content-Type": "application/json",
        "User-Agent": "CSS-PRO-Institutional-Bot/1.0"
    })
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            if res_data.get("ok"):
                return {"success": True, "result": res_data.get("result")}
            return {"success": False, "error": res_data.get("description", "Erro desconhecido")}
    except Exception as e2:
        logger.error(f"Erro fatal ao enviar mensagem para o Telegram: {e2}")
        return {"success": False, "error": str(e2)}


