"""Per-dataset SQLite storage. Each upload becomes its own small SQLite file
with a single table named "data". Queries run against it read-only."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from app.core.config import get_settings
from app.services.profiling import TABLE_NAME


def dataset_db_path(dataset_id: str) -> Path:
    if not dataset_id.isalnum():
        raise ValueError("invalid dataset id")
    return get_settings().datasets_dir / f"{dataset_id}.sqlite"


def _prepare_for_sqlite(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        s = out[col]
        if pd.api.types.is_datetime64_any_dtype(s):
            if s.dropna().dt.normalize().eq(s.dropna()).all():
                out[col] = s.dt.strftime("%Y-%m-%d")
            else:
                out[col] = s.dt.strftime("%Y-%m-%d %H:%M:%S")
            out[col] = out[col].astype("object").where(s.notna(), None)
        elif pd.api.types.is_bool_dtype(s):
            out[col] = s.astype("Int64")
        elif isinstance(s.dtype, pd.StringDtype):
            out[col] = s.astype("object")
    return out


def save_dataframe(dataset_id: str, df: pd.DataFrame) -> Path:
    path = dataset_db_path(dataset_id)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    try:
        _prepare_for_sqlite(df).to_sql(TABLE_NAME, conn, index=False, if_exists="replace")
        conn.commit()
    finally:
        conn.close()
    return path


def delete_dataset_files(dataset_id: str) -> None:
    path = dataset_db_path(dataset_id)
    path.unlink(missing_ok=True)


def load_dataframe(dataset_id: str, profile: dict) -> pd.DataFrame:
    """Load the full table back into Pandas, restoring datetime columns."""
    path = dataset_db_path(dataset_id)
    if not path.exists():
        raise FileNotFoundError("Dataset file is missing on disk.")
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        df = pd.read_sql_query(f'SELECT * FROM "{TABLE_NAME}"', conn)
    finally:
        conn.close()
    for c in profile["columns"]:
        if c["kind"] == "datetime" and c["name"] in df:
            df[c["name"]] = pd.to_datetime(df[c["name"]], errors="coerce")
        elif c["kind"] == "boolean" and c["name"] in df:
            df[c["name"]] = df[c["name"]].astype("boolean")
    return df


def preview_rows(dataset_id: str, profile: dict, page: int, page_size: int,
                 sort_by: str | None, sort_dir: str, search: str | None) -> dict:
    """Paginated, sortable, searchable view of the raw table (Explorer page).
    Column names are whitelisted against the profile; values are bound as
    parameters, so this is injection-safe."""
    names = [c["name"] for c in profile["columns"]]
    path = dataset_db_path(dataset_id)
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        where, params = "", []
        if search and search.strip():
            like = f"%{search.strip()}%"
            where = " WHERE " + " OR ".join(f'CAST("{n}" AS TEXT) LIKE ?' for n in names)
            params = [like] * len(names)
        total = conn.execute(f'SELECT COUNT(*) FROM "{TABLE_NAME}"{where}', params).fetchone()[0]
        order = ""
        if sort_by in names:
            order = f' ORDER BY "{sort_by}" {"DESC" if sort_dir == "desc" else "ASC"}'
        offset = (page - 1) * page_size
        cols_sql = ", ".join(f'"{n}"' for n in names)
        cur = conn.execute(
            f'SELECT rowid, {cols_sql} FROM "{TABLE_NAME}"{where}{order} LIMIT ? OFFSET ?',
            params + [page_size, offset],
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    return {"columns": names, "row_ids": [r[0] for r in rows], "rows": [list(r[1:]) for r in rows],
            "total": total, "page": page, "page_size": page_size}
