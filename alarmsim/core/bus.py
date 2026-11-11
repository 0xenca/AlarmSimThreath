"""Bus en memoria para enviar novedades al navegador (Server-Sent Events)."""
from __future__ import annotations

import asyncio
import json
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._subs: set[asyncio.Queue[str]] = set()

    def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=1000)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        self._subs.discard(q)

    def publish(self, kind: str, data: dict[str, Any]) -> None:
        payload = json.dumps({"type": kind, **data}, default=str)
        for q in list(self._subs):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass


bus = EventBus()
