import os
import urllib.parse
import urllib.request

from dotenv import load_dotenv

load_dotenv()


def telegram_configured():
    return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))


def send_telegram(message):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        return False

    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message,
    }).encode()

    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=data,
    )

    try:
        urllib.request.urlopen(request, timeout=10).read()
        return True
    except Exception as exc:
        print(f"Telegram notification failed: {exc}")
        return False
