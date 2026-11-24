"""Dataset upload, listing, preview and deletion."""
import io
import logging
import re
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Dataset
from app.db.session import get_db
from app.schemas import DatasetDetail, DatasetOut
from app.services import ingest, profiling, samples, storage

log = logging.getLogger("datasense.datasets")
router = APIRouter(prefix="/datasets", tags=["datasets"])


def get_dataset_or_404(db: Session, dataset_id: str) -> Dataset:
    ds = db.get(Dataset, dataset_id) if dataset_id.isalnum() else None
    if ds is None:
        raise HTTPException(404, "Dataset not found.")
    return ds


def _register(db: Session, name: str, filename: str, df, size_bytes: int) -> Dataset:
    profile = profiling.profile_dataframe(df)
    ds = Dataset(name=name, filename=filename, n_rows=profile["n_rows"], n_cols=profile["n_cols"],
                 size_bytes=size_bytes, profile=profile)
    db.add(ds)
    db.flush()  # assigns ds.id
    try:
        storage.save_dataframe(ds.id, df)
    except Exception:
        db.rollback()
        storage.delete_dataset_files(ds.id)
        log.exception("Failed to store dataset")
        raise HTTPException(500, "Could not store the dataset.")
    db.commit()
    return ds


@router.post("", response_model=DatasetDetail, status_code=201)
async def upload_dataset(file: UploadFile = File(...), name: str | None = Form(default=None),
                         db: Session = Depends(get_db)):
    settings = get_settings()
    limit = settings.max_upload_mb * 1024 * 1024
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(413, f"File is larger than the {settings.max_upload_mb} MB limit.")
    filename = Path(file.filename or "upload").name
    try:
        df = ingest.read_upload(filename, content)
    except ingest.IngestError as exc:
        raise HTTPException(422, str(exc)) from exc
    display = (name or Path(filename).stem).strip()[:200] or "Untitled dataset"
    return _register(db, display, filename, df, len(content))


@router.post("/sample", response_model=DatasetDetail, status_code=201)
def create_sample(db: Session = Depends(get_db)):
    df = ingest.clean_dataframe(samples.generate_sales_demo())
    return _register(db, "Retail sales demo", "retail_sales_demo.csv", df, 0)


@router.get("", response_model=list[DatasetOut])
def list_datasets(db: Session = Depends(get_db)):
    return db.scalars(select(Dataset).order_by(Dataset.created_at.desc())).all()


@router.get("/{dataset_id}", response_model=DatasetDetail)
def get_dataset(dataset_id: str, db: Session = Depends(get_db)):
    return get_dataset_or_404(db, dataset_id)


@router.delete("/{dataset_id}", status_code=204)
def delete_dataset(dataset_id: str, db: Session = Depends(get_db)):
    ds = get_dataset_or_404(db, dataset_id)
    db.delete(ds)
    db.commit()
    storage.delete_dataset_files(dataset_id)


@router.get("/{dataset_id}/preview")
def preview(dataset_id: str, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200),
            sort_by: str | None = None, sort_dir: str = Query("asc", pattern="^(asc|desc)$"),
            search: str | None = Query(None, max_length=100), db: Session = Depends(get_db)):
    ds = get_dataset_or_404(db, dataset_id)
    try:
        return storage.preview_rows(dataset_id, ds.profile, page, page_size, sort_by, sort_dir, search)
    except FileNotFoundError:
        raise HTTPException(404, "Dataset file is missing on disk.")
