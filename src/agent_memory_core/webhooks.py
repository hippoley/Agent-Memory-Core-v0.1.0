"""Durable, signed webhook fanout.

Outbound destinations are restricted to deployment-configured HTTPS hostnames.
Delivery is at-least-once: receivers MUST deduplicate by delivery_id/event_id.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlsplit
from uuid import uuid4


def validate_target(url: str, allowed_hosts: set[str]) -> str:
    parsed=urlsplit(url)
    host=(parsed.hostname or "").lower().rstrip(".")
    if (parsed.scheme!="https" or not host or parsed.username or parsed.password
            or parsed.port not in (None,443) or parsed.path in ("","/")
            or parsed.query or parsed.fragment or host not in allowed_hosts):
        raise ValueError("Webhook URL must be HTTPS on an explicitly allowed host and include a path")
    return url


class WebhookStore:
    def __init__(self,conn):
        self.conn=conn

    def create(self, *, tenant_id: str, target_url: str,
               event_types: list[str]) -> str:
        if not tenant_id or not target_url or not event_types or len(event_types)>50:
            raise ValueError("Tenant, target URL and 1..50 event types required")
        if any(not isinstance(e,str) or not e or len(e)>200 for e in event_types):
            raise ValueError("Invalid webhook event type")
        subscription_id=str(uuid4())
        with self.conn.transaction():
            self.conn.execute(
                """INSERT INTO memory_webhook_subscriptions
                   (subscription_id,tenant_id,target_url,event_types)
                   VALUES (%s,%s,%s,%s)""",
                (subscription_id,tenant_id,target_url,sorted(set(event_types))))
        return subscription_id

    def list(self, *, tenant_id: str) -> list[dict]:
        rows=self.conn.execute(
            """SELECT subscription_id::text,target_url,event_types,active,created_at
               FROM memory_webhook_subscriptions
               WHERE tenant_id=%s ORDER BY created_at DESC""",(tenant_id,)).fetchall()
        return [{"subscription_id":r[0],"target_url":r[1],"event_types":r[2],
                 "active":r[3],"created_at":r[4]} for r in rows]

    def revoke(self, *, tenant_id: str, subscription_id: str) -> bool:
        with self.conn.transaction():
            row=self.conn.execute(
                """UPDATE memory_webhook_subscriptions
                   SET active=false,revoked_at=clock_timestamp()
                   WHERE tenant_id=%s AND subscription_id=%s::uuid AND active
                   RETURNING subscription_id""",(tenant_id,subscription_id)).fetchone()
        return row is not None


class WebhookWorker:
    def __init__(self,conn,*,signing_master_key: str,allowed_hosts: set[str],
                 max_attempts: int=12,lease_seconds: int=60,timeout: float=8.0,
                 http_client=None):
        if len(signing_master_key)<32:
            raise ValueError("Webhook signing master key must be at least 32 characters")
        if max_attempts<1 or lease_seconds<5 or timeout<=0:
            raise ValueError("Invalid webhook worker limits")
        self.conn=conn
        self.master=signing_master_key.encode()
        self.allowed_hosts={h.lower().rstrip(".") for h in allowed_hosts}
        self.max_attempts=max_attempts
        self.lease_seconds=lease_seconds
        self.timeout=timeout
        self.http_client=http_client

    def _secret(self,subscription_id: str) -> bytes:
        return hmac.new(self.master,("agent-memory-webhook:"+subscription_id).encode(),
                        hashlib.sha256).digest()

    def enqueue(self,limit: int=500) -> int:
        if not 1<=limit<=5000:
            raise ValueError("limit must be 1..5000")
        with self.conn.transaction():
            result=self.conn.execute(
                """INSERT INTO memory_webhook_deliveries
                   (delivery_id,subscription_id,event_id,tenant_id)
                   SELECT gen_random_uuid(),s.subscription_id,o.event_id,o.tenant_id
                   FROM memory_outbox o
                   JOIN memory_webhook_subscriptions s
                     ON s.tenant_id=o.tenant_id AND s.active
                    AND o.event_type=ANY(s.event_types)
                   WHERE NOT EXISTS (
                     SELECT 1 FROM memory_webhook_deliveries d
                     WHERE d.subscription_id=s.subscription_id AND d.event_id=o.event_id)
                   ORDER BY o.created_at,o.event_id
                   LIMIT %s
                   ON CONFLICT(subscription_id,event_id) DO NOTHING""",(limit,))
            return result.rowcount

    def _claim(self):
        with self.conn.transaction():
            row=self.conn.execute(
                """SELECT d.delivery_id::text,d.subscription_id::text,
                          d.event_id::text,d.tenant_id,s.target_url,
                          o.event_type,o.payload,d.attempts+1
                   FROM memory_webhook_deliveries d
                   JOIN memory_webhook_subscriptions s
                     ON s.subscription_id=d.subscription_id
                   JOIN memory_outbox o ON o.event_id=d.event_id
                   WHERE s.active AND d.attempts<%s
                     AND ((d.status='pending' AND d.available_at<=clock_timestamp())
                       OR (d.status='delivering' AND d.lease_until<clock_timestamp()))
                   ORDER BY d.available_at,d.created_at,d.delivery_id
                   LIMIT 1 FOR UPDATE OF d SKIP LOCKED""",(self.max_attempts,)).fetchone()
            if row is None:
                return None
            self.conn.execute(
                """UPDATE memory_webhook_deliveries SET status='delivering',
                   attempts=attempts+1,lease_until=clock_timestamp()+(%s*interval '1 second')
                   WHERE delivery_id=%s::uuid""",(self.lease_seconds,row[0]))
            return row

    def _finish(self,delivery_id: str,*,success: bool,status: int|None,error: str|None,
                attempt: int):
        with self.conn.transaction():
            if success:
                self.conn.execute(
                    """UPDATE memory_webhook_deliveries SET status='delivered',
                       delivered_at=clock_timestamp(),lease_until=NULL,
                       response_status=%s,last_error=NULL
                       WHERE delivery_id=%s::uuid AND status='delivering'""",
                    (status,delivery_id))
            else:
                dead=attempt>=self.max_attempts
                delay=min(3600,5*(2**min(attempt-1,9)))
                self.conn.execute(
                    """UPDATE memory_webhook_deliveries SET status=%s,
                       available_at=clock_timestamp()+(%s*interval '1 second'),
                       lease_until=NULL,response_status=%s,last_error=%s
                       WHERE delivery_id=%s::uuid AND status='delivering'""",
                    ("dead" if dead else "pending",delay,status,(error or "delivery failed")[:500],delivery_id))

    def deliver_one(self) -> bool:
        row=self._claim()
        if row is None:
            return False
        delivery_id,subscription_id,event_id,tenant_id,target,event_type,payload,attempt=row
        try:
            validate_target(target,self.allowed_hosts)
            envelope={"delivery_id":delivery_id,"event_id":event_id,
                      "tenant_id":tenant_id,"event_type":event_type,"payload":payload}
            body=json.dumps(envelope,ensure_ascii=False,sort_keys=True,
                            separators=(",",":"),default=str).encode()
            timestamp=str(int(time.time()))
            signature=hmac.new(self._secret(subscription_id),
                               timestamp.encode()+b"."+body,hashlib.sha256).hexdigest()
            headers={"Content-Type":"application/json","User-Agent":"Agent-Memory-Core-Webhook/1",
                     "X-Memory-Delivery":delivery_id,"X-Memory-Event":event_id,
                     "X-Memory-Timestamp":timestamp,"X-Memory-Signature":"sha256="+signature}
            if self.http_client is None:
                import httpx
                with httpx.Client(timeout=self.timeout,follow_redirects=False,
                                  trust_env=False) as client:
                    response=client.post(target,content=body,headers=headers)
            else:
                response=self.http_client.post(target,content=body,headers=headers)
            status=int(response.status_code)
            if 200<=status<300:
                self._finish(delivery_id,success=True,status=status,error=None,attempt=attempt)
            else:
                self._finish(delivery_id,success=False,status=status,
                             error="Receiver returned HTTP "+str(status),attempt=attempt)
        except Exception as exc:
            self._finish(delivery_id,success=False,status=None,
                         error=type(exc).__name__+": "+str(exc),attempt=attempt)
        return True

    def consume(self,limit: int=100) -> int:
        if not 1<=limit<=1000:
            raise ValueError("limit must be 1..1000")
        self.enqueue()
        done=0
        for _ in range(limit):
            if not self.deliver_one():
                break
            done+=1
        return done

    def requeue_dead(self,*,tenant_id: str,delivery_id: str) -> bool:
        with self.conn.transaction():
            row=self.conn.execute(
                """UPDATE memory_webhook_deliveries d SET status='pending',
                   attempts=0,available_at=clock_timestamp(),lease_until=NULL,
                   last_error=NULL,response_status=NULL
                   FROM memory_webhook_subscriptions s
                   WHERE d.subscription_id=s.subscription_id
                     AND s.tenant_id=%s AND d.delivery_id=%s::uuid
                     AND d.status='dead' RETURNING d.delivery_id""",
                (tenant_id,delivery_id)).fetchone()
        return row is not None
