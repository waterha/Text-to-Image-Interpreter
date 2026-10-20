import json
import multiprocessing
import queue
import threading
import time
from pathlib import Path


def renderer_worker(connection):
    import webview

    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
    webview.settings["ALLOW_DOWNLOADS"] = False
    window = webview.create_window("TextLens Mermaid renderer", (Path(__file__).parent / "assets" / "renderer.html").as_uri(), hidden=True, width=800, height=600)

    def render(source, width):
        finished = threading.Event()
        result = []

        def receive(value):
            result.append(value)
            finished.set()

        window.evaluate_js(f"window.renderDiagram({json.dumps(source)}, {width})", callback=receive)
        if not finished.wait(12):
            raise TimeoutError("Mermaid renderer timed out")
        return result[0]

    def serve():
        try:
            if not window.events.loaded.wait(20):
                raise TimeoutError("WebView2 initialization timed out")
            result = render('flowchart TD\na["Ready"]\na --> b["Mermaid"]', 700)
            if result.get("error"):
                raise RuntimeError("Mermaid warmup failed")
            connection.send(("ready", None))
            while True:
                request = connection.recv()
                if request is None:
                    break
                token, source, width = request
                result = render(source, width)
                result["source"] = source
                connection.send((token, result))
        except (EOFError, BrokenPipeError, OSError):
            pass
        except Exception:
            try:
                connection.send(("error", "本地 Mermaid 渲染器启动或运行失败，请确认已安装 Microsoft Edge WebView2 Runtime，退出后重试。"))
            except (BrokenPipeError, OSError):
                pass
        finally:
            window.destroy()

    try:
        webview.start(serve, gui="edgechromium", private_mode=True, http_server=False)
    except Exception:
        connection.send(("error", "无法启动 WebView2；请安装 Microsoft Edge WebView2 Runtime 后重新启动。"))
    finally:
        connection.close()


class MermaidRenderer:
    def __init__(self):
        self.requests = queue.Queue(maxsize=1)
        self.results = queue.Queue()
        self.stopped = threading.Event()
        self.ready = threading.Event()
        self.error = None
        self.thread = threading.Thread(target=self.run, name="MermaidRenderer", daemon=True)
        self.thread.start()

    def submit(self, token, source, width):
        if self.error:
            self.results.put((token, {"error": self.error}))
            return
        try:
            self.requests.get_nowait()
        except queue.Empty:
            pass
        self.requests.put_nowait((token, source, width))

    def run(self):
        context = multiprocessing.get_context("spawn")
        connection, child = context.Pipe()
        process = context.Process(target=renderer_worker, args=(child,), daemon=True, name="MermaidWebView")
        current = None
        try:
            process.start()
            child.close()
            deadline = time.monotonic() + 30
            while not self.stopped.is_set():
                if not connection.poll(.05):
                    if time.monotonic() > deadline or not process.is_alive():
                        raise RuntimeError("Renderer unavailable")
                    continue
                stage, result = connection.recv()
                if stage == "error":
                    self.error = result
                    raise RuntimeError("Renderer failed")
                if stage == "ready":
                    self.ready.set()
                else:
                    self.results.put((stage, result))
                while not self.stopped.is_set():
                    try:
                        current = self.requests.get(timeout=.05)
                        connection.send(current)
                        deadline = time.monotonic() + 15
                        break
                    except queue.Empty:
                        if not process.is_alive():
                            raise RuntimeError("Renderer exited")
        except Exception:
            self.error = self.error or "Mermaid 渲染器不可用，请检查 WebView2 Runtime 并重新启动程序。"
            self.ready.set()
            if current:
                self.results.put((current[0], {"error": self.error}))
            try:
                pending = self.requests.get_nowait()
                self.results.put((pending[0], {"error": self.error}))
            except queue.Empty:
                pass
        finally:
            try:
                connection.send(None)
            except (BrokenPipeError, OSError):
                pass
            connection.close()
            if process.pid:
                process.join(2)
                if process.is_alive():
                    process.terminate()
                    process.join(2)

    def close(self):
        self.stopped.set()
        self.thread.join(4)


def check_renderer():
    started = time.perf_counter()
    renderer = MermaidRenderer()
    try:
        if not renderer.ready.wait(35) or renderer.error:
            return {"ok": False, "error": renderer.error or "Renderer startup timed out"}
        cold = round((time.perf_counter() - started) * 1000)
        timings = []
        for token in range(2):
            started = time.perf_counter()
            renderer.submit(token, 'flowchart TD\na["English text"]\na -->|解释| b["中文图解"]', 700)
            received, result = renderer.results.get(timeout=20)
            if received != token or result.get("error") or not result.get("svg", "").startswith("<svg") or not result.get("png"):
                return {"ok": False, "error": result.get("error", "Invalid render result")}
            timings.append(round((time.perf_counter() - started) * 1000))
        return {"ok": True, "cold_ms": cold, "warm_ms": timings, "svg": True, "png": True}
    except Exception:
        return {"ok": False, "error": "Renderer check failed"}
    finally:
        renderer.close()
