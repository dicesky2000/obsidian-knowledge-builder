# -*- coding: utf-8 -*-
"""建库模块：一键创建标准 Obsidian 知识库。

按配置 [structure] 顺序创建目录（默认：01未处理/已处理 + 兵哥四层架构 B/C/D + 06附件/日记），
并写入：
  - .obsidian/app.json（附件目录、新笔记位置等基础设置）
  - D 层「规则模板」：豆包知识提炼提示词（用户可随时改，程序每次读最新）
  - README.md 库说明
  - B 层示范笔记（含 [[双链]]，供 Obsidian 图谱预览）
  - 07日记/模板.md

幂等：已存在的目录/文件默认跳过，--force 可覆盖。
"""
import json
import os
from typing import Any, Dict, List, Optional

from . import frontmatter

OBSIDIAN_APP_JSON = {
    "attachmentFolderPath": "06附件",
    "newFileLocation": "folder",
    "newFileFolderPath": "07日记",
    "useMarkdownLinks": False,
    "showLineNumber": True,
}

PROMPT_TEMPLATE = """# 豆包提示词 — 知识提炼助手

## 角色

你是一位知识管家，负责将原始素材提炼为标准知识笔记。输出格式精确，不做多余解释。

## 输出格式

严格按以下 markdown 结构输出：

```markdown
---
type: 笔记
分类: 技术原理， 商业策略
原始链接: （出处URL或说明）
作者: （原作者，未知则留空）
---

# （4～20字的一句话总结）

**关键词**：（8～18个关键词，逗号分隔）

**摘要**：（浓缩核心内容，不超过120字）

## 详细内容

（用自己的话重新组织原始素材的核心知识点）

## 逻辑树（可选）

- 主论点
  - 支撑论据
```

## 要求

1. **分类**：从素材内容自行归纳，最多6个。例如：技术原理、商业策略、学习方法、系统设计、心理学等
2. **关键词**：8～18个，逗号分隔
3. **摘要**：不超过120字
4. **详细内容**：**不少于600字**
5. **逻辑树**：可选
6. **语言**：全部中文
7. **简洁**：只输出笔记本身，无解释无问候。**不要用 ``` 代码块包围**

---

**未收录**：{素材内容}
"""


README_MD = """# {vault_name}

这是一个由「Obsidian 知识库自动化搭建工具」创建的标准知识库。

## 目录结构（01未处理/已处理 + 兵哥四层架构）

- **01未处理/** —— 批量导入与豆包提炼的统一源目录，把要整理的资料丢进来
- **02已处理/** —— 导入/提炼完成后，源文件自动移入此处归档
- **03知识提炼/** —— B 层：标准化知识笔记，自动打标 + 自动双链
- **04知识聚合/** —— C 层：你的创作空间，程序不会触碰
- **05规则模板/** —— D 层：提示词与格式规范，可自行修改
- **06附件/** —— PDF、图片等原始文件归档
- **07日记/** —— 学习心得、日常记录

## 常用命令

```bash
python run.py init            # 建库（重复运行安全）
python run.py sync            # 导入 + 打标 + 双链 + 索引 + 报告（一次性）
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
        frontmatter.build_frontmatter({"title": "07日记", "tags": ["日记"], "category": "07日记"})
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
