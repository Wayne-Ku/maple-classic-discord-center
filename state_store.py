"""Atomic JSON persistence for sent announcement IDs."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


class StateStoreError(RuntimeError):
    """Raised when state cannot be read or written safely."""


def load_sent_ids(path: Path) -> set[str] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError
        version = payload["version"]
        if isinstance(version, bool) or not isinstance(version, int) or version != 1:
            raise ValueError
        ids = payload["sent_announcement_ids"]
        if not isinstance(ids, list):
            raise ValueError
        normalized_ids: set[str] = set()
        for item in ids:
            if not isinstance(item, str) or not item.strip():
                raise ValueError
            normalized_ids.add(item.strip())
        return normalized_ids
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        raise StateStoreError(
            f"狀態檔 {path} 無法讀取或格式不正確；為避免重複推播，已停止執行。"
        ) from exc


def save_sent_ids(path: Path, sent_ids: set[str]) -> None:
    payload = {
        "version": 1,
        "sent_announcement_ids": sorted(sent_ids),
    }
    temporary_name: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            json.dump(payload, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        os.replace(temporary_name, path)
    except OSError as exc:
        if temporary_name:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass
        raise StateStoreError(f"無法安全寫入狀態檔 {path}：{exc}") from exc
