# 團隊電腦 YOLO 推論服務（T09）

雲端後端把使用者上傳、已轉正並重新編碼的照片送到這裡；本服務先做與訓練相同的 OpenCV 前處理，再以 YOLO26s 偵測，回傳全部偵測。雲端把門檻以上的每種蔬菜各回填一個文字框。

## 目前使用的模型

| 項目 | 內容 |
|---|---|
| 模型 | YOLO26s，Food41 39 類，權重 `inference/models/yolo26s-food41-denoise-veg3-ft-20261108.pt`（20 MB，已放在 Git；來源 `yolo_training/runs/yolo26s_denoise_veg3_finetune_20261108/v2_oi_cabbage_from_denoise_seed45_30e/weights/last.pt`） |
| SHA-256 | `d04d4b5015e2f43b0f30824c23ec2c3acb10c6b1c76e9246271af281f71405ca`（啟動時核對，不符就拒絕啟動） |
| 前處理 | `denoise`：`cv2.medianBlur(image, 3)` 中值濾波降噪，與訓練資料相同 |
| 來歷 | 由前一版 `yolo26s-food41-denoise-seed45-50e` 微調 30 epochs：保留完整 Food41 訓練集，加入 LVIS 番茄／胡蘿蔔與人工挑選的 Open Images 結球高麗菜 |
| 選擇依據 | 前一版在家中實拍照片認不出番茄、把胡蘿蔔判成辣椒、高麗菜判成福山萵苣；本版三張實拍照片三種蔬菜都正確（高麗菜遠拍僅 0.28）。代價是 591 張 test 集 mAP50–95 由 63.39% 降為 63.11%（mAP50 82.05%、P 80.57%、R 76.39%） |

微調資料、實拍檢查與各類別變化見 [model_manifest.json](model_manifest.json)。**AC22 尚未通過**：precision 已達 80%，recall 仍未達，且需另外報告主要類別 macro 指標。高麗菜實拍辨識仍不穩，需要更多實拍照片補強。

權重檔放在 `inference/models/`，隊友下載分支即可使用；其他訓練產生的 `*.pt` 仍不進 Git。換模型時把新權重放進 `inference/models/`，並同時更新 `model_manifest.json`（`weights_path`、雜湊、類別、前處理、指標）與雲端的 `config/recognition_labels.json`，再發行新版本。

## 前處理模式

| 模式 | 說明 |
|---|---|
| `denoise`（預設） | 中值濾波 3×3，去除拍攝雜訊點並保留邊緣；目前模型以此訓練 |
| `none` | 原圖，只供比較或評估 |

預設模式由 manifest 決定，可用 `INFERENCE_PREPROCESS` 改變；單次請求可加 `?preprocess=none` 做評估。回傳的 `model_version` 會帶上實際使用的前處理（例如 `...@6f72205222e3+denoise`），避免混用不同設定的結果。

## 啟動

本機開發直接雙擊 `start-dev.cmd`：腳本會自動建立 `.venv-yolo`、安裝 `inference/requirements.txt`（第一次需下載 PyTorch，約數分鐘），再用 Git 裡的權重啟動本服務並接到後端。已有訓練環境可在 `.env.dev` 填 `YOLO_PYTHON`；不需要辨識時設 `DEV_SKIP_YOLO=1`。

手動啟動時 `INFERENCE_WEIGHTS` 可省略（預設為 manifest 的 `weights_path`）；服務只用標準函式庫的 HTTP 伺服器：

```powershell
$env:INFERENCE_TOKEN = "與雲端 RECOGNITION_SERVICE_TOKEN 相同的隨機字串"
$env:INFERENCE_DEVICE = "0"   # GPU；未設定時用 cpu
D:\AIPE04期末專題\yolo_training\.venv\Scripts\python.exe inference\server.py
```

服務預設只聽 `127.0.0.1:8765`。雲端連線須經加密通道並驗證（T17 負責），不要直接把這個埠開到外網。服務不記錄請求內容或照片。

- `GET /v1/health`：模型版本、前處理、類別數（需 token）
- `POST /v1/detect`：multipart 欄位 `image`；回傳 `model_version`、`preprocess`、`inference_ms` 與 `detections`（`label`、`confidence`、`box`）

## 測試

```powershell
python -m unittest discover -s inference -p "test_*.py" -v
```

測試以假模型驗證前處理與 HTTP 契約，只需要 `opencv-python-headless` 與 `numpy`。2026-10-18 另以真實權重在 CPU 上，經雲端後端的轉接器辨識三張 Food41 test 照片：高麗菜、青江菜、胡蘿蔔皆正確（青江菜在不降噪時沒有偵測結果）。這只是串接確認，不是模型品質證據；首次推論約 5 秒（載入暖機），之後約 0.3 秒／張。
