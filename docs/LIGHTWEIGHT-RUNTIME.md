# Lightweight Runtime — reproducible local profiles

This is a development / isolated demo deployment, **not a public multi-tenant SaaS stack**. All exposed ports bind to the host loopback, and database credentials in the existing `compose.yaml` are development-only.

## One command

Prerequisites: Python 3.10+ and Docker Compose v2 with `--wait` support.

```bash
python scripts/launch_local.py up --profile lite
```

The launcher generates `.env.local` with a random `MEMORY_API_KEY`, creates the database and API containers, applies packaged SQL migrations, waits for health, and prints:

- Console: http://127.0.0.1:8765/dashboard/
- Swagger: http://127.0.0.1:8765/docs/swagger
- Manual: http://127.0.0.1:8765/docs
- OpenAPI: http://127.0.0.1:8765/openapi.json

The credential is in the ignored `.env.local` file. Do not paste it into a public link, browser screenshot, README, issue, log, or shared demo. The database uses a development administrator role; **do not publish this API directly to the Internet**.

To switch profiles (without dropping volumes):

```bash
python scripts/launch_local.py up --profile standard
python scripts/launch_local.py up --profile full
# Explicitly start the AGE + Temporal development extension:
python scripts/launch_local.py up --profile extended
python scripts/launch_local.py status
python scripts/launch_local.py down
```

`down` does **not** delete PostgreSQL or object volume data. The launcher does not provision TLS, custom domains, backups, identity providers, RLS principals, or external model servers.

## Profile comparison

| Capability | Lite | Standard | Full | Extended |
|---|---|---|---|
| PostgreSQL canonical facts, provenance and history | Yes | Yes | Yes | Yes |
| HTTP API, Console, Swagger | Yes | Yes | Yes | Yes |
| Rule-based extraction (no LLM) | Yes | Yes | Yes | Yes |
| Local content-addressed object storage | Yes | Yes | Yes | Yes |
| pgvector retrieval flag | Off | On | On | On |
| Apache AGE retrieval flag | Off | On | On | On |
| Standalone projection worker | No | No | Yes | Yes |
| Model provider / embedding inference | Not configured | Not configured | Not configured | Not configured |
| Temporal server | Not started | Not started | Not started | Local dev server + worker |
| Redis / Kafka / Neo4j / Elasticsearch | Not required | Not required | Not required | Not required |

**Important distinction:** Lite switches off query channels at application level, but the existing combined PostgreSQL image still includes pgvector and AGE binaries and the packaged schema still creates vector tables. **This is service/topology lightweighting, not yet a measured minimal database footprint or extension-free mode.** A truly extension-free PostgreSQL deployment requires separate, verified migrations and is not advertised here.

Standard enables vector/graph capability flags but does **not** provide an embedding model. Graph/index requests can degrade and must report capability; see `GET /v1/capabilities`.

Full activates a projection worker, not a Temporal server. **Extended** explicitly adds the Temporal CLI **development** server (UI at `http://127.0.0.1:8233`) and `agent_memory_core.temporal_worker` using the packaged `VerifyMemoryWorkflow`. It also keeps PostgreSQL, AGE, pgvector and the projection worker. Temporal dev server uses a SQLite file under `/tmp` backed by the named Docker volume `agent-memory-core-temporal-dev-data`; workflow history survives container replacement while that volume is retained. This is local single-machine persistence, not production HA or external backup. PostgreSQL remains canonical memory storage and is not deleted by this. This overlay is not a production Temporal deployment and does **not** claim durable Temporal storage across recreation. The workflow worker may need a short startup retry while the dev server becomes available; inspect its logs before asserting it is ready. No model server or embedding service is installed by these profiles.

```bash
python scripts/launch_local.py up --profile extended
python scripts/launch_local.py status
# Debug if the workflow worker is not connected:
docker compose --env-file .env.local -f compose.yaml -f compose.app.yaml \
  -f compose.worker.yaml -f compose.temporal.yaml logs --tail=100 memory-temporal-worker
```

The existing verification workflow can be invoked using a Temporal client and the worker queue `agent-memory-core` (see `scripts/smoke_extended_temporal.py`). **Starting Temporal does not itself verify every claim; an authenticated REST trigger for that workflow is not yet provided.**

## Measure actual efficiency

Run on the target host after the local stack is healthy:

```bash
python scripts/benchmark_resources.py --requests 200 --concurrency 4 --output benchmark-results/lite.json
```

Repeat once per profile, **on the same machine, same dataset and same software SHA**. The script captures two Docker CPU/memory snapshots, volume usage and authenticated read-only `GET /v1/memories?limit=10` latency distribution. No synthetic measurements are inserted. The script exits nonzero if requests fail.

```bash
python scripts/launch_local.py up --profile standard
python scripts/benchmark_resources.py --requests 200 --concurrency 4 --output benchmark-results/standard.json
python scripts/launch_local.py up --profile full
python scripts/benchmark_resources.py --requests 200 --concurrency 4 --output benchmark-results/full.json
```

Do not interpret two Docker samples as peak RAM, time-averaged CPU, write latency, or semantic retrieval latency. Docker DB volume usage does not include image layers. Host hardware, dataset size, query workload, network locality and model configuration must accompany any published benchmark.

Existing opt-in **write** load test: `scripts/benchmark_http.py`. It deliberately requires `--allow-write`, uses unique test facts, and should only target a disposable local tenant.

## Acceptance checklist

1. `docker compose` starts DB and API and reports both healthy.
2. `GET /v1/health` and `GET /openapi.json` return successfully.
3. `GET /v1/memories` without a bearer token is rejected.
4. A private test key can write and recall a fact through the API.
5. Profile switching does not erase existing data.
6. Resource script writes a report containing non-null measured RAM, CPU, disk and P95 values with the target host and profile.
7. Same-SHA CI validates the starter; real host results must be collected separately for any performance claims.

No performance gate is currently marked passed until measured. For the canonical correctness / model-quality acceptance gates, see [User Story Acceptance](USER-STORY-ACCEPTANCE.md).
