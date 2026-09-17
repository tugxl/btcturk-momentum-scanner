import math


def analyze_book(payload):
    data = payload.get('data', payload)
    bids = sorted([(float(p), float(q)) for p, q in data['bids']], reverse=True)
    asks = sorted([(float(p), float(q)) for p, q in data['asks']])
    if not bids or not asks or any(not math.isfinite(p) or not math.isfinite(q) or p <= 0 or q <= 0 for p, q in bids + asks):
        raise ValueError('Empty or invalid book')
    bid, ask = bids[0][0], asks[0][0]
    if ask < bid:
        raise ValueError('Crossed book')
    mid = (bid + ask) / 2
    b = sum(p*q for p, q in bids if p >= mid * .99)
    a = sum(p*q for p, q in asks if p <= mid * 1.01)
    return dict(spread=(ask-bid)/mid*100, bid_depth=b, ask_depth=a, depth=min(a,b), imbalance=(b-a)/(b+a) if b+a else 0)
