"""httpx.AsyncClient helpers — per-call timeout enforcement.

Catches a common mistake where learners set the timeout at the session
level (once across the whole AsyncClient lifecycle) instead of per
.get/.post call. The session-level timeout still applies but does not
fire per call, so slow upstreams can starve faster ones.
"""
import json
import logging
import time

import httpx

logger = logging.getLogger(__name__)


def _latency_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 3)


async def call_upstream(service: str, url: str, payload: dict, timeout_s: float = 5.0):
    """Call one upstream service. Returns an UpstreamResult-shaped dict.

    Per-call timeout via `httpx.Timeout(timeout_s)` on `.post`.
    """
    start = time.perf_counter()
    status = "error"
    error = None
    payload_json = None

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s)) as client:
            response = await client.post(url, json=payload)
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            elif getattr(response, "status_code", 200) >= 400:
                raise httpx.HTTPStatusError(
                    f"upstream returned {response.status_code}",
                    request=None,
                    response=response,
                )
            payload_json = response.json()
            status = "ok"
    except httpx.TimeoutException as exc:
        status = "timeout"
        error = str(exc) or "upstream timed out"
    except httpx.HTTPStatusError as exc:
        status = "error"
        error = f"upstream returned {exc.response.status_code}"
    except Exception as exc:
        status = "error"
        error = str(exc)

    result = {
        "service": service,
        "status": status,
        "latency_ms": _latency_ms(start),
        "payload": payload_json,
        "error": error,
    }
    logger.info(json.dumps({
        "event": "upstream_call",
        "service": service,
        "status": status,
        "latency_ms": result["latency_ms"],
    }))
    return result
