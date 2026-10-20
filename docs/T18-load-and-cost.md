# T18 五人負載與每月總費用驗證：工具與記錄範本

> **狀態：ticket 18 尚未驗證。**
> 這份文件和 `scripts/load/` 只是可重現的量測工具與空白記錄表。ticket 18 的驗收（AC21、AC25、AC28／G06）必須在真實部署環境量測：雲端網站加團隊電腦辨識／RAG 的實際跨節點連線，並使用真實報價。這些量測都還沒做。下面的表格是空的，G06 結論為「未驗證」。dry-run 與單元測試的數字不能當作驗收證據。

覆蓋：R11、G06；AC21、AC25、AC28。前置：T09、T16、T17（外網 HTTPS 部署與備份還原）。

## 工具一覽

| 檔案 | 用途 |
|---|---|
| `scripts/load/five_user_load.py` | 五人並行負載腳本（httpx＋asyncio），輸出 JSON＋Markdown 報告 |
| `scripts/load/load_report.py` | p95（nearest-rank）、AC21 門檻判定、環境資料完整性、報告產生 |
| `scripts/load/fake_server.py` | dry-run 用的程序內假 API，只用來測試腳本本身 |
| `scripts/load/scenario.example.json` | 負載情境範本：成員、資料庫菜單問卷、AI 回退問卷、辨識照片 |
| `scripts/load/environment.example.json` | 必填的環境資料欄位：機型、模型、記憶體、輸入、來源狀態、團隊電腦／網路狀態 |
| `scripts/load/monthly_cost.py` | 每月總費用試算（Decimal），檢查 NT$2,000 上限 |
| `scripts/load/quote.example.json` | 真實報價範本，項目含主機、資料、備份、模型、向量化、稅率、匯率 |
| `scripts/load/spend_guard.py` | 驗證多人並行加重試不能繞過 T10 預留；只用假的付費呼叫 |
| `tests/test_t18_*.py` | 上述工具的測試 |

報告預設寫到 `scripts/load/results/`。這個目錄已 gitignore。正式證據請另外整理後提交。

## 不可變更的規則

- 門檻固定為：一般 API p95 ≤ 1 秒；暖機辨識端到端 p95 ≤ 10 秒；正常外部來源下，生成含排隊 p95 ≤ 90 秒。
  - `--threshold` 只能把門檻調得更嚴。
  - 想放寬門檻或新增門檻名稱時，腳本會拒絕執行（exit 2）。
- 月上限 NT$2,000 只能調低。`monthly_cost.calculate(cap_twd>2000)` 會丟出 `CapRaiseRefused`。
- 量測未達標時：不得降低門檻，也不得自動升級到超額方案。產品條件若要變更，必須回到規劃。
- 報價缺金額、稅率或匯率時，結果為 `incomplete`，缺值不當作 0。未列在 `config/model_pricing.json` 的模型一律拒絕計價。
- 下列情況下 G06 一律為 `not_verified`：dry-run、環境資料缺任一欄、任一類別樣本數少於 20、並行人數少於 5。

## 1. 五人負載（AC21）

### 腳本走的流程

每位使用者各用一個測試帳號和測試家庭。每一輪開始時，五人用 barrier 對齊後同時出發，依序執行：

1. 登入／家庭／成員（`/auth/*`、`/household`、`/members`）
2. 問卷建立與讀取（`/meal-requests`）
3. 暖機辨識：每次執行只做一次，由使用者 0 送出，不計入 p95。之後每輪一次辨識，從上傳到 API 回應，計入 `recognition_warm`；接著確認食材種類。
4. 前一日歷史讀取：`GET /meal-plans?date=<餐食日期-1>`
5. 資料庫菜單生成（結構化查詢）
   - 計時從 `POST .../submit` 開始，一路輪詢 `/jobs/{id}` 到工作結束，所以包含排隊時間。
   - 記入 `generation`，並標註 mode。
6. 自動 AI 回退生成
   - 使用 `fallback_request`。結果模式不是 `ai_generated_menu` 時，記錄問題 `FALLBACK_NOT_TRIGGERED`。
7. 結果讀取：記錄知識 RAG 狀態（`knowledge.status`）、前一日比對、採用資格。
8. 採用：`POST /meal-plans`。後端在同一交易內重驗前一日歷史。
9. 依日期查歷史，並讀取採用明細。

第 N 輪的餐食日期等於 `base_meal_date` 加 N 天，所以前一輪採用的菜，正好成為下一輪的「昨日」，會實際觸發前一日排除與重驗。

一般 API 類別（`api`）包含所有非辨識、非生成的請求，其中也包括輪詢請求。報告另外列出各步驟的 p95，以及兩種生成模式各自的 p95。

### 執行步驟（人工）

1. 確認 T17 已部署到外網 HTTPS，團隊電腦的 YOLO 與知識 RAG 服務在線。各服務版本記錄在環境檔。
2. 複製並填寫情境檔。
   - `fallback_request` 必須是在該部署的實際食譜庫中「資料庫無可行組合」的輸入。
   - `recognition.image_files` 指向實際照片，路徑相對於情境檔。
3. 複製並填寫 `environment.example.json`，所有欄位都要填（見下表）。
4. 預算：負載會產生真實的 OpenAI 呼叫，全部經過 T10 預留與結算。執行前先用 `python -m app.jobs.admin budget` 確認剩餘額度。
5. 執行（repo 根目錄）：

       .venv\Scripts\python.exe scripts\load\five_user_load.py --base-url https://<網域>/api --origin https://<網域> --scenario <情境檔> --environment <環境檔> --iterations 6

   - 預設每人 6 輪。5 人共產生 30 筆生成、30 筆辨識樣本，每類至少需要 20 筆。
   - 帳號：未提供 `--accounts` 時，腳本自動註冊 `t18-load-<run>-<n>@example.test`。密碼取自環境變數 `LOAD_TEST_PASSWORD`；未設定時隨機產生，不輸出。測試結束後請刪除這些測試帳號。
   - exit code：0 表示 G06 量測通過；1 表示未通過或未驗證；2 表示參數被拒。
6. 要記錄團隊電腦離線再恢復的情境（R11），可在執行期間人工停用服務，並把觀察寫入下表。腳本會把辨識非成功狀態記為問題 `RECOGNITION_NOT_SUCCEEDED`。

只驗證腳本本身（不連任何服務）：

    .venv\Scripts\python.exe scripts\load\five_user_load.py --dry-run

### 環境資料（AC21 要求記錄；空白＝未驗證）

| 欄位 | 值 |
|---|---|
| 雲端主機供應商／機型／CPU／記憶體／區域 | |
| 團隊電腦機型／CPU／GPU／記憶體／OS | |
| 團隊電腦狀態（在線、溫度、負載） | |
| 雲端↔團隊電腦連線方式／RTT／網路狀態 | |
| 生成模型／向量化模型／YOLO 模型與是否暖機 | |
| 情境檔與照片（含 sha256）／資料集版本 | |
| 價格來源／知識 RAG／OpenAI／辨識服務狀態 | |
| 部署網址／git commit／pricing_version | |

### 量測結果（空白＝未驗證）

| 類別 | 樣本數 | 錯誤 | p95 (s) | 門檻 (s) | 判定 |
|---|---|---|---|---|---|
| 一般 API | | | | 1 | 未驗證 |
| 暖機辨識端到端 | | | | 10 | 未驗證 |
| 生成含排隊（資料庫菜單） | | | | 90 | 未驗證 |
| 生成含排隊（AI 回退） | | | | 90 | 未驗證 |

| 兩端容量 | 雲端 | 團隊電腦 |
|---|---|---|
| 尖峰記憶體 | | |
| 尖峰 CPU／GPU | | |
| 離線→恢復觀察 | | |

## 2. 每月總費用（AC25）

1. 複製 `scripts/load/quote.example.json`，填入真實報價。
   - 金額用字串表示。
   - 每一項都要附來源與查核日。
   - 稅率必填，免稅填 `"0"`。
   - 匯率填 `fx.USD_TWD`。
   - 模型與向量化依每月預估 token 數計算，價格取自 `config/model_pricing.json`，與 T10 預留用的是同一份價格表。
   - 預估 token 數必須涵蓋菜單生成、自動 AI 回退、知識說明與知識向量化，不可沿用食譜 embedding 的費用假設。
2. 執行：

       .venv\Scripts\python.exe scripts\load\monthly_cost.py <報價檔>

   - 輸出 `within_cap`、`over_cap` 或 `incomplete`，以及合計、各類別小計、固定費用、剩餘額度。
3. 把 `.env` 的 `COMMITTED_SERVICES_TWD`（T10 `committed_services_twd`）設成不低於「固定費用」。這樣 T10 預留的可用額度，就只剩模型與向量化的部分。

| 類別 | 品項 | 月費 NT$（含稅） | 報價來源／查核日 |
|---|---|---|---|
| 主機 | | | |
| 資料（DB／儲存／流量） | | | |
| 備份 | | | |
| 模型（生成＋回退＋說明） | | | |
| 向量化 | | | |
| **合計（上限 2,000）** | | | **未驗證** |

## 3. 多人加重試不繞過預留（AC25）

`scripts/load/spend_guard.py` 直接使用 T10 `app.services.spend`。

- 做法：5 個執行緒並行呼叫，每個呼叫輪流套用以下四種情境：
  - 正常結算
  - 確定未計費後換鍵重試
  - 已結算後同鍵重送
  - 結果不明後同鍵重送
- 檢查項目：
  - 帳本（固定＋預留＋已花）任何時刻都不超過上限
  - 同鍵重送全部被拒
  - 會走到額度不足
  - 帳本與預留紀錄一致
  - 每個呼叫鍵只有一筆預留
- 隔離：帳本寫在合成月份 2099-12，結束後清除。資料庫名稱為 `*_dev` 或 `*_prod` 時拒絕執行。

執行（測試或 staging 資料庫）：

    DATABASE_URL=<同下> PYTHONPATH=backend .venv/Scripts/python.exe scripts/load/spend_guard.py --database-url <DB URL>

`tests/test_t18_spend_guard.py` 另外驗證：額度用盡時送出生成會回 503 `SERVICE_BUDGET_EXHAUSTED`，而 `/meal-plans` 歷史與問卷讀取仍回 200。

| 檢查 | 結果（實機 staging） |
|---|---|
| ledger_never_over_limit | 未驗證 |
| duplicate_call_key_refused | 未驗證 |
| budget_exhausted_reached | 未驗證 |
| ledger_matches_reservations | 未驗證 |
| one_reservation_per_call_key | 未驗證 |
| 額度不足仍可查歷史（實機） | 未驗證 |

## 4. G06 結論與問題修正（AC28）

| 項目 | 證據檔（報告 JSON／MD、版本） | 結論 |
|---|---|---|
| 五人負載 | | 未驗證 |
| 兩端容量／離線恢復 | | 未驗證 |
| 月費試算 | | 未驗證 |
| 預留防繞過 | | 未驗證 |
| 問題修正後重測 | | 未驗證 |

任一項未通過時，必須阻擋對應的真實功能交付，並回到規劃。不可放寬門檻、提高上限，也不可自動升級方案。

## 測試

    PYTHONPATH=backend DATABASE_URL=... TEST_DATABASE_URL=...meal_planner_t18 .venv/Scripts/python.exe -m unittest discover -s tests -p 'test_t18_*.py'

- 不需資料庫：`test_t18_load_report`、`test_t18_load_runner`（dry-run 假伺服器）、`test_t18_monthly_cost`。
- 需要資料庫：
  - `test_t18_spend_guard`：預留防繞過、額度不足仍可查歷史。
  - `test_t18_api_contract`：負載腳本的請求能被真實後端 API 接受。使用程序內 ASGI 與假辨識器，沒有 worker，所以生成會逾時，記為失敗樣本。

測試不呼叫任何付費 API。
