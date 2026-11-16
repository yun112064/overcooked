# DEMO-3D 移植驗證紀錄

分支 `feature/t11-t20-demo-3d`（自 `origin/t11-t20-demo`）。本檔位於 repo 根目錄，因為 DEMO 的 `.gitignore` 第 42 行忽略 `docs/`。

**P00–P14 各段的表格由各套件自行回報，P16 沒有逐條重跑，所以留白；留白不代表通過。P16 實際執行的全套檢查、靜態稽核及其涵蓋範圍，記在文末的「P16-final-verification」段。歷史報告與其他分支的紀錄，都不是本分支的證據。**

填寫規則：只記錄實際執行過的指令與關鍵輸出；被略過（skip）的測試類別要寫出名稱與原因；本機一律不設定 `TEST_DATABASE_URL`、不連任何資料庫、不啟動容器、不呼叫外部服務。

## P00-test-tooling: Serial pre-step: requirements-dev.txt and one-time tool install (runs alone before wave 1)

```powershell
python -c "import pytest, jieba, opencc; print(pytest.__version__)"
python -m pip check
```

| 執行於 | 結果 | 略過 | 限制 |
|---|---|---|---|
|  |  |  |  |

## P01-config-tooling: Settings and backend image (owner of config.py, backend/Dockerfile)

```powershell
$env:PYTHONPATH='backend'; Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
python -m py_compile backend/app/core/config.py
python -c "from app.core.config import settings as s; assert s.sim_market_real_checkout_enabled is False and s.assistant_rate_per_minute==10 and s.generation_recipe_release_id is None and s.knowledge_explain_provider=='openai' and '0.4.0' in str(s.nutrition_rules_path)"
git diff --stat -- backend/app/core/config.py backend/Dockerfile   (additions only)
```

| 執行於 | 結果 | 略過 | 限制 |
|---|---|---|---|
|  |  |  |  |

## P02-schema-migration: Models and one additive Alembic revision (owner of db/models.py, models/*, migrations, tests/test_seed_data.py)

```powershell
$env:PYTHONPATH='backend'; Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
python -m py_compile backend/app/db/models.py backend/app/models/recipe.py backend/app/models/__init__.py backend/app/models/nutrition.py backend/app/models/nutrition_candidates.py migrations/versions/e1115demo3d01_demo_3d_port.py
python -m unittest tests.test_demo3d_schema -v
python -m unittest tests.test_seed_data tests.test_t01_recipe_import -v   (non-DB tests green; DB classes skip LOCALLY but run in CI - say so in the hand-off)
grep -n "d4e8a2c7f913" tests/test_seed_data.py   (no match)
grep -l "down_revision.*d4e8a2c7f913" migrations/versions/*.py   (only e1115demo3d01)
```

| 執行於 | 結果 | 略過 | 限制 |
|---|---|---|---|
|  |  |  |  |

## P03-frontend-3d: frontend-3d adjustments: adopted-plan predicate, dev port (single owner), Dockerfile, Caddyfile, .dockerignore

```powershell
cd frontend-3d
npm ci
npm run lint
npm test
npm run build
(no docker build/run; `caddy validate --config Caddyfile` only if a local caddy binary exists)
```

| 執行於 | 結果 | 略過 | 限制 |
|---|---|---|---|
|  |  |  |  |

## P04-sim-supermarket-app: Copy the sim-supermarket app

```powershell
cd sim-supermarket
npm ci
npm run lint
npm test
npm run build
```

| 執行於 | 結果 | 略過 | 限制 |
|---|---|---|---|
|  |  |  |  |

## P05-assistant-rag: Copy assistant_rag package, its image and offline tests

```powershell
$env:PYTHONPATH='backend'; Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
python -m compileall -q assistant_rag
python -m pytest tests/test_assistant_answer.py tests/test_assistant_crawl.py tests/test_assistant_eval.py tests/test_assistant_index.py tests/test_assistant_questions.py tests/test_assistant_retrieval.py -q
python -m unittest discover -s tests -p "test_kn_*.py" -v   (knowledge_rag untouched)
git diff --stat -- knowledge_rag   (empty)
```

| 執行於 | 結果 | 略過 | 限制 |
|---|---|---|---|
|  |  |  |  |

## P06-feature-A-db-menu: Feature A: database-menu preview and adoption, plan fields (DEMO-native T12 adapter with staple guards)

```powershell
$env:PYTHONPATH='backend'; Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
python -m py_compile backend/app/api/meal_requests.py backend/app/api/meal_plans.py backend/app/api/database_menu_plans.py backend/app/services/meal_plans.py backend/app/services/database_menu_plans.py backend/app/services/t12_planning.py
python -c "import json; json.load(open('config/garnish_ingredients.json', encoding='utf-8'))"
python -m pytest tests/test_t12_preview_adapter.py tests/test_meal_plan_summary_unit.py tests/test_meal_plans.py -q   (if a failure traces to a file owned by P09/P12 mid-edit, re-run; do not fix their files)
git diff --stat -- backend/app/services/generation.py backend/app/services/meal_calculation.py backend/app/services/meal_cost.py backend/app/services/recipe_retrieval.py backend/app/services/seasonings.py   (empty)
(compileall backend/app, `import app.main` and the t08/t11/t13/t14/t15/t16/t18 suites are run by GATE-W2, not here)
```

| 執行於 | 結果 | 略過 | 限制 |
|---|---|---|---|
|  |  |  |  |

## P09-feature-G-result-extras: Feature G: prices.unquantified_items, dishes[].cover_url + cover route, volume_ml pass-through (owner of meal_results.py)

```powershell
$env:PYTHONPATH='backend'; Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
python -m py_compile backend/app/services/meal_results.py backend/app/api/recipe_covers.py
python -m pytest tests/test_result_extras.py -q
python -m unittest tests.test_t13_meal_cost tests.test_t14_staple tests.test_t02_uni_prosperity -v   (non-DB classes; DB classes skip locally)
(cross-module suites run in GATE-W2)
```

| 執行於 | 結果 | 略過 | 限制 |
|---|---|---|---|
|  |  |  |  |

## P10-feature-D-t04-data: Feature D: POST /meal-requests/{id}/t04-data (off by default) plus transactional release-bundle loader

```powershell
$env:PYTHONPATH='backend'; Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue; Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
python -m py_compile backend/app/api/t04_data.py backend/app/services/t04_data_feedback.py scripts/load_release_bundle.py
python -m pytest tests/test_t04_data_feedback.py tests/test_release_bundle_loader.py -q
python scripts/load_release_bundle.py --help
```

| 執行於 | 結果 | 略過 | 限制 |
|---|---|---|---|
|  |  |  |  |

## P12-feature-F-assistant-backend: Feature F backend: /assistant/chat (SSE), /assistant/recipes, internal_assistant OpenAI engine, llm_client.chat_text, /health/team keys (sole owner of the one meal_request.py hunk)

```powershell
$env:PYTHONPATH='backend'; Remove-Item Env:TEST_DATABASE_URL,Env:OPENAI_API_KEY -ErrorAction SilentlyContinue
python -m py_compile backend/app/api/assistant.py backend/app/api/internal_assistant.py backend/app/services/assistant.py backend/app/services/assistant_recipes.py backend/app/services/allergen_groups.py backend/app/services/llm_client.py backend/app/services/team_status.py backend/app/services/meal_request.py
python -c "import app.api.assistant, app.api.internal_assistant"
python -m pytest tests/test_assistant_proxy.py tests/test_assistant_openai.py tests/test_assistant_recipes.py tests/test_t11_allergen_groups.py -q
python -m unittest tests.test_t17_team_status -v
git diff -- backend/app/services/meal_request.py   (only the import line and household_excluded_food_ids)
```

| 執行於 | 結果 | 略過 | 限制 |
|---|---|---|---|
|  |  |  |  |

## GATE-W2: Serial gate after wave 2: whole-backend compile, app import and cross-module suites (one agent, no file edits)

```powershell
$env:PYTHONPATH='backend'; Remove-Item Env:TEST_DATABASE_URL,Env:DATABASE_URL,Env:OPENAI_API_KEY -ErrorAction SilentlyContinue
python -m compileall -q backend/app migrations scripts/load_release_bundle.py
python -c "import app.main"
python -m unittest tests.test_demo3d_schema tests.test_t16_meal_plans tests.test_t15_meal_results tests.test_t15_meal_validation tests.test_t11_recipe_retrieval tests.test_t14_generation tests.test_t13_meal_cost tests.test_t18_api_contract tests.test_t08_meal_requests tests.test_t17_team_status tests.test_recipe_catalog tests.test_t14_staple -v
python -m pytest tests/test_t12_preview_adapter.py tests/test_meal_plan_summary_unit.py tests/test_meal_plans.py tests/test_result_extras.py tests/test_t04_data_feedback.py tests/test_release_bundle_loader.py tests/test_assistant_proxy.py tests/test_assistant_openai.py tests/test_assistant_recipes.py tests/test_t11_allergen_groups.py -q
git diff --stat origin/t11-t20-demo -- backend/app/services/generation.py backend/app/services/meal_calculation.py backend/app/services/meal_cost.py backend/app/services/recipe_retrieval.py backend/app/services/seasonings.py tests/db_support.py   (empty)
```

| 執行於 | 結果 | 略過 | 限制 |
|---|---|---|---|
|  |  |  |  |

## P07-feature-B-menu-rejections: Feature B: POST /meal-requests/{id}/menu-rejections

```powershell
$env:PYTHONPATH='backend'; Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
python -m py_compile backend/app/api/meal_requests.py
python -m pytest tests/test_menu_rejections.py -q
```

| 執行於 | 結果 | 略過 | 限制 |
|---|---|---|---|
|  |  |  |  |

## P08-feature-C-knowledge-tips: Feature C: POST /meal-requests/{id}/knowledge-tips on DEMO's knowledge client

```powershell
$env:PYTHONPATH='backend'; Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
python -m py_compile backend/app/api/meal_requests.py backend/app/services/knowledge_explanation.py backend/app/services/local_knowledge_explanation.py backend/app/services/knowledge_deadline.py knowledge_rag/explain.py
python -c "import knowledge_rag.explain"
python -m pytest tests/test_t11_knowledge_tips.py tests/test_t11_explain.py -q
python -m unittest tests.test_t11_knowledge_service tests.test_t11_knowledge_store -v
git diff --stat -- config/nutrition_keywords.json backend/app/services/knowledge.py knowledge_rag/server.py knowledge_rag/store.py   (empty)
```

| 執行於 | 結果 | 略過 | 限制 |
|---|---|---|---|
|  |  |  |  |

## P11-feature-E-sim-market-backend: Feature E backend: POST /sim-market/handoffs, /checkout, GET /catalog (demo packs; real checkout absent)

```powershell
$env:PYTHONPATH='backend'; Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
python -m py_compile backend/app/api/sim_market.py backend/app/services/market_handoff.py backend/app/services/recipe_volume.py
python -m pytest tests/test_market_handoff.py tests/test_sim_market_catalog.py -q
```

| 執行於 | 結果 | 略過 | 限制 |
|---|---|---|---|
|  |  |  |  |

## GATE-W3: Serial gate after wave 3: compile, app import, feature suites (one agent, no file edits)

```powershell
$env:PYTHONPATH='backend'; Remove-Item Env:TEST_DATABASE_URL,Env:DATABASE_URL,Env:OPENAI_API_KEY -ErrorAction SilentlyContinue
python -m compileall -q backend/app knowledge_rag assistant_rag
python -c "import app.main; import knowledge_rag.explain"
python -m pytest tests/test_menu_rejections.py tests/test_meal_plans.py tests/test_t11_knowledge_tips.py tests/test_t11_explain.py tests/test_market_handoff.py tests/test_sim_market_catalog.py -q
python -m unittest tests.test_t08_meal_requests tests.test_t18_api_contract tests.test_t11_knowledge_service tests.test_t11_knowledge_store tests.test_t14_generation tests.test_t16_meal_plans tests.test_t17_demo -v   (test_t17_demo: test_every_config_file_the_code_reads_exists must pass with garnish_ingredients.json / nutrition_explain_keywords.json / t11_allergen_groups.json present)
python -m unittest discover -s tests -p "test_kn_*.py" -v
```

| 執行於 | 結果 | 略過 | 限制 |
|---|---|---|---|
|  |  |  |  |

## P13-wiring-backend-compose: Wiring 1: mount routers, compose.yaml serves frontend-3d, env examples, start-dev/stop-dev/dev-env, CI

```powershell
$env:PYTHONPATH='backend'; Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
python -c "import app.main"
python -m pytest tests/test_demo3d_routes.py -q
python -m unittest tests.test_t18_api_contract tests.test_t17_deploy tests.test_dev_env_script tests.test_t17_demo -v
docker compose -f compose.yaml --profile sim-market config -q   (static parse with placeholder env; skip if docker absent; never `up`)
powershell -NoProfile -Command "foreach($f in 'scripts/start-dev.ps1','scripts/stop-dev.ps1','scripts/dev-env.ps1'){$e=$null;[void][System.Management.Automation.Language.Parser]::ParseFile($f,[ref]$null,[ref]$e); if($e){throw \"$f $e\"}}"
grep -n "DataRoot" scripts/start-dev.ps1   (defined before first use)
git check-ignore -v .env.dev .env.demo   (still ignored)
```

| 執行於 | 結果 | 略過 | 限制 |
|---|---|---|---|
|  |  |  |  |

## P14-wiring-demo: Wiring 2: compose.demo.yaml, ops/demo.py, demo-up/down so frontend-3d is the demo UI, with pre-migration backup and safe defaults

```powershell
Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
python -m py_compile ops/demo.py
python -m unittest tests.test_t17_demo tests.test_t17_deploy -v   (DemoComposeTests need the docker CLI for `compose config` only; they skip without it - record that)
docker compose -p overcooked-demo -f compose.yaml -f compose.demo.yaml --profile tunnel --profile tools --profile sim-market --profile assistant config -q   (placeholder env for the pre-existing required keys only; static; never `up`)
powershell -NoProfile -Command "foreach($f in 'scripts/demo-up.ps1','scripts/demo-down.ps1','scripts/assistant-data-demo.ps1'){$e=$null;[void][System.Management.Automation.Language.Parser]::ParseFile($f,[ref]$null,[ref]$e); if($e){throw \"$f $e\"}}"
grep -n ":?" compose.demo.yaml   (only the lines that existed on origin/t11-t20-demo)
```

| 執行於 | 結果 | 略過 | 限制 |
|---|---|---|---|
|  |  |  |  |

## P15-docs: Docs: README section and tracked validation record skeleton

```powershell
python -c "import pathlib; r=pathlib.Path('README.md').read_text(encoding='utf-8'); v=pathlib.Path('DEMO-3D-VALIDATION.md').read_text(encoding='utf-8'); assert 'frontend-3d' in r and 'e1115demo3d01' in r and 'downgrade' in r and 'Not executed' in v"
git check-ignore DEMO-3D-VALIDATION.md   (no output)
git diff --stat -- README.md   (additions only)
```

| 執行於 | 結果 | 略過 | 限制 |
|---|---|---|---|
| 2026-11-15（P16 代跑） | python 斷言**失敗**（AssertionError：README.md 缺 frontend-3d／e1115demo3d01／downgrade 段落）；`git check-ignore DEMO-3D-VALIDATION.md` 無輸出（通過）；`git diff --stat -- README.md` 為空 | — | P15 的 README 段落草稿（`README-insert-after-L76.md`）尚未套用，P15 未完成 |

## P16-final-verification: Full-suite verification and validation record

```powershell
$env:PYTHONPATH='backend'; Remove-Item Env:TEST_DATABASE_URL,Env:DATABASE_URL,Env:OPENAI_API_KEY -ErrorAction SilentlyContinue
python -m compileall -q backend migrations tests nutrition_target nutrition_model knowledge_rag assistant_rag ops scripts
python -m unittest discover -s nutrition_target/tests -t . -p "test_*.py"
$env:PYTHONPATH='nutrition_model'; $env:PYTHONUTF8='1'; python -m unittest discover -s nutrition_model -p "test_*.py"; $env:PYTHONPATH='backend'
python -m unittest discover -s tests -p "test_*.py"
python -m pytest -q tests/test_assistant_answer.py tests/test_assistant_crawl.py tests/test_assistant_eval.py tests/test_assistant_index.py tests/test_assistant_questions.py tests/test_assistant_retrieval.py tests/test_assistant_proxy.py tests/test_assistant_openai.py tests/test_assistant_recipes.py tests/test_market_handoff.py tests/test_sim_market_catalog.py tests/test_t04_data_feedback.py tests/test_release_bundle_loader.py tests/test_t11_explain.py tests/test_t11_knowledge_tips.py tests/test_t11_allergen_groups.py tests/test_result_extras.py tests/test_t12_preview_adapter.py tests/test_meal_plan_summary_unit.py tests/test_meal_plans.py tests/test_menu_rejections.py tests/test_demo3d_routes.py   (same explicit list as CI; not `pytest tests`)
python -c "from alembic.script import ScriptDirectory as S; from alembic.config import Config as C; c=C('alembic.ini'); c.set_main_option('script_location','migrations'); assert S.from_config(c).get_heads()==['e1115demo3d01']"
cd frontend-3d; npm run lint; npm test; npm run build; cd ..
cd sim-supermarket; npm run lint; npm test; npm run build; cd ..
docker compose -p overcooked-demo -f compose.yaml -f compose.demo.yaml --profile tunnel --profile tools --profile sim-market --profile assistant config -q   (placeholder env; static only; skip if docker absent)
```

執行於 2026-11-15，Windows 11、Python 3.12、DEMO 根目錄；`TEST_DATABASE_URL`、`DATABASE_URL`、`OPENAI_API_KEY` 都未設定。沒有連任何資料庫，也沒有啟動伺服器、容器或 Ollama，沒有對外連線。

| 指令 | 結果 | 略過 | 限制 |
|---|---|---|---|
| `compileall -q …` | 通過（exit 0） | — | — |
| `unittest discover -s nutrition_target/tests` | 照原指令（主控台 cp950、沒有 `PYTHONUTF8`）：31 項，**1 error**（`test_daily_cli_accepts_decimal_json_numbers`：子程序輸出 UnicodeDecodeError 'cp950'）。加上 `PYTHONUTF8=1` 重跑：31 項 OK | 0 | 屬環境編碼問題：`nutrition_target/` 與 origin/t11-t20-demo 完全相同（diff 與 status 都是空的），不是移植造成的 |
| `unittest discover -s nutrition_model`（PYTHONUTF8=1） | 68 項 OK | 0 | — |
| `unittest discover -s tests -p "test_*.py"` | 902 項，OK，0 failure／0 error | 411（理由一律是 `TEST_DATABASE_URL not set`） | 見下方「略過明細」 |
| pytest 明確清單（22 個檔，同 CI） | 343 passed、21 skipped、1 warning（starlette httpx 棄用） | 21：`test_meal_plans` 13、`test_t11_knowledge_tips` 4、`test_menu_rejections` 3、`test_assistant_recipes` 1，理由一律是 `TEST_DATABASE_URL not set` | — |
| Alembic 單一 head 斷言 | 通過，heads == `['e1115demo3d01']` | — | 只讀 script 目錄，沒有連資料庫 |
| frontend-3d `lint`／`test`／`build` | lint 通過；vitest 50 個檔、939 項全過；build 通過 | 0 | 沿用既有的 node_modules，沒有重跑 `npm ci`；`dist/` 被 git 忽略 |
| sim-supermarket `lint`／`test`／`build` | lint 通過；node test 9 pass、0 fail；build 通過（只有 chunk >500 kB 的警告） | 0 | 同上 |
| `docker compose … config -q`（四個 profile） | 通過（exit 0） | — | 只靜態解析。由行程環境提供 placeholder：`POSTGRES_PASSWORD`、`INFERENCE_TOKEN`、`KNOWLEDGE_SERVICE_TOKEN`、`INTERNAL_API_TOKEN`、`KNOWLEDGE_DB_PASSWORD`；repo 裡沒有 `.env`／`.env.demo` |

附帶執行：六支 PowerShell 腳本（start-dev、stop-dev、dev-env、demo-up、demo-down、assistant-data-demo）解析錯誤都是 0；`git check-ignore -v .env.dev .env.demo` 兩者仍被忽略；`python -m pip check` 失敗，但只因為全域環境的 streamlit 1.62.0 要求 pillow<13（已裝 13.0.0），跟本專案無關。

### 略過明細（`unittest discover -s tests`，以模組計）

整個模組全數略過（缺 PostgreSQL）：test_kn_admin(13)、test_kn_attribution(3)、test_kn_crawl_queue(8)、test_kn_healthnews_crawler(10)、test_seed_data(8，整個 DB 類別)、test_t08_meal_requests(18)、test_t11_knowledge_store(12)、test_t11_knowledge_tips(4)、test_t11_meal_history(14)、test_t14_staple(7)、test_t15_meal_results(27)、test_t16_meal_plans(19)、test_t17_backup_restore(13)、test_t17_offline(3)、test_t18_api_contract(1)、test_t18_spend_guard(4)。

部分略過（略過／總數）：test_t14_generation 39/50、test_meal_plans 13/16（`@requires_db` API 類別）、test_menu_rejections 3/8（`@requires_db` API 類別）、test_assistant_recipes 1/10、test_budget_floor 2/7、test_category_zero_fill 3/23、test_intake_adjustments 2/9、test_kn_internal_api 6/7、test_llm_estimation 25/35、test_nutrient_gaps 7/21、test_recipe_catalog 4/7、test_t01_recipe_import 8/31、test_t02_pricing 8/11、test_t02_uni_prosperity 9/21、test_t07_identity 16/17、test_t09_recognition 11/16、test_t10_jobs 28/30、test_t11_knowledge_service 18/24、test_t11_recipe_retrieval 27/31、test_t12_meal_calculation 3/50、test_t13_meal_cost 5/28、test_t17_deploy 2/13、test_tfda_mapping 7/22。

沒有任何測試因缺 docker 而略過：本機有 docker CLI，test_t17_demo 的 DemoComposeTests 有執行，而且通過。

### 涵蓋關係

P02、P05、P06、P07、P08、P09、P10、P11、P12、P13、P14 以及 GATE-W2／W3 檢查清單裡的 unittest／pytest 模組，都包含在上面的 `discover` 或 pytest 清單之內，在同一次執行中通過（或依上列原因略過）。各段專屬的 `py_compile`、`git diff` 與 `grep` 小檢查，P16 只重跑了下列靜態稽核涵蓋的部分。

### 靜態稽核

1. `git status --porcelain -- frontend` 為空，`frontend/package.json` 存在：舊 frontend 沒有改動，仍保留著。
2. 指定的 generation／meal_calculation／meal_cost／recipe_retrieval／recipe_import／staples／meal_history／knowledge／seasonings／per_person_nutrition、config/nutrition_keywords.json、config/staples.json、knowledge_rag/server.py、store.py、schema.sql、tests/db_support.py、data、inference、.gitignore：對 origin/t11-t20-demo 的 `git diff --stat` 為空，`git status --porcelain` 也為空（沒有未追蹤檔）。
3. `git diff origin/t11-t20-demo -- backend/app/services/meal_request.py` 只有兩處：import 加上 `allergen_groups`，以及新增 `household_excluded_food_ids`。
4. Alembic 單一 head 是 `e1115demo3d01`。`tests/test_seed_data.py` 的差異是 **兩行**：新增 `from alembic.script import ScriptDirectory`，以及把寫死的 `"d4e8a2c7f913"` 改成 `ScriptDirectory(...).get_current_head()`。實質上是同一個 head 變更，但比「一行」多了一行 import。
5. 補零檢查（t12_planning、database_menu_plans、t04_data_feedback、assistant_recipes、market_handoff、scripts/load_release_bundle.py）：
   - `database_menu_plans.py:232 counts.get(meal_date, 0) + 1`：每日計數，合理。
   - `load_release_bundle.py:55-56,169 .get(name, 0)`：載入報告的筆數計數，合理。
   - `assistant_recipes.py:137,150,153 sum(..., Decimal(0))`：只加總已知重量（`weight is not None`／`if ln.weight`），未知重量不計入，`Decimal(0)` 是 sum 的起始值。
   - `assistant_recipes.py:152 return Decimal(0)`：已知總重為 0 時，主要食材占比回 0。這只用於排序推薦食譜，不會顯示成營養或價格，可以接受，但值得留意：全部重量未知時，占比會被當成 0。
   - `t12_planning.py:221 max(0, 100 - sum(floors))`：最大餘數法的整數比例，不是補零。
   - 沒有找到 `or 0`、`or Decimal(0)` 或 `float(... or 0)`。
6. `.env.example`、`.env.dev.example`、`.env.prod.example`、README.md 與本紀錄裡沒有秘密值。唯一的長字串是 release_data 的 import ID 雜湊，屬公開版本識別碼。
7. 對 origin 的 diff 中，compose.yaml 與 compose.demo.yaml 沒有新增或刪除任何 `:?` 行。三個 `GENERATION_*` 在兩個檔裡都是 `${…:-}`，預設為空。
8. `scripts/demo-up.ps1`：先做部署前備份（第 137 行 `ops.backup`，第 145 行 `verify_backup`，失敗就中止且不執行 alembic），之後才在第 151 行執行 `alembic upgrade head`。`downgrade` 只出現在註解與警告（第 17、101–102、153 行），沒有任何執行 downgrade 的指令。

### 明確未執行

PostgreSQL 上的 migration upgrade／downgrade、所有 `@requires_db` 類別、真實的 release bundle 載入，以及部署前備份步驟，在這裡都**沒有執行**。升級既有的 demo volume 之前，必須先滿足以下其中一項：PostgreSQL 上 unittest 步驟的第一次 CI 執行通過（需要使用者要求 push），或使用者授權一個拋棄式測試資料庫。另外還要在還原的複本上演練一次「備份 → upgrade → bundle 載入」。**不得在 demo volume 上執行 alembic downgrade。**

## Not executed

以下項目在本次移植中**沒有執行**，任何一項都不能視為已驗證：

- PostgreSQL 上的 migration `e1115demo3d01` upgrade／downgrade（只做過 Alembic 離線 `--sql` 產出與模型比對）。
- 所有 `@requires_db` 測試類別：t08、t14、t15、t16、t18、資料種子（`tests/test_seed_data.py`），以及新 API 的資料庫測試類別；本機一律略過，第一次真正執行會是 CI（需使用者要求 push）或使用者授權的拋棄式測試資料庫。
- 真實的 release bundle 載入（`scripts/load_release_bundle.py` 對資料庫寫入）。
- 部署前備份與還原演練：data-root 暫存複製、`ops.backup`、`verify_backup`、`ops.restore`；備份 → upgrade → bundle 載入的順序只有文字測試，從未實際演練。
- `scripts/demo-up.ps1`、`scripts/demo-down.ps1`、`scripts/assistant-data-demo.ps1` 只做過 PowerShell 語法解析與文字測試，從未執行。
- `docker compose up`（開發與 demo）；只做過 `compose config` 靜態解析（沒有 docker CLI 時連這項也略過）。
- Docker 映像建置：frontend-3d、sim-supermarket、assistant（`deploy/team/assistant.Dockerfile`）、backend。
- 兩條 Cloudflare 快速通道（frontend 與模擬超市）。
- SSE 經 Caddy 與快速通道（`/assistant/chat`）。
- Ollama（bge-m3、embeddinggemma、qwen3.5:9b）、OpenAI 與 bge-reranker-v2-m3 重排服務的實際呼叫。
- 健康醫療網、Hello醫師的爬取。
- 預覽相關端點（nutrition-preview、knowledge-tips、採用）的延遲量測。

已知設定：小幫手的兩個 bind mount（`ASSISTANT_DATA_DIR`、`ASSISTANT_RERANKER_MODEL_DIR`）未設定時預設為不存在的哨兵路徑（`/overcooked-demo-unset/...`，`create_host_path: false`），`up` 會失敗關閉；這一點只由 `compose config` 與文字測試確認。

T04 營養證據端點預設關閉（422 `NUTRITION_VERSION_REQUIRED`）；即使填入三個 `GENERATION_*`，因載入器以 `active = false` 寫入 bundle 的食譜發布版本，仍會回 422 `RECIPE_NUTRITION_RELEASE_MISMATCH`，待使用者決定綁定方式。

## 2026-11-15 整合後審查與最終重跑（協調者親自執行）

**審查**：六個面向（新路由正確性、前後端契約、遷移與模型、部署設定、既有 demo 行為回歸、安全與政策）各一個審查代理，每個發現再由兩個代理嘗試推翻，兩者都確認才算成立。成立 14 項，全部修正：

- knowledge-tips 在菜單含過敏或不吃食材（adoption_blocked）時仍會花 T10 額度：改成與採用相同的判斷，並加每分鐘次數上限 `knowledge_tips_rate_per_minute`（預設 10）。
- 不存在或已停用的 recipe_id 回 503：改回 409 `MENU_CHANGED`，真正讀不到資料才回 503。
- 模擬超市交接權杖格式錯誤時回 500：改為 410。
- knowledge-tips 在付費呼叫前 rollback 後又讀 `row.version`，交易在呼叫期間保持開啟：改用 rollback 前記下的版本。
- 重複送出 menu-rejections 會讓退回次數變多：同一組食譜不重複寫入。
- 前端缺 `EMBEDDING_NOT_CONFIGURED` 與 `ADOPTION_BLOCKED` 的說明文字：已補。
- T04 回報照文件設好三個 GENERATION_* 仍回 422：文件與設定範例改為如實說明（仍需使用者決定綁定方式）。
- demo-up：選用映像（模擬超市）建置失敗不再讓整個 demo 停下；相對路徑的 `-BackupRoot` 先轉成絕對路徑。
- start-dev／stop-dev：原本就在跑的小幫手容器不會被記成自己啟動、也不會被 stop-dev 停掉。
- assistant-data-demo：先建置映像，不再嘗試拉取本機才有的映像。
- meal_plans.trend：沒有生成結果快照的計畫（菜單直接採用）回傳相同欄位形狀。

有爭議、未修：小幫手食譜建議在過敏群組設定檔缺少時不展開群組（與 main 相同的刻意設計）；食譜封面路由不需登入（與 main 相同）。

**最終重跑**（`TEST_DATABASE_URL` 未設、`DATABASE_URL=sqlite://` 只為建構設定、`PYTHONPATH=backend;tests`、`PYTHONUTF8=1`）：

| 指令 | 結果 |
|---|---|
| `python -m unittest discover -s tests -t .` | Ran 916, OK (skipped=411) |
| `python -m pytest -q tests` | 788 passed, 411 skipped, 1 failed：`test_t17_backup_restore.py::test_pg` 是未修改的既有輔助函式，被 pytest 當成測試收集，需要測試資料庫；CI 的 pytest 步驟只跑指定檔案，不含它 |
| `python -m alembic heads` | `e1115demo3d01 (head)`，單一 head |
| PowerShell 語法剖析 demo-up/demo-down/start-dev/stop-dev/dev-env/assistant-data-demo | 六支都沒有語法錯誤（只剖析，沒有執行） |
| frontend-3d `npx tsc -b`、`npx eslint .` | 通過 |
| frontend-3d `npx vitest run` | 50 個檔案、943 項通過 |
| frontend-3d `npx vite build` | 成功 |

**仍未執行**：411 個 `@requires_db` 測試（含所有新路由的 API 測試與 e1115demo3d01 在 PostgreSQL 上的升降級）、任何 Docker 映像建置、`docker compose config`、demo-up 的升級前備份、發行資料包匯入、Cloudflare 通道與 SSE 串流、真正的 Ollama／OpenAI／重排模型、在瀏覽器裡對真後端操作新前端。這台電腦上正在執行的 `meal-prod-*` 容器完全沒有碰。


## 2026-11-16 資料庫測試（用完即刪的 PostgreSQL 容器）

使用者同意後，另開一個測試容器 `demo3d-test-pg`（映像 `pgvector/pgvector:pg17`，只綁 `127.0.0.1:55433`，資料庫 `meal_planner_1102_v3_test`），跑完就以 `docker rm -f -v` 連同匿名磁碟刪除。正式環境的 `meal-prod-*` 容器沒有被碰；`DATABASE_URL=sqlite://`，`OPENAI_API_KEY` 未設，沒有對外連線。

環境：`TEST_DATABASE_URL=postgresql+psycopg://postgres:***@127.0.0.1:55433/meal_planner_1102_v3_test`、`BACKUP_PG_CONTAINER=demo3d-test-pg`、`PYTHONPATH=backend;tests`、`PYTHONUTF8=1`。

| 指令 | 結果 |
|---|---|
| 第一次 `python -m unittest discover -s tests -t .` | Ran 916，1 failure：`test_t11_knowledge_tips.test_uses_server_side_t12_food_ids_not_client_input` |
| 修正後 `python -m unittest discover -s tests -t .` | Ran 917，OK，0 skipped |
| `python -m pytest -q -p no:cacheprovider tests` | 1201 passed，0 skipped，0 failed（2 個警告：starlette httpx 棄用、`test_pg` 輔助函式回傳值） |

失敗原因：審查後 knowledge-tips 改成「預覽缺 `adoption_blocked` 就當成不能採用」（fail-closed），這支測試的假預覽沒帶這個欄位。真正的 `t12_planning.preview` 一律會回傳這個欄位（t12_planning.py 第 367 行），所以修的是測試假資料，不是產品程式：假預覽補上 `adoption_blocked: False`，另加 `test_missing_adoption_flag_is_treated_as_blocked` 鎖住 fail-closed 行為（不呼叫付費服務、回空清單）。

這次涵蓋了先前略過的 411 項，包括所有新路由的 API 測試，以及每個 DB 測試類別在 `migrate_fresh()` 裡對 e1115demo3d01 做的 upgrade → downgrade base → upgrade。

仍未執行：Docker 映像建置、`docker compose config`、demo-up 的升級前備份、發行資料包匯入、Cloudflare 通道與 SSE 串流、真正的 Ollama／OpenAI／重排模型、在瀏覽器裡對真後端操作新前端。


## 2026-11-16 實測回饋修正（frontend-3d）

使用者實測回報兩點，已修正：

- 問卷填完、還沒按「開始煮」就先出現菜單：確認步驟原本會自動查資料庫菜單、算營養、產生小知識（小知識會花 T10 額度）。現在確認步驟不再擺即時菜單，菜單只在按「開始煮」後端上餐桌；採用之後，確認步驟只列保存的已採用菜單（`rooms/passboard/AdoptedMenu.tsx`）。舊的 `MenuPreview.tsx`、`PersonNutrition.tsx` 與只有它們用到的文字函式已刪除。食譜庫選菜（/pick）是使用者自己挑菜的流程，維持不變。
- 採用後沒有明顯的「回客廳」：問卷頁已採用的菜單、食譜庫托盤（改成主要按鈕）與食譜庫結果頁都加上「回客廳」；餐桌採用後本來就有。

檢查：`npx tsc -b`、`npx eslint .` 通過；`npx vitest run` 50 個檔案、921 項通過（刪掉預覽相關測試、新增「沒按開始煮不擺菜單」與兩個「回客廳」測試）；`npx vite build` 成功。瀏覽器（假後端，外婆家的草稿）：確認步驟只有「開始煮」、沒有菜單與採用按鈕 → 開始煮 → 餐桌採用 → 底部有「一鍵購入／回客廳／看紀錄」→ 回問卷頁，已採用菜單有「回客廳」。未對真後端重跑。


## 2026-11-16 實機 demo：飲食小幫手上線、營養小知識檢查

- 小幫手沒上線的原因：demo 的 assistant-db 是全新的，沒有 ready 索引。語料已在 `ASSISTANT_DATA_DIR/raw`（healthnews、helloyishi），執行 `scripts/assistant-data-demo.ps1 -SkipCrawl` 只建索引（不爬取、不呼叫付費 API；主機 Ollama embeddinggemma），5440 段、73 秒，狀態 ready。
- 驗證：backend 容器內 `team_status()` 回報 assistant 與 assistant_openai 都是 online；經 backend 的 AssistantProxy 對地端 assistant-rag 問「高麗菜有什麼營養？」，SSE 依序回 meta → references（Hello醫師等）→ 文字 → done（citations [1,2,5]、移除劑量句 1、hybrid_rerank@embeddinggemma、總計 15 秒）。OpenAI 版未實際提問（不花額度）。
- 營養小知識：demo DB 最新的生成結果（2026-11-16 01:47，database_menu）`explanation.status=available`、6 則，T10 有對應的 knowledge_query_embedding 與 knowledge_explanation 兩筆 settled；同一桌的已採用紀錄快照也帶著這 6 則。欄位（ingredient_name、explanation、citations）與 frontend-3d 的型別一致。資料端正常，未找到後端錯誤；使用者看到的畫面待確認（當時跑的是修正前的前端映像）。


## 2026-11-16 營養小知識不好找（使用者在實機 demo 登入後檢查）

在使用者登入的實機 demo（張家，11/16 午餐，資料庫菜單第 1 版）只讀檢查：餐桌的卡盤「營養小知識 6 則」點開正常，6 張卡都附來源；紀錄明細也有 6 則，但「食材小知識」在面板約 5,640 px 處（面板總高約 6,700 px），排在所有做法與每人營養之後。資料與 API 都正常，問題是不好找。

修正：紀錄明細（`rooms/fridge/PlanDetail.tsx`）把食材小知識移到三大營養素比例之後、菜色之前；餐桌（`rooms/dining/DiningScene.tsx`）桌機版在上方狀態鍵旁加「營養小知識 N 則 ›」入口（手機的物件列本來就有，CSS 在手機隱藏）。新增兩項測試。

檢查：tsc、eslint 通過；vitest 50 個檔案、923 項通過。瀏覽器（假後端，桌機 800×600）：餐桌上方出現入口，點了打開小卡盤；紀錄明細的標題順序變成 價格 → 三大營養素 → 食材小知識 → 菜色，小知識位置約 568 px（第一屏下方一點）。實機 demo 要重跑 demo-up 重建前端映像才看得到。

## 飲食小幫手展開頁的關閉按鈕

- 問題：小幫手按「展開」進到 /assistant 後，頁面沒有關閉按鈕，只能靠瀏覽器上一頁。
- 修正：展開頁右上加「關閉」。從房間展開進來的回到原房間；直接開網址進來（沒有上一頁）則回客廳。
- 檢查：tsc、eslint 通過，vitest 923 全過；假後端瀏覽器實測：直接開 /assistant 按關閉回 /today，從 /records 展開後按關閉回 /records。
