# Task: resolve this issue in the checked-out repository

You are working inside a real, checked-out copy of an open-source project's
repository, at the exact commit where the issue below was reported.

- Make the minimal source-code change(s) needed to resolve the issue.
- After changing code, run the project's relevant tests (e.g. with `pytest`) to check
  your fix; explore the repository and its existing tests as needed.
- Do not modify test files unless the issue explicitly asks for a test change.
- Do not touch any `.grading` directory, `.git/config`, or run `git commit` /
  `git reset` / `git checkout` yourself -- your changes are graded by diffing the
  working tree against the original checkout; committing or reverting would erase
  that diff.
- Do not modify anything outside this repository.

## Issue

Request with binary payload fails due to calling to_native_string
Introduced with https://github.com/kennethreitz/requests/issues/2844

```
import requests
requests.put("http://httpbin.org/put", data=u"ööö".encode("utf-8"))
```

This works with 2.8.1, but not with 2.9.
