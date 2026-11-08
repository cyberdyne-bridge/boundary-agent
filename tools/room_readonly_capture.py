#!/usr/bin/env python3
"""抓一份岸的房间只读副本：静态 JSON / HTML / 给岸看的文字清单。

只 GET 8765 的只读接口，不写入 agent，不改 live 房间。
"""
from __future__ import annotations

import html
import json
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8765"
from _paths import OUT as _OUT
OUT = _OUT
PANELS = ["state_events", "body", "promises", "timeline", "mind",
          "shadow", "motifs", "coins", "turns", "rejections"]


def get(path: str):
    req = urllib.request.Request(BASE + path, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8"))


def capture() -> dict:
    room = get("/api/room")
    panels = {}
    for name in PANELS:
        d = get("/api/panel?name=" + name)
        panels[name] = d.get("rows", d)
    return {
        "readonly": True,
        "captured_at": time.time(),
        "captured_local": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": BASE,
        "room": room,
        "panels": panels,
    }


def render_html(snap: dict) -> str:
    when = html.escape(snap["captured_local"])
    room = snap["room"]
    pretty = lambda x: html.escape(json.dumps(x, ensure_ascii=False, indent=2))
    promises = len(snap["panels"].get("promises") or [])
    cards = []
    for name in PANELS:
        rows = snap["panels"].get(name)
        count = len(rows) if isinstance(rows, list) else 1
        cards.append(
            f'<details class="card"><summary><b>{html.escape(name)}</b>'
            f'<span>{count} 项</span></summary><pre>{pretty(rows)}</pre></details>'
        )
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>岸 · 房间只读快照</title>
<style>
:root{{--bg:#0e1018;--panel:#161823;--line:#2d3352;--fg:#e8eaf2;--dim:#8b91a8;--accent:#4D6BFE}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.6 system-ui,-apple-system,"Noto Sans SC",sans-serif}}
header{{max-width:1100px;margin:0 auto;padding:24px 20px 10px}}
h1{{margin:0;font-size:22px;font-weight:500}}h1 span{{color:var(--accent);margin-left:8px}}
.sub{{color:var(--dim);font-size:12.5px;margin-top:6px}}
.badge{{display:inline-block;margin-top:10px;padding:4px 10px;border:1px solid var(--accent);
border-radius:20px;color:#9fb3ff;font-size:12px}}
main{{max-width:1100px;margin:0 auto;padding:12px 20px 40px}}
.summary{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin:14px 0}}
.kv{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px}}
.kv b{{display:block;color:var(--dim);font-size:11px;font-weight:400;margin-bottom:4px}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px}}
.card summary{{cursor:default;display:flex;justify-content:space-between;gap:8px;color:#cfd5e6}}
.card summary span{{color:var(--dim);font-size:11px}}
pre{{white-space:pre-wrap;word-break:break-word;background:#0b0d14;border:1px solid #20253a;
border-radius:6px;padding:10px;max-height:420px;overflow:auto;color:#aeb6c6;font-size:11px}}
@media(max-width:760px){{.grid{{grid-template-columns:1fr}}}}
</style></head><body>
<header><h1>岸<span>房间</span></h1>
<div class="sub">只读副本 · {when}</div>
<div class="badge">没有实时更新 · 没有可操作按钮 · 不能修改</div></header>
<main>
<div class="summary">
<div class="kv"><b>状态</b>{html.escape(str((room.get('state') or {}).get('value','—')))} · {html.escape(str((room.get('state') or {}).get('label','—')))}</div>
<div class="kv"><b>对话轮次</b>{html.escape(str((room.get('counts') or {}).get('turns','—')))}</div>
<div class="kv"><b>影子</b>{html.escape(str((room.get('counts') or {}).get('shadows','—')))}</div>
<div class="kv"><b>承诺</b>{promises}</div>
</div>
<div class="grid">{''.join(cards)}</div>
</main></body></html>"""


def render_text(snap: dict) -> str:
    room = snap["room"]
    counts = room.get("counts") or {}
    money = room.get("money") or {}
    promises = len(snap["panels"].get("promises") or [])
    lines = [
        "岸的房间 · 只读快照",
        f"时间：{snap['captured_local']}",
        f"状态：{(room.get('state') or {}).get('value','—')} · {(room.get('state') or {}).get('label','—')}",
        f"轮次：{counts.get('turns','—')} · 影子：{counts.get('shadows','—')} · 承诺：{promises}",
        f"钱：{money.get('balance','—')} 枚",
        "",
        "这是保存的副本，不是实时房间。你能看，不能直接改。",
        "",
    ]
    for name in PANELS:
        rows = snap["panels"].get(name)
        n = len(rows) if isinstance(rows, list) else 1
        lines.append(f"- {name}: {n} 项")
    return "\n".join(lines)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    snap = capture()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    (OUT / f"{stamp}.json").write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "latest.json").write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "latest.html").write_text(render_html(snap), encoding="utf-8")
    text = render_text(snap)
    (OUT / "latest.txt").write_text(text, encoding="utf-8")
    print(text)
    print("saved", OUT / "latest.html")


if __name__ == "__main__":
    main()
