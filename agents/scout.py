"""Scout: expand seed keywords into targeted search queries (PRIMARY model)."""
from __future__ import annotations

import heuristics
from agents.state import Deps, PipelineState
from logging_setup import get_logger

logger = get_logger(__name__)


def make_scout_node(deps: Deps):
    def scout(state: PipelineState) -> dict:
        seeds = deps.settings.seed_keyword_list
        count = deps.settings.query_count
        try:
            queries = deps.llm.generate_queries(seeds, count)
        except Exception as exc:
            logger.warning("LLM query generation failed (%s); using template expansion", exc)
            queries = heuristics.generate_queries(seeds, count)
        queries = list(dict.fromkeys(q.strip() for q in queries if q.strip()))[:count]
        logger.info("scout: %d queries from %d seeds", len(queries), len(seeds))
        return {"queries": queries}

    return scout