"""Built-in demo dataset so anyone can try DataSense AI in one click.
Synthetic retail sales with seasonality, a growth trend and a few injected
anomalies (so the anomaly detector has something real to find)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def generate_sales_demo(seed: int = 7, n: int = 4000) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    days = pd.date_range("2023-01-01", "2024-12-31", freq="D")
    dates = rng.choice(days, n)
    dates = pd.to_datetime(dates)
    region = rng.choice(["North", "South", "East", "West"], n, p=[0.3, 0.25, 0.25, 0.2])
    category = rng.choice(["Electronics", "Furniture", "Office Supplies", "Apparel"], n, p=[0.3, 0.2, 0.3, 0.2])
    segment = rng.choice(["Consumer", "Corporate", "Small Business"], n, p=[0.5, 0.3, 0.2])
    base_price = {"Electronics": 320, "Furniture": 210, "Office Supplies": 35, "Apparel": 60}
    unit_price = np.array([base_price[c] for c in category]) * rng.lognormal(0, 0.18, n)
    quantity = rng.integers(1, 8, n)
    discount = rng.choice([0, 0, 0, 0.05, 0.1, 0.2], n)
    day_index = (dates - dates.min()).days.to_numpy()
    seasonal = 1 + 0.25 * np.sin(2 * np.pi * day_index / 365) + 0.0006 * day_index
    revenue = unit_price * quantity * (1 - discount) * seasonal
    cost_ratio = rng.normal(0.62, 0.05, n)
    profit = revenue * (1 - cost_ratio)

    df = pd.DataFrame({
        "order_id": [f"ORD-{100000 + i}" for i in range(n)],
        "order_date": dates.strftime("%Y-%m-%d"),
        "region": region, "category": category, "customer_segment": segment,
        "unit_price": unit_price.round(2), "quantity": quantity, "discount": discount,
        "revenue": revenue.round(2), "profit": profit.round(2),
    })
    # injected anomalies: a flash-sale spike day, a data-entry error, a loss-making bulk order
    spike = df["order_date"] == "2024-06-18"
    df.loc[spike, "revenue"] = (df.loc[spike, "revenue"] * 3.2).round(2)
    df.loc[17, ["quantity", "revenue", "profit"]] = [900, 184000.0, 41000.0]
    df.loc[311, ["revenue", "profit"]] = [5200.0, -7800.0]
    # a little missing data, as in real life
    df.loc[rng.choice(n, 70, replace=False), "customer_segment"] = np.nan
    return df.sort_values("order_date").reset_index(drop=True)
