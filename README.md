# Stretch Thu — Multi-Service Coordinator (Honors Track)

> Honors Track — for learners who have completed all core Module 10
> assignments, are On Track or Advanced, and are attending consistently.

Decompose the Module 10 Lab's monolithic backend into three downstream
microservices (NLP, KG, RAG), add a query-classifier service, and add
a coordinator service that fans out to the selected downstream(s) and
handles partial failure gracefully.

> Read the full spec on the cohort site:
> <https://LevelUp-Applied-AI.github.io/aispire-14005-pages/modules/module-10/14bc816e>

## Submission

PR URL pasted into TalentLMS → Module 10 → Stretch Thu.

## Coordinator Design

The coordinator exposes `POST /answer`. For each question it calls
`classifier_svc` to choose one or more downstream services, fans out to
those services concurrently with `httpx.AsyncClient`, and aggregates the
per-service payloads into one response. RAG calls get a 10 second timeout
because generation can dominate latency; NLP and KG calls use 5 seconds.
If at least one selected upstream succeeds and another fails or times
out, the coordinator returns HTTP 200 with `partial: true` and lists the
successful services in `responded`. If every selected upstream fails, the
coordinator returns HTTP 503 with structured failure detail.

## Microservice Split

Splitting NLP, KG, and RAG gives the coordinator independent scaling,
failure isolation, and explicit routing telemetry for each tool-shaped
backend. The cost is more operational surface area: five containers,
healthchecks, service-to-service timeouts, and duplicated API concerns
that would be simpler inside one FastAPI process.

## Runbook

Start the five-service stack:

```bash
docker compose up -d --build
docker compose ps
```

Expected result: `coordinator`, `classifier_svc`, `nlp_svc`, `kg_svc`,
and `rag_svc` are all `healthy`.

Ask a KG-shaped question:

```bash
curl -s http://localhost:8000/answer \
  -H 'content-type: application/json' \
  -d '{"question":"find recipes by ingredient"}' | python -m json.tool
```

Ask a RAG-shaped question:

```bash
curl -s http://localhost:8000/answer \
  -H 'content-type: application/json' \
  -d '{"question":"how do I prep ginger?"}' | python -m json.tool
```

Ask a hybrid question:

```bash
curl -s http://localhost:8000/answer \
  -H 'content-type: application/json' \
  -d '{"question":"find recipes that prep ginger"}' | python -m json.tool
```

Run the partial-failure demo by stopping RAG while KG still responds:

```bash
docker compose stop rag_svc
curl -s http://localhost:8000/answer \
  -H 'content-type: application/json' \
  -d '{"question":"find recipes that prep ginger"}' | python -m json.tool
docker compose start rag_svc
```

Expected partial-failure output shape:

```json
{
  "results": {
    "kg_svc": {
      "cypher": "MATCH (n) RETURN n",
      "rows": [],
      "count": 0,
      "service": "kg_svc"
    },
    "rag_svc": {
      "service": "rag_svc",
      "status": "error",
      "latency_ms": 0.0,
      "payload": null,
      "error": "connection error"
    }
  },
  "partial": true,
  "responded": [
    "kg_svc"
  ]
}
```

The exact `latency_ms` and `error` text will vary. Coordinator logs emit
one JSON line per inbound request with `called`, `responded`, `partial`,
and total `latency_ms`; upstream helper logs include `service`, `status`,
and per-call `latency_ms`.

---

## License

This repository is provided for educational use only. See
[LICENSE](LICENSE) for terms.
