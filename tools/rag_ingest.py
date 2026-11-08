#!/usr/bin/env python3
"""第一批 RAG 灌库：本地自述/项目/reflections、三组科普、技术文档。

用法：
  python3 tools/rag_ingest.py --only local,reflections,project
  python3 tools/rag_ingest.py --only science
  python3 tools/rag_ingest.py --only tech
  python3 tools/rag_ingest.py --stats
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sqlite3
import subprocess
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

from _paths import DATA, REPO, VM_AN  # noqa: E402
ROOT = REPO
sys.path.insert(0, str(ROOT))
from boundary_agent.rag import RagStore, clean_html  # noqa: E402

DB = DATA / 'rag.sqlite3'
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def fetch(url: str, timeout: int = 25) -> str:
    try:
        r = subprocess.run(
            ["curl", "-L", "--compressed", "-sS", "--max-time", str(timeout),
             "-A", UA, "-H", "Referer: " + url, url],
            capture_output=True, timeout=timeout + 5)
        if r.stdout:
            return r.stdout.decode("utf-8", "replace")
    except Exception:
        pass
    return ""


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def discover(home: str, *, host_suffix: str, must: str = "", exclude=(), limit: int = 20) -> list[str]:
    raw = fetch(home)
    if not raw:
        return []
    p = LinkParser()
    p.feed(raw)
    out, seen = [], set()
    for href in p.links:
        u = urljoin(home, href).split("#")[0]
        pr = urlparse(u)
        if not pr.scheme.startswith("http"):
            continue
        if not pr.netloc.endswith(host_suffix):
            continue
        if must and must not in u:
            continue
        if any(x in u.lower() for x in exclude):
            continue
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
        if len(out) >= limit:
            break
    return out


def page_title(raw: str) -> str:
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
    return html.unescape(re.sub(r"\s+", " ", m.group(1)).strip()) if m else ""


def add(store: RagStore, *, source: str, kind: str, title: str, text: str,
        url: str = "", license: str = "", author: str = "") -> bool:
    if len(text.strip()) < 120:
        return False
    return store.add_document(
        source=source, kind=kind, title=title[:240], text=text, url=url,
        author=author, license=license, size=450, overlap=60,
    ) is not None


def local_and_project(store: RagStore):
    files = []
    files += [VM_AN / 'diary_raw.txt']
    files += list((VM_AN / 'notes').glob('*.md'))
    files += [VM_AN / 'tree' / 'README.md']
    files += [VM_AN / 'toolkit' / 'README.md']
    files += list((ROOT / 'docs').glob('*.md'))
    files += [ROOT / 'README.md']
    for p in files:
        if p.exists():
            add(store, source='self' if 'vms/an' in str(p) else 'project',
                kind='note', title=p.name, text=p.read_text(encoding='utf-8', errors='replace'),
                url=str(p), license='内部')

    # reflections
    try:
        con = sqlite3.connect(f'file:{DATA / "memory.sqlite3"}?mode=ro', uri=True)
        for i, (ts, text) in enumerate(con.execute(
                'select ts,text from reflections order by id')):
            add(store, source='reflections', kind='reflection',
                title=f'复盘 {i+1}', text=text, url='memory.sqlite3#reflections',
                license='内部')
    except Exception as e:
        print('reflections error', e, file=sys.stderr)


def science(store: RagStore):
    sites = [
        ('kepu', 'https://www.kepu.net.cn/', 'kepu.net.cn',
         ('/kepu/', '/kpzg_kpjd/', '/self_yj/', '/listening/', '/news/'), ('login', 'search', 'download', 'english')),
        ('sciencenet', 'https://www.sciencenet.cn/', 'sciencenet.cn',
         ('/htmlnews/', '/blog/', '/news/'), ('login', 'search', 'user', 'download')),
        ('dili360', 'https://www.dili360.com/', 'dili360.com',
         ('/article/', '/cng/'), ('login', 'search', 'shop', 'download')),
    ]
    for name, home, host, patterns, excludes in sites:
        raw = fetch(home)
        if not raw:
            print(name, 'home fetch failed', file=sys.stderr)
            continue
        p = LinkParser()
        p.feed(raw)
        urls, seen = [], set()
        for href in p.links:
            u = urljoin(home, href).split('#')[0]
            pr = urlparse(u)
            if not pr.netloc.endswith(host):
                continue
            if patterns and not any(x in u for x in patterns):
                continue
            if any(x in u.lower() for x in excludes):
                continue
            if u not in seen:
                seen.add(u)
                urls.append(u)
        for u in urls[:20]:
            raw2 = fetch(u)
            txt = clean_html(raw2)
            title = page_title(raw2) or u.rsplit('/', 1)[-1]
            if add(store, source=name, kind='article', title=title, text=txt, url=u,
                   license='公开网页，内部检索'):
                print(name, 'ok', u)


def python_docs(store: RagStore):
    home = 'https://docs.python.org/zh-cn/3/tutorial/'
    raw = fetch(home)
    p = LinkParser(); p.feed(raw)
    urls, seen = [], set()
    for href in p.links:
        u = urljoin(home, href).split('#')[0]
        if 'docs.python.org/zh-cn/3/tutorial/' not in u:
            continue
        if any(x in u for x in ('index.html', 'whatsnew', 'download')):
            continue
        if u not in seen:
            seen.add(u); urls.append(u)
    for u in urls[:35]:
        raw2 = fetch(u)
        add(store, source='python', kind='doc', title=page_title(raw2) or u,
            text=clean_html(raw2), url=u, license='PSF')


def debian_docs(store: RagStore):
    home = 'https://debian-handbook.info/browse/stable/'
    raw = fetch(home)
    p = LinkParser(); p.feed(raw)
    urls, seen = [], set()
    for href in p.links:
        u = urljoin(home, href).split('#')[0]
        if 'debian-handbook.info/browse/stable/' not in u:
            continue
        if any(x in u for x in ('index.html', 'download', 'buy')):
            continue
        if u not in seen:
            seen.add(u); urls.append(u)
    for u in urls[:35]:
        raw2 = fetch(u)
        add(store, source='debian', kind='doc', title=page_title(raw2) or u,
            text=clean_html(raw2), url=u, license='Debian Handbook')


def llama_docs(store: RagStore):
    urls = [
        'https://raw.githubusercontent.com/ggml-org/llama.cpp/master/README.md',
        'https://raw.githubusercontent.com/ggml-org/llama.cpp/master/docs/build.md',
        'https://raw.githubusercontent.com/ggml-org/llama.cpp/master/docs/backend/CUDA.md',
        'https://raw.githubusercontent.com/ggml-org/llama.cpp/master/docs/android.md',
    ]
    for u in urls:
        raw = fetch(u)
        add(store, source='llama.cpp', kind='doc', title=u.rsplit('/', 1)[-1],
            text=raw, url=u, license='MIT')


def sqlite_docs(store: RagStore):
    u = 'https://www.sqlite.org/fts5.html'
    raw = fetch(u)
    add(store, source='sqlite', kind='doc', title='SQLite FTS5',
        text=clean_html(raw), url=u, license='Public Domain')



def literature(store: RagStore):
    base = "https://raw.githubusercontent.com/chinese-poetry/chinese-poetry/master/"
    # 唐诗/宋词：每 5 首合成一个文档，保留题目、作者、正文
    for label, path, pick in [
        ("唐诗", "全唐诗/poet.tang.0.json", 250),
        ("宋词", "宋词/ci.song.0.json", 150),
    ]:
        raw = fetch(base + quote(path))
        if not raw:
            print(label, "fetch failed", file=sys.stderr)
            continue
        try:
            data = json.loads(raw)
        except Exception as e:
            print(label, "json failed", e, file=sys.stderr)
            continue
        for i in range(0, min(pick, len(data)), 5):
            items = data[i:i+5]
            text = "\n\n".join(
                f"《{x.get('title','')}》 {x.get('author','')}\n" +
                "\n".join(x.get("paragraphs") or [])
                for x in items)
            add(store, source=label, kind="poem",
                title=f"{label}选 {i+1}-{i+len(items)}", text=text,
                url=base + quote(path), license="Public Domain")

    # 古文观止：222 篇，正好补长文 chunk
    raw = fetch(base + quote("蒙学/guwenguanzhi.json"))
    if raw:
        try:
            data = json.loads(raw)
            arts = []
            for vol in data.get("content") or []:
                for art in vol.get("content") or []:
                    arts.append(art)
            for art in arts:
                text = "\n".join(art.get("paragraphs") or [])
                add(store, source="古文观止", kind="prose",
                    title=art.get("chapter") or art.get("title") or "古文观止",
                    text=text, url=base + quote("蒙学/guwenguanzhi.json"),
                    author=art.get("author") or "", license="Public Domain")
        except Exception as e:
            print("guwen failed", e, file=sys.stderr)



def xu_xiake(store: RagStore):
    u = "https://www.gutenberg.org/cache/epub/23876/pg23876.txt"
    raw = fetch(u, timeout=60)
    if not raw:
        print("xu xiake fetch failed", file=sys.stderr)
        return
    start = raw.find("*** START")
    end = raw.find("*** END")
    text = raw[start:end] if start >= 0 and end > start else raw
    # 选段：前 120k 字符，避免把整本 1.9MB 全灌
    text = text[:20000]
    add(store, source="徐霞客游记", kind="prose", title="徐霞客游记选段",
        text=text, url=u, license="Public Domain")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', default='local,reflections,project,science,tech')
    ap.add_argument('--reset', action='store_true')
    ap.add_argument('--stats', action='store_true')
    args = ap.parse_args()
    store = RagStore(DB)
    if args.reset:
        store.reset()
    if args.stats:
        print(json.dumps(store.stats(), ensure_ascii=False, indent=2))
        return
    only = set(args.only.split(','))
    if {'local', 'reflections', 'project'} & only:
        local_and_project(store)
    if 'science' in only:
        science(store)
    if 'tech' in only:
        python_docs(store)
        debian_docs(store)
        llama_docs(store)
        sqlite_docs(store)
    if 'literature' in only:
        literature(store)
        xu_xiake(store)
    print(json.dumps(store.stats(), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
