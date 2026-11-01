"""The live design test's transcript redaction (tests/live/live_helpers.py): emails only, so a
flow id made per machine (``<flow>@<machine>``) is kept as it is."""

from live.live_helpers import redact


def test_an_email_is_redacted():
    assert redact("mail someone.name+x@gmail.com now", []) == "mail [email] now"
    assert redact("<noreply@anthropic.com>", []) == "<[email]>"
    assert redact("at a.b@mail.example.co.uk.", []) == "at [email]."


def test_a_flow_id_with_an_at_is_not_an_email():
    for fid in ("test_report_files_read@orchestra.instruments.test_reports", "repo_files_in@bot.jira",
                "notes_in@planner", "x@repo_stats.sh_out", "flow@gate.landed_rate"):
        assert redact(f"  {fid} (outside -> test_report_files_read)", []) == f"  {fid} (outside -> test_report_files_read)"


def test_the_demos_own_address_and_tokens():
    assert redact("demo@example.invalid", []) == "demo@example.invalid"
    assert redact("Bearer abcdefghijklmnop sk-ant-xyz123", ["abcdefghijklmnop"]) == "Bearer [redacted] [redacted]"
