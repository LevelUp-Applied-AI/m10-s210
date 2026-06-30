"""Rules-based query classifier for the stretch coordinator."""
import re

from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="classifier_svc")


class ClassifyRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)


NLP_KEYWORDS = {
    "extract",
    "entity",
    "entities",
    "name",
    "names",
    "email",
    "emails",
    "phone",
    "text",
}
KG_KEYWORDS = {"recipes", "recipe", "cuisine", "ingredient", "ingredients", "chef", "find", "graph"}
RAG_KEYWORDS = {"how", "why", "what", "prep", "prepare", "cook", "make", "explain", "summarize"}


def score(tokens: set[str], keywords: set[str]) -> float:
    return len(tokens & keywords) / max(len(keywords), 1)


@app.post("/classify")
async def classify(payload: ClassifyRequest | dict):
    if isinstance(payload, dict):
        question = payload.get("question") or ""
    else:
        question = payload.question

    tokens = set(re.findall(r"[a-z0-9']+", question.lower()))
    candidates = [
        ("nlp_svc", score(tokens, NLP_KEYWORDS)),
        ("kg_svc", score(tokens, KG_KEYWORDS)),
        ("rag_svc", score(tokens, RAG_KEYWORDS)),
    ]
    routes = [
        {"service": service, "confidence": round(confidence, 3)}
        for service, confidence in candidates
        if confidence > 0
    ]
    if not routes:
        routes.append({"service": "rag_svc", "confidence": 0.0})
    return {"routes": routes}


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
