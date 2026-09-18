import subprocess
import sys

from telegram_notifier import send_telegram


def main():
    command = [sys.executable, "-u", "main.py", "--watch"]

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    capturing = False
    alert_lines = []

    try:
        for line in process.stdout:
            print(line, end="", flush=True)

            clean = line.rstrip()

            if clean == "ACTION CANDIDATE":
                if alert_lines:
                    send_telegram("\n".join(alert_lines))
                capturing = True
                alert_lines = [clean]
                continue

            if capturing:
                if clean.startswith("{") and '"logger":' in clean:
                    send_telegram("\n".join(alert_lines))
                    alert_lines = []
                    capturing = False
                elif clean:
                    alert_lines.append(clean)
                elif alert_lines:
                    send_telegram("\n".join(alert_lines))
                    alert_lines = []
                    capturing = False

    except KeyboardInterrupt:
        print("\nStopping scanner...")
    finally:
        if alert_lines:
            send_telegram("\n".join(alert_lines))

        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    main()