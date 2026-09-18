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

    def close(self):
        self.db.close()
