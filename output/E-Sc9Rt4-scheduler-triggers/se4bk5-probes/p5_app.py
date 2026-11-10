import logging, io, tempfile
from datetime import UTC, datetime
from fastapi.testclient import TestClient
from agent_orchestrator.service import webhook as w
from agent_orchestrator.schedules.models import ScheduleFile
secret = b"S3cr3t-value-0123456789abcdef"
b = ScheduleFile.from_data({"version":"1.0","schedules":[{"id":"h1","kind":"webhook","workflow":"w.json","limits":{"cost":{"per_run_usd":1.0}},"webhook":{"secret_env":"WH_SECRET"}}]},source="t").bindings[0]
root = tempfile.mkdtemp(); now = datetime(2026,11,11,tzinfo=UTC)
lookup = lambda ws, sid: w.WebhookTarget(root, b) if (ws, sid) == ("ws","h1") else None
buf = io.StringIO(); h = logging.StreamHandler(buf); logging.getLogger().addHandler(h); logging.getLogger().setLevel(logging.DEBUG)
app = w.build_webhook_app(lookup, w.WebhookRequestQueue(tempfile.mkdtemp()), clock=lambda: now, secret_resolver=lambda t: secret)
c = TestClient(app, base_url="http://127.0.0.1")
body = b'{"a":1}'; ts = str(int(now.timestamp())); good = w.compute_signature(secret, body, ts); bad = "sha256=" + "0"*64
H = {"content-type":"application/json"}
r_unknown = c.post("/hooks/ws/nope", content=body, headers={**H, "x-ao-signature": bad, "x-ao-timestamp": ts})
r_badsig = c.post("/hooks/ws/h1", content=body, headers={**H, "x-ao-signature": bad, "x-ao-timestamp": ts})
r_stale = c.post("/hooks/ws/h1", content=body, headers={**H, "x-ao-signature": w.compute_signature(secret, body, "1"), "x-ao-timestamp": "1"})
r_ok = c.post("/hooks/ws/h1", content=body, headers={**H, "x-ao-signature": good, "x-ao-timestamp": ts})
r_dup = c.post("/hooks/ws/h1", content=body, headers={**H, "x-ao-signature": good, "x-ao-timestamp": ts})
r_host = TestClient(app, base_url="http://evil.example").post("/hooks/ws/h1", content=body, headers={**H, "x-ao-signature": good, "x-ao-timestamp": ts})
print("unknown", r_unknown.status_code, r_unknown.content, "| badsig", r_badsig.status_code, r_badsig.content, "| identical:", r_unknown.content == r_badsig.content and r_unknown.headers.get("content-length")==r_badsig.headers.get("content-length"))
print("stale", r_stale.status_code, "ok", r_ok.status_code, "dup", r_dup.status_code, "foreign Host", r_host.status_code)
blob = buf.getvalue() + "".join(r.text for r in (r_unknown, r_badsig, r_stale, r_ok, r_dup, r_host))
for label, needle in [("secret", secret.decode()), ("expected sig", good[7:]), ("received bad sig", "0"*64)]:
    print(f"leak {label}:", needle in blob)
print("log lines captured:", len(buf.getvalue().splitlines()))
