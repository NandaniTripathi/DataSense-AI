import os
import tempfile

# Must be set before the app is imported: isolated data dir + mock LLM.
_TMP = tempfile.mkdtemp(prefix="datasense_test_")
os.environ["DATA_DIR"] = _TMP
os.environ["LLM_PROVIDER"] = "mock"
os.environ["GROQ_API_KEY"] = ""

import io  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core import ratelimit  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    ratelimit.reset()


@pytest.fixture(scope="session")
def sales_csv() -> bytes:
    from app.services.samples import generate_sales_demo
    return generate_sales_demo().to_csv(index=False).encode()


@pytest.fixture(scope="session")
def dataset_id(client, sales_csv) -> str:
    r = client.post("/api/datasets", files={"file": ("sales.csv", io.BytesIO(sales_csv), "text/csv")})
    assert r.status_code == 201, r.text
    return r.json()["id"]
