"""Compare small asking-price models and expose advert-level validation diagnostics.

Displayed predictions always hold out the advert's own asking price. Model selection
uses the same folds, so comparison metrics are exploratory, not an independent audit.
"""

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import threading

import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import Ridge
from sklearn.model_selection import KFold, cross_val_predict
from sklearn.pipeline import make_pipeline

from .db import connect, now, get_state
from .vehicles import vehicle_group
from .description import FACT_LABELS, add_analysis, fact_value

logger = logging.getLogger(__name__)
MODEL_VERSION = 7
TRAINING_LOCK = threading.Lock()


def group(car):
    """Use an exact normalized make/model pair for comparable counts."""
    return vehicle_group(car["make"], car["model"])


def features(car, flexible=False):
    """Encode km and vehicle attributes, optionally allowing nonlinear depreciation."""
    data = {key: str(car.get(key) or "unknown").casefold()
            for key in ("make", "model", "fuel", "transmission", "seller_type")}
    data["make_model"] = group(car)
    analysis = car.get("analysis", {})
    for key in FACT_LABELS:
        value = fact_value(analysis, key)
        data["description:" + key] = "unknown" if value is None else "yes" if value else "no"
    for key, offset, scale in (("year", 2000, 10), ("mileage", 0, 100000), ("power", 0, 100)):
        value = car.get(key)
        data[key] = (value - offset) / scale if value is not None else 0.0
        data[key + "_missing"] = float(value is None)
    if flexible:
        data["variant"] = str(car.get("variant") or "unknown").casefold()
        # Fixed year knots allow the slope to change without a high-degree polynomial.
        for year in (2000, 2005, 2010, 2015, 2020):
            data[f"year_after_{year}"] = max(0, (car["year"] - year) / 10)
        data["year_for:" + group(car)] = data["year"]
        data["mileage_for:" + group(car)] = data["mileage"]
    return data


def metrics(actual, predicted):
    """Summarize absolute asking-price errors in SEK and percentage units."""
    return {"mape": round(float(np.mean(abs(predicted - actual) / actual) * 100), 1),
            "mae_sek": round(float(np.mean(abs(predicted - actual))))}


def diagnostics(cars, actual, predicted):
    """Build per-model and per-year diagnostics using held-out predictions only."""
    groups = defaultdict(list)
    for index, car in enumerate(cars):
        groups[group(car)].append(index)
    result = {}
    for key, indices in groups.items():
        years = []
        for year in sorted({int(cars[i]["year"]) for i in indices}):
            selected = [i for i in indices if int(cars[i]["year"]) == year]
            years.append({"year": year, "count": len(selected),
                          "median_asking": round(float(np.median(actual[selected]))),
                          "median_estimated": round(float(np.median(predicted[selected]))),
                          **metrics(actual[selected], predicted[selected])})
        result[key] = {"count": len(indices), **metrics(actual[indices], predicted[indices]), "years": years}
    return result


def train():
    """Serialize collector and LLM-triggered training so older snapshots cannot win."""
    with TRAINING_LOCK:
        _train()


def _train():
    """Select a model by identical five-fold validation and save its diagnostics.

    Require at least a 2% relative MAPE improvement over the linear baseline to
    select a more complex model. Price estimates remain out-of-fold regardless of
    selection. Training is CPU-only and models are refit after input/version changes.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=180)).isoformat(timespec="seconds")
    with connect() as db:
        rows = db.execute("""SELECT l.data,a.result FROM listings l
            LEFT JOIN description_analysis a ON a.listing_id=l.id
            WHERE l.last_seen>=? ORDER BY l.id""", (cutoff,)).fetchall()
    cars = [add_analysis(json.loads(row[0]), json.loads(row[1]) if row[1] else None) for row in rows]
    total = len(cars)
    cars = [c for c in cars if c.get("price") and c["price"] > 0 and c.get("make") and c.get("model")
            and c.get("year") is not None and c.get("mileage") is not None]
    signature = hashlib.sha256(json.dumps([MODEL_VERSION, [(c["id"], c["price"], features(c, True)) for c in cars]], sort_keys=True).encode()).hexdigest()
    if get_state("model", {}).get("signature") == signature:
        return
    counts = Counter(group(c) for c in cars)
    meta = {"version": MODEL_VERSION, "trained_at": now(), "sample_size": len(cars), "excluded_count": total - len(cars),
            "signature": signature, "minimum_sample": 100, "minimum_comparables": 20, "mileage_unit": "km",
            "features": ["make", "model", "year", "mileage", "fuel", "transmission", "power", "seller_type", *FACT_LABELS],
            "description_analyzed_count": sum(c["analysis"].get("status") == "ready" for c in cars),
            "training_window_days": 180, "status": "insufficient_data", "validation_mape": None,
            "validation_mae_sek": None, "candidates": [],
            "groups": {key: {"count": count, "mape": None, "mae_sek": None, "years": []}
                       for key, count in counts.items()}}
    predictions = []
    if len(cars) >= 100:
        actual = np.array([c["price"] for c in cars])
        y = np.log(actual)
        candidates = [
            ("linear", "Linear price model", False, Ridge(alpha=10, solver="lsqr")),
            ("depreciation", "Flexible depreciation model", True, Ridge(alpha=1, solver="lsqr")),
            ("forest", "Car comparison forest", True, RandomForestRegressor(
                n_estimators=100, min_samples_leaf=3, max_depth=16, random_state=42, n_jobs=1)),
        ]
        evaluated = []
        folds = list(KFold(5, shuffle=True, random_state=42).split(cars))
        for key, name, flexible, estimator in candidates:
            x = [features(c, flexible) for c in cars]
            pipeline = make_pipeline(DictVectorizer(), estimator)
            logs = cross_val_predict(pipeline, x, y, cv=folds, n_jobs=1)
            predicted = np.exp(np.clip(logs, 0, 25))
            score = float(np.mean(abs(predicted - actual) / actual))
            evaluated.append((score, key, name, flexible, pipeline, x, predicted))
            local_scores = {}
            for model_group in counts:
                indices = [i for i, car in enumerate(cars) if group(car) == model_group]
                local_scores[model_group] = metrics(actual[indices], predicted[indices])
            meta["candidates"].append({"id": key, "name": name, **metrics(actual, predicted), "groups": local_scores})
        selected = min(evaluated, key=lambda item: item[0])
        if selected[0] > evaluated[0][0] * 0.98:
            selected = evaluated[0]
        _, key, name, flexible, pipeline, x, predicted = selected
        error = metrics(actual, predicted)
        meta.update(status="ready", algorithm=key, name=name, validation_mape=error["mape"],
                    validation_mae_sek=error["mae_sek"], groups=diagnostics(cars, actual, predicted),
                    validation_method="5-fold advert-level validation; each estimate excludes its advert's price",
                    year_method={"linear": "One shared percentage change per model year.",
                                 "depreciation": "Depreciation slope can change by year, with make/model-specific year and mileage adjustments.",
                                 "forest": "Trees learn year ranges and interactions with mileage, fuel, variant, and other attributes."}[key])
        if flexible:
            meta["features"].append("variant")
        for car, estimate in zip(cars, predicted):
            if counts[group(car)] >= 20:
                predictions.append((car["id"], round(float(estimate)), counts[group(car)]))
        pipeline.fit(x, y)
        # Show model response to a one-year increase with other attributes fixed.
        newer = [{**car, "year": car["year"] + 1} for car in cars]
        change = np.exp(np.clip(pipeline.predict([features(c, flexible) for c in newer]) - pipeline.predict(x), -5, 5)) - 1
        meta["year_effect_percent"] = round(float(np.median(change) * 100), 1)
        if key != "forest":
            meta["coefficients"] = dict(zip(pipeline[0].get_feature_names_out(), pipeline[1].coef_.tolist()))
            meta["intercept"] = float(pipeline[1].intercept_)
    with connect() as db:
        db.execute("DELETE FROM predictions")
        db.executemany("INSERT INTO predictions VALUES (?,?,?)", predictions)
        db.execute("INSERT OR REPLACE INTO state VALUES ('model',?)", (json.dumps(meta),))
    logger.info("Model %s: %s usable adverts; MAPE %s", meta.get("name", meta["status"]), len(cars), meta["validation_mape"])
