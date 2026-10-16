"""Reproducible source QA; never label host tests as other-machine validation."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def summarize_junit(path):
    suites = ET.parse(path).getroot()
    cases = list(suites.iter('testcase'))
    failed = sum(any(c.tag in {'failure', 'error'} for c in case) for case in cases)
    skipped = sum(any(c.tag == 'skipped' for c in case) for case in cases)
    return {'total': len(cases), 'passed': len(cases) - failed - skipped,
            'failed': failed, 'skipped': skipped}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--timeout', type=int, default=1200)
    args = parser.parse_args()
    output = (args.output or ROOT / 'artifacts' / ('qa-' + uuid.uuid4().hex)).resolve()
    output.mkdir(parents=True, exist_ok=False)
    packages = {}
    for name in ('PyQt6', 'PyQt6-Qt6', 'windows-capture', 'onnxruntime', 'rapidocr', 'rapidfuzz', 'numpy', 'pyinstaller'):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    env = {**os.environ, 'PYTHONPATH': str(ROOT / 'src'), 'PYTHONDONTWRITEBYTECODE': '1',
           'QT_QPA_PLATFORM': 'offscreen', 'PYTHONIOENCODING': 'utf-8'}
    report = {'schema': 1, 'started_at': datetime.now(timezone.utc).isoformat(),
              'host': {'system': platform.system(), 'release': platform.release(),
                       'version': platform.version(), 'machine': platform.machine(),
                       'python': platform.python_version(), 'packages': packages},
              'scope': 'source unit/integration; native capture requires separate frozen interactive smoke',
              'other_os_or_hardware_validation': 'NOT_RUN',
              'success': False}
    command = [sys.executable, '-B', '-m', 'pytest', 'tests', '-q', '-p', 'no:cacheprovider',
               '--basetemp', str(output / 'temp'), '--junitxml', str(output / 'junit.xml')]
    report['command'] = command
    try:
        # Refuse accidental imports from an editable install of another checkout.
        check = subprocess.run([sys.executable, '-B', '-c',
            'import ludiglot; from pathlib import Path; print(Path(ludiglot.__file__).resolve())'],
            env=env, cwd=ROOT, capture_output=True, text=True, check=True, timeout=30)
        origin = Path(check.stdout.strip()).resolve()
        if not origin.is_relative_to((ROOT / 'src').resolve()):
            raise RuntimeError('QA imported a different checkout: ' + str(origin))
        report['source_origin'] = str(origin)
        with (output / 'pytest.log').open('w', encoding='utf-8') as log:
            completed = subprocess.run(command, cwd=ROOT, env=env, stdout=log,
                                       stderr=subprocess.STDOUT, timeout=args.timeout)
        report['returncode'] = completed.returncode
        if (output / 'junit.xml').is_file():
            report['tests'] = summarize_junit(output / 'junit.xml')
        report['success'] = completed.returncode == 0 and report.get('tests', {}).get('failed') == 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        report['error'] = str(exc)
    finally:
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
