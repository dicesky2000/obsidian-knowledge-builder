# -*- coding: utf-8 -*-
"""验证网页版浏览器打开修复 + 未处理拖放上传（同名重命名）后端逻辑。

覆盖：_find_browser_exe 优先级探测 / _open_doubao_web 显式浏览器与回退 /
_unique_inbox_dst 同名自动重命名（时间戳 + _N 递增）。
"""
import base64
import os
import sys
import tempfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "obsidian_kb"))

import gui_server as g


def test_find_browser_exe_chrome_when_no_edge():
    env = {"ProgramFiles": r"C:\Program Files",
           "ProgramFiles(x86)": r"C:\Program Files (x86)",
           "LOCALAPPDATA": r"C:\Users\x\AppData\Local"}

    def fake_isfile(p):
        return p.endswith("chrome.exe") or p.endswith("firefox.exe")

    with mock.patch.dict(g.os.environ, env), \
         mock.patch.object(g.os.path, "isfile", side_effect=fake_isfile):
        exe = g._find_browser_exe()
    assert exe == r"C:\Program Files\Google\Chrome\Application\chrome.exe", exe
    print("[OK] 无 Edge 时命中 Chrome（Program Files）")


def test_find_browser_exe_edge_priority():
    env = {"ProgramFiles": r"C:\Program Files",
           "ProgramFiles(x86)": r"C:\Program Files (x86)",
           "LOCALAPPDATA": r"C:\Users\x\AppData\Local"}

    def fake_isfile(p):
        return p.endswith("msedge.exe")

    with mock.patch.dict(g.os.environ, env), \
         mock.patch.object(g.os.path, "isfile", side_effect=fake_isfile):
        exe = g._find_browser_exe()
    assert exe is not None and exe.endswith("msedge.exe"), exe
    print("[OK] Edge 优先")


def test_find_browser_exe_none():
    env = {"ProgramFiles": r"C:\Program Files",
           "ProgramFiles(x86)": r"C:\Program Files (x86)",
           "LOCALAPPDATA": r"C:\Users\x\AppData\Local"}
    with mock.patch.dict(g.os.environ, env), \
         mock.patch.object(g.os.path, "isfile", return_value=False):
        assert g._find_browser_exe() is None
    print("[OK] 无已知浏览器 → None（走回退）")


def test_open_web_uses_browser_exe():
    captured = []

    def fake_popen(args, **kw):
        captured.append((args, kw))
        return mock.MagicMock()

    with mock.patch.object(g, "_find_browser_exe",
                           return_value=r"C:\Edge\msedge.exe"), \
         mock.patch.object(g.subprocess, "Popen", side_effect=fake_popen):
        ok = g._open_doubao_web(log_fn=lambda m: None)
    assert ok is True
    assert captured and captured[0][0] == [r"C:\Edge\msedge.exe", g.DOUBAO_WEB_URL], captured
    assert captured[0][1].get("creationflags") is not None, "应带 CREATE_NO_WINDOW"
    print("[OK] 找到浏览器 → Popen 显式打开 URL（不经系统默认关联）")


def test_open_web_fallback_webbrowser():
    import webbrowser as _wb
    opened = []
    with mock.patch.object(g, "_find_browser_exe", return_value=None), \
         mock.patch.object(_wb, "open", side_effect=lambda url: opened.append(url)):
        ok = g._open_doubao_web(log_fn=lambda m: None)
    assert ok is False
    assert opened == [g.DOUBAO_WEB_URL], opened
    print("[OK] 无浏览器 exe → 回退 webbrowser.open")


def test_open_web_popen_failure_fallback():
    import webbrowser as _wb
    opened = []
    with mock.patch.object(g, "_find_browser_exe", return_value=r"C:\x\chrome.exe"), \
         mock.patch.object(g.subprocess, "Popen", side_effect=OSError("boom")), \
         mock.patch.object(_wb, "open", side_effect=lambda url: opened.append(url)):
        ok = g._open_doubao_web(log_fn=lambda m: None)
    assert ok is False and opened == [g.DOUBAO_WEB_URL]
    print("[OK] Popen 失败 → 回退 webbrowser.open")


def test_unique_dst_no_conflict():
    with tempfile.TemporaryDirectory() as d:
        p, n = g._unique_inbox_dst(d, "a.md")
        assert n == "a.md" and p == os.path.join(d, "a.md")
    print("[OK] 无冲突 → 原名")


def test_unique_dst_timestamp_rename():
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "a.md"), "w").close()
        p, n = g._unique_inbox_dst(d, "a.md")
        assert n != "a.md" and n.startswith("a_") and n.endswith(".md"), n
        assert os.path.basename(p) == n
        # 再次同名 → _2 递增
        open(p, "w").close()
        p2, n2 = g._unique_inbox_dst(d, "a.md")
        assert n2 != n and "_2" in n2, n2
    print("[OK] 同名 → 时间戳重命名；再冲突 → _N 递增")


def test_handle_upload_rename_via_http_like():
    # 直接调用模块逻辑等价路径：_unique_inbox_dst + 写文件（模拟 _handle_upload 核心）
    with tempfile.TemporaryDirectory() as d:
        data = base64.b64encode("hello".encode()).decode()
        files = [{"name": "x.txt", "data": data}]
        saved, renamed = [], []
        for f in files:
            base = os.path.basename(f["name"])
            dst, final = g._unique_inbox_dst(d, base)
            with open(dst, "wb") as out:
                out.write(base64.b64decode(f["data"]))
            saved.append(final)
            if final != base:
                renamed.append(base + " -> " + final)
        assert saved == ["x.txt"] and renamed == []
        # 第二次同名上传
        for f in files:
            base = os.path.basename(f["name"])
            dst, final = g._unique_inbox_dst(d, base)
            with open(dst, "wb") as out:
                out.write(base64.b64decode(f["data"]))
            saved.append(final)
            if final != base:
                renamed.append(base + " -> " + final)
        assert len(saved) == 2 and saved[0] == "x.txt" and saved[1] != "x.txt"
        assert len(renamed) == 1, renamed
    print("[OK] 上传核心：第二次同名自动重命名，原文件保留")


if __name__ == "__main__":
    import traceback
    results = []
    tests = [test_find_browser_exe_chrome_when_no_edge,
             test_find_browser_exe_edge_priority,
             test_find_browser_exe_none,
             test_open_web_uses_browser_exe,
             test_open_web_fallback_webbrowser,
             test_open_web_popen_failure_fallback,
             test_unique_dst_no_conflict,
             test_unique_dst_timestamp_rename,
             test_handle_upload_rename_via_http_like]
    try:
        for t in tests:
            t()
        results.append("ALL PASS (%d tests)" % len(tests))
    except Exception as e:
        results.append("FAIL: " + repr(e))
        results.append(traceback.format_exc())
    text = "\n".join(results)
    with open(os.path.join(HERE, "test_result.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
