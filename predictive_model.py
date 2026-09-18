"""Offline sklearn training with timestamp-grouped walk-forward validation."""
from collections import Counter
from datetime import datetime, timezone
import math
from pathlib import Path
from statistics import mean, median, pstdev

from opportunity_features import BASE_FEATURES

MODEL_SCHEMA=1
REGRESSION_TARGETS=('return_30m','return_60m','return_120m','mfe_60m','mae_60m')
CLASSIFICATION_TARGETS=('positive_60m','tp_1.5_sl_1','tp_2_sl_1.5','tp_3_sl_2')


def chronological_splits(timestamps, folds=3, min_train_groups=20):
    unique=sorted(set(timestamps))
    if len(unique)<min_train_groups+folds:
        return []
    remaining=len(unique)-min_train_groups
    width=max(1,remaining//folds)
    splits=[]
    for fold in range(folds):
        start=min_train_groups+fold*width
        end=len(unique) if fold==folds-1 else min(len(unique),start+width)
        if start>=len(unique) or start==end:
            continue
        train_times=set(unique[:start])
        test_times=set(unique[start:end])
        train=[i for i,t in enumerate(timestamps) if t in train_times]
        test=[i for i,t in enumerate(timestamps) if t in test_times]
        if train and test and max(timestamps[i] for i in train)<min(timestamps[i] for i in test):
            splits.append((train,test))
    return splits


def _target(row, name):
    labels=row['labels']
    if name=='positive_60m':
        return float(labels['return_60m']>0)
    if name.startswith('tp_'):
        outcome=labels['paths'].get(name)
        return None if not outcome or outcome['ambiguous'] else float(outcome['success'])
    return float(labels[name])


def _matrix(rows):
    return [[row['features'].get(name) for name in BASE_FEATURES] for row in rows]


def _factories(classification):
    from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression, Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    if classification:
        return {
            'logistic':lambda:make_pipeline(SimpleImputer(strategy='median'),StandardScaler(),LogisticRegression(max_iter=1000,class_weight='balanced',random_state=17)),
            'hist_gradient_boosting':lambda:make_pipeline(SimpleImputer(strategy='median'),HistGradientBoostingClassifier(max_iter=150,max_leaf_nodes=15,l2_regularization=1.,random_state=17)),
        }
    return {
        'ridge':lambda:make_pipeline(SimpleImputer(strategy='median'),StandardScaler(),Ridge(alpha=10.)),
        'hist_gradient_boosting':lambda:make_pipeline(SimpleImputer(strategy='median'),HistGradientBoostingRegressor(max_iter=150,max_leaf_nodes=15,l2_regularization=1.,random_state=17)),
    }


def _calibration_error(y,probabilities):
    buckets=[]
    for low in (0,.2,.4,.6,.8):
        pairs=[(a,p) for a,p in zip(y,probabilities) if low<=p<(low+.2 if low<.8 else 1.000001)]
        if pairs:
            buckets.append(abs(mean(a for a,_ in pairs)-mean(p for _,p in pairs))*len(pairs))
    return sum(buckets)/len(y) if y else None


def walk_forward_compare(rows, target):
    classification=target in CLASSIFICATION_TARGETS
    usable=[row for row in rows if _target(row,target) is not None]
    timestamps=[row['timestamp'] for row in usable]
    splits=chronological_splits(timestamps,min_train_groups=max(20,len(set(timestamps))//3))
    if not splits:
        raise ValueError(f'Not enough chronological groups for {target}')
    x=_matrix(usable)
    y=[_target(row,target) for row in usable]
    if classification:
        if len(set(y))<2:
            raise ValueError(f'Need both outcome classes for {target}')
        splits=[(train,test) for train,test in splits if len({y[i] for i in train})==2]
        if not splits:
            raise ValueError(f'No leakage-safe split has both classes for {target}')
    comparisons={}
    fitted={}
    for name,factory in _factories(classification).items():
        actual=[]
        predicted=[]
        for train,test in splits:
            model=factory()
            model.fit([x[i] for i in train],[y[i] for i in train])
            if classification:
                pred=[float(v) for v in model.predict_proba([x[i] for i in test])[:,1]]
            else:
                pred=[float(v) for v in model.predict([x[i] for i in test])]
            actual.extend(y[i] for i in test)
            predicted.extend(pred)
        if classification:
            brier=mean((a-p)**2 for a,p in zip(actual,predicted))
            negatives=[(a,p) for a,p in zip(actual,predicted) if a==0]
            fpr=sum(p>=.5 for _,p in negatives)/len(negatives) if negatives else 0.
            metrics=dict(brier=brier,false_positive_rate=fpr,calibration_error=_calibration_error(actual,predicted),
                         objective=brier+.1*fpr,oos_samples=len(actual))
        else:
            errors=[a-p for a,p in zip(actual,predicted)]
            mae=mean(abs(e) for e in errors)
            false_positive=sum(a<=0<p for a,p in zip(actual,predicted))/len(actual) if target=='return_60m' else 0.
            metrics=dict(mae=mae,residual_std=pstdev(errors) if len(errors)>1 else 0.,false_positive_rate=false_positive,
                         objective=mae+.1*false_positive,oos_samples=len(actual))
        comparisons[name]=metrics
        full=factory()
        full.fit(x,y)
        fitted[name]=full
    selected=min(comparisons,key=lambda name:comparisons[name]['objective'])
    alternate=next(name for name in comparisons if name!=selected)
    return dict(selected_name=selected,selected=fitted[selected],alternate_name=alternate,alternate=fitted[alternate],
                metrics=comparisons,samples=len(usable))


def _reference(rows, limit=1500):
    step=max(1,len(rows)//limit)
    sample=rows[::step][:limit]
    columns=list(zip(*_matrix(rows)))
    medians=[]
    scales=[]
    for column in columns:
        values=[float(v) for v in column if v is not None and math.isfinite(float(v))]
        middle=median(values) if values else 0.
        scale=pstdev(values) if len(values)>1 else 1.
        medians.append(middle)
        scales.append(scale or 1.)
    vectors=[]
    for row in sample:
        vector=[medians[i] if value is None else float(value) for i,value in enumerate(_matrix([row])[0])]
        vectors.append(dict(values=vector,regime=row['regime'],labels=row['labels']))
    return dict(medians=medians,scales=scales,rows=vectors)


def train_candidate(rows, cfg, output_path, production_bundle=None):
    if len(rows)<cfg.model_min_observations:
        raise ValueError(f'Need {cfg.model_min_observations} labelled observations; have {len(rows)}')
    unique=sorted({row['timestamp'] for row in rows})
    if len(unique)<cfg.model_min_unique_timestamps:
        raise ValueError(f'Need {cfg.model_min_unique_timestamps} unique scan timestamps; have {len(unique)}')
    history_days=(unique[-1]-unique[0])/86400
    if history_days<cfg.model_min_history_days:
        raise ValueError(f'Need {cfg.model_min_history_days:g} history days; have {history_days:.2f}')
    targets={name:walk_forward_compare(rows,name) for name in REGRESSION_TARGETS+CLASSIFICATION_TARGETS}
    objectives=[target['metrics'][target['selected_name']]['objective'] for target in targets.values()]
    bundle=dict(schema=MODEL_SCHEMA,status='candidate',created_at=datetime.now(timezone.utc).timestamp(),
                version=datetime.now(timezone.utc).strftime('v3-%Y%m%dT%H%M%SZ'),feature_names=list(BASE_FEATURES),
                observations=len(rows),unique_timestamps=len(unique),history_days=history_days,
                regime_counts=dict(Counter(row['regime'] for row in rows)),targets=targets,
                reference=_reference(rows),aggregate_objective=mean(objectives),promotion_recommended=False)
    if production_bundle:
        old=production_bundle.get('aggregate_objective')
        bundle['promotion_recommended']=bool(old and bundle['aggregate_objective']<=old*.95)
        if not bundle['promotion_recommended']:
            bundle['status']='rejected_candidate'
    import joblib
    Path(output_path).parent.mkdir(parents=True,exist_ok=True)
    joblib.dump(bundle,output_path)
    return bundle


def load_bundle(path, require_approved=True):
    if not Path(path).exists():
        return None
    import joblib
    bundle=joblib.load(path)
    if bundle.get('schema')!=MODEL_SCHEMA or bundle.get('feature_names')!=list(BASE_FEATURES):
        raise ValueError('Incompatible model schema')
    if require_approved and bundle.get('status')!='approved':
        return None
    return bundle


def approve_candidate(candidate_path, production_path):
    """Explicit human-operated promotion; never called by training or live ranking."""
    import joblib
    bundle=load_bundle(candidate_path,require_approved=False)
    if not bundle or bundle.get('status')!='candidate':
        raise ValueError('No candidate model to approve')
    bundle['status']='approved'
    bundle['approved_at']=datetime.now(timezone.utc).timestamp()
    joblib.dump(bundle,production_path)
    return bundle
