"""Durable shadow-mode observations, labels, predictions, and notification state."""
import json
import sqlite3
from pathlib import Path


class OpportunityStore:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db=sqlite3.connect(path)
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS feature_snapshots (
          id INTEGER PRIMARY KEY, timestamp REAL NOT NULL, symbol TEXT NOT NULL,
          price REAL NOT NULL, features TEXT NOT NULL, regime TEXT NOT NULL,
          safety_pass INTEGER NOT NULL, UNIQUE(symbol,timestamp));
        CREATE INDEX IF NOT EXISTS feature_snapshot_time ON feature_snapshots(timestamp);
        CREATE TABLE IF NOT EXISTS market_bars (
          symbol TEXT NOT NULL, timestamp REAL NOT NULL, open REAL NOT NULL,
          high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL, volume REAL NOT NULL,
          PRIMARY KEY(symbol,timestamp));
        CREATE INDEX IF NOT EXISTS market_bar_time ON market_bars(timestamp);
        CREATE TABLE IF NOT EXISTS forward_labels (
          snapshot_id INTEGER PRIMARY KEY, completed_at REAL NOT NULL, labels TEXT NOT NULL,
          FOREIGN KEY(snapshot_id) REFERENCES feature_snapshots(id));
        CREATE TABLE IF NOT EXISTS predictions (
          id INTEGER PRIMARY KEY, timestamp REAL NOT NULL, symbol TEXT NOT NULL,
          model_version TEXT NOT NULL, payload TEXT NOT NULL, evaluated INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS shadow_notifications (
          symbol TEXT PRIMARY KEY, last_alert REAL NOT NULL, opportunity_score REAL NOT NULL,
          net_expectancy REAL NOT NULL);
        ''')
        self.db.commit()

    def save_bars(self, raw_rows):
        with self.db:
            for symbol, _price, _bars, minutes, *_rest in raw_rows:
                for candle in minutes[-130:]:
                    self.db.execute('INSERT OR IGNORE INTO market_bars VALUES (?,?,?,?,?,?,?)',
                                    (symbol,candle.t+60,candle.o,candle.h,candle.l,candle.c,candle.v))

    def save_features(self, timestamp, feature_rows):
        with self.db:
            for row in feature_rows:
                self.db.execute('''INSERT OR REPLACE INTO feature_snapshots
                    (timestamp,symbol,price,features,regime,safety_pass) VALUES (?,?,?,?,?,?)''',
                    (timestamp,row['symbol'],row['price'],json.dumps(row['features'],allow_nan=False),
                     row['regime'],int(row['safety_pass'])))

    def pending_labels(self, cutoff):
        return self.db.execute('''SELECT f.* FROM feature_snapshots f
            LEFT JOIN forward_labels l ON l.snapshot_id=f.id
            WHERE l.snapshot_id IS NULL AND f.timestamp<=? ORDER BY f.timestamp,f.symbol''',(cutoff,)).fetchall()

    def bars_between(self, symbol, start, end):
        return self.db.execute('''SELECT timestamp,open,high,low,close,volume FROM market_bars
            WHERE symbol=? AND timestamp>? AND timestamp<=? ORDER BY timestamp''',(symbol,start,end)).fetchall()

    def save_label(self, snapshot_id, completed_at, labels):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO forward_labels VALUES (?,?,?)',
                            (snapshot_id,completed_at,json.dumps(labels,allow_nan=False)))

    def prune_bars(self, cutoff):
        with self.db:
            self.db.execute('DELETE FROM market_bars WHERE timestamp<?',(cutoff,))

    def labelled_rows(self):
        rows=self.db.execute('''SELECT f.timestamp,f.symbol,f.price,f.features,f.regime,l.labels
            FROM feature_snapshots f JOIN forward_labels l ON l.snapshot_id=f.id
            WHERE f.safety_pass=1 ORDER BY f.timestamp,f.symbol''').fetchall()
        parsed=[]
        for r in rows:
            labels=json.loads(r['labels'])
            if labels.get('status')=='UNAVAILABLE':
                continue
            parsed.append(dict(timestamp=r['timestamp'],symbol=r['symbol'],price=r['price'],
                               features=json.loads(r['features']),regime=r['regime'],labels=labels))
        return parsed

    def save_predictions(self, timestamp, opportunities, model_version):
        with self.db:
            for item in opportunities:
                self.db.execute('INSERT INTO predictions(timestamp,symbol,model_version,payload) VALUES (?,?,?,?)',
                                (timestamp,item['symbol'],model_version,json.dumps(item,allow_nan=False)))

    def should_notify(self, item, cfg, timestamp):
        old=self.db.execute('SELECT last_alert,opportunity_score,net_expectancy FROM shadow_notifications WHERE symbol=?',
                            (item['symbol'],)).fetchone()
        notify=(not old or (timestamp-old['last_alert']>=cfg.shadow_cooldown_seconds and
                (item['opportunity_score']-old['opportunity_score']>=cfg.shadow_upgrade_score_delta or
                 item['net_expectancy_pct']-old['net_expectancy']>=cfg.shadow_upgrade_expectancy_delta)))
        if notify:
            with self.db:
                self.db.execute('INSERT OR REPLACE INTO shadow_notifications VALUES (?,?,?,?)',
                                (item['symbol'],timestamp,item['opportunity_score'],item['net_expectancy_pct']))
        return notify

    def close(self):
        self.db.close()
