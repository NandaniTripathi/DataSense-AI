"""Downloadable analytical report."""
import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db.models import Conversation
from app.db.session import get_db
from app.routers.analytics import get_or_compute_insights
from app.routers.datasets import get_dataset_or_404
from app.services.report import build_report

router = APIRouter(prefix="/datasets", tags=["reports"])


@router.get("/{dataset_id}/report")
def download_report(dataset_id: str, conversation_id: str | None = None, db: Session = Depends(get_db)):
    ds = get_dataset_or_404(db, dataset_id)
    convo = None
    if conversation_id:
        convo = db.get(Conversation, conversation_id)
        if convo is None or convo.dataset_id != ds.id:
            raise HTTPException(404, "Conversation not found for this dataset.")
    insights = get_or_compute_insights(db, ds)
    html_doc = build_report(ds, insights, convo)
    slug = re.sub(r"[^a-z0-9]+", "-", ds.name.lower()).strip("-") or "report"
    return Response(html_doc, media_type="text/html; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="datasense-{slug}-report.html"'})
