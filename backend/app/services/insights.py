"""Automated insights + anomaly detection (pure Pandas / NumPy / scikit-learn).

Nothing here needs the LLM API, so the dashboard works instantly and
deterministically. The LLM can optionally turn these facts into a written
executive summary (see ai_summary()).
"""
from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd
import plotly.express as px
from sklearn.ensemble import IsolationForest

from app.services import charts
from app.services.profiling import _py

METRIC_HINTS = ("revenue", "sales", "amount", "profit", "total", "price", "cost", "income", "value", "spend", "quantity", "units")


# ----------------------------------------------------------------- helpers
def _kinds(profile: dict, kind: str) -> list[str]:
    return [c["name"] for c in profile["columns"] if c["kind"] == kind]


def primary_metric(df: pd.DataFrame, profile: dict) -> str | None:
    nums = _kinds(profile, "numeric")
    if not nums:
        return None
    for hint in METRIC_HINTS:
        for n in nums:
            if hint in n.lower():
                return n
    return nums[0]


def _fmt(v: float) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "n/a"
    a = abs(v)
    if a >= 1e9: return f"{v/1e9:.2f}B"
    if a >= 1e6: return f"{v/1e6:.2f}M"
    if a >= 1e4: return f"{v/1e3:.1f}K"
    if a >= 100: return f"{v:,.0f}"
    return f"{v:,.2f}"


def _time_grain(span_days: float) -> tuple[str, str]:
    if span_days > 365 * 2: return "MS", "month"
    if span_days > 120: return "W", "week"
    return "D", "day"


def _label(name: str) -> str:
    return name.replace("_", " ")


# ----------------------------------------------------------------- insights
def compute_insights(df: pd.DataFrame, profile: dict) -> dict:
    metric = primary_metric(df, profile)
    dates = _kinds(profile, "datetime")
    cats = [c for c in _kinds(profile, "categorical") if 2 <= df[c].nunique() <= 30]
    nums = _kinds(profile, "numeric")

    findings: list[dict] = []
    findings += _quality_findings(profile)
    if metric:
        findings += _concentration_findings(df, metric, cats)
        if dates:
            findings += _trend_findings(df, metric, dates[0])
    findings += _correlation_findings(df, nums)
    findings += _skew_findings(df, nums)

    anomalies = detect_anomalies(df, profile, metric, dates[0] if dates else None)
    if anomalies["total"]:
        findings.append({
            "category": "anomaly", "severity": "warning",
            "title": f"{anomalies['total']} unusual values detected",
            "detail": anomalies["headline"],
        })
    order = {"warning": 0, "positive": 1, "info": 2}
    findings.sort(key=lambda f: order.get(f["severity"], 3))

    kpis = [
        {"label": "Rows", "value": f"{profile['n_rows']:,}"},
        {"label": "Columns", "value": str(profile["n_cols"])},
        {"label": "Data quality", "value": f"{profile['quality_score']:.0f}/100"},
    ]
    if metric:
        s = df[metric].dropna()
        kpis += [
            {"label": f"Total {_label(metric)}", "value": _fmt(float(s.sum()))},
            {"label": f"Average {_label(metric)}", "value": _fmt(float(s.mean()))},
        ]

    return {
        "primary_metric": metric,
        "kpis": kpis,
        "findings": findings,
        "anomalies": anomalies,
        "charts": build_dashboard_charts(df, profile, metric, dates[0] if dates else None, cats, nums, anomalies),
        "ai_summary": None,
    }


def _quality_findings(profile: dict) -> list[dict]:
    out = []
    bad = [c for c in profile["columns"] if c["null_pct"] >= 5]
    if bad:
        worst = sorted(bad, key=lambda c: -c["null_pct"])[:3]
        out.append({"category": "quality", "severity": "warning",
                    "title": f"{len(bad)} column(s) have 5%+ missing values",
                    "detail": "Most affected: " + ", ".join(f"{c['name']} ({c['null_pct']:.0f}%)" for c in worst) + "."})
    if profile["duplicate_rows"]:
        out.append({"category": "quality", "severity": "warning",
                    "title": f"{profile['duplicate_rows']:,} duplicate rows",
                    "detail": "Fully identical rows can inflate totals. Consider removing them before analysis."})
    if not bad and not profile["duplicate_rows"]:
        out.append({"category": "quality", "severity": "positive", "title": "Clean dataset",
                    "detail": "No significant missing values or duplicate rows were found."})
    return out


def _concentration_findings(df: pd.DataFrame, metric: str, cats: list[str]) -> list[dict]:
    out = []
    total = df[metric].sum()
    if not total or np.isnan(total):
        return out
    for c in cats[:3]:
        g = df.groupby(c, observed=True)[metric].sum().sort_values(ascending=False)
        if len(g) < 2 or (g < 0).any():
            continue
        top_share = g.iloc[0] / g.sum()
        if top_share >= 0.4 or len(g) >= 5:
            out.append({"category": "segment", "severity": "info",
                        "title": f"{g.index[0]} leads {_label(c)} on {_label(metric)}",
                        "detail": f"{g.index[0]} contributes {top_share:.0%} of total {_label(metric)} "
                                  f"({_fmt(float(g.iloc[0]))}); lowest is {g.index[-1]} at {g.iloc[-1] / g.sum():.0%}."})
    return out


def _trend_findings(df: pd.DataFrame, metric: str, date_col: str) -> list[dict]:
    series = _time_series(df, metric, date_col)
    if series is None or len(series) < 4:
        return []
    y = series["value"].to_numpy(dtype=float)
    x = np.arange(len(y))
    slope = np.polyfit(x, y, 1)[0]
    mean = y.mean() or 1.0
    rel = slope * (len(y) - 1) / abs(mean)
    first_k = y[: max(1, len(y) // 4)].mean()
    last_k = y[-max(1, len(y) // 4):].mean()
    change = (last_k - first_k) / abs(first_k) if first_k else 0.0
    grain = series.attrs["grain"]
    best, worst = series["value"].idxmax(), series["value"].idxmin()
    sev = "positive" if rel > 0.1 else "warning" if rel < -0.1 else "info"
    direction = "trending upward" if rel > 0.1 else "trending downward" if rel < -0.1 else "broadly flat over time"
    return [{
        "category": "trend", "severity": sev,
        "title": f"{_label(metric).capitalize()} is {direction}",
        "detail": f"Comparing the first and last quarter of the timeline, {grain}ly {_label(metric)} moved {change:+.0%}. "
                  f"Peak {grain}: {best:%Y-%m-%d} ({_fmt(float(series.loc[best, 'value']))}); "
                  f"lowest: {worst:%Y-%m-%d} ({_fmt(float(series.loc[worst, 'value']))}).",
    }]


def _correlation_findings(df: pd.DataFrame, nums: list[str]) -> list[dict]:
    usable = [n for n in nums if df[n].nunique() > 5 and not n.endswith("id")]
    if len(usable) < 2:
        return []
    corr = df[usable].corr(numeric_only=True)
    pairs = []
    for i, a in enumerate(usable):
        for b in usable[i + 1:]:
            r = corr.loc[a, b]
            if pd.notna(r) and abs(r) >= 0.6:
                pairs.append((abs(r), r, a, b))
    pairs.sort(reverse=True)
    return [{
        "category": "correlation", "severity": "info",
        "title": f"{_label(a)} and {_label(b)} move {'together' if r > 0 else 'in opposite directions'}",
        "detail": f"Correlation r = {r:.2f}. Correlation is not causation; use it as a lead to investigate.",
    } for _, r, a, b in pairs[:3]]


def _skew_findings(df: pd.DataFrame, nums: list[str]) -> list[dict]:
    out = []
    for n in nums:
        s = df[n].dropna()
        if len(s) < 30 or s.nunique() < 10 or n.endswith("id"):
            continue
        sk = float(s.skew())
        if abs(sk) >= 2 and s.mean():
            out.append({"category": "distribution", "severity": "info",
                        "title": f"{_label(n).capitalize()} is heavily skewed",
                        "detail": f"Mean ({_fmt(float(s.mean()))}) and median ({_fmt(float(s.median()))}) differ a lot, "
                                  f"so a few large values dominate. Prefer the median for 'typical' values."})
        if len(out) >= 2:
            break
    return out


# ----------------------------------------------------------------- time series
def _time_series(df: pd.DataFrame, metric: str, date_col: str) -> pd.DataFrame | None:
    d = df[[date_col, metric]].dropna()
    if len(d) < 4:
        return None
    span = (d[date_col].max() - d[date_col].min()).days
    freq, grain = _time_grain(span)
    ts = d.set_index(date_col)[metric].resample(freq).sum().to_frame("value")
    if grain != "day" and len(ts) >= 6:
        ts = ts.iloc[1:-1]  # first/last buckets are usually partial and would look like fake dips
    ts.attrs["grain"] = grain
    return ts


# ----------------------------------------------------------------- anomalies
def detect_anomalies(df: pd.DataFrame, profile: dict, metric: str | None, date_col: str | None) -> dict:
    nums = [n for n in _kinds(profile, "numeric") if df[n].nunique() > 5 and not n.endswith("id")]
    result: dict[str, Any] = {"time_series": [], "outliers": [], "multivariate": [], "total": 0, "headline": ""}

    # 1) time-series anomalies on the primary metric (robust z-score vs rolling median)
    if metric and date_col:
        ts = _time_series(df, metric, date_col)
        if ts is not None and len(ts) >= 8:
            v = ts["value"]
            window = max(5, len(v) // 8) | 1
            baseline = v.rolling(window, center=True, min_periods=3).median()
            resid = v - baseline
            mad = np.median(np.abs(resid.dropna() - np.median(resid.dropna()))) or resid.std() or 1.0
            z = 0.6745 * resid / mad
            for ts_idx in z[np.abs(z) > 3.5].index:
                result["time_series"].append({
                    "period": ts_idx.strftime("%Y-%m-%d"), "value": _py(v[ts_idx]),
                    "expected": _py(baseline[ts_idx]), "z_score": round(float(z[ts_idx]), 2),
                    "direction": "spike" if z[ts_idx] > 0 else "drop",
                })
            result["time_series"].sort(key=lambda r: -abs(r["z_score"]))
            result["time_series"] = result["time_series"][:10]
            result["_ts_frame"] = ts  # used for the chart, removed before returning

    # 2) univariate outliers per numeric column (modified z-score using MAD)
    for n in nums[:12]:
        s = df[n].dropna()
        if len(s) < 20:
            continue
        med = s.median()
        mad = np.median(np.abs(s - med))
        if mad == 0:
            continue
        z = 0.6745 * (s - med) / mad
        flagged = z[np.abs(z) > 5].sort_values(key=np.abs, ascending=False).head(3)
        for idx, zz in flagged.items():
            result["outliers"].append({"row": int(idx) + 1, "column": n, "value": _py(s[idx]),
                                       "typical": _py(med), "z_score": round(float(zz), 1)})
    result["outliers"].sort(key=lambda r: -abs(r["z_score"]))
    result["outliers"] = result["outliers"][:15]

    # 3) multivariate anomalies with Isolation Forest
    if len(nums) >= 2 and len(df) >= 50:
        X = df[nums[:10]].astype("float64")
        X = X.fillna(X.median()).fillna(0)
        Xs = (X - X.mean()) / X.std(ddof=0).replace(0, 1)
        model = IsolationForest(n_estimators=150, contamination=min(0.02, 10 / len(df) + 0.005), random_state=42)
        labels = model.fit_predict(Xs)
        scores = -model.score_samples(Xs)
        bad = np.where(labels == -1)[0]
        bad = bad[np.argsort(-scores[bad])][:10]
        for i in bad:
            row = df.iloc[i]
            result["multivariate"].append({
                "row": int(i) + 1, "score": round(float(scores[i]), 3),
                "values": {n: _py(row[n]) for n in nums[:6]},
            })

    result["total"] = len(result["time_series"]) + len(result["outliers"]) + len(result["multivariate"])
    parts = []
    if result["time_series"]:
        t = result["time_series"][0]
        parts.append(f"largest {t['direction']} in {_label(metric or 'the metric')} on {t['period']}")
    if result["outliers"]:
        o = result["outliers"][0]
        parts.append(f"extreme {_label(o['column'])} value {_fmt(float(o['value']))} (typical {_fmt(float(o['typical']))}) at row {o['row']}")
    if result["multivariate"]:
        parts.append(f"{len(result['multivariate'])} rows with unusual combinations of values")
    result["headline"] = "; ".join(parts).capitalize() + "." if parts else ""
    return result


# ----------------------------------------------------------------- charts
def build_dashboard_charts(df, profile, metric, date_col, cats, nums, anomalies) -> list[dict]:
    out: list[dict] = []
    try:
        if metric and date_col:
            ts = anomalies.get("_ts_frame")
            if ts is None:
                ts = _time_series(df, metric, date_col)
            if ts is not None and len(ts) >= 3:
                fig = px.line(ts.reset_index(), x=date_col, y="value", markers=len(ts) <= 60)
                fig.update_traces(name=_label(metric), showlegend=False)
                if anomalies["time_series"]:
                    pts = pd.DataFrame(anomalies["time_series"])
                    fig.add_scatter(x=pts["period"], y=pts["value"], mode="markers", name="Anomaly",
                                    marker=dict(color="#EF4444", size=11, symbol="diamond", line=dict(width=1, color="white")))
                    fig.update_layout(showlegend=True)
                fig.update_yaxes(title=_label(metric))
                fig.update_xaxes(title=None)
                out.append({"id": "trend", "title": f"{_label(metric).capitalize()} over time", "figure": charts.to_json(charts._theme(fig))})
        if metric and cats:
            c = cats[0]
            g = df.groupby(c, observed=True)[metric].sum().sort_values(ascending=False).head(12).reset_index()
            fig = px.bar(g, x=c, y=metric) if g[c].astype(str).str.len().max() <= 14 else px.bar(g.sort_values(metric), x=metric, y=c, orientation="h")
            out.append({"id": "segments", "title": f"{_label(metric).capitalize()} by {_label(c)}", "figure": charts.to_json(charts._theme(fig))})
        if metric:
            fig = px.histogram(df, x=metric, nbins=30)
            out.append({"id": "distribution", "title": f"Distribution of {_label(metric)}", "figure": charts.to_json(charts._theme(fig))})
        usable = [n for n in nums if df[n].nunique() > 5 and not n.endswith("id")][:8]
        if len(usable) >= 3:
            corr = df[usable].corr(numeric_only=True).round(2)
            fig = px.imshow(corr, text_auto=True, color_continuous_scale="RdBu_r", zmin=-1, zmax=1, aspect="auto")
            out.append({"id": "correlation", "title": "Correlation between numeric columns", "figure": charts.to_json(charts._theme(fig, height=420))})
        if len(cats) >= 2:
            c = cats[1]
            vc = df[c].value_counts().head(8).reset_index()
            vc.columns = [c, "count"]
            fig = px.pie(vc, names=c, values="count", hole=0.55)
            out.append({"id": "mix", "title": f"Share of rows by {_label(c)}", "figure": charts.to_json(charts._theme(fig))})
    finally:
        anomalies.pop("_ts_frame", None)
    return out


# ----------------------------------------------------------------- AI summary
def facts_for_llm(profile: dict, insights: dict) -> str:
    slim = {
        "rows": profile["n_rows"], "columns": profile["n_cols"], "quality_score": profile["quality_score"],
        "primary_metric": insights["primary_metric"], "kpis": insights["kpis"],
        "findings": [{k: f[k] for k in ("severity", "title", "detail")} for f in insights["findings"]],
        "anomalies": {k: insights["anomalies"][k] for k in ("time_series", "outliers", "multivariate")},
    }
    return json.dumps(slim, default=str)[:12000]
