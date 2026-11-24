"""AI analyst chat: ask questions, keep conversations, export results."""
import io
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.ratelimit import check_chat_rate_limit
from app.db.models import Conversation, Message
from app.db.session import get_db
from app.routers.datasets import get_dataset_or_404
from app.schemas import ChatRequest, ConversationDetail, ConversationOut, MessageOut
from app.services import query_engine
from app.services.llm import LLMError, get_llm
from app.services.sqlite_runner import QueryExecutionError, run_query
from app.services.sql_guard import UnsafeSQLError

log = logging.getLogger("datasense.chat")
router = APIRouter(tags=["chat"])


def _title_from(question: str) -> str:
    q = " ".join(question.split())
    return q if len(q) <= 60 else q[:57] + "..."


@router.post("/chat")
def chat(req: ChatRequest, request: Request, db: Session = Depends(get_db)):
    check_chat_rate_limit(request)
    ds = get_dataset_or_404(db, req.dataset_id)
    try:
        llm = get_llm()  # raises a friendly error if the API key is missing
    except LLMError as exc:
        raise HTTPException(exc.status, str(exc)) from exc

    if req.conversation_id:
        convo = db.get(Conversation, req.conversation_id)
        if convo is None or convo.dataset_id != ds.id:
            raise HTTPException(404, "Conversation not found for this dataset.")
    else:
        convo = Conversation(dataset_id=ds.id, title=_title_from(req.message))
        db.add(convo)
        db.flush()

    history = [{"role": m.role, "content": m.content, "payload": m.payload} for m in convo.messages]
    try:
        answer = query_engine.answer_question(llm, ds.id, ds.profile, history, req.message.strip())
    except LLMError as exc:
        db.rollback()
        raise HTTPException(exc.status, str(exc)) from exc
    except Exception:
        db.rollback()
        log.exception("Unexpected error while answering")
        raise HTTPException(500, "Something went wrong while analysing your question. Please try again.")

    user_msg = Message(conversation_id=convo.id, role="user", content=req.message.strip())
    db.add(user_msg)
    db.flush()
    assistant_msg = Message(conversation_id=convo.id, role="assistant", content=answer["summary"], payload=answer)
    db.add(assistant_msg)
    db.commit()
    return {
        "conversation_id": convo.id,
        "title": convo.title,
        "user_message": MessageOut.model_validate(user_msg),
        "assistant_message": MessageOut.model_validate(assistant_msg),
    }


@router.get("/datasets/{dataset_id}/conversations", response_model=list[ConversationOut])
def list_conversations(dataset_id: str, db: Session = Depends(get_db)):
    get_dataset_or_404(db, dataset_id)
    return db.scalars(
        select(Conversation).where(Conversation.dataset_id == dataset_id).order_by(Conversation.created_at.desc())
    ).all()


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(conversation_id: str, db: Session = Depends(get_db)):
    convo = db.get(Conversation, conversation_id)
    if convo is None:
        raise HTTPException(404, "Conversation not found.")
    return convo


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str, db: Session = Depends(get_db)):
    convo = db.get(Conversation, conversation_id)
    if convo is None:
        raise HTTPException(404, "Conversation not found.")
    db.delete(convo)
    db.commit()


@router.get("/messages/{message_id}/csv")
def download_result_csv(message_id: int, db: Session = Depends(get_db)):
    """Re-run the stored (validated) SQL and return the full result as CSV."""
    msg = db.get(Message, message_id)
    sql = (msg.payload or {}).get("sql") if msg else None
    if not sql:
        raise HTTPException(404, "No query result stored for this message.")
    try:
        result = run_query(msg.conversation.dataset_id, sql)
    except (UnsafeSQLError, QueryExecutionError) as exc:
        raise HTTPException(422, str(exc)) from exc
    buf = io.StringIO()
    result.df.to_csv(buf, index=False)
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="datasense_result_{message_id}.csv"'})
