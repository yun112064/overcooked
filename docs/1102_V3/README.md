# 1102_V3：T01–T13 架構整合

盤點日期：2026-11-03（Asia/Taipei）。基底：`origin/main@007e51dff66069195544468c0ba5f7020cafd303`。

本次交付是**程式架構整合與資料統一規格**。沒有把各分支的資料列合成正式資料庫，沒有啟動 PostgreSQL、Compose、worker、scheduler、YOLO 或知識服務；也沒有執行實際來源匯入、價格抓取或外部模型。合成 SQLite／暫存測試資料與正式來源分開。

## 分支盤點與取捨

已更新遠端參照並檢查下列 15 個分支的 Git tree、差異及相關模組。`t11-t20-demo` 不作為檢查／選檔來源。這是選檔整合，沒有整條 merge 任何來源分支。`1102_V1` 自身含較後期功能，不能因此宣稱 T14–T20 驗收完成。

| 分支 | 固定 SHA 前綴 | 架構及整合決定 |
|---|---|---|
| main | 007e51d | 本機乾淨基底：FastAPI、React、T01／T04 資料模型、T02、T03、T05–T10；既有資料與匯入器原樣保留 |
| 1102_V1 | eca4ff4 | 提供主要 T11 查詢／歷史／RAG、T12 計算、T13 成本、版本固定 ORM、前端結果／歷史元件與工作程序接線；選取程式及結構遷移，不取 SQL／seed／來源修正資料 |
| T02_pricing | f57478f | 食材查價、商品匹配、批次鎖與快照；採用 main／V1 延續版本。額外 reassess 更新指令暫緩，不另啟動查價 |
| T05-yolo26s-spec-change.patch | 5c5496a | YOLO26s 規格修正，已在 main；保留既有 inference HTTP／前處理／manifest，不重新訓練或載入權重 |
| T13_ingredient_costs | 3f9ce9d | Decimal 缺料、整包採購／比例成本；採 V1 的 `meal_cost` 與 T02 快照介面。原 `retail_*` schema、另一組 recipe 建表及匯入管線不併入，以免雙份資料模型 |
| codex/integrate-t11-t12-audit | 00fad2f | 取較新 `nutrition_planning`、Food_ID 來源選擇、精確份量、營養預覽及過敏群組讀取介面；1102 Excel、過敏名單、營養對照／補值資料暫緩 |
| codex/t04-nutrition-module | e205980 | TFDA 熟食優先、生食＋烹調係數、USDA 參考及缺值語意；核心已被 main／V1 延續；舊數值資料保留 main 版本 |
| codex/t11 | 9a76f9f | 結構化食譜、昨日去重、獨立 pgvector 知識庫與來源審核；程式取 V1／稽核後續版，不發布候選知識 |
| codex/t12-baseline-scaling-nutrition | cf3b47c | Fraction 基準對齊、n 倍、T03／T04 接線；採稽核分支後續版，不建立第三套資料表 |
| feature/data-import | b6ad14f | 食譜／營養 Excel 匯入合約與原始來源鏈；main 已含其結構。現有兩個 importer、CLI 及來源審核檔保持不變 |
| feature/t07-t10 | bae1bfa | 登入／家庭／問卷／辨識／背景工作／額度；沿用 main／V1 的整合版，保留家庭授權與冪等契約 |
| integration/food-nutrition | 6a5fc7a | 帶 import_id 的 FOOD 營養資料與 T04 DB adapter，已併入 main；保留其原始來源分表 |
| t07-t10-main | 4b94bda | T07–T10 整合與 YOLO 部署接線，已由 main 延續；不倒退版本 |
| ticket/T03-Zhu | 123872d | 原 `backend/app/nutrition` 規則／CLI；核心已整理到 main 的 `nutrition_target`，不重複安裝舊命名空間 |
| ticket/T06-foundation | d4f139e | API／DB／React／日誌／Compose 基礎，已由 main 延續；保留最新整合結構 |

完整逐檔 blob／分支 SHA／資料候選路徑見 [branch-audit.json](branch-audit.json)。來源選檔與後續本地修改見 [integration-files.json](integration-files.json)。這些紀錄不包含資料庫密碼或來源資料列。

## 整合後架構

```text
frontend/ React + TypeScript + Vite + MUI
  → backend/app/api/ FastAPI，登入／家庭範圍／問卷／辨識／結果
    → services/identity, household, meal_request            T07–T08
    → services/recognition → inference/ HTTP               T05、T09
    → jobs/worker + jobs/spend／admin                       T10
    → recipe_retrieval + meal_history                     T11 結構化 SQL
    → knowledge → knowledge_rag/ HTTP → PostgreSQL pgvector T11 知識
    → meal_calculation + nutrition_planning → T03／T04     T12
    → meal_cost → source_adapters/price_*                  T02、T13
    → app/models/ + app/db/models/ → 單一雲端 ORM／Alembic
```

| Ticket | 現行程式位置 | 此次狀態 |
|---|---|---|
| T01 | `app/models/recipe.py`、原 `recipe_import.py` | 共用結構已接入；來源內容／匯入器凍結 |
| T02 | `app/source_adapters/`、`pricing_scheduler.py` | adapter／匹配／快照架構保留；收集預設關閉 |
| T03 | `nutrition_target/` | 保留 main 的 0.3.0 規則與公式；V1 的 0.4.0 及未成年表暫緩採用 |
| T04 | `nutrition_model/`、`t04_database_nutrition`、`t04_receipt*` | 版本固定、來源與估算分離的介面已接；實際映射及收據資料未載入 |
| T05 | `inference/` | HTTP、降噪及模型 manifest 沿用；不重訓／不重測真實模型 |
| T06 | `main.py`、日誌、Docker、React | 基礎架構及新增服務依賴已接；啟動不自動改 DB |
| T07–T08 | auth／households／members／meal_requests | 保留權限與問卷；補 server-side T03 參與輸入橋接 |
| T09 | recognitions API／RecognitionCapture | 辨識不可用時保留手動輸入 |
| T10 | `app/jobs/`、spend／idempotency | 背景工作與額度介面保留，未啟動程序 |
| T11 | recipe_retrieval／meal_history／knowledge／knowledge_rag | 結構查詢與向量知識分開；候選審核不等於索引發布 |
| T12 | meal_calculation／nutrition_planning／t12_planning | 生熟來源、缺值、±10%、n 倍及營養預覽架構接入；預覽須明確設定 import_id |
| T13 | `app/services/meal_cost.py` | 採購價／比例成本、有效期、幣別與未知價格語意共用 T02 |

V1 的生成、結果、已採用快照及歷史 UI 保留作 T10–T13 接線依賴；本次沒有新增 T14–T20 的驗收範圍。兩個既有 T12 入口仍分別服務生成與獨立營養預覽，沒有宣稱已證明兩條數值路徑等價；其共用資料版本及生熟計算政策需在資料統一時驗證。

## 本次資料邊界

- 原 main 的 `data/`、`nutrition_model/data/`、`nutrition_target/rules/`、既有 `config/` 及八個 importer 檔案保持原樣；[source-freeze.json](source-freeze.json) 逐檔提供 SHA-256 與比對結果。
- 不複製 V1 SQL／seed、稽核分支 1102 Excel／知識候選、營養補值或歷史人工核准。新增的主食、熟食類別、零值及每日額外營養參考設定是**空契約**，沒有食材數值或已核准資料列。
- `staples` 沒有核准選項時不捏造主食；空白補值規則不把未知營養變為 0；高鈉門檻未選定時回傳未知。
- 後端 Docker 啟動使用唯讀 `schema_gate`；PowerShell 開發腳本移除自動遷移／食譜匯入；scheduler 移至 Compose maintenance profile，`.env.example` 預設停用查價及營養模型。
- 不執行 `start-dev.cmd`、`docker compose up`、Alembic upgrade 或資料指令。它們仍可能建立容器／工作資料目錄，待資料方案確認後另行啟動。

後續先依 [SOURCES.md](SOURCES.md) 選定資料來源與版本，再按 [DATABASE.md](DATABASE.md) 統一資料內容。驗證結果與尚未完成項目列在 [VALIDATION.md](VALIDATION.md)。
