# STATUS

- ID: `T-Ov3Sbj-bench-ao-template-subject`
- Updated At: 2026-10-24
- State: **Done**

## This update
- By: Claude · Role: developer · Date: 2026-10-24 · Comment: landed in `246ee31` (template mode + `init_git` +
  `ao-overseer-sonnet`), tests `tests/bench/test_ao_template_subject.py` (6). Real smoke on `dev-core`
  `bugfix-off-by-one`: solved, $1.16, 203 s, 7 sessions all `claude-sonnet-5-5` (git-branch-off, intake,
  w01-01-fix, w01-02-verify, ck-01, final-verify, closeout), init fingerprint == baseline, branch
  `overseer/o-…-bench` created off `main`. The git-caller guard allow-list gained `bench/subjects.py`.
