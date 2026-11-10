"""S08 end-to-end TLS delivery to a separate HTTPS receiver process.

No HTTP MockTransport: openssl creates an ephemeral X.509 certificate;
a separate Python interpreter terminates TLS and verifies the real raw-body
HMAC. This test requires openssl, localhost TCP and real PostgreSQL.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime,timezone
from uuid import uuid4
import pytest

pytestmark=pytest.mark.integration

RECEIVER_PROGRAM=r"""
import hashlib,hmac,json,os,ssl,time
from http.server import HTTPServer,BaseHTTPRequestHandler
from pathlib import Path
secret=bytes.fromhex(os.environ['TEST_RECEIVER_SECRET'])
sink=Path(os.environ['TEST_RECEIVER_SINK'])
class Receiver(BaseHTTPRequestHandler):
    def log_message(self,*a):
        pass
    def do_POST(self):
        length=int(self.headers.get('Content-Length','0'))
        body=self.rfile.read(length)
        timestamp=self.headers.get('X-Memory-Timestamp','')
        signature=self.headers.get('X-Memory-Signature','')
        try:
            fresh=abs(time.time()-int(timestamp)) <= 300
            expected='sha256='+hmac.new(secret,timestamp.encode()+b'.'+body,
                                          hashlib.sha256).hexdigest()
            valid=(fresh and hmac.compare_digest(expected,signature)
                   and self.path=='/receive')
        except (ValueError,TypeError):
            valid=False
        if valid:
            record=json.loads(body)
            with sink.open('a',encoding='utf-8') as stream:
                stream.write(json.dumps(record)+'\n')
                stream.flush()
            self.send_response(204)
        else:
            self.send_response(401)
        self.end_headers()
server=HTTPServer(('127.0.0.1',0),Receiver)
context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain(os.environ['TEST_RECEIVER_CERT'],
                        os.environ['TEST_RECEIVER_KEY'])
server.socket=context.wrap_socket(server.socket,server_side=True)
Path(os.environ['TEST_RECEIVER_PORT']).write_text(str(server.server_port))
server.serve_forever()
"""

def test_webhook_actual_https_process_signature_and_redelivery_safety():
    dsn=os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("Real PostgreSQL required")
    if not shutil.which("openssl"):
        pytest.skip("openssl is required for local TLS integration")
    httpx=pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.api import create_app
    from agent_memory_core.webhooks import WebhookWorker
    tenant="s08-tls-"+uuid4().hex
    key="s08-api-key-"+uuid4().hex
    master="s08-real-tls-master-key-at-least-32-characters"
    db=PostgresMemory(dsn)
    proc=None
    try:
        db.migrate()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            cert=root/"cert.pem"
            key_file=root/"key.pem"
            sink=root/"events.jsonl"
            port_file=root/"port"
            subprocess.run(["openssl","req","-x509","-nodes","-newkey","rsa:2048",
                "-keyout",str(key_file),"-out",str(cert),"-days","1",
                "-subj","/CN=localhost",
                "-addext","subjectAltName=DNS:localhost,IP:127.0.0.1"],
                check=True,capture_output=True,text=True,timeout=30)
            # Receiver needs its per-subscription secret. First provision an
            # allowed TCP port independently, then subscribe at that address.
            # A prebound port is not used here: the child starts immediately.
            # The test receiver obtains its secret from this function below.
            from agent_memory_core.webhooks import WebhookStore
            from agent_memory_core.api import create_app
            # Create a listener and inspect its ephemeral port before registering.
            # The child verifies signatures using HMAC-derived subscription secret.
            sub_id=str(uuid4())
            import hmac,hashlib
            derived=hmac.new(master.encode(),
                ("agent-memory-webhook:"+sub_id).encode(),
                hashlib.sha256).hexdigest()
            env=os.environ.copy()
            env.update({"TEST_RECEIVER_CERT":str(cert),"TEST_RECEIVER_KEY":str(key_file),
                        "TEST_RECEIVER_SINK":str(sink),"TEST_RECEIVER_PORT":str(port_file),
                        "TEST_RECEIVER_SECRET":derived})
            proc=subprocess.Popen([sys.executable,"-u","-c",RECEIVER_PROGRAM],
                env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            for _ in range(100):
                if port_file.exists():
                    break
                if proc.poll() is not None:
                    raise AssertionError("Receiver exited before becoming ready")
                time.sleep(0.05)
            assert port_file.exists(),"Independent TLS receiver did not start"
            port=int(port_file.read_text())
            target=f"https://127.0.0.1:{port}/receive"
            with db.conn.transaction():
                db.conn.execute(
                    """INSERT INTO memory_webhook_subscriptions
                       (subscription_id,tenant_id,target_url,event_types)
                       VALUES (%s,%s,%s,%s)""",
                    (sub_id,tenant,target,["memory.claim.created"]))
            mid=db.remember(tenant_id=tenant,subject_id="bedroom",
                predicate="noise",value={"level":"quiet"},source_id="utterance",
                valid_from=datetime(2026,1,1,tzinfo=timezone.utc))
            with httpx.Client(verify=str(cert),trust_env=False,
                              follow_redirects=False,timeout=5.0) as http:
                worker=WebhookWorker(db.conn,signing_master_key=master,
                    allowed_hosts={f"127.0.0.1:{port}"},http_client=http)
                assert worker.consume(limit=10,tenant_id=tenant)==1
                assert worker.consume(limit=10,tenant_id=tenant)==0
                # Receiver independently rejects a corrupted signature.
                invalid=http.post(target,content=b'{}',headers={
                    "X-Memory-Timestamp":str(int(time.time())),
                    "X-Memory-Signature":"sha256="+"0"*64})
                assert invalid.status_code==401
            records=[json.loads(x) for x in sink.read_text().splitlines()]
            assert len(records)==1
            assert records[0]["event_id"]==mid
            assert records[0]["tenant_id"]==tenant
            assert records[0]["delivery_id"]
            delivered=db.conn.execute(
                """SELECT status,response_status,attempts
                   FROM memory_webhook_deliveries WHERE event_id=%s""",
                (mid,)).fetchone()
            assert delivered==("delivered",204,1)
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
        db.close()


def test_explicit_host_port_allowlist_does_not_authorize_other_ports():
    from agent_memory_core.webhooks import validate_target
    assert validate_target("https://127.0.0.1:10443/receive",
        {"127.0.0.1:10443"})=="https://127.0.0.1:10443/receive"
    with pytest.raises(ValueError):
        validate_target("https://127.0.0.1:10444/receive",
                        {"127.0.0.1:10443"})
    with pytest.raises(ValueError):
        validate_target("https://127.0.0.1:10443/receive",
                        {"127.0.0.1"})
