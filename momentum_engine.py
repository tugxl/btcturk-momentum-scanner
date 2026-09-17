"""Transparent heuristic scoring. No predictive guarantee or order execution."""
from indicators import metrics, pct
from relative_strength import relative_strength


def clamp(value):
    return max(0., min(1., value))


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


def evaluate(symbol, price, candles, btc, book, cfg, errors=None):
    m = metrics(candles)
    b = metrics(btc)
    aligned = bool(candles and btc and candles[-1].t == btc[-1].t)
    rs = relative_strength(m.get('r60'), b.get('r60')) if aligned else None
    m['rs'] = rs
    s = structure(candles)
    reasons = anti_fomo(m, s, price, cfg)
    v = m.get('volume_ratio')
    accel = m.get('volume_accel')
    quality = dict(momentum=sum(clamp((m.get('r'+str(n)) or 0)/target) for n,target in [(15,1.5),(30,2),(60,3)])/3,
                   volume=(clamp(((v or 0)-1)/2)+clamp(((accel or 0)-1)/1.5))/2,
                   relative_strength=clamp((rs or 0)/3),
                   structure={'BREAKOUT': .9, 'RETEST': 1, 'BREAKOUT HOLD': .4}.get(s['kind'],0),
                   liquidity=0., early=0.)
    if rs is not None and b.get('r60') is not None and b['r60'] < 0 and rs > 2:
        quality['relative_strength'] = clamp(quality['relative_strength'] + .15)
    if book:
        quality['liquidity'] = .4*clamp(1-book['spread']/cfg.max_spread_pct)+.4*clamp(book['depth']/cfg.min_depth_try)+.2*clamp((book['imbalance']+1)/2)
    if m.get('r240') is not None and not reasons:
        quality['early'] = 1. if 0 < (m.get('r60') or 0) <= 5 and s['kind'] in ('BREAKOUT','RETEST') else .3
    components = {k: round(quality[k]*w/sum(cfg.weights.values())*100,2) for k,w in cfg.weights.items()}
    penalties = []
    if accel is not None and accel < .7:
        penalties.append(('collapsing volume', 15))
    if b.get('r60', 0) is not None and b.get('r60',0) < 0 and rs is not None and rs <= 0:
        penalties.append(('weak BTC without outperformance', 10))
    score = round(max(0, min(100, sum(components.values()) - sum(p[1] for p in penalties))),1)
    available = sum(m.get(k) is not None for k in ('r5','r15','r30','r60','r240','volume_ratio','volume_accel','extension','rs')) + bool(book)
    confidence = available / 10
    score = min(score, round(confidence*100,1))
    gates = list(errors or [])
    if confidence < 1:
        gates.append('incomplete market data')
    if not book or book['spread'] > cfg.max_spread_pct or book['depth'] < cfg.min_depth_try:
        gates.append('liquidity/spread')
    if s['kind'] not in ('BREAKOUT','RETEST'):
        gates.append('no fresh breakout/retest')
    if not all((m.get('r'+str(n)) or 0) > 0 for n in (15,30,60)) or (rs or 0) <= 0 or (accel or 0) < 1:
        gates.append('momentum confirmation missing')
    if reasons:
        gates.append('FOMO / TOO LATE')
    stop = s.get('stop')
    highs = sorted(set(x.h for x in candles[:-1] if x.h > price))
    zones = []
    for high in highs:
        if not zones or high > zones[-1]*1.003:
            zones.append(high)
    zones = zones[:2]
    rr = (zones[0]-price)/(price-stop) if zones and stop and stop < price else None
    return dict(symbol=symbol, price=price, score=score, metrics=m, structure=s, book=book, fomo=reasons,
                candle_timestamp=candles[-1].t if candles else None,
                components=components, penalties=penalties, confidence=confidence, gates=gates,
                eligible=not gates and score >= cfg.alert_threshold, stop=stop, targets=zones, rr=rr)
