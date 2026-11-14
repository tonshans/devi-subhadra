"""Unit tests for confined workspace/atelier file operations."""

from plugins.file_manager import FileManager


def _manager(tmp_path) -> FileManager:
    plugin = FileManager()
    plugin.initialize(
        {
            "paths": {
                "workspace_dir": str(tmp_path / "workspace"),
                "atelier_dir": str(tmp_path / "atelier"),
            }
        }
    )
    return plugin


def test_writes_and_reads_inside_workspace(tmp_path):
    plugin = _manager(tmp_path)

    written = plugin.execute(
        {"action": "write", "path": "notes/todo.txt", "content": "remember the ledger"}
    )
    assert written.success is True
    assert (tmp_path / "workspace" / "notes" / "todo.txt").read_text() == (
        "remember the ledger"
    )

    read = plugin.execute({"action": "read", "path": "notes/todo.txt"})
    assert read.success is True
    assert read.data["content"] == "remember the ledger"


def test_blocks_path_traversal(tmp_path):
    plugin = _manager(tmp_path)

    result = plugin.execute(
        {"action": "write", "path": "../../escape.txt", "content": "nope"}
    )

    assert result.success is False
    assert "outside" in result.error_message.lower()
    assert not (tmp_path / "escape.txt").exists()


def test_blocks_absolute_paths_outside_root(tmp_path):
    plugin = _manager(tmp_path)

    result = plugin.execute({"action": "read", "path": "/etc/passwd"})

    assert result.success is False
    assert not result.success


def test_atelier_root_is_separate_from_workspace(tmp_path):
    plugin = _manager(tmp_path)

    result = plugin.execute(
        {
            "action": "write",
            "root": "atelier",
            "path": "essay.md",
            "content": "independent study",
        }
    )

    assert result.success is True
    assert (tmp_path / "atelier" / "essay.md").read_text() == "independent study"
    assert not (tmp_path / "workspace" / "essay.md").exists()


def test_lists_directory_and_reports_missing_file(tmp_path):
    plugin = _manager(tmp_path)
    plugin.execute({"action": "write", "path": "a.txt", "content": "a"})
    plugin.execute({"action": "write", "path": "b.txt", "content": "b"})

    listed = plugin.execute({"action": "list", "path": "."})
    assert listed.success is True
    assert listed.data["entries"] == ["a.txt", "b.txt"]

    missing = plugin.execute({"action": "read", "path": "missing.txt"})
    assert missing.success is False