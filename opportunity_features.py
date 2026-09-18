"""Leakage-safe feature vectors computed only from information known at scan time."""
from statistics import median


BASE_FEATURES=(
    'r1','r3','r5','r15','r30','r60','volume_ratio','relative_volume','volume_accel',
    'trade_count_accel','extension','price_vs_ema9','price_vs_ema21','ema9_slope',
    'ema21_slope','ema_separation','rsi','rsi_delta','atr_pct','atr_expansion',
    'realized_volatility','distance_local_high','breakout_distance','breakout_strength',
    'spread','imbalance','bid_depth','ask_depth','depth','rs15','rs30','rs60','score',
    'score_velocity','penalty_total','btc_r1','btc_r5','btc_r15','btc_r30',
    'breadth_up','breadth_down','median_market_r5','btc_up','btc_down','btc_high_vol',
    'alt_broad_up','alt_broad_down','component_momentum','component_volume','component_trend',
    'component_rsi','component_breakout','component_volatility','component_orderbook',
    'component_relative_strength','component_early','chase_penalty_count')


def classify_regime(btc_metrics, breadth):
    atr=btc_metrics.get('atr_expansion') or 0
    rv=btc_metrics.get('realized_volatility') or 0
    r15=btc_metrics.get('r15') or 0
    if atr>=1.6 or rv>=1.2:
        btc='BTC_HIGH_VOLATILITY'
    elif r15>=.5:
        btc='BTC_UP'
    elif r15<=-.5:
        btc='BTC_DOWN'
    else:
        btc='BTC_FLAT'
    if breadth['up']>=.62:
        alt='ALT_BROAD_UP'
    elif breadth['down']>=.62:
        alt='ALT_BROAD_DOWN'
    else:
        alt='ALT_MIXED'
    return f'{btc} / {alt}'


def market_context(results):
    returns=[r['metrics'].get('r5') for r in results if r['metrics'].get('r5') is not None]
    total=len(returns)
    breadth=dict(up=sum(x>0 for x in returns)/total if total else 0.,
                 down=sum(x<0 for x in returns)/total if total else 0.,
                 median=median(returns) if returns else 0.)
    btc=next((r for r in results if r['symbol']=='BTCTRY'),None)
    btc_metrics=(btc or {}).get('metrics',{})
    return breadth,btc_metrics,classify_regime(btc_metrics,breadth)


def build_feature_rows(results):
    breadth,btc,regime=market_context(results)
    rows=[]
    for result in results:
        m=result['metrics']
        book=result.get('book') or {}
        history=result.get('history',{})
        values={key:m.get(key) for key in BASE_FEATURES}
        values.update(spread=book.get('spread'),imbalance=book.get('imbalance'),
                      bid_depth=book.get('bid_depth'),ask_depth=book.get('ask_depth'),depth=book.get('depth'),
                      rs15=m.get('rs15'),rs30=m.get('rs30'),rs60=m.get('rs'),score=result['score'],
                      score_velocity=history.get('score_delta_15m') if history.get('score_delta_15m') is not None else history.get('score_delta_5m'),
                      penalty_total=sum(p[1] for p in result.get('penalties',[])),
                      btc_r1=btc.get('r1'),btc_r5=btc.get('r5'),btc_r15=btc.get('r15'),btc_r30=btc.get('r30'),
                      breadth_up=breadth['up'],breadth_down=breadth['down'],median_market_r5=breadth['median'],
                      btc_up=float(regime.startswith('BTC_UP')),btc_down=float(regime.startswith('BTC_DOWN')),
                      btc_high_vol=float(regime.startswith('BTC_HIGH_VOLATILITY')),
                      alt_broad_up=float(regime.endswith('ALT_BROAD_UP')),alt_broad_down=float(regime.endswith('ALT_BROAD_DOWN')))
        values.update(**{f'component_{name}':result.get('components',{}).get(name) for name in
                         ('momentum','volume','trend','rsi','breakout','volatility','orderbook','relative_strength','early')},
                      chase_penalty_count=len(result.get('penalties',[])))
        values={key:(float(values[key]) if values.get(key) is not None else None) for key in BASE_FEATURES}
        values.update(vwap=m.get('vwap'),ema9=m.get('ema9'),ema21=m.get('ema21'),atr=m.get('atr'),
                      local_high=m.get('local_high'),v2_components=result.get('components',{}),
                      chase_penalties=[name for name,_points in result.get('penalties',[])])
        rows.append(dict(symbol=result['symbol'],price=result['price'],features=values,regime=regime,
                         safety_pass=not result.get('safety_gates'),result=result))
    return rows
