"""Automated insights, anomaly detection and the optional AI executive summary."""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.routers.datasets import get_dataset_or_404
from app.services import insights as insight_service
from app.services import storage
from app.services.llm import LLMError, get_llm
from app.services.prompts import INSIGHT_SUMMARY_SYSTEM

log = logging.getLogger("datasense.analytics")
router = APIRouter(prefix="/datasets", tags=["analytics"])


def get_or_compute_insights(db: Session, ds, force: bool = False) -> dict:
    if ds.insights and not force:
        return ds.insights
    df = storage.load_dataframe(ds.id, ds.profile)
    result = insight_service.compute_insights(df, ds.profile)
    if ds.insights and ds.insights.get("ai_summary"):
        result["ai_summary"] = ds.insights["ai_summary"]
    ds.insights = result
    db.commit()
    return result


@router.get("/{dataset_id}/insights")
def get_insights(dataset_id: str, refresh: bool = False, db: Session = Depends(get_db)):
    ds = get_dataset_or_404(db, dataset_id)
    try:
        return get_or_compute_insights(db, ds, force=refresh)
    except FileNotFoundError:
        raise HTTPException(404, "Dataset file is missing on disk.")


@router.post("/{dataset_id}/insights/ai-summary")
def generate_ai_summary(dataset_id: str, db: Session = Depends(get_db)):
    ds = get_dataset_or_404(db, dataset_id)
    insights = get_or_compute_insights(db, ds)
    try:
        llm = get_llm()
        resp = llm.create(
            system=INSIGHT_SUMMARY_SYSTEM,
            messages=[{"role": "user", "content": "Analysis results (JSON):\n" + insight_service.facts_for_llm(ds.profile, insights)}],
            max_tokens=2500,  # gpt-oss reasoning counts against this budget
        )
    except LLMError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    text = "\n".join(b["text"] for b in resp.blocks if b["type"] == "text").strip()
    if not text:
        raise HTTPException(502, "The model returned an empty summary. Try again.")
    insights = {**insights, "ai_summary": text}
    ds.insights = insights
    db.commit()
    return {"ai_summary": text}
