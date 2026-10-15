"""
services/prediction_service.py

Connects the trained model to the web application.

train_model.py did the learning and saved two files:
    models/best_model.joblib   the trained model itself
    models/model_info.json     which model won, its features, its scores

This module loads those, builds the same features from live sales data,
and predicts forward. It must build the features exactly as training did,
in the same order, or the model receives nonsense.

Forecasting several days ahead is done one day at a time, feeding each
prediction back in as the previous day's value. That is how a model
trained on "yesterday's sales" can look a week ahead at all.
"""

import json
import os
from datetime import date, datetime, timedelta

import joblib
import pandas as pd

from models import db, Sale, Medicine, Forecast

MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "models")
MODEL_PATH = os.path.join(MODEL_DIR, "best_model.joblib")
INFO_PATH = os.path.join(MODEL_DIR, "model_info.json")

MAX_DAYS = 30            # beyond this the error compounds too far to be useful

# The model and its metadata are loaded once and kept, because reading a
# joblib file on every request would make the page noticeably slower.
_model = None
_info = None


def is_ready():
    """True when train_model.py has been run and its output is present."""
    return os.path.exists(MODEL_PATH) and os.path.exists(INFO_PATH)


def load():
    """Load the model and its metadata, caching both."""
    global _model, _info
    if _model is None:
        _model = joblib.load(MODEL_PATH)
        with open(INFO_PATH) as f:
            _info = json.load(f)
    return _model, _info


def model_info():
    """The metrics shown on the page, so the user sees how good the model is."""
    if not is_ready():
        return None
    _, info = load()

    best = next((m for m in info["metrics"]
                 if m["model"] == info["best_model"]), {})
    return {
        "name": info["best_model"],
        "mae": best.get("MAE"),
        "rmse": best.get("RMSE"),
        "r2": best.get("R2"),
        "all_metrics": info["metrics"],
        "trained_on_rows": info.get("trained_on_rows"),
        "last_date": info.get("last_date"),
        "feature_count": len(info["features"]),
    }


def _history(medicine_id):
    """
    Daily sales for one medicine, with missing days filled as zero.

    The gaps matter: if a medicine sold nothing on a Sunday, that day is
    absent from the sales table. Leaving it out would shift every lag by
    one and quietly corrupt the features.
    """
    rows = (db.session.query(Sale.sale_date,
                             db.func.sum(Sale.quantity))
            .filter(Sale.medicine_id == medicine_id)
            .group_by(Sale.sale_date)
            .order_by(Sale.sale_date).all())

    if not rows:
        return None

    series = pd.Series({r[0]: float(r[1]) for r in rows})
    series.index = pd.to_datetime(series.index)
    full = pd.date_range(series.index.min(), series.index.max(), freq="D")
    return series.reindex(full).fillna(0)


def forecast(medicine, days=7):
    """
    Predict demand for the next `days` days.

    Returns (rows, error). Each row is a dict with the date, the predicted
    quantity and the weekday, ready for the table and the chart.
    """
    if not is_ready():
        return None, ("No trained model found. Run train_model.py first "
                      "to create models/best_model.joblib.")

    model, info = load()
    series = _history(medicine.medicine_id)

    if series is None:
        return None, f"{medicine.name} has no sales history to learn from."
    if len(series) < 30:
        return None, (f"{medicine.name} has only {len(series)} days of "
                      "history. At least 30 are needed for the 28-day "
                      "rolling average.")

    base_features = info["base_features"]
    med_columns = info["medicine_columns"]
    all_features = info["features"]
    my_column = f"med_{medicine.medicine_code}"

    if my_column not in med_columns:
        return None, (f"{medicine.name} was added after the model was "
                      "trained, so the model does not know it. Re-run "
                      "train_model.py to include it.")

    rows = []
    working = series.copy()

    for _ in range(days):
        next_date = working.index[-1] + pd.Timedelta(days=1)

        # these must match train_model.py exactly, including the names
        row = {
            "dayofweek": next_date.dayofweek,
            "month": next_date.month,
            "dayofmonth": next_date.day,
            "weekofyear": int(next_date.isocalendar().week),
            "is_sunday": 1 if next_date.dayofweek == 6 else 0,
            "lag_1": working.iloc[-1],
            "lag_7": working.iloc[-7],
            "lag_14": working.iloc[-14],
            "roll_mean_7": working.iloc[-7:].mean(),
            "roll_mean_28": working.iloc[-28:].mean(),
        }
        for column in med_columns:
            row[column] = 1 if column == my_column else 0

        frame = pd.DataFrame([row])[all_features]
        predicted = max(float(model.predict(frame)[0]), 0.0)

        # feed the prediction back in, so tomorrow's lag_1 is today's answer
        working.loc[next_date] = predicted

        rows.append({
            "date": next_date.date(),
            "weekday": next_date.strftime("%A"),
            "quantity": round(predicted, 1),
        })

    return rows, None


def recent_actuals(medicine, days=21):
    """The last few weeks of real sales, drawn behind the forecast."""
    series = _history(medicine.medicine_id)
    if series is None:
        return []
    tail = series.tail(days)
    return [{"date": d.date(), "quantity": float(q)} for d, q in tail.items()]


def summarise(rows, medicine):
    """
    Turn the forecast into the numbers a pharmacist acts on.

    Predicting demand is only useful if it answers "do I need to order?",
    so the stock on hand is compared against the total predicted.
    """
    total = sum(r["quantity"] for r in rows)
    stock = medicine.current_stock
    shortfall = max(0, round(total - stock, 1))

    # how many days until stock runs out at the predicted rate
    running = stock
    days_cover = None
    for i, r in enumerate(rows, start=1):
        running -= r["quantity"]
        if running < 0:
            days_cover = i
            break

    return {
        "total": round(total, 1),
        "daily_average": round(total / len(rows), 1) if rows else 0,
        "peak": max(rows, key=lambda r: r["quantity"]) if rows else None,
        "stock": stock,
        "shortfall": shortfall,
        "days_cover": days_cover,
        "reorder_level": medicine.reorder_level,
    }


def save_forecast(medicine, rows, user_id):
    """
    Store the prediction with a timestamp, as the project requires.

    Each run is kept rather than overwriting the last, so the History page
    can show what was predicted, when, and by which model. That is the
    audit trail: you can later compare a forecast with what actually sold.
    """
    _, info = load()
    saved = []

    # One timestamp for the whole run, taken once. Letting each row default
    # to its own utcnow() would give them timestamps microseconds apart,
    # and the History page would then read one run as several.
    run_timestamp = datetime.utcnow()

    for r in rows:
        forecast_row = Forecast(medicine_id=medicine.medicine_id,
                                forecast_date=r["date"],
                                predicted_quantity=r["quantity"],
                                model_name=info["best_model"],
                                created_by=user_id,
                                created_at=run_timestamp)
        db.session.add(forecast_row)
        saved.append(forecast_row)

    db.session.commit()
    return saved


def forecastable_medicines():
    """Medicines the model was trained on, so it can actually predict them."""
    if not is_ready():
        return Medicine.query.order_by(Medicine.name).all()

    _, info = load()
    known = {c.replace("med_", "") for c in info["medicine_columns"]}
    return [m for m in Medicine.query.order_by(Medicine.name).all()
            if m.medicine_code in known]