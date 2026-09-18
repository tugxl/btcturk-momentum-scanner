import tempfile
import unittest
from pathlib import Path

from config import Config
from forward_labels import build_forward_labels
from opportunity_engine import (confidence_level, optimize_plan, position_size,
                                rank_opportunities, round_trip_cost_pct)
from opportunity_features import BASE_FEATURES, build_feature_rows, classify_regime
from opportunity_store import OpportunityStore
from predictive_model import chronological_splits
from predictive_model import approve_candidate, load_bundle, walk_forward_compare, MODEL_SCHEMA
from shadow_reporting import live_ranking_text, shadow_opportunity_text


class Regressor:
    def __init__(self,value): self.value=value
    def predict(self,x): return [self.value for _ in x]


class Classifier:
    def __init__(self,value): self.value=value
    def predict_proba(self,x): return [[1-self.value,self.value] for _ in x]


def bars(entry=100, both=False):
    result=[]
    for minute in range(1,123):
        close=entry*(1+minute*.001/100)
        high=close*1.001
        low=close*.999
        if minute==5:
            high=102.1 if both else 101.6
            low=98.9 if both else 100
        result.append(dict(timestamp=minute*60,open=entry,high=high,low=low,close=close,volume=10))
    return result


def labels_for_reference(success=1):
    plan=dict(outcome='TP' if success else 'SL',success=success,gross_return_pct=2 if success else -1.5,ambiguous=False)
    return dict(plans={'tp_2_sl_1.5_h_60':plan})


def bundle(return60=1.5,probability=.7):
    targets={}
    for name,value in [('return_30m',1.),('return_60m',return60),('return_120m',1.8),('mfe_60m',2.5),('mae_60m',-.7)]:
        targets[name]=dict(selected=Regressor(value),alternate=Regressor(value-.1),selected_name='ridge',
                           metrics={'ridge':dict(residual_std=.5,objective=.4)})
    for name,value in [('positive_60m',.7),('tp_1.5_sl_1',.68),('tp_2_sl_1.5',probability),('tp_3_sl_2',.55)]:
        targets[name]=dict(selected=Classifier(value),alternate=Classifier(value-.02),selected_name='logistic',
                           metrics={'logistic':dict(calibration_error=.05,objective=.1)})
    reference_features={name:0. for name in BASE_FEATURES}
    reference_features.update(score=72,spread=.1,depth=500000,score_velocity=8)
    reference_values=[reference_features[name] for name in BASE_FEATURES]
    reference=dict(medians=reference_values,scales=[1.]*len(BASE_FEATURES),
                   rows=[dict(values=reference_values,regime='BTC_FLAT / ALT_MIXED',labels=labels_for_reference()) for _ in range(120)])
    return dict(status='approved',created_at=1000,version='test',observations=10000,
                regime_counts={'BTC_FLAT / ALT_MIXED':500},targets=targets,reference=reference)


def feature_row(score=72,safety=True):
    features={name:0. for name in BASE_FEATURES}
    features.update(score=score,spread=.1,depth=500000,score_velocity=8)
    result=dict(symbol='METTRY',price=100.,score=score,book=dict(spread=.1,depth=500000),
                safety_gates=[] if safety else ['liquidity/spread safety'],penalties=[],early_watch_evidence=['score rising'])
    return dict(symbol='METTRY',price=100.,features=features,regime='BTC_FLAT / ALT_MIXED',safety_pass=safety,result=result)


class LeakageAndLabelTests(unittest.TestCase):
    def test_chronological_split_has_no_lookahead_or_timestamp_overlap(self):
        timestamps=[t for t in range(30) for _ in range(3)]
        splits=chronological_splits(timestamps,folds=3,min_train_groups=10)
        self.assertTrue(splits)
        for train,test in splits:
            self.assertLess(max(timestamps[i] for i in train),min(timestamps[i] for i in test))
            self.assertTrue(set(timestamps[i] for i in train).isdisjoint(timestamps[i] for i in test))

    def test_forward_returns_mfe_mae_and_tp_before_sl(self):
        labels=build_forward_labels(100,0,bars())
        self.assertIsNotNone(labels)
        self.assertGreater(labels['return_60m'],0)
        self.assertGreaterEqual(labels['mfe_60m'],labels['return_60m'])
        self.assertLess(labels['mae_60m'],0)
        self.assertEqual(labels['paths']['tp_1.5_sl_1']['outcome'],'TP')

    def test_same_candle_tp_sl_is_conservative_ambiguous(self):
        labels=build_forward_labels(100,0,bars(both=True))
        outcome=labels['paths']['tp_1_sl_1']
        self.assertTrue(outcome['ambiguous'])
        self.assertIsNone(outcome['success'])
        self.assertEqual(outcome['gross_return_pct'],-1)

    def test_linear_and_tree_models_use_walk_forward_oos(self):
        try:
            import sklearn  # noqa: F401
        except ImportError:
            self.skipTest('offline sklearn dependency is not installed')
        rows=[]
        for timestamp in range(40):
            for offset in range(2):
                features={name:float((timestamp+offset)%7) for name in BASE_FEATURES}
                forward=(timestamp%6-2.5)*.2+offset*.05
                rows.append(dict(timestamp=timestamp,features=features,regime='BTC_FLAT / ALT_MIXED',
                                 labels={'return_60m':forward}))
        regression=walk_forward_compare(rows,'return_60m')
        classification=walk_forward_compare(rows,'positive_60m')
        self.assertEqual(set(regression['metrics']),{'ridge','hist_gradient_boosting'})
        self.assertEqual(set(classification['metrics']),{'logistic','hist_gradient_boosting'})
        self.assertGreater(regression['metrics'][regression['selected_name']]['oos_samples'],0)

    def test_candidate_is_not_loaded_until_explicit_approval(self):
        try:
            import joblib
        except ImportError:
            self.skipTest('offline joblib dependency is not installed')
        with tempfile.TemporaryDirectory() as tmp:
            candidate=Path(tmp)/'candidate.joblib'
            production=Path(tmp)/'production.joblib'
            joblib.dump(dict(schema=MODEL_SCHEMA,status='candidate',feature_names=list(BASE_FEATURES),version='x'),candidate)
            self.assertIsNone(load_bundle(candidate,require_approved=True))
            approved=approve_candidate(candidate,production)
            self.assertEqual(approved['status'],'approved')
            self.assertEqual(load_bundle(production)['version'],'x')


class OpportunityTests(unittest.TestCase):
    def test_trading_cost(self):
        cfg=Config(trading_fee_pct_per_side=.1,slippage_pct_per_side=.05)
        self.assertAlmostEqual(round_trip_cost_pct(.2,cfg),.5)

    def test_plan_optimizer_requires_samples_and_uses_net(self):
        refs=[dict(labels=labels_for_reference()) for _ in range(60)]
        plan=optimize_plan(refs,.4,50)
        self.assertEqual((plan['tp_pct'],plan['sl_pct'],plan['max_hold_minutes']),(2,1.5,60))
        self.assertGreater(plan['net_expectancy_pct'],0)
        self.assertIsNone(optimize_plan(refs[:20],.4,50))

    def test_opportunity_ranking_and_no_trade(self):
        cfg=Config(model_min_observations=100,model_min_unique_timestamps=20,min_similar_observations=50,min_regime_observations=50)
        trades,rejected=rank_opportunities([feature_row()],bundle(),cfg,1100)
        self.assertEqual(trades[0]['symbol'],'METTRY')
        self.assertGreater(trades[0]['net_expectancy_pct'],cfg.min_net_expectancy_pct)
        low_bundle=bundle(return60=.1,probability=.4)
        trades,rejected=rank_opportunities([feature_row()],low_bundle,cfg,1100)
        self.assertEqual(trades,[])
        self.assertIn('net expectancy below threshold',rejected[0]['rejection_reasons'])
        unsafe_trades,_=rank_opportunities([feature_row(safety=False)],bundle(),cfg,1100)
        self.assertEqual(unsafe_trades,[])

    def test_confidence_and_position_size(self):
        cfg=Config(model_min_observations=100,min_similar_observations=50,min_regime_observations=50,
                   trading_bankroll_try=5000,max_loss_per_trade_pct=1)
        self.assertEqual(confidence_level(bundle(),120,'BTC_FLAT / ALT_MIXED',.1,.5,cfg),'HIGH')
        self.assertEqual(confidence_level(bundle(),10,'BTC_FLAT / ALT_MIXED',.1,.5,cfg),'LOW')
        self.assertEqual(position_size('LOW',100,98,cfg),None)
        self.assertEqual(position_size('HIGH',100,98,cfg),2500)

    def test_market_regime(self):
        self.assertEqual(classify_regime(dict(r15=.8,atr_expansion=1,realized_volatility=.2),dict(up=.7,down=.2)),
                         'BTC_UP / ALT_BROAD_UP')
        self.assertTrue(classify_regime(dict(r15=0,atr_expansion=2,realized_volatility=.2),dict(up=.5,down=.5)).startswith('BTC_HIGH_VOLATILITY'))

    def test_feature_vector_never_contains_symbol(self):
        result=dict(symbol='METTRY',price=100,score=60,metrics={},book=None,history={},penalties=[],safety_gates=[],components={})
        btc={**result,'symbol':'BTCTRY'}
        row=next(r for r in build_feature_rows([result,btc]) if r['symbol']=='METTRY')
        self.assertNotIn('symbol',row['features'])
        self.assertTrue(set(BASE_FEATURES).issubset(row['features']))
        self.assertIn('v2_components',row['features'])


class ShadowNotificationTests(unittest.TestCase):
    def test_shadow_dedup_and_upgrade(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=OpportunityStore(Path(tmp)/'v3.sqlite')
            cfg=Config()
            item=dict(symbol='METTRY',opportunity_score=80,net_expectancy_pct=1.)
            self.assertTrue(store.should_notify(item,cfg,1000))
            self.assertFalse(store.should_notify(item,cfg,1100))
            upgraded={**item,'opportunity_score':90}
            self.assertTrue(store.should_notify(upgraded,cfg,5000))
            store.close()

    def test_model_not_ready_and_shadow_message(self):
        output=live_ranking_text('BTC_FLAT / ALT_MIXED',[],[],model_ready=False)
        self.assertIn('MODEL NOT READY',output)
        self.assertIn('NO TRADE',output)
        cfg=Config(model_min_observations=100,model_min_unique_timestamps=20,min_similar_observations=50,min_regime_observations=50)
        trade=rank_opportunities([feature_row()],bundle(),cfg,1100)[0][0]
        message=shadow_opportunity_text(trade)
        self.assertIn('SHADOW OPPORTUNITY',message)
        self.assertIn('Net expectancy',message)
        self.assertIn('no order is sent',message)


if __name__=='__main__':
    unittest.main()
