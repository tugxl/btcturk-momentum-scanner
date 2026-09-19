import hashlib
import os
import sqlite3
import subprocess
import sys
import time

from telegram_notifier import send_telegram


DATA_DIR = os.getenv("SCANNER_DATA_DIR", "data")
ALERT_DB = os.path.join(DATA_DIR, "cloud_alerts.sqlite3")

COOLDOWN_SECONDS = 60 * 60
HEARTBEAT_SECONDS = 60 * 60


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

    conn.execute("""
        CREATE TABLE IF NOT EXISTS system_state (
            key TEXT PRIMARY KEY,
            value REAL
        )
    """)

    conn.commit()
    return conn


def candidate_key(candidate):
    symbol = "UNKNOWN"

    for line in candidate.splitlines():
        clean = line.strip()

        if clean.endswith("/TRY"):
            symbol = clean
            break

    key = hashlib.sha256(symbol.encode()).hexdigest()
    return key, symbol


def should_send_candidate(conn, candidate):
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


def heartbeat_due(conn):
    now = time.time()

    row = conn.execute(
        "SELECT value FROM system_state WHERE key = 'heartbeat'"
    ).fetchone()

    if row is not None:
        if now - row[0] < HEARTBEAT_SECONDS:
            return False

    conn.execute(
        """
        INSERT INTO system_state(key, value)
        VALUES ('heartbeat', ?)
        ON CONFLICT(key)
        DO UPDATE SET value = excluded.value
        """,
        (now,)
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
            "⚠️ BtcTurk Momentum Scanner ERROR\n\n"
            f"Scanner cloud taraması başarısız.\n"
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
        # One Telegram digest per workflow run. The workflow itself runs hourly,
        # so users receive at most one ranked message per hour instead of one
        # notification per candidate.
        selected = candidates[:10]
        sent_count = 0
        if selected:
            body = ["🔥 BtcTurk Hacim & Sıkışma Kırılımı — SAATLİK TOP 10", ""]
            for index, candidate in enumerate(selected, 1):
                body.append(f"{index}. {candidate}")
                body.append("")
            body.append("⚠️ Scanner sıralamasıdır; işlem öncesi güncel fiyat ve risk kontrolü gerekir.")
            if send_telegram("\n".join(body)):
                sent_count = len(selected)

        if not candidates and heartbeat_due(conn):
            send_telegram(
                "💓 BtcTurk Momentum Scanner ACTIVE\n\n"
                "Cloud taraması başarılı.\n"
                "ACTION CANDIDATE bulunamadı.\n"
                "Scanner çalışmaya devam ediyor."
            )

        print(
            f"Candidates found: {len(candidates)} | "
            f"Telegram alerts sent: {sent_count}"
        )

    finally:
        conn.close()


if __name__ == "__main__":
    main()
