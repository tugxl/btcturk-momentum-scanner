"""V3 public-market shadow dataset collector and offline model utilities."""
import argparse
import json
import logging
import os
from pathlib import Path
import signal
import sqlite3
import sys
import threading
import time

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
from shadow_client import ShadowBtcTurkClient
from shadow_health import ShadowReporter,ShadowSettings,resource_snapshot
from shadow_reporting import live_ranking_text
from state_manager import StateManager


def predictive_scan_once(market,score_state,store,cfg,production_model):
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


def _counter_delta(after,before,key):
    return max(0,int(after.get(key,0)-before.get(key,0)))


def collect_shadow_once(market,score_state,store,cfg,reporter):
    """Collect one synchronous dataset snapshot. No model is loaded or evaluated."""
    started=time.monotonic()
    timestamp=time.time()
    resources=resource_snapshot()
    counters=market.client.counter_snapshot()
    scan_stats=dict(discovered_pairs=0,successful_pairs=0,failed_pairs=0,stale_pairs=0,stale_order_books=0)
    snapshots=labels=db_errors=0
    status,error='ok',None
    try:
        raw=market.scan()
        successful=sum(not errors for *_prefix,errors in raw)
        stale=sum(any(('stale' in item or 'aged' in item) for item in errors)
                  for *_prefix,errors in raw)
        stale_books=sum(book is None or any('book aged' in item for item in errors)
                        for _symbol,_price,_bars,_minutes,_btc,_btc_minutes,book,errors in raw)
        scan_stats=dict(discovered_pairs=len(market.universe),successful_pairs=successful,
                        failed_pairs=len(market.universe)-successful,stale_pairs=stale,
                        stale_order_books=stale_books)
        rows=[evaluate(s,p,c,b,k,cfg,e,minute_candles=m,btc_minutes=bm) for s,p,c,m,b,bm,k,e in raw]
        observed_at=time.time()
        update_history(score_state,rows,observed_at,cfg,market.universe)
        feature_rows=build_feature_rows(rows)
        store.save_bars(raw)
        snapshots=store.save_features(observed_at,feature_rows)
        labels=label_pending(store,observed_at)
        store.prune_bars(observed_at-cfg.raw_bar_retention_days*86400)
    except sqlite3.Error as exc:
        db_errors=1
        status,error='failed',f'{type(exc).__name__}: {exc}'
        logging.getLogger('shadow-scanner').exception('shadow_db_write_failure')
        reporter.alert('db','DB WRITE FAILURE\n'+error)
    except Exception as exc:
        status,error='failed',f'{type(exc).__name__}: {exc}'
        logging.getLogger('shadow-scanner').exception('shadow_data_collection_failure')
    finished=resource_snapshot()
    after=market.client.counter_snapshot()
    duration=time.monotonic()-started
    metrics=dict(timestamp=time.time(),duration=duration,**scan_stats,snapshots_written=snapshots,
                 api_requests=_counter_delta(after,counters,'requests'),
                 api_errors=_counter_delta(after,counters,'errors'),
                 api_retries=_counter_delta(after,counters,'retries'),
                 response_bytes=_counter_delta(after,counters,'response_bytes'),db_errors=db_errors,
                 label_updates=labels,db_size_bytes=store.db_size_bytes(),
                 cpu_seconds=max(0,finished['cpu']-resources['cpu']),
                 cpu_percent=max(0,finished['cpu']-resources['cpu'])/duration*100 if duration else 0,
                 memory_mb=finished['memory_mb'],status=status,error=error)
    try:
        store.save_health(metrics)
        store.set_metadata('last_heartbeat',metrics['timestamp'])
        stats,quality=reporter.after_scan(metrics)
        logging.getLogger('shadow-scanner').info(
            'shadow_scan_complete status=%s duration=%.2f discovered=%d successful=%d failed=%d stale=%d snapshots=%d labels=%d api_requests=%d api_errors=%d db_bytes=%d quality=%s ready=%s',
            status,duration,metrics['discovered_pairs'],metrics['successful_pairs'],metrics['failed_pairs'],
            metrics['stale_pairs'],snapshots,labels,metrics['api_requests'],metrics['api_errors'],
            metrics['db_size_bytes'],quality['status'],stats['ready'])
    except sqlite3.Error as exc:
        logging.getLogger('shadow-scanner').exception('shadow_health_write_failure')
        reporter.alert('db','DB WRITE FAILURE\n'+f'{type(exc).__name__}: {exc}')
    return metrics


def main():
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--live-ranking',action='store_true')
    mode.add_argument('--shadow-watch',action='store_true')
    mode.add_argument('--shadow-once',action='store_true',help='One data-only collection cycle')
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
    # Railway/persistent volumes may be mounted at an empty path. Ensure the
    # configured data directory exists before SQLite/StateManager open files.
    data_dir.mkdir(parents=True, exist_ok=True)
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
        market=MarketData(ShadowBtcTurkClient(cfg),cfg)
        if args.live_ranking:
            predictive_scan_once(market,score_state,store,cfg,production_path)
            return 0
        reporter=ShadowReporter(store,ShadowSettings.from_config(cfg))
        previous_clean=bool(store.metadata('clean_shutdown',True))
        now=time.time()
        store.set_metadata('worker_started_at',now)
        store.set_metadata('clean_shutdown',False)
        if not previous_clean:
            reporter.alert('worker_down','SHADOW WORKER DOWN\nWorker restarted after an unclean shutdown',now)
        if args.shadow_once:
            metrics=collect_shadow_once(market,score_state,store,cfg,reporter)
            store.set_metadata('clean_shutdown',True)
            return int(metrics['status']!='ok')
        stop=threading.Event()
        for sig in (signal.SIGINT,signal.SIGTERM):
            signal.signal(sig,lambda *_:stop.set())
        while not stop.is_set():
            started=time.monotonic()
            try:
                collect_shadow_once(market,score_state,store,cfg,reporter)
            except Exception:
                logging.getLogger('shadow-scanner').exception('shadow_scan_failed')
            stop.wait(max(1,cfg.scan_interval-(time.monotonic()-started)))
        store.set_metadata('clean_shutdown',True)
        return 0
    except (ValueError,OSError,TypeError) as exc:
        print(f'MODEL NOT READY: {exc}')
        return 2
    finally:
        score_state.close()
        store.close()


if __name__=='__main__':
    raise SystemExit(main())
