"""Column profiling: types, stats, quality. The profile is what the LLM sees
(never the raw data), and what the Explorer / Dashboard pages display."""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

TABLE_NAME = "data"


def _py(v: Any) -> Any:
    """Convert numpy / pandas scalars into JSON-safe Python values."""
    if v is None or v is pd.NA or v is pd.NaT:
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if isinstance(v, pd.Timestamp):
        # match the text format used in SQLite so the LLM writes correct filters
        return v.strftime("%Y-%m-%d") if v == v.normalize() else v.strftime("%Y-%m-%d %H:%M:%S")
    return v


def column_kind(s: pd.Series, n_rows: int, name: str) -> str:
    if pd.api.types.is_bool_dtype(s):
        return "boolean"
    if pd.api.types.is_datetime64_any_dtype(s):
        return "datetime"
    nunique = s.nunique(dropna=True)
    if pd.api.types.is_numeric_dtype(s):
        if name.endswith("_id") or name == "id":
            return "id"
        return "numeric"
    if nunique >= 0.95 * max(n_rows, 1) and n_rows > 20:
        return "id" if (name.endswith("id") or nunique == n_rows) else "text"
    if nunique <= max(25, int(0.05 * n_rows)):
        return "categorical"
    return "text"


def sql_type(s: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(s):
        return "BOOLEAN"
    if pd.api.types.is_datetime64_any_dtype(s):
        return "DATETIME"
    if pd.api.types.is_integer_dtype(s):
        return "INTEGER"
    if pd.api.types.is_float_dtype(s):
        return "REAL"
    return "TEXT"


def profile_dataframe(df: pd.DataFrame) -> dict:
    n_rows, n_cols = df.shape
    columns = []
    total_missing = int(df.isna().sum().sum())
    for name in df.columns:
        s = df[name]
        kind = column_kind(s, n_rows, name)
        nulls = int(s.isna().sum())
        entry: dict[str, Any] = {
            "name": name,
            "sql_type": sql_type(s),
            "kind": kind,
            "null_count": nulls,
            "null_pct": round(100 * nulls / max(n_rows, 1), 2),
            "unique": int(s.nunique(dropna=True)),
            "samples": [_py(v) for v in s.dropna().head(3).tolist()],
        }
        if kind == "numeric" and s.notna().any():
            num = pd.to_numeric(s, errors="coerce").astype("float64")
            q1, med, q3 = num.quantile([0.25, 0.5, 0.75]).tolist()
            entry["stats"] = {
                "min": _py(num.min()), "max": _py(num.max()), "mean": _py(num.mean()),
                "median": _py(med), "std": _py(num.std()), "q1": _py(q1), "q3": _py(q3),
                "sum": _py(num.sum()),
            }
        elif kind == "datetime" and s.notna().any():
            entry["stats"] = {"min": _py(s.min()), "max": _py(s.max())}
        elif kind in {"categorical", "boolean"}:
            vc = s.value_counts(dropna=True).head(8)
            entry["top_values"] = [{"value": _py(k), "count": int(v)} for k, v in vc.items()]
        elif kind == "text" and s.notna().any():
            entry["top_values"] = [
                {"value": _py(k), "count": int(v)} for k, v in s.value_counts().head(3).items()
            ]
        columns.append(entry)

    dup = int(df.duplicated().sum())
    missing_pct = 100 * total_missing / max(n_rows * n_cols, 1)
    quality = max(0.0, 100 - missing_pct * 1.5 - 100 * dup / max(n_rows, 1) * 2)
    return {
        "table": TABLE_NAME,
        "n_rows": int(n_rows),
        "n_cols": int(n_cols),
        "duplicate_rows": dup,
        "missing_cells": total_missing,
        "missing_pct": round(missing_pct, 2),
        "quality_score": round(quality, 1),
        "columns": columns,
    }


def schema_card(profile: dict) -> str:
    """Compact text description of the dataset for the LLM prompt."""
    lines = [
        f'Table: "{profile["table"]}"  ({profile["n_rows"]:,} rows, {profile["n_cols"]} columns)',
        "Columns (name | sqlite type | kind | details):",
    ]
    for c in profile["columns"]:
        detail = [f'{c["null_pct"]}% null', f'{c["unique"]} unique']
        st = c.get("stats")
        if c["kind"] == "numeric" and st:
            detail.append(f'min={st["min"]}, max={st["max"]}, mean={round(st["mean"], 3) if st["mean"] is not None else None}')
        elif c["kind"] == "datetime" and st:
            detail.append(f'range {st["min"]} to {st["max"]} (stored as ISO text)')
        if c.get("top_values") and c["kind"] in {"categorical", "boolean"}:
            tv = ", ".join(f'{t["value"]!r}' for t in c["top_values"][:8])
            detail.append(f"values: {tv}")
        elif c["samples"]:
            detail.append("e.g. " + ", ".join(repr(x) for x in c["samples"]))
        lines.append(f'- {c["name"]} | {c["sql_type"]} | {c["kind"]} | ' + "; ".join(detail))
    return "\n".join(lines)
