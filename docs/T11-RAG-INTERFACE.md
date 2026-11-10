# T11 營養知識 RAG：重做用整合介面規格

狀態：草稿（2026-11-08）。依 `main`（a5ad10d）現有程式整理，供重做 RAG 時對接；尚未實作、未執行任何驗證。
規格依據：`spec.md` R05、R11、R12、AC24、AC29、AC32；`architecture.md` 第五節；`tickets/11-recipe-retrieval.md`。

本文件分兩類內容：

- **必須維持**：新 RAG 只要照這些契約輸出，前端、結果檢視、採用快照、歷史頁與重試流程都不必修改。
- **現況問題／待決定**：讀程式發現的不一致，重做時建議一併處理；標「待決定」的項目需使用者決定。

---

## 1. RAG 在整體流程中的位置

```text
問卷 → 昨日去重 → 結構化食譜查詢（不用向量）─┬─ database_menu
                                              └─ ai_generated_menu（雲端生成）
→ 禁忌／去重檢查 → 基準營養對齊 → n 縮放 → 缺料查價
→ ★ 營養知識 RAG：最終食材 FOOD ID → 檢索審核知識 → 生成附引用說明 → 引用核對
→ 結果顯示 → 採用（快照保存知識與說明）
```

RAG 只產生「食材小知識」，在配方與最終食材確定之後才執行。

## 2. 不可改變的規則（必須維持）

| # | 規則 | 依據 |
|---|---|---|
| I1 | 只依**最終配方食材的 FOOD ID**（`FOOD\d{6}`）檢索；先用 ID 篩選，再做相關性排序。主食不在 `final_food_ids` 內。 | R12、AC32 |
| I2 | 只回傳 `status='approved'` 的知識；未審核、被拒、舊版本不可出現。 | R12、AC29 |
| I3 | 知識**不能**改菜、改份量、改 TFDA 數值或採用資格。說明失敗不能讓生成失敗。 | R05、R12 |
| I4 | 「查無知識」(`not_found`) 與「服務故障」(`unavailable`) 必須分開；故障不可偽裝為零筆。 | R12、AC32 |
| I5 | 說明只能引用本次檢索快照中實際取得、且與該食材相符的紀錄；偽造 ID、錯食材、錯版本一律丟棄。 | AC29 |
| I6 | 配方食材改變後重新檢索，不保留已移除食材的說明。 | R12、AC32 |
| I7 | 所有付費呼叫（雲端向量化、雲端說明生成）都經 T10 `paid_call` 預留與結算；額度不足不使菜單失敗。 | R11 |
| I8 | 來源出處（G07：來源名稱、標題、發布日）顯示時不可移除。 | G07 |
| I9 | 不記錄查詢文字、token、模型原始輸入／輸出或例外內文；日誌只寫固定錯誤代碼。 | 正式 Log 政策 |
| I10 | 知識服務在團隊電腦，經 Cloudflare Tunnel＋Access（`CF-Access-Client-Id/Secret`）＋服務 Bearer token 連線；資料庫不對外。 | R11、AC24、T17 |

## 3. 整合點總覽

| 整合點 | 呼叫者 | 檔案 | 新 RAG 要提供 |
|---|---|---|---|
| P1 正式生成的知識步驟 | T14 背景工作 | `backend/app/services/generation.py` `knowledge_section()` | 契約 A（快照）＋契約 B（說明） |
| P2 只重試知識說明 | T15 背景工作 | `generation.py` `make_knowledge_retry_handler()`；API `POST /meal-requests/{id}/results/{v}/knowledge-retry` | 同 P1，重用 `knowledge_section()` |
| P3 預覽小知識 | 同步 API | `backend/app/api/meal_requests.py` `POST /meal-requests/{id}/knowledge-tips` → `knowledge_explanation.tips_for_menu()` | 契約 C |
| P4 服務狀態 | `GET /health/team` | `backend/app/services/team_status.py` | 契約 D 的 `GET /v1/health` |
| P5 缺知識補抓佇列 | P1 內部 | `backend/app/services/knowledge_queue.record_missing(factory, snapshot)` | 契約 A 的 `status`／`by_ingredient` |
| P6 結果檢視、採用快照、歷史 | 讀取 | `meal_results.view()`、`meal_plans.py` | 契約 B 存在 `payload["explanation"]`；版本欄位見第 8 節 |
| P7 前端 | 顯示 | `frontend/src/api/types.ts`、`MealResultView.tsx`、`MenuPreviewPanel.tsx`、`GenerationHistoryPage.tsx` | 契約 B／C 的欄位名稱 |

---

## 4. 契約 A：知識檢索快照 `KnowledgeSnapshot`（必須維持）

保存在結果 `payload["knowledge"]`；也是說明生成與引用核對的唯一依據。

```jsonc
{
  "status": "available" | "not_found" | "unavailable",
  "final_food_ids": ["FOOD000123", "FOOD000456"],   // 已排序、去重
  "records": [ KnowledgeRecord, ... ],              // unavailable 時為 []
  "by_ingredient": { "FOOD000123": "available", "FOOD000456": "not_found" },
  "index_version": "dev-20261108T010203Z" | null,
  "embedding_model": "embeddinggemma:latest@<64位digest>" | null,
  // 僅 status=unavailable：
  "reason": "<見第 7 節>",
  "code": "KNOWLEDGE_UNAVAILABLE"
}
```

規則：

- `final_food_ids` 為空 → `status='not_found'`，`records=[]`。
- `status='available'` 若且唯若 `records` 非空。
- `by_ingredient` 的鍵必須恰為 `final_food_ids`；有紀錄者為 `available`，其餘 `not_found`。
- `unavailable` 時 `by_ingredient` 目前是 `{}`（`knowledge.py`）。`preview_knowledge.unavailable()` 則填 `"unavailable"`；兩者都可以，因為 `knowledge_queue.missing_food_ids` 只看 `available`／`not_found` 狀態的快照。
- 每個食材最多 `KNOWLEDGE_LIMIT_PER_INGREDIENT`（預設 3）筆，依相關性排序。
- `(knowledge_id, source_version, ingredient_id)` 在同一快照內不可重複。

### `KnowledgeRecord`

| 欄位 | 型別 | 必填 | 用途 |
|---|---|---|---|
| `knowledge_id` | str | ✔ | 引用鍵 |
| `source_version` | str | ✔ | 引用鍵（不可變版本） |
| `ingredient_id` | str `FOOD\d{6}` | ✔ | 引用鍵；必須屬於 `final_food_ids` |
| `excerpt` | str | ✔ | 原文片段（或標明「整理摘要」） |
| `source_url` | str（http/https） | ✔ | 前端「原文」連結 |
| `rights` | str | ✔ | 使用條件 |
| `conditions` | str | ✔ | 適用條件／限制 |
| `reviewer` | str | ✔ | 實際審核者 |
| `reviewed_at` | ISO 時間字串 | ✔ | 審核時間 |
| `distance` | float \| null | | 相關性分數（僅追溯用） |
| `source_name` | str \| null | G07 | 來源網站名稱；有值時 `title` 必填 |
| `title` | str \| null | G07 | 文章標題 |
| `published_at` | ISO 時間字串 \| null | G07 | 發布日（顯示為台北日期） |
| `content_provider` | str \| null | G07 | 內容提供者 |
| `nutrition_fact` | str \| null | P1 必要 | 一句營養知識；P1 只用同時有 `nutrition_fact` 與 `evidence_quote` 的紀錄寫說明 |
| `evidence_quote` | str \| null | P1 必要 | `excerpt` 中逐字出現的支持句 |

預覽路徑（P3）另外讀 `fact_zh`、`evidence_type`、`source_label`、`ingredient_name`（見契約 C），目前由紀錄 payload 直接帶出。

## 5. 契約 B：正式結果的說明 `Explanation`（必須維持）

保存在結果 `payload["explanation"]`。`meal_results.view()` 會原樣輸出為 API 的 `knowledge` 欄位，並加上 `retry_available`；採用快照與歷史頁也直接讀這份資料。對應前端型別 `MealResult['knowledge']`。

```jsonc
// available
{ "status": "available",
  "items": [{
    "ingredient_id": "FOOD000123",
    "ingredient_name": "菠菜" | null,
    "explanation": "富含膳食纖維，有助腸道蠕動。",   // 一句，不含食材名稱（畫面另外顯示）
    "citations": [ Citation, ... ]                    // 至少一筆
  }],
  "dropped": [{ "ingredient_id": "...", "reason": "<見第 7 節>" }] }

// not_found：缺知識，畫面省略該區，不阻擋採用
{ "status": "not_found", "items": [], "dropped": [...] }

// unavailable：服務故障或額度不足，畫面顯示警告與「只重試知識說明」
{ "status": "unavailable", "code": "KNOWLEDGE_UNAVAILABLE", "reason": "...", "items": [], "dropped": [] }
```

`Citation`（前端型別 `KnowledgeCitation`）＝引用鍵加上原紀錄欄位：

```text
knowledge_id, source_version, ingredient_id,
excerpt, source_url, rights, conditions,
source_name, title, published_at, content_provider,
nutrition_fact, evidence_quote,
attribution   // 「來源：<網站>《標題》（YYYY-MM-DD）」；無 source_name 時為 null
```

引用欄位一律從快照的紀錄重建（`knowledge.verify_citations`），不信任模型回傳的網址或內文。

說明的程式驗證（全部通過才可放入 `items`）：

1. `ingredient_id` 有可用知識，且每個食材只出現一次。
2. 所有引用的 `ingredient_id` 等於該項目的食材。
3. 每個引用都存在於快照（`verify_citations`）。
4. `grounded_in_nutrition`：說明至少含一個營養關鍵字（`config/nutrition_keywords.json`），且所有關鍵字都出現在所引用紀錄的 `nutrition_fact`／`evidence_quote`。

`retry_available` 的條件是 `explanation.status == 'unavailable'`（`meal_results.knowledge_retry_available`）。前端只特別處理 `reason == 'BUDGET_EXHAUSTED'`，其他原因都顯示「服務暫時無法使用」。

## 6. 契約 C：預覽小知識 `KnowledgeTips`（必須維持）

`POST /meal-requests/{id}/knowledge-tips`，body `{"recipe_ids": [...]}`。後端自己重算 T12 取得 `final_food_ids`，不接受前端傳入食材或知識。對應前端型別 `KnowledgeTips`。

```jsonc
{
  "status": "available" | "not_found" | "unavailable",
  "reason": "..." ,                       // 僅 unavailable
  "items": [{
    "ingredient_id": "FOOD000123",
    "ingredient_name": "菠菜",
    "tip_zh": "一句繁中小知識（≤80 字）",
    "prompt_version": "t11-explain-prompt-v3",
    "model": "...", "latency_ms": 1234,
    "citations": [{ "knowledge_id", "source_version", "ingredient_id",
                    "source_url", "source_label", "conditions" }]
  }],
  "dropped": [{ "ingredient_id": "...", "reason": "..." }],
  "not_found": ["FOOD000456"],
  "final_food_ids": [...], "knowledge_status": "...", "index_version": "...",
  "input_version": 3, "recipe_ids": [...], "nutrition_status": "..."
}
```

前端 `MenuPreviewPanel` 認得的 `reason`：`EXPLANATION_NOT_CONFIGURED`、`NOT_CONFIGURED`、`KNOWLEDGE_UNAVAILABLE`、`EMBEDDING_UNAVAILABLE`。新增原因代碼時請同步更新 `TIP_UNAVAILABLE`。

說明驗證規則見 `knowledge_rag/explain.py` 的 `validate()`：引用必須是本次提供的知識、一句繁中、關鍵字與數字都要出自引用知識、不得誇大、一般衛教不得冒稱研究、不得寫「原因不明」或份量換算。

## 7. 狀態與原因代碼

| 層 | `reason` | 意義 | 使用者看到 |
|---|---|---|---|
| 檢索 | `NOT_CONFIGURED` | 未設定知識服務 URL／token | 不可用 |
| 檢索 | `EMBEDDING_NOT_CONFIGURED`／`EMBEDDING_UNAVAILABLE` | 查詢向量化未設定，或確定未計費的失敗 | 不可用 |
| 檢索 | `TIMEOUT`／`UNREACHABLE`／`AUTH_FAILED`／`SERVICE_ERROR` | HTTP 連線類（`team_link.failure_reason`；502/504/530 視為 UNREACHABLE） | 不可用 |
| 檢索 | `MODEL_MISMATCH` | 索引模型與查詢模型不同（服務回 409） | 不可用 |
| 檢索 | `INVALID_RESPONSE` | 回應含錯食材、未審核、缺欄位，整份不採用 | 不可用 |
| 費用 | `BUDGET_EXHAUSTED`／`BILLING_UNCERTAIN` | T10 額度不足／可能已計費 | 不可用（顯示額度訊息） |
| 說明 | `EXPLANATION_NOT_CONFIGURED`／`EXPLANATION_UNAVAILABLE` | 無 API key／確定未計費的失敗 | 不可用 |
| 說明 | `EXPLANATION_MODEL_REFUSED`／`_OUTPUT_TRUNCATED`／`_OUTPUT_INCOMPLETE`／`_INVALID_FORMAT` | 模型回應異常 | 不可用 |
| dropped | `NO_NUTRITION_FACT` | 有紀錄但缺 `nutrition_fact`／`evidence_quote` | 省略該食材 |
| dropped | `INGREDIENT_NOT_IN_SNAPSHOT`／`CITATION_INGREDIENT_MISMATCH`／`CITATION_NOT_IN_SNAPSHOT`／`NOT_GROUNDED_IN_NUTRITION_FACT` | 說明驗證不通過 | 省略該食材 |

`dropped` 只用於追溯，前端不顯示。新原因代碼可以增加，但上述代碼的意義不可改變。

## 8. 版本與存證欄位（必須維持）

`payload["evidence"]` 中由 `explanation_evidence()` 產生：

```text
explanation_model, explanation_prompt_version, explanation_schema_version  // 未產生說明時為 null
knowledge_index_version   ← snapshot.index_version
embedding_model           ← snapshot.embedding_model
```

只重試知識說明時另外寫入 `evidence.knowledge_retry_call_keys` 與 `payload["knowledge_retry"] = {from_result_version, retried_at, job_id}`。`meal_plans.py` 的採用快照 `versions` 會複製 `knowledge_index_version`、`embedding_model`、`explanation_*`。新 RAG 需固定並回報這些版本，換模型或維度時必須用新的 `index_version`。

## 9. 後端入口函式（必須維持簽名，內部可重寫）

```python
# generation.py — P1/P2 唯一入口
knowledge_section(*, final_food_ids: list[str], names: dict[str, str], paid_call, responder,
                  knowledge_client, embedder, stage: Callable[[str], None], factory,
                  call_suffix: str = "") -> tuple[KnowledgeSnapshot, Explanation]
#  - 先 stage("knowledge")，產生說明前 stage("explaining")（前端 GenerationPanel 顯示階段）
#  - 不可拋出 BudgetExhausted/UncertainBilling；改回 unavailable
#  - 呼叫 knowledge_queue.record_missing(factory, snapshot)

# knowledge_explanation.py — P3
tips_for_menu(factory, *, final_food_ids, household_id, request_tag: str,
              client=None, embedder=None, explainer=None, names=None) -> KnowledgeTips 主體

# knowledge.py — 引用核對（P1 用）
verify_citations(citations: list[dict], snapshot: KnowledgeSnapshot) -> {"valid": bool, "citations": [...], "invalid": [...]}
```

`paid_call` 介面（`JobContext.paid_call`／`request_paid_call` 相同）：

```python
paid_call(*, name: str, purpose: str, provider: str, model: str,
          max_input_tokens: int, max_output_tokens: int,
          call: Callable[[], tuple[result, {"input_tokens": int, "output_tokens": int}]]) -> result
# 拋 spend.CallNotBilled：確定未計費，可重試
# 拋 spend.BudgetExhausted / spend.UncertainBilling：交由上層轉成 unavailable
# 同一工作嘗試內 name 不可重複；P2 以 call_suffix 區分
```

`purpose` 目前用：`knowledge_query_embedding`、`knowledge_index_embedding`、`knowledge_explanation`。模型價格必須登錄於 `config/model_pricing.json`，未列價的模型會被拒絕呼叫。

測試透過 `make_generation_handler(knowledge_client=..., embedder=..., responder=...)` 與 `make_knowledge_retry_handler(...)` 注入假物件；新 RAG 的依賴必須同樣可以注入。

## 10. 契約 D：團隊電腦知識服務 HTTP（可重新設計，但需滿足下列要求）

驗證：每個端點都要求 `Authorization: Bearer <KNOWLEDGE_SERVICE_TOKEN>`（以 `hmac.compare_digest` 比對）。雲端另外帶 Cloudflare Access 標頭。不提供 OpenAPI 或 docs 頁面。

| 端點 | 現況 | 要求 |
|---|---|---|
| `GET /v1/health` | 200 `{status: ok\|not_ready, index_version, embedding_model}`；資料庫故障回 503 | `team_status.probe` 只看 HTTP 狀態碼（200＝online，503＝`SERVICE_NOT_READY`）。**沒有 active 索引時應回 503**（見 §11 問題 3） |
| `POST /v1/retrieve` | body `{food_ids, query_text, index_version?}`；在地端向量化 → 回快照＋`provider='ollama'`、`provider_cost_usd='0'`、`embedding_dimensions=768`、`template_version` | 建議作為唯一檢索端點 |
| `POST /v1/knowledge/search`、`/v1/search` | body `{food_ids, query_embedding, embedding_model, index_version?, limit_per_ingredient?}` | 舊的雲端向量化契約；模型不符時回 409 |

服務回應的錯誤碼：`401 AUTH_FAILED`、`422 INVALID_QUERY`、`409 EMBEDDING_MODEL_MISMATCH`、`503 KNOWLEDGE_UNAVAILABLE`／`NO_ACTIVE_INDEX`／`INDEX_NOT_AVAILABLE`／`STORE_UNAVAILABLE`／`LOCAL_EMBEDDING_NOT_CONFIGURED`。

雲端用戶端必須把回應當成不可信資料，並檢查：`status ∈ {available, not_found}`、每筆都是 approved 且食材在 `food_ids` 內、必填欄位齊全、無重複、`by_ingredient` 與紀錄一致、模型／索引與預期相同、指定 `index_version` 時沒有被替換。任一項不符就把整份視為 `unavailable`（`INVALID_RESPONSE`）。

團隊電腦呼叫雲端的內部 API（`/internal/knowledge/*`：補抓佇列 claim/complete/summary、classify、embeddings）屬於知識維護流程，不在線上檢索路徑上。重做時如果保留爬蟲／分類，這些端點的契約見 `backend/app/api/internal_knowledge.py` 與 `knowledge_rag/cloud.py`。

## 11. 現況問題與待決定事項

1. **兩條檢索路徑不一致（建議統一）。** P1／P2 用 `knowledge.retrieve_knowledge`：雲端以 `default_embedder()`（OpenAI `text-embedding-3-small`，1536 維）算查詢向量，再呼叫 `/v1/knowledge/search`。地端 active 索引是 `embeddinggemma@digest`（768 維），會回 409，所以正式生成很可能一律得到 `MODEL_MISMATCH` → `unavailable`。P3 用 `preview_knowledge.retrieve_knowledge` 呼叫 `/v1/retrieve`，可以正常運作。以上是讀程式推論，未實測。
   **建議**：新 RAG 只保留一個雲端用戶端，P1／P2／P3 都呼叫 `/v1/retrieve`（地端向量化、不付費），回傳契約 A。
2. **兩套說明生成（待決定）。** P1 使用 `nutrition_fact`／`evidence_quote` 和 `t14-explain-prompt-v3`，一次呼叫處理全部食材，輸出 `explanation`。P3 使用 `fact_zh`／`conditions` 和 `t11-explain-prompt-v3`，每個食材一次呼叫，輸出 `tip_zh`。兩者的欄位名稱與驗證規則都不同。是否合併成一套，需要決定；若合併，契約 B 與 C 的輸出欄位仍須維持（或同步修改前端型別）。
3. **health 沒有索引時回 200。** `team_status` 會把這種狀態顯示為「在線」，實際上檢索會失敗。建議 `not_ready` 改回 503。
4. **建索引的文件文字。** `build_index` 向量化的是整份 payload JSON（含網址、權利等欄位），bootstrap 呼叫的是 `embed` 而非 `documents()`，所以沒有加上文件前綴；查詢端則有 `query` 前綴。建議只向量化知識內容並套用一致的模板，模板改變時更新 `template_version`。
5. **`knowledge.py` 結尾重新匯入 `preview_knowledge` 的同名函式**（`retrieve_for_job`、`unavailable` 等），兩個模組互相纏繞。重做時建議只留一個模組。
6. **前端寫死的來源名稱。** `MealResultView` 的區塊標題固定寫「資料來源：健康醫療網」；如果來源變多，建議改為依引用的 `source_name` 動態產生（`MenuPreviewPanel` 已是動態）。
7. **知識資料欄位（待決定）。** 123 筆審核資料（`candidates/web_paper_2026-10-30.json`）以 `fact_zh` 為主；P1 需要的 `nutrition_fact`／`evidence_quote` 來自分類器（決策 #29）。若資料沒有經過分類，P1 會全部落入 `NO_NUTRITION_FACT`。新 RAG 需決定以哪個欄位作為說明依據。

## 12. 設定與啟動

| 變數 | 用途 |
|---|---|
| `KNOWLEDGE_PROVIDER` | `ollama`（預設）／`openai`（舊雲端向量化，需 T10） |
| `KNOWLEDGE_SERVICE_URL`、`KNOWLEDGE_SERVICE_TOKEN` | 雲端 → 團隊電腦知識服務 |
| `TEAM_ACCESS_CLIENT_ID`、`TEAM_ACCESS_CLIENT_SECRET` | Cloudflare Access 服務權杖（YOLO 與知識服務共用） |
| `KNOWLEDGE_TIMEOUT_SECONDS`（10）、`KNOWLEDGE_LIMIT_PER_INGREDIENT`（3） | 檢索逾時與每食材筆數 |
| `KNOWLEDGE_INTERNAL_TOKEN` | 團隊電腦 → 雲端內部 API |
| `KNOWLEDGE_RETRY_DAILY_LIMIT`（10） | 只重試知識說明的每日上限 |
| `OPENAI_API_KEY`、`KNOWLEDGE_EXPLAIN_MODEL`、`GENERATION_MODEL` | 說明生成（P3 用前者、P1 用後者） |
| 團隊電腦：`KNOWLEDGE_DATABASE_URL`、`OLLAMA_URL`、`PROJECT_DATA_ROOT` | 知識庫與地端模型 |

本機開發：`scripts/start-dev.ps1` 會建立 `overcooked_knowledge_dev`、執行 `python -m knowledge_rag.bootstrap`，再以 `uvicorn knowledge_rag.server:create_app --factory` 啟動於 `KNOWLEDGE_PORT`（8091），並把網址與 token 傳給後端；設 `DEV_SKIP_KNOWLEDGE=1` 可略過。新 RAG 若改變啟動入口，只需修改這段腳本。

## 13. 驗收：需保持通過或改寫的測試

| 測試 | 涵蓋 |
|---|---|
| `tests/test_t11_knowledge_service.py` | 雲端用戶端、快照、unavailable／not_found、引用核對 |
| `tests/test_t11_server_contract.py` | 地端 HTTP 服務契約與驗證 |
| `tests/test_t11_knowledge_store.py`、`test_t11_knowledge_review.py`、`test_kn_admin.py` | 版本不可變、審核、索引發布 |
| `tests/test_t11_explain.py`、`test_t11_knowledge_tips.py` | P3 說明驗證與 API |
| `tests/test_kn_attribution.py` | G07 出處欄位與顯示文字 |
| `tests/test_kn_crawl_queue.py`、`test_kn_internal_api.py`、`test_kn_healthnews_*.py` | 補抓佇列、內部 API、爬蟲 |
| `tests/generation_support.py`、`tests/knowledge_support.py` | 生成流程用的假知識服務 |

另外需要新增的檢查：P1 在 `KNOWLEDGE_PROVIDER=ollama` 下能取得 `available`（目前缺少的端到端情境，對應 §11 問題 1）、配方食材變更後重新檢索（AC32）、錯食材／未審核／偽造引用不顯示（AC29）。依 AGENTS.md，例行檢查使用合成資料與假外部服務，不連正式知識庫或付費模型。
