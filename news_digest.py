"""每日新聞摘要：抓中央社、公視、報導者的新聞，去重後彙整成一則 Joplin 筆記放進 INBOX。

之後由 note2read.py 的 CronJob 照常送進 Instapaper。

來源（見 SECTIONS）：
  中央社  RSS（社會、財經、國際、文化）
  公視    分類頁（社會、國際、生活）—— 公視沒有分類 RSS，只能解析網頁
  報導者  RSS（全部，量少）

排程：一天跑數次（例如每 3 小時）。平常只抓新聞存進暫存檔 NEWS_STATE_FILE；
到了發布時段（NEWS_PUBLISH_WEEKDAY 的 NEWS_PUBLISH_HOUR 那一小時），再把累積的新聞彙整成一則筆記並清空暫存。
（中央社每個分類 RSS 只留最新 20 則，一天只抓一次會漏掉白天大部分新聞）

去重：
  1. 同一網址只收一次
  2. 同一事件不同媒體都有報：標題正規化後相似度 >= NEWS_SIMILARITY 視為重複（先出現者優先）
  3. 跨次：暫存檔裡的、以及最近 NEWS_DEDUP_DAYS 天摘要筆記裡的網址 / 標題不再收

環境變數：
  JOPLIN_DATA_API_URL, JOPLIN_DATA_API_TOKEN, INBOX   （與 note2read.py 共用）
  NEWS_KEYWORDS         選用。設定後只收標題含任一關鍵字的新聞（換行或逗號分隔）
  NEWS_EXCLUDE          標題含任一字串就排除（預設排除彩券開獎）
  NEWS_TITLE_PREFIX     筆記標題前綴（預設「新聞摘要」）
  NEWS_LOOKBACK_HOURS   只收這段時間內發布的新聞（預設 30）
  NEWS_MAX_PER_SECTION  每個分類最多列幾則，取最新的（預設 50）
  NEWS_STATE_FILE       暫存檔路徑（預設 ./news_pending.json）
  NEWS_PUBLISH_HOUR     自動模式下，幾點那一小時發布（預設 5，台北時間）
  NEWS_PUBLISH_WEEKDAY  自動模式下，星期幾發布（0=一 … 6=日）；留空 = 每天發布（預設留空）
  NEWS_DEDUP_DAYS       回看幾天內的摘要筆記做去重（預設 10，週報需 > 7）
  NEWS_PENDING_DAYS     暫存檔中超過幾天的新聞丟棄，避免發布失敗時無限累積（預設 14）
  NEWS_NEW_USERS_FILE   選用。garmin 的 new-users.json 路徑；設定後在摘要最前面加「新使用者」統計（見 docs/specs/T42-new-users-section.md）
  NEWS_SIMILARITY       標題相似度門檻 0~1（預設 0.75）

用法：
  python news_digest.py            # 自動：發布時段 → 發布；其他時段 → 只累積
  python news_digest.py --collect  # 只抓新聞存進暫存
  python news_digest.py --publish  # 抓新聞 + 彙整暫存，建立筆記並清空暫存
  python news_digest.py --dry-run  # 預覽會發布的內容，不寫入 Joplin 也不改暫存
"""
import html
import json
import os
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from typing import Callable, Optional
from urllib.parse import urlsplit

import requests
from dotenv import load_dotenv

load_dotenv()

from note2read import get_notebook_id_by_name  # noqa: E402

TZ = timezone(timedelta(hours=8))  # Asia/Taipei，避免依賴系統 tzdata

API_URL = os.getenv("JOPLIN_DATA_API_URL")
API_TOKEN = os.getenv("JOPLIN_DATA_API_TOKEN")
INBOX = os.getenv("INBOX")
KEYWORDS = [k.strip() for k in re.split(r"[\n,]", os.getenv("NEWS_KEYWORDS", "")) if k.strip()]
EXCLUDE = [k.strip() for k in re.split(r"[\n,]", os.getenv("NEWS_EXCLUDE", "今彩539,大樂透,威力彩,雙贏彩,賓果")) if k.strip()]
TITLE_PREFIX = os.getenv("NEWS_TITLE_PREFIX", "新聞摘要")
LOOKBACK_HOURS = float(os.getenv("NEWS_LOOKBACK_HOURS", "30"))
MAX_PER_SECTION = int(os.getenv("NEWS_MAX_PER_SECTION", "50"))
STATE_FILE = os.getenv("NEWS_STATE_FILE", "news_pending.json")
PUBLISH_HOUR = int(os.getenv("NEWS_PUBLISH_HOUR", "5"))
_weekday = os.getenv("NEWS_PUBLISH_WEEKDAY", "").strip()
PUBLISH_WEEKDAY = int(_weekday) if _weekday else None
DEDUP_DAYS = int(os.getenv("NEWS_DEDUP_DAYS", "10"))
PENDING_DAYS = int(os.getenv("NEWS_PENDING_DAYS", "14"))
NEW_USERS_FILE = os.getenv("NEWS_NEW_USERS_FILE", "")
SIMILARITY = float(os.getenv("NEWS_SIMILARITY", "0.75"))

UA = {"User-Agent": "Mozilla/5.0 (news-digest)"}


@dataclass
class Article:
    title: str
    link: str
    source: str
    published: Optional[datetime] = None
    summary: str = ""

    @property
    def key(self) -> str:
        return url_key(self.link)

    @property
    def norm(self) -> str:
        return norm_title(self.title)


# ---------- 抓取 ----------

def _get(url: str) -> bytes:
    r = requests.get(url, headers=UA, timeout=20)
    r.raise_for_status()
    return r.content


def clean_text(s: str) -> str:
    s = html.unescape(re.sub(r"<[^>]+>", "", s or ""))
    return re.sub(r"\s+", " ", s).strip()


def rss(url: str, source: str, with_summary: bool = False) -> Callable[[], list[Article]]:
    def fetch() -> list[Article]:
        root = ET.fromstring(_get(url))
        out = []
        for item in root.iter("item"):
            title = clean_text(item.findtext("title") or "")
            link = (item.findtext("link") or "").strip()
            if not title or not link:
                continue
            published = None
            if item.findtext("pubDate"):
                try:
                    published = parsedate_to_datetime(item.findtext("pubDate")).astimezone(TZ)
                except (TypeError, ValueError):
                    pass
            summary = clean_text(item.findtext("description") or "") if with_summary else ""
            out.append(Article(title, link, source, published, summary))
        return out
    return fetch


def pts_category(cat_id: int) -> Callable[[], list[Article]]:
    """公視分類頁：同一篇文章有多個 <a>，取最短的非空文字當標題（較長的是內文摘要）。
    分類頁沒有發布時間，再逐篇抓 article:published_time。"""
    def fetch() -> list[Article]:
        page = _get(f"https://news.pts.org.tw/category/{cat_id}").decode("utf-8", "replace")
        texts: dict[str, list[str]] = {}
        for m in re.finditer(r'<a\b[^>]*href="(?:https://news\.pts\.org\.tw)?/article/(\d+)"[^>]*>(.*?)</a>', page, re.S):
            t = clean_text(m.group(2))
            texts.setdefault(m.group(1), [])
            # 排除「閱讀更多文章」與標籤連結（如「伊朗 23小時前」）
            if len(t) >= 6 and t != "閱讀更多文章" and not re.search(r"\d+\s*(分鐘|小時|天)前$", t):
                texts[m.group(1)].append(t)
        out = []
        for art_id, ts in texts.items():
            if ts:
                out.append(Article(min(ts, key=len), f"https://news.pts.org.tw/article/{art_id}", "公視"))
        with ThreadPoolExecutor(max_workers=8) as pool:
            for a, published in zip(out, pool.map(pts_published, [a.link for a in out])):
                a.published = published
        # 抓不到時間的寧可不收，避免舊聞混進來
        return [a for a in out if a.published]
    return fetch


def pts_published(url: str) -> Optional[datetime]:
    try:
        page = _get(url).decode("utf-8", "replace")
    except requests.RequestException:
        return None
    m = re.search(r'article:published_time"\s+content="([^"]+)"', page)
    if not m:
        return None
    try:
        return datetime.fromisoformat(m.group(1)).astimezone(TZ)
    except ValueError:
        return None


CNA = "https://feeds.feedburner.com/rsscna/{}"
# (分類, [抓取函式...])；同分類內依列出順序，跨來源重複時先出現者保留
SECTIONS: list[tuple[str, list[Callable[[], list[Article]]]]] = [
    ("社會", [rss(CNA.format("social"), "中央社"), pts_category(7)]),
    ("財經", [rss(CNA.format("finance"), "中央社")]),
    ("國際", [rss(CNA.format("intworld"), "中央社"), pts_category(4)]),
    ("文化・生活", [rss(CNA.format("culture"), "中央社"), pts_category(5)]),
    ("深度報導", [rss("https://www.twreporter.org/a/rss2.xml", "報導者", with_summary=True)]),
]


# ---------- 去重 ----------

def url_key(url: str) -> str:
    p = urlsplit(url)
    return f"{p.netloc}{p.path}".rstrip("/").lower()


def norm_title(title: str) -> str:
    t = unicodedata.normalize("NFKC", title).lower()
    return re.sub(r"[\W_]+", "", t)


def similar(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a in b or b in a:
        return min(len(a), len(b)) >= 8
    return SequenceMatcher(None, a, b).ratio() >= SIMILARITY


def previous_digests(since: datetime) -> tuple[set[str], list[str]]:
    """回傳最近摘要筆記裡出現過的 (網址 key 集合, 正規化標題清單)。"""
    keys: set[str] = set()
    titles: list[str] = []
    since_ms = int(since.timestamp() * 1000)
    page = 1
    while True:
        r = requests.get(f"{API_URL}/notes", params={
            "token": API_TOKEN, "fields": "id,title,created_time",
            "order_by": "created_time", "order_dir": "DESC", "limit": 100, "page": page,
        }, timeout=20)
        r.raise_for_status()
        data = r.json()
        for n in data.get("items", []):
            if n["created_time"] < since_ms:
                return keys, titles
            if not n["title"].startswith(TITLE_PREFIX):
                continue
            body = requests.get(f"{API_URL}/notes/{n['id']}", params={
                "token": API_TOKEN, "fields": "body"}, timeout=20).json().get("body") or ""
            for text, url in re.findall(r"\[((?:\\.|[^\]])+)\]\((https?://[^)\s]+)\)", body):
                keys.add(url_key(url))
                titles.append(norm_title(text))
        if not data.get("has_more"):
            return keys, titles
        page += 1


def collect(now: datetime, seen_keys: set[str], seen_titles: list[str]) -> list[tuple[str, Article]]:
    """抓所有來源，回傳去重後的新文章 [(分類, 文章)]。"""
    cutoff = now - timedelta(hours=LOOKBACK_HOURS)
    taken_keys = set(seen_keys)
    taken_titles = list(seen_titles)
    result: list[tuple[str, Article]] = []
    for section, fetchers in SECTIONS:
        for fetch in fetchers:
            try:
                articles = fetch()
            except (requests.RequestException, ET.ParseError) as e:
                print(f"⚠️ {section} 抓取失敗: {e}", file=sys.stderr)
                continue
            n = 0
            for a in articles:
                if a.published and a.published < cutoff:
                    continue
                if KEYWORDS and not any(k.lower() in a.title.lower() for k in KEYWORDS):
                    continue
                if any(x in a.title for x in EXCLUDE):
                    continue
                if a.key in taken_keys or any(similar(a.norm, t) for t in taken_titles):
                    continue
                result.append((section, a))
                taken_keys.add(a.key)
                taken_titles.append(a.norm)
                n += 1
            src = articles[0].source if articles else "?"
            print(f"{section}/{src}: 抓到 {len(articles)} 則，新增 {n} 則", file=sys.stderr)
    return result


# ---------- 暫存（兩次發布之間累積的新聞） ----------

def load_pending(now: datetime) -> list[tuple[str, Article]]:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return []
    out = []
    oldest = now - timedelta(days=PENDING_DAYS)
    for d in raw:
        published = datetime.fromisoformat(d["published"]) if d.get("published") else None
        if published and published < oldest:
            continue
        out.append((d["section"], Article(d["title"], d["link"], d["source"], published, d.get("summary", ""))))
    return out


def save_pending(items: list[tuple[str, Article]]) -> None:
    data = [{**asdict(a), "section": sec, "published": a.published.isoformat() if a.published else None}
            for sec, a in items]
    os.makedirs(os.path.dirname(os.path.abspath(STATE_FILE)), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_FILE)


# ---------- 新使用者統計（T42，讀 garmin 的 new-users.json） ----------

def _warn_new_users(msg: str) -> None:
    print(f"⚠️ 新使用者統計：{msg}，這一段不放", file=sys.stderr)


def _parse_time(value) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=TZ)


def load_new_users(path: str, now: datetime) -> Optional[dict]:
    """讀取並檢查 new-users.json（version 1）；任何問題都回 None 並印警告。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        _warn_new_users(f"找不到 {path}")
        return None
    except (OSError, ValueError) as e:
        _warn_new_users(f"讀取失敗 {path}: {e}")
        return None
    if not isinstance(data, dict) or not all(k in data for k in ("version", "generated", "devices", "links")):
        _warn_new_users("缺少必要欄位")
        return None
    if data["version"] != 1:
        _warn_new_users(f"不支援的 version {data['version']}")
        return None
    if not isinstance(data["devices"], list) or not isinstance(data["links"], list):
        _warn_new_users("devices / links 不是陣列")
        return None
    generated = _parse_time(data["generated"])
    if not generated or generated.astimezone(TZ).date() != now.astimezone(TZ).date():
        _warn_new_users(f"檔案不是今天產生的（generated={data['generated']}）")
        return None
    return data


def filter_new_users(data: dict, since: Optional[datetime]) -> tuple[list[dict], list[dict]]:
    """篩出 firstSeen 嚴格晚於 since 的裝置與連結（since 為 None 時全收），依 firstSeen 排序。"""
    skipped = 0

    def pick(entries, valid) -> list[dict]:
        nonlocal skipped
        out = []
        for e in entries:
            if not isinstance(e, dict) or not valid(e):
                skipped += 1
                continue
            t = _parse_time(e.get("firstSeen"))
            if t and (since is None or t > since):
                out.append((t, e))
        return [e for _, e in sorted(out, key=lambda x: x[0])]

    devices = pick(data["devices"], _valid_device)
    links = pick(data["links"], _valid_link)
    if skipped:
        print(f"⚠️ 新使用者統計：跳過 {skipped} 項（欄位型別不對）", file=sys.stderr)
    return devices, links


def _valid_device(d: dict) -> bool:
    county = d.get("county")
    return county is None or isinstance(county, str)


def _valid_link(l: dict) -> bool:
    opens = l.get("opens")
    countries = l.get("countries")
    return (isinstance(opens, int) and not isinstance(opens, bool) and opens >= 0
            and isinstance(countries, list) and all(isinstance(c, str) for c in countries))


def render_new_users(devices: list[dict], links: list[dict]) -> str:
    """只放數量與地區，不放任何名稱、id、hash、label。"""
    lines = []
    if devices:
        counts: dict[str, int] = {}
        for d in devices:
            county = d.get("county") or "未知"
            counts[county] = counts.get(county, 0) + 1
        places = "、".join(f"{c}（{n}）" if n > 1 else c for c, n in counts.items())
        lines.append(f"- 新裝置 {len(devices)} 台：{places}")
    if links:
        opens = sum(l["opens"] for l in links)
        countries = sorted({c for l in links for c in l["countries"]})
        line = f"- 新連結 {len(links)} 個：被開啟 {opens} 次"
        if countries:
            line += f"（{'、'.join(countries)}）"
        lines.append(line)
    if not lines:
        return ""
    return "\n".join(["## 新使用者", "", *lines, ""])


def last_digest_time() -> Optional[datetime]:
    """最近一則摘要筆記的建立時間；沒有則回 None。"""
    page = 1
    while True:
        r = requests.get(f"{API_URL}/notes", params={
            "token": API_TOKEN, "fields": "title,created_time",
            "order_by": "created_time", "order_dir": "DESC", "limit": 100, "page": page,
        }, timeout=20)
        r.raise_for_status()
        data = r.json()
        for n in data.get("items", []):
            if n["title"].startswith(TITLE_PREFIX):
                return datetime.fromtimestamp(n["created_time"] / 1000, TZ)
        if not data.get("has_more"):
            return None
        page += 1


# ---------- 輸出 ----------

def md_escape(text: str) -> str:
    return re.sub(r"([\[\]])", r"\\\1", text)


def render(now: datetime, items: list[tuple[str, Article]], new_users: str = "") -> str:
    groups: dict[str, list[Article]] = {}
    for section, _ in SECTIONS:
        arts = [a for sec, a in items if sec == section]
        arts.sort(key=lambda a: a.published or now, reverse=True)
        if arts:
            groups[section] = arts
    shown = sum(min(len(v), MAX_PER_SECTION) for v in groups.values())
    head = f"{now:%Y-%m-%d %H:%M} 產生，共 {shown} 則。來源：中央社、公視、報導者"
    if KEYWORDS:
        head += f"。關鍵字：{'、'.join(KEYWORDS)}"
    lines = [head, ""]
    if new_users:
        lines += new_users.splitlines() + [""]
    for section, articles in groups.items():
        extra = len(articles) - MAX_PER_SECTION
        lines += [f"## {section}（{min(len(articles), MAX_PER_SECTION)}）", ""]
        for a in articles[:MAX_PER_SECTION]:
            meta = a.source + (f" · {a.published:%m/%d %H:%M}" if a.published else "")
            lines.append(f"- [{md_escape(a.title)}]({a.link})  \n  {meta}")
            if a.summary:
                lines.append(f"  {a.summary[:120]}{'…' if len(a.summary) > 120 else ''}")
        if extra > 0:
            lines.append(f"\n（另有 {extra} 則較舊的未列出）")
        lines.append("")
    return "\n".join(lines)


def create_note(title: str, body: str, folder_id: str) -> str:
    # POST /notes 會忽略 body，需先建立再 PUT 內容
    r = requests.post(f"{API_URL}/notes", params={"token": API_TOKEN},
                      json={"title": title, "parent_id": folder_id}, timeout=20)
    r.raise_for_status()
    note_id = r.json()["id"]
    r = requests.put(f"{API_URL}/notes/{note_id}", params={"token": API_TOKEN},
                     json={"body": body}, timeout=20)
    r.raise_for_status()
    return note_id


def main() -> int:
    args = set(sys.argv[1:])
    now = datetime.now(TZ)
    dry_run = "--dry-run" in args
    if "--collect" in args:
        publish = False
    elif "--publish" in args or dry_run:
        publish = True
    else:  # 自動：到了發布時段就發布，其他時段只累積
        publish = now.hour == PUBLISH_HOUR and (PUBLISH_WEEKDAY is None or now.weekday() == PUBLISH_WEEKDAY)

    if not API_URL or not API_TOKEN or (publish and not dry_run and not INBOX):
        print("請設定 JOPLIN_DATA_API_URL、JOPLIN_DATA_API_TOKEN、INBOX", file=sys.stderr)
        return 1

    pending = load_pending(now)
    seen_keys, seen_titles = previous_digests(now - timedelta(days=DEDUP_DAYS))
    seen_keys |= {a.key for _, a in pending}
    seen_titles += [a.norm for _, a in pending]
    print(f"已收錄：最近 {DEDUP_DAYS} 天摘要 + 暫存 {len(pending)} 則", file=sys.stderr)

    items = pending + collect(now, seen_keys, seen_titles)

    if not publish:
        if not dry_run:
            save_pending(items)
        print(f"累積中：暫存共 {len(items)} 則（{STATE_FILE}）", file=sys.stderr)
        return 0

    new_users = ""
    if NEW_USERS_FILE:
        data = load_new_users(NEW_USERS_FILE, now)
        if data:
            new_users = render_new_users(*filter_new_users(data, last_digest_time()))

    if not items and not new_users:
        print("沒有新的新聞，也沒有新使用者，不建立筆記", file=sys.stderr)
        return 0

    dates = [a.published for _, a in items if a.published]
    start = min(dates) if dates else now
    # 週報：標題帶日期區間；日報：只有當天日期。前綴不變，跨次去重靠它找舊摘要
    span = f"{start:%Y-%m-%d} ~ {now:%m-%d}" if (now - start).days >= 2 else f"{now:%Y-%m-%d}"
    title = f"{TITLE_PREFIX} {span}"
    body = render(now, items, new_users)
    if dry_run:
        print(f"# {title}\n\n{body}")
        return 0

    folder_id = get_notebook_id_by_name(API_URL, API_TOKEN, INBOX)
    if not folder_id:
        print(f"找不到筆記本 {INBOX}", file=sys.stderr)
        return 1
    note_id = create_note(title, body, folder_id)
    save_pending([])  # 筆記建立成功才清空暫存
    print(f"✅ 已建立筆記 {title} ({note_id})，共 {len(items)} 則", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
