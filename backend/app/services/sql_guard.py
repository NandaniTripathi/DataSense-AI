"""Static SQL validation (first line of defence for AI-generated queries).

We parse the SQL with sqlglot and accept only a single read-only SELECT that
touches only the dataset table. The SQLite runner adds two more layers
(read-only connection + authorizer callback), so a bug here is not fatal.
"""
from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from app.services.profiling import TABLE_NAME


class UnsafeSQLError(ValueError):
    """The query is not a single read-only SELECT on the dataset table."""


_FORBIDDEN_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter,
    exp.Command, exp.Pragma, exp.Attach, exp.Transaction, exp.Merge,
)


def validate_sql(sql: str) -> str:
    sql = (sql or "").strip().rstrip(";").strip()
    if not sql:
        raise UnsafeSQLError("The query is empty.")
    if len(sql) > 8000:
        raise UnsafeSQLError("The query is too long.")
    try:
        statements = sqlglot.parse(sql, read="sqlite")
    except ParseError as exc:
        raise UnsafeSQLError(f"SQL syntax error: {str(exc).splitlines()[0]}") from exc
    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise UnsafeSQLError("Exactly one SQL statement is allowed.")
    tree = statements[0]
    if not isinstance(tree, exp.Query):
        raise UnsafeSQLError("Only SELECT queries are allowed.")
    for node in tree.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise UnsafeSQLError("Only read-only SELECT queries are allowed.")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if name in cte_names:
            continue
        if table.db or table.catalog:
            raise UnsafeSQLError("Schema-qualified tables are not allowed.")
        if name != TABLE_NAME:
            raise UnsafeSQLError(
                f'Unknown table "{table.name}". The only table is "{TABLE_NAME}".'
            )
    return sql
