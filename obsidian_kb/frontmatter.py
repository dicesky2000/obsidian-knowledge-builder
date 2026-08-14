# -*- coding: utf-8 -*-
"""YAML frontmatter 的解析 / 生成 / 更新。

规范化的 frontmatter：
  ---
  title: 笔记标题
  tags: [标签1, 标签2]
  created: 2026-08-14 15:00
  updated: 2026-08-14 15:30
  source: 原始来源路径
  category: 自动归类
  status: 待整理
  ---

设计要点：
  - 已存在的 frontmatter：保留用户手写字段（如 aliases、author），只合并/更新
    title、tags、created、updated 等规范字段，绝不删除用户信息；
  - 无 frontmatter：自动在文件头部插入新块；
  - YAML 库不可用时，回退到轻量文本解析器，保证工具始终可用。
"""
import datetime
import os
import re
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml as _yaml
    HAS_YAML = True
except Exception:  # pragma: no cover
    _yaml = None
    HAS_YAML = False

FM_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)[ \t]*\r?\n---[ \t]*(?:\r?\n|$)", re.S)

# 规范字段集合：这些字段由程序管理；其余字段视为用户自定义，原样保留
MANAGED_FIELDS = {"title", "tags", "created", "updated", "source",
                  "category", "status", "aliases"}


# ---------------------------------------------------------------------------
# 轻量 YAML 回退解析（仅支持 frontmatter 常见形态）
# ---------------------------------------------------------------------------
def _parse_scalar(text: str) -> Any:
    text = text.strip()
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [x.strip().strip("'\"") for x in inner.split(",") if x.strip()]
    if text.startswith("#") or text.lower() == "null":
        return None
    if text.lower() in ("true", "yes"):
        return True
    if text.lower() in ("false", "no"):
        return False
    if text.startswith(("'", '"')) and text.endswith(text[0]):
        return text[1:-1]
    return text


def _parse_fm_fallback(text: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    current_key: Optional[str] = None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][\w\-]*):(.*)$", line)
        if m:
            current_key = m.group(1)
            rest = m.group(2).strip()
            result[current_key] = _parse_scalar(rest) if rest else None
        elif current_key and line.startswith("  - "):
            item = line.strip()[3:].strip().strip("'\"")
            val = result.get(current_key)
            if not isinstance(val, list):
                val = []
            val.append(item)
            result[current_key] = val
    return result


def parse_frontmatter(text: str) -> Tuple[Optional[Dict[str, Any]], str, bool]:
    """解析文本，返回 (frontmatter字典或None, 正文, 是否含frontmatter)。"""
    m = FM_RE.match(text)
    if not m:
        return None, text, False
    block = m.group(1)
    if HAS_YAML:
        try:
            data = _yaml.safe_load(block) or {}
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = _parse_fm_fallback(block)
    else:
        data = _parse_fm_fallback(block)
    return data, text[m.end():], True


def build_frontmatter(fields: Dict[str, Any]) -> str:
    """由字段字典生成 frontmatter 文本（含 --- 包裹）。"""
    if HAS_YAML:
        body = _yaml.safe_dump(fields, allow_unicode=True, sort_keys=False,
                               default_flow_style=False).rstrip()
    else:
        lines = []
        for k, v in fields.items():
            if isinstance(v, (list, tuple)):
                lines.append("%s:" % k)
                for item in v:
                    lines.append("  - %s" % str(item))
            else:
                lines.append("%s: %s" % (k, _fmt_scalar(v)))
        body = "\n".join(lines)
    return "---\n%s\n---\n" % body


def _fmt_scalar(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    text = str(v)
    if re.search(r"[:#\[\]{},&\*!|>'\"]|^\s|\s$", text):
        return "'%s'" % text.replace("'", "''")
    return text


def _now_str(date_format: str) -> str:
    return datetime.datetime.now().strftime(date_format)


def read_text_auto(path: str) -> str:
    """读取文本文件，自动探测 UTF-8 / GBK 编码，并把换行统一归一化为 \\n。"""
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def write_text_auto(path: str, text: str) -> None:
    """以 UTF-8 写回文本文件；newline='\\n' 禁用 Windows 的换行转换，
    避免 CRLF 内容二次写入时产生 \\r\\r\\n 双回车。"""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def ensure_frontmatter(path: str,
                       title: Optional[str] = None,
                       tags: Optional[List[str]] = None,
                       source: Optional[str] = None,
                       category: Optional[str] = None,
                       status: Optional[str] = None,
                       date_format: str = "%Y-%m-%d %H:%M",
                       aliases: Optional[List[str]] = None
                       ) -> Tuple[bool, Dict[str, Any]]:
    """确保笔记具有规范化 frontmatter，返回 (是否有变更, frontmatter)。

    - created：仅缺失时写入（保留首次创建时间）；
    - updated：每次运行刷新为当前时间；
    - tags：合并去重；
    - 其余规范字段仅缺失时写入，避免覆盖用户手工归类。
    """
    text = read_text_auto(path)
    fm, body, has_fm = parse_frontmatter(text)
    if not fm:
        fm = {}

    now = _now_str(date_format)
    changed = False

    if title and not fm.get("title"):
        fm["title"] = title
        changed = True
    if aliases and not fm.get("aliases"):
        fm["aliases"] = aliases
        changed = True

    if tags:
        old_tags = set(_as_list(fm.get("tags")))
        new_tags = [t for t in tags if t not in old_tags]
        if new_tags:
            fm["tags"] = old_tags | set(tags)
            changed = True

    if source and not fm.get("source"):
        fm["source"] = source
        changed = True
    if category and not fm.get("category"):
        fm["category"] = category
        changed = True
    if status and not fm.get("status"):
        fm["status"] = status
        changed = True
    if not fm.get("created"):
        fm["created"] = now
        changed = True
    if fm.get("updated") != now:
        fm["updated"] = now
        changed = True

    if changed:
        new_text = build_frontmatter(fm) + body
        write_text_auto(path, new_text)
    return changed, fm


def touch_updated(path: str, date_format: str = "%Y-%m-%d %H:%M") -> bool:
    """仅刷新 updated 字段（链接引擎追加链接后调用）。"""
    text = read_text_auto(path)
    fm, body, has_fm = parse_frontmatter(text)
    if not fm:
        return False
    now = _now_str(date_format)
    if fm.get("updated") == now:
        return False
    fm["updated"] = now
    write_text_auto(path, build_frontmatter(fm) + body)
    return True


def _as_list(v: Any) -> List[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v]
    return [str(v)]
