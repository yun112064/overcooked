"""G07 健康醫療網爬蟲（團隊電腦）：分類頁／文章 ID 範圍／待補食材 → 知識庫 PENDING 紀錄。

- 先以 FoodMatcher.relevant 篩出文章的相關食材（標題食材或多次出現、排除通用詞、有上限；pre-review #7），
  再把段落切開，段落提到的相關食材各成一筆知識：
  knowledge_id = hn-<文章 ID>-<FOOD 編號>-<該食材在本文的第幾段>，source_version = sha256 前 16 碼。
- 一律以 pending 匯入；只有人工審核通過＋重建索引後才可被檢索與顯示（spec：只用審核通過資料）。
- 轉載文章（文末「內容授權提供／X」）標 license_status=third_party_unconfirmed：核准時必須另加
  --confirm-third-party-license，確認寫入審核紀錄（pre-review #8）。
- 冪等：已抓過的文章預設不再請求；refresh 時重抓，內容雜湊相同即略過。
  內容改變 → 匯入新版本（pending）；舊的已審核版本保留、不被覆寫，報告列在 superseded_approved，
  依 T11 規則新版本審核前該知識不再提供（不會顯示未審核的新內容，也不會讓舊內容冒充新原文）。
- 抓取禮節由 PoliteFetcher 負責（robots、≥2 秒、User-Agent、重試）；不使用站內搜尋或 Google。
"""
import hashlib
import unicodedata
from datetime import datetime, timezone

from knowledge_rag.healthnews import (NUTRITION_CHANNEL, SOURCE_NAME, FoodMatcher, article_url, channel_url,
                                      parse_article, parse_listing, split_passages)

CONDITIONS = "摘自健康醫療網報導，僅供一般飲食知識參考，不是個人醫療建議"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text).lower()


class HealthnewsCrawler:
    def __init__(self, store, fetcher, matcher: FoodMatcher, *, authorization_ref: str,
                 max_passage_chars: int = 400, now=lambda: datetime.now(timezone.utc)):
        if not isinstance(authorization_ref, str) or not authorization_ref.strip():
            raise ValueError("authorization_ref (where the written permission is stored) is required")
        self.store, self.fetcher, self.matcher = store, fetcher, matcher
        self.authorization_ref = authorization_ref.strip()
        self.max_passage_chars = max_passage_chars
        self.now = now

    # --- 單篇文章 ---

    def _records(self, article) -> list[dict]:
        fetched_at = self.now()
        rights = f"健康醫療網書面授權（授權紀錄：{self.authorization_ref}）；顯示須保留來源、標題、日期與原文連結"
        conditions = CONDITIONS + (f"；原始供稿：{article.content_provider}（審核時確認授權涵蓋轉載內容）"
                                   if article.content_provider else "")
        records, ordinal = [], {}
        relevant = set(self.matcher.relevant(article.title, article.paragraphs))
        for passage in split_passages(article.paragraphs, self.max_passage_chars):
            for food_id in sorted(self.matcher.match(passage) & relevant):
                ordinal[food_id] = ordinal.get(food_id, 0) + 1
                digest = _sha(passage)
                records.append({
                    "knowledge_id": f"hn-{article.article_id}-{food_id}-{ordinal[food_id]}",
                    "source_version": digest[:16], "ingredient_id": food_id, "excerpt": passage,
                    "source_url": article.url, "rights": rights, "conditions": conditions,
                    "source_name": SOURCE_NAME, "title": article.title, "published_at": article.published_at,
                    "fetched_at": fetched_at, "content_hash": digest, "authorization_ref": self.authorization_ref,
                    "article_id": article.article_id, "content_provider": article.content_provider,
                    # pre-review #8：轉載文章預設擱置，審核時需明確確認授權（--confirm-third-party-license）
                    "license_status": "third_party_unconfirmed" if article.content_provider else None})
        return records

    def ingest(self, article, report: dict) -> None:
        records = self._records(article)
        article_hash = _sha("\n".join([article.title, *article.paragraphs]))
        for rec in records:
            current = self.store.get_record(rec["knowledge_id"])
            if current is not None and current["content_hash"] == rec["content_hash"]:
                continue
            if current is not None and current["source_version"] == rec["source_version"]:
                continue  # 同版本已存在（不可覆寫）
            had_approved = any(v["status"] == "approved" for v in self.store.list_versions(rec["knowledge_id"]))
            self.store.import_record(rec)
            report["records_created"] += 1
            if had_approved:
                report["superseded_approved"].append(rec["knowledge_id"])
        self.store.save_article({
            "source_name": SOURCE_NAME, "article_id": article.article_id, "url": article.url, "title": article.title,
            "published_at": article.published_at, "modified_at": article.modified_at, "content_hash": article_hash,
            "status": "matched" if records else "no_match",
            "matched_food_ids": sorted({r["ingredient_id"] for r in records})})

    def fetch_article(self, article_id: str, report: dict, *, refresh: bool, known: set[str]) -> None:
        if not refresh and article_id in known:
            report["articles_skipped_known"] += 1
            return
        url = article_url(article_id)
        html = self.fetcher.get(url)
        article = parse_article(html, url) if html else None
        if article is None:
            report["articles_missing"] += 1
            self.store.save_article({"source_name": SOURCE_NAME, "article_id": str(article_id), "url": url,
                                     "status": "not_found"})
            return
        report["articles_fetched"] += 1
        before = report["records_created"]
        self.ingest(article, report)
        if report["records_created"] == before:
            report["articles_unchanged"] += 1
        known.add(article_id)

    # --- 抓取模式 ---

    @staticmethod
    def _report() -> dict:
        return {"pages": 0, "articles_fetched": 0, "articles_missing": 0, "articles_skipped_known": 0,
                "articles_unchanged": 0, "records_created": 0, "superseded_approved": []}

    def _listing_pages(self, channel_id: str, max_pages: int | None, report: dict):
        page, last = 1, 1
        while page <= last and (max_pages is None or page <= max_pages):
            html = self.fetcher.get(channel_url(channel_id, page))
            if html is None:
                break
            listing = parse_listing(html)
            report["pages"] += 1
            last = max(last, listing.last_page)
            yield listing
            page += 1

    def crawl_channel(self, channel_id: str = NUTRITION_CHANNEL, *, max_pages: int | None = None,
                      refresh: bool = False) -> dict:
        report = self._report()
        known = self.store.known_article_ids(SOURCE_NAME)
        for listing in self._listing_pages(channel_id, max_pages, report):
            for article_id, _title in listing.articles:
                self.fetch_article(article_id, report, refresh=refresh, known=known)
        return report

    def crawl_ids(self, start: int, end: int, *, refresh: bool = False) -> dict:
        if start > end:
            start, end = end, start
        report = self._report()
        known = self.store.known_article_ids(SOURCE_NAME)
        for article_id in range(end, start - 1, -1):  # 新文章優先
            self.fetch_article(str(article_id), report, refresh=refresh, known=known)
        return report

    def crawl_for_foods(self, food_ids, *, channels=(NUTRITION_CHANNEL,), max_pages: int = 20,
                        max_articles: int = 30) -> dict:
        """待補食材優先：掃分類頁，標題提到該食材名稱（含別名）的文章先抓；其餘未抓過的文章排在後面。"""
        food_ids = sorted(set(food_ids))
        names = [_norm(n) for n in self.matcher.names_for(food_ids)]
        report = self._report()
        known = self.store.known_article_ids(SOURCE_NAME)
        hits, others = [], []
        for channel_id in channels:
            for listing in self._listing_pages(channel_id, max_pages, report):
                for article_id, title in listing.articles:
                    if article_id in known or article_id in hits or article_id in others:
                        continue
                    (hits if any(n in _norm(title) for n in names) else others).append(article_id)
        for article_id in (hits + others)[:max_articles]:
            self.fetch_article(article_id, report, refresh=False, known=known)
        report["title_hits"] = len(hits)
        report["by_food"] = self.store.ingredient_coverage(food_ids)
        return report
