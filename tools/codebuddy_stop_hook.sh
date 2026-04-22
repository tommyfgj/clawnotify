#!/usr/bin/env bash
# Codebuddy 内网版插件 (iwiki 文档) 的 stop hook 适配器。
#
# 插件约定：
#   stdin  -> JSON: { "status": "completed"|"aborted"|"error", "loop_count": N, ... }
#   stdout -> JSON: { "followup_message": "..."}  （可省略：什么都不输出表示不追加）
#
# 行为：
#   - completed -> done
#   - aborted   -> attention
#   - error     -> error
# 不返回 followup_message，纯观察型。

NOTIFIER="$(cd "$(dirname "$0")" && pwd)/notify.py"

# 读取 stdin（Codebuddy 会给我们一段 JSON）
payload="$(cat || true)"

# 简单解析 status，不依赖 jq
status="$(printf '%s' "$payload" | sed -n 's/.*"status"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)"

case "$status" in
  completed) "$NOTIFIER" done      >/dev/null 2>&1 & ;;
  aborted)   "$NOTIFIER" attention >/dev/null 2>&1 & ;;
  error)     "$NOTIFIER" error     >/dev/null 2>&1 & ;;
  *)         "$NOTIFIER" heartbeat >/dev/null 2>&1 & ;;
esac

# 不追加消息，直接结束
exit 0
