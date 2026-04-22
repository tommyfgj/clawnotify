#!/usr/bin/env bash
# 把任何一条长命令包进来，跑完就敲一下。
#   用法：  wrap_notify.sh done   -- pytest -q
#   用法：  wrap_notify.sh error  -- npm run build
set -u
NOTIFIER="$(cd "$(dirname "$0")" && pwd)/notify.py"

PRESET_OK="${1:-done}"; shift || true
PRESET_FAIL="error"
if [[ "${1:-}" == "--fail" ]]; then PRESET_FAIL="$2"; shift 2; fi
if [[ "${1:-}" == "--" ]]; then shift; fi

"$@"
rc=$?
if [[ $rc -eq 0 ]]; then
  "$NOTIFIER" "$PRESET_OK"  >/dev/null 2>&1 || true
else
  "$NOTIFIER" "$PRESET_FAIL" >/dev/null 2>&1 || true
fi
exit $rc
