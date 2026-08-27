from pathlib import Path


WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "check-announcements.yml"


def test_partial_success_state_is_saved_even_when_a_later_announcement_fails():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "- name: Detect state changes\n        id: state_after\n        if: always()" in text
    assert (
        "- name: Save announcement state\n"
        "        if: always() && steps.state_after.outputs.changed == 'true'"
    ) in text
