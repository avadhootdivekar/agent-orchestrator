# Feature: implement `attrs.validators.ne` (value must not equal a given value)

This repository is the `attrs` library. Its documentation (`docs/api.rst`) and changelog
fragment (`changelog.d/1571.change.md`) describe a number-comparison validator
`attrs.validators.ne(val)`, but the implementation is missing from this checkout, and so is
the test-suite coverage for the validators module.

Implement it so that it behaves like its siblings `lt`, `le`, `ge` and `gt`:

- `attrs.validators.ne(val)` returns a validator for `attrs.field(validator=...)` /
  `attr.ib(validator=...)` that raises `ValueError` when the attribute is initialised with a
  value *equal* to `val`, and accepts every other value. It compares with `operator.ne`.
- The error message and `repr` follow the siblings' conventions with the operator `!=`
  (e.g. the repr of `ne(42)` is `<Validator for x != 42>`; the message is
  `'<attribute name>' must be != 42: 42`).
- It is part of the public API: listed in `attr.validators.__all__` (so it is available as
  both `attr.validators.ne` and `attrs.validators.ne`) and declared in the type stub
  `src/attr/validators.pyi`.

Keep the change small and consistent with the surrounding code. The repository is not pre-installed: create an environment first, e.g. `uv venv .venv && uv pip install -e . pytest hypothesis` (the grader rebuilds `.venv` itself, so anything you keep there is thrown away).
Run the rest of the test suite as you work (`python -m pytest tests/`). Leave your changes in
the working tree.
