import subprocess
import sys

from telegram_notifier import send_telegram


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

    for candidate in candidates:
        send_telegram(
            "🚨 BtcTurk MOMENTUM ALERT\n\n" + candidate
        )


if __name__ == "__main__":
    main()
