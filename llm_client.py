"""OpenAI-SDK wrapper around LM Studio with task-based model routing.

strategy / clustering -> PRIMARY_MODEL (Qwen 27B Flash)
extraction / classification -> SMALL_MODEL (Qwen MoE 35B Flash)

Handles context limits by truncating the input middle and retrying with a
smaller budget on context-overflow errors. Transient failures get exponential
backoff. In demo mode every method delegates to the deterministic heuristics.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
from openai import (
    APIConnectionError,
    APITimeoutError,
    BadRequestError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)

import heuristics
from config import Settings
from logging_setup import get_logger
from utils import parse_json_loose, retry_call, truncate_text

logger = get_logger(__name__)

PRIMARY_TASKS = {"strategy", "clustering"}
RETRYABLE = (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)

QUERY_SYSTEM = (
    "You are a market-research strategist. Generate targeted web search queries "
    "that surface people complaining about manual work, missing APIs, and data "
    "extraction problems on Hacker News, Reddit, X, and job boards. Mix complaint "
    "phrasing ('frustrated with', 'wish there was'), tool-seeking phrasing, and "
    "workaround phrasing. Respond with JSON only: {\"queries\": [\"...\"]}."
)

EXTRACTION_SYSTEM = (
    "You are a precision business analyst. Read a community post and extract "
    "genuine, specific pain points that could become a software product. Only "
    "extract pains explicitly expressed in the text. Discard noise: pure link "
    "drops, memes, generic discussion without a concrete struggle. For each pain "
    "return: pain_statement (one descriptive sentence), persona (who suffers), "
    "buyer_intent (0-10 likelihood they would pay for a fix), severity (0-10), "
    "current_solution (what they do today, empty string if unknown), extraction_fit "
    "(true if the pain involves extracting, moving, or monitoring data that "
    "software could automate). Respond with JSON only: "
    "{\"pain_points\": [...]} — an empty list when the post is noise."
)

CLUSTER_SYSTEM = (
    "You group customer pain points into semantic opportunity clusters for a "
    "startup studio. Produce 3-8 clusters with short, concrete names. Assign each "
    "pain id to at most one cluster; omit ids that fit nowhere. Respond with JSON "
    "only: {\"clusters\": [{\"cluster_name\": \"...\", \"pain_ids\": [1, 2]}]}."
)

STRATEGY_SYSTEM = (
    "You are a startup strategist. Given a cluster of customer pain points, "
    "produce a concrete software product idea and a cheap validation plan. "
    "Respond with JSON only, exactly these keys: product_idea (2-3 sentences), "
    "validation_plan (numbered steps, tests before building), competition (0-10, "
    "10 = saturated), legal_risk (0-10, 10 = severe legal/ToS risk), reachability "
    "(0-10, how easily the persona can be reached online)."
)


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, settings: Settings, demo: bool = False, client: Any = None):
        self.settings = settings
        self.demo = demo
        self._client = client
        if not demo and client is None:
            self._client = OpenAI(
                base_url=settings.lm_studio_base_url,
                api_key=settings.lm_studio_api_key,
                timeout=settings.llm_timeout_seconds,
                max_retries=0,
                http_client=httpx.Client(trust_env=False, timeout=settings.llm_timeout_seconds),
            )

    def model_for(self, task: str) -> str:
        return self.settings.primary_model if task in PRIMARY_TASKS else self.settings.small_model

    # --- Public task API used by the agents ---

    def generate_queries(self, seeds: list[str], count: int = 20) -> list[str]:
        if self.demo:
            return heuristics.generate_queries(seeds, count)
        data = self._json_task(
            "strategy", QUERY_SYSTEM, json.dumps({"seeds": seeds, "count": count})
        )
        queries = data.get("queries") if isinstance(data, dict) else data
        if not isinstance(queries, list):
            raise LLMError("query generation returned no list")
        return [str(q) for q in queries if str(q).strip()][:count]

    def extract_pain_points(self, post: dict) -> list[dict]:
        if self.demo:
            return heuristics.extract_pain_points(post)
        payload = {
            "title": post.get("title", ""),
            "body": post.get("body", ""),
            "source": post.get("source", ""),
        }
        data = self._json_task("extraction", EXTRACTION_SYSTEM, json.dumps(payload))
        pains = data.get("pain_points", []) if isinstance(data, dict) else []
        return [p for p in pains if isinstance(p, dict)]

    def cluster_pains(self, pains: list[dict]) -> list[dict]:
        if self.demo:
            return heuristics.cluster_pains(pains)
        compact = [
            {"id": p["id"], "pain_statement": p["pain_statement"], "persona": p.get("persona", "")}
            for p in pains
        ]
        compact = self._fit_budget(compact)
        data = self._json_task("clustering", CLUSTER_SYSTEM, json.dumps({"pain_points": compact}))
        clusters = data.get("clusters", []) if isinstance(data, dict) else []
        return [c for c in clusters if isinstance(c, dict) and c.get("pain_ids")]

    def _fit_budget(self, items: list[dict]) -> list[dict]:
        """Drop whole items from the tail until the JSON payload fits the context
        budget, so truncation never cuts a JSON document in half. Dropped pains
        flow to the 'Miscellaneous Signals' cluster downstream."""
        budget = int(self.settings.primary_max_input_chars * 0.8)
        used = len(CLUSTER_SYSTEM) + 200
        kept: list[dict] = []
        for item in items:
            cost = len(json.dumps(item)) + 2
            if used + cost > budget and kept:
                break
            used += cost
            kept.append(item)
        if len(kept) < len(items):
            logger.warning(
                "clustering payload capped at %d of %d pains to fit the %d-char "
                "context budget; the rest are grouped heuristically downstream",
                len(kept), len(items), self.settings.primary_max_input_chars,
            )
        return kept

    def strategize(self, cluster_name: str, pains: list[dict]) -> dict:
        if self.demo:
            return heuristics.strategize(cluster_name, pains)
        payload = {
            "cluster_name": cluster_name,
            "pain_points": [p["pain_statement"] for p in pains][:40],
            "personas": sorted({p.get("persona", "") for p in pains}),
        }
        data = self._json_task("strategy", STRATEGY_SYSTEM, json.dumps(payload))
        if not isinstance(data, dict) or "product_idea" not in data:
            raise LLMError("strategy returned no product_idea")
        return data

    # --- Internals ---

    def _json_task(self, task: str, system: str, user: str):
        model = self.model_for(task)
        limit = (
            self.settings.primary_max_input_chars
            if task in PRIMARY_TASKS
            else self.settings.small_max_input_chars
        )
        for parse_attempt in range(2):
            text = self._request_with_context_fallback(model, system, user, limit)
            try:
                return parse_json_loose(text)
            except ValueError:
                logger.warning("model returned invalid JSON (attempt %d); retrying", parse_attempt + 1)
                system += "\nIMPORTANT: Respond with valid JSON only. No prose, no code fences."
        raise LLMError(f"{task}: model did not return valid JSON")

    def _request_with_context_fallback(self, model: str, system: str, user: str, limit: int) -> str:
        for attempt in range(3):
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": truncate_text(user, limit)},
            ]
            try:
                return self._request(model, messages)
            except BadRequestError as exc:
                if "context" in str(exc).lower() and attempt < 2:
                    limit = max(2000, limit // 2)
                    logger.warning("context overflow; shrinking input budget to %d chars", limit)
                    continue
                raise LLMError(str(exc)) from exc
        raise LLMError("context fallback exhausted")

    def _request(self, model: str, messages: list[dict]) -> str:
        def call():
            resp = self._client.chat.completions.create(
                model=model, messages=messages, temperature=0.2
            )
            return resp.choices[0].message.content or ""

        return retry_call(
            call,
            attempts=self.settings.llm_max_retries,
            base_delay=1.0,
            retry_on=RETRYABLE,
            logger=logger,
        )