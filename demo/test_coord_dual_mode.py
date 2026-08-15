# -*- coding: utf-8 -*-
"""验证豆包双启动途径（网页版/桌面版）坐标分套功能。

覆盖：旧格式迁移 / 嵌套解析 / 按 mode 取套 / record 只写当前套 /
import-export 语义 / URL 常量 / find_doubao_windows(mode) 窗口匹配 /
bring_doubao_to_front(mode) 透传 / load_state 迁移 doubao_mode。
"""
import json
import os
import sys
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "obsidian_kb"))

import doubao_automation as d
import gui_server as g

OLD = {"输入框": {"x": 1081, "y": 935},
       "下翻箭头": {"x": 978, "y": 868},
       "复制按钮": {"x": 473, "y": 701}}


def test_is_old_coord_format():
    assert g._is_old_coord_format(OLD) is True
    assert g._is_old_coord_format({"desktop": OLD, "web": {}}) is False
    assert g._is_old_coord_format({}) is False
    assert g._is_old_coord_format(None) is False
    print("[OK] _is_old_coord_format 旧/新/空判定")


def test_parse_coord_data():
    # 旧格式 → 包成 desktop
    r = g._parse_coord_data(OLD)
    assert r == {"desktop": OLD, "web": {}}, r
    # 新格式缺 web → 补 {}
    r = g._parse_coord_data({"desktop": {"输入框": {"x": 1, "y": 2}}})
    assert r["desktop"] == {"输入框": {"x": 1, "y": 2}} and r["web"] == {}, r
    # 非法项过滤
    r = g._parse_coord_data({"desktop": {"输入框": {"x": 1, "y": 2}, "垃圾": "x"},
                             "web": {"下翻箭头": {"x": 3, "y": 4}, "坏": {"a": 1}}})
    assert r["desktop"] == {"输入框": {"x": 1, "y": 2}}, r
    assert r["web"] == {"下翻箭头": {"x": 3, "y": 4}}, r
    print("[OK] _parse_coord_data 迁移/补套/过滤")


def test_active_mode_and_coords():
    saved_mode = g._state.get("doubao_mode")
    saved_coords = g._coords
    try:
        g._state["doubao_mode"] = "desktop"
        g._coords = {"desktop": {"输入框": {"x": 1, "y": 2}}, "web": {}}
        assert g._active_mode() == "desktop"
        assert g._active_coords() == {"输入框": {"x": 1, "y": 2}}
        # 非法值回退 web
        g._state["doubao_mode"] = "hack"
        assert g._active_mode() == "web"
        assert g._active_coords() == {}
    finally:
        g._state["doubao_mode"] = saved_mode
        g._coords = saved_coords
    print("[OK] _active_mode/_active_coords 取当前套 + 非法回退")


def test_record_writes_only_active_set():
    saved_mode = g._state.get("doubao_mode")
    saved_coords = g._coords
    try:
        g._state["doubao_mode"] = "desktop"
        g._coords = {"desktop": {"输入框": {"x": 1, "y": 2}},
                     "web": {"输入框": {"x": 9, "y": 9}}}
        g._active_coords()["下翻箭头"] = {"x": 5, "y": 6}   # record 写库逻辑
        assert g._coords["desktop"]["下翻箭头"] == {"x": 5, "y": 6}
        assert "下翻箭头" not in g._coords["web"], "另一套不应被写入"
    finally:
        g._state["doubao_mode"] = saved_mode
        g._coords = saved_coords
    print("[OK] record 只写当前途径套，另一套不变")


def test_save_coords_exports_nested():
    saved_coords = g._coords
    tmp = os.path.join(HERE, "_coords_tmp.json")
    captured = {}
    try:
        g._coords = {"desktop": {"输入框": {"x": 1, "y": 2}}, "web": {}}
        with mock.patch.object(g, "_coord_path", return_value=tmp), \
             mock.patch.object(g, "save_state"), \
             mock.patch.object(g.json, "dump",
                               side_effect=lambda obj, f, **kw: captured.update(data=obj)):
            g._save_coords("test_coords")
        assert "desktop" in captured["data"] and "web" in captured["data"], captured
    finally:
        g._coords = saved_coords
        if os.path.exists(tmp):
            os.remove(tmp)
    print("[OK] 导出为嵌套（含 desktop/web 两键）")


def test_import_old_format_wraps_desktop():
    parsed = g._parse_coord_data(OLD)   # import 旧内容走同一入口
    assert parsed == {"desktop": OLD, "web": {}}
    print("[OK] import 旧格式 → 自动包成 desktop")


def test_web_url():
    assert g.DOUBAO_WEB_URL == "https://www.doubao.com/chat/?channel=RSX4N"
    assert g.DOUBAO_MODES == ("web", "desktop")
    print("[OK] DOUBAO_WEB_URL 常量")


_TITLES = {1001: "豆包 - Microsoft Edge", 1002: "豆包"}
_PIDS = {1001: 1, 1002: 2}


def _fake_enum(cb, lparam):
    for hwnd in (1001, 1002):
        cb(hwnd, lparam)
    return True


def _fill(hwnd, buf, n):
    buf.value = _TITLES[hwnd]
    return len(buf.value) + 1


def _setpid(hwnd, ptr):
    # GetWindowThreadProcessId 被 mock 后丢失 argtypes 指针转换，
    # byref(pid) 收到的是 CArgObject，其 _obj 指向原 pid 对象。
    ptr._obj.value = _PIDS[hwnd]
    return True


def test_find_windows_by_mode():
    with mock.patch.object(d.user32, "EnumWindows", side_effect=_fake_enum), \
         mock.patch.object(d.user32, "IsWindowVisible", return_value=True), \
         mock.patch.object(d.user32, "GetWindowTextLengthW", return_value=20), \
         mock.patch.object(d.user32, "GetWindowTextW", side_effect=_fill), \
         mock.patch.object(d.user32, "GetWindowThreadProcessId", side_effect=_setpid), \
         mock.patch.object(d, "_process_name", side_effect=lambda pid: {1: "msedge", 2: "doubao"}[pid]):
        wins_all = d.find_doubao_windows()                  # 默认：标题或进程命中 → 2
        wins_desktop = d.find_doubao_windows(mode="desktop")  # → 2
        wins_web = d.find_doubao_windows(mode="web")          # 仅浏览器 → 1
    assert len(wins_all) == 2, wins_all
    assert len(wins_desktop) == 2, wins_desktop
    assert len(wins_web) == 1 and wins_web[0]["process"] == "msedge", wins_web
    print("[OK] find_doubao_windows(mode) 桌面2/网页1/默认同旧版")


def test_bring_front_passes_mode():
    with mock.patch.object(d, "find_doubao_windows", return_value=[]) as m_find:
        ok = d.bring_doubao_to_front(log_fn=lambda m: None, mode="web")
    assert ok is False
    assert m_find.call_args == mock.call(mode="web"), m_find.call_args
    print("[OK] bring_doubao_to_front(mode) 透传 mode 参数")


def test_load_state_migrates_mode():
    tmp = os.path.join(HERE, "_state_tmp.json")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"root": "x"}, f)
    saved_state = g._state
    try:
        with mock.patch.object(g, "STATE_FILE", tmp):
            g._state = {"root": "", "coord_file": g.DEFAULT_COORD_FILE,
                        "prompts": {}, "debug": {"enabled": False, "snapshot": {}}}
            g.load_state()
        assert g._state.get("doubao_mode") == "web", g._state
    finally:
        g._state = saved_state
        if os.path.exists(tmp):
            os.remove(tmp)
    print("[OK] load_state 缺 doubao_mode → 自动补 'web'")


if __name__ == "__main__":
    import traceback
    results = []
    tests = [test_is_old_coord_format,
             test_parse_coord_data,
             test_active_mode_and_coords,
             test_record_writes_only_active_set,
             test_save_coords_exports_nested,
             test_import_old_format_wraps_desktop,
             test_web_url,
             test_find_windows_by_mode,
             test_bring_front_passes_mode,
             test_load_state_migrates_mode]
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
