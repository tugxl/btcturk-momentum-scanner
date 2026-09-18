"""Time-based comparisons of observed scans, never reconstructed scores."""
import json
import math


def snapshot(result, timestamp):
    m = result['metrics']
    safety = result.get('safety_gates')
    if safety is None:
        safety = [g for g in result.get('gates',[]) if any(word in g for word in ('liquidity','stale','aged','unavailable'))]
    return dict(timestamp=timestamp, score=result.get('base_score',result['score']), final_score=result['score'], price=result['price'],
                r5=m.get('r5'), r15=m.get('r15'), r60=m.get('r60'), volume_accel=m.get('volume_accel'),
                rs=m.get('rs'), structure=result['structure']['kind'],
                candle_timestamp=result.get('candle_timestamp'),
                comparable=result['confidence'] >= .75 and not safety)


def compare(current, history, cfg):
    now = current['timestamp']
    past = sorted((h for h in history if h['timestamp'] < now), key=lambda h:h['timestamp'])
    previous = past[-1] if past else None
    out = dict(previous_score=previous['score'] if previous else None, score_delta=None,
               score_delta_5m=None, score_delta_15m=None, price_acceleration=None,
               volume_acceleration_trend=None, btc_rs_trend=None,
               trend='FLAT', history_status='WARMING UP', rapidly_forming=False,
               score_rising=False, volume_rising=False, rs_rising=False)
    def valid(h):
        return h and h['comparable'] and current['comparable']
    if not valid(previous) or now-previous['timestamp'] > cfg.history_max_gap_seconds:
        return out
    out['score_delta'] = round(current['score']-previous['score'], 2)
    def anchor(minutes):
        target = now-minutes*60
        candidates = [h for h in past if target-cfg.history_tolerance_seconds <= h['timestamp'] <= target]
        h = candidates[-1] if candidates else None
        if not valid(h):
            return None
        chain = [p for p in past if h['timestamp'] <= p['timestamp']] + [current]
        if any(not p['comparable'] for p in chain) or any(b['timestamp']-a['timestamp'] > cfg.history_max_gap_seconds for a,b in zip(chain,chain[1:])):
            return None
        return h
    a5, a15 = anchor(5), anchor(15)
    for minutes, a in ((5,a5),(15,a15)):
        if a:
            out[f'score_delta_{minutes}m'] = round(current['score']-a['score'],2)
    if a5:
        out['history_status'] = 'READY'
        dt = (now-a5['timestamp'])/60
        for field, key in [('volume_accel','volume_acceleration_trend'),('rs','btc_rs_trend')]:
            if current[field] is not None and a5[field] is not None:
                out[key] = round((current[field]-a5[field])*5/dt,4)
        out['volume_rising'] = (out['volume_acceleration_trend'] or 0) >= cfg.volume_trend_min
        out['rs_rising'] = (out['btc_rs_trend'] or 0) >= cfg.rs_trend_min
    # Price acceleration: change in log-price velocity divided by midpoint spacing.
    # Use three time anchors, not consecutive near-simultaneous API calls.
    a10 = anchor(10)
    if a5 and a10 and min(current['price'],a5['price'],a10['price']) > 0:
        dt1=(a5['timestamp']-a10['timestamp'])/60
        dt2=(now-a5['timestamp'])/60
        if dt1 > 0:
            v1=100*math.log(a5['price']/a10['price'])/dt1
            v2=100*math.log(current['price']/a5['price'])/dt2
            out['price_acceleration']=round((v2-v1)/((dt1+dt2)/2),6)
    d5,d15=out['score_delta_5m'],out['score_delta_15m']
    out['score_rising'] = (d5 is not None and d5 >= cfg.score_rise_5m) or (d15 is not None and d15 >= cfg.score_rise_15m)
    # Three consecutive distinct completed candle observations: prevents scan-rate noise.
    distinct=[]
    for h in past+[current]:
        if h['timestamp'] < now-900 or not h['comparable']:
            distinct=[]
            continue
        if distinct and h['candle_timestamp'] == distinct[-1]['candle_timestamp']:
            distinct[-1]=h
        else:
            distinct.append(h)
    if len(distinct)>=3 and all(h['candle_timestamp'] is not None for h in distinct[-3:]):
        a,b,c=distinct[-3:]
        out['rapidly_forming'] = (current['score'] >= 60 and now-a['timestamp'] >= 300
                                  and all(y['timestamp']-x['timestamp'] <= cfg.history_max_gap_seconds for x,y in ((a,b),(b,c)))
                                  and b['score']-a['score'] >= 5 and c['score']-b['score'] >= 5 and c['score']-a['score'] >= 12)
    if out['rapidly_forming'] or (d5 is not None and d5 >= 10) or (d15 is not None and d15 >= 15):
        out['trend']='RISING FAST'
    elif out['score_rising']:
        out['trend']='RISING'
    elif (d5 is not None and d5 <= -cfg.score_rise_5m) or (d15 is not None and d15 <= -cfg.score_rise_15m):
        out['trend']='FADING'
    return out


def apply_stages(result, history, cfg):
    result['history']=history
    safety = result.get('safety_gates')
    if safety is None:
        safety = [g for g in result.get('gates',[]) if any(word in g for word in ('liquidity','stale','aged','unavailable'))]
    quality = not safety
    evidence = []
    for key,label in [('score_rising','score rising'),('volume_rising','volume acceleration rising'),('rs_rising','BTC relative strength rising')]:
        if history[key]:
            evidence.append(label)
    if result['structure']['kind'] == 'BREAKOUT':
        evidence.append('fresh breakout')
    elif result['structure']['kind'] == 'RETEST':
        evidence.append('first healthy breakout retest')
    result['early_watch_evidence']=evidence
    velocity = max(0., history.get('score_delta_5m') or 0.)*.35 + max(0., history.get('score_delta_15m') or 0.)*.15
    result['score_velocity_bonus'] = round(min(cfg.score_velocity_max_bonus, velocity),1)
    result['score'] = round(min(100., result.get('base_score',result['score']) + result['score_velocity_bonus']),1)
    result['rapidly_forming']=bool(quality and history['rapidly_forming'])
    result['eligible']=bool(quality and result['score'] >= cfg.alert_threshold)
    result['early_watch']=bool(quality and not result['eligible'] and result['score'] >= cfg.early_watch_threshold)
    result['stage']='ACTION' if result['eligible'] else ('WATCH' if result['early_watch'] else 'NONE')
    blockers = [b for b in result.get('action_blockers',[]) if not b.startswith('score needs')]
    if not result['eligible']:
        gap=max(0.,cfg.alert_threshold-result['score'])
        if gap:
            blockers.insert(0,f'score needs +{gap:.1f}')
    result['action_blockers']=blockers
    result['missing_condition']=(result.get('safety_gates') or blockers or [None])[0]
    return result


def update_history(state, results, timestamp, cfg, universe=None):
    """Compare before insertion; commit all observations and retention atomically."""
    with state.db:
        seen={r['symbol'] for r in results}
        for symbol in set(universe or [])-seen:
            unavailable=dict(timestamp=timestamp,score=None,final_score=None,price=None,r5=None,r15=None,r60=None,volume_accel=None,
                             rs=None,structure='UNAVAILABLE',candle_timestamp=None,comparable=False)
            state.db.execute('INSERT OR REPLACE INTO score_history VALUES (?,?,?)',
                             (symbol,timestamp,json.dumps(unavailable)))
        for result in results:
            rows=state.db.execute('SELECT payload FROM score_history WHERE symbol=? AND timestamp>=? AND timestamp<? ORDER BY timestamp',
                                  (result['symbol'],timestamp-1800,timestamp)).fetchall()
            current=snapshot(result,timestamp)
            apply_stages(result,compare(current,[json.loads(r[0]) for r in rows],cfg),cfg)
            current['final_score']=result['score']
            state.db.execute('INSERT OR REPLACE INTO score_history VALUES (?,?,?)',
                             (result['symbol'],timestamp,json.dumps(current,allow_nan=False)))
        state.db.execute('DELETE FROM score_history WHERE timestamp<?',(timestamp-cfg.history_retention_days*86400,))
