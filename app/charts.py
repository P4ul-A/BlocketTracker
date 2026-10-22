"""Prepare price history coordinates for server-rendered SVG charts."""

from datetime import datetime
from math import isfinite
from zoneinfo import ZoneInfo


def price_history_chart(history, last_seen=None):
    """Return dated points, axis ticks and a smooth path, or None for no prices.

    Horizontal spacing reflects elapsed time. Cubic curves join observations
    without overshooting their prices; intermediate prices are illustrative.
    History stores changes only, so last_seen extends an unchanged price to its
    latest confirmed sighting. Never extend beyond a missing-price observation.
    """
    observations = []
    for change in history:
        price = change["price"]
        if price is None or not isfinite(price):
            continue
        observed = datetime.fromisoformat(change["observed_at"])
        observations.append((observed, price))
    observations.sort(key=lambda observation: observation[0])
    if not observations:
        return None

    if last_seen:
        seen = datetime.fromisoformat(last_seen)
        last_change = max(datetime.fromisoformat(change["observed_at"]) for change in history)
        observed, price = observations[-1]
        if seen > observed and observed == last_change:
            observations.append((seen, price))

    start, end = observations[0][0].timestamp(), observations[-1][0].timestamp()
    prices = [price for _, price in observations]
    minimum, maximum = min(prices), max(prices)
    padding = max((maximum - minimum) * .15, maximum * .05, 1)
    low, high = max(0, minimum - padding), maximum + padding
    points = []
    for observed, price in observations:
        local = observed.astimezone(ZoneInfo("Europe/Stockholm"))
        points.append({
            "x": 254 if start == end else 78 + (observed.timestamp() - start) / (end - start) * 352,
            "y": 204 - (price - low) / (high - low) * 180,
            "date": local.strftime("%d/%m/%Y"),
            "time": local.strftime("%H:%M"),
            "price": f"{price:,.0f}".replace(",", " "),
        })
    path = f'M {points[0]["x"]} {points[0]["y"]}'
    for previous, point in zip(points, points[1:]):
        middle = (previous["x"] + point["x"]) / 2
        path += f' C {middle} {previous["y"]}, {middle} {point["y"]}, {point["x"]} {point["y"]}'
    ticks = [{"y": 204 - tick * 45,
              "label": f"{low + (high - low) * tick / 4:,.0f}".replace(",", " ")}
             for tick in range(5)]
    return {"points": points, "path": path, "ticks": ticks,
            "ends": [points[0]] if start == end else [points[0], points[-1]]}
