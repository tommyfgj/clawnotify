/*
 * Agent Notifier Firmware  (Clawbot 硬件复用)
 * Board : ESP32-C3 SuperMini
 * 功能  : 接收串口指令，驱动 LY011E 电磁铁敲击桌面，
 *        用于提醒 Codebuddy / Claude Code / Cursor 的 agent 状态。
 *
 * 硬件接线（与 clawbot.ino 相同）：
 *   ESP32-C3 5V    --> 电磁铁 (+)
 *   ESP32-C3 GND   --> MOS S
 *   ESP32-C3 GPIO4 --> MOS G
 *   电磁铁 (-)     --> MOS D
 *   1N4148 并联在电磁铁 +/- 两端，黑带(K)朝 (+)
 *
 * 串口协议（115200 8N1，每条指令以 '\n' 结尾）：
 *   PING                       -> 回 "PONG"
 *   TAP <ms>                   -> 单击一次，吸合 ms 毫秒（上限 200ms）
 *   PATTERN <on,off,on,off...> -> 按序列敲击，单位 ms；on 上限 200，off ≥ 80
 *   PRESET <name>              -> 预置模式，见下表
 *   STOP                       -> 立刻停止当前模式
 *   STATUS                     -> 回当前状态
 *
 * 预置模式（可被 STOP 打断）：
 *   attention  单点   一次 ·              （需要关注）
 *   ask        两点   · ·                 （agent 在等输入 / 等审批）
 *   done       三点   · · ·               （任务完成）
 *   error      长-短-长  — · —            （出错，需要人工介入）
 *   heartbeat  极短一下                   （活着）
 *   sos        · · · — — — · · ·         （紧急）
 *
 * ⚠️ 安全约束（写死在固件里，不可由串口突破）：
 *   - 单次吸合时长硬限 <= 200ms（敲击用，不是吸持用）
 *   - 任意 10 秒窗口内，累计通电时长 <= 2000ms（防过热）
 *   - 冷却期：两次敲击之间至少 80ms
 */

#include <Arduino.h>

// ============ 引脚 ============
static const int  PIN_MOS         = 4;
static const int  PIN_LED         = 8;      // 板载 LED，低有效
static const bool LED_ACTIVE_LOW  = true;

// ============ 安全红线（硬编码） ============
static const unsigned long MAX_TAP_MS          = 200;   // 单次吸合 <= 200ms
static const unsigned long MIN_GAP_MS          = 80;    // 两次敲击之间最少 80ms
static const unsigned long DUTY_WINDOW_MS      = 10000; // 10 秒滑窗
static const unsigned long DUTY_BUDGET_MS      = 2000;  // 窗口内累计通电 <= 2s
static const unsigned long BOOT_DELAY_MS       = 1200;

// ============ 滑窗限幅：简单环形缓冲 ============
struct DutyEvent { unsigned long start; unsigned long dur; };
static const int DUTY_MAX = 64;
static DutyEvent dutyBuf[DUTY_MAX];
static int dutyHead = 0;
static int dutyCount = 0;

static unsigned long dutyUsedInWindow(unsigned long now) {
  unsigned long used = 0;
  int i = dutyHead;
  for (int k = 0; k < dutyCount; ++k) {
    i = (i - 1 + DUTY_MAX) % DUTY_MAX;
    const DutyEvent &e = dutyBuf[i];
    if (now - e.start > DUTY_WINDOW_MS) break;
    used += e.dur;
  }
  return used;
}

static void dutyRecord(unsigned long start, unsigned long dur) {
  dutyBuf[dutyHead] = { start, dur };
  dutyHead = (dutyHead + 1) % DUTY_MAX;
  if (dutyCount < DUTY_MAX) dutyCount++;
}

// ============ 底层敲击 ============
static void setCoil(bool on) {
  digitalWrite(PIN_MOS, on ? HIGH : LOW);
  digitalWrite(PIN_LED, (on ^ LED_ACTIVE_LOW) ? HIGH : LOW);
}

// 单次敲击，受安全红线保护。返回真实通电时长（可能被截短）。
static unsigned long safeTap(unsigned long ms) {
  if (ms == 0) return 0;
  if (ms > MAX_TAP_MS) ms = MAX_TAP_MS;

  const unsigned long now = millis();
  const unsigned long used = dutyUsedInWindow(now);
  if (used >= DUTY_BUDGET_MS) {
    Serial.println(F("WARN duty_budget_exhausted"));
    return 0;
  }
  if (used + ms > DUTY_BUDGET_MS) {
    ms = DUTY_BUDGET_MS - used;
  }

  setCoil(true);
  delay(ms);
  setCoil(false);
  dutyRecord(now, ms);
  return ms;
}

// ============ 模式执行（可被 STOP 中断） ============
static volatile bool g_stopFlag = false;

// 非阻塞地等待 ms，期间轮询串口以便响应 STOP
static bool interruptibleDelay(unsigned long ms);

static void runPattern(const int *seq, int n, const char *tag) {
  Serial.printf("OK pattern_start %s len=%d\n", tag, n);
  for (int i = 0; i < n && !g_stopFlag; ++i) {
    if (i % 2 == 0) {
      // on
      safeTap(seq[i]);
    } else {
      // off
      unsigned long gap = seq[i];
      if (gap < MIN_GAP_MS) gap = MIN_GAP_MS;
      if (!interruptibleDelay(gap)) break;
    }
  }
  setCoil(false);
  Serial.printf("OK pattern_end %s%s\n", tag, g_stopFlag ? " stopped" : "");
  g_stopFlag = false;
}

// ============ 预置模式 ============
static void presetAttention()  { static const int s[] = {60, 0};                         runPattern(s, 2,  "attention"); }
static void presetAsk()        { static const int s[] = {60,120, 60, 0};                 runPattern(s, 4,  "ask"); }
static void presetDone()       { static const int s[] = {60,120, 60,120, 60, 0};         runPattern(s, 6,  "done"); }
static void presetError()      { static const int s[] = {150,150, 60,150,150, 0};        runPattern(s, 6,  "error"); }
static void presetHeartbeat()  { static const int s[] = {25, 0};                         runPattern(s, 2,  "heartbeat"); }
static void presetSOS()        {
  static const int s[] = {
    60,120, 60,120, 60,300,          // · · ·
    150,120,150,120,150,300,         // — — —
    60,120, 60,120, 60,  0           // · · ·
  };
  runPattern(s, sizeof(s)/sizeof(s[0]), "sos");
}

// ============ 串口解析 ============
static String rxLine;

static void trimInPlace(String &s) {
  while (s.length() && (s[0] == ' ' || s[0] == '\t' || s[0] == '\r')) s.remove(0, 1);
  while (s.length() && (s[s.length()-1] == ' ' || s[s.length()-1] == '\t' || s[s.length()-1] == '\r')) s.remove(s.length()-1, 1);
}

static bool interruptibleDelay(unsigned long ms) {
  unsigned long t0 = millis();
  while (millis() - t0 < ms) {
    // 只检查 STOP，不解析其他指令，避免嵌套
    while (Serial.available()) {
      char c = Serial.read();
      if (c == '\n') {
        String line = rxLine; rxLine = "";
        trimInPlace(line);
        if (line.equalsIgnoreCase("STOP")) {
          g_stopFlag = true;
          Serial.println(F("OK stop"));
          return false;
        }
        // 其它指令在模式运行期间排队到下一轮（丢弃，避免并发）
      } else if (rxLine.length() < 200) {
        rxLine += c;
      }
    }
    delay(1);
  }
  return true;
}

static void handleLine(String line) {
  trimInPlace(line);
  if (!line.length()) return;

  // 大小写不敏感的命令识别
  String up = line; up.toUpperCase();

  if (up == "PING") {
    Serial.println(F("PONG"));
    return;
  }
  if (up == "STATUS") {
    Serial.printf("OK status duty_used_ms=%lu budget_ms=%lu window_ms=%lu\n",
                  dutyUsedInWindow(millis()), DUTY_BUDGET_MS, DUTY_WINDOW_MS);
    return;
  }
  if (up == "STOP") {
    g_stopFlag = true;
    Serial.println(F("OK stop"));
    return;
  }
  if (up.startsWith("TAP")) {
    long ms = line.substring(3).toInt();
    if (ms <= 0) { Serial.println(F("ERR tap_need_ms")); return; }
    unsigned long actual = safeTap((unsigned long)ms);
    Serial.printf("OK tap %lu\n", actual);
    return;
  }
  if (up.startsWith("PRESET")) {
    String name = line.substring(6); trimInPlace(name); name.toLowerCase();
    if      (name == "attention") presetAttention();
    else if (name == "ask")       presetAsk();
    else if (name == "done")      presetDone();
    else if (name == "error")     presetError();
    else if (name == "heartbeat") presetHeartbeat();
    else if (name == "sos")       presetSOS();
    else { Serial.println(F("ERR unknown_preset")); return; }
    return;
  }
  if (up.startsWith("PATTERN")) {
    String payload = line.substring(7); trimInPlace(payload);
    // 逗号分隔的整数列表：on,off,on,off,...
    int nums[64]; int n = 0;
    int start = 0;
    for (int i = 0; i <= (int)payload.length() && n < 64; ++i) {
      if (i == (int)payload.length() || payload[i] == ',') {
        if (i > start) {
          String tok = payload.substring(start, i); trimInPlace(tok);
          if (tok.length()) nums[n++] = tok.toInt();
        }
        start = i + 1;
      }
    }
    if (n < 1) { Serial.println(F("ERR pattern_empty")); return; }
    runPattern(nums, n, "custom");
    return;
  }

  Serial.printf("ERR unknown_cmd %s\n", line.c_str());
}

// ============ 初始化 ============
void setup() {
  pinMode(PIN_MOS, OUTPUT);
  digitalWrite(PIN_MOS, LOW);
  pinMode(PIN_LED, OUTPUT);
  digitalWrite(PIN_LED, LED_ACTIVE_LOW ? HIGH : LOW);

  Serial.begin(115200);
  delay(BOOT_DELAY_MS);

  Serial.println();
  Serial.println(F("[AgentNotifier] boot ok"));
  Serial.printf ("[AgentNotifier] PIN_MOS=%d MAX_TAP_MS=%lu DUTY=%lums/%lums\n",
                 PIN_MOS, MAX_TAP_MS, DUTY_BUDGET_MS, DUTY_WINDOW_MS);
  Serial.println(F("[AgentNotifier] try: PING | PRESET attention | TAP 60 | STOP"));
}

// ============ 主循环：纯串口驱动 ============
void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      String line = rxLine; rxLine = "";
      handleLine(line);
    } else if (rxLine.length() < 200) {
      rxLine += c;
    }
  }
  delay(1);
}
