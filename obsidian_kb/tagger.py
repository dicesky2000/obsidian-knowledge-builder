# -*- coding: utf-8 -*-
"""关键词规则自动打标。

- 配置 [tags.rules] 定义「关键词 → 标签」规则表；
- 命中判定：文件名（权重高）+ 正文前 500 字 + 全文；命中任一关键词即打标；
- 可选从正文提取 #hashtag 并入标签；
- 自动去重、数量上限（max_tags）。
"""
import re
from collections import Counter
from typing import Any, Dict, List

HASHTAG_RE = re.compile(r"(?<![#\w])#([\w\u4e00-\u9fa5][\w\u4e00-\u9fa5\-_/]{1,39})")


class Tagger(object):
    def __init__(self, cfg: Dict[str, Any]) -> None:
        rules_cfg = cfg.get("tags", {})
        self.rules: List[Dict[str, Any]] = rules_cfg.get("rules", [])
        self.max_tags = int(rules_cfg.get("max_tags", 10))
        self.extract_hashtags = bool(rules_cfg.get("extract_hashtags", True))

    def tag_for(self, filename: str, content: str) -> List[str]:
        """根据文件名与正文计算标签列表（已去重、已上限）。"""
        head = content[:500]
        full = content
        tags: Counter = Counter()

        for rule in self.rules:
            tag = str(rule.get("tag", "")).strip()
            keywords = rule.get("keywords", [])
            if not tag or not keywords:
                continue
            hit = False
            for kw in keywords:
                kw_s = str(kw)
                if not kw_s:
                    continue
                if _contains(filename, kw_s):
                    hit = True
                    break
                if _contains(head, kw_s) or _contains(full, kw_s):
                    hit = True
                    break
            if hit:
                tags[tag] += 1

        if self.extract_hashtags:
            for m in HASHTAG_RE.finditer(full):
                tag = m.group(1).strip()
                if tag and tag.lower() not in ("c", "include", "obsidian"):
                    tags[tag] += 1

        # 按命中次数降序，保留 max_tags 个
        ordered = [t for t, _ in tags.most_common(self.max_tags)]
        return ordered


def _contains(text: str, keyword: str) -> bool:
    """大小写不敏感的子串匹配（ASCII 部分忽略大小写）。"""
    if keyword.isascii():
        return keyword.lower() in text.lower()
    return keyword in text
