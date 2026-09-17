def relative_strength(asset_return, btc_return):
    """Percentage-point outperformance, using identical candle end times."""
    if asset_return is None or btc_return is None:
        return None
    return asset_return - btc_return
