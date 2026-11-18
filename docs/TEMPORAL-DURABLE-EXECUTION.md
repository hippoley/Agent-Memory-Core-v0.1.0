# Temporal Durable Execution — Local Runtime

## What survives a failure

The `extended` local profile runs the Temporal development server and the
Agent Memory Core Temporal Worker as separate containers.

Temporal Workflow History is stored in the server's SQLite database. The
database file is placed under `/tmp`, which is backed by the named Docker
volume `agent-memory-core-temporal-dev-data`. Therefore:

- Restarting the Worker does not erase Workflow History.
- Recreating the Temporal server container does not erase Workflow History,
  provided the named volume is retained.
- Restarting the application/API does not erase Workflow History.
- A temporary network interruption can be recovered after connectivity returns;
  in-flight Activities may be retried and must therefore be idempotent.
- `docker compose down -v`, deleting the named volume, deleting Docker's
  volume data, or losing the host disk destroys this local history unless it
  has been backed up.

This is a single-machine developer setup. Temporal's SQLite development
server is **not** the production persistence topology. Production requires a
supported Temporal Server deployment with external persistence, retention,
TLS, credentials, monitoring and tested backups.

## Start it

From the repository root:

```bash
python scripts/launch_local.py up --profile extended
python scripts/launch_local.py status --profile extended
```

The extended profile includes the PostgreSQL-backed API, projection worker,
Temporal server and Temporal Worker. Temporal UI is available at
http://127.0.0.1:8233 and the gRPC endpoint at localhost:7233.

To inspect the services:

```bash
docker compose --env-file .env.local -f compose.yaml -f compose.app.yaml -f compose.worker.yaml -f compose.temporal.yaml ps
docker compose --env-file .env.local -f compose.yaml -f compose.app.yaml -f compose.worker.yaml -f compose.temporal.yaml logs -f memory-temporal memory-temporal-worker
```

## Restart without losing history

Restart only the Worker:

```bash
docker compose --env-file .env.local -f compose.yaml -f compose.app.yaml -f compose.worker.yaml -f compose.temporal.yaml restart memory-temporal-worker
```

Recreate the Temporal server container while preserving the named volume:

```bash
docker compose --env-file .env.local -f compose.yaml -f compose.app.yaml -f compose.worker.yaml -f compose.temporal.yaml up -d --force-recreate memory-temporal
```

Do not add `-v` to `docker compose down` when you intend to retain local
Workflow History.

## Back up local Workflow History

Stop the Temporal server first so SQLite is not being copied while writes are
in progress:

```bash
docker compose --env-file .env.local -f compose.yaml -f compose.app.yaml -f compose.worker.yaml -f compose.temporal.yaml stop memory-temporal
mkdir -p backups
docker run --rm -v agent-memory-core-temporal-dev-data:/data -v "${PWD}/backups:/backup" alpine:3.20 sh -c "tar -czf /backup/temporal-dev-data.tgz -C /data ."
docker compose --env-file .env.local -f compose.yaml -f compose.app.yaml -f compose.worker.yaml -f compose.temporal.yaml start memory-temporal
```

Keep the archive outside the Docker volume and protect it as operational data.
A backup is not proven recoverable until it has been restored into a separate
volume and the Temporal server can read the retained Workflow History.

## Important execution semantics

Temporal replays Workflow code from persisted Event History. It does not
serialize arbitrary Python process memory or resume a Python Activity at the
exact instruction where its process died.

- Completed Activities are replayed from their recorded results.
- An Activity that was running when its Worker died can be attempted again
  according to its retry policy.
- Long-running Activities must heartbeat progress and implement cancellation.
- Side effects must use stable idempotency keys or transactional outbox writes.
- If an Activity has an external side effect and crashes after the side effect
  but before reporting completion to Temporal, that Activity may run again.
  The external system must deduplicate the repeated request.

Current Agent Memory Core `VerifyMemoryWorkflow` is a two-Activity workflow
(claim verification and verification-receipt write). It now exposes durable
stage progress (queued, verifying, writing receipt, completed/failed) and
cooperative pause/resume signals. Pause takes effect only at safe stage
boundaries; it does not stop an Activity mid-transaction. The API and SDK expose
progress, pause and resume operations.

This is still not a generic checkpointed long-running task engine. Arbitrary
Activity heartbeat details, persisted domain checkpoints for multi-minute
external operations, operator-driven retry-from-checkpoint, and fault-injected
SIGKILL recovery remain unimplemented and must not be inferred from this
two-step workflow.

## Current guarantee boundary

| Failure | Local extended profile |
|---|---|
| API container restart | Workflow History retained |
| Worker restart / replacement | Workflow History retained; pending work can be polled again |
| Temporal container replacement | History retained by named volume |
| Temporary network loss | Work can resume after connectivity returns; Activity retry semantics apply |
| Host reboot | Expected to retain data if Docker volume survives; verify on the target host |
| Docker volume deletion / host disk loss | Not protected without external backup |
| Production HA / multi-node Temporal | Not provided by this local profile |


## Worker process crash acceptance

The dedicated Temporal CI workflow also runs
`tests/test_temporal_worker_crash.py`. It starts a real Worker in a separate
OS process, starts a long-running heartbeat-enabled Activity, sends SIGKILL to
that process, then starts a replacement Worker on the same task queue. The
acceptance requires Temporal to time out the lost Activity attempt, dispatch a
retry to the replacement process, and complete the original Workflow ID with
attempt 2.

This proves process-level Activity retry and replacement-worker recovery. It
does not yet prove arbitrary business checkpoint resume inside an Activity:
the demo Activity is deliberately restartable and the workflow's idempotent
side-effect requirements still apply to production Activities.
