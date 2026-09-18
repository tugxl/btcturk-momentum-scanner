import json
import logging
from signal_engine import signal_level


class JsonFormatter(logging.Formatter):
    def format(self, record):
        entry = dict(time=self.formatTime(record), level=record.levelname, logger=record.name, message=record.getMessage())
        if record.exc_info:
            entry['exception'] = self.formatException(record.exc_info)
        return json.dumps(entry)


def fmt(value, suffix='', signed=False):
    return 'N/A' if value is None else (f'{value:+.2f}' if signed else f'{value:.2f}') + suffix


def alert_text(r):
    m, book = r['metrics'], r['book'] or {}
    return '\n'.join([
        r.get('stage','ACTION CANDIDATE'),r['symbol'].replace('TRY','/TRY'),
        f"BtcTurk price: {r['price']:.8g} TRY", f"Score: {r['score']}/100 ({signal_level(r['score'])})",
        f"Trend: {r.get('history',{}).get('trend','N/A')} | Previous: {fmt(r.get('history',{}).get('previous_score'))} | Score delta: {fmt(r.get('history',{}).get('score_delta'),signed=True)}",
        f"15m: {fmt(m.get('r15'),'%',True)} | 1h: {fmt(m.get('r60'),'%',True)} | 4h: {fmt(m.get('r240'),'%',True)}",
        f"Volume acceleration: {fmt(m.get('volume_accel'),'x')} | BTC relative strength: {fmt(m.get('rs'),' pp',True)}",
        f"Spread: {fmt(book.get('spread'),'%')} | Order book: healthy",
        'Structure: ' + ('BREAKOUT -> FIRST RETEST -> MOMENTUM RESUMING' if r['structure']['kind']=='RETEST' else r['structure']['kind']),
        'FOMO filter: PASS', f"Invalidation: {fmt(r['stop'])} TRY | resistance zones: {', '.join(fmt(v) for v in r['targets']) or 'N/A (no overhead structure)'} | R/R: {fmt(r['rr'])}",
        f"Components: {r['components']} | penalties: {r['penalties']}",
    ])


def ranked_table(results, top):
    print('Rank | Pair | Price TRY | Score | Previous | Score Δ | Δ5m | Δ15m | Trend | Stage | 15m | 1h | 4h | Vol Accel | BTC RS | Structure | Spread | Liquidity | FOMO | Confidence')
    for i,r in enumerate(results[:top],1):
        m=r['metrics']
        h=r.get('history',{})
        trend=h.get('trend','FLAT')+(' (warming up)' if h.get('history_status')=='WARMING UP' else '')
        stage=r.get('stage','NONE')+(' / RAPIDLY FORMING' if r.get('rapidly_forming') else '')
        book=r['book'] or {}
        liquidity='PASS' if r['book'] and 'liquidity/spread' not in r['gates'] else 'FAIL/UNKNOWN'
        print(f"{i} | {r['symbol']} | {r['price']:.8g} | {r['score']} | {fmt(h.get('previous_score'))} | {fmt(h.get('score_delta'),signed=True)} | {fmt(h.get('score_delta_5m'),signed=True)} | {fmt(h.get('score_delta_15m'),signed=True)} | {trend} | {stage} | {fmt(m.get('r15'),'%',True)} | {fmt(m.get('r60'),'%',True)} | {fmt(m.get('r240'),'%',True)} | {fmt(m.get('volume_accel'),'x')} | {fmt(m.get('rs'),' pp',True)} | {r['structure']['kind']} | {fmt(book.get('spread'),'%')} | {liquidity} | {'FOMO / TOO LATE' if r['fomo'] else ('PASS' if r['confidence']==1 else 'UNKNOWN')} | {r['confidence']:.0%}")
