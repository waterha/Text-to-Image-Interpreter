import ctypes
import hashlib
import queue
import sys
import time
import tkinter as tk
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from tkinter import messagebox, ttk

import win32api
import win32clipboard
import win32con
import win32event
import win32gui

from textlens.capture import SelectionCapture
from textlens.config import Settings, load_settings, save_settings
from textlens.diagram import example_diagram, local_preview, to_svg
from textlens.model import ModelJob
from textlens.tray import Tray
from textlens.ui import Popup, SettingsWindow


SAMPLE = "存储层是所有存储系统的集合，包括分布式缓存、关系型数据库、NoSQL数据库等。"


class Application:
    def __init__(self, demo=False):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("句意图")
        self.root.option_add("*Font", ("Microsoft YaHei UI", 10))
        ttk.Style().theme_use("vista")
        self.events = queue.Queue()
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="Model")
        self.job, self.future, self.test_job, self.test_future = None, None, None, None
        self.revision = 0
        self.test_revision = 0
        self.settings_window = None
        self.cache = OrderedDict()
        self.cache_key = None
        self.preview_svg = ""
        self.generation_started = 0.0
        self.first_graph_seconds = None
        self.notices = []
        try:
            self.settings = load_settings()
        except Exception:
            self.settings = Settings()
            self.notices.append("无法读取原有配置或解密 API Key。原文件未被改动，请检查设置。")
        if demo:
            self.settings = replace(self.settings, auto_capture=False)
        self.capture = SelectionCapture(self.publish)
        self.tray = Tray(self.publish)
        self.popup = Popup(self.root, self.hide_popup)
        self.apply_capture()
        self.capture.start()
        self.tray.start()
        self.root.after(60, self.poll)
        if demo:
            self.root.after(250, self.show_demo)

    def publish(self, kind, payload):
        self.events.put((kind, payload))

    def apply_capture(self):
        enabled = self.settings.auto_capture and self.settings_window is None
        self.capture.configure(enabled, self.settings.max_chars)
        self.tray.enabled = enabled

    def poll(self):
        now = time.monotonic()
        for key, (created, _) in list(self.cache.items()):
            if now - created >= 300:
                del self.cache[key]
        for _ in range(100):
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "quit":
                self.quit()
                return
            if kind == "selection":
                text, bounds, generation, hwnd = payload
                if generation == self.capture.generation and self.settings_window is None and hwnd == win32gui.GetForegroundWindow():
                    self.generate(text, bounds)
            elif kind == "model":
                revision, stage, content = payload
                if revision == self.revision:
                    if stage == "error":
                        if self.preview_svg:
                            self.popup.render(self.preview_svg)
                            self.popup.status.set("本地规则预览 · AI 未完成：" + content)
                        else:
                            self.popup.error(content)
                    else:
                        self.popup.render(content, done=stage == "done")
                        elapsed = time.perf_counter() - self.generation_started
                        if self.first_graph_seconds is None:
                            self.first_graph_seconds = elapsed
                        if stage == "done":
                            self.cache[self.cache_key] = (time.monotonic(), content)
                            self.cache.move_to_end(self.cache_key)
                            while len(self.cache) > 16:
                                self.cache.popitem(last=False)
                            self.popup.status.set(f"首图 {self.first_graph_seconds:.2f}s · AI 完成 {elapsed:.2f}s · 请结合原文核对")
            elif kind == "test":
                revision, stage, content = payload
                if revision == self.test_revision and self.settings_window and stage in ("done", "error"):
                    self.settings_window.test_result(stage == "done", content)
            elif kind == "toggle":
                self.toggle()
            elif kind == "settings":
                self.open_settings()
            elif kind == "demo":
                self.show_demo()
            elif kind == "hotkey" and self.settings_window is None:
                self.capture.request()
            elif kind == "clipboard" and self.settings_window is None:
                self.from_clipboard()
            elif kind == "notice":
                if payload not in self.notices:
                    self.notices.append(payload)
                if self.settings_window:
                    self.settings_window.test_status.set("\n".join(self.notices))
            elif kind == "capture_notice":
                messagebox.showinfo("句意图", payload, parent=self.root)
        self.root.after(60, self.poll)

    def cancel_generation(self):
        self.revision += 1
        self.preview_svg = ""
        if self.job:
            self.job.cancel()
        if self.future:
            self.future.cancel()

    def hide_popup(self):
        self.cancel_generation()
        self.popup.hide()

    def generate(self, text, bounds):
        self.generation_started = time.perf_counter()
        self.first_graph_seconds = None
        try:
            self.settings.validate()
        except ValueError:
            self.cancel_generation()
            self.popup.loading(bounds, self.settings.provider)
            self.popup.error("请右键系统托盘中的句意图图标，选择“设置”并完成模型配置后再划词。")
            return
        if not 2 <= len(text) <= self.settings.max_chars:
            messagebox.showinfo("文字长度", f"请选择 2–{self.settings.max_chars} 字的文本。", parent=self.root)
            return
        self.cancel_generation()
        revision = self.revision
        self.cache_key = hashlib.sha256(text.encode("utf-8")).digest()
        cached = self.cache.get(self.cache_key)
        if cached and time.monotonic() - cached[0] < 300:
            self.cache.move_to_end(self.cache_key)
            self.popup.bounds = bounds
            self.popup.render(cached[1], done=True)
            self.popup.status.set("已复用近期 AI 图解 · 未重复请求模型 · 请结合原文核对")
            return
        self.popup.loading(bounds, self.settings.provider)
        preview = local_preview(text)
        if preview:
            self.preview_svg = to_svg(preview)
            self.popup.render(self.preview_svg)
            self.first_graph_seconds = time.perf_counter() - self.generation_started
            self.popup.status.set(f"本地规则预览 {self.first_graph_seconds:.2f}s · 尚未经 AI 核对 · 正在请求 AI…")
        self.job = ModelJob(replace(self.settings), text, lambda stage, content: self.publish("model", (revision, stage, content)))
        self.future = self.executor.submit(self.job.run)

    def from_clipboard(self):
        opened = False
        try:
            win32clipboard.OpenClipboard()
            opened = True
            if not win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                return
            text = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT).strip()
        except Exception:
            messagebox.showinfo("剪贴板忙", "请稍后重试。", parent=self.root)
            return
        finally:
            if opened:
                win32clipboard.CloseClipboard()
        point = win32api.GetCursorPos()
        self.generate(text, (*point, point[0] + 1, point[1] + 18))

    def show_demo(self):
        self.cancel_generation()
        point = win32api.GetCursorPos()
        self.popup.bounds = (*point, point[0] + 1, point[1] + 18)
        self.popup.render(to_svg(example_diagram()), done=True)
        self.popup.status.set("本地示例  ·  未调用模型，也未发送任何文本")
        self.popup.window.deiconify()

    def open_settings(self):
        self.hide_popup()
        if self.settings_window:
            self.settings_window.window.lift()
            self.settings_window.window.focus_force()
            return
        self.capture.configure(False, self.settings.max_chars)
        self.settings_window = SettingsWindow(self.root, self.settings, self.save, self.test, self.show_demo, self.close_settings)
        if self.notices:
            self.settings_window.test_status.set("\n".join(self.notices))

    def close_settings(self):
        if self.settings_window:
            self.settings_window.window.destroy()
            self.settings_window = None
        self.test_revision += 1
        if self.test_job:
            self.test_job.cancel()
        if self.test_future:
            self.test_future.cancel()
        self.apply_capture()

    def save(self, settings):
        try:
            save_settings(settings)
        except Exception as error:
            raise ValueError("设置保存失败。请检查配置目录权限和 Windows 账户加密服务。") from error
        self.settings = settings
        self.cache.clear()
        self.notices.clear()
        self.close_settings()

    def toggle(self):
        if self.settings_window:
            return
        if not self.settings.auto_capture:
            try:
                self.settings.validate()
            except ValueError:
                messagebox.showinfo("尚未配置模型", "请右键托盘图标选择“设置”，完成配置后再启用自动划词。", parent=self.root)
                return
            if not messagebox.askyesno("启用自动划词", f"之后选中的文字将自动发送给 {self.settings.provider}。\n请避免选择敏感内容。是否启用？", parent=self.root):
                return
        updated = replace(self.settings, auto_capture=not self.settings.auto_capture)
        try:
            save_settings(updated)
        except Exception:
            messagebox.showerror("保存失败", "未能保存开关状态，原设置保持不变。", parent=self.root)
            return
        self.settings = updated
        self.apply_capture()
        self.hide_popup()

    def test(self, settings):
        self.test_revision += 1
        revision = self.test_revision
        if self.test_job:
            self.test_job.cancel()
        if self.test_future:
            self.test_future.cancel()
        self.test_job = ModelJob(settings, SAMPLE, lambda stage, content: self.publish("test", (revision, stage, content)))
        self.test_future = self.executor.submit(self.test_job.run)

    def quit(self):
        self.cancel_generation()
        if self.test_job:
            self.test_job.cancel()
        if self.test_future:
            self.test_future.cancel()
        self.capture.stop()
        self.tray.stop()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        pass
    mutex = win32event.CreateMutex(None, False, "Local\\TextLens.SingleInstance")
    try:
        if win32api.GetLastError() == 183:
            return
        Application(demo="--demo" in sys.argv).run()
    finally:
        win32api.CloseHandle(mutex)
