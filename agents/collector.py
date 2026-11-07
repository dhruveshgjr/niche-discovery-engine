"""Collector: run queries against enabled sources, persist RawPosts.

A failing source never kills the run — it is logged and skipped. Posts are
deduped globally by URL (across all runs) so nightly re-scans don't inflate
the evidence vault. Each post is saved in its own short transaction, so one
bad insert or a mid-save crash never loses the rest of the batch. If every
live source fails or returns nothing, local fixtures are loaded so downstream
agents still have evidence to work with.
"""
from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agents.state import Deps, PipelineState
from database.db import session_scope
from database.models import RawPost
from fixtures import load_fixture_posts
from logging_setup import get_logger
from scraper.safety import is_safe
from scraper.sources import build_sources
from utils import parse_date

logger = get_logger(__name__)


def make_collector_node(deps: Deps, source_factory=None):
    factory = source_factory or (lambda names: build_sources(names, deps.settings))

    def collector(state: PipelineState) -> dict:
        errors = list(state.get("errors", []))
        names = state.get("sources") or deps.settings.enabled_source_list
        limit = state.get("limit_per_source", deps.settings.items_per_source)
        posts: list[dict] = []

        if state.get("demo_mode"):
            posts = load_fixture_posts(names)
            logger.info("collector: demo mode, loaded %d fixture posts", len(posts))
        else:
            for source in factory(names):
                try:
                    got = source.collect(state.get("queries", []), limit)
                    logger.info("collector: %s returned %d posts", source.name, len(got))
                    posts.extend(got)
                except Exception as exc:
                    logger.warning("collector: source %s failed: %s", source.name, exc)
                    errors.append(f"{source.name}: {exc}")
                finally:
                    close = getattr(source, "close", None)
                    if callable(close):
                        close()
            if not posts:
                logger.warning("collector: all live sources empty/failed — loading fixtures")
                posts = load_fixture_posts(names)
                errors.append("live sources returned no data; used local fixtures")

        saved = save_posts(deps.engine, state["run_id"], posts, limit)
        logger.info("collector: saved %d new posts", saved)
        return {"posts_collected": saved, "errors": errors}

    return collector


def save_posts(engine, run_id: str, posts: list[dict], limit_per_source: int) -> int:
    per_source: dict[str, int] = {}
    saved = 0
    with Session(engine) as session:
        existing = set(session.exec(select(RawPost.url)).all())
    for post in posts:
        source = post.get("source", "unknown")
        if per_source.get(source, 0) >= limit_per_source:
            continue
        url = post.get("url", "")
        if not url or url in existing:
            continue
        if not is_safe(f"{post.get('title', '')} {post.get('body', '')}"):
            logger.info("collector: filtered unsafe post %s", url)
            continue
        with session_scope(engine) as session:
            try:
                session.add(RawPost(
                    run_id=run_id,
                    source=source,
                    url=url,
                    title=post.get("title", "")[:500],
                    body=post.get("body", ""),
                    date_posted=parse_date(post.get("date_posted")),
                    engagement=int(post.get("engagement") or 0),
                ))
            except IntegrityError:
                session.rollback()
                logger.warning("collector: url %s already stored; skipping", url)
                continue
        existing.add(url)
        per_source[source] = per_source.get(source, 0) + 1
        saved += 1
    return saved
