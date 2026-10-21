"""G07 知識管理指令（python -m knowledge_rag.admin）：待審清單、檢視、核准／退回、建索引、抓取、狀態。

依賴（知識庫、抓取器、食材比對）以注入取代；佇列與建索引向量化經雲端內部 API（pre-review #6），
測試以 FastAPI TestClient 當傳輸層、伺服器端的向量化以假物件取代：不連網、不呼叫付費 API，
團隊電腦端不需要雲端 DATABASE_URL。
"""
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import select, text

from db_support import FOODS, migrate_fresh, requires_db, session_factory, truncate_all
from test_kn_healthnews_crawler import (AUTH_REF, BOKCHOY, CABBAGE, CARROT, CHANNEL, EGG, SITE, TOMATO, FakeFetcher,
                                        site_pages, synthetic_article)

INTERNAL_TOKEN = "internal-token-for-tests-0123456789"


class FakeClassifier:
    """取代 gpt-5.4-mini 營養知識分類（Structured Outputs）：依片段內容決定回答，記錄每次呼叫。

    verdicts：knowledge_id → dict（覆寫該筆回答）；預設規則：片段含「膳食纖維」「鈣」「維生素」「蛋白質」→ 營養知識，
    nutrition_fact 為合成句、evidence_quote 取片段中含該字的句子；否則判為非營養。
    """

    TERMS = ("膳食纖維", "鈣", "維生素", "蛋白質")

    def __init__(self, verdicts=None, fail_with=None):
        self.verdicts = dict(verdicts or {})
        self.fail_with = fail_with
        self.calls = []

    def _verdict(self, rec):
        if rec["knowledge_id"] in self.verdicts:
            return {"knowledge_id": rec["knowledge_id"], "source_version": rec["source_version"],
                    "is_nutrition_knowledge": False, "nutrition_fact": "", "evidence_quote": "", "reason": "",
                    **self.verdicts[rec["knowledge_id"]]}
        term = next((t for t in self.TERMS if t in rec["excerpt"]), None)
        if term is None:
            return {"knowledge_id": rec["knowledge_id"], "source_version": rec["source_version"],
                    "is_nutrition_knowledge": False, "nutrition_fact": "", "evidence_quote": "",
                    "reason": "只描述料理做法與口感，沒有營養成分或健康效益"}
        sentence = next(s for s in rec["excerpt"].replace("。", "。\n").splitlines() if term in s)
        return {"knowledge_id": rec["knowledge_id"], "source_version": rec["source_version"],
                "is_nutrition_knowledge": True, "nutrition_fact": f"{rec['ingredient_name']}含{term}。",
                "evidence_quote": sentence.strip(), "reason": "陳述營養成分"}

    def respond(self, **kwargs):
        from app.services.llm_client import StructuredResponse
        self.calls.append(kwargs)
        if self.fail_with is not None:
            if isinstance(self.fail_with, Exception):
                raise self.fail_with
            return self.fail_with, {"input_tokens": 500, "output_tokens": 0}
        records = json.loads(kwargs["input_text"])["records"]
        out = {"items": [self._verdict(r) for r in records]}
        return StructuredResponse("completed", output_text=json.dumps(out, ensure_ascii=False)), \
            {"input_tokens": 800, "output_tokens": 200}


def internal_cloud(factory, embedder, classifier=None):
    """CloudClient → 真實的雲端內部 API（TestClient），伺服器端以測試資料庫與假向量化器／假分類器執行。"""
    from fastapi.testclient import TestClient
    from app.api import internal_knowledge
    from app.main import app
    from knowledge_rag.cloud import CloudClient
    app.dependency_overrides[internal_knowledge.get_session_factory] = lambda: factory
    app.dependency_overrides[internal_knowledge.get_index_embedder] = lambda: embedder
    app.dependency_overrides[internal_knowledge.get_classifier_responder] = lambda: classifier
    http = TestClient(app, base_url="https://cloud-internal.example.invalid")

    def transport(method, url, headers, body, timeout):
        res = http.request(method, url, headers=headers, content=body)
        return res.status_code, res.content
    return CloudClient("https://cloud-internal.example.invalid", INTERNAL_TOKEN, access_client_id="cid",
                       access_client_secret="csecret", transport=transport)



@requires_db
class KnowledgeAdminTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migrate_fresh()

    def setUp(self):
        from knowledge_rag.admin import Deps
        from knowledge_rag.healthnews import FoodMatcher
        from knowledge_rag.store import KnowledgeStore
        from knowledge_support import FakeEmbedder, fresh_knowledge_db
        truncate_all()
        self.factory = session_factory()
        self.store = KnowledgeStore(fresh_knowledge_db())
        self.fetcher = FakeFetcher(site_pages())
        self.embedder = FakeEmbedder()
        matcher = FoodMatcher([(i, name, aliases, []) for i, (name, aliases) in FOODS.items()])
        token = patch("app.core.config.settings.internal_api_token", INTERNAL_TOKEN)
        token.start()
        self.addCleanup(token.stop)
        from app.main import app
        self.addCleanup(app.dependency_overrides.clear)
        self.classifier = FakeClassifier()
        cloud = internal_cloud(self.factory, self.embedder, self.classifier)
        self.deps = Deps(store=lambda: self.store, fetcher=lambda: self.fetcher, matcher=lambda: matcher,
                         cloud=lambda: cloud, authorization_ref=AUTH_REF)

    def run_cli(self, *argv):
        from knowledge_rag.admin import main
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(argv), deps=self.deps)
        self.last_err = err.getvalue()
        return code, (json.loads(out.getvalue()) if out.getvalue().strip() else None)

    def crawl_one(self):
        code, report = self.run_cli("crawl", "--ids", "80003-80003")
        self.assertEqual(code, 0, self.last_err)
        return report

    def test_crawl_then_list_pending_and_show_with_attribution(self):
        report = self.crawl_one()
        self.assertEqual(report["records_created"], 3)
        code, pending = self.run_cli("list", "--status", "pending")
        self.assertEqual(code, 0)
        kid = f"hn-80003-{BOKCHOY}-1"
        self.assertIn(kid, [r["knowledge_id"] for r in pending])
        code, shown = self.run_cli("show", kid)
        [version] = shown["versions"]
        self.assertEqual((version["status"], version["source_name"], version["title"], version["source_url"],
                          version["authorization_ref"]),
                         ("pending", "健康醫療網", "合成：青江菜補鈣小知識", f"{SITE}/article/80003", AUTH_REF))

    def test_approve_and_reject_need_reviewer_and_note(self):
        self.crawl_one()
        kid = f"hn-80003-{BOKCHOY}-1"
        version = self.store.get_record(kid)["source_version"]
        code, _ = self.run_cli("approve", kid, version, "--reviewer", "審核者")
        self.assertNotEqual(code, 0)
        # 轉載文章（內容授權提供／合成月刊）：沒有明確確認轉載授權不可核准
        code, _ = self.run_cli("approve", kid, version, "--reviewer", "審核者", "--note", "已核對原文與出處")
        self.assertNotEqual(code, 0)
        self.assertIn("--confirm-third-party-license", self.last_err)
        code, res = self.run_cli("approve", kid, version, "--reviewer", "審核者", "--note", "已核對原文與出處",
                                 "--confirm-third-party-license")
        self.assertEqual((code, res["status"]), (0, "approved"))
        code, shown = self.run_cli("show", kid)
        self.assertEqual(shown["versions"][0]["license_status"], "third_party_confirmed")
        code, _ = self.run_cli("reject", kid, version, "--reviewer", "審核者", "--note", "改判")
        self.assertNotEqual(code, 0)  # 已審核不可再改
        other = f"hn-80003-FOOD001566-1"
        code, res = self.run_cli("reject", other, self.store.get_record(other)["source_version"],
                                 "--reviewer", "審核者", "--note", "只是順帶提到")
        self.assertEqual((code, res["status"]), (0, "rejected"))

    def bulk(self, *extra, app_env="development"):
        with patch.dict("os.environ", {"APP_ENV": app_env} if app_env is not None else {}, clear=False):
            if app_env is None:
                import os
                os.environ.pop("APP_ENV", None)
            return self.run_cli("approve-all", *extra)

    # --- 決策 #29：營養分類與萃取 ---

    def add_classification_articles(self):
        self.fetcher.pages.update({
            f"{SITE}/article/79970": synthetic_article("79970", "合成：晶瑩五彩春捲", [
                "合成：高麗菜出現在晶瑩五彩春捲的蔬菜餡料中，可與紅蘿蔔等一起簡單處理，保留清甜與脆度，是低熱量的點心。"]),
            f"{SITE}/article/79960": synthetic_article("79960", "合成：高麗菜的維生素", [
                "合成：高麗菜富含維生素C。"]),
        })
        self.classifier.verdicts[f"hn-79960-{CABBAGE}-1"] = {
            "is_nutrition_knowledge": True, "nutrition_fact": "高麗菜含維生素，可以預防癌症。",
            "evidence_quote": "高麗菜可以預防癌症。", "reason": "陳述健康效益"}  # 原文沒有這句
        for ids in ("79990-79990", "79960-79970"):
            code, _ = self.run_cli("crawl", "--ids", ids)
            self.assertEqual(code, 0, self.last_err)

    def test_classify_extracts_grounded_nutrition_facts_and_rejects_the_rest(self):
        self.add_classification_articles()
        code, report = self.run_cli("classify")
        self.assertEqual(code, 0, self.last_err)
        self.assertEqual((report["classified"], report["nutrition"], report["not_nutrition"], report["invalid"]),
                         (4, 1, 2, 1))
        self.assertEqual(len(self.classifier.calls), 1)  # 一批
        cabbage = self.store.get_record(f"hn-79990-{CABBAGE}-1")
        self.assertEqual((cabbage["status"], cabbage["classification"], cabbage["nutrition_fact"],
                          cabbage["evidence_quote"], cabbage["classifier_model"]),
                         ("pending", "nutrition", "高麗菜含膳食纖維。", "合成：高麗菜含膳食纖維。",
                          "gpt-5.4-mini-2026-03-17"))
        spring = self.store.get_record(f"hn-79970-{CABBAGE}-1")  # 使用者回報的春捲段落
        self.assertEqual((spring["status"], spring["classification"], spring["reviewer"]),
                         ("rejected", "not_nutrition", "classifier"))
        self.assertIn("料理做法", spring["review_note"])
        self.assertEqual(spring["excerpt"], self.store.list_versions(f"hn-79970-{CABBAGE}-1")[0]["excerpt"])  # 保留紀錄
        invalid = self.store.get_record(f"hn-79960-{CABBAGE}-1")  # 支持句不在原文：程式驗證退回
        self.assertEqual((invalid["status"], invalid["classification"], invalid["reviewer"]),
                         ("rejected", "invalid", "classifier"))
        self.assertIn("EVIDENCE_NOT_IN_EXCERPT", invalid["classification_reason"])
        self.assertIsNone(invalid["nutrition_fact"])
        # 已分類的不再送出
        code, again = self.run_cli("classify")
        self.assertEqual((code, again["classified"], len(self.classifier.calls)), (0, 0, 1))
        # --reclassify 重跑全部現行的待審／已核准紀錄（已退回的不再花錢）
        code, again = self.run_cli("classify", "--reclassify")
        self.assertEqual((code, again["classified"], len(self.classifier.calls)), (0, 1, 2))

    def test_classify_dry_run_spends_nothing_and_limit_caps_the_run(self):
        self.add_classification_articles()
        code, report = self.run_cli("classify", "--dry-run")
        self.assertEqual(code, 0, self.last_err)
        self.assertEqual((report["dry_run"], report["to_classify"], report["batches"]), (True, 4, 1))
        self.assertEqual(self.classifier.calls, [])
        self.assertIsNone(self.store.get_record(f"hn-79990-{CABBAGE}-1")["classification"])
        code, report = self.run_cli("classify", "--limit", "1")
        self.assertEqual((code, report["classified"]), (0, 1))

    def test_dev_bulk_approves_only_records_classified_as_nutrition(self):
        # 決策 #24＋#29：開發環境整批核准只核准分類為營養知識者；轉載文章（third_party_unconfirmed）仍排除
        code, _ = self.run_cli("crawl", "--ids", "80001-80003")
        self.assertEqual(code, 0, self.last_err)
        self.add_classification_articles()
        code, res = self.bulk("--dev-bulk", "--reviewer", "dev-bulk", "--note", "開發環境整批核准")
        self.assertEqual((code, res["approved"]), (0, []))  # 尚未分類：一律不核准
        self.assertIn(f"hn-79990-{CABBAGE}-1", res["skipped_unclassified"])
        self.assertEqual(self.run_cli("classify")[0], 0, self.last_err)
        code, res = self.bulk("--dev-bulk", "--reviewer", "dev-bulk", "--note", "開發環境整批核准")
        self.assertEqual(code, 0, self.last_err)
        self.assertEqual(res["approved"], [f"hn-79990-{CABBAGE}-1"])
        self.assertIn(f"hn-80003-{BOKCHOY}-1", res["skipped_third_party"])
        cabbage = self.store.get_record(f"hn-79990-{CABBAGE}-1")
        self.assertEqual((cabbage["status"], cabbage["reviewer"]), ("approved", "dev-bulk"))
        self.assertIn("開發環境整批核准", cabbage["review_note"])
        self.assertEqual(self.store.get_record(f"hn-80003-{BOKCHOY}-1")["status"], "pending")

    def test_apply_rejections_revokes_approved_non_nutrition_records(self):
        # 舊的開發整批核准把非營養段落也核准了：分類後以 --apply-rejections 退回（reviewer=classifier＋原因）
        self.add_classification_articles()
        kid = f"hn-79970-{CABBAGE}-1"
        self.store.review(kid, self.store.get_record(kid)["source_version"], reviewer="dev-bulk", approved=True,
                          note="[開發環境整批核准] 舊批次")
        code, report = self.run_cli("classify")
        self.assertEqual(code, 0, self.last_err)
        self.assertEqual(report["approved_non_nutrition"], [kid])
        self.assertEqual(self.store.get_record(kid)["status"], "approved")  # 沒加旗標不改核准狀態
        code, report = self.run_cli("classify", "--apply-rejections", "--dry-run")
        self.assertEqual((code, report["would_revoke"]), (0, [kid]))
        self.assertEqual(self.store.get_record(kid)["status"], "approved")
        code, report = self.run_cli("classify", "--apply-rejections")
        self.assertEqual((code, report["revoked"]), (0, [kid]))
        revoked = self.store.get_record(kid)
        self.assertEqual((revoked["status"], revoked["reviewer"]), ("rejected", "classifier"))
        self.assertIn("料理做法", revoked["review_note"])
        self.assertIn("dev-bulk", revoked["review_note"])  # 保留原核准者

    def test_dev_bulk_approve_refused_outside_development(self):
        self.crawl_one()
        self.run_cli("crawl", "--ids", "80001-80001")
        for env, extra in (("production", ("--dev-bulk",)), (None, ("--dev-bulk",)), ("development", ())):
            code, _ = self.bulk(*extra, "--reviewer", "dev-bulk", "--note", "x", app_env=env)
            self.assertNotEqual(code, 0, (env, extra))
        self.assertEqual(self.run_cli("list", "--status", "approved")[1], [])

    def test_index_build_reserves_spend_and_activates_approved_nutrition_only(self):
        self.crawl_one()
        code, _ = self.run_cli("classify", "--run-id", "run-a")
        self.assertEqual(code, 0, self.last_err)
        for food in (BOKCHOY, EGG, CARROT):
            kid = f"hn-80003-{food}-1"
            self.store.review(kid, self.store.get_record(kid)["source_version"], reviewer="審核者",
                              approved=True, note="已核對", confirm_third_party_license=True)
        # 已核准但重新分類為非營養的（尚未 --apply-rejections）不進索引
        self.classifier.verdicts[f"hn-80003-{CARROT}-1"] = {"reason": "只是順帶提到"}
        code, _ = self.run_cli("classify", "--reclassify", "--run-id", "run-b")
        self.assertEqual(code, 0, self.last_err)
        self.assertEqual(self.store.get_record(f"hn-80003-{CARROT}-1")["status"], "approved")
        code, res = self.run_cli("index", "build", "--version", "idx-hn-1")
        self.assertEqual(code, 0, self.last_err)
        self.assertEqual((res["index_version"], res["records"]), ("idx-hn-1", 2))
        from app.db.models import UsageReservation
        with self.factory() as db:
            rows = db.scalars(select(UsageReservation).where(UsageReservation.purpose == "knowledge_index_embedding")).all()
        self.assertEqual([(r.call_key, r.purpose, r.status) for r in rows],
                         [("knowledge-index:idx-hn-1:0", "knowledge_index_embedding", "settled")])
        from knowledge_support import fake_vector
        result = self.store.search([BOKCHOY], fake_vector("鈣"), "text-embedding-3-small")
        self.assertEqual(result["records"][0]["nutrition_fact"], "青江菜含鈣。")
        self.assertEqual(result["records"][0]["evidence_quote"], "合成段落一：青江菜是常見的深綠色蔬菜，含有鈣質。")
        self.assertEqual(self.store.active_index()["index_version"], "idx-hn-1")

    def test_index_build_stops_when_budget_is_exhausted(self):
        from app.services import spend
        self.crawl_one()
        self.assertEqual(self.run_cli("classify")[0], 0, self.last_err)
        kid = f"hn-80003-{BOKCHOY}-1"
        self.store.review(kid, self.store.get_record(kid)["source_version"], reviewer="審核者",
                          approved=True, note="已核對", confirm_third_party_license=True)
        with self.factory() as db, db.begin():  # 分類已建立本月額度列；把剩餘額度用完
            db.execute(text("UPDATE budget_months SET spent_twd = limit_twd - committed_twd - reserved_twd"))
        code, _ = self.run_cli("index", "build", "--version", "idx-hn-2")
        self.assertNotEqual(code, 0)
        self.assertIn("BUDGET", self.last_err.upper())
        # 分類同樣停在額度不足，不會繼續送出
        calls = len(self.classifier.calls)
        self.assertNotEqual(self.run_cli("classify", "--reclassify")[0], 0)
        self.assertIn("BUDGET", self.last_err.upper())
        self.assertEqual(len(self.classifier.calls), calls)
        self.assertEqual(self.embedder.calls, [])
        self.assertIsNone(self.store.active_index())

    def test_crawl_channel_with_page_limit(self):
        code, report = self.run_cli("crawl", "--channel", CHANNEL, "--max-pages", "1")
        self.assertEqual((code, report["pages"], report["articles_fetched"]), (0, 1, 3))

    def test_crawl_queued_consumes_cloud_requests_and_marks_them(self):
        from app.services import knowledge_queue
        knowledge_queue.record_missing(self.factory, {"status": "not_found",
                                                      "by_ingredient": {CABBAGE: "not_found", TOMATO: "not_found"}})
        code, report = self.run_cli("crawl", "--queued", "--max-pages", "3")
        self.assertEqual(code, 0, self.last_err)
        self.assertEqual(report["by_food"][CABBAGE], {"pending": 1, "approved": 0})
        self.assertEqual(knowledge_queue.pending(self.factory), [])
        with self.factory() as db:
            outcome = db.execute(text("SELECT last_outcome FROM knowledge_crawl_requests WHERE food_id = :i"),
                                 {"i": CABBAGE}).scalar_one()
        self.assertEqual(outcome, {"pending": 1, "approved": 0})

    def test_crawl_requires_authorization_reference(self):
        self.deps.authorization_ref = None
        code, _ = self.run_cli("crawl", "--ids", "80003-80003")
        self.assertNotEqual(code, 0)
        self.assertIn("authorization", self.last_err.lower())

    def test_status_summarises_review_queue_index_and_crawl(self):
        self.crawl_one()
        code, status = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertEqual(status["records"], {"pending": 3, "approved": 0, "rejected": 0})
        self.assertEqual(status["articles"], {"matched": 1})
        self.assertIsNone(status["active_index"])
        self.assertEqual(status["crawl_queue_pending"], 0)


if __name__ == "__main__":
    unittest.main()
