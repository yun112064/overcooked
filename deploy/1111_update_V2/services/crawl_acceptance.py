"""Small real R14 acceptance corpus, fetched by the existing robots-aware crawler.

Uses twelve explicit URLs already listed in the project's reviewed T11 source file.
This is not the historical 1,298-article assistant corpus. Raw text stays in /data.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from assistant_rag.config import USER_AGENT, data_dir
from assistant_rag.crawl import SOURCES, crawl, source_dir, stats
from knowledge_rag.fetcher import PoliteFetcher


def main():
    root = data_dir()
    if str(root) != "/data/assistant_rag":
        raise SystemExit("This acceptance fetcher is restricted to its isolated Docker data volume")
    source = Path(__file__).resolve().parents[3] / "knowledge_rag/candidates/web_paper_2026-10-30.json"
    records = json.loads(source.read_text(encoding="utf-8"))["records"]
    for name, module in SOURCES.items():
        urls = list(dict.fromkeys(r["source_url"] for r in records
                                 if urlsplit(r["source_url"]).hostname == module.HOST))[:6]
        folder = source_dir(root, name)
        cache = folder / "urls.json"
        if not cache.exists():
            cache.write_text(json.dumps({
                "source": name, "discovered_at": datetime.now(timezone.utc).isoformat(),
                "discovery_method": "1111_update explicit existing project source URLs; bounded acceptance corpus",
                "count": len(urls), "candidates": [
                    {"url": url, "article_id": None, "title_hint": None, "category": "指定補抓",
                     "subcategory": None, "supplement_reason": "1111_update integration acceptance"} for url in urls],
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        fetcher = PoliteFetcher(user_agent=USER_AGENT, allowed_host=module.HOST)
        crawl(name, fetcher, root, limit=6)
    print(json.dumps(stats(root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
