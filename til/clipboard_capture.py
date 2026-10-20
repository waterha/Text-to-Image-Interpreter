import ctypes
import time
from ctypes import wintypes
from pathlib import PureWindowsPath

import win32api
import win32clipboard
import win32con
import win32gui
import win32process
import pywintypes


DOCUMENT_APPS = frozenset(("wps.exe", "wpspdf.exe", "winword.exe", "onenote.exe", "wordpad.exe", "notepad.exe", "notepad++.exe", "acrord32.exe", "acrobat.exe", "sumatrapdf.exe", "foxitpdfreader.exe", "foxitreader.exe", "pdfxedit.exe", "pdfxcview.exe", "chrome.exe", "msedge.exe", "firefox.exe"))


class ClipboardCaptureError(Exception):
    pass


class KeyboardInput(ctypes.Structure):
    _fields_ = [("virtual_key", wintypes.WORD), ("scan", wintypes.WORD), ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]


class MouseInput(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("data", wintypes.DWORD), ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]


class InputPayload(ctypes.Union):
    _fields_ = [("keyboard", KeyboardInput), ("mouse", MouseInput)]


class Input(ctypes.Structure):
    _fields_ = [("kind", wintypes.DWORD), ("payload", InputPayload)]


def document_application(hwnd):
    process_id = win32process.GetWindowThreadProcessId(hwnd)[1]
    handle = win32api.OpenProcess(0x1000, False, process_id)
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buffer))
        query = ctypes.windll.kernel32.QueryFullProcessImageNameW
        query.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        query.restype = wintypes.BOOL
        return bool(query(int(handle), 0, buffer, ctypes.byref(size))) and PureWindowsPath(buffer.value).name.lower() in DOCUMENT_APPS
    finally:
        win32api.CloseHandle(handle)


def send_copy():
    send = ctypes.windll.user32.SendInput
    send.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
    send.restype = wintypes.UINT
    events = (Input * 4)(*[Input(1, InputPayload(keyboard=KeyboardInput(key, 0, flags, 0, 0))) for key, flags in ((win32con.VK_CONTROL, 0), (ord("C"), 0), (ord("C"), 2), (win32con.VK_CONTROL, 2))])
    if send(len(events), events, ctypes.sizeof(Input)) != len(events):
        release = (Input * 2)(*events[2:])
        send(len(release), release, ctypes.sizeof(Input))
        raise ClipboardCaptureError("无法发送复制操作；请重新选择文本后按 Alt+Q 重试。")


def snapshot(owner):
    win32clipboard.OpenClipboard(owner)
    try:
        saved, size, clipboard_format = [], 0, 0
        while True:
            clipboard_format = win32clipboard.EnumClipboardFormats(clipboard_format)
            if not clipboard_format:
                break
            if len(saved) >= 32:
                raise ClipboardCaptureError("剪贴板格式较复杂，已跳过自动复制以保护原内容。请重新选择文本后按 Alt+Q 重试。")
            if clipboard_format >= 0xC000:
                name = win32clipboard.GetClipboardFormatName(clipboard_format)
                if name in ("DataObject", "Ole Private Data") and win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                    continue
                if name in ("DataObject", "Ole Private Data", "Link Source", "Embed Source", "Embedded Object"):
                    raise ClipboardCaptureError("剪贴板包含应用对象，已跳过自动复制。请重新选择文本后按 Alt+Q 重试。")
            data = win32clipboard.GetClipboardData(clipboard_format)
            if not isinstance(data, (str, bytes)):
                raise ClipboardCaptureError("剪贴板包含无法安全还原的内容，已跳过自动复制。请重新选择文本后按 Alt+Q 重试。")
            size += len(data.encode("utf-16-le")) if isinstance(data, str) else len(data)
            if size > 2_000_000:
                raise ClipboardCaptureError("剪贴板内容较大，已跳过自动复制。请重新选择文本后按 Alt+Q 重试。")
            saved.append((clipboard_format, data))
        return win32clipboard.GetClipboardSequenceNumber(), saved
    finally:
        win32clipboard.CloseClipboard()


def read_change(owner, before, processes):
    win32clipboard.OpenClipboard(owner)
    try:
        sequence = win32clipboard.GetClipboardSequenceNumber()
        if sequence == before:
            return None
        source = win32clipboard.GetClipboardOwner()
        if not source or win32process.GetWindowThreadProcessId(source)[1] not in processes:
            raise ClipboardCaptureError("剪贴板被其他程序更新，本次未读取或覆盖其内容。")
        try:
            text = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT) if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT) else ""
        except pywintypes.error:
            text = ""
        return win32clipboard.GetClipboardSequenceNumber(), text
    finally:
        win32clipboard.CloseClipboard()


def restore(owner, sequence, saved):
    deadline = time.monotonic() + .25
    while True:
        try:
            win32clipboard.OpenClipboard(owner)
            break
        except Exception:
            if time.monotonic() >= deadline:
                raise ClipboardCaptureError("剪贴板被占用，未能还原原内容；当前可能仍保留刚才复制的选区。")
            time.sleep(.01)
    try:
        if win32clipboard.GetClipboardSequenceNumber() != sequence:
            return
        win32clipboard.EmptyClipboard()
        for clipboard_format, data in saved:
            win32clipboard.SetClipboardData(clipboard_format, data)
    finally:
        win32clipboard.CloseClipboard()


def copy_selection(hwnd, point, max_chars, processes, is_current):
    if not is_current() or not document_application(hwnd):
        return None
    deadline = time.monotonic() + .35
    modifiers = (win32con.VK_CONTROL, win32con.VK_MENU, win32con.VK_SHIFT, win32con.VK_LWIN, win32con.VK_RWIN, win32con.VK_LBUTTON, win32con.VK_RBUTTON, ord("C"))
    while any(win32api.GetAsyncKeyState(key) & 0x8000 for key in modifiers):
        if not is_current() or time.monotonic() >= deadline:
            return None
        time.sleep(.01)
    owner = win32gui.CreateWindowEx(0, "STATIC", "TILClipboard", 0, 0, 0, 0, 0, win32con.HWND_MESSAGE, 0, 0, None)
    copied_sequence = None
    try:
        before, saved = snapshot(owner)
        if not is_current() or win32clipboard.GetClipboardSequenceNumber() != before:
            return None
        send_copy()
        deadline = time.monotonic() + .7
        while time.monotonic() < deadline:
            try:
                changed = read_change(owner, before, processes)
            except pywintypes.error:
                time.sleep(.01)
                continue
            if changed:
                copied_sequence, text = changed
                text = text.strip() if isinstance(text, str) else ""
                if not text:
                    time.sleep(.01)
                    continue
                if is_current() and 2 <= len(text) <= max_chars:
                    return text, (point[0], point[1], point[0] + 1, point[1] + 18)
                return None
            time.sleep(.01)
        raise ClipboardCaptureError("阅读器未及时提供复制结果；扫描件需要先 OCR，限制复制的 PDF 无法直接读取。请重新选择文本后按 Alt+Q 重试。")
    finally:
        try:
            if copied_sequence is not None:
                restore(owner, copied_sequence, saved)
        finally:
            win32gui.DestroyWindow(owner)
