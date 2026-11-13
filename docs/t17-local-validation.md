# PR #39 可自行重建的本機驗證

此清單以「是否能取得資源並在自己的電腦重建」劃分，不將所有本機驗證交給另一位同學。Codex／CI 的通過紀錄與使用者電腦的操作證據分別記錄。

下方資料包安裝與 verifier 已用等價的 Linux 指令，在新的隔離 PostgreSQL 容器實際通過：293 道食譜、1,996 筆營養，verifier 回報 `ok=true`；保留原政策的 4 筆缺來源阻擋列。Windows PowerShell 語法與使用者自己的硬體操作仍待在其電腦執行。Git 中 YOLO 權重的 SHA-256 亦已核對符合 manifest，尚未據此宣稱使用者電腦的真實模型推論已通過。

## main 已有的資源

| 資源 | 可測內容 |
| --- | --- |
| `release_data/nutrition-v1/` | 經 hash 核對的 293 道食譜、1,996 筆營養；可安裝到新測試庫並驗證 |
| `inference/models/yolo26s-food41-denoise-veg3-ft-20261108.pt` | 真實 YOLO 推論，可用 CPU；GPU 延遲與容量另外量測 |
| `knowledge_rag/candidates/`、`reviews/` | T11 的 123 筆審核知識，可用下載的 embeddinggemma 重建索引 |
| `assistant_rag/eval/`、schema、程式及規則 | 題庫與客服／檢索邏輯；完整小幫手問答仍需原文語料、索引及模型 |
| 前後端、Compose、requirements／lock 檔與測試 | 套件安裝、建置、API、隔離 PostgreSQL 與合成資料驗證 |

截至此文件建立時，T17 新部署／備份檔案及本輪修正在 PR #39 分支，尚未進 main。驗證 T17 請取 `claude/t17-deploy-backup`；合併後才改以包含修正的 main 提交測試。

## 先做不需要 GPU 的測試

以下為 Windows PowerShell 範例。需要 Git、Python 3.12 與 Docker Desktop；前端建置另需 Node.js。其他作業系統可使用相同 Python／Docker 指令，調整 venv 路徑與環境變數語法。

```powershell
git clone --branch claude/t17-deploy-backup https://github.com/yun112064/overcooked.git overcooked-pr39-check
cd overcooked-pr39-check
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

docker run -d --name overcooked-pr39-check-db -e POSTGRES_PASSWORD=pr39_check_only -e POSTGRES_DB=meal_planner_1102_v3_local_test -p 127.0.0.1:55439:5432 pgvector/pgvector:0.8.6-pg17
docker exec overcooked-pr39-check-db pg_isready -U postgres -d meal_planner_1102_v3_local_test
```

等 `pg_isready` 回報接受連線後，再標記這個新建立的拋棄式資料庫並執行測試。若容器名稱或埠已被使用，先查明用途並調整測試名稱／埠，不刪除或沿用不明的現有庫。範例密碼只供此本機隔離測試容器使用。

```powershell
docker exec overcooked-pr39-check-db psql -U postgres -d postgres -c "COMMENT ON DATABASE meal_planner_1102_v3_local_test IS 'overcooked:disposable:pr39-local'"
$env:DATABASE_URL = 'postgresql+psycopg://postgres:pr39_check_only@127.0.0.1:55439/meal_planner_1102_v3_local_test'
$env:TEST_DATABASE_URL = $env:DATABASE_URL
$env:BACKUP_PG_CONTAINER = 'overcooked-pr39-check-db'
$env:PROJECT_DATA_ROOT = Join-Path $env:LOCALAPPDATA 'overcooked-pr39-check/data'
$env:PYTHONPATH = 'backend;tests'
.\.venv\Scripts\python.exe -m pytest tests/test_t17_ops.py tests/test_t17_backup_restore.py tests/test_t17_rag_image.py -q
```

預期為 49 項通過；其中備份／還原案例使用這個新測試庫及合成資料，會重建測試 schema，不應指向自己的使用中業務庫。此階段不需同學的資料、NVIDIA GPU、Ollama 或 API 金鑰。完整後端 CI 的重現設定另見 `.github/workflows/t06-ci.yml`（含第二個 T02 拋棄式資料庫）。

## 使用 main 的真實初始資料包

可在同一個新測試容器內建立另一個全新資料庫，檢查真實食譜／營養安裝及受限帳號。此庫與上方會被重建的 pytest 庫分開；不要改 TEST_DATABASE_URL 指向它。

```powershell
$env:INSTALL_DATABASE_ADMIN_URL = 'postgresql+psycopg://postgres:pr39_check_only@127.0.0.1:55439/postgres'
.\.venv\Scripts\python.exe scripts/install_real_nutrition.py --database meal_planner_1102_v3_local_bundle_test --output-env .test-runtime/local-bundle.env
Remove-Item Env:INSTALL_DATABASE_ADMIN_URL
.\.venv\Scripts\python.exe scripts/verify_portable_nutrition.py --env-file .test-runtime/local-bundle.env --require-empty-runtime
```

安裝器拒絕覆寫既有資料庫、角色與輸出檔；失敗時保留現況供檢查。`.test-runtime/local-bundle.env` 是本機秘密檔，不要提交或貼出內容。政策與來源驗證應使用資料包，不重新匯入 Git 中同名但 hash 不同的歷史 Excel。詳見 [可攜式初始資料](portable-nutrition.md)。

## 可再下載或安裝的模型測試

- **YOLO CPU**：依 `inference/requirements.txt` 建立獨立 `.venv-yolo`，設定 `INFERENCE_DEVICE=cpu`、本機 `INFERENCE_TOKEN` 與未使用的埠，執行 `inference/server.py`。用測試蔬菜照片呼叫 `/v1/detect`；健康檢查 `/v1/health` 也需要 Bearer token。權重在 Git，不需重新訓練或向同學索取；CPU 結果不能當作指定 GPU 延遲證據。
- **T11 知識**：自行安裝 Ollama 並下載 `embeddinggemma`，即可在獨立知識庫執行 `knowledge_rag.bootstrap`。所需 123 筆審核資料在 Git。
- **小幫手**：可下載 `qwen3.5:9b-q4_K_M` 與完整 `BAAI/bge-reranker-v2-m3` 模型；另需可用的原文語料與建立好的索引。Ollama 的 CPU 路徑依 RAM／延遲決定可行性；正式 Compose 的 TEI 配置要求相容 NVIDIA GPU，沒有 GPU 時先測其他分項，不將整體驗證一律交出。
- **公開語料重建**：`scripts/assistant-data.ps1` 可重抓與建索引；原文只放 Data Root。新抓內容與原機語料不保證相同，不能直接沿用原評估分數或聲稱完整重現補充 84 篇。

## 可在自己的電腦再做的操作

自己的 OS／硬體符合條件時，可測服務正常／離線／恢復、瀏覽器操作、快速通道外網／POST 串流、自己的測試磁碟備份還原，以及 Windows 排程。完整 `prod-up.ps1` 需要符合其 Windows、GPU、模型與資料前置條件；這不影響先執行上方的無 GPU 測試。

僅下列項目需要另一位同學：原有使用者／歷史／照片／價格備份、完整舊語料及補充來源紀錄，以及指定正式機的 GPU／容量、正式磁碟／排程／網路驗收。完整分項與證據欄位見 [驗收交接清單](t17-machine-handoff.md)。
