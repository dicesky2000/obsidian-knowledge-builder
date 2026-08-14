# -*- coding: utf-8 -*-
"""幂等性注册表。

以文件内容 SHA-256 为唯一标识，记录「已导入」状态，存入库根目录 .kb_registry.json。
重复运行时：
  - 同一内容的文件 → 直接跳过，避免重复导入；
  - 同名的不同内容 → 正常处理，文件名冲突时自动加序号；
  - 注册表损坏 → 自动重建，不阻断流程。
"""
import datetime
import hashlib
import json
import os
from typing import Dict, List, Optional


class Registry(object):
    def __init__(self, vault_root: str) -> None:
        self.path = os.path.join(vault_root, ".kb_registry.json")
        self.data: Dict[str, Dict] = {"version": 1, "files": {}}
        self._load()

    def _load(self) -> None:
        if os.path.isfile(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict) and isinstance(raw.get("files"), dict):
                    self.data = raw
            except Exception:
                # 损坏则重建（幂等兜底）
                self.data = {"version": 1, "files": {}}

    def save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    @staticmethod
    def hash_file(path: str) -> str:
        """计算文件内容 SHA-256（分块读取，支持大文件）。"""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def contains(self, content_hash: str) -> bool:
        return content_hash in self.data["files"]

    def get(self, content_hash: str) -> Optional[Dict]:
        return self.data["files"].get(content_hash)

    def mark(self, content_hash: str, note_rel: str, source_rel: str) -> None:
        self.data["files"][content_hash] = {
            "note": note_rel,
            "source": source_rel,
            "imported_at": datetime.datetime.now().isoformat(timespec="seconds"),
        }

    def remove_by_note(self, note_rel: str) -> None:
        """清理指向某笔记的注册项（清理笔记时用）。"""
        for h, info in list(self.data["files"].items()):
            if info.get("note") == note_rel:
                del self.data["files"][h]

    def all_entries(self) -> List[Dict]:
        return list(self.data["files"].values())
