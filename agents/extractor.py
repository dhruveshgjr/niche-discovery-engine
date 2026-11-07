"""Extractor: SMALL model reads each RawPost and emits structured PainPoints.

Each post is processed in its own short transaction: the post is read, the
session closes, the LLM runs without holding any database transaction, and
only then are the extracted pains committed. A crash mid-run keeps every
already-extracted post's pains instead of losing the whole batch.
"""
from __future__ import annotations

from pydantic import ValidationError
from sqlmodel import Session, select

from agents.schemas import PainPointDraft
from agents.state import Deps, PipelineState
from database.db import session_scope
from database.models import PainPoint, RawPost
from logging_setup import get_logger

logger = get_logger(__name__)


def make_extractor_node(deps: Deps):
    def extractor(state: PipelineState) -> dict:
        errors = list(state.get("errors", []))
        extracted = 0
        discarded = 0
        with Session(deps.engine) as session:
            post_ids = session.exec(
                select(RawPost.id)
                .where(RawPost.run_id == state["run_id"])
                .limit(deps.settings.max_posts_to_extract)
            ).all()

        for post_id in post_ids:
            with Session(deps.engine) as session:
                post = session.get(RawPost, post_id)
            if post is None:
                continue
            try:
                drafts = deps.llm.extract_pain_points({
                    "title": post.title, "body": post.body, "source": post.source,
                })
            except Exception as exc:
                logger.warning("extractor: post %s failed: %s", post_id, exc)
                errors.append(f"extract post {post_id}: {exc}")
                continue
            valid = []
            for draft in drafts:
                try:
                    valid.append(PainPointDraft.model_validate(draft))
                except ValidationError:
                    discarded += 1
            if not valid:
                continue
            with session_scope(deps.engine) as session:
                for draft in valid:
                    session.add(PainPoint(raw_post_id=post_id, **draft.model_dump()))
            extracted += len(valid)

        logger.info("extractor: %d pain points (%d noise drafts discarded)", extracted, discarded)
        return {"pains_extracted": extracted, "errors": errors}

    return extractor
