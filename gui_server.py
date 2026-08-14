# -*- coding: utf-8 -*-
"""知识库助手 —— 本地服务端（配合 gui_index.html 使用）。

启动方式：双击「launch-kb-assistant.bat」（自动启动本服务并打开浏览器），
或手动执行：python gui_server.py

功能接口：
  GET  /                    界面页面
  GET  /api/status          状态（库路径/忙碌/定时/最近结果）
  GET  /api/logs?after=N    增量运行日志
  GET  /api/report          最近处理报告摘要
  GET  /api/inbox           未处理文件列表
  POST /api/set_root        设置知识库位置
  POST /api/init            创建知识库
  POST /api/sync            一键同步
  POST /api/upload          上传资料到未处理（base64 JSON）
  POST /api/inbox/delete    删除未处理文件（移入 _kb_回收站，可找回）
  POST /api/schedule        自动同步开关
  POST /api/open            打开知识库文件夹
  POST /api/open_path       打开指定路径（第一步「知识库放在哪里」用）
  POST /api/open_report     打开处理报告
  POST /api/exit            退出服务
"""
import base64
import datetime
import itertools
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUN_PY = os.path.join(BASE_DIR, "run.py")
INDEX_FILE = os.path.join(BASE_DIR, "gui_index.html")
DEFAULT_CONFIG = os.path.join(BASE_DIR, "kbconfig.yaml")
USER_CONFIG = os.path.join(BASE_DIR, "kbconfig_user.yaml")
STATE_FILE = os.path.join(BASE_DIR, "gui_state.json")
DEFAULT_COORD_FILE = "豆包坐标.json"
STARTUP_LOG = os.path.join(BASE_DIR, "gui_startup.log")
URL_FILE = os.path.join(BASE_DIR, "gui_url.txt")
PORT = 8765
PORT_TRIES = 10

# 可自定义的存放位置（值 = 相对库根的目录名/子路径，空 = 用默认）
DIR_KEYS = {"笔记": "B_知识提炼", "报告": "logging.log_dir",
            "附件": "附件"}

# 子进程统一用 python.exe（pythonw 下无法可靠读管道输出）
PYTHON = sys.executable
if os.name == "nt" and os.path.basename(PYTHON).lower().startswith("pythonw"):
    PYTHON = PYTHON[:-1] + "e"   # pythonw.exe -> python.exe

# ---------------------------------------------------------------------------
# 全局状态
# ---------------------------------------------------------------------------
_lock = threading.Lock()                 # 操作串行锁
_state = {
    "root": "",                          # 知识库位置
    "schedule_enabled": False,           # 自动同步开关
    "schedule_minutes": 30,              # 自动同步间隔（分钟）
    "dirs": {"笔记": "", "报告": "", "附件": ""},  # 自定义存放位置
    "coord_file": DEFAULT_COORD_FILE,    # 当前豆包坐标文件名（可自定义，支持多套）
}
_log_seq = itertools.count(1)
_log_lines = deque(maxlen=4000)          # 界面日志缓冲
_busy = {"flag": False, "action": ""}    # 是否有任务在运行
_exiting = {"flag": False}
_coords = {}                             # 豆包坐标 {'输入框':{'x','y'},...}
_coord_waiting = {"which": ""}           # 正在等待用户记录哪个坐标
_doubao_stop = threading.Event()         # 豆包整理停止信号
_doubao_running = {"flag": False}
_coord_cancel = threading.Event()        # 坐标记录取消信号


class _GuiLogHandler(logging.Handler):
    """把子线程里的日志转发到界面日志缓冲。"""

    def emit(self, record):
        try:
            _log(self.format(record))
        except Exception:
            pass


def startup_log(text):
    try:
        with open(STARTUP_LOG, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (datetime.datetime.now().strftime("%H:%M:%S"), text))
    except Exception:
        pass


def _log(text):
    seq = next(_log_seq)
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    _log_lines.append((seq, "[%s] %s" % (ts, text)))


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        if isinstance(data, dict):
            _state.update(data)
    except Exception:
        pass


def save_state():
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(_state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        _log("保存设置失败：%s" % e)


def _norm_root(root):
    root = (root or "").strip().strip('"').strip("'").strip()
    return os.path.abspath(root) if root else ""


def _cfg_path():
    """返回 run.py 使用的配置文件路径。若设置了自定义存放位置，生成覆盖配置。"""
    dirs = _state.get("dirs") or {}
    custom = {k: v for k, v in dirs.items() if v and str(v).strip()}
    if not custom:
        return DEFAULT_CONFIG
    overrides = {"structure": {}}
    for label, key in DIR_KEYS.items():
        val = (dirs.get(label) or "").strip()
        if not val:
            continue
        if key == "logging.log_dir":
            overrides.setdefault("logging", {})["log_dir"] = val
        else:
            overrides["structure"][key] = val
    try:
        with open(USER_CONFIG, "w", encoding="utf-8") as f:
            json.dump(overrides, f, ensure_ascii=False, indent=2)
        return USER_CONFIG
    except Exception:
        return DEFAULT_CONFIG


def _coord_path(name=None):
    """坐标文件绝对路径（文件名可自定义，仅允许文件名，防路径穿越）。"""
    name = (name or _state.get("coord_file") or DEFAULT_COORD_FILE).strip()
    name = os.path.basename(name) or DEFAULT_COORD_FILE
    if not name.lower().endswith(".json"):
        name += ".json"
    return os.path.join(BASE_DIR, name)


def _safe_coord_name(name):
    name = (name or "").strip()
    if not name:
        return ""
    name = os.path.basename(name)
    if name.lower().endswith(".json"):
        name = name[:-5]
    return name


def _load_coords(name=None):
    """加载坐标文件到内存；name 缺省用当前 coord_file。"""
    global _coords
    _coords = {}
    p = _coord_path(name)
    if os.path.isfile(p):
        try:
            with open(p, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            if isinstance(data, dict):
                _coords = {k: v for k, v in data.items()
                           if isinstance(v, dict) and "x" in v and "y" in v}
        except Exception:
            _coords = {}
    if name:
        _state["coord_file"] = os.path.basename(name) or DEFAULT_COORD_FILE
        save_state()
    return _coords


def _save_coords(name=None):
    """把当前内存坐标写入坐标文件；name 缺省用当前 coord_file。"""
    p = _coord_path(name)
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(_coords, f, ensure_ascii=False, indent=2)
    except Exception as e:
        _log("保存豆包坐标失败：%s" % e)
    if name:
        _state["coord_file"] = os.path.basename(name) or DEFAULT_COORD_FILE
        save_state()


def _list_coord_files():
    """列出程序目录下的坐标文件（*.json 且文件名含"坐标"，或为当前文件）。"""
    out = []
    current = _state.get("coord_file") or DEFAULT_COORD_FILE
    try:
        for fn in sorted(os.listdir(BASE_DIR)):
            if fn.lower().endswith(".json"):
                if "坐标" in fn or fn == current or fn == DEFAULT_COORD_FILE:
                    out.append(fn)
    except Exception:
        pass
    if current not in out:
        out.append(current)
    return out


def _run_py(args, on_line=None):
    """以子进程方式执行 run.py，逐行回调输出，返回退出码。"""
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    # 关键：Windows 下子进程 stdout 走管道时默认用 GBK(cp936) 编码，
    # 而本进程用 utf-8 读，会导致中文日志乱码。强制子进程以 UTF-8 输出。
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    cmd = [PYTHON, RUN_PY] + args
    _log("执行：python run.py %s" % " ".join(args))
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=flags, cwd=BASE_DIR, env=env)
    except Exception as e:
        _log("启动失败：%s" % e)
        return -1
    for raw in proc.stdout:
        line = raw.rstrip("\r\n")
        if line:
            _log(line)
            if on_line:
                on_line(line)
    proc.wait()
    return proc.returncode


def _task(name, args):
    """在后台线程执行任务，全程加锁。"""
    with _lock:
        if _busy["flag"] or _exiting["flag"]:
            _log("已有任务在运行，请稍候……")
            return
        _busy.update(flag=True, action=name)
    _log("———————— 开始：%s ————————" % name)

    def _worker():
        try:
            _run_py(args)
        finally:
            _busy.update(flag=False, action="")
            _log("———————— 完成：%s ————————" % name)

    threading.Thread(target=_worker, daemon=True).start()


def _sync_now():
    """定时触发的同步（与手动同步共用同一串行锁）。"""
    with _lock:
        if _busy["flag"] or _exiting["flag"]:
            return
        _busy.update(flag=True, action="自动同步")
    _log("———————— 开始：自动同步 ————————")
    try:
        _run_py(["sync", "--root", _state["root"], "--config", _cfg_path()])
    finally:
        _busy.update(flag=False, action="")
        _log("———————— 完成：自动同步 ————————")


def _scheduler_loop():
    """自动同步调度线程：每 10 秒检查一次是否需要执行。"""
    while not _exiting["flag"]:
        time.sleep(10)
        if _state["schedule_enabled"] and _state["root"]:
            interval = max(1, int(_state.get("schedule_minutes") or 30))
            now = time.time()
            last = getattr(_scheduler_loop, "_last", 0)
            if now - last >= interval * 60:
                _scheduler_loop._last = now
                threading.Thread(target=_sync_now, daemon=True).start()


def _latest_report_summary():
    """解析最新 处理报告.md 的统计表，返回 dict。"""
    if not _state["root"]:
        return {}
    path = os.path.join(_state["root"], "处理日志", "处理报告.md")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return {}
    summary = {}
    for line in text.splitlines():
        m = line.strip()
        if m.startswith("|") and "---" not in m:
            cells = [c.strip() for c in m.strip("|").split("|")]
            if len(cells) >= 2 and cells[0] and cells[0] != "指标":
                summary[cells[0]] = cells[1]
    return summary


def _import_source_dirs():
    """返回 [(来源标签, 绝对路径), ...]：统一为「未处理」单一来源。"""
    root = _state.get("root")
    if not root:
        return []
    try:
        from obsidian_kb import config as _cfgmod
        cfg = _cfgmod.load_config(_cfg_path(), cwd=BASE_DIR)
        inbox_rel = cfg["import"].get("inbox", "未处理")
    except Exception:
        inbox_rel = "未处理"
    return [("未处理", os.path.abspath(os.path.join(root, inbox_rel)))]


def _inbox_path():
    root = _state.get("root")
    if not root:
        return ""
    inbox_rel = "未处理"
    try:
        from obsidian_kb import config as _cfgmod
        cfg = _cfgmod.load_config(_cfg_path(), cwd=BASE_DIR)
        inbox_rel = cfg["import"].get("inbox", "未处理")
    except Exception:
        pass
    inbox = os.path.join(root, inbox_rel)
    if not os.path.isdir(inbox):
        os.makedirs(inbox, exist_ok=True)
    return inbox


def _inbox_list():
    root = _state.get("root")
    if not root:
        return []
    items = []
    try:
        for label, d in _import_source_dirs():
            if not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)
            for fn in sorted(os.listdir(d)):
                p = os.path.join(d, fn)
                if os.path.isfile(p):
                    st = os.stat(p)
                    items.append({
                        "name": fn,
                        "source": label,
                        "size": st.st_size,
                        "mtime": datetime.datetime.fromtimestamp(
                            st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    })
    except Exception:
        pass
    return items


def _inbox_breakdown():
    """按来源统计待同步文件数，如 {"未处理": 5}。"""
    bd: Dict[str, int] = {}
    try:
        for it in _inbox_list():
            bd[it.get("source", "未处理")] = bd.get(it.get("source", "未处理"), 0) + 1
    except Exception:
        pass
    return bd


def _doubao_materials():
    """豆包提炼实际会扫描的素材清单：统一为「未处理」（仅 .md/.txt）。

    与 doubao_automation._iter_material_sources 保持同源，供「自动匹配」按钮统计。
    """
    root = _state.get("root")
    if not root:
        return []
    items = []
    seen_paths = set()
    try:
        for label, d in _import_source_dirs():
            if not os.path.isdir(d):
                continue
            for fn in sorted(os.listdir(d)):
                p = os.path.join(d, fn)
                if os.path.isfile(p) and not fn.startswith(".") \
                        and fn.lower().endswith((".md", ".txt")):
                    ap = os.path.abspath(p)
                    if ap in seen_paths:
                        continue
                    seen_paths.add(ap)
                    st = os.stat(p)
                    items.append({
                        "name": fn,
                        "source": label,
                        "size": st.st_size,
                        "mtime": datetime.datetime.fromtimestamp(
                            st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    })
    except Exception:
        pass
    return items


def _inbox_delete(items):
    """把指定来源的文件移入 <库>/_kb_回收站/（可找回，不做物理删除）。

    入参 items 为 [{"name": ..., "source": ...}, ...]；source 缺省按「未处理」处理。
    """
    root = _state.get("root")
    if not root:
        return [], ["知识库未设置"]
    trash = os.path.join(root, "_kb_回收站")
    os.makedirs(trash, exist_ok=True)
    src_map = {label: path for label, path in _import_source_dirs()}
    moved, errors = [], []
    for it in (items or []):
        if isinstance(it, dict):
            label = it.get("source") or "未处理"
            name = str(it.get("name") or "")
        else:
            label, name = "未处理", str(it)
        base = os.path.basename(name)            # 防路径穿越：只取文件名
        if not base or base.startswith("."):
            errors.append("%s（非法文件名）" % base)
            continue
        base_dir = src_map.get(label) or _inbox_path()
        src = os.path.join(base_dir, base)
        if not os.path.isfile(src):
            errors.append("%s（不存在）" % base)
            continue
        try:
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            dst = os.path.join(trash, "%s_%s" % (stamp, base))
            i = 1
            while os.path.exists(dst):
                i += 1
                dst = os.path.join(trash, "%s_%s_%d" % (stamp, base, i))
            os.replace(src, dst)
            moved.append(base)
        except Exception as e:
            errors.append("%s（%s）" % (base, e))
    return moved, errors


# ---------------------------------------------------------------------------
# HTTP 处理
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "KBHelper/1.1"

    def log_message(self, fmt, *args):
        pass  # 静默访问日志

    # ---------- GET ----------
    def do_GET(self):
        raw_path = self.path
        path = raw_path.split("?")[0]
        if path in ("/", "/index.html"):
            self._send_file(INDEX_FILE)
        elif path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        elif path == "/api/status":
            self._send_json(self._status())
        elif path == "/api/logs":
            self._send_json(self._logs())
        elif path == "/api/report":
            self._send_json({"ok": True, "summary": _latest_report_summary()})
        elif path == "/api/inbox":
            self._send_json({"ok": True, "items": _inbox_list()})
        elif path == "/api/doubao/status":
            self._send_json({"ok": True, "coords": _coords,
                             "coord_file": _state.get("coord_file") or DEFAULT_COORD_FILE,
                             "coord_files": _list_coord_files(),
                             "coord_waiting": _coord_waiting.get("which", ""),
                             "running": _doubao_running["flag"]})
        elif path == "/api/doubao/materials":
            # 豆包实际会扫描的素材（统一为「未处理」），供「自动匹配」统计
            self._send_json({"ok": True, "items": _doubao_materials()})
        elif path == "/api/coord/files":
            self._send_json({"ok": True, "files": _list_coord_files(),
                             "current": _state.get("coord_file") or DEFAULT_COORD_FILE})
        elif path in ("/api/init", "/api/sync", "/api/open",
                      "/api/open_path", "/api/open_report", "/api/exit"):
            # 兼容旧页面用 GET 触发这些操作
            self._action(path)
        else:
            startup_log("404 GET " + raw_path)
            self._send_json({"ok": False, "error": "未知接口: GET " + raw_path},
                            code=404)

    def _status(self):
        root = _state["root"]
        return {
            "ok": True,
            "root": root,
            "busy": _busy["flag"],
            "busy_action": _busy["action"],
            "vault_exists": bool(root) and os.path.isdir(root),
            "schedule_enabled": _state["schedule_enabled"],
            "schedule_minutes": _state.get("schedule_minutes", 30),
            "inbox_count": len(_inbox_list()) if root else 0,
            "inbox_breakdown": _inbox_breakdown() if root else {},
            "dirs": _state.get("dirs") or {"笔记": "", "报告": "", "附件": ""},
            "coord_file": _state.get("coord_file") or DEFAULT_COORD_FILE,
            "coord_files": _list_coord_files(),
            "coords": _coords,
            "coord_waiting": _coord_waiting.get("which", ""),
            "doubao_running": _doubao_running["flag"],
            "summary": _latest_report_summary(),
        }

    def _logs(self):
        import urllib.parse
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        try:
            after = int(q.get("after", ["0"])[0])
        except ValueError:
            after = 0
        items = [{"id": s, "text": t} for s, t in _log_lines if s > after]
        last = _log_lines[-1][0] if _log_lines else after
        return {"ok": True, "items": items, "last": last}

    # ---------- POST ----------
    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            body = {}

        path = self.path
        if path in ("/api/init", "/api/sync", "/api/open",
                    "/api/open_path", "/api/open_report", "/api/exit"):
            self._action(path, body)
        elif path == "/api/dirs":
            dirs = body.get("dirs") or {}
            for label in DIR_KEYS:
                if label in dirs:
                    _state["dirs"][label] = str(dirs.get(label) or "").strip()
            save_state()
            _log("自定义存放位置已保存：%s" % json.dumps(
                _state["dirs"], ensure_ascii=False))
            self._send_json({"ok": True, "status": self._status()})

        elif path == "/api/coord/record":
            which = body.get("which", "")
            if which not in ("输入框", "下翻箭头", "复制按钮"):
                self._send_json({"ok": False, "error": "坐标名称无效"})
                return
            _coord_cancel.clear()
            _coord_waiting["which"] = which
            _log("请把鼠标移到豆包【%s】位置，按 F6 确认（按 Esc 取消）" % which)
            self._send_json({"ok": True, "status": self._status()})

            def _worker():
                try:
                    from obsidian_kb import doubao_automation
                except Exception as e:
                    _log("豆包自动化不可用：%s" % e)
                    _coord_waiting["which"] = ""
                    return
                pt = doubao_automation.record_coordinate(
                    which, log_fn=_log, stop_event=_coord_cancel)
                _coord_waiting["which"] = ""
                if pt != (0, 0):
                    _coords[which] = {"x": pt[0], "y": pt[1]}
                    _save_coords()
                    _log("豆包坐标已保存：%s(%d,%d)" % (which, pt[0], pt[1]))

            threading.Thread(target=_worker, daemon=True).start()

        elif path == "/api/coord/cancel":
            _coord_cancel.set()
            self._send_json({"ok": True})

        elif path == "/api/coord/clear":
            _coords.clear()
            _save_coords()
            _log("豆包坐标已清空（%s）" % _state.get("coord_file"))
            self._send_json({"ok": True, "status": self._status()})

        elif path == "/api/coord/set_file":
            name = _safe_coord_name(body.get("name", ""))
            if not name:
                self._send_json({"ok": False, "error": "请填写坐标文件名称"})
                return
            _load_coords(name + ".json")
            _log("已切换到坐标文件：%s.json（共 %d 个坐标）" % (
                name, len(_coords)))
            self._send_json({"ok": True, "status": self._status()})

        elif path == "/api/coord/export":
            name = _safe_coord_name(body.get("name", "")) or \
                _state.get("coord_file", DEFAULT_COORD_FILE)[:-5]
            if len(_coords) < 3:
                self._send_json({"ok": False,
                                 "error": "还没有完整的三个坐标，请先依次记录"})
                return
            _save_coords(name + ".json")
            _log("已生成坐标文件：%s.json（输入框/下翻/复制）" % name)
            self._send_json({"ok": True, "file": name + ".json",
                             "status": self._status()})

        elif path == "/api/coord/import":
            name = _safe_coord_name(body.get("name", ""))
            content = body.get("content", "")
            if not name:
                self._send_json({"ok": False, "error": "请填写坐标文件名称"})
                return
            try:
                data = json.loads(content)
            except Exception:
                self._send_json({"ok": False, "error": "文件内容不是有效的 JSON"})
                return
            if not isinstance(data, dict):
                self._send_json({"ok": False, "error": "坐标文件格式不正确"})
                return
            valid = {k: v for k, v in data.items()
                     if isinstance(v, dict) and "x" in v and "y" in v}
            if not valid:
                self._send_json({"ok": False,
                                 "error": "文件中没有有效坐标（需要 x/y 字段）"})
                return
            _coords.clear()
            _coords.update(valid)
            _save_coords(name + ".json")
            _log("已导入坐标文件：%s.json（%d 个坐标）" % (name, len(valid)))
            self._send_json({"ok": True, "file": name + ".json",
                             "status": self._status()})

        elif path == "/api/doubao/start":
            self._start_doubao(body)

        elif path == "/api/doubao/stop":
            _doubao_stop.set()
            _log("已请求停止豆包整理（当前条完成后或等待期间停止）")
            self._send_json({"ok": True})

        elif path == "/api/doubao/materials":
            # 豆包实际会扫描的素材（统一为「未处理」），供「自动匹配」统计
            self._send_json({"ok": True, "items": _doubao_materials()})

        elif path == "/api/doubao/test":
            dry = bool(body.get("dry"))
            _log("正在执行豆包自动化诊断……")
            self._send_json({"ok": True})

            def _diag():
                try:
                    from obsidian_kb import doubao_automation
                    doubao_automation.diagnostic(log_fn=_log, dry_run=dry,
                                                 coords=_coords)
                except Exception as e:
                    _log("诊断失败：%s" % e)

            threading.Thread(target=_diag, daemon=True).start()

        elif path == "/api/set_root":
            root = _norm_root(body.get("root", ""))
            if not root:
                self._send_json({"ok": False, "error": "请先填写知识库位置"})
                return
            _state["root"] = root
            save_state()
            _log("知识库位置已设置：%s" % root)
            self._send_json({"ok": True, "status": self._status()})

        elif path == "/api/upload":
            self._handle_upload(body)

        elif path == "/api/inbox/delete":
            moved, errors = _inbox_delete(body.get("items") or [])
            _log("删除未处理文件：成功 %d 个%s" % (
                len(moved),
                "（%s）" % "、".join(moved) if moved else ""))
            if errors:
                _log("删除失败：%s" % "；".join(errors))
            self._send_json({"ok": True, "moved": moved, "errors": errors,
                             "items": _inbox_list()})

        elif path == "/api/schedule":
            _state["schedule_enabled"] = bool(body.get("enabled"))
            try:
                _state["schedule_minutes"] = max(
                    1, min(int(body.get("minutes", 30)), 1440))
            except (TypeError, ValueError):
                _state["schedule_minutes"] = 30
            save_state()
            _log("自动同步已%s（间隔 %d 分钟）" % (
                "开启" if _state["schedule_enabled"] else "关闭",
                _state["schedule_minutes"]))
            self._send_json({"ok": True, "status": self._status()})

        else:
            startup_log("404 POST " + self.path)
            self._send_json({"ok": False, "error": "未知接口: POST " + self.path},
                            code=404)

    def _action(self, path, body=None):
        """无参数操作（init / sync / open / open_path / open_report / exit），GET 与 POST 通用。"""
        if path == "/api/init":
            if not _state["root"]:
                self._send_json({"ok": False, "error": "请先填写知识库位置"})
                return
            _task("创建知识库",
                  ["init", "--root", _state["root"], "--config", _cfg_path()])
            self._send_json({"ok": True, "status": self._status()})
        elif path == "/api/sync":
            if not _state["root"]:
                self._send_json({"ok": False, "error": "请先填写知识库位置"})
                return
            _task("一键同步",
                  ["sync", "--root", _state["root"], "--config", _cfg_path()])
            self._send_json({"ok": True, "status": self._status()})
        elif path == "/api/open":
            root = _state["root"]
            if root and os.path.isdir(root):
                if os.name == "nt":
                    os.startfile(root)  # noqa
                else:
                    subprocess.Popen(["xdg-open", root])
                self._send_json({"ok": True})
            else:
                self._send_json({"ok": False, "error": "知识库文件夹还不存在，请先创建知识库"})
        elif path == "/api/open_path":
            # 打开指定路径（第一步「知识库放在哪里」的「打开该路径」按钮）
            p = _norm_root((body or {}).get("path", "")) if body else ""
            target = p or _state.get("root") or ""
            if target and os.path.isdir(target):
                try:
                    if os.name == "nt":
                        os.startfile(target)  # noqa
                    else:
                        subprocess.Popen(["xdg-open", target])
                    self._send_json({"ok": True})
                except Exception as e:
                    self._send_json({"ok": False, "error": "打开失败：%s" % e})
            else:
                self._send_json({"ok": False,
                                 "error": "该路径还不存在，请先在文件管理器中创建，或先点「① 创建知识库」"})
        elif path == "/api/open_report":
            root = _state["root"]
            p = os.path.join(root, "处理日志", "处理报告.md")
            if os.path.isfile(p):
                if os.name == "nt":
                    os.startfile(p)  # noqa
                self._send_json({"ok": True})
            else:
                self._send_json({"ok": False, "error": "还没有处理报告，请先执行一键同步"})
        elif path == "/api/exit":
            _log("正在退出知识库助手……")
            _exiting["flag"] = True
            threading.Thread(target=lambda: (
                time.sleep(0.5), self.server.shutdown()), daemon=True).start()
            self._send_json({"ok": True})

    def _start_doubao(self, body):
        """启动豆包键鼠批量提炼（后台线程）。"""
        if not _state["root"]:
            self._send_json({"ok": False, "error": "请先填写知识库位置"})
            return
        missing = [k for k in ("输入框", "下翻箭头", "复制按钮") if k not in _coords]
        if missing:
            self._send_json({"ok": False,
                             "error": "请先记录豆包坐标：%s" % "、".join(missing)})
            return
        with _lock:
            if _busy["flag"]:
                self._send_json({"ok": False, "error": "已有任务在运行，请稍候"})
                return
            _busy.update(flag=True, action="豆包整理中")
        _doubao_running["flag"] = True
        _doubao_stop.clear()

        try:
            wait = max(5, min(int(body.get("wait_seconds", 30) or 30), 300))
        except (TypeError, ValueError):
            wait = 30
        try:
            max_items = int(body.get("max_items", 0) or 0)
        except (TypeError, ValueError):
            max_items = 0
        max_items = max_items if max_items > 0 else None

        _log("———————— 开始：豆包批量提炼（等待 %d 秒/条%s）————————" % (
            wait, ("，最多 %d 条" % max_items) if max_items else ""))
        self._send_json({"ok": True, "status": self._status()})

        def _worker():
            try:
                from obsidian_kb import (config as config_mod,
                                         doubao_automation,
                                         linker,
                                         logger as logger_mod,
                                         registry)
                # 开始后约 2 秒，自动把豆包客户端切到前台并最大化
                time.sleep(2)
                doubao_automation.bring_doubao_to_front(log_fn=_log)
                cfg = config_mod.load_config(_cfg_path(), cwd=BASE_DIR)
                root = _state["root"]
                log_dir = (_state.get("dirs") or {}).get("报告") or \
                    cfg["logging"].get("log_dir", "处理日志")
                logger, report = logger_mod.setup_logging(log_dir, root)
                logger.addHandler(_GuiLogHandler())
                reg = registry.Registry(root)
                doubao_automation.refine_loop(
                    cfg, root, _coords, reg, logger, report,
                    wait_seconds=wait, max_items=max_items,
                    stop_event=_doubao_stop)
                # 全部完成后运行链接引擎 + MOC（对齐模板流程）
                if not _doubao_stop.is_set():
                    linker.run_linking(cfg, root, reg, logger, report)
                    linker.generate_mocs(cfg, root, logger, report)
                    reg.save()
                    started = datetime.datetime.now()
                    path = logger_mod.write_report(
                        report, root, log_dir, started, 0.0)
                    _log("处理报告：%s" % path)
            except Exception as e:
                _log("豆包整理出错：%s" % e)
                startup_log("DOUBAO ERROR " + traceback.format_exc())
            finally:
                _doubao_running["flag"] = False
                with _lock:
                    _busy.update(flag=False, action="")
                _log("———————— 完成：豆包整理 ————————")

        threading.Thread(target=_worker, daemon=True).start()

    def _handle_upload(self, body):
        """接收前端选中的文件（base64），写入 <库>/未处理/。"""
        files = body.get("files") or []
        if not files:
            self._send_json({"ok": False, "error": "没有收到文件"})
            return
        if not _state["root"]:
            self._send_json({"ok": False, "error": "请先填写知识库位置"})
            return
        inbox = _inbox_path()
        saved, errors = [], []
        for f in files:
            name = (f.get("name") or "").replace("\\", "/")
            if not name:
                continue
            base = os.path.basename(name)
            if not base or base.startswith("."):
                continue
            try:
                data = base64.b64decode(f.get("data") or "")
            except Exception:
                errors.append(name + "（解码失败）")
                continue
            dst = os.path.join(inbox, base)
            try:
                with open(dst, "wb") as out:
                    out.write(data)
                saved.append(base)
            except Exception as e:
                errors.append("%s（%s）" % (name, e))
        if saved:
            _log("已放入未处理：%s" % "、".join(saved))
        if errors:
            _log("导入失败：%s" % "；".join(errors))
        self._send_json({
            "ok": True, "saved": saved, "errors": errors,
            "items": _inbox_list(),
            "tip": "文件已放入未处理，可在下方列表检查/删减，然后点「一键同步」",
        })

    # ---------- 响应工具 ----------
    def _send_file(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                html = f.read()
        except Exception:
            html = "<html><body><h2>缺少界面文件 gui_index.html</h2></body></html>"
        self._send_html(html)

    def _send_html(self, html):
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def _pick_port(start=PORT, tries=PORT_TRIES):
    """找一个可用端口（先 bind 测试再释放，交给服务器绑定）。"""
    for p in range(start, start + tries):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", p))
            return p
        except OSError:
            continue
        finally:
            s.close()
    return None


def main():
    load_state()
    _load_coords()   # 启动时加载当前坐标文件
    port = _pick_port()
    if port is None:
        msg = "在 %d~%d 端口范围内都找不到可用端口，请检查系统端口占用情况。" % (
            PORT, PORT + PORT_TRIES - 1)
        startup_log("ERROR " + msg)
        print(msg, flush=True)
        return 1

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = "http://127.0.0.1:%d/" % port
    try:
        with open(URL_FILE, "w", encoding="utf-8") as f:
            f.write(url)
    except Exception:
        pass

    threading.Thread(target=_scheduler_loop, daemon=True).start()
    startup_log("READY " + url)
    _log("知识库助手已启动：%s" % url)
    print("知识库助手已启动：%s（点界面「退出」或关闭此进程停止）" % url, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    startup_log("STOPPED")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        startup_log("FATAL " + traceback.format_exc())
        raise
