import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from indicators import parse_candles
from orderbook import analyze_book

log = logging.getLogger(__name__)


class MarketData:
    def __init__(self, client, config):
        self.client, self.config = client, config
        self.candle_cache = {}

    def history(self, symbol, now):
        boundary = int(now)//60*60
        cached = self.candle_cache.get(symbol)
        if cached and cached[0] == boundary:
            return cached[1]
        params = dict(symbol=symbol, resolution=1, **{'from': boundary-self.config.history_hours*3600, 'to': boundary})
        try:
            payload = self.client.get('candles', params)
            minutes = parse_candles(payload, boundary, resolution=1, target_resolution=1)
            bars = parse_candles(payload, boundary, resolution=1, target_resolution=5)
        except Exception as exc:
            log.warning('minute_candles_unavailable symbol=%s error=%s',symbol,exc)
            minutes, bars = [], []
        # Do not issue a second native-5m request when the 1m series is sparse.
        # Sparse pairs are not suitable for the short-horizon predictive dataset;
        # a fallback request roughly doubles scan cost while hiding that data gap.
        if bars:
            self.candle_cache[symbol] = (boundary, (bars, minutes))
        return bars, minutes

    def scan(self):
        symbols = self.client.get('exchange', ttl=3600)['data']['symbols']
        universe = sorted({s['name'] for s in symbols if s['denominator'] == 'TRY' and s['status'] == 'TRADING'})
        self.universe = universe
        if not universe:
            raise ValueError('No active TRY pairs returned')
        tickers = {t['pair']: t for t in self.client.get('ticker')['data']}
        now = time.time()
        try:
            btc, btc_minutes = self.history('BTCTRY', now)
        except Exception as exc:
            log.warning('benchmark_unavailable error=%s', exc)
            btc, btc_minutes = [], []

        def fetch(symbol):
            errors = []
            ticker = tickers.get(symbol, {})
            try:
                price = float(ticker['last'])
                stamp = float(ticker['timestamp'])/1000
                # Validate against the scan snapshot time. A long full-universe scan
                # refreshes ticker prices at the end instead of dropping later symbols.
                if not math.isfinite(price) or price <= 0 or not -60 <= now-stamp <= 180:
                    raise ValueError('stale or invalid ticker')
            except (KeyError, TypeError, ValueError):
                log.warning('pair_unavailable symbol=%s reason=missing_or_stale_ticker', symbol)
                return None
            try:
                if symbol == 'BTCTRY':
                    bars, minutes = btc, btc_minutes
                else:
                    bars, minutes = self.history(symbol, now)
                if not bars:
                    errors.append('candles unavailable')
            except Exception as exc:
                bars, minutes = [], []
                errors.append('candles unavailable')
                log.warning('candles_unavailable symbol=%s error=%s', symbol, exc)
            try:
                # Order-book features are useful only when a contiguous candle history exists.
                # Skipping books for sparse/inactive pairs cuts request volume without losing
                # trainable observations. Cache valid books across one neighbouring cycle.
                if not bars:
                    raise ValueError('book skipped: insufficient candle history')
                payload = self.client.get('book', dict(pairSymbol=symbol, limit=100), ttl=max(300, self.config.scan_interval*1.5))
                stamp = float(payload['data']['timestamp'])/1000
                if not -60 <= time.time()-stamp <= 180:
                    raise ValueError('stale book')
                book = analyze_book(payload)
                book['timestamp'] = stamp
            except Exception as exc:
                book = None
                errors.append('book unavailable')
                log.warning('book_unavailable symbol=%s error=%s', symbol, exc)
            # Snapshots acquired late during a slow scan must not validate stale candles.
            if bars and time.time()-(bars[-1].t+300) > 660:
                errors.append('candles stale during scan')
            return symbol, price, bars, minutes, btc, btc_minutes, book, errors

        results = []
        with ThreadPoolExecutor(max_workers=self.config.workers) as pool:
            for future in as_completed([pool.submit(fetch, s) for s in universe]):
                result = future.result()
                if result:
                    results.append(result)
        # Refresh the bulk ticker when a full-universe scan outlives its snapshot.
        if time.time() - now > 120:
            try:
                refreshed = {t['pair']: t for t in self.client.get('ticker')['data']}
                for i, result in enumerate(results):
                    symbol, price, bars, minutes, benchmark, benchmark_minutes, book, errors = result
                    ticker = refreshed.get(symbol, {})
                    fresh_price = float(ticker.get('last', 0))
                    stamp = float(ticker.get('timestamp', 0))/1000
                    if math.isfinite(fresh_price) and fresh_price > 0 and -60 <= time.time()-stamp <= 180:
                        tickers[symbol] = ticker
                        results[i] = symbol, fresh_price, bars, minutes, benchmark, benchmark_minutes, book, errors
            except Exception as exc:
                log.warning('ticker_refresh_failed error=%s',exc)
        # Validate again after all workers finish: early snapshots may have aged.
        finished = time.time()
        for symbol, price, bars, minutes, benchmark, benchmark_minutes, book, errors in results:
            if finished - float(tickers[symbol]['timestamp'])/1000 > 180:
                errors.append('ticker aged during scan')
            if book and finished - book['timestamp'] > 180:
                errors.append('book aged during scan')
            if bars and finished - (bars[-1].t+300) > 660:
                errors.append('candles aged during scan')
        log.info('scan_coverage active=%d retrieved=%d unavailable=%d', len(universe),len(results),len(universe)-len(results))
        if not results:
            raise ValueError('No fresh BtcTurk TRY prices available')
        return results
