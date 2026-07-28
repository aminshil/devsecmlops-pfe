"""
MASTER COMPARISON — every model x every threshold x with/without feedback.
One run, one table. Honest, no test-set leakage:
  - Models trained on seed-42; feedback drawn from seed-42 only.
  - All evaluation on seed-123 test set.
  - Per-class thresholds tuned on a validation split of the test set,
    reported on the held-out half (so per-class isn't overfit).
XGBoost-only metrics (the safety-net IsolationForest is unsupervised and
unaffected by feedback; it's a separate live OR-gate).
"""
import gc, json, time, sys
from datetime import datetime, timezone
import numpy as np, pandas as pd
from sklearn.metrics import f1_score, precision_score, recall_score
from sklearn.preprocessing import LabelEncoder
from xgboost import XGBClassifier

sys.path.insert(0,"ml-model")
from preprocess import apply_zscore, add_window_column, add_rolling_features, rolling_feature_names

FEAT=["cpu","ram","network","disk_io","disk_usage","load_avg"]
ROLL=rolling_feature_names()
CAUSES=["cpu_spike","disk_saturation","memory_leak","network_flood","silent_failure"]
WEAK=["memory_leak","cascade"]
FB_SIZE=50000          # feedback volume for the "with feedback" rows
FB_WEIGHT=5.0
def log(m): print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {m}",flush=True)

log("Loading seed-42 training data + rolling features...")
bl=json.load(open("models/telecom_baselines_v2.json"))
df=pd.read_csv("data/telecom_fleet_v2_labeled.csv")
df=add_window_column(df); df=add_rolling_features(df)
anom=df[df.label==1]; norm=df[df.label==0].sample(n=2_000_000,random_state=42)
base=pd.concat([anom,norm],ignore_index=True).sample(frac=1,random_state=42).reset_index(drop=True)
base["anomaly_type"]=base["anomaly_type"].fillna("normal").replace("","normal").replace("cascade","normal")
log(f"  base {len(base):,} rows")

# feedback pool (seed-42), weak-weighted
_keep=FEAT+ROLL+["anomaly_type","machine","window"]
_keep=[c for c in _keep if c in df.columns]
if "type" in df.columns: _keep.append("type")
pool=df[df.label==1][_keep].copy()
pool["anomaly_type"]=pool["anomaly_type"].fillna("normal").replace("","normal")
wk=pool[pool.anomaly_type.isin(WEAK)]; ot=pool[~pool.anomaly_type.isin(WEAK)]

# feature matrices for base (6 and 15)
Xb6=apply_zscore(base,bl,FEAT).to_numpy().astype(np.float32)
Xb15=np.column_stack([Xb6, base[ROLL].to_numpy().astype(np.float32)])
yb=base["anomaly_type"].values
del df; gc.collect()

# test set (seed-123)
log("Loading seed-123 test set + rolling...")
dt=pd.read_csv("data/telecom_fleet_v2_test.csv")
dt=add_window_column(dt); dt=add_rolling_features(dt)
Xt6=apply_zscore(dt,bl,FEAT).to_numpy().astype(np.float32)
Xt15=np.column_stack([Xt6, dt[ROLL].to_numpy().astype(np.float32)])
ytb=dt.label.values
ytc=dt["anomaly_type"].fillna("normal").replace("","normal").values
log(f"  test {len(Xt6):,} rows")
del dt; gc.collect()

# validation/holdout split of the test set (for per-class threshold tuning)
rng=np.random.RandomState(42); idx=rng.permutation(len(Xt6)); h=len(idx)//2
val,hold=idx[:h],idx[h:]

def make_fb(n):
    nw=int(n*0.7); no=n-nw
    w=wk.sample(n=nw,replace=nw>len(wk),random_state=42)
    o=ot.sample(n=no,replace=no>len(ot),random_state=42)
    fb=pd.concat([w,o],ignore_index=True)
    X6=apply_zscore(fb,bl,FEAT).to_numpy().astype(np.float32)
    X15=np.column_stack([X6, fb[ROLL].to_numpy().astype(np.float32)])
    return X6,X15,fb["anomaly_type"].values

def train(X,y,w=None):
    m=XGBClassifier(n_estimators=150,max_depth=6,learning_rate=0.1,random_state=42,
                    n_jobs=-1,verbosity=0,use_label_encoder=False)
    m.fit(X,y,sample_weight=w); return m

def probs(model,le,X): return model.predict_proba(X)

def flag_single(pr,le,thr):
    ni=list(le.classes_).index("normal")
    return (pr[:,ni]<thr).astype(int)

def tune_perclass(pr,le,yv):
    cls=list(le.classes_); ni=cls.index("normal")
    grid=np.round(np.arange(0.05,0.96,0.05),2)
    thr={c:0.5 for c in CAUSES if c in cls}
    def flag(pr,tm):
        f=np.zeros(len(pr),dtype=int)
        for c in tm:
            f|=(pr[:,cls.index(c)]>=tm[c]).astype(int)
        return f
    for _ in range(2):
        for c in list(thr):
            best_t,best=thr[c],-1
            for tv in grid:
                tr=dict(thr); tr[c]=tv
                s=f1_score(yv,flag(pr,tr))
                if s>best: best,best_t=s,tv
            thr[c]=best_t
    return thr,flag

def metr(pred,yb_,yc_):
    m={"F1":f1_score(yb_,pred),"Prec":precision_score(yb_,pred,zero_division=0),"Rec":recall_score(yb_,pred)}
    for c in ["memory_leak","cascade"]:
        mk=(yc_==c); m[c]=pred[mk].mean() if mk.sum() else 0.0
    return m

rows=[]
def evaluate(tag, model, le, Xt):
    pr=probs(model,le,Xt)
    # single thresholds on full test
    for thr in [0.50,0.85]:
        pred=flag_single(pr,le,thr)
        m=metr(pred,ytb,ytc); m["config"]=f"{tag} | single@{thr}"; rows.append(m)
    # per-class: tune on val half, report on holdout half
    prv=pr[val]; prh=pr[hold]
    thr,flag=tune_perclass(prv,le,ytb[val])
    predh=flag(prh,thr)
    m=metr(predh,ytb[hold],ytc[hold]); m["config"]=f"{tag} | per-class(F1)"; rows.append(m)

# ---- v3 baseline (6 feat) ----
log("\n[v3] training baseline...")
le3=LabelEncoder(); y3=le3.fit_transform(yb)
m=train(Xb6,y3); evaluate("v3 base", m, le3, Xt6); del m; gc.collect()

# ---- v3 + feedback ----
log("[v3] training + feedback...")
f6,_,fy=make_fb(FB_SIZE)
Xc=np.vstack([Xb6,f6]); yc=list(yb)+list(fy); w=np.concatenate([np.ones(len(Xb6)),np.full(len(f6),FB_WEIGHT)])
lec=LabelEncoder(); yce=lec.fit_transform(yc)
m=train(Xc,yce,w); evaluate("v3 +feedback", m, lec, Xt6); del m,Xc,f6; gc.collect()

# ---- v4 baseline (15 feat) ----
log("[v4] training baseline...")
le4=LabelEncoder(); y4=le4.fit_transform(yb)
m=train(Xb15,y4); evaluate("v4 base", m, le4, Xt15); del m; gc.collect()

# ---- v4 + feedback ----
log("[v4] training + feedback...")
_,f15,fy=make_fb(FB_SIZE)
Xc=np.vstack([Xb15,f15]); yc=list(yb)+list(fy); w=np.concatenate([np.ones(len(Xb15)),np.full(len(f15),FB_WEIGHT)])
lec=LabelEncoder(); yce=lec.fit_transform(yc)
m=train(Xc,yce,w); evaluate("v4 +feedback", m, lec, Xt15); del m,Xc,f15; gc.collect()

# ---- print master table ----
print("\n"+"="*98)
print("MASTER COMPARISON — every model x threshold x feedback (XGBoost-only, seed-123 test)")
print("="*98)
print(f"{'config':<28}{'F1':>8}{'Prec':>8}{'Rec':>8}{'mem_rec':>9}{'casc_rec':>10}")
print("-"*98)
best=max(rows,key=lambda r:r["F1"])
for r in rows:
    star=" <== BEST F1" if r is best else ""
    print(f"{r['config']:<28}{r['F1']:>8.3f}{r['Prec']:>8.3f}{r['Rec']:>8.3f}{r['memory_leak']:>9.3f}{r['cascade']:>10.3f}{star}")
print("="*98)
print(f"\nBEST F1: {best['config']}  ->  F1={best['F1']:.3f}  Prec={best['Prec']:.3f}  Rec={best['Rec']:.3f}")
