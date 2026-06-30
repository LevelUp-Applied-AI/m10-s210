"""Multi-service coordinator for the Module 10 stretch stack."""
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
except ImportError:  # Allows `uvicorn main:app` inside the container.
    from models import AnswerRequest, AnswerResponse
    from upstream import call_upstream

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("coordinator")

CLASSIFIER_URL = os.getenv("CLASSIFIER_URL", "http://classifier_svc:8000/classify")
SERVICE_URLS = {
    "nlp_svc": os.getenv("NLP_URL", "http://nlp_svc:8000/extract"),
    "kg_svc": os.getenv("KG_URL", "http://kg_svc:8000/kg/query"),
    "rag_svc": os.getenv("RAG_URL", "http://rag_svc:8000/rag/answer"),
}
SERVICE_TIMEOUTS = {
    "nlp_svc": float(os.getenv("NLP_TIMEOUT_S", "5")),
    "kg_svc": float(os.getenv("KG_TIMEOUT_S", "5")),
    "rag_svc": float(os.getenv("RAG_TIMEOUT_S", "10")),
}

app = FastAPI(title="Stretch Thu - Multi-Service Coordinator")


async def classify_question(question: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
        response = await client.post(CLASSIFIER_URL, json={"question": question})
    if getattr(response, "status_code", 200) >= 400:
        raise HTTPException(status_code=503, detail="classifier_svc unavailable")
    routes = response.json().get("routes", [])
    return [route for route in routes if route.get("service") in SERVICE_URLS]


@app.post("/answer", response_model=AnswerResponse)
async def answer(req: AnswerRequest):
    """Classify, fan out to selected services, aggregate, and respond."""
    start = time.perf_counter()
    try:
        routes = await classify_question(req.question)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"classifier_svc": str(exc)}) from exc

    if not routes:
        routes = [{"service": "rag_svc", "confidence": 0.0}]

    selected = []
    for route in routes:
        service = route["service"]
        if service not in selected:
            selected.append(service)

    payload = {"question": req.question}
    calls = [
        call_upstream(
            service,
            SERVICE_URLS[service],
            payload,
            timeout_s=SERVICE_TIMEOUTS.get(service, 5.0),
        )
        for service in selected
    ]
    upstream_results = await asyncio.gather(*calls)

    results = {}
    responded = []
    failures = {}
    for result in upstream_results:
        if hasattr(result, "model_dump"):
            result = result.model_dump()
        service = result["service"]
        body = result.get("payload", result.get("result"))
        if result.get("status") == "ok":
            results[service] = body
            responded.append(service)
        else:
            results[service] = None
            failures[service] = {
                "status": result.get("status"),
                "error": result.get("error"),
            }

    latency_ms = round((time.perf_counter() - start) * 1000, 2)
    logger.info(
        json.dumps(
            {
                "event": "answer_request",
                "called": selected,
                "responded": responded,
                "partial": bool(failures and responded),
                "latency_ms": latency_ms,
            }
        )
    )

    if failures and not responded:
        raise HTTPException(status_code=503, detail={"failed": failures, "responded": []})

    return AnswerResponse(
        results=results,
        partial=bool(failures and responded),
        responded=responded,
    )


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
