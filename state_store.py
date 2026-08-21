"""Atomic JSON persistence for sent announcements and Discord message IDs."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


class StateStoreError(RuntimeError):
    """Raised when state cannot be read or written safely."""


@dataclass
class AnnouncementState:
    sent_ids: set[str] = field(default_factory=set)
    discord_message_ids: dict[str, tuple[str, ...]] = field(default_factory=dict)
    missing_checks: dict[str, int] = field(default_factory=dict)


def _normalized_ids(value: object) -> set[str]:
    if not isinstance(value, list):
        raise ValueError
    normalized: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError
        normalized.add(item.strip())
    return normalized


def _message_ids(value: object, sent_ids: set[str]) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, dict):
        raise ValueError
    result: dict[str, tuple[str, ...]] = {}
    for announcement_id, raw_ids in value.items():
        if announcement_id not in sent_ids or not isinstance(raw_ids, list):
            raise ValueError
        message_ids: list[str] = []
        for message_id in raw_ids:
            if not isinstance(message_id, str) or not message_id.isdecimal():
                raise ValueError
            if message_id not in message_ids:
                message_ids.append(message_id)
        if message_ids:
            result[announcement_id] = tuple(message_ids)
    return result


def _missing_checks(value: object, sent_ids: set[str]) -> dict[str, int]:
    if not isinstance(value, dict):
        raise ValueError
    result: dict[str, int] = {}
    for announcement_id, count in value.items():
        if (
            announcement_id not in sent_ids
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 1
        ):
            raise ValueError
        result[announcement_id] = count
    return result


def load_state(path: Path) -> AnnouncementState | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError
        version = payload["version"]
        if isinstance(version, bool) or not isinstance(version, int):
            raise ValueError
        sent_ids = _normalized_ids(payload["sent_announcement_ids"])
        if version == 1:
            return AnnouncementState(sent_ids=sent_ids)
        if version != 2:
            raise ValueError
        return AnnouncementState(
            sent_ids=sent_ids,
            discord_message_ids=_message_ids(
                payload.get("discord_message_ids", {}), sent_ids
            ),
            missing_checks=_missing_checks(
                payload.get("missing_announcement_checks", {}), sent_ids
            ),
        )
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        raise StateStoreError(
            f"狀態檔 {path} 無法讀取或格式不正確；為避免重複推播，已停止執行。"
        ) from exc


def load_sent_ids(path: Path) -> set[str] | None:
    """Compatibility projection used by existing callers and tools."""
    state = load_state(path)
    return None if state is None else state.sent_ids


def save_state(path: Path, state: AnnouncementState) -> None:
    payload = {
        "version": 2,
        "sent_announcement_ids": sorted(state.sent_ids),
        "discord_message_ids": {
            announcement_id: list(state.discord_message_ids[announcement_id])
            for announcement_id in sorted(state.discord_message_ids)
            if announcement_id in state.sent_ids
        },
        "missing_announcement_checks": {
            announcement_id: state.missing_checks[announcement_id]
            for announcement_id in sorted(state.missing_checks)
            if announcement_id in state.sent_ids
        },
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


def save_sent_ids(path: Path, sent_ids: set[str]) -> None:
    """Compatibility writer that preserves known message metadata."""
    existing = load_state(path)
    state = existing or AnnouncementState()
    state.sent_ids = set(sent_ids)
    state.discord_message_ids = {
        key: value
        for key, value in state.discord_message_ids.items()
        if key in state.sent_ids
    }
    state.missing_checks = {
        key: value
        for key, value in state.missing_checks.items()
        if key in state.sent_ids
    }
    save_state(path, state)
