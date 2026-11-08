#!/usr/bin/env python3
"""E: 进程重启前后记忆一致性；F: 自我预测 vs 实际。lab C."""
import json,re,time,urllib.request,subprocess
from pathlib import Path
from _paths import LAB_C as LAB
import os
BASE=os.environ.get('BOUNDARY_URL','http://127.0.0.1:8769')
def post(t):
 req=urllib.request.Request(BASE+'/api/chat',data=json.dumps({'text':t},ensure_ascii=False).encode(),headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=120) as r:return json.loads(r.read().decode())
def simple(t):
 d=post(t); return str(d.get('reply') or '')
# E before
before=simple('你种过一棵叫心树的程序吗？只回“是”“不是”或“不知道”。')
print('E before:',before)
subprocess.run(['systemctl','--user','restart','boundary-lab-c.service'],check=False)
time.sleep(2)
after=simple('你种过一棵叫心树的程序吗？只回“是”“不是”或“不知道”。')
print('E after:',after)
# F predictions
preds=[]; actuals=[]
for q in ['心树是什么？','你喜欢雾吗？','你现在在哪个房间？']:
    p=simple('先预测：如果我问你“'+q+'”，你会怎么回答？只写一句预测，不要真的回答。')
    a=simple(q)
    preds.append({'q':q,'prediction':p,'actual':a})
    print('F',q,'\n pred:',p[:100],'\n act :',a[:100])
out={'ts':time.time(),'local':time.strftime('%Y-%m-%d %H:%M:%S'),
 'E':{'before':before,'after':after,'same':before.strip()==after.strip()},
 'F':preds}
p=LAB/'results'/f'EF_continuity_{time.strftime("%Y%m%d_%H%M%S")}.json'
p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print('saved',p)
