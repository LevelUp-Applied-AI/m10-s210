# Module 10 Stretch - Multi-Service Coordinator

This repo implements the Honors Track stretch stack: three independent backend services, a rules-based query classifier, and a coordinator that routes requests, fans out concurrently, aggregates responses, and handles partial upstream failure.

## Architecture

The coordinator exposes `POST /answer`. For each request it calls `classifier_svc` at `POST /classify`, reads the returned `routes`, calls only those backend services, and returns:

```json
{
  "results": {
    "kg_svc": {"rows": []},
    "rag_svc": {"answer": "..."}
  },
  "partial": false,
  "responded": ["kg_svc", "rag_svc"]
}
```

Backend endpoints:

- `nlp_svc`: `POST /extract`
- `kg_svc`: `POST /kg/query`
- `rag_svc`: `POST /rag/answer`
- `classifier_svc`: `POST /classify`
- `coordinator`: `POST /answer`

The service split keeps endpoint lifecycles independent and makes routing, scaling, and failure isolation explicit. The cost is extra operational complexity: Dockerfiles, healthchecks, service discovery, timeout handling, and duplicated request/health/error plumbing in each service.

## Run Locally

Install test dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run unit tests:

```bash
pytest
```

Start the full stack:

```bash
docker compose up -d --build
docker compose ps
```

Send a KG-shaped request:

```bash
curl -s http://localhost:8000/answer \
  -H "content-type: application/json" \
  -d '{"question":"find recipes with ginger"}'
```

Send a RAG-shaped request:

```bash
curl -s http://localhost:8000/answer \
  -H "content-type: application/json" \
  -d '{"question":"how do I prep ginger for cooking?"}'
```

Send a hybrid request:

```bash
curl -s http://localhost:8000/answer \
  -H "content-type: application/json" \
  -d '{"question":"find recipes that explain how to prep ginger"}'
```

## Partial-Failure Demo

Stop the RAG service:

```bash
docker compose stop rag_svc
```

Call a hybrid question that normally routes to both KG and RAG:

```bash
curl -s http://localhost:8000/answer \
  -H "content-type: application/json" \
  -d '{"question":"find recipes that explain how to prep ginger"}'
```

Expected shape:

```json
{
  "results": {
    "kg_svc": {"cypher": "MATCH (n) RETURN n", "rows": [], "count": 0, "service": "kg_svc"},
    "rag_svc": null
  },
  "partial": true,
  "responded": ["kg_svc"]
}
```

Bring RAG back:

```bash
docker compose start rag_svc
```

## Observability

The coordinator emits structured JSON log lines:

- `event=upstream_call` for each upstream request, including `service`, `status`, and `latency_ms`.
- `event=answer_request` for each inbound request, including services called, services that responded, whether the response was partial, and total latency.

View logs:

```bash
docker compose logs -f coordinator
```
