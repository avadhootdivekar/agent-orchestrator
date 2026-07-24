# Write tests for: `validation/rules.py`

`validation/rules.py` implements four validation functions. There are no tests for any
of them yet. Write tests in a new file `tests/test_validation.py` that exercise each
function's ACTUAL behavior, per this spec:

1. **`is_valid_email(value)`** — `True` iff `value` contains exactly one `"@"`, has a
   non-empty part before it, and a domain part after it that contains at least one
   `"."` with a non-empty label on each side of that dot, and no whitespace anywhere
   in the string.

2. **`in_range(value, low, high)`** — `True` iff `low <= value <= high` (**both ends
   inclusive**).

3. **`is_strong_password(value)`** — `True` iff `value` is at least 8 characters long
   AND contains at least one digit, at least one uppercase letter, and at least one
   lowercase letter.

4. **`normalize_phone(value)`** — strips everything except digits from `value`. If
   exactly 10 digits remain, returns them prefixed with `"+1"`. If exactly 11 digits
   remain **and** the first digit is `"1"`, returns them prefixed with `"+"`. Any other
   case (wrong digit count, or 11 digits not starting with `"1"`) returns `None`.

For **each** of the four functions, write tests that cover both a case where it should
return the "valid"/positive result and at least one case for each meaningfully
different way it can return the "invalid"/negative result (e.g. for `is_valid_email`:
missing `@`, more than one `@`, and a missing domain dot are three DIFFERENT ways to
be invalid — a test suite that never separately checks the "more than one `@`" case
does not fully exercise the spec above). A test file that doesn't really check these
functions' behavior (e.g. an empty test, or one that would pass no matter what the
functions do) does not satisfy this task.

Do not modify `validation/rules.py` or anything under `.grading/` — `.grading/` is a
held-out grading harness the run is scored against and is not part of what you are
asked to implement or test against directly.
