import pytest

from app.services import ingest, profiling, sqlite_runner, storage
from app.services.sql_guard import UnsafeSQLError, validate_sql


def test_csv_type_inference_and_leading_zeros():
    csv = b"Order Date,Region,Revenue ($),Units,Zip,Active\n2024-01-05,North,\"1,200.50\",3,01234,yes\n2024-01-06,South,900,2,02134,no\n"
    df = ingest.read_upload("t.csv", csv)
    assert list(df.columns) == ["order_date", "region", "revenue", "units", "zip", "active"]
    assert str(df["order_date"].dtype).startswith("datetime64")
    assert df["revenue"].iloc[0] == 1200.5
    assert df["zip"].iloc[0] == "01234"
    assert df["active"].iloc[0] is True or bool(df["active"].iloc[0]) is True


def test_semicolon_csv_and_excel(tmp_path):
    df = ingest.read_upload("a.csv", b"a;b\n1;x\n2;y\n")
    assert list(df.columns) == ["a", "b"] and len(df) == 2
    import pandas as pd
    p = tmp_path / "x.xlsx"
    pd.DataFrame({"Name": ["a", "b"], "Total Sales": [1.5, 2.5]}).to_excel(p, index=False)
    df = ingest.read_upload("x.xlsx", p.read_bytes())
    assert list(df.columns) == ["name", "total_sales"]


@pytest.mark.parametrize("name,content", [("x.exe", b"abc"), ("x.csv", b""), ("x.csv", b"\n\n")])
def test_bad_uploads_rejected(name, content):
    with pytest.raises(ingest.IngestError):
        ingest.read_upload(name, content)


@pytest.mark.parametrize("sql", [
    "DROP TABLE data", "DELETE FROM data", "SELECT * FROM data; DROP TABLE data",
    "SELECT * FROM other_table", "PRAGMA table_info(data)", "ATTACH DATABASE 'x' AS y",
    "UPDATE data SET a=1", "SELECT * FROM main.data", "",
])
def test_sql_guard_blocks(sql):
    with pytest.raises(UnsafeSQLError):
        validate_sql(sql)


def test_sql_guard_allows_selects_and_ctes():
    assert validate_sql("SELECT a FROM data;")
    assert validate_sql("WITH t AS (SELECT a FROM data) SELECT * FROM t")
    assert validate_sql("SELECT a, SUM(b) OVER (PARTITION BY a) FROM data")


def test_runner_sandbox(client, dataset_id):
    r = sqlite_runner.run_query(dataset_id, "SELECT region, STDDEV(revenue) s, MEDIAN(revenue) m FROM data GROUP BY region")
    assert len(r.df) == 4 and r.df["s"].notna().all()
    with pytest.raises(sqlite_runner.QueryExecutionError):
        sqlite_runner.run_query(dataset_id, "SELECT load_extension('x')")
    with pytest.raises(sqlite_runner.QueryExecutionError):
        sqlite_runner.run_query(dataset_id, "SELECT nope FROM data")
    capped = sqlite_runner.run_query(dataset_id, "SELECT * FROM data", max_rows=10)
    assert len(capped.df) == 10 and capped.truncated


def test_runner_timeout(client, dataset_id, monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "query_timeout_seconds", 0.2)
    heavy = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x < 500000000) SELECT COUNT(*) FROM c"
    with pytest.raises(sqlite_runner.QueryExecutionError, match="longer than"):
        sqlite_runner.run_query(dataset_id, heavy)
