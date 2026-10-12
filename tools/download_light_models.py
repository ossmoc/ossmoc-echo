"""Explicit developer download; runtime never downloads OCR models."""
from pathlib import Path
import hashlib
import sys
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from ludiglot.core.light_ocr import MODEL_HASHES


def main():
    root=Path(__file__).resolve().parents[1]/'models/light_ocr'
    root.mkdir(parents=True,exist_ok=True)
    for name,expected in MODEL_HASHES.items():
        path=root/name
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==expected:
            print('Verified '+name); continue
        family='det' if name.startswith('ch_') else 'rec'
        url=f'https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/onnx/PP-OCRv5/{family}/{name}'
        temp=path.with_suffix('.download')
        urllib.request.urlretrieve(url,temp)
        if hashlib.sha256(temp.read_bytes()).hexdigest()!=expected:
            raise ValueError('Model checksum mismatch: '+name)
        temp.replace(path); print('Downloaded and verified '+name)

if __name__=='__main__': main()
