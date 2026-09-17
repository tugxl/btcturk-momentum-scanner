"""Indicators use completed, contiguous five-minute BtcTurk candles only."""
from dataclasses import dataclass
from statistics import mean
import math


@dataclass(frozen=True)
class Candle:
    t: int
    o: float
    h: float
    l: float
    c: float
    v: float


def parse_candles(payload, now, resolution=1):
    if payload.get('s') != 'ok':
        return []
    keys = ('t', 'o', 'h', 'l', 'c', 'v')
    arrays = [payload[k] for k in keys]
    if len({len(a) for a in arrays}) != 1:
        raise ValueError('Mismatched candle arrays')
    minutes = {}
    for row in zip(*arrays):
        t = int(row[0])
        values = list(map(float, row[1:]))
        o, h, l, c, v = values
        if not all(math.isfinite(x) for x in values) or min(o, h, l, c) <= 0 or v < 0 or not l <= min(o, c) <= max(o, c) <= h:
            raise ValueError('Invalid candle')
        step = resolution * 60
        if resolution not in (1, 5) or t % step != 0:
            raise ValueError('Unexpected candle resolution or timestamp')
        if t + step <= now:
            minutes[t] = Candle(t, o, h, l, c, v)
    # Accept native five-minute candles or aggregate complete minute buckets.
    result = []
    for start in sorted({t // 300 * 300 for t in minutes}):
        group = [minutes.get(start + i * step) for i in range(300//step)]
        if all(group) and start + 300 <= now:
            result.append(Candle(start, group[0].o, max(x.h for x in group), min(x.l for x in group), group[-1].c, sum(x.v for x in group)))
    # Never calculate across gaps; retain the latest contiguous tail.
    tail = []
    for candle in result:
        if tail and candle.t != tail[-1].t + 300:
            tail = []
        tail.append(candle)
    return tail if tail and now - (tail[-1].t + 300) <= 360 else []


def pct(current, previous):
    return (current / previous - 1) * 100 if previous and previous > 0 else None


def metrics(candles):
    if not candles:
        return {}
    c = candles
    out = {f'r{m}': pct(c[-1].c, c[-1-m//5].c) if len(c) > m//5 else None for m in (5, 15, 30, 60, 240)}
    if len(c) >= 27:
        baseline = mean(x.v for x in c[-27:-3])
        previous = mean(x.v for x in c[-6:-3])
        recent = mean(x.v for x in c[-3:])
        out.update(volume_ratio=recent / baseline if baseline else None, volume_accel=recent / previous if previous else None)
    total = sum(x.v for x in c[-12:])
    vwap = sum((x.h+x.l+x.c)/3*x.v for x in c[-12:]) / total if total else None
    local_high = max(x.h for x in c[-12:])
    out.update(extension=pct(c[-1].c, vwap), spike=max(pct(x.h, x.o) for x in c[-3:]), drawdown=pct(c[-1].c, max(x.h for x in c[-48:])), local_high=local_high, distance_local_high=pct(c[-1].c, local_high))
    return out
