# -*- coding: utf-8 -*-
"""建库模块：一键创建标准 Obsidian 知识库。

按配置 [structure] 顺序创建目录（默认：未处理/已处理 + 兵哥四层架构 B/C/D + 附件/日记），
并写入：
  - .obsidian/app.json（附件目录、新笔记位置等基础设置）
  - D 层「规则模板」：豆包知识提炼提示词、笔记格式规范（用户可随时改，程序每次读最新）
  - README.md 库说明
  - B 层示范笔记（含 [[双链]]，供 Obsidian 图谱预览）
  - 日记/模板.md

幂等：已存在的目录/文件默认跳过，--force 可覆盖。
"""
import json
import os
from typing import Any, Dict, List, Optional

from . import frontmatter

OBSIDIAN_APP_JSON = {
    "attachmentFolderPath": "附件",
    "newFileLocation": "folder",
    "newFileFolderPath": "日记",
    "useMarkdownLinks": False,
    "showLineNumber": True,
}

PROMPT_TEMPLATE = """# 豆包知识提炼提示词（D 层 · 可自行修改）

> 本文件属于知识库「D 层规则模板」。程序每次运行都会重新读取，修改后无需改程序。
> 用途：把素材（未处理）交给 AI（豆包等）提炼为 B 层标准化知识笔记。

【任务】
请把下面的原始素材提炼为一篇结构化的知识笔记，输出 Markdown 格式。

【输出结构】
# 标题（概括核心主题）
## 核心观点（3~5 条要点，每条一句话）
## 关键概念（列出术语并一句话解释）
## 延伸思考（2~3 条，结合其他领域）
## 行动清单（可选）

【要求】
- 用简体中文，语言精炼、客观，不夸大；
- 保留素材中的关键数字与专有名词；
- 结尾给出 3~5 个关键词，用 #标签 形式列出，方便建立双向链接与图谱。

【原始素材】
{素材内容}
"""

FORMAT_SPEC = """# 笔记格式规范（D 层 · 可自行修改）

本知识库采用「兵哥四层架构」：

| 层级 | 目录 | 角色 | 谁负责 |
| --- | --- | --- | --- |
| 输入层 | 未处理/ | 待整理素材的入口：链接、灵感、摘抄 | 用户扔进去（程序辅助整理） |
| 归档层 | 已处理/ | 导入/提炼完成后源文件归档处 | 程序自动移入 |
| B 层 | 知识提炼/ | 标准化知识笔记，自动建双链 | 程序导入/AI 提炼生成 |
| C 层 | 知识聚合/ | 创作空间：文章、方案、总结 | 用户自己（程序不碰） |
| D 层 | 规则模板/ | 提示词与格式规范 | 用户维护，程序读取执行 |

## 笔记 frontmatter 规范

```yaml
---
title: 笔记标题
tags: [标签1, 标签2]
created: 2026-08-14 15:00
updated: 2026-08-14 15:30
source: 原始来源
category: 自动归类
status: 待整理
---
```

## 双链约定

- 笔记之间用 `[[笔记名]]` 建立关联；
- 程序自动维护每篇笔记尾部的「## 相关笔记」区块；
- 每个标签/分类自动生成 MOC 索引页，位于 `知识聚合/MOC/`。
"""

README_MD = """# {vault_name}

这是一个由「Obsidian 知识库自动化搭建工具」创建的标准知识库。

## 目录结构（未处理/已处理 + 兵哥四层架构）

- **未处理/** —— 批量导入与豆包提炼的统一源目录，把要整理的资料丢进来
- **已处理/** —— 导入/提炼完成后，源文件自动移入此处归档
- **知识提炼/** —— B 层：标准化知识笔记，自动打标 + 自动双链
- **知识聚合/** —— C 层：你的创作空间，程序不会触碰
- **知识聚合/MOC/** —— 自动生成的标签/分类索引页
- **规则模板/** —— D 层：提示词与格式规范，可自行修改
- **附件/** —— PDF、图片等原始文件归档
- **日记/** —— 学习心得、日常记录

## 常用命令

```bash
python run.py init            # 建库（重复运行安全）
python run.py sync            # 导入 + 打标 + 双链 + MOC + 报告（一次性）
python run.py watch           # 定时自动同步（内置循环）
python run.py schedule --install   # 注册 Windows 计划任务
```

## 用 Obsidian 打开

1. 下载安装 Obsidian：https://obsidian.md/zh/download
2. 打开库 → 「打开本地仓库文件夹」→ 选择本目录
3. 在「关系图谱」中查看自动生成的知识网络
"""

DEMO_NOTES = [
    {
        "name": "什么是知识库自动化",
        "tags": ["知识管理", "AI工具"],
        "category": "知识管理",
        "body": """# 什么是知识库自动化

知识库自动化的核心是：**该自动的自动，该手工的手工**。

- 程序负责重复劳动：导入、归类、打标、建链；
- AI 负责提炼精读：把原始素材变成结构化笔记；
- 人负责创作思考：从知识网络中产出真正的作品。

## 相关概念

- 参考 [[卡片盒笔记法入门]] 了解方法论基础
- 参考 [[Obsidian 双向链接实践]] 了解双链的用法
"""
    },
    {
        "name": "卡片盒笔记法入门",
        "tags": ["知识管理"],
        "category": "知识管理",
        "body": """# 卡片盒笔记法入门

卢曼（Niklas Luhmann）的卡片盒笔记法（Zettelkasten）是现代知识管理的基石：

- 每张卡片只记录一个概念；
- 卡片之间通过编号与引用建立网络；
- 大脑只负责「连接」，不负责「存储」。

## 相关概念

- 参见 [[什么是知识库自动化]] 了解工具化落地
"""
    },
    {
        "name": "Obsidian 双向链接实践",
        "tags": ["知识管理"],
        "category": "知识管理",
        "body": """# Obsidian 双向链接实践

Obsidian 用 `[[笔记名]]` 建立双向链接：

- 点击链接即可跳转；
- 关系图谱中能看到知识网络；
- 每添加一篇新笔记，都像大脑新增一个神经元。

## 相关概念

- 参见 [[什么是知识库自动化]]
"""
    },
]


def init_vault(cfg: Dict[str, Any], vault_root: str,
               logger: Optional[Any] = None, force: bool = False) -> Dict[str, int]:
    """创建/补齐知识库标准结构，返回统计 {dirs, files, skipped_files}。"""
    import logging
    if logger is None:
        logger = logging.getLogger("kb")
    stats = {"dirs_created": 0, "files_written": 0, "files_skipped": 0}

    structure: Dict[str, str] = cfg["structure"]
    root = vault_root
    os.makedirs(root, exist_ok=True)

    # 非空保护：若目录里已有内容（用户已有库），只补齐缺失目录，绝不写入任何文件
    existing = [e for e in os.listdir(root) if e not in (".obsidian",)]
    fresh = not existing
    if not fresh and not force:
        for logic, rel in structure.items():
            d = os.path.join(root, rel)
            if not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)
                stats["dirs_created"] += 1
                logger.info("[建库] 补齐目录 %s/（%s）", rel, logic)
        logger.info("[建库] 检测到知识库已有内容（%d 项），仅补齐缺失目录，"
                    "不写入任何文件。如需写入模板请加 --force", len(existing))
        return stats

    # 1. 目录
    for logic, rel in structure.items():
        d = os.path.join(root, rel)
        if os.path.isdir(d):
            logger.debug("目录已存在，跳过: %s (%s)", rel, logic)
        else:
            os.makedirs(d, exist_ok=True)
            stats["dirs_created"] += 1
            logger.info("[建库] 创建目录 %s/（%s）", rel, logic)

    # 2. Obsidian 基础配置
    os.makedirs(os.path.join(root, ".obsidian"), exist_ok=True)
    _write_if_missing(os.path.join(root, ".obsidian", "app.json"),
                      json.dumps(OBSIDIAN_APP_JSON, ensure_ascii=False, indent=2),
                      logger, stats, force=force)

    # 3. D 层规则模板
    rules_dir = os.path.join(root, structure.get("D_规则模板", "规则模板"))
    _write_if_missing(os.path.join(rules_dir, "豆包知识提炼提示词.md"), PROMPT_TEMPLATE,
                      logger, stats, force=force)
    _write_if_missing(os.path.join(rules_dir, "笔记格式规范.md"), FORMAT_SPEC,
                      logger, stats, force=force)

    # 4. 库说明
    _write_if_missing(os.path.join(root, "README.md"),
                      README_MD.format(vault_name=cfg["vault"]["name"]),
                      logger, stats, force=force)

    # 5. B 层示范笔记
    b_dir = os.path.join(root, structure.get("B_知识提炼", "知识提炼"))
    date_format = cfg["frontmatter"].get("date_format", "%Y-%m-%d %H:%M")
    for demo in DEMO_NOTES:
        note_path = os.path.join(b_dir, demo["name"] + ".md")
        if os.path.exists(note_path) and not force:
            stats["files_skipped"] += 1
            logger.debug("示范笔记已存在，跳过: %s", demo["name"])
            continue
        fm = {
            "title": demo["name"],
            "tags": demo["tags"],
            "category": demo["category"],
            "status": cfg["frontmatter"].get("status_done", "已整理"),
        }
        text = frontmatter.build_frontmatter(fm) + demo["body"]
        with open(note_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        stats["files_written"] += 1
        logger.info("[建库] 生成示范笔记 %s/%s.md", b_dir, demo["name"])

    # 6. 日记模板
    diary_dir = os.path.join(root, structure.get("日记", "日记"))
    diary_tpl = os.path.join(diary_dir, "模板.md")
    diary_body = (
        frontmatter.build_frontmatter({"title": "日记", "tags": ["日记"], "category": "日记"})
        + "# {date} 日记\n\n## 今天学到了什么\n\n## 今天想通了什么\n\n## 明天要做什么\n"
    )
    _write_if_missing(diary_tpl, diary_body, logger, stats, force=force)

    logger.info("[建库] 完成：新建目录 %d 个，写入文件 %d 个，跳过 %d 个",
                stats["dirs_created"], stats["files_written"], stats["files_skipped"])
    return stats


def _write_if_missing(path: str, content: str, logger: Any, stats: Dict[str, int],
                      force: bool = False) -> None:
    if os.path.exists(path) and not force:
        stats["files_skipped"] += 1
        logger.debug("文件已存在，跳过: %s", path)
        return
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    stats["files_written"] += 1
    logger.info("[建库] 写入文件 %s", path)
