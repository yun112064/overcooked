# assistant_rag：飲食客服 RAG（spec R14、tickets/21）

與 `knowledge_rag/`（T11 食材知識）完全分開的獨立套件：使用者問飲食問題，只依健康醫療網與 Hello醫師的飲食文章回答並附出處；不診斷、不開藥、不給劑量。只讀用到 `knowledge_rag.fetcher`（限速／robots）與 `knowledge_rag.healthnews.parse_listing` 的程式，不讀寫它的資料庫或資料。

## 進度

| 步驟 | 狀態 |
|---|---|
| 0 規格與授權 | 完成（spec R14／G08／AC33–AC36、AGENTS.md、tickets/21） |
| 1 資料蒐集 | 完成（健康醫療網 1,213 篇、Hello醫師 365 篇；另補 T11 來源 41 篇與食材補充 84 篇，2026-11-09） |
| 2 分段與索引 | 完成（目前 1,298 篇、5,858 片，`v-762f2ad289`，已套用 402 篇排除名單（含疾病主題 188 篇）；bge-m3、embeddinggemma 向量＋jieba BM25；reranker 服務就緒，2026-11-09） |
| 3 六種檢索方法 | 完成（11 組比較、命令列試查，2026-11-09） |
| 4 客服回答服務 | 完成（規則、追問、門檻、類別回答、qwen3.5 串流回答、逐句檢查、`/v1/chat` SSE，2026-11-09） |
| 5 題庫與驗證集 | 使用者通過（開發 111 題含 3 追問、驗證 51；`eval/README.md`，2026-11-10） |
| 6 評估與比較 | 完成（門檻 0.5、`eval/evaluate.py`、`eval/answers.py`；開發集 110/111、驗證集只跑一次 49/51，2026-11-10） |
| 7 前端與整合 | 完成（雲端 `/assistant/chat` 代理、前端「飲食小幫手」頁、start-dev 自動啟動，2026-11-10）；尚未在正式雲端＋Cloudflare 通道上驗證 |

## 步驟 1：資料蒐集

資料目錄：`ASSISTANT_DATA_DIR`，或 `PROJECT_DATA_ROOT/assistant_rag`；不可位於專案目錄內（原文不進 Git）。

```powershell
$env:ASSISTANT_DATA_DIR = 'D:/AIPE04期末專題_data/assistant_rag'
python -m assistant_rag.crawl healthnews --limit 5   # 試抓
python -m assistant_rag.crawl healthnews             # 完整（可中斷，重跑會接續）
python -m assistant_rag.crawl helloyishi
python -m assistant_rag.crawl stats                  # 篇數、日期範圍、平均字數、略過原因
```

| 來源 | 範圍 | 探索方式 |
|---|---|---|
| 健康醫療網 | 營養飲食頻道 | `/channel/<營養飲食>/<頁碼>` 列表 |
| Hello醫師 | 健康飲食：營養知識、特殊飲食、健康飲食小撇步、肥胖 | sitemap 索引 → 每月文章 sitemap → `/healthy-eating/<子分類>/<文章>/` |

抓取規則：每個來源單一連線、每次請求間隔 ≥2 秒、遵守 robots.txt、可識別 User-Agent（標明非商業專題）、失敗重試退避。Hello醫師的贊助文章不收錄。

列表以外的指定文章（同一來源網站）用 `supplement` 加入，再跑一次抓取；紀錄的 `category` 為「指定補抓」並保留 `supplement_reason`。修好 parser 後可用 `--retry parse_failed` 重抓之前解析失敗的網址：

```powershell
python -m assistant_rag.crawl supplement healthnews --reason "T11 RAG 原始來源" https://www.healthnews.com.tw/article/13547
python -m assistant_rag.crawl healthnews --retry parse_failed
```

2026-11-09 依使用者要求補抓 T11 RAG 原始來源中不在列表內的 41 篇網頁文章（健康醫療網 33、Hello醫師 8；論文不收）。健康醫療網 2017 年前的舊文章沒有 `<p>`，內文在 `<div>` 或以 `<br><br>` 分段，parser 依序退回這兩種版面。

輸出 `raw/<來源>/`：`urls.json`（候選清單）、`articles.jsonl`（每篇一行）、`skipped.jsonl`（略過原因）。每篇紀錄欄位：

```text
source, source_name, article_id, url, discovered_url, title, published_at, modified_at,
author, reviewers{撰文, 醫學審稿, 資料查核, 編輯}, content_provider, category, subcategory,
keywords[], description, references[], is_sponsored,
sections[{heading, heading_path[], paragraphs[]}], content, char_count, content_hash, fetched_at, parser_version
```


## 步驟 2：分段與索引

### 服務（皆在本機，與業務庫、T11 知識庫分開）

| 服務 | 內容 | 位置 |
|---|---|---|
| 資料庫 | 容器 `overcooked-assistant-db`（`pgvector/pgvector:0.8.6-pg17`），資料庫 `overcooked_assistant`，volume `overcooked-assistant-pgdata` | `127.0.0.1:55436` |
| 嵌入 | Ollama `bge-m3:latest`（1024 維）、`embeddinggemma:latest`（768 維） | `RAG_OLLAMA_URL`，預設 `127.0.0.1:11434` |
| 重排序 | 容器 `overcooked-assistant-reranker`（Text Embeddings Inference `120-1.9.4`，RTX 50 系列），`BAAI/bge-reranker-v2-m3` | `RAG_RERANK_URL`，預設 `127.0.0.1:8092` |

連線字串 `ASSISTANT_DATABASE_URL`；未設定時讀 Data Root 的 `db.env`（密碼隨機產生、不進 Git）。程式只接受資料庫名稱以 `overcooked_assistant` 開頭的連線。

reranker 權重放在 Data Root `models/bge-reranker-v2-m3/`（HuggingFace revision 記在 `REVISION`；`model.safetensors` SHA-256 `d9e3e081…5286` 已核對），唯讀掛進容器，不在容器內下載：

```powershell
docker run -d --name overcooked-assistant-reranker --gpus all -p 127.0.0.1:8092:80 `
  -v "D:/AIPE04期末專題_data/assistant_rag/models/bge-reranker-v2-m3:/model:ro" `
  ghcr.io/huggingface/text-embeddings-inference:120-1.9.4 --model-id /model
```

本機 PyTorch 的 DLL 會被 Windows 應用程式控制原則擋下（WinError 4551），所以 reranker 改用容器、以 HTTP 呼叫。

### 建索引

```powershell
python -m assistant_rag.index build      # 分段、斷詞、兩套向量；可中斷，重跑會接續
python -m assistant_rag.index status     # 版本、篇數、片數、各模型 digest 與耗時
python -m assistant_rag.index probe "吃地瓜有什麼好處"   # BM25 與兩套向量各前 5 名
```

- **分段**（`chunking.py`）：段落累積到約 400 字為一片，相鄰片段重疊約 80 字（整句）；新的 h2 小節另起一片（目前片段不足 150 字則併入）；h3 小標以「▸小標」寫進內文；`embed_text` 前綴「標題｜小標路徑」。與小標完全相同的段落視為文章目錄，不收。內容雜湊相同的文章只收第一篇。
- **斷詞**（`segment.py`）：jieba，加入專案 TFDA 食材標準名／別名與食材 ID 主檔（只讀，2–8 字）及營養詞，共 4,523 詞；去掉停用詞與問句用語（吃什麼、有什麼…）；「吃豆腐」「喝咖啡」拆成食物；「祕／秘」等異體字統一。
- **BM25**（`bm25.py`）：Okapi，k1=1.5、b=0.75；斷詞存在 `chunks.tokens`，查詢時在記憶體建索引。
- **向量**：每個嵌入模型一張表（`emb_bge_m3`、`emb_embeddinggemma`），精確 cosine 搜尋（語料數千片，不用近似索引，比較方法時不受近似誤差影響）。
- **排除名單**（`exclusions.json`）：健康醫療網營養飲食頻道中的非飲食文章（醫美、眼科、健保專輯等），以及 2026-11-09 使用者決定先不處理的疾病主題文章（病人飲食、疾病管理與預防專題；以食物為主角的文章與食物安全新聞保留）。`status: approved` 時建索引略過，原始資料不刪；`pending_review` 時不生效。
- **版本**：語料＋清理後標題＋parser／分段／斷詞版本決定 `index_versions.version`；所有嵌入模型完成才標 ready 並切為 active，舊版標 retired。`embedding_runs` 記錄模型 digest，模型換版時拒絕接續、需建新版本。

### 可替換介面（`providers.py`）

`Embedder`（`documents`、`query`、`fingerprint`）與 `Reranker`（`rerank`）。目前實作 `OllamaEmbedder`、`TeiReranker`；`RAG_EMBED_PROVIDER`／`RAG_RERANK_PROVIDER` 之外的值回報 `*_PROVIDER_NOT_CONFIGURED`。之後接 OpenAI 時新增同介面類別，付費呼叫必須經 T10 額度控制，不從地端失敗自動改走付費。換嵌入模型需重建索引。

## 步驟 3：六種檢索方法

`retrieval.py` 的 `Retriever` 載入 active 索引（片段與 BM25 在記憶體），`search(問題, 方法, 嵌入模型)` 回傳前 5 篇文章（每篇只留分數最高的一段），附各階段耗時。

| 方法 | 做法 | 分數 |
|---|---|---|
| `bm25` | jieba 斷詞 BM25，取前 50 | BM25 分 |
| `vector` | 向量精確 cosine，取前 50 | cosine |
| `hybrid` | BM25 前 50＋向量前 50，RRF：Σ 1/(60+名次) | RRF 分 |
| `hybrid_rerank` | hybrid 前 30 段（每篇最多 3 段）交給 bge-reranker-v2-m3 | 重排機率 0–1 |
| `entity_boost` | hybrid_rerank 後，問題中的食材出現在標題 +0.15、只在內文 +0.05；「地瓜葉」不算提到「地瓜」 | 重排機率＋加權 |
| `multi_query` | qwen3.5 把問題改寫成 3 句；原句＋改寫各跑 hybrid，再 RRF 合併，前 30 段用原句重排 | 重排機率 0–1 |

`vector` 之後的方法都分 bge-m3／embeddinggemma，加上 `bm25` 共 11 組。食材辨識用步驟 2 的食材詞典。同一題的改寫在同一個 `Retriever` 內快取，兩個嵌入模型用同一組改寫，比較才公平；改寫失敗（LLM 不可用、非 JSON）時只用原句並在 `extras.rewrite_error` 標示。

```powershell
python -m assistant_rag.search "吃地瓜有什麼好處"                                  # 11 組
python -m assistant_rag.search "痛風可以吃豆腐嗎" --method entity_boost --embedder bge-m3 --text
python -m assistant_rag.search "…" --json                                            # 完整結果
python -m assistant_rag.search --interactive
```

LLM：`RAG_LLM_PROVIDER`（預設 ollama）、`RAG_LLM_MODEL`（預設 `qwen3.5:9b-q4_K_M`），`providers.ChatModel` 介面之後回答與評審共用。

## 步驟 4：客服回答服務

流程（`answer.py`）：

1. **寒暄**（`safety.greeting`）：整句只有你好／謝謝／你是誰／再見時直接回覆，不檢索。
2. **急症與醫療越界**（`safety.precheck`）：胸痛、中風徵兆、呼吸困難、中毒等 → 提醒撥 119；自殺念頭另附 1925 安心專線；問用藥、要求診斷或解讀檢查數值 → 婉拒並建議諮詢醫師／藥師；疾病相關的飲食問題（患者怎麼吃、控制或預防特定疾病，`DISEASE_TOPIC`）→ 婉拒並建議諮詢醫師或營養師（2026-11-09 使用者決定先不處理；會不會致癌、食物過敏、食物中毒屬食物安全，照常回答）。都不檢索、不呼叫 LLM。
3. **追問改寫**：有前文時由 LLM 把最後一句補成完整問題（「那地瓜葉呢」→「地瓜葉有什麼好處」），改寫後再做一次第 2 步檢查。
4. **檢索與拒答門檻**：預設 `hybrid_rerank@bge-m3`；最高重排分數低於門檻時回固定訊息、不呼叫 LLM。門檻目前是**暫定** 0.5（`threshold_status: provisional`），步驟 5／6 以開發題庫選定、驗證集報告後定案。
   - **類別回答**（`categories.py`、`food_categories.json`）：低於門檻時，若問題提到對照表裡的食材（取最長名稱），改用該食材所屬類別的檢索句再查一次；過門檻就以 `category_answer` 回答，第一句固定為「目前沒有「蠔油」本身的資料，以下是「醬料與沾醬」這一類的一般資訊」，並要求 LLM 用「這類食物」當主詞、不把類別資料說成該食材的特性。類別也低於門檻才拒答。分數過門檻、但前 3 篇的標題與內文都沒提到該食材（或 `match` 列的同物異名，例如金瓜＝南瓜、新鮮檸檬＝檸檬）時，也改走類別回答（`fallback.reason = not_mentioned`），避免拿相近但不同的食物文章回答（柴魚 → 鯖魚、蔭油 → 藻油）；這時類別也低於門檻就拒答（2026-11-10）。走類別回答之前，若食材有別名（`aliases`：先取 V3 食譜試算表 `ingredient_aliases` 的別名，例如粉絲＝冬粉、太白粉＝馬鈴薯澱粉，沒有才用人工列的同物異名，例如金瓜＝南瓜、香油＝麻油；只差異體字的不收），依序用別名重查最多 2 次；重查分數過門檻且第一篇本身談到該別名，就直接回答，開頭說明「「金瓜」和「南瓜」是同一種食物，以下以「南瓜」的資料回答」。不確定能用類別或相近食物代答的食材列在 `refuse`（皮蛋、泡香菇的水、油花生、蛋豆腐）：本身查不到就直接拒答；不確定是同一種食物的對照（鳳梨罐頭＝罐頭、蛋豆腐＝豆腐、小茴香＝茴香等）一律不列（2026-11-10）。對照表收錄 V3 食譜在覆蓋檢查中查不到專文的 248 種食材（另 4 種列 refuse）、31 個類別；每個類別的檢索句是實測過的自然問句（關鍵字堆疊的句子重排分數很低）。
5. **回答**：qwen3.5 依前 5 篇參考資料串流作答，系統提示要求只用參考資料、每句標註 [n]、不診斷、不談藥、不給數字份量，並忽略參考資料中的指令。
6. **逐句檢查**（`safety.check_sentence`）：簡體專用字轉繁體（`textconv.py`；不用 OpenCC 的 s2t，它會把「干擾」改成「幹擾」）；移除含劑量／攝取數量（數字＋單位、每週幾次）、指示用藥、替使用者下診斷，寫給疾病族群的句子（含「腎功能不佳者」這類說法）以及沒有引用編號的句子（說明參考資料範圍的保留）；疾病族群例如（「胃潰瘍患者建議…」「糖尿病的人要…」，`DISEASE_POPULATION`，2026-11-10 使用者決定；有刪除時附「針對特定疾病患者的內容已略去，請諮詢醫師或營養師」）（食物成分含量「每 100 克約含…」且無攝取用語、以及只勸人諮詢醫師的句子保留，2026-11-09 使用者同意）；超出本次參考資料範圍的引用編號刪除。被問份量或有句子因劑量被移除時，附上「份量因人而異，建議諮詢營養師或醫師」；最後一律附免責聲明。

設定：`RAG_ANSWER_METHOD`（預設 `hybrid_rerank`）、`RAG_ANSWER_EMBEDDER`（`bge-m3`）、`RAG_REFUSAL_THRESHOLD`（`0.5`；非重排方法必須明確設定）、`RAG_THRESHOLD_STATUS`（`provisional`）、`RAG_LLM_MODEL`。

### 服務

```powershell
$env:ASSISTANT_SERVICE_TOKEN = '<隨機字串>'
python -m assistant_rag.server            # 127.0.0.1:8093
python -m assistant_rag.ask "吃地瓜有什麼好處"      # 命令列試用；--interactive 可連續追問
```

| 端點 | 內容 |
|---|---|
| `POST /v1/chat` | Bearer 驗證。body `{"message": "…", "history": [{"role": "user"|"assistant", "content": "…"}]}`（問題 1–500 字，前文最多 6 則）。回應 `text/event-stream`：`meta`（回覆類型）→ `references`（編號、標題、網址、來源網站、小標、作者、審稿、供稿者、日期、分數）→ `delta`（逐句文字）→ `done`（引用編號、移除數、最高分、門檻、方法、耗時）；失敗時 `error`。輸入格式錯誤回 422。 |
| `GET /v1/health` | Bearer 驗證。資料庫／active 索引、嵌入、重排、LLM 各自狀態；任一不可用回 503。 |

回覆類型：`greeting`、`emergency`、`declined_medical`、`out_of_scope`、`answer`、`category_answer`。日誌只記類型、耗時與數量，不記問題、回答或權杖。

測試：`PYTHONPATH="backend;tests;." python -m pytest tests/test_assistant_crawl.py tests/test_assistant_index.py tests/test_assistant_retrieval.py tests/test_assistant_answer.py tests/test_assistant_proxy.py`（合成資料、假 HTTP／LLM，不連網、不連資料庫）。

### 步驟 7：網站上的「飲食小幫手」

- **雲端後端**（`backend/app/api/assistant.py`、`backend/app/services/assistant.py`）：`POST /assistant/chat` 需登入與允許的來源（同其他寫入 API），body 同 `/v1/chat`（問題 1–500 字、前文最多 6 則、角色只能 user／assistant）。經 T17 的 Cloudflare Access 服務權杖＋Bearer 轉送到 `ASSISTANT_SERVICE_URL`，先確認團隊電腦回 200 才開始串流，SSE 原樣轉送；團隊電腦未設定、離線、逾時、驗證失敗或沒準備好時回 `503 ASSISTANT_UNAVAILABLE`（`details.reason`），不捏造回答；串流中斷時補一個 `error` 事件。每位使用者每分鐘 10 題（`ASSISTANT_RATE_PER_MINUTE`）。不記錄問題或回答。設定：`ASSISTANT_SERVICE_URL`、`ASSISTANT_SERVICE_TOKEN`、`ASSISTANT_TIMEOUT_SECONDS`（預設 120）。
- **服務狀態**：`GET /health/team` 多了 `assistant`（online／offline／not_configured），頁面據此顯示離線並停用輸入。
- **前端**（`frontend/src/pages/AssistantPage.tsx`、`src/api/assistant.ts`）：導覽列「飲食小幫手」（手機底部「問答」）。以 fetch 讀串流逐句顯示，[n] 變成可點的上標，下方列出被引用的參考資料（來源網站、醫學審稿或作者、日期、原文連結）；可停止、重新開始，追問時帶最近 6 則對話。
- **本機啟動**：`start-dev` 在 Data Root 有 `assistant_rag\db.env`、且 `overcooked-assistant-db`／`overcooked-assistant-reranker` 兩個容器存在時，啟動容器與 `python -m assistant_rag.server`（port 8093），並把網址與隨機權杖（`.dev\assistant-token`）傳給後端；缺任何一項就略過、頁面顯示離線。不建立容器、不建索引、不下載模型；Ollama 需自行開啟。`DEV_SKIP_ASSISTANT=1` 可關閉。
- **模型費用**：目前全用團隊電腦本機模型，不經 T10；若改用 OpenAI，必須在雲端代理這一層加上 T10 預留與結算（AC36）。

## OpenAI 版（另一份服務，回答改用 OpenAI 生成）

同一份程式再開一個服務，只把「生成回答」這一步換成 OpenAI；檢索、重排、安全檢查、逐句引用過濾與結尾提醒都和地端版相同。網站上會出現「地端 qwen／OpenAI」切換（只有 OpenAI 版在線時才顯示），每則回答下方標出由哪個模型生成，方便比較。

```
團隊電腦 assistant_rag（RAG_LLM_PROVIDER=openai，例如 port 8094）
  └─ 生成時 POST {CLOUD_INTERNAL_URL}/internal/assistant/chat
       └─ 雲端後端：T10 額度預留 → OpenAI Responses API（ASSISTANT_OPENAI_MODEL）→ 依 usage 結算
```

- 團隊電腦不持有 `OPENAI_API_KEY`，沿用知識庫的雲端內部 API 設定：`CLOUD_INTERNAL_URL`、`CLOUD_INTERNAL_TOKEN`（＝雲端 `INTERNAL_API_TOKEN`）、`CLOUD_ACCESS_CLIENT_ID／SECRET`。
- 雲端需要：`OPENAI_API_KEY`、`INTERNAL_API_TOKEN`、`ASSISTANT_OPENAI_SERVICE_URL`（指向這份服務）；模型 `ASSISTANT_OPENAI_MODEL` 預設 `gpt-5.4-mini-2026-03-17`，必須列在 `config/model_pricing.json`，未列價的模型一律拒絕。
- 額度用完回 `LLM_BUDGET_EXHAUSTED`，前端顯示「本月 AI 額度已用完」；不會自動改用地端模型，地端版也不會自動改走付費。
- OpenAI 版不是逐字串流：整段回答回來後逐句送出。健康檢查只看設定，不為了檢查花額度。
- 本機開發：`.env.dev` 填 `ASSISTANT_OPENAI_PORT=8094` 與 `OPENAI_API_KEY`，`scripts/start-dev.ps1` 會另外啟動 `assistant-openai`，回答經本機後端的內部 API 呼叫 OpenAI（內部權杖自動產生在 `.dev\internal-token`）。
