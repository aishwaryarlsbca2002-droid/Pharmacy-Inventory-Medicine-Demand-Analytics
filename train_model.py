"""
train_model.py  -  Trains the demand forecasting model.

Reads daily sales from the database, builds features, compares three
regression models, and saves the best one for Flask to use.

Run:  python train_model.py

Outputs (all in models/):
    best_model.joblib        the trained model Flask will load
    model_info.json          which model won, its features and metrics
    model_comparison.csv     the metrics table for your report
    model_comparison.png     bar chart of MAE and RMSE
    actual_vs_predicted.png  how close the predictions are
    feature_importance.png   which features the model relies on
"""

import os
import json
import joblib
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")                    # save charts without opening a window
import matplotlib.pyplot as plt

from sklearn.linear_model import LinearRegression
from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

from app import app
from models import db

MODEL_DIR = "models"
os.makedirs(MODEL_DIR, exist_ok=True)

TEST_DAYS = 180          # the most recent 180 days are held back for testing

BASE_FEATURES = [
    "dayofweek", "month", "dayofmonth", "weekofyear", "is_sunday",
    "lag_1", "lag_7", "lag_14", "roll_mean_7", "roll_mean_28",
]


# ---------------------------------------------------------------- 1. LOAD
def load_daily_sales():
    """One row per medicine per day, straight from the sales table."""
    with app.app_context():
        df = pd.read_sql("""
            SELECT s.sale_date, m.medicine_id, m.medicine_code, m.name,
                   SUM(s.quantity) AS quantity
            FROM sales s
            JOIN medicines m ON m.medicine_id = s.medicine_id
            GROUP BY s.sale_date, m.medicine_id, m.medicine_code, m.name
            ORDER BY m.medicine_id, s.sale_date
        """, db.engine, parse_dates=["sale_date"])

    df = df.rename(columns={"sale_date": "date"})

    # If a medicine had no sales on some day, that day is missing from the
    # table. We fill those gaps with 0 so the lag features line up correctly.
    full_range = pd.date_range(df.date.min(), df.date.max(), freq="D")
    filled = []
    for (mid, code, name), g in df.groupby(["medicine_id", "medicine_code", "name"]):
        g = (g.set_index("date").reindex(full_range)
              .rename_axis("date").reset_index())
        g["medicine_id"], g["medicine_code"], g["name"] = mid, code, name
        g["quantity"] = g["quantity"].fillna(0)
        filled.append(g)
    return pd.concat(filled, ignore_index=True)


# ------------------------------------------------------------ 2. FEATURES
def add_features(df):
    """
    Build the inputs the model learns from.

    Calendar features explain the weekly and yearly pattern.
    Lag and rolling features carry the recent demand level.
    Every lag uses shift(), so no future information leaks into the past.
    """
    out = []
    for mid, g in df.groupby("medicine_id"):
        g = g.sort_values("date").copy()

        g["dayofweek"] = g.date.dt.dayofweek
        g["month"] = g.date.dt.month
        g["dayofmonth"] = g.date.dt.day
        g["weekofyear"] = g.date.dt.isocalendar().week.astype(int)
        g["is_sunday"] = (g.dayofweek == 6).astype(int)

        g["lag_1"] = g.quantity.shift(1)
        g["lag_7"] = g.quantity.shift(7)
        g["lag_14"] = g.quantity.shift(14)
        g["roll_mean_7"] = g.quantity.shift(1).rolling(7).mean()
        g["roll_mean_28"] = g.quantity.shift(1).rolling(28).mean()

        out.append(g)

    feat = pd.concat(out, ignore_index=True).dropna().reset_index(drop=True)

    # encode the medicine as 0/1 columns (one-hot encoding)
    dummies = pd.get_dummies(feat.medicine_code, prefix="med", dtype=int)
    feat = pd.concat([feat, dummies], axis=1)
    return feat, list(dummies.columns)


# --------------------------------------------------------------- 3. TRAIN
def main():
    print("Loading sales from the database ...")
    daily = load_daily_sales()
    print(f"  {len(daily)} daily rows, "
          f"{daily.date.min().date()} to {daily.date.max().date()}")

    feat, med_cols = add_features(daily)
    features = BASE_FEATURES + med_cols
    print(f"  {len(feat)} rows usable after building lag features")

    # time-based split: we must never train on days after the test period
    cutoff = feat.date.max() - pd.Timedelta(days=TEST_DAYS)
    train = feat[feat.date <= cutoff]
    test = feat[feat.date > cutoff]
    X_train, y_train = train[features], train.quantity
    X_test, y_test = test[features], test.quantity
    print(f"  Train: {len(train)} rows (to {cutoff.date()})   Test: {len(test)} rows")

    candidates = {
        "Linear Regression": Pipeline([("scale", StandardScaler()),
                                       ("model", LinearRegression())]),
        "Random Forest": RandomForestRegressor(n_estimators=200, max_depth=14,
                                               min_samples_leaf=2,
                                               random_state=42, n_jobs=-1),
        "Gradient Boosting": GradientBoostingRegressor(n_estimators=250,
                                                       learning_rate=0.05,
                                                       max_depth=3, random_state=42),
    }

    print("\nTraining models ...")
    results, preds = [], {}
    for name, model in candidates.items():
        model.fit(X_train, y_train)
        p = model.predict(X_test)
        preds[name] = p
        results.append({
            "model": name,
            "MAE": round(mean_absolute_error(y_test, p), 3),
            "RMSE": round(float(np.sqrt(mean_squared_error(y_test, p))), 3),
            "R2": round(r2_score(y_test, p), 3),
        })
        print(f"  {name} done")

    table = pd.DataFrame(results).sort_values("RMSE").reset_index(drop=True)
    table.to_csv(os.path.join(MODEL_DIR, "model_comparison.csv"), index=False)

    print("\n=== Model comparison ===")
    print("(lower MAE and RMSE are better, higher R2 is better)")
    print(table.to_string(index=False))

    best_name = table.loc[0, "model"]
    best_model = candidates[best_name]
    print(f"\nBest model: {best_name}")

    # ---- charts ----
    make_charts(table, test, preds[best_name], best_name, best_model, features)

    # ---- retrain on all data, then save ----
    print("\nRetraining the best model on all data ...")
    best_model.fit(feat[features], feat.quantity)
    joblib.dump(best_model, os.path.join(MODEL_DIR, "best_model.joblib"))

    with open(os.path.join(MODEL_DIR, "model_info.json"), "w") as f:
        json.dump({
            "best_model": best_name,
            "features": features,
            "base_features": BASE_FEATURES,
            "medicine_columns": med_cols,
            "test_days": TEST_DAYS,
            "metrics": table.to_dict(orient="records"),
            "trained_on_rows": len(feat),
            "last_date": str(feat.date.max().date()),
        }, f, indent=2)

    print(f"\nSaved everything to {MODEL_DIR}/")
    print("Files: best_model.joblib, model_info.json, model_comparison.csv,")
    print("       model_comparison.png, actual_vs_predicted.png, feature_importance.png")


def make_charts(table, test, best_pred, best_name, best_model, features):
    # 1. model comparison
    fig, ax = plt.subplots(figsize=(8, 4))
    table.plot(x="model", y=["MAE", "RMSE"], kind="bar", ax=ax)
    ax.set_title("Model comparison (lower is better)")
    ax.set_xlabel("")
    plt.xticks(rotation=15, ha="right")
    plt.tight_layout()
    plt.savefig(os.path.join(MODEL_DIR, "model_comparison.png"), dpi=110)
    plt.close()

    # 2. actual vs predicted for the busiest medicine, last 90 days
    t = test.copy()
    t["pred"] = best_pred
    busiest = t.groupby("name").quantity.sum().idxmax()
    sub = t[t.name == busiest].sort_values("date").tail(90)

    plt.figure(figsize=(11, 4))
    plt.plot(sub.date, sub.quantity, label="Actual", alpha=0.8)
    plt.plot(sub.date, sub.pred, label="Predicted", linewidth=2)
    plt.title(f"{busiest}: actual vs predicted ({best_name}, last 90 test days)")
    plt.ylabel("Units")
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(MODEL_DIR, "actual_vs_predicted.png"), dpi=110)
    plt.close()

    # 3. feature importance, only for tree models
    if hasattr(best_model, "feature_importances_"):
        imp = (pd.Series(best_model.feature_importances_, index=features)
                 .sort_values(ascending=False).head(10))
        plt.figure(figsize=(8, 4))
        imp.sort_values().plot.barh(color="seagreen")
        plt.title(f"Top 10 features used by {best_name}")
        plt.tight_layout()
        plt.savefig(os.path.join(MODEL_DIR, "feature_importance.png"), dpi=110)
        plt.close()
        print("\nTop 5 features by importance:")
        for name, val in imp.head(5).items():
            print(f"  {name:16} {val:.3f}")


if __name__ == "__main__":
    main()