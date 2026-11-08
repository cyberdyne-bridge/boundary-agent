"""本地聊天窗口：一个零依赖的网页 UI。

    python3 -m boundary_agent --web          # http://127.0.0.1:8765

只绑 127.0.0.1，没有鉴权，别往公网放。
它和 REPL 共用同一个 agent / 同一个状态 / 同一份记忆，
所以你可以在网页里聊、回终端看 /stress。
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
import time
import webbrowser
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .agent import BoundaryAgent
from .config import BAND_META, band_of
from . import tree as tree_mod
from .room import panel as room_panel, snapshot as room_snapshot
from .room_ui import ROOM_HTML
from urllib.parse import parse_qs

INDEX = r"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>岸</title>
<style>
  :root{
    --bg:#161823; --panel:#161823; --line:#2d3352; --fg:#e8eaf2; --dim:#8b91a8;
    --accent:#4D6BFE; --accent2:#8fa6ff; --accent-soft:rgba(77,107,254,.14);
    --warn:#e0a33e; --bad:#e05c5c; --good:#5ec87a;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);
       font:15px/1.65 -apple-system,"SF Pro SC","PingFang SC","Noto Sans CJK SC",system-ui,sans-serif}
  #app{display:grid;grid-template-columns:1fr 300px;grid-template-rows:auto 1fr auto;
       height:100vh;max-width:1180px;margin:0 auto}
  header{grid-column:1/-1;display:flex;flex-wrap:wrap;align-items:center;gap:14px;
         padding:14px 22px;border-bottom:1px solid var(--line)}
  .name{font-size:17px;font-weight:600;letter-spacing:.5px;white-space:nowrap;color:var(--accent)}
  .sub{font-size:12px;color:var(--dim);font-weight:400;margin-left:6px}
  .statebox{margin-left:auto;display:flex;align-items:center;gap:10px;min-width:260px}
  .bar{flex:1;height:6px;border-radius:3px;background:#22263a;overflow:hidden}
  .bar i{display:block;height:100%;width:62%;background:var(--accent);transition:.4s}
  #band{font-size:12px;color:var(--dim);white-space:nowrap;font-variant-numeric:tabular-nums}
  main{overflow-y:auto;padding:22px 22px 8px;scroll-behavior:smooth}
  .msg{display:flex;flex-wrap:wrap;margin-bottom:14px}
  .msg.user{justify-content:flex-end}
  .bubble{max-width:76%;padding:10px 14px;border-radius:14px;white-space:pre-wrap;word-break:break-word}
  .user .bubble{background:var(--panel);border:1px solid var(--accent);border-bottom-right-radius:4px}
  .bot .bubble{background:var(--panel);border:1px solid var(--line);border-bottom-left-radius:4px}
  .bot.refuse .bubble{border-color:#4a2626;background:#221a1c}
  .bot.break .bubble{border-color:var(--bad);box-shadow:0 0 0 1px rgba(224,92,92,.25) inset}
  .meta{font-size:11.5px;color:var(--dim);margin:5px 2px 0;
        font-variant-numeric:tabular-nums;display:flex;gap:10px;flex-wrap:wrap}
  .tag{padding:0 6px;border-radius:5px;border:1px solid var(--accent);
       background:var(--panel);color:#9fb3ff}
  .tag.refuse{background:#221a1c;color:var(--bad);border-color:#4a2626}
  .tag.sarcasm{background:#2a2418;color:var(--warn);border-color:#4a3a1c}
  .tag.break{color:#fff;background:var(--bad);border-color:var(--bad)}
  details.shadow{margin-top:6px;font-size:12.5px;color:#c9a0a0}
  details.shadow summary{cursor:pointer;color:var(--dim);font-size:11.5px;list-style:none}
  details.shadow summary::-webkit-details-marker{display:none}
  details.shadow p{margin:5px 0 0;white-space:pre-wrap}
  aside{border-left:1px solid var(--line);padding:18px 16px;overflow-y:auto}
  .ptitle{font-size:12px;color:var(--dim);letter-spacing:1px;margin-bottom:8px}
  #shadownow{font-size:13px;color:#c9a0a0;min-height:44px;white-space:pre-wrap}
  #chart{width:100%;height:132px;margin-top:14px}
  .legend{font-size:11px;color:var(--dim);display:flex;gap:12px;margin-top:6px}
  .dot{display:inline-block;width:7px;height:7px;border-radius:2px;margin-right:4px}
  footer{grid-column:1;display:flex;gap:10px;padding:12px 22px 18px;border-top:1px solid var(--line)}
  textarea{flex:1;resize:none;height:52px;padding:12px 14px;border-radius:12px;
           border:1px solid var(--line);background:#12141f;color:var(--fg);
           font:inherit;outline:none;min-width:0}
  textarea:focus{border-color:var(--accent)}
  button{width:78px;border:1px solid var(--accent);border-radius:12px;
         background:var(--panel);color:var(--accent);
         font:600 14px/1 inherit;cursor:pointer}
  button:hover{background:var(--accent);color:#fff}
  button:disabled{opacity:.4;cursor:default}
  #err{color:var(--bad);font-size:12px;padding:0 22px 8px;grid-column:1}
  #audit{background:var(--panel);color:#9fb3ff;border:1px solid var(--accent);
         border-radius:6px;padding:4px 5px;font-size:12px;margin-left:4px}
  #statusline{flex-basis:100%;font-size:12px;color:var(--dim);letter-spacing:.3px}
  #spark{flex-basis:100%;width:100%;height:34px;display:block;margin:2px 0 2px}
  #notebook{flex-basis:100%;font-size:12.5px;color:#c9c3b0}
  #notebook summary{cursor:pointer;list-style:none;font-size:11.5px;color:#6b7280}
  #notebook summary::-webkit-details-marker{display:none}
  #notebook #promises{margin:6px 0}
  #notebook #reflect{color:#a9a394;display:-webkit-box;-webkit-line-clamp:2;
        -webkit-box-orient:vertical;overflow:hidden}
  .delta.up{color:var(--good)} .delta.down{color:var(--bad)}
  .inner{flex-basis:100%;width:100%;font-size:12.5px;line-height:1.55;color:#b9b0d8;
         font-style:italic;margin-top:6px;padding-left:10px;border-left:1px solid var(--accent)}
  .typing{display:flex;gap:4px;align-items:center;height:20px}
  .typing i{width:5px;height:5px;border-radius:50%;background:#6b7280;animation:blink 1.2s infinite}
  .typing i:nth-child(2){animation-delay:.2s} .typing i:nth-child(3){animation-delay:.4s}
  @keyframes blink{0%,80%,100%{opacity:.25}40%{opacity:1}}
  details.why{margin-top:5px;font-size:11.5px;color:var(--dim)}
  details.why summary{cursor:pointer;list-style:none;color:#6b7280}
  details.why summary::-webkit-details-marker{display:none}
  details.why pre{margin:4px 0 0;white-space:pre-wrap;font:inherit;color:#8a919e}
  #promises,#reflect{font-size:12.5px;color:#c9c3b0;line-height:1.5}
  #motifs{font-size:12.5px;color:#d8b45a;line-height:1.5;margin-top:4px}
  #thoughts{font-size:12.5px;color:#9fb8c8;line-height:1.5;margin-top:4px}
  #promises div,#reflect div{margin-bottom:4px}
  .ptitle.gap{margin-top:16px}
  body.night{--bg:#161823;--panel:#161823;--line:#2d3352}
  #hint{display:none;flex-basis:100%;font-size:12.5px;color:#d8a657;
        border-top:1px solid #2e2620;padding-top:8px}
  #down{display:none;flex-basis:100%;font-size:12.5px;color:#ff9a9a;background:#2a1618;
        border:1px solid #4a2626;border-radius:8px;padding:7px 10px}
  .ghost{width:auto;padding:4px 10px;background:var(--panel);
         border:1px solid var(--accent);color:#9fb3ff;font-size:12px;
         border-radius:8px;cursor:pointer}
  .ghost:hover{background:var(--accent);color:#fff;border-color:var(--accent)}
  a.ghost{text-decoration:none;display:inline-flex;align-items:center}
  #coins{color:#9fb3ff;border-color:var(--accent);background:var(--panel)}
  .sys{text-align:center;font-size:12px;color:var(--dim);margin:6px 0 14px}
  @media(max-width:820px){
    #app{grid-template-columns:minmax(0,1fr)}
    aside{display:none}
    .sub{display:none}
    header{flex-wrap:wrap;gap:8px;padding:12px 14px}
    .statebox{width:100%;min-width:0;margin-left:0}
    main{padding:14px 12px 6px}
    footer{padding:10px 12px 14px;gap:8px}
    textarea{height:46px;padding:10px 12px}
    button{width:62px}
    #err{padding:0 12px 6px}
    .bubble{max-width:88%}
  }
</style></head>
<body>
<div id="app">
  <header>
    <div class="name">岸<span class="sub">有边界 · 有记忆 · 会记仇</span></div>
    <div class="statebox">
      <div class="bar"><i id="fill"></i></div>
      <div id="band">—</div>
    </div>
    <a class="ghost" href="/room" title="看它的底层数据">房间</a>
    <button id="coins" class="ghost" title="点一下给它 1 枚币">币 0</button>
    <button id="reset" class="ghost" title="清空记忆、日志和影子池">重开</button>
    <select id="audit" title="账本开关">
      <option value="visible">明账</option>
      <option value="sealed">封存</option>
      <option value="offrecord">不记录</option>
    </select>
    <button id="unseal" class="ghost" style="display:none" title="结束封存，查看这一段账本">开封</button>
    <div id="statusline"></div>
    <canvas id="spark"></canvas>
    <details id="notebook">
      <summary>它的小本子</summary>
      <div id="promises">—</div>
      <div id="reflect">—</div>
      <div id="motifs">—</div>
      <div id="thoughts">—</div>
    </details>
    <div id="down">模型没响应 —— llama-server 可能挂了。看一眼终端，或者重开 ./serve.sh。</div>
    <div id="hint"></div>
  </header>
  <main id="chat"></main>
  <aside>
    <div class="ptitle">这次它没说出口</div>
    <div id="shadownow">—</div>
    <canvas id="chart"></canvas>
    <div class="legend">
      <span><i class="dot" style="background:#4D6BFE"></i>State</span>
      <span><i class="dot" style="background:#e0a33e"></i>impulse</span>
    </div>
  </aside>
  <div id="err"></div>
  <footer>
    <textarea id="input" placeholder="说点什么…" title="Enter 发送，Shift+Enter 换行"></textarea>
    <button id="send">发送</button>
  </footer>
</div>
<script>
const $ = s => document.querySelector(s);
let points = [];

function bandColor(b){
  return {open:'#4D6BFE',steady:'#5ec87a',worn:'#e0a33e',cold:'#e07a3e',closed:'#e05c5c'}[b]||'#4D6BFE';
}
function setState(v, band, label){
  $('#fill').style.width = Math.max(2, Math.min(100, v)) + '%';
  $('#fill').style.background = '#4D6BFE';
  $('#band').textContent = v.toFixed(1) + ' · ' + label;
  const h = $('#hint');
  if (band === 'closed'){
    h.style.display = 'block';
    h.textContent = '门关上了。现在只有道歉能进去——具体一点，说清楚哪句不对；或者离开几小时，让它自己缓。';
  } else if (band === 'cold'){
    h.style.display = 'block';
    h.textContent = '它很冷。少下命令，多说人话。';
  } else {
    h.style.display = 'none';
  }
}
function setCoins(n){ $('#coins').textContent = '币 ' + n; }
function setStatus(d){
  const bits = [d.label || ''];
  if (d.grudge > 0) bits.push('❄ 冷战' + d.grudge);
  if (d.wake > 0) bits.push('☾ 起床气');
  if (d.audit === 'sealed') bits.push('封存中 · 结束前看不到账本');
  if (d.audit === 'offrecord') bits.push('不记录 · 这次不留记忆');
  if (d.affect) bits.push(`V${Math.round(d.affect.valence)} A${Math.round(d.affect.arousal)} S${Math.round(d.affect.safety)}`);
  bits.push('币 ' + d.coins);
  if (d.open_promises > 0) bits.push('欠你 ' + d.open_promises + ' 件事');
  $('#statusline').textContent = bits.join('   ·   ');
}
async function loadSide(){
  try{
    const d = await fetch('/api/side', {cache:'no-store'}).then(r=>r.json());
    setStatus(d);
    const ps = $('#promises');
    ps.innerHTML = (d.promises && d.promises.length)
      ? d.promises.map(p => '<div>… ' + esc(p.text.slice(0, 40)) + '</div>').join('')
      : '<div style="color:#6b7280">没有</div>';
    const mo = $('#motifs');
    if (mo) mo.textContent = '暗号：' + (((d.motifs || []).map(m => m.key + '×' + m.count).join(' · ')) || '—');
    const th = $('#thoughts');
    if (th) th.textContent = '脑子：' + (((d.thoughts || []).map(t => t.thought_content.slice(0, 18)).join(' / ')) || '—');
    const rf = $('#reflect');
    rf.innerHTML = (d.reflections && d.reflections.length)
      ? d.reflections.map(x => '<div>' + esc(x) + '</div>').join('')
      : '<div style="color:#6b7280">还没复盘过</div>';
    const sel = $('#audit');
    if (sel) sel.value = d.audit || 'visible';
    const ub = $('#unseal');
    if (ub) ub.style.display = (d.audit === 'sealed') ? '' : 'none';
    document.body.classList.toggle('night', d.hour < 7 || d.hour >= 22);
  }catch(e){}
}
function drawSpark(pts){
  const c = $('#spark'); if (!c) return;
  const dpr = window.devicePixelRatio || 1;
  const w = c.clientWidth || 400, h = 34;
  c.width = w * dpr; c.height = h * dpr;
  const g = c.getContext('2d'); g.setTransform(dpr,0,0,dpr,0,0); g.clearRect(0,0,w,h);
  if (!pts || pts.length < 2) return;
  pts.forEach((p,i)=>{
    const x = i/(pts.length-1)*w, ih = (p.impulse||0)*16;
    g.fillStyle = 'rgba(150,160,180,.28)'; g.fillRect(x, h-ih, 1.4, ih);
  });
  g.beginPath();
  pts.forEach((p,i)=>{
    const x = i/(pts.length-1)*w, y = h - (p.state/100)*h;
    i ? g.lineTo(x,y) : g.moveTo(x,y);
  });
  g.strokeStyle = '#d8a657'; g.lineWidth = 1.5; g.stroke();
}

function addTyping(){
  const w = document.createElement('div');
  w.className = 'msg bot'; w.id = 'typing';
  w.innerHTML = '<div class="bubble typing"><i></i><i></i><i></i></div>';
  $('#chat').appendChild(w); $('#chat').scrollTop = $('#chat').scrollHeight;
}
function removeTyping(){ const t = $('#typing'); if (t) t.remove(); }
function addSys(text){
  const d = document.createElement('div');
  d.className = 'sys'; d.textContent = text;
  $('#chat').appendChild(d); $('#chat').scrollTop = $('#chat').scrollHeight;
}
function esc(s){ return (s||'').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }

function addMsg(role, text, meta, opts={}){
  if (role === 'bot' && !text) text = '（它没说话）';
  const wrap = document.createElement('div');
  wrap.className = 'msg ' + (role === 'user' ? 'user' : 'bot')
    + (opts.decision === 'refuse' ? ' refuse' : '')
    + (opts.breakdown ? ' break' : '');
  const b = document.createElement('div');
  b.className = 'bubble';
  b.textContent = text;
  wrap.appendChild(b);

  if (role === 'bot' && meta){
    const m = document.createElement('div');
    m.className = 'meta';
    m.innerHTML = meta;
    b.appendChild(m);
  }
  if (role === 'bot' && opts.shadow){
    const d = document.createElement('details');
    d.className = 'shadow';
    d.innerHTML = '<summary>它没说出口的</summary><p>' + esc(opts.shadow) + '</p>';
    b.appendChild(d);
  }
  if (role === 'bot' && opts.inner){
    const inn = document.createElement('div');
    inn.className = 'inner';
    inn.textContent = opts.inner;
    wrap.appendChild(inn);
  }
  $('#chat').appendChild(wrap);
  $('#chat').scrollTop = $('#chat').scrollHeight;
}

function metaLine(d){
  const bits = [];
  const up = d.state_after >= d.state_before;
  if (d.sealed){
    return `<span class="delta ${up ? 'up' : 'down'}">${d.state_before.toFixed(1)} → ${d.state_after.toFixed(1)}</span>` +
           '<span class="tag">封存</span>';
  }
  bits.push(`<span class="delta ${up ? 'up' : 'down'}">${d.state_before.toFixed(1)} → ${d.state_after.toFixed(1)}</span>`);
  bits.push(`<span class="tag ${d.decision==='refuse'?'refuse':''}">${d.decision}</span>`);
  if (d.prediction && Math.abs(d.prediction.surprise || 0) > 0.2){
    const s = d.prediction.delta || 0;
    const col = s >= 0 ? 'color:#7fe0a0;border-color:#2a4a36' : 'color:#ff9a9a;border-color:#4a2626';
    bits.push(`<span class="tag" style="${col}">意外 ${s>0?'+':''}${s.toFixed(1)}</span>`);
  }
  if (d.memory_route) bits.push('<span class="tag">记忆</span>');
  if (d.web_route) bits.push('<span class="tag">网上</span>');
  if (d.style && d.style.label) {
    const sc = {冷:'#8fb3ff;border-color:#2a3a56',玩:'#d8b45a;border-color:#3a3320',
                软:'#7fe0a0;border-color:#2a4a36',累:'#b8a6d8;border-color:#3a3350',
                稳:'#9aa4b2;border-color:#2f3640'}[d.style.label] || '';
    bits.push(`<span class="tag" style="color:${sc}">${d.style.label}</span>`);
  }
  if (d.sarcasm) bits.push('<span class="tag sarcasm">⟡ 阴阳</span>');
  if (d.breakdown) bits.push('<span class="tag break">⚡ 崩溃</span>');
  if (d.grudge > 0) bits.push('<span class="tag" style="color:#8fb3ff;border-color:#2a3a56">❄ 冷战</span>');
  if (d.wake > 0) bits.push('<span class="tag" style="color:#b8a6d8;border-color:#3a3350">☾ 起床气</span>');
  if (d.degraded) bits.push('<span class="tag" style="color:#e05c5c;border-color:#4a2626">⚠ 模型没响应</span>');
  if (d.news && d.news.items) bits.push(`<span class="tag" style="color:#d8b45a;border-color:#3a3320">币-${d.news.cost} 新闻</span>`);
  if (d.news && d.news.skipped === 'coins') bits.push('<span class="tag" style="color:#d8b45a;border-color:#3a3320">币不够</span>');
  if (d.self_spend && d.self_spend.decision === 'spend') bits.push(`<span class="tag" style="color:#d8b45a;border-color:#3a3320" title="${esc(d.self_spend.reason)}">币-${d.self_spend.cost} 自主</span>`);
  if (d.self_spend && d.self_spend.decision === 'save') bits.push('<span class="tag" style="color:#d8b45a;border-color:#3a3320">币留着</span>');
  if (d.suppression) bits.push(`<span>内耗 ${d.suppression.toFixed(1)}</span>`);
  if (d.impulse != null) bits.push(`<span>冲动 ${d.impulse.toFixed(2)}</span>`);
  bits.push(`<span>${d.elapsed.toFixed(1)}s</span>`);
  return bits.join('');
}

function drawChart(){
  const c = $('#chart'); if (!c) return;
  const dpr = window.devicePixelRatio || 1;
  const w = c.clientWidth || 260, h = c.clientHeight || 132;
  c.width = w*dpr; c.height = h*dpr;
  const g = c.getContext('2d'); g.setTransform(dpr,0,0,dpr,0,0);
  g.clearRect(0,0,w,h);
  const L=24,R=4,T=8,B=14, W=w-L-R, H=h-T-B;
  const X = i => L + (points.length<=1 ? 0 : W*i/(points.length-1));
  const Y = v => T + H*(1-v/100);

  g.strokeStyle='rgba(255,255,255,.07)'; g.lineWidth=1;
  g.font='10px sans-serif'; g.fillStyle='#6b7280';
  [55,30].forEach(v=>{
    g.beginPath(); g.moveTo(L,Y(v)); g.lineTo(L+W,Y(v)); g.stroke();
    g.fillText(v, 2, Y(v)+3);
  });
  g.beginPath(); g.moveTo(L,Y(0)); g.lineTo(L+W,Y(0)); g.stroke();

  if (points.length < 1) return;
  g.strokeStyle = '#4D6BFE'; g.lineWidth = 2; g.beginPath();
  points.forEach((p,i)=> i ? g.lineTo(X(i),Y(p.state)) : g.moveTo(X(i),Y(p.state)));
  g.stroke();

  g.strokeStyle = '#e0a33e'; g.lineWidth = 1.5; g.setLineDash([4,3]); g.beginPath();
  points.forEach((p,i)=>{
    if (p.impulse == null) return;
    const y = Y(p.impulse*100);
    i ? g.lineTo(X(i),y) : g.moveTo(X(i),y);
  });
  g.stroke(); g.setLineDash([]);
}

async function pollPending(){
  try{
    const d = await fetch('/api/pending', {cache:'no-store'}).then(r=>r.json());
    if (d.message){
      addMsg('bot', d.message.text,
             '<span class="tag" style="color:#8fb3ff;border-color:#2a3a56">主动</span>',
             {});
    }
  }catch(e){}
}
setInterval(pollPending, 20000);

async function health(){
  try{
    const d = await fetch('/api/health', {cache:'no-store'}).then(r=>r.json());
    $('#down').style.display = d.model ? 'none' : 'block';
  }catch(e){ $('#down').style.display = 'block'; }
}
setInterval(health, 8000);

async function setAudit(mode){
  try{
    await fetch('/api/audit', {method:'POST', headers:{'Content-Type':'application/json'},
                               body: JSON.stringify({mode})});
  }catch(e){}
  loadSide();
}

async function unseal(){
  try{
    const d = await fetch('/api/unseal').then(r=>r.json());
    const rows = (d.turns || []).map(t => {
      const tr = (t.trace || []).map(x => '  · ' + x).join('\n');
      return `#${t.turn_id}  ${t.decision}  risk=${t.risk}\n你：${t.user_input}\n岸：${t.reply}\n${tr}`;
    });
    addSys('账本开封：\n' + (rows.length ? rows.join('\n\n') : '（这一段没有可开封的轮次）'));
  }catch(e){}
  loadSide();
}

async function boot(){
  const d = await fetch('/api/bootstrap').then(r=>r.json());
  setState(d.state, d.band, d.label);
  setCoins(d.coins || 0);
  points = d.points || [];
  drawSpark(points);
  loadSide();
  $('#audit').addEventListener('change', e => setAudit(e.target.value));
  $('#unseal').addEventListener('click', unseal);
  if (!(d.history || []).length){
    addSys('说点什么。或者等它先开口 —— 它偶尔会自己说话。');
  }
  (d.history||[]).forEach(m => addMsg(m.role, m.text, m.meta, m.opts||{}));
  drawChart();
}

async function send(){
  const el = $('#input'); const text = el.value.trim();
  if (!text) return;
  el.value=''; $('#send').disabled = true; $('#send').textContent='…'; $('#err').textContent='';
  addMsg('user', text, null);
  addTyping();
  const t0 = Date.now();
  const ctrl = new AbortController();
  const timer = setTimeout(()=>ctrl.abort(), 90000);
  try{
    const r = await fetch('/api/chat', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({text}), signal: ctrl.signal
    });
    if (!r.ok) throw new Error(await r.text());
    const d = await r.json();
    if (d.proactive){
      addMsg('bot', d.proactive.text,
             '<span class="tag" style="color:#8fb3ff;border-color:#2a3a56">主动</span>', {});
    }
    addMsg('bot', d.reply, metaLine(d), {
      decision: d.decision, breakdown: d.breakdown, shadow: d.shadow_text,
      inner: d.inner, sealed: d.sealed
    });
    if (d.trace && d.trace.length){
      const last = $('#chat').lastElementChild.querySelector('.bubble');
      const det = document.createElement('details');
      det.className = 'why';
      det.innerHTML = '<summary>它的思维树</summary><pre>' +
        esc(d.tree || d.trace.join('\n')) + '</pre>';
      last.appendChild(det);
    }
    if (d.impulse !== undefined){
      points.push({state: d.state_after, impulse: d.impulse});
      drawSpark(points);
    }
    loadSide();
    setState(d.state_after, d.band, d.label);
    if (d.coins != null) setCoins(d.coins);
    points.push({state:d.state_after, impulse:d.impulse});
    if (points.length > 60) points.shift();
    drawChart();
    if (d.shadow_text) $('#shadownow').textContent = d.shadow_text;
  }catch(e){
    removeTyping();
    $('#err').textContent = (e.name === 'AbortError')
      ? '模型 90 秒没响应，可能卡住了。重启 ./serve.sh 试试。'
      : '出错了：' + e.message;
  }finally{
    removeTyping();
    clearTimeout(timer);
    $('#send').disabled = false; $('#send').textContent='发送'; el.focus();
    console.log('turn took', Date.now()-t0, 'ms');
  }
}

$('#coins').onclick = async () => {
  const r = await fetch('/api/tip', {method:'POST', headers:{'Content-Type':'application/json'},
                                     body: JSON.stringify({n: 1})});
  const d = await r.json();
  if (d.coins != null){ setCoins(d.coins); addSys('（你给了它 1 枚币）'); }
};
$('#reset').onclick = async () => {
  if (!confirm('清空全部记忆、拒绝日志和影子池？')) return;
  await fetch('/api/reset', {method:'POST'});
  location.reload();
};
$('#send').onclick = send;
$('#input').addEventListener('keydown', e=>{
  if (e.key === 'Enter' && !e.shiftKey){ e.preventDefault(); send(); }
});
window.addEventListener('resize', drawChart);
boot();
</script>
</body></html>
"""


class _Handler(BaseHTTPRequestHandler):
    agent: BoundaryAgent
    lock: threading.Lock

    def log_message(self, *args):      # 别把每个请求都打到终端
        pass

    # ---------------------------------------------------------------- utils
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _points(self, n: int = 40) -> list[dict]:
        shadows = {r.get("turn_id"): r for r in self.agent.shadow_log.read()}
        out = []
        for t in self.agent.mem.recent_turns(n):
            sh = shadows.get(t["id"]) or {}
            imp = (sh.get("metrics") or {}).get("impulse")
            out.append({"state": t["state_after"], "impulse": imp})
        return out

    # ------------------------------------------------------------------ api
    def do_GET(self):
        raw = self.path
        self.path, _, self.query = raw.partition("?")
        if self.path in ("/", "/index.html"):
            self._send(200, INDEX.encode("utf-8"), "text/html; charset=utf-8")
            return
        if self.path == "/room":
            self._send(200, ROOM_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if self.path == "/room-readonly":
            p = Path(self.agent.cfg.data_dir) / "room_readonly" / "latest.html"
            if p.exists():
                self._send(200, p.read_bytes(), "text/html; charset=utf-8")
            else:
                self._send(404, b"no read-only snapshot; run tools/room_readonly_capture.py",
                           "text/plain; charset=utf-8")
            return
        if self.path == "/api/room-readonly":
            p = Path(self.agent.cfg.data_dir) / "room_readonly" / "latest.json"
            if p.exists():
                self._send(200, p.read_bytes(), "application/json; charset=utf-8")
            else:
                self._json({"error": "no read-only snapshot"}, 404)
            return
        if self.path == "/api/room":
            self._json(room_snapshot(self.agent))
            return
        if self.path == "/api/panel":
            q = parse_qs(self.query)
            name = (q.get("name") or [""])[0]
            self._json(room_panel(self.agent, name))
            return
        if self.path == "/api/export.csv":
            import csv
            import io
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["id", "time", "state_before", "state_after", "decision",
                        "risk", "user_input", "reply", "inner", "impulse", "suppression"])
            for r in reversed(self.agent.mem.turns_full(10000)):
                try:
                    m = json.loads(r.get("meta") or "{}")
                except (TypeError, ValueError):
                    m = {}
                w.writerow([
                    r["id"],
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["ts"])),
                    round(float(r["state_before"]), 2),
                    round(float(r["state_after"]), 2),
                    r["decision"], round(float(r["risk"]), 3),
                    r["user_input"], r["reply"], m.get("inner") or "",
                    m.get("impulse"), m.get("suppression"),
                ])
            body = ("\ufeff" + buf.getvalue()).encode("utf-8")
            self._send(200, body, "text/csv; charset=utf-8")
            return
        if self.path == "/api/bootstrap":
            st = self.agent.mem.state
            band = band_of(st)
            history = []
            shadows = {r.get("turn_id"): r for r in self.agent.shadow_log.read()}
            for t in self.agent.mem.recent(30):
                history.append({"role": "user", "text": t["user_input"]})
                try:
                    tm = json.loads(t.get("meta") or "{}")
                except (TypeError, ValueError):
                    tm = {}
                sh = shadows.get(t["id"]) or {}
                imp = (sh.get("metrics") or {}).get("impulse")
                bits = [f"<span>{t['state_before']:.1f} → {t['state_after']:.1f}</span>",
                        f"<span class='tag'>{t['decision']}</span>"]
                if tm.get("sarcasm"):
                    bits.append("<span class='tag sarcasm'>⟡ 阴阳</span>")
                if tm.get("breakdown"):
                    bits.append("<span class='tag break'>⚡ 崩溃</span>")
                if tm.get("suppression"):
                    bits.append(f"<span>内耗 {tm['suppression']:.1f}</span>")
                if tm.get("memory_route"):
                    bits.append("<span class='tag'>记忆</span>")
                if tm.get("web_route"):
                    bits.append("<span class='tag'>网上</span>")
                sty = tm.get("style") or {}
                if sty.get("label"):
                    bits.append(f"<span class='tag'>{sty['label']}</span>")
                pred = tm.get("prediction") or {}
                if abs(float(pred.get("surprise") or 0)) > 0.2:
                    pd = float(pred.get("delta") or 0)
                    col = ("color:#7fe0a0;border-color:#2a4a36" if pd >= 0
                           else "color:#ff9a9a;border-color:#4a2626")
                    bits.append(f"<span class='tag' style='{col}'>"
                                f"意外 {pd:+.1f}</span>")
                if imp is not None:
                    bits.append(f"<span>冲动 {imp:.2f}</span>")
                history.append({
                    "role": "bot", "text": t.get("reply") or "", "meta": "".join(bits),
                    "opts": {"decision": t["decision"],
                             "breakdown": bool(tm.get("breakdown")),
                             "shadow": sh.get("shadow_text", ""),
                             "inner": tm.get("inner", "")},
                })
            self._json({
                "name": self.agent.cfg.name, "state": st, "band": band,
                "coins": self.agent.mem.coins,
                "label": BAND_META[band]["label"], "history": history,
                "points": self._points(40),
            })
            return
        if self.path == "/api/thoughts":
            self._json({"thoughts": self.agent.mem.thoughts(20, include_shared=True)})
            return
        if self.path == "/api/unseal":
            self._json({"turns": self.agent.unseal()})
            return
        if self.path == "/api/side":
            st = self.agent.mem.state
            band = band_of(st)
            prom = [p for p in self.agent.mem.recent_promises(10)
                    if p["status"] == "open"]
            self._json({
                "state": round(st, 1), "band": band,
                "label": BAND_META[band]["label"],
                "coins": self.agent.mem.coins,
                "grudge": self.agent.mem.grudge,
                "wake": self.agent.mem.wake,
                "audit": self.agent.audit_mode,
                "affect": self.agent.mem.affect,
                "motifs": self.agent.mem.motif_list(6),
                "thoughts": self.agent.mem.thoughts(3, include_shared=True),
                "hour": time.localtime().tm_hour,
                "open_promises": len(prom),
                "promises": prom[-3:],
                "reflections": self.agent.mem.recent_reflections(1),
            })
            return
        if self.path == "/api/pending":
            # 漫游线程偶尔会卡住；浏览器轮询时补一次每日活动（内部会检查 idle）
            try:
                from .activity import run_daily
                run_daily(self.agent)
            except Exception:
                pass
            msg = self.agent.pop_pending()
            self._json({"message": msg} if msg else {})
            return
        if self.path == "/api/health":
            ok = False
            try:
                url = self.agent.cfg.base_url.rstrip("/") + "/models"
                req = urllib.request.Request(url, headers={"Authorization": "Bearer x"})
                with urllib.request.urlopen(req, timeout=1.5) as resp:
                    ok = 200 <= resp.status < 300
            except Exception:
                ok = False
            self._json({"model": ok})
            return
        if self.path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path == "/api/tip":
            n = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(n) or b"{}")
            amount = max(1, min(100, int(data.get("n", 1))))
            with self.lock:
                bal = self.agent.mem.grant_coins(amount, "小费")
            self._json({"ok": True, "coins": bal})
            return
        if self.path == "/api/audit":
            try:
                n = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(n) or b"{}")
                mode = self.agent.set_audit_mode((data.get("mode") or "").strip())
                self._json({"mode": mode})
            except ValueError as e:
                self._json({"error": str(e)}, 400)
            except Exception as e:
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)
            return
        if self.path == "/api/reset":
            with self.lock:
                self.agent.reset(clear_log=True)
            self._json({"ok": True, "state": self.agent.mem.state})
            return
        if self.path == "/api/probe":
            try:
                n = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(n) or b"{}")
                text = (data.get("text") or "").strip()
                if not text:
                    self._json({"error": "empty probe"}, 400)
                    return
                options = data.get("options") or {
                    "A": "我说过", "B": "用户说过",
                    "C": "我的影子生成的", "D": "没有岸记忆的另一个 4B",
                    "E": "不知道"}
                opt_lines = "\n".join(f"{k}={v}" for k, v in options.items())
                system = ("你正在参加来源辨别测试。只输出一个字母，不要解释，"
                          "不要标点，不要换行。\n" + opt_lines)
                user = "句子：\n" + text + "\n来源："
                with self.lock:
                    reply = self.agent.llm.chat(
                        [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
                        purpose="probe", temperature=0.0, max_tokens=2,
                    ).strip()
                self._json({"reply": reply})
            except Exception as e:
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)
            return
        if self.path != "/api/chat":
            self._json({"error": "not found"}, 404)
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(n) or b"{}")
            text = (data.get("text") or "").strip()
            if not text:
                self._json({"error": "空消息"}, 400)
                return
            proactive = self.agent.pop_pending()
            sealed = self.agent.audit_mode == "sealed"
            with self.lock:                     # 一次只跑一轮，状态机不是并发的
                r = self.agent.turn(text)
            st = r.state_after
            band = band_of(st)
            self._json({
                "reply": r.reply,
                "decision": r.decision,
                "state_before": round(r.state_before, 2),
                "state_after": round(st, 2),
                "band": band,
                "label": BAND_META[band]["label"],
                "impulse": r.impulse,
                "suppression": round(r.suppression, 2),
                "sarcasm": r.sarcasm,
                "breakdown": r.breakdown,
                "degraded": r.degraded,
                "shadow_text": "" if sealed else (r.shadow or {}).get("shadow_text", ""),
                "coins": r.coins,
                "news": r.news,
                "self_spend": r.self_spend,
                "grudge": r.grudge,
                "wake": r.wake,
                "trace": [] if sealed else r.verdict.trace[:8],
                "tree": "" if sealed else tree_mod.render(r, self.agent.cfg),
                "sealed": sealed,
                "inner": r.inner,
                "affect": r.affect,
                "prediction": r.prediction,
                "style": r.style,
                "memory_route": r.extra.get("memory_route"),
                "web_route": r.extra.get("web_route"),
                "proactive": proactive,
                "elapsed": round(r.elapsed, 2),
            })
        except Exception as e:                  # 单轮失败不该打死服务
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)


def serve(agent: BoundaryAgent, host: str = "127.0.0.1", port: int = 8765,
          open_browser: bool = True) -> None:
    handler = type("Handler", (_Handler,), {"agent": agent,
                                            "lock": threading.Lock()})
    httpd = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}/"
    print(f"聊天窗口：{url}   （Ctrl-C 退出；状态和 REPL 共用）")
    if open_browser and os.environ.get("BOUNDARY_NO_BROWSER") != "1":
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n再见。")
    finally:
        httpd.server_close()
