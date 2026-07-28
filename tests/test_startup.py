"""Startup tests for the prototype."""

from business_analysis_agents.controller import run


def test_run_prints_startup_message(capsys) -> None:
    """The current prototype should start with a clear CLI message."""

    run()
    captured = capsys.readouterr()
    assert "システムを開始しました" in captured.out
