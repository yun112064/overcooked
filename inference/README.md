# 團隊電腦 YOLO 推論服務（T09）

雲端後端把使用者上傳、已轉正並重新編碼的照片送到這裡；本服務先做與訓練相同的 OpenCV 前處理，再以 YOLO26s 偵測，回傳全部偵測。雲端把門檻以上的每種蔬菜各回填一個文字框。

## 目前使用的模型

| 項目 | 內容 |
|---|---|
| 模型 | YOLO26s，Food41 39 類，`yolo_training/runs/yolo26s_opencv_full_20261017/denoise_seed45_50e/weights/best.pt` |
| SHA-256 | `6f72205222e3b20ba0edd477f8f6ad44b1ca0082d5309383e2759cde0cad205f`（啟動時核對，不符就拒絕啟動） |
| 前處理 | `denoise`：`cv2.medianBlur(image, 3)` 中值濾波降噪，與訓練資料相同 |
| 選擇依據 | YOLO26s 各次訓練中，同一份 591 張獨立 test 集 mAP50–95 最高（63.39%；mAP50 82.98%、P 77.79%、R 76.99%） |

完整比較與尚未納入的組合前處理實驗見 [model_manifest.json](model_manifest.json)。**AC22 尚未通過**：整體 precision／recall 仍未達 80%，且需另外報告主要類別 macro 指標。

權重檔不進 Git，請放在 Runtime Root 並以 `INFERENCE_WEIGHTS` 指定。換模型時要同時更新 `model_manifest.json`（雜湊、類別、前處理、指標）與雲端的 `config/recognition_labels.json`，並發行新版本。

## 前處理模式

| 模式 | 說明 |
|---|---|
| `denoise`（預設） | 中值濾波 3×3，去除拍攝雜訊點並保留邊緣；目前模型以此訓練 |
| `none` | 原圖，只供比較或評估 |

預設模式由 manifest 決定，可用 `INFERENCE_PREPROCESS` 改變；單次請求可加 `?preprocess=none` 做評估。回傳的 `model_version` 會帶上實際使用的前處理（例如 `...@6f72205222e3+denoise`），避免混用不同設定的結果。

## 啟動

使用已安裝 Ultralytics 的訓練環境即可，不需另外安裝套件（只用標準函式庫的 HTTP 伺服器）：

```powershell
$env:INFERENCE_TOKEN = "與雲端 RECOGNITION_SERVICE_TOKEN 相同的隨機字串"
$env:INFERENCE_WEIGHTS = "D:\AIPE04期末專題_runtime\models\yolo26s-food41-denoise-seed45-50e.pt"
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
