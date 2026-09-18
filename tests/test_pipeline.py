"""Deterministic public-response fixtures; never represented as live prices."""
import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from btcturk_client import BtcTurkClient
from config import Config, load_config
from market_data import MarketData
from momentum_engine import evaluate


NOW = 1800000000


class FixtureClient:
    def get(self, endpoint, params=None, ttl=0):
        if endpoint == 'exchange':
            return {'data': {'symbols': [dict(name='BTCTRY',denominator='TRY',status='TRADING'),dict(name='RAYTRY',denominator='TRY',status='TRADING'),dict(name='RAYUSDT',denominator='USDT',status='TRADING'),dict(name='OLDTRY',denominator='TRY',status='HALTED')]}}
        if endpoint == 'ticker':
            return {'data': [dict(pair=s,last=100,timestamp=NOW*1000) for s in ('BTCTRY','RAYTRY')]}
        if endpoint == 'book':
            return {'data': dict(timestamp=NOW*1000,bids=[[99.99,10000]],asks=[[100.01,10000]])}
        if endpoint == 'candles':
            step=params['resolution']*60
            size=18000//step
            return dict(s='ok',t=list(range(NOW-18000,NOW,step)),o=[100]*size,h=[100.1]*size,l=[99.9]*size,c=[100]*size,v=[100]*size)
        raise AssertionError(endpoint)


class PipelineTests(unittest.TestCase):
    def test_scan_all_active_try_and_flat_market(self):
        cfg=Config()
        with patch('market_data.time.time',return_value=NOW):
            rows=MarketData(FixtureClient(),cfg).scan()
        self.assertEqual({r[0] for r in rows},{'BTCTRY','RAYTRY'})
        results=[evaluate(s,p,c,b,k,cfg,e) for s,p,c,b,k,e in rows]
        self.assertTrue(all(r['confidence']==1 for r in results))
        self.assertTrue(all(not r['eligible'] for r in results))

    def test_candle_outage_preserves_other_pair(self):
        client=FixtureClient()
        original=client.get
        def get(endpoint, params=None, ttl=0):
            if endpoint=='candles' and params['symbol']=='RAYTRY':
                raise TimeoutError('fixture outage')
            return original(endpoint,params,ttl)
        client.get=get
        with patch('market_data.time.time',return_value=NOW):
            rows=MarketData(client,Config()).scan()
        ray=next(r for r in rows if r[0]=='RAYTRY')
        self.assertEqual(ray[2],[])
        self.assertIn('candles unavailable',ray[-1])

    def test_http_429_retry(self):
        error=HTTPError('fixture',429,'limit',{'Retry-After':'0'},None)
        response=io.BytesIO(json.dumps({'success':True,'data':[]}).encode())
        with patch('btcturk_client.urlopen',side_effect=[error,response]) as request, patch('btcturk_client.time.sleep'):
            self.assertEqual(BtcTurkClient(Config()).get('ticker')['data'],[])
        self.assertEqual(request.call_count,2)
        self.assertEqual(request.call_args.args[0].method,'GET')

    def test_permanent_403_not_retried(self):
        with patch('btcturk_client.urlopen',side_effect=HTTPError('fixture',403,'forbidden',{},None)) as request:
            with self.assertRaises(HTTPError):
                BtcTurkClient(Config()).get('exchange')
        self.assertEqual(request.call_count,1)

    def test_example_configuration(self):
        cfg=load_config('config.example.yaml')
        self.assertEqual(cfg.weights['momentum'],20)


if __name__=='__main__':
    unittest.main()
