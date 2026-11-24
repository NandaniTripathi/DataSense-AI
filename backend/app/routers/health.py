from fastapi import APIRouter

from app.core.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    s = get_settings()
    return {"status": "ok", "app": s.app_name, "llm_configured": s.llm_configured,
            "model": s.groq_model if s.llm_provider != "mock" else "mock-llm"}
