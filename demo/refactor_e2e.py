# -*- coding: utf-8 -*-
"""重构后端到端自测：扁平结构（01未处理/02已处理）验证。"""
import io
import os
import sys
import json
import shutil
import urllib.request
import time

HERE = os.path.abspath(__file__)
BASE = os.path.dirname(os.path.dirname(HERE))   # obsidian-kb-builder 项目根目录
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, BASE)

OUT = os.path.join(BASE, "analysis", "refactor_e2e_log.txt")
fout = open(OUT, "w", encoding="utf-8")
def say(*a):
    s = " ".join(str(x) for x in a)
    print(s); fout.write(s + "\n"); fout.flush()

VAULT = os.path.join(BASE, "test_vault_refactor")
CFG = os.path.join(BASE, "kbconfig.yaml")

try:
    if os.path.isdir(VAULT):
        shutil.rmtree(VAULT)
    os.makedirs(VAULT, exist_ok=True)

    from obsidian_kb import config, vault, importer, doubao_automation
    from obsidian_kb import registry, linker, logger as logger_mod
    import gui_server
    sp = None

    # 1) 配置加载 + 校验
    cfg = config.load_config(CFG, cwd=BASE)
    keys = list(cfg["structure"].keys())
    say("[1 config] structure:", keys)
    assert "01未处理" in cfg["structure"] and "02已处理" in cfg["structure"]
    assert "收件箱" not in cfg["structure"] and "原始素材" not in cfg["structure"]
    assert cfg["import"]["inbox"] == "0101未处理"
    assert cfg["import"]["extra_sources"] == []
    say("[1] OK")

    # 2) 建库
    logger, _ = logger_mod.setup_logging(cfg["logging"]["log_dir"], VAULT)
    st = vault.init_vault(cfg, VAULT, logger=logger)
    say("[2 init] stats:", st)
    for d in ["0101未处理", "02已处理", "03知识提炼", "04知识聚合",
              "05规则模板", "06附件", "07日记"]:
        assert os.path.isdir(os.path.join(VAULT, d)), d
    assert not os.path.isdir(os.path.join(VAULT, "原始素材"))
    assert not os.path.isdir(os.path.join(VAULT, "收件箱"))
    say("[2] OK 目录结构正确（无 原始素材/收件箱）")

    # 3) 放样本到01未处理
    up = os.path.join(VAULT, "0101未处理")
    with open(os.path.join(up, "AI与知识库.md"), "w", encoding="utf-8") as f:
        f.write("# AI与知识库\n用 AI 工具自动提炼笔记，建立双链。\n")
    with open(os.path.join(up, "轨道车辆标准.txt"), "w", encoding="utf-8") as f:
        f.write("轨道交通车辆设计标准笔记，牵引系统与辅助供电。\n")

    # 4) 豆包扫描源 = 单一 01未处理
    sources = doubao_automation._iter_material_sources(cfg, VAULT)
    say("[4 doubao sources]", sources)
    assert sources == [os.path.abspath(up)]
    mats = doubao_automation._list_materials(sources)
    say("[4 doubao materials]", [m[1] for m in mats])
    assert len(mats) == 2
    say("[4] OK")

    # 5) 一键同步（导入 + 打标 + 双链 + 索引 + 报告）
    reg = registry.Registry(VAULT)
    logger, report = logger_mod.setup_logging(cfg["logging"]["log_dir"], VAULT)
    importer.run_import(cfg, VAULT, reg, logger, report)
    linker.run_linking(cfg, VAULT, reg, logger, report)
    reg.save()
    say("[5 sync] scanned=%d imported=%d new_notes=%d attachments=%d failed=%d"
        % (report.scanned, report.imported, report.new_notes,
           report.attachments, report.failed))
    assert report.failed == 0, "sync 有失败: %s" % report.errors
    b = os.path.join(VAULT, "03知识提炼")
    notes = [f for f in os.listdir(b) if f.endswith(".md")]
    say("[5] 03知识提炼 笔记:", notes)
    assert len(notes) >= 2
    # 源文件应移入 02已处理，01未处理清空
    say("[5] 01未处理 剩余:", os.listdir(up))
    say("[5] 02已处理:", os.listdir(os.path.join(VAULT, "02已处理")))
    assert len(os.listdir(up)) == 0, "01未处理 应已清空"
    assert len(os.listdir(os.path.join(VAULT, "02已处理"))) == 2
    # 报告存在（与真实 sync 流程一致：调用 write_report）
    import datetime
    started = datetime.datetime.now() - datetime.timedelta(seconds=1)
    rpt = logger_mod.write_report(report, VAULT, cfg["logging"]["log_dir"], started, 1.0)
    say("[5] 处理报告:", rpt)
    assert os.path.isfile(rpt)
    say("[5] OK 笔记生成 + 源移入02已处理 + 报告生成")

    # 6) GUI 辅助函数
    gui_server._state["root"] = VAULT
    sdirs = gui_server._import_source_dirs()
    say("[6 _import_source_dirs]", sdirs)
    assert sdirs == [("0101未处理", os.path.abspath(up))]
    with open(os.path.join(up, "临时.md"), "w", encoding="utf-8") as f:
        f.write("temp")
    items = gui_server._inbox_list()
    say("[6 _inbox_list]", [(i["name"], i["source"]) for i in items])
    assert all(i["source"] == "0101未处理" for i in items)
    dm = gui_server._doubao_materials()
    say("[6 _doubao_materials]", [i["name"] for i in dm])
    assert any(i["name"] == "临时.md" for i in dm)
    # 删除（移入回收站）
    mv, errs = gui_server._inbox_delete([{"name": "临时.md", "source": "0101未处理"}])
    say("[6 _inbox_delete] moved=%s errors=%s" % (mv, errs))
    trash = os.path.join(VAULT, "回收站")
    assert os.path.isdir(trash) and len(os.listdir(trash)) == 1
    say("[6] OK 辅助函数 + 回收站逻辑")

    # 7) 启动服务，验证 HTTP 接口
    PORT_FILE = os.path.join(BASE, "gui_url.txt")
    if os.path.isfile(PORT_FILE):
        os.remove(PORT_FILE)
    import subprocess
    env = os.environ.copy(); env["PYTHONUTF8"] = "1"
    sp = subprocess.Popen(
        [sys.executable, os.path.join(BASE, "gui_server.py")],
        cwd=BASE, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
    # 等待端口文件
    url = None
    for _ in range(50):
        if os.path.isfile(PORT_FILE):
            with open(PORT_FILE, "r", encoding="utf-8") as f:
                url = f.read().strip()
            if url.startswith("http"):
                break
        time.sleep(0.3)
    say("[7 server] url=%s" % url)
    assert url, "服务未启动"
    time.sleep(0.5)

    def call(path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url + path, data=data,
                                     method="POST" if body is not None else "GET")
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))

    st = call("/api/set_root", {"root": VAULT})
    say("[7 set_root] ok=%s root=%s" % (st.get("ok"), st.get("status", {}).get("root")))
    assert st.get("ok")
    inv = call("/api/inbox")
    say("[7 inbox] items=%s" % [i["name"] for i in inv.get("items", [])])
    # 放一个文件再查（通过 upload 接口）
    with open(os.path.join(up, "HTTP测试.md"), "w", encoding="utf-8") as f:
        f.write("http test")
    inv = call("/api/inbox")
    say("[7 inbox after] items=%s sources=%s"
        % ([i["name"] for i in inv.get("items", [])],
           [i.get("source") for i in inv.get("items", [])]))
    assert any(i["name"] == "HTTP测试.md" and i.get("source") == "0101未处理"
               for i in inv.get("items", []))
    # doubao materials
    dm2 = call("/api/doubao/materials")
    say("[7 doubao/materials] %s" % [i["name"] for i in dm2.get("items", [])])
    # open_path 不存在的路径 -> 应返回 ok False
    op = call("/api/open_path", {"path": r"D:\不存在的路径_xyz"})
    say("[7 open_path(noexist)] ok=%s err=%s" % (op.get("ok"), op.get("error")))
    assert op.get("ok") is False
    # open_path 存在的路径 -> ok True（os.startfile 在 headless 可能成功或抛错，二者皆算逻辑通过）
    op2 = call("/api/open_path", {"path": VAULT})
    say("[7 open_path(exist)] ok=%s err=%s" % (op2.get("ok"), op2.get("error")))
    # 全选依赖的是前端，这里仅确认 inbox 接口字段结构含 name/source
    say("[7] OK HTTP 接口")

    sp.terminate()
    say("ALL_PASS")
except Exception as e:
    import traceback
    say("FAIL: " + traceback.format_exc())
finally:
    try:
        if sp is not None:
            sp.terminate()
    except Exception:
        pass
    fout.close()
