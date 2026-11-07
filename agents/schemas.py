"""Validation for LLM-produced pain points; invalid drafts are discarded as noise."""
from __future__ import annotations

from pydantic import BaseModel, field_validator

from utils import clamp

NOISE_VALUES = {"", "none", "n/a", "null", "no pain points"}


class PainPointDraft(BaseModel):
    pain_statement: str
    persona: str = ""
    buyer_intent: float = 0.0
    severity: float = 0.0
    current_solution: str = ""
    extraction_fit: bool = False

    @field_validator("pain_statement")
    @classmethod
    def meaningful(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 10 or value.lower() in NOISE_VALUES:
            raise ValueError("noise")
        return value

    @field_validator("buyer_intent", "severity", mode="before")
    @classmethod
    def score_range(cls, value):
        try:
            return clamp(float(value))
        except (TypeError, ValueError):
            return 0.0

    @field_validator("extraction_fit", mode="before")
    @classmethod
    def to_bool(cls, value):
        if isinstance(value, str):
            return value.strip().lower() in {"true", "yes", "1"}
        return bool(value)