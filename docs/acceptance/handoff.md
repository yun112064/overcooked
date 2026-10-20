# 操作、維護與展示交接

- 適用版本：整合分支 `feature/t11-t20` @ `870230f`（2026-10-20）
- **目前狀態：可在本機開發環境展示；尚未部署到正式環境，MVP 尚未完成。** 各驗收項狀態見 [matrix.md](matrix.md)，限制見 [limitations.md](limitations.md)。
- 本文件只寫流程與指令，詳細參數以各模組文件為準。文件中**不含任何秘密值**；所有秘密只放在被 `.gitignore` 排除的 `.env*` 檔。

## 0. 先分清楚的四組概念

展示、文件與畫面都必須分開呈現，不能混用：

| 概念 A | 概念 B | 差別 | 程式裡怎麼分 |
|---|---|---|---|
| **資料庫菜單** | **AI 生成菜單** | 資料庫菜單只用 T01 已匯入、合格（`is_eligible`）的真實食譜，不呼叫模型組菜。只有資料庫查無可行方案時，才自動改由 OpenAI 生成，並標示「AI 生成菜單」與回退原因。AI 菜沒有 `recipe_id`、`source = ai`。 | `generation_results.mode` = `database_menu`／`ai_generated_menu`；`fallback_reason` 只在 AI 模式必填（DB CHECK）；畫面 `mode_label` |
| **營養知識** | **TFDA 營養數值** | 熱量、蛋白質等數值一律由 T12 依 TFDA 資料計算，決定能不能採用。營養知識（健康醫療網文章片段）只是附在最終食材旁的說明，**不參與選菜、不改數值、不影響採用**；缺知識只省略說明。 | 數值在 `payload.calculation`；知識在 `payload.knowledge`／`payload.explanation`。知識狀態 `available`／`not_found`／`unavailable` |
| **查無知識** | **知識服務故障** | 查無是「沒有已審核知識」，可省略；故障是團隊電腦關機、tunnel 斷線、驗證失敗等，標 `KNOWLEDGE_UNAVAILABLE`，不能當成零筆。 | `knowledge.status`、`reason` |
| **已採用** | **實際食用** | 「採用」只表示家庭選定這一餐的規劃並保存快照，系統**沒有**記錄實際吃了什麼或吃了多少。歷史頁顯示的是已採用的規劃；營養數值是估算，不是實測攝取。未採用的結果不算歷史。 | `adopted_meal_plans`（`status = adopted`）；沒有任何「已食用」欄位 |

## 1. 取得程式、資料與模型

| 項目 | 取得方式 | 是否在 Git | 注意 |
|---|---|---|---|
| 程式 | `git clone` 本 repo | 是 | — |
| 食譜工作簿 | `data/recipe_dataset_1017.xlsx`（SHA-256 `6bcaca41549572e39bd0fc40d0d216663df6438ae8be50079f92d81aa32c7304`） | 是（已審核版本） | 來源 iCook 愛料理，授權資訊在 `config/recipe_sources.json`。只供本專案使用，不對外散布 |
| 食譜審核紀錄 | `config/recipe_reviews.json`（綁定工作簿雜湊，工作簿一改即失效） | 是 | 審核人姓名待補 |
| 同義食材、免計價食材 | `config/ingredient_synonyms.json`、`config/price_exempt_ingredients.json` | 是 | — |
| TFDA／USDA 營養工作簿 | `nutrition_model/data/source/`，由 `nutrition_model/import_workbook.py` 產生 JSON | 是 | 未重新向官方查證時效 |
| 營養目標規則 | `nutrition_target/rules/hpa-dri8-mvp-0.3.0.json`（雜湊已註冊，`.gitattributes` 禁止換行轉換） | 是 | G03 未通過 |
| YOLO 權重 | `inference/models/`（YOLO26s），選擇依據在 `inference/model_manifest.json` | 是 | AC22 未達標 |
| 模型價格表 | `config/model_pricing.json`（`pricing_version = plan-2026-10-20`） | 是 | 正式上線前要依官方價格重新核對 |
| 全聯價格快照 | 由排程程式每日抓取，存在雲端資料庫 | 否 | 執行輸出放 `outputs/`（已忽略） |
| 健康醫療網知識 | 團隊電腦執行爬蟲後存在團隊電腦知識庫 | **否，也不應放入** | 文章內容受授權限制；備份檔 `knowledge_sources.jsonl` 含片段，只能存放在團隊控管的位置，不可公開或散布 |
| 書面授權證據 | 由使用者存放在團隊保存位置 | **否** | 存放路徑填入 `KNOWLEDGE_AUTHORIZATION_REF`；repo 只記路徑，不放文件本身 |
| 秘密 | 依範本自行建立：`.env.dev`（`start-dev` 自動產生隨機值）、`.env.prod`（`.env.prod.example`）、`deploy/team/.env.team`（`deploy/team/.env.team.example`） | **否** | OpenAI 金鑰、Cloudflare 權杖、資料庫密碼都只放這些檔；不要貼到文件、Issue、聊天或 commit |

## 2. 本機啟動（開發與展示）

需要 Docker Desktop、Python 3.12、Node.js。

1. 在 repo 根目錄雙擊 `start-dev.cmd`（或執行 `scripts\start-dev.ps1`）。
   - 第一次會建立 `.env.dev`（隨機密碼與 token）、`.venv`、資料庫容器 `overcooked-dev-db`，執行 `alembic upgrade head`，資料庫沒有食譜時自動匯入工作簿。
   - 後端、worker、前端、YOLO 在同一個視窗執行；`DEV_SKIP_YOLO=1` 可略過 YOLO（辨識顯示不可用，可手動輸入）。
2. 開 http://localhost:5173 。
3. AI 回退與知識說明需要在 `.env.dev` 設定 `OPENAI_API_KEY`；沒設定時資料庫菜單照常，需要 AI 時會明確失敗。
4. 關閉：雙擊 `stop-dev.cmd`（資料保留）。
5. 測試資料庫連線一律寫 `127.0.0.1`，不要寫 `localhost`（Docker Desktop 在 `::1` 不完成握手，測試會卡住）。

自動化測試（repo 根目錄，`TEST_DATABASE_URL` 指向可清空的測試庫，**不可指向開發或正式資料庫**）：

```sh
DATABASE_URL=<測試庫URL> TEST_DATABASE_URL=<測試庫URL> PYTHONPATH=backend python -m unittest discover -s tests -p 'test_*.py'
python -m unittest discover -s nutrition_target/tests -t .
python -m unittest discover -s nutrition_model
```

需要資料庫的測試在沒有 `TEST_DATABASE_URL` 時會略過；略過**不算**通過。

## 3. 資料更新

### 3.1 食譜（T01）

1. 修改工作簿後，先 dry-run，不寫資料庫：
   `python scripts/import_recipes.py --excel data/recipe_dataset_1017.xlsx --sources config/recipe_sources.json --reviews config/recipe_reviews.json --dry-run --report <報告.json>`
2. 確認報告（合格數、未合格原因）。工作簿雜湊改變後，舊審核紀錄自動失效，**必須重新人工審核並更新 `config/recipe_reviews.json`**，否則新食譜不會合格。
3. 正式匯入（先備份資料庫）：同上指令拿掉 `--dry-run`，加 `--database-url`。重跑冪等；工作簿移除的食譜會被刪除。
4. 細節：[docs/T01-recipe-database.md](../T01-recipe-database.md)。

### 3.2 價格（T02）

- 正式環境由 `scheduler` 每日排程抓全聯小時達公開頁，寫入 append-only 價格快照；同一天的 run key 只執行一次。
- 手動補抓或小量驗證：`python scripts/collect_pxmart_prices.py`（`--limit`、`--batch-size`、`--checkpoint` 續跑，參數見 [docs/T02-pricing.md](../T02-pricing.md)）。
- 規則：每種食材間隔 ≥1.5 秒；遇 401／403／429 或 CAPTCHA 立刻停，不繞過。估價不算已知價格；缺價一律 `null`，不填 0。
- 結果頁的「缺價重試」只重讀資料庫最新快照，不會即時查價。

### 3.3 TFDA 營養資料（T04）

- 更新 `nutrition_model/data/source/食材營養成分表.xlsx` 後執行 `python nutrition_model/import_workbook.py --input <工作簿路徑>`，產生 JSON 與 `workbook_manifest.json`（來源雜湊）。
- 再跑 `python -m unittest discover -s nutrition_model`。
- 熟食對應 `tfda_cooked_mapping.json` 需人工確認。LLM 烹調係數（P01）未核准，正式流程不使用。
- 細節：`nutrition_model/README.md`、[docs/T04-tfda-llm-changes.md](../T04-tfda-llm-changes.md)。

### 3.4 營養知識：抓取 → 審核 → 建索引（團隊電腦）

在團隊電腦 repo 根目錄、`PYTHONPATH=backend` 執行；完整說明見 [docs/knowledge-source-healthnews.md](../knowledge-source-healthnews.md)。

1. 設定 `KNOWLEDGE_DATABASE_URL`（團隊電腦知識庫，`127.0.0.1:55433`）與 `KNOWLEDGE_AUTHORIZATION_REF`（授權證據存放路徑）。沒填授權路徑就不能抓取。
2. 抓取：`python -m knowledge_rag.admin crawl --max-pages 3`（每日新文章）；`crawl --queued`（雲端待補食材優先）。限速 ≥2 秒／請求，遵守 robots.txt。
3. 審核：`list --status pending` → `show <knowledge_id>` → `approve <id> <版本> --reviewer <姓名> --note <理由>` 或 `reject`。只是順帶提到、錯食材、轉載內容授權不明的一律退回。**抓到的內容在審核前絕不會被檢索或顯示。**
4. 建索引：`index build --version idx-<日期>`（每次新版本名稱）。向量化經雲端 T10 額度預留，團隊電腦不直接花錢。
5. 狀態：`status`（待審數、active 索引、佇列）。

> 注意：目前 `crawl --queued`、`index build`、`status` 的佇列數需要直連雲端資料庫（已知問題 #6，違反 T17「資料庫不對外」界線，修正前不可在正式環境開放雲端資料庫連線）。

## 4. 正式部署（Cloudflare Tunnel）

**尚未實際執行。** 依使用者決定（decisions #13）不用 Caddy 處理憑證，改用 Cloudflare Tunnel：雲端主機與團隊電腦都不開任何連入埠。前端映像內部的 Caddy 只在容器內提供靜態檔與轉發 `/api`（decisions #15）。完整步驟：[docs/T17-deployment.md](../T17-deployment.md)。

摘要：

1. **Cloudflare（人工）**：網域交給 Cloudflare；建立 `meal-planner-cloud` tunnel（`<PUBLIC_HOSTNAME>` → `frontend:80`）與 `meal-planner-team` tunnel（`yolo.<網域>` → `127.0.0.1:8765`、`knowledge.<網域>` → `127.0.0.1:8766`）；建立 Access 服務權杖與兩個 Self-hosted 應用程式（Policy 只允許該服務權杖）。
2. **雲端主機**：clone 到 `/opt/meal-planner/code`；建立 Data／Runtime／備份目錄與獨立備份位置；填 `.env.prod`；`python3 -m ops.preflight cloud --env-file .env.prod`（只列變數名稱，不印值）；`python3 -m ops.deploy --env-file .env.prod`（設定檢查 → 部署前備份 → build → `alembic upgrade head` → `up -d`，任一步失敗即停，不做 downgrade）。
3. **團隊電腦**：填 `deploy/team/.env.team`；`python -m ops.preflight team --env-file deploy/team/.env.team`；`docker compose --env-file deploy/team/.env.team -f deploy/team/compose.yaml up -d --build`（知識庫＋RAG）；YOLO 以 `python inference\server.py` 執行（只聽 127.0.0.1）；`cloudflared service install <權杖>` 安裝成 Windows 服務。
4. **驗證**：`https://<PUBLIC_HOSTNAME>/api/health`、`/api/health/db`；不帶 Access 權杖呼叫團隊 hostname 應被擋、只帶 Access 無 Bearer 應 401、兩者都帶才 200。
5. 雲端與團隊電腦之間：Cloudflare HTTPS＋tunnel 加密；兩層驗證（Access 服務權杖＋服務自己的 Bearer token）。團隊電腦離線時辨識與知識顯示「不可用」，菜單、登入、草稿、歷史照常。

## 5. 費用

- 上限：每月總費用 NT$2,000，含主機、網域、備份位置、Cloudflare、OpenAI 生成與向量化。只能調低，不能調高。
- 每次付費呼叫（菜單、說明、向量化）都經 T10 預留與結算；未列價的模型一律拒絕呼叫。
- 查帳：`python -m app.jobs.admin budget`（本月額度帳與未結清呼叫）；`python -m app.jobs.admin reconcile <id> <實際金額> <說明>`（依帳單核對結果不明的呼叫）。
- 月費試算：填 `scripts/load/quote.example.json` 的副本（每項附來源與查核日、稅率、匯率），執行 `python scripts/load/monthly_cost.py <報價檔>`；缺任一值結果為 `incomplete`，不當 0。
- 把 `COMMITTED_SERVICES_TWD` 設成不低於試算的固定費用（目前 1400 只是佔位）。
- **實際報價尚未填寫**（`docs/T17-deployment.md` 第 8 節、`docs/T18-load-and-cost.md` 第 2 節）。

## 6. 日誌

- JSONL 寫在 Data Root 的 `logs/`，API、worker、scheduler 分檔；欄位含時間、等級、事件、來源、訊息，必要時附工作 ID、trace ID、錯誤碼、耗時。
- 每日或單檔 10 MiB 輪替，保留 14 天，總量 200 MiB；`scheduler` 每日 03:30 清理已關閉的舊檔，不動正在寫入或調查中的檔案。
- 不記錄密碼、session、API key、完整健康資料、原始照片、模型原始輸入／輸出。
- 調查問題時以 `job_id`、`trace_id` 搜尋；付費呼叫追查用上方 `admin budget`。
- 正式環境每日 Log 量尚未實測。

## 7. 備份與還原

完整說明：[docs/T17-deployment.md](../T17-deployment.md) 第 5、6 節。

- 備份：`python -m ops.backup cloud` 與 `python -m ops.backup knowledge`（`--mirror` 或 `BACKUP_MIRROR_ROOT` 複製到獨立位置並重新核對校驗碼）。雲端用 systemd timer（`deploy/cloud/meal-planner-backup.*`），團隊電腦用工作排程器每日 03:45。兩處各保留 7 日，最新一份永遠保留。
- 內容：雲端業務庫 dump、Data Root（不含 logs）、來源／規則快照；知識庫 dump、`knowledge_sources.jsonl`、schema 與價格表。每份有 `manifest.json`（校驗碼、git commit、Alembic 版本、筆數）。
- 還原：`python -m ops.restore <備份目錄> --target-database <新資料庫> [--data-target <新目錄>] [--rebuild-from-sources]`。先核對校驗碼；只還原到空資料庫，絕不覆寫；驗證筆數並讀出家庭與已採用餐食。正式切換：停服務 → 還原到新庫 → 舊庫改名保留 → 新庫改名 → 啟動。
- 知識庫只剩來源快照時，`--rebuild-from-sources` 重建紀錄，再用 `index build` 重建索引（只含已審核知識，需付費向量化）。
- 已完成：本機合成資料演練（2026-10-20）。**未完成：正式環境異地還原演練。**

## 8. 展示流程（建議腳本）

在本機開發環境（或正式環境部署後）依序展示，每一步說明「是什麼」與「不是什麼」：

1. **註冊、建立家庭與成員**：設定成人目標（維持／增肌／減脂）、份量 n（0.7／0.85／1／1.15／1.3）、過敏／禁吃；示範未成年不能選增肌／減脂。
2. **當餐問卷**：選餐別、參與成員、便當（預設午餐、可改）、菜數、指定菜、預算；草稿可保存返回。
3. **辨識（團隊電腦在線時）**：上傳照片，每種蔬菜一個可編輯框，可修正、略過、補充；不問重量。可示範關掉 YOLO 後顯示「不可用」、照片保留、改手動輸入。
4. **生成資料庫菜單**：結果頁標示「資料庫菜單」；顯示全家配方、整餐用量與步驟、缺料的採購價／成本價、熱量與蛋白質是否在 ±10% 內。說明營養數值來自 TFDA 計算。
5. **知識說明**：每則顯示「來源：健康醫療網《標題》（日期）」與原文連結。說明這是輔助閱讀，不影響數值與採用。**目前沒有經審核的真實知識，只能以合成資料展示流程，必須口頭聲明。**
6. **自動 AI 回退**：用資料庫找不到可行組合的輸入，結果標示「AI 生成菜單」與原因，沒有詢問同意的對話。
7. **缺值決策**：缺價時明確接受後才可採用並留下標示；缺熱量／蛋白質可查看但不能採用；已知成本超預算不能採用。
8. **採用與歷史**：採用一個結果版本；歷史頁依日期回看。說明「已採用」不等於「已吃」。
9. **昨日去重**：隔天再規劃，昨天採用的菜被排除；明確指定的昨日菜可以重複，其他仍排除。
10. **額度與故障**：額度用盡時新生成被拒但歷史可查；知識服務故障與查無分開顯示。

展示時避免的說法：「AI 已驗證營養」「系統記錄了家人吃了什麼」「知識內容已完整審核」「已可對外上線」。

## 9. 交付時不可包含

- 任何 `.env*`（範本 `*.example` 除外）、權杖、金鑰、資料庫 dump、`outputs/`、`scripts/load/results/`。
- 健康醫療網文章全文或知識庫匯出檔（授權限制）。
- 書面授權文件本身（只交付存放位置的紀錄）。
- 測試帳號密碼（負載測試帳號用完即刪）。
