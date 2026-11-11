# 1111_update_V2：T01–T16 交付驗收矩陣

盤點日期：2026-11-11。工作目錄為本儲存庫 `overcooked/overcooked`，分支 `1111_update_V2`，盤點起點 `2e43bf5e53dbeb5f3f5350889522eeffbadd5a54`。下方逐項表保留開工時的**靜態盤點**；其後實際執行的進展以本節最新證據為準。單一流程成功不代表整張 ticket 通過，更不代表使用者已親自驗收。

## 最新實測進展（2026-11-11 晚間）

依據 [本分支驗證紀錄](../1102_V3/VALIDATION.md) 與 [AI 身分修正實測](AI-IDENTITY-VALIDATION.md)，人工操作步驟另見 [使用者驗收清單](USER-ACCEPTANCE.md)。

| 範圍 | 已實際完成 | 仍未證明 |
| --- | --- | --- |
| 隔離／管理帳號 | exact V2 DB 與 scope guard；既有身分保留，重新登入核對12角色／34權限，撤臨時operator權限。 | 每項管理提交／審查／發布／回復；自審限制仍保留。 |
| T12–T16 DB主要鏈路 | 真R0036工作 `197984e2-0e43-41b2-9025-2e5275da4d62` 約42秒成功，採用 `e443e6cb-7d01-4ff4-8757-babebbdd3a84`；真報價99元Sim訂單 `13d7043d-b507-4310-bb7c-c8e451ba6179`，重送同收據，Admin恰一筆checkout事件。 | 使用者完整瀏覽器驗收、兩日排除／例外／競態／發布回復；本次是HTTP E2E。 |
| T14 AI阻擋修正 | AI來源解析與名稱身分流程已修；外部schema限定合法名稱與有限正用量，後端解析FOOD_ID，保留歧義拒絕。最終prompt v9/schema v5兩種真OpenAI情境均一次成功。 | 最終兩筆僅adoptable=true，未採用，不能視為AI採用→Sim→Admin完成。 |
| T10 真費用 | 最終different-stock `ae7015e8-266e-4a36-8d0f-9b9b623d7906` 0.1653 TWD；matching `ac1202f3-358b-487a-8ce4-8cfafccfcab9` 0.1608 TWD，全部結算。修正階段含失敗共1.0584 TWD。 | 不代表全部配額／重試故障情境已在V2實驗；早期401、身分錯配、鹽歧義、INVALID_QUANTITY失敗紀錄保留。 |
| T14 回歸 | 最終identity／diagnostics／source-resolution 41 passed；先前管理與知識相容49 passed、11 skipped。 | skipped是需要專用PostgreSQL，不能算通過；中斷的parent管理測試不計成功。 |
| T11（依使用者指示暫緩） | 保留既有索引與30秒單次／45秒共用時間限制；暖載短輸出實測仍超過60秒。 | 使用者已指示跳過，本輪不切雲端、不列為驗收通過。 |
| T15／T16追加 | 兩日採用、昨日排除與指定例外、舊結果409、重送事件不增、跨家庭404通過；真實新報價snapshot10寫入後，兩筆完整快照仍保留snapshot7且hash不變。 | 來源政策發布／回復、跨餐別／競態及T15全部缺值分支仍待驗；使用者畫面驗收未做。 |
| T05／T09真照片 | 真實食譜照片上傳201，YOLO回傳牛番茄建議、未自動指派FOOD_ID，confirmed保持空。 | 未驗使用者確認與完整照片問卷流程；單張成功不是品質門檻通過。 |

下表與優先清單所述「AI line無綁定」是已修復的開工問題，不再是目前未解阻擋；現在需要的是AI採用後流程及使用者操作驗收。兩日歷史、YOLO品質、四項來源缺口、T11合格證據與乾淨機交付仍不得標完成。R0036分類已唯讀核對：SQL、portable bundle、repo同名Excel皆為「主食」，V2未找到其管理版本更正，未擅自重分類。

依據為根目錄 `AGENTS.md`、[整合說明](../1102_V3/README.md)、[來源清冊](../1102_V3/SOURCES.md)、[資料庫責任](../1102_V3/DATABASE.md)、`spec.md` 及 [tickets 01–16](../../tickets)。早期文件的「尚未實作／尚未匯入」是當時狀態，不能取代本次程式盤點。後續實作若改變下列阻擋，應附本分支執行證據回填，而非沿用舊報告。

## 交付入口與共通通過條件

新版主站使用 `overcooked-frontend/frontend/src/live/App.tsx`；`src/main.tsx` 只有明確 `demo` mode 才載入展示站。正式 API 為 `backend/app/main.py`，搭配正式生成 worker；另有 `sim-supermarket/` 與 `admin-dashboard/`。YOLO、T11 與飲食小幫手均為使用者指定標配，小幫手是獨立 R14／ticket 21 服務，不能因矩陣止於 T16 而省略。

每次實際驗收須留下程式 commit、migration head、各服務實際資料庫名稱、recipe release、nutrition import、mapping version、policy manifest SHA256、價格政策、模型／索引版本，以及不含秘密的測試操作識別。API 與 worker 必須讀同一組固定版本。知識與小幫手各用獨立資料庫。測試使用者、問卷和訂單可為驗收操作資料，但食譜、營養、來源、照片、價格及文章不得以合成資料替代而宣稱真實來源驗收。

**禁止直接依根目錄 compose 的預設啟動而推定隔離成立。** 盤點時 `compose.yaml` 的 backend／worker／scheduler URL 仍指向 `meal_planner_1102_v3`；單獨提供一個 `DATABASE_URL` 不代表 compose 內硬編碼值已替換。V2 必須使用獨立設定，先唯讀核對實際連線，再啟動會寫入的服務；不碰原正式庫。

## 逐項矩陣

下列路徑皆相對儲存庫根目錄。測試欄表示已存在的覆蓋來源，**不表示本分支已執行通過**。

| Ticket／交付契約 | 真實來源與目前程式 | API／使用者入口 | 已有測試來源 | V2 必須補的真實驗收與缺口 |
| --- | --- | --- | --- | --- |
| [T01 食譜](../../tickets/01-recipe-corpus.md) | `release_data/nutrition-v1/` 已有 SQL 來源資料包、manifest、policy；`scripts/install_real_nutrition.py` 與 verifier 已存在。保留 SQL 已修訂 distinct names、batch／portion 與 950 份食譜，不重匯同名不同 hash 工作簿。 | `/meal-requests/{id}/recipes`、`/recipe-catalog`；live `RecipePickerPage`、`RecipeCatalogPicker`；Admin 食譜管理。 | `test_t01_recipe_import.py`、`test_t01_release.py`、`test_recipe_dataset_1103.py`、`test_recipe_catalog.py`、management recipe tests。 | 新庫重建後逐表 hash、筆數、來源連結與 release 綁定一致；293 道基線需在 V2 實查；新發布食譜需新綁定，不能自動繼承原 line approval。 |
| [T02 價格來源](../../tickets/02-retail-price-source.md) | 既有 T02 匯入、核准／重查及價格管理程式，不是未實作；價格查詢時間、包裝、Food_ID 與 release 需一起保存。 | Admin 價格審核／重查；主站成本與 Sim catalog。 | `test_t02_pricing.py`、`test_t02_uni_prosperity.py`、`test_t02_release.py`、`test_t02_review_recheck.py`、`test_management_price.py`。 | 在隔離庫跑真爬蟲→候選→核准→查價→成本。歷史包保留原時間，不算現價；確認更新後實際生成取新資料、舊快照不變。Sim 真實模式不可落入 `DEMO_PACKS`。 |
| [T03 營養目標](../../tickets/03-nutrition-target-rules.md) | `nutrition_target/`、`nutrition_planning/`；設定預設 `hpa-dri8-mvp0.4.1`。 | 成員表單、問卷、結果每人目標。 | `nutrition_target/tests/test_confirmed_targets.py`、`nutrition_planning/tests/test_targets.py`；`test_t03_v3.py` 含舊版固定契約。 | 成人／未成年人／多人家庭用真規則驗算並記 rule version。不可把舊 0.3 測試通過當目前 0.4.1 證據；缺健康欄位需可修正而非亂估。 |
| [T04 營養與烹調](../../tickets/04-tfda-cooking-data.md) | `reconciled_policy.py`、`nutrition_planning/reconciled_nutrition.py` 已有固定 policy 的共用橋接。接續 281 mapping、62 supplement 與逐筆重疊來源決策，保留 SQL rejected／未知。 | `/t04-data` router、`T04DataPanel`，預覽及正式計算。 | `test_t04_release.py`、`test_versioned_sql_nutrition.py`、`test_t04_data_feedback.py`、`test_portable_nutrition_policy.py`、`test_portable_nutrition_verifier.py`、`test_install_real_nutrition.py`、`test_reconciled_nutrition.py`。 | 重驗來源值及逐 line provenance，不只檢查 env。舊分支 2477 lines 中 2473 可算、4 recipe-derived-only blocked 必須重現或附新的真實來源證據；不得補零。factor_one 與生熟／重量政策不得暗改。 |
| [T05 YOLO](../../tickets/05-yolo-model-evaluation.md) | 真權重 `yolo26s-food41-denoise-veg3-ft-20261108` 已有模型交付線索與 inference adapter，並非缺整個功能。 | inference 服務→`recognitions`→照片確認。 | `inference/test_inference.py`；模型 manifest／評估文件。 | 真照片可運作與品質 gate 分開。已有 precision 80.57、recall 76.39 不能證明 AC22 的指定主要類 macro 指標達標。須獨立無洩漏測試集、IoU≥0.5 一對一匹配與逐類樣本數；早期 ticket 的重複照片問題要有修正證據。 |
| [T06 基礎／日誌](../../tickets/06-runnable-foundation-logging.md) | FastAPI routers、migration、健康檢查、日誌與隱私測試已存在。 | `/health`、`/health/db`、`/health/team`。 | `test_health.py`、`test_logging.py`、`test_ac26_privacy.py`、`test_no_committed_secrets.py`、`test_pr25_migrations.py`、`test_management_postgres.py`。 | API／worker／scheduler 的庫與角色分權、重啟恢復、敏感值遮蔽、保留／清理政策需實驗。健康綠燈不得只表示 HTTP 存活；必須揭露標配依賴缺檔、索引或模型未就緒。 |
| [T07 身分／家庭](../../tickets/07-identity-household-members.md) | auth／households／members routers 與身分服務已存在。 | `/login`、`/households`、live `MembersPage`／`MemberFormDialog`。 | `test_t07_identity.py`。 | 新註冊、家庭切換、增修成員、未成年人欄位、跨家庭讀寫拒絕；用第二個家庭驗證 jobs／results／history 亦隔離。 |
| [T08 問卷](../../tickets/08-meal-questionnaire.md) | meal_requests 持久化、版本、草稿、主食契約已存在。 | `/meal-requests` CRUD、`/{id}/versions/{version}`、`/staple-options`；`MealRequestEditor`。 | `test_t08_meal_requests.py`、`test_staple_options.py`。 | 真實成員→預算／日期餐別／指定菜／過敏忌口／已有食材→重開草稿→修訂版本。生成應鎖定提交版本；主食 raw amount、batch 與份量語意需在 UI 清楚。 |
| [T09 拍照確認](../../tickets/09-photo-ingredient-confirmation.md) | recognition upload、詞典候選與使用者確認已有實作；YOLO label 不直接當 FOOD_ID。 | `recognitions` router、live `RecognitionCapture`、`IngredientPicker`。 | `test_t09_recognition.py`、`test_recognition_dictionary_candidates.py`。 | 真照片→模型 label→真詞典候選→手動確認→問卷已有食材。含低信心、多候選、無候選、超限圖片與服務失效；不得未確認就扣採購量。 |
| [T10 工作／費用](../../tickets/10-durable-jobs-spend-control.md) | durable jobs、lease、重試、付費 reservation／settlement、每日與月限額已有實作。 | `/{id}/submit`、`/cancel`、`/jobs`、`/jobs/{id}`；`GenerationPanel`。 | `test_t10_jobs.py` 覆蓋同 key、競態、兩次上限、失聯 lease、費用不明、未知價格拒絕、預算競態等。 | V2 真 worker 同 idempotency key 重送不重扣；停 worker／恢復；失敗保留問卷、歷史可讀。需要外部 AI 時先核實 model pricing version，未知價格不能零估。 |
| [T11 檢索／知識](../../tickets/11-recipe-retrieval.md) | `recipe_retrieval` 結構化菜單查詢；`knowledge_rag/` 真 123 筆來源與獨立索引；local knowledge explanation 已存在。 | `/knowledge-tips`、internal knowledge router；預覽及正式結果知識欄。 | `test_t11_recipe_retrieval.py` 的≥20場景、`test_t11_meal_history.py`、`test_t11_knowledge_*`、`test_t11_explain.py`、`test_local_knowledge_explanation.py`。 | 兩條知識路徑必須使用同版 active index。123 筆「已審核來源」不等於可做營養宣稱：舊驗收全部缺合格 nutrition_fact／verbatim evidence_quote，因此 NO_NUTRITION_FACT 是正確拒答。需補可追溯原文審查才能宣稱完整說明。 |
| [T12 份量／營養](../../tickets/12-baseline-scaling-nutrition.md) | `meal_calculation.py`、`nutrition_planning/`；預覽與正式來源橋接已在 main。 | `/nutrition-preview`、正式結果；`MenuPreviewPanel`、`NutritionTable`、`PersonNutrition`、`PortionsTable`。 | `test_t12_meal_calculation.py`、`test_t12_calc_fallbacks.py`、`test_meal.py`、`test_imported_nutrition.py`、`test_t04_bridge.py`。 | 同食譜、同份量／主食／版本，逐食材與逐人兩路一致；不偷用 AI 營養值。最終 gate 依使用者決策 energy≤110%、protein≥90%，不可照舊 ticket 文句改回雙向±10%。 |
| [T13 採購成本](../../tickets/13-missing-ingredient-costs.md) | `meal_cost`、volume price policy、package spend 與比例成本已有實作。 | 預覽／正式結果成本、`MarketCheckoutButton`、Sim。 | `test_t13_meal_cost.py`、`test_t13_volume.py`、`test_preview_capacity_adoption.py`、`test_volume_price_policy.py`。 | 真包裝可購量、已有食材抵扣、整包實付與比例用量成本分別驗算；過期／缺報價與不可換算量需明示，不能視為0或偽稱預算通過。 |
| [T14 正式生成／AI](../../tickets/14-cloud-recipe-generation.md) | `generation.py` 先 DB 組合再 AI fallback；`llm_client.py` 為 OpenAI Structured Outputs。Ollama 的 T11 解釋不是 AI 菜單 provider。 | 問卷 submit→jobs→result，`GenerationPanel`／`MealResultView`。 | `test_generation_nutrition_source.py` 只驗 handler 接線（mock _generate）；另有 jobs、management recipe activation/generated recipe、knowledge attribution tests，不能當完整 AI E2E。 | **盤點時 blocker**：AI `_ai_dishes` 無 DB recipe_ingredient_id，固定 calculator 因 recipe_line_bindings 不存在報 nutrition_policy_line_not_bound。須有獨立、唯一且核准的 FOOD 來源解析與衝突拒絕，再跑真 AI fallback；詳後節。 |
| [T15 結果決策](../../tickets/15-meal-result-decisions.md) | results 版本、decisions、price／knowledge retry 與 adopt 已存在，並非只有展示 UI。 | `/meal-requests/{id}/results`、`/{version}/decisions`、`/price-retry`、`/knowledge-retry`；`MealResultView`／`ResultSections`。 | `test_meal_plans.py`、`test_preview_capacity_adoption.py`、`test_t13_volume.py` 及管理測試。 | 查看不等於採用；逐版接受／拒絕／重新生成／明示接受價格例外。核對 preview 的 missing-price flagged 採用與正式明確接受例外的 UI 政策；AI 標記、失敗理由、來源與成本快照可見。 |
| [T16 採用／歷史](../../tickets/16-adoption-history.md) | `meal_plans.py` 以交易、idempotency、history recheck、snapshot、確認事件採用；`meal_history.py` 已包含前一日全部餐別與同日其他餐別排除。 | `/meal-plans` POST／GET、`/trend`、`/{id}`；live `MealHistoryPage`、`GenerationHistoryPage`，Sim 訂單與 Admin 事件。 | `test_meal_plans.py`、`test_t11_meal_history.py`、`test_v3_management_adoption.py`、`test_sim_market.py`、`test_market_handoff.py`。 | **必驗兩個日曆日**的真食譜採用與翌日排除、指定菜例外、交易競態與跨家庭；不可以同日一筆成功替代。舊快照在發布／回復後不重算。完整步驟見後節。 |

## T14：何時才能宣告 AI 生成可用

1. 在隔離庫先完成 DB 菜單生成→結果→採用，並準備以真實食譜條件形成的 DB 無可行組合案例；不能用任意中斷資料庫冒充正常 fallback。
2. 開工盤點時，`reconciled_policy.make_calculator` 僅接受綁定 recipe lines，AI 新菜沒有這些識別。修正必須在無 DB recipe ID 的 AI line 使用可追溯且唯一的核准來源，保留重量、生熟與烹調條件；同 FOOD 多個不同來源決策要報 ambiguity。不得偽造 recipe ID、偷接版本不符的 DB line 或採用 AI 自報營養。
3. 設定有效 provider、model、API key 與經核實的 pricing version；T10 reservation、配額、未知費用與最多兩次嘗試都需生效。當前 OpenAIResponder 不會因啟動 Ollama 就自動切為本機菜單模型。
4. 真回應通過 schema、合法 FOOD_ID、指定菜／過敏／忌口／歷史、營養與採購檢查；拒絕、不完整 JSON、來源不足、超預算或費用不明必須留下可操作錯誤，問卷可保留。
5. 成功結果、採用與歷史皆標 AI generated，食譜來源不能偽稱資料庫原食譜。T11 沒有合格營養證據時需誠實顯示不足，不應產生捏造營養理由。
6. 回填完整 job→result→decision→plan→Sim→Admin 證據與實際費用帳，不把 fake LLM 的 unit test 當真外部生成驗收。若本輪未啟用外部 provider，交付狀態應明列「DB 路徑可用／AI 未實驗」，不能標 T14 全部通過。

## T16：兩日歷史的必要驗收順序

1. 建立驗收家庭 A、家庭 B，使用真實食譜與固定來源版本。A 在 D−1 不同餐別各採用菜單；保留 plan ID、標準菜名／別名與凍結快照摘要。問卷日期可為測試日期，不需等待一天。
2. A 在 D 生成時，確認 D−1 所有餐別已採用標準菜名均排除，別名也不能繞過；僅查看、拒絕、失敗工作、B 家庭與 D−2 資料不得被當成 D−1 已採用菜。
3. 明確指定其中一菜時，只豁免該指定菜，其餘前日排除仍生效；再驗當日其他餐別排除與同餐別重新規劃的既有政策。
4. 預覽／生成後、採用前再新增衝突歷史，採用必須重查並報 PREVIOUS_DAY_DISH_CONFLICT。正常採用同 idempotency key 重送只產生一筆，變更 payload 不得誤認同筆；失敗交易不能留下半筆快照或事件。
5. 在 UI 及 API 查 D−1、D 兩日歷史與 trend；顯示採用時版本、份量、各人營養、成本／價格例外與 AI 標記。之後發布或回復來源／價格版本，歷史快照摘要仍一致。
6. 對 D 的有效採用開啟 Sim 真實 catalog，完成模擬結帳，核對同一 plan／handoff／order 與 Admin 確認事件。重送 checkout 不增加第二筆訂單；跨家庭／失效 token 被拒絕。模擬結帳是測試交易，不得描述為外部真付款。

## 接續既有成果，但重新驗證

前分支依據位於外層 `../.worktrees/1111_update/docs/1111_update/ACCEPTANCE.md`。該報告記載真食譜 R0036 生成→採用→Sim→Admin、真照片番茄辨識後使用者確認 FOOD_ID、123 筆 T11 索引、飲食小幫手 11 篇文章／52 chunks 的串流回答，以及業務與兩知識庫還原演練。這些應接續，不能再次誤報為完全不存在；其 port、DB、job／plan ID 與測試通過數也**不能套用到 V2**。它沒有證明 T16 完整兩日驗收、YOLO 品質 gate、T14 真 AI 菜單、完整語料或乾淨電腦安裝全部通過。

資料庫原食譜來源 hash 為 `dd4f75c1377299fdef858c98d8625cfbdee1e21dd7699f3f78e210048cf0289b`；同名 `data/recipe_dataset_1103_V3.xlsx` 為 `6599f53b16a6b4bfc7f8aed4a44fd10758cf46b8d96a744faa36eefe56d43908`。前者原始工作簿未找到，portable bundle 保留的是 hash 固定的 SQL 來源與逐列證據；歷史 source_uri 不承諾檔案現存。必須保留兩個 distinct names 與 36 項步驟編輯差異，不用同名工作簿重匯覆蓋。

舊分支四項 recipe-derived-only blocked FOOD 為 `FOOD000312`、`FOOD000808`、`FOOD001170`、`FOOD001682`。補齊前需在 UI 明確指出不可算原因與可採取動作，不能自行批准來源或以0隱藏。T11 原來源 manifest 的未匯入旗標是歷史來源階段資訊，運行中的 active index 狀態應直接檢查；同樣不能用它否定已完成的索引。

## 優先阻擋與交付順序

| 優先 | 阻擋／風險 | 完成證據 |
| --- | --- | --- |
| P0 | 獨立 V2 連線、migration、應用角色與所有服務固定資料版本 | API／worker／scheduler 實際連線核對；新庫初始化逐表 hash／筆數；原庫未改。 |
| P0 | T14 AI line 尚無可用的來源綁定計算路徑（盤點時） | 唯一來源解析、ambiguity／版本隔離測試，以及真 AI E2E；不能僅提供 API key。 |
| P0 | 真價格資料與 Sim 真實 checkout 模式 | 抓取／核准／計算／catalog 同來源；`SIM_MARKET_REAL_CHECKOUT_ENABLED=true`，缺價明示，沒有 DEMO_PACKS 假成功。 |
| P0 | T16 兩日歷史與 T15 完整決策未有本分支證據 | 按上述六步留下結果、快照及 Admin 同筆事件；重送／隔離／回復皆驗。 |
| P1 | T11 可檢索來源缺合格營養事實與原文引用 | 審核後 nutrition_fact／evidence_quote、版本化重建索引與預覽／正式一致；缺證據時正確拒答。 |
| P1 | YOLO 品質驗收尚不能由現有整體數字判定通過 | 無洩漏資料集、指定類 macro metrics／IoU／逐類樣本與真照片確認流程。 |
| P1 | 四項營養來源缺口、batch 語意及新發布食譜綁定 | 保留未知，逐項有來源／blocked 原因；預覽與正式一致，新發布／回復有版本證據。 |
| P1 | 標配小幫手依賴與長串流穩定性 | 獨立 pgvector、embedding／reranker／Ollama 模型、active index 與兩核准來源實查；有引用回答及證據不足拒答；慢回應與依賴失效可恢復。 |
| P1 | 完整交付包與乾淨機重現 | Install／Start／Stop／Verify 對缺 archive／weights 明確失敗；備份還原到新目標；乾淨機實裝／重啟記錄。尚未執行就列待驗，不宣稱離線全包已完成。 |

執行順序為：隔離與來源固定 → 真資料預覽／正式 DB 計算一致 → 價格／Sim／Admin → T15／T16 兩日完整操作 → T14 AI 路徑與三項標配驗收 → 安裝／重啟／備份還原。可並行準備證據與模型，但不得用後續功能啟動成功掩蓋前置資料版本或權限未核對。

## 後續接線查核（同日）

- Admin 驗證不是只有 CLI：`backend/app/api/management.py` 支援 `validations?run_now=true` 及 `/tasks/{tid}/run`，`admin-dashboard/src/components/management/Proposal.tsx` 已使用立即驗證。無需為此新增 polling worker；若外部呼叫者省略 run_now，則任務會排隊等明確執行。執行仍核對原申請者權限、修訂與提案狀態。
- 單一 `app.jobs.scheduler` 已同時負責失聯／排隊工作回收、日誌及 T02 日排程。本輪在 `pricing_scheduler.py` 增加 T02 專用 `CRAWLER_DATABASE_URL`（或 create_service 的 price_database_url）；未設定保留舊預設，明設空值拒絕。此 URL 應使用同一隔離業務庫的 crawler 角色，而 jobs 保留 app DATABASE_URL；不是將業務資料拆至另一個價庫。
- 本輪另外實際執行 `python -m pytest tests/test_price_scheduler_database.py -q --basetemp=.test-runtime/pytest-price-url-20261111-a`，**5 passed**，驗證兩個合成 SQLite 庫不交叉寫入、明確參數優先、舊預設與空值拒絕；這不等於 PostgreSQL 角色或爬蟲實驗通過。首次執行因 sandbox 預設 tmp 目錄無法建立而 4 passed／1 setup error，改用儲存庫內專屬暫存路徑後通過。上述矩陣其餘 runtime 狀態不因此變更。
