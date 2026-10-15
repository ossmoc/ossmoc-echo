from __future__ import annotations

import argparse
from pathlib import Path


def main():
    import sys
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) in (3,4) and sys.argv[1] == '--oneocr-worker':
        from ludiglot.core.one_ocr import worker_main
        budget = int(sys.argv[3]) if len(sys.argv) == 4 else 0
        if not 0 <= budget <= 64:
            raise SystemExit('Invalid OneOCR CPU budget')
        raise SystemExit(worker_main(Path(sys.argv[2]), budget))
    from ludiglot.infrastructure.proxy_setup import setup_system_proxy
    setup_system_proxy()
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    parser.add_argument("--smoke-report", type=Path)
    parser.add_argument("--qa-images", type=Path, nargs="+", default=[])
    args = parser.parse_args()
    if args.qa_images and not args.smoke_report:
        parser.error("--qa-images requires --smoke-report (local diagnostic run only)")
    from ludiglot.ui.desktop_app import run_desktop
    run_desktop(args.config, args.smoke_report, args.qa_images)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Windowed builds have no stderr. Preserve the full startup traceback
        # before PyInstaller shows its fatal-error dialog.
        import sys
        import traceback
        application = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
        startup_log = application / "userdata" / "logs" / "startup-error.log"
        try:
            startup_log.parent.mkdir(parents=True, exist_ok=True)
            startup_log.write_text(traceback.format_exc(), encoding="utf-8")
        except OSError:
            pass
        raise
