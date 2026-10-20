import os
import queue
import threading
import time
from ctypes import wintypes

import comtypes
import win32api
import win32gui
import win32process


class SelectionCapture:
    def __init__(self, publish):
        self.publish = publish
        self.enabled = False
        self.max_chars = 2000
        self.generation = 0
        self.commands = queue.Queue(maxsize=1)
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name="SelectionCapture", daemon=True)

    def start(self):
        self.thread.start()

    def configure(self, enabled, max_chars):
        self.enabled, self.max_chars = enabled, max_chars
        self.generation += 1

    def request(self):
        try:
            self.commands.put_nowait((win32gui.GetForegroundWindow(), win32api.GetCursorPos(), self.generation))
        except queue.Full:
            pass

    def stop(self):
        self.stop_event.set()

    @staticmethod
    def external_window(hwnd):
        return bool(hwnd) and win32process.GetWindowThreadProcessId(hwnd)[1] != os.getpid()

    def run(self):
        from comtypes.client import CreateObject, GetModule

        comtypes.CoInitialize()
        try:
            module = GetModule("UIAutomationCore.dll")
            automation = CreateObject(module.CUIAutomation, interface=module.IUIAutomation)
            pressed, down_point, down_window, pending = False, (0, 0), 0, None
            last_click, last_click_point, last_result, last_result_time = 0.0, (0, 0), None, 0.0
            while not self.stop_event.wait(.025 if self.enabled else .1):
                try:
                    hwnd, point, generation = self.commands.get_nowait()
                    pending = (time.monotonic(), hwnd, point, generation, True)
                except queue.Empty:
                    pass
                now = time.monotonic()
                mouse_down = bool(win32api.GetAsyncKeyState(0x01) & 0x8000)
                if self.enabled and mouse_down and not pressed:
                    down_point, down_window = win32api.GetCursorPos(), win32gui.GetForegroundWindow()
                    pending = None
                if self.enabled and pressed and not mouse_down:
                    point = win32api.GetCursorPos()
                    moved = abs(point[0] - down_point[0]) + abs(point[1] - down_point[1]) >= 5
                    double_click = now - last_click < .5 and abs(point[0] - last_click_point[0]) + abs(point[1] - last_click_point[1]) < 8
                    if moved or double_click:
                        pending = (now + .08, down_window, point, self.generation, False)
                    last_click, last_click_point = now, point
                pressed = mouse_down
                if pending and now >= pending[0] and not mouse_down:
                    _, hwnd, point, generation, manual = pending
                    pending = None
                    if generation != self.generation or (not manual and not self.enabled):
                        continue
                    if hwnd != win32gui.GetForegroundWindow() or not self.external_window(hwnd):
                        continue
                    if not manual and not self.external_window(win32gui.WindowFromPoint(point)):
                        continue
                    try:
                        result = self.read_selection(automation, module, hwnd, point)
                        if generation != self.generation or hwnd != win32gui.GetForegroundWindow():
                            continue
                        if result:
                            text, bounds = result
                            identity = (hwnd, text)
                            if manual or identity != last_result or now - last_result_time > 1:
                                last_result = identity
                                last_result_time = now
                                self.publish("selection", (text, bounds, generation, hwnd))
                        else:
                            last_result = None
                            if manual:
                                self.publish("capture_notice", "此应用没有提供可读选区。可复制文本后按 Ctrl+Alt+V，主动生成剪贴板图解。")
                    except Exception:
                        if manual:
                            self.publish("capture_notice", "读取选区失败。请复制文本后按 Ctrl+Alt+V。")
        except Exception:
            self.publish("notice", "系统选区接口初始化失败；仍可使用 Ctrl+Alt+V 图解已复制文本。")
        finally:
            comtypes.CoUninitialize()

    def read_selection(self, automation, module, hwnd, point):
        focused = automation.GetFocusedElement()
        if focused and (focused.CurrentIsPassword or focused.CurrentProcessId == os.getpid()):
            return None
        pointed = automation.ElementFromPoint(wintypes.POINT(*point))
        candidates = [focused, pointed]
        process_id = win32process.GetWindowThreadProcessId(hwnd)[1]
        for candidate in candidates:
            for _ in range(5):
                if not candidate or candidate.CurrentProcessId != process_id or candidate.CurrentIsPassword:
                    break
                try:
                    pattern = candidate.GetCurrentPattern(module.UIA_TextPatternId)
                    if pattern:
                        selected = pattern.QueryInterface(module.IUIAutomationTextPattern).GetSelection()
                        if selected and selected.Length:
                            text_range = selected.GetElement(0)
                            text = text_range.GetText(self.max_chars + 1).strip()
                            if 2 <= len(text) <= self.max_chars:
                                rectangles = text_range.GetBoundingRectangles()
                                bounds = (point[0], point[1], point[0] + 1, point[1] + 18)
                                if len(rectangles) >= 4:
                                    left, top, width, height = rectangles[:4]
                                    if width > 0 and height > 0:
                                        bounds = (int(left), int(top), int(left + width), int(top + height))
                                return text, bounds
                except (comtypes.COMError, OSError):
                    pass
                candidate = automation.ControlViewWalker.GetParentElement(candidate)
        return None
