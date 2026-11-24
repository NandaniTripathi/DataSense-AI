"""Pydantic request/response models."""
from datetime import datetime

from pydantic import BaseModel, Field


class DatasetOut(BaseModel):
    id: str
    name: str
    filename: str
    n_rows: int
    n_cols: int
    size_bytes: int
    created_at: datetime

    model_config = {"from_attributes": True}


class DatasetDetail(DatasetOut):
    profile: dict


class ChatRequest(BaseModel):
    dataset_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, max_length=64)


class ConversationOut(BaseModel):
    id: str
    dataset_id: str
    title: str
    created_at: datetime

    model_config = {"from_attributes": True}


class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    payload: dict | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]
