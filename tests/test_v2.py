import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from config import Config
from indicators import Candle, metrics
from notifier import diagnostic_report, scan_message
from score_history import apply_stages, compare
from state_manager import StateManager


def candidate(score=60, stage='WATCH', symbol='METTRY'):
    return dict(symbol=symbol, score=score, base_score=score, price=12.34, stage=stage,
                eligible=stage=='ACTION', early_watch=stage=='WATCH', rapidly_forming=False,
                confidence=1., safety_gates=[], gates=[], action_blockers=['breakout confirmation'],
                missing_condition='breakout confirmation', score_velocity_bonus=0,
                metrics=dict(r5=.4,r15=.8,volume_ratio=1.7,volume_accel=1.4,rs=.5),
                book=dict(spread=.08,imbalance=.22,depth=250000), components={'volume':12},
                penalties=[], structure=dict(kind='NONE',setup=None,level=None))


def observation(timestamp, score):
    return dict(timestamp=timestamp, score=score, final_score=score, price=100, r5=.2, r15=.5, r60=1,
                volume_accel=1.3, rs=.5, structure='NONE', comparable=True,
                candle_timestamp=timestamp//300*300)


class IndicatorV2Tests(unittest.TestCase):
    def test_extended_metrics(self):
        candles=[]
        price=100.
        for i in range(70):
            price *= 1.001
            candles.append(Candle(i*300,price*.999,price*1.002,price*.998,price,100+i*2,50+i))
        minute=[Candle(i*60,100+i*.01,100+i*.02,99+i*.01,100+i*.01,10,5) for i in range(10)]
        out=metrics(candles,minute)
        for key in ('r1','r5','r15','r30','volume_ratio','volume_accel','relative_volume',
                    'price_vs_ema9','price_vs_ema21','ema9_slope','rsi','rsi_delta',
                    'atr_pct','atr_expansion','breakout_distance','trade_count_accel'):
            self.assertIn(key,out)
            self.assertIsNotNone(out[key])


class ScoreVelocityTests(unittest.TestCase):
    def test_velocity_bonus_changes_classification(self):
        cfg=Config()
        current=observation(900,50)
        history=compare(current,[observation(0,40),observation(600,40)],cfg)
        result=candidate(50,stage='NONE')
        apply_stages(result,history,cfg)
        self.assertEqual(result['score_velocity_bonus'],5)
        self.assertEqual(result['score'],55)
        self.assertEqual(result['stage'],'WATCH')

    def test_soft_missing_confirmation_does_not_block_watch(self):
        result=candidate(60)
        result['action_blockers']=['breakout confirmation','volume acceleration']
        apply_stages(result,compare(observation(900,60),[],Config()),Config())
        self.assertEqual(result['stage'],'WATCH')
        result['safety_gates']=['liquidity/spread safety']
        apply_stages(result,compare(observation(900,60),[],Config()),Config())
        self.assertEqual(result['stage'],'NONE')


class NotificationV2Tests(unittest.TestCase):
    def test_dedup_acceleration_action_and_hourly_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            state=StateManager(Path(tmp)/'state.sqlite')
            cfg=Config()
            watch=candidate(60)
            self.assertEqual(state.notification_events([watch],cfg,1000),'WATCH')
            self.assertIsNone(state.notification_events([watch],cfg,1100))
            faster={**watch,'score':70,'base_score':70,'rapidly_forming':True,
                    'history':{'trend':'RISING FAST'}}
            self.assertEqual(state.notification_events([faster],cfg,2000),'WATCH')
            action={**faster,'stage':'ACTION','eligible':True,'early_watch':False,'score':82}
            self.assertEqual(state.notification_events([action],cfg,2010),'ACTION')
            self.assertIsNone(state.notification_events([action],cfg,2050))
            none={**watch,'stage':'NONE','score':30,'base_score':30,'early_watch':False}
            self.assertEqual(state.notification_events([none],cfg,6000),'SUMMARY')
            self.assertIsNone(state.notification_events([none],cfg,6100))
            state.close()

    def test_messages_contain_required_fields(self):
        message=scan_message([candidate(68)],'SUMMARY')
        for text in ('NO ACTION','MET','68.0','WATCH','fiyat','5m','15m','vol','spread','imbalance','eksik'):
            self.assertIn(text,message)

    def test_diagnostic_explains_breakdown_and_losses(self):
        result=candidate(60)
        result['penalties']=[('vertical candle',7)]
        stream=io.StringIO()
        with redirect_stdout(stream):
            diagnostic_report([result])
        output=stream.getvalue()
        self.assertIn('volume +12.0',output)
        self.assertIn('vertical candle -7',output)
        self.assertIn('breakout confirmation',output)


if __name__ == '__main__':
    unittest.main()
