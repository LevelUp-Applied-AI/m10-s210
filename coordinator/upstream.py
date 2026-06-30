"""httpx.AsyncClient helpers for service-to-service calls."""
import json
import logging
import time

import httpx

logger = logging.getLogger("coordinator.upstream")


async def call_upstream(service: str, url: str, payload: dict, timeout_s: float = 5.0):
    """Call one upstream service and return an UpstreamResult-shaped dict."""
    start = time.perf_counter()

    def finish(status: str, payload_body=None, error: str | None = None):
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        result = {
            "service": service,
            "status": status,
            "latency_ms": latency_ms,
            "payload": payload_body,
            "error": error,
        }
        logger.info(
            json.dumps(
                {
                    "event": "upstream_call",
                    "service": service,
                    "status": status,
                    "latency_ms": latency_ms,
                }
            )
        )
        return result

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s)) as client:
            response = await client.post(url, json=payload)
    except httpx.TimeoutException:
        return finish("timeout", error=f"{service} timed out after {timeout_s}s")
    except Exception as exc:
        return finish("error", error=str(exc))

    status_code = getattr(response, "status_code", 200)
    try:
        body = response.json()
    except Exception as exc:
        return finish("error", error=f"invalid JSON from {service}: {exc}")

    if status_code >= 400:
        return finish("error", payload_body=body, error=f"{service} returned HTTP {status_code}")

    return finish("ok", payload_body=body)
