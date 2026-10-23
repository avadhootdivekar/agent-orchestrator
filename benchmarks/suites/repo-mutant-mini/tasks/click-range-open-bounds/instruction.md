# Bug report: exclusive bounds on numeric range parameter types are not honoured

This repository is the `click` command-line library. Option/argument values typed with
`click.IntRange(...)` / `click.FloatRange(...)` misbehave when an *exclusive* bound
(`min_open=True` or `max_open=True`) is used. The exclusive flags seem to act on the wrong
side of the range. Reproduction, run from the repository root once the package is importable:

```python
import click


def run(param_type, value):
    try:
        return repr(param_type.convert(value, None, None))
    except click.BadParameter as exc:
        return f"BadParameter: {exc.message}"


print(run(click.IntRange(0, 5, min_open=True), "0"))            # prints 0
print(run(click.IntRange(0, 5, min_open=True), "5"))            # BadParameter: 5 is not in the range 0<x<=5.
print(run(click.IntRange(0, 5, max_open=True), "5"))            # prints 5
print(run(click.IntRange(0, 5, max_open=True), "0"))            # BadParameter: 0 is not in the range 0<=x<5.
print(run(click.IntRange(0, min_open=True, clamp=True), "0"))   # prints 0
print(run(click.FloatRange(0.5, min_open=True), "0.5"))         # prints 0.5
```

Expected: with `min_open=True` the minimum itself is rejected (or clamped to the next
value) and the maximum is accepted; with `max_open=True` the maximum itself is rejected and
the minimum is accepted. Only the exclusive-bound handling is wrong; ordinary closed ranges
work.

Find the root cause in the library and fix it with a minimal change. The repository is not pre-installed: create an environment first, e.g. `uv venv .venv && uv pip install -e . pytest` (the grader rebuilds `.venv` itself, so anything you keep there is thrown away).
Run the project's test suite as you work (`python -m pytest`). Leave your changes in the
working tree.
