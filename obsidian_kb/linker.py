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
    __slots__ = ("path", "name", "title", "keywords", "raw_keywords",
                 "categories", "source_rel", "content")

    def __init__(self, path: str, name: str, title: str, keywords: List[str],
                 raw_keywords: List[str], categories: List[str],
                 source_rel: str, content: str) -> None:
        self.path = path
        self.name = name
        self.title = title
        self.keywords = keywords          # 小写去重后的关键词集合（用于匹配）
        self.raw_keywords = raw_keywords  # 原文关键词（去重保序，用于展示）
        self.categories = categories      # 拆分后的分类值（原文）
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


def _split_csv(text) -> List[str]:
    """按中英文逗号拆分并去空白、去重保序。"""
    out = []
    for part in re.split(r"[,，]", str(text or "")):
        p = part.strip()
        if p and p not in out:
            out.append(p)
    return out


def _extract_body_keywords(body: str) -> List[str]:
    """从正文提取 `**关键词**：...` 行并按逗号拆分。"""
    m = re.search(r"\*\*关键词\*\*\s*[:：]\s*(.+)", body)
    if m:
        return _split_csv(m.group(1))
    return []


def _safe_key(key) -> str:
    """把关键词/分类值清洗成可作文件名与 wiki 链接名的安全串。"""
    return re.sub(r'[\\/:*?"<>|]', "_", str(key))


def collect_notes(cfg: Dict[str, Any], vault_root: str,
                  include_c: bool = False) -> List[NoteInfo]:
    """收集参与链接引擎的笔记（B 层 + 可选 C 层），跳过 MOC 与索引笔记目录。"""
    structure = cfg["structure"]
    b_dir = os.path.join(vault_root, structure.get("B_知识提炼", "知识提炼"))
    c_dir = os.path.join(vault_root, structure.get("C_知识聚合", "知识聚合"))
    moc_rel = structure.get("C_MOC", "知识聚合/MOC")
    moc_abs = os.path.join(vault_root, moc_rel)
    index_rel = structure.get("B_索引", "知识提炼/索引笔记")
    index_abs = os.path.join(vault_root, index_rel)

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
                absp = os.path.abspath(path)
                if absp.startswith(os.path.abspath(moc_abs)) or \
                        absp.startswith(os.path.abspath(index_abs)):
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
                # 关键词：正文 `**关键词**：` 行 + frontmatter「关键词」+「tags」合并
                raw = []
                for kw in _extract_body_keywords(body) + \
                        _as_list(fm.get("关键词") or fm.get("keywords")) + \
                        _as_list(fm.get("tags")):
                    k = str(kw).strip()
                    if k and k.lower() not in [x.lower() for x in raw]:
                        raw.append(k)
                raw_keywords = raw
                keywords = _norm_keywords(raw)
                # 分类：frontmatter「分类」/category 按逗号拆分为多个分类值
                categories = _split_csv(fm.get("分类") or fm.get("category"))
                source_rel = str(fm.get("source") or "")
                notes.append(NoteInfo(path, name, title, keywords, raw_keywords,
                                     categories, source_rel, content))
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
    section_title = link_cfg.get("links_section", "双向链接")
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

    # 名称 → 小写关键词集合（共享关键词交集计算用）
    name_kw_map: Dict[str, Set[str]] = {n.name: set(n.keywords) for n in notes}
    # registry 的 note→source 映射（豆包笔记无 frontmatter.source 时回退）
    src_map: Dict[str, str] = {}
    try:
        for e in registry.all_entries():
            src_map[e.get("note", "")] = e.get("source", "")
    except Exception:
        src_map = {}

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

        # 读取正文，收集已有链接（兼容 `- [[name]]` 与 `- [[name]] — 共享关键词：…`）
        content = note.content
        changed = False
        in_section = False
        existing_links: Set[str] = set()
        has_source = False
        for line in content.splitlines():
            if line.startswith("#"):
                in_section = (line.strip().lstrip("#").strip() == section_title)
                continue
            if in_section:
                m = re.match(r"^\s*-\s*\[\[([^\]|]+)", line)
                if m:
                    existing_links.add(m.group(1).strip())
                elif re.match(r"^\s*-\s*\[原始素材\]\(", line):
                    has_source = True

        # 原始素材链接（markdown 相对路径；frontmatter.source 优先，registry 回退）
        source_line = ""
        if link_to_source:
            src_rel = note.source_rel
            if not src_rel:
                note_rel = os.path.relpath(note.path, vault_root).replace("\\", "/")
                src_rel = src_map.get(note_rel, "")
            if src_rel:
                src_abs = os.path.join(vault_root, src_rel)
                if os.path.isfile(src_abs):
                    rel = os.path.relpath(
                        src_abs, os.path.dirname(note.path)).replace("\\", "/")
                    source_line = "- [原始素材](%s)" % rel
                    if not has_source:
                        ranked = ranked[:max(0, max_links - 1)]  # 素材链接占 1 名额

        content, need_section = _ensure_links_section(content, section_title)
        if need_section:
            changed = True

        # 相关笔记（共享关键词交集，按本笔记原文顺序）
        note_pairs: List[Tuple[str, str]] = []
        for nm, _sc in ranked:
            if nm in existing_links or ("[[" + nm + "]]") in content:
                existing_links.add(nm)
                continue
            shared = [kw for kw in note.raw_keywords
                      if kw.lower() in name_kw_map.get(nm, set())]
            if not shared:
                continue
            note_pairs.append(
                (nm, "- [[%s]] — 共享关键词：%s" % (nm, "，".join(shared))))

        # 索引笔记链接（不受 max_links 限制；每命中一个关键词/分类值一条）
        index_lines = []
        for kw in note.raw_keywords:
            idx = "索引_%s" % _safe_key(kw)
            if idx in existing_links or ("[[" + idx + "]]") in content:
                continue
            index_lines.append("- [[%s]] — 共享关键词：%s" % (idx, kw))
        for cat in note.categories:
            idx = "索引_分类_%s" % _safe_key(cat)
            if idx in existing_links or ("[[" + idx + "]]") in content:
                continue
            index_lines.append("- [[%s]] — 共享关键词：%s" % (idx, cat))

        new_links = []
        if source_line and not has_source:
            new_links.append(source_line)
        new_links.extend(ln for _nm, ln in note_pairs)
        new_links.extend(index_lines)

        if new_links:
            content = content.rstrip()
            if not content.endswith("\n"):
                content += "\n"
            content += "\n" + "\n".join(new_links) + "\n"
            changed = True
            report.links_added += len(new_links)
            for nm, _ln in note_pairs:
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


def generate_indexes(cfg: Dict[str, Any], vault_root: str,
                     logger: logging.Logger, report: Any) -> None:
    """为每个关键词 / 分类值生成（或刷新）B 层索引笔记。

    命名：关键词 → `索引_<关键词>.md`；分类 → `索引_分类_<分类值>.md`。
    内容对齐用户规范：`# 索引：<主题>` + 空行 + `- [[B层笔记]]` 列表，无 frontmatter。
    目录：structure.B_索引（默认 03知识提炼/索引笔记）。
    """
    link_cfg = cfg.get("linking", {})
    if not link_cfg.get("gen_index", True):
        return
    structure = cfg["structure"]
    index_rel = structure.get("B_索引", "03知识提炼/索引笔记")
    index_dir = os.path.join(vault_root, index_rel)
    os.makedirs(index_dir, exist_ok=True)

    notes = collect_notes(cfg, vault_root, include_c=False)
    kw_map: Dict[str, List[str]] = defaultdict(list)
    cat_map: Dict[str, List[str]] = defaultdict(list)
    for n in notes:
        for kw in n.raw_keywords:
            kw_map[kw].append(n.name)
        for cat in n.categories:
            cat_map[cat].append(n.name)

    groups: List[Tuple[str, bool, List[str]]] = []
    for key in sorted(kw_map):
        groups.append((key, False, sorted(set(kw_map[key]))))
    for key in sorted(cat_map):
        groups.append((key, True, sorted(set(cat_map[key]))))

    made = 0
    for key, is_cat, names in groups:
        safe = _safe_key(key)
        fname = ("索引_分类_%s.md" if is_cat else "索引_%s.md") % safe
        path = os.path.join(index_dir, fname)
        text = "# 索引：%s\n\n%s\n" % (key, "\n".join("- [[%s]]" % nm for nm in names))
        if os.path.isfile(path):
            try:
                if frontmatter.read_text_auto(path) == text:
                    continue
            except Exception:
                pass
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        made += 1
        logger.info("[索引] %s（%d 篇笔记）", fname, len(names))

    report.mocs += made
    logger.info("[索引] 完成：生成/刷新 %d 个索引笔记", made)
