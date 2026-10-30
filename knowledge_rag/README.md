# T11：結構化食譜查詢與地端營養知識RAG

更新：2026-10-30。程式來源版本為G5-T11 `a84b68c`，本次新增123筆網頁／論文知識、食材ID對照及逐筆來源審核結果。沒有新增T11測試程式、測試資料、原始驗收輸出或模型評比檔。

## 分支基底與合併順序

`codex/t11`接續組內既有`feature/t07-t10`（`bae1bfa876468f24159ffcdc4e4776565ccc4832`）。這些前置功能提供T01食譜表、T07家庭權限、T08問卷、T09及T10工作／額度服務；目前main尚未整合此前置分支。

建議先將前置分支整合到main，再合併T11。本次不合併main、不更新其他人的分支，也不重做前置Ticket。相對基底只新增或修改本文列出的T11檔案；基底原有測試與模型檔保持原狀，沒有在T11差異中新增、刪除或重寫。

## 功能與邊界

- 食譜：以結構化欄位查詢合格資料庫菜，處理指定菜／別名、禁忌、偏好排序、菜色組合、同家庭前一日所有餐別已採用菜排除及明確指定例外。不做食譜向量搜尋。
- 歷史：提供最小已採用快照與採用前重查契約；T16／19接續真實採用交易及次日生成整合。
- 知識：最終食材ID限制已審核資料，再使用Ollama embeddinggemma＋pgvector檢索。資料與索引有獨立版本，回傳知識ID、片段、來源、條件及當次快照。
- 狀態：正常查無為not_found，可省略知識；服務故障為unavailable，不偽裝成零筆。配方食材改動後重查，引用依當次快照核對。
- T12營養、T14菜單／營養說明生成、T16及T19不在本分支實作範圍。

## 啟動前置

需要專案既有Python 3.12依賴、業務PostgreSQL，以及團隊電腦上的PostgreSQL＋pgvector與Ollama。先在業務庫執行`alembic -c alembic.ini upgrade head`，建立T11的adopted_meal_plans快照表。

地端知識庫使用獨立768維資料庫，不與業務庫或舊1536維知識表混用。設定實際環境值，密碼與token不要放進Git：

```powershell
$env:KNOWLEDGE_DATABASE_URL='postgresql://<帳號>:<密碼>@127.0.0.1:5432/<知識庫>'
$env:OLLAMA_URL='http://127.0.0.1:11434'
$env:PROJECT_DATA_ROOT='<可寫入的資料目錄>'
$env:KNOWLEDGE_SERVICE_TOKEN='<團隊服務token>'
python -m knowledge_rag.admin init
python -m uvicorn knowledge_rag.server:create_app --factory --host 127.0.0.1 --port 8091
```

Ollama須已安裝embeddinggemma；程式不下載模型、不自動改走付費API。服務固定模型digest、文件／查詢模板與768維；更換模型digest或維度後需重建相應索引。

雲端backend／worker設定`KNOWLEDGE_PROVIDER=ollama`、`KNOWLEDGE_SERVICE_URL`及`KNOWLEDGE_SERVICE_TOKEN`。實際跨網連線須使用既有驗證及加密入口，資料庫不公開。舊OpenAI轉接保留明確選用及T10費用控制，非地端模式必需。

## 自行新增或修改食材知識

食材ID依`sources/All_Food_ID_1018.xlsx`的`All`工作表A欄`Food_ID`與C欄`標準食材名稱`，不以B欄原始名稱或別名取代C欄。同ID可能有不同生熟／料理列，仍須保留知識適用條件。目前匯入器驗證ID格式，不會自動載入此對照表核對名稱。

目前維護來源為`sources/RAG食材知識來源_網頁及論文_ID對齊版.xlsx`，123筆（網頁103筆、論文20筆）涵蓋76種食材；韭菜ID已修正為`FOOD000687`，123筆ID／名稱均與對照表一致。閱讀用整合表的表頭在第8列，不能直接交給既有Excel匯入器；已轉為`candidates/web_paper_2026-10-30.json`，可由專案根目錄預檢：

```powershell
python -m knowledge_rag.admin import --file knowledge_rag/candidates/web_paper_2026-10-30.json --dry-run
# 設定實際KNOWLEDGE_DATABASE_URL後才匯入。
python -m knowledge_rag.admin import --file knowledge_rag/candidates/web_paper_2026-10-30.json
```

本輪123筆皆已逐筆核對來源、食材、摘要與適用限制，結果記於`reviews/web_paper_2026-10-30.json`：

| 來源類型 | 符合使用者內容準則 | 待具名專業依據 |
|---|---:|---:|
| 健康醫療網 | 53 | 5 |
| Hello醫師 | 23 | 22 |
| 原始論文 | 20 | 0 |
| 合計 | 96 | 27 |

準則沿用使用者指定：文章有支持該筆主張的具名醫師、營養師等專業人士即可採用，不額外要求每筆衛教都有人體試驗。Hello醫師頁尾具名醫學審稿可作為專業署名依據；僅寫「Hello醫師資料查核」或同文醫師談其他主題，不視為符合。本輪論文以具名醫學／營養研究者與原始摘要或全文對照，保留族群、品種、份量、時間與未顯著結果；不將相關性、生物標記或單組前後結果當成治療保證。這是Codex依使用者準則的來源審核，不冒稱使用者逐筆親閱或專業人士認證本系統。

27筆待補依據：健康醫療網的萵苣、毛豆、草莓、虱目魚肚、雞胸肉；Hello醫師的水蓮菜、福山萵苣、玉米筍、白芝麻、萵苣、玉米、白蘿蔔、筊白筍、秋葵、四季豆、苦瓜、絲瓜、櫛瓜、青蔥、奇亞籽、酪梨、百香果、檸檬、新鮮檸檬、鱸魚、青江菜、毛豆。來源內容可找到，但未確認支持該筆主張的具名專業依據，故不標approved。

新版`source_version=web-paper-reviewed-2026-10-30-r2`，原始Excel及原欄位／列號保留追溯；缺逐字原文時`excerpt`明標「整理摘要（非逐字原文）」。同食材多篇文章以原知識ID加來源文章ID區分；韭菜無原知識ID，以食材ID加文章ID編號。`current-source.json`只記錄維護來源，不會切換執行中資料庫或索引。

來源內容審核與資料庫發布分開：96筆內容符合不等於已進正式庫；目前123筆匯入狀態均為pending，未連正式知識庫、未執行review核准／建索引，來源表也未附完整使用授權。本次預檢123筆通過，不宣稱實際向量檢索、索引發布或完整G07通過。匯入不自動刪除不同ID的舊知識，正式替換須核對舊資料處理方式。

Excel及JSON共用通用匯入模組，不綁定原58筆。實際資料庫、向量、模型權重另行管理；本分支不提供預先核准的種子資料。新增資料無須改動T11檢索程式。

Excel工作表命名`食材知識`或`knowledge`，第一列使用下列欄名；單一工作表也可使用其他名稱：

```text
知識ID	食材ID	來源版本	知識內容	原文片段	來源網址	使用條件	適用條件
```

每列一筆，必填值使用文字、不使用公式。可增加食材名稱、專業人士姓名／身分、文章標題、原文定位及證據類型。食材ID沿用系統標準FOOD加6位數；全新食材須先完成標準ID對映。

```powershell
# 僅檢查檔案，不連資料庫或模型。
python -m knowledge_rag.admin import --file my-knowledge.xlsx --dry-run
# 匯入；新增資料一律pending，不採信Excel的approved欄位。
python -m knowledge_rag.admin import --file my-knowledge.xlsx
# 實際核對內容、專業人士與使用依據後，逐筆核准。
python -m knowledge_rag.admin review --knowledge-id '<知識ID>' --source-version '<來源版本>' --reviewer '<實際核對者>' --note '<核對依據>' --approve
# 本機已安裝embeddinggemma，使用新的索引版本發布。
python -m knowledge_rag.admin index --index-version '<新索引版本>'
```

JSON使用`{"records": [...]}`；英文必填欄位為knowledge_id、ingredient_id、source_version、excerpt、source_url、rights、conditions，建議同時保留fact_zh知識摘要。

整份重匯時，相同ID／版本且內容未變會略過，既有審核狀態保留。修改知識必須換新的source_version；同版內容不同會整批回滾。新pending版本會先隱藏該知識舊版，完成審核與新索引後回傳新版；其他知識仍可查。新查詢使用active索引，已固定的索引／結果快照不會被偷偷改寫。新增資料不需要重訓模型或修改檢索程式。

## 提供其他Ticket的介面

| 入口 | 用途 |
|---|---|
| `GET /meal-requests/{id}/recipes` | 讀已存問卷、家庭權限及伺服器禁忌，回傳資料庫菜單／候選、原因、input_version及fallback_eligible |
| `retrieve_database_menu(...)` | T11結構化食譜服務；供T14消費回退原因，不在此執行生成 |
| `retrieve_for_job(...)` | 最終配方食材確定後取得知識；地端模式不發付費呼叫 |
| `record_adopted(...)`、`recheck_previous_day(...)` | T16採用交易的快照寫入與歷史重查接縫 |
| `verify_citations(...)` | 依實際檢索快照核對知識ID、食材與版本，重建來源，不接受生成端任意偽造網址 |
| `/v1/retrieve`、`/v1/search` | 受Bearer驗證的知識服務；前者在地端向量化，後者保留舊向量輸入契約 |

## 驗證摘要與未包含項目

本次組別匯出副本已另行驗證：直接載入本分支的app／knowledge_rag程式、使用本分支Alembic遷移，從原工作區執行完整190項後端測試，全部通過、0失敗、0略過。測試程式及輸出只留原工作區。沿用來源專案Pyright basic設定檢查為0 errors／0 warnings，語法編譯通過。

來源版本本機完整後端190項通過，包含72項T11測試與7項新增知識維護測試；23個固定食譜情境包含於T11測試中，不重複加總。Pyright及語法檢查通過；來源版本GitHub CI的後端、前端及推論測試皆成功：[a84b68c CI](https://github.com/queena17chao/G5-T11/actions/runs/40559151123)。

實際工作簿23個食譜案例通過；embeddinggemma＋pgvector＋HTTP的真實知識子集13項檢查及20個暖機請求通過，5並行時p95約2.746秒，付費API呼叫0。這是該次本機實測，不表示跨雲容量、完整月費或全語料品質驗收。

| Ticket11原始驗收條件 | 結果摘要 |
|---|---|
| 至少20個固定食譜情境 | 23個固定情境符合預期 |
| 同家庭昨日全部餐別、標準菜色／別名與指定例外 | 測試通過；不讀其他家庭或未採用紀錄 |
| 資料庫菜單、無解回退原因與資料庫故障分開 | 測試通過 |
| 最終食材＋approved限定、pgvector、模型／索引固定及向量額度 | 契約及實際地端串接通過 |
| 查無／故障分開、錯食材／未審核／虛構引用／換食材 | 測試通過 |
| 已採用快照與採用前重查，可先用測試紀錄 | 測試通過；真實採用UI由T16／19接續 |

歷史58筆來源依使用者核准的具名專業人士準則核對：53筆內容符合、5筆待補署名／身分。當次僅1筆來源與使用條件均確認的新版本在隔離庫核准，其餘57筆未進可用索引。舊批次審核檔不隨本分支發布；歷史索引驗證結果不可套用到本次123筆新版，不宣稱全量G07或所有專案Gate通過。

網站computer-use未完成，原始阻礙保留於來源專案；不冒稱UI驗收通過，不新增T11以外的網站或G06跨雲部署門檻。測試程式、測試資料、驗收JSON／log、暫存執行環境、模型評比及模型權重均未加入本次T11差異。

組別分支的CI沿用既有workflow（目前僅main推送與PR觸發），本次不為T11改動CI或上傳測試。最終合併時仍由整合者依實際main版本執行整合檢查。

## 檔案用途

| 檔案 | 用途 |
|---|---|
| `backend/app/db/models.py` | 新增已採用餐食快照資料模型 |
| `migrations/versions/a1c4e7f20b39_adopted_meal_plans.py` | 建立已採用餐食資料表與約束 |
| `backend/app/services/recipe_import.py` | 統一食譜名稱異體字正規化 |
| `backend/app/services/meal_history.py` | 提供昨日菜色查詢與採用前歷史重查 |
| `backend/app/services/recipe_retrieval.py` | 實作結構化食譜選菜與回退原因 |
| `backend/app/core/config.py` | 加入地端知識服務與明確舊供應者設定 |
| `backend/app/core/logging_config.py` | 納入知識檢索失敗的安全日誌事件 |
| `config/model_pricing.json` | 登錄舊向量供應者的預留與結算價格 |
| `backend/app/services/knowledge.py` | 封裝知識HTTP檢索與引用快照驗證 |
| `backend/app/api/internal_knowledge.py` | 提供受驗證且受T10額度控制的舊向量化端點 |
| `backend/app/api/meal_requests.py` | 加入已存問卷的食譜預覽API |
| `backend/app/main.py` | 註冊T11內部知識服務路由 |
| `knowledge_rag/__init__.py` | 建立獨立地端知識套件入口 |
| `knowledge_rag/schema.sql` | 定義版本化知識與pgvector索引結構 |
| `knowledge_rag/store.py` | 管理知識版本審核與原子索引發布 |
| `knowledge_rag/local_embedding.py` | 封裝固定digest的embeddinggemma向量化 |
| `knowledge_rag/importers.py` | 提供通用Excel與JSON維護檔讀取 |
| `knowledge_rag/admin.py` | 提供匯入審核與建索引管理命令 |
| `knowledge_rag/server.py` | 提供具Bearer驗證的地端檢索服務 |
| `.env.example` | 補上T11知識服務設定範例 |
| `compose.yaml` | 傳入T11知識服務連線設定 |
| `spec.md` | 同步T11地端RAG與使用者核准的來源準則 |
| `architecture.md` | 對齊地端知識檢索責任與版本邊界 |
| `tickets/11-recipe-retrieval.md` | 整理Ticket11交付範圍與原始六條驗收 |
| `knowledge_rag/README.md` | T11部署、維護、跨Ticket介面、驗證摘要與分支整合說明 |
| `knowledge_rag/sources/All_Food_ID_1018.xlsx` | A欄食材ID與C欄標準名稱的維護對照來源 |
| `knowledge_rag/sources/RAG食材知識來源_網頁及論文_ID對齊版.xlsx` | 123筆網頁／論文知識的閱讀用來源表 |
| `knowledge_rag/candidates/web_paper_2026-10-30.json` | T11可匯入的123筆新版知識，含專業署名與內容審核判定 |
| `knowledge_rag/reviews/web_paper_2026-10-30.json` | 逐筆來源內容判定、專業署名、原文定位及研究限制，不冒充索引核准 |
| `knowledge_rag/current-source.json` | 目前維護來源的位置、版本與尚未匯入狀態，不控制執行中索引 |
| `README.md` | 組別原有模組表的T11文件入口 |
