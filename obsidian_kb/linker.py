# -*- coding: utf-8 -*-
"""双链关联引擎（按兵哥规则模板：关键词是链接的唯一依据）。

匹配规则（对齐《B层知识提炼笔记标准格式》/《知识库四层架构整体流程》）：
  1. 关键词来源：frontmatter「关键词」字段（豆包提炼模式）与「tags」字段（本地规则模式）合并；
  2. 全转小写后精确匹配，共享 ≥3 个相同关键词即建链（阈值 min_keywords 可调）；
  3. 每篇笔记最多保留前 8 条强相关链接（max_links_per_note）；
  4. 新增笔记自动链接其自身原始素材文件（已处理/xxx.md）；
  5. 链接引擎在全部笔记处理完毕后统一运行，每篇只追加缺失链接（幂等）；
  6. 为每个标签 / 分类生成 MOC 索引页。

约束：默认只处理 B 层（知识提炼），C 层（知识聚合）只读不写，程序不碰 C 层。
"""
import logging
import os
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

from . import frontmatter


class NoteInfo(object):
    __slots__ = ("path", "name", "title", "keywords", "source_rel", "content")

    def __init__(self, path: str, name: str, title: str, keywords: List[str],
                 source_rel: str, content: str) -> None:
        self.path = path
        self.name = name
        self.title = title
        self.keywords = keywords          # 小写去重后的关键词集合
        self.source_rel = source_rel      # 已处理相对路径（frontmatter.source）
        self.content = content


def _as_list(v: Any) -> List[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v]
    return [str(v)]


def _norm_keywords(raw_list) -> List[str]:
    """小写去重，剔除空白与过短项。"""
    out = []
    for kw in _as_list(raw_list):
        k = str(kw).strip().lower()
        if not k or len(k) < 2:
            continue
        if k not in out:
            out.append(k)
    return out


def collect_notes(cfg: Dict[str, Any], vault_root: str,
                  include_c: bool = False) -> List[NoteInfo]:
    """收集参与链接引擎的笔记（B 层 + 可选 C 层），跳过 MOC 目录。"""
    structure = cfg["structure"]
    b_dir = os.path.join(vault_root, structure.get("B_知识提炼", "知识提炼"))
    c_dir = os.path.join(vault_root, structure.get("C_知识聚合", "知识聚合"))
    moc_rel = structure.get("C_MOC", "知识聚合/MOC")
    moc_abs = os.path.join(vault_root, moc_rel)

    dirs = [b_dir]
    if include_c:
        dirs.append(c_dir)

    notes: List[NoteInfo] = []
    seen: Set[str] = set()
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for dirpath, dirnames, filenames in os.walk(d):
            dirnames[:] = [x for x in dirnames if not x.startswith(".")]
            for fn in sorted(filenames):
                if not fn.lower().endswith(".md"):
                    continue
                path = os.path.join(dirpath, fn)
                if os.path.abspath(path).startswith(os.path.abspath(moc_abs)):
                    continue
                name = os.path.splitext(fn)[0]
                if name in seen:
                    continue
                seen.add(name)
                try:
                    content = frontmatter.read_text_auto(path)
                except Exception:
                    continue
                fm, body, _ = frontmatter.parse_frontmatter(content)
                fm = fm or {}
                title = str(fm.get("title") or name)
                # 关键词：tags 字段（本地） + 关键词 字段（豆包提炼）
                keywords = _norm_keywords(fm.get("tags")) + \
                    _norm_keywords(fm.get("关键词") or fm.get("keywords"))
                source_rel = str(fm.get("source") or "")
                notes.append(NoteInfo(path, name, title, keywords, source_rel, content))
    return notes


def _ensure_links_section(content: str, section_title: str) -> Tuple[str, bool]:
    """确保正文尾部存在「## 相关笔记」区块，返回 (content, 是否需要写回)。"""
    heading = "## " + section_title
    if heading in content:
        return content, False
    if content and not content.endswith("\n"):
        content += "\n"
    content += "\n%s\n\n" % heading
    return content, True


def run_linking(cfg: Dict[str, Any], vault_root: str, registry: Any,
                logger: logging.Logger, report: Any) -> None:
    """执行链接引擎：关键词匹配建链 + 链接原始素材 + 更新 updated。"""
    link_cfg = cfg.get("linking", {})
    strategy = link_cfg.get("strategy", "keywords")
    if strategy == "none":
        logger.info("[链接] 链接策略为 none，跳过")
        return
    section_title = link_cfg.get("links_section", "相关笔记")
    max_links = int(link_cfg.get("max_links_per_note", 8))
    min_kw = max(1, int(link_cfg.get("min_keywords", 3)))
    link_to_source = bool(link_cfg.get("link_to_source", True))
    include_c = bool(link_cfg.get("include_c", False))
    date_format = cfg["frontmatter"].get("date_format", "%Y-%m-%d %H:%M")

    notes = collect_notes(cfg, vault_root, include_c=include_c)
    if len(notes) < 2:
        logger.info("[链接] 笔记不足 2 篇，跳过建链")
        return

    # 关键词倒排索引
    kw_index: Dict[str, List[str]] = defaultdict(list)
    for n in notes:
        for kw in n.keywords:
            kw_index[kw].append(n.name)

    logger.info("[链接] 共 %d 篇笔记参与匹配（策略：%s，阈值：%d 个关键词）",
                len(notes), strategy, min_kw)

    for note in notes:
        # 候选打分：共享关键词数（小写精确匹配）
        score: Dict[str, int] = defaultdict(int)
        for kw in note.keywords:
            for other_name in kw_index.get(kw, []):
                if other_name != note.name:
                    score[other_name] += 1

        ranked = [(nm, sc) for nm, sc in score.items() if sc >= min_kw]
        ranked.sort(key=lambda x: (-x[1], x[0]))
        ranked = ranked[:max_links]

        # 读取正文，收集已有链接
        content = note.content
        changed = False
        lines = content.splitlines()
        in_section = False
        existing_links: Set[str] = set()
        for line in lines:
            if line.startswith("#"):
                in_section = (line.strip().lstrip("#").strip() == section_title)
                continue
            if in_section:
                m = re.match(r"^\s*-\s*\[\[([^\]|]+)", line)
                if m:
                    existing_links.add(m.group(1).strip())

        # 链接原始素材（新增笔记自动关联自身来源）
        if link_to_source and note.source_rel:
            src_name = os.path.splitext(note.source_rel)[0]
            if src_name and src_name not in existing_links:
                ranked = ranked[:max(0, max_links - 1)]
                ranked.append((src_name, 999))  # 强关联，排在最后

        content, need_section = _ensure_links_section(content, section_title)
        if need_section:
            changed = True

        new_links = []
        for nm, _sc in ranked:
            if nm in existing_links:
                continue
            if ("[[" + nm + "]]") in content:
                existing_links.add(nm)
                continue
            new_links.append(nm)

        if new_links:
            content = content.rstrip()
            if not content.endswith("\n"):
                content += "\n"
            content += "\n" + "".join("- [[%s]]\n" % nm for nm in new_links)
            changed = True
            report.links_added += len(new_links)
            for nm in new_links:
                report.relations.append((note.name, nm))
                logger.debug("[链接] %s → [[%s]]", note.name, nm)

        if changed:
            frontmatter.write_text_auto(note.path, content)
            frontmatter.touch_updated(note.path, date_format)
            report.updated_notes += 1

    logger.info("[链接] 完成：新增双链 %d 条，更新笔记 %d 篇",
                report.links_added, report.updated_notes)


def generate_mocs(cfg: Dict[str, Any], vault_root: str,
                  logger: logging.Logger, report: Any) -> None:
    """为每个标签 / 分类生成（或刷新）MOC 索引页。"""
    link_cfg = cfg.get("linking", {})
    if not link_cfg.get("gen_moc", True):
        return
    structure = cfg["structure"]
    moc_rel = structure.get("C_MOC", "知识聚合/MOC")
    moc_dir = os.path.join(vault_root, moc_rel)
    os.makedirs(moc_dir, exist_ok=True)
    date_format = cfg["frontmatter"].get("date_format", "%Y-%m-%d %H:%M")

    notes = collect_notes(cfg, vault_root, include_c=False)
    tag_map: Dict[str, List[str]] = defaultdict(list)
    cat_map: Dict[str, List[str]] = defaultdict(list)
    for n in notes:
        for kw in n.keywords:
            tag_map[kw].append(n.name)
        fm, _, _ = frontmatter.parse_frontmatter(n.content)
        cat = (fm or {}).get("分类") or (fm or {}).get("category")
        if cat:
            cat_map[str(cat)].append(n.name)

    mocs: List[Tuple[str, str, List[str]]] = []
    for tag in sorted(tag_map):
        mocs.append(("标签", tag, sorted(set(tag_map[tag]))))
    for cat in sorted(cat_map):
        mocs.append(("分类", cat, sorted(set(cat_map[cat]))))

    for kind, key, names in mocs:
        safe = re.sub(r"[\\/:*?\"<>|]", "_", key)
        title = "%s · %s" % (kind, key)
        fname = "%s_%s.md" % (kind, safe)
        path = os.path.join(moc_dir, fname)
        _write_moc(path, title, [kind], names, date_format)
        report.mocs += 1
        logger.info("[MOC] %s 索引页：%s （%d 篇笔记）", kind, fname, len(names))

    logger.info("[MOC] 完成：生成/刷新 %d 个索引页", report.mocs)


def _write_moc(path: str, title: str, tags: List[str], note_names: List[str],
               date_format: str) -> None:
    """写 MOC 文件：保留已有 created，刷新 updated，列表确定性重排。"""
    import datetime

    body = ["# %s" % title, "",
            "> 本页由链接引擎自动生成（MOC / Map of Content），作为知识图谱的枢纽。",
            "", "## 收录笔记", ""]
    for nm in note_names:
        body.append("- [[%s]]" % nm)
    body.append("")

    fm: Dict[str, Any] = {"title": title, "tags": tags, "category": "MOC 索引"}
    existing = {}
    if os.path.exists(path):
        try:
            existing, _, _ = frontmatter.parse_frontmatter(frontmatter.read_text_auto(path))
        except Exception:
            existing = {}
    now = datetime.datetime.now().strftime(date_format)
    if existing.get("created"):
        fm["created"] = existing["created"]
    else:
        fm["created"] = now
    fm["updated"] = now

    text = frontmatter.build_frontmatter(fm) + "\n".join(body)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
