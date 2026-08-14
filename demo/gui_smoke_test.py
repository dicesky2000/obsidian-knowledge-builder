# -*- coding: utf-8 -*-
"""知识库助手 GUI 服务端到端自测（模拟前端点击流程）。"""
import base64
import io
import json
import os
import sys
import time
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # obsidian-kb-builder 项目根目录
BASE = "http://127.0.0.1:8765"
_URL_FILE = os.path.join(_PROJ, "gui_url.txt")
if os.path.isfile(_URL_FILE):
    with open(_URL_FILE, "r", encoding="utf-8") as f:
        _u = f.read().strip()
    if _u.startswith("http"):
        BASE = _u.rstrip("/")
VAULT = os.path.join(_PROJ, "test_vault_gui")
OUT = os.path.join(_PROJ, "analysis", "gui_test_log.txt")
fout = open(OUT, "w", encoding="utf-8")


def say(*a):
    line = " ".join(str(x) for x in a)
    print(line)
    fout.write(line + "\n")
    fout.flush()


def api(path, body=None, method=None):
    url = BASE + path
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method or ("POST" if body is not None else "GET"))
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def wait_busy(desc, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = api("/api/status")
        if not st.get("busy"):
            return st
        time.sleep(1)
    raise RuntimeError("超时: " + desc)


def main():
    # 0. 首页
    try:
        with urllib.request.urlopen(BASE + "/", timeout=10) as r:
            html = r.read().decode("utf-8")
        say("[首页] 返回 %d 字节，含「发送格式」文本框: %s" % (len(html), "promptSend" in html))
    except Exception as e:
        say("[首页] 失败: %s" % e)
        return 1

    # 1. 设置知识库位置
    st = api("/api/set_root", {"root": VAULT})
    say("[1 设置位置] ok=%s root=%s" % (st.get("ok"), st.get("status", {}).get("root")))

    # 2. 创建知识库
    api("/api/init", {})
    wait_busy("init")
    say("[2 创建知识库] 完成")

    # 3. 上传两个文件到01未处理
    c1 = "人工智能大模型与知识库结合，用 AI 工具自动提炼笔记，建立双链。".encode("utf-8")
    c2 = "轨道交通车辆设计标准笔记，牵引系统与辅助供电。".encode("utf-8")
    payload = {"files": [
        {"name": "AI与知识库.md", "data": base64.b64encode(c1).decode()},
        {"name": "轨道车辆标准.txt", "data": base64.b64encode(c2).decode()},
    ]}
    r = api("/api/upload", payload)
    say("[3 上传] saved=%s items=%d" % (r.get("saved"), len(r.get("items", []))))

    # 3.1 01未处理列表
    r = api("/api/inbox")
    names = [it["name"] for it in r.get("items", [])]
    say("[3.1 01未处理列表] %s" % names)

    # 3.2 删除其中一个（应移入 _kb_回收站，可找回）
    r = api("/api/inbox/delete", {"items": [{"name": "轨道车辆标准.txt", "source": "01未处理"}]})
    say("[3.2 删除] moved=%s 剩余=%s" % (r.get("moved"),
         [it["name"] for it in r.get("items", [])]))
    trash = os.path.join(VAULT, "_kb_回收站")
    say("[3.2 回收站] 目录存在=%s 文件数=%d" % (
        os.path.isdir(trash),
        len(os.listdir(trash)) if os.path.isdir(trash) else 0))

    # 4. 保存提示词格式
    r = api("/api/prompts", {"send_format": "测试发送格式：{素材内容}"})
    say("[4 提示词] ok=%s" % r.get("ok"))
    r = api("/api/status")
    p = r.get("prompts", {})
    say("[4 提示词回填] send=%s" % p.get("send_format"))
    say("[4.2 只读参考] gen_spec 长度=%d（应>0）" % len(r.get("gen_spec", "")))

    # 4.1 GUI 已取消一键同步，改用命令行 sync 生成产物供校验
    import subprocess
    cp = subprocess.run(
        [sys.executable, os.path.join(_PROJ, "run.py"), "sync", "--root", VAULT],
        cwd=_PROJ, capture_output=True, text=True, encoding="utf-8", errors="replace")
    say("[4.1 CLI sync] rc=%d" % cp.returncode)

    # 5. 最近结果摘要
    r = api("/api/report")
    summary = r.get("summary", {})
    say("[5 报告摘要] %s" % json.dumps(summary, ensure_ascii=False))

    # 6. 调试模式开关（开启→快照；关闭→清空）
    r = api("/api/debug/toggle", {"enabled": True})
    d = (r.get("status") or {}).get("debug", {})
    say("[6 调试开启] enabled=%s snapshot=%s" % (d.get("enabled"), d.get("snapshot")))
    r = api("/api/debug/reset", {})   # 开启状态下复位应被受理
    say("[6 复位受理] ok=%s" % r.get("ok"))
    r = api("/api/debug/toggle", {"enabled": False})
    d = (r.get("status") or {}).get("debug", {})
    say("[6 调试关闭] enabled=%s snapshot=%s" % (d.get("enabled"), d.get("snapshot")))
    r = api("/api/debug/reset", {})   # 关闭状态下复位应被拒绝
    say("[6 复位拒绝] ok=%s err=%s" % (r.get("ok"), r.get("error", "")))

    # 7. 日志
    r = api("/api/logs?after=0")
    say("[7 日志] 共 %d 条，末条: %s" % (len(r.get("items", [])), r.get("items", [{}])[-1].get("text", "")))

    # 7. 退出
    api("/api/exit", {})
    say("[7 退出] 已发送")

    # 校验产物（笔记带日期前缀，用前缀匹配）
    b_dir = os.path.join(VAULT, "03知识提炼")
    ai_note = [f for f in os.listdir(b_dir) if f.endswith("AI与知识库.md")]
    checks = [
        os.path.isdir(b_dir),
        len(ai_note) == 1,
        os.path.isfile(os.path.join(VAULT, "处理日志", "处理报告.md")),
    ]
    say("[校验] 03知识提炼目录/AI笔记/报告: %s" % checks)
    say("ALL DONE")
    return 0


if __name__ == "__main__":
    code = main()
    fout.close()
    sys.exit(code)
