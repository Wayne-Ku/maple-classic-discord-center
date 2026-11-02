import re
from pathlib import Path


WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "check-announcements.yml"


def test_schedule_keeps_fifteen_minute_cadence_off_hour_boundaries():
    text = WORKFLOW.read_text(encoding="utf-8")
    match = re.search(r'cron: "(?P<minutes>[0-9,]+) \* \* \* \*"', text)

    assert match is not None
    minutes = [int(value) for value in match.group("minutes").split(",")]
    cyclic_intervals = [
        (minutes[(index + 1) % len(minutes)] - minute) % 60
        for index, minute in enumerate(minutes)
    ]
    assert minutes == [7, 22, 37, 52]
    assert cyclic_intervals == [15, 15, 15, 15]
    assert 0 not in minutes


def test_partial_success_state_is_saved_even_when_a_later_announcement_fails():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "- name: Detect state changes\n        id: state_after\n        if: always()" in text
    assert (
        "- name: Save announcement state\n"
        "        if: always() && steps.state_after.outputs.changed == 'true'"
    ) in text


def test_manual_cleanup_requires_announcement_and_keep_message_ids():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "cleanup_announcement_id:" in text
    assert "keep_message_id:" in text
    assert "python tools/cleanup_discord_announcement.py" in text
    assert '--id "$CLEANUP_ANNOUNCEMENT_ID"' in text
    assert '--keep-message-id "$KEEP_MESSAGE_ID"' in text
