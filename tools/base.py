"""Base tool interface for class-based tool wrappers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseTool(ABC):
    name: str
    description: str
    input_schema: dict

    deferred: bool = False
    read_only: bool = False
    edit_operation: bool = False
    concurrency_safe: bool = False
    requires_confirmation: bool = False

    def definition(self) -> dict:
        payload = {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
        if self.deferred:
            payload["deferred"] = True
        return payload

    def required_fields(self) -> list[str]:
        return list(self.input_schema.get("required", []) or [])

    @abstractmethod
    def run(self, inp: dict) -> Any:
        raise NotImplementedError
