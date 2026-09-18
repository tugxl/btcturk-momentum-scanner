"""V3 shadow opportunity scanner. Public BtcTurk data only; never sends orders."""
import argparse
import json
import logging
import os
from pathlib import Path
import signal
import sys
import threading
import time

from btcturk_client import BtcTurkClient
from config import load_config
from forward_labels import label_pending
from market_data import MarketData
from momentum_engine import evaluate
from notifier import JsonFormatter
from opportunity_engine import rank_opportunities
from opportunity_features import build_feature_rows, market_context
from opportunity_store import OpportunityStore
from performance import performance_report
from predictive_model import approve_candidate, load_bundle, train_candidate
from score_history import update_history
from shadow_reporting import live_ranking_text, shadow_opportunity_text
from state_manager import StateManager
from telegram_notifier import send_telegram, telegram_configured


def scan_once(market,score_state,store,cfg,production_model,notify=False):
    raw=market.scan()
    rows=[evaluate(s,p,c,b,k,cfg,e,minute_candles=m,btc_minutes=bm) for s,p,c,m,b,bm,k,e in raw]
    observed_at=time.time()
    update_history(score_state,rows,observed_at,cfg,market.universe)
    feature_rows=build_feature_rows(rows)
    store.save_bars(raw)
    store.save_features(observed_at,feature_rows)
    label_pending(store,observed_at)
    store.prune_bars(observed_at-cfg.raw_bar_retention_days*86400)
    _breadth,_btc,regime=market_context(rows)
    bundle=load_bundle(production_model,require_approved=True)
    if not bundle:
        print(live_ranking_text(regime,[],[],model_ready=False),flush=True)
        return
    trades,rejected=rank_opportunities(feature_rows,bundle,cfg,observed_at)
    store.save_predictions(observed_at,trades+rejected,bundle['version'])
    print(live_ranking_text(regime,trades,rejected,model_ready=True),flush=True)
    if notify:
        for item in trades:
            if store.should_notify(item,cfg,observed_at):
                message=shadow_opportunity_text(item)
                print('\n'+message,flush=True)
                if telegram_configured():
                    send_telegram(message)


def main():
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--live-ranking',action='store_true')
    mode.add_argument('--shadow-watch',action='store_true')
    mode.add_argument('--train-model',action='store_true')
    mode.add_argument('--approve-model',action='store_true',help='Explicit human-operated candidate promotion')
    mode.add_argument('--performance-report',action='store_true')
    parser.add_argument('--config',default='config.yaml')
    parser.add_argument('--data-dir',default=os.environ.get('SCANNER_V3_DATA_DIR','data/v3'))
    args=parser.parse_args()
    handler=logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO,handlers=[handler])
    cfg=load_config(args.config)
    data_dir=Path(args.data_dir)
    db_path=data_dir/'shadow.sqlite3'
    candidate_path=data_dir/'model_candidate.joblib'
    production_path=data_dir/'model_production.joblib'
    store=OpportunityStore(db_path)
    score_state=StateManager(db_path)
    try:
        if args.train_model:
            production=load_bundle(production_path,require_approved=True)
            bundle=train_candidate(store.labelled_rows(),cfg,candidate_path,production)
            print(json.dumps({k:v for k,v in bundle.items() if k not in ('targets','reference')},indent=2))
            print('CANDIDATE ONLY — human approval required; production model unchanged')
            return 0
        if args.approve_model:
            bundle=approve_candidate(candidate_path,production_path)
            print(f"Approved model {bundle['version']} for shadow inference")
            return 0
        if args.performance_report:
            print(json.dumps(performance_report(store),indent=2,allow_nan=False))
            return 0
        market=MarketData(BtcTurkClient(cfg),cfg)
        if args.live_ranking:
            scan_once(market,score_state,store,cfg,production_path,notify=False)
            return 0
        stop=threading.Event()
        for sig in (signal.SIGINT,signal.SIGTERM):
            signal.signal(sig,lambda *_:stop.set())
        while not stop.is_set():
            started=time.monotonic()
            try:
                scan_once(market,score_state,store,cfg,production_path,notify=True)
            except Exception:
                logging.getLogger('shadow-scanner').exception('shadow_scan_failed')
            stop.wait(max(1,cfg.scan_interval-(time.monotonic()-started)))
        return 0
    except (ValueError,OSError,TypeError) as exc:
        print(f'MODEL NOT READY: {exc}')
        return 2
    finally:
        score_state.close()
        store.close()


if __name__=='__main__':
    raise SystemExit(main())
