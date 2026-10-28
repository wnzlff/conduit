import json
from pathlib import Path

from conduit.core.models import AuthMethod, Connection
from conduit.core.settings import Settings
from conduit.core.storage import ConnectionStore


def test_roundtrip(tmp_path: Path) -> None:
    store = ConnectionStore(tmp_path / "connections.json")
    a = Connection(name="Web", host="10.0.0.1", username="root", group="Prod", auth_method=AuthMethod.KEY, key_path="C:/k.ppk")
    b = Connection(name="Db", host="db.example", port=2222, username="admin")
    store.upsert(a)
    store.upsert(b)
    store.save()

    loaded = ConnectionStore(tmp_path / "connections.json")
    loaded.load()
    assert [c.name for c in loaded.connections] == ["Web", "Db"]
    assert loaded.groups == ["Prod"]
    web = loaded.get(a.id)
    assert web is not None and web.auth_method == AuthMethod.KEY and web.key_path == "C:/k.ppk"
    assert loaded.get(b.id).port == 2222
    assert b.display_target == "admin@db.example:2222"


def test_groups_and_search(tmp_path: Path) -> None:
    store = ConnectionStore(tmp_path / "c.json")
    store.add_group("Staging")
    store.upsert(Connection(name="s1", host="1.1.1.1", group="Staging"))
    store.upsert(Connection(name="p1", host="2.2.2.2", group="Prod"))
    store.upsert(Connection(name="loose", host="3.3.3.3"))
    grouped = store.by_group()
    assert list(grouped) == ["Staging", "Prod", ""]
    assert [c.name for c in grouped[""]] == ["loose"]
    assert [c.name for c in store.search("2.2")] == ["p1"]
    assert [c.name for c in store.search("stag")] == ["s1"]

    store.rename_group("Prod", "Production")
    assert store.get_by_name("p1").group == "Production"
    store.remove_group("Staging")
    assert store.get_by_name("s1").group == ""
    assert "Staging" not in store.groups


def test_malformed_entries_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"connections": [{"name": "ok", "host": "h"}, {"nonsense": 1}, "junk"]}))
    store = ConnectionStore(path)
    store.load()
    assert [c.name for c in store.connections] == ["ok"]


def test_from_dict_tolerates_unknown_fields() -> None:
    c = Connection.from_dict({"name": "x", "host": "h", "port": "2200", "auth_method": "bogus", "future_field": 1})
    assert c.port == 2200
    assert c.auth_method == AuthMethod.PASSWORD


def test_settings_roundtrip(tmp_path: Path) -> None:
    s = Settings(theme="light", font_size=14)
    s.save(tmp_path / "s.json")
    loaded = Settings.load(tmp_path / "s.json")
    assert loaded.theme == "light" and loaded.font_size == 14
    assert loaded.resolved_font_family()
    (tmp_path / "s.json").write_text("{not json")
    assert Settings.load(tmp_path / "s.json").theme == "dark"
