import tkinter as tk
import xml.etree.ElementTree as ET
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import win32api
import win32con
import win32gui

from til import theme
from til.config import FORMATS, Settings
from til.diagram import draw_svg


def place_popup(bounds, width, height, work_area):
    left, top, right, bottom = work_area
    anchor_left, anchor_top, anchor_right, anchor_bottom = bounds
    position_x = max(left + 8, min((anchor_left + anchor_right - width) // 2, right - width - 8))
    position_y = anchor_top - height - 12
    if position_y < top + 8:
        position_y = anchor_bottom + 12
    position_y = max(top + 8, min(position_y, bottom - height - 8))
    return int(position_x), int(position_y)


def action_button(parent, text, command, background, foreground, active_background, active_foreground, font, padx=16, pady=8, outline=None):
    """扁平按钮；悬停换底色，禁用时保持安静。"""
    button = tk.Button(
        parent, text=text, command=command, relief="flat", borderwidth=0, highlightthickness=1 if outline else 0,
        highlightbackground=outline or background, highlightcolor=outline or background,
        background=background, foreground=foreground, activebackground=active_background, activeforeground=active_foreground,
        disabledforeground=theme.HANDLE, cursor="hand2", font=font, padx=padx, pady=pady, takefocus=True,
    )

    def enter(event):
        if str(button.cget("state")) != "disabled":
            button.configure(background=active_background, foreground=active_foreground)

    def leave(event):
        button.configure(background=background, foreground=foreground)

    button.bind("<Enter>", enter)
    button.bind("<Leave>", leave)
    return button


class Popup:
    """选区上方的小图解浮窗：立即显示，内容轻量动效，不留大片空白。"""

    HEADER = 42
    FOOTER = 26
    RADIUS = 24
    # 浮窗的最大内容宽度。图比这窄时按原尺寸显示，不再拉满。
    MAX_WIDTH = 380

    def __init__(self, root, close):
        self.window = tk.Toplevel(root)
        self.window.title("TIL · 图解")
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.attributes("-alpha", 1.0)
        self.window.configure(background=theme.LINE)
        theme.configure_styles(self.window)
        self.svg = ""
        self.bounds = (300, 500, 600, 520)
        self.scale = 1
        self.status = tk.StringVar(value="")
        self.shown_status = tk.StringVar(value="")
        self.status.trace_add("write", lambda *_: self.fit_status())
        self.close = close
        self.pulse = None
        self.visible = False
        self.hover_card = None
        self.content_frames = []
        shell = tk.Frame(self.window, background=theme.PAPER)
        shell.pack(fill="both", expand=True, padx=1, pady=1)
        header = tk.Frame(shell, background=theme.PAPER, height=self.HEADER)
        header.pack(fill="x")
        header.pack_propagate(False)
        mark = tk.Canvas(header, width=28, height=28, background=theme.PAPER, highlightthickness=0)
        mark.pack(side="left", padx=(12, 6), pady=7)
        mark.create_oval(2, 2, 26, 26, fill=theme.TINT, outline="")
        mark.create_polygon(14, 5, 18, 14, 14, 23, 10, 14, fill=theme.ACCENT, outline="")
        mark.create_oval(22, 2, 26, 6, fill=theme.ACCENT, outline="")
        title = tk.Label(header, text="TIL", foreground=theme.INK, background=theme.PAPER, font=theme.font(10, "bold"))
        title.pack(side="left", pady=8)
        subtitle = tk.Label(header, text="灵感小笺", foreground=theme.MUTED, background=theme.PAPER, font=theme.font(8))
        subtitle.pack(side="left", padx=(8, 0), pady=8)
        self.close_button = action_button(header, "×", close, theme.PAPER, theme.MUTED, theme.TINT, theme.ACCENT, theme.font(11), padx=8, pady=1)
        self.close_button.pack(side="right", padx=(2, 8), pady=8)
        self.export_button = action_button(header, "导出 ↗", self.export, theme.PAPER, theme.ACCENT, theme.TINT, theme.ACCENT, theme.font(8), padx=8, pady=3)
        self.export_button.pack(side="right", pady=8)
        for widget in (header, mark, title, subtitle):
            widget.bind("<ButtonPress-1>", self.drag_start)
            widget.bind("<B1-Motion>", self.drag_move)
        # 先给页脚和分隔线占位，再让画布吃掉剩余高度；否则画布会把它们压成 0。
        footer = tk.Frame(shell, background=theme.PAPER, height=self.FOOTER)
        footer.pack(side="bottom", fill="x")
        footer.pack_propagate(False)
        tk.Label(footer, textvariable=self.shown_status, background=theme.PAPER, foreground=theme.MUTED, anchor="w", font=theme.font(8)).pack(side="left", fill="x", expand=True, padx=(10, 8), pady=3)
        tk.Frame(shell, background=theme.TINT, height=1).pack(side="bottom", fill="x", padx=16)
        content = tk.Frame(shell, background=theme.PAPER)
        content.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(content, background=theme.PAPER, highlightthickness=0, borderwidth=0, width=self.MAX_WIDTH, height=160)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar = ttk.Scrollbar(content, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.tag_bind("node", "<Enter>", self.highlight_node)
        self.canvas.tag_bind("node", "<Leave>", self.clear_highlight)
        self.canvas.bind("<MouseWheel>", lambda event: self.canvas.yview_scroll(-int(event.delta / 120), "units"))
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

    def fit_status(self):
        """状态栏只有一行；按窗口宽度截断，原文仍完整保存在 status 里。"""
        text = self.status.get()
        limit = max(16, int((self.window.winfo_width() - 26) / 7.2))
        self.shown_status.set(text if len(text) <= limit else text[:limit - 1] + "…")

    def shape(self, width, height):
        """圆角窗口：用窗口区域裁掉四角。"""
        try:
            region = win32gui.CreateRoundRectRgn(0, 0, width + 1, height + 1, self.RADIUS, self.RADIUS)
            win32gui.SetWindowRgn(self.hwnd, region, True)
        except Exception:
            pass

    def layout(self, diagram_width=360, diagram_height=200):
        monitor = win32api.MonitorFromPoint(self.bounds[:2], win32con.MONITOR_DEFAULTTONEAREST)
        work_area = win32api.GetMonitorInfo(monitor)["Work"]
        available_width = work_area[2] - work_area[0] - 48
        chrome = self.HEADER + self.FOOTER + 3
        self.scale = min(1.0, max(.5, available_width / max(1, diagram_width)))
        content_width = max(240, min(self.MAX_WIDTH, round(diagram_width * self.scale)))
        content_height = round(diagram_height * self.scale)
        max_height = work_area[3] - work_area[1] - 32
        scroll_width = self.scrollbar.winfo_reqwidth() if content_height + chrome > max_height else 0
        if scroll_width:
            self.scrollbar.pack(side="right", fill="y", before=self.canvas)
        else:
            self.scrollbar.pack_forget()
        width = content_width + scroll_width + 2
        height = min(content_height + chrome, max_height)
        self.window.geometry(f"{width}x{height}")
        self.window.update_idletasks()
        self.fit_status()
        self.shape(width, height)
        position_x, position_y = place_popup(self.bounds, width, height, work_area)
        self.reveal(position_x, position_y, width, height)

    def reveal(self, position_x, position_y, width, height):
        """Visibility is synchronous; only diagram content may animate afterward."""
        if not self.window.winfo_exists():
            return
        self.window.attributes("-alpha", 1.0)
        self.window.deiconify()
        win32gui.SetWindowPos(self.hwnd, win32con.HWND_TOPMOST, position_x, position_y, width, height,
                              win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW)
        self.visible = True

    def animate_pulse(self):
        """加载态的小呼吸点，让等待看起来是活的。"""
        if not self.window.winfo_exists():
            return
        if self.visible and not self.svg:
            self.pulse_index = (getattr(self, "pulse_index", 0) + 1) % 3
            for index in range(3):
                self.canvas.itemconfigure(f"loading-dot:{index}", fill=theme.ACCENT if index == self.pulse_index else theme.LINE)
        self.pulse = self.window.after(240, self.animate_pulse)

    def loading(self, bounds, provider):
        self.cancel_pending()
        self.bounds = bounds
        self.svg = ""
        self.export_button.configure(state="disabled")
        self.status.set(f"{provider}  ·  正在分析，节点会逐步出现  ·  点击 × 取消")
        self.layout(320, 150)
        self.draw_loading()
        # 布局刚设好时 winfo_width 还是 1，等窗口真正映射后再按实际宽度重画居中。
        self.pending_loading = self.window.after(40, self.draw_loading)

    def draw_loading(self):
        if not self.window.winfo_exists() or self.svg:
            return
        self.canvas.delete("all")
        width = self.canvas.winfo_width()
        if width <= 1:
            width = self.window.winfo_width() - 16
        height = max(self.canvas.winfo_height(), 140)
        self.canvas.configure(scrollregion=(0, 0, width, height))
        center = width / 2
        self.canvas.create_oval(center - 23, 6, center + 23, 52, fill=theme.TINT, outline="")
        self.canvas.create_polygon(center, 13, center + 10, 29, center, 45, center - 10, 29, fill=theme.ACCENT, outline="")
        self.canvas.create_oval(center + 19, 8, center + 25, 14, fill=theme.ACCENT, outline="")
        self.canvas.create_text(center, 70, text="让想法，慢慢浮现。", fill=theme.INK, font=theme.font(11, "bold"))
        self.canvas.create_text(center, 94, text="正在连接概念与关系", fill=theme.MUTED, font=theme.font(8))
        for index in range(3):
            x = center + (index - 1) * 12
            self.canvas.create_oval(x - 2, 115, x + 2, 119, fill=theme.LINE, outline="", tags=f"loading-dot:{index}")
        if self.pulse is None:
            self.pulse_index = 0
            self.pulse = self.window.after(240, self.animate_pulse)

    def clear_highlight(self, event=None):
        if self.hover_card:
            item, outline, width = self.hover_card
            if self.canvas.type(item):
                self.canvas.itemconfigure(item, outline=outline, width=width)
            self.hover_card = None

    def highlight_node(self, event):
        self.clear_highlight()
        tags = self.canvas.gettags("current")
        identity = next((tag[5:] for tag in tags if tag.startswith("node:")), None)
        cards = self.canvas.find_withtag(f"card:{identity}") if identity else ()
        if cards:
            item = cards[0]
            self.hover_card = (item, self.canvas.itemcget(item, "outline"), self.canvas.itemcget(item, "width"))
            self.canvas.itemconfigure(item, outline=theme.ACCENT, width=1.6)

    def settle_content(self):
        # Six frames once, never an idle animation; cancellation restores the final state.
        items = self.canvas.find_all()
        if not items:
            return
        for item in items[1:]:  # Keep the paper background stationary.
            self.canvas.addtag_withtag("arrival", item)
        self.canvas.move("arrival", 0, 5)
        self.arrival_offset = 5
        self.content_frames = [self.window.after(step * 16, lambda step=step: self.settle_step(step)) for step in range(1, 7)]

    def settle_step(self, step):
        offset = 5 * (1 - step / 6) ** 3
        self.canvas.move("arrival", 0, offset - self.arrival_offset)
        self.arrival_offset = offset
        if step == 6:
            self.content_frames = []
            self.canvas.dtag("arrival", "arrival")

    def render(self, content, done=False, complete=None):
        arriving = not self.svg
        self.cancel_pending()
        self.clear_highlight()
        try:
            root = ET.fromstring(content)
            width, height = float(root.get("width")), float(root.get("height"))
            self.layout(width, height)
            draw_svg(self.canvas, content, self.scale)
            self.canvas.yview_moveto(0)
            self.canvas.configure(background=theme.PAPER)
        except (ValueError, TypeError, ET.ParseError, tk.TclError):
            message = "本地图解绘制失败，请重新选择文本。"
            if complete:
                complete(None, message)
            else:
                self.error(message)
            return
        self.svg = content
        if arriving:
            self.settle_content()
        self.stop_pulse()
        self.export_button.configure(state="normal" if done else "disabled")
        self.status.set("图解已完成  ·  AI 可能有误，请结合原文核对" if done else "正在生成图解…  ·  点击 × 取消")
        if complete:
            complete(content, None)

    def stop_pulse(self):
        if self.pulse is not None:
            try:
                self.window.after_cancel(self.pulse)
            except tk.TclError:
                pass
            self.pulse = None

    def error(self, message):
        self.cancel_pending()
        self.clear_highlight()
        self.stop_pulse()
        self.export_button.configure(state="disabled")
        self.status.set("生成未完成  ·  检查设置后重新选择文本")
        self.layout(320, 120)
        self.canvas.delete("all")
        width = self.canvas.winfo_width()
        if width <= 1:
            width = self.window.winfo_width() - 16
        center, top = width / 2, 8
        self.canvas.configure(scrollregion=(0, 0, width, max(self.canvas.winfo_height(), 100)))
        self.canvas.create_text(center, top, text="◈  图解未完成", fill=theme.ACCENT, font=theme.font(10, "bold"))
        self.canvas.create_text(center, top + 26, text=message, width=max(160, width - 32), justify="center", fill=theme.INK, font=theme.font(8))

    def hide(self):
        self.clear_highlight()
        self.svg = ""
        self.stop_pulse()
        self.cancel_pending()
        self.visible = False
        self.window.attributes("-alpha", 1.0)
        self.window.withdraw()

    def cancel_pending(self):
        for frame in self.content_frames:
            self.window.after_cancel(frame)
        self.content_frames = []
        if getattr(self, "arrival_offset", 0):
            self.canvas.move("arrival", 0, -self.arrival_offset)
        self.arrival_offset = 0
        self.canvas.dtag("arrival", "arrival")
        pending = getattr(self, "pending_loading", None)
        if pending is not None:
            try:
                self.window.after_cancel(pending)
            except tk.TclError:
                pass
            self.pending_loading = None

    def shutdown(self):
        self.stop_pulse()
        self.cancel_pending()

    def export(self):
        if not self.svg:
            return
        path = filedialog.asksaveasfilename(parent=self.window, title="导出图解", defaultextension=".svg", initialfile="TIL.svg", filetypes=[("SVG 矢量图", "*.svg")])
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
        self.window.title("TIL · 设置")
        self.window.configure(background=theme.PAPER)
        self.window.resizable(False, False)
        theme.configure_styles(self.window)
        self.window.protocol("WM_DELETE_WINDOW", close)
        self.window.bind("<Escape>", lambda event: close())
        self.values = {name: tk.StringVar(value=str(getattr(current, name))) for name in ("provider", "api_format", "endpoint", "api_key", "model", "max_chars", "timeout")}
        self.enabled = tk.BooleanVar(value=current.auto_capture)
        self.copy_fallback = tk.BooleanVar(value=current.copy_fallback)
        self.test_status = tk.StringVar(value="未连接 · 选择格式并填写你的模型服务")
        tk.Frame(self.window, background=theme.ACCENT, height=4).pack(fill="x")
        page = tk.Frame(self.window, background=theme.PAPER, padx=30, pady=20)
        page.pack(fill="both", expand=True)
        masthead = tk.Frame(page, background=theme.PAPER)
        masthead.pack(fill="x", pady=(0, 16))
        tk.Label(masthead, text="◈", foreground=theme.ACCENT, background=theme.PAPER, font=theme.font(17, "bold")).pack(side="left", padx=(0, 10), pady=(2, 0))
        heading = tk.Frame(masthead, background=theme.PAPER)
        heading.pack(side="left")
        tk.Label(heading, text="TIL · 文转图理解器", font=theme.font(22, "bold"), foreground=theme.INK, background=theme.PAPER).pack(anchor="w")
        tk.Label(heading, text="选中一句话，读懂它背后的结构。", foreground=theme.MUTED, background=theme.PAPER, font=theme.font(10)).pack(anchor="w", pady=(3, 0))
        tk.Label(masthead, text="设置", foreground=theme.ACCENT, background=theme.PAPER, font=theme.font(10, "bold")).pack(side="right", pady=(8, 0))
        service = self.card(page, "01", "模型服务")
        fields = tk.Frame(service, background=theme.SURFACE)
        fields.pack(fill="x")
        fields.columnconfigure(1, weight=1)
        for row, (name, label) in enumerate((("provider", "供应商名称"), ("api_format", "上游格式"), ("endpoint", "API 请求地址"), ("api_key", "API Key"), ("model", "模型 ID"))):
            tk.Label(fields, text=label, background=theme.SURFACE, foreground=theme.INK, anchor="w", font=theme.font(10)).grid(row=row, column=0, sticky="w", padx=(0, 20), pady=5)
            if name == "api_format":
                field = ttk.Combobox(fields, textvariable=self.values[name], values=FORMATS, state="readonly", width=38, font=theme.font(10))
            else:
                field = ttk.Entry(fields, textvariable=self.values[name], width=40, show="●" if name == "api_key" else "", font=theme.font(10))
            field.grid(row=row, column=1, sticky="ew", pady=5)
        self.note(service, "地址可填 https://供应商/v1 或完整接口路径；本机服务允许 HTTP。API Key 使用 Windows 当前账户加密保存，不写入日志。", (12, 0))
        privacy = self.card(page, "02", "划词与隐私")
        ttk.Checkbutton(privacy, text="启用自动划词图解（选中的文字将发送到上述供应商）", variable=self.enabled, style="Card.TCheckbutton").pack(anchor="w", pady=(0, 5))
        ttk.Checkbutton(privacy, text="PDF / 文档兼容划词（读不到时临时 Ctrl+C，并尝试还原剪贴板）", variable=self.copy_fallback, style="Card.TCheckbutton").pack(anchor="w")
        limits = tk.Frame(privacy, background=theme.SURFACE)
        limits.pack(fill="x", pady=(12, 0))
        for name, label in (("max_chars", "最多字数"), ("timeout", "请求超时 / 秒")):
            tk.Label(limits, text=label, background=theme.SURFACE, foreground=theme.INK, font=theme.font(10)).pack(side="left", padx=(0, 8))
            ttk.Entry(limits, textvariable=self.values[name], width=8, font=theme.font(10)).pack(side="left", padx=(0, 28))
        self.note(privacy, "默认只读选区；兼容模式支持 WPS/PDF/Word 等文档应用，跳过可识别的密码框。临时复制可能进入系统剪贴板历史；无法安全备份时不自动复制。扫描件须先 OCR。手动读取当前选区：Alt+Q。关闭设置后继续后台运行；图解仅短期缓存在内存。", (12, 0))
        status = tk.Frame(page, background=theme.PAPER)
        status.pack(fill="x", pady=(0, 12))
        tk.Label(status, text="◈", foreground=theme.ACCENT, background=theme.PAPER, font=theme.font(8)).pack(side="left", padx=(3, 8), pady=(2, 0))
        tk.Label(status, textvariable=self.test_status, wraplength=540, justify="left", foreground=theme.MUTED, background=theme.PAPER, font=theme.font(9)).pack(side="left", fill="x", expand=True)
        tk.Frame(page, background=theme.LINE, height=1).pack(fill="x", pady=(0, 14))
        actions = tk.Frame(page, background=theme.PAPER)
        actions.pack(fill="x")
        self.test_button = action_button(actions, "测试模型连接", self.test, theme.SURFACE, theme.ACCENT, theme.BAND, theme.ACCENT_DEEP, theme.font(10), outline=theme.LINE)
        self.test_button.pack(side="left")
        action_button(actions, "本地示例", demo, theme.SURFACE, theme.MUTED, theme.BAND, theme.ACCENT_DEEP, theme.font(10), outline=theme.LINE).pack(side="left", padx=8)
        action_button(actions, "保存设置", self.save, theme.ACCENT, theme.PAPER, theme.ACCENT_DEEP, theme.PAPER, theme.font(10, "bold"), padx=22).pack(side="right")
        action_button(actions, "取消", close, theme.SURFACE, theme.INK, theme.BAND, theme.ACCENT_DEEP, theme.font(10), padx=18, outline=theme.LINE).pack(side="right", padx=(0, 10))
        self.window.update_idletasks()
        width, height = self.window.winfo_reqwidth(), self.window.winfo_reqheight()
        self.window.geometry(f"+{max(0, (self.window.winfo_screenwidth() - width) // 2)}+{max(0, (self.window.winfo_screenheight() - height) // 3)}")
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()

    def card(self, parent, index, title):
        card = tk.Frame(parent, background=theme.SURFACE, highlightthickness=1, highlightbackground=theme.LINE)
        card.pack(fill="x", pady=(0, 12))
        inner = tk.Frame(card, background=theme.SURFACE, padx=20, pady=15)
        inner.pack(fill="both", expand=True)
        head = tk.Frame(inner, background=theme.SURFACE)
        head.pack(fill="x")
        tk.Label(head, text=index, foreground=theme.ACCENT, background=theme.SURFACE, font=theme.font(10, "bold")).pack(side="left", padx=(0, 10))
        tk.Label(head, text=title, foreground=theme.INK, background=theme.SURFACE, font=theme.font(11, "bold")).pack(side="left")
        tk.Frame(inner, background=theme.LINE, height=1).pack(fill="x", pady=(8, 12))
        body = tk.Frame(inner, background=theme.SURFACE)
        body.pack(fill="both", expand=True)
        return body

    def note(self, parent, text, pady):
        tk.Label(parent, text=text, justify="left", wraplength=500, foreground=theme.MUTED, background=theme.SURFACE, font=theme.font(9)).pack(anchor="w", pady=pady)

    def read(self):
        values = {name: variable.get().strip() for name, variable in self.values.items()}
        try:
            values["max_chars"], values["timeout"] = int(values["max_chars"]), int(values["timeout"])
        except ValueError as error:
            raise ValueError("最多字数和超时必须为整数。") from error
        settings = Settings(**values, auto_capture=self.enabled.get(), copy_fallback=self.copy_fallback.get())
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
        self.test_status.set("连接成功 · 模型返回的 DSL 图解已通过解析和 SVG 生成校验，可保存并开始划词。" if success else message)
