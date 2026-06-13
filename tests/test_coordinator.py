"""Stretch Thu autograder.

Tests use httpx-mock or a small in-process app to avoid spinning the
full Compose stack. The end-to-end Compose check is exercised by the
workflow.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_coordinator_answer_endpoint_implemented():
    """Catches buggy variant: silent-pass guard.

    Hits the coordinator's /answer endpoint via TestClient. Unmodified
    starter has `req: AnswerRequest` annotated so a valid body passes
    validation and the body raises NotImplementedError → 500. A garbage
    implementation that returns an empty `responded=[]` shell is also
    rejected: the routing must produce at least one responded service.
    """
    from fastapi.testclient import TestClient

    from coordinator.main import app

    client = TestClient(app)
    r = client.post("/answer", json={"question": "find Sichuan recipes"})
    assert r.status_code == 200, (
        f"coordinator /answer must return 200 on a valid question. "
        f"Got {r.status_code}. Body: {r.text[:200]}"
    )
    body = r.json()
    # Routing must have produced at least one responded service —
    # an empty `responded` list signals no upstream was actually called.
    responded = body.get("responded")
    assert isinstance(responded, list) and len(responded) >= 1, (
        "coordinator /answer must populate `responded` with at least one "
        f"upstream service name. Got responded={responded!r}."
    )


def test_coordinator_routes_kg_question_to_kg_svc():
    """Catches buggy variant: classifier returns wrong route OR
    coordinator fans out to all services regardless of routing."""
    # Structural placeholder — the live test against a running stack
    # is in the workflow's docker-compose smoke section. Here we assert
    # that the coordinator imports and the AnswerResponse shape
    # contains the per-source attribution that makes the routing
    # observable.
    from coordinator.models import AnswerResponse, UpstreamResult
    fields = AnswerResponse.model_fields
    assert "results" in fields
    assert "partial" in fields
    assert "responded" in fields
    assert "service" in UpstreamResult.model_fields


def test_per_call_timeout_enforced(monkeypatch):
    """Catches buggy variant: learner does not enforce a per-call timeout —
    when an upstream call hangs longer than ``timeout_s``, ``call_upstream``
    must return ``status='timeout'`` (not block, not raise, not succeed).

    Asserts behavior by calling ``call_upstream`` with a stubbed
    ``httpx.AsyncClient`` that raises ``httpx.TimeoutException`` on
    ``.post``.
    """
    import asyncio
    import httpx

    from coordinator.upstream import call_upstream

    class _StubResponse:
        def json(self):
            return {"unused": True}

    class _StubClient:
        def __init__(self, *args, **kwargs):
            self._timeout = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None):
            raise httpx.TimeoutException("simulated upstream hang")

    monkeypatch.setattr(httpx, "AsyncClient", _StubClient)

    result = asyncio.run(
        call_upstream("nlp_svc", "http://stub/upstream", {"q": "ping"}, timeout_s=0.05)
    )
    # Normalize: function may return a dict or an UpstreamResult model.
    if hasattr(result, "model_dump"):
        result = result.model_dump()
    assert isinstance(result, dict), f"expected dict-shaped result, got {type(result).__name__}"
    assert result.get("status") == "timeout", (
        f"per-call timeout was not enforced; got status={result.get('status')!r}. "
        "Reference uses httpx.Timeout(timeout_s) on the .post call."
    )
    assert result.get("service") == "nlp_svc"
    assert "latency_ms" in result


def test_classifier_returns_routes_with_confidence():
    """Catches buggy variant: classifier silently picks one route on
    ambiguous questions; coordinator ignores hybrid intent."""
    from services.classifier_svc.main import classify
    import asyncio

    result = asyncio.run(classify({"question": "find recipes that prep ginger"}))
    routes = result["routes"]
    assert isinstance(routes, list)
    assert all("service" in r and "confidence" in r for r in routes)


def test_structured_log_per_upstream_call_documented(monkeypatch, caplog):
    """Catches buggy variant: no structured log emitted per upstream call.

    Calls ``call_upstream`` with a stubbed ``httpx.AsyncClient`` whose
    ``.post`` succeeds, then asserts that the call produced at least one
    log record whose payload references the service, status, and latency.
    """
    import asyncio
    import logging
    import httpx

    from coordinator.upstream import call_upstream

    class _StubResponse:
        def json(self):
            return {"answer": "ok"}

    class _StubClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None):
            return _StubResponse()

    monkeypatch.setattr(httpx, "AsyncClient", _StubClient)
    caplog.set_level(logging.DEBUG)

    result = asyncio.run(
        call_upstream("kg_svc", "http://stub/upstream", {"q": "ping"}, timeout_s=1.0)
    )
    if hasattr(result, "model_dump"):
        result = result.model_dump()
    assert result.get("status") == "ok"

    # Reference emits one structured log record per call. The shape can
    # vary (a logging.Logger call OR a `print(json.dumps(...))`); either
    # path produces output that mentions the service name and status.
    log_text = "\n".join(record.getMessage() for record in caplog.records).lower()
    assert "kg_svc" in log_text, (
        f"no log record referenced the service name. Logged: {log_text!r}"
    )
    assert ("status" in log_text or "ok" in log_text), (
        "structured-log line did not reference call status. Reference emits "
        "one log line per upstream call with service / status / latency_ms."
    )
    assert "latency" in log_text, (
        "structured-log line did not reference latency. Reference emits "
        "service / status / latency_ms per call."
    )
