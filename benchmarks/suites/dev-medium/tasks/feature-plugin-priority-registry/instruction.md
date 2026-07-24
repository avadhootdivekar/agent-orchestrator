# Feature: priority-ordered handler dispatch

This package (`registry/`) implements a small chain-of-responsibility plugin registry.
`Registry` holds named "slots"; each slot can have multiple handlers registered at
different priorities. Two pieces of behavior are unfinished (both are marked with a
`TODO` comment in the source):

1. **`registry/core.py`'s `Registry.handlers_for(name)`** must return the handlers
   registered for `name` ordered from **highest priority to lowest**. Handlers
   registered at the **same** priority must keep their relative registration order
   (the one registered first, among equal priorities, must come first). Right now it
   ignores `priority` entirely and just returns raw registration order.

2. **`registry/dispatch.py`'s `dispatch(registry, name, payload)`** must call handlers
   for `name` **in priority order** (via `handlers_for`) and **stop at the first
   handler whose return value is not `None`** — later (lower-priority) handlers must
   not be called once one has resolved the payload. If no handler resolves the
   payload (either the slot has no handlers, or every handler returns `None`),
   `dispatch` must raise `registry.dispatch.NoHandlerResolvedError`. Right now it
   calls every handler unconditionally and never raises.

`registry/handlers.py` is already correct and complete — you should not need to change
it, though you're free to add more example handlers if useful for your own testing.

Fix both `handlers_for` and `dispatch` so that `tests/test_registry.py` passes.

Do not modify `tests/test_registry.py`, and do not modify anything under the
`.grading/` directory — it is a held-out grading harness the run is scored against
and is not part of what you are asked to implement or test against directly.
