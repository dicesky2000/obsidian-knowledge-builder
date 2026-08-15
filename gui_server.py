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
  POST /api/upload          上传资料到未处理（base64 JSON）
  POST /api/inbox/delete    删除未处理文件（移入 回收站，可找回）
  GET  /api/trash           回收站文件列表（回收站，可恢复）
  POST /api/trash/restore   恢复回收站文件回 01未处理（还原原名）
  POST /api/trash/clear     清空回收站（物理删除，不可找回）
  POST /api/prompts         保存豆包提示词格式（发送格式）
  POST /api/debug/toggle    开关调试模式（开启时记录各目录快照）
  POST /api/debug/reset     调试复位：清除调试期间生成文件、已处理素材移回未处理
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
import re
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
STATE_FILE = os.path.join(BASE_DIR, "gui_state.json")
DEFAULT_COORD_FILE = "豆包坐标.json"
# 豆包启动途径：网页版（默认，自动打开浏览器）/ 桌面版(exe，用户自行打开客户端)
DOUBAO_MODES = ("web", "desktop")
DOUBAO_WEB_URL = "https://www.doubao.com/chat/?channel=RSX4N"
DOUBAO_MODE_LABEL = {"web": "网页版", "desktop": "桌面版(exe)"}
STARTUP_LOG = os.path.join(BASE_DIR, "gui_startup.log")
URL_FILE = os.path.join(BASE_DIR, "gui_url.txt")
PORT = 8765
PORT_TRIES = 10

# 豆包提示词默认内容（未在 GUI 配置时使用；{素材内容} 为素材正文占位符）
SEND_DEFAULT = """# 豆包提示词 — 知识提炼助手

## 角色

你是一位知识管家，负责将原始素材提炼为标准知识笔记。输出格式精确，不做多余解释。

## 输出格式

严格按以下 markdown 结构输出：

```markdown
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
```

## 要求

1. **分类**：从素材内容自行归纳，最多6个。例如：技术原理、商业策略、学习方法、系统设计、心理学等
2. **关键词**：8～18个，逗号分隔
3. **摘要**：不超过120字
4. **详细内容**：**不少于600字**
5. **逻辑树**：可选
6. **语言**：全部中文
7. **简洁**：只输出笔记本身，无解释无问候。**不要用 ``` 代码块包围**
8. **短内容**：素材有实际文字且少于300字 → 详细内容不强制600字，豆包自由发挥即可；纯链接（内容以 http 开头）不受此规则影响，仍要求不少于600字

---

**未收录**：{素材内容}
"""

# B 层知识提炼笔记的生成逻辑与结构（只读展示，不参与生成）
GEN_SPEC = """B层知识提炼笔记 — 生成逻辑与结构（只读参考）

【一、由哪些部分组成】（7 个固定部分，顺序不可变）
1. frontmatter 元数据：type: 笔记；分类（最多6个）；原始链接（出处URL或说明）；作者（未知留空）
2. 一级标题：# 一句话总结（4~20 个中文字符）
3. 关键词行：**关键词**：8~18 个，逗号分隔（中英文混合，保持原样）
4. 摘要行：**摘要**：浓缩核心内容，不超过 120 字
5. 详细内容：## 详细内容 —— 不少于 600 字，分自然段，用自己的话重新组织，不能原文摘抄
6. 逻辑树（可选）：## 逻辑树 —— 结构复杂的知识点用层级列表表达
7. 双向链接（末尾自动追加）：## 双向链接 —— 原始素材回链 + 相关笔记互链（共享关键词）+ 索引笔记链接（详见【六、双向链接】）

【二、拼接方式】
文件名 = 一句话总结 + "_" + YYYYMMDDHHMMSS + ".md"
  · 一句话总结：由豆包从素材提炼（4~20 字）
  · 时间戳：取自 01未处理 素材文件名中的 14 位时间戳；无则取当前时间
文件内容 = frontmatter + 标题 + 关键词 + 摘要 + 详细内容 + 逻辑树（依次拼接）
  · 素材正文整体替换提示词中的 {素材内容} 占位符后发给豆包
  · 豆包按上述结构一次性输出整篇笔记；程序只负责解析一句话总结、
    去掉代码块包裹后原样保存，不做二次改写

【三、生成逻辑】（A 层 → B 层完整链路）
1. 素材存入 01未处理/时间戳.md（A 层，只进不出）
2. 豆包自动整理：按 01未处理 文件顺序逐个读取素材
3. 组装提示词：发送格式模板（{素材内容} 处替换为素材正文）
4. 键鼠自动化发送给豆包（豆包置顶在前，不碰鼠标键盘）
5. 等待生成 → 复制回复 → 解析一句话总结、去代码块包裹
6. 保存到 03知识提炼/一句话总结_时间戳.md（B 层，只删不改）
7. 素材从 01未处理 移至 02已处理
8. 全部完成后运行链接引擎：关键词全转小写精确匹配，共享 ≥3 个即建链，
   每篇最多保留前 8 个链接，并链接到自身对应的已处理原文；再生成 双向链接 区块与索引笔记（详见【六、双向链接】）

【四、字段约束】
· 分类：最多 6 个，由豆包自行归纳（技术原理、商业策略、学习方法等）
· 关键词：8~18 个，逗号分隔，中英文混合
· 摘要：≤120 字，是提炼后的核心梗概
· 详细内容：≥600 字；素材有实际文字且少于 300 字时不强制，豆包自由发挥；
  纯链接（以 http 开头）不受影响，仍要求 ≥600 字
· 语言：全部中文；只输出笔记本身，无解释无问候

【五、维护纪律】
· B 层笔记只删不改；过时/错误内容直接删除笔记文件
· 删除后产生的死链接不予修复
· 链接引擎在全部新笔记处理完毕后统一运行，每篇笔记只写一次

【六、双向链接】（B 层笔记末尾自动追加）
· 区块位置：每篇 B 层笔记正文最末尾，自动追加「## 双向链接」小节，依次包含三部分：
  ① 原始素材回链：`- [原始素材](相对路径)` —— 链接到自身对应的已处理原文
     （frontmatter.source 优先，.kb_registry.json 注册表回退；占用 1 个链接名额）
  ② 相关笔记互链：`- [[笔记名]] — 共享关键词：交集` —— 关键词全转小写后精确匹配，
     共享 ≥3 个相同关键词即建链（阈值可调），每篇最多保留前 8 条，按共享数降序排列
  ③ 索引笔记链接：`- [[索引_x]] — 共享关键词：…` —— 不受 8 条限制，每命中一个关键词或分类值各一条
· 索引笔记：目录 = 03知识提炼/索引笔记（structure.B_索引）；命名 = 索引_<关键词>.md /
  索引_分类_<分类值>.md；内容 = `# 索引：<主题>` + 空行 + `- [[B层笔记]]` 列表，
  无 frontmatter；与 B 层笔记互相链接，形成「笔记 ↔ 索引」双向链接
· 关键词来源：正文 **关键词**：行 + frontmatter「关键词」/「tags」+「分类」（按中文逗号拆分）
"""


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
    "coord_file": DEFAULT_COORD_FILE,    # 当前豆包坐标文件名（可自定义，支持多套）
    "doubao_mode": "web",                # 豆包启动途径：web 网页版 / desktop 桌面版(exe)
    "prompts": {},                       # 豆包提示词配置 {"send_format"}
    "debug": {"enabled": False, "snapshot": {"inbox": [], "done": [], "notes": [], "logs": []}},
}
_log_seq = itertools.count(1)
_log_lines = deque(maxlen=4000)          # 界面日志缓冲
_busy = {"flag": False, "action": ""}    # 是否有任务在运行
_exiting = {"flag": False}
_coords = {"desktop": {}, "web": {}}     # 豆包坐标（按启动途径分套，与坐标文件同构）
_coord_waiting = {"which": ""}           # 正在等待用户记录哪个坐标
_doubao_stop = threading.Event()         # 豆包整理停止信号
_doubao_running = {"flag": False}
_doubao_end = {"state": "", "msg": ""}   # 最近一次豆包整理结束状态（ok/error/stopped）
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
            # 清理旧版本残留字段（自动同步已随「一键同步」移除；存放目录自定义已移除）
            for k in ("schedule_enabled", "schedule_minutes", "dirs"):
                data.pop(k, None)
            _state.update(data)
    except Exception:
        pass
    # 旧版 gui_state.json 无 doubao_mode → 自动补默认（网页版）
    _state.setdefault("doubao_mode", "web")


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
    """返回 run.py 使用的配置文件路径（固定使用默认配置）。"""
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


# ---------------------------------------------------------------------------
# 豆包坐标：双途径（网页版/桌面版）分套处理
# ---------------------------------------------------------------------------
_COORD_KEYS = ("输入框", "下翻箭头", "复制按钮")


def _is_old_coord_format(data):
    """旧格式（平铺坐标名→{x,y}）判定：顶层存在任一坐标名即 True。"""
    return isinstance(data, dict) and any(k in data for k in _COORD_KEYS)


def _extract_coord_set(obj):
    """过滤出含 x/y 的坐标项，返回平铺 dict。"""
    if not isinstance(obj, dict):
        return {}
    return {k: v for k, v in obj.items()
            if isinstance(v, dict) and "x" in v and "y" in v}


def _parse_coord_data(data):
    """统一解析坐标数据 → 嵌套结构 {"desktop": {...}, "web": {...}}。

    旧格式（平铺坐标名）自动归为 desktop；新格式缺套补空 {}；非法项过滤。
    """
    if _is_old_coord_format(data):
        return {"desktop": _extract_coord_set(data), "web": {}}
    out = {}
    for mode in DOUBAO_MODES:
        out[mode] = _extract_coord_set(data.get(mode)) if isinstance(data, dict) else {}
    return out


def _active_mode():
    """返回当前合法启动途径，非法值回退 'web'。"""
    m = _state.get("doubao_mode")
    return m if m in DOUBAO_MODES else "web"


def _active_coords():
    """返回当前途径的坐标套（不存在则补空）。"""
    return _coords.setdefault(_active_mode(), {})


def _coord_counts():
    """返回各途径坐标数量，如 {"desktop": 3, "web": 0}。"""
    return {m: len(_extract_coord_set(_coords.get(m))) for m in DOUBAO_MODES}


def _load_coords(name=None):
    """加载坐标文件到内存；name 缺省用当前 coord_file。

    文件为旧平铺格式时自动迁移为 {"desktop": 原内容, "web": {}} 并写回一次（幂等）。
    """
    global _coords
    _coords = {"desktop": {}, "web": {}}
    p = _coord_path(name)
    if os.path.isfile(p):
        try:
            with open(p, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            parsed = _parse_coord_data(data)
            if _is_old_coord_format(data):
                # 旧格式 → 归 desktop 并立即写回迁移（幂等，二次加载走新格式）
                _coords = parsed
                _save_coords()
            else:
                _coords = parsed
        except Exception:
            _coords = {"desktop": {}, "web": {}}
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


def _wait_doubao_window(mode, timeout=30, log_fn=None):
    """按途径轮询查找豆包窗口（网页版/桌面版），命中即置前并最大化。

    返回 True=已找到并置前；False=超时未找到。
    """
    from obsidian_kb import doubao_automation
    t0 = time.time()
    while time.time() - t0 < timeout:
        if doubao_automation.find_doubao_windows(mode=mode):
            return doubao_automation.bring_doubao_to_front(
                log_fn=log_fn, mode=mode)
        time.sleep(1)
    return False


def _find_browser_exe():
    """探测真实浏览器 exe（Edge > Chrome > Firefox），保证打开 URL 不经过系统默认 URL 关联。

    系统默认 URL 关联若被劫持/损坏（如 .html 关联到编辑器），webbrowser.open
    会打开无关应用；显式指定浏览器 exe 可彻底避免。找不到返回 None。
    """
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    lpd = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        os.path.join(pf86, "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(pf, "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(pf, "Google", "Chrome", "Application", "chrome.exe"),
    ]
    if lpd:
        candidates.append(os.path.join(lpd, "Google", "Chrome", "Application", "chrome.exe"))
    candidates.append(os.path.join(pf, "Mozilla Firefox", "firefox.exe"))
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    return None


def _open_doubao_web(log_fn=None):
    """打开豆包网页：优先用真实浏览器 exe（不经系统默认 URL 关联，避免误开无关应用）。

    找不到已知浏览器时回退系统默认方式（webbrowser.open）。返回 True=已用浏览器打开。
    """
    def _log(m):
        if log_fn:
            log_fn(m)

    exe = _find_browser_exe()
    if exe:
        try:
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            subprocess.Popen([exe, DOUBAO_WEB_URL], creationflags=flags)
            _log("已用浏览器打开豆包网页：%s" % os.path.basename(exe))
            return True
        except Exception as e:
            _log("启动浏览器失败（%s），回退系统默认方式……" % e)
    try:
        import webbrowser
        webbrowser.open(DOUBAO_WEB_URL)
        _log("已尝试用系统默认方式打开豆包网页（未找到已知浏览器 exe）")
        return False
    except Exception as e:
        _log("打开豆包网页失败：%s" % e)
        return False


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


def _write_prompt_files():
    """把 GUI 配置的提示词写回知识库 D 层规则模板目录，与库内模板保持一致。"""
    root = _state.get("root")
    prompts = _state.get("prompts") or {}
    send = (prompts.get("send_format") or "").strip()
    if not root or not send:
        return
    try:
        from obsidian_kb import config as _cfgmod
        cfg = _cfgmod.load_config(_cfg_path(), cwd=BASE_DIR)
        rules_dir = os.path.join(root, cfg["structure"].get("D_规则模板", "05规则模板"))
        os.makedirs(rules_dir, exist_ok=True)
        with open(os.path.join(rules_dir, "豆包知识提炼提示词.md"),
                  "w", encoding="utf-8", newline="\n") as f:
            f.write(send)
        _log("已写回规则模板目录：%s" % rules_dir)
    except Exception as e:
        _log("写回规则模板失败：%s" % e)


def _prompts_config():
    """返回当前提示词配置；未配置或内容全空时返回内置默认，供前端回填。"""
    p = _state.get("prompts") or {}
    if p.get("send_format"):
        return p
    return {"send_format": SEND_DEFAULT}


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
        inbox_rel = cfg["import"].get("inbox", "01未处理")
    except Exception:
        inbox_rel = "01未处理"
    return [("01未处理", os.path.abspath(os.path.join(root, inbox_rel)))]


def _inbox_path():
    root = _state.get("root")
    if not root:
        return ""
    inbox_rel = "01未处理"
    try:
        from obsidian_kb import config as _cfgmod
        cfg = _cfgmod.load_config(_cfg_path(), cwd=BASE_DIR)
        inbox_rel = cfg["import"].get("inbox", "01未处理")
    except Exception:
        pass
    inbox = os.path.join(root, inbox_rel)
    if not os.path.isdir(inbox):
        os.makedirs(inbox, exist_ok=True)
    return inbox


def _unique_inbox_dst(inbox, base):
    """目标路径冲突时自动重命名，避免覆盖已有文件。

    策略：已存在同名 → `名字_YYYYMMDDHHMMSS.扩展名`，仍冲突则追加 _2/_3 递增。
    返回 (绝对路径, 最终文件名)；无冲突时最终文件名=原名。
    """
    dst = os.path.join(inbox, base)
    if not os.path.exists(dst):
        return dst, base
    stem, ext = os.path.splitext(base)
    stamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    cand = "%s_%s%s" % (stem, stamp, ext)
    i = 2
    while os.path.exists(os.path.join(inbox, cand)):
        cand = "%s_%s_%d%s" % (stem, stamp, i, ext)
        i += 1
    return os.path.join(inbox, cand), cand


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
    """按来源统计待同步文件数，如 {"01未处理": 5}。"""
    bd: Dict[str, int] = {}
    try:
        for it in _inbox_list():
            bd[it.get("source", "01未处理")] = bd.get(it.get("source", "01未处理"), 0) + 1
    except Exception:
        pass
    return bd


def _doubao_materials():
    """豆包提炼实际会扫描的素材清单：统一为「未处理」（所有非隐藏文件，.md/.txt 走文本、其余整文件直发）。

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
                if os.path.isfile(p) and not fn.startswith("."):
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
    """把指定来源的文件移入 <库>/回收站/（可找回，不做物理删除）。

    入参 items 为 [{"name": ..., "source": ...}, ...]；source 缺省按「未处理」处理。
    """
    root = _state.get("root")
    if not root:
        return [], ["知识库未设置"]
    trash = _trash_path()    # 复用路径函数（内部惰性迁移旧目录 _kb_回收站）
    os.makedirs(trash, exist_ok=True)
    src_map = {label: path for label, path in _import_source_dirs()}
    moved, errors = [], []
    for it in (items or []):
        if isinstance(it, dict):
            label = it.get("source") or "01未处理"
            name = str(it.get("name") or "")
        else:
            label, name = "01未处理", str(it)
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
# 回收站管理：列表 / 恢复 / 清空
# ---------------------------------------------------------------------------
_trash_mig_lock = threading.Lock()   # 回收站旧目录迁移锁（独立于 _lock，避免与业务串行锁耦合）

def _trash_migrate():
    """把旧目录 <root>/_kb_回收站 一次性迁移到 <root>/回收站（幂等、并发安全）。

    仅当旧目录存在时执行；文件与子目录整体搬移（若跳过子目录，旧目录删不掉、
    且子目录会失联）。目标重名时加 _mig_<n> 后缀，不覆盖。迁移完成后删除旧目录，
    删除失败仅记日志、保留旧目录，下次访问自动重试。
    """
    root = _state.get("root")
    if not root:
        return
    old = os.path.join(root, "_kb_回收站")
    if not os.path.isdir(old):
        return                            # 幂等：旧目录不存在即无副作用
    new = os.path.join(root, "回收站")
    with _trash_mig_lock:
        if not os.path.isdir(old):        # 二次检查：另一线程已迁移完成
            return
        try:
            os.makedirs(new, exist_ok=True)
            for fn in sorted(os.listdir(old)):
                src = os.path.join(old, fn)
                dst = os.path.join(new, fn)
                i = 1
                while os.path.exists(dst):  # 目标重名：_mig_<n> 后缀不覆盖
                    stem, ext = os.path.splitext(fn)
                    i += 1
                    dst = os.path.join(new, "%s_mig_%d%s" % (stem, i, ext))
                try:
                    os.replace(src, dst)    # 同 root 下同文件系统，文件/子目录均可整体搬移
                except Exception:
                    continue                # 单条目失败不阻断整体迁移
            try:
                os.rmdir(old)               # 空目录删除；仍非空（有失败项）则抛 OSError
            except OSError:
                _log("回收站旧目录 _kb_回收站 未能删除，请手动处理：%s" % old)
            else:
                _log("回收站旧目录已迁移：_kb_回收站 → 回收站")
        except Exception as e:
            _log("回收站迁移异常：%s" % e)


def _trash_path():
    """回收站绝对路径（<root>/回收站）；root 未设置时返回空串。

    首次访问时自动把旧目录 _kb_回收站 迁移到 回收站（幂等，见 _trash_migrate）。
    """
    root = _state.get("root")
    if not root:
        return ""
    _trash_migrate()
    return os.path.join(root, "回收站")


_TRASH_PREFIX_RE = re.compile(r"^(\d{8}_\d{6})_(.+)$")


def _trash_restore_name(fn):
    """从回收站文件名还原原始文件名。

    回收站文件名有两种后缀来源，都要剥离：
    - 删除时重名：_inbox_delete 生成 `YYYYMMDD_HHMMSS_<原名>_<N>`（_N 在扩展名后，如 aaa.md_2）
    - 恢复时重名：_trash_restore 生成 `<原名去掉扩展名>_<N>.<ext>`（_N 在扩展名前，如 aaa_2.md）
    """
    m = _TRASH_PREFIX_RE.match(fn)
    rest = m.group(2) if m else fn
    m2 = re.match(r"^(.*)_(\d+)$", rest)
    return m2.group(1) if m2 else rest


def _trash_list():
    """回收站内文件列表 [{name, size, mtime}]；目录不存在或为空时返回 []。"""
    trash = _trash_path()
    if not trash or not os.path.isdir(trash):
        return []
    items = []
    try:
        for fn in sorted(os.listdir(trash)):
            p = os.path.join(trash, fn)
            if os.path.isfile(p):
                st = os.stat(p)
                items.append({
                    "name": fn,
                    "size": st.st_size,
                    "mtime": datetime.datetime.fromtimestamp(
                        st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                })
    except Exception:
        pass
    return items


def _trash_restore(items):
    """把回收站中指定文件移回 01未处理，还原原名；目标重名时加 _2/_3 后缀不覆盖。"""
    root = _state.get("root")
    if not root:
        return [], ["知识库未设置"]
    trash = _trash_path()
    inbox = _inbox_path()
    if not trash or not os.path.isdir(trash):
        return [], ["回收站为空或不存在"]
    restored, errors = [], []
    for it in (items or []):
        name = str(it.get("name") if isinstance(it, dict) else it or "")
        base = os.path.basename(name)            # 防路径穿越：只取文件名
        if not base or base.startswith("."):
            errors.append("%s（非法文件名）" % base)
            continue
        src = os.path.join(trash, base)
        if not os.path.isfile(src):
            errors.append("%s（不存在）" % base)
            continue
        orig = _trash_restore_name(base)         # 还原原名
        dst = os.path.join(inbox, orig)
        if os.path.exists(dst):                  # 目标重名：_2/_3 唯一后缀
            stem, ext = os.path.splitext(orig)
            i = 2
            while os.path.exists(os.path.join(inbox, "%s_%d%s" % (stem, i, ext))):
                i += 1
            dst = os.path.join(inbox, "%s_%d%s" % (stem, i, ext))
        try:
            os.replace(src, dst)
            restored.append(os.path.basename(dst))
        except Exception as e:
            errors.append("%s（%s）" % (base, e))
    return restored, errors


def _trash_clear():
    """物理删除回收站内全部文件（子目录跳过），返回清除的文件数。"""
    trash = _trash_path()
    if not trash or not os.path.isdir(trash):
        return 0
    cleared = 0
    for fn in sorted(os.listdir(trash)):
        p = os.path.join(trash, fn)
        try:
            if os.path.isfile(p):
                os.remove(p)
                cleared += 1
        except Exception:
            continue
    return cleared


# ---------------------------------------------------------------------------
# 调试模式：快照 + 复位
# ---------------------------------------------------------------------------
def _debug_dirs():
    """调试模式涉及的 5 个目录绝对路径；root 未设置或配置读取失败时返回空串。"""
    root = _state.get("root")
    if not root:
        return "", "", "", "", ""
    try:
        from obsidian_kb import config as _cfgmod
        cfg = _cfgmod.load_config(_cfg_path(), cwd=BASE_DIR)
    except Exception:
        return "", "", "", "", ""
    structure = cfg.get("structure", {})
    inbox = os.path.join(root, cfg.get("import", {}).get("inbox", "01未处理"))
    done = os.path.join(root, structure.get("已处理", "02已处理"))
    notes = os.path.join(root, structure.get("B_知识提炼", "03知识提炼"))
    log_dir = cfg.get("logging", {}).get("log_dir", "处理日志")
    logs = os.path.join(root, log_dir)
    return inbox, done, notes, logs


def _debug_snapshot():
    """记录 5 个目录当前 basename 集合（目录不存在记空列表）。"""
    inbox, done, notes, logs = _debug_dirs()
    snap = {}
    for key, d in (("inbox", inbox), ("done", done), ("notes", notes),
                   ("logs", logs)):
        snap[key] = sorted(os.listdir(d)) if d and os.path.isdir(d) else []
    return snap


def _strip_hmss(fn, candidates):
    """去掉文件名扩展名前的 _HHMMSS 冲突后缀；若还原名在候选集合中则返回，否则返回 None。"""
    stem, ext = os.path.splitext(fn)
    m = re.match(r"^(.*)_\d{6}$", stem)
    if m and m.group(1) + ext in candidates:
        return m.group(1) + ext
    return None


def _debug_reset():
    """按快照撤销调试期间的全部操作（在后台线程中执行）。

    ① 删除 03知识提炼 / 处理日志 中调试期间新生成的文件；
    ② 把调试期间从 01未处理 移到 02已处理 的素材移回（恢复原名，重名加唯一后缀不覆盖）；
    ③ 清理 .kb_registry.json 中已删除笔记的注册项。
    复位后保持调试模式开启、快照不变，可反复运行再复位。
    """
    inbox, done, notes, logs = _debug_dirs()
    snap = (_state.get("debug") or {}).get("snapshot") or {}
    root = _state.get("root")
    deleted_notes = []
    removed = {"notes": 0, "logs": 0}
    errors = []

    # ① 删除调试期间新生成的文件（笔记 / 日志报告）
    for d, key in ((notes, "notes"), (logs, "logs")):
        if not d or not os.path.isdir(d):
            continue
        base = set(snap.get(key) or [])
        for fn in sorted(os.listdir(d)):
            p = os.path.join(d, fn)
            if not os.path.isfile(p) or fn in base:
                continue
            try:
                os.remove(p)
                removed[key] += 1
                if key == "notes" and root:
                    deleted_notes.append(
                        os.path.relpath(p, root).replace("\\", "/"))
            except Exception as e:
                errors.append("删除 %s：%s" % (fn, e))

    # ② 素材移回 01未处理（恢复原名；重名加 _2/_3 唯一后缀，不覆盖）
    moved = 0
    if done and os.path.isdir(done) and inbox:
        os.makedirs(inbox, exist_ok=True)
        inbox_base = set(snap.get("inbox") or [])
        done_base = set(snap.get("done") or [])
        for fn in sorted(os.listdir(done)):
            p = os.path.join(done, fn)
            if not os.path.isfile(p) or fn in done_base:
                continue
            orig = fn if fn in inbox_base else (_strip_hmss(fn, inbox_base) or fn)
            dst = os.path.join(inbox, orig)
            if os.path.exists(dst):
                stem, ext = os.path.splitext(orig)
                i = 2
                while os.path.exists(os.path.join(inbox, "%s_%d%s" % (stem, i, ext))):
                    i += 1
                dst = os.path.join(inbox, "%s_%d%s" % (stem, i, ext))
            try:
                os.replace(p, dst)
                moved += 1
            except Exception as e:
                errors.append("移回 %s：%s" % (fn, e))

    # ③ registry 清理（失败仅告警，不阻断文件还原）
    try:
        if root and deleted_notes:
            from obsidian_kb.registry import Registry
            reg = Registry(root)
            for rel in deleted_notes:
                reg.remove_by_note(rel)
            reg.save()
    except Exception as e:
        errors.append("registry 清理：%s" % e)

    _log("调试复位完成：删除笔记 %d、日志/报告 %d，移回素材 %d%s" % (
        removed["notes"], removed["logs"], moved,
        "；告警：%s" % "；".join(errors[:5]) if errors else ""))


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
        elif path == "/api/trash":
            self._send_json({"ok": True, "items": _trash_list()})
        elif path == "/api/doubao/status":
            self._send_json({"ok": True, "coords": _active_coords(),
                             "coord_file": _state.get("coord_file") or DEFAULT_COORD_FILE,
                             "coord_files": _list_coord_files(),
                             "coord_waiting": _coord_waiting.get("which", ""),
                             "running": _doubao_running["flag"],
                             "doubao_mode": _active_mode(),
                             "doubao_modes": list(DOUBAO_MODES),
                             "coord_counts": _coord_counts()})
        elif path == "/api/doubao/materials":
            # 豆包实际会扫描的素材（统一为「未处理」），供「自动匹配」统计
            self._send_json({"ok": True, "items": _doubao_materials()})
        elif path == "/api/coord/files":
            self._send_json({"ok": True, "files": _list_coord_files(),
                             "current": _state.get("coord_file") or DEFAULT_COORD_FILE})
        elif path in ("/api/init", "/api/open",
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
            "inbox_count": len(_inbox_list()) if root else 0,
            "inbox_breakdown": _inbox_breakdown() if root else {},
            "coord_file": _state.get("coord_file") or DEFAULT_COORD_FILE,
            "coord_files": _list_coord_files(),
            "coords": _active_coords(),
            "coord_waiting": _coord_waiting.get("which", ""),
            "doubao_mode": _active_mode(),
            "doubao_modes": list(DOUBAO_MODES),
            "coord_counts": _coord_counts(),
            "doubao_running": _doubao_running["flag"],
            "doubao_end": {"state": _doubao_end["state"],
                           "msg": _doubao_end["msg"]},
            "prompts": _prompts_config(),
            "gen_spec": GEN_SPEC,
            "debug": {
                "enabled": bool((_state.get("debug") or {}).get("enabled")),
                "snapshot": {k: len(v) for k, v in
                             ((_state.get("debug") or {}).get("snapshot") or {}).items()},
            },
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
        if path in ("/api/init", "/api/open",
                    "/api/open_path", "/api/open_report", "/api/exit"):
            self._action(path, body)
        elif path == "/api/prompts":
            send = str(body.get("send_format") or "").strip()
            _state["prompts"] = {"send_format": send}
            save_state()
            _write_prompt_files()
            _log("提示词格式已保存（发送格式 %d 字）" % len(send))
            self._send_json({"ok": True, "status": self._status()})

        elif path == "/api/debug/toggle":
            dbg = _state.setdefault("debug", {})
            if body.get("enabled"):
                dbg["enabled"] = True
                dbg["snapshot"] = _debug_snapshot()
                _log("调试模式已开启（已记录各目录快照：%s）" % json.dumps(
                    {k: len(v) for k, v in dbg["snapshot"].items()},
                    ensure_ascii=False))
            else:
                dbg["enabled"] = False
                dbg["snapshot"] = {"inbox": [], "done": [], "notes": [],
                                   "logs": []}
                _log("调试模式已关闭（快照已清空，已生成文件保留）")
            save_state()
            self._send_json({"ok": True, "status": self._status()})

        elif path == "/api/debug/reset":
            dbg = _state.get("debug") or {}
            if not dbg.get("enabled"):
                self._send_json({"ok": False, "error": "请先开启调试模式"})
                return
            with _lock:
                if _busy["flag"]:
                    self._send_json({"ok": False, "error": "已有任务在运行，请稍候"})
                    return
                _busy.update(flag=True, action="调试复位")
            _log("———————— 开始：调试复位 ————————")
            self._send_json({"ok": True, "status": self._status()})

            def _reset_worker():
                try:
                    _debug_reset()
                except Exception as e:
                    _log("调试复位出错：%s" % e)
                finally:
                    with _lock:
                        _busy.update(flag=False, action="")
                    _log("———————— 完成：调试复位 ————————")

            threading.Thread(target=_reset_worker, daemon=True).start()

        elif path == "/api/coord/record":
            which = body.get("which", "")
            if which not in ("输入框", "下翻箭头", "复制按钮"):
                self._send_json({"ok": False, "error": "坐标名称无效"})
                return
            mode = _active_mode()
            _coord_cancel.clear()
            _coord_waiting["which"] = which
            _log("请把鼠标移到豆包【%s】（%s）位置，按 F6 确认（按 Esc 取消）"
                 % (which, DOUBAO_MODE_LABEL[mode]))
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
                    _active_coords()[which] = {"x": pt[0], "y": pt[1]}
                    _save_coords()
                    _log("豆包坐标已保存：%s【%s】(%d,%d)"
                         % (DOUBAO_MODE_LABEL[mode], which, pt[0], pt[1]))

            threading.Thread(target=_worker, daemon=True).start()

        elif path == "/api/coord/cancel":
            _coord_cancel.set()
            self._send_json({"ok": True})

        elif path == "/api/coord/clear":
            mode = _active_mode()
            _active_coords().clear()
            _save_coords()
            _log("豆包坐标已清空（%s %s）"
                 % (DOUBAO_MODE_LABEL[mode], _state.get("coord_file")))
            self._send_json({"ok": True, "status": self._status()})

        elif path == "/api/coord/set_file":
            name = _safe_coord_name(body.get("name", ""))
            if not name:
                self._send_json({"ok": False, "error": "请填写坐标文件名称"})
                return
            _load_coords(name + ".json")
            _log("已切换到坐标文件：%s.json（%s 共 %d 个坐标）" % (
                name, DOUBAO_MODE_LABEL[_active_mode()], len(_active_coords())))
            self._send_json({"ok": True, "status": self._status()})

        elif path == "/api/coord/export":
            name = _safe_coord_name(body.get("name", "")) or \
                _state.get("coord_file", DEFAULT_COORD_FILE)[:-5]
            if len(_active_coords()) < 3:
                self._send_json({"ok": False,
                                 "error": "还没有完整的三个坐标，请先依次记录"})
                return
            _save_coords(name + ".json")
            _log("已生成坐标文件：%s.json（含网页版/桌面版两套坐标）" % name)
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
            parsed = _parse_coord_data(data)
            if not any(parsed.values()):
                self._send_json({"ok": False,
                                 "error": "文件中没有有效坐标（需要 x/y 字段）"})
                return
            _coords.clear()
            _coords.update(parsed)
            _save_coords(name + ".json")
            counts = _coord_counts()
            _log("已导入坐标文件：%s.json（%s）" % (
                name, " / ".join("%s %d 个" % (DOUBAO_MODE_LABEL[m], n)
                                 for m, n in counts.items())))
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

        elif path == "/api/doubao/mode":
            m = body.get("mode", "")
            if m not in DOUBAO_MODES:
                self._send_json({"ok": False, "error": "启动途径无效"})
                return
            _state["doubao_mode"] = m
            save_state()
            _log("已切换豆包启动途径：%s（坐标文件：%s）"
                 % (DOUBAO_MODE_LABEL[m], _state.get("coord_file")))
            self._send_json({"ok": True, "status": self._status()})

        elif path == "/api/doubao/test":
            dry = bool(body.get("dry"))
            _log("正在执行豆包自动化诊断……")
            self._send_json({"ok": True})

            def _diag():
                try:
                    from obsidian_kb import doubao_automation
                    doubao_automation.diagnostic(log_fn=_log, dry_run=dry,
                                                 coords=_active_coords())
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

        elif path == "/api/trash/restore":
            restored, errors = _trash_restore(body.get("items") or [])
            _log("恢复回收站文件：成功 %d 个%s" % (
                len(restored),
                "（%s）" % "、".join(restored) if restored else ""))
            if errors:
                _log("恢复失败：%s" % "；".join(errors))
            self._send_json({"ok": True, "restored": restored, "errors": errors,
                             "items": _trash_list()})

        elif path == "/api/trash/clear":
            cleared = _trash_clear()
            _log("已清空回收站：%d 个文件" % cleared)
            self._send_json({"ok": True, "cleared": cleared,
                             "items": _trash_list()})

        else:
            startup_log("404 POST " + self.path)
            self._send_json({"ok": False, "error": "未知接口: POST " + self.path},
                            code=404)

    def _action(self, path, body=None):
        """无参数操作（init / open / open_path / open_report / exit），GET 与 POST 通用。"""
        if path == "/api/init":
            if not _state["root"]:
                self._send_json({"ok": False, "error": "请先填写知识库位置"})
                return
            _task("创建知识库",
                  ["init", "--root", _state["root"], "--config", _cfg_path()])
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
                self._send_json({"ok": False, "error": "还没有处理报告，请先执行豆包自动整理或命令行 sync"})
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
        missing = [k for k in ("输入框", "下翻箭头", "复制按钮") if k not in _active_coords()]
        if missing:
            self._send_json({"ok": False,
                             "error": "请先记录豆包坐标（%s）：%s"
                             % (DOUBAO_MODE_LABEL[_active_mode()],
                                "、".join(missing))})
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
            end_state, end_msg = "ok", "豆包整理已完成，素材已提炼为 B 层笔记"
            try:
                from obsidian_kb import (config as config_mod,
                                         doubao_automation,
                                         linker,
                                         logger as logger_mod,
                                         registry)
                # 按启动途径把豆包切到前台并最大化：
                # 网页版 → 用真实浏览器打开豆包网页（不经系统默认关联，避免误开无关应用）
                # 桌面版 → 用户已自行打开客户端
                mode = _active_mode()
                if mode == "web":
                    _log("正在打开豆包网页版：%s" % DOUBAO_WEB_URL)
                    _open_doubao_web(log_fn=_log)
                    if not _wait_doubao_window("web", timeout=30, log_fn=_log):
                        _log("未检测到豆包网页版窗口（浏览器加载慢？），继续尝试……")
                else:
                    time.sleep(2)
                    doubao_automation.bring_doubao_to_front(
                        log_fn=_log, mode="desktop")
                cfg = config_mod.load_config(_cfg_path(), cwd=BASE_DIR)
                root = _state["root"]
                log_dir = cfg["logging"].get("log_dir", "处理日志")
                logger, report = logger_mod.setup_logging(log_dir, root)
                logger.addHandler(_GuiLogHandler())
                reg = registry.Registry(root)
                prompts = _state.get("prompts") or {}
                doubao_automation.refine_loop(
                    cfg, root, _active_coords(), reg, logger, report,
                    wait_seconds=wait, max_items=max_items,
                    stop_event=_doubao_stop,
                    send_format=prompts.get("send_format") or None)
                # 全部完成后运行链接引擎 + 索引笔记（对齐模板流程；调试模式下跳过，便于复位撤销）
                if not (_state.get("debug") or {}).get("enabled") \
                        and not _doubao_stop.is_set():
                    linker.run_linking(cfg, root, reg, logger, report)
                    linker.generate_indexes(cfg, root, logger, report)
                    reg.save()
                    started = datetime.datetime.now()
                    path = logger_mod.write_report(
                        report, root, log_dir, started, 0.0)
                    _log("处理报告：%s" % path)
            except Exception as e:
                end_state, end_msg = "error", "豆包整理出错：%s" % e
                _log("豆包整理出错：%s" % e)
                startup_log("DOUBAO ERROR " + traceback.format_exc())
            else:
                if _doubao_stop.is_set():
                    end_state, end_msg = "stopped", "豆包整理已被手动停止"
            finally:
                _doubao_running["flag"] = False
                with _lock:
                    _busy.update(flag=False, action="")
                _doubao_end["state"] = end_state
                _doubao_end["msg"] = end_msg
                if end_state == "ok":
                    _log("———————— 完成：豆包整理（正常跑完）————————")
                elif end_state == "error":
                    _log("———————— 完成：豆包整理（出错中断）————————")
                else:
                    _log("———————— 完成：豆包整理（手动停止）————————")

        threading.Thread(target=_worker, daemon=True).start()

    def _handle_upload(self, body):
        """接收前端选中的文件（base64），写入 <库>/未处理/。

        同名文件自动重命名（名字_时间戳.扩展名，不覆盖已有文件）；
        返回 saved（最终文件名）与 renamed（原名→新名映射），供前端反馈。
        """
        files = body.get("files") or []
        if not files:
            self._send_json({"ok": False, "error": "没有收到文件"})
            return
        if not _state["root"]:
            self._send_json({"ok": False, "error": "请先填写知识库位置"})
            return
        inbox = _inbox_path()
        saved, errors, renamed = [], [], []
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
            dst, final_name = _unique_inbox_dst(inbox, base)
            try:
                with open(dst, "wb") as out:
                    out.write(data)
                saved.append(final_name)
                if final_name != base:
                    renamed.append("%s → %s" % (base, final_name))
            except Exception as e:
                errors.append("%s（%s）" % (name, e))
        if saved:
            _log("已放入未处理：%s" % "、".join(saved))
        if renamed:
            _log("同名文件已自动重命名：%s" % "；".join(renamed))
        if errors:
            _log("导入失败：%s" % "；".join(errors))
        self._send_json({
            "ok": True, "saved": saved, "errors": errors, "renamed": renamed,
            "items": _inbox_list(),
            "tip": "文件已放入01未处理，可在下方列表检查/删减，然后点「豆包自动整理」",
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
