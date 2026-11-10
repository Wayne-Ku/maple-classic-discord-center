from pathlib import Path


WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "check-announcements.yml"


def test_cloudflare_dispatch_replaces_github_schedule():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "  schedule:" not in text
    assert "  workflow_dispatch:" in text
    assert "      slot_id:" in text
    assert "      attempt:" in text
    assert "format('公告時段 {0}-{1}', inputs.slot_id, inputs.attempt)" in text


def test_cloudflare_dispatch_only_runs_hour_aligned_slots():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "- name: Decide announcement cadence" in text
    assert '(( (SLOT_ID / 60000) % 60 == 0 ))' in text
    assert 'if [[ -z "$SLOT_ID" ]]' in text
    assert 'echo "run=true" >> "$GITHUB_OUTPUT"' in text
    assert 'echo "run=false" >> "$GITHUB_OUTPUT"' in text
    assert text.count("if: steps.cadence.outputs.run == 'true'") == 6


def test_partial_success_state_is_saved_even_when_a_later_announcement_fails():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert (
        "- name: Detect state changes\n"
        "        id: state_after\n"
        "        if: always() && steps.cadence.outputs.run == 'true'"
    ) in text
    assert (
        "- name: Save announcement state\n"
        "        if: always() && steps.cadence.outputs.run == 'true' && "
        "steps.state_after.outputs.changed == 'true'"
    ) in text


def test_manual_cleanup_requires_announcement_and_keep_message_ids():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "cleanup_announcement_id:" in text
    assert "keep_message_id:" in text
    assert "python tools/cleanup_discord_announcement.py" in text
    assert '--id "$CLEANUP_ANNOUNCEMENT_ID"' in text
    assert '--keep-message-id "$KEEP_MESSAGE_ID"' in text
