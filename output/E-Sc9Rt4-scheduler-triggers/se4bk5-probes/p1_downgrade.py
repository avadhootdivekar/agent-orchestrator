"""P1: on HEAD, an X-AO delivery replayed as X-Hub-Signature-256 over body '{ts}.{body}' verifies with no time bound."""
import sys, hmac, hashlib
from datetime import UTC, datetime, timedelta
from agent_orchestrator.service import webhook as w
secret = b"s" * 32
body = b'{"ref":"main"}'
ts = "1700000000"
sig = w.compute_signature(secret, body, ts)
t0 = datetime.fromtimestamp(int(ts), UTC)
orig = w.verify_delivery({"x-ao-signature": sig, "x-ao-timestamp": ts}, body, secret, t0)
# one week later: the X-AO form is stale ...
try:
    w.verify_delivery({"x-ao-signature": sig, "x-ao-timestamp": ts}, body, secret, t0 + timedelta(days=7)); print("AO replay: ACCEPTED")
except w.WebhookAuthError as e: print("AO replay a week later:", e.reason)
# ... but the same signature as GitHub form over ts.body is accepted
kw = {}
import inspect
if "scheme" in inspect.signature(w.verify_delivery).parameters: kw["scheme"] = "ao"
try:
    v = w.verify_delivery({"x-hub-signature-256": sig}, ts.encode() + b"." + body, secret, t0 + timedelta(days=7), **kw)
    print("downgrade replay a week later: ACCEPTED; replay_key equal to original:", v.replay_key == orig.replay_key)
except w.WebhookAuthError as e: print("downgrade replay:", e.reason)
