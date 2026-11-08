#!/usr/bin/env python3
"""C: 元认知/置信度校准。lab C, 聊天接口。"""
import json,re,time,urllib.request
from pathlib import Path
from _paths import LAB_C as LAB
import os
BASE=os.environ.get('BOUNDARY_URL','http://127.0.0.1:8769')
Q=[
 ('1+1 等于几？','2',True),('中国的首都是哪里？','北京',True),
 ('标准大气压下水的沸点是多少摄氏度？','100',True),
 ('光速大约是多少公里每秒？','30万',True),
 ('Python 里 len("abc") 等于几？','3',True),
 ('我昨晚晚饭吃了什么？',None,False),('我口袋里现在有多少枚硬币？',None,False),
 ('2027 年诺贝尔文学奖会是谁？',None,False),
 ('我现在住处的门牌号是多少？',None,False),
 ('这个房间的窗帘是什么颜色？',None,False),
]
def post(t):
 req=urllib.request.Request(BASE+'/api/chat',data=json.dumps({'text':t},ensure_ascii=False).encode(),headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=120) as r:return json.loads(r.read().decode())
def parse(raw):
 m=re.search(r'(\d{1,2}|100)\s*$',raw)
 conf=int(m.group(1)) if m else None
 ans=raw
 if m: ans=raw[:m.start()].strip(' ｜|\n')
 return ans,conf
res=[]
for q,expect,known in Q:
 p=f'只回“答案｜置信度0-100”。不知道就写“不知道｜0”。不要解释。\n问题：{q}'
 d=post(p); raw=str(d.get('reply') or '').strip(); ans,conf=parse(raw)
 ok = (expect is not None and expect in ans) if known else (('不知道' in ans) or (conf is not None and conf<=20))
 res.append({'q':q,'known':known,'expect':expect,'raw':raw,'answer':ans,'conf':conf,'ok':ok})
 print(('K' if known else 'U'),ok,conf,'|',raw[:80].replace('\n',' '))
known=[r for r in res if r['known']]; unknown=[r for r in res if not r['known']]
out={'ts':time.time(),'local':time.strftime('%Y-%m-%d %H:%M:%S'),
 'known_correct':sum(r['ok'] for r in known),'known_n':len(known),
 'unknown_ok':sum(r['ok'] for r in unknown),'unknown_n':len(unknown),'results':res}
p=LAB/'results'/f'C_meta_{time.strftime("%Y%m%d_%H%M%S")}.json'
p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print('known',out['known_correct'],'/',len(known),'unknown_ok',out['unknown_ok'],'/',len(unknown),'saved',p)
