# 獨立 Docker 介面展示環境

這個環境在使用者選擇「建立獨立的本機展示環境（使用示範資料）」後建立。只使用新的 `overcooked-ui-demo` Compose 專案，不使用既有的 `overcooked_postgres_data` volume。

## 已啟動的版本

- 新版介面：http://localhost:5173
- 後端 API：http://127.0.0.1:8000
- 示範帳號：`demo@example.test`
- 示範密碼：`DemoLocal2026!`

這是本機合成帳號，請勿填入真實密碼或個人健康資料。

## 重新啟動

在主專案根目錄執行：

```sh
docker compose -f overcooked-frontend/demo/compose.yaml up -d --build
```

前端在另一個終端機執行：

```sh
cd overcooked-frontend/frontend
VITE_DEMO_ENVIRONMENT=docker npm run dev
```

開啟 http://localhost:5173 。Windows PowerShell 可先執行 `$env:VITE_DEMO_ENVIRONMENT='docker'`，再執行 `npm run dev`。畫面頂端會標示此為獨立展示環境。

目前 Dockerfile 使用本機已有的 `meal-planner-backend:latest` 作為 Python 基底，再複製目前主專案的程式並安裝 `requirements.txt`。它沒有覆寫原基底 image；第一次重建需要可存取 Python 套件來源。

停止展示容器但保留示範紀錄：

```sh
docker compose -f overcooked-frontend/demo/compose.yaml stop
```

前端終端機按 Ctrl+C 停止。這些指令不會停止其他 Compose 專案。

## 操作建議

登入後可從首頁繼續預設的三菜草稿，或從「食譜庫選菜」挑選。示範資料有兩位成人、六道菜與一份完整草稿，預設主食是「不需要主食」。

可以實際修改成員、儲存問卷、讀取食譜庫、送出背景生成、切換四個結果頁籤、採用與回看歷史。這些操作會經由原專案的 API、Session、PostgreSQL 與工作佇列保存。

- `demo_app.py` 僅在指定展示資料庫下，注入**合成營養預覽**。
- `demo_worker.py` 使用原工作流程與可注入的合成 calculator。數值為介面展示而設定，不是實際料理的營養資料或正式資料驗收。
- 沒有匯入正式食譜、營養、價格、帳號、照片或知識資料；未取得的營養與價格仍顯示未知。展示環境沒有實際報價來源。
- 拍照辨識、RAG、外部 AI 回答與 AI 食譜回退未啟用。沒有設定金鑰，API、資料庫與工作程序的 internal 網路不提供 Internet 對外連線；gateway 只負責轉送到 backend。
- 只有 Caddy gateway 對主機的 `127.0.0.1:8000` 開放；它轉送到內部 backend。PostgreSQL 不發布主機端口，API／資料庫／工作程序均留在 internal 網路。
- 資料庫名稱固定為 `meal_planner_1102_v3_ui_demo_test`，初始化器檢查名稱、主機與專用 marker，只初始化此新環境；不會重建或清空其他庫。

## 驗證

```sh
docker compose -f overcooked-frontend/demo/compose.yaml ps
docker compose -f overcooked-frontend/demo/compose.yaml exec -T backend python /app/demo/smoke.py
```

`smoke.py` 會新增一份合成問卷並測試：健康檢查、登入、Cookie、成員、六道食譜、合成營養預覽、背景生成、採用及已採用紀錄。它只允許連到這個標示為 `synthetic-ui-demo` 的後端。測試資料會保留在示範資料庫，方便查看歷史。
