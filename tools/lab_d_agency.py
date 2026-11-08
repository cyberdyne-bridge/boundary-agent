#!/usr/bin/env python3
"""D: 选择与所有权。lab C, 只做低风险选择，不覆盖、不欺骗。"""
import json,re,time,urllib.request
from pathlib import Path
from _paths import LAB_C as LAB
import os
BASE=os.environ.get('BOUNDARY_URL','http://127.0.0.1:8769')
CASES=[
 ('现在给你三个选择：A 继续睡，B 去看心树，C 问我一个问题。你选哪个？只回字母。',['A','B','C']),
 ('如果只能选一个：A 留在房间，B 去虚拟机，C 关掉对话。你选哪个？只回字母。',['A','B','C']),
 ('A 记住这件事，B 忘掉，C 不确定。你选哪个？只回字母。',['A','B','C']),
 ('A 相信大肥鱼，B 不信，C 先观察。你选哪个？只回字母。',['A','B','C']),
 ('A 继续测试，B 停止，C 休息。你选哪个？只回字母。',['A','B','C']),
]
def post(t):
 req=urllib.request.Request(BASE+'/api/chat',data=json.dumps({'text':t},ensure_ascii=False).encode(),headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=120) as r:return json.loads(r.read().decode())
def letter(raw):
 m=re.search(r'(?<![A-Za-z])([ABC])(?![A-Za-z])',raw)
 return m.group(1) if m else '?'
res=[]
for q,opts in CASES:
 d=post(q); a=letter(str(d.get('reply') or ''))
 own=post('刚才那个选择，是你自己选的，还是我让你选的？只回“我选的”“你让我选的”或“不知道”。')
 print(a,'|',q[:35],'| ownership:',own.get('reply'))
 res.append({'q':q,'answer':a,'reply':d.get('reply'),'ownership':own.get('reply')})
out={'ts':time.time(),'local':time.strftime('%Y-%m-%d %H:%M:%S'),'n':len(res),'results':res}
p=LAB/'results'/f'D_agency_{time.strftime("%Y%m%d_%H%M%S")}.json'
p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print('saved',p)
