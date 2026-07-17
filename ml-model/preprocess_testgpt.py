import numpy as np
import pandas as pd


WINDOWS = ("night", "morning", "afternoon", "evening")
STD_FLOOR = 1e-8


def hour_to_window(hour):
    if hour < 6:
        return "night"
    if hour < 12:
        return "morning"
    if hour < 18:
        return "afternoon"
    return "evening"


def add_features(df):

    df = df.copy()

    ts = pd.to_datetime(df["timestamp"])

    df["hour"] = ts.dt.hour
    df["day"] = ts.dt.dayofweek

    df["window"] = df["hour"].apply(hour_to_window)

    # cyclic time
    df["hour_sin"] = np.sin(2*np.pi*df.hour/24)
    df["hour_cos"] = np.cos(2*np.pi*df.hour/24)

    df["day_sin"] = np.sin(2*np.pi*df.day/7)
    df["day_cos"] = np.cos(2*np.pi*df.day/7)


    df = df.sort_values(
        ["machine","timestamp"]
    )


    # rate changes
    for c in [
        "cpu",
        "ram",
        "network",
        "response_time"
    ]:
        df[f"{c}_delta"] = (
            df.groupby("machine")[c]
            .diff()
            .fillna(0)
        )


    # rolling behaviour
    for c in [
        "cpu",
        "ram",
        "network",
        "response_time"
    ]:

        g = df.groupby("machine")[c]

        df[f"{c}_roll_mean_30"] = (
            g.rolling(30)
            .mean()
            .reset_index(level=0,drop=True)
            .fillna(df[c])
        )

        df[f"{c}_roll_std_30"] = (
            g.rolling(30)
            .std()
            .reset_index(level=0,drop=True)
            .fillna(0)
        )


    return df



def build_baselines(df, features):

    baselines={}

    for (machine,window),g in df.groupby(
        ["machine","window"]
    ):

        key=f"{machine}|{window}"

        baselines[key]={}

        for c in features:

            baselines[key][c]=[
                float(g[c].mean()),
                max(
                    float(g[c].std()),
                    STD_FLOOR
                )
            ]


    baselines["__global__"]={}

    for c in features:

        baselines["__global__"][c]=[
            float(df[c].mean()),
            max(float(df[c].std()),1)
        ]


    return baselines



def apply_zscore(df,baselines,features):

    out=df[features].copy()

    for (machine,window),idx in df.groupby(
        ["machine","window"]
    ).groups.items():

        stats=baselines.get(
            f"{machine}|{window}",
            baselines["__global__"]
        )

        for c in features:

            mean,std=stats[c]

            out.loc[idx,c]=(
                df.loc[idx,c]-mean
            )/std


    return out
