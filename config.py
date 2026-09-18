"""Validated, non-secret runtime configuration."""
from dataclasses import dataclass, field
from pathlib import Path
import math


@dataclass
class Config:
    scan_interval: float = 60
    request_interval: float = .35
    timeout: float = 15
    retries: int = 3
    workers: int = 4
    history_hours: int = 24
    top: int = 20
    alert_threshold: float = 80
    reset_threshold: float = 70
    max_spread_pct: float = .5
    min_depth_try: float = 100000
    max_1h_pct: float = 8
    max_4h_pct: float = 15
    max_extension_pct: float = 5
    max_breakout_extension_pct: float = 4
    early_watch_threshold: float = 70
    watch_reset_threshold: float = 65
    watch_cooldown_seconds: float = 900
    score_rise_5m: float = 5
    score_rise_15m: float = 10
    volume_trend_min: float = .2
    rs_trend_min: float = .3
    history_tolerance_seconds: float = 180
    history_max_gap_seconds: float = 600
    history_retention_days: int = 7
    weights: dict = field(default_factory=lambda: dict(momentum=20, volume=20, relative_strength=20, structure=15, liquidity=15, early=10))

    def __post_init__(self):
        for key, value in vars(self).items():
            if key != 'weights' and (not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0):
                raise ValueError(f'{key} must be finite and positive')
        for key in ('workers', 'retries', 'top', 'history_hours'):
            if not isinstance(getattr(self, key), int):
                raise ValueError(f'{key} must be an integer')
        if self.history_hours < 5 or not 60 <= self.alert_threshold <= 100 or self.reset_threshold >= self.alert_threshold:
            raise ValueError('Require history_hours >= 5 and reset < alert threshold (60..100)')
        expected = {'momentum', 'volume', 'relative_strength', 'structure', 'liquidity', 'early'}
        if not 70 <= self.early_watch_threshold <= self.alert_threshold or self.alert_threshold < 80 or self.watch_reset_threshold >= self.early_watch_threshold:
            raise ValueError('Require 70 <= early watch <= action (at least 80), and watch reset below early watch')
        if set(self.weights) != expected or any(not math.isfinite(v) or v < 0 for v in self.weights.values()) or sum(self.weights.values()) <= 0:
            raise ValueError('Invalid weights')


def load_config(path='config.yaml'):
    if not Path(path).exists():
        return Config()
    import yaml
    return Config(**(yaml.safe_load(Path(path).read_text(encoding='utf-8')) or {}))
