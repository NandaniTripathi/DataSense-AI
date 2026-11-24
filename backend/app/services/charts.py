"""Turn a query result (DataFrame) + a chart spec into a Plotly figure (JSON).

The LLM only picks the chart *type* and columns; all rendering decisions and
validation happen here, so a bad suggestion can never crash the UI.
"""
from __future__ import annotations

import json
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

PALETTE = ["#4F6BED", "#14B8A6", "#F59E0B", "#EF4444", "#8B5CF6", "#EC4899", "#22C55E", "#0EA5E9"]
MAX_CATEGORIES = 30


def _theme(fig: go.Figure, title: str | None = None, height: int = 380) -> go.Figure:
    fig.update_layout(
        title=dict(text=title or "", x=0, xanchor="left", font=dict(size=15)),
        colorway=PALETTE,
        font=dict(family="Manrope, system-ui, sans-serif", size=12, color="#334155"),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=48, r=16, t=48 if title else 16, b=48),
        height=height,
        hoverlabel=dict(font_family="Manrope, system-ui, sans-serif"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    fig.update_xaxes(showgrid=False, linecolor="#CBD5E1", automargin=True)
    fig.update_yaxes(gridcolor="#E2E8F0", zeroline=False, automargin=True)
    return fig


def to_json(fig: go.Figure) -> dict:
    d = json.loads(fig.to_json())
    d.get("layout", {}).pop("template", None)  # big default theme; our own styling is applied in _theme()
    return d


def _is_num(df: pd.DataFrame, col: str) -> bool:
    return pd.api.types.is_numeric_dtype(df[col]) and not pd.api.types.is_bool_dtype(df[col])


def auto_spec(df: pd.DataFrame) -> dict | None:
    """Pick a reasonable chart when the LLM didn't (or gave an invalid one)."""
    if df.empty or df.shape[1] < 1:
        return None
    cols = list(df.columns)
    nums = [c for c in cols if _is_num(df, c)]
    cats = [c for c in cols if c not in nums]
    if len(df) == 1 and len(cols) <= 2:
        return None
    if cats and nums:
        x = cats[0]
        looks_time = any(k in x.lower() for k in ("date", "month", "year", "week", "day", "quarter", "period"))
        return {"type": "line" if looks_time else "bar", "x": x, "y": nums[0], "title": f"{nums[0]} by {x}"}
    if len(nums) >= 2:
        return {"type": "scatter", "x": nums[0], "y": nums[1], "title": f"{nums[1]} vs {nums[0]}"}
    if len(nums) == 1 and len(df) > 5:
        return {"type": "histogram", "x": nums[0], "title": f"Distribution of {nums[0]}"}
    return None


def build_chart(df: pd.DataFrame, spec: dict | None) -> dict | None:
    if df is None or df.empty:
        return None
    spec = _validate_spec(df, spec) or auto_spec(df)
    if not spec:
        return None
    try:
        return _render(df, spec)
    except Exception:  # never let a chart problem break an answer
        fallback = auto_spec(df)
        if fallback and fallback != spec:
            try:
                return _render(df, fallback)
            except Exception:
                return None
        return None


def _validate_spec(df: pd.DataFrame, spec: dict | None) -> dict | None:
    if not spec or not isinstance(spec, dict):
        return None
    ctype = spec.get("type")
    if ctype not in {"bar", "line", "area", "scatter", "pie", "histogram", "box"}:
        return None
    x, y, color = spec.get("x"), spec.get("y"), spec.get("color")
    if x not in df.columns:
        return None
    if ctype not in {"histogram"} and ctype != "pie" and y not in df.columns:
        if ctype == "box":
            y = None
        else:
            return None
    if ctype == "pie" and y not in df.columns:
        return None
    if ctype in {"scatter", "line", "area", "bar"} and y is not None and not _is_num(df, y):
        return None
    if color is not None and color not in df.columns:
        color = None
    return {"type": ctype, "x": x, "y": y, "color": color, "title": spec.get("title") or ""}


def _render(df: pd.DataFrame, spec: dict) -> dict:
    t, x, y, color, title = spec["type"], spec["x"], spec.get("y"), spec.get("color"), spec.get("title")
    data = df.copy()
    if t == "bar":
        if data[x].nunique() > MAX_CATEGORIES:
            data = data.sort_values(y, ascending=False).head(MAX_CATEGORIES)
        horizontal = data[x].astype(str).str.len().max() > 14 and not color
        if horizontal:
            data = data.sort_values(y, ascending=True)
            fig = px.bar(data, x=y, y=x, color=color, orientation="h")
        else:
            fig = px.bar(data, x=x, y=y, color=color, barmode="group")
    elif t in {"line", "area"}:
        data = data.sort_values(x)
        fn = px.line if t == "line" else px.area
        fig = fn(data, x=x, y=y, color=color, markers=(len(data) <= 60))
    elif t == "scatter":
        fig = px.scatter(data.head(5000), x=x, y=y, color=color, opacity=0.75)
    elif t == "pie":
        data = data.sort_values(y, ascending=False).head(8)
        fig = px.pie(data, names=x, values=y, hole=0.5)
    elif t == "histogram":
        fig = px.histogram(data, x=x, nbins=30, color=color)
    else:  # box
        fig = px.box(data, x=color or x if y else None, y=y or x, points="outliers")
    _theme(fig, title)
    return to_json(fig)


def _fmt_title(t: str) -> str:
    return t.replace("_", " ").strip().capitalize()
