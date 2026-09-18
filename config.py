"""Validated, non-secret runtime configuration."""
from dataclasses import dataclass, field
from pathlib import Path
import math


@dataclass
class Config:
    scan_interval: float = 180
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
    early_watch_threshold: float = 55
    watch_reset_threshold: float = 48
    watch_cooldown_seconds: float = 900
    action_cooldown_seconds: float = 1800
    summary_interval_seconds: float = 3600
    notify_score_jump: float = 7
    score_velocity_max_bonus: float = 8
    trading_fee_pct_per_side: float = .12
    slippage_pct_per_side: float = .05
    min_net_expectancy_pct: float = .30
    min_tp_probability: float = .55
    max_model_disagreement_pct: float = 1.0
    model_min_observations: int = 5000
    model_min_unique_timestamps: int = 200
    model_min_history_days: float = 7
    min_similar_observations: int = 50
    min_regime_observations: int = 100
    model_max_age_days: float = 14
    raw_bar_retention_days: int = 8
    shadow_upgrade_score_delta: float = 8
    shadow_upgrade_expectancy_delta: float = .35
    shadow_cooldown_seconds: float = 3600
    trading_bankroll_try: float = 0
    max_loss_per_trade_pct: float = 1
    high_confidence_max_bankroll_pct: float = 50
    medium_confidence_max_bankroll_pct: float = 25
    score_rise_5m: float = 5
    score_rise_15m: float = 10
    volume_trend_min: float = .2
    rs_trend_min: float = .3
    history_tolerance_seconds: float = 180
    history_max_gap_seconds: float = 600
    history_retention_days: int = 7
    weights: dict = field(default_factory=lambda: dict(momentum=22, volume=18, trend=13, rsi=7,
                                                       breakout=10, volatility=7, orderbook=10,
                                                       relative_strength=8, early=5))

    def __post_init__(self):
        allow_zero={'trading_bankroll_try','trading_fee_pct_per_side','slippage_pct_per_side',
                    'min_net_expectancy_pct','shadow_upgrade_expectancy_delta'}
        for key, value in vars(self).items():
            if key != 'weights' and (not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or (value == 0 and key not in allow_zero)):
                raise ValueError(f'{key} must be finite and positive')
        for key in ('workers', 'retries', 'top', 'history_hours', 'model_min_observations',
                    'model_min_unique_timestamps', 'min_similar_observations', 'min_regime_observations',
                    'raw_bar_retention_days'):
            if not isinstance(getattr(self, key), int):
                raise ValueError(f'{key} must be an integer')
        if self.history_hours < 5 or not 60 <= self.alert_threshold <= 100 or self.reset_threshold >= self.alert_threshold:
            raise ValueError('Require history_hours >= 5 and reset < alert threshold (60..100)')
        expected = {'momentum', 'volume', 'trend', 'rsi', 'breakout', 'volatility', 'orderbook', 'relative_strength', 'early'}
        if not 40 <= self.early_watch_threshold < self.alert_threshold or self.alert_threshold < 70 or self.watch_reset_threshold >= self.early_watch_threshold:
            raise ValueError('Require 40 <= watch < action (at least 70), and watch reset below watch')
        if set(self.weights) != expected or any(not math.isfinite(v) or v < 0 for v in self.weights.values()) or sum(self.weights.values()) <= 0:
            raise ValueError('Invalid weights')
        if max(self.high_confidence_max_bankroll_pct,self.medium_confidence_max_bankroll_pct,self.max_loss_per_trade_pct)>100:
            raise ValueError('Risk percentages cannot exceed 100')


def load_config(path='config.yaml'):
    if not Path(path).exists():
        return Config()
    import yaml
    data=yaml.safe_load(Path(path).read_text(encoding='utf-8')) or {}
    legacy={'momentum','volume','relative_strength','structure','liquidity','early'}
    if set(data.get('weights',{})) == legacy:
        data['weights']=Config().weights
        if data.get('early_watch_threshold') == 70:
            data['early_watch_threshold']=55
        if data.get('watch_reset_threshold') == 65:
            data['watch_reset_threshold']=48
        if data.get('scan_interval') == 60:
            data['scan_interval']=180
    return Config(**data)
