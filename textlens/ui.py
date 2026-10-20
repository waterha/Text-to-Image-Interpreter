import tkinter as tk
import xml.etree.ElementTree as ET
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import win32api
import win32con
import win32gui

from textlens.config import FORMATS, Settings
from textlens.diagram import draw_svg


BACKGROUND = "#f6f9fb"
INK = "#173442"
TEAL = "#0d766e"


def place_popup(bounds, width, height, work_area):
    left, top, right, bottom = work_area
    anchor_left, anchor_top, anchor_right, anchor_bottom = bounds
    position_x = max(left + 8, min((anchor_left + anchor_right - width) // 2, right - width - 8))
    position_y = anchor_top - height - 12
    if position_y < top + 8:
        position_y = anchor_bottom + 12
    position_y = max(top + 8, min(position_y, bottom - height - 8))
    return int(position_x), int(position_y)


class Popup:
    def __init__(self, root, close):
        self.window = tk.Toplevel(root)
        self.window.title("句意图 · 图解")
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.configure(background="#cbdce4")
        self.svg = ""
        self.bounds = (300, 500, 600, 520)
        self.scale = 1
        self.status = tk.StringVar(value="")
        self.close = close
        frame = tk.Frame(self.window, background=BACKGROUND, borderwidth=1)
        frame.pack(fill="both", expand=True, padx=1, pady=1)
        header = tk.Frame(frame, background="#e9f2f4", height=42)
        header.pack(fill="x")
        title = tk.Label(header, text="◈  句意图  /  TEXTLENS", foreground=TEAL, background="#e9f2f4", font=("Microsoft YaHei UI", 10, "bold"), padx=14, pady=10)
        title.pack(side="left")
        for label, command in (("×", close), ("导出 SVG", self.export)):
            button = tk.Button(header, text=label, command=command, relief="flat", background="#e9f2f4", foreground=INK, activebackground="#dbe9ed", cursor="hand2", padx=10, takefocus=True)
            button.pack(side="right", padx=2, pady=5)
            if label == "导出 SVG":
                self.export_button = button
        for widget in (header, title):
            widget.bind("<ButtonPress-1>", self.drag_start)
            widget.bind("<B1-Motion>", self.drag_move)
        content = tk.Frame(frame, background=BACKGROUND)
        content.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(content, background=BACKGROUND, highlightthickness=0, width=700, height=340)
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(content, orient="vertical", command=self.canvas.yview)
        scrollbar.pack(side="right", fill="y")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.bind("<MouseWheel>", lambda event: self.canvas.yview_scroll(-int(event.delta / 120), "units"))
        footer = tk.Label(frame, textvariable=self.status, background=BACKGROUND, foreground="#657c88", anchor="w", padx=14, pady=9, font=("Microsoft YaHei UI", 9))
        footer.pack(fill="x")
        self.window.bind("<Escape>", lambda event: close())
        self.window.update_idletasks()
        self.hwnd = win32gui.GetParent(self.window.winfo_id()) or self.window.winfo_id()
        style = win32gui.GetWindowLong(self.hwnd, win32con.GWL_EXSTYLE)
        win32gui.SetWindowLong(self.hwnd, win32con.GWL_EXSTYLE, style | win32con.WS_EX_TOOLWINDOW | 0x08000000)

    def drag_start(self, event):
        self.drag_offset = (event.x_root - self.window.winfo_x(), event.y_root - self.window.winfo_y())

    def drag_move(self, event):
        offset_x, offset_y = self.drag_offset
        win32gui.SetWindowPos(self.hwnd, win32con.HWND_TOPMOST, event.x_root - offset_x, event.y_root - offset_y, 0, 0, win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)

    def layout(self, diagram_height=340):
        monitor = win32api.MonitorFromPoint(self.bounds[:2], win32con.MONITOR_DEFAULTTONEAREST)
        work_area = win32api.GetMonitorInfo(monitor)["Work"]
        available_width = work_area[2] - work_area[0] - 32
        self.scale = min(1.15, max(.45, (available_width - 20) / 700))
        width = round(700 * self.scale) + 20
        height = min(round(diagram_height * self.scale) + 86, 620, work_area[3] - work_area[1] - 32)
        self.window.geometry(f"{width}x{height}")
        self.window.update_idletasks()
        position_x, position_y = place_popup(self.bounds, width, height, work_area)
        self.window.deiconify()
        win32gui.SetWindowPos(self.hwnd, win32con.HWND_TOPMOST, position_x, position_y, width, height, win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW)

    def loading(self, bounds, provider):
        self.bounds = bounds
        self.svg = ""
        self.export_button.configure(state="disabled")
        self.status.set(f"{provider}  ·  正在分析，节点会逐步出现  ·  点击 × 取消")
        self.layout()
        self.canvas.delete("all")
        self.canvas.configure(scrollregion=(0, 0, 700 * self.scale, 340 * self.scale))
        self.canvas.create_text(350 * self.scale, 130 * self.scale, text="把一段话，变成一张图。", fill=INK, font=("Microsoft YaHei UI", 19, "bold"))
        self.canvas.create_text(350 * self.scale, 184 * self.scale, text="正在识别概念与关系…", fill="#657c88", font=("Microsoft YaHei UI", 11))

    def render(self, svg, done=False):
        self.svg = svg
        height = float(ET.fromstring(svg).get("height"))
        self.layout(height)
        draw_svg(self.canvas, svg, self.scale)
        self.export_button.configure(state="normal" if done else "disabled")
        self.status.set("图解已完成  ·  AI 可能有误，请结合原文核对  ·  可拖动标题栏" if done else "正在生成图解…  ·  点击 × 取消")

    def error(self, message):
        self.export_button.configure(state="disabled")
        self.status.set("生成未完成  ·  检查设置后重新选择文本")
        self.canvas.delete("all")
        self.canvas.create_text(350 * self.scale, 150 * self.scale, text=message, width=580 * self.scale, justify="center", fill="#9d4336", font=("Microsoft YaHei UI", 12))

    def hide(self):
        self.window.withdraw()

    def export(self):
        if not self.svg:
            return
        path = filedialog.asksaveasfilename(parent=self.window, title="导出图解", defaultextension=".svg", initialfile="句意图.svg", filetypes=[("SVG 矢量图", "*.svg")])
        if path:
            try:
                Path(path).write_text(self.svg, encoding="utf-8")
                self.status.set("已导出 SVG  ·  文件含图解内容，请注意隐私")
            except OSError:
                messagebox.showerror("导出失败", "无法写入文件，请换一个保存位置。", parent=self.window)


class SettingsWindow:
    def __init__(self, root, current, save, test, demo, close):
        self.save_callback, self.test_callback, self.close_callback = save, test, close
        self.window = tk.Toplevel(root)
        self.window.title("句意图 · 设置")
        self.window.configure(background=BACKGROUND)
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", close)
        self.window.bind("<Escape>", lambda event: close())
        self.values = {name: tk.StringVar(value=str(getattr(current, name))) for name in ("provider", "api_format", "endpoint", "api_key", "model", "max_chars", "timeout")}
        self.enabled = tk.BooleanVar(value=current.auto_capture)
        self.test_status = tk.StringVar(value="未连接 · 选择格式并填写你的模型服务")
        body = tk.Frame(self.window, background=BACKGROUND, padx=28, pady=22)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="句意图", font=("Microsoft YaHei UI", 25, "bold"), foreground=INK, background=BACKGROUND).grid(row=0, column=0, columnspan=2, sticky="w")
        tk.Label(body, text="选中一句话，读懂它背后的结构。", foreground="#657c88", background=BACKGROUND, font=("Microsoft YaHei UI", 11)).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 20))
        tk.Label(body, text="01   模型服务", foreground=TEAL, background=BACKGROUND, font=("Microsoft YaHei UI", 11, "bold")).grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 8))
        labels = [("provider", "供应商名称"), ("api_format", "上游格式"), ("endpoint", "API 请求地址"), ("api_key", "API Key"), ("model", "模型 ID")]
        for row, (name, label) in enumerate(labels, 3):
            tk.Label(body, text=label, background=BACKGROUND, foreground=INK, anchor="w").grid(row=row, column=0, sticky="w", padx=(0, 18), pady=7)
            if name == "api_format":
                field = ttk.Combobox(body, textvariable=self.values[name], values=FORMATS, state="readonly", width=52)
            else:
                field = ttk.Entry(body, textvariable=self.values[name], width=55, show="●" if name == "api_key" else "")
            field.grid(row=row, column=1, sticky="ew", pady=7)
        tk.Label(body, text="地址可填 https://供应商/v1 或完整接口路径；本机服务允许 HTTP。\nAPI Key 使用 Windows 当前账户加密保存，不写入日志。", justify="left", foreground="#657c88", background=BACKGROUND, font=("Microsoft YaHei UI", 9)).grid(row=8, column=0, columnspan=2, sticky="w", pady=(6, 16))
        tk.Label(body, text="02   划词与隐私", foreground=TEAL, background=BACKGROUND, font=("Microsoft YaHei UI", 11, "bold")).grid(row=9, column=0, columnspan=2, sticky="w", pady=(0, 6))
        ttk.Checkbutton(body, text="启用自动划词图解（选中的文字将发送到上述供应商）", variable=self.enabled).grid(row=10, column=0, columnspan=2, sticky="w", pady=6)
        limits = tk.Frame(body, background=BACKGROUND)
        limits.grid(row=11, column=0, columnspan=2, sticky="w", pady=6)
        for name, label in (("max_chars", "最多字数"), ("timeout", "请求超时 / 秒")):
            tk.Label(limits, text=label, background=BACKGROUND, foreground=INK).pack(side="left", padx=(0, 8))
            ttk.Entry(limits, textvariable=self.values[name], width=8).pack(side="left", padx=(0, 24))
        tk.Label(body, text="不会读取密码框、不会自动复制，也不保存划词历史。\n不支持划词的应用：复制后按 Ctrl+Alt+V 主动图解剪贴板。\n手动读取当前选区：Ctrl+Alt+I。关闭此窗口后仍在托盘运行。", justify="left", foreground="#657c88", background=BACKGROUND, font=("Microsoft YaHei UI", 9)).grid(row=12, column=0, columnspan=2, sticky="w", pady=(7, 12))
        tk.Label(body, textvariable=self.test_status, wraplength=570, justify="left", foreground=TEAL, background=BACKGROUND, font=("Microsoft YaHei UI", 9)).grid(row=13, column=0, columnspan=2, sticky="w", pady=(0, 14))
        actions = tk.Frame(body, background=BACKGROUND)
        actions.grid(row=14, column=0, columnspan=2, sticky="ew")
        self.test_button = ttk.Button(actions, text="测试模型连接", command=self.test)
        self.test_button.pack(side="left")
        ttk.Button(actions, text="本地示例", command=demo).pack(side="left", padx=8)
        ttk.Button(actions, text="保存设置", command=self.save).pack(side="right")
        ttk.Button(actions, text="取消", command=close).pack(side="right", padx=8)
        self.window.update_idletasks()
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()

    def read(self):
        values = {name: variable.get().strip() for name, variable in self.values.items()}
        try:
            values["max_chars"], values["timeout"] = int(values["max_chars"]), int(values["timeout"])
        except ValueError as error:
            raise ValueError("最多字数和超时必须为整数。") from error
        settings = Settings(**values, auto_capture=self.enabled.get())
        settings.validate()
        return settings

    def save(self):
        try:
            self.save_callback(self.read())
        except (ValueError, OSError) as error:
            messagebox.showerror("无法保存", str(error), parent=self.window)

    def test(self):
        try:
            settings = self.read()
        except ValueError as error:
            messagebox.showerror("请检查设置", str(error), parent=self.window)
            return
        self.test_button.configure(state="disabled")
        self.test_status.set("正在用内置示例测试模型与流式图解协议（会产生一次 API 调用）…")
        self.test_callback(settings)

    def test_result(self, success, message):
        self.test_button.configure(state="normal")
        self.test_status.set("连接成功 · 模型已返回有效图解，可保存并开始划词。" if success else message)
