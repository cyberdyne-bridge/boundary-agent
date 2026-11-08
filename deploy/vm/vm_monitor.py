#!/usr/bin/env python3
"""岸 · VM 观察窗：看她在虚拟机里想什么、跑什么、输出什么。

独立端口 8766，只读 activity.jsonl，不碰 8765。
"""
from __future__ import annotations

import html
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(os.environ.get("BOUNDARY_VM_ROOT", Path(__file__).resolve().parents[2] / "data" / "vm"))
LOG = ROOT / 'activity.jsonl'
PID = ROOT / 'qemu.pid'
ACT_PID = ROOT / 'activity.pid'
PORT = 8766

HTML = r'''<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>岸 · VM 观察窗</title>
<style>
:root{--bg:#0c0e12;--panel:#141820;--line:#252b36;--text:#d7dbe3;--dim:#7d8798;
--accent:#d8a657;--blue:#8fb3ff;--green:#7fe0a0;--red:#ff9a9a;--purple:#c7a7ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
font:14px/1.65 ui-sans-serif,system-ui,"Noto Sans CJK SC",sans-serif}
.wrap{max-width:1040px;margin:0 auto;padding:28px 20px 80px}
header{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;
border-bottom:1px solid var(--line);padding-bottom:18px;margin-bottom:22px}
h1{font-size:22px;margin:0;letter-spacing:.08em;font-weight:600}
h1 span{color:var(--accent)}
.sub{color:var(--dim);font-size:12px;margin-top:5px;letter-spacing:.04em}
.status{text-align:right;font-size:12px;color:var(--dim)}
.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;
padding:3px 10px;margin-left:6px;color:var(--text)}
.pill.on{color:var(--green);border-color:#2a4a36}
.pill.off{color:var(--red);border-color:#4a2626}
.toolbar{display:flex;justify-content:space-between;align-items:center;margin:0 0 16px}
.toolbar .left{color:var(--dim);font-size:12px}
button{background:transparent;border:1px solid var(--line);color:var(--dim);
border-radius:6px;padding:5px 10px;font:inherit;font-size:12px;cursor:pointer}
button:hover{color:var(--text);border-color:#3a4352}
.card{background:linear-gradient(180deg,#151a23,#12161e);border:1px solid var(--line);
border-radius:12px;padding:16px 18px;margin-bottom:14px;box-shadow:0 10px 30px #0003}
.card.new{animation:in .4s ease}
@keyframes in{from{opacity:0;transform:translateY(-4px)}to{opacity:1;transform:none}}
.meta{display:flex;flex-wrap:wrap;gap:8px;align-items:center;color:var(--dim);
font-size:11px;letter-spacing:.03em;margin-bottom:10px}
.idx{color:var(--accent);font-weight:600}
.status-ok{color:var(--green)}.status-timeout{color:var(--red)}
.status-nonzero{color:var(--accent)}.status-empty,.status-llm_error,.status-error{color:var(--red)}
.thought{font-size:16px;line-height:1.55;color:#eef1f6;margin:2px 0 10px}
.thought b{color:var(--purple);font-weight:600;margin-right:4px}
.explain{border-left:2px solid var(--blue);padding:2px 0 2px 12px;color:#bfc9da;
margin:0 0 12px;font-size:13px}
.explain b{color:var(--blue);font-weight:600;margin-right:4px}
details{border-top:1px solid #1d232d;padding-top:8px;margin-top:8px}
summary{cursor:pointer;color:var(--dim);font-size:12px;list-style:none}
summary::-webkit-details-marker{display:none}
summary:before{content:"▸ ";color:#596273}
details[open] summary:before{content:"▾ "}
pre{white-space:pre-wrap;word-break:break-word;background:#0b0e13;border:1px solid #1d232d;
border-radius:7px;padding:10px 12px;margin:8px 0 0;font:12px/1.55 ui-monospace,monospace;
color:#aeb7c6;max-height:320px;overflow:auto}
.empty{border:1px dashed var(--line);border-radius:12px;padding:40px;text-align:center;
color:var(--dim);font-size:13px}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div>
    <h1>岸 <span>· VM 观察窗</span></h1>
    <div class="sub">她在虚拟机里想做什么 · 中文翻译 · 原始命令与输出</div>
  </div>
  <div class="status">
    <span id="vm" class="pill">VM ...</span>
    <span id="act" class="pill">活动 ...</span>
    <div id="clock" style="margin-top:6px"></div>
  </div>
</header>
<div class="toolbar">
  <div class="left" id="count">等待她开始……</div>
  <button onclick="loadAll()">刷新</button>
</div>
<div id="list"><div class="empty">还没有活动记录。<br>启动 <code>vm_activity.py</code> 后，这里会出现她的想法、命令和输出。</div></div>
</div>
<script>
let lastTs = 0, items = [];
const list = document.getElementById('list');
const esc = s => String(s ?? '').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const fmt = t => t ? new Date(t*1000).toLocaleTimeString('zh-CN',{hour12:false}) : '--:--:--';
function card(r){
  const status = r.status || 'ok';
  const exp = r.explain ? `<div class="explain"><b>翻译</b>${esc(r.explain)}</div>` : '';
  const cmd = r.cmd ? `<details><summary>原始命令</summary><pre>${esc(r.cmd)}</pre></details>` : '';
  const out = r.out ? `<details><summary>原始输出</summary><pre>${esc(r.out)}</pre></details>` : '';
  const raw = r.raw && r.raw !== r.cmd ? `<details><summary>模型原文</summary><pre>${esc(r.raw)}</pre></details>` : '';
  return `<article class="card new" data-ts="${r.ts||0}">
    <div class="meta"><span class="idx">#${r.round||'?'}</span><span>${fmt(r.ts)}</span>
      <span class="status-${esc(status)}">${esc(status)}</span>
      <span>${r.duration != null ? esc(r.duration)+'s' : ''}</span></div>
    <div class="thought"><b>她想</b>${esc(r.thought || '（没写想法）')}</div>
    ${exp}${cmd}${out}${raw}
  </article>`;
}
function render(){
  if(!items.length){ list.innerHTML = '<div class="empty">还没有活动记录。</div>'; return; }
  list.innerHTML = items.slice().reverse().map(card).join('');
  document.getElementById('count').textContent = `${items.length} 轮 · 最新 ${fmt(items[items.length-1].ts)}`;
}
async function loadAll(){
  const r = await fetch('/api/activity'); const d = await r.json();
  items = d.items || []; lastTs = d.last_ts || 0; render();
}
async function poll(){
  try{
    const r = await fetch('/api/activity?since='+encodeURIComponent(lastTs));
    const d = await r.json(); const fresh = d.items || [];
    if(fresh.length){
      items = items.concat(fresh); lastTs = d.last_ts || lastTs;
      const box = document.createElement('div'); box.innerHTML = fresh.map(card).join('');
      list.prepend(...box.children); 
      document.getElementById('count').textContent = `${items.length} 轮 · 最新 ${fmt(items[items.length-1].ts)}`;
      if(items.length === fresh.length) list.querySelector('.empty')?.remove();
    }
    const h = await fetch('/api/status').then(x=>x.json());
    const vm = document.getElementById('vm'), act = document.getElementById('act');
    vm.textContent = h.vm_running ? 'VM running' : 'VM stopped';
    vm.className = 'pill ' + (h.vm_running ? 'on' : 'off');
    act.textContent = h.activity_running ? '她在活动' : '活动停止';
    act.className = 'pill ' + (h.activity_running ? 'on' : 'off');
    document.getElementById('clock').textContent = new Date().toLocaleTimeString('zh-CN',{hour12:false});
  }catch(e){}
}
loadAll().then(poll);
setInterval(poll, 1500);
</script>
</body></html>'''


def read_items(limit: int = 200) -> list[dict]:
    if not LOG.exists():
        return []
    out = []
    try:
        with LOG.open(encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out[-limit:]


def pid_alive(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        pid = int(path.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, OSError):
        return False


class Handler(BaseHTTPRequestHandler):
    def _json(self, obj: dict, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        u = urlparse(self.path)
        if u.path == '/':
            body = HTML.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if u.path == '/api/health':
            self._json({'ok': True})
            return
        if u.path == '/api/activity':
            q = parse_qs(u.query)
            since = float((q.get('since') or ['0'])[0] or 0)
            items = [x for x in read_items() if float(x.get('ts') or 0) > since]
            self._json({'items': items,
                        'last_ts': max([float(x.get('ts') or 0) for x in items] or [since])})
            return
        if u.path == '/api/status':
            self._json({'vm_running': pid_alive(PID),
                        'activity_running': pid_alive(ACT_PID),
                        'rounds': len(read_items())})
            return
        self.send_error(404)

    def log_message(self, fmt, *args):
        pass


def main() -> None:
    httpd = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    print(f'VM 观察窗：http://127.0.0.1:{PORT}/')
    httpd.serve_forever()


if __name__ == '__main__':
    main()
