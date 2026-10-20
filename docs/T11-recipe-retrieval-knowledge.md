# T11 結構化食譜查詢、昨日去重與營養知識 RAG

覆蓋 R04、R12、R13；AC06、AC07、AC09、AC29–AC32 的程式與合成測試。**知識來源尚未提供（G07 未通過），目前只驗證合成流程，不代表真實知識交付。**

## (A) 雲端結構化查詢（不用向量）

| 模組 | 公開介面 |
|---|---|
| `backend/app/services/recipe_retrieval.py` | `retrieve_database_menu(db, household_id, draft, excluded_food_ids=...) -> RetrievalResult`；資料庫故障拋 `RecipeRetrievalError("RECIPE_DATABASE_UNAVAILABLE")` |
| `backend/app/services/meal_history.py` | `previous_day_context(db, household_id, meal_date, specified_dish_ids=...)`、`recheck_previous_day(db, context, dishes)`、`record_adopted(...)` |

- 只查 `recipes.is_eligible IS TRUE`；同一 `standard_dish_id` 的多個來源視為同一道菜，只選一次。
- 指定菜：名稱正規化（`recipe_import.normalize_name`）後比對菜名與別名。查無 → `no_feasible_database_menu`／`SPECIFIED_DISH_NOT_IN_DATABASE`；對到多道不同菜 → `dish_identity_unresolved`／`SPECIFIED_DISH_AMBIGUOUS`（回報問題，不默認為不同菜，也不自動轉 AI）；指定菜含排除食材 → `SPECIFIED_DISH_DIETARY_CONFLICT`。
- 禁忌：呼叫端傳入 `meal_request.restrictions(...)["excluded_food_ids"]`（已含同義／上位展開），含任一排除食材的食譜不入選。
- 昨日排除：餐食日期減一個日曆日、同家庭、全部餐別、`status='adopted'`；不往前回溯。同菜以標準菜色 ID 加名稱／別名換算的 ID 交集判定。明確指定菜只豁免它自己。
- 不足菜數時的原因：排除昨日菜前足夠 → `ALL_EXCLUDED_BY_PREVIOUS_DAY`，否則 `NO_MATCHING_RECIPES`。兩者皆是自動 AI 回退原因（`code = NO_FEASIBLE_DATABASE_MENU`），結果附 `previous_day`，AI 模式仍須避開其中的 `excluded_dishes`。
- 已有食材（含已確認同義組）與口味偏好（比對菜類、菜系、烹調方法）只影響排序。預算、營養由 T12／T13／T15 驗證；主食不計入菜數。
- 結果 `to_dict()` 可直接保存為 GenerationResult 的「實際結構化查詢來源」與「昨日比對」。

### 已採用餐食紀錄（最小快照）

遷移 `a1c4e7f20b39` 建立 `adopted_meal_plans`：家庭、餐食日期、餐別、模式（`database_menu`／`ai_generated_menu`）、狀態（`adopted`／`superseded`）、版本、`dishes` JSONB（`standard_dish_id`、`dish_name`、`aliases`、`source`、`recipe_id`）。T11 只讀它；**Ticket 16 以 ALTER 擴充**採用交易、冪等鍵與完整快照，不另建平行表。採用前呼叫 `recheck_previous_day`，回傳 `conflict`／`PREVIOUS_DAY_DISH_CONFLICT` 時拒絕過時結果。

## (B) 營養知識 RAG（團隊電腦）

| 位置 | 用途 |
|---|---|
| `knowledge_rag/schema.sql` | 團隊電腦知識庫（PostgreSQL＋pgvector）：`knowledge_records`、`knowledge_indexes`、`knowledge_embeddings(vector(1536))`。**不在雲端 Alembic 鏈內** |
| `knowledge_rag/store.py` | 匯入（一律 pending）、審核（不可再改）、新版本取代舊版、`build_index`、`search` |
| `knowledge_rag/server.py` | `python -m knowledge_rag.server`；`GET /v1/health`、`POST /v1/knowledge/search`，Bearer token，預設綁 127.0.0.1:8766 |
| `backend/app/services/knowledge.py` | 雲端：`HttpKnowledgeClient`、`retrieve_knowledge(...)`、`verify_citations(...)`、`index_embedder(...)` |
| `backend/app/services/llm_client.py` | `OpenAIEmbedder`（`text-embedding-3-small`）；未設定 `OPENAI_API_KEY` 時不可用 |

- 檢索：先限定最終食材 FOOD 編號＋現行版本＋已審核＋active 索引，再依向量距離每食材取前 `KNOWLEDGE_LIMIT_PER_INGREDIENT`（預設 3）筆。回傳知識 ID、片段、來源、版本、審核者、索引版本與向量模型。
- 費用：查詢向量在雲端 worker 以 `ctx.paid_call(name="embed_query", ...)` 預留／結算後才送到團隊電腦；同一次嘗試內重新對應請改 `call_name`。建索引用 `index_embedder(factory, embedder, index_version)`，每批各自預留（呼叫鍵 `knowledge-index:<版本>:<批次>`，重建請用新版本）。
- 狀態：`available`；`not_found`（可省略，`by_ingredient` 逐食材標示）；`unavailable`＋`reason`（`NOT_CONFIGURED`、`EMBEDDING_NOT_CONFIGURED`、`EMBEDDING_UNAVAILABLE`、`TIMEOUT`、`UNREACHABLE`、`AUTH_FAILED`、`SERVICE_ERROR`、`MODEL_MISMATCH`、`INVALID_RESPONSE`），`code = KNOWLEDGE_UNAVAILABLE`。回應含錯食材或未審核紀錄時整份視為 `INVALID_RESPONSE`。額度不足與計費不明照 T10 規則往上拋。
- 設定：雲端 `KNOWLEDGE_SERVICE_URL`、`KNOWLEDGE_SERVICE_TOKEN`、`OPENAI_API_KEY`；團隊電腦 `KNOWLEDGE_DATABASE_URL`、`KNOWLEDGE_TOKEN`。`config/model_pricing.json` 新增 `text-embedding-3-small`（pricing_version `plan-2026-10-20`）。

## 測試

`tests/test_t11_*.py`（需要 `TEST_DATABASE_URL`）。知識庫測試會在同一伺服器建立 `<測試庫>_knowledge` 並以 `schema.sql` 重建。OpenAI 一律以假轉接器或替換 `httpx.post` 測試。

## 尚未完成／需真實環境

- 真實知識來源、審核與索引（G07）；團隊電腦實際部署、加密通道（T17）。
- 知識匯入／審核／建索引目前是 Python API，尚無管理指令。
- 次日生成的真實寫入整合由 Ticket 16／19 驗收。

## 2026-10-20 追加：真實來源（G07）

知識來源改為健康醫療網，爬蟲、管理指令（`python -m knowledge_rag.admin`）、補抓佇列與出處顯示見 [knowledge-source-healthnews.md](knowledge-source-healthnews.md)。上方「尚無管理指令」已由該文件取代；授權證據仍待使用者存檔。
