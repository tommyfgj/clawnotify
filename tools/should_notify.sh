#!/usr/bin/env bash
# should_notify.sh — 判断当前是否"应该"敲击提醒用户
#
# 策略（任一满足 => 应该敲 => exit 0）：
#   1. 屏幕已锁
#   2. 前台 app 不是 Codebuddy IDE（bundle id com.tencent.codebuddycn）
#   3. 系统 idle 超过 IDLE_THRESHOLD_SEC（默认 20 秒，即使 IDE 在前台但人没动）
#
# 否则 exit 1（= 用户在看着 IDE，别打扰）。
#
# 环境变量覆盖：
#   FOCUS_BUNDLE_IDS   逗号分隔的 bundle id 列表，匹配到就算"用户在看"
#                      默认 "com.tencent.codebuddycn"
#   IDLE_THRESHOLD_SEC idle 多少秒算"虽然 IDE 在前台但人离开了"，默认 20
#   SKIP_FOCUS_CHECK=1 跳过"前台 app"判定，只看屏幕锁 + idle。适合 CLI 类 agent
#                      （Claude Code 等），因为它们跑在任意终端里，无法精确锚定
#                      "用户在看的窗口"。此时仅在 idle 较长时才敲。
#   DEBUG=1            打印判定过程到 stderr

set -u

FOCUS_BUNDLE_IDS="${FOCUS_BUNDLE_IDS:-com.tencent.codebuddycn}"
IDLE_THRESHOLD_SEC="${IDLE_THRESHOLD_SEC:-20}"
SKIP_FOCUS_CHECK="${SKIP_FOCUS_CHECK:-0}"

log() { [ "${DEBUG:-0}" = "1" ] && echo "[should_notify] $*" >&2 || true; }

# 1) 屏幕锁？CGSessionCopyCurrentDictionary 里 CGSSessionScreenIsLocked=1 表示锁屏
locked=$(python3 - <<'PY' 2>/dev/null || echo "0"
try:
    from Quartz import CGSessionCopyCurrentDictionary
    d = CGSessionCopyCurrentDictionary() or {}
    print(1 if d.get("CGSSessionScreenIsLocked") else 0)
except Exception:
    # Quartz 不可用时，退化用 ioreg 查 clamshell / display 状态
    import subprocess
    try:
        out = subprocess.check_output(
            ["ioreg", "-n", "Root Domain"], text=True, stderr=subprocess.DEVNULL)
        # 无法准确判锁屏时，保守认为没锁
        print(0)
    except Exception:
        print(0)
PY
)
if [ "$locked" = "1" ]; then
    log "screen locked -> notify"
    exit 0
fi

# 2) 前台 app bundle id（可跳过）
if [ "$SKIP_FOCUS_CHECK" = "1" ]; then
    log "SKIP_FOCUS_CHECK=1 -> skip focus check, only rely on idle"
else
    frontmost_bundle=$(osascript -e \
        'tell application "System Events" to get bundle identifier of first application process whose frontmost is true' \
        2>/dev/null)
    log "frontmost_bundle=$frontmost_bundle"

    IFS=',' read -ra WATCH <<< "$FOCUS_BUNDLE_IDS"
    is_focus=0
    for b in "${WATCH[@]}"; do
        if [ "$frontmost_bundle" = "$b" ]; then
            is_focus=1
            break
        fi
    done

    if [ "$is_focus" = "0" ]; then
        log "front app not in FOCUS_BUNDLE_IDS -> notify"
        exit 0
    fi
fi

# 3) IDE 在前台，但检查 idle 时间
# HIDIdleTime 单位是纳秒
idle_ns=$(ioreg -c IOHIDSystem 2>/dev/null | awk '/HIDIdleTime/ {print $NF; exit}')
if [ -z "$idle_ns" ]; then
    log "cannot read idle time, assume active -> suppress"
    exit 1
fi
idle_sec=$(( idle_ns / 1000000000 ))
log "idle_sec=$idle_sec threshold=$IDLE_THRESHOLD_SEC"

if [ "$idle_sec" -ge "$IDLE_THRESHOLD_SEC" ]; then
    log "idle too long -> notify"
    exit 0
fi

log "user is watching IDE -> suppress"
exit 1
