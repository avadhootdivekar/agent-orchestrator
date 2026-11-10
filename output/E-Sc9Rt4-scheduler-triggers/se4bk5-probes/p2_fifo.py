import os, sys, tempfile, threading
from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.service.schedule_launch import ScheduledLauncher
root = tempfile.mkdtemp(); os.mkfifo(os.path.join(root, "p.fifo"))
res = {}
def go():
    try: res["v"] = ScheduledLauncher._read_prompt(ScheduledLauncher, LocalFsArtifactStore(root), "p.fifo", None)
    except Exception as e: res["e"] = repr(e)
t = threading.Thread(target=go, daemon=True); t.start(); t.join(3)
print("FIFO read: BLOCKED >3s (hang)" if t.is_alive() else f"FIFO read returned: {res}", flush=True)
os._exit(0)
