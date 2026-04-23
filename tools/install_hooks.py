#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
install_hooks.py — 一键把 Agent Notifier hook 装到 / 卸载出各家 agent

支持的 agent：
    codebuddy   ~/.codebuddy/settings.json        (Stop hook)
    claude      ~/.claude/settings.json           (Stop / Notification / SubagentStop)
    cursor      ~/Library/Application Support/Cursor/User/settings.json
                (Cursor 没有事件式 hook，改成注册 3 个 VS Code task，
                 由 agent 通过终端按需调用；同时提示合并 .cursor/rules)

用法：
    install_hooks.py install              # 装所有已检测到的 agent
    install_hooks.py install codebuddy    # 只装某个
    install_hooks.py uninstall            # 卸载所有
    install_hooks.py uninstall claude
    install_hooks.py status               # 查看当前安装状态

幂等：
    多次 install 不会重复；通过 "_clawbot_managed": true 标记自己添加的条目。
    卸载只删带标记的条目，不会动用户自己写的 hook。

备份：
    每次修改前自动把原文件复制一份到 <file>.bak.<时间戳>。
"""
from __future__ import annotations
import argparse
import json
import os
import pathlib
import shutil
import sys
import time
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
NOTIFY = HERE / "notify.py"
SHOULD = HERE / "should_notify.sh"
MARK_KEY = "_clawbot_managed"   # 用来标记我们添加的 hook 条目
MARK_VAL = True

IS_WIN = os.name == "nt"

if IS_WIN:
    # Windows 中文控制台默认 GBK，打印 ✓/✗ 会抛 UnicodeEncodeError。
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _pythonw() -> str:
    """Windows: 返回 pythonw.exe（无控制台窗口），找不到就退回 python.exe。"""
    exe = pathlib.Path(sys.executable)
    pw = exe.with_name("pythonw.exe")
    return str(pw if pw.exists() else exe)

# ---------- 通用工具 ----------

def banner(msg: str) -> None:
    print(f"\n\033[1;36m==> {msg}\033[0m")

def ok(msg: str) -> None:
    print(f"  \033[32m✓\033[0m {msg}")

def warn(msg: str) -> None:
    print(f"  \033[33m!\033[0m {msg}")

def err(msg: str) -> None:
    print(f"  \033[31m✗\033[0m {msg}")

def backup(p: pathlib.Path) -> None:
    if p.exists():
        bak = p.with_suffix(p.suffix + f".bak.{int(time.time())}")
        shutil.copy2(p, bak)
        ok(f"备份：{bak}")

def load_json(p: pathlib.Path) -> dict:
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError as e:
        err(f"JSON 解析失败：{p} — {e}")
        sys.exit(1)

def save_json(p: pathlib.Path, data: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

def cmd_with_guard(
    pattern: str,
    env: dict[str, str] | None = None,
    skip_guard: bool = False,
) -> str:
    """生成 hook 的 shell 命令，末尾异步不阻塞。

    skip_guard=True: 绕过 should_notify.sh，直接敲 notify.py。
                     适合 CLI 类 agent（Claude Code 等）——用户运行它时通常
                     不盯着看，任务完成一律要提醒。
    env: 注入到 should_notify.sh 的环境变量，仅在 skip_guard=False 时生效。
    """
    if IS_WIN:
        # Windows 上没有 should_notify.sh（依赖 osascript/Quartz/ioreg），直接敲。
        # 用 notify.py 自带的 --detach：把自己 Popen 成 DETACHED_PROCESS 子进程
        # 后立刻 exit，这样 Claude 的 hook shell 回合瞬间返回，真正干活的进程
        # 脱离控制台独立存活。pythonw.exe 避免 Stop 时闪一下控制台窗口。
        return f'"{_pythonw()}" "{NOTIFY}" --detach {pattern}'
    if skip_guard:
        return f"( {NOTIFY} {pattern} ) >/dev/null 2>&1 &"
    env_prefix = ""
    if env:
        env_prefix = " ".join(f"{k}={v}" for k, v in env.items()) + " "
    return (
        f"( {env_prefix}{SHOULD} && {NOTIFY} {pattern} ) >/dev/null 2>&1 &"
    )

def make_hook_entry(
    pattern: str,
    matcher: str = "",
    env: dict[str, str] | None = None,
    skip_guard: bool = False,
) -> dict:
    """生成带标记的单条 hook 条目。"""
    return {
        "matcher": matcher,
        MARK_KEY: MARK_VAL,
        "hooks": [
            {"type": "command",
             "command": cmd_with_guard(pattern, env=env, skip_guard=skip_guard)}
        ],
    }

def ensure_executable(*paths: pathlib.Path) -> None:
    if IS_WIN:
        return  # Windows 上没有 +x 位；.py 通过 python/pythonw 显式调起
    for p in paths:
        if p.exists():
            p.chmod(p.stat().st_mode | 0o111)

# ---------- hook 合并/清理 ----------

def merge_hooks(existing: list, event_name: str, new_entries: list[dict]) -> list:
    """把 new_entries 追加进 existing；先删掉旧的 managed 条目（幂等）。"""
    cleaned = [e for e in (existing or [])
               if not (isinstance(e, dict) and e.get(MARK_KEY))]
    return cleaned + new_entries

def purge_hooks(existing: list) -> list:
    """把 managed 的条目都删掉。"""
    return [e for e in (existing or [])
            if not (isinstance(e, dict) and e.get(MARK_KEY))]

def prune_empty(data: dict) -> None:
    """如果 hooks 下某事件空了就删掉；hooks 整体空了也删掉。"""
    hooks = data.get("hooks")
    if isinstance(hooks, dict):
        for k in list(hooks.keys()):
            if not hooks[k]:
                del hooks[k]
        if not hooks:
            del data["hooks"]

# ---------- 各 agent 的安装/卸载 ----------

HOOK_PLANS = {
    # agent -> (settings 路径, {事件: notify 模式}, opts)
    # opts 支持：
    #   env        dict[str,str]  — 注入到 should_notify.sh 的环境变量
    #   skip_guard bool           — 直接敲，不过 should_notify.sh
    "codebuddy": (
        pathlib.Path.home() / ".codebuddy" / "settings.json",
        {
            "Stop": "done",
        },
        # GUI 应用：通过 should_notify.sh 判前台 bundle id = com.tencent.codebuddycn
        {},
    ),
    "claude": (
        pathlib.Path.home() / ".claude" / "settings.json",
        {
            "Stop": "done",
            "Notification": "ask",
            "SubagentStop": "attention",
        },
        # Claude Code 是 CLI：用户跑它时通常不盯着终端看，而是在别的 app 里
        # 干别的活。因此 Stop 事件**一律敲**，绕过所有"智能静音"判定。
        {"skip_guard": True},
    ),
}

def install_hook_based(agent: str) -> bool:
    path, plan, opts = HOOK_PLANS[agent]
    env = opts.get("env", {})
    skip_guard = opts.get("skip_guard", False)
    banner(f"安装 {agent}  -> {path}")
    backup(path)
    data = load_json(path)
    hooks = data.setdefault("hooks", {})
    for event, pattern in plan.items():
        hooks[event] = merge_hooks(
            hooks.get(event),
            event,
            [make_hook_entry(pattern, env=env, skip_guard=skip_guard)],
        )
        if skip_guard:
            suffix = "  [always notify]"
        elif env:
            suffix = f"  [{' '.join(f'{k}={v}' for k, v in env.items())}]"
        else:
            suffix = ""
        ok(f"{event} -> notify.py {pattern}{suffix}")
    save_json(path, data)
    ok(f"写入完成：{path}")
    return True

def uninstall_hook_based(agent: str) -> bool:
    path, _, _ = HOOK_PLANS[agent]
    banner(f"卸载 {agent}  -> {path}")
    if not path.exists():
        warn("文件不存在，跳过")
        return True
    backup(path)
    data = load_json(path)
    hooks = data.get("hooks", {}) or {}
    removed = 0
    for event in list(hooks.keys()):
        before = len(hooks[event] or [])
        hooks[event] = purge_hooks(hooks[event])
        removed += before - len(hooks[event])
    prune_empty(data)
    save_json(path, data)
    ok(f"清理了 {removed} 条 managed hook")
    return True

# ---------- Cursor（没有事件 hook，用 tasks + rules 让 agent 主动调） ----------

CURSOR_SETTINGS = (
    pathlib.Path.home() / "Library" / "Application Support" / "Cursor" / "User" / "settings.json"
)
CURSOR_RULE_HINT = (
    "Cursor 没有事件式 hook。已注册 VS Code task，"
    "请在 .cursor/rules 里告诉 agent：\n"
    "    - 任务完成时在终端运行 `{notify} done`\n"
    "    - 需要用户决策时运行 `{notify} ask`\n"
    "    - 出错时运行 `{notify} error`\n"
)

def _cursor_tasks() -> list[dict]:
    if IS_WIN:
        # Windows：type=process 直接起 python，绕开 cmd/pwsh 的引号差异。
        py = _pythonw()
        return [
            {
                "label": f"clawbot: {p}",
                "type": "process",
                "command": py,
                "args": [str(NOTIFY), p],
                "presentation": {"reveal": "never", "panel": "dedicated"},
                "problemMatcher": [],
                MARK_KEY: MARK_VAL,
            }
            for p in ("attention", "done", "error")
        ]
    return [
        {
            "label": f"clawbot: {p}",
            "type": "shell",
            "command": f"{NOTIFY} {p}",
            "presentation": {"reveal": "never", "panel": "dedicated"},
            "problemMatcher": [],
            MARK_KEY: MARK_VAL,
        }
        for p in ("attention", "done", "error")
    ]

def install_cursor() -> bool:
    # Cursor 的 tasks 其实是项目级 .vscode/tasks.json，不是 user settings.json。
    # 但我们还是把 "全局可调用" 放到项目级——优先找当前工作目录或 ~/.
    banner("安装 cursor")
    # 优先当前工作目录的 .vscode/tasks.json；不存在就写到用户 home 下面做个模板
    cwd = pathlib.Path.cwd()
    tasks_path = cwd / ".vscode" / "tasks.json"
    if not tasks_path.parent.exists():
        # 当前目录看起来不是项目，就只给用户提示而不写
        warn(f"当前目录没有 .vscode/，跳过项目级 tasks.json 写入（请到你的 Cursor 项目根目录重跑）")
    else:
        backup(tasks_path)
        data = load_json(tasks_path)
        if not data:
            data = {"version": "2.0.0", "tasks": []}
        tasks = data.setdefault("tasks", [])
        # 删旧 managed
        tasks[:] = [t for t in tasks if not (isinstance(t, dict) and t.get(MARK_KEY))]
        tasks.extend(_cursor_tasks())
        save_json(tasks_path, data)
        ok(f"写入：{tasks_path}")
    # 提示 rules
    print(CURSOR_RULE_HINT.format(notify=NOTIFY))
    return True

def uninstall_cursor() -> bool:
    banner("卸载 cursor")
    cwd = pathlib.Path.cwd()
    tasks_path = cwd / ".vscode" / "tasks.json"
    if not tasks_path.exists():
        warn(f"未找到 {tasks_path}，跳过")
        return True
    backup(tasks_path)
    data = load_json(tasks_path)
    tasks = data.get("tasks", [])
    before = len(tasks)
    data["tasks"] = [t for t in tasks if not (isinstance(t, dict) and t.get(MARK_KEY))]
    if not data["tasks"]:
        data.pop("tasks", None)
    save_json(tasks_path, data)
    ok(f"清理了 {before - len(data.get('tasks', []))} 条 managed tasks")
    return True

# ---------- status ----------

def status() -> None:
    banner("当前安装状态")
    for agent, (path, plan, _opts) in HOOK_PLANS.items():
        if not path.exists():
            print(f"  {agent:10s} [未安装] {path}")
            continue
        data = load_json(path)
        hooks = data.get("hooks", {}) or {}
        managed = []
        for event in plan:
            for e in hooks.get(event, []) or []:
                if isinstance(e, dict) and e.get(MARK_KEY):
                    pattern = ""
                    try:
                        pattern = e["hooks"][0]["command"]
                    except Exception:
                        pass
                    managed.append(f"{event}")
        mark = "\033[32m已安装\033[0m" if managed else "\033[33m未安装\033[0m"
        print(f"  {agent:10s} [{mark}] {path}  events={managed}")
    # cursor
    tasks_path = pathlib.Path.cwd() / ".vscode" / "tasks.json"
    if tasks_path.exists():
        data = load_json(tasks_path)
        n = sum(1 for t in data.get("tasks", []) if isinstance(t, dict) and t.get(MARK_KEY))
        print(f"  {'cursor':10s} [{'已安装' if n else '未安装'}] {tasks_path}  managed_tasks={n}")
    else:
        print(f"  {'cursor':10s} [未检测] {tasks_path} (当前目录无 .vscode/)")

# ---------- main ----------

INSTALLERS = {
    "codebuddy": (install_hook_based, uninstall_hook_based),
    "claude":    (install_hook_based, uninstall_hook_based),
    "cursor":    (install_cursor,     uninstall_cursor),
}

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["install", "uninstall", "status"])
    ap.add_argument("agents", nargs="*",
                    help=f"留空=全部；可选：{', '.join(INSTALLERS)}")
    args = ap.parse_args()

    ensure_executable(NOTIFY, SHOULD)

    if args.action == "status":
        status()
        return 0

    targets = args.agents or list(INSTALLERS.keys())
    unknown = [a for a in targets if a not in INSTALLERS]
    if unknown:
        err(f"未知 agent：{unknown}；可选：{list(INSTALLERS)}")
        return 2

    rc = 0
    for a in targets:
        install_fn, uninstall_fn = INSTALLERS[a]
        fn = install_fn if args.action == "install" else uninstall_fn
        try:
            if a in HOOK_PLANS:
                fn(a)
            else:
                fn()
        except Exception as e:
            err(f"{a}: {e}")
            rc = 1
    banner("完成")
    if args.action == "install":
        print("  提示：Codebuddy IDE / Claude Code / Cursor 需要重启才会加载新的 hook 配置")
    return rc

if __name__ == "__main__":
    sys.exit(main())
