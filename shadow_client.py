"""Instrumented public-only BtcTurk client used exclusively by the V3 worker."""
import json
import logging
import random
import threading
import time
from datetime import datetime,timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError,URLError
from urllib.parse import urlencode
from urllib.request import Request,urlopen

log=logging.getLogger(__name__)


class ShadowBtcTurkClient:
    ENDPOINTS={
        'exchange':'https://api.btcturk.com/api/v2/server/exchangeinfo',
        'ticker':'https://api.btcturk.com/api/v2/ticker',
        'book':'https://api.btcturk.com/api/v2/orderbook',
        'candles':'https://graph-api.btcturk.com/v1/klines/history',
    }

    def __init__(self,config):
        self.config=config
        self.lock=threading.Lock()
        self.next_request=0.
        self.cache={}
        self.counters=dict(requests=0,retries=0,errors=0,rate_limits=0,response_bytes=0)

    def counter_snapshot(self):
        with self.lock:
            return dict(self.counters)

    def _count(self,**values):
        with self.lock:
            for key,value in values.items(): self.counters[key]+=value

    def get(self,endpoint,params=None,ttl=0):
        url=self.ENDPOINTS[endpoint]+('?' + urlencode(params) if params else '')
        with self.lock:
            cached=self.cache.get(url)
            if cached and time.monotonic()<cached[0]: return cached[1]
        for attempt in range(self.config.retries+1):
            with self.lock:
                time.sleep(max(0,self.next_request-time.monotonic()))
                self.next_request=time.monotonic()+self.config.request_interval
            try:
                self._count(requests=1)
                request=Request(url,headers={'User-Agent':'BtcTurkEarlyMomentumScanner-V3/1.0','Accept':'application/json'},method='GET')
                with urlopen(request,timeout=self.config.timeout) as response:
                    payload=json.load(response)
                self._count(response_bytes=len(json.dumps(payload,separators=(',',':')).encode('utf-8')))
                if isinstance(payload,dict) and payload.get('success') is False:
                    self._count(errors=1)
                    raise ValueError('BtcTurk reported unsuccessful response')
                with self.lock:
                    if ttl: self.cache[url]=(time.monotonic()+ttl,payload)
                return payload
            except HTTPError as exc:
                self._count(errors=1,rate_limits=int(exc.code==429))
                if exc.code!=429 and exc.code<500: raise
                delay=2**attempt+random.random()
                retry_after=exc.headers.get('Retry-After')
                if retry_after:
                    try: delay=max(delay,float(retry_after))
                    except ValueError:
                        try: delay=max(delay,(parsedate_to_datetime(retry_after)-datetime.now(timezone.utc)).total_seconds())
                        except (ValueError,TypeError): pass
                with self.lock: self.next_request=max(self.next_request,time.monotonic()+delay)
                if attempt==self.config.retries: raise
            except (URLError,TimeoutError,OSError):
                self._count(errors=1)
                if attempt==self.config.retries: raise
                time.sleep(2**attempt+random.random())
            self._count(retries=1)
            log.warning('request_retry endpoint=%s attempt=%s',endpoint,attempt+1)
