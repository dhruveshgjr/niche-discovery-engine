"""Shared LangGraph state and dependency container for the agent nodes."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from config import Settings
from llm_client import LLMClient


class PipelineState(TypedDict, total=False):
    run_id: str
    demo_mode: bool
    sources: list[str]
    limit_per_source: int
    queries: list[str]
    posts_collected: int
    pains_extracted: int
    clusters_created: int
    errors: list[str]
    status: str


@dataclass
class Deps:
    settings: Settings
    llm: LLMClient
    engine: object