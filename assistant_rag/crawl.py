"""飲食客服語料爬取（步驟 1）：每個來源一條連線、每次請求間隔 ≥2 秒、遵守 robots.txt、可中斷接續。

    python -m assistant_rag.crawl healthnews [--limit N] [--refresh-discovery]
    python -m assistant_rag.crawl helloyishi [--limit N] [--refresh-discovery]
    python -m assistant_rag.crawl stats
    python -m assistant_rag.crawl supplement <來源> --reason "理由" URL ...   # 加入列表以外的指定網址，再執行抓取指令

輸出（Data Root，不進 Git）：raw/<來源>/
- urls.json       候選文章清單（discover 結果與時間；預設沿用，--refresh-discovery 重抓）
- supplement.json 列表以外、指定補抓的網址（附理由），抓取時併入候選清單；收錄的紀錄 category 為「指定補抓」
- articles.jsonl  每篇一行的統一紀錄（邊抓邊寫）
- skipped.jsonl   略過的網址與原因：not_found、parse_failed、sponsored、fetch_failed、robots_disallowed
接續時略過 articles／skipped 中已處理的網址；fetch_failed 會重試，其他原因可用 --retry <原因> 重試（例如修好 parser 後）。
"""
import argparse
import json
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from assistant_rag.config import USER_AGENT, data_dir
from assistant_rag.sources import healthnews, helloyishi
from knowledge_rag.fetcher import FetchError, PoliteFetcher, RobotsDisallowed

SOURCES = {healthnews.SOURCE: healthnews, helloyishi.SOURCE: helloyishi}
RETRYABLE = {"fetch_failed"}
SUPPLEMENT_CATEGORY = "指定補抓"


def source_dir(root: Path, source: str) -> Path:
    path = root / "raw" / source
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue  # 中斷時可能留下半行；略過即可，接續時會重抓
    return rows


def _append(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        fh.flush()


def candidates(module, fetcher, folder: Path, *, refresh: bool, log=print) -> list[dict]:
    cache = folder / "urls.json"
    if cache.exists() and not refresh:
        found = json.loads(cache.read_text(encoding="utf-8"))["candidates"]
    else:
        found = module.discover(fetcher, log=log)
        cache.write_text(json.dumps({"source": module.SOURCE, "discovered_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"), "count": len(found), "candidates": found}, ensure_ascii=False, indent=1),
            encoding="utf-8")
        log(f"[{module.SOURCE}] 候選文章 {len(found)} 篇（已存 urls.json）")
    listed = {c["url"] for c in found}
    return found + [c for c in read_supplement(folder) if c["url"] not in listed]


def read_supplement(folder: Path) -> list[dict]:
    path = folder / "supplement.json"
    return json.loads(path.read_text(encoding="utf-8"))["candidates"] if path.exists() else []


def add_supplement(source: str, root: Path, urls: list[str], reason: str) -> int:
    """把列表以外的指定網址加入 supplement.json（同網址不重複），回傳新增數。只接受該來源網站的 https 網址。"""
    module = SOURCES[source]
    folder = source_dir(root, source)
    current = read_supplement(folder)
    known = {c["url"] for c in current}
    added = 0
    for url in urls:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname != module.HOST:
            raise ValueError(f"{url} 不是 {module.SOURCE_NAME}（https://{module.HOST}/）的網址")
        if url not in known:
            current.append({"url": url, "article_id": None, "title_hint": None, "category": SUPPLEMENT_CATEGORY,
                            "subcategory": None, "supplement_reason": reason})
            known.add(url)
            added += 1
    (folder / "supplement.json").write_text(json.dumps({"source": source, "candidates": current},
                                                       ensure_ascii=False, indent=1), encoding="utf-8")
    return added


def crawl(source: str, fetcher, root: Path, *, limit: int | None = None, refresh: bool = False,
          retry: frozenset = frozenset(), log=print) -> dict:
    module = SOURCES[source]
    folder = source_dir(root, source)
    articles, skipped = folder / "articles.jsonl", folder / "skipped.jsonl"
    done = {r["discovered_url"] for r in read_jsonl(articles) if r.get("discovered_url")}
    done |= {r["url"] for r in read_jsonl(skipped) if r.get("reason") not in RETRYABLE | retry}
    done -= {r["url"] for r in read_jsonl(skipped) if r.get("reason") in retry} - {
        r["discovered_url"] for r in read_jsonl(articles) if r.get("discovered_url")}
    todo = [c for c in candidates(module, fetcher, folder, refresh=refresh, log=log) if c["url"] not in done]
    if limit is not None:
        todo = todo[:limit]
    log(f"[{source}] 已處理 {len(done)} 篇，本次待抓 {len(todo)} 篇")
    counts = Counter()
    try:
        for i, cand in enumerate(todo, 1):
            url = cand["url"]
            try:
                html = fetcher.get(url)
            except RobotsDisallowed:
                reason, html = "robots_disallowed", None
            except FetchError as exc:
                reason, html = "fetch_failed", None
                log(f"[{source}] 抓取失敗 {url}：{type(exc).__name__}")
            else:
                reason = "not_found" if html is None else None
            record = module.parse(html, url, cand) if html is not None else None
            if record and "skip" in record:
                reason, record = record["skip"], None
            elif html is not None and record is None:
                reason = "parse_failed"
            if record:
                if cand.get("supplement_reason"):
                    record = {**record, "category": SUPPLEMENT_CATEGORY, "supplement_reason": cand["supplement_reason"]}
                _append(articles, {**record, "discovered_url": url})
                counts["saved"] += 1
            else:
                _append(skipped, {"url": url, "reason": reason,
                                  "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
                counts[reason] += 1
            if i % 50 == 0:
                log(f"[{source}] {i}/{len(todo)}：{dict(counts)}")
    except KeyboardInterrupt:
        log(f"[{source}] 已中斷；下次執行會從未處理的網址接續")
    log(f"[{source}] 完成：{dict(counts)}")
    return dict(counts)


def stats(root: Path) -> dict:
    """各來源篇數、日期範圍、平均字數、分類與略過原因（簡報第 4 頁的資料）。"""
    out = {}
    for source in SOURCES:
        folder = root / "raw" / source
        rows = {r["url"]: r for r in read_jsonl(folder / "articles.jsonl")}.values()  # 同網址以最後一筆為準
        dates = sorted(r["published_at"][:10] for r in rows if r.get("published_at"))
        chars = [r["char_count"] for r in rows]
        out[source] = {
            "articles": len(chars),
            "date_range": [dates[0], dates[-1]] if dates else None,
            "avg_chars": round(statistics.mean(chars)) if chars else None,
            "median_chars": round(statistics.median(chars)) if chars else None,
            "sections_avg": round(statistics.mean(len(r["sections"]) for r in rows), 1) if chars else None,
            "subcategories": dict(Counter(r.get("subcategory") or r.get("category") for r in rows)),
            "with_reviewer": sum(1 for r in rows if r.get("reviewers")),
            "skipped": dict(Counter(r["reason"] for r in read_jsonl(folder / "skipped.jsonl"))),
        }
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=[*SOURCES, "stats", "supplement"])
    parser.add_argument("args", nargs="*", help="supplement：<來源> URL ...")
    parser.add_argument("--reason", help="supplement：補抓理由（寫進紀錄）")
    parser.add_argument("--limit", type=int, help="本次最多抓幾篇（試抓用）")
    parser.add_argument("--retry", action="append", default=[], help="重試這個略過原因的網址（可重複）")
    parser.add_argument("--refresh-discovery", action="store_true", help="重新讀列表／sitemap")
    parser.add_argument("--data-dir", help="覆寫 ASSISTANT_DATA_DIR")
    args = parser.parse_intermixed_args(argv)  # 允許 --reason 出現在網址之前
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    root = data_dir(args.data_dir)
    if args.command == "stats":
        print(json.dumps(stats(root), ensure_ascii=False, indent=2))
        return 0
    if args.command == "supplement":
        if len(args.args) < 2 or args.args[0] not in SOURCES or not args.reason:
            parser.error("supplement 需要 <來源> URL ... 與 --reason")
        print(f"[{args.args[0]}] 新增 {add_supplement(args.args[0], root, args.args[1:], args.reason)} 個補抓網址")
        return 0
    module = SOURCES[args.command]
    fetcher = PoliteFetcher(user_agent=USER_AGENT, allowed_host=module.HOST)
    crawl(args.command, fetcher, root, limit=args.limit, refresh=args.refresh_discovery, retry=frozenset(args.retry))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
