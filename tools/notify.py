#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
notify.py — Agent Notifier 主机端 CLI

用法：
    ./notify.py attention            # 单击
    ./notify.py ask                  # 两下（agent 等输入）
    ./notify.py done                 # 三下（任务完成）
    ./notify.py error                # 长-短-长（出错）
    ./notify.py heartbeat            # 心跳
    ./notify.py sos                  # SOS
    ./notify.py tap 60               # 单次吸合 60ms
    ./notify.py pattern 60,120,60    # 自定义序列（on,off,on,off,...）
    ./notify.py morse "HI"           # 把文本翻成 Morse 敲击
    ./notify.py status               # 查询占空状态
    ./notify.py ping                 # 连通性测试

环境变量：
    CLAWBOT_PORT   强制指定串口，例如 /dev/cu.usbmodem1101
                   不设置时自动探测 usbmodem* / usbserial* / wchusbserial*
    CLAWBOT_BAUD   波特率，默认 115200

退出码：
    0   成功或固件返回 OK
    2   串口打不开 / 未找到设备
    3   固件返回 ERR
    4   超时
"""
import argparse
import glob
import os
import sys
import time

try:
    import serial  # pyserial
except ImportError:
    sys.stderr.write(
        "[notify] 缺少 pyserial，请先安装：  python3 -m pip install pyserial\n"
    )
    sys.exit(2)


BAUD = int(os.environ.get("CLAWBOT_BAUD", "115200"))
PORT_PATTERNS = [
    "/dev/cu.usbmodem*",
    "/dev/cu.usbserial*",
    "/dev/cu.wchusbserial*",
    "/dev/ttyUSB*",
    "/dev/ttyACM*",
]


def _find_port_windows() -> str:
    # ESP32-C3 USB-CDC: VID=0x303A, PID=0x1001
    from serial.tools import list_ports
    ports = list(list_ports.comports())
    esp = [p.device for p in ports if (p.vid, p.pid) == (0x303A, 0x1001)]
    if esp:
        return sorted(esp)[0]
    # 退化：任意非 COM1 的 USB 串口（CH340/CP210x 等第三方 USB-UART）
    others = [p.device for p in ports
              if p.device.upper() != "COM1" and (p.vid is not None)]
    if not others:
        sys.stderr.write(
            "[notify] 未找到串口设备。请插好 ESP32-C3，或用 CLAWBOT_PORT 显式指定（例如 COM5）。\n"
        )
        sys.exit(2)
    return sorted(others)[0]


def find_port() -> str:
    env = os.environ.get("CLAWBOT_PORT", "").strip()
    if env:
        return env
    if sys.platform == "win32":
        return _find_port_windows()
    candidates = []
    for pat in PORT_PATTERNS:
        candidates.extend(sorted(glob.glob(pat)))
    if not candidates:
        sys.stderr.write(
            "[notify] 未找到串口设备。请插好 ESP32-C3，或用 CLAWBOT_PORT 显式指定。\n"
        )
        sys.exit(2)
    return candidates[0]


def open_serial(port: str) -> "serial.Serial":
    try:
        ser = serial.Serial(port, BAUD, timeout=1.0, write_timeout=1.0)
    except serial.SerialException as e:
        sys.stderr.write(f"[notify] 打不开 {port}: {e}\n")
        sys.exit(2)
    # ESP32-C3 CDC 刚打开时会复位，等固件 boot
    time.sleep(0.05)
    ser.reset_input_buffer()
    return ser


def send_cmd(ser, cmd: str, wait_s: float = 2.0) -> str:
    """发一条指令，读到 OK/ERR/PONG 行为止；超时返回已收到内容。"""
    line = (cmd.strip() + "\n").encode("ascii", errors="ignore")
    ser.write(line)
    ser.flush()
    buf = []
    t0 = time.time()
    while time.time() - t0 < wait_s:
        raw = ser.readline()
        if not raw:
            continue
        try:
            s = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        except Exception:
            continue
        if not s:
            continue
        buf.append(s)
        # 终止条件
        head = s.split(" ", 1)[0].upper()
        if head in ("OK", "ERR", "PONG", "WARN"):
            # 对于 pattern_start，我们还要等 pattern_end
            if "pattern_start" in s:
                continue
            return "\n".join(buf)
    return "\n".join(buf) if buf else "(timeout)"


# --------- Morse ---------
MORSE = {
    "A": ".-",   "B": "-...", "C": "-.-.", "D": "-..",  "E": ".",
    "F": "..-.", "G": "--.",  "H": "....", "I": "..",   "J": ".---",
    "K": "-.-",  "L": ".-..", "M": "--",   "N": "-.",   "O": "---",
    "P": ".--.", "Q": "--.-", "R": ".-.",  "S": "...",  "T": "-",
    "U": "..-",  "V": "...-", "W": ".--",  "X": "-..-", "Y": "-.--",
    "Z": "--..",
    "0": "-----","1": ".----","2": "..---","3": "...--","4": "....-",
    "5": ".....","6": "-....","7": "--...","8": "---..","9": "----.",
}
# 单位时间（ms）；点=60，划=150；码元间=120；字母间=300；单词间=700
DOT = 60
DASH = 150
INTRA = 120
INTER_LETTER = 300
INTER_WORD = 700


def morse_pattern(text: str) -> list:
    seq = []
    text = text.upper().strip()
    words = text.split()
    for wi, word in enumerate(words):
        letters = [ch for ch in word if ch in MORSE]
        for li, ch in enumerate(letters):
            code = MORSE[ch]
            for ci, sym in enumerate(code):
                seq.append(DOT if sym == "." else DASH)
                # 码元间
                if ci != len(code) - 1:
                    seq.append(INTRA)
            # 字母间
            if li != len(letters) - 1:
                seq.append(INTER_LETTER)
        # 单词间
        if wi != len(words) - 1:
            seq.append(INTER_WORD)
    return seq


# --------- 子命令 ---------
def cmd_preset(ser, name: str) -> str:
    return send_cmd(ser, f"PRESET {name}", wait_s=6.0)


def cmd_tap(ser, ms: int) -> str:
    return send_cmd(ser, f"TAP {ms}", wait_s=2.0)


def cmd_pattern(ser, seq) -> str:
    payload = ",".join(str(int(x)) for x in seq)
    # 固件上限 64 个数
    if len(seq) > 64:
        sys.stderr.write(f"[notify] 序列太长（{len(seq)}>64），已截断\n")
        seq = seq[:64]
        payload = ",".join(str(int(x)) for x in seq)
    return send_cmd(ser, f"PATTERN {payload}", wait_s=max(3.0, sum(seq) / 1000 + 2.0))


def _maybe_detach() -> None:
    """Windows：`--detach` 让脚本把自己重生成一个脱离控制台的子进程并立即退出。

    hook 回调被 Claude Code 通过 cmd.exe 短生命周期 shell 调起；如果直接用
    `start /b` 挂后台，父 cmd 退出会顺带把子进程干掉。走 DETACHED_PROCESS +
    CREATE_NEW_PROCESS_GROUP 才能真正脱离。
    """
    if sys.platform != "win32" or "--detach" not in sys.argv:
        return
    import subprocess
    args = [a for a in sys.argv if a != "--detach"]
    DETACHED_PROCESS = 0x00000008
    CREATE_NO_WINDOW = 0x08000000
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    subprocess.Popen(
        [sys.executable] + args,
        creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    sys.exit(0)


def main():
    _maybe_detach()
    ap = argparse.ArgumentParser(
        prog="notify",
        description="Agent Notifier — 通过电磁铁敲击提醒",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    for p in ("attention", "ask", "done", "error", "heartbeat", "sos"):
        sub.add_parser(p, help=f"预置模式：{p}")

    sp = sub.add_parser("tap", help="单次吸合，单位 ms（<=200）")
    sp.add_argument("ms", type=int)

    sp = sub.add_parser("pattern", help="自定义序列，逗号分隔 on,off,on,off,...")
    sp.add_argument("seq", type=str)

    sp = sub.add_parser("morse", help="把文本翻译成 Morse 敲击")
    sp.add_argument("text", type=str)

    sub.add_parser("status", help="查询占空状态")
    sub.add_parser("ping", help="连通性测试")
    sub.add_parser("stop", help="停止当前模式")

    args = ap.parse_args()
    port = find_port()
    ser = open_serial(port)

    try:
        if args.cmd in ("attention", "ask", "done", "error", "heartbeat", "sos"):
            resp = cmd_preset(ser, args.cmd)
        elif args.cmd == "tap":
            resp = cmd_tap(ser, args.ms)
        elif args.cmd == "pattern":
            seq = [int(x) for x in args.seq.split(",") if x.strip()]
            resp = cmd_pattern(ser, seq)
        elif args.cmd == "morse":
            seq = morse_pattern(args.text)
            if not seq:
                sys.stderr.write("[notify] morse 序列为空\n")
                sys.exit(3)
            resp = cmd_pattern(ser, seq)
        elif args.cmd == "status":
            resp = send_cmd(ser, "STATUS", wait_s=1.0)
        elif args.cmd == "ping":
            resp = send_cmd(ser, "PING", wait_s=1.0)
        elif args.cmd == "stop":
            resp = send_cmd(ser, "STOP", wait_s=1.0)
        else:
            ap.error(f"unknown cmd {args.cmd}")
            return
    finally:
        try:
            ser.close()
        except Exception:
            pass

    print(resp)
    last = resp.strip().splitlines()[-1] if resp.strip() else ""
    head = last.split(" ", 1)[0].upper() if last else ""
    if head == "ERR":
        sys.exit(3)
    if last == "(timeout)":
        sys.exit(4)
    sys.exit(0)


if __name__ == "__main__":
    main()
