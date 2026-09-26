"""Shared test doubles."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from archdiag.llm.client import Message

GOOD_MODEL: dict[str, Any] = {
    "title": "Mini ECU",
    "groups": [{"id": "firmware", "label": "Firmware", "parent": None}],
    "nodes": [
        {
            "id": "app",
            "label": "Application",
            "kind": "application",
            "group": "firmware",
            "paths": ["src/app"],
            "description": "Main loop reading CAN and driving GPIO.",
        },
        {
            "id": "can_driver",
            "label": "CAN driver",
            "kind": "driver",
            "group": "firmware",
            "paths": ["src/drivers/can"],
            "description": "Sends and receives CAN frames.",
        },
        {
            "id": "gpio_hal",
            "label": "GPIO HAL",
            "kind": "hal",
            "group": "firmware",
            "paths": ["src/hal"],
            "description": "Sets output pins.",
        },
    ],
    "edges": [
        {"from": "app", "to": "can_driver", "label": "receives frames", "kind": "uses"},
        {"from": "app", "to": "gpio_hal", "label": "drives outputs", "kind": "uses"},
    ],
    "explanation": "The application loop receives CAN frames and drives GPIO outputs.",
}


def good_reply(**overrides: Any) -> str:
    data = json.loads(json.dumps(GOOD_MODEL))
    data.update(overrides)
    return json.dumps(data)


class FakeClient:
    def __init__(
        self, replies: Sequence[str | Exception] = (), models: dict[str, int] | None = None
    ) -> None:
        self.replies = list(replies)
        self.calls: list[list[Message]] = []
        self.models = models if models is not None else {"qwen3-coder:30b": 19_000_000_000}

    def chat(self, messages: Sequence[Message], schema: dict[str, Any]) -> str:
        self.calls.append(list(messages))
        if not self.replies:
            raise AssertionError("FakeClient ran out of replies")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def list_models(self) -> dict[str, int]:
        return self.models
