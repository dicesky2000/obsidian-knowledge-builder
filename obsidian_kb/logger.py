# -*- coding: utf-8 -*-
"""日志与处理报告。

- 控制台 INFO 级日志 + 文件 DEBUG 级日志（处理日志/kb_YYYYMMDD.log）；
- Report 收集运行统计（扫描数/导入数/新增笔记数/跳过数/失败数/标签/关联/索引），
  运行结束生成 Markdown 版《处理报告》，方便核对。
"""
import datetime
import logging
import os
import sys
from collections import Counter, OrderedDict
from typing import Dict, List, Optional, Tuple


class Report(object):
    """一次运行的统计与明细。"""

    def __init__(self) -> None:
        self.scanned = 0            # 扫描到的文件数
        self.imported = 0           # 成功导入/处理的文件数
        self.new_notes = 0          # 新生成的笔记数
        self.updated_notes = 0      # 更新了 frontmatter/链接的已有笔记数
        self.skipped = 0            # 跳过（重复/不支持）数
        self.skipped_dups = 0       # 其中：重复文件数
        self.failed = 0             # 失败数
        self.attachments = 0        # 归档到附件的文件数
        self.indexes = 0            # 生成的索引笔记数
        self.tags_generated: Counter = Counter()   # tag -> 次数
        self.links_added = 0        # 新增双链数
        self.relations: List[Tuple[str, str]] = []  # (来源笔记, 目标笔记)
        self.errors: List[str] = []
        self.details: List[str] = []  # 逐条处理明细行

    def add_detail(self, level: str, msg: str) -> None:
        self.details.append("[%s] %s" % (level, msg))

    def to_markdown(self, vault_root: str, started: datetime.datetime,
                    duration: float) -> str:
        lines: List[str] = []
        lines.append("# 知识库处理报告")
        lines.append("")
        lines.append("- 生成时间：%s" % started.strftime("%Y-%m-%d %H:%M:%S"))
        lines.append("- 知识库根：`%s`" % vault_root)
        lines.append("- 运行耗时：%.2f 秒" % duration)
        lines.append("")
        lines.append("## 一、运行统计")
        lines.append("")
        lines.append("| 指标 | 数值 |")
        lines.append("| --- | --- |")
        stats = [
            ("扫描文件数", self.scanned),
            ("导入文件数", self.imported),
            ("新增笔记数", self.new_notes),
            ("更新笔记数", self.updated_notes),
            ("归档附件数", self.attachments),
            ("跳过文件数（其中重复 %d）" % self.skipped_dups, self.skipped),
            ("失败文件数", self.failed),
            ("新增双链数", self.links_added),
            ("生成索引页数", self.indexes),
        ]
        for name, val in stats:
            lines.append("| %s | %d |" % (name, val))
        lines.append("")
        lines.append("## 二、自动生成的标签")
        lines.append("")
        if self.tags_generated:
            for tag, cnt in self.tags_generated.most_common():
                lines.append("- `#%s` × %d" % (tag, cnt))
        else:
            lines.append("（本次运行未生成新标签）")
        lines.append("")
        lines.append("## 三、建立的关联关系")
        lines.append("")
        if self.relations:
            for src, dst in self.relations:
                lines.append("- [[%s]] ↔ [[%s]]" % (src, dst))
        else:
            lines.append("（本次运行未建立新关联）")
        lines.append("")
        lines.append("## 四、处理明细")
        lines.append("")
        if self.details:
            for d in self.details[:500]:
                lines.append("- %s" % d)
        else:
            lines.append("（无）")
        lines.append("")
        if self.errors:
            lines.append("## 五、错误信息")
            lines.append("")
            for e in self.errors:
                lines.append("- ⚠ %s" % e)
            lines.append("")
        return "\n".join(lines)


def setup_logging(log_dir: str, vault_root: str,
                  console_level: str = "INFO") -> Tuple[logging.Logger, Report]:
    """初始化日志系统，返回 (logger, report)。"""
    logger = logging.getLogger("kb")
    logger.setLevel(logging.DEBUG)
    logger.handlers = []  # 清空，避免重复添加

    # 统一 stdout/stderr 为 UTF-8，规避 Windows 控制台/管道默认 GBK 导致的中文乱码
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    fmt_console = logging.Formatter("%(levelname)s %(message)s")
    ch = logging.StreamHandler()
    ch.setLevel(getattr(logging, console_level.upper(), logging.INFO))
    ch.setFormatter(fmt_console)
    logger.addHandler(ch)

    log_dir_abs = os.path.join(vault_root, log_dir)
    # 兼容旧版：若仍存在旧的 _kb_logs 目录，重命名为新的日志目录（处理日志）
    old_log_abs = os.path.join(vault_root, "_kb_logs")
    if os.path.isdir(old_log_abs) and not os.path.exists(log_dir_abs):
        try:
            os.rename(old_log_abs, log_dir_abs)
        except Exception:
            pass
    os.makedirs(log_dir_abs, exist_ok=True)
    fh = logging.FileHandler(
        os.path.join(log_dir_abs, "kb_%s.log" % datetime.datetime.now().strftime("%Y%m%d")),
        encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [%(module)s] %(message)s"))
    logger.addHandler(fh)

    report = Report()
    return logger, report


def write_report(report: Report, vault_root: str, log_dir: str,
                 started: datetime.datetime, duration: float) -> str:
    """把报告写入 处理日志/处理报告_YYYYMMDD_HHMMSS.md 与 处理报告.md（最新）。"""
    md = report.to_markdown(vault_root, started, duration)
    log_dir_abs = os.path.join(vault_root, log_dir)
    os.makedirs(log_dir_abs, exist_ok=True)
    stamp = started.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(log_dir_abs, "处理报告_%s.md" % stamp)
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    # 覆盖“最新报告”，便于随时查看
    with open(os.path.join(log_dir_abs, "处理报告.md"), "w", encoding="utf-8") as f:
        f.write(md)
    return path
