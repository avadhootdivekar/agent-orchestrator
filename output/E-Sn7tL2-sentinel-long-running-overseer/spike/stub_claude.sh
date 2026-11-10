#!/usr/bin/env bash
# Stand-in for `claude -p`: emits stream-json-ish lines, spawns a grandchild (like a Bash tool
# running `sleep`), then sits silent. Args are ignored. STUB_CHILD_PIDFILE records the grandchild pid.
echo '{"type":"system","subtype":"init"}'
( sleep "${STUB_SLEEP:-300}" & echo $! > "${STUB_CHILD_PIDFILE:-/dev/null}"; wait ) &
sleep "${STUB_SLEEP:-300}"
echo '{"type":"result","subtype":"success","is_error":false,"result":"ok","usage":{"input_tokens":1,"output_tokens":1}}'
