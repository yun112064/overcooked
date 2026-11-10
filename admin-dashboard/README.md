# Overcooked Admin Dashboard

獨立的管理網站，提供食譜與 AI 候選審核、食材與價格管理、版本發布與啟用、任務／連線監控、角色權限和管理稽核。共用既有後端 `/admin/v1` 契約，不需要建立或選擇家庭。

## 開發

使用 Node.js 24，在此目錄執行：

```sh
npm ci
npm run dev
```

瀏覽 `http://admin.localhost:5175`，API 預設轉送到 `http://127.0.0.1:8000`；可用 `VITE_API_PROXY_TARGET` 指向已確認的隔離後端。後端必須把該管理網站的完整來源加入 `ALLOWED_ORIGINS`。不要只為測試開啟正式資料庫的管理寫入。

登入使用既有帳號服務，沒有自助註冊或自動授予管理權限。帳號的既有有效角色決定可見頁面與可執行操作；權限由後端逐次檢查。未授權帳號會看到拒絕訊息。登入過期時清除前端快取並返回登入畫面；登出失敗時不假裝已成功登出。

開發時一般使用者網站用 `localhost:5173`、管理網站用 `admin.localhost:5175`。**不同 port 不會隔離 Cookie**，所以請使用不同 hostname。若系統無法解析 `admin.localhost`，需自行配置該名稱指向 `127.0.0.1`；不要把管理網站改成與使用者網站相同 hostname。正式環境使用不同 HTTPS hostname，維持 host-only、HttpOnly、Secure Session Cookie。兩個網站共用帳號，但各自登入／登出。

## 建置及部署

```sh
npm run lint
npm run build
```

代理邊界回歸會同時啟動兩個開發伺服器與合成 API：先執行 `npm ci --prefix ../frontend` 安裝主前端測試依賴，再執行 `npm test`。不會連線正式後端。

瀏覽器驗收：先 `npm run build`，再 `npm run test:browser`。需安裝 Chrome，非預設安裝位置可設定 `CHROME_PATH`。測試啟動隱藏瀏覽器與合成 HTTP API，涵蓋登入、拒絕未授權帳號、唯讀角色、登入過期、登出成功／失敗、不同 hostname 的 Cookie 隔離及手機版面。截圖與暫存 profile 寫入專案忽略目錄 `.test-runtime/`；這項測試不取代真實後端／資料庫整合驗收。

Docker 建置目錄為本目錄。Compose 的 `admin` profile 提供 `admin-dashboard` 服務，對應 `http://admin.localhost:8082`，不是預設啟動項目。部署前須確認後端／資料庫目標、`ALLOWED_ORIGINS` 和 Cookie 設定。`npm run preview` 只預覽靜態頁面，沒有 API 代理；完整操作使用開發伺服器或 Caddy。

管理網站的代理只開放 `/auth/login`、`/auth/logout`、`/auth/me` 與 `/admin/v1`。主網站不提供管理選單、管理頁面或管理 API 代理。代理限制不能取代後端授權；部署時不可另行公開未受保護的後端入口。

## 保持的資料規則

- 編輯、驗證、獨立審核、發布和啟用是不同動作；不因網站拆分而自動核准。
- 原始來源、未知數值、版本綁定、人工價格優先與歷史快照保持既有規則。
- 搬移管理元件不會執行 migration、啟用排程、擷取來源或呼叫模型。
- 管理功能維持由後端設定與角色授權控制；部署本網站不等於開啟寫入權限。
