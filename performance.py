"""Out-of-sample shadow prediction performance grouped by score and confidence."""
import json
from statistics import mean, median


def _summary(rows):
    if not rows:
        return dict(samples=0)
    returns=[r['actual_return_60m']-r['cost'] for r in rows]
    wins=[x for x in returns if x>0]
    losses=[x for x in returns if x<0]
    tp=[r['tp_success'] for r in rows if r['tp_success'] is not None]
    return dict(samples=len(rows),prediction_mae=mean(abs(r['predicted']-r['actual_return_60m']) for r in rows),
                win_rate=sum(x>0 for x in returns)/len(returns),mean_net_return=mean(returns),
                median_net_return=median(returns),mean_mfe=mean(r['mfe'] for r in rows),
                mean_mae=mean(r['mae'] for r in rows),tp_before_stop_rate=mean(tp) if tp else None,
                net_expectancy=mean(returns),profit_factor=(sum(wins)/abs(sum(losses))) if losses else None,
                brier=mean((r['positive_probability']-float(r['actual_return_60m']>0))**2 for r in rows))


def performance_report(store):
    joined=store.db.execute('''SELECT p.payload,l.labels FROM predictions p
        JOIN feature_snapshots f ON f.symbol=p.symbol AND f.timestamp=p.timestamp
        JOIN forward_labels l ON l.snapshot_id=f.id ORDER BY p.timestamp''').fetchall()
    rows=[]
    for row in joined:
        prediction=json.loads(row['payload'])
        labels=json.loads(row['labels'])
        if labels.get('status')=='UNAVAILABLE':
            continue
        path=labels['paths']['tp_2_sl_1.5']
        rows.append(dict(predicted=prediction['predicted_return_60m'],positive_probability=prediction['probability_positive_60m'],
                         actual_return_60m=labels['return_60m'],cost=prediction['estimated_round_trip_cost_pct'],
                         mfe=labels['mfe_60m'],mae=labels['mae_60m'],
                         tp_success=None if path['ambiguous'] else path['success'],
                         momentum_score=prediction['momentum_score'],opportunity_score=prediction['opportunity_score'],
                         confidence=prediction['confidence'],regime=prediction['regime']))
    report={'overall':_summary(rows),'momentum_buckets':{},'opportunity_buckets':{},'confidence':{},'regime':{}}
    for low in (50,60,70,80,90):
        high=100 if low==90 else low+10
        label=f'{low}+' if low==90 else f'{low}-{high-1}'
        report['momentum_buckets'][label]=_summary([r for r in rows if low<=r['momentum_score']<(101 if low==90 else high)])
        report['opportunity_buckets'][label]=_summary([r for r in rows if low<=r['opportunity_score']<(101 if low==90 else high)])
    for confidence in ('MEDIUM','HIGH'):
        report['confidence'][confidence]=_summary([r for r in rows if r['confidence']==confidence])
    for regime in sorted({r['regime'] for r in rows}):
        report['regime'][regime]=_summary([r for r in rows if r['regime']==regime])
    return report
