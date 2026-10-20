import multiprocessing
import sys


if __name__ == "__main__":
    multiprocessing.freeze_support()
    if len(sys.argv) == 3 and sys.argv[1] == "--check-renderer":
        import json
        from pathlib import Path
        from textlens.renderer import check_renderer

        result = check_renderer()
        Path(sys.argv[2]).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        raise SystemExit(0 if result["ok"] else 1)
    from textlens.app import main

    main()
