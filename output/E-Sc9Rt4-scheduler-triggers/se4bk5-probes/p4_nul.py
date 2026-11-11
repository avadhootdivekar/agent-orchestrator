import tempfile

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.schedules.models import ScheduleFile
from agent_orchestrator.service import governance_gate as gg
from agent_orchestrator.service import webhook as w

root = tempfile.mkdtemp()


def mk(field, v):
    b = {
        "id": "s1",
        "kind": "cron",
        "schedule": "0 * * * *",
        "workflow": "w.json",
        "limits": {"cost": {"per_run_usd": 1.0}},
    }
    if field == "webhook":
        b.update(kind="webhook", webhook={"secret_file": v})
        b.pop("schedule")
    else:
        b[field] = v
    return ScheduleFile.from_data({"version": "1.0", "schedules": [b]}, source="t").bindings[0]


for label, fn in [
    ("governed read_prompt", lambda: gg.read_prompt(mk("prompt_file", "a\x00b"), root)),
    ("artifact store resolve", lambda: LocalFsArtifactStore(root).resolve("a\x00b")),
    (
        "webhook resolve_secret",
        lambda: w.resolve_secret(w.WebhookTarget(root, mk("webhook", "a\x00b"))),
    ),
    ("long prompt_file", lambda: gg.read_prompt(mk("prompt_file", "x" * 5000), root)),
]:
    try:
        print(label, "->", repr(fn())[:160])
    except Exception as e:
        print(label, "-> RAISES", type(e).__name__, str(e)[:100])
