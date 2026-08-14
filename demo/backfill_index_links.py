# -*- coding: utf-8 -*-
"""为既有 B 层笔记补写「索引笔记」双向链接（历史库回填工具，幂等）。

用法：
  python demo/backfill_index_links.py --root <vault>                         # 指定库根（必填）
  python demo/backfill_index_links.py --root <vault> --b-dir <dir> --index-dir <dir>   # 自定义目录
  python demo/backfill_index_links.py --root <vault> --dry-run               # 只统计，不写文件

原理：
  1. 用 obsidian_kb.config.load_config 加载标准配置，深覆盖 structure.B_知识提炼 / B_索引；
  2. 调 linker.run_linking()：按修复后引擎的幂等逻辑，只补缺失的「原始素材」与
     「- [[索引_x]] — 共享关键词：…」行（已有链接经 existing_links + 全文去重跳过）；
  3. 调 linker.generate_indexes()：刷新/生成 索引_<kw>.md 与 索引_分类_<cat>.md；
  4. 历史库无注册表 → registry 传 None（run_linking 内部 try/except 兜底）。
"""
import argparse
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJ = os.path.dirname(_HERE)
sys.path.insert(0, _PROJ)

from obsidian_kb import config as config_mod   # noqa: E402
from obsidian_kb import linker                 # noqa: E402
from obsidian_kb import logger as logger_mod   # noqa: E402

# 其他库用命令行 --root / --b-dir / --index-dir 覆盖
DEFAULT_B_DIR = "辅助系统设计"
DEFAULT_INDEX_DIR = "知识提炼/索引笔记"


def build_cfg(b_dir: str, index_dir: str) -> dict:
    cfg = config_mod.load_config(None, cwd=_PROJ)
    cfg["structure"]["B_知识提炼"] = b_dir
    cfg["structure"]["B_索引"] = index_dir
    return cfg


def main() -> int:
    ap = argparse.ArgumentParser(description="为既有 B 层笔记补写索引双向链接")
    ap.add_argument("--root", required=True, help="知识库根目录（必填）")
    ap.add_argument("--b-dir", default=DEFAULT_B_DIR, help="B 层笔记目录（相对库根）")
    ap.add_argument("--index-dir", default=DEFAULT_INDEX_DIR, help="索引笔记目录（相对库根）")
    ap.add_argument("--dry-run", action="store_true", help="只统计，不写任何文件")
    args = ap.parse_args()

    vault_root = os.path.abspath(args.root)
    if not os.path.isdir(vault_root):
        print("[错误] 知识库根不存在：%s" % vault_root)
        return 1

    cfg = build_cfg(args.b_dir, args.index_dir)
    logger, report = logger_mod.setup_logging(
        cfg["logging"].get("log_dir", "处理日志"), vault_root)
    logger.info("知识库根：%s", vault_root)
    logger.info("B 层目录：%s，索引目录：%s", args.b_dir, args.index_dir)

    if args.dry_run:
        notes = linker.collect_notes(cfg, vault_root, include_c=False)
        missing = 0
        for n in notes:
            for kw in n.raw_keywords:
                if ("[[" + "索引_%s" % linker._safe_key(kw) + "]]") not in n.content:
                    missing += 1
            for cat in n.categories:
                if ("[[" + "索引_分类_%s" % linker._safe_key(cat) + "]]") not in n.content:
                    missing += 1
        logger.info("[dry-run] %d 篇 B 层笔记，预计补写 %d 条索引链接", len(notes), missing)
        return 0

    # registry=None：不读写注册表文件；run_linking 内部对 registry 有 try/except 兜底
    linker.run_linking(cfg, vault_root, None, logger, report)
    linker.generate_indexes(cfg, vault_root, logger, report)
    logger.info("回填完成：新增双链 %d 条，更新笔记 %d 篇，索引页 %d 个",
                report.links_added, report.updated_notes, report.indexes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
