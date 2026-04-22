#!/usr/bin/env bash
# Codebuddy 内网版插件的 afterAgentResponse hook 适配器。
#
# 插件约定：
#   stdin -> JSON: { "text": "<Agent返回的content内容>" }
#   监控型 hook，stdout 的返回值不会影响流程。
#
# 行为：Agent 每次完成一次响应 -> heartbeat（极短一下）
# 结合 stop hook 使用时可注释掉，避免太吵。

NOTIFIER="$(cd "$(dirname "$0")" && pwd)/notify.py"
cat >/dev/null || true  # 吞掉 stdin，防止管道阻塞

"$NOTIFIER" heartbeat >/dev/null 2>&1 &
exit 0
