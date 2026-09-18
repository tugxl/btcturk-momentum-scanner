# BtcTurk Early Momentum Scanner v2

Deterministic, public-market-data-only scanner for active BtcTurk TRY pairs. It does not trade, provide an automated investment decision, or call an LLM/AI API at runtime. It ranks candidates and reports the evidence.

## Run

```powershell
python main.py --once
python main.py --diagnostic
python main.py --watch
python main.py --watch --json
```

`--once` performs a dry run and records score history without consuming notification state. `--diagnostic` prints every TRY pair, every score component, penalties, mandatory safety failures, and conditions still missing for ACTION. `--watch` scans continuously and sends Telegram messages when `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are configured.

The default interval is 180 seconds. A full public-API scan is intentionally rate-limited and never overlaps the next scan; lower intervals can otherwise create continuous API load on the current TRY universe.

## Classification

- **ACTION (default 80+)**: high-confidence candidate whose mandatory market-data, spread, and depth safety checks pass.
- **WATCH (default 55–79.9)**: momentum is forming, but the weighted evidence is not yet strong enough for ACTION.
- **NONE (<55)**: no meaningful momentum evidence at the moment, or a mandatory safety check failed.

Breakout, positive returns, relative strength, RSI, and volume acceleration are weighted evidence, not individual hard gates. Missing optional indicators lose their component points; they do not independently erase a candidate. Stale/unavailable data, excessive spread, and inadequate two-sided order-book depth remain mandatory safety filters.

## Score (0–100)

| Component | Points | Evidence |
|---|---:|---|
| Momentum | 22 | 1m, 5m, 15m, 30m returns |
| Volume | 18 | volume ratio, acceleration, relative volume, optional trade-count acceleration |
| Trend | 13 | price versus VWAP/EMA9/EMA21 and EMA slopes |
| RSI | 7 | RSI level and three-bar RSI change |
| Breakout | 10 | proximity to the recent local high plus breakout/retest structure |
| Volatility | 7 | ATR percentage and ATR expansion |
| Order book | 10 | bid/ask imbalance, spread quality, two-sided depth |
| BTC relative strength | 8 | 15m/30m/60m performance versus BTC/TRY |
| Early alignment | 5 | volume acceleration + bid pressure + improving short momentum + RSI change |

Score history can add up to eight score-velocity points. A rapidly rising sequence is ranked above a static candidate with the same raw score. Extended 1h/4h moves, VWAP extension, vertical candles, climax reversals, and deep retracements receive chase penalties.

Every result contains `base_score`, `score_velocity_bonus`, `components`, `penalties`, `safety_gates`, `action_blockers`, and `missing_condition` for auditability.

## Telegram policy

- A new ACTION transition is reported immediately. Repeated ACTION reports use per-coin cooldown and material-score-jump deduplication.
- WATCH is reported on first formation or after meaningful acceleration outside the cooldown.
- When there is no ACTION, one hourly `NO ACTION — TOP WATCHLIST` summary shows the five highest scores, including NONE rows when necessary.
- Each row includes score, class, last price, 5m/15m change, volume ratio, spread, imbalance, and the leading condition missing for ACTION.

Notification state and score history are persisted in `SCANNER_DATA_DIR/signals.sqlite3` (default `data/signals.sqlite3`). Failed Telegram sends remain in the durable outbox for retry. If Telegram credentials are absent, reports are printed and treated as delivered console notifications.

## Configuration and tests

Copy `config.example.yaml` to `config.yaml` to override defaults. Secrets belong in environment variables; `.env`, `config.yaml`, positions, and SQLite state are ignored by Git.

```powershell
python -m unittest discover -s tests -v
```

The test suite covers candle validation, expanded indicators, scoring and chase penalties, score velocity, safety-only hard filtering, three-stage classification, notification dedup/cooldowns, hourly summaries, diagnostic output, public API retries, persistence, and the full market-data pipeline.

Positive expectancy is not assumed. Evaluate it with forward returns (for example +30m, +60m, +120m), drawdown, precision by class/score bucket, and walk-forward samples before changing thresholds or using reports operationally.
