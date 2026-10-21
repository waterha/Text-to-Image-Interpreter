"""Native rendering selfcheck, also available from the packaged EXE."""
import time


def check_renderer():
    import sys
    import tkinter as tk
    import win32api
    import win32process
    from til.diagram import DiagramStream, draw_svg

    started = time.perf_counter()
    root = None
    try:
        root = tk.Tk()
        root.withdraw()
        canvas = tk.Canvas(root)
        cold_ms = round((time.perf_counter() - started) * 1000, 2)
        stream = DiagramStream()
        stream.feed('+a 存储层\n+b 缓存\na>b 包括\n+c 数据库\na>c 包括\nc>a 反馈', final=True)
        svg = stream.source
        timings = []
        for _ in range(3):
            started = time.perf_counter()
            draw_svg(canvas, svg)
            root.update_idletasks()
            timings.append(round((time.perf_counter() - started) * 1000, 2))
        assert canvas.find_all() and svg.startswith('<svg')
        assert not any(module in sys.modules for module in ('webview', 'clr', 'til.mermaid'))
        memory = win32process.GetProcessMemoryInfo(win32api.GetCurrentProcess())
        return {"ok": True, "renderer": "tk-canvas", "cold_ms": cold_ms, "warm_ms": timings,
                "working_set_mb": round(memory["WorkingSetSize"] / 1024 ** 2, 2), "svg": True,
                "canvas_items": len(canvas.find_all()), "browser_required": False}
    except Exception:
        return {"ok": False, "error": "Native Canvas renderer check failed"}
    finally:
        if root is not None:
            root.destroy()
