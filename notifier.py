import json
import logging


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
        r.get('stage','ACTION'),r['symbol'].replace('TRY','/TRY'),
        f"BtcTurk price: {r['price']:.8g} TRY", f"Score: {r['score']}/100 ({r.get('stage','NONE')})",
        f"Trend: {r.get('history',{}).get('trend','N/A')} | Previous: {fmt(r.get('history',{}).get('previous_score'))} | Score delta: {fmt(r.get('history',{}).get('score_delta'),signed=True)}",
        f"5m: {fmt(m.get('r5'),'%',True)} | 15m: {fmt(m.get('r15'),'%',True)} | 1h: {fmt(m.get('r60'),'%',True)}",
        f"Volume ratio: {fmt(m.get('volume_ratio'),'x')} | acceleration: {fmt(m.get('volume_accel'),'x')} | BTC relative strength: {fmt(m.get('rs'),' pp',True)}",
        f"Spread: {fmt(book.get('spread'),'%')} | imbalance: {fmt(book.get('imbalance'))}",
        'Structure: ' + ('BREAKOUT -> FIRST RETEST -> MOMENTUM RESUMING' if r['structure']['kind']=='RETEST' else r['structure']['kind']),
        f"Chase penalties: {r['penalties'] or 'none'}", f"Invalidation: {fmt(r['stop'])} TRY | resistance zones: {', '.join(fmt(v) for v in r['targets']) or 'N/A (no overhead structure)'} | R/R: {fmt(r['rr'])}",
        f"Components: {r['components']} | velocity bonus: +{r.get('score_velocity_bonus',0):.1f}",
    ])


def _candidate_line(index, r):
    m, book = r['metrics'], r.get('book') or {}
    symbol=r['symbol'].removesuffix('TRY')
    missing=r.get('missing_condition') or 'ready'
    return (f"{index}. {symbol} — {r['score']:.1f} — {r['stage']} — fiyat {r['price']:.8g} TRY\n"
            f"   5m {fmt(m.get('r5'),'%',True)} | 15m {fmt(m.get('r15'),'%',True)} | vol {fmt(m.get('volume_ratio'),'x')} | "
            f"spread {fmt(book.get('spread'),'%')} | imbalance {fmt(book.get('imbalance'))} | eksik: {missing}")


def scan_message(results, mode, top=5):
    """Compact Telegram report; mode is ACTION, WATCH, or SUMMARY."""
    if mode == 'ACTION':
        selected=[r for r in results if r['stage']=='ACTION']
        header='🔥 ACTION — RANKED CANDIDATES'
    elif mode == 'WATCH':
        selected=[r for r in results if r['stage']=='WATCH']
        header='🟡 EARLY MOMENTUM WATCH'
    else:
        selected=list(results)
        header='🟡 NO ACTION — TOP WATCHLIST'
    selected=sorted(selected,key=lambda r:r['score'],reverse=True)[:top]
    return '\n\n'.join([header]+[_candidate_line(i,r) for i,r in enumerate(selected,1)])


def diagnostic_report(results):
    print('Rank | Pair | Score | Class | Breakdown | Penalties | Safety | Missing for ACTION')
    for index,r in enumerate(sorted(results,key=lambda x:x['score'],reverse=True),1):
        breakdown=' | '.join(f"{name} {points:+.1f}" for name,points in r['components'].items())
        if r.get('score_velocity_bonus'):
            breakdown += f" | velocity +{r['score_velocity_bonus']:.1f}"
        penalties=', '.join(f'{name} -{points}' for name,points in r['penalties']) or 'none'
        safety=', '.join(r.get('safety_gates',[])) or 'PASS'
        blockers=', '.join(r.get('action_blockers',[])) or 'none'
        print(f"{index} | {r['symbol']} | {r['score']:.1f} | {r['stage']} | {breakdown} | {penalties} | {safety} | {blockers}")


def ranked_table(results, top):
    print('Rank | Pair | Price TRY | Score | Previous | Score Δ | Δ5m | Δ15m | Trend | Stage | 5m | 15m | Vol Ratio | Vol Accel | BTC RS | Structure | Spread | Imbalance | Safety | Confidence')
    for i,r in enumerate(results[:top],1):
        m=r['metrics']
        h=r.get('history',{})
        trend=h.get('trend','FLAT')+(' (warming up)' if h.get('history_status')=='WARMING UP' else '')
        stage=r.get('stage','NONE')+(' / RAPIDLY FORMING' if r.get('rapidly_forming') else '')
        book=r['book'] or {}
        safety='PASS' if not r.get('safety_gates') else 'FAIL'
        print(f"{i} | {r['symbol']} | {r['price']:.8g} | {r['score']} | {fmt(h.get('previous_score'))} | {fmt(h.get('score_delta'),signed=True)} | {fmt(h.get('score_delta_5m'),signed=True)} | {fmt(h.get('score_delta_15m'),signed=True)} | {trend} | {stage} | {fmt(m.get('r5'),'%',True)} | {fmt(m.get('r15'),'%',True)} | {fmt(m.get('volume_ratio'),'x')} | {fmt(m.get('volume_accel'),'x')} | {fmt(m.get('rs'),' pp',True)} | {r['structure']['kind']} | {fmt(book.get('spread'),'%')} | {fmt(book.get('imbalance'))} | {safety} | {r['confidence']:.0%}")
