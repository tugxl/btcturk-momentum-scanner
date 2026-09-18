# BtcTurk Early Momentum Scanner

Read-only Python scanner for every `TRADING` TRY pair reported by BtcTurk. It ranks fresh momentum and first breakout retests, compares them with BTC/TRY, rejects late FOMO setups, and optionally monitors manually entered positions. It never submits orders, requests private exchange keys, or converts another exchange's prices to TRY.

Scores are transparent heuristics, not calibrated probabilities or a promise of continuation over the next 1–2 hours. Validate behavior on live data and paper observations before relying on alerts.

## Install and run

Python 3.10 or later. From this project directory, in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\Activate.ps1
python main.py --once
```

If activation is disabled, use `.\.venv\Scripts\python.exe main.py --once` directly. On macOS/Linux use `source .venv/bin/activate` after creating the environment.

`--once` fetches public data, displays the ranked table and qualifying alert previews, then exits without updating alert suppression. It now saves score history for comparisons across runs. There are no orders in either mode. Unavailable metrics display `N/A`; a total fetch failure returns exit code 1 and a structured error on stderr, never a fabricated ranking.

```powershell
python main.py --watch
python main.py --once --json
python -m unittest discover -s tests -v
```

Watch mode scans every 60 seconds by default; Ctrl+C stops it. Long scans finish before the next scan starts. JSON output includes every result, component score, penalty, gate, metric, and confidence value. Logs go to stderr as JSON. Position rows come first, followed by eligible signals and score-ranked candidates. The table defaults to 20 entries; all pairs are scanned.

## Configuration

Copy `config.example.yaml` to `config.yaml`, then edit weights, thresholds, request pacing, workers, history length, table length, or scan interval. Alternatively pass `--config path.yaml`. Defaults work without any configuration file. `.env.example` documents the optional `SCANNER_DATA_DIR` environment variable; `.env` files are not automatically loaded. No secret is required.

## Scoring and alert gates

Weights are normalized to 100, so changing their sum does not change the score scale:

| Component | Default | Calculation |
|---|---:|---|
| Price momentum | 20 | Average of positive 15m/30m/60m returns clipped at 1.5%/2%/3% |
| Volume | 20 | Equal parts rolling volume ratio and acceleration, full credit at 3x and 2.5x |
| BTC relative strength | 20 | 1h asset return minus BTC return in percentage points; full credit at +3 pp; bonus for strength against falling BTC |
| Structure | 15 | First retest 100%, new breakout 90%, breakout hold 40% |
| Liquidity | 15 | Spread 40%, smaller-side near-market TRY depth 40%, imbalance 20% |
| Early stage | 10 | Fresh setup with positive 1h return up to 5%, sufficient history, and no FOMO flag |

Volume compares the last three completed five-minute bars with the preceding 24 bars; acceleration compares the last three with the prior three. Collapsing acceleration below 0.7x subtracts 15 points; negative BTC with no asset outperformance subtracts 10. Components, penalties, and confidence caps are reported separately.

Score levels: 0–59 IGNORE; 60–69 WATCH; 70–79 SETUP FORMING; 80–89 STRONG EARLY MOMENTUM; 90–100 EXCEPTIONAL. Decimal scores use these lower boundaries.

An ACTION CANDIDATE additionally requires complete metrics, synchronized BTC history, positive 15m/30m/60m returns, positive BTC relative strength, volume acceleration >=1, a fresh breakout or first retest, spread <=0.5%, and at least 100,000 TRY on **each** book side within 1% of midprice. A high score alone never triggers an alert. Daily ticker gains are not used for scoring.

Confidence is the fraction of nine required indicators plus the order book that are available. Missing data gets no component credit and caps the score; any missing required metric prevents an alert. Confidence measures data completeness, not predictive success.

## Structure, anti-FOMO, and levels

A breakout closes at least 0.1% above the previous 24 completed bars' high. The retest detector searches the latest hour for that breakout, then requires the first contiguous pullback-touch episode near its level, no closing failure more than 0.5% below it, and a later candle closing above the previous high with recovering volume. Separate second retests do not qualify. This is a deterministic candle pattern, not a trained forecast.

`FOMO / TOO LATE` overrides alerts when 1h return exceeds 8%, 4h exceeds 15%, extension above the one-hour typical-price VWAP exceeds 5%, price exceeds breakout by 4%, a recent candle spikes over 5%, a >6x volume climax retraces over 2%, or price is over 5% below its four-hour high. Main extension thresholds are configurable; detailed pattern constants are explicit in `momentum_engine.py`.

Invalidation uses the breakout's nearby candle lows or retest low. Resistance zones use observed overhead BtcTurk candle highs within the configured history, clustered by at least 0.3%. No synthetic target is invented at a new high: targets and R/R can be unavailable. R/R uses the nearest overhead zone and invalidation, excludes fees/slippage, and is unavailable if invalidation is not below the current price.

## State and positions

### Score history and earlier detection

Both `--once` and `--watch` persist every observed TRY pair's timestamp, score, BtcTurk price, 15m/1h returns, volume acceleration, BTC relative strength, structure, candle timestamp and data completeness in SQLite. Unavailable pairs in a successful universe scan receive explicit null observations, never invented prices or scores. History survives restarts and retains seven days by default. A total API outage cannot create a new market observation.

The table includes previous score, **Score Δ** from the previous valid recent scan, **Δ5m**, **Δ15m**, and **Trend**. Comparisons use the latest observation at or before each target time with a maximum 180-second tolerance; actual observed scores are never interpolated. Windows spanning missing data or gaps over ten minutes are unavailable. On first run, `FLAT (warming up)` means insufficient history, not measured flat momentum. JSON gives `history_status` explicitly. Earlier exported dry runs are not backfilled into history.

- **EARLY WATCH:** score >=70 and two independent confirmations among a meaningful score rise, rising volume acceleration, rising BTC RS, fresh breakout, or first healthy retest. All existing data-quality, momentum, liquidity, and anti-FOMO gates still apply. Structure may still be forming; breakout and retest are mutually exclusive confirmations.
- **ACTION CANDIDATE:** score >=80 (or a higher configured threshold) and all existing gates including structure pass. Existing scores and weights are unchanged.
- **RAPIDLY FORMING:** a highlight, not a third action stage. Three consecutive distinct completed-candle observations within 15 minutes must gain at least five score points per step and twelve overall, finish at >=60, and pass watch quality gates. A 55→64→72 sequence qualifies. Repeated scans of the same candle cannot manufacture the pattern. It does not send a separate alert below EARLY WATCH eligibility.

Meaningful score rise defaults to +5 points over five minutes or +10 over fifteen. `RISING FAST` means +10/5m, +15/15m, or the sustained rapid pattern; `RISING` means a meaningful rise; `FADING` means −5/5m or −10/15m; otherwise `FLAT`. Fast-rise signals take precedence when windows disagree. Missing history does not count as confirming evidence.

Volume acceleration trend is the change in the volume-acceleration multiplier, normalized to five elapsed minutes (rising threshold +0.2x/5m). BTC RS trend is the change in percentage-point outperformance, normalized to five minutes (threshold +0.3 pp/5m). Price acceleration compares log-price velocities around the 0/5/10-minute anchors and divides by their midpoint separation, in percentage-log-return points per minute squared. These metrics are exposed in JSON; price acceleration is diagnostic and does not independently lower alert requirements.

EARLY WATCH emits once per episode, resets only on complete-data scores below 65, and has a 15-minute re-alert cooldown. Promotion to ACTION CANDIDATE uses independent action suppression and can emit immediately. An active action episode suppresses downgrade watch notifications. `--once` previews signals and updates observations but never consumes a watch/action notification or modifies its suppression state. Ranking prioritizes held positions, action candidates, early watches, rapid setups, then the existing FOMO/score ordering.

All new thresholds and history retention are configurable in `config.example.yaml`. There are no OpenAI, Codex, LLM, or other AI API dependencies or runtime calls. Continuous execution uses ordinary Python and public BtcTurk endpoints; no ChatGPT/Codex credits are used by the scanner process.

Watch mode stores signal state and an alert outbox in `data/signals.sqlite3` with transactional writes and WAL. An 82→83 score change stays suppressed. Escalation requires score >=90, an increase of at least eight points, a new breakout identity, and a breakout level at least 0.5% higher. A complete-data score below 70 resets a signal; a later alert must have a different breakout identity. An outage does not reset state.

Console delivery is at-least-once: a crash between printing and marking delivered can repeat one alert. Pending alerts retain their original data; future push adapters should expire stale pending alerts and use the outbox ID for deduplication. Run one scanner per state directory, retain the directory on restarts, and back up SQLite with its backup API. The alert audit table grows over time; archive it periodically for a long-lived deployment.

`positions.json` starts as `[]`. Copy the shape from `positions.example.json`, using actual manually selected values; example values are only schema examples. Required fields: `symbol`, `entry_price`, `position_size_try`, `stop`, `tp1`, `tp2`. The file reloads each scan. It reports estimated P/L (excluding fees), percent distance from current price to stop/targets, threshold touches, and momentum status. Incomplete data produces UNKNOWN. No position action is executed.

## BtcTurk public API and limitations

Official sources:

- [Public endpoints](https://docs.btcturk.com/docs/public-endpoints/all-public-endpoints/)
- [Exchange metadata](https://docs.btcturk.com/docs/public-endpoints/exchange-info/)
- [Minute history](https://docs.btcturk.com/docs/public-endpoints/get-kline-data/)
- [Order book](https://docs.btcturk.com/docs/public-endpoints/orderbook/)

The client uses only four allowlisted GET endpoints: exchange information, ticker, order book, and graph-api kline history. It requests native five-minute bars (verified against the live API), with a one-minute aggregation fallback when native bars are unavailable. In-progress candles are excluded. Missing five-minute bars break the sequence; only the latest contiguous tail is used. The minute fallback requires every minute in each bucket. New listings, inactive minutes, or history truncation may therefore prevent alerts. No gap filling, external price substitution, or daily-OHLC approximation is used. The separate OHLC endpoint is not a substitute for minute history.

Returns use completed candle closes, while displayed prices are BtcTurk ticker snapshots. Ticker and book timestamps must be fresh within 180 seconds. Market snapshots are not simultaneous. The 100-level book is only visible near-market depth, not a full liquidity/slippage guarantee. Minute history availability and retention are controlled by BtcTurk. A daily bar cannot support 5m/15m momentum.

Calls have timeouts, bounded exponential retry with jitter, shared pacing, and HTTP 429 Retry-After handling. Permanent 4xx errors fail without pointless retries. Pacing (0.35 seconds globally by default) is a conservative setting, not a claimed official quota. Exchange metadata caches for one hour; candles cache for the current five-minute boundary. A large universe can take longer than 60 seconds, especially on the first scan. Decrease load or increase interval if scan-duration warnings appear.

During development on 2026-09-17, an initial bare HTTP probe returned 403, but the completed client successfully retrieved live prices for all 190 active TRY pairs. Live history probes confirmed native five-minute support. BTC minute history contained gaps of up to 15 minutes and native five-minute history also had some gaps; 15-minute bars were contiguous in the sampled day, but cannot replace five-minute metrics. Missing bars are not fabricated. Run `--once` on the intended host to validate connectivity and current coverage before cloud deployment.

## Cloud and iPhone preparation

A Dockerfile is included; no paid service or external notification is configured. Later run one always-on container with restart policy, a persistent `/data` volume, and read-only mounted configuration/positions files. Set `SCANNER_DATA_DIR=/data` (already the container default). Do not run this continuous process on an ephemeral request-only serverless function.

```sh
docker build -t btcturk-momentum .
docker run --name btcturk-momentum --restart unless-stopped -v btcturk-state:/data btcturk-momentum
```

Add an iPhone push adapter to `notifier.py` later (for example a user-chosen push service). Store its credentials in host environment secrets, send only eligible outbox events, use ID-based deduplication, retries and expiry, and mark delivered only after provider acknowledgement. Validate BtcTurk access from the cloud region first. Console logs currently provide the only notification channel. Monitor scan failures, duration, and last successful data timestamp; no cloud resources have been provisioned.

## Project map

`btcturk_client.py`: public HTTP and rate pacing; `market_data.py`: universe, timestamps and concurrent fetching; `indicators.py`: candles and metrics; `relative_strength.py`: BTC comparison; `orderbook.py`: spread/depth/imbalance; `momentum_engine.py`: structure, score and gates; `signal_engine.py`: level labels; `state_manager.py`: SQLite/outbox; `notifier.py`: output/logging; `positions.py`: manual holdings; `config.py`: settings; `main.py`: CLI lifecycle. `tests/` covers scoring, patterns, data validation, positions, and state durability.


