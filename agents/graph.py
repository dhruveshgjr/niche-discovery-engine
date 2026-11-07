"""LangGraph orchestration: scout -> collector -> extractor -> cluster -> strategist."""
from __future__ import annotations

import json
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from sqlmodel import Session

from agents.cluster import make_cluster_node
from agents.collector import make_collector_node
from agents.extractor import make_extractor_node
from agents.scout import make_scout_node
from agents.state import Deps, PipelineState
from agents.strategist import make_strategist_node
from config import Settings, resolve_demo_mode
from database.db import create_db_engine, init_db, session_scope
from database.models import Run
from llm_client import LLMClient
from logging_setup import get_logger, set_log_run_id
from utils import utcnow

logger = get_logger(__name__)


def build_graph(deps: Deps):
    graph = StateGraph(PipelineState)
    graph.add_node("scout", make_scout_node(deps))
    graph.add_node("collector", make_collector_node(deps))
    graph.add_node("extractor", make_extractor_node(deps))
    graph.add_node("cluster", make_cluster_node(deps))
    graph.add_node("strategist", make_strategist_node(deps))
    graph.add_edge(START, "scout")
    graph.add_edge("scout", "collector")
    graph.add_edge("collector", "extractor")
    graph.add_edge("extractor", "cluster")
    graph.add_edge("cluster", "strategist")
    graph.add_edge("strategist", END)
    return graph.compile()


def run_pipeline(
    settings: Settings | None = None,
    sources: list[str] | None = None,
    limit_per_source: int | None = None,
    demo: bool | None = None,
) -> PipelineState:
    settings = settings or Settings()
    demo_mode = resolve_demo_mode(settings) if demo is None else demo
    engine = create_db_engine(settings.resolved_database_url)
    init_db(engine)
    llm = LLMClient(settings, demo=demo_mode)
    deps = Deps(settings=settings, llm=llm, engine=engine)

    run_id = uuid4().hex
    names = sources or settings.enabled_source_list
    with session_scope(engine) as session:
        session.add(Run(
            id=run_id,
            mode="demo" if demo_mode else "live",
            sources=",".join(names),
        ))

    initial: PipelineState = {
        "run_id": run_id,
        "demo_mode": demo_mode,
        "sources": names,
        "limit_per_source": limit_per_source
        if limit_per_source is not None
        else settings.items_per_source,
        "errors": [],
        "status": "running",
    }
    set_log_run_id(run_id)
    logger.info("run %s starting (mode=%s, sources=%s)",
                run_id[:8], "demo" if demo_mode else "live", ",".join(names))
    try:
        result = build_graph(deps).invoke(initial)
        status = result.get("status", "completed")
    except Exception as exc:
        logger.exception("run %s failed", run_id[:8])
        result = {**initial, "status": "failed", "errors": initial["errors"] + [str(exc)]}
        status = "failed"

    with Session(engine) as session:
        run = session.get(Run, run_id)
        run.status = status
        run.finished_at = utcnow()
        run.posts_collected = result.get("posts_collected", 0)
        run.pains_extracted = result.get("pains_extracted", 0)
        run.clusters_created = result.get("clusters_created", 0)
        run.errors = json.dumps(result.get("errors", []))
        session.add(run)
        session.commit()

    result["run_id"] = run_id
    logger.info(
        "run %s %s: %d posts, %d pains, %d clusters",
        run_id[:8], status, result.get("posts_collected", 0),
        result.get("pains_extracted", 0), result.get("clusters_created", 0),
    )
    return result