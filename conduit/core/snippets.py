"""User-defined snippets: reusable command text that can be inserted into any session."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_SNIPPETS = [
    {
        "name": "disk usage here",
        "description": "Largest items in the current directory",
        "body": "du -sh * 2>/dev/null | sort -h | tail -n 20",
        "run": True,
    },
    {
        "name": "follow syslog",
        "description": "Follow the system journal",
        "body": "sudo journalctl -f -n 50",
        "run": True,
    },
    {
        "name": "ports",
        "description": "Listening ports with owning processes",
        "body": "sudo ss -tulpn",
        "run": True,
    },
]


@dataclass
class Snippet:
    name: str
    body: str
    description: str = ""
    run: bool = False  # press Enter after inserting
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Snippet":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


class SnippetStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.snippets: list[Snippet] = []

    def load(self) -> None:
        self.snippets = []
        if not self.path.exists():
            self.snippets = [Snippet.from_dict(d) for d in DEFAULT_SNIPPETS]
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.error("Could not read snippets %s: %s", self.path, exc)
            return
        for item in data.get("snippets", []):
            if isinstance(item, dict) and item.get("name"):
                try:
                    self.snippets.append(Snippet.from_dict(item))
                except TypeError as exc:
                    log.warning("Skipping malformed snippet %r: %s", item, exc)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "snippets": [s.to_dict() for s in self.snippets]}
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def get(self, snippet_id: str) -> Snippet | None:
        return next((s for s in self.snippets if s.id == snippet_id), None)

    def upsert(self, snippet: Snippet) -> None:
        for i, existing in enumerate(self.snippets):
            if existing.id == snippet.id:
                self.snippets[i] = snippet
                return
        self.snippets.append(snippet)

    def remove(self, snippet_id: str) -> None:
        self.snippets = [s for s in self.snippets if s.id != snippet_id]

    def sorted(self) -> list[Snippet]:
        return sorted(self.snippets, key=lambda s: s.name.lower())

    def as_tuples(self) -> list[tuple[str, str, str, bool]]:
        return [(s.name, s.description, s.body, s.run) for s in self.sorted()]
