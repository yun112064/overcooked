# T17 對外 HTTPS 部署與備份還原

- Ticket：`tickets/17-https-backup-restore.md`；覆蓋 R11、G06（部署部分）、AC24、AC27
- 狀態：程式、設定範本與本機還原演練已完成。**真實雲端主機、網域、Cloudflare tunnel、正式還原演練與實際報價都還沒有做**（見文末清單）。
- **部署完成不代表五人容量已通過**：AC21／G06 的五人跨節點實測是另一項工作，本文件沒有任何容量證據。

> **與 ticket 文字的差異（2026-10-20 使用者決定）**：ticket 與 Spec 寫「Compose＋Caddy」。使用者認為 Caddy 的憑證處理太難用，改用 **Cloudflare Tunnel**：HTTPS 憑證由 Cloudflare 處理，雲端主機與團隊電腦都不開任何連入埠。
> 現有 `frontend` 映像（T06）內部仍用 Caddy 的 `:80` 提供靜態檔，並把 `/api/*` 轉給後端。它只在 Compose 內部網路，不處理憑證，也不對外開埠。要換成其他靜態伺服器（例如 nginx）是另一項選擇，本 ticket 沒有更動。

## 1. 節點配置

| 節點 | 服務 | 資料位置 | 對外方式 |
|---|---|---|---|
| 雲端主機 | `db`（PostgreSQL＋pgvector）<br>`backend`、`worker`、`scheduler`<br>`frontend`<br>`cloudflared` | Code `/opt/meal-planner/code`<br>Data `/var/lib/meal-planner/data`<br>Runtime `/var/lib/meal-planner/runtime`<br>DB 存在具名 volume `postgres_data` | `cloudflared` 主動連出，Cloudflare 把 `https://<PUBLIC_HOSTNAME>` 轉到 `http://frontend:80`。<br>所有容器都不對主機開埠，db 與 backend 只在 Compose 內部網路。 |
| 團隊電腦 | YOLO 推論<br>知識 RAG（`knowledge-rag` 容器）<br>知識庫（`knowledge-db` 容器）<br>`cloudflared`（Windows 服務） | 知識庫（知識紀錄＋pgvector 索引）存在具名 volume `meal-team_knowledge_pg`<br>YOLO 權重在 Git `inference/models/` | 服務只綁 `127.0.0.1`（YOLO 8765、知識 8766、知識庫 55433 只供本機工具使用）。<br>`cloudflared` 把 `yolo.<網域>`、`knowledge.<網域>` 轉到本機兩個埠，前面有 Cloudflare Access。 |

**雲端與團隊電腦之間的連線**：雲端後端呼叫 `https://yolo.<網域>`、`https://knowledge.<網域>`。

- **加密**：Cloudflare 邊緣的 HTTPS，加上 tunnel 本身的加密連線。
- **驗證（兩層）**：
  1. Cloudflare Access 只放行帶有效服務權杖的請求。後端在每次呼叫附上 `CF-Access-Client-Id`／`CF-Access-Client-Secret`（設定 `TEAM_ACCESS_CLIENT_ID`／`TEAM_ACCESS_CLIENT_SECRET`）。
  2. 服務自己再核對 `Authorization: Bearer`（`RECOGNITION_SERVICE_TOKEN`＝團隊端 `INFERENCE_TOKEN`；`KNOWLEDGE_SERVICE_TOKEN`＝團隊端 `KNOWLEDGE_TOKEN`）。
- **失敗分類**：
  - Access 拒絕（403，或缺權杖時導向登入頁的 3xx）→ `AUTH_FAILED`。
  - 團隊電腦關機或 tunnel 斷線（Cloudflare 530／502／504）或連線失敗 → `UNREACHABLE`。
  - 回應不是 JSON（例如 Access 的登入頁 HTML）→ `INVALID_RESPONSE`。
  - 以上一律是「服務不可用」，不是「沒有辨識結果」或「查無知識」（`backend/app/services/team_link.py`）。

**RAG 資料與索引在團隊電腦**：知識紀錄、審核狀態與 pgvector 索引都在 `meal-knowledge-db`。

- 查詢向量由雲端 worker 經 T10 額度預留後向 OpenAI 取得，再送到團隊電腦。團隊電腦不呼叫付費 API。
- **團隊電腦→雲端（pre-review #6）**：知識管理指令的補抓佇列與建索引向量化走雲端內部 API
  `https://cloud-internal.<網域>/internal/knowledge/*`（cloudflared 直接轉到 `backend:8000`），同樣兩層驗證：
  Access 應用程式只允許團隊電腦專用的服務權杖（團隊端 `CLOUD_ACCESS_CLIENT_ID`／`SECRET`），後端再核對 `INTERNAL_API_TOKEN`。
  團隊電腦**不持有雲端 `DATABASE_URL`**；公開網站的 Caddy 對 `/api/internal/*` 回 404。詳見 `docs/knowledge-source-healthnews.md`。
- **團隊電腦在線狀態（pre-review #5）**：登入後 `GET /api/health/team` 回報辨識與知識服務 online／offline／not_configured（快取 30 秒），生成面板顯示。
- 結構化食譜查詢在雲端 PostgreSQL，不依賴團隊電腦。

## 2. 秘密與設定（只列名稱，不含值）

真實值只放主機上的 `.env.prod`（雲端）與 `deploy/team/.env.team`（團隊電腦），兩者都被 `.gitignore` 排除。範本見 `.env.prod.example`、`deploy/team/.env.team.example`。

| 位置 | 秘密 | 其他必要設定 |
|---|---|---|
| 雲端 `.env.prod` | `CLOUDFLARE_TUNNEL_TOKEN`<br>`POSTGRES_PASSWORD`<br>`RECOGNITION_SERVICE_TOKEN`<br>`KNOWLEDGE_SERVICE_TOKEN`<br>`TEAM_ACCESS_CLIENT_ID`<br>`TEAM_ACCESS_CLIENT_SECRET`<br>`OPENAI_API_KEY`<br>`INTERNAL_API_TOKEN` | `PUBLIC_HOSTNAME`<br>`ALLOWED_ORIGINS=https://<PUBLIC_HOSTNAME>`<br>`COOKIE_SECURE=true`<br>`PROJECT_DATA_ROOT`、`PROJECT_RUNTIME_ROOT`、`PROJECT_BACKUP_ROOT`<br>`BACKUP_MIRROR_ROOT`<br>`RECOGNITION_SERVICE_URL`、`KNOWLEDGE_SERVICE_URL` |
| 團隊電腦 `deploy/team/.env.team` | `TEAM_TUNNEL_TOKEN`<br>`INFERENCE_TOKEN`<br>`KNOWLEDGE_TOKEN`<br>`KNOWLEDGE_DB_PASSWORD`<br>`CLOUD_INTERNAL_TOKEN`<br>`CLOUD_ACCESS_CLIENT_ID`／`SECRET` | `TEAM_BACKUP_ROOT`<br>`BACKUP_MIRROR_ROOT` |

檢查設定（只列變數名稱，絕不印值）：`python -m ops.preflight cloud --env-file .env.prod`、`python -m ops.preflight team --env-file deploy/team/.env.team`。

- 檢查項目：權杖長度與範例值、ALLOWED_ORIGINS 只能是 https 正式網域、COOKIE_SECURE、服務 URL 必須是 https、有團隊服務時必須有 Access 服務權杖、備份不可放在 Data Root 內、必須有獨立位置。
- 團隊服務未設定只是警告，對應功能會顯示不可用。

## 3. Cloudflare 設定步驟（由人執行）

1. **網域交給 Cloudflare**：在 Cloudflare 新增網站（Free 方案即可），到註冊商把 nameserver 改成 Cloudflare 指定的兩組，等狀態變成 Active。
2. **雲端 tunnel**：
   - Zero Trust → Networks → Tunnels → Create tunnel（Cloudflared），命名 `meal-planner-cloud`，複製權杖填入 `.env.prod` 的 `CLOUDFLARE_TUNNEL_TOKEN`。
   - 在 tunnel 的 Public Hostname 新增 `<PUBLIC_HOSTNAME>`（例如 `meal.<網域>`）→ Service `HTTP`、URL `frontend:80`。DNS CNAME 會自動建立。
   - 網站本身不加 Access，讓一般使用者以 App 自己的登入使用。
   - 另新增內部 hostname `cloud-internal.<網域>` → Service `HTTP`、URL `backend:8000`，並為它建 Access 應用程式（Service Auth，
     只允許團隊電腦專用的服務權杖 `meal-planner-team-computer`）；雲端 `.env.prod` 設 `INTERNAL_API_TOKEN`，團隊端設 `CLOUD_INTERNAL_*`。
3. **團隊電腦 tunnel**：
   - 另建 tunnel `meal-planner-team`。在團隊電腦以系統管理員執行 `cloudflared service install <TEAM_TUNNEL_TOKEN>`，安裝成 Windows 服務、開機自動啟動。
   - 新增兩個 Public Hostname：`yolo.<網域>` → `HTTP` `127.0.0.1:8765`；`knowledge.<網域>` → `HTTP` `127.0.0.1:8766`。
4. **Access 服務權杖**：
   - Zero Trust → Access → Service Auth → Create Service Token（名稱 `meal-planner-cloud-backend`）。把 Client ID／Secret 填入雲端 `.env.prod` 的 `TEAM_ACCESS_CLIENT_ID`／`TEAM_ACCESS_CLIENT_SECRET`。Secret 只顯示一次。
   - 記下到期日，到期前輪替。
5. **Access 應用程式**：
   - Access → Applications → Self-hosted，各為 `yolo.<網域>`、`knowledge.<網域>` 建一個。
   - Policy 的 Action 選 **Service Auth**，Include 選上一步的服務權杖。不要加其他允許規則。
6. **驗證**（第 4 節的步驟 4）：
   - 不帶權杖呼叫 `https://knowledge.<網域>/v1/health` 應被 Access 擋下（403 或導向登入）。
   - 帶 Access 權杖但沒有 Bearer 應回 401。
   - 兩者都帶應回 200。

本機設定範例 `.env.prod.example` 使用 `meal.example.com` 等佔位網域。

## 4. 部署步驟

**雲端主機**（Ubuntu LTS 類，已安裝 Docker Engine＋Compose v2 與 python3）：

1. `git clone` 到 `/opt/meal-planner/code`。建立 `/var/lib/meal-planner/{data,runtime}` 與 `/var/backups/meal-planner`，並掛載獨立備份位置到 `BACKUP_MIRROR_ROOT`（另一台主機或物件儲存，不能是同一顆磁碟）。
2. 把 `data/recipe_dataset_1017.xlsx` 複製到 Data Root（排程查價使用）。複製 `.env.prod.example` 為 `.env.prod` 並填值，再執行 `python3 -m ops.preflight cloud --env-file .env.prod`。
3. 第一次部署：`python3 -m ops.deploy --env-file .env.prod`，依序執行：
   1. 設定檢查。
   2. 部署前備份：先 `up -d --wait db`，再備份（`label=predeploy`，複製到獨立位置）。首次部署時資料庫是空的，會略過備份並明確提示。
   3. `build`。
   4. `run --rm backend alembic upgrade head`。
   5. `up -d`。

   任何一步失敗都會停止。
4. 確認 `https://<PUBLIC_HOSTNAME>/api/health`、`/api/health/db` 回 ok。主機防火牆只需開放 SSH 等管理用途，80／443 不必開。
5. 啟用每日備份：`sudo cp deploy/cloud/meal-planner-backup.service deploy/cloud/meal-planner-backup.timer /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now meal-planner-backup.timer`。

**Schema 規則**：

- 正式後端 `AUTO_MIGRATE=false`。啟動時 `python -m app.core.schema_gate` 確認資料庫在 Alembic head，不符就拒絕啟動。
- 升級只在 `ops.deploy` 的部署前備份之後執行。
- 遷移失敗或需要退版時**不執行 downgrade**：舊映像可繼續使用未變更的 schema。若 schema 已部分變更，以部署前備份還原到新資料庫（第 6 節），驗證後再切換。
- 開發環境（未設 `AUTO_MIGRATE`）維持啟動時自動升級。

**團隊電腦**（Windows＋Docker Desktop，GPU 給 YOLO）：

1. 複製 `deploy/team/.env.team.example` 為 `deploy/team/.env.team` 並填值，再執行 `python -m ops.preflight team --env-file deploy/team/.env.team`。
2. 知識庫與 RAG：`docker compose --env-file deploy/team/.env.team -f deploy/team/compose.yaml up -d --build`。
   - 第一次啟動時以 `knowledge_rag/schema.sql` 初始化。
   - 知識匯入、審核與建索引沿用 T11 流程（`KnowledgeStore`，連 `127.0.0.1:55433`）。
   - 建索引的向量化必須經雲端 T10 額度預留，不得在團隊電腦直接呼叫 OpenAI。
3. YOLO（主機 GPU）：在 PowerShell 設定 `$env:INFERENCE_TOKEN`（與雲端 `RECOGNITION_SERVICE_TOKEN` 相同）、`$env:INFERENCE_DEVICE="0"`，再以 YOLO 環境執行 `python inference\server.py`。
   - 預設只聽 `127.0.0.1:8765`，詳見 `inference/README.md`。
   - 要開機自動啟動，可用工作排程器建立「登入時」工作。
4. `cloudflared` Windows 服務：見第 3 節步驟 3。
5. 每日備份（工作排程器，03:45）：`schtasks /Create /SC DAILY /ST 03:45 /TN MealPlannerKnowledgeBackup /TR "cmd /c cd /d D:\AIPE04期末專題 && python -m ops.backup knowledge --env-file deploy\team\.env.team"`。

**離線行為**（測試：`tests/test_t17_offline.py`、`tests/test_t17_team_link.py`，以及 T09／T11／T14 既有測試）：

- 團隊電腦關機或 tunnel 斷線時：
  - 辨識回 `unavailable`、`retryable=true`，照片保留可重試，也可手動輸入。
  - 菜單生成照常使用雲端結構化食譜，知識說明標 `KNOWLEDGE_UNAVAILABLE`（不是 `not_found`，也不呼叫說明模型）。
- 登入、草稿與已採用歷史照常可用。
- 恢復後，辨識可用「重試」；知識說明可在結果頁「只重試知識說明」（不重新生成菜單，存成新版本）。

## 5. 備份內容與保留

`python -m ops.backup {cloud|knowledge}`（`ops/backup.py`，只用標準函式庫）。

- **寫入方式**：每份備份寫在 `<備份根>/<side>/<UTC 時間戳>[-label]/`。先寫 `.partial`，全部完成並寫入 `manifest.json`（每個檔案的 SHA-256 與大小）後才改名，所以未完成的備份不會被當成可用備份。
- **獨立位置**：`--mirror`／`BACKUP_MIRROR_ROOT` 複製到獨立位置，並在目的地重新核對校驗碼。未設定時會警告，preflight 則視為錯誤。
- **保留**：兩處各自保留 7 日（`BACKUP_RETENTION_DAYS`）。最新一份永遠保留；超過一天的 `.partial` 會清掉。

| side | 檔案 | 內容 |
|---|---|---|
| cloud | `database.dump` | 業務庫 `pg_dump -Fc`（家庭、成員、問卷、生成結果、已採用歷史含菜色識別與快照、食譜庫、價格快照、工作與額度） |
| cloud | `data_root.tar.gz` | Data Root 全部內容（上傳照片等），**不含 `logs/`**（Log 依 T06 政策另行輪替） |
| cloud | `snapshot.tar.gz` | 來源／規則快照：`config/*.json`、`nutrition_target/`（規則）、`data/recipe_dataset_1017.xlsx`、`knowledge_rag/schema.sql` |
| knowledge | `database.dump` | 知識庫 `pg_dump -Fc`（知識紀錄、審核、索引版本與向量） |
| knowledge | `knowledge_sources.jsonl` | 知識紀錄逐筆 JSON（含來源、版本、審核狀態）。不依賴 PostgreSQL 格式，可據以重建 |
| knowledge | `snapshot.tar.gz` | `knowledge_rag/schema.sql`、`config/model_pricing.json` |
| 兩者 | `manifest.json` | 格式版本、時間、git commit、Alembic 版本（cloud）、各表筆數、active 索引版本與向量數（knowledge）、檔案校驗碼 |

## 6. 還原操作

`python -m ops.restore <備份目錄> --target-database <新資料庫> [--data-target <新目錄>] [--rebuild-from-sources]`

1. **核對**：先核對 manifest 的所有校驗碼，不符就在動到任何資料庫之前停止。
2. **只還原到不存在或沒有資料表的資料庫**，有資料表就拒絕（`TARGET_NOT_EMPTY`），絕不覆寫現有資料庫。Data Root 也只解到空目錄。
3. **驗證**：
   - 雲端：Alembic 版本等於備份、各表筆數等於 manifest，並實際讀出家庭與已採用餐食的日期與菜名。
   - 知識：筆數與 active 索引向量數一致。
   - 結束碼：0＝通過、2＝驗證不符、1＝備份不可用或拒絕還原。
4. **切換**（正式）：
   1. 停 `backend`／`worker`／`scheduler`。
   2. 還原到新庫（例如 `meal_planner_restored`）並驗證。
   3. 舊庫改名保留（`ALTER DATABASE meal_planner RENAME TO meal_planner_old_<日期>`），新庫改名為 `meal_planner`。
   4. Data Root 指向還原目錄。
   5. 用 `ops.deploy` 或 `up -d` 啟動（schema 閘門會再確認版本）。
5. **知識只剩來源快照時**：`--rebuild-from-sources` 用備份內的 `schema.sql` 與 `knowledge_sources.jsonl` 重建知識紀錄，保留來源版本與審核狀態。
   - 向量索引不在快照內，報告的 `index_to_rebuild` 列出原索引版本與模型。
   - 再以 T11 `KnowledgeStore.build_index` 經雲端 T10 額度預留重新向量化（只有已審核的知識進索引）。

## 7. 本機還原演練證據（2026-10-20）

**環境**：

- 開發機 Windows 11、Docker Desktop。資料庫是 `overcooked-dev-db` 容器（pgvector 0.8.6／PostgreSQL 17.11）。
- 工具經 `docker exec` 在容器內執行 pg_dump／pg_restore／psql。
- 全部使用合成資料與臨時資料庫 `meal_planner_t17_*`，演練後已刪除。
- 備份根、獨立位置與還原目錄都在暫存目錄，模擬兩個位置；這不是真正的異地。

**步驟與結果**：

1. **建立來源資料**：建立來源庫 `meal_planner_t17_drill`，Alembic head `d9a4b7e2c5f1`，含 1 個使用者、1 個家庭、1 位成員、2 筆已採用晚餐（2026-10-18 番茄炒蛋、2026-10-19 清炒高麗菜）。Data Root 含一張合成上傳照片與一個 Log 檔。
   - 另建知識庫 `meal_planner_t17_drill_knowledge`：3 筆知識（2 筆已審核），active 索引 `idx-drill-1` 有 2 個向量（合成向量，未呼叫 OpenAI）。
2. **備份**：`python -m ops.backup cloud ... --mirror <offsite>` 與 `python -m ops.backup knowledge ... --mirror <team-offsite>`，兩者都顯示 `backup ok` 與 `mirror ok`。
3. **模擬遺失**：**刪除兩個來源資料庫**。
4. **還原雲端**：從**獨立位置的副本**還原到 `meal_planner_t17_restored`，並把 Data Root 解到新目錄。
   - 驗證 `ok: true`，Alembic `d9a4b7e2c5f1`。
   - 筆數：users 1、households 1、household_members 1、adopted_meal_plans 2。
   - 讀出 `演練家庭|2026-10-18|番茄炒蛋`、`演練家庭|2026-10-19|清炒高麗菜`。
   - 上傳照片已還原，`logs/` 未包含。
5. **還原知識庫**：從獨立位置的副本還原到 `meal_planner_t17_knowledge_restored`。驗證 `ok: true`：knowledge_records 3、approved 2，active `idx-drill-1` 的 record_count 2 與 embeddings 2 一致。
6. **只用來源快照重建**：還原到 `meal_planner_t17_knowledge_rebuilt`。結果 `ok: true`：knowledge_records 3、approved 2，`active_index: null`，`index_to_rebuild=idx-drill-1`。
7. **拒絕覆寫**：再次還原到已有資料表的 `meal_planner_t17_restored`，結果 `restore FAILED: 目標資料庫 … 已有資料表，拒絕覆寫`，結束碼 1。

**自動化測試**（每次 CI 與本機都跑真實 Docker／PostgreSQL）：`tests/test_t17_backup_restore.py`。

- 備份 manifest 與校驗碼。
- 竄改後拒絕還原。
- 還原後家庭與餐食可讀、Data Root 還原且不含 Log。
- 拒絕覆寫、筆數不符會被抓出。
- 7 日保留、最新一份不刪、`.partial` 清理、獨立位置副本核對。
- 知識庫還原後可檢索。
- 只用來源快照重建後可重建索引（只含已審核知識），反斜線等特殊字元原樣保留。

另有 `tests/test_t17_deploy.py`：

- schema 閘門。
- 部署順序：備份先於遷移，失敗不 downgrade。
- 正式設定檢查，不洩漏秘密。
- `docker compose config`：正式環境沒有任何主機埠、缺 tunnel 權杖或路徑時拒絕產生設定；團隊電腦的埠只綁 127.0.0.1。

**限制**：

- 演練在開發機同一個 Docker 主機上進行，不能取代正式環境的異地還原演練。
- 正式庫的資料量、`pg_dump` 時間與備份大小尚未量測。
- 知識向量索引的重建在演練中以合成向量驗證機制，真實重建需要 OpenAI 向量化費用與 T10 預留。

## 8. 費用紀錄（待填實際報價）

R11 規定每月總上限 NT$2,000，包含主機、模型、向量化、資料、備份等全部服務。下表必須用**實際報價**（含幣別、稅費、匯率日期）填寫，並同步更新 `settings.committed_services_twd`（目前 1400 只是佔位）。試算與上限檢查用 T18 的 `scripts/load/monthly_cost.py`（見 `docs/T18-load-and-cost.md`）。

| 項目 | 供應商／方案 | 原幣報價 | 稅費 | 匯率／日期 | 每月 NT$ | 報價來源與日期 | 填寫人 |
|---|---|---|---|---|---|---|---|
| 雲端主機（VM＋磁碟） | 待選 | | | | | | |
| 網域（年費÷12） | 待選 | | | | | | |
| Cloudflare（Tunnel、Access） | Free 方案（需確認 Access 人數上限與條款） | | | | | | |
| 獨立備份位置（物件儲存或另一台主機） | 待選 | | | | | | |
| OpenAI 生成（gpt-5.4-mini） | T10 額度 | | | | | | |
| OpenAI 向量化（text-embedding-3-small） | T10 額度 | | | | | | |
| 團隊電腦電費／網路（若計入） | — | | | | | | |
| **合計（須 ≤ 2,000）** | | | | | | | |

## 9. 尚未在正式環境驗證（交付前必須由人完成）

- [ ] 選定雲端主機與網域，填入第 8 節實際報價，確認合計 ≤ NT$2,000，並更新 `committed_services_twd`
- [ ] 網域接入 Cloudflare；建立雲端與團隊電腦兩條 tunnel、兩個 Access 應用程式與服務權杖（第 3 節）
- [ ] 從外網以 `https://<PUBLIC_HOSTNAME>` 完成註冊、登入、建立家庭與問卷（AC24 外網 HTTPS）
- [ ] 確認雲端主機沒有任何對外連入埠（例如外部掃描 80／443／5432／8000 都不通），資料庫不對外
- [ ] 不帶 Access 權杖或 Bearer 呼叫團隊電腦兩個 hostname 都被拒絕，帶齊才成功
- [ ] 雲端內部 hostname 同樣：不帶團隊電腦的 Access 權杖或 `INTERNAL_API_TOKEN` 被拒絕；公開網站的 `/api/internal/*` 回 404；團隊電腦以 `crawl --queued`、`index build` 成功（未設定雲端 `DATABASE_URL`）
- [ ] 團隊電腦在線時，雲端實際完成一次辨識與一次含知識說明的生成
- [ ] 團隊電腦關機或停掉 cloudflared 時，畫面顯示服務不可用、照片保留，歷史與一般功能可用；恢復後重試成功
- [ ] 在正式主機啟用每日備份 timer 與團隊電腦工作排程，確認連續數日產生備份、獨立位置有副本，並在第 8 天開始清掉舊備份
- [ ] **正式環境還原演練**：從獨立位置副本還原到新資料庫，驗證家庭、已採用餐食與來源／規則快照可讀，知識庫可還原或重建；記錄日期、備份大小、耗時與結果
- [ ] 第一次正式 `ops.deploy`（含部署前備份與 schema 閘門）成功
- [ ] G06／AC21 五人跨節點容量測試：**另案進行，部署完成不等於通過**
