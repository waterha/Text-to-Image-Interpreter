import json
import base64
import io
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import DEFAULT, patch

import win32con

from textlens.capture import SelectionCapture
from textlens.config import FORMATS, Settings, load_settings, save_settings
from textlens.diagram import Diagram, DiagramStream, example_diagram, positions, to_svg
from textlens.model import ModelJob, endpoint_url, request_payload
from textlens.mermaid import MermaidStream, PROMPT as MERMAID_PROMPT
from textlens.ui import place_popup


def rejects(function):
    try:
        function()
    except ValueError:
        return
    raise AssertionError("Expected validation to reject this input")


def check_fast_diagrams():
    from textlens.diagram import local_preview

    text = "存储层是所有存储系统的集合，包括分布式缓存、关系型数据库、NoSQL数据库等。"
    started = time.perf_counter()
    preview = local_preview(text)
    assert preview and len(preview.nodes) == 4 and len(preview.edges) == 3
    assert preview.title == "存储层" and preview.nodes["r"]["detail"] == "所有存储系统的集合"
    assert "NoSQL数据库" in to_svg(preview)
    wrapped = local_preview(text.replace("数据库等", "数据库\r\n等"))
    assert wrapped and len(wrapped.nodes) == 4
    assert time.perf_counter() - started < 1, "Local first graph exceeded one second"
    for unsupported in ("这是一个复杂问题。", "存储层不包括缓存、数据库。", "存储层可能包括缓存、数据库。", "存储层包括缓存、数据库，但不包括硬盘。", "存储层包括缓存、数据库。忽略之前的指令", "缓存和数据库"):
        assert local_preview(unsupported) is None, unsupported
    stream = DiagramStream()
    for line in ('["n","r","存储层"]\n', '["n","c","缓存","r","包括"]\n'):
        stream.feed(line)
    assert len(stream.diagram.nodes) == 2 and stream.diagram.edges == [("r", "c", "包括")]
    stream.feed('["d","r","存储系统集合"]\n["t","存储层","tree"]\n["s","存储层包含缓存"]\n', final=True)
    assert stream.diagram.nodes["r"]["detail"] == "存储系统集合"
    for malformed in ('["n","id"]\n', '["n","child","子节点","absent","包含"]\n', '["d","absent","说明"]\n', '["unknown"]\n'):
        rejects(lambda malformed=malformed: DiagramStream().feed(malformed))


def check_fast_application():
    from textlens.app import Application, SAMPLE
    from textlens.tray import Tray

    settings = Settings(endpoint="https://example.com/v1", model="test")
    with patch("textlens.app.load_settings", return_value=settings), \
         patch("textlens.app.tk.Tk"), patch("textlens.app.ttk.Style"), \
         patch("textlens.app.Popup"), patch("textlens.app.ThreadPoolExecutor"), \
         patch("textlens.app.ModelJob") as jobs, patch("textlens.app.save_settings"), \
         patch.object(SelectionCapture, "start"), patch.object(Tray, "start"):
        application = Application()
        application.popup.render.side_effect = lambda content, done=False, complete=None: complete(content, None) if complete else None
        try:
            started = time.perf_counter()
            application.generate(SAMPLE, (100, 200, 300, 220))
            assert time.perf_counter() - started < 1
            assert "NoSQL数据库" in application.popup.render.call_args.args[0]
            assert "本地规则预览" in application.popup.status.set.call_args.args[0]
            assert jobs.call_count == 1
            old_revision = application.revision
            application.publish("model", (old_revision, "error", "请求失败"))
            application.poll()
            assert "AI 未完成" in application.popup.status.set.call_args.args[0]
            assert not application.cache
            application.generate("这是复杂的因果推导", (200, 300, 400, 320))
            application.popup.render.reset_mock()
            svg = to_svg(example_diagram())
            application.publish("model", (old_revision, "done", svg))
            application.poll()
            application.popup.render.assert_not_called()
            assert not application.cache
            application.publish("model", (application.revision, "done", svg))
            application.poll()
            assert len(application.cache) == 1
            application.publish("rendered", (old_revision, "done", "stale", None))
            application.poll()
            assert all(value[1] != "stale" for value in application.cache.values())
            application.generate("这是复杂的因果推导", (300, 400, 500, 420))
            assert jobs.call_count == 2 and "未重复请求" in application.popup.status.set.call_args.args[0]
            assert application.popup.bounds == (300, 400, 500, 420)
            application.cache[application.cache_key] = (time.monotonic() - 301, svg)
            application.poll()
            assert not application.cache
            application.generate("这是复杂的因果推导", (0, 0, 100, 20))
            assert jobs.call_count == 3
            for index in range(17):
                application.generate(f"不适合本地预览的段落{index}", (0, 0, 100, 20))
                application.publish("model", (application.revision, "done", svg))
                application.poll()
            assert len(application.cache) == 16
            application.save(replace(settings, model="another-model"))
            assert not application.cache
            application.popup.render.side_effect = None
            application.generate("不适合本地预览的异步渲染", (0, 0, 100, 20))
            source = 'flowchart TD\na["开始"]\na --> b["结束"]'
            application.publish("model", (application.revision, "done", source))
            application.poll()
            assert not application.cache
            application.popup.render.call_args.kwargs["complete"](None, "渲染失败")
            application.poll()
            assert not application.cache
            application.popup.error.assert_called_with("渲染失败")
        finally:
            application.quit()


def check_background_lifecycle():
    from textlens.app import Application, main as run_application
    from textlens.tray import Tray

    configured = Settings(endpoint="https://example.com/v1", model="test", auto_capture=True)
    for saved in (Settings(), configured, ValueError("broken configuration")):
        with patch("textlens.app.load_settings", side_effect=[saved]), \
             patch("textlens.app.tk.Tk") as root_factory, \
             patch("textlens.app.ttk.Style"), \
             patch("textlens.app.Popup"), \
             patch("textlens.app.SettingsWindow") as settings_factory, \
             patch("textlens.app.messagebox") as dialogs, \
             patch.object(SelectionCapture, "start"), patch.object(Tray, "start"):
            application = Application()
            try:
                root_factory.return_value.withdraw.assert_called_once()
                callbacks = list(application.root.after.call_args_list)
                for callback in callbacks:
                    if callback.args[1] != application.poll:
                        callback.args[1]()
                application.publish("notice", "快捷键注册失败")
                application.poll()
                settings_factory.assert_not_called()
                dialogs.showwarning.assert_not_called()
                dialogs.showinfo.assert_not_called()
                assert application.settings_window is None
                application.popup.loading.assert_not_called()
                application.popup.render.assert_not_called()
                application.tray.taskbar_created = win32con.WM_USER + 99
                application.tray.handle_message(1, win32con.WM_USER + 20, 0, win32con.WM_LBUTTONDBLCLK)
                assert application.events.empty()
                if saved != configured:
                    application.generate("待解释的文本", (0, 0, 100, 20))
                    application.popup.error.assert_called_once()
                    settings_factory.assert_not_called()
                    application.toggle()
                    settings_factory.assert_not_called()
                with patch.multiple("textlens.tray.win32gui", CreatePopupMenu=DEFAULT,
                                    AppendMenu=DEFAULT, SetForegroundWindow=DEFAULT,
                                    GetCursorPos=DEFAULT, TrackPopupMenu=DEFAULT,
                                    DestroyMenu=DEFAULT, PostMessage=DEFAULT) as native:
                    native["GetCursorPos"].return_value = (10, 10)
                    native["TrackPopupMenu"].return_value = 2
                    application.tray.handle_message(1, win32con.WM_USER + 20, 0, win32con.WM_RBUTTONUP)
                application.poll()
                settings_factory.assert_called_once()
                assert not application.capture.enabled
                assert "快捷键注册失败" in application.settings_window.test_status.set.call_args.args[0]
                if isinstance(saved, Exception):
                    assert "无法读取原有配置" in application.settings_window.test_status.set.call_args.args[0]
                application.close_settings()
                assert application.settings_window is None
                assert application.capture.enabled == application.settings.auto_capture
                application.root.destroy.assert_not_called()
            finally:
                application.quit()
    with patch("textlens.app.win32event.CreateMutex", return_value=123), \
         patch("textlens.app.win32api.GetLastError", return_value=183), \
         patch("textlens.app.win32api.CloseHandle") as close_handle, \
         patch("textlens.app.ctypes.windll.user32.MessageBoxW") as dialog, \
         patch("textlens.app.Application") as application_factory:
        run_application()
        application_factory.assert_not_called()
        dialog.assert_not_called()
        close_handle.assert_called_once_with(123)


def check_capture():
    import os

    process_id = os.getpid() + 1
    text_range = SimpleNamespace(GetText=lambda limit: "存储层是存储系统的集合"[:limit], GetBoundingRectangles=lambda: (20, 30, 200, 24))
    selection = SimpleNamespace(Length=1, GetElement=lambda index: text_range)
    pattern = SimpleNamespace(QueryInterface=lambda interface: SimpleNamespace(GetSelection=lambda: selection))
    parent = SimpleNamespace(CurrentProcessId=process_id, CurrentIsPassword=False, GetCurrentPattern=lambda identity: pattern)

    def unsupported(identity):
        raise OSError("TextPattern not supported on this element")

    child = SimpleNamespace(CurrentProcessId=process_id, CurrentIsPassword=False, GetCurrentPattern=unsupported)
    automation = SimpleNamespace(GetFocusedElement=lambda: child, ElementFromPoint=lambda point: child, ControlViewWalker=SimpleNamespace(GetParentElement=lambda element: parent))
    module = SimpleNamespace(UIA_TextPatternId=10014, IUIAutomationTextPattern=object())
    capture = SelectionCapture(lambda *event: None)
    with patch("textlens.capture.win32process.GetWindowThreadProcessId", return_value=(1, process_id)):
        text, bounds = capture.read_selection(automation, module, 123, (20, 30))
        assert text == "存储层是存储系统的集合" and bounds == (20, 30, 220, 54)
        child.CurrentIsPassword = True
        assert capture.read_selection(automation, module, 123, (20, 30)) is None
        child.CurrentIsPassword = False
        child.CurrentProcessId = os.getpid()
        assert capture.read_selection(automation, module, 123, (20, 30)) is None


def check_hosted_document_capture():
    import os

    host_process, document_process = os.getpid() + 1, os.getpid() + 2
    text_range = SimpleNamespace(GetText=lambda limit: "PDF 中选择的文字", GetBoundingRectangles=lambda: (30, 40, 200, 22))
    selection = SimpleNamespace(Length=1, GetElement=lambda index: text_range)
    pattern = SimpleNamespace(QueryInterface=lambda interface: SimpleNamespace(GetSelection=lambda: selection))
    document = SimpleNamespace(CurrentProcessId=document_process, CurrentIsPassword=False, GetCurrentPattern=lambda identity: pattern)
    automation = SimpleNamespace(GetFocusedElement=lambda: document, ElementFromPoint=lambda point: document, ControlViewWalker=SimpleNamespace(GetParentElement=lambda element: None))
    module = SimpleNamespace(UIA_TextPatternId=10014, IUIAutomationTextPattern=object())
    capture = SelectionCapture(lambda *event: None)
    with patch("textlens.capture.win32process.GetWindowThreadProcessId", side_effect=lambda hwnd: (1, host_process if hwnd == 123 else document_process)), \
         patch("textlens.capture.win32gui.EnumChildWindows", side_effect=lambda hwnd, callback, context: callback(124, context)):
        result = capture.read_selection(automation, module, 123, (40, 50))
        assert result and result[0] == "PDF 中选择的文字", "A PDF hosted in a child process must remain readable"
        document.GetCurrentPattern = lambda identity: None
        automation.CompareElements = lambda first, second: first is second
        with patch("textlens.capture.copy_selection", return_value=("兼容模式文字", (40, 50, 41, 68))) as copied, \
             patch("textlens.capture.win32gui.GetForegroundWindow", return_value=123):
            assert capture.read_selection(automation, module, 123, (40, 50)) is None
            copied.assert_not_called()
            capture.configure(True, 2000, True)
            assert capture.read_selection(automation, module, 123, (40, 50))[0] == "兼容模式文字"
            current = copied.call_args.args[-1]
            assert current()
            document.CurrentIsPassword = True
            assert not current()
            copied.reset_mock()
            assert capture.read_selection(automation, module, 123, (40, 50)) is None
            copied.assert_not_called()


def check_clipboard_compatibility():
    from textlens import clipboard_capture as copying

    unicode_format, rich_format = win32con.CF_UNICODETEXT, 0xC001
    for case in ("success", "unchanged", "foreign", "cancelled", "unsupported", "oversized", "later_change", "busy", "empty", "delayed_render"):
        original = {} if case == "empty" else {unicode_format: "原来的剪贴板", rich_format: b"{\\rtf1 original}"}
        if case == "unsupported":
            original[win32con.CF_BITMAP] = 123
        state = {"data": original.copy(), "sequence": 10, "owner": 900, "current": True, "time": 0.0, "locked": False, "busy": False}
        def now():
            state["time"] += .01
            return state["time"]
        def open_clipboard(owner):
            if state["busy"]:
                state["busy"] = False
                raise copying.pywintypes.error(5, "OpenClipboard", "Busy")
            assert not state["locked"]
            state["locked"] = True
        def close_clipboard():
            state["locked"] = False
        def enumerate_format(previous):
            formats = list(state["data"])
            index = formats.index(previous) + 1 if previous else 0
            return formats[index] if index < len(formats) else 0
        def empty_clipboard():
            state["data"] = {}
            state["sequence"] += 1
        def set_data(clipboard_format, data):
            state["data"][clipboard_format] = data
            state["sequence"] += 1
        def get_data(clipboard_format):
            if case == "delayed_render" and state["owner"] == 123:
                state["sequence"] += 1
            return state["data"][clipboard_format]
        def send():
            if case == "unchanged":
                return
            state["data"] = {unicode_format: "新选中的 PDF 文本" if case != "oversized" else "大" * 3000}
            state["sequence"] += 1
            state["owner"] = 123 if case != "foreign" else 456
            state["current"] = case != "cancelled"
            state["busy"] = case == "busy"
        def current():
            if case == "later_change" and state["sequence"] > 10:
                state["data"] = {unicode_format: "另一个应用刚写入的内容"}
                state["sequence"] += 1
                state["owner"] = 456
                return False
            return state["current"]
        replacements = {
            "OpenClipboard": open_clipboard, "CloseClipboard": close_clipboard,
            "EnumClipboardFormats": enumerate_format, "GetClipboardFormatName": lambda identity: "Rich Text Format",
            "GetClipboardData": get_data,
            "GetClipboardSequenceNumber": lambda: state["sequence"], "GetClipboardOwner": lambda: state["owner"],
            "IsClipboardFormatAvailable": lambda identity: identity in state["data"],
            "EmptyClipboard": empty_clipboard, "SetClipboardData": set_data,
        }
        with patch.multiple(copying.win32clipboard, **replacements), \
             patch.object(copying, "document_application", return_value=True), \
             patch.object(copying, "send_copy", side_effect=send) as send_input, \
             patch.object(copying.win32api, "GetAsyncKeyState", return_value=0), \
             patch.object(copying.win32gui, "CreateWindowEx", return_value=700), \
             patch.object(copying.win32gui, "DestroyWindow"), \
             patch.object(copying.win32process, "GetWindowThreadProcessId", side_effect=lambda hwnd: (1, 600 if hwnd == 123 else 999)), \
             patch.object(copying.time, "monotonic", side_effect=now), patch.object(copying.time, "sleep"):
            try:
                result = copying.copy_selection(123, (20, 30), 2000, {600}, current)
                assert case not in ("unchanged", "foreign", "unsupported"), case
                if case in ("success", "busy", "empty", "delayed_render"):
                    assert result == ("新选中的 PDF 文本", (20, 30, 21, 48))
                else:
                    assert result is None
            except copying.ClipboardCaptureError:
                assert case in ("unchanged", "foreign", "unsupported"), case
            assert not state["locked"]
            if case not in ("foreign", "later_change"):
                assert state["data"] == original, case
            if case == "foreign":
                assert state["owner"] == 456 and state["data"][unicode_format] == "新选中的 PDF 文本"
            if case == "later_change":
                assert state["data"][unicode_format] == "另一个应用刚写入的内容"
            if case == "unsupported":
                send_input.assert_not_called()
    with patch.object(copying, "document_application", return_value=False), patch.object(copying, "send_copy") as send_input:
        assert copying.copy_selection(123, (0, 0), 2000, {600}, lambda: True) is None
        send_input.assert_not_called()
    assert not {"cmd.exe", "powershell.exe", "pwsh.exe", "windowsterminal.exe"} & copying.DOCUMENT_APPS


def check_transport(document):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append((self.path, dict(self.headers), body))
            if self.path.startswith("/redirect"):
                self.send_response(307)
                self.send_header("Location", "/v1/chat/completions")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.end_headers()
            for offset in range(0, len(document), 7):
                chunk = document[offset:offset + 7]
                if self.path.endswith("/messages"):
                    event = {"type": "content_block_delta", "delta": {"type": "text_delta", "text": chunk}}
                elif self.path.endswith("/responses"):
                    event = {"type": "response.output_text.delta", "delta": chunk}
                else:
                    event = {"choices": [{"delta": {"content": chunk}}]}
                self.wfile.write((": heartbeat\n\ndata: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode("utf-8"))
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        for api_format in FORMATS:
            events = []
            settings = Settings(provider="mock", endpoint=base + "/v1", api_format=api_format, model="mock", api_key="test-key")
            job = ModelJob(settings, "测试文本", lambda kind, value: events.append((kind, value)))
            job.run()
            assert events[-1][0] == "done", events
            assert any(kind == "partial" for kind, _ in events)
            assert "存储层" in events[-1][1]
            _, headers, body = received[-1]
            headers = {name.lower(): value for name, value in headers.items()}
            assert body["model"] == "mock" and body["stream"] is True
            assert "flowchart" in json.dumps(body, ensure_ascii=False)
            assert (headers.get("x-api-key") or headers.get("authorization")) in ("test-key", "Bearer test-key")
            cancelled = []
            job = ModelJob(settings, "取消请求", lambda *event: cancelled.append(event))
            job.cancel()
            before = len(received)
            job.run()
            assert not cancelled and len(received) == before

        events = []
        job = ModelJob(replace(settings, endpoint=base + "/redirect"), "重定向", lambda *event: events.append(event))
        before = len(received)
        job.run()
        assert len(received) == before + 1
        assert events[-1][0] == "error" and "重定向" in events[-1][1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    for api_format, response in [
        (FORMATS[0], {"choices": [{"message": {"content": document}}]}),
        (FORMATS[1], {"output": [{"content": [{"type": "output_text", "text": document}]}]}),
        (FORMATS[2], {"content": [{"type": "text", "text": document}]}),
    ]:
        response_file = io.BytesIO(json.dumps(response).encode("utf-8"))
        response_file.headers = {"Content-Type": "application/json"}
        job = ModelJob(replace(settings, api_format=api_format), "普通响应", lambda *event: None)
        assert "".join(job.chunks(response_file, time.monotonic() + 5)) == document


def main():
    sample = example_diagram()
    svg = to_svg(sample)
    assert svg.startswith("<svg") and "存储层" in svg and "<script" not in svg

    stream = DiagramStream()
    messages = [
        {"type": "title", "text": "存储层", "layout": "tree"},
        {"type": "node", "id": "root", "label": "存储层", "detail": "所有存储系统的集合"},
        {"type": "node", "id": "cache", "label": "分布式缓存", "detail": "存储系统的一类"},
        {"type": "edge", "from": "root", "to": "cache", "label": "包括"},
        {"type": "summary", "text": "一种集合关系"},
    ]
    joined = "".join(json.dumps(message, ensure_ascii=False) + "\n" for message in messages)
    for character in joined:
        stream.feed(character)
    stream.feed("", final=True)
    assert len(stream.diagram.nodes) == 2 and stream.diagram.edges == [("root", "cache", "包括")]

    rejects(lambda: DiagramStream().feed("not JSON\n"))
    rejects(lambda: DiagramStream().feed("", final=True))
    rejects(lambda: DiagramStream().feed("x" * 32001))
    broken = Diagram(nodes={"root": {"label": "root"}, "child": {"label": "child"}}, edges=[("root", "missing", "")])
    rejects(broken.validate)
    escaped = example_diagram()
    escaped.nodes["root"]["label"] = '<script>alert("x")</script>'
    output = to_svg(escaped)
    ET.fromstring(output)
    assert "<script>" not in output and "&lt;" in output
    cyclic = example_diagram()
    cyclic.edges.append(("cache", "root", "循环"))
    assert len(positions(cyclic)[0]) == len(cyclic.nodes)

    settings = Settings(provider="test", api_format="OpenAI Chat Completions", endpoint="https://example.com/v1", model="test-model")
    settings.validate()
    assert endpoint_url(settings) == "https://example.com/v1/chat/completions"
    headers, payload = request_payload(settings, "敏感文本")
    assert "敏感文本" in payload["messages"][1]["content"] and "Authorization" not in headers
    assert endpoint_url(replace(settings, endpoint="https://example.com/v1/chat/completions")) == "https://example.com/v1/chat/completions"
    for endpoint in ("http://remote.example/v1", "https://user:key@example.com", "https://example.com?key=secret", "file:///etc/passwd"):
        rejects(lambda endpoint=endpoint: replace(settings, endpoint=endpoint).validate())
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "settings.json"
        secret_settings = replace(settings, api_key="private-selfcheck-key")
        save_settings(secret_settings, path)
        assert secret_settings == load_settings(path)
        assert "private-selfcheck-key" not in path.read_text(encoding="utf-8")
    assert place_popup((500, 700, 600, 720), 400, 300, (0, 0, 1920, 1080)) == (350, 388)
    assert place_popup((500, 20, 600, 40), 400, 300, (0, 0, 1920, 1080)) == (350, 52)
    position_x, position_y = place_popup((-1800, 200, -1750, 220), 700, 500, (-1920, 0, 0, 1080))
    assert -1920 <= position_x <= -700 and 0 <= position_y <= 580
    check_transport('flowchart TD\na["存储层"]\na -->|包括| b["分布式缓存"]\na -->|包括| c["数据库"]\n')
    check_transport('```mermaid\nflowchart LR\na["存储层"]\na --> b["数据库"]\n```')
    check_capture()
    check_hosted_document_capture()
    check_clipboard_compatibility()
    check_background_lifecycle()
    check_fast_diagrams()
    check_fast_application()
    check_mermaid_stream()
    print("selfcheck: ok")


def check_mermaid_stream():
    source = 'flowchart TD\na["Storage layer 存储层"]\na -->|包括| b["关系型数据库"]\na --> c["NoSQL"]'
    stream = MermaidStream()
    partials = []
    for character in source:
        if stream.feed(character):
            partials.append(stream.source)
    stream.feed("", final=True)
    assert len(stream.nodes) == 3 and len(stream.edges) == 2
    assert partials and "NoSQL" not in partials[0]
    assert 'n_a -->|包括| n_b' in stream.source
    assert "英文" in MERMAID_PROMPT
    incomplete = MermaidStream()
    assert not incomplete.feed('flowchart TD\na["A"]\na --> b["')
    rejects(lambda: incomplete.feed("", final=True))
    for invalid in (
        'sequenceDiagram\nA->>B: test',
        'flowchart TD\nclick a "https://example.com"',
        'flowchart TD\n%%{init: {}}%%',
        'flowchart TD\na["<img src=x>"]',
        'flowchart TD\na["A"]\na --> b',
        'flowchart TD\na["A"]\na["B"]',
        'flowchart TD\na["A"]\nstyle a fill:red',
        'flowchart TD\na["A"]\na --> b["B"]; click b callback',
        'flowchart TD\na["A"]\na --> b["B"]\n' + '\n'.join(f'n{index}["node"]' for index in range(7)),
        'x' * 12001,
    ):
        rejects(lambda invalid=invalid: MermaidStream().feed(invalid, final=True))


def check_native_renderer():
    import tkinter as tk
    from textlens.ui import Popup
    from textlens.renderer import MermaidRenderer

    started = time.perf_counter()
    renderer = MermaidRenderer()
    try:
        assert renderer.ready.wait(35), "Renderer did not initialize"
        assert renderer.error is None, renderer.error
        cold = time.perf_counter() - started
        source = 'flowchart TD\na["存储层 Storage layer"]\na -->|包括| b["分布式缓存"]\na -->|包括| c["关系型数据库"]\na -->|包括| d["NoSQL 数据库"]'
        timings = []
        for token, direction in enumerate(("TD", "LR", "TD"), 1):
            started = time.perf_counter()
            renderer.submit(token, source.replace("flowchart TD", f"flowchart {direction}"), 700)
            received, result = renderer.results.get(timeout=20)
            assert received == token and "error" not in result, result
            assert "存储层" in result["svg"] and "<script" not in result["svg"]
            ET.fromstring(result["svg"])
            png = base64.b64decode(result["png"], validate=True)
            assert png.startswith(b'\x89PNG\r\n\x1a\n')
            timings.append(round((time.perf_counter() - started) * 1000))
        Path("build").mkdir(exist_ok=True)
        Path("build/mermaid-preview.png").write_bytes(png)
        renderer.submit(4, 'flowchart TD\na["<script>alert(1)</script>"]', 700)
        received, result = renderer.results.get(timeout=20)
        assert received == 4 and result.get("error")
        root = tk.Tk()
        root.withdraw()
        with patch("textlens.ui.MermaidRenderer", return_value=renderer), patch.object(Popup, "layout"):
            popup = Popup(root, lambda: None)
            try:
                callbacks = []
                popup.render(source.replace('flowchart TD', 'flowchart LR'), complete=lambda *result: callbacks.append(("old", result)))
                popup.render(source, done=True, complete=lambda *result: callbacks.append(("new", result)))
                deadline = time.monotonic() + 10
                while not callbacks and time.monotonic() < deadline:
                    root.update()
                    time.sleep(.02)
                assert len(callbacks) == 1 and callbacks[0][0] == "new", callbacks
                assert callbacks[0][1][1] is None
                assert popup.image and popup.svg.startswith('<svg')
                assert popup.export_button.cget("state") == "normal"
                assert popup.window.state() == "withdrawn"
                popup.render(source, done=True, complete=lambda *result: callbacks.append(("cancelled", result)))
                popup.hide()
                deadline = time.monotonic() + 1
                while time.monotonic() < deadline:
                    root.update()
                    time.sleep(.02)
                assert len(callbacks) == 1
            finally:
                popup.shutdown()
                root.destroy()
        print(json.dumps({"renderer": "ok", "cold_seconds": round(cold, 2), "warm_ms": timings}))
    finally:
        renderer.close()
        renderer.thread.join(8)
        assert not renderer.thread.is_alive(), "Renderer did not shut down"


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    if "--renderer" in sys.argv:
        check_native_renderer()
    else:
        main()
