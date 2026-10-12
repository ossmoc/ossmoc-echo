# Offline CPU OCR models

English PP-OCRv5 mobile recognition and PP-OCRv5 mobile detection originate
from PaddlePaddle/PaddleOCR, deployed as ONNX models by RapidAI/RapidOCR.
See the upstream Apache-2.0 licenses and model documentation:

- https://github.com/PaddlePaddle/PaddleOCR/blob/main/LICENSE
- https://github.com/RapidAI/RapidOCR/blob/main/LICENSE
- https://github.com/RapidAI/RapidOCR/blob/main/python/rapidocr/default_models.yaml

Pinned conversion release: RapidOCR 3.10.0. Download sources:

- https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/onnx/PP-OCRv5/det/ch_PP-OCRv5_det_mobile.onnx
- https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/onnx/PP-OCRv5/rec/en_PP-OCRv5_rec_mobile.onnx

SHA-256 values are pinned in `src/ludiglot/core/light_ocr.py`. Put these two
models in this directory before building the portable EXE. Runtime checks
their hashes, uses explicit local paths and CPU inference, and never downloads
models. Screenshots stay on the user's machine. Third-party Python runtime
licenses are copied to the portable `licenses` directory by the build tool.
