"""Halt-file tests: case-sensitive names, mount fallback, message text."""

from pathlib import Path

from core.halt import find_halt_file, halt_message, resolve_halt_dir


def test_no_file_means_no_halt(tmp_path):
    assert find_halt_file(tmp_path, tmp_path / "workspace") is None
    assert halt_message(tmp_path, tmp_path / "workspace") is None


def test_halt_and_stop_are_detected(tmp_path):
    (tmp_path / "HALT").write_text("")
    assert find_halt_file(tmp_path, tmp_path) == "HALT"

    (tmp_path / "HALT").unlink()
    (tmp_path / "STOP").write_text("")
    assert find_halt_file(tmp_path, tmp_path) == "STOP"


def test_lowercase_names_are_ignored(tmp_path):
    (tmp_path / "halt").write_text("")
    (tmp_path / "stop").write_text("")
    assert find_halt_file(tmp_path, tmp_path) is None


def test_directory_named_halt_is_not_a_file(tmp_path):
    (tmp_path / "HALT").mkdir()
    assert find_halt_file(tmp_path, tmp_path) is None


def test_missing_configured_dir_falls_back_to_the_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    configured = tmp_path / "does-not-exist"

    assert resolve_halt_dir(configured, workspace) == workspace
    (workspace / "STOP").write_text("")
    assert find_halt_file(configured, workspace) == "STOP"


def test_existing_configured_dir_wins(tmp_path):
    mount = tmp_path / "mount"
    mount.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    assert resolve_halt_dir(mount, workspace) == mount


def test_halt_message_names_the_file_and_directory(tmp_path):
    (tmp_path / "HALT").write_text("")

    message = halt_message(tmp_path, tmp_path)

    assert "HALT" in message
    assert str(Path(tmp_path)) in message
    assert "Remove it to continue" in message
