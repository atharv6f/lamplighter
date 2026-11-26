import pytest

from lamplighter.app.main import main


def test_main_runs(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "lamplighter" in capsys.readouterr().out
