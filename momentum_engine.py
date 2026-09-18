"""Transparent heuristic scoring. No predictive guarantee or order execution."""
from indicators import metrics, pct
from relative_strength import relative_strength


def clamp(value):
    return max(0., min(1., value))


def scaled(value, low, high):
    """Map a market metric to 0..1 without turning it into an eligibility gate."""
    if value is None or high <= low:
        return 0.
    return clamp((value-low)/(high-low))


def structure(c):
    if len(c) < 26:
        return dict(kind='UNAVAILABLE', setup=None, level=None, stop=None)
    # Find the most recent breakout; retest must be its first distinct touch.
    for i in range(len(c)-1, max(23, len(c)-13), -1):
        level = max(x.h for x in c[i-24:i])
        if c[i].c <= level * 1.001 or c[i-1].c > level:
            continue
        post = c[i+1:]
        base = dict(setup=str(c[i].t), level=level, stop=min(x.l for x in c[max(0,i-3):i+1]))
        if not post:
            return dict(kind='BREAKOUT', **base)
        if any(x.c < level * .995 for x in post):
            continue
        touches = [j for j,x in enumerate(post) if x.l <= level * 1.004]
        episodes = sum(j == 0 or touches[j]-touches[j-1] > 1 for j in range(len(touches)))
        if touches and episodes == 1 and touches[0] < len(post)-1 and post[-1].c > post[-2].h and post[-1].c > level and post[-1].v >= post[-2].v:
            return dict(kind='RETEST', **{**base, 'stop': min(x.l for x in post)})
        return dict(kind='BREAKOUT HOLD', **base)
    return dict(kind='NONE', setup=None, level=None, stop=min(x.l for x in c[-6:]))


def anti_fomo(m, s, price, cfg):
    reasons = []
    for name, limit in [('r60', cfg.max_1h_pct), ('r240', cfg.max_4h_pct), ('extension', cfg.max_extension_pct)]:
        if m.get(name) is not None and m[name] > limit:
            reasons.append(name + ' extended')
    if s.get('level') and pct(price, s['level']) > cfg.max_breakout_extension_pct:
        reasons.append('far above breakout')
    if m.get('spike', 0) > 5:
        reasons.append('vertical candle')
    if m.get('volume_ratio', 0) is not None and m.get('volume_ratio', 0) > 6 and m.get('drawdown', 0) < -2:
        reasons.append('volume climax reversal')
    if m.get('drawdown', 0) < -5:
        reasons.append('intraday spike retracement')
    return reasons


def evaluate(symbol, price, candles, btc, book, cfg, errors=None, minute_candles=None, btc_minutes=None):
    m = metrics(candles, minute_candles)
    b = metrics(btc, btc_minutes)
    aligned = bool(candles and btc and candles[-1].t == btc[-1].t)
    rs = relative_strength(m.get('r60'), b.get('r60')) if aligned else None
    m['rs'] = rs
    m['rs15'] = relative_strength(m.get('r15'), b.get('r15')) if aligned else None
    m['rs30'] = relative_strength(m.get('r30'), b.get('r30')) if aligned else None
    s = structure(candles)
    reasons = anti_fomo(m, s, price, cfg)
    v = m.get('volume_ratio')
    accel = m.get('volume_accel')
    momentum_parts = [scaled(m.get('r1'), -.25, .45), scaled(m.get('r5'), -.4, 1.2),
                      scaled(m.get('r15'), -.7, 2.2), scaled(m.get('r30'), -1., 3.)]
    volume_parts = [scaled(v, .7, 2.5), scaled(accel, .7, 2.2), scaled(m.get('relative_volume'), .8, 3.)]
    if m.get('trade_count_accel') is not None:
        volume_parts.append(scaled(m['trade_count_accel'], .8, 2.))
    trend_parts = [scaled(m.get('price_vs_ema9'), -.8, 1.2), scaled(m.get('price_vs_ema21'), -1.2, 2.),
                   scaled(m.get('ema9_slope'), -.3, 1.), scaled(m.get('ema21_slope'), -.2, .7),
                   scaled(m.get('extension'), -1., 2.)]
    rsi = m.get('rsi')
    rsi_quality = 0. if rsi is None else (scaled(rsi, 40, 62) if rsi <= 72 else scaled(82-rsi, 0, 10))
    rsi_quality = .7*rsi_quality + .3*scaled(m.get('rsi_delta'), -3, 8)
    distance = m.get('breakout_distance')
    proximity = 0. if distance is None else (scaled(distance, -2., .2) if distance <= .2 else scaled(3.-distance, 0, 2.8))
    breakout_quality = clamp(proximity + {'BREAKOUT': .2, 'RETEST': .25, 'BREAKOUT HOLD': .08}.get(s['kind'], 0))
    volatility_quality = .6*scaled(m.get('atr_expansion'), .85, 1.6) + .4*scaled(m.get('atr_pct'), .2, 2.)
    orderbook_quality = 0.
    if book:
        orderbook_quality = .65*scaled(book.get('imbalance'), -.15, .45) + .2*clamp(1-book['spread']/cfg.max_spread_pct) + .15*scaled(book.get('depth'), cfg.min_depth_try, cfg.min_depth_try*5)
    rs_values = [x for x in (m.get('rs15'),m.get('rs30'),rs) if x is not None]
    relative_quality = sum(scaled(x, -.5, 3.) for x in rs_values)/len(rs_values) if rs_values else 0.
    early_flags = [scaled(accel, .9, 1.8), scaled(book.get('imbalance') if book else None, 0, .35),
                   scaled(m.get('r1'), -.1, .3), scaled(m.get('rsi_delta'), -1, 6)]
    early_quality = sum(early_flags)/len(early_flags)
    quality = dict(momentum=sum(momentum_parts)/len(momentum_parts), volume=sum(volume_parts)/len(volume_parts),
                   trend=sum(trend_parts)/len(trend_parts), rsi=rsi_quality, breakout=breakout_quality,
                   volatility=volatility_quality, orderbook=orderbook_quality,
                   relative_strength=relative_quality, early=early_quality)
    total_weight=sum(cfg.weights.values())
    components = {k: round(quality[k]*w/total_weight*100,2) for k,w in cfg.weights.items()}
    penalties = []
    if accel is not None and accel < .7:
        penalties.append(('collapsing volume', 8))
    if b.get('r60', 0) is not None and b.get('r60',0) < 0 and rs is not None and rs <= 0:
        penalties.append(('weak BTC without outperformance', 5))
    penalty_points = {'r60 extended':6, 'r240 extended':7, 'extension extended':8,
                      'far above breakout':6, 'vertical candle':7,
                      'volume climax reversal':10, 'intraday spike retracement':10}
    penalties.extend((reason, penalty_points.get(reason, 5)) for reason in reasons)
    base_score = round(max(0, min(100, sum(components.values()) - sum(p[1] for p in penalties))),1)
    observed = ('r1','r5','r15','r30','volume_ratio','volume_accel','relative_volume','ema9','ema21','rsi','atr_expansion','rs')
    confidence = (sum(m.get(k) is not None for k in observed) + bool(book))/(len(observed)+1)
    safety_gates = [g for g in (errors or []) if 'stale' in g or 'aged' in g or 'unavailable' in g]
    if not book or book['spread'] > cfg.max_spread_pct or book['depth'] < cfg.min_depth_try:
        safety_gates.append('liquidity/spread safety')
    action_blockers = []
    if base_score < cfg.alert_threshold:
        action_blockers.append(f'score needs +{cfg.alert_threshold-base_score:.1f}')
    if (accel or 0) < 1.1:
        action_blockers.append('volume acceleration')
    if max(m.get('r1') or -99, m.get('r5') or -99) <= 0:
        action_blockers.append('improving short-term momentum')
    if distance is None or distance < -.6:
        action_blockers.append('breakout confirmation')
    if not book or book.get('imbalance',0) < .05:
        action_blockers.append('order-book bid pressure')
    stop = s.get('stop')
    highs = sorted(set(x.h for x in candles[:-1] if x.h > price))
    zones = []
    for high in highs:
        if not zones or high > zones[-1]*1.003:
            zones.append(high)
    zones = zones[:2]
    rr = (zones[0]-price)/(price-stop) if zones and stop and stop < price else None
    return dict(symbol=symbol, price=price, score=base_score, base_score=base_score, score_velocity_bonus=0., metrics=m, structure=s, book=book, fomo=reasons,
                candle_timestamp=candles[-1].t if candles else None,
                components=components, penalties=penalties, confidence=confidence, gates=safety_gates,
                safety_gates=safety_gates, action_blockers=action_blockers,
                missing_condition=action_blockers[0] if action_blockers else ('safety filter' if safety_gates else None),
                eligible=False, early_watch=False, stage='NONE', stop=stop, targets=zones, rr=rr)
