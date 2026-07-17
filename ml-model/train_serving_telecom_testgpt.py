from pathlib import Path
import sys

import pandas as pd
import numpy as np
import joblib

import mlflow
import mlflow.sklearn

from sklearn.ensemble import IsolationForest
from sklearn.metrics import (
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score
)


from sklearn.metrics import precision_recall_curve


sys.path.insert(
    0,
    str(Path(__file__).parent)
)


from preprocess_testgpt import (
    add_features,
    build_baselines,
    apply_zscore
)



ROOT=Path(__file__).resolve().parent.parent

DATA=ROOT/"data"/"telecom_fleet_v2.csv"

MODEL_DIR=ROOT/"models"



mlflow.set_tracking_uri(
    "http://localhost:5001"
)

mlflow.set_experiment(
    "telecom-testgpt-experiment"
)


with mlflow.start_run():


    print("Loading dataset")

    df=pd.read_csv(DATA)


    print(df.shape)


    df=add_features(df)



    META={
        "timestamp",
        "machine",
        "type",
        "label",
        "anomaly_type",
        "hour",
        "day",
        "window"
    }


    features=[
        c for c in df.columns
        if c not in META
    ]


    print(
        "Features:",
        len(features)
    )



    # time split

    df=df.sort_values(
        "timestamp"
    )


    split=int(
        len(df)*0.7
    )


    train=df.iloc[:split]
    test=df.iloc[split:]



    baselines=build_baselines(
        train,
        features
    )


    X_train=apply_zscore(
        train,
        baselines,
        features
    )


    X_test=apply_zscore(
        test,
        baselines,
        features
    )



    print(
        "Training Isolation Forest"
    )


    model=IsolationForest(
        n_estimators=300,
        contamination=0.02,
        random_state=42,
        n_jobs=-1
    )


    model.fit(
        X_train
    )


    scores=-model.score_samples(
        X_test
    )


    y=test.label.values



    best=0
    best_threshold=0


    for t in np.linspace(
        scores.min(),
        scores.max(),
        200
    ):

        pred=(scores>=t).astype(int)

        f=f1_score(
            y,
            pred
        )

        if f>best:
            best=f
            best_threshold=t



    pred=(
        scores>=best_threshold
    ).astype(int)



    print()
    print(
        "RESULTS"
    )

    print(
        "F1:",
        best
    )

    print(
        "Precision:",
        precision_score(
            y,pred
        )
    )

    print(
        "Recall:",
        recall_score(
            y,pred
        )
    )

    print(
        "ROC:",
        roc_auc_score(
            y,
            scores
        )
    )



    print()
    print(
        "By anomaly type"
    )


    tmp=test.copy()

    tmp["pred"]=pred


    for a,g in tmp[
        tmp.label==1
    ].groupby(
        "anomaly_type"
    ):

        print(
            a,
            f1_score(
                g.label,
                g.pred
            )
        )



    MODEL_DIR.mkdir(
        exist_ok=True
    )


    joblib.dump(
        model,
        MODEL_DIR/
        "telecom_testgpt_model.pkl"
    )


    mlflow.log_param(
        "features",
        len(features)
    )

    mlflow.log_param(
        "contamination",
        0.02
    )


    mlflow.log_metric(
        "f1",
        best
    )

    mlflow.log_metric(
        "roc_auc",
        roc_auc_score(
            y,scores
        )
    )


    print(
        "Saved model"
    )
