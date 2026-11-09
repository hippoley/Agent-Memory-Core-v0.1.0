"""Independent per-destination projection delivery over PostgreSQL.

A canonical outbox event is fanned out by a database trigger into one durable
job per projection. Each destination has its own acknowledgement, retry and
dead-letter state. Handlers must use the supplied connection and must not
commit; remote effects still require destination-side idempotency.
"""
from __future__ import annotations


class ProjectionWorker:
    def __init__(self, conn, *, max_attempts: int = 8,
                 base_retry_seconds: float = 2.0,
                 max_retry_seconds: float = 300.0):
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if base_retry_seconds <= 0 or max_retry_seconds < base_retry_seconds:
            raise ValueError("Invalid retry interval bounds")
        self.conn = conn
        self.max_attempts = max_attempts
        self.base_retry_seconds = base_retry_seconds
        self.max_retry_seconds = max_retry_seconds

    def consume(self, *, projection_name: str, handler, limit: int = 20,
                tenant_id: str | None = None) -> int:
        if not projection_name or not (1 <= limit <= 100):
            raise ValueError("Projection name and limit required")
        if tenant_id is not None and not tenant_id:
            raise ValueError("tenant_id cannot be empty")
        tenant_clause = " AND tenant_id=%s" if tenant_id is not None else ""
        done = 0
        for _ in range(limit):
            event_id = None
            attempts = 0
            try:
                with self.conn.transaction():
                    row = self.conn.execute(
                        """SELECT event_id,tenant_id,aggregate_id,payload,attempts
                           FROM memory_projection_jobs
                           WHERE projection_name=%s AND status IN ('pending','retry')
                             AND available_at <= clock_timestamp()
                           """ + tenant_clause + """
                           ORDER BY created_at,event_id
                           LIMIT 1 FOR UPDATE SKIP LOCKED""",
                        ((projection_name, tenant_id) if tenant_id is not None else (projection_name,)),
                    ).fetchone()
                    if row is None:
                        break
                    event_id, tenant, aggregate, payload, attempts = row
                    handler(self.conn, tenant, str(event_id), aggregate, payload)
                    self.conn.execute(
                        """UPDATE memory_projection_jobs
                           SET status='succeeded',attempts=attempts+1,
                               processed_at=clock_timestamp(),
                               last_attempt_at=clock_timestamp(),
                               updated_at=clock_timestamp(),last_error=NULL
                           WHERE event_id=%s AND projection_name=%s""",
                        (event_id, projection_name),
                    )
                done += 1
            except Exception as exc:
                if event_id is None:
                    raise
                exhausted = attempts + 1 >= self.max_attempts
                delay = min(
                    self.base_retry_seconds * (2 ** max(0, attempts)),
                    self.max_retry_seconds,
                )
                with self.conn.transaction():
                    self.conn.execute(
                        """UPDATE memory_projection_jobs
                           SET status=CASE WHEN %s THEN 'dead_letter' ELSE 'retry' END,
                               attempts=attempts+1,
                               available_at=clock_timestamp()+(%s * interval '1 second'),
                               last_attempt_at=clock_timestamp(),
                               dead_lettered_at=CASE WHEN %s
                                 THEN clock_timestamp() ELSE NULL END,
                               updated_at=clock_timestamp(),last_error=%s
                           WHERE event_id=%s AND projection_name=%s
                             AND status IN ('pending','retry')""",
                        (exhausted, delay, exhausted, str(exc)[:1000],
                         event_id, projection_name),
                    )
                break
        return done

    def retry_dead_letter(self, *, event_id: str, projection_name: str,
                          reset_attempts: bool = True) -> bool:
        if not event_id or not projection_name:
            raise ValueError("event_id and projection_name are required")
        with self.conn.transaction():
            result = self.conn.execute(
                """UPDATE memory_projection_jobs
                   SET status='pending',dead_lettered_at=NULL,
                       available_at=clock_timestamp(),last_error=NULL,
                       attempts=CASE WHEN %s THEN 0 ELSE attempts END,
                       updated_at=clock_timestamp()
                   WHERE event_id=%s AND projection_name=%s
                     AND status='dead_letter'""",
                (reset_attempts, event_id, projection_name),
            )
        return result.rowcount == 1

    def stats(self, *, projection_name: str | None = None,
              tenant_id: str | None = None) -> dict:
        filters = []
        params = []
        if projection_name is not None:
            if not projection_name:
                raise ValueError("projection_name cannot be empty")
            filters.append("projection_name=%s")
            params.append(projection_name)
        if tenant_id is not None:
            if not tenant_id:
                raise ValueError("tenant_id cannot be empty")
            filters.append("tenant_id=%s")
            params.append(tenant_id)
        where = "WHERE " + " AND ".join(filters) if filters else ""
        rows = self.conn.execute(
            f"""SELECT
                  count(*) FILTER (WHERE status IN ('pending','retry')) AS pending,
                  count(*) FILTER (WHERE status='succeeded') AS succeeded,
                  count(*) FILTER (WHERE status='dead_letter') AS dead_lettered,
                  coalesce(sum(attempts),0) AS total_attempts
                FROM memory_projection_jobs {where}""",
            tuple(params),
        ).fetchone()
        return {"pending": int(rows[0]), "succeeded": int(rows[1]),
                "dead_lettered": int(rows[2]), "total_attempts": int(rows[3])}
