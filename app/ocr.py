# Muggle Word Cards — OCR 模块
# -*- coding: utf-8 -*-
# 把照片转成文字。默认用本地免费的 Tesseract，不需要联网、不花钱。
# 以后想换云端 OCR（识别率更高，尤其手写/模糊照片），直接实现下面的
# ocr_with_cloud() 即可，主流程（main.py）不用改。
from __future__ import annotations

import io


class OCRError(Exception):
    """OCR 失败时抛这个错，main.py 会把它转成 502 返回给前端。"""


def ocr_image(image_bytes: bytes, lang: str = "eng") -> str:
    """用本地 Tesseract 识别一张图片里的英文文字。

    依赖：pip 装 pytesseract + Pillow，以及系统里装 tesseract 可执行文件。
    macOS: brew install tesseract；Ubuntu/Debian: sudo apt install tesseract-ocr。
    没装的话会抛 OCRError，错误信息里会告诉用户怎么装。
    """
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        raise OCRError(
            "OCR 依赖未安装：请执行 pip install pytesseract Pillow，"
            "并确认系统里装了 tesseract（brew install tesseract / apt install tesseract-ocr）。"
        )

    try:
        image = Image.open(io.BytesIO(image_bytes))
    except Exception as exc:
        raise OCRError(f"图片无法打开：{exc}")

    # 手机照片动辄几千万像素，免费版小服务器上 Tesseract 跑大图特别慢。
    # 先在服务端也压一下：最长边超过 2000 像素就等比缩小，并转灰度，
    # 英文印刷体识别几乎不受影响，速度能快好几倍。
    try:
        if max(image.size) > 2000:
            ratio = 2000 / max(image.size)
            image = image.resize(
                (int(image.width * ratio), int(image.height * ratio))
            )
        if image.mode != "L":
            image = image.convert("L")
    except Exception as exc:
        raise OCRError(f"图片预处理失败：{exc}")

    try:
        return pytesseract.image_to_string(image, lang=lang)
    except pytesseract.TesseractNotFoundError:
        raise OCRError(
            "找不到 tesseract 可执行文件：请安装 Tesseract OCR"
            "（brew install tesseract / apt install tesseract-ocr），"
            "或把 OCR_PROVIDER 改成云端方案（见 ocr_with_cloud）。"
        )


def ocr_with_cloud(image_bytes: bytes, provider: str = "") -> str:
    """【预留桩】云端 OCR 接入点，以后在这里接第三方 OCR 服务。

    怎么接（以腾讯云 / 阿里云 OCR 为例）：
    1. 在对应平台开通 OCR 服务，拿到 SecretId/SecretKey（放 .env，不进代码）；
    2. 在这里用官方 SDK 把 image_bytes 发过去，返回识别出的文字；
    3. 在 main.py 里根据 OCR_PROVIDER 环境变量选择调 ocr_image 还是这个函数。

    现在是桩，直接抛错，防止误以为已实现。
    """
    raise NotImplementedError(
        f"云端 OCR（provider={provider!r}）尚未接入。"
        "请在 app/ocr.py 的 ocr_with_cloud() 里按注释实现，或继续用本地 Tesseract。"
    )
