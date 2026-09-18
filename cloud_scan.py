import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time

from telegram_notifier import send_telegram


DATA_DIR = os.getenv("SCANNER_DATA_DIR", "data")
ALERT_DB = os.path.join(DATA_DIR, "cloud_alerts.sqlite3")

# Aynı candidate için tekrar bildirim süresi
COOLDOWN_SECONDS = 60 * 60  # 1 saat


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)

    conn = sqlite3.connect(ALERT_DB)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            alert_key TEXT PRIMARY KEY,
            symbol TEXT,
            sent_at REAL
        )
    """)

    conn.commit()
    return conn


def candidate_key(candidate):
    # Coin sembolünü candidate metninden bul.
    symbol = "UNKNOWN"

    for line in candidate.splitlines():
        clean = line.strip()

        if clean.endswith("/TRY"):
            symbol = clean
            break

    # Aynı coin için tek cooldown anahtarı.
    return hashlib.sha256(symbol.encode()).hexdigest(), symbol


def should_send(conn, candidate):
    key, symbol = candidate_key(candidate)
    now = time.time()

    row = conn.execute(
        "SELECT sent_at FROM alerts WHERE alert_key = ?",
        (key,)
    ).fetchone()

    if row is not None:
        elapsed = now - row[0]

        if elapsed < COOLDOWN_SECONDS:
            print(
                f"Telegram suppressed: {symbol} "
                f"cooldown active ({elapsed:.0f}s)"
            )
            return False

    conn.execute(
        """
        INSERT INTO alerts(alert_key, symbol, sent_at)
        VALUES (?, ?, ?)
        ON CONFLICT(alert_key)
        DO UPDATE SET sent_at = excluded.sent_at
        """,
        (key, symbol, now)
    )

    conn.commit()
    return True


def main():
    result = subprocess.run(
        [sys.executable, "-u", "main.py", "--once"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    output = (result.stdout or "") + (result.stderr or "")
    print(output)

    if result.returncode != 0:
        send_telegram(
            "⚠️ BtcTurk Momentum Scanner hata verdi.\n"
            f"Exit code: {result.returncode}"
        )
        raise SystemExit(result.returncode)

    lines = output.splitlines()

    candidates = []
    capturing = False
    current = []

    for line in lines:
        clean = line.strip()

        if clean == "ACTION CANDIDATE":
            if current:
                candidates.append("\n".join(current))

            current = [clean]
            capturing = True
            continue

        if capturing:
            if clean.startswith("{") and '"logger":' in clean:
                if current:
                    candidates.append("\n".join(current))

                current = []
                capturing = False

            elif clean:
                current.append(clean)

            else:
                if current:
                    candidates.append("\n".join(current))

                current = []
                capturing = False

    if current:
        candidates.append("\n".join(current))

    conn = init_db()

    try:
        for candidate in candidates:
            if should_send(conn, candidate):
                send_telegram(
                    "🚨 BtcTurk MOMENTUM ALERT\n\n" + candidate
                )

    finally:
        conn.close()


if __name__ == "__main__":
    main()
