"""Reading and cleaning uploaded CSV / Excel files with Pandas."""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path

import numpy as np
import pandas as pd

ALLOWED_EXTENSIONS = {".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".xls"}
MAX_COLUMNS = 300
_DATE_NAME_HINT = re.compile(r"(date|time|day|month|created|updated|timestamp|_at$|^dt$)", re.I)
_DATE_VALUE_HINT = re.compile(r"\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4}|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}")


class IngestError(ValueError):
    """Raised for problems the user can fix (bad file, empty data...)."""


def read_upload(filename: str, content: bytes) -> pd.DataFrame:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise IngestError(
            f"Unsupported file type '{ext or 'unknown'}'. Upload a .csv, .tsv, .xlsx or .xls file."
        )
    if not content:
        raise IngestError("The uploaded file is empty.")
    try:
        if ext in {".xlsx", ".xlsm", ".xls"}:
            df = pd.read_excel(io.BytesIO(content), sheet_name=0)
        else:
            df = _read_delimited(content)
    except IngestError:
        raise
    except Exception as exc:  # pandas/openpyxl raise many different errors
        raise IngestError(f"Could not read the file: {exc}") from exc
    return clean_dataframe(df)


def _decode(content: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    raise IngestError("Could not decode the file. Please save it as UTF-8 CSV.")


def _read_delimited(content: bytes) -> pd.DataFrame:
    text = _decode(content)
    sample = text[:20000]
    try:
        sep = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        sep = ","
    # Read everything as text first so IDs / zip codes keep leading zeros;
    # clean_dataframe() then infers proper numeric/date/bool types itself.
    return pd.read_csv(io.StringIO(text), sep=sep, dtype=str, keep_default_na=False, na_values=[""])


def sanitize_column_names(columns) -> list[str]:
    seen: dict[str, int] = {}
    out: list[str] = []
    for i, col in enumerate(columns):
        name = re.sub(r"[^0-9a-zA-Z]+", "_", str(col).strip()).strip("_").lower()
        if not name or name.startswith("unnamed"):
            name = f"column_{i + 1}"
        if name[0].isdigit():
            name = f"c_{name}"
        base, n = name, seen.get(name, 0)
        while name in seen:
            n += 1
            name = f"{base}_{n}"
        seen[name] = 0
        seen[base] = max(seen.get(base, 0), n)
        out.append(name)
    return out


def clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    df = df.dropna(how="all").dropna(axis=1, how="all")
    if df.empty or df.shape[1] == 0:
        raise IngestError("The file has no usable data (no rows or columns found).")
    if df.shape[1] > MAX_COLUMNS:
        raise IngestError(f"Too many columns ({df.shape[1]}). The limit is {MAX_COLUMNS}.")
    df = df.copy()
    df.columns = sanitize_column_names(df.columns)
    df = df.reset_index(drop=True)

    for col in df.columns:
        s = df[col]
        if pd.api.types.is_bool_dtype(s) or pd.api.types.is_datetime64_any_dtype(s):
            continue
        if pd.api.types.is_numeric_dtype(s):
            continue
        # text-like column: strip whitespace, empty -> NaN, then try smarter types
        s = s.astype("object").map(lambda v: v.strip() if isinstance(v, str) else v)
        s = s.replace({"": np.nan, "NA": np.nan, "N/A": np.nan, "null": np.nan, "NULL": np.nan, "nan": np.nan})
        converted = _try_numeric(s)
        if converted is None:
            converted = _try_datetime(col, s)
        if converted is None:
            converted = _try_bool(s)
        df[col] = converted if converted is not None else s.astype("object")
    return df


def _try_numeric(s: pd.Series) -> pd.Series | None:
    non_null = s.dropna()
    if non_null.empty:
        return None
    as_str = non_null.astype(str)
    # keep identifiers like 007 or 01234 as text
    if as_str.str.match(r"^0\d+$").any():
        return None
    cleaned = as_str.str.replace(r"[,\s$€£₹%]", "", regex=True)
    cleaned = cleaned.str.replace(r"^\((.*)\)$", r"-\1", regex=True)
    nums = pd.to_numeric(cleaned, errors="coerce")
    if nums.notna().mean() < 0.95:
        return None
    full = pd.to_numeric(
        s.astype(str).str.replace(r"[,\s$€£₹%]", "", regex=True).str.replace(r"^\((.*)\)$", r"-\1", regex=True),
        errors="coerce",
    )
    full[s.isna()] = np.nan
    if (full.dropna() % 1 == 0).all() and full.abs().max() < 2**53:
        return full.astype("Int64")
    return full.astype("float64")


def _try_datetime(col: str, s: pd.Series) -> pd.Series | None:
    non_null = s.dropna().astype(str)
    if non_null.empty:
        return None
    sample = non_null.head(200)
    looks_like_date = sample.str.contains(_DATE_VALUE_HINT).mean() >= 0.8
    if not looks_like_date and not (_DATE_NAME_HINT.search(col) and sample.str.contains(r"\d").mean() >= 0.8):
        return None
    parsed = pd.to_datetime(s, errors="coerce", format="mixed")
    if parsed.notna().sum() / max(len(non_null), 1) < 0.9:
        return None
    return parsed


def _try_bool(s: pd.Series) -> pd.Series | None:
    non_null = s.dropna().astype(str).str.lower()
    if non_null.empty or non_null.nunique() > 2:
        return None
    vals = set(non_null.unique())
    if vals <= {"true", "false"} or vals <= {"yes", "no"}:
        mapping = {"true": True, "yes": True, "false": False, "no": False}
        return s.map(lambda v: mapping.get(str(v).lower()) if pd.notna(v) else None).astype("boolean")
    return None
