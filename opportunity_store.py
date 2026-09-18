"""Durable shadow-mode observations, labels, predictions, and notification state."""
import json
import sqlite3
from pathlib import Path


class OpportunityStore:
    def __init__(self, path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db=sqlite3.connect(self.path)
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
        CREATE TABLE IF NOT EXISTS shadow_health (
          id INTEGER PRIMARY KEY, timestamp REAL NOT NULL, duration REAL NOT NULL,
          discovered_pairs INTEGER NOT NULL, successful_pairs INTEGER NOT NULL,
          failed_pairs INTEGER NOT NULL, stale_pairs INTEGER NOT NULL,
          stale_order_books INTEGER NOT NULL DEFAULT 0,
          snapshots_written INTEGER NOT NULL, api_requests INTEGER NOT NULL,
          api_errors INTEGER NOT NULL, api_retries INTEGER NOT NULL,
          response_bytes INTEGER NOT NULL, db_errors INTEGER NOT NULL,
          label_updates INTEGER NOT NULL, db_size_bytes INTEGER NOT NULL,
          cpu_seconds REAL NOT NULL, cpu_percent REAL NOT NULL, memory_mb REAL,
          status TEXT NOT NULL, error TEXT);
        CREATE INDEX IF NOT EXISTS shadow_health_time ON shadow_health(timestamp);
        CREATE TABLE IF NOT EXISTS shadow_metadata (
          key TEXT PRIMARY KEY, value TEXT NOT NULL);
        ''')
        health_columns={row['name'] for row in self.db.execute('PRAGMA table_info(shadow_health)')}
        if 'stale_order_books' not in health_columns:
            self.db.execute('ALTER TABLE shadow_health ADD COLUMN stale_order_books INTEGER NOT NULL DEFAULT 0')
        self.db.commit()

    def save_bars(self, raw_rows):
        with self.db:
            for symbol, _price, _bars, minutes, *_rest in raw_rows:
                for candle in minutes[-130:]:
                    self.db.execute('INSERT OR IGNORE INTO market_bars VALUES (?,?,?,?,?,?,?)',
                                    (symbol,candle.t+60,candle.o,candle.h,candle.l,candle.c,candle.v))

    def save_features(self, timestamp, feature_rows):
        written=0
        with self.db:
            for row in feature_rows:
                cursor=self.db.execute('''INSERT OR REPLACE INTO feature_snapshots
                    (timestamp,symbol,price,features,regime,safety_pass) VALUES (?,?,?,?,?,?)''',
                    (timestamp,row['symbol'],row['price'],json.dumps(row['features'],allow_nan=False),
                     row['regime'],int(row['safety_pass'])))
                written += max(0,cursor.rowcount)
        return written

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
            return self.db.execute('DELETE FROM market_bars WHERE timestamp<?',(cutoff,)).rowcount

    def db_size_bytes(self):
        return sum(path.stat().st_size for path in (self.path,Path(str(self.path)+'-wal'),Path(str(self.path)+'-shm'))
                   if path.exists())

    def save_health(self, metrics):
        columns=('timestamp','duration','discovered_pairs','successful_pairs','failed_pairs','stale_pairs','stale_order_books',
                 'snapshots_written','api_requests','api_errors','api_retries','response_bytes','db_errors',
                 'label_updates','db_size_bytes','cpu_seconds','cpu_percent','memory_mb','status','error')
        with self.db:
            self.db.execute(f"INSERT INTO shadow_health ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                            tuple(metrics.get(key) for key in columns))

    def metadata(self, key, default=None):
        row=self.db.execute('SELECT value FROM shadow_metadata WHERE key=?',(key,)).fetchone()
        return json.loads(row['value']) if row else default

    def set_metadata(self, key, value):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO shadow_metadata VALUES (?,?)',(key,json.dumps(value)))

    def dataset_stats(self):
        feature=self.db.execute('''SELECT COUNT(*) rows,COUNT(DISTINCT timestamp) scans,
            MIN(timestamp) first_at,MAX(timestamp) last_at,COUNT(DISTINCT symbol) pairs
            FROM feature_snapshots''').fetchone()
        label_counts=self.db.execute('''SELECT COUNT(*) total,
            SUM(CASE WHEN labels LIKE '%"status": "UNAVAILABLE"%' THEN 1 ELSE 0 END) unavailable
            FROM forward_labels''').fetchone()
        unavailable=label_counts['unavailable'] or 0
        completed=label_counts['total']-unavailable
        days=0
        if feature['first_at'] is not None:
            days=len(self.db.execute("SELECT DISTINCT date(timestamp,'unixepoch') FROM feature_snapshots").fetchall())
        return dict(feature_rows=feature['rows'],unique_scan_timestamps=feature['scans'],
                    first_at=feature['first_at'],last_at=feature['last_at'],active_pairs=feature['pairs'],
                    completed_labels=completed,unavailable_labels=unavailable,calendar_days=days)

    def health_rows(self, since=0):
        return self.db.execute('SELECT * FROM shadow_health WHERE timestamp>=? ORDER BY timestamp',(since,)).fetchall()

    def quality_counts(self, now, expected_interval, retention_days):
        window_start=now-86400
        duplicates=self.db.execute('''SELECT COUNT(*) n FROM (SELECT symbol,timestamp,COUNT(*) c
            FROM feature_snapshots WHERE timestamp>=? GROUP BY symbol,timestamp HAVING c>1)''',(window_start,)).fetchone()['n']
        invalid_prices=self.db.execute('''SELECT COUNT(*) n FROM feature_snapshots
            WHERE timestamp>=? AND (price<=0 OR price!=price)''',(window_start,)).fetchone()['n']
        timestamps=[r['timestamp'] for r in self.db.execute(
            'SELECT DISTINCT timestamp FROM feature_snapshots WHERE timestamp>=? ORDER BY timestamp',(window_start,)).fetchall()]
        gaps=[b-a for a,b in zip(timestamps,timestamps[1:])]
        feature_rows=self.db.execute('SELECT features FROM feature_snapshots WHERE timestamp>=?',(window_start,)).fetchall()
        # Gate on features required for a trainable short-horizon observation.
        # Optional fields (for example trade-count acceleration and score velocity)
        # may legitimately be absent during warm-up and must not fail the dataset.
        critical_features=('r5','r15','r30','r60','volume_ratio','volume_accel',
                           'price_vs_ema9','price_vs_ema21','rsi','atr_pct',
                           'spread','imbalance','depth','score')
        values=missing=0
        trainable_rows=0
        for row in feature_rows:
            features=json.loads(row['features'])
            row_values=[features.get(key) for key in critical_features]
            values += len(row_values)
            row_missing=sum(value is None for value in row_values)
            missing += row_missing
            trainable_rows += int(row_missing==0)
        eligible=self.db.execute('SELECT COUNT(*) n FROM feature_snapshots WHERE timestamp<=?',(now-150*60,)).fetchone()['n']
        labelled=self.db.execute('''SELECT COUNT(*) n FROM forward_labels l JOIN feature_snapshots f ON f.id=l.snapshot_id
            WHERE f.timestamp<=? AND l.labels NOT LIKE '%"status": "UNAVAILABLE"%' ''',(now-150*60,)).fetchone()['n']
        bar_gaps=self.db.execute('''SELECT COUNT(*) n FROM (
            SELECT timestamp-LAG(timestamp) OVER (PARTITION BY symbol ORDER BY timestamp) gap
            FROM market_bars WHERE timestamp>=?) WHERE gap>90''',(now-retention_days*86400,)).fetchone()['n']
        old_bars=self.db.execute('SELECT COUNT(*) n FROM market_bars WHERE timestamp<?',
                                 (now-retention_days*86400-120,)).fetchone()['n']
        unavailable=self.dataset_stats()['unavailable_labels']
        return dict(duplicate_timestamps=duplicates,invalid_prices=invalid_prices,
                    max_timestamp_gap=max(gaps,default=0),feature_nan_rate=missing/values if values else 0,
                    trainable_feature_rows=trainable_rows,total_feature_rows=len(feature_rows),
                    label_completion_rate=labelled/eligible if eligible else 1.,missing_bars=unavailable+bar_gaps,
                    stale_order_books=sum(r['stale_order_books'] for r in self.health_rows(now-86400)),
                    raw_bars_past_retention=old_bars,expected_interval=expected_interval)

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
