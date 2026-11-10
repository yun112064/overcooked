# 今天吃啥：獨立前端改版

此目錄預設執行正式 API 版本，沿用原本示範版的焦橘、奶油白、淡紫、廚師帽與蔬果主視覺。原示範版仍保留，可獨立切換。這次所有程式、套件、測試與文件的新增及修改均位於 `overcooked-frontend/`。

## 獨立 Docker 展示

已提供隔離的 Docker API、資料庫與背景工作示範，帳號及啟動方式見 [展示環境說明](../demo/README.md)。此環境使用合成營養數值，畫面會持續標示展示狀態。

## 啟動正式版

請先讓主專案既有後端依原流程運作；本目錄不會啟動資料庫、worker、模型服務或執行遷移。

```sh
cd /Users/kuyun/Documents/AIPE04_team_5/overcooked/overcooked-frontend/frontend
npm ci
npm run dev
```

開啟 **http://localhost:5173/**。此處的 5173 與原前端相同；若原前端正在執行，請先停止原前端的開發伺服器，再啟動此版本。`--strictPort` 會在端口被占用時停止，避免自動換端口後與後端的 Origin 驗證設定不符。

- 瀏覽器呼叫 `/api`，Vite 去掉此前綴後轉到 `http://127.0.0.1:8000`。
- 若既有後端位於其他位址，可透過原本支援的 `VITE_API_PROXY_TARGET` 環境變數指定；本次沒有更改後端端口或設定。
- 使用 `localhost` 開啟頁面，沿用後端已允許的前端來源；不要自行換端口或改用 `127.0.0.1` 網址。
- 登入／註冊會呼叫真實 API，Cookie 隨請求送出。正式版沒有「示範登入」或本機帳號資料回退。
- 此資料夾上一層的 `compose.yaml` 與 `backend/` 是早期後端骨架，沒有主專案的完整 API；請勿用它們替代現行後端。

## 已完成的呈現調整

- 登入：雙欄版型、蔬果圖、登入／註冊頁籤與密碼顯示切換；確認密碼只在前端檢查，API 仍只接收 Email 和密碼。
- 首頁：蔬果主視覺、開始規劃、繼續草稿、歷史與家庭入口；草稿和成員均從既有 API 讀取。
- 共用介面：橘色主操作、淡紫狀態與頁籤、白色圓角卡片、字體、表單與導覽；保留食譜庫、營養與採購紀錄、飲食小幫手。
- 結果：整理成食譜／採購／營養／分餐四個頁籤。頁籤切換不卸載內容，不因此重送營養或知識生成請求。
- 分餐：只呈現後端實際提供的分配比例與主食量。正式生成結果沒有每人主食重量時顯示「—」，不以示範熟食量或生重推算。
- 問卷沿用既有五步驟、版本控制、草稿保存及錯誤處理；這次沒有改成示範版的兩步驟流程。

## 與原始前端的關係

正式版本位於 `src/live/`，以主專案 `frontend/src/` 的本機 `main@835b4e7` 為基底，複製後在此目錄獨立修改。它不會在執行時引用或寫回主專案的前端原始碼。

`src/live/api/`、辨識、工作輪詢、問卷儲存及成員表單保留原始契約；畫面使用後端回傳的數值、版本、採用資格和缺值狀態。熱量 ≤110%、蛋白質 ≥90% 的現行規則保持一致。

Vite 與 Caddy 設定沿用現有正式前端，包含管理 API／內部 API 的既有邊界。原始 `frontend/`、後端、主專案 Compose、設定與 GitHub 均未修改。

## 原示範版

```sh
npm run dev:demo
```

開啟 **http://localhost:5175/**。此模式保留原本的 `sessionStorage`／`localStorage` 與示範服務，不會取代正式資料。詳細功能與素材來源請見 [README.demo.md](README.demo.md) 與 [PHOTO_CREDITS.md](PHOTO_CREDITS.md)。

```sh
npm run build:demo
```

輸出到 `dist-demo/`，不覆蓋正式版的 `dist/`。

## 驗證

```sh
npm test
npm run build
```

包含原有 22 項示範測試，以及 6 項隔離／正式契約測試。測試會檢查原專案 974 個受 Git 追蹤檔案的 SHA-256 是否與施工前相同、保留原有 API 方法與路徑、Cookie／SSE／輪詢模組、營養界線及未知值語意。

`npm run build` 輸出正式版 `dist/`；`npm run preview -- --host localhost --port 5173 --strictPort` 只能預覽靜態檔案，Vite preview 不提供 `/api` 代理。正式連線開發請使用 `npm run dev`，或透過已配置後端服務的 Caddy 部署。

本次實際檢查與限制見 [整合驗證紀錄](../docs/FRONTEND-INTEGRATION-VALIDATION.md)。
