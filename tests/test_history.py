import copy
import io
import json
import math
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from config import Config
from notifier import ranked_table
from score_history import compare, apply_stages, update_history
from state_manager import StateManager


def observation(t, score, price=100, volume=1, rs=1, comparable=True):
    return dict(timestamp=t,score=score,price=price,volume_accel=volume,rs=rs,
                r15=1,r60=2,structure='NONE',comparable=comparable,candle_timestamp=int(t)//300*300)


def result(score=72, kind='NONE'):
    return dict(symbol='TESTTRY',score=score,base_score=score,price=103,metrics=dict(r5=.5,r15=1,r60=3,r240=4,volume_accel=2,rs=2),
                structure=dict(kind=kind,setup='100' if kind!='NONE' else None,level=100),
                candle_timestamp=900,confidence=1,book=dict(spread=.1,depth=100000),fomo=[],
                gates=[],safety_gates=[],action_blockers=['breakout confirmation'],eligible=False)


class HistoryTests(unittest.TestCase):
    def test_first_scan_warms_up(self):
        h=compare(observation(900,72),[],Config())
        self.assertIsNone(h['previous_score'])
        self.assertIsNone(h['score_delta_5m'])
        self.assertEqual(h['history_status'],'WARMING UP')

    def test_time_anchors_not_scan_count(self):
        past=[observation(t,s) for t,s in [(0,50),(600,60),(780,65),(840,68)]]
        h=compare(observation(900,72),past,Config())
        self.assertEqual(h['previous_score'],68)
        self.assertEqual(h['score_delta'],4)
        self.assertEqual(h['score_delta_5m'],12)
        self.assertEqual(h['score_delta_15m'],22)
        self.assertEqual(h['trend'],'RISING FAST')

    def test_irregular_interval_normalization(self):
        h=compare(observation(1000,72,volume=2,rs=2),[observation(600,65)],Config())
        self.assertEqual(h['score_delta_5m'],7)
        self.assertAlmostEqual(h['volume_acceleration_trend'],.75)
        self.assertAlmostEqual(h['btc_rs_trend'],.75)

    def test_price_acceleration(self):
        past=[observation(0,55,100),observation(300,64,101)]
        h=compare(observation(600,72,103),past,Config())
        expected=(100*math.log(103/101)/5-100*math.log(101/100)/5)/5
        self.assertAlmostEqual(h['price_acceleration'],expected,places=6)
        self.assertTrue(h['rapidly_forming'])

    def test_rapid_requires_distinct_bars(self):
        past=[observation(0,55),observation(60,64)]
        h=compare(observation(120,72),past,Config())
        self.assertFalse(h['rapidly_forming'])

    def test_history_gaps_and_quality_recovery(self):
        for past in ([observation(0,50)], [observation(600,50,comparable=False)]):
            h=compare(observation(900,72),past,Config())
            self.assertIsNone(h['score_delta_5m'])
            self.assertFalse(h['score_rising'])

    def test_invalid_intermediate_observation_blocks_window(self):
        h=compare(observation(900,72),[observation(600,50),observation(700,0,comparable=False),observation(800,70)],Config())
        self.assertIsNone(h['score_delta_5m'])

    def test_fading_and_flat(self):
        for score,trend in [(60,'FADING'),(69,'FLAT'),(76,'RISING')]:
            self.assertEqual(compare(observation(900,score),[observation(600,70)],Config())['trend'],trend)

    def test_early_watch_two_confirmations(self):
        h=compare(observation(900,72,volume=2,rs=2),[observation(600,64)],Config())
        r=apply_stages(result(),h,Config())
        self.assertEqual(r['stage'],'WATCH')
        self.assertEqual(len(r['early_watch_evidence']),3)
        self.assertFalse(r['eligible'])

    def test_structure_is_only_one_confirmation(self):
        h=compare(observation(900,72),[],Config())
        self.assertEqual(apply_stages(result(kind='BREAKOUT'),h,Config())['stage'],'WATCH')

    def test_only_safety_filters_block_watch(self):
        h=compare(observation(900,72,volume=2,rs=2),[observation(600,64)],Config())
        for gate in ['liquidity/spread safety','ticker aged during scan']:
            r=result()
            r['safety_gates'].append(gate)
            self.assertFalse(apply_stages(r,h,Config())['early_watch'])
        r=result()
        r['action_blockers'].extend(['breakout confirmation','volume acceleration'])
        self.assertTrue(apply_stages(r,h,Config())['early_watch'])

    def test_action_stage_preserves_eligibility(self):
        r=result(85,'BREAKOUT')
        r['eligible']=True
        h=compare(observation(900,85),[],Config())
        self.assertEqual(apply_stages(r,h,Config())['stage'],'ACTION')

    def test_persist_restart_and_retention(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'s.sqlite'
            s=StateManager(path)
            update_history(s,[result(60)],600,Config())
            s.close()
            s=StateManager(path)
            r=result(72)
            update_history(s,[r],900,Config())
            self.assertEqual(r['history']['score_delta_5m'],12)
            saved=json.loads(s.db.execute('SELECT payload FROM score_history ORDER BY timestamp DESC').fetchone()[0])
            self.assertTrue({'timestamp','score','price','r15','r60','volume_accel','rs','structure'} <= saved.keys())
            self.assertEqual(s.pending(),[])
            update_history(s,[result()],8*86400,Config())
            self.assertEqual(s.db.execute('SELECT count(*) FROM score_history').fetchone()[0],1)
            s.close()

    def test_watch_dedup_promotion_and_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=StateManager(Path(tmp)/'s.sqlite')
            cfg=Config()
            r=result()
            r.update(stage='EARLY WATCH',early_watch=True)
            self.assertTrue(s.record_watch(r,cfg,1000))
            self.assertFalse(s.record_watch(r,cfg,1300))
            r.update(score=82,eligible=True,stage='ACTION CANDIDATE',structure=dict(kind='BREAKOUT',setup='1',level=100))
            self.assertTrue(s.record(r,70))
            self.assertFalse(s.record_watch(r,cfg,1400))
            self.assertFalse(s.record(r,70))
            r.update(score=40,eligible=False,early_watch=False,stage='NONE')
            s.record_watch(r,cfg,1500)
            r.update(score=72,early_watch=True,stage='EARLY WATCH')
            self.assertFalse(s.record_watch(r,cfg,1600))
            self.assertTrue(s.record_watch(r,cfg,2400))
            self.assertEqual(len(s.pending()),3)
            s.close()

    def test_table_has_trend_and_delta(self):
        r=result()
        apply_stages(r,compare(observation(900,72),[],Config()),Config())
        out=io.StringIO()
        with redirect_stdout(out):
            ranked_table([r],15)
        self.assertIn('Score Δ',out.getvalue())
        self.assertIn('Previous',out.getvalue())
        self.assertIn('warming up',out.getvalue())

    def test_unavailable_pair_recorded_without_invented_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=StateManager(Path(tmp)/'s.sqlite')
            update_history(s,[result()],900,Config(),['TESTTRY','MISSINGTRY'])
            payload=json.loads(s.db.execute('SELECT payload FROM score_history WHERE symbol=?',('MISSINGTRY',)).fetchone()[0])
            self.assertIsNone(payload['score'])
            self.assertIsNone(payload['price'])
            self.assertFalse(payload['comparable'])
            s.close()


if __name__=='__main__':
    unittest.main()
