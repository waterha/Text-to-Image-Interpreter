import json
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
import win32gui

from til.capture import SelectionCapture
from til.config import FORMATS, Settings, load_settings, save_settings
from til.diagram import Diagram, DiagramStream, PROMPT, example_diagram, positions, to_svg, node_height, edge_route, rounded_route
from til.model import ModelJob, endpoint_url, request_payload
from til.ui import place_popup


def rejects(function):
    try:
        function()
    except ValueError:
        return
    raise AssertionError("Expected validation to reject this input")


def check_fast_diagrams():
    from til.diagram import local_preview

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


def check_fast_application():
    from til.app import Application, SAMPLE
    from til.tray import Tray

    settings = Settings(endpoint="https://example.com/v1", model="test")
    with patch("til.app.load_settings", return_value=settings), \
         patch("til.app.tk.Tk"), patch("til.app.ttk.Style"), \
         patch("til.app.Popup"), patch("til.app.ThreadPoolExecutor"), \
         patch("til.app.ModelJob") as jobs, patch("til.app.save_settings"), \
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
            source = svg
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
    from til.app import Application, main as run_application
    from til.tray import Tray

    configured = Settings(endpoint="https://example.com/v1", model="test", auto_capture=True)
    for saved in (Settings(), configured, ValueError("broken configuration")):
        with patch("til.app.load_settings", side_effect=[saved]), \
             patch("til.app.tk.Tk") as root_factory, \
             patch("til.app.ttk.Style"), \
             patch("til.app.Popup"), \
             patch("til.app.SettingsWindow") as settings_factory, \
             patch("til.app.messagebox") as dialogs, \
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
                with patch.multiple("til.tray.win32gui", CreatePopupMenu=DEFAULT,
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
    with patch("til.app.win32event.CreateMutex", return_value=123), \
         patch("til.app.win32api.GetLastError", return_value=183), \
         patch("til.app.win32api.CloseHandle") as close_handle, \
         patch("til.app.ctypes.windll.user32.MessageBoxW") as dialog, \
         patch("til.app.Application") as application_factory:
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
    with patch("til.capture.win32process.GetWindowThreadProcessId", return_value=(1, process_id)):
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
    with patch("til.capture.win32process.GetWindowThreadProcessId", side_effect=lambda hwnd: (1, host_process if hwnd == 123 else document_process)), \
         patch("til.capture.win32gui.EnumChildWindows", side_effect=lambda hwnd, callback, context: callback(124, context)):
        result = capture.read_selection(automation, module, 123, (40, 50))
        assert result and result[0] == "PDF 中选择的文字", "A PDF hosted in a child process must remain readable"
        document.GetCurrentPattern = lambda identity: None
        automation.CompareElements = lambda first, second: first is second
        with patch("til.capture.copy_selection", return_value=("兼容模式文字", (40, 50, 41, 68))) as copied, \
             patch("til.capture.win32gui.GetForegroundWindow", return_value=123):
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
    from til import clipboard_capture as copying

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
            assert "存储层" in events[-1][1] and events[-1][1].startswith("<svg")
            ET.fromstring(events[-1][1])
            _, headers, body = received[-1]
            headers = {name.lower(): value for name, value in headers.items()}
            assert body["model"] == "mock" and body["stream"] is True
            assert "DSL" in json.dumps(body, ensure_ascii=False)
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
        response_file.seek(0)
        events = []
        job.publish = lambda *event: events.append(event)
        with patch('til.model.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value = response_file
            job.run()
        assert events[-1][0] == 'done', events
        assert '存储层' in events[-1][1] and events[-1][1].startswith('<svg')
        ET.fromstring(events[-1][1])


def main():
    sample = example_diagram()
    svg = to_svg(sample)
    assert svg.startswith("<svg") and "存储层" in svg and "<script" not in svg

    check_dsl_stream()
    check_layouts()
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
    check_transport('+a 存储层\n+b 缓存\na>b 包括\n+c 数据库\na>c 包括\n')
    check_transport('```dsl\n+a 存储层\n+b 数据库\na>b 包括\n```')
    check_transport('<think>先分析原文</think>\n下面是关系图：\n```DSL\n+a 存储层\n+b 数据库\na>b 包括\n```\n图解完成。')
    check_capture()
    check_hosted_document_capture()
    check_clipboard_compatibility()
    check_background_lifecycle()
    check_fast_diagrams()
    check_fast_application()
    print("selfcheck: ok")


def check_dsl_stream():
    source = '+a Storage layer 存储层\n+b 关系型数据库\na>b 包括\n+c NoSQL\na>c'
    stream = DiagramStream()
    partials = []
    for character in source:
        if stream.feed(character):
            partials.append(stream.source)
    stream.feed('', final=True)
    assert len(stream.diagram.nodes) == 3 and len(stream.diagram.edges) == 2
    assert partials and 'NoSQL' not in partials[0]
    assert '英文' in PROMPT
    expected = stream.source
    for document in (
        source,
        '以下是关系图：\n```dsl\n' + source + '\n```\n以上是图解。',
        '\ufeff``` DSL\r\n' + source.replace('\n', '\r\n') + '\r\n```',
        '~~~text\n' + source + '\n~~~\n说明文字',
        '<think>分析原文\n+x 不是答案\n+y 推理\nx>y\n</think>\n' + source,
        '<analysis>内部推理</analysis>```dsl\n' + source + '\n```',
    ):
        for size in (1, 7, len(document)):
            wrapped = DiagramStream()
            updates = []
            for offset in range(0, len(document), size):
                if wrapped.feed(document[offset:offset + size]):
                    updates.append(wrapped.source)
            wrapped.feed('', final=True)
            assert wrapped.source == expected
            assert updates
    duplicate = DiagramStream()
    duplicate.feed(source + '\n+a Storage layer 存储层\na>b 包括', final=True)
    assert duplicate.source == expected
    escaped = DiagramStream()
    escaped.feed('+a <script>alert(1)</script>\n+b A & B\na>b <img>', final=True)
    root = ET.fromstring(escaped.source)
    assert all(node.tag.rsplit('}', 1)[-1] not in ('script', 'image', 'a') for node in root.iter())
    assert '&lt;script&gt;' in escaped.source and '&amp;' in escaped.source
    missing = DiagramStream()
    assert not missing.feed('目前只有说明文字。\n')
    rejects(lambda: missing.feed('', final=True))
    for invalid in (
        '', 'flowchart TD\na["A"]', 'x' * 12001,
        '<think>\n' + source + '\n</think>', '<think>尚未结束\n' + source,
        '+a A\n+b B', '+a A\na>b', '+a A\n+a B',
        '+a A\n+b B\na>b\nclick b callback',
        '+a ' + '字' * 41, '+1 非法ID', '+a A\n+b B\na>b ' + '字' * 17,
        '+a A\n+b B\na>b test\x00',
        '\n'.join(f'+n{index} 节点' for index in range(9)),
        '+a A\n+b B\n' + '\n'.join(f'a>b 关系{index}' for index in range(13)),
    ):
        rejects(lambda invalid=invalid: DiagramStream().feed(invalid, final=True))
    partial = DiagramStream()
    assert not partial.feed('+a 节点\n+b')
    rejects(lambda: partial.feed('', final=True))


def check_layouts():
    documents = [
        '+a 起点\n+b 左侧\na>b\n+c 右侧\na>c\n+d 汇合\nb>d\nc>d',
        '+a 开始\n+b 处理\na>b\n+c 完成\nb>c\na>c 跳过\nc>a 重试\nb>b 自检',
        '\n'.join(f'+n{i} ' + '长标签' * 13 for i in range(8)) + '\n' + '\n'.join(f'n{i}>n{i+1} 下一步' for i in range(7)),
        '+a 根节点\n' + '\n'.join(f'+n{i} 分支{i}\na>n{i} 包含' for i in range(7)),
    ]
    import random
    rng = random.Random(20261021)
    for _ in range(32):
        count = rng.randint(2, 8)
        document = '\n'.join(f'+n{i} ' + '长标签' * rng.randint(1, 13) for i in range(count))
        pairs = [(a, b) for a in range(count) for b in range(count)]
        for a, b in rng.sample(pairs, min(12, len(pairs))):
            document += f'\nn{a}>n{b} 关系'
        documents.append(document)
    for document in documents:
        stream = DiagramStream()
        stream.feed(document, final=True)
        diagram = stream.diagram
        locations, height = positions(diagram)
        assert len(locations) == len(diagram.nodes)
        for identity, (x, y) in locations.items():
            assert 0 <= x - 54 < x + 54 <= 360
            assert y + node_height(diagram.nodes[identity]) < height
        root = ET.fromstring(stream.source)
        namespace = {'s': 'http://www.w3.org/2000/svg'}
        assert len(root.findall('s:polyline', namespace)) == len(diagram.edges)
        for index, (source, target, _) in enumerate(diagram.edges):
            points, label = edge_route(diagram, locations, source, target, index)
            rounded = rounded_route(points)
            assert rounded[0] == points[0] and rounded[-1] == points[-1]
            assert all(0 <= x <= 360 and 0 <= y <= height for x, y in rounded)
            assert all(0 <= x <= 360 and 0 <= y <= height for x, y in points)
            assert all(x == xx or y == yy for (x, y), (xx, yy) in zip(points, points[1:]))
            for identity, (cx, top) in locations.items():
                if identity in (source, target):
                    continue
                bottom = top + node_height(diagram.nodes[identity])
                for (x, y), (xx, yy) in zip(points, points[1:]):
                    assert not (x == xx and cx - 54 < x < cx + 54 and max(min(y, yy), top) < min(max(y, yy), bottom)), (identity, points)
                    assert not (y == yy and top < y < bottom and max(min(x, xx), cx - 54) < min(max(x, xx), cx + 54)), (identity, points)


def check_native_renderer():
    import tkinter as tk
    from til.ui import Popup
    from til.renderer import check_renderer

    result = check_renderer()
    assert result['ok'], result
    root = tk.Tk()
    root.withdraw()
    try:
        with patch.object(Popup, 'layout'):
            popup = Popup(root, lambda: None)
            try:
                stream = DiagramStream()
                stream.feed('+a 存储层\n+b 缓存\na>b 包括\n+c 数据库\na>c 包括', final=True)
                callbacks = []
                popup.render(stream.source, complete=lambda *args: callbacks.append(args))
                assert len(callbacks) == 1 and callbacks[0] == (stream.source, None)
                assert popup.canvas.find_all()
                assert popup.content_frames and popup.canvas.find_withtag('arrival')
                popup.cancel_pending()
                assert not popup.content_frames and not popup.canvas.find_withtag('arrival')
                card = popup.canvas.find_withtag('card:b')[0]
                original = popup.canvas.itemcget(card, 'outline')
                with patch.object(popup.canvas, 'gettags', return_value=('node', 'node:b')):
                    popup.highlight_node(None)
                assert popup.canvas.itemcget(card, 'outline') != original
                popup.clear_highlight()
                assert popup.canvas.itemcget(card, 'outline') == original
                assert str(popup.export_button.cget('state')) == 'disabled'
                popup.render(stream.source, done=True)
                assert str(popup.export_button.cget('state')) == 'normal'
                assert not hasattr(popup, 'renderer')
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / 'diagram.svg'
                    with patch('til.ui.filedialog.asksaveasfilename', return_value=str(path)):
                        popup.export()
                    assert path.read_text(encoding='utf-8') == stream.source
                popup.hide()
                root.update()
                assert popup.window.state() == 'withdrawn'
                popup.render('<svg', complete=lambda *args: callbacks.append(args))
                assert callbacks[-1][0] is None and callbacks[-1][1]
                assert len(callbacks) == 2
            finally:
                popup.shutdown()
        popup = Popup(root, lambda: None)
        try:
            popup.render(stream.source, done=True)
            assert float(popup.window.attributes('-alpha')) == 1, 'Visibility must not depend on animation timers'
            assert win32gui.IsWindowVisible(popup.hwnd)
            popup.hide()
            deadline = time.monotonic() + .25
            while time.monotonic() < deadline:
                root.update()
                time.sleep(.01)
            assert popup.window.state() == 'withdrawn', 'Animation resurrected a cancelled popup'
            assert not popup.visible
            assert not popup.content_frames and not popup.canvas.find_withtag('arrival')
            popup.render(stream.source, done=True)
            deadline = time.monotonic() + .3
            while time.monotonic() < deadline:
                root.update()
                time.sleep(.01)
            assert not popup.content_frames and popup.arrival_offset == 0
            assert not popup.canvas.find_withtag('arrival')
            assert not popup.scrollbar.winfo_ismapped(), 'Small graphs should have no scrollbar'
            tall = DiagramStream()
            tall.feed('\n'.join(f'+n{i} 长节点' for i in range(8)) + '\n' + '\n'.join(f'n{i}>n{i+1} 下一步' for i in range(7)), final=True)
            with patch("til.ui.win32api.GetMonitorInfo", return_value={"Work": (0, 0, 1920, 600)}):
                popup.render(tall.source, done=True)
            root.update_idletasks()
            assert popup.scrollbar.winfo_ismapped()
            popup.canvas.yview_moveto(1)
            assert popup.canvas.yview()[0] > 0
            popup.render(stream.source, done=True)
            root.update_idletasks()
            assert not popup.scrollbar.winfo_ismapped()
            popup.hide()
        finally:
            popup.shutdown()
    finally:
        root.destroy()
    check_ui_recovery()
    print(json.dumps(result))


def check_ui_recovery():
    import tkinter as tk
    from til.app import Application
    from til.tray import Tray

    with patch('til.app.load_settings', return_value=Settings(endpoint='https://example.com/v1', model='test')), \
         patch.object(SelectionCapture, 'start'), patch.object(Tray, 'start'), \
         patch('til.app.win32gui.GetForegroundWindow', return_value=123):
        application = Application()
        try:
            # An OS/Tk error used to consume the only poll timer, silently losing all later events.
            with patch.object(application.popup, 'loading', side_effect=tk.TclError('private selected content')):
                application.publish('selection', ('测试文本', (300, 500, 500, 520), application.capture.generation, 123))
                application.publish('demo', None)
                deadline = time.monotonic() + .6
                while time.monotonic() < deadline:
                    application.root.update()
                    time.sleep(.01)
            assert application.events.empty(), 'UI error permanently stopped the event queue'
            assert any('TclError' in notice for notice in application.notices)
            assert all('private selected content' not in notice for notice in application.notices)
            assert application.popup.window.winfo_ismapped()
            assert float(application.popup.window.attributes('-alpha')) == 1
            assert '本地示例' in application.popup.status.get()
            application.hide_popup()
            application.publish('demo', None)
            deadline = time.monotonic() + .2
            while time.monotonic() < deadline:
                application.root.update()
                time.sleep(.01)
            assert win32gui.IsWindowVisible(application.popup.hwnd), 'Subsequent tray event did not reopen the popup'
        finally:
            application.quit()


if __name__ == '__main__':
    if '--renderer' in sys.argv:
        check_native_renderer()
    else:
        main()
