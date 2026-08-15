# -*- coding: utf-8 -*-
"""豆包键鼠自动化 —— 模拟鼠标键盘操作豆包桌面客户端批量提炼知识笔记。

对齐兵哥原版「批量素材提炼 A→B」逻辑，不依赖豆包 API：

  1. 用户依次用 F6 记录三个坐标：输入框 / 下翻箭头 / 复制按钮；
  2. 程序循环处理「未处理」素材：点击输入框 → 粘贴(提示词+素材) → Enter 发送
     → 等待 N 秒 → 点下翻箭头 → 点复制按钮 → 读取剪贴板 → 保存 B 层笔记
     → 素材移入「已处理」；
  3. 循环次数可由用户设定，可随时中断（界面「停止」按钮或按 Esc）。

技术实现：ctypes 调用 user32（SetCursorPos / mouse_event / keybd_event /
剪贴板），零第三方依赖；仅支持 Windows。
"""
import datetime
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

if sys.platform != "win32":
    raise ImportError("豆包键鼠自动化仅支持 Windows")

import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
advapi32 = ctypes.windll.advapi32

# 显式声明关键 Win32 函数签名，避免 ctypes 在 64 位下把指针/句柄误当 int 推断
# 重要：返回 HANDLE/指针的函数（GlobalAlloc/GlobalLock/GlobalSize/OpenClipboard 等）
# 若不声明 restype，ctypes 默认按 32 位 int 截断 64 位返回值，导致 memmove/string_at
# 写入非法地址 → "access violation writing 0x..." 崩溃（豆包整理第一步写剪贴板即炸）
user32.SetCursorPos.argtypes = (wintypes.INT, wintypes.INT)
user32.SetCursorPos.restype = wintypes.BOOL
user32.mouse_event.argtypes = (wintypes.DWORD, wintypes.DWORD,
                               wintypes.DWORD, wintypes.DWORD, wintypes.LPARAM)
user32.mouse_event.restype = None
user32.keybd_event.argtypes = (wintypes.BYTE, wintypes.BYTE,
                               wintypes.DWORD, wintypes.LPARAM)
user32.keybd_event.restype = None
user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
user32.GetCursorPos.restype = wintypes.BOOL

# 剪贴板：返回/参数为 HANDLE 的函数必须声明 64 位，否则返回值被截断 → 崩溃
kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
kernel32.GlobalAlloc.restype = wintypes.HANDLE
kernel32.GlobalLock.argtypes = (wintypes.HANDLE,)
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = (wintypes.HANDLE,)
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalSize.argtypes = (wintypes.HANDLE,)
kernel32.GlobalSize.restype = ctypes.c_size_t
user32.OpenClipboard.argtypes = (wintypes.HWND,)
user32.OpenClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.restype = wintypes.BOOL
user32.CloseClipboard.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = (wintypes.UINT,)
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
user32.SetClipboardData.restype = wintypes.HANDLE

# 窗口/进程/令牌：返回 HWND/HANDLE/DWORD 的函数也固定签名，防 64 位截断
user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.ShowWindow.argtypes = (wintypes.HWND, wintypes.INT)
user32.ShowWindow.restype = wintypes.BOOL
user32.BringWindowToTop.argtypes = (wintypes.HWND,)
user32.BringWindowToTop.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = (wintypes.HWND,)
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
user32.GetWindowTextLengthW.restype = wintypes.INT
user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, wintypes.INT)
user32.GetWindowTextW.restype = wintypes.INT
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND,
                                            ctypes.POINTER(wintypes.DWORD))
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.EnumWindows.argtypes = (ctypes.WINFUNCTYPE(wintypes.BOOL,
                                                    wintypes.HWND, wintypes.LPARAM),
                               wintypes.LPARAM)
user32.EnumWindows.restype = wintypes.BOOL
kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD,
                                                wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD))
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
advapi32.OpenProcessToken.argtypes = (wintypes.HANDLE, wintypes.DWORD,
                                      ctypes.POINTER(wintypes.HANDLE))
advapi32.OpenProcessToken.restype = wintypes.BOOL
advapi32.GetTokenInformation.argtypes = (wintypes.HANDLE, wintypes.DWORD,
                                         wintypes.LPVOID, wintypes.DWORD,
                                         ctypes.POINTER(wintypes.DWORD))
advapi32.GetTokenInformation.restype = wintypes.BOOL

# ---- 常量 ----
VK_F6 = 0x75
VK_ESCAPE = 0x1B
VK_RETURN = 0x0D
VK_CONTROL = 0x11
VK_V = 0x56
VK_MENU = 0x12  # Alt

KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
CF_UNICODETEXT = 13
CF_HDROP = 15
GMEM_MOVEABLE = 0x0002
SW_MAXIMIZE = 3
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TokenElevation = 20

FILE_UPLOAD_WAIT = 3          # 文件粘贴后等待豆包上传解析的秒数（2~4 秒可取中值）
FILE_MAX_MB = 50              # 文件直发大小上限（超过则跳过并计入失败，防豆包拒收/长等）

COORD_NAMES = ["输入框", "下翻箭头", "复制按钮"]


# ---------------------------------------------------------------------------
# 剪贴板（ctypes，读写 Unicode 文本）
# ---------------------------------------------------------------------------
def clipboard_set_text(text: str) -> bool:
    data = text.encode("utf-16-le") + b"\x00\x00"
    if not user32.OpenClipboard(None):
        return False
    try:
        user32.EmptyClipboard()
        h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not h_mem:
            return False
        ptr = kernel32.GlobalLock(h_mem)
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(h_mem)
        user32.SetClipboardData(CF_UNICODETEXT, h_mem)
        return True
    finally:
        user32.CloseClipboard()


class _DROPFILES(ctypes.Structure):
    """CF_HDROP 头结构。内存布局（x86/x64 均为 20 字节）：
    offset 0  pFiles  DWORD 文件列表相对本结构起点的偏移
    offset 4  pt      POINT 投放点（用不上，全 0）
    offset 12 fNC     BOOL  非客户区标志（0）
    offset 16 fWide   BOOL  1=UTF-16 路径
    offset 20 ── 文件列表从这里开始 ──
    """
    _fields_ = [
        ("pFiles", wintypes.DWORD),
        ("pt", wintypes.POINT),
        ("fNC", wintypes.BOOL),
        ("fWide", wintypes.BOOL),
    ]


def clipboard_set_files(paths: List[str]) -> bool:
    """把文件列表写入剪贴板（CF_HDROP，UTF-16），供输入框 Ctrl+V 直接粘贴附件。

    布局：DROPFILES 头(20B) + 各绝对路径以 \\0 分隔 + 末尾 \\0\\0 双 null 结束。
    与 clipboard_set_text 互斥：每次粘贴前都会重置剪贴板内容。
    """
    if not paths:
        return False
    files = [os.path.abspath(p) for p in paths]
    df = _DROPFILES()
    df.pFiles = ctypes.sizeof(_DROPFILES)          # 20：文件列表起始偏移
    df.fWide = 1                                    # UTF-16 路径
    header = ctypes.string_at(ctypes.addressof(df), ctypes.sizeof(df))
    payload = ("\0".join(files) + "\0\0").encode("utf-16-le")
    data = header + payload
    if not user32.OpenClipboard(None):
        return False
    try:
        user32.EmptyClipboard()
        h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not h_mem:
            return False
        ptr = kernel32.GlobalLock(h_mem)
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(h_mem)
        user32.SetClipboardData(CF_HDROP, h_mem)
        return True
    finally:
        user32.CloseClipboard()


def clipboard_get_text() -> str:
    if not user32.OpenClipboard(None):
        return ""
    try:
        h_mem = user32.GetClipboardData(CF_UNICODETEXT)
        if not h_mem:
            return ""
        ptr = kernel32.GlobalLock(h_mem)
        try:
            size = kernel32.GlobalSize(h_mem)
            raw = ctypes.string_at(ptr, size)
            return raw.decode("utf-16-le", errors="replace").rstrip("\x00")
        finally:
            kernel32.GlobalUnlock(h_mem)
    finally:
        user32.CloseClipboard()


# ---------------------------------------------------------------------------
# 键鼠模拟
# ---------------------------------------------------------------------------
def _move_to(x: int, y: int) -> None:
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.15)


def _click() -> None:
    """发送左键按下+抬起（mouse_event，稳定且不依赖手搓结构体）。

    注意：mouse_event 作用于当前光标位置，因此调用前务必先 SetCursorPos。
    """
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.08)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    time.sleep(0.08)


def _key(vk: int) -> None:
    user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.04)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.08)


def _paste_clipboard() -> None:
    """Ctrl+V 粘贴。"""
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    _key(VK_V)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.4)


def press_escape() -> None:
    _key(VK_ESCAPE)


# ---------------------------------------------------------------------------
# 豆包窗口定位 / 置前 / 权限诊断
# ---------------------------------------------------------------------------
def _process_name(pid: int) -> str:
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(260)
        size = wintypes.DWORD(260)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
    except Exception:
        pass
    finally:
        kernel32.CloseHandle(h)
    return ""


def _is_process_elevated(pid: int):
    """检测进程是否以管理员(提升)权限运行；无法判断返回 None。"""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return None
    try:
        h_token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(h, TOKEN_QUERY, ctypes.byref(h_token)):
            return None
        try:
            elevation = wintypes.DWORD()
            size = wintypes.DWORD(4)
            ok = advapi32.GetTokenInformation(h_token, TokenElevation,
                                              ctypes.byref(elevation), 4,
                                              ctypes.byref(size))
            return bool(elevation.value) if ok else None
        finally:
            kernel32.CloseHandle(h_token)
    except Exception:
        return None
    finally:
        kernel32.CloseHandle(h)


def find_doubao_windows():
    """枚举顶层窗口，找出豆包客户端窗口（标题含'豆包'或进程为 doubao/bytebot）。"""
    results = []
    PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        name = _process_name(pid.value)
        if ("豆包" in title or "doubao" in title.lower()
                or "doubao" in name or "bytebot" in name
                or "豆包" in name):
            results.append({"hwnd": hwnd, "title": title,
                            "process": name, "pid": pid.value})
        return True

    user32.EnumWindows(PROC(_cb), 0)
    return results


def bring_doubao_to_front(log_fn=None, maximize: bool = True) -> bool:
    """把豆包客户端窗口切到前台并最大化；找不到返回 False。"""
    def _log(msg):
        if log_fn:
            log_fn(msg)

    wins = find_doubao_windows()
    if not wins:
        _log("未找到豆包窗口（标题含「豆包」或进程 doubao/bytebot）。"
             "请先打开豆包电脑客户端")
        return False
    win = wins[0]
    hwnd = win["hwnd"]
    _log("找到豆包窗口：%s（进程 %s）" % (win["title"], win["process"] or "未知"))

    # Alt 键技巧：绕过 Windows 前台切换限制
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.SetForegroundWindow(hwnd)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    if maximize:
        user32.ShowWindow(hwnd, SW_MAXIMIZE)
    user32.BringWindowToTop(hwnd)
    time.sleep(0.6)
    # 最大化后再置前一次，确保在最上层
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.SetForegroundWindow(hwnd)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    _log("豆包客户端已切到前台%s" % ("并最大化" if maximize else ""))
    return True


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def diagnostic(log_fn=None, dry_run: bool = False,
               coords: Optional[Dict[str, Dict[str, int]]] = None) -> Dict[str, Any]:
    """诊断环境：权限、豆包窗口、鼠标模拟（按导入坐标执行）。返回结果字典。"""
    def _log(msg):
        if log_fn:
            log_fn(msg)

    info: Dict[str, Any] = {"admin": is_admin(), "doubao_windows": [],
                            "mouse_ok": None, "has_coords": False}
    _log("===== 豆包自动化诊断 =====")
    _log("本程序是否管理员运行：%s" % ("是" if info["admin"] else "否（普通权限）"))

    wins = find_doubao_windows()
    if wins:
        info["doubao_windows"] = [{"title": w["title"], "process": w["process"]}
                                  for w in wins]
        for w in wins:
            elev = _is_process_elevated(w["pid"])
            info["doubao_elevated"] = elev
            _log("豆包窗口：%s（进程 %s，管理员运行：%s）" % (
                w["title"], w["process"] or "未知",
                "是" if elev else ("否" if elev is False else "无法判断")))
        if info["admin"] is False and info.get("doubao_elevated") is True:
            _log("⚠ 重要：豆包以管理员运行、本程序以普通权限运行，"
                 "Windows 会拦截鼠标键盘模拟（UIPI）。"
                 "解决办法：请以相同方式运行——都勾选「以管理员身份运行」，或都不勾。")
    else:
        _log("未找到豆包窗口，请先打开豆包电脑客户端")

    # 坐标检查：测试按导入的坐标执行
    if not coords or "输入框" not in coords:
        _log("❌ 无坐标文件：请先「记录」三个坐标或「导入」豆包坐标文件")
        info["has_coords"] = False
        return info
    info["has_coords"] = True
    ib = coords["输入框"]
    _log("已加载豆包坐标：输入框(%s,%s) 下翻(%s,%s) 复制(%s,%s)" % (
        ib["x"], ib["y"],
        coords.get("下翻箭头", {}).get("x", "-"), coords.get("下翻箭头", {}).get("y", "-"),
        coords.get("复制按钮", {}).get("x", "-"), coords.get("复制按钮", {}).get("y", "-")))

    if dry_run:
        _log("（dry-run 模式：未实际移动鼠标）")
        return info

    # 按坐标执行一次真实点击测试
    user32.SetCursorPos(int(ib["x"]), int(ib["y"]))
    time.sleep(0.3)
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    _log("鼠标已移动到输入框坐标 (%d, %d)" % (int(pt.x), int(pt.y)))
    info["mouse_ok"] = (int(pt.x), int(pt.y)) == (int(ib["x"]), int(ib["y"]))
    _click()
    _log("已发送一次左键点击。请观察豆包输入框是否被点中（光标闪烁）。")
    _log("===== 诊断结束 =====")
    return info


# ---------------------------------------------------------------------------
# 坐标采集：鼠标移到目标位置按 F6 记录
# ---------------------------------------------------------------------------
def record_coordinate(target_name: str, log_fn=None,
                      stop_event: Optional[Any] = None) -> Tuple[int, int]:
    """进入监听模式：轮询鼠标位置，检测 F6 按下即记录当前坐标并返回。

    返回 (x, y)；用户按 Esc 或 stop_event 触发时返回 (0, 0) 表示取消。
    """
    def _log(msg):
        if log_fn:
            log_fn(msg)

    _log("请把鼠标移到豆包【%s】位置，然后按 F6 确认（按 Esc 取消）" % target_name)
    while True:
        if stop_event and stop_event.is_set():
            return 0, 0
        if user32.GetAsyncKeyState(VK_F6) & 0x8000:
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            _log("已记录【%s】坐标：(%d, %d)" % (target_name, pt.x, pt.y))
            time.sleep(0.6)  # 防重复触发
            return int(pt.x), int(pt.y)
        if user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000:
            _log("已取消记录【%s】" % target_name)
            time.sleep(0.6)
            return 0, 0
        time.sleep(0.05)


def record_all_coordinates(log_fn=None, stop_event: Optional[Any] = None) -> Dict[str, Dict[str, int]]:
    """依次记录三个坐标，返回 {'输入框':{'x':..,'y':..}, ...}；任一步取消则返回 {}。"""
    coords: Dict[str, Dict[str, int]] = {}
    for name in COORD_NAMES:
        x, y = record_coordinate(name, log_fn=log_fn, stop_event=stop_event)
        if x == 0 and y == 0:
            return {}
        coords[name] = {"x": x, "y": y}
    return coords


# ---------------------------------------------------------------------------
# 批量提炼循环
# ---------------------------------------------------------------------------
DEFAULT_PROMPT = """# 豆包提示词 — 知识提炼助手

## 角色

你是一位知识管家，负责将原始素材提炼为标准知识笔记。输出格式精确，不做多余解释。

## 输出格式

严格按以下 markdown 结构输出：

---
type: 笔记
分类: 技术原理， 商业策略
原始链接: （出处URL或说明）
作者: （原作者，未知则留空）
---

# （4～20字的一句话总结）

**关键词**：（8～18个关键词，逗号分隔）

**摘要**：（浓缩核心内容，不超过120字）

## 详细内容

（用自己的话重新组织原始素材的核心知识点）

## 逻辑树（可选）

- 主论点
  - 支撑论据

## 要求

1. **分类**：从素材内容自行归纳，最多6个。例如：技术原理、商业策略、学习方法、系统设计、心理学等
2. **关键词**：8～18个，逗号分隔
3. **摘要**：不超过120字
4. **详细内容**：**不少于600字**
5. **逻辑树**：可选
6. **语言**：全部中文
7. **简洁**：只输出笔记本身，无解释无问候。**不要用 ``` 代码块包围**

原始素材："""


def _iter_material_sources(cfg: Dict[str, Any], vault_root: str) -> List[str]:
    """返回豆包提炼要扫描的素材目录（统一入口「未处理」，与 CLI sync 同源）。

    合并前的「收件箱 / 未收录 / 原始素材/未处理」现已统一为「未处理」，
    故豆包只扫描 import.inbox（默认 未处理）一处，避免放错目录识别不到。
    """
    import_cfg = cfg.get("import", {})
    inbox = os.path.abspath(os.path.join(vault_root, import_cfg.get("inbox", "未处理")))
    return [inbox]


def _list_materials(sources: List[str]) -> List[Tuple[str, str]]:
    """列出多个素材目录下的所有非隐藏素材文件（.md/.txt 走文本，其余整文件直发豆包），
    返回 [(绝对路径, 文件名), ...]（按文件名排序）。"""
    out: List[Tuple[str, str]] = []
    for d in sources:
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            p = os.path.join(d, f)
            if os.path.isfile(p) and not f.startswith("."):
                out.append((p, f))
    out.sort(key=lambda x: x[1])
    return out


def _read_material(src: str) -> Tuple[str, Any]:
    """读取素材，返回 (kind, payload)：
    - .md/.txt → ("text", 文本内容)；读取失败抛 ValueError（编码探测失败/文件不可读）。
    - 其他非隐藏文件 → ("file", 绝对路径)：不提取文字，整文件交给豆包解析。
    """
    ext = os.path.splitext(src)[1].lower()
    if ext in (".md", ".txt"):
        from . import frontmatter
        return "text", frontmatter.read_text_auto(src)
    return "file", os.path.abspath(src)


def build_prompt(material_text: str, vault_root: str, cfg: Dict[str, Any],
                 send_format: Optional[str] = None) -> str:
    """构造发送给豆包的完整提示词。

    模板来源优先级：GUI 配置 send_format → D 层「豆包知识提炼提示词.md」→ 内置默认。
    素材正文插到占位符 {素材内容} 处；无占位符则追加到末尾。
    """
    material = material_text.strip()
    if send_format and str(send_format).strip():
        template = str(send_format)
    else:
        structure = cfg.get("structure", {})
        tpl_path = os.path.join(vault_root, structure.get("D_规则模板", "规则模板"),
                                "豆包知识提炼提示词.md")
        template = None
        if os.path.isfile(tpl_path):
            try:
                with open(tpl_path, "r", encoding="utf-8-sig") as f:
                    template = f.read()
            except Exception:
                template = None
        if template is None:
            template = DEFAULT_PROMPT
    # 素材占位：先兜底删除旧模板「原始素材：」占位行，再统一替换 {素材内容}
    template = re.sub(r"^.*原始素材.*[:：].*$", "", template, flags=re.M)
    if "{素材内容}" in template:
        prompt = template.replace("{素材内容}", material)
    else:
        prompt = template.rstrip() + "\n\n" + material
    return prompt


def parse_refined_note(reply: str) -> Tuple[Optional[str], str]:
    """解析豆包回复：去掉代码块包裹，返回 (一句话总结, 笔记正文)。"""
    text = reply.strip()
    # 去掉 ```markdown ... ``` 包裹
    m = re.search(r"```(?:markdown)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    # 提取 # 开头的标题作为一句话总结
    title = None
    m2 = re.search(r"^#\s+(.+)$", text, re.M)
    if m2:
        title = m2.group(1).strip()
    # 标题清洗（去掉 markdown 装饰）
    if title:
        title = re.sub(r"[#*`]", "", title).strip()
        title = re.sub(r"\s+", " ", title)[:40]
    return title, text


def make_note_name(summary: Optional[str], material_stem: str, cfg: Dict[str, Any]) -> str:
    """B 层文件名：一句话总结_YYYYMMDDHHMMSS.md（时间戳取自素材名，无则取当前时间）。"""
    m = re.search(r"(\d{14})", material_stem)
    if m:
        stamp = m.group(1)
    else:
        stamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    if summary:
        summary = re.sub(r"[\\/:*?\"<>|\s]+", "", summary)[:24] or "知识笔记"
    else:
        summary = "知识笔记"
    return "%s_%s.md" % (summary, stamp)


def refine_loop(cfg: Dict[str, Any], vault_root: str, coords: Dict[str, Dict[str, int]],
                registry: Any, logger: Any, report: Any,
                wait_seconds: int = 30, max_items: Optional[int] = None,
                stop_event: Optional[Any] = None,
                on_item: Optional[Any] = None,
                send_format: Optional[str] = None) -> None:
    """批量提炼主循环。stop_event 置位或用户按 Esc 即中断。"""
    if not coords or not all(k in coords for k in COORD_NAMES):
        raise ValueError("坐标不完整，请先依次记录输入框/下翻箭头/复制按钮坐标")
    if sys.platform != "win32":
        raise RuntimeError("豆包键鼠自动化仅支持 Windows")

    structure = cfg.get("structure", {})
    b_dir = os.path.join(vault_root, structure.get("B_知识提炼", "03知识提炼"))
    done_dir = os.path.join(vault_root, structure.get("已处理", "已处理"))
    os.makedirs(b_dir, exist_ok=True)
    os.makedirs(done_dir, exist_ok=True)

    # 素材来源：未处理（统一入口，与 CLI sync 同源）
    sources = _iter_material_sources(cfg, vault_root)
    for d in sources:
        os.makedirs(d, exist_ok=True)
    files = _list_materials(sources)  # [(绝对路径, 文件名), ...]
    if not files:
        logger.info("[豆包] 在以下目录均未找到可提炼素材（非隐藏文件），请先把要提炼的素材放进去：%s",
                    "、".join(sources))
        return
    if max_items:
        files = files[:max_items]

    logger.info("[豆包] 开始批量提炼：%d 个素材，等待 %d 秒/条（按 Esc 或点停止可中断）",
                len(files), wait_seconds)
    in_box = (coords["输入框"]["x"], coords["输入框"]["y"])
    scroll_pt = (coords["下翻箭头"]["x"], coords["下翻箭头"]["y"])
    copy_pt = (coords["复制按钮"]["x"], coords["复制按钮"]["y"])

    for idx, (src, fname) in enumerate(files, 1):
        if stop_event and stop_event.is_set():
            logger.info("[豆包] 已收到停止信号，中断于第 %d/%d 条", idx, len(files))
            break
        if user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000:
            logger.info("[豆包] 检测到 Esc，中断于第 %d/%d 条", idx, len(files))
            break
        if on_item:
            on_item(idx, len(files), fname)

        report.scanned += 1
        try:
            kind, payload = _read_material(src)
        except Exception as e:
            report.failed += 1
            report.errors.append("%s: %s" % (fname, e))
            logger.error("[豆包] 读取素材失败 %s: %s", fname, e)
            continue

        if kind == "text":
            prompt = build_prompt(payload, vault_root, cfg,
                                  send_format=send_format)
        else:
            # 文件直发：{素材内容} 占位符替换为文件名说明（无正文可填）
            file_desc = "素材文件：%s（请先读取上方粘贴的附件文件内容，再按模板格式提炼笔记）" % fname
            prompt = build_prompt(file_desc, vault_root, cfg,
                                  send_format=send_format)
            # 超大文件直接跳过，避免豆包拒收 + 浪费等待时间
            try:
                if os.path.getsize(src) > FILE_MAX_MB * 1024 * 1024:
                    report.failed += 1
                    report.errors.append("%s: 文件超过 %dMB 上限" % (fname, FILE_MAX_MB))
                    logger.error("[豆包] 跳过超大文件 %s（>%dMB）", fname, FILE_MAX_MB)
                    continue
            except OSError as e:
                report.failed += 1
                report.errors.append("%s: %s" % (fname, e))
                logger.error("[豆包] 读取素材失败 %s: %s", fname, e)
                continue

        logger.info("[豆包] (%d/%d) 正在提炼：%s（%s）", idx, len(files), fname,
                    "文本" if kind == "text" else "文件直发")
        ok = False
        for attempt in (1, 2):  # 失败自动重试一次
            try:
                _move_to(*in_box)
                _click()                      # 1. 点击输入框坐标
                time.sleep(0.5)               #    等待 0.5s
                if kind == "text":
                    clipboard_set_text(prompt)
                    _paste_clipboard()        # 2a. 粘贴文本
                    time.sleep(0.4)
                else:
                    if not clipboard_set_files([src]):
                        raise RuntimeError("文件剪贴板设置失败（剪贴板被占用或非 Windows）")
                    _paste_clipboard()        # 2b. ① 文件进输入框
                    logger.info("[豆包] 已粘贴文件，等待上传解析……")
                    if _wait_interruptible(FILE_UPLOAD_WAIT, stop_event):
                        logger.info("[豆包] 文件上传等待期间被中断")
                        return
                    clipboard_set_text(prompt)
                    _paste_clipboard()        # 2c. ② 提示词进输入框
                    time.sleep(0.4)
                _key(VK_RETURN)               # 3. 发送
                logger.info("[豆包] 已发送，轮询复制直到拿到新回复……")
                clipboard_set_text("")        # 清空剪贴板：避免残留提示词/旧内容误导「新内容」判定
                reply = _wait_new_reply(copy_pt, scroll_pt, "", stop_event,
                                        wait_seconds, logger)
                if reply is None:
                    logger.info("[豆包] 等待新回复期间被中断")
                    return
                if not reply or len(reply) < 30:
                    logger.warning("[豆包] 第 %d 次未获取到新回复（%d 字），重试……",
                                   attempt, len(reply or ""))
                    time.sleep(3)
                    continue
                ok = True
                break
            except Exception as e:
                logger.error("[豆包] 模拟操作异常：%s", e)
                time.sleep(2)

        if not ok:
            report.failed += 1
            report.errors.append("%s: 未获取到豆包回复" % fname)
            logger.error("[豆包] 失败：%s（未能获取回复）", fname)
            continue

        # 解析回复并保存 B 层笔记
        summary, note_body = parse_refined_note(reply)
        note_name = make_note_name(summary, os.path.splitext(fname)[0], cfg)
        note_path = os.path.join(b_dir, note_name)
        try:
            with open(note_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(note_body)
            # 源素材移入已处理
            dst = os.path.join(done_dir, fname)
            if os.path.exists(dst):
                stem, ext = os.path.splitext(fname)
                dst = os.path.join(done_dir, "%s_%s%s" % (
                    stem, datetime.datetime.now().strftime("%H%M%S"), ext))
            os.replace(src, dst)
            # 登记幂等
            import hashlib
            h = hashlib.sha256()
            with open(dst, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
            content_hash = h.hexdigest()
            registry.mark(content_hash,
                          os.path.relpath(note_path, vault_root).replace("\\", "/"),
                          os.path.relpath(dst, vault_root).replace("\\", "/"))
            report.imported += 1
            report.new_notes += 1
            report.add_detail("OK", "%s → 03知识提炼/%s" % (fname, note_name))
            logger.info("[豆包] 已保存笔记：%s", note_name)
        except Exception as e:
            report.failed += 1
            report.errors.append("%s: 保存失败 %s" % (fname, e))
            logger.error("[豆包] 保存失败 %s: %s", fname, e)

    registry.save()
    logger.info("[豆包] 批量提炼结束：成功 %d，失败 %d", report.imported, report.failed)


def _wait_interruptible(seconds: int, stop_event: Optional[Any]) -> bool:
    """可中断的等待：返回 True 表示被中断。"""
    deadline = time.time() + max(1, seconds)
    while time.time() < deadline:
        if stop_event and stop_event.is_set():
            return True
        if user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000:
            return True
        time.sleep(0.2)
    return False


def _wait_new_reply(copy_pt, scroll_pt, old_reply, stop_event, wait_seconds, logger) -> Optional[str]:
    """发送后轮询复制，直到剪贴板出现非空新内容（= 豆包新回复）。

    调用方在发送后会先 clipboard_set_text("") 清空剪贴板，并把 old_reply 传 ""，
    因此判定「cur 非空」即视为复制到新回复（旧基准为空的特殊情况）。
    豆包生成中点「复制按钮」剪贴板不变（用户实测）；生成完点复制可复制到新回复。
    循环：点复制 → 读剪贴板 → 未复制到新内容则点下翻滚到最新 → 等 1s → 再点复制。

    返回 None=被中断（调用方应 return 退出循环）；
    否则返回复制到的内容（新内容，或超时兜底的当前内容）。
    """
    timeout = max((wait_seconds or 30) * 3, 180)
    deadline = time.time() + timeout
    while True:
        if stop_event and stop_event.is_set():
            return None
        if user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000:
            return None
        _move_to(*copy_pt)
        _click()                      # 点复制按钮
        time.sleep(0.5)
        cur = clipboard_get_text().strip()
        if cur and cur != old_reply:
            logger.info("[豆包] 已复制到新回复（%d 字）", len(cur))
            return cur
        if time.time() > deadline:
            logger.warning("[豆包] 等待新回复超时（%.0fs），按当前内容继续", timeout)
            return cur
        _move_to(*scroll_pt)
        _click()                      # 下翻滚到最新回复
        time.sleep(1.0)
