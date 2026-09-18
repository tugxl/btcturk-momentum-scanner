"""Indicators use completed, contiguous five-minute BtcTurk candles only."""
from dataclasses import dataclass
from statistics import mean, median
import math


@dataclass(frozen=True)
class Candle:
    t: int
    o: float
    h: float
    l: float
    c: float
    v: float
    trades: float | None = None


def parse_candles(payload, now, resolution=1, target_resolution=5):
    if payload.get('s') != 'ok':
        return []
    keys = ('t', 'o', 'h', 'l', 'c', 'v')
    arrays = [payload[k] for k in keys]
    if len({len(a) for a in arrays}) != 1:
        raise ValueError('Mismatched candle arrays')
    trade_counts = payload.get('n') or payload.get('trades')
    if not isinstance(trade_counts, (list, tuple)):
        trade_counts = None
    if trade_counts is not None and len(trade_counts) != len(arrays[0]):
        raise ValueError('Mismatched trade-count array')
    minutes = {}
    for index, row in enumerate(zip(*arrays)):
        t = int(row[0])
        values = list(map(float, row[1:]))
        o, h, l, c, v = values
        if not all(math.isfinite(x) for x in values) or min(o, h, l, c) <= 0 or v < 0 or not l <= min(o, c) <= max(o, c) <= h:
            raise ValueError('Invalid candle')
        step = resolution * 60
        if resolution not in (1, 5) or t % step != 0:
            raise ValueError('Unexpected candle resolution or timestamp')
        if t + step <= now:
            trades = float(trade_counts[index]) if trade_counts is not None else None
            if trades is not None and (not math.isfinite(trades) or trades < 0):
                raise ValueError('Invalid trade count')
            minutes[t] = Candle(t, o, h, l, c, v, trades)
    # Accept native data or aggregate complete target buckets.
    target_step = target_resolution * 60
    if target_resolution not in (1, 5) or target_step % step:
        raise ValueError('Unsupported target candle resolution')
    result = []
    for start in sorted({t // target_step * target_step for t in minutes}):
        group = [minutes.get(start + i * step) for i in range(target_step//step)]
        if all(group) and start + target_step <= now:
            trades = None if any(x.trades is None for x in group) else sum(x.trades for x in group)
            result.append(Candle(start, group[0].o, max(x.h for x in group), min(x.l for x in group), group[-1].c, sum(x.v for x in group), trades))
    # Never calculate across gaps; retain the latest contiguous tail.
    tail = []
    for candle in result:
        if tail and candle.t != tail[-1].t + target_step:
            tail = []
        tail.append(candle)
    return tail if tail and now - (tail[-1].t + target_step) <= target_step + 60 else []


def pct(current, previous):
    return (current / previous - 1) * 100 if previous and previous > 0 else None


def _ema(values, period):
    if len(values) < period:
        return None
    alpha = 2 / (period + 1)
    value = mean(values[:period])
    for item in values[period:]:
        value = alpha * item + (1 - alpha) * value
    return value


def _rsi(values, period=14):
    if len(values) <= period:
        return None
    changes = [b-a for a,b in zip(values, values[1:])]
    gains = [max(0, x) for x in changes[-period:]]
    losses = [max(0, -x) for x in changes[-period:]]
    avg_gain, avg_loss = mean(gains), mean(losses)
    if avg_loss == 0:
        return 100. if avg_gain else 50.
    return 100 - 100 / (1 + avg_gain / avg_loss)


def _atr(candles, period=14):
    if len(candles) <= period:
        return None
    ranges = [max(cur.h-cur.l, abs(cur.h-prev.c), abs(cur.l-prev.c)) for prev,cur in zip(candles,candles[1:])]
    return mean(ranges[-period:])


def metrics(candles, minute_candles=None):
    if not candles:
        return {}
    c = candles
    micro = minute_candles or []
    out = {'r1': pct(micro[-1].c, micro[-2].c) if len(micro) >= 2 else None}
    out.update({f'r{m}': pct(c[-1].c, c[-1-m//5].c) if len(c) > m//5 else None for m in (5, 15, 30, 60, 240)})
    if len(c) >= 27:
        baseline = mean(x.v for x in c[-27:-3])
        previous = mean(x.v for x in c[-6:-3])
        recent = mean(x.v for x in c[-3:])
        recent_last = c[-1].v
        history = [x.v for x in c[-27:-3]]
        out.update(volume_ratio=recent / baseline if baseline else None,
                   volume_accel=recent / previous if previous else None,
                   relative_volume=recent_last / median(history) if history and median(history) else None)
        recent_trades = [x.trades for x in c[-3:]]
        previous_trades = [x.trades for x in c[-6:-3]]
        if all(x is not None for x in recent_trades + previous_trades) and mean(previous_trades):
            out['trade_count_accel'] = mean(recent_trades) / mean(previous_trades)
        else:
            out['trade_count_accel'] = None
    total = sum(x.v for x in c[-12:])
    vwap = sum((x.h+x.l+x.c)/3*x.v for x in c[-12:]) / total if total else None
    local_high = max(x.h for x in c[-12:])
    closes = [x.c for x in c]
    ema9, ema21 = _ema(closes, 9), _ema(closes, 21)
    ema9_old = _ema(closes[:-3], 9) if len(c) >= 12 else None
    ema21_old = _ema(closes[:-3], 21) if len(c) >= 24 else None
    rsi = _rsi(closes)
    rsi_old = _rsi(closes[:-3]) if len(c) >= 18 else None
    atr = _atr(c)
    recent_atrs = [_atr(c[:i]) for i in range(max(15, len(c)-5), len(c)+1)]
    baseline_atrs = [_atr(c[:i]) for i in range(max(15, len(c)-17), max(16, len(c)-5))]
    recent_atrs = [x for x in recent_atrs if x is not None]
    baseline_atrs = [x for x in baseline_atrs if x is not None]
    prior_high = max((x.h for x in c[-13:-1]), default=None)
    out.update(vwap=vwap, extension=pct(c[-1].c, vwap), ema9=ema9, ema21=ema21,
               price_vs_ema9=pct(c[-1].c, ema9), price_vs_ema21=pct(c[-1].c, ema21),
               ema9_slope=pct(ema9, ema9_old) if ema9_old else None,
               ema21_slope=pct(ema21, ema21_old) if ema21_old else None,
               rsi=rsi, rsi_delta=(rsi-rsi_old) if rsi is not None and rsi_old is not None else None,
               atr_pct=(atr/c[-1].c*100) if atr else None,
               atr_expansion=mean(recent_atrs)/mean(baseline_atrs) if recent_atrs and baseline_atrs and mean(baseline_atrs) else None,
               spike=max(pct(x.h, x.o) for x in c[-3:]), drawdown=pct(c[-1].c, max(x.h for x in c[-48:])),
               local_high=local_high, distance_local_high=pct(c[-1].c, local_high),
               breakout_distance=pct(c[-1].c, prior_high) if prior_high else None)
    return out
