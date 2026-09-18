"""BtcTurk Early Momentum Scanner: public market data only."""
import argparse
import json
import logging
import os
from pathlib import Path
import signal
import threading
import time
import sys
from btcturk_client import BtcTurkClient
from config import load_config
from market_data import MarketData
from momentum_engine import evaluate
from notifier import JsonFormatter, alert_text, ranked_table, diagnostic_report, scan_message
from telegram_notifier import send_telegram, telegram_configured
from positions import load_positions, position_status
from state_manager import StateManager
from score_history import update_history


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--once', action='store_true', help='Public-data dry run; saves score history but does not change alert suppression')
    mode.add_argument('--watch', action='store_true')
    mode.add_argument('--diagnostic', action='store_true', help='Score every TRY pair and explain lost points and ACTION blockers')
    parser.add_argument('--config', default='config.yaml')
    parser.add_argument('--positions', default='positions.json')
    parser.add_argument('--json', action='store_true', help='Output complete metrics and score explanations as JSON')
    args = parser.parse_args()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    log = logging.getLogger('scanner')
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    state = None
    try:
        cfg = load_config(args.config)
        market = MarketData(BtcTurkClient(cfg), cfg)
        state = StateManager(Path(os.environ.get('SCANNER_DATA_DIR','data'))/'signals.sqlite3')
        while not stop.is_set():
            started = time.monotonic()
            try:
                positions = load_positions(args.positions)
                rows = [evaluate(s,p,c,b,k,cfg,e,minute_candles=m,btc_minutes=bm)
                        for s,p,c,m,b,bm,k,e in market.scan()]
                observed_at=time.time()
                update_history(state,rows,observed_at,cfg,market.universe)
                held = {p['symbol'] for p in positions}
                rows.sort(key=lambda r:(r['symbol'] in held, r['stage']=='ACTION', r['stage']=='WATCH', r['rapidly_forming'],r['score']), reverse=True)
                statuses = [position_status(p,next((r for r in rows if r['symbol']==p['symbol']),None)) for p in positions]
                if args.json:
                    print(json.dumps(dict(timestamp=observed_at,positions=statuses, results=rows), allow_nan=False), flush=True)
                elif args.diagnostic:
                    diagnostic_report(rows)
                else:
                    for status in statuses:
                        print(status)
                    ranked_table(rows,cfg.top)
                if args.watch:
                    notification_mode=state.notification_events(rows,cfg,observed_at)
                    if notification_mode:
                        state.queue_message(scan_message(rows,notification_mode,5),observed_at)
                    for alert_id, r in state.pending():
                        message=r['text'] if r.get('kind')=='message' else alert_text(r)
                        print(json.dumps({'alert_id':alert_id, 'message':message},ensure_ascii=False) if args.json else '\n'+message,flush=True)
                        if not telegram_configured() or send_telegram(message):
                            state.delivered(alert_id)
                elif not args.json:
                    for r in rows:
                        if r['eligible'] or r['early_watch']:
                            print('\nDRY RUN PREVIEW\n'+alert_text(r))
            except Exception:
                log.exception('scan_failed')
                if args.once or args.diagnostic:
                    return 1
            if args.once or args.diagnostic:
                return 0
            elapsed = time.monotonic()-started
            if elapsed > cfg.scan_interval:
                log.warning('scan_exceeded_interval elapsed_seconds=%.1f',elapsed)
            stop.wait(max(1,cfg.scan_interval-elapsed))
    except (ValueError, OSError, TypeError) as exc:
        log.error('startup_failed error=%s',exc)
        return 2
    finally:
        if state:
            state.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
