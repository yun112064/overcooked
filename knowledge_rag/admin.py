"""營養知識管理指令（團隊電腦）：python -m knowledge_rag.admin <指令>

  status                                     知識狀態計數、待審新版本、active 索引、抓取統計、雲端待補食材數
  list [--status pending|approved|rejected] [--limit N]
  show <knowledge_id>                        所有版本（含出處與授權紀錄欄位）
  approve <knowledge_id> <version> --reviewer 名字 --note 核對說明
  reject  <knowledge_id> <version> --reviewer 名字 --note 退回原因
  index build --version <索引版本>            以 OpenAI text-embedding-3-small 建索引並啟用（每批經 T10 額度預留／結算）
  crawl --channel <頻道 ID> [--max-pages N] [--refresh]   （可重複 --channel；未指定時用營養飲食）
  crawl --ids <起>-<迄> [--refresh]
  crawl --queued [--limit N] [--max-pages N] [--max-articles N]   雲端待補食材優先

輸出一律為 JSON（stdout）；錯誤訊息到 stderr、結束碼非 0。

環境變數：
  KNOWLEDGE_DATABASE_URL       團隊電腦知識庫（必填）
  KNOWLEDGE_AUTHORIZATION_REF  健康醫療網書面授權證據的存放位置（crawl 必填；見 docs/knowledge-source-healthnews.md）
  KNOWLEDGE_CRAWLER_USER_AGENT 選填，覆寫預設 User-Agent
  KNOWLEDGE_FOOD_WORKBOOK      選填，FOOD 字典來源工作簿（預設 data/recipe_dataset_1017.xlsx）
  DATABASE_URL                 雲端業務庫：index build（額度控制）與 crawl --queued / status 的佇列需要
  OPENAI_API_KEY               index build 需要
需要 PYTHONPATH 含 backend（使用 app.services 的額度控制、OpenAIEmbedder、FOOD 字典解析）。
"""
import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]


def _default_store():
    from knowledge_rag.store import KnowledgeStore
    url = os.environ.get("KNOWLEDGE_DATABASE_URL")
    if not url:
        raise SystemExit("KNOWLEDGE_DATABASE_URL is required")
    return KnowledgeStore(url)


def _default_fetcher():
    from knowledge_rag.fetcher import DEFAULT_USER_AGENT, PoliteFetcher
    return PoliteFetcher(user_agent=os.environ.get("KNOWLEDGE_CRAWLER_USER_AGENT") or DEFAULT_USER_AGENT)


def load_food_matcher(workbook: str | Path | None = None, synonyms: str | Path | None = None):
    """FOOD 字典＝食譜工作簿的標準食材＋無歧義別名（與雲端 ingredient_aliases 相同規則）＋已確認同義組。"""
    from app.services.recipe_import import parse_recipe_workbook
    from knowledge_rag.healthnews import FoodMatcher
    bundle = parse_recipe_workbook(workbook or os.environ.get("KNOWLEDGE_FOOD_WORKBOOK")
                                   or ROOT / "data" / "recipe_dataset_1017.xlsx")
    rows = [(r["food_id"], r["canonical_name"], r["aliases"], r["ambiguous_aliases"])
            for r in bundle.ingredient_aliases]
    path = Path(synonyms or ROOT / "config" / "ingredient_synonyms.json")
    groups = []
    if path.is_file():
        table = json.loads(path.read_text(encoding="utf-8"))
        groups = [{f["food_id"] for f in g["foods"]} for g in table.get("groups", []) if g.get("status") == "confirmed"]
    return FoodMatcher(rows, synonym_groups=groups)


def _default_cloud_factory():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL (cloud database) is required for this command")
    return sessionmaker(bind=create_engine(url, pool_pre_ping=True), autoflush=False, expire_on_commit=False)


def _default_embedder():
    from app.services.llm_client import default_embedder
    embedder = default_embedder()
    if embedder is None:
        raise SystemExit("OPENAI_API_KEY is required for index build")
    return embedder


@dataclass
class Deps:
    store: Callable = _default_store
    fetcher: Callable = _default_fetcher
    matcher: Callable = load_food_matcher
    cloud_factory: Callable = _default_cloud_factory
    embedder: Callable = _default_embedder
    authorization_ref: str | None = None


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(type(value).__name__)


def _print(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=_json_default))


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m knowledge_rag.admin", description="營養知識管理（審核、索引、抓取）")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    ls = sub.add_parser("list")
    ls.add_argument("--status", choices=("pending", "approved", "rejected"))
    ls.add_argument("--limit", type=int, default=200)
    show = sub.add_parser("show")
    show.add_argument("knowledge_id")
    for name in ("approve", "reject"):
        r = sub.add_parser(name)
        r.add_argument("knowledge_id")
        r.add_argument("version")
        r.add_argument("--reviewer", required=True)
        r.add_argument("--note", required=True)
    idx = sub.add_parser("index")
    idx_sub = idx.add_subparsers(dest="index_cmd", required=True)
    build = idx_sub.add_parser("build")
    build.add_argument("--version", required=True)
    build.add_argument("--batch-size", type=int, default=50)
    crawl = sub.add_parser("crawl")
    mode = crawl.add_mutually_exclusive_group()
    mode.add_argument("--ids", help="文章 ID 範圍，例如 69000-70078")
    mode.add_argument("--queued", action="store_true", help="處理雲端待補食材")
    crawl.add_argument("--channel", action="append", help="分類頁頻道 ID（可重複）")
    crawl.add_argument("--max-pages", type=int)
    crawl.add_argument("--max-articles", type=int, default=30)
    crawl.add_argument("--limit", type=int, default=20, help="--queued 一次處理的食材數")
    crawl.add_argument("--refresh", action="store_true", help="重抓已抓過的文章（內容未變即略過）")
    crawl.add_argument("--authorization-ref", help="書面授權證據的存放位置（或設 KNOWLEDGE_AUTHORIZATION_REF）")
    return p


def _crawl(args, deps: Deps) -> dict:
    from knowledge_rag.crawler import HealthnewsCrawler
    from knowledge_rag.healthnews import NUTRITION_CHANNEL
    ref = args.authorization_ref or deps.authorization_ref or os.environ.get("KNOWLEDGE_AUTHORIZATION_REF")
    if not ref or not ref.strip():
        raise ValueError("authorization reference required: set --authorization-ref or KNOWLEDGE_AUTHORIZATION_REF "
                         "to where the written permission is stored")
    crawler = HealthnewsCrawler(deps.store(), deps.fetcher(), deps.matcher(), authorization_ref=ref)
    channels = args.channel or [NUTRITION_CHANNEL]
    if args.ids:
        start, _, end = args.ids.partition("-")
        return crawler.crawl_ids(int(start), int(end or start), refresh=args.refresh)
    if args.queued:
        from app.services import knowledge_queue
        factory = deps.cloud_factory()
        requests = knowledge_queue.pending(factory, limit=args.limit)
        food_ids = [r["food_id"] for r in requests]
        if not food_ids:
            return {"queued": [], "by_food": {}}
        report = crawler.crawl_for_foods(food_ids, channels=channels, max_pages=args.max_pages or 20,
                                         max_articles=args.max_articles)
        knowledge_queue.mark_crawled(factory, report["by_food"])
        return {"queued": food_ids, **report}
    reports = {c: crawler.crawl_channel(c, max_pages=args.max_pages, refresh=args.refresh) for c in channels}
    return reports[channels[0]] if len(channels) == 1 else reports


def _index_build(args, deps: Deps) -> dict:
    from app.services.knowledge import index_embedder
    embedder = deps.embedder()
    return deps.store().build_index(args.version, embedder.model,
                                    index_embedder(deps.cloud_factory(), embedder, args.version),
                                    batch_size=args.batch_size)


def _status(deps: Deps) -> dict:
    summary = deps.store().status_summary()
    try:
        from app.services import knowledge_queue
        summary["crawl_queue_pending"] = len(knowledge_queue.pending(deps.cloud_factory(), limit=10000))
    except (SystemExit, ImportError, Exception):  # noqa: BLE001 — 雲端庫不可達時只略過這一項
        summary["crawl_queue_pending"] = None
    return summary


def main(argv: list[str] | None = None, *, deps: Deps | None = None) -> int:
    deps = deps or Deps()
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 2)
    try:
        if args.cmd == "status":
            _print(_status(deps))
        elif args.cmd == "list":
            _print([{k: r[k] for k in ("knowledge_id", "source_version", "ingredient_id", "status", "title",
                                       "source_url", "published_at", "imported_at")}
                    for r in deps.store().list_records(status=args.status, limit=args.limit)])
        elif args.cmd == "show":
            versions = deps.store().list_versions(args.knowledge_id)
            if not versions:
                raise ValueError("Knowledge not found")
            _print({"knowledge_id": args.knowledge_id, "versions": versions})
        elif args.cmd in ("approve", "reject"):
            _print(deps.store().review(args.knowledge_id, args.version, reviewer=args.reviewer,
                                       approved=args.cmd == "approve", note=args.note))
        elif args.cmd == "index":
            _print(_index_build(args, deps))
        elif args.cmd == "crawl":
            _print(_crawl(args, deps))
        return 0
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 — 管理指令：顯示錯誤類型與訊息，不印堆疊
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
