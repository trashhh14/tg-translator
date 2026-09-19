from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any


class SettingsStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._data: dict[str, dict[str, Any]] = {}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._data = loaded
            except json.JSONDecodeError:
                self._data = {}

    def get(self, user_id: int, native: str = "ru", foreign: str = "en") -> dict[str, str]:
        stored = self._data.get(str(user_id), {})
        return {
            "native": stored.get("native") or native,
            "foreign": stored.get("foreign") or foreign,
        }

    async def set(self, user_id: int, **fields: str) -> dict[str, str]:
        async with self._lock:
            current = dict(self._data.get(str(user_id), {}))
            current.update({k: v for k, v in fields.items() if v})
            self._data[str(user_id)] = current
            self.path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return {
                "native": current.get("native", "ru"),
                "foreign": current.get("foreign", "en"),
            }
