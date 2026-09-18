"""Current, risk-adjusted opportunity inference from an approved offline model."""
import math
from statistics import mean, pstdev

from forward_labels import HOLDS, SLS, TPS
from opportunity_features import BASE_FEATURES


def clamp(value,low=0.,high=1.):
    return max(low,min(high,value))


def round_trip_cost_pct(spread_pct,cfg):
    return float(spread_pct)+2*cfg.slippage_pct_per_side+2*cfg.trading_fee_pct_per_side


def _vector(features):
    return [[features.get(name) for name in BASE_FEATURES]]


def _predict_target(bundle,name,features):
    target=bundle['targets'][name]
    x=_vector(features)
    classification=name in ('positive_60m','tp_1.5_sl_1','tp_2_sl_1.5','tp_3_sl_2')
    if classification:
        selected=float(target['selected'].predict_proba(x)[0][1])
        alternate=float(target['alternate'].predict_proba(x)[0][1])
    else:
        selected=float(target['selected'].predict(x)[0])
        alternate=float(target['alternate'].predict(x)[0])
    return selected,alternate


def similar_references(bundle,features,regime,threshold=1.75):
    reference=bundle['reference']
    medians,scales=reference['medians'],reference['scales']
    current=[medians[i] if features.get(name) is None else float(features[name]) for i,name in enumerate(BASE_FEATURES)]
    matches=[]
    for row in reference['rows']:
        distance=math.sqrt(mean(((a-b)/scales[i])**2 for i,(a,b) in enumerate(zip(current,row['values']))))
        if distance<=threshold:
            matches.append((distance,row))
    matches.sort(key=lambda item:item[0])
    same_regime=[row for _,row in matches if row['regime']==regime]
    return same_regime if len(same_regime)>=20 else [row for _,row in matches]


def optimize_plan(similar,cost_pct,min_samples):
    best=None
    for tp in TPS:
        for sl in SLS:
            for hold in HOLDS:
                key=f'tp_{tp:g}_sl_{sl:g}_h_{hold}'
                outcomes=[row['labels']['plans'].get(key) for row in similar]
                outcomes=[x for x in outcomes if x is not None]
                if len(outcomes)<min_samples:
                    continue
                successes=[x for x in outcomes if not x['ambiguous']]
                probability=sum(x['success'] for x in successes)/len(successes) if successes else 0.
                net_returns=[x['gross_return_pct']-cost_pct for x in outcomes]
                net=mean(net_returns)
                standard_error=pstdev(net_returns)/math.sqrt(len(net_returns)) if len(net_returns)>1 else 99.
                conservative=net-1.28*standard_error
                candidate=dict(tp_pct=tp,sl_pct=sl,max_hold_minutes=hold,samples=len(outcomes),
                               tp_before_stop_probability=probability,net_expectancy_pct=net,
                               conservative_net_expectancy_pct=conservative)
                if best is None or candidate['conservative_net_expectancy_pct']>best['conservative_net_expectancy_pct']:
                    best=candidate
    return best


def confidence_level(bundle,similar_count,regime,agreement,dispersion,cfg):
    regime_count=bundle['regime_counts'].get(regime,0)
    binary=bundle['targets']['positive_60m']
    calibration=binary['metrics'][binary['selected_name']].get('calibration_error',1.)
    if (bundle['observations']>=2*cfg.model_min_observations and similar_count>=2*cfg.min_similar_observations
            and regime_count>=2*cfg.min_regime_observations and agreement<=.35 and dispersion<=1.0 and calibration<=.08):
        return 'HIGH'
    if (similar_count>=cfg.min_similar_observations and regime_count>=cfg.min_regime_observations
            and agreement<=cfg.max_model_disagreement_pct and calibration<=.15):
        return 'MEDIUM'
    return 'LOW'


def position_size(confidence,entry,stop,cfg):
    if cfg.trading_bankroll_try<=0 or confidence=='LOW' or stop>=entry:
        return None
    cap_pct=cfg.high_confidence_max_bankroll_pct if confidence=='HIGH' else cfg.medium_confidence_max_bankroll_pct
    bankroll_cap=cfg.trading_bankroll_try*cap_pct/100
    loss_cap=cfg.trading_bankroll_try*cfg.max_loss_per_trade_pct/100
    stop_fraction=(entry-stop)/entry
    return round(min(bankroll_cap,loss_cap/stop_fraction),2) if stop_fraction>0 else None


def infer_one(feature_row,bundle,cfg,now):
    result=feature_row['result']
    features=feature_row['features']
    predictions={}
    alternates={}
    for target in bundle['targets']:
        predictions[target],alternates[target]=_predict_target(bundle,target,features)
    disagreement=max(abs(predictions[name]-alternates[name]) for name in ('return_30m','return_60m','return_120m'))
    selected_metrics=bundle['targets']['return_60m']['metrics'][bundle['targets']['return_60m']['selected_name']]
    dispersion=selected_metrics.get('residual_std',99.)
    similar=similar_references(bundle,features,feature_row['regime'])
    confidence=confidence_level(bundle,len(similar),feature_row['regime'],disagreement,dispersion,cfg)
    spread=(result.get('book') or {}).get('spread')
    cost=round_trip_cost_pct(spread or 0,cfg)
    gross=predictions['return_60m']
    net=gross-cost
    plan=optimize_plan(similar,cost,cfg.min_similar_observations)
    probability=predictions['tp_2_sl_1.5']
    mfe=predictions['mfe_60m']
    mae=predictions['mae_60m']
    ratio=max(0,mfe)/max(.1,abs(mae))
    liquidity=clamp(1-(spread or cfg.max_spread_pct)/cfg.max_spread_pct)
    velocity=clamp(((features.get('score_velocity') or 0)+2)/17)
    regime_reliability=clamp(bundle['regime_counts'].get(feature_row['regime'],0)/(2*cfg.min_regime_observations))
    confidence_value={'LOW':.25,'MEDIUM':.65,'HIGH':1.}[confidence]
    opportunity_score=round(100*(.30*clamp(net/2)+.20*clamp((probability-.45)/.35)+
        .15*confidence_value+.10*clamp(ratio/3)+.10*liquidity+.08*velocity+.07*regime_reliability),1)
    reasons=[]
    if not feature_row['safety_pass']:
        reasons.extend(result.get('safety_gates') or ['liquidity/data safety'])
    age_days=(now-bundle['created_at'])/86400
    if age_days>cfg.model_max_age_days:
        reasons.append('model stale')
    if len(similar)<cfg.min_similar_observations:
        reasons.append('insufficient similar observations')
    if net<cfg.min_net_expectancy_pct:
        reasons.append('net expectancy below threshold')
    if probability<cfg.min_tp_probability:
        reasons.append('TP-before-stop probability below threshold')
    if confidence=='LOW':
        reasons.append('confidence LOW')
    if disagreement>cfg.max_model_disagreement_pct:
        reasons.append('model disagreement')
    if sum(p[1] for p in result.get('penalties',[]))>=10:
        reasons.append('chase/parabolic risk')
    if plan is None:
        reasons.append('trade-plan sample insufficient')
    entry=result['price']
    tp2_pct=plan['tp_pct'] if plan else 2.
    tp1_pct=max(1.,next((tp for tp in reversed(TPS) if tp<tp2_pct),tp2_pct))
    sl_pct=plan['sl_pct'] if plan else 1.5
    stop=entry*(1-sl_pct/100)
    item=dict(symbol=result['symbol'],price=entry,regime=feature_row['regime'],opportunity_score=opportunity_score,
              momentum_score=result['score'],predicted_return_30m=predictions['return_30m'],
              predicted_return_60m=gross,predicted_return_120m=predictions['return_120m'],
              probability_positive_60m=predictions['positive_60m'],
              probability_tp_1_5_sl_1=predictions['tp_1.5_sl_1'],
              probability_tp_2_sl_1_5=probability,probability_tp_3_sl_2=predictions['tp_3_sl_2'],
              expected_mfe_60m=mfe,expected_mae_60m=mae,gross_expected_return_pct=gross,
              estimated_round_trip_cost_pct=cost,net_expectancy_pct=net,confidence=confidence,
              model_disagreement_pct=disagreement,similar_observations=len(similar),plan=plan,
              probability_tp_before_stop=plan['tp_before_stop_probability'] if plan else probability,
              entry=entry,tp1=entry*(1+tp1_pct/100),tp2=entry*(1+tp2_pct/100),stop=stop,
              max_hold_minutes=plan['max_hold_minutes'] if plan else 60,
              position_size_try=position_size(confidence,entry,stop,cfg),rejection_reasons=reasons,
              why_now=result.get('early_watch_evidence',[]),risks=[p[0] for p in result.get('penalties',[])])
    item['trade']=not reasons
    return item


def rank_opportunities(feature_rows,bundle,cfg,now):
    items=[infer_one(row,bundle,cfg,now) for row in feature_rows if row['symbol']!='BTCTRY']
    items.sort(key=lambda item:(item['trade'],item['opportunity_score'],item['net_expectancy_pct']),reverse=True)
    return [x for x in items if x['trade']],[x for x in items if not x['trade']]
