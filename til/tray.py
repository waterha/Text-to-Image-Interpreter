import os
import threading
import time

import pywintypes
import win32api
import win32con
import win32gui

from til.diagnostics import exception as log_exception, info as log_info
from til.icons import icon_bytes


def _icon_handle(size=32):
    """把内存 ICO 写入临时文件后加载；CreateIconFromResourceEx 拒绝这种结构。"""
    import tempfile

    path = None
    try:
        handle = None
        with tempfile.NamedTemporaryFile(suffix=".ico", delete=False) as file:
            file.write(icon_bytes(size))
            path = file.name
        handle = win32gui.LoadImage(0, path, win32con.IMAGE_ICON, size, size, win32con.LR_LOADFROMFILE)
        return handle
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


class Tray:
    def __init__(self, publish):
        self.publish = publish
        self.trigger_mode = "hotkey"
        self.hwnd = None
        self.stopping = False
        self.restart_after = 0.0
        self.thread = threading.Thread(target=self.run, name="Tray", daemon=True)

    def start(self):
        if self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self.run, name="Tray", daemon=True)
        self.thread.start()
        log_info("tray thread started")

    def ensure_running(self):
        if self.stopping or self.thread.is_alive():
            return
        now = time.monotonic()
        if now >= self.restart_after:
            self.restart_after = now + 1
            log_info("tray thread is not alive; restarting")
            self.start()

    def stop(self):
        self.stopping = True
        if self.hwnd:
            win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)

    def add_icon(self):
        flags = win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP
        win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, (self.hwnd, 1, flags, win32con.WM_USER + 20, self.icon, "TIL · 右键设置"))

    def run(self):
        try:
            instance = win32api.GetModuleHandle(None)
            self.taskbar_created = win32gui.RegisterWindowMessage("TaskbarCreated")
            window_class = win32gui.WNDCLASS()
            window_class.hInstance = instance
            window_class.lpszClassName = f"TILTray{os.getpid()}"
            window_class.lpfnWndProc = self.handle_message
            atom = win32gui.RegisterClass(window_class)
            self.hwnd = win32gui.CreateWindow(atom, "TIL 后台服务", 0, 0, 0, 0, 0, 0, 0, instance, None)
            self.icon = _icon_handle(32)
            self.add_icon()
            try:
                win32gui.RegisterHotKey(self.hwnd, 1, win32con.MOD_ALT | 0x4000, ord("Q"))
            except pywintypes.error:
                self.publish("notice", "Alt+Q 注册失败，可能被其他软件占用。")
            win32gui.PumpMessages()
        except Exception as error:
            log_exception("tray thread failed", error)
            self.publish("notice", "系统托盘初始化失败，请关闭后重新启动。")
        finally:
            log_info("tray thread exited")

    def handle_message(self, hwnd, message, wparam, lparam):
        if message == self.taskbar_created:
            self.add_icon()
        elif message == win32con.WM_USER + 20:
            if lparam == win32con.WM_RBUTTONUP:
                menu = win32gui.CreatePopupMenu()
                for identity, label in ((1, "触发方式：自动划词…" if self.trigger_mode == "automatic" else "触发方式：Alt+Q（默认）…"), (2, "设置…"), (3, "查看示例图解"), (4, "退出")):
                    win32gui.AppendMenu(menu, win32con.MF_STRING, identity, label)
                win32gui.SetForegroundWindow(hwnd)
                point = win32gui.GetCursorPos()
                selected = win32gui.TrackPopupMenu(menu, win32con.TPM_RETURNCMD | win32con.TPM_NONOTIFY | win32con.TPM_RIGHTBUTTON, *point, 0, hwnd, None)
                win32gui.DestroyMenu(menu)
                win32gui.PostMessage(hwnd, win32con.WM_NULL, 0, 0)
                action = {1: "settings", 2: "settings", 3: "demo", 4: "quit"}.get(selected)
                if action:
                    self.publish(action, None)
        elif message == win32con.WM_HOTKEY:
            self.publish("hotkey", None)
        elif message == win32con.WM_CLOSE:
            win32gui.DestroyWindow(hwnd)
        elif message == win32con.WM_DESTROY:
            win32gui.UnregisterHotKey(hwnd, 1)
            win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, (hwnd, 1))
            self.hwnd = None
            win32gui.PostQuitMessage(0)
        else:
            return win32gui.DefWindowProc(hwnd, message, wparam, lparam)
        return 0
