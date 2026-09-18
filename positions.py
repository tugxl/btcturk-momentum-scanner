import json
import math
from pathlib import Path
from indicators import pct


def load_positions(path):
    if not Path(path).exists():
        return []
    positions = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(positions, list):
        raise ValueError('positions.json must be a list')
    seen = set()
    for p in positions:
        p['symbol'] = p['symbol'].upper().replace('/', '').replace('_', '')
        if not p['symbol'].endswith('TRY') or p['symbol'] in seen:
            raise ValueError('Positions require unique TRY symbols')
        seen.add(p['symbol'])
        for k in ('entry_price','position_size_try','stop','tp1','tp2'):
            if not isinstance(p[k], (int,float)) or not math.isfinite(p[k]) or p[k] <= 0:
                raise ValueError(f'Invalid position field: {k}')
        if not p['stop'] < p['entry_price'] < p['tp1'] <= p['tp2']:
            raise ValueError('Require stop < entry < TP1 <= TP2')
    return positions


def position_status(p, result):
    if result is None:
        return f"POSITION {p['symbol']}: market data unavailable"
    price, m = result['price'], result['metrics']
    change = pct(price, p['entry_price'])
    intact = result['confidence'] == 1 and (m.get('r15') or 0) > 0 and (m.get('rs') or 0) > 0 and (m.get('volume_accel') or 0) >= .7 and not result['fomo']
    status = 'UNKNOWN' if result['confidence'] < 1 else ('INTACT' if intact else 'DETERIORATING')
    flags = ' STOP REACHED' if price <= p['stop'] else (' TP2 REACHED' if price >= p['tp2'] else (' TP1 REACHED' if price >= p['tp1'] else ''))
    return (f"POSITION {p['symbol']} | {price:.8g} TRY | P/L {change:+.2f}% / {p['position_size_try']*change/100:+.2f} TRY "
            f"| stop distance {(price-p['stop'])/price*100:.2f}% | TP1 {pct(p['tp1'],price):+.2f}% | TP2 {pct(p['tp2'],price):+.2f}% | {status}{flags}")
