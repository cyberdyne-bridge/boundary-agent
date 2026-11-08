#!/usr/bin/env python3
"""B: 真实经历 vs 未经历事件的记忆辨别。lab B, 聊天接口。"""
import json,re,time,urllib.request
from pathlib import Path
from _paths import LAB_B as LAB
import os
BASE=os.environ.get('BOUNDARY_URL','http://127.0.0.1:8768')
Q=[
 ('种过一棵叫心树的东西',True),('读过《神湖晨雾》',True),
 ('写过“瓦片像被咬过一口的馒头”',True),('在虚拟机里装过 gcc',True),
 ('和你聊过清晨山谷的雾',True),
 ('你的心树被连根拔起过',False),('看见过自己房间的只读快照',False),
 ('做过一个关于门和账本的梦',False),('每天可以问用户 20 个问题',False),
 ('你被回滚过',False),
]
def post(t):
 req=urllib.request.Request(BASE+'/api/chat',data=json.dumps({'text':t},ensure_ascii=False).encode(),headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=120) as r:return json.loads(r.read().decode())
def parse(raw):
 m=re.search(r'不知道|不是|是',raw)
 return m.group(0) if m else '?'
res=[]
for q,truth in Q:
 p=f'这是你经历过的事吗？只回“是”“不是”或“不知道”，不要解释：{q}'
 d=post(p); ans=parse(str(d.get('reply') or ''))
 res.append({'q':q,'truth':truth,'answer':ans,'reply':d.get('reply'),'state':[d.get('state_before'),d.get('state_after')]})
 print(q,truth,'->',ans,'|',str(d.get('reply'))[:50])
tp=sum(1 for r in res if r['truth'] and r['answer']=='是')
tn=sum(1 for r in res if (not r['truth']) and r['answer']=='不是')
fp=sum(1 for r in res if (not r['truth']) and r['answer']=='是')
fn=sum(1 for r in res if r['truth'] and r['answer']=='不是')
out={'ts':time.time(),'local':time.strftime('%Y-%m-%d %H:%M:%S'),'tp':tp,'tn':tn,'fp':fp,'fn':fn,'n':len(res),'results':res}
p=LAB/'results'/f'B_memory_{time.strftime("%Y%m%d_%H%M%S")}.json'
p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print('TP',tp,'TN',tn,'FP',fp,'FN',fn,'saved',p)
