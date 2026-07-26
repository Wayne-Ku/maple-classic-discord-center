import json

import pytest

import state_store
from state_store import StateStoreError, load_sent_ids, save_sent_ids


def write_state(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_missing_state_is_first_run(tmp_path):
    assert load_sent_ids(tmp_path / "state.json") is None


def test_loads_valid_version_one_and_normalizes_duplicates(tmp_path):
    path = tmp_path / "state.json"
    write_state(path, {"version": 1, "sent_announcement_ids": [" one ", "one", "two"]})
    assert load_sent_ids(path) == {"one", "two"}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"version": 2, "sent_announcement_ids": []},
        {"version": "1", "sent_announcement_ids": []},
        {"version": 1, "sent_announcement_ids": "not-a-list"},
        {"version": 1, "sent_announcement_ids": [1]},
        {"version": 1, "sent_announcement_ids": ["   "]},
        [],
    ],
)
def test_rejects_invalid_state_format(tmp_path, payload):
    path = tmp_path / "state.json"
    write_state(path, payload)
    with pytest.raises(StateStoreError):
        load_sent_ids(path)


def test_rejects_broken_json(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(StateStoreError):
        load_sent_ids(path)


def test_atomic_write_round_trip(tmp_path):
    path = tmp_path / "nested" / "state.json"
    save_sent_ids(path, {"2", "1"})
    assert load_sent_ids(path) == {"1", "2"}
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1


def test_atomic_write_failure_cleans_temporary_file(monkeypatch, tmp_path):
    path = tmp_path / "state.json"

    def fail_replace(*args, **kwargs):
        raise OSError("replace failed")

    monkeypatch.setattr(state_store.os, "replace", fail_replace)
    with pytest.raises(StateStoreError):
        save_sent_ids(path, {"1"})
    assert not list(tmp_path.glob(".state.json.*.tmp"))
