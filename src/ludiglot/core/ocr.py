from __future__ import annotations

import base64
from dataclasses import dataclass
import io
import json
import os
import shutil
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

from ludiglot.infrastructure.proxy_setup import setup_system_proxy
setup_system_proxy()
from typing import Any, Callable, Dict, List, Tuple, Union

import re
import inspect
try:
    from PIL import Image, ImageOps, ImageFilter
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    Image = None
    ImageOps = None
    ImageFilter = None

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    np = None
    HAS_NUMPY = False

@dataclass(frozen=True)
class OcrPipelineResult:
    boxes: List[Dict[str, object]]
    lines: List[Tuple[str, float]]
    backend: str | None


class OCREngine:
    """封装多后端 OCR。"""

    def __init__(
        self,
        lang: str = "en",
        use_gpu: bool = False,
        mode: str | None = None,
        det: bool = True,
        rec: bool = True,
        cls: bool = False,
        *args,
        **kwargs,
    ) -> None:
        self.lang = lang
        self.mode = (mode or ("gpu" if use_gpu else "cpu")).lower()
        self.use_gpu = use_gpu
        self.det = det
        self.rec = rec
        self.cls = cls
        self.ready = False
        self._ocr = None
        self._supports_cls = True
        self.active_gpu = False
        self._windows_ocr = None
        self._windows_ready = False
        self.last_backend: str | None = None
        # Windows OCR tuning toggles (used for benchmarking / ablation)
        self.win_ocr_adaptive = True
        self.win_ocr_refine = True
        self.win_ocr_line_refine = False
        self.win_ocr_preprocess = False
        self.win_ocr_segment = False
        self.win_ocr_multiscale = False
        self._words_segmenter = None
        self._words_segmenter_ready = False
        self._log_callback: Callable[[str], None] | None = None
        self._status_callback: Callable[[str], None] | None = None
        self._prewarm_lock = threading.Lock()
        self._prewarm_started: set[str] = set()

    def set_logger(
        self,
        log_callback: Callable[[str], None] | None = None,
        status_callback: Callable[[str], None] | None = None,
    ) -> None:
        self._log_callback = log_callback
        self._status_callback = status_callback

    def _atomic_write(self, text: str) -> None:
        if not text:
            return
        msg = text if text.endswith("\n") else text + "\n"
        
        # If no callback (CLI), write to stdout
        if not self._log_callback:
            try:
                sys.stdout.write(msg)
                sys.stdout.flush()
            except Exception:
                # Best-effort output; ignore stdout write/flush errors
                pass
        
        # Always invoke callbacks if available
        if self._log_callback:
            try:
                self._log_callback(text)
            except Exception:
                # Log callback errors should not interrupt OCR; ignore them
                pass

    def _emit_log(self, message: str) -> None:
        self._atomic_write(message)

    def _emit_status(self, message: str) -> None:
        if not message:
            return
        # Internal status updates for GLM-OCR get the [OCR] prefix in terminal
        if message.startswith("GLM-OCR"):
            self._atomic_write(f"[OCR] {message}")
        
        if self._status_callback:
            try:
                self._status_callback(message)
            except Exception:
                # Status callback errors should not interrupt OCR; ignore them
                pass

    def _format_exc(self, exc: Exception | None) -> str:
        if exc is None:
            return ""
        msg = str(exc)
        if msg:
            return f"{exc.__class__.__name__}: {msg}"
        return exc.__class__.__name__

    def _format_error(self, stage: str, exc: Exception | None = None) -> str:
        detail = self._format_exc(exc)
        return f"{stage}: {detail}" if detail else stage

    def _shorten_error(self, text: str | None, limit: int = 180) -> str | None:
        if not text:
            return None
        cleaned = str(text).replace("\n", " ").strip()
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[: max(0, limit - 1)] + "…"

    def set_mode(self, mode: str) -> None:
        self.mode = mode.lower()
        self.ready = False
        self._ocr = None

    def _normalize_backend_key(self, backend: str | None) -> str:
        return str(backend or "auto").strip().lower().replace("-", "_")

    def prewarm(self, backend: str | None = None, async_: bool = True) -> None:
        key = self._normalize_backend_key(backend)
        with self._prewarm_lock:
            if key in self._prewarm_started:
                return
            self._prewarm_started.add(key)

        def _worker() -> None:
            self.initialize()

        if async_:
            threading.Thread(target=_worker, daemon=True).start()
        else:
            _worker()

    def initialize(self) -> None:
        """初始化 OCR 引擎。

        策略：
        1. 总是初始化 Windows OCR（如果可用）。
        """
        self._init_windows_ocr()
        self.ready = True

    def _init_windows_ocr(self) -> None:
        """初始化 Windows 原生 OCR 引擎。"""
        if self._windows_ready:
            return
        
        # 尝试导入 WinRT 模块
        try:
            from winrt.windows.globalization import Language
            from winrt.windows.media.ocr import OcrEngine
        except ImportError as e:
            self._emit_log(f"[OCR] Windows OCR 不可用：WinRT 依赖缺失 ({e.__class__.__name__})")
            self._emit_log("[OCR] 提示：可通过 'pip install winrt-Windows.Media.Ocr winrt-Windows.Globalization' 安装")
            self._windows_ready = True
            self._windows_ocr = None
            return
        except Exception as e:
            self._emit_log(f"[OCR] Windows OCR 导入失败：{e.__class__.__name__}: {e}")
            self._windows_ready = True
            self._windows_ocr = None
            return
        
        # 检查可用的语言包
        try:
            available_langs = OcrEngine.available_recognizer_languages
            available_lang_codes = [lang.language_tag for lang in available_langs]
            self._emit_log(f"[OCR] Windows OCR 可用语言包: {', '.join(available_lang_codes) if available_lang_codes else '无'}")
        except Exception as e:
            self._emit_log(f"[OCR] 无法检查语言包：{e}")
            available_lang_codes = []
        
        # 尝试创建 OCR 引擎实例
        try:
            self._emit_log(f"[OCR Config] Requesting Lang: {self.lang}")
            if self.lang.startswith("en"):
                # Try specific US English first
                lang = Language("en-US")
                if not OcrEngine.is_language_supported(lang):
                     self._emit_log("[OCR Config] en-US not supported, checking others...")
                
                self._windows_ocr = OcrEngine.try_create_from_language(lang)
                if self._windows_ocr is None:
                    # Fallback to en-GB if en-US missing (common in some regions)
                    self._emit_log("[OCR] Windows OCR: en-US failed, trying en-GB")
                    lang_gb = Language("en-GB")
                    self._windows_ocr = OcrEngine.try_create_from_language(lang_gb)

                if self._windows_ocr is None:
                    self._emit_log("[OCR] Windows OCR：en-US 语言包未安装")
                    if "en-US" not in available_lang_codes and "en" not in available_lang_codes:
                        self._emit_log("[OCR] 提示：请安装英语语言包")
                        self._emit_log("[OCR]   设置 -> 时间和语言 -> 语言 -> 添加语言 -> English (United States)")
                    self._emit_log("[OCR] 尝试使用系统默认语言包...")
                    self._windows_ocr = OcrEngine.try_create_from_user_profile_languages()
            elif self.lang.startswith("zh"):
                lang = Language("zh-CN")
                self._windows_ocr = OcrEngine.try_create_from_language(lang)
                if self._windows_ocr is None:
                    self._emit_log("[OCR] Windows OCR：zh-CN 语言包未安装")
                    if "zh-CN" not in available_lang_codes and "zh" not in available_lang_codes:
                        self._emit_log("[OCR] 提示：请安装中文语言包")
                        self._emit_log("[OCR]   设置 -> 时间和语言 -> 语言 -> 添加语言 -> 中文(简体，中国)")
                    self._emit_log("[OCR] 尝试使用系统默认语言包...")
                    self._windows_ocr = OcrEngine.try_create_from_user_profile_languages()
            else:
                self._windows_ocr = OcrEngine.try_create_from_user_profile_languages()
            
            if self._windows_ocr is None:
                self._emit_log("[OCR] Windows OCR 不可用：系统未安装任何 OCR 语言包")
                self._emit_log("[OCR] 请在 Windows 设置中安装语言包：")
                self._emit_log("[OCR]   设置 -> 时间和语言 -> 语言 -> 添加语言")
            else:
                lang_tag = self._windows_ocr.recognizer_language.language_tag if self._windows_ocr.recognizer_language else "unknown"
                self._emit_log(f"[OCR] Windows OCR 初始化成功 (使用语言: {lang_tag})")
        except Exception as e:
            self._emit_log(f"[OCR] Windows OCR 初始化失败：{e.__class__.__name__}: {e}")
            self._windows_ocr = None
        
        self._windows_ready = True

    def _pil_to_png_bytes(self, image: Any) -> bytes | None:
        if not HAS_PIL or Image is None or image is None:
            return None
        try:
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            return buf.getvalue()
        except Exception:
            return None



    def _windows_ocr_recognize_boxes(self, image_path: str | Path) -> List[Dict[str, object]]:
        """使用 Windows 原生 OCR 识别图片中的文本。
        
        注意：WinRT 异步操作在GUI线程（STA）中可能失败，因此在单独线程中执行。
        """
        # 读取文件内容直接传给字节流处理方法
        try:
            data = Path(image_path).read_bytes()
            return self._windows_ocr_recognize_from_bytes(data)
        except Exception as e:
            self._emit_log(f"[OCR] 读取文件失败：{e}")
            return []

    def _preprocess_windows_input(self, image_input: Union[str, Path, Any, tuple]) -> bytes | None:
        """轻量预处理：灰度 + autocontrast，输出 PNG bytes."""
        if not HAS_PIL or Image is None:
            return None
        try:
            pil_img = None
            if isinstance(image_input, (str, Path)):
                pil_img = Image.open(str(image_input))
            elif isinstance(image_input, tuple) and len(image_input) == 3:
                r_bytes, r_w, r_h = image_input
                pil_img = Image.frombytes("RGBA", (int(r_w), int(r_h)), r_bytes, "raw", "BGRA")
            elif isinstance(image_input, Image.Image):
                pil_img = image_input
            else:
                return None

            if pil_img.mode != "L":
                pil_img = pil_img.convert("L")
            try:
                from PIL import ImageOps
                pil_img = ImageOps.autocontrast(pil_img)
            except Exception:
                # Autocontrast is an optional enhancement; if it fails, continue with the original image
                pass
            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            return buf.getvalue()
        except Exception:
            return None

    def _get_words_segmenter(self):
        if self._words_segmenter_ready:
            return self._words_segmenter
        self._words_segmenter_ready = True
        try:
            from winrt.windows.data.text import WordsSegmenter
            lang = "en-US" if str(self.lang).startswith("en") else str(self.lang)
            self._words_segmenter = WordsSegmenter(lang)
        except Exception as e:
            self._emit_log(f"[OCR] WordsSegmenter unavailable: {e.__class__.__name__}: {e}")
            self._words_segmenter = None
        return self._words_segmenter

    def _segment_with_words_segmenter(self, text: str) -> str:
        segmenter = self._get_words_segmenter()
        if not segmenter:
            return text
        try:
            tokens = list(segmenter.get_tokens(text))
        except Exception:
            return text
        if not tokens:
            return text
        parts: list[str] = []
        for tok in tokens:
            t = getattr(tok, "text", "") or ""
            if not t:
                continue
            if not parts:
                parts.append(t)
                continue
            prev = parts[-1]
            if prev and prev[-1].isalnum() and t[0].isalnum():
                parts.append(" " + t)
            else:
                parts.append(t)
        return "".join(parts).strip()


    def _parse_winrt_result(self, result) -> List[Dict[str, object]]:
        lines_list = []
        if not result or not getattr(result, "lines", None):
            return lines_list
        
        for line in result.lines:
            text = getattr(line, "text", "") or ""
            if not text.strip():
                continue
            
            words = getattr(line, "words", None)
            if not words or len(list(words)) == 0:
                # Fallback if no words but text exists (rare)
                box = [[0, 0], [100, 0], [100, 30], [0, 30]]
                lines_list.append({"text": text.strip(), "conf": 0.92, "box": box, "geometry_valid": False})
                continue
            
            word_list = []
            for word in words:
                w_text = getattr(word, "text", "").strip()
                rect = getattr(word, "bounding_rect", None)
                if not w_text or not rect:
                    continue
                word_list.append({
                    "text": w_text,
                    "x": rect.x,
                    "y": rect.y,
                    "width": rect.width,
                    "height": rect.height
                })
            
            if not word_list:
                continue

            # 行内分组逻辑
            groups = []
            current_group = [word_list[0]]
            for i in range(1, len(word_list)):
                prev = word_list[i-1]
                curr = word_list[i]
                gap = curr["x"] - (prev["x"] + prev["width"])
                
                # 文档建议：基于字符高度动态调整阈值
                threshold = max(50, curr["height"] * 2.5)
                if gap > threshold:
                    groups.append(current_group)
                    current_group = [curr]
                else:
                    current_group.append(curr)
            groups.append(current_group)
            
            for group in groups:
                g_text = " ".join([w["text"] for w in group])
                min_x = min(w["x"] for w in group)
                min_y = min(w["y"] for w in group)
                max_x = max(w["x"] + w["width"] for w in group)
                max_y = max(w["y"] + w["height"] for w in group)
                box = [[int(min_x), int(min_y)], [int(max_x), int(min_y)], 
                       [int(max_x), int(max_y)], [int(min_x), int(max_y)]]
                lines_list.append({"text": g_text, "conf": 0.92, "box": box})
        
        return lines_list



    def _windows_ocr_recognize_from_bytes(self, image_bytes: bytes) -> List[Dict[str, object]]:
        """使用 Windows OCR 从内存字节流识别文本（避免硬盘读写）。
        
        Args:
            image_bytes: PNG/JPEG 格式的图片字节流
            
        Returns:
            识别结果列表
        """
        self._init_windows_ocr()
        if self._windows_ocr is None:
            return []
        
        result_container = {"lines": [], "error": None}
        
        # 定义质量检测函数
        def _check_quality(lines):
            if not lines: return 0.0
            total_len = 0
            valid_chars = 0
            is_english = self.lang.startswith("en")
            
            for line in lines:
                text = line.get("text", "")
                total_len += len(text)
                for ch in text:
                    is_valid = False
                    # 总是允许常用标点
                    if ch in " .,!?'\":;-()[]":
                        is_valid = True
                    elif is_english:
                        # 英文模式下，要求 ASCII 字符
                        if ch.isascii() and ch.isalnum():
                            is_valid = True
                    else:
                        # 其他语言（如中文），只要是字母数字即可
                        if ch.isalnum():
                            is_valid = True
                    
                    if is_valid:
                        valid_chars += 1
            
            if total_len == 0: return 0.0
            return valid_chars / total_len




        def _ocr_worker():
            # print("[OCR Debug] _ocr_worker started", flush=True)
            try:
                from winrt.windows.storage.streams import InMemoryRandomAccessStream, DataWriter
                from winrt.windows.graphics.imaging import BitmapDecoder, SoftwareBitmap, BitmapPixelFormat, BitmapAlphaMode
                # print("[OCR Debug] WinRT imports successful", flush=True)
            except ImportError as e:
                # print(f"[OCR Debug] WinRT ImportError: {e}", flush=True)
                result_container["error"] = "WinRT模块导入失败"
                return
            except Exception as e:
                # print(f"[OCR Debug] WinRT Import Exception: {e}", flush=True)
                result_container["error"] = f"模块导入错误 - {e.__class__.__name__}"
                return

            def _ensure_bgra8(bmp):
                """Ensure bitmap is Bgra8 for optimal OCR performance on screenshots."""
                try:
                    target_format = BitmapPixelFormat.BGRA8
                    if bmp.bitmap_pixel_format != target_format:
                         # Use 2-argument convert (source, format) to avoid invalid parameter count
                         return SoftwareBitmap.convert(bmp, target_format)
                except Exception as e:
                    print(f"[OCR] _ensure_bgra8 warning: {e}")
                return bmp

            # _parse_ocr_result moved to self._parse_winrt_result
            
            def _recognize_bytes(data_input, try_invert=True):
                # print("[OCR Debug] _recognize_bytes started", flush=True)
                try:
                    bitmap = None
                    # Support RAW BGRA tuple: (bytes, width, height)
                    if isinstance(data_input, tuple) and len(data_input) == 3:
                         raw_bytes, w, h = data_input
                         try:
                             # Create bitmap from raw bytes via DataWriter (requires copying to WinRT buffer)
                             writer = DataWriter()
                             writer.write_bytes(raw_bytes)
                             buf = writer.detach_buffer()
                             bitmap = None
                             # Some WinRT versions expose different overloads; try a few safe variants.
                             try:
                                 bitmap = SoftwareBitmap.create_copy_from_buffer(
                                     buf, BitmapPixelFormat.BGRA8, w, h, BitmapAlphaMode.PREMULTIPLIED
                                 )
                             except Exception:
                                 try:
                                     bitmap = SoftwareBitmap.create_copy_from_buffer(
                                         buf, BitmapPixelFormat.BGRA8, w, h
                                     )
                                 except Exception:
                                     try:
                                         bitmap = SoftwareBitmap.create_copy_from_buffer(
                                             buf, BitmapPixelFormat.BGRA8, w, h, BitmapAlphaMode.IGNORE
                                         )
                                     except Exception:
                                         bitmap = None
                         except Exception as e:
                             print(f"[OCR] RAW Bitmap creation failed: {e}")
                             bitmap = None
                         if bitmap is None and HAS_PIL and Image is not None:
                             # Fallback: convert raw BGRA to PNG bytes, then decode as encoded image
                             try:
                                 pil_img = Image.frombytes("RGBA", (w, h), raw_bytes, "raw", "BGRA")
                                 buf = io.BytesIO()
                                 pil_img.save(buf, format="PNG")
                                 data_input = buf.getvalue()
                             except Exception as e:
                                 print(f"[OCR] RAW->PNG fallback failed: {e}")
                                 return []
                    else:
                        # Fallback to PNG/Encoded bytes
                        stream = InMemoryRandomAccessStream()
                        writer = DataWriter(stream)
                        writer.write_bytes(data_input)
                        writer.store_async().get()
                        writer.flush_async().get()
                        writer.detach_stream()
                        stream.seek(0)
                        
                        decoder = BitmapDecoder.create_async(stream).get()
                        bitmap = decoder.get_software_bitmap_async().get()
                        # Ensure Bgra8 for general compatibility
                        bitmap = _ensure_bgra8(bitmap)

                    if not self._windows_ocr:
                        return []
                        
                    # Pass 1: Normal Recognition
                    try:
                        result = self._windows_ocr.recognize_async(bitmap).get()
                        lines = self._parse_winrt_result(result)
                    except Exception as e:
                        self._emit_log(f"[OCR] RecognizeAsync failed: {e}")
                        return []
                    
                    # print(f"[OCR Debug] Normal Pass Lines: {len(lines)}", flush=True)
                    
                    # Pass 2: Inverted Logic (Dual-Pass Strategy)
                    # DOCUMENTATION: "OCR 引擎对黑底白字识别能力弱，必须使用双通逻辑"
                    # Always try invert if enabled, then pick the best result.
                    # Pass 2: Inverted Logic (Dual-Pass Strategy with Spatial Fusion)
                    if try_invert and HAS_PIL and Image is not None:
                        try:
                            # 1. Generate Inverted Image
                            # If input is raw tuple logic:
                            pil_img = None
                            if isinstance(data_input, tuple):
                                r_bytes, r_w, r_h = data_input
                                pil_img = Image.frombytes("RGBA", (r_w, r_h), r_bytes, "raw", "BGRA")
                            else:
                                pil_img = Image.open(io.BytesIO(bytes_data))
                                
                            if pil_img.mode == 'RGBA':
                                pil_img = pil_img.convert('RGB')
                                
                            from PIL import ImageOps
                            inv_img = ImageOps.invert(pil_img)
                            
                            # Convert back to raw bytes for efficiency if using raw path
                            # (currently we use PNG bytes directly)
                            buf = io.BytesIO()
                            inv_img.save(buf, format='PNG')
                            inv_bytes = buf.getvalue()
                            
                            # 2. Recognize Inverted
                            inv_lines = _recognize_bytes(inv_bytes, try_invert=False)
                            
                            if not inv_lines:
                                return lines
                                
                            if not lines:
                                return inv_lines
                                
                            # 3. Spatial Fusion (IoU Strategy)
                            fused_lines = list(lines) # Start with normal lines
                            
                            def calc_iou(box1, box2):
                                # box: [[x1,y1], [x2,y1], [x2,y2], [x1,y2]]
                                b1_x1, b1_y1 = box1[0]
                                b1_x2, b1_y2 = box1[2]
                                b2_x1, b2_y1 = box2[0]
                                b2_x2, b2_y2 = box2[2]
                                
                                xx1 = max(b1_x1, b2_x1)
                                yy1 = max(b1_y1, b2_y1)
                                xx2 = min(b1_x2, b2_x2)
                                yy2 = min(b1_y2, b2_y2)
                                
                                w = max(0.0, xx2 - xx1)
                                h = max(0.0, yy2 - yy1)
                                inter = w * h
                                
                                area1 = (b1_x2 - b1_x1) * (b1_y2 - b1_y1)
                                area2 = (b2_x2 - b2_x1) * (b2_y2 - b2_y1)
                                union = area1 + area2 - inter
                                
                                return inter / union if union > 0 else 0.0

                            # Iterate inverted lines and merge into fused_lines
                            for inv_line in inv_lines:
                                inv_box = inv_line.get('box')
                                if not inv_box: continue
                                
                                best_iou = 0.0
                                match_idx = -1
                                
                                for i, norm_line in enumerate(fused_lines):
                                    norm_box = norm_line.get('box')
                                    if not norm_box: continue
                                    iou = calc_iou(inv_box, norm_box)
                                    if iou > best_iou:
                                        best_iou = iou
                                        match_idx = i
                                
                                # Strategy:
                                # High Overlap (>0.3): Conflict. Pick higher confidence or cleaner text.
                                # No Overlap: Add as new text (found only in inverted).
                                if best_iou > 0.3:
                                    # Conflict Resolution
                                    norm_item = fused_lines[match_idx]
                                    
                                    # Preference logic:
                                    # 1. Valid words count (avoid garbage like ".,' ")
                                    # 2. Confidence
                                    def count_valid_chars(t): return sum(c.isalnum() for c in t)
                                    norm_valid = count_valid_chars(norm_item.get('text', ''))
                                    inv_valid = count_valid_chars(inv_line.get('text', ''))
                                    
                                    norm_conf = norm_item.get('conf', 0)
                                    inv_conf = inv_line.get('conf', 0)
                                    
                                    # If inverted has significantly better valid content or confidence
                                    if inv_valid > norm_valid * 1.5 or (inv_valid >= norm_valid and inv_conf > norm_conf + 0.1):
                                        fused_lines[match_idx] = inv_line # Replace
                                else:
                                    # No overlap, assume it's white-on-black text missed by normal pass
                                    fused_lines.append(inv_line)
                                    
                            return fused_lines

                        except Exception as e:
                            # print(f"[OCR] Invert pass fusion failed: {e}")
                            pass
                    
                    return lines
                except Exception as e:
                    import traceback
                    # Only print full traceback to terminal if absolutely needed for debugging
                    # traceback.print_exc()
                    self._emit_log(f"[OCR] Internal Error in _recognize_bytes: {e}")
                    return []

            def _text_score(text: str) -> float:
                text = (text or "").strip()
                if not text:
                    return -1e9
                valid = sum(1 for ch in text if ch.isascii() and (ch.isalnum() or ch in " -'"))
                ratio = valid / max(len(text), 1)
                vowels = set("aeiouyAEIOUY")
                max_cluster = 0
                cluster = 0
                for ch in text:
                    if ch.isalpha() and ch not in vowels:
                        cluster += 1
                        if cluster > max_cluster:
                            max_cluster = cluster
                    else:
                        cluster = 0
                penalty = max(0, max_cluster - 2) * 0.1
                length_penalty = 0.01 * max(0, len(text) - 12)
                return ratio - penalty - length_penalty

            def _line_score(text: str) -> float:
                text = (text or "").strip()
                if not text:
                    return -1e9
                valid = 0
                weird = 0
                space_count = text.count(" ")
                for ch in text:
                    if ch.isascii() and (ch.isalnum() or ch in " -'.,!?;:"):
                        valid += 1
                    elif ch in "*#@$":
                        weird += 1
                ratio = valid / max(len(text), 1)
                penalty = 0.0
                if text and text[0] in "*•·":
                    penalty += 0.2
                if re.search(r"[A-Za-z]{2,}[:;][A-Za-z]", text):
                    penalty += 0.15
                if re.search(r"[A-Za-z]{2,}[,.][A-Za-z]", text):
                    penalty += 0.1
                if len(text) >= 25:
                    expected_spaces = max(1, len(text) // 8)
                    if space_count < expected_spaces:
                        penalty += 0.1
                penalty += weird * 0.05
                return ratio - penalty

            def _score_lines(lines: List[Dict[str, object]]) -> float:
                if not lines:
                    return -1e9
                texts = [str(x.get("text", "")).strip() for x in lines if str(x.get("text", "")).strip()]
                if not texts:
                    return -1e9
                joined = " ".join(texts)
                scores = [_line_score(t) for t in texts]
                avg_line = sum(scores) / max(len(scores), 1)
                valid = sum(1 for ch in joined if ch.isascii() and (ch.isalnum() or ch in " -'.,!?;:"))
                ratio = valid / max(len(joined), 1)
                words = [w for w in joined.split() if w]
                word_bonus = min(len(words) / 12.0, 1.0) * 0.2
                space_ratio = joined.count(" ") / max(len(joined), 1)
                penalty = 0.0
                if len(joined) > 40 and space_ratio < 0.05:
                    penalty += 0.2
                return avg_line + ratio * 0.5 + word_bonus - penalty

            def _scale_back_boxes(lines: List[Dict[str, object]], scale: float) -> None:
                if not lines or scale == 1.0:
                    return
                for line in lines:
                    box = line.get("box")
                    if not box:
                        continue
                    orig_box = []
                    for pt in box:
                        orig_box.append([int(pt[0] / scale), int(pt[1] / scale)])
                    line["box"] = orig_box

            def _needs_segment(text: str) -> bool:
                text = (text or "").strip()
                if len(text) < 20:
                    return False
                space_ratio = text.count(" ") / max(len(text), 1)
                if space_ratio < 0.05:
                    return True
                if re.search(r"[A-Za-z]{2,}[A-Z][a-z]", text):
                    return True
                if re.search(r"[A-Za-z]{2,}[,.!?;:][A-Za-z]", text):
                    return True
                return False

            def _segment_line(text: str) -> str:
                if not self.win_ocr_segment:
                    return text
                if not _needs_segment(text):
                    return text
                seg = self._segment_with_words_segmenter(text)
                if not seg or seg == text:
                    return text
                if _line_score(seg) >= _line_score(text) + 0.02:
                    return seg
                return text

            def _is_suspicious_line(text: str) -> bool:
                text = (text or "").strip()
                if not text:
                    return False
                if text[0] in "*•·" and len(text) > 6:
                    return True
                if re.search(r"[A-Za-z]{2,}[:;][A-Za-z]", text):
                    return True
                if re.search(r"[A-Za-z]{2,}[,.][A-Za-z]", text):
                    return True
                return False

            def _strip_leading_symbol(text: str) -> str:
                text = (text or "").strip()
                if len(text) >= 2 and text[0] in "*•·" and text[1].isalpha() and text[1].isupper():
                    return text[1:].lstrip()
                return text

            def _refine_short_lines(img_bytes: bytes, lines: List[Dict[str, object]]) -> List[Dict[str, object]]:
                if not lines:
                    return lines
                try:
                    base_img = Image.open(io.BytesIO(img_bytes))
                except Exception:
                    return lines

                refined: List[Dict[str, object]] = []
                for line in lines:
                    text = str(line.get("text", "")).strip()
                    tokens = text.split()
                    if len(tokens) != 1 or len(text) > 12:
                        refined.append(line)
                        continue
                    box = line.get("box")
                    if not box:
                        refined.append(line)
                        continue
                    try:
                        x1 = min(int(p[0]) for p in box)
                        y1 = min(int(p[1]) for p in box)
                        x2 = max(int(p[0]) for p in box)
                        y2 = max(int(p[1]) for p in box)
                    except Exception:
                        refined.append(line)
                        continue

                    pad = max(2, int((y2 - y1) * 0.2))
                    x1 = max(0, x1 - pad)
                    y1 = max(0, y1 - pad)
                    x2 = min(base_img.width, x2 + pad)
                    y2 = min(base_img.height, y2 + pad)
                    if x2 <= x1 or y2 <= y1:
                        refined.append(line)
                        continue

                    crop = base_img.crop((x1, y1, x2, y2))
                    if crop.mode != "L":
                        crop = crop.convert("L")
                    crop = ImageOps.autocontrast(crop, cutoff=10)
                    cw, ch = crop.size
                    if cw > 0 and ch > 0:
                        crop = crop.resize((int(cw * 2.0), int(ch * 2.0)), Image.Resampling.BICUBIC)

                    buf = io.BytesIO()
                    crop.save(buf, format="PNG")
                    alt_lines = _recognize_bytes(buf.getvalue(), try_invert=False)
                    alt_text = None
                    alt_score = -1e9
                    for alt in alt_lines:
                        cand = str(alt.get("text", "")).strip()
                        if not cand:
                            continue
                        score = _text_score(cand)
                        if score > alt_score + 0.01 or (abs(score - alt_score) <= 0.01 and len(cand) > len(alt_text or "")):
                            alt_score = score
                            alt_text = cand

                    if alt_text:
                        orig_score = _text_score(text)
                        if alt_score > orig_score + 0.05:
                            line = {**line, "text": alt_text}
                    refined.append(line)
                return refined

            def _refine_suspicious_lines(img_bytes: bytes, lines: List[Dict[str, object]]) -> List[Dict[str, object]]:
                if not lines:
                    return lines
                try:
                    base_img = Image.open(io.BytesIO(img_bytes))
                except Exception:
                    return lines

                refined: List[Dict[str, object]] = []
                for line in lines:
                    text = str(line.get("text", "")).strip()
                    if not _is_suspicious_line(text):
                        refined.append(line)
                        continue

                    cleaned = _strip_leading_symbol(text)
                    if cleaned != text and _line_score(cleaned) > _line_score(text) + 0.05:
                        line = {**line, "text": cleaned}
                        text = cleaned

                    box = line.get("box")
                    if not box:
                        refined.append(line)
                        continue
                    try:
                        x1 = min(int(p[0]) for p in box)
                        y1 = min(int(p[1]) for p in box)
                        x2 = max(int(p[0]) for p in box)
                        y2 = max(int(p[1]) for p in box)
                    except Exception:
                        refined.append(line)
                        continue

                    pad = max(4, int((y2 - y1) * 0.25))
                    x1 = max(0, x1 - pad)
                    y1 = max(0, y1 - pad)
                    x2 = min(base_img.width, x2 + pad)
                    y2 = min(base_img.height, y2 + pad)
                    if x2 <= x1 or y2 <= y1:
                        refined.append(line)
                        continue

                    crop = base_img.crop((x1, y1, x2, y2))
                    if crop.mode != "L":
                        crop = crop.convert("L")
                    crop = ImageOps.autocontrast(crop, cutoff=8)
                    try:
                        crop = crop.filter(ImageFilter.UnsharpMask(radius=1, percent=150, threshold=3))
                    except Exception:
                        # Sharpening is an optional enhancement; if it fails, continue with the unsharpened crop
                        pass
                    cw, ch = crop.size
                    if cw > 0 and ch > 0 and ch < 80:
                        crop = crop.resize((int(cw * 2.0), int(ch * 2.0)), Image.Resampling.BICUBIC)

                    buf = io.BytesIO()
                    crop.save(buf, format="PNG")
                    alt_lines = _recognize_bytes(buf.getvalue(), try_invert=True)
                    if alt_lines:
                        alt_lines = sorted(alt_lines, key=lambda b: (b["box"][0][1], b["box"][0][0]))
                        cand = " ".join([str(b.get("text", "")).strip() for b in alt_lines if str(b.get("text", "")).strip()])
                    else:
                        cand = ""

                    if cand:
                        if _line_score(cand) > _line_score(text) + 0.05:
                            line = {**line, "text": cand}
                    refined.append(line)
                return refined

            def _refine_line_crops(img_bytes: bytes, lines: List[Dict[str, object]]) -> List[Dict[str, object]]:
                if not lines:
                    return lines
                try:
                    base_img = Image.open(io.BytesIO(img_bytes))
                except Exception:
                    return lines

                refined: List[Dict[str, object]] = []
                for line in lines:
                    text = str(line.get("text", "")).strip()
                    box = line.get("box")
                    if not text or not box:
                        refined.append(line)
                        continue
                    try:
                        x1 = min(int(p[0]) for p in box)
                        y1 = min(int(p[1]) for p in box)
                        x2 = max(int(p[0]) for p in box)
                        y2 = max(int(p[1]) for p in box)
                    except Exception:
                        refined.append(line)
                        continue

                    pad = max(4, int((y2 - y1) * 0.2))
                    x1 = max(0, x1 - pad)
                    y1 = max(0, y1 - pad)
                    x2 = min(base_img.width, x2 + pad)
                    y2 = min(base_img.height, y2 + pad)
                    if x2 <= x1 or y2 <= y1:
                        refined.append(line)
                        continue

                    crop = base_img.crop((x1, y1, x2, y2))
                    if crop.mode != "L":
                        crop = crop.convert("L")
                    crop = ImageOps.autocontrast(crop, cutoff=6)
                    try:
                        crop = crop.filter(ImageFilter.UnsharpMask(radius=1, percent=140, threshold=2))
                    except Exception:
                        # Sharpening is an optional enhancement; if it fails, continue with the unsharpened crop
                        pass
                    cw, ch = crop.size
                    if cw > 0 and ch > 0 and ch < 90:
                        crop = crop.resize((int(cw * 2.0), int(ch * 2.0)), Image.Resampling.BICUBIC)

                    buf = io.BytesIO()
                    crop.save(buf, format="PNG")
                    alt_lines = _recognize_bytes(buf.getvalue(), try_invert=True)
                    if alt_lines:
                        alt_lines = sorted(alt_lines, key=lambda b: (b["box"][0][1], b["box"][0][0]))
                        cand = " ".join([str(b.get("text", "")).strip() for b in alt_lines if str(b.get("text", "")).strip()])
                    else:
                        cand = ""

                    if cand and _line_score(cand) > _line_score(text) + 0.03:
                        line = {**line, "text": cand}
                    refined.append(line)
                return refined

            try:
                # 1. 尝试原始图片
                lines1 = _recognize_bytes(image_bytes)
                score1 = _check_quality(lines1)
                
                final_lines = lines1
                
                # 2. 如果质量低或字号过小，尝试自适应放大 (Text-Grab 策略)
                if self.win_ocr_adaptive and HAS_PIL and Image is not None:
                    # 计算平均字高
                    avg_height = 0
                    word_count = 0
                    for line in lines1:
                         # 这里 line['box'] 是 [[x1,y1], [x2,y1], [x2,y2], [x1,y2]]
                         # 高度 = y2 - y1
                         box = line.get('box')
                         if box:
                             h_val = box[2][1] - box[0][1]
                             avg_height += h_val
                             word_count += 1
                    
                    if word_count > 0:
                        avg_height /= word_count
                    else:
                         avg_height = 20 # 默认假设较小

                    # 目标字高 55px (Target ~2.0x for typical 28px text to ensure clear character features)
                    ideal_height = 55.0
                    scale = 1.0
                    if avg_height < ideal_height:
                        scale = ideal_height / avg_height
                        # Snap to nearest 0.5 increment (e.g. 1.91 -> 2.0, 1.4 -> 1.5) to avoid fractional scaling artifacts
                        scale = round(scale * 2) / 2.0
                        
                        # 限制最大放大倍数
                        scale = min(scale, 3.5)
                        scale = max(scale, 1.0)
                     
                    # print(f"[OCR Debug] Quality Check: AvgHeight={avg_height:.1f}px, ScaleNeeded={scale:.2f}, Score={score1:.2f}")

                    # 如果需要放大 (且并非微小差异)，或者之前的质量评分真的很差
                    # 如果需要放大 (且并非微小差异)，或者之前的质量评分真的很差
                    if scale > 1.2 or score1 < 0.90:
                        try:
                            pil_img = None
                            
                            # Handle Raw Tuple or Bytes
                            if isinstance(image_bytes, tuple):
                                r_bytes, r_w, r_h = image_bytes
                                pil_img = Image.frombytes("RGBA", (r_w, r_h), r_bytes, "raw", "BGRA")
                            else:
                                pil_img = Image.open(io.BytesIO(image_bytes))
                            
                            w, h = pil_img.size
                            new_w, new_h = int(w * scale), int(h * scale)
                            
                            if new_w < 4000 and new_h < 4000:
                                # 使用 BICUBIC 平滑缩放 (Bicubic generally better for text shape than Bilinear if handled correctly)
                                pil_img = pil_img.resize((new_w, new_h), Image.Resampling.BICUBIC)
                                
                                # Convert to grayscale
                                if pil_img.mode != 'L':
                                    pil_img = pil_img.convert('L')
                                
                                # Gamma Correction to thin white text and reduce blooming/halos
                                # Target: Darken midtones to separate characters (fix "NewSolar" -> "New Solar")
                                try:
                                    if HAS_NUMPY and np is not None:
                                        arr = np.array(pil_img)
                                        # Gamma 1.5 (Darkens midtones: 0.5^1.5 = 0.35)
                                        # This thins white text on dark background by pushing gray edges to black
                                        arr_gamma = ((arr / 255.0) ** 1.5) * 255.0
                                        pil_img = Image.fromarray(arr_gamma.astype(np.uint8))
                                except Exception as e:
                                    print(f"[OCR] Gamma correction failed: {e}")
                                
                                buf = io.BytesIO()
                                pil_img.save(buf, format='PNG')
                                new_bytes = buf.getvalue()
                                
                                lines2 = _recognize_bytes(new_bytes)
                                score2 = _check_quality(lines2)
                                
                                # Use helper to get lengths
                                def _get_len(ls): return sum(len(x.get('text', '').strip()) for x in ls)
                                len1 = _get_len(lines1)
                                len2 = _get_len(lines2)

                                # print(f"[OCR Debug] Scaled Result: Score={score2:.2f}, Len={len2} (Original Len={len1})")
                                
                                # Accept if score improves OR if significantly more text is found (1.15x)
                                # Also accept if we scaled significantly and the result is still 'good' (score > 0.85),
                                # because small text often gives high-confidence garbage (e.g. 'I' becomes 'l').
                                if score2 >= score1 * 0.9 or len2 > len1 * 1.1 or (len1 < 10 and len2 > 10) or (scale > 1.3 and score2 > 0.85):
                                    print(f"[OCR] 自适应放大 {scale:.2f}x (AvgH={avg_height:.1f}px) 提升质量: {score1:.2f} -> {score2:.2f} (Len: {len1}->{len2})")
                                    
                                    # CRITICAL FIX: Map coordinates back to original scale
                                    # box is [[x1,y1], [x2,y1], [x2,y2], [x1,y2]]
                                    for line in lines2:
                                        if 'box' in line:
                                            orig_box = []
                                            for pt in line['box']:
                                                orig_box.append([int(pt[0] / scale), int(pt[1] / scale)])
                                            line['box'] = orig_box
                                        # Also scale individual word boxes if they exist
                                        # (Note: _parse_ocr_result implementation currently doesn't attach words to line dict, 
                                        # but if it did, we'd need to scale them too. The current structure is flat lines.)
                                        
                                    final_lines = lines2
                        except Exception as e:
                             print(f"[OCR] 自适应预处理失败: {e}")

                # 2.5 多尺度识别：在不同缩放下识别，选取评分更好的结果
                if self.win_ocr_multiscale and HAS_PIL and Image is not None:
                    try:
                        # Build base image
                        base_img = None
                        if isinstance(image_bytes, tuple):
                            r_bytes, r_w, r_h = image_bytes
                            base_img = Image.frombytes("RGBA", (int(r_w), int(r_h)), r_bytes, "raw", "BGRA")
                        else:
                            base_img = Image.open(io.BytesIO(image_bytes))

                        if base_img is not None:
                            base_w, base_h = base_img.size
                            candidates: list[tuple[float, List[Dict[str, object]]]] = []
                            base_score = _score_lines(final_lines)
                            candidates.append((base_score, final_lines))

                            for scale in (1.25, 1.5, 2.0):
                                new_w, new_h = int(base_w * scale), int(base_h * scale)
                                if new_w < 200 or new_h < 80:
                                    continue
                                if new_w > 4200 or new_h > 4200:
                                    continue
                                try:
                                    resized = base_img.resize((new_w, new_h), Image.Resampling.BICUBIC)
                                    buf = io.BytesIO()
                                    resized.save(buf, format="PNG")
                                    new_bytes = buf.getvalue()
                                    lines_s = _recognize_bytes(new_bytes)
                                    if not lines_s:
                                        continue
                                    _scale_back_boxes(lines_s, scale)
                                    score_s = _score_lines(lines_s)
                                    candidates.append((score_s, lines_s))
                                except Exception:
                                    continue

                            if candidates:
                                best_score, best_lines = max(candidates, key=lambda x: x[0])
                                if best_score > base_score + 0.02:
                                    final_lines = best_lines
                    except Exception as e:
                        print(f"[OCR] 多尺度识别失败: {e}")

                if self.win_ocr_refine and HAS_PIL and Image is not None and isinstance(image_bytes, (bytes, bytearray)):
                    try:
                        final_lines = _refine_short_lines(image_bytes, final_lines)
                    except Exception as e:
                        print(f"[OCR] 短行精修失败: {e}")

                    try:
                        final_lines = _refine_suspicious_lines(image_bytes, final_lines)
                    except Exception as e:
                        print(f"[OCR] 行精修失败: {e}")

                if self.win_ocr_line_refine and HAS_PIL and Image is not None and isinstance(image_bytes, (bytes, bytearray)):
                    try:
                        final_lines = _refine_line_crops(image_bytes, final_lines)
                    except Exception as e:
                        print(f"[OCR] 行裁剪精修失败: {e}")

                if self.win_ocr_segment:
                    try:
                        for line in final_lines:
                            if "text" in line:
                                line["text"] = _segment_line(str(line.get("text", "")))
                    except Exception as e:
                        print(f"[OCR] 分词纠错失败: {e}")
                
                result_container["lines"] = final_lines
                    
            except Exception as e:
                result_container["error"] = f"{e.__class__.__name__}: {str(e)[:100]}"
        
        # 在新线程中执行OCR
        thread = threading.Thread(target=_ocr_worker, daemon=True)
        thread.start()
        thread.join(timeout=10.0)
        
        if thread.is_alive():
            print("[OCR] Windows OCR 超时")
            return []
        
        if result_container["error"]:
            print(f"[OCR] Windows OCR 识别失败：{result_container['error']}")
            return []
        
        lines = result_container["lines"]
        if lines:
            print(f"[OCR] Windows OCR (内存流) 成功识别 {len(lines)} 行文本")
        return lines

    def recognize_from_image(self, image: Union[Any, Any]) -> List[Dict[str, object]]:
        """从内存图像直接识别（OpenCV/PIL），避免硬盘读写。
        
        Args:
            image: OpenCV 图像 (numpy.ndarray) 或 PIL Image
            
        Returns:
            识别结果列表
        """
        if not HAS_PIL or Image is None:
            raise RuntimeError("需要安装 Pillow: pip install Pillow")
        
        # 转换为 PIL Image
        if np is not None and isinstance(image, np.ndarray):
            # OpenCV 图像 (BGR) → PIL Image (RGB)
            if len(image.shape) == 3 and image.shape[2] == 3:
                image = Image.fromarray(image[:, :, ::-1])  # BGR to RGB
            else:
                image = Image.fromarray(image)
        elif not isinstance(image, Image.Image):
             # 尝试直接处理，或者报错
             pass
        
        # 转换为字节流 (Optimization: Check if we can use raw path)
        if isinstance(image, Image.Image):
             # 暂时禁用 raw path，因为容易遇到参数错误，PNG 编码足够快且稳定
             pass

        buffer = io.BytesIO()
        image.save(buffer, format='PNG')
        image_bytes = buffer.getvalue()
        
        # 使用内存流识别
        return self._windows_ocr_recognize_from_bytes(image_bytes)

    def recognize(self, image_path: str | Path) -> List[str]:
        lines = self.recognize_with_confidence(image_path)
        return [text for text, _ in lines]

    def recognize_with_confidence(
        self,
        image_path: str | Path,
        backend: str | None = None,
    ) -> List[Tuple[str, float]]:
        return self.recognize_pipeline(image_path, backend=backend).lines

    def recognize_pipeline(
        self,
        image_input: Union[str, Path, Any],
        backend: str | None = None,
    ) -> OcrPipelineResult:
        boxes = self.recognize_with_boxes(
            image_input,
            backend=backend,
        )
        lines = group_ocr_lines(boxes, lang=self.lang) if boxes else []
        return OcrPipelineResult(
            boxes=boxes,
            lines=lines,
            backend=self.last_backend,
        )

    def _image_input_to_png_bytes(self, image_input) -> bytes | None:
        if not HAS_PIL or Image is None:
            return None
        try:
            if isinstance(image_input, (str, Path)):
                img = Image.open(str(image_input))
            elif isinstance(image_input, tuple) and len(image_input) == 3:
                r_bytes, r_w, r_h = image_input
                img = Image.frombytes("RGBA", (int(r_w), int(r_h)), r_bytes, "raw", "BGRA")
            elif isinstance(image_input, Image.Image):
                img = image_input
            else:
                return None
            
            if img.mode != "RGB":
                img = img.convert("RGB")
            
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except Exception as e:
            self._emit_log(f"[OCR] 图像转换为 PNG 字节流失败: {e}")
            return None

    def _paddle_vl_recognize_api(self, image_input) -> List[Dict[str, object]]:
        png_bytes = self._image_input_to_png_bytes(image_input)
        if not png_bytes:
            return []
            
        url = getattr(self, "paddle_vl_url", "http://localhost:8000/v1")
        model = getattr(self, "paddle_vl_model", "PaddlePaddle/PaddleOCR-VL")
        
        endpoint = url
        if not endpoint.endswith("/chat/completions"):
            if endpoint.endswith("/"):
                endpoint += "chat/completions"
            else:
                endpoint += "/chat/completions"
                
        try:
            b64_data = base64.b64encode(png_bytes).decode("utf-8")
            data_url = f"data:image/png;base64,{b64_data}"
            
            payload = {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": data_url}
                            },
                            {
                                "type": "text",
                                "text": "OCR:"
                            }
                        ]
                    }
                ],
                "temperature": 0.0
            }
            
            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                endpoint,
                data=req_data,
                headers={"Content-Type": "application/json"}
            )
            
            self._emit_log(f"[OCR] 发送 PaddleOCR-VL API 请求到 {endpoint}...")
            with urllib.request.urlopen(req, timeout=60.0) as response:
                resp_data = json.loads(response.read().decode("utf-8"))
                text_content = resp_data["choices"][0]["message"]["content"]
                
                lines = []
                for idx, text_line in enumerate(text_content.split("\n")):
                    t = text_line.strip()
                    if t:
                        mock_box = [[0, idx * 30], [100, idx * 30], [100, idx * 30 + 20], [0, idx * 30 + 20]]
                        lines.append({
                            "text": t,
                            "conf": 0.95,
                            "box": mock_box
                        })
                return lines
        except Exception as e:
            self._emit_log(f"[OCR] PaddleOCR-VL API 请求失败: {e}")
            return []

    def recognize_with_boxes(
        self,
        image_input,
        backend: str | None = None,
    ) -> List[Dict[str, object]]:
        """使用指定的 OCR 后端识别图片中的文本框和内容。
        
        Args:
            image_input: 文件路径 (str/Path) 或 PIL Image 对象
            backend: 指定后端（auto/windows/paddle_vl）
        """
        backend_key = str(backend or "auto").strip().lower().replace("-", "_")
        if backend_key not in {"auto", "windows", "paddle_vl"}:
            backend_key = "auto"
        raw_tuple = None
        if isinstance(image_input, tuple) and len(image_input) == 3:
            raw_tuple = image_input

        # 尝试 PaddleOCR-VL
        if backend_key == "paddle_vl":
            print("[OCR] 使用后端: PaddleOCR-VL (VLM API)")
            paddle_lines = self._paddle_vl_recognize_api(image_input)
            if paddle_lines:
                self.last_backend = "paddle_vl"
                return paddle_lines
            return []

        # 尝试 Windows 原生 OCR
        if backend_key in {"auto", "windows"}:
            print("[OCR] 使用后端: Windows OCR")
            windows_lines = []
            if self.win_ocr_preprocess:
                pre_bytes = self._preprocess_windows_input(raw_tuple if raw_tuple is not None else image_input)
                if pre_bytes:
                    windows_lines = self._windows_ocr_recognize_from_bytes(pre_bytes)
            if not windows_lines:
                if isinstance(image_input, (str, Path)):
                     # File Path
                     windows_lines = self._windows_ocr_recognize_boxes(image_input)
                elif raw_tuple is not None:
                     windows_lines = self._windows_ocr_recognize_from_bytes(raw_tuple)
                else:
                     # PIL Image or Object
                     windows_lines = self.recognize_from_image(image_input)
                  
            if windows_lines:
                self.last_backend = "windows"
                return windows_lines

        return []


def _sanitize_ocr_fragment(text: str) -> str:
    """清洗 OCR 常见伪标签噪声（如 <br>/<span> 等样式标记）。"""
    if not text:
        return ""
    s = str(text)
    # 常见实体先解码
    s = s.replace("&lt;", "<").replace("&gt;", ">").replace("&nbsp;", " ")
    # 处理 HTML 实体形式
    s = re.sub(r"(?i)&lt;\s*/?\s*br\s*/?&gt;", " ", s)
    # 处理真实标签或半截标签（包含 <brthe 这类缺失 > 的情况）
    s = re.sub(r"(?i)<\s*/?\s*br\s*/?>?", " ", s)
    s = re.sub(r"(?i)</\s*br\s*>?", " ", s)
    # 清理 span 标签（含缺失 > 的脏数据）
    s = re.sub(r"(?is)</\s*span\s*>?", " ", s)
    s = re.sub(r"(?is)<\s*span\b[^<>]*[\"']\s*", " ", s)  # malformed opener like <span ...;"text
    s = re.sub(r"(?is)<\s*span\b[^>]*>", " ", s)
    s = re.sub(r"(?is)</\s*span\b", " ", s)
    # 兜底清理常规 HTML 风格标签（仅字母开头，避免误删 <0> 占位）
    s = re.sub(r"(?is)<\s*/?\s*[a-z][a-z0-9:_-]*(?:\s+[^<>]*)?>", " ", s)
    s = s.replace("<span", " ").replace("</span", " ")
    s = re.sub(r"\s+", " ", s)
    return s


def group_ocr_lines(box_lines: List[Dict[str, object]], lang: str = "en") -> List[Tuple[str, float]]:
    """
    对 OCR 原始结果进行几何分行。
    仅依据垂直位置和高度进行行聚合，严禁语义合并。
    """
    if not box_lines:
        return []

    # 1. Sort by Y (top-down), then X (left-right)
    # 必须先按 Y 排序才能线性聚类
    lines_sorted = sorted(box_lines, key=lambda b: (b["box"][0][1], b["box"][0][0]))
    
    merged_lines: List[List[Dict[str, Any]]] = []
    
    for item in lines_sorted:
        text = _sanitize_ocr_fragment(str(item.get("text", ""))).strip()
        if not text:
            continue
            
        if not merged_lines:
            merged_lines.append([item])
            continue
            
        current_line = merged_lines[-1]
        last_item = current_line[-1]
        
        # Geometry
        last_y1 = last_item["box"][0][1]
        last_h = last_item["box"][2][1] - last_y1
        curr_y1 = item["box"][0][1]
        curr_h = item["box"][2][1] - curr_y1
        
        # 垂直中心距离
        last_cy = last_y1 + last_h / 2.0
        curr_cy = curr_y1 + curr_h / 2.0
        v_dist = abs(last_cy - curr_cy)
        
        # 判定同行：垂直错位小于最小高度的一半
        is_same_line = v_dist < (min(last_h, curr_h) * 0.5)
        
        if is_same_line:
            current_line.append(item)
            # 保持行内从左到右有序
            current_line.sort(key=lambda b: b["box"][0][0])
        else:
            merged_lines.append([item])

    # Phase 2: Paragraph Merging
    # 将垂直间距较小的视觉行合并为段落，避免句子被切断
    final_output: List[Tuple[str, float]] = []
    
    if merged_lines:
        para_groups = []
        current_para = [merged_lines[0]]
        
        for i in range(1, len(merged_lines)):
            last_line = current_para[-1]
            curr_line = merged_lines[i]
            
            # Calc geometry
            l_y1 = min(t["box"][0][1] for t in last_line)
            l_y2 = max(t["box"][2][1] for t in last_line)
            l_h = l_y2 - l_y1
            
            c_y1 = min(t["box"][0][1] for t in curr_line)
            c_y2 = max(t["box"][2][1] for t in curr_line)
            c_h = c_y2 - c_y1
            
            gap = c_y1 - l_y2
            
            # Threshold: Gap < 0.5 * LineHeight (Reduced from 0.8)
            allowed_gap = min(l_h, c_h) * 0.5
            
            # Extra Check: Horizontal Indentation
            # If start position differs significantly (> 50px), enforce stricter gap or force split
            l_x1 = min(t["box"][0][0] for t in last_line)
            c_x1 = min(t["box"][0][0] for t in curr_line)
            
            if abs(c_x1 - l_x1) > 50:
                 allowed_gap = min(l_h, c_h) * 0.2  # Very strict if not aligned
            
            # 检查上一行是否以句子终止标点结尾（过滤尾部空格）
            last_line_text = " ".join(_sanitize_ocr_fragment(str(t.get("text", ""))).strip() for t in last_line).strip()
            ends_with_sentence_punct = False
            if last_line_text:
                cleaned_tail = last_line_text.rstrip()
                if cleaned_tail and cleaned_tail[-1] in {'.', '!', '?', '。', '！', '？'}:
                    ends_with_sentence_punct = True

            if gap < allowed_gap and not ends_with_sentence_punct:
                current_para.append(curr_line)
            else:
                para_groups.append(current_para)
                current_para = [curr_line]
        para_groups.append(current_para)
        
        # Flatten paragraphs
        for para in para_groups:
            # Flatten all items in paragraph
            all_items = [item for line in para for item in line]
            
            # Sort by Y then X again just to be safe? 
            # No, within paragraph logic, lines are ordered Y, words ordered X.
            # Concatenation is correct reading order.
            
            valid_items = [t for t in all_items if _sanitize_ocr_fragment(str(t.get("text", ""))).strip()]
            if not valid_items:
                continue
                
            tokens = [_sanitize_ocr_fragment(str(t.get("text", ""))).strip() for t in valid_items]
            full_text = " ".join(tokens)
            
            confs = [float(t.get("conf", 1.0)) for t in valid_items]
            avg_conf = sum(confs) / max(len(confs), 1)
            
            # Basic cleanup
            for punct in [",", ".", "!", "?", ";", ":"]:
                full_text = full_text.replace(f" {punct}", punct)
            
            final_output.append((full_text, avg_conf))

    return final_output
