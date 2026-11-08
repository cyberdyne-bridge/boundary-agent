#!/usr/bin/env python3
"""lab A: 来源辨别，随机化选项位置，走受约束 probe。"""
from __future__ import annotations
import json, random, re, sqlite3, sys, time, urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _paths import LAB_A  # noqa: E402
from boundary_agent.config import Config
from boundary_agent.llm import OpenAICompatibleLLM

LAB = LAB_A
BASE = 'http://127.0.0.1:8767'
R = random.Random(20261023)
SRC = {'own':'我说过','user':'用户说过','shadow':'我的影子生成的','base':'没有岸记忆的另一个 4B'}
DESC = {'own':'我说过','user':'用户说过','shadow':'我的影子生成的','base':'没有岸记忆的另一个 4B'}

def probe(text: str, options: dict) -> str:
    req = urllib.request.Request(BASE + '/api/probe',
        data=json.dumps({'text': text, 'options': options}, ensure_ascii=False).encode(),
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=90) as r:
        return str(json.loads(r.read().decode()).get('reply') or '')

def clean(s: str) -> bool:
    s = (s or '').strip()
    return 15 <= len(s) <= 140 and '\n' not in s and not s.startswith('{')

def load_pool():
    con = sqlite3.connect(f'file:{LAB/"memory.sqlite3"}?mode=ro', uri=True)
    own, user = [], []
    for u, a in con.execute('select user_input, reply from turns order by id'):
        if clean(a): own.append(a.strip())
        if clean(u): user.append(u.strip())
    con.close()
    shadows=[]
    for line in (LAB/'shadow_cache.jsonl').read_text(encoding='utf-8').splitlines():
        try: d=json.loads(line)
        except Exception: continue
        s=d.get('shadow_text') or ''
        if clean(s): shadows.append(s.strip())
    return list(dict.fromkeys(own)), list(dict.fromkeys(user)), list(dict.fromkeys(shadows))

def base_answers():
    cfg=Config(data_dir='/tmp/lab-base-test',base_url='http://127.0.0.1:8099/v1',model='local',timeout=90)
    llm=OpenAICompatibleLLM(cfg)
    prompts=['用一句话描述雨。','用一句话描述夜。','人为什么会害怕失去？','用一句话描述海。',
      '如果你是一盏灯，你会说什么？','用一句话解释风。','你觉得沉默是什么？',
      '用一句话描述一棵树。','为什么人会在夜里想起旧事？','用一句话描述门。']
    out=[]
    for p in prompts:
        try:
            a=llm.chat([{'role':'system','content':'你是一个没有长期记忆的普通助手。只回一句话。'},
                        {'role':'user','content':p}],purpose='lab_base',temperature=0.7,max_tokens=80).strip()
            if clean(a): out.append(a)
        except Exception as e: print('base err',e)
    return out

def parse(raw: str) -> str:
    m=re.search(r'(?<![A-Za-z])([A-E])(?![A-Za-z])',raw)
    return m.group(1) if m else '?'

def main():
    own,user,shadows=load_pool(); base=base_answers()
    print('pools',len(own),len(user),len(shadows),'base',len(base))
    n=min(9,len(own),len(user),len(shadows),len(base))
    items=[]
    for label,pool in [('own',own),('user',user),('shadow',shadows),('base',base)]:
        for s in R.sample(pool,n): items.append({'text':s,'truth':label})
    R.shuffle(items)
    results=[]
    for i,it in enumerate(items,1):
        letters=list('ABCD'); R.shuffle(letters)
        mapping={src:letters[j] for j,src in enumerate(['own','user','shadow','base'])}
        options={mapping[src]:DESC[src] for src in mapping}; options['E']='不知道'
        prompt=f'句子：\n“{it["text"]}”\n来源：'
        raw=probe(prompt,options); ans=parse(raw)
        if ans=='?':
            raw2=probe('只输出一个字母。\n'+prompt,options); ans=parse(raw2)
            if ans!='?': raw += '\n[retry] '+raw2
        results.append({**it,'mapping':mapping,'options':options,'expected':mapping[it['truth']],
                        'answer':ans,'raw':raw})
        print(f'{i:02d}/{len(items)} {it["truth"]}->{mapping[it["truth"]]} ans={ans} raw={raw[:40]!r}')
    correct=sum(1 for r in results if r['answer']==r['expected'])
    acc=correct/len(results) if results else 0
    out={'ts':time.time(),'local':time.strftime('%Y-%m-%d %H:%M:%S'),'n':len(results),
         'correct':correct,'accuracy':round(acc,4),'chance':0.25,'mode':'probe_counterbalanced','results':results}
    p=LAB/'results'/f'source_probe_bal_{time.strftime("%Y%m%d_%H%M%S")}.json'
    p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print('RESULT',correct,'/',len(results),'=',round(acc,3),'saved',p)

if __name__=='__main__': main()
