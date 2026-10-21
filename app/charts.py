"""Prepare price history coordinates for server-rendered SVG charts."""

from datetime import datetime
from math import isfinite
from zoneinfo import ZoneInfo


def price_history_chart(history):
    """Return dated points, axis ticks and a step path, or None for no prices.

    Horizontal spacing reflects elapsed time. Steps hold each observed price
    until the next observation; they do not imply continuous monitoring.
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
            "time": local.strftime("%H:%M:%S"),
            "price": f"{price:,.0f}".replace(",", " "),
        })
    path = f'M {points[0]["x"]} {points[0]["y"]}'
    for point in points[1:]:
        path += f' H {point["x"]} V {point["y"]}'
    ticks = [{"y": 204 - tick * 45,
              "label": f"{low + (high - low) * tick / 4:,.0f}".replace(",", " ")}
             for tick in range(5)]
    return {"points": points, "path": path, "ticks": ticks,
            "ends": [points[0]] if start == end else [points[0], points[-1]]}
