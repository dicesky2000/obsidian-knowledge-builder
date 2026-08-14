# -*- coding: utf-8 -*-
"""配置加载与校验。

- 支持 YAML(.yaml/.yml) 与 JSON(.json) 两种格式，YAML 不可用时自动回退 JSON；
- 采用「默认值 + 用户覆盖」的深合并策略，未声明的键全部使用内置默认值；
- 配置文件可自由增删「目录结构 / 标签规则 / 命名规范 / 链接策略」等参数，
  满足"按需调整"的定制化需求。
"""
import copy
import json
import os
from typing import Any, Dict, List, Optional

try:
    import yaml as _yaml
    HAS_YAML = True
except Exception:  # pragma: no cover
    _yaml = None
    HAS_YAML = False

# ---------------------------------------------------------------------------
# 内置默认配置（兵哥四层架构：未处理 / 已处理 / B 知识提炼 / C 知识聚合 / D 规则模板）
# ---------------------------------------------------------------------------
DEFAULT_CONFIG: Dict[str, Any] = {
    "vault": {
        "name": "我的知识库",
        "root": "",                       # 空字符串 → 使用「当前目录 / vault.name」
    },
    "structure": {                        # 建库时按此顺序创建；键为逻辑名，值为相对路径
        "未处理": "未处理",                # 批量导入与豆包提炼的统一源目录（把要整理的资料丢进来）
        "已处理": "已处理",                # 导入/提炼完成后，源文件移入此处归档
        "B_知识提炼": "知识提炼",
        "C_知识聚合": "知识聚合",
        "C_MOC": "知识聚合/MOC",
        "D_规则模板": "规则模板",
        "附件": "附件",
        "日记": "日记",
    },
    "import": {
        "inbox": "未处理",                 # 相对库根的导入源目录（也支持绝对路径）
        "extra_sources": [],              # 额外扫描源（默认无；未处理已统一收口）
        "include_exts": [".md", ".txt", ".pdf", ".docx", ".png", ".jpg",
                         ".jpeg", ".gif", ".webp"],
        "attachment_exts": [".png", ".jpg", ".jpeg", ".gif", ".webp"],
        "archive_exts": [".pdf", ".docx", ".xlsx", ".pptx"],
        "pdf_mode": "extract",            # extract=提取正文生成笔记 | archive=仅归档
        "pdf_max_pages": 50,
        "move_after_import": True,        # 导入后把源文件移入「已处理」或「附件」
        "dedupe_by_hash": True,           # 幂等：按内容 SHA-256 去重
        "keep_subdirs": False,            # 是否保留未处理子目录层级
        "max_depth": 5,
    },
    "frontmatter": {
        "fields": ["title", "tags", "created", "updated", "source", "category", "status"],
        "status_new": "待整理",
        "status_done": "已整理",
        "date_format": "%Y-%m-%d %H:%M",
    },
    "tags": {
        # 关键词 → 标签 规则表（命中文件名或正文即自动打标）。示例规则，可按需增删。
        "rules": [
            {"tag": "轨道交通", "keywords": ["轨道", "铁路", "高铁", "地铁", "城轨",
                                             "机车", "车辆", "牵引", "信号"]},
            {"tag": "电力电子", "keywords": ["变流", "逆变", "整流", "IGBT", "igbt",
                                             "辅助供电", "牵引变流器"]},
            {"tag": "知识管理", "keywords": ["知识库", "笔记法", "卡片盒", "Zettelkasten",
                                             "zettelkasten", "双链", "双向链接", "Obsidian",
                                             "obsidian", "第二大脑"]},
            {"tag": "AI工具", "keywords": ["AI", "ai", "大模型", "豆包", "提示词", "GPT",
                                            "gpt", "智能体", "agent", "Agent"]},
            {"tag": "标准规范", "keywords": ["标准", "规范", "国标", "EN ", "IEC", "iec",
                                             "ISO", "iso", "认证"]},
        ],
        "extract_hashtags": True,         # 从正文提取 #标签
        "max_tags": 10,
    },
    "linking": {
        "strategy": "keywords",           # keywords=关键词匹配（兵哥模板）| title_tags=标题+标签 | none
        "min_keywords": 3,                # 共享 ≥3 个相同关键词即建链（模板阈值，可调）
        "max_links_per_note": 8,          # 每篇笔记最多保留前 8 条强相关链接（模板）
        "link_to_source": True,           # 新笔记自动链接其自身原始素材（已处理/）
        "gen_moc": True,                  # 为每个标签/分类生成 MOC 索引页
        "include_c": False,               # 是否把 C 层创作笔记纳入链接引擎（默认不碰 C 层）
        "links_section": "相关笔记",       # 笔记尾部自动维护的关联区块标题
    },
    "naming": {
        "date_prefix": True,              # 笔记名加日期前缀：2026-08-14_xxx.md
        "date_prefix_format": "%Y-%m-%d",
        "sanitize": True,                 # 清理文件名非法字符
        "space_replacement": "_",
        "max_len": 80,
    },
    "scheduler": {
        "interval_minutes": 30,           # watch 内置循环的轮询间隔
        "task_name": "ObsidianKB-Sync",   # Windows 计划任务名称
        "task_time": "09:00",
    },
    "logging": {
        "log_dir": "处理日志",
        "report_file": "处理报告",          # 每次运行生成 处理报告_YYYYMMDD_HHMMSS.md
    },
}


def default_config() -> Dict[str, Any]:
    """返回默认配置的深拷贝，避免调用方互相污染。"""
    return copy.deepcopy(DEFAULT_CONFIG)


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """递归深合并：override 覆盖 base。列表与标量直接替换。"""
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _load_yaml(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8-sig") as f:
        data = _yaml.safe_load(f)
    return data or {}


def _load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8-sig") as f:
        data = json.load(f)
    return data or {}


def find_default_config(cwd: str) -> Optional[str]:
    """在当前目录查找默认配置文件 kbconfig.yaml / kbconfig.yml / kbconfig.json。"""
    for name in ("kbconfig.yaml", "kbconfig.yml", "kbconfig.json"):
        candidate = os.path.join(cwd, name)
        if os.path.isfile(candidate):
            return candidate
    return None


def load_config(path: Optional[str] = None, cwd: Optional[str] = None) -> Dict[str, Any]:
    """加载配置：用户配置 深合并 到 默认配置 之上。

    返回合并后的完整配置字典。
    """
    cwd = cwd or os.getcwd()
    used_path: Optional[str] = path or find_default_config(cwd)

    cfg = default_config()
    if used_path and os.path.isfile(used_path):
        lower = used_path.lower()
        if lower.endswith((".yaml", ".yml")):
            if not HAS_YAML:
                raise RuntimeError(
                    "检测到 YAML 配置文件，但当前环境未安装 PyYAML。"
                    "请执行: pip install pyyaml，或改用 JSON 格式配置文件。")
            raw = _load_yaml(used_path)
        else:
            raw = _load_json(used_path)
        cfg = deep_merge(cfg, raw)

    validate_config(cfg)
    return cfg


def validate_config(cfg: Dict[str, Any]) -> None:
    """关键字段存在性校验，缺失/类型错误时抛出 ValueError。"""
    for section in ("vault", "structure", "import", "frontmatter",
                    "tags", "linking", "naming", "scheduler", "logging"):
        if not isinstance(cfg.get(section), dict):
            raise ValueError("配置缺少或格式错误: [%s] 应为对象/字典" % section)
    if not cfg["structure"]:
        raise ValueError("配置 [structure] 不能为空，至少需要一个目录")
    # 确保 未处理/已处理 与 B/C/D 各层、附件、日记等关键目录存在
    required_keys = ("未处理", "已处理", "B_知识提炼", "C_知识聚合",
                     "D_规则模板", "附件", "日记")
    missing = [k for k in required_keys if k not in cfg["structure"]]
    if missing:
        raise ValueError("配置 [structure] 缺少关键目录: %s" % "、".join(missing))
    if not isinstance(cfg["tags"].get("rules"), list):
        raise ValueError("配置 [tags.rules] 应为列表")


def resolve_vault_root(cfg: Dict[str, Any], cwd: Optional[str] = None) -> str:
    """解析库根目录绝对路径：优先 config.vault.root，否则 <cwd>/<vault.name>。"""
    cwd = cwd or os.getcwd()
    root = (cfg["vault"].get("root") or "").strip()
    if root:
        return os.path.abspath(root)
    return os.path.join(os.path.abspath(cwd), cfg["vault"]["name"])
