# -*- coding: utf-8 -*-
"""命令行接口。

命令一览：
  python run.py init [--config P] [--root R] [--force]
  python run.py import [SRC] [--config P] [--dry-run] [--no-move] [--no-dedupe] [--root R]
  python run.py link  [--config P] [--root R]
  python run.py sync  [--config P] [--dry-run] [--no-move] [--no-dedupe] [--root R]
  python run.py watch [--config P] [--interval MIN]
  python run.py schedule {install,uninstall} [--config P] [--time HH:MM] [--name N]
  python run.py report [--config P]
  python run.py --version
"""
import argparse
import datetime
import logging
import os
import sys
from typing import Any, Dict, List, Optional

from . import __version__
from . import config as config_mod
from . import frontmatter, importer, linker, logger as logger_mod, registry
from . import scheduler as scheduler_mod, vault as vault_mod


def _load(cfg_path: Optional[str]) -> Dict[str, Any]:
    return config_mod.load_config(cfg_path, cwd=os.getcwd())


def _root(cfg: Dict[str, Any], root: Optional[str]) -> str:
    if root:
        return os.path.abspath(root)
    return config_mod.resolve_vault_root(cfg, os.getcwd())


def _sync(cfg: Dict[str, Any], vault_root: str, cfg_path: Optional[str],
          dry_run: bool = False, no_move: bool = False,
          no_dedupe: bool = False) -> None:
    """导入 + 打标 + 双链 + MOC + 报告（sync 的实质）。"""
    logger, report = logger_mod.setup_logging(
        cfg["logging"].get("log_dir", "处理日志"), vault_root)
    started = datetime.datetime.now()
    logger.info("========== Obsidian 知识库同步开始 ==========")
    logger.info("知识库根：%s", vault_root)
    if cfg_path:
        logger.info("配置文件：%s", cfg_path)

    reg = registry.Registry(vault_root)
    importer.run_import(cfg, vault_root, reg, logger, report,
                        dry_run=dry_run, no_move=no_move, no_dedupe=no_dedupe)
    if not dry_run:
        linker.run_linking(cfg, vault_root, reg, logger, report)
        linker.generate_mocs(cfg, vault_root, logger, report)
        reg.save()

    duration = (datetime.datetime.now() - started).total_seconds()
    if not dry_run:
        path = logger_mod.write_report(report, vault_root,
                                       cfg["logging"].get("log_dir", "处理日志"),
                                       started, duration)
        logger.info("处理报告：%s", path)
    else:
        logger.info("（dry-run 模式：未写入任何文件）")
    logger.info("========== 同步结束，耗时 %.1f 秒 ==========", duration)


def cmd_init(args: argparse.Namespace) -> int:
    cfg = _load(args.config)
    root = _root(cfg, args.root)
    logger, _ = logger_mod.setup_logging(
        cfg["logging"].get("log_dir", "处理日志"), root)
    logger.info("知识库根：%s", root)
    vault_mod.init_vault(cfg, root, logger=logger, force=args.force)
    logger.info("完成。用 Obsidian「打开本地仓库文件夹」选择 %s 即可", root)
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    cfg = _load(args.config)
    root = _root(cfg, args.root)
    logger, report = logger_mod.setup_logging(
        cfg["logging"].get("log_dir", "处理日志"), root)
    started = datetime.datetime.now()
    reg = registry.Registry(root)
    importer.run_import(cfg, root, reg, logger, report, src=args.src,
                        dry_run=args.dry_run, no_move=args.no_move,
                        no_dedupe=args.no_dedupe)
    reg.save()
    if not args.dry_run:
        duration = (datetime.datetime.now() - started).total_seconds()
        path = logger_mod.write_report(report, root,
                                       cfg["logging"].get("log_dir", "处理日志"),
                                       started, duration)
        logger.info("处理报告：%s", path)
    return 0


def cmd_link(args: argparse.Namespace) -> int:
    cfg = _load(args.config)
    root = _root(cfg, args.root)
    logger, report = logger_mod.setup_logging(
        cfg["logging"].get("log_dir", "处理日志"), root)
    started = datetime.datetime.now()
    reg = registry.Registry(root)
    linker.run_linking(cfg, root, reg, logger, report)
    linker.generate_mocs(cfg, root, logger, report)
    reg.save()
    duration = (datetime.datetime.now() - started).total_seconds()
    path = logger_mod.write_report(report, root,
                                   cfg["logging"].get("log_dir", "处理日志"),
                                   started, duration)
    logger.info("处理报告：%s", path)
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    cfg = _load(args.config)
    root = _root(cfg, args.root)
    _sync(cfg, root, args.config, dry_run=args.dry_run,
          no_move=args.no_move, no_dedupe=args.no_dedupe)
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    cfg = _load(args.config)
    root = _root(cfg, args.root)
    logger, _ = logger_mod.setup_logging(
        cfg["logging"].get("log_dir", "处理日志"), root)
    scheduler_mod.watch_loop(cfg, root, logger, lambda: _sync(cfg, root, args.config),
                             interval_minutes=args.interval)
    return 0


def cmd_schedule(args: argparse.Namespace) -> int:
    cfg = _load(args.config)
    root = _root(cfg, args.root)
    logger, _ = logger_mod.setup_logging(
        cfg["logging"].get("log_dir", "处理日志"), root)
    if args.action == "install":
        info = scheduler_mod.install_task(cfg, root, logger,
                                          config_path=args.config,
                                          task_time=args.time)
        print("生成的批处理：", info["bat"])
        print("注册命令：", info["cmd"] or "（无）")
        print("执行结果：", info["output"])
    else:
        print(scheduler_mod.uninstall_task(cfg, logger))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    cfg = _load(args.config)
    root = _root(cfg, args.root)
    report_path = os.path.join(root, cfg["logging"].get("log_dir", "处理日志"),
                               "处理报告.md")
    if os.path.isfile(report_path):
        with open(report_path, "r", encoding="utf-8") as f:
            sys.stdout.write(f.read())
    else:
        print("暂无处理报告：%s（先运行 python run.py sync）" % report_path)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="obsidian-kb",
        description="兵哥式 Obsidian 知识库自动化搭建工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    parser.add_argument("--version", action="version", version="obsidian-kb %s" % __version__)
    sub = parser.add_subparsers(dest="command")

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--config", default=None,
                       help="配置文件路径（默认找当前目录 kbconfig.yaml/json）")
        p.add_argument("--root", default=None, help="知识库根目录（覆盖配置）")

    p = sub.add_parser("init", help="创建标准知识库结构")
    add_common(p)
    p.add_argument("--force", action="store_true", help="覆盖已存在的模板文件")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("import", help="批量导入（可指定导入源目录）")
    p.add_argument("src", nargs="?", default=None, help="导入源目录（默认配置的未处理）")
    add_common(p)
    p.add_argument("--dry-run", action="store_true", help="只演练，不写文件")
    p.add_argument("--no-move", action="store_true", help="导入后不移走源文件")
    p.add_argument("--no-dedupe", action="store_true", help="关闭内容去重")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("link", help="仅执行双链引擎 + MOC 索引")
    add_common(p)
    p.set_defaults(func=cmd_link)

    p = sub.add_parser("sync", help="一次性同步：导入+打标+双链+MOC+报告")
    add_common(p)
    p.add_argument("--dry-run", action="store_true", help="只演练，不写文件")
    p.add_argument("--no-move", action="store_true", help="导入后不移走源文件")
    p.add_argument("--no-dedupe", action="store_true", help="关闭内容去重")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("watch", help="内置循环定时同步")
    add_common(p)
    p.add_argument("--interval", type=int, default=None,
                   help="轮询间隔（分钟），默认取配置 scheduler.interval_minutes")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("schedule", help="Windows 计划任务注册/卸载")
    p.add_argument("action", choices=["install", "uninstall"])
    add_common(p)
    p.add_argument("--time", default=None, help="每日执行时间 HH:MM（默认 09:00）")
    p.set_defaults(func=cmd_schedule)

    p = sub.add_parser("report", help="查看最新处理报告")
    add_common(p)
    p.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
