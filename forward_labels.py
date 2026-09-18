"""Forward-only labels. Feature timestamps never read bars at or before their future horizon."""
HORIZONS=(15,30,60,90,120)
PATH_TARGETS=((1.,1.),(1.5,1.),(2.,1.),(2.,1.5),(2.5,1.5),(3.,1.5),(3.,2.))
TPS=(1.,1.5,2.,2.5,3.,4.)
SLS=(.75,1.,1.25,1.5,2.)
HOLDS=(30,45,60,90,120)


def _pct(value, entry):
    return (value/entry-1)*100


def _bar_value(bar, key):
    return float(bar[key])


def _path(bars, entry, tp_pct, sl_pct, hold_minutes):
    tp=entry*(1+tp_pct/100)
    sl=entry*(1-sl_pct/100)
    selected=[b for b in bars if b['minutes']<=hold_minutes]
    for bar in selected:
        hit_tp=_bar_value(bar,'high')>=tp
        hit_sl=_bar_value(bar,'low')<=sl
        if hit_tp and hit_sl:
            return dict(outcome='AMBIGUOUS',success=None,gross_return_pct=-sl_pct,ambiguous=True)
        if hit_sl:
            return dict(outcome='SL',success=0,gross_return_pct=-sl_pct,ambiguous=False)
        if hit_tp:
            return dict(outcome='TP',success=1,gross_return_pct=tp_pct,ambiguous=False)
    if not selected:
        return None
    return dict(outcome='TIME',success=0,gross_return_pct=_pct(_bar_value(selected[-1],'close'),entry),ambiguous=False)


def build_forward_labels(entry, timestamp, raw_bars):
    bars=[]
    for bar in raw_bars:
        minutes=(float(bar['timestamp'])-timestamp)/60
        if 0 < minutes <= 122:
            bars.append({**dict(bar), 'minutes':minutes})
    if not bars or bars[-1]['minutes'] < 119:
        return None
    labels={}
    for horizon in HORIZONS:
        eligible=[b for b in bars if b['minutes']<=horizon+1]
        if not eligible or eligible[-1]['minutes']<horizon-2:
            return None
        end=eligible[-1]
        labels[f'return_{horizon}m']=round(_pct(_bar_value(end,'close'),entry),6)
        labels[f'mfe_{horizon}m']=round(max(_pct(_bar_value(b,'high'),entry) for b in eligible),6)
        labels[f'mae_{horizon}m']=round(min(_pct(_bar_value(b,'low'),entry) for b in eligible),6)
    paths={}
    for tp,sl in PATH_TARGETS:
        key=f'tp_{tp:g}_sl_{sl:g}'
        paths[key]=_path(bars,entry,tp,sl,120)
    labels['paths']=paths
    plans={}
    for tp in TPS:
        for sl in SLS:
            for hold in HOLDS:
                plans[f'tp_{tp:g}_sl_{sl:g}_h_{hold}']=_path(bars,entry,tp,sl,hold)
    labels['plans']=plans
    return labels


def label_pending(store, now):
    completed=0
    for row in store.pending_labels(now-120*60):
        bars=store.bars_between(row['symbol'],row['timestamp'],row['timestamp']+122*60)
        labels=build_forward_labels(row['price'],row['timestamp'],bars)
        if labels is not None:
            store.save_label(row['id'],now,labels)
            completed+=1
        elif now-row['timestamp']>150*60:
            store.save_label(row['id'],now,dict(status='UNAVAILABLE',reason='insufficient contiguous future bars'))
    return completed
