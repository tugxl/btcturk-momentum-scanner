"""Single scanner instance per SQLite file; durable alert state and audit outbox."""
import json
import sqlite3
import time
from pathlib import Path


class StateManager:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS signals (symbol TEXT PRIMARY KEY, setup TEXT, score REAL, level REAL, active INTEGER)')
        self.db.execute('CREATE TABLE IF NOT EXISTS alerts (id INTEGER PRIMARY KEY, created REAL, payload TEXT, delivered INTEGER DEFAULT 0)')
        self.db.execute('CREATE TABLE IF NOT EXISTS score_history (symbol TEXT, timestamp REAL, payload TEXT, PRIMARY KEY(symbol,timestamp))')
        self.db.execute('CREATE INDEX IF NOT EXISTS score_history_time ON score_history(timestamp)')
        self.db.execute('CREATE TABLE IF NOT EXISTS watches (symbol TEXT PRIMARY KEY, active INTEGER, last_alert REAL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS notification_state (symbol TEXT PRIMARY KEY, stage TEXT, last_alert REAL, last_score REAL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)')
        self.db.commit()

    def record(self, result, reset_threshold):
        symbol, score = result['symbol'], result['score']
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            old = self.db.execute('SELECT setup,score,level,active FROM signals WHERE symbol=?',(symbol,)).fetchone()
            # Missing data is not evidence that a setup has ended.
            if result['confidence'] == 1 and score < reset_threshold:
                self.db.execute('UPDATE signals SET active=0 WHERE symbol=?',(symbol,))
            if not result['eligible']:
                return False
            setup = result['structure']['setup']
            level = result['structure']['level']
            if old and not old[3] and setup == old[0]:
                return False
            escalation = old and score >= 90 and score >= old[1]+8 and setup != old[0] and level > old[2]*1.005
            if old and old[3] and not escalation:
                return False
            self.db.execute('INSERT OR REPLACE INTO signals VALUES (?,?,?,?,1)',(symbol,setup,score,level))
            self.db.execute('INSERT INTO alerts(created,payload) VALUES (?,?)',(time.time(),json.dumps(result, allow_nan=False)))
            return True

    def pending(self):
        return [(row[0],json.loads(row[1])) for row in self.db.execute('SELECT id,payload FROM alerts WHERE delivered=0 ORDER BY id')]

    def record_watch(self, result, cfg, timestamp):
        """One watch per episode; promotion uses the independent action state."""
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            old=self.db.execute('SELECT active,last_alert FROM watches WHERE symbol=?',(result['symbol'],)).fetchone()
            if result['confidence']==1 and result['score'] < cfg.watch_reset_threshold:
                self.db.execute('UPDATE watches SET active=0 WHERE symbol=?',(result['symbol'],))
            if result['stage']=='ACTION CANDIDATE':
                self.db.execute('INSERT OR REPLACE INTO watches VALUES (?,1,?)',(result['symbol'],timestamp))
                return False
            if not result['early_watch'] or (old and (old[0] or timestamp-old[1] < cfg.watch_cooldown_seconds)):
                return False
            self.db.execute('INSERT OR REPLACE INTO watches VALUES (?,1,?)',(result['symbol'],timestamp))
            self.db.execute('INSERT INTO alerts(created,payload) VALUES (?,?)',(timestamp,json.dumps(result,allow_nan=False)))
            return True

    def delivered(self, alert_id):
        with self.db:
            self.db.execute('UPDATE alerts SET delivered=1 WHERE id=?',(alert_id,))

    def notification_events(self, results, cfg, timestamp):
        """Return scan-level notification mode while applying per-symbol dedup/cooldowns."""
        action_trigger = False
        watch_trigger = False
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            for result in results:
                symbol, stage, score = result['symbol'], result['stage'], result['score']
                old=self.db.execute('SELECT stage,last_alert,last_score FROM notification_state WHERE symbol=?',(symbol,)).fetchone()
                old_stage, last_alert, last_score = old or ('NONE',0.,0.)
                new_stage=old_stage
                alert=False
                if stage=='ACTION':
                    alert = old_stage!='ACTION' or (timestamp-last_alert >= cfg.action_cooldown_seconds and score-last_score >= cfg.notify_score_jump)
                    action_trigger = action_trigger or alert
                    new_stage='ACTION'
                elif stage=='WATCH':
                    if old_stage=='ACTION' and score >= cfg.reset_threshold:
                        new_stage='ACTION'
                    else:
                        accelerated = result.get('rapidly_forming') or result.get('history',{}).get('trend')=='RISING FAST'
                        alert = old_stage!='WATCH' or (accelerated and timestamp-last_alert >= cfg.watch_cooldown_seconds and score-last_score >= cfg.notify_score_jump)
                        watch_trigger = watch_trigger or alert
                        new_stage='WATCH'
                elif result['confidence'] >= .75 and not result.get('safety_gates') and score < cfg.watch_reset_threshold:
                    new_stage='NONE'
                saved_alert=timestamp if alert else last_alert
                saved_score=score if alert or not old else last_score
                self.db.execute('INSERT OR REPLACE INTO notification_state VALUES (?,?,?,?)',(symbol,new_stage,saved_alert,saved_score))
            if action_trigger:
                return 'ACTION'
            if any(r['stage']=='ACTION' for r in results):
                return None
            if watch_trigger:
                return 'WATCH'
            row=self.db.execute("SELECT value FROM metadata WHERE key='last_summary'").fetchone()
            last_summary=float(row[0]) if row else 0.
            if timestamp-last_summary >= cfg.summary_interval_seconds:
                self.db.execute("INSERT OR REPLACE INTO metadata VALUES ('last_summary',?)",(str(timestamp),))
                return 'SUMMARY'
        return None

    def queue_message(self, text, timestamp):
        with self.db:
            self.db.execute('INSERT INTO alerts(created,payload) VALUES (?,?)',
                            (timestamp,json.dumps({'kind':'message','text':text},ensure_ascii=False)))

    def close(self):
        self.db.close()
