#!/usr/bin/env python3
"""네이버 블로그 전체 글 -> 정적 사이트(색인용) 생성기.

사용: python build_site.py --site-url https://사용자명.github.io/papas-camping
- 글 목록: 네이버 PostTitleListAsync (전체 글), 요약/이미지: 각 글의 og 메타
- 이미 받은 요약은 cache.json에 저장되어 다시 받지 않는다 (새 글만 추가로 받음)
- 결과: site/ (index, page/N, posts/*.html, sitemap.xml, robots.txt, 구글 인증 파일)
"""
import argparse, html, json, re, shutil, sys, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

BLOG_ID = "papas_camping"
RSS = f"https://rss.blog.naver.com/{BLOG_ID}.xml"
GOOGLE_VERIFY = "google62284531b5565f66.html"  # Search Console 소유 인증 파일
NAVER_VERIFY = "naver5f8ace752ce99f1137709e71cec4863f.html"  # 네이버 서치어드바이저 인증 파일
BLOG_NAME = "파파스캠핑 장비리뷰"
BLOG_DESC = "써보고 말하는 캠핑장비 리뷰 | 장비리뷰 500+ | 유튜브 파파스캠핑"
PER_PAGE = 100
UA = {"User-Agent": "Mozilla/5.0"}
CACHE = Path("cache.json")

CSS = """body{font-family:system-ui,'Malgun Gothic',sans-serif;max-width:760px;margin:0 auto;padding:16px;line-height:1.7;color:#222}
a{color:#0a58ca}header{border-bottom:1px solid #ddd;margin-bottom:24px}li{margin:14px 0}
.date{color:#777;font-size:.9em}.btn{display:inline-block;padding:10px 16px;background:#03c75a;color:#fff;border-radius:6px;text-decoration:none}
img{max-width:100%;height:auto}nav a{margin-right:10px}"""


def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read().decode("utf8")


def fetch_list():
    """전체 글 목록 (중복 제거, 최신순). 공개 + 검색 허용 글만."""
    seen, out = set(), []
    for page in range(1, 200):
        raw = get(f"https://blog.naver.com/PostTitleListAsync.naver?blogId={BLOG_ID}&viewdate="
                  f"&currentPage={page}&categoryNo=0&parentCategoryNo=&countPerPage=30")
        raw = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', raw)  # 네이버 JSON의 잘못된 이스케이프 보정
        pl = json.loads(raw, strict=False).get("postList", [])
        new = [p for p in pl if p["logNo"] not in seen]
        if not new:
            break
        for p in new:
            seen.add(p["logNo"])
            if p.get("openType") == "2" and p.get("searchYn") == "true":
                out.append(p)
        time.sleep(0.2)
    return out


def parse_date(s):
    m = re.match(r"(\d+)\. (\d+)\. (\d+)\.", s)
    if m:
        return datetime(*map(int, m.groups()))
    now = datetime.now()
    m = re.match(r"(\d+)시간 전", s)
    if m:
        return now - timedelta(hours=int(m.group(1)))
    m = re.match(r"(\d+)분 전", s)
    return now - timedelta(minutes=int(m.group(1))) if m else now


def fetch_rss():
    res = {}
    try:
        root = ET.fromstring(get(RSS).encode("utf8"))
    except Exception:
        return res
    for it in root.iter("item"):
        m = re.search(r"/(\d+)$", it.findtext("guid", "").strip())
        if not m:
            continue
        desc = it.findtext("description", "")
        img = re.search(r'<img src="([^"]+)"', desc)
        res[m.group(1)] = dict(
            cat=it.findtext("category", "").strip(),
            text=html.unescape(re.sub(r"<[^>]+>", "", desc)).strip(),
            img=img.group(1) if img else "",
            dt=parsedate_to_datetime(it.findtext("pubDate")).replace(tzinfo=None).isoformat())
    return res


def fetch_meta(log_no):
    try:
        s = get(f"https://blog.naver.com/PostView.naver?blogId={BLOG_ID}&logNo={log_no}")
    except Exception:
        return None
    def og(k):
        m = re.search(r'property="og:%s" content="([^"]*)"' % k, s)
        return html.unescape(m.group(1)).strip() if m else ""
    return dict(text=og("description"), img=og("image"))


def load_posts():
    cache = json.loads(CACHE.read_text(encoding="utf8")) if CACHE.exists() else {}
    listing = fetch_list()
    rss = fetch_rss()
    todo = [p["logNo"] for p in listing if p["logNo"] not in rss and p["logNo"] not in cache]
    if todo:
        print(f"새로 요약을 받을 글: {len(todo)}개")
        with ThreadPoolExecutor(3) as ex:
            for no, meta in zip(todo, ex.map(fetch_meta, todo)):
                if meta:
                    cache[no] = meta
        CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf8")
    posts = []
    for p in listing:
        no = p["logNo"]
        meta = rss.get(no) or cache.get(no) or {}
        dt = datetime.fromisoformat(meta["dt"]) if meta.get("dt") else parse_date(p["addDate"])
        title = urllib.parse.unquote_plus(p["title"]).strip()
        posts.append(dict(id=no, url=f"https://blog.naver.com/{BLOG_ID}/{no}", title=title,
                          cat=meta.get("cat", ""), text=meta.get("text") or title,
                          img=meta.get("img", ""), dt=dt))
    return sorted(posts, key=lambda x: x["dt"], reverse=True)


def page(title, desc, canonical, body):
    e = html.escape
    home = canonical.split("/page/")[0].split("/posts/")[0].rstrip("/") + "/"
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)}</title><meta name="description" content="{e(desc[:150])}">
<link rel="canonical" href="{e(canonical)}"><meta name="robots" content="index,follow">
<style>{CSS}</style></head><body>
<header><h1><a href="{e(home)}" style="color:inherit;text-decoration:none">{e(BLOG_NAME)}</a></h1><p>{e(BLOG_DESC)}</p></header>
{body}</body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site-url", required=True)
    ap.add_argument("--out", default="docs")
    a = ap.parse_args()
    base = a.site_url.rstrip("/")
    out = Path(a.out)
    e = html.escape

    posts = load_posts()
    if len(posts) < 500:  # 안전장치: 목록 수집이 비정상이면 기존 사이트를 지우지 않고 중단
        sys.exit(f"중단: 수집된 글이 {len(posts)}개뿐입니다. 사이트를 갱신하지 않습니다.")

    if out.exists():  # .git은 보존하고 나머지만 지운다
        for child in out.iterdir():
            if child.name != ".git":
                shutil.rmtree(child) if child.is_dir() else child.unlink()
    (out / "posts").mkdir(parents=True, exist_ok=True)
    (out / "page").mkdir(exist_ok=True)
    (out / ".nojekyll").write_text("")
    (out / GOOGLE_VERIFY).write_text(f"google-site-verification: {GOOGLE_VERIFY}\n")
    (out / NAVER_VERIFY).write_text(f"naver-site-verification: {NAVER_VERIFY}")

    for p in posts:
        cat = f" · {e(p['cat'])}" if p["cat"] else ""
        body = f"""<article><p class="date">{p['dt']:%Y-%m-%d}{cat}</p><h2>{e(p['title'])}</h2>
{f'<img src="{e(p["img"])}" alt="{e(p["title"])}">' if p['img'] else ''}
<p>{e(p['text'])}</p>
<p><a class="btn" href="{e(p['url'])}">네이버 블로그에서 전체 글 보기</a></p></article>
<p><a href="{base}/">← 전체 글 목록</a></p>"""
        (out / "posts" / f"{p['id']}.html").write_text(
            page(f"{p['title']} | {BLOG_NAME}", p["text"], f"{base}/posts/{p['id']}.html", body),
            encoding="utf-8")

    chunks = [posts[i:i + PER_PAGE] for i in range(0, len(posts), PER_PAGE)]
    def url_of(n):
        return f"{base}/" if n == 1 else f"{base}/page/{n}.html"
    for n, chunk in enumerate(chunks, 1):
        items = "".join(
            f'<li><a href="{base}/posts/{p["id"]}.html">{e(p["title"])}</a><br>'
            f'<span class="date">{p["dt"]:%Y-%m-%d}</span></li>' for p in chunk)
        nav = "".join(f'<a href="{url_of(i)}">{"<b>%d</b>" % i if i == n else i}</a>'
                      for i in range(1, len(chunks) + 1))
        body = (f'<ul>{items}</ul><nav>{nav}</nav>'
                f'<p><a href="https://blog.naver.com/{BLOG_ID}">네이버 블로그 바로가기</a></p>')
        title = BLOG_NAME if n == 1 else f"{BLOG_NAME} - {n}페이지"
        target = out / "index.html" if n == 1 else out / "page" / f"{n}.html"
        target.write_text(page(title, BLOG_DESC, url_of(n), body), encoding="utf-8")

    urls = [(url_of(n), posts[0]["dt"]) for n in range(1, len(chunks) + 1)]
    urls += [(f"{base}/posts/{p['id']}.html", p["dt"]) for p in posts]
    sm = "".join(f"<url><loc>{e(u)}</loc><lastmod>{d:%Y-%m-%d}</lastmod></url>" for u, d in urls)
    (out / "sitemap.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{sm}</urlset>',
        encoding="utf-8")
    (out / "robots.txt").write_text(f"User-agent: *\nAllow: /\n\nSitemap: {base}/sitemap.xml\n", encoding="utf-8")
    print(f"완료: 글 {len(posts)}개, 목록 {len(chunks)}페이지 -> {out.resolve()}")


if __name__ == "__main__":
    main()
