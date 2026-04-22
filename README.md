# clawnotify — 用电磁铁给 AI agent 当物理提醒

> 基于自制硬件（ESP32‑C3 SuperMini + AO3400 MOS + LY011E 电磁铁）做的桌面敲击器，
> 把 Codebuddy / Claude Code / Cursor 这些 agent 的状态变成桌面上"咚、咚咚"的敲击声。

你在另一个窗口处理别的事，agent 说完话 → 桌面"· · ·"敲三下，不用再切回去看。

---

## 1. 总体架构

```
┌──────────────┐   stdin/CLI    ┌──────────────┐  USB‑Serial  ┌────────────────────┐
│  Agent IDE   │ ─────────────▶ │  notify.py   │ ───────────▶ │ ESP32‑C3 SuperMini │──▶ 电磁铁敲桌面
│ (Claude/…)   │  hook / task   │  (本机 CLI)  │   115200     │  agent_notifier    │
└──────────────┘                └──────────────┘              └────────────────────┘
```

- **硬件**：见 [`ASSEMBLY.md`](ASSEMBLY.md)（机械 + BOM + 装配步骤） 和 [`wiring.html`](wiring.html)（可视化接线图）。
- **固件**：[`firmware/agent_notifier/agent_notifier.ino`](firmware/agent_notifier/agent_notifier.ino)，纯串口协议驱动，写死了 3 道安全红线。
- **桥接**：[`tools/notify.py`](tools/notify.py)，一行命令搞定敲击。
- **集成**：给三款 agent 各配一份示例（hooks / tasks / 脚本包装）。

---

## 2. 烧录固件

和普通 ESP32‑C3 一样，sketch 指向 `firmware/agent_notifier/`：

**Arduino IDE**
1. 打开 `firmware/agent_notifier/agent_notifier.ino`。
2. 开发板选 `ESP32C3 Dev Module`，`USB CDC On Boot = Enabled`。
3. 编译 → 上传。
4. 打开串口监视器（115200），应看到：
   ```
   [AgentNotifier] boot ok
   [AgentNotifier] PIN_MOS=4 MAX_TAP_MS=200 DUTY=2000ms/10000ms
   [AgentNotifier] try: PING | PRESET attention | TAP 60 | STOP
   ```

**arduino‑cli**
```bash
cd firmware
arduino-cli compile --fqbn esp32:esp32:esp32c3 agent_notifier
arduino-cli upload  --fqbn esp32:esp32:esp32c3 -p /dev/cu.usbmodemXXXX agent_notifier
```

---

## 3. 安装主机端

```bash
python3 -m pip install pyserial
./tools/notify.py ping
# -> PONG
```

端口自动探测 `/dev/cu.usbmodem*`、`/dev/cu.usbserial*` 等。也可显式指定：

```bash
export CLAWBOT_PORT=/dev/cu.usbmodem1101
```

### 3.1 一键挂接 agent hook（推荐）

用 `tools/install_hooks.py` 自动把 hook 合并到各家 agent 的 settings：

```bash
# 装所有检测到的 agent
./tools/install_hooks.py install

# 或只装一个
./tools/install_hooks.py install codebuddy
./tools/install_hooks.py install claude
./tools/install_hooks.py install cursor       # 需 cd 到 Cursor 项目根目录

# 查看状态 / 卸载
./tools/install_hooks.py status
./tools/install_hooks.py uninstall
./tools/install_hooks.py uninstall claude
```

特性：
- **幂等**：重复 install 不会重复插条目（通过 `_clawbot_managed` 标记识别自己）。
- **安全合并**：保留用户原有的 `enabledPlugins` / 其他 hook，不会覆盖。
- **自动备份**：每次修改前把原文件复制到 `<file>.bak.<时间戳>`。
- **卸载干净**：只删自己打的标记条目，用户手写的 hook 原样保留。

### 3.2 智能静音：只在你离开 IDE 时才敲

**GUI agent（Codebuddy / Cursor）走完整判定** —— hook 先过 `tools/should_notify.sh`，按 bundle id 认窗口：

| 条件 | 行为 |
|---|---|
| 屏幕已锁 | 敲 |
| 前台 app 不是你当前在用的 IDE（bundle id 不匹配）| 敲 |
| IDE 在前台但 **≥20 秒** 没动键鼠 | 敲 |
| 你正盯着 IDE 输入 | 静音 |

**CLI agent（Claude Code）一律敲，不走静音** —— 因为它跑在终端里，典型用法是"让它在后台干活、我切走处理别的事"。这种场景下键鼠一直在动（你在别处打字），"idle → 静音"会漏掉所有提醒；"前台 app" 也没法精确锚定。所以 installer 装 Claude 时直接 **绕过 `should_notify.sh`**，三个事件（Stop / Notification / SubagentStop）无条件敲。

想给 GUI agent 调整行为时，环境变量覆盖默认值：
```bash
# 多个 GUI app 都算"在看"
FOCUS_BUNDLE_IDS="com.tencent.codebuddycn,com.todesktop.230313mzl4w4u92"

# 离开阈值改成 60 秒
IDLE_THRESHOLD_SEC=60
```

---

## 4. 指令速查

### 预置模式（推荐）

| 命令                           | 敲击节奏         | 语义               |
|--------------------------------|------------------|--------------------|
| `notify.py attention`          | · （一下）       | 需要关注           |
| `notify.py ask`                | · ·              | agent 在等输入/审批 |
| `notify.py done`               | · · ·            | 任务完成           |
| `notify.py error`              | — · —            | 出错，需要人工介入 |
| `notify.py heartbeat`          | 极短一下         | 活着（心跳）       |
| `notify.py sos`                | · · · — — — · · ·| SOS                |

### 自由模式

```bash
notify.py tap 60                      # 单次吸合 60ms（上限 200）
notify.py pattern 60,120,60,120,60    # on,off,on,off,... （ms）
notify.py morse "HI"                  # 文本 → Morse 节奏
notify.py status                      # 查询 10 秒滑窗内累计通电
notify.py stop                        # 打断正在执行的模式
```

---

## 5. 接入 Claude Code（hooks）

Claude Code 官方支持 `Stop` / `Notification` / `SubagentStop` 等事件 hook。用 `install_hooks.py` 一键装最省事；想手动合并见 [`tools/examples/claude_code_hooks.json`](tools/examples/claude_code_hooks.json)。

末尾的 `&` 让敲击异步进行，不阻塞 agent 主循环。

---

## 6. 接入 Codebuddy

Codebuddy 实际上有 **两套 Hook 体系**，按你用的版本挑一套。

### 6.1 Codebuddy IDE（外部版） — settings.json 式，兼容 Claude Code

- 配置位置：项目级 `.codebuddy/settings.json` 或个人级 `~/.codebuddy/settings.json`
- 事件：`SessionStart` / `SessionEnd` / `PreToolUse` / `PostToolUse` /
  `UserPromptSubmit` / **`Stop`** / `PreCompact`
- 环境变量：`CODEBUDDY_PROJECT_DIR`（同时兼容 `CLAUDE_PROJECT_DIR`）

把 [`tools/examples/codebuddy_ide_settings.json`](tools/examples/codebuddy_ide_settings.json) 合并到你的 settings.json 即可：
`Stop → done`、`UserPromptSubmit → heartbeat`、`PostToolUse(execute_command) → attention`。

### 6.2 Codebuddy 内网版插件 — 插件设置里的 Hooks 选项卡

- 开启位置：**插件设置 → Hooks → 打开开关 → 管理 Hooks**（在 Knot 上配置）
- 事件：`beforeShellExecution` / `beforeMCPExecution` / `afterShellExecution` /
  `afterMCPExecution` / `afterSearchRplaceFileEdit` / `afterFileEdit` /
  `afterFileRead` / `beforeSubmitPrompt` / `afterAgentResponse` / **`stop`**
- 协议：stdin 喂 JSON，stdout 回 JSON；控制型 hook 可返回 `{"continue":"allow|deny|ask"}`

推荐接入方式（敲桌子只关心"agent 循环结束"）：
- **stop** 事件 → 命令填 `<REPO>/tools/codebuddy_stop_hook.sh`
  它会按 `status` 自动敲 `done / attention / error`。
- 想更频繁的心跳，可再挂 **afterAgentResponse** → `tools/codebuddy_response_hook.sh`。

### 6.3 不想碰 hook 的兜底方案

- **Automations**：自动化任务的 prompt 最后一步写"运行 `tools/notify.py done`"，跑完自动敲三下。
- **命令包装**：
  ```bash
  tools/wrap_notify.sh done -- pytest -q           # 成功敲三下，失败敲"长-短-长"
  tools/wrap_notify.sh done --fail error -- make   # 可自定义失败模式
  ```

---

## 7. 接入 Cursor

Cursor 没有官方 hook，但可以借道 VS Code 的 `tasks.json`。把
[`tools/examples/cursor_tasks.json`](tools/examples/cursor_tasks.json) 里的内容合并到项目的 `.vscode/tasks.json`，
之后用 `⌘⇧P → Run Task → clawbot: done`；也可以让 Cursor 的 agent
在完成时执行终端命令 `notify.py done`。

---

## 8. 串口协议（给想自己集成的人）

所有指令 ASCII，以 `\n` 结尾，115200 8N1。

```
PING                             -> PONG
STATUS                           -> OK status duty_used_ms=... budget_ms=2000 window_ms=10000
TAP <ms>                         -> OK tap <真实通电时长>        （ms 会被钳到 <=200）
PATTERN <on,off,on,off,...>      -> OK pattern_start custom ...  / OK pattern_end custom
PRESET attention|ask|done|error|heartbeat|sos
STOP                             -> OK stop                      （可打断正在跑的 PRESET/PATTERN）
未知指令                         -> ERR unknown_cmd ...
占空超预算                       -> WARN duty_budget_exhausted
```

序列里奇数项是通电（on），偶数项是间隔（off）。

---

## 9. 安全红线（硬编码，串口改不了）

| 限制                  | 数值        | 原因 |
|-----------------------|-------------|------|
| 单次吸合              | ≤ **200ms** | 这是敲击用，不是吸持；LY011E 连续通电 3s 就会过热 |
| 两次敲击最小间隔      | **80ms**    | 给 MOS / 线圈 一点恢复时间 |
| 10 秒滑窗累计通电     | ≤ **2000ms**| 限制平均占空比约 20%，线圈不会烫手 |
| 1N4148 续流二极管     | **必须装** | 关断瞬间反向电动势会击穿 MOS |
| USB 电源              | **≥ 1A**    | 吸合瞬间电流大，弱电源会导致 ESP32 复位 |

超出预算时固件会回 `WARN duty_budget_exhausted`，该次敲击被丢弃；过一会儿自动恢复。

---

## 10. 自测清单

```bash
# 1) 连通
tools/notify.py ping              # PONG

# 2) 单击
tools/notify.py tap 60            # OK tap 60

# 3) 预置
tools/notify.py done              # · · ·
tools/notify.py error             # — · —

# 4) Morse
tools/notify.py morse "SOS"       # 对比 notify.py sos 听起来一致

# 5) 过载保护
for i in $(seq 1 50); do tools/notify.py tap 200; done  # 中途会出现 WARN，但设备不会烫

# 6) 中断
tools/notify.py sos &
sleep 0.5
tools/notify.py stop              # OK stop
```

---

## 11. 目录结构

```
clawnotify/
├─ README.md                           # 本文档
├─ ASSEMBLY.md                         # 硬件装配（BOM + 机械 + 电路）
├─ wiring.html                         # 可视化接线图（浏览器打开）
├─ firmware/
│  └─ agent_notifier/agent_notifier.ino# 串口驱动固件
└─ tools/
   ├─ notify.py                        # 主机端 CLI
   ├─ install_hooks.py                 # 一键装/卸 hook（codebuddy/claude/cursor）
   ├─ should_notify.sh                 # 智能静音：只在你离开 IDE 时才敲
   ├─ wrap_notify.sh                   # 命令包装器（成功/失败自动敲）
   ├─ codebuddy_stop_hook.sh           # 内网版插件 stop hook 适配器
   ├─ codebuddy_response_hook.sh       # 内网版插件 afterAgentResponse 适配器
   └─ examples/
      ├─ claude_code_hooks.json        # 合并到 ~/.claude/settings.json
      ├─ codebuddy_ide_settings.json   # 合并到 ~/.codebuddy/settings.json
      └─ cursor_tasks.json             # 合并到 .vscode/tasks.json
```

---

## License

MIT. See [LICENSE](LICENSE).
