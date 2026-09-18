import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock,patch

from config import Config
from opportunity_features import BASE_FEATURES
from opportunity_store import OpportunityStore
from scanner import collect_shadow_once
from shadow_health import ShadowReporter,ShadowSettings,quality_report,readiness


def feature(symbol='METTRY'):
    return dict(symbol=symbol,price=100.,features={name:0. for name in BASE_FEATURES},
                regime='BTC_FLAT / ALT_MIXED',safety_pass=True,result={})


class FakeClient:
    def __init__(self): self.values=dict(requests=0,retries=0,errors=0,rate_limits=0,response_bytes=0)
    def counter_snapshot(self): return dict(self.values)


class ShadowCollectionTests(unittest.TestCase):
    @staticmethod
    def metrics(timestamp=100,api_errors=0,status='ok'):
        return dict(timestamp=timestamp,duration=1,discovered_pairs=1,successful_pairs=1,
                    failed_pairs=0,stale_pairs=0,stale_order_books=0,snapshots_written=1,
                    api_requests=2,api_errors=api_errors,api_retries=0,response_bytes=100,
                    db_errors=0,label_updates=0,db_size_bytes=4096,cpu_seconds=.1,
                    cpu_percent=10,memory_mb=20,status=status,error=None)

    def test_collection_is_data_only_and_records_health(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=OpportunityStore(Path(tmp)/'shadow.sqlite3')
            market=Mock()
            market.client=FakeClient()
            market.universe=['METTRY']
            market.last_scan_stats=dict(discovered_pairs=1,successful_pairs=1,failed_pairs=0,stale_pairs=0)
            market.scan.return_value=[('METTRY',100.,[],[],[],[],None,[])]
            reporter=ShadowReporter(store,ShadowSettings.from_config(Config()))
            with patch('scanner.evaluate',return_value={}),patch('scanner.update_history'),\
                 patch('scanner.build_feature_rows',return_value=[feature()]),\
                 patch('scanner.label_pending',return_value=0),\
                 patch('scanner.load_bundle') as load_model,patch('scanner.rank_opportunities') as rank:
                metrics=collect_shadow_once(market,Mock(),store,Config(),reporter)
            self.assertEqual(metrics['status'],'ok')
            self.assertEqual(metrics['snapshots_written'],1)
            self.assertEqual(store.health_rows()[0]['successful_pairs'],1)
            load_model.assert_not_called()
            rank.assert_not_called()
            store.close()

    def test_readiness_requires_all_three_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=OpportunityStore(Path(tmp)/'shadow.sqlite3')
            for timestamp,symbol in ((1,'ATRY'),(86401,'BTRY')):
                store.save_features(timestamp,[feature(symbol)])
            for row in store.pending_labels(999999):
                store.save_label(row['id'],999999,{'return_120m':1})
            cfg=Config(model_min_observations=2,model_min_unique_timestamps=2,model_min_history_days=2)
            self.assertTrue(readiness(store,cfg)['ready'])
            cfg=Config(model_min_observations=3,model_min_unique_timestamps=2,model_min_history_days=2)
            self.assertFalse(readiness(store,cfg)['ready'])
            store.close()

    def test_raw_bar_retention_does_not_delete_features_or_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=OpportunityStore(Path(tmp)/'shadow.sqlite3')
            store.save_features(1,[feature()])
            row=store.pending_labels(10)[0]
            store.save_label(row['id'],10,{'return_120m':1})
            with store.db:
                store.db.execute('INSERT INTO market_bars VALUES (?,?,?,?,?,?,?)',('METTRY',1,1,1,1,1,1))
            store.prune_bars(2)
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM market_bars').fetchone()[0],0)
            self.assertEqual(store.dataset_stats()['feature_rows'],1)
            self.assertEqual(store.dataset_stats()['completed_labels'],1)
            store.close()

    def test_quality_detects_invalid_price_and_large_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=OpportunityStore(Path(tmp)/'shadow.sqlite3')
            store.save_features(1,[feature()])
            bad=feature('BADTRY'); bad['price']=0
            store.save_features(4001,[bad])
            report=quality_report(store,ShadowSettings.from_config(Config()),now=4001)
            self.assertEqual(report['status'],'FAIL')
            self.assertEqual(report['invalid_prices'],1)
            store.close()

    def test_v3_notifier_never_uses_v1_credentials(self):
        with patch.dict(os.environ,{'TELEGRAM_BOT_TOKEN':'v1','TELEGRAM_CHAT_ID':'v1'},clear=True):
            self.assertFalse(ShadowReporter.configured())

    def test_api_failure_alert_is_consecutive_threshold_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=OpportunityStore(Path(tmp)/'shadow.sqlite3')
            settings=replace(ShadowSettings.from_config(Config()),shadow_api_failure_alert_threshold=3)
            reporter=ShadowReporter(store,settings)
            reporter.send=Mock(return_value=True)
            for timestamp in (100,101): reporter.after_scan(self.metrics(timestamp,api_errors=1),timestamp)
            reporter.send.assert_not_called()
            reporter.after_scan(self.metrics(102,api_errors=1),102)
            self.assertIn('BTC API FAILURE',reporter.send.call_args.args[0])
            reporter.after_scan(self.metrics(103,api_errors=1),103)
            self.assertEqual(reporter.send.call_count,1)
            store.close()

    def test_dataset_ready_alerts_once_without_training(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=OpportunityStore(Path(tmp)/'shadow.sqlite3')
            for timestamp,symbol in ((1,'ATRY'),(86401,'BTRY')):
                store.save_features(timestamp,[feature(symbol)])
            for row in store.pending_labels(999999): store.save_label(row['id'],999999,{'return_120m':1})
            settings=replace(ShadowSettings.from_config(Config()),model_min_observations=2,
                             model_min_unique_timestamps=2,model_min_history_days=2)
            reporter=ShadowReporter(store,settings)
            reporter.send=Mock(return_value=True)
            reporter.after_scan(self.metrics(86402),86402)
            reporter.after_scan(self.metrics(86403),86403)
            messages=[call.args[0] for call in reporter.send.call_args_list]
            self.assertEqual(messages.count('🧪 V3 DATASET READY FOR TRAINING'),1)
            store.close()


if __name__=='__main__':
    unittest.main()
