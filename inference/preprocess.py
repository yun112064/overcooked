"""推論前的 OpenCV 影像前處理（T09）。

前處理必須與模型訓練時完全相同，否則線上表現會偏離測試集結果。
denoise 與 yolo_training/scripts/opencv_food41_experiment.py 的 "denoise"
（中值濾波 3×3）一致；主力模型就是用這份前處理後的資料訓練與評估。
"""
from collections.abc import Callable

import cv2
import numpy as np

PREPROCESSORS: dict[str, Callable[[np.ndarray], np.ndarray]] = {
    # 原圖，不做處理（僅供比較或評估用）
    "none": lambda image: image,
    # 中值濾波降噪，kernel 3×3：去除手機拍攝的雜訊點，同時保留邊緣
    "denoise": lambda image: cv2.medianBlur(image, 3),
}

DESCRIPTIONS = {
    "none": "Original image, no OpenCV transform",
    "denoise": "Median-filter denoising, kernel=3x3 (cv2.medianBlur)",
}


def decode(data: bytes) -> np.ndarray:
    """把上傳的影像位元組解碼成 BGR 陣列；無法解碼時拋出 ValueError。"""
    if not data:
        raise ValueError("empty image")
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("cannot decode image")
    return image


def apply(image: np.ndarray, mode: str) -> np.ndarray:
    try:
        return PREPROCESSORS[mode](image)
    except KeyError:
        raise ValueError(f"unknown preprocess mode {mode!r}") from None
