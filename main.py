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
from notifier import JsonFormatter, alert_text, ranked_table
from telegram_notifier import send_telegram
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
                rows = [evaluate(s,p,c,b,k,cfg,e) for s,p,c,b,k,e in market.scan()]
                observed_at=time.time()
                update_history(state,rows,observed_at,cfg,market.universe)
                held = {p['symbol'] for p in positions}
                rows.sort(key=lambda r:(r['symbol'] in held, r['eligible'], r['early_watch'], r['rapidly_forming'], not bool(r['fomo']),r['score']), reverse=True)
                statuses = [position_status(p,next((r for r in rows if r['symbol']==p['symbol']),None)) for p in positions]
                if args.json:
                    print(json.dumps(dict(timestamp=observed_at,positions=statuses, results=rows), allow_nan=False), flush=True)
                else:
                    for status in statuses:
                        print(status)
                    ranked_table(rows,cfg.top)
                if args.watch:
                    for r in rows:
                        state.record(r,cfg.reset_threshold)
                        state.record_watch(r,cfg,observed_at)
                    for alert_id, r in state.pending():
                        print(json.dumps({'alert_id':alert_id, 'alert':r}) if args.json else '\n'+alert_text(r),flush=True)
                        state.delivered(alert_id)
                elif not args.json:
                    for r in rows:
                        if r['eligible'] or r['early_watch']:
                            print('\nDRY RUN PREVIEW\n'+alert_text(r))
            except Exception:
                log.exception('scan_failed')
                if args.once:
                    return 1
            if args.once:
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
