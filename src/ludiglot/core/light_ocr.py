"""Offline, CPU-only dialogue fallback. Never downloads models at runtime."""
from pathlib import Path
import hashlib


MODEL_HASHES = {
    'ch_PP-OCRv5_det_mobile.onnx': '4d97c44a20d30a81aad087d6a396b08f786c4635742afc391f6621f5c6ae78ae',
    'en_PP-OCRv5_rec_mobile.onnx': 'c3461add59bb4323ecba96a492ab75e06dda42467c9e3d0c18db5d1d21924be8',
}


class LightDialogueOcr:
    def __init__(self, model_root: Path, logger=lambda _: None):
        self.model_root = model_root
        self.logger = logger
        self.engine = None
        self.failed = False

    def prepare_runtime(self):
        """Load optional native imports before WinRT, not OCR model sessions.

        RapidOCR loads its detector/recognizer lazily on the first inference.
        Importing their runtime after Windows OCR can terminate this machine's
        process inside a native library instead of raising a Python exception.
        Prepare on the background initialization thread; keep Windows usable
        if the optional runtime or local model files are unavailable.
        """
        self._initialize()
        return self.engine is not None

    def _initialize(self):
        if self.engine is not None or self.failed:
            return
        try:
            for name, expected in MODEL_HASHES.items():
                path = self.model_root / name
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                    raise ValueError('Thiếu hoặc sai checksum model OCR CPU: '+name)
            from rapidocr import RapidOCR, OCRVersion, ModelType, LangRec
            self.engine = RapidOCR(params={
                'Det.ocr_version': OCRVersion.PPOCRV5, 'Det.model_type': ModelType.MOBILE,
                'Det.model_path': str(self.model_root/'ch_PP-OCRv5_det_mobile.onnx'),
                'Rec.ocr_version': OCRVersion.PPOCRV5, 'Rec.model_type': ModelType.MOBILE,
                'Rec.lang_type': LangRec.EN,
                'Rec.model_path': str(self.model_root/'en_PP-OCRv5_rec_mobile.onnx'),
                'Global.model_root_dir': str(self.model_root), 'Global.use_cls': False,
                'Global.log_level': 'error', 'Global.text_score': .7,
                'EngineConfig.onnxruntime.intra_op_num_threads': 2,
                'EngineConfig.onnxruntime.inter_op_num_threads': 1,
                'EngineConfig.onnxruntime.use_cuda': False,
                'EngineConfig.onnxruntime.use_dml': False,
                'Det.limit_type': 'max', 'Det.limit_side_len': 1536,
            })
            self.logger('OCR dự phòng CPU: PP-OCRv5 English đã sẵn sàng (offline).')
        except Exception as exc:
            self.failed = True
            self.logger(f'Không dùng được OCR dự phòng CPU; giữ Windows OCR: {exc}')

    def recognize(self, image):
        self._initialize()
        if self.engine is None:
            return []
        try:
            result = self.engine(image)
            if result.boxes is None or result.txts is None:
                return []
            return [{'text': str(text), 'box': points.tolist()}
                    for text, points in zip(result.txts, result.boxes)]
        except Exception as exc:
            # Bound retries after a broken native runtime; don't spin each tick.
            self.engine = None; self.failed = True
            self.logger(f'OCR dự phòng CPU lỗi; giữ Windows OCR: {exc}')
            return []
