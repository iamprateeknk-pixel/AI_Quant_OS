import subprocess

import pytest

from scripts import start_data_stack


def test_missing_credentials_do_not_rotate_existing_database(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".local-stack.env"
    monkeypatch.setattr(start_data_stack, "ENV_FILE", env_file)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0),
    )

    with pytest.raises(RuntimeError, match="existing local data"):
        start_data_stack.ensure_environment()
    assert not env_file.exists()


def test_incomplete_credentials_are_not_silently_replaced(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".local-stack.env"
    env_file.write_text("AI_QUANT_LOCAL_POSTGRES_PASSWORD=partial\n", encoding="utf-8")
    monkeypatch.setattr(start_data_stack, "ENV_FILE", env_file)
    with pytest.raises(RuntimeError, match="incomplete"):
        start_data_stack.ensure_environment()
    assert env_file.read_text(encoding="utf-8") == "AI_QUANT_LOCAL_POSTGRES_PASSWORD=partial\n"
