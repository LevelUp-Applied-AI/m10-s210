"""Multi-service coordinator — Stretch Thu (Honors Track).

The coordinator exposes a single POST /answer endpoint. On each call it:
1. Calls the classifier service to identify which downstream service(s)
   should answer the question.
2. Fans out to the selected service(s) via httpx.AsyncClient with a
   per-call 5-second timeout.
3. Aggregates the responses and returns a single AnswerResponse.
4. If any upstream returns a 5xx or times out, the coordinator returns
   200 with `partial: true` and a per-service attribution payload —
   never a 5xx that would lose the working upstream's response.
"""
import asyncio
import json
import logging
import os
import time

import httpx
from fastapi import FastAPI, HTTPException

try:
    from .models import AnswerRequest, AnswerResponse
    from .upstream import call_upstream
except ImportError:  # pragma: no cover - used when Docker runs `main:app`
    from models import AnswerRequest, AnswerResponse
    from upstream import call_upstream

app = FastAPI(title="Stretch Thu — Multi-Service Coordinator")
logger = logging.getLogger(__name__)

CLASSIFIER_URL = os.getenv("CLASSIFIER_URL", "http://classifier_svc:8000/classify")
SERVICE_URLS = {
    "nlp_svc": os.getenv("NLP_SVC_URL", "http://nlp_svc:8000/extract"),
    "kg_svc": os.getenv("KG_SVC_URL", "http://kg_svc:8000/kg/query"),
    "rag_svc": os.getenv("RAG_SVC_URL", "http://rag_svc:8000/rag/answer"),
}
SERVICE_TIMEOUTS = {"rag_svc": 10.0}


@app.post("/answer", response_model=AnswerResponse)
async def answer(req: AnswerRequest):
    """Classify → fan out → aggregate → respond.

    Returns AnswerResponse. `partial: true` iff one or more upstreams
    failed or timed out.
    """
    start = time.perf_counter()
    routes = await classify_question(req.question)
    services = [route["service"] for route in routes if route.get("service") in SERVICE_URLS]
    if not services:
        services = ["rag_svc"]

    payload = {"question": req.question, "text": req.question}
    calls = [
        call_upstream(
            service,
            SERVICE_URLS[service],
            payload,
            timeout_s=SERVICE_TIMEOUTS.get(service, 5.0),
        )
        for service in services
    ]
    upstream_results = await asyncio.gather(*calls)
    responded = [
        item["service"] for item in upstream_results if item.get("status") == "ok"
    ]

    if not responded:
        detail = {
            "message": "all upstream services failed",
            "results": {item["service"]: item for item in upstream_results},
            "responded": [],
        }
        _log_request(req.question, services, responded, start, partial=True)
        raise HTTPException(status_code=503, detail=detail)

    failed = len(responded) != len(upstream_results)
    results = {
        item["service"]: item.get("payload") if item.get("status") == "ok" else item
        for item in upstream_results
    }
    _log_request(req.question, services, responded, start, partial=failed)
    return AnswerResponse(results=results, partial=failed, responded=responded)


async def classify_question(question: str):
    async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
        response = await client.post(CLASSIFIER_URL, json={"question": question})
        response.raise_for_status()
    routes = response.json().get("routes", [])
    return routes or [{"service": "rag_svc", "confidence": 0.0}]


def _log_request(question: str, called: list[str], responded: list[str], start: float, partial: bool):
    logger.info(json.dumps({
        "event": "coordinator_request",
        "question_len": len(question),
        "called": called,
        "responded": responded,
        "partial": partial,
        "latency_ms": round((time.perf_counter() - start) * 1000, 3),
    }))


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
