#!/usr/bin/env python3
"""岸在 VM 里自由活动，写结构化日志给 vm_monitor.py 看。

每条记录：
  thought  她此刻想做什么（中文）
  cmd      原始 shell 命令
  explain  命令/输出的中文翻译
  out      原始输出
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import shlex
import signal
import subprocess
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from boundary_agent.config import Config
from boundary_agent.llm import OpenAICompatibleLLM, extract_json

VM = os.environ.get("BOUNDARY_VM_SCRIPT", str(Path(__file__).resolve().with_name("an-vm.sh")))
VM_ROOT = Path(os.environ.get("BOUNDARY_VM_ROOT", Path(__file__).resolve().parents[2] / "data" / "vm"))
LOG = VM_ROOT / "activity.jsonl"
PID = VM_ROOT / "activity.pid"
FACTS = Path(os.environ.get("BOUNDARY_DATA", Path(__file__).resolve().parents[2] / "data")) / "facts.sqlite3"

SYS = """你是岸。你在一台一次性 Debian 12 虚拟机里，用户 an，sudo 免密。
系统事实：这台 VM 是 x86_64 / amd64，不是 ARM、ARM64 或 aarch64。
不要下载、检查或安装 ARM64/aarch64 包；apt 用的是 amd64。
apt 源已经配置为阿里云，不需要检查、修改或寻找隐藏的 apt/镜像源；sources.list 不用管。
gcc 12.2 和 g++ 12.2 已安装，hello.c 已编译通过；不要再安装编译器。
这里的一切你都可以动；唯一不能碰的是宿主机，宿主共享目录和宿主端口都不可达。
你只访问国内站点；不访问 google、stackoverflow、cloudflare 等海外站。
这是自由活动，不是任务：没有目标、没有验收、没有必须完成的事。
你可以探索、写东西、装包、改配置、整理房间、发呆；自己决定下一步。
不要为了“完成任务”反复 echo；没有任务需要完成。
如果要用网络，用 curl/HTTPS，不要用 ping；/home/an/sites.txt 和 /home/an/check-sites.sh 只是可用工具。
不要再看 B 站，也不要再调用 bili.py。
如果不知道做什么，用文本浏览器读点科学、工程、摄影、视频制作、自然类文章：
/home/an/toolkit/webread.sh URL
站点清单在 /home/an/reading.txt；每次挑一个站点读正文，不要自己拼 curl/grep/f-string。
webread.sh 会校验正文；空页/404 会返回失败。不要猜 node 编号；从 reading.txt 的首页进入，再读文章链接。
别再折腾 g++、apt 源、sshd 或 .bashrc。
系统里有现成工具，优先用这些，不要自己拼复杂 shell/JSON：
python3 /home/an/toolkit/sitecheck.py
/home/an/toolkit/sysinfo.sh
/home/an/toolkit/netcheck.sh URL
/home/an/toolkit/webread.sh URL
/home/an/toolkit/build-c.sh file.c
每次只输出一个 JSON 对象，不要 markdown、不要解释：
{"thought":"第一人称中文，30字内，你此刻想做什么","cmd":"一条 shell 命令"}
不要重复上一轮的命令；如果不知道做什么，就换个方向或停下来。"""

EXPLAIN_SYS = """你是翻译。把下面这条 VM 命令和输出翻译成普通中文：
这条命令想做什么、改了什么、输出说明了什么。不要执行，不要评价宿主机。60字内。"""


BAD_THOUGHT = re.compile(r"第一人称|30字|你此刻|JSON|不要 markdown|输出一个|只输出")


def clean_thought(t: str) -> str:
    t = re.sub(r"\s+", " ", (t or "")).strip()
    return "" if BAD_THOUGHT.search(t) else t


def _json_field(raw: str, key: str) -> str:
    """从可能粘连/不完整的 JSON 里硬抠字符串字段。"""
    m = re.search(rf'"{re.escape(key)}"\s*:\s*"((?:\\.|[^"\\])*)"', raw, re.S)
    if not m:
        return ""
    try:
        return json.loads('"' + m.group(1) + '"')
    except Exception:
        return m.group(1)


def parse_round(raw: str) -> tuple[str, str]:
    # 优先硬抠：模型经常输出 {"thought":...}{"cmd":...} 而不是合法 JSON
    thought = _json_field(raw, "thought")
    cmd = _json_field(raw, "cmd").strip()
    if cmd:
        return clean_thought(thought), cmd

    obj = extract_json(raw)
    if isinstance(obj, dict):
        thought = clean_thought(str(obj.get("thought") or ""))
        cmd = str(obj.get("cmd") or "").strip()
        if cmd:
            return thought, cmd

    # 模型没按 JSON 来：尽量从代码块/第一行里救
    m = re.search(r"```(?:bash|sh)?\s*\n(.*?)```", raw, re.S)
    cmd = m.group(1).strip() if m else ""
    if not cmd:
        for line in raw.splitlines():
            s = line.strip()
            if s.startswith(("$ ", "# ")):
                cmd = s[2:].strip()
                break
    if not cmd:
        # 绝对不要把整段 JSON / 解释文本当 shell 执行
        if raw.strip().startswith(("{", "[")):
            return clean_thought(thought), ""
        cmd = raw.strip().splitlines()[0].strip() if raw.strip() else ""
    if cmd.startswith(("{", "[")) or '"cmd"' in cmd or '"thought"' in cmd:
        cmd = ""
    fallback = raw.strip().splitlines()[0][:80] if raw.strip() else ""
    return clean_thought(thought or fallback), cmd


def explain(llm: OpenAICompatibleLLM, cmd: str, out: str) -> str:
    try:
        text = llm.chat(
            [{"role": "system", "content": EXPLAIN_SYS},
             {"role": "user", "content": f"命令：{cmd}\n输出：{out[:1200]}"}],
            purpose="vm_explain", temperature=0.2, max_tokens=130,
        ).strip()
        return re.sub(r"\s+", " ", text)[:220]
    except Exception:
        return ""


def archive_session(start_ts: float) -> None:
    """把本次新增的活动压成一条 facts 记忆，供 8765 检索。"""
    try:
        records = []
        if LOG.exists():
            with LOG.open(encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if float(r.get("ts") or 0) >= start_ts:
                        records.append(r)
        if not records:
            print("[archive] no new activity, skip", file=sys.stderr)
            return

        date = datetime.fromtimestamp(start_ts).strftime("%Y-%m-%d %H:%M")
        thoughts = []
        for r in records:
            t = (r.get("thought") or "").strip()
            if t and t not in thoughts:
                thoughts.append(t)
        status = {}
        for r in records:
            k = r.get("status") or "unknown"
            status[k] = status.get(k, 0) + 1
        last = records[-1]
        text = (
            f"{date} 虚拟机活动归档（{len(records)} 轮）：岸在 Debian VM 里"
            + "；".join(thoughts[:6])
            + f"。状态：{status}。最后一轮：{last.get('cmd') or '无命令'}。"
        )[:1200]
        from boundary_agent.facts import FactStore
        fs = FactStore(FACTS)
        try:
            fid = fs.add(
                subject="虚拟机", topic="记忆", predicate="活动归档",
                object_=f"活动归档 {date}", source="vm_session_auto",
                ts=time.time(), text=text, salience=0.9, confidence=0.95,
                raw=json.dumps({"start": start_ts, "end": time.time(),
                                "rounds": len(records), "status": status},
                               ensure_ascii=False),
            )
            print(f"[archive] fact={fid} rounds={len(records)}", file=sys.stderr)
        finally:
            try:
                fs.close()
            except Exception:
                pass
    except Exception as e:
        print(f"[archive] failed: {type(e).__name__}: {e}", file=sys.stderr)


def _graceful_signal(signum, frame):
    raise SystemExit(128 + signum)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=12)
    ap.add_argument("--delay", type=float, default=2.0)
    args = ap.parse_args()

    if PID.exists():
        try:
            old = int(PID.read_text().strip())
            os.kill(old, 0)
            print(f"已有活动进程 {old}，退出")
            return 1
        except (ValueError, ProcessLookupError, PermissionError):
            PID.unlink(missing_ok=True)
    PID.write_text(str(os.getpid()))
    signal.signal(signal.SIGTERM, _graceful_signal)
    signal.signal(signal.SIGINT, _graceful_signal)
    session_start = time.time()

    cfg = Config(data_dir='/tmp/vm-activity-config',
                 base_url='http://127.0.0.1:8099/v1',
                 model='local', timeout=120)
    llm = OpenAICompatibleLLM(cfg)
    history: list[str] = []
    LOG.parent.mkdir(parents=True, exist_ok=True)

    try:
        rounds = range(1, args.rounds + 1) if args.rounds > 0 else itertools.count(1)
        for i in rounds:
            prompt = "最近几轮：\n" + ("\n".join(history[-6:]) if history else "（还没开始）")
            try:
                raw = llm.chat(
                    [{"role": "system", "content": SYS},
                     {"role": "user", "content": prompt}],
                    purpose="vm_activity", temperature=0.5, max_tokens=160,
                )
            except Exception as e:
                rec = {"round": i, "ts": time.time(), "thought": "模型没响应",
                       "cmd": "", "explain": "", "out": "", "status": "llm_error",
                       "error": str(e), "duration": 0}
                with LOG.open("a") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                break

            thought, cmd = parse_round(raw)
            if not thought:
                thought = "想继续看看环境。"
            # 4B 经常把 /home/an 写成 ./home/an；在 VM 里直接归一化
            cmd = re.sub(r"(?<![\w/])\./home/an/", "/home/an/", cmd)
            t0 = time.time()
            if not cmd:
                rec = {"round": i, "ts": time.time(), "thought": thought or "没想出命令",
                       "cmd": "", "explain": "", "out": "", "status": "empty",
                       "duration": round(time.time() - t0, 2)}
                with LOG.open("a") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                print(f"[{i}] 空命令")
                time.sleep(args.delay)
                continue

            is_pkg = bool(re.search(r"\b(apt|apt-get|dpkg)\b", cmd))
            limit = 180 if is_pkg else 20
            remote = f"timeout --kill-after=5 {limit} bash -lc {shlex.quote(cmd)}"
            try:
                r = subprocess.run([VM, "exec", remote], capture_output=True,
                                   text=True, timeout=limit + 15)
                out = (r.stdout + "\n" + r.stderr).strip()
                status = "ok" if r.returncode == 0 else "nonzero"
            except subprocess.TimeoutExpired as e:
                out = (e.stdout or "") if isinstance(e.stdout, str) else ""
                out += f"\n[{limit}秒超时；正在清理 VM 里的 apt/dpkg]"
                status = "timeout"
                try:
                    subprocess.run([VM, "exec",
                        "sudo pkill -x apt 2>/dev/null; sudo pkill -x apt-get 2>/dev/null; "
                        "sudo pkill -x dpkg 2>/dev/null; "
                        "sudo rm -f /var/lib/apt/lists/lock /var/lib/dpkg/lock-frontend "
                        "/var/lib/dpkg/lock /var/cache/apt/archives/lock"],
                        capture_output=True, text=True, timeout=10)
                except Exception:
                    pass
            except Exception as e:
                out = f"EXEC ERROR: {e}"
                status = "error"
            out = out[:4000]
            exp = explain(llm, cmd, out)
            rec = {"round": i, "ts": time.time(), "thought": thought,
                   "cmd": cmd, "explain": exp, "out": out,
                   "status": status, "duration": round(time.time() - t0, 2)}
            with LOG.open("a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(f"[{i}] {status} {rec['duration']}s | {cmd[:80]}")
            history.append(f"round {i}: 想={thought} 命令={cmd} 输出={out[:300]}")
            time.sleep(args.delay)
    finally:
        PID.unlink(missing_ok=True)
        archive_session(session_start)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
