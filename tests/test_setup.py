import pytest

from lamplighter.app.main import main


def test_main_runs(capsys: pytest.CaptureFixture[str]) -> None:
    main()
    assert "lamplighter" in capsys.readouterr().out
