"""Text to Image Interpreter（TIL）的配色、字体与 ttk 外观。

整套界面只用三种颜色：纸色（底）、墨色（字）、陶土色（强调）。
其余色阶都由这三种颜色按比例混合得到，不再引入新的色相。

  PAPER  #faf3e7   暖米白，窗口与画布底色
  INK    #2e2018   暖褐黑，标题与正文      纸色上 14.3:1
  ACCENT #9c4520   陶土红，强调与主按钮    纸色上 5.8:1
"""

import tkinter as tk
from tkinter import ttk


PAPER = "#faf3e7"
INK = "#2e2018"
ACCENT = "#9c4520"

# 纸色向白 62%：输入框、卡片等需要比底色亮一层的表面。
SURFACE = "#fdfaf6"
# 墨色 5.5% 混入纸色：标题栏、悬停等需要比底色暗一层的表面。
BAND = "#efe7dc"
# 墨色 13% 混入纸色：分隔线与描边。
LINE = "#dfd8cc"
# 墨色 26% 混入纸色：滚动条滑块。
HANDLE = "#cbbfae"
# 墨色 66% 混入纸色：次要文字，纸色上 4.9:1。
MUTED = "#73685e"
# 陶土色 10% 混入纸色：节点底色与选中态。
TINT = "#f1e2d3"
# 墨色 18% 混入陶土色：主按钮的按下态，纸色上 6.9:1。
ACCENT_DEEP = "#883e1f"

FONT = "Microsoft YaHei UI"


def font(size, weight="normal"):
    return (FONT, size, weight)


def configure_styles(window):
    """把 ttk 控件换成暖色外观；ttk 不支持逐控件配色，只能走 Style。"""
    style = ttk.Style(window)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure("TEntry", fieldbackground=SURFACE, foreground=INK, bordercolor=LINE, lightcolor=SURFACE, darkcolor=SURFACE, insertcolor=ACCENT, padding=6)
    style.map("TEntry", bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)], foreground=[("disabled", MUTED)], fieldbackground=[("disabled", BAND)])
    style.configure("TCombobox", fieldbackground=SURFACE, background=SURFACE, foreground=INK, arrowcolor=ACCENT, bordercolor=LINE, lightcolor=SURFACE, darkcolor=SURFACE, padding=5)
    style.map("TCombobox", fieldbackground=[("readonly", SURFACE)], foreground=[("readonly", INK)], bordercolor=[("focus", ACCENT)], arrowcolor=[("active", ACCENT_DEEP)])
    style.configure("TScrollbar", background=HANDLE, troughcolor=BAND, bordercolor=BAND, arrowcolor=INK, lightcolor=HANDLE, darkcolor=HANDLE, gripcount=0, arrowsize=14)
    style.map("TScrollbar", background=[("active", ACCENT)], arrowcolor=[("active", PAPER)])
    style.configure("Card.TCheckbutton", background=SURFACE, foreground=INK, focuscolor=SURFACE, indicatorbackground=SURFACE, indicatorforeground=ACCENT, indicatormargin=4, padding=2)
    style.map("Card.TCheckbutton", background=[("active", TINT)], indicatorbackground=[("selected", ACCENT)], indicatorforeground=[("selected", PAPER)], foreground=[("active", ACCENT_DEEP)])
    for pattern, color in (("*TCombobox*Listbox.background", SURFACE), ("*TCombobox*Listbox.foreground", INK), ("*TCombobox*Listbox.selectBackground", ACCENT), ("*TCombobox*Listbox.selectForeground", PAPER)):
        window.option_add(pattern, color)


def window_icon(master, size=32):
    """窗口图标：陶土色底上一枚纸色菱形。"""
    image = tk.PhotoImage(master=master, width=size, height=size)
    center = (size - 1) / 2
    radius = size * 0.31
    image.put([[PAPER if abs(column - center) + abs(row - center) <= radius else ACCENT for column in range(size)] for row in range(size)])
    return image
