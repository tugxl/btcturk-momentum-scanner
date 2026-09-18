import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from config import Config, load_config
from indicators import Candle, metrics, parse_candles
from momentum_engine import anti_fomo, evaluate, structure
from orderbook import analyze_book
from positions import load_positions, position_status
from relative_strength import relative_strength
from signal_engine import signal_level
from state_manager import StateManager


def bars():
    return [Candle(i*300,100,100.2,99.8,100,100) for i in range(60)]


class IndicatorTests(unittest.TestCase):
    def test_relative_strength(self):
        self.assertAlmostEqual(relative_strength(3,-2),5)
        self.assertIsNone(relative_strength(3,None))

    def test_returns(self):
        c=bars()
        c[-1]=Candle(c[-1].t,100,103,100,103,200)
        self.assertAlmostEqual(metrics(c)['r60'],3)
        self.assertAlmostEqual(metrics(c)['r240'],3)

    def test_parse_complete_bars_and_gap(self):
        data=dict(s='ok', t=list(range(0,960,60)),o=[100]*16,h=[101]*16,l=[99]*16,c=[100]*16,v=[1]*16)
        c=parse_candles(data,960)
        self.assertEqual(len(c),3)
        self.assertEqual(c[-1].v,5)
        for key in ('t','o','h','l','c','v'):
            del data[key][6]
        self.assertEqual(len(parse_candles(data,960)),1)
        self.assertEqual(parse_candles(data,1800),[])

    def test_invalid_candles(self):
        with self.assertRaises(ValueError):
            parse_candles(dict(s='ok',t=[0],o=[1],h=[1],l=[1],c=[float('nan')],v=[1]),300)

    def test_native_five_minute_candles(self):
        data=dict(s='ok',t=[0,300,600],o=[100]*3,h=[101]*3,l=[99]*3,c=[100]*3,v=[5]*3)
        self.assertEqual(len(parse_candles(data,900,resolution=5)),3)
        self.assertEqual(len(parse_candles(data,850,resolution=5)),2)

    def test_spread_and_imbalance(self):
        b=analyze_book(dict(bids=[[99,2]],asks=[[101,1]]))
        self.assertAlmostEqual(b['spread'],2)
        self.assertAlmostEqual(b['imbalance'],97/299)
        self.assertEqual(b['depth'],101)

    def test_bad_book(self):
        for book in [dict(bids=[],asks=[]),dict(bids=[[101,1]],asks=[[100,1]])]:
            with self.assertRaises(ValueError):
                analyze_book(book)


class MomentumTests(unittest.TestCase):
    def test_breakout(self):
        c=bars()
        c.append(Candle(18000,100,102,100,101.5,300))
        self.assertEqual(structure(c)['kind'],'BREAKOUT')

    def test_first_retest(self):
        c=bars()+[Candle(18000,100,102,100,101.5,300),Candle(18300,101.5,101.6,100.1,100.5,150),Candle(18600,100.5,102,100.5,101.8,250)]
        self.assertEqual(structure(c)['kind'],'RETEST')
        c[-1]=Candle(18600,100.5,102,99,99.2,250)
        self.assertNotEqual(structure(c)['kind'],'RETEST')

    def test_second_retest_not_accepted(self):
        c=bars()+[Candle(18000,100,102,100,101.5,300),Candle(18300,101.5,101.6,100.1,100.5,150),Candle(18600,100.5,101.9,100.7,101.7,250),Candle(18900,101.7,101.8,100.1,100.5,100),Candle(19200,100.5,102,100.6,101.9,250)]
        self.assertNotEqual(structure(c)['kind'],'RETEST')

    def test_fomo(self):
        self.assertTrue(anti_fomo(dict(r60=12,r240=20,extension=6),dict(level=100),110,Config()))
        self.assertFalse(anti_fomo(dict(r60=3,r240=4,extension=1),dict(level=100),101,Config()))
        self.assertTrue(anti_fomo(dict(volume_ratio=7,drawdown=-3),{},100,Config()))

    def test_score_and_missing_data(self):
        r=evaluate('TESTTRY',100,[],[],None,Config())
        self.assertFalse(r['eligible'])
        self.assertEqual(r['score'],0)
        self.assertEqual(r['confidence'],0)

    def test_high_score_and_fomo_override(self):
        c=bars()
        c[-1]=Candle(c[-1].t,100,103,100,103,300)
        good=dict(r5=1,r15=1.5,r30=2,r60=3,r240=4,volume_ratio=3,volume_accel=2.5,extension=1,spike=2,drawdown=0)
        book=dict(spread=.01,depth=1000000,imbalance=.5)
        with patch('momentum_engine.metrics',side_effect=[good,dict(r60=-1)]):
            r=evaluate('TESTTRY',103,c,bars(),book,Config())
        self.assertGreaterEqual(r['score'],90)
        self.assertTrue(r['eligible'])
        self.assertAlmostEqual(sum(r['components'].values()),r['score'],delta=.1)
        with patch('momentum_engine.metrics',side_effect=[{**good,'r60':12},dict(r60=-1)]):
            r=evaluate('TESTTRY',103,c,bars(),book,Config())
        self.assertFalse(r['eligible'])
        self.assertTrue(r['fomo'])

    def test_benchmark_alignment(self):
        c=bars()
        r=evaluate('TESTTRY',100,c,c[:-1],None,Config())
        self.assertIsNone(r['metrics']['rs'])

    def test_thresholds(self):
        self.assertEqual([signal_level(n) for n in [59,60,70,80,90]],['IGNORE','WATCH','SETUP FORMING','STRONG EARLY MOMENTUM','EXCEPTIONAL'])


class StateTests(unittest.TestCase):
    def test_dedup_escalate_reset_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'state.sqlite'
            state=StateManager(path)
            r=dict(symbol='RAYTRY',score=82,confidence=1,eligible=True,structure=dict(setup='1',level=100))
            self.assertTrue(state.record(r,70))
            r['score']=83
            self.assertFalse(state.record(r,70))
            state.close()
            state=StateManager(path)
            self.assertFalse(state.record(r,70))
            r.update(score=91,structure=dict(setup='2',level=102))
            self.assertTrue(state.record(r,70))
            r.update(score=40,eligible=False)
            state.record(r,70)
            r.update(score=85,eligible=True)
            self.assertFalse(state.record(r,70))
            r['structure']=dict(setup='3',level=103)
            self.assertTrue(state.record(r,70))
            self.assertEqual(len(state.pending()),3)
            state.delivered(state.pending()[0][0])
            self.assertEqual(len(state.pending()),2)
            state.close()

    def test_missing_data_does_not_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            state=StateManager(Path(tmp)/'s.sqlite')
            r=dict(symbol='RAYTRY',score=82,confidence=1,eligible=True,structure=dict(setup='1',level=100))
            state.record(r,70)
            missing={**r,'score':0,'confidence':0,'eligible':False}
            state.record(missing,70)
            self.assertFalse(state.record(r,70))
            state.close()

    def test_positions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'p.json'
            path.write_text(json.dumps([dict(symbol='RAY/TRY',entry_price=100,position_size_try=1000,stop=90,tp1=110,tp2=120)]))
            p=load_positions(path)[0]
            r=dict(price=110,confidence=0,metrics={},fomo=[])
            self.assertIn('+100.00 TRY',position_status(p,r))
            self.assertIn('UNKNOWN',position_status(p,r))

    def test_config_validation(self):
        with self.assertRaises(ValueError):
            Config(workers=0)
        with self.assertRaises(ValueError):
            Config(weights={'momentum':100})


if __name__ == '__main__':
    unittest.main()
