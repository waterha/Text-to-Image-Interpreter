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


def check_popup():
    """Visible, offline EXE check of the actual popup, not just an offscreen Canvas."""
    import tkinter as tk
    import win32gui
    from til.diagram import example_diagram, to_svg
    from til.ui import Popup

    root = tk.Tk()
    root.withdraw()
    popup = None
    try:
        popup = Popup(root, lambda: None)
        for _ in range(2):
            popup.loading((300, 500, 500, 520), "本地显示自检")
            assert float(popup.window.attributes('-alpha')) == 1
            popup.render(to_svg(example_diagram()), done=True)
            deadline = time.monotonic() + .25
            while time.monotonic() < deadline:
                root.update()
                time.sleep(.01)
            assert win32gui.IsWindowVisible(popup.hwnd)
            assert popup.canvas.find_all() and popup.svg
            popup.hide()
            root.update()
            assert not win32gui.IsWindowVisible(popup.hwnd)
        return {"ok": True, "popup_visible": True, "reopen": True, "offline": True}
    except Exception as error:
        return {"ok": False, "error_type": type(error).__name__}
    finally:
        if popup is not None:
            popup.shutdown()
        root.destroy()
