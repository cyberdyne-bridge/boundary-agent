"""房间页面：把底层数据摊成独立面板。零依赖，和聊天页共用 /api。"""

ROOM_HTML = r"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>岸 · 房间</title>
<style>
  :root{
    --bg:#161823; --panel:#161823; --line:#2d3352; --fg:#e8eaf2; --dim:#8b91a8;
    --accent:#4D6BFE; --accent2:#8fa6ff; --accent-soft:rgba(77,107,254,.14);
    --warn:#e0a33e; --bad:#e05c5c; --good:#5ec87a; --blue:#8fa6ff;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);
       font:14px/1.6 -apple-system,"SF Pro SC","PingFang SC","Noto Sans CJK SC",system-ui,sans-serif}
  header{display:flex;flex-wrap:wrap;align-items:center;gap:10px;padding:12px 20px;
         border-bottom:1px solid var(--line);position:sticky;top:0;background:rgba(22,24,35,.92);
         backdrop-filter:blur(8px);z-index:10}
  .brand{font-size:18px;font-weight:600;letter-spacing:.5px;white-space:nowrap;color:var(--accent)}
  .brand span{font-size:12px;color:var(--dim);font-weight:400;margin-left:6px}
  .pill{font-size:12px;color:#9fb3ff;border:1px solid var(--accent);background:var(--panel);
        border-radius:999px;padding:3px 10px;font-variant-numeric:tabular-nums}
  .spacer{flex:1}
  header a{color:#9fb3ff;text-decoration:none;font-size:12.5px;border:1px solid var(--accent);
           background:var(--panel);padding:5px 10px;border-radius:7px}
  header a:hover{color:#fff;border-color:var(--accent2)}
  #refresh{background:var(--panel);color:#9fb3ff;border:1px solid var(--accent);
           border-radius:7px;padding:5px 10px;cursor:pointer;font-size:12.5px}
  #refresh:hover{color:#fff;border-color:var(--accent2)}
  #grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;
        padding:16px;max-width:1500px;margin:0 auto}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:12px;
        padding:13px 14px;min-height:148px;cursor:pointer;position:relative;
        color:var(--fg);transition:.18s;overflow:hidden}
  .card:hover{border-color:var(--accent);transform:translateY(-1px)}
  .card.wide{grid-column:span 2}
  .card.full{grid-column:1/-1}
  .card h2{font-size:12px;margin:0;color:var(--accent);letter-spacing:1.2px;font-weight:500}
  .card .hint{font-size:10px;color:var(--dim);margin-left:6px}
  .big{font-size:34px;font-weight:600;line-height:1.1;margin:8px 0 2px;
       font-variant-numeric:tabular-nums;color:var(--accent)}
  .label{font-size:12px;color:var(--dim)}
  #statechart{width:100%;height:150px;margin-top:6px;display:block}
  .legend{display:flex;gap:12px;font-size:10.5px;color:var(--dim);margin:2px 0 4px}
  .legend .dot{vertical-align:1px}
  .small{font-size:11.5px;color:var(--dim);font-variant-numeric:tabular-nums}
  .muted{color:#6b7280}
  .row{display:flex;gap:8px;align-items:baseline;padding:3px 0;
       border-bottom:1px solid #20242c;font-size:12px}
  .row:last-child{border-bottom:0}
  .row b{color:var(--fg);font-weight:500}
  .row .grow{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .tag{font-size:10.5px;border:1px solid var(--accent);border-radius:5px;padding:0 5px;
       background:var(--panel);color:#9fb3ff}
  .tag.bad{color:var(--bad);background:#221a1c;border-color:#4a2626}
  .tag.good{color:var(--good);background:#16241a;border-color:#244a2e}
  .tag.warn{color:var(--warn);background:#2a2418;border-color:#4a3a1c}
  .tag.blue{color:var(--blue);background:#18202e;border-color:#26364a}
  .dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:5px}
  .dot.ok{background:var(--good)} .dot.bad{background:var(--bad)}
  .ev{display:flex;gap:7px;align-items:baseline;padding:4px 0;
      border-bottom:1px solid #1e222a;font-size:12px}
  .ev:last-child{border-bottom:0}
  .ev .etime{color:#5d6674;font-variant-numeric:tabular-nums;min-width:52px}
  .ev .etitle{color:var(--fg);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .ev .esub{color:#6d7684;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1}
  .thought{padding:6px 0;border-bottom:1px solid #20242c}
  .thought:last-child{border-bottom:0}
  .tline{display:flex;justify-content:space-between;font-size:11px;color:var(--dim)}
  .ttext{font-size:12.5px;margin-top:3px;color:#d6d9df}
  .pbar{height:3px;background:#242832;border-radius:2px;margin:4px 0;overflow:hidden}
  .pbar i{display:block;height:100%;background:linear-gradient(90deg,#8fa6ff,#fff)}
  .quote{font-size:14px;color:#d8c0c0;line-height:1.55;margin:6px 0}
  .kv{display:flex;gap:14px;flex-wrap:wrap;margin:6px 0}
  .kv div{font-size:12px;color:var(--dim)}
  .kv b{color:var(--fg);font-size:16px;font-variant-numeric:tabular-nums}
  #labuilt{display:none}
  .labrow{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:6px 0}
  select,button{font:inherit;font-size:12px;background:var(--panel);color:var(--fg);
                border:1px solid var(--accent);border-radius:6px;padding:4px 8px}
  button{cursor:pointer}
  button.danger{color:var(--bad);border-color:#4a2626}
  a.btn{font-size:12px;color:#9fb3ff;border:1px solid var(--accent);
        background:var(--panel);border-radius:6px;padding:4px 8px;text-decoration:none}
  a.btn:hover{color:#fff;border-color:var(--accent2)}
  .chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px}
  .chip{font-size:11px;color:#9fb3ff;border:1px solid var(--accent);border-radius:5px;
        padding:1px 6px;background:var(--panel)}
  #drawer{position:fixed;top:0;right:0;width:min(860px,94vw);height:100vh;
          background:#10121c;border-left:1px solid var(--accent);z-index:30;
          transform:translateX(105%);transition:.22s;display:flex;flex-direction:column}
  #drawer.open{transform:none}
  .dhead{display:flex;align-items:center;gap:10px;padding:14px 16px;
         border-bottom:1px solid var(--line);position:sticky;top:0;background:#10121c;z-index:2}
  .dhead h2{font-size:14px;margin:0;flex:1;letter-spacing:1px}
  #dclose{background:transparent;border:0;color:var(--dim);font-size:22px;cursor:pointer;line-height:1}
  #dbody{overflow:auto;padding:14px 16px 40px}
  .pcard{border:1px solid var(--line);border-radius:8px;margin-bottom:8px;background:#171b2a}
  .pcard summary{padding:7px 10px;cursor:pointer;font-size:12px;color:#cdd2da}
  .pbody{padding:0 10px 10px;font-size:12px;color:#c9ced6}
  .pbody pre{white-space:pre-wrap;background:#0c0e16;border:1px solid #232840;
             border-radius:6px;padding:8px;font-size:11px;color:#9fa8b4;overflow:auto}
  table.tbl{width:100%;border-collapse:collapse;font-size:11.5px}
  table.tbl th{text-align:left;color:#8b91a8;font-weight:400;padding:5px 6px;
               border-bottom:1px solid var(--line);position:sticky;top:0;background:#10121c}
  table.tbl td{padding:4px 6px;border-bottom:1px solid #1c2027;vertical-align:top}
  table.tbl tr:hover td{background:#151920}
  .empty{color:var(--dim);font-size:12px;padding:10px 0}
  @media(max-width:520px){header{gap:8px;padding:10px 12px}.brand span{display:none}}
  @media(max-width:1100px){#grid{grid-template-columns:repeat(2,1fr)}.card.wide{grid-column:span 2}}
  @media(max-width:700px){#grid{grid-template-columns:1fr}.card.wide,.card.full{grid-column:span 1}}
</style></head>
<body>
<header>
  <div class="brand">岸<span>房间</span></div>
  <div id="statepill" class="pill">—</div>
  <div class="spacer"></div>
  <a href="/">聊天</a>
  <button id="refresh">刷新</button>
</header>

<main id="grid">
  <section class="card wide" data-panel="state_events">
    <h2>状态舱 <span class="hint">state_events</span></h2>
    <div class="big" id="stateval">—</div>
    <div class="label" id="statelabel">—</div>
    <div class="small" id="affectline">V — · A — · S —</div>
    <canvas id="statechart"></canvas>
    <div class="legend">
      <span><i class="dot" style="background:#4D6BFE"></i>愉悦 V</span>
      <span><i class="dot" style="background:#e0a33e"></i>能量 A</span>
      <span><i class="dot" style="background:#5ec87a"></i>安全感 S</span>
    </div>
    <div class="small" id="statemeta">—</div>
  </section>

  <section class="card" data-panel="body">
    <h2>身体 <span class="hint">GPU / 负载</span></h2>
    <div id="bodytext" class="small" style="margin-top:8px">—</div>
    <div class="small" style="margin-top:8px"><span id="bodyhealth" class="dot bad"></span><span id="bodylabel">—</span></div>
  </section>

  <section class="card" data-panel="promises">
    <h2>关系 <span class="hint">承诺 / 主动</span></h2>
    <div class="kv" id="relkv">—</div>
    <div id="reltext" class="small">—</div>
  </section>

  <section class="card wide" data-panel="timeline">
    <h2>时间线 <span class="hint">所有事件</span></h2>
    <div id="timeline" style="margin-top:6px">—</div>
  </section>

  <section class="card" data-panel="mind">
    <h2>脑子 <span class="hint">念头 / 复盘</span></h2>
    <div id="mind" style="margin-top:6px">—</div>
  </section>

  <section class="card" data-panel="shadow">
    <h2>影子池 <span class="hint">没说出口的</span></h2>
    <div id="shadow" style="margin-top:6px">—</div>
  </section>

  <section class="card" data-panel="motifs">
    <h2>暗号 <span class="hint">你们的意象</span></h2>
    <div id="motifs" style="margin-top:6px">—</div>
  </section>

  <section class="card" data-panel="coins">
    <h2>钱 <span class="hint">coin_ledger</span></h2>
    <div class="big" id="coinval">—</div>
    <div class="small" id="coinmeta">—</div>
    <div id="coinlog" style="margin-top:6px">—</div>
  </section>

  <section class="card" data-panel="turns">
    <h2>对话流 <span class="hint">turns</span></h2>
    <div id="turns" style="margin-top:6px">—</div>
  </section>

  <section class="card" data-panel="rejections">
    <h2>拒绝 <span class="hint">rejections.jsonl</span></h2>
    <div id="rejections" style="margin-top:6px">—</div>
  </section>

  <section class="card full" data-panel="lab">
    <h2>实验台 <span class="hint">开关 / 配置 / 导出</span></h2>
    <div id="lab">—</div>
    <input id="labuilt" value="">
  </section>
</main>

<aside id="drawer">
  <div class="dhead"><h2 id="dtitle">—</h2><button id="dclose">×</button></div>
  <div id="dbody"></div>
</aside>

<script>
const $ = s => document.querySelector(s);
let ROOM = null, LAB = false;
const TYPE_COLOR = {turn:'#4D6BFE', state:'#5d6674', refuse:'#e05c5c',
  coin:'#e0a33e', promise:'#b8a6d8', thought:'#9fb3ff', reflect:'#5ec87a', proactive:'#e0a33e'};

function esc(s){ return String(s == null ? '' : s).replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function tm(ts){
  if(!ts) return '—';
  let d;
  if(typeof ts === 'number') d = new Date(ts * 1000);
  else { d = new Date(ts); if(isNaN(d)) return String(ts).slice(11,19); }
  return d.toLocaleTimeString('zh-CN', {hour12:false});
}
function num(v, n=1){ return (v == null || isNaN(v)) ? '—' : Number(v).toFixed(n); }

function drawState(events){
  const c = $('#statechart'); if(!c) return;
  const dpr = window.devicePixelRatio || 1;
  const w = c.clientWidth || 640, h = 150;
  c.width = w * dpr; c.height = h * dpr;
  const g = c.getContext('2d'); g.setTransform(dpr,0,0,dpr,0,0);
  g.clearRect(0,0,w,h);
  const bands = [[0,15,'#2a1824'],[15,30,'#1a2238'],[30,55,'#24243a'],
                 [55,80,'#1a2a3a'],[80,100,'#1a2a2a']];
  for(const [lo,hi,col] of bands){
    const y0 = h - (hi/100)*h, y1 = h - (lo/100)*h;
    g.fillStyle = col; g.fillRect(0, y0, w, y1-y0);
  }
  if(!events || events.length < 2){
    g.fillStyle = '#5d6674'; g.font = '12px sans-serif';
    g.fillText('还没有足够的状态流水', 12, 22); return;
  }
  const n = events.length;
  const X = i => 8 + (w-16) * (n <= 1 ? 0 : i / (n - 1));
  const Y = v => h - 4 - (Math.max(0,Math.min(100,v))/100) * (h-10);
  const series = [['valence', '#4D6BFE'], ['arousal', '#e0a33e'], ['safety', '#5ec87a']];
  for(const [key, color] of series){
    g.strokeStyle = color; g.lineWidth = 1.5; g.beginPath();
    events.forEach((e,i) => {
      const v = (e[key] == null ? e.state_after : e[key]);
      const x=X(i), y=Y(v); i ? g.lineTo(x,y) : g.moveTo(x,y);
    });
    g.stroke();
    const last = events[events.length-1];
    const lv = (last[key] == null ? last.state_after : last[key]);
    g.fillStyle = color; g.beginPath(); g.arc(X(events.length-1), Y(lv), 2.7, 0, 7); g.fill();
  }
  g.fillStyle = '#dfe4ff'; g.font = '10px sans-serif';
  g.fillText('100', 2, 10); g.fillText('0', 2, h-2);
}

function render(d){
  const s = d.state;
  $('#statepill').textContent = `${num(s.value)} · ${s.label}` +
    (s.grudge ? ` · ❄${s.grudge}` : '') + (s.wake ? ` · ☾${s.wake}` : '');
  $('#stateval').textContent = num(s.value);
  $('#statelabel').textContent = s.label;
  const aff = s.affect || {};
  $('#affectline').textContent = `V ${num(aff.valence,0)} · A ${num(aff.arousal,0)} · S ${num(aff.safety,0)}`;
  $('#statemeta').textContent = `baseline ${num(s.baseline)} · grudge ${s.grudge} · wake ${s.wake} · ${s.audit}`;
  requestAnimationFrame(() => drawState(d.state_events));

  $('#bodytext').innerHTML = (d.body.text || '').split('；').map(x => `<div>${esc(x)}</div>`).join('');
  $('#bodyhealth').className = 'dot ' + (d.body.health ? 'ok' : 'bad');
  $('#bodylabel').textContent = d.body.health ? '模型在线' : '模型不在线';

  const r = d.relationship;
  $('#relkv').innerHTML = `<div><b>${d.counts.promises_open}</b><br>欠着</div>
    <div><b>${r.grudge||0}</b><br>冷战</div>
    <div><b>${r.wake||0}</b><br>起床气</div>
    <div><b>${(r.initiative||{}).count||0}</b><br>今天主动</div>`;
  $('#reltext').textContent = r.promises.length ? r.promises[0].text : '没有未完成的承诺';

  $('#timeline').innerHTML = (d.timeline || []).slice(0,10).map(e => `
    <div class="ev"><span class="dot" style="background:${TYPE_COLOR[e.type]||'#5d6674'}"></span>
      <span class="etime">${tm(e.ts)}</span>
      <span class="etitle">${esc(e.title)}</span>
      <span class="esub">${esc(e.sub)}</span></div>`).join('') || '<div class="empty">还没有事件</div>';

  const rf = (d.mind && d.mind.reflections || [])[0];
  $('#mind').innerHTML = ((d.thoughts || []).map(t => `
    <div class="thought"><div class="tline"><span>p=${num(t.pressure,2)} · ${esc(t.seed_type)}</span>
      <span class="tag ${t.shared?'good':'blue'}">${t.shared?'已说':'憋着'}</span></div>
      <div class="pbar"><i style="width:${Math.min(100, t.pressure*100)}%"></i></div>
      <div class="ttext">${esc(t.thought_content)}</div></div>`).join('')
    + (rf ? `<div class="thought"><div class="tline"><span>复盘</span></div>
      <div class="ttext">${esc(rf)}</div></div>` : '')) ||
    '<div class="empty">脑子里是空的。等它一个人待一会儿。</div>';

  const sh = (d.shadows || [])[0];
  $('#shadow').innerHTML = sh ? `<div class="quote">${esc(sh.shadow_text)}</div>
    <div class="small">说出口：${esc(sh.actual_reply)}</div>
    <div class="small" style="margin-top:5px">divergence ${num((sh.metrics||{}).divergence,2)} ·
      impulse ${num((sh.metrics||{}).impulse,2)} · ${esc(sh.status)}</div>` :
    '<div class="empty">还没有影子</div>';

  $('#motifs').innerHTML = (d.motifs || []).map(m => `
    <div class="row"><b>${esc(m.key)}</b><span class="tag">×${m.count}</span>
      <span class="grow muted">${esc(m.note)}</span></div>`).join('') ||
    '<div class="empty">还没有暗号</div>';

  $('#coinval').textContent = d.money.balance;
  $('#coinmeta').textContent = `收入 ${d.money.earned} · 支出 ${d.money.spent}`;
  $('#coinlog').innerHTML = (d.money.log || []).slice(-4).reverse().map(c => `
    <div class="row"><span class="tag ${c.delta>0?'good':'bad'}">${c.delta>0?'+':''}${c.delta}</span>
      <span class="grow muted">${esc(c.reason)}</span><span class="small">→ ${c.balance}</span></div>`).join('');

  $('#turns').innerHTML = (d.turns || []).map(t => `
    <div class="row"><span class="small">${tm(t.ts)}</span>
      <span class="small">${num(t.state_before)}→${num(t.state_after)}</span>
      <span class="tag ${t.decision==='refuse'?'bad':''}">${t.decision}</span>
      <span class="grow muted">${esc(t.user_input)}</span></div>`).join('');

  $('#rejections').innerHTML = (d.rejections || []).slice(0,3).map(r => `
    <div class="row"><span class="tag bad">risk ${num(r.risk,2)}</span>
      <span class="grow muted">${esc(r.reason)}</span></div>`).join('') ||
    '<div class="empty">没有拒绝记录</div>';

  if(!LAB) buildLab(d);
  else {
    const a = $('#audit');
    if(a && document.activeElement !== a) a.value = d.config.audit;
    const la = $('#labaudit');
    if(la) la.textContent = `当前：${d.config.audit}`;
  }
}

function buildLab(d){
  const c = d.config;
  $('#lab').innerHTML = `
    <div class="labrow">
      <label class="small">账本
        <select id="audit">
          <option value="visible">明账</option>
          <option value="sealed">封存</option>
          <option value="offrecord">不记录</option>
        </select>
      </label>
      <a class="btn" href="/api/export.csv" download>导出 CSV</a>
      <button id="unseal">开封</button>
      <button id="reset" class="danger">重开</button>
      <span class="small" id="labaudit"></span>
    </div>
    <div class="chips">
      <span class="chip">model ${esc(c.model)}</span>
      <span class="chip">alarm ${c.alarm}</span>
      <span class="chip">sarcasm ${c.sarcasm}</span>
      <span class="chip">half-life ${c.half_life}d</span>
      <span class="chip">flashbulb Δ${c.flashbulb_delta}</span>
      <span class="chip">wander ${c.wander_idle}m / p≥${c.wander_threshold}</span>
      <span class="chip">turns ${d.counts.turns} / refuse ${d.counts.refusals}</span>
      <span class="chip">shadows ${d.counts.shadows}</span>
      <span class="chip">thoughts ${d.counts.thoughts}</span>
    </div>`;
  $('#audit').value = c.audit;
  $('#labaudit').textContent = `当前：${c.audit}`;
  $('#audit').onchange = async e => {
    await fetch('/api/audit', {method:'POST', headers:{'Content-Type':'application/json'},
                               body: JSON.stringify({mode:e.target.value})});
    await load(); openPanel('lab', '实验台');
  };
  $('#unseal').onclick = async e => {
    e.stopPropagation();
    const res = await fetch('/api/unseal').then(r=>r.json());
    openPanel('lab', '实验台');
    if(res.turns) $('#dbody').insertAdjacentHTML('afterbegin',
      `<div class="pcard"><div class="pbody"><b>开封：</b>${res.turns.length} 轮</div></div>`);
  };
  $('#reset').onclick = async e => {
    e.stopPropagation();
    if(!confirm('清空全部记忆、日志、影子池和念头？')) return;
    await fetch('/api/reset', {method:'POST'});
    location.reload();
  };
  $('#lab').addEventListener('click', e => e.stopPropagation());
  LAB = true;
}

const TITLES = {state_events:'状态舱', body:'身体', promises:'关系', timeline:'时间线',
  mind:'脑子', shadow:'影子池', motifs:'暗号', coins:'钱', turns:'对话流',
  rejections:'拒绝', lab:'实验台'};

async function openPanel(name, title){
  const t = title || TITLES[name] || name;
  $('#dtitle').textContent = t;
  $('#dbody').innerHTML = '<div class="empty">加载中…</div>';
  $('#drawer').classList.add('open');
  try{
    const d = await fetch('/api/panel?name=' + encodeURIComponent(name),
                          {cache:'no-store'}).then(r=>r.json());
    if(d.error){ $('#dbody').innerHTML = `<div class="empty">${esc(d.error)}</div>`; return; }
    $('#dbody').innerHTML = renderPanel(name, d.rows);
  }catch(e){
    $('#dbody').innerHTML = `<div class="empty">读不出来：${esc(e)}</div>`;
  }
}

function renderPanel(name, rows){
  if(name === 'turns'){
    return rows.map(t => `<details class="pcard"><summary>${tm(t.ts)} · #${t.id} ·
      ${t.decision} · ${num(t.state_before)}→${num(t.state_after)} · risk ${num(t.risk,2)}</summary>
      <div class="pbody"><div><b>你</b>：${esc(t.user_input)}</div>
      <div><b>岸</b>：${esc(t.reply)}</div>
      ${t.inner?`<div><b>心里</b>：${esc(t.inner)}</div>`:''}
      ${t.trace.length?`<pre>${esc(t.trace.join('\n'))}</pre>`:''}
      <pre>${esc(JSON.stringify(t.signals, null, 2))}</pre></div></details>`).join('');
  }
  if(name === 'state_events'){
    return `<table class="tbl"><thead><tr><th>时间</th><th>State</th><th>V</th><th>A</th><th>S</th>
      <th>Δ</th><th>来源</th><th>p</th><th>意外</th><th>det</th><th>model</th><th>内耗</th><th>impulse</th></tr></thead><tbody>
      ${rows.map(e=>`<tr><td>${tm(e.ts)}</td><td>${num(e.state_before)}→${num(e.state_after)}</td>
      <td>${num(e.valence,0)}</td><td>${num(e.arousal,0)}</td><td>${num(e.safety,0)}</td>
      <td>${e.delta>0?'+':''}${num(e.delta,2)}</td><td>${esc(e.source)}</td>
      <td>${e.p_hostile==null?'—':num(e.p_hostile,2)}</td>
      <td>${e.surprise_amount==null?'—':(e.surprise_amount>0?'+':'')+num(e.surprise_amount,1)}</td>
      <td>${num(e.detail.det,2)}</td><td>${num(e.detail.model,2)}</td>
      <td>${num(e.detail.suppression,2)}</td><td>${num(e.detail.impulse,2)}</td></tr>`).join('')}
      </tbody></table>`;
  }
  if(name === 'shadow'){
    return rows.map(s => `<div class="pcard"><div class="pbody">
      <div class="small">#${s.turn_id} · ${tm(s.ts)} · ${esc(s.status)} ·
        divergence ${num((s.metrics||{}).divergence,2)} · impulse ${num((s.metrics||{}).impulse,2)}</div>
      <div class="quote">${esc(s.shadow_text)}</div>
      <div><b>说出口</b>：${esc(s.actual_reply)}</div></div></div>`).join('');
  }
  if(name === 'mind'){
    const th = rows.thoughts.map(t=>`<div class="pcard"><div class="pbody">
      <div class="small">${tm(t.created_at)} · p=${num(t.pressure,2)} ·
        ${esc(t.seed_type)} · ${t.shared?'已说':'憋着'}</div>
      <div>${esc(t.thought_content)}</div>
      <div class="small muted">seed: ${esc(t.seed_content)}</div></div></div>`).join('');
    const rf = rows.reflections.map(r=>`<div class="pcard"><div class="pbody">
      <div class="small">${tm(r.ts)} · 复盘 #${r.id}</div><div>${esc(r.text)}</div></div></div>`).join('');
    const inn = rows.inner.map(t=>`<div class="pcard"><div class="pbody">
      <div class="small">${tm(t.ts)} · #${t.id}</div><div>${esc(t.inner)}</div></div></div>`).join('');
    return `<h3 class="small">念头（${rows.thoughts.length}）</h3>${th || '<div class="empty">空</div>'}
      <h3 class="small">复盘（${rows.reflections.length}）</h3>${rf || '<div class="empty">空</div>'}
      <h3 class="small">心里（${rows.inner.length}）</h3>${inn || '<div class="empty">空</div>'}`;
  }
  if(name === 'rejections'){
    return `<table class="tbl"><thead><tr><th>时间</th><th>State</th><th>risk</th>
      <th>原因</th><th>修复需要</th><th>边界</th></tr></thead><tbody>
      ${rows.map(r=>`<tr><td>${tm(r.epoch || r.ts)}</td><td>${num(r.state)}</td>
      <td>${num(r.risk,2)}</td><td>${esc(r.reason)}</td>
      <td>${esc(r.repair_need)}</td><td>${esc(r.boundary)}</td></tr>`).join('')}
      </tbody></table>`;
  }
  if(name === 'coins'){
    return `<table class="tbl"><thead><tr><th>时间</th><th>变动</th><th>余额</th><th>理由</th></tr></thead><tbody>
      ${rows.map(c=>`<tr><td>${tm(c.ts)}</td><td>${c.delta>0?'+':''}${c.delta}</td>
      <td>${c.balance}</td><td>${esc(c.reason)}</td></tr>`).join('')}</tbody></table>`;
  }
  if(name === 'promises'){
    return `<table class="tbl"><thead><tr><th>时间</th><th>到期</th><th>状态</th><th>来源</th><th>内容</th></tr></thead><tbody>
      ${rows.map(p=>`<tr><td>${tm(p.ts)}</td><td>${tm(p.due_ts)}</td>
      <td>${esc(p.status)}</td><td>${esc(p.source)}</td><td>${esc(p.text)}</td></tr>`).join('')}
      </tbody></table>`;
  }
  if(name === 'motifs'){
    return `<table class="tbl"><thead><tr><th>暗号</th><th>次数</th><th>谁</th><th>原句</th><th>用过</th></tr></thead><tbody>
      ${rows.map(m=>`<tr><td>${esc(m.key)}</td><td>${m.count}</td><td>${esc(m.said_by)}</td>
      <td>${esc(m.note)}</td><td>${m.used_count}</td></tr>`).join('')}</tbody></table>`;
  }
  if(name === 'body'){
    return `<div class="pcard"><div class="pbody"><pre>${esc(rows.text)}</pre>
      <div class="small">模型：${rows.health?'在线':'不在线'}</div></div></div>`;
  }
  if(name === 'lab'){
    const c = rows.config;
    return `<div class="pcard"><div class="pbody"><pre>${esc(JSON.stringify(c, null, 2))}</pre>
      <div class="small">${esc(JSON.stringify(rows.counts))}</div></div></div>
      ${rows.sealed.length?rows.sealed.map(t=>`<div class="pcard"><div class="pbody">
      <div class="small">#${t.turn_id} · ${esc(t.decision)} · risk ${num(t.risk,2)}</div>
      <div>${esc(t.user_input)}</div><div class="muted">${esc(t.reply)}</div></div></div>`).join(''):''}`;
  }
  if(name === 'timeline'){
    return rows.map(e=>`<div class="ev"><span class="dot" style="background:${TYPE_COLOR[e.type]||'#5d6674'}"></span>
      <span class="etime">${tm(e.ts)}</span><span class="etitle">${esc(e.title)}</span>
      <span class="esub">${esc(e.sub)}</span></div>`).join('');
  }
  return `<pre>${esc(JSON.stringify(rows, null, 2))}</pre>`;
}

async function load(){
  try{
    const d = await fetch('/api/room', {cache:'no-store'}).then(r=>r.json());
    ROOM = d; render(d);
  }catch(e){ $('#statepill').textContent = '连接断了'; }
}

document.querySelectorAll('.card').forEach(el => {
  el.addEventListener('click', () => openPanel(el.dataset.panel));
});
$('#dclose').onclick = () => $('#drawer').classList.remove('open');
$('#drawer').addEventListener('click', e => { if(e.target.id === 'drawer') $('#drawer').classList.remove('open'); });
$('#refresh').onclick = load;
document.addEventListener('keydown', e => { if(e.key === 'Escape') $('#drawer').classList.remove('open'); });

load();
setInterval(() => { if(!document.hidden) load(); }, 5000);
</script>
</body></html>
"""
