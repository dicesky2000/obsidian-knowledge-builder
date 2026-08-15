# -*- coding: utf-8 -*-
"""验证「轮询复制新内容」修复：剪贴板清空基准 + _wait_new_reply 轮询逻辑。"""
import os
import sys
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "obsidian_kb"))

import doubao_automation as d


def test_clipboard_clear():
    d.clipboard_set_text("hello world")
    assert d.clipboard_get_text().strip() == "hello world"
    d.clipboard_set_text("")   # 清空剪贴板
    assert d.clipboard_get_text().strip() == ""
    print("[OK] clipboard_set_text('') 清空后读回为空")


def test_wait_new_reply_found():
    fake = mock.MagicMock()
    # 第一次复制读回空（豆包未生成，剪贴板已清空）-> 下翻继续；第二次读回"新回复"-> 成功
    seq = iter(["", "新回复内容"])
    with mock.patch.object(d, "clipboard_get_text", side_effect=lambda: next(seq)), \
         mock.patch.object(d, "_move_to"), mock.patch.object(d, "_click"), \
         mock.patch.object(d, "time",
                           mock.MagicMock(time=lambda: 1000.0, sleep=lambda s: None)), \
         mock.patch.object(d.user32, "GetAsyncKeyState", return_value=0):
        r = d._wait_new_reply((100, 100), (200, 200), "", None, 30, fake)
    assert r == "新回复内容", "应返回复制到的新内容，实际=%r" % r
    print("[OK] _wait_new_reply 空->新回复 判定成功")


def test_wait_new_reply_interrupt():
    fake = mock.MagicMock()
    with mock.patch.object(d.user32, "GetAsyncKeyState", return_value=0x8000), \
         mock.patch.object(d, "_move_to"), mock.patch.object(d, "_click"), \
         mock.patch.object(d, "clipboard_get_text", return_value=""):
        r = d._wait_new_reply((100, 100), (200, 200), "", None, 30, fake)
    assert r is None, "Esc 应返回 None(中断)，实际=%r" % r
    print("[OK] _wait_new_reply Esc 中断 -> None")


if __name__ == "__main__":
    import traceback
    results = []
    try:
        test_clipboard_clear()
        test_wait_new_reply_found()
        test_wait_new_reply_interrupt()
        results.append("ALL PASS")
    except Exception as e:
        results.append("FAIL: " + repr(e))
        results.append(traceback.format_exc())
    text = "\n".join(results)
    with open(os.path.join(HERE, "test_result.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
