# -*- coding: utf-8 -*-
"""迁移 _tt_vault_m12 到新扁平结构（未处理/已处理）。

步骤（安全、幂等、可回溯）：
  1. 先整库备份为 _tt_vault_m12.bak；
  2. 在库根新建 未处理/、已处理/；
  3. 把旧 收件箱 / 原始素材/未处理 / 原始素材/未收录 的文件并入 未处理，
     把 原始素材/已处理 的文件并入 已处理（同名冲突自动加 _mig 后缀）；
  4. 仅当上述旧目录变空后才删除，绝不删除非空目录。
"""
import os
import shutil
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# 要迁移的知识库根目录：改成你自己的库路径；支持相对路径（在库所在目录运行本脚本）
V = r"_tt_vault_m12"
BAK = V + ".bak"


def log(*a):
    print(" ".join(str(x) for x in a))


# 1) 备份
if not os.path.exists(BAK):
    shutil.copytree(V, BAK)
    log("[备份] ->", BAK)
else:
    log("[备份] 已存在，跳过:", BAK)

# 2) 新根级目录
os.makedirs(os.path.join(V, "未处理"), exist_ok=True)
os.makedirs(os.path.join(V, "已处理"), exist_ok=True)
log("[新建] 未处理/ 已处理/")

# 3) 文件并入（防御式；预期均为空）
moves = [
    (os.path.join(V, "收件箱"), os.path.join(V, "未处理")),
    (os.path.join(V, "原始素材", "未处理"), os.path.join(V, "未处理")),
    (os.path.join(V, "原始素材", "未收录"), os.path.join(V, "未处理")),
    (os.path.join(V, "原始素材", "已处理"), os.path.join(V, "已处理")),
]
for src, dst in moves:
    if not os.path.isdir(src):
        continue
    for fn in os.listdir(src):
        sp = os.path.join(src, fn)
        if not os.path.isfile(sp):
            continue
        dp = os.path.join(dst, fn)
        if os.path.exists(dp):
            base, ext = os.path.splitext(fn)
            dp = os.path.join(dst, base + "_mig" + ext)
        shutil.move(sp, dp)
        log("[并入] %s -> %s" % (os.path.relpath(sp, V), os.path.relpath(dp, V)))

# 4) 删除已空的旧目录
for d in [os.path.join(V, "收件箱"),
          os.path.join(V, "原始素材", "未处理"),
          os.path.join(V, "原始素材", "未收录"),
          os.path.join(V, "原始素材", "已处理"),
          os.path.join(V, "原始素材")]:
    if os.path.isdir(d) and not os.listdir(d):
        os.rmdir(d)
        log("[删除空目录]", os.path.relpath(d, V))

log("[完成] 新结构顶层：")
for e in sorted(os.listdir(V)):
    full = os.path.join(V, e)
    tag = "<DIR>" if os.path.isdir(full) else ""
    log("  ", e, tag)
