"""Operational health, readiness and quality reporting for the data-only V3 worker."""
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from statistics import median
import time
import urllib.parse
import urllib.request

log=logging.getLogger(__name__)


def _env_number(name,default,cast=float):
    value=cast(os.getenv(name,default))
    if value<=0: raise ValueError(f'{name} must be positive')
    return value


@dataclass(frozen=True)
class ShadowSettings:
    scan_interval: float
    raw_bar_retention_days: int
    model_min_observations: int
    model_min_unique_timestamps: int
    model_min_history_days: float
    shadow_api_failure_alert_threshold: int
    shadow_alert_cooldown_seconds: float
    shadow_health_summary_seconds: float
    shadow_gap_warning_seconds: float
    shadow_gap_fail_seconds: float
    shadow_nan_warning_rate: float
    shadow_nan_fail_rate: float
    shadow_label_warning_rate: float

    @classmethod
    def from_config(cls,cfg):
        return cls(cfg.scan_interval,cfg.raw_bar_retention_days,cfg.model_min_observations,
                   cfg.model_min_unique_timestamps,cfg.model_min_history_days,
                   _env_number('V3_API_FAILURE_ALERT_THRESHOLD',3,int),
                   _env_number('V3_ALERT_COOLDOWN_SECONDS',21600),
                   _env_number('V3_HEALTH_SUMMARY_SECONDS',86400),
                   _env_number('V3_GAP_WARNING_SECONDS',600),_env_number('V3_GAP_FAIL_SECONDS',1800),
                   _env_number('V3_NAN_WARNING_RATE',.10),_env_number('V3_NAN_FAIL_RATE',.25),
                   _env_number('V3_LABEL_WARNING_RATE',.90))


def resource_snapshot():
    memory_mb=None
    try:
        status=Path('/proc/self/status').read_text(encoding='utf-8')
        line=next(line for line in status.splitlines() if line.startswith('VmRSS:'))
        memory_mb=float(line.split()[1])/1024
    except (OSError,StopIteration,ValueError):
        try:
            import resource
            rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            memory_mb=float(rss)/(1024 if os.name!='nt' else 1024*1024)
        except (ImportError,OSError,ValueError):
            pass
    return dict(cpu=time.process_time(),memory_mb=memory_mb)


def quality_report(store,cfg,now=None):
    now=now or time.time()
    values=store.quality_counts(now,cfg.scan_interval,cfg.raw_bar_retention_days)
    fail=(values['duplicate_timestamps']>0 or values['invalid_prices']>0 or
          values['raw_bars_past_retention']>0 or values['max_timestamp_gap']>cfg.shadow_gap_fail_seconds or
          values['feature_nan_rate']>cfg.shadow_nan_fail_rate or
          (values['label_completion_rate']<.75 and values['missing_bars']>0))
    warning=(values['max_timestamp_gap']>cfg.shadow_gap_warning_seconds or
             values['feature_nan_rate']>cfg.shadow_nan_warning_rate or values['stale_order_books']>0 or
             values['missing_bars']>0 or values['label_completion_rate']<cfg.shadow_label_warning_rate)
    values['status']='FAIL' if fail else 'WARNING' if warning else 'PASS'
    return values


def readiness(store,cfg):
    stats=store.dataset_stats()
    stats['ready']=(stats['completed_labels']>=cfg.model_min_observations and
                    stats['unique_scan_timestamps']>=cfg.model_min_unique_timestamps and
                    stats['calendar_days']>=cfg.model_min_history_days)
    return stats


class ShadowReporter:
    """V3-only Telegram channel. It never reads the V1 Telegram environment variables."""
    def __init__(self,store,cfg):
        self.store,self.cfg=store,cfg
        self._last_alert={}

    @staticmethod
    def configured():
        return bool(os.getenv('V3_TELEGRAM_BOT_TOKEN') and os.getenv('V3_TELEGRAM_CHAT_ID'))

    def send(self,message):
        if not self.configured():
            log.info('shadow_notification_not_configured message=%s',message.replace('\n',' | '))
            return True
        data=urllib.parse.urlencode({'chat_id':os.environ['V3_TELEGRAM_CHAT_ID'],'text':message}).encode()
        request=urllib.request.Request(
            f"https://api.telegram.org/bot{os.environ['V3_TELEGRAM_BOT_TOKEN']}/sendMessage",data=data)
        try:
            urllib.request.urlopen(request,timeout=10).read()
            return True
        except Exception as exc:
            log.error('shadow_telegram_failed error=%s',exc)
            return False

    def alert(self,key,message,now=None):
        now=now or time.time()
        try:
            last=float(self.store.metadata(f'alert:{key}',self._last_alert.get(key,0)))
        except Exception:
            last=self._last_alert.get(key,0)
        if last and now-last<self.cfg.shadow_alert_cooldown_seconds:
            return False
        sent=self.send(message)
        if sent:
            self._last_alert[key]=now
            try:
                self.store.set_metadata(f'alert:{key}',now)
            except Exception:
                log.exception('shadow_alert_dedup_write_failed key=%s',key)
        return sent

    def after_scan(self,metrics,now=None):
        now=now or metrics['timestamp']
        stats=readiness(self.store,self.cfg)
        quality=quality_report(self.store,self.cfg,now)
        if metrics['status']!='ok':
            self.alert('collection','DATA COLLECTION FAILURE\n'+(metrics.get('error') or 'unknown error'),now)
        elif quality['status']=='FAIL':
            self.alert('quality','DATA COLLECTION FAILURE\nDATA QUALITY: FAIL\n'
                       f"Critical NaN rate: {quality['feature_nan_rate']:.1%}; trainable rows: {quality['trainable_feature_rows']}/{quality['total_feature_rows']}; missing bars: {quality['missing_bars']}; "
                       f"stale books: {quality['stale_order_books']}",now)
        consecutive=int(self.store.metadata('consecutive_api_failures',0))
        consecutive=consecutive+1 if metrics['api_errors'] else 0
        self.store.set_metadata('consecutive_api_failures',consecutive)
        if consecutive>=self.cfg.shadow_api_failure_alert_threshold:
            self.alert('api',f'BTC API FAILURE > {self.cfg.shadow_api_failure_alert_threshold}\nConsecutive scans: {consecutive}',now)
        if stats['ready'] and not self.store.metadata('dataset_ready_alerted',False):
            if self.send('🧪 V3 DATASET READY FOR TRAINING'):
                self.store.set_metadata('dataset_ready_alerted',True)
        started=float(self.store.metadata('worker_started_at',now))
        last=float(self.store.metadata('last_health_summary',0))
        if now-started>=self.cfg.shadow_health_summary_seconds and now-last>=self.cfg.shadow_health_summary_seconds:
            rows=self.store.health_rows(now-self.cfg.shadow_health_summary_seconds)
            scans=len(rows)
            snapshots=sum(row['snapshots_written'] for row in rows)
            failed=sum(row['status']!='ok' for row in rows)
            durations=[row['duration'] for row in rows]
            first_size=rows[0]['db_size_bytes'] if rows else metrics['db_size_bytes']
            growth=metrics['db_size_bytes']-first_size
            requests=sum(row['api_requests'] for row in rows)
            message=(f"🧪 V3 SHADOW HEALTH\n\nUptime: {(now-started)/3600:.1f}h\nScans: {scans}\n"
                     f"Snapshots: {snapshots}\nCompleted labels: {stats['completed_labels']}\n"
                     f"Active TRY pairs: {metrics['discovered_pairs']}\nFailed scans: {failed}\n"
                     f"Median scan duration: {median(durations) if durations else 0:.1f}s\n"
                     f"DB size: {metrics['db_size_bytes']/1048576:.2f} MB\n"
                     f"DB growth/24h: {growth/1048576:.2f} MB\nAPI requests/24h: {requests}\n"
                     f"Data days accumulated: {stats['calendar_days']}\nModel status: "
                     f"{'READY FOR TRAINING' if stats['ready'] else 'MODEL NOT READY'}\n"
                     f"DATA QUALITY: {quality['status']}")
            if self.send(message):
                self.store.set_metadata('last_health_summary',now)
        return stats,quality
