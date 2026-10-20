"""營養知識管理指令（團隊電腦）：python -m knowledge_rag.admin <指令>

  status                                     知識狀態計數、待審新版本、active 索引、抓取統計、雲端待補食材數
  list [--status pending|approved|rejected] [--limit N]
  show <knowledge_id>                        所有版本（含出處與授權紀錄欄位）
  approve <knowledge_id> <version> --reviewer 名字 --note 核對說明 [--confirm-third-party-license]
                                             轉載文章（內容授權提供／X）必須加此旗標，確認授權涵蓋轉載內容
  reject  <knowledge_id> <version> --reviewer 名字 --note 退回原因
  index build --version <索引版本>            以 text-embedding-3-small 建索引並啟用（每批送雲端內部 API，經 T10 額度預留／結算）
  crawl --channel <頻道 ID> [--max-pages N] [--refresh]   （可重複 --channel；未指定時用營養飲食）
  crawl --ids <起>-<迄> [--refresh]
  crawl --queued [--limit N] [--max-pages N] [--max-articles N]   雲端待補食材優先

輸出一律為 JSON（stdout）；錯誤訊息到 stderr、結束碼非 0。

環境變數：
  KNOWLEDGE_DATABASE_URL       團隊電腦知識庫（必填）
  KNOWLEDGE_AUTHORIZATION_REF  健康醫療網書面授權證據的存放位置（crawl 必填；見 docs/knowledge-source-healthnews.md）
  KNOWLEDGE_CRAWLER_USER_AGENT 選填，覆寫預設 User-Agent
  KNOWLEDGE_FOOD_WORKBOOK      選填，FOOD 字典來源工作簿（預設 data/recipe_dataset_1017.xlsx）
  CLOUD_INTERNAL_URL           雲端內部 API（例如 https://cloud-internal.<網域>）：index build、crawl --queued、status 的佇列數
  CLOUD_INTERNAL_TOKEN         與雲端 INTERNAL_API_TOKEN 相同
  CLOUD_ACCESS_CLIENT_ID／CLOUD_ACCESS_CLIENT_SECRET  內部 hostname 的 Cloudflare Access 服務權杖
團隊電腦不需要雲端 DATABASE_URL 或 OPENAI_API_KEY（pre-review #6）：佇列與向量化都經雲端內部 API。
需要 PYTHONPATH 含 backend（只用 app.services.recipe_import 解析 FOOD 字典，不連雲端資料庫）。
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


def _default_cloud():
    from knowledge_rag.cloud import CloudClient
    url, token = os.environ.get("CLOUD_INTERNAL_URL"), os.environ.get("CLOUD_INTERNAL_TOKEN")
    if not url or not token:
        raise SystemExit("CLOUD_INTERNAL_URL and CLOUD_INTERNAL_TOKEN are required for this command")
    return CloudClient(url, token, access_client_id=os.environ.get("CLOUD_ACCESS_CLIENT_ID"),
                       access_client_secret=os.environ.get("CLOUD_ACCESS_CLIENT_SECRET"))


EMBEDDING_MODEL = "text-embedding-3-small"


@dataclass
class Deps:
    store: Callable = _default_store
    fetcher: Callable = _default_fetcher
    matcher: Callable = load_food_matcher
    cloud: Callable = _default_cloud
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
        if name == "approve":
            r.add_argument("--confirm-third-party-license", action="store_true",
                           help="轉載文章：確認書面授權涵蓋原始供稿者的內容（寫入審核紀錄）")
    idx = sub.add_parser("index")
    idx_sub = idx.add_subparsers(dest="index_cmd", required=True)
    build = idx_sub.add_parser("build")
    build.add_argument("--version", required=True)
    build.add_argument("--batch-size", type=int, default=50)
    build.add_argument("--embedding-model", default=EMBEDDING_MODEL)
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
        cloud = deps.cloud()
        food_ids = [r["food_id"] for r in cloud.claim(args.limit)]
        if not food_ids:
            return {"queued": [], "by_food": {}}
        report = crawler.crawl_for_foods(food_ids, channels=channels, max_pages=args.max_pages or 20,
                                         max_articles=args.max_articles)
        cloud.complete(report["by_food"])
        return {"queued": food_ids, **report}
    reports = {c: crawler.crawl_channel(c, max_pages=args.max_pages, refresh=args.refresh) for c in channels}
    return reports[channels[0]] if len(channels) == 1 else reports


def _index_build(args, deps: Deps) -> dict:
    return deps.store().build_index(args.version, args.embedding_model,
                                    deps.cloud().index_embedder(args.version, args.embedding_model),
                                    batch_size=args.batch_size)


def _status(deps: Deps) -> dict:
    summary = deps.store().status_summary()
    try:
        summary["crawl_queue_pending"] = deps.cloud().pending_count()
    except (SystemExit, Exception):  # noqa: BLE001 — 雲端內部 API 不可達時只略過這一項
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
            _print([{k: r.get(k) for k in ("knowledge_id", "source_version", "ingredient_id", "status", "title",
                                           "source_url", "published_at", "imported_at", "license_status")}
                    for r in deps.store().list_records(status=args.status, limit=args.limit)])
        elif args.cmd == "show":
            versions = deps.store().list_versions(args.knowledge_id)
            if not versions:
                raise ValueError("Knowledge not found")
            _print({"knowledge_id": args.knowledge_id, "versions": versions})
        elif args.cmd in ("approve", "reject"):
            _print(deps.store().review(args.knowledge_id, args.version, reviewer=args.reviewer,
                                       approved=args.cmd == "approve", note=args.note,
                                       confirm_third_party_license=getattr(args, "confirm_third_party_license", False)))
        elif args.cmd == "index":
            _print(_index_build(args, deps))
        elif args.cmd == "crawl":
            _print(_crawl(args, deps))
        return 0
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 — 管理指令：顯示錯誤類型與經遮蔽的訊息，不印堆疊
        from ops.env import safe_error
        print(safe_error(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
