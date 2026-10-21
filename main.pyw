import sys


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] in ("--check-renderer", "--check-popup"):
        import json
        from pathlib import Path
        from til.renderer import check_renderer, check_popup

        result = check_popup() if sys.argv[1] == "--check-popup" else check_renderer()
        Path(sys.argv[2]).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        raise SystemExit(0 if result["ok"] else 1)
    from til.app import main

    main()
