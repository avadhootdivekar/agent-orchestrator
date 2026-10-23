# Bug report: progress/file-size units switch one step too late at exact powers of the base

This repository is the `rich` library. Progress columns pick a unit by magnitude, but a value
that is *exactly* a power of the unit base stays in the smaller unit. Examples (from the
repository root, with `rich` importable):

```python
from rich.progress import DownloadColumn, Task, TaskProgressColumn

# A task without a total shows its speed in iterations per second:
print(TaskProgressColumn.render_speed(1000.0).plain)       # 1000.0 it/s          (expected 1.0×10³ it/s)
print(TaskProgressColumn.render_speed(1_000_000.0).plain)  # 1000.0×10³ it/s      (expected 1.0×10⁶ it/s)

# Exactly 1 KiB / 1 kB downloaded sizes:
column = DownloadColumn(binary_units=True)
print(column.render(Task(1, "t", 1024, 512, _get_time=lambda: 1.0)))  # 512/1,024 bytes  (expected 0.5/1.0 KiB)
```

Values strictly between powers of the base (999, 1500, ...) render correctly. The existing
test-suite already fails on this; make it (and the examples above) pass without changing
unrelated behaviour. The repository is not pre-installed: create an environment first, e.g. `uv venv .venv && uv pip install -e . pytest attrs` (the grader rebuilds `.venv` itself, so anything you keep there is thrown away).
Run `python -m pytest tests/test_progress.py tests/test_filesize.py` as you work. Leave your
changes in the working tree.
