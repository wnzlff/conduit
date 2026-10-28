from pathlib import Path

from conduit.core.history import CommandHistory
from conduit.core.snippets import DEFAULT_SNIPPETS, Snippet, SnippetStore


def test_defaults_when_no_file(tmp_path: Path) -> None:
    store = SnippetStore(tmp_path / "snippets.json")
    store.load()
    assert len(store.snippets) == len(DEFAULT_SNIPPETS)
    assert not (tmp_path / "snippets.json").exists()


def test_roundtrip_and_sorting(tmp_path: Path) -> None:
    store = SnippetStore(tmp_path / "snippets.json")
    store.snippets = []
    store.upsert(Snippet(name="zeta", body="echo z", run=True))
    store.upsert(Snippet(name="Alpha", body="echo a\necho b", description="two lines"))
    store.save()
    loaded = SnippetStore(tmp_path / "snippets.json")
    loaded.load()
    assert [s.name for s in loaded.sorted()] == ["Alpha", "zeta"]
    assert loaded.as_tuples()[0] == ("Alpha", "two lines", "echo a\necho b", False)
    alpha = loaded.sorted()[0]
    alpha.body = "changed"
    loaded.upsert(alpha)
    assert loaded.get(alpha.id).body == "changed"
    loaded.remove(alpha.id)
    assert [s.name for s in loaded.snippets] == ["zeta"]


def test_history_dedupes_and_limits(tmp_path: Path) -> None:
    history = CommandHistory(tmp_path / "h.json", limit=3)
    for cmd in ["ls -la", "cd /tmp", "ls -la", "htop", "df -h"]:
        history.add(cmd)
    assert history.items == ["df -h", "htop", "ls -la"]
    history.add("x")  # too short, ignored
    assert history.items[0] == "df -h"
    history.save()
    again = CommandHistory(tmp_path / "h.json", limit=3)
    again.load()
    assert again.items == ["df -h", "htop", "ls -la"]
