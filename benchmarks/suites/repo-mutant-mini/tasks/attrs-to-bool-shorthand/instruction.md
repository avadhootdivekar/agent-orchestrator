# Bug report: single-letter `y` / `n` answers are rejected by the boolean converter

This repository is the `attrs` library. We read yes/no flags from environment variables and
interactive prompts and convert them with `attrs.converters.to_bool`. Since a recent change,
the single-letter answers raise instead of converting:

```python
import attrs


@attrs.define
class Settings:
    verbose: bool = attrs.field(converter=attrs.converters.to_bool)


print(Settings("yes"))   # Settings(verbose=True)        -- fine
print(Settings("t"))     # Settings(verbose=True)        -- fine
print(Settings("y"))     # ValueError: Cannot convert value to bool: 'y'
print(Settings("N"))     # ValueError: Cannot convert value to bool: 'n'
```

Expected: `"y"`/`"Y"` convert to `True` and `"n"`/`"N"` convert to `False`, exactly as the
converter's own docstring promises; every other documented value keeps working and
unrecognised strings keep raising `ValueError`.

Fix the bug with a minimal change. The repository is not pre-installed: create an environment first, e.g. `uv venv .venv && uv pip install -e . pytest hypothesis` (the grader rebuilds `.venv` itself, so anything you keep there is thrown away).
Run the existing tests as you work (`python -m pytest tests/test_converters.py`). Leave your
changes in the working tree.
