"""P3: path-shape fuzz over schedule path fields + secret_file NUL in resolve_secret."""
from agent_orchestrator.schedules.models import ScheduleFile
from agent_orchestrator.schedules.errors import ScheduleFileError
cases = ["..", "../x", "a/../../x", "/etc/passwd", "~/x", "~root/x", "%2e%2e/x", "a\x00b", "x"*5000, "C:/x", "a\\..\\..\\x", "./ok"]
def binding(field, v):
    b = {"id": "s1", "kind": "cron", "schedule": "0 * * * *", "workflow": "w.json"}
    if field == "template": b.pop("workflow"); b["template"] = v
    elif field == "watch.paths": b.update(kind="file_watch", watch={"paths": [v]}); b.pop("schedule")
    elif field == "webhook.secret_file": b.update(kind="webhook", webhook={"secret_file": v}); b.pop("schedule")
    elif field in ("until.artifact_exists","until.gate_file"): b["until"] = {field.split(".")[1]: v}
    else: b[field] = v
    b["limits"]={"cost":{"per_run_usd":1.0}}; return {"version": "1.0", "schedules": [b]}
for field in ["workflow","prompt_file","template","watch.paths","webhook.secret_file","until.artifact_exists","until.gate_file"]:
    row = []
    for c in cases:
        try: ScheduleFile.from_data(binding(field, c), source="t"); row.append("ACCEPT")
        except ScheduleFileError: row.append("reject")
        except Exception as e: row.append("EXC:"+type(e).__name__)
    print(f"{field:24}", " ".join(f"{repr(c)[:10]}={r}" for c, r in zip(cases, row)))
