import io


def test_health(client):
    r = client.get("/api/health").json()
    assert r["status"] == "ok" and r["llm_configured"] is True


def test_upload_list_preview_delete(client, sales_csv):
    r = client.post("/api/datasets", files={"file": ("tmp.csv", io.BytesIO(sales_csv), "text/csv")}, data={"name": "Temp"})
    assert r.status_code == 201
    ds = r.json()
    assert ds["name"] == "Temp" and ds["n_rows"] == 4000
    assert any(d["id"] == ds["id"] for d in client.get("/api/datasets").json())
    pv = client.get(f"/api/datasets/{ds['id']}/preview", params={"page": 2, "page_size": 10, "sort_by": "revenue", "sort_dir": "desc"}).json()
    assert len(pv["rows"]) == 10 and pv["total"] == 4000 and pv["page"] == 2
    srch = client.get(f"/api/datasets/{ds['id']}/preview", params={"search": "Electronics"}).json()
    assert 0 < srch["total"] < 4000
    inj = client.get(f"/api/datasets/{ds['id']}/preview", params={"sort_by": "revenue; DROP TABLE data"})
    assert inj.status_code == 200  # unknown sort column is simply ignored
    assert client.delete(f"/api/datasets/{ds['id']}").status_code == 204
    assert client.get(f"/api/datasets/{ds['id']}").status_code == 404


def test_upload_validation(client):
    assert client.post("/api/datasets", files={"file": ("x.exe", b"abc", "application/octet-stream")}).status_code == 422
    assert client.post("/api/datasets", files={"file": ("x.csv", b"", "text/csv")}).status_code == 422
    assert client.get("/api/datasets/not-a-real-id").status_code == 404
    assert client.get("/api/datasets/..%2F..%2Fetc").status_code in (404, 422)


def test_sample_dataset(client):
    r = client.post("/api/datasets/sample")
    assert r.status_code == 201 and r.json()["n_rows"] == 4000


def test_insights_and_anomalies(client, dataset_id):
    ins = client.get(f"/api/datasets/{dataset_id}/insights").json()
    assert ins["primary_metric"] == "revenue"
    assert len(ins["charts"]) >= 4 and ins["findings"]
    assert ins["anomalies"]["total"] > 0
    # the injected 184,000 revenue data-entry error must be caught
    assert any(o["column"] == "revenue" and o["value"] == 184000.0 for o in ins["anomalies"]["outliers"])
    # the injected 2024-06-18 flash-sale spike must show up in the time series
    assert ins["anomalies"]["time_series"]


def test_ai_summary(client, dataset_id):
    r = client.post(f"/api/datasets/{dataset_id}/insights/ai-summary")
    assert r.status_code == 200 and "summary" in r.json()["ai_summary"].lower()
    assert client.get(f"/api/datasets/{dataset_id}/insights").json()["ai_summary"]


def test_chat_flow_followup_csv_and_report(client, dataset_id):
    r = client.post("/api/chat", json={"dataset_id": dataset_id, "message": "total revenue by region"})
    assert r.status_code == 200, r.text
    body = r.json()
    a = body["assistant_message"]["payload"]
    assert a["sql"].lower().startswith("select") and a["table"]["rows"] and a["chart"]["data"]
    assert len(a["follow_ups"]) == 3
    cid = body["conversation_id"]

    r2 = client.post("/api/chat", json={"dataset_id": dataset_id, "message": "now by month", "conversation_id": cid})
    assert r2.status_code == 200
    assert "5 messages" in r2.json()["assistant_message"]["content"]  # history was sent (2 prior + SQL note + new question)
    convo = client.get(f"/api/conversations/{cid}").json()
    assert [m["role"] for m in convo["messages"]] == ["user", "assistant", "user", "assistant"]
    assert client.get(f"/api/datasets/{dataset_id}/conversations").json()[0]["id"] == cid

    csv = client.get(f"/api/messages/{body['assistant_message']['id']}/csv")
    assert csv.status_code == 200 and csv.text.splitlines()[0].startswith("region")

    rep = client.get(f"/api/datasets/{dataset_id}/report", params={"conversation_id": cid})
    assert rep.status_code == 200 and "Key findings" in rep.text and "SQL used" in rep.text
    assert "attachment" in rep.headers["content-disposition"]
    assert client.delete(f"/api/conversations/{cid}").status_code == 204


def test_chat_self_repair_and_unsafe_sql(client, dataset_id):
    for marker in ("BREAKIT", "EVIL"):
        r = client.post("/api/chat", json={"dataset_id": dataset_id, "message": f"{marker} total"})
        assert r.status_code == 200
        qs = r.json()["assistant_message"]["payload"]["queries"]
        assert qs[0]["ok"] is False and qs[-1]["ok"] is True


def test_chat_validation_and_rate_limit(client, dataset_id):
    assert client.post("/api/chat", json={"dataset_id": dataset_id, "message": ""}).status_code == 422
    assert client.post("/api/chat", json={"dataset_id": "nope", "message": "hi"}).status_code == 404
    assert client.post("/api/chat", json={"dataset_id": dataset_id, "message": "x" * 3000}).status_code == 422
    codes = [client.post("/api/chat", json={"dataset_id": dataset_id, "message": "UNRELATED"}).status_code for _ in range(22)]
    assert codes.count(429) >= 1


def test_chat_without_api_key(client, dataset_id, monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "llm_provider", "groq")
    r = client.post("/api/chat", json={"dataset_id": dataset_id, "message": "hello"})
    assert r.status_code == 503 and "GROQ_API_KEY" in r.json()["detail"]
