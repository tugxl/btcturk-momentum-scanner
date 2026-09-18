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
        boundary = int(now)//300*300
        cached = self.candle_cache.get(symbol)
        if cached and cached[0] == boundary:
            return cached[1]
        params = dict(symbol=symbol, resolution=5, **{'from': boundary-self.config.history_hours*3600, 'to': boundary})
        try:
            payload = self.client.get('candles', params)
            bars = parse_candles(payload, boundary, resolution=5)
        except Exception as exc:
            log.warning('native_candles_unavailable symbol=%s error=%s',symbol,exc)
            bars = []
        if not bars:
            payload = self.client.get('candles', {**params, 'resolution': 1})
            bars = parse_candles(payload, boundary)
        if bars:
            self.candle_cache[symbol] = (boundary, bars)
        return bars

    def scan(self):
        symbols = self.client.get('exchange', ttl=3600)['data']['symbols']
        universe = sorted({s['name'] for s in symbols if s['denominator'] == 'TRY' and s['status'] == 'TRADING'})
        self.universe = universe
        if not universe:
            raise ValueError('No active TRY pairs returned')
        tickers = {t['pair']: t for t in self.client.get('ticker')['data']}
        now = time.time()
        try:
            btc = self.history('BTCTRY', now)
        except Exception as exc:
            log.warning('benchmark_unavailable error=%s', exc)
            btc = []

        def fetch(symbol):
            errors = []
            ticker = tickers.get(symbol, {})
            try:
                price = float(ticker['last'])
                stamp = float(ticker['timestamp'])/1000
                if not math.isfinite(price) or price <= 0 or not -60 <= time.time()-stamp <= 180:
                    raise ValueError('stale or invalid ticker')
            except (KeyError, TypeError, ValueError):
                log.warning('pair_unavailable symbol=%s reason=missing_or_stale_ticker', symbol)
                return None
            try:
                bars = btc if symbol == 'BTCTRY' else self.history(symbol, now)
            except Exception as exc:
                bars = []
                errors.append('candles unavailable')
                log.warning('candles_unavailable symbol=%s error=%s', symbol, exc)
            try:
                payload = self.client.get('book', dict(pairSymbol=symbol, limit=100))
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
            return symbol, price, bars, btc, book, errors

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
                    symbol, price, bars, benchmark, book, errors = result
                    ticker = refreshed.get(symbol, {})
                    fresh_price = float(ticker.get('last', 0))
                    stamp = float(ticker.get('timestamp', 0))/1000
                    if math.isfinite(fresh_price) and fresh_price > 0 and -60 <= time.time()-stamp <= 180:
                        tickers[symbol] = ticker
                        results[i] = symbol, fresh_price, bars, benchmark, book, errors
            except Exception as exc:
                log.warning('ticker_refresh_failed error=%s',exc)
        # Validate again after all workers finish: early snapshots may have aged.
        finished = time.time()
        for symbol, price, bars, benchmark, book, errors in results:
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
