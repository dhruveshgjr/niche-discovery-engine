"""Cluster: PRIMARY model semantically groups PainPoints into OpportunityClusters.

Pains are read in a short session, the LLM run happens with no transaction
open, then clusters and assignments are persisted in one write transaction.
Every pain the LLM failed to place lands in a "Miscellaneous Signals" cluster
so no evidence is ever dropped.
"""
from __future__ import annotations

from sqlmodel import Session, select

import heuristics
from agents.state import Deps, PipelineState
from database.db import session_scope
from database.models import OpportunityCluster, PainPoint, RawPost
from logging_setup import get_logger

logger = get_logger(__name__)


def make_cluster_node(deps: Deps):
    def cluster(state: PipelineState) -> dict:
        errors = list(state.get("errors", []))
        with Session(deps.engine) as session:
            pains = session.exec(
                select(PainPoint).join(RawPost, PainPoint.raw_post_id == RawPost.id)
                .where(RawPost.run_id == state["run_id"])
            ).all()
        if not pains:
            return {"clusters_created": 0, "errors": errors}

        payload = [
            {"id": p.id, "pain_statement": p.pain_statement, "persona": p.persona}
            for p in pains
        ]
        try:
            groups = deps.llm.cluster_pains(payload)
        except Exception as exc:
            logger.warning("LLM clustering failed (%s); using heuristic grouping", exc)
            errors.append(f"cluster: {exc}")
            groups = heuristics.cluster_pains(payload)

        valid_ids = {p.id for p in pains}
        assigned: set[int] = set()
        created = 0
        with session_scope(deps.engine) as session:
            for group in groups:
                ids = [i for i in group.get("pain_ids", []) if i in valid_ids and i not in assigned]
                if not ids:
                    continue
                assigned.update(ids)
                record = OpportunityCluster(
                    run_id=state["run_id"],
                    cluster_name=str(group.get("cluster_name", "Untitled"))[:200],
                    total_evidence_count=len(ids),
                )
                session.add(record)
                session.flush()
                for pain_id in ids:
                    session.get(PainPoint, pain_id).cluster_id = record.id
                created += 1

            leftover = valid_ids - assigned
            if leftover:
                record = OpportunityCluster(
                    run_id=state["run_id"],
                    cluster_name="Miscellaneous Signals",
                    total_evidence_count=len(leftover),
                )
                session.add(record)
                session.flush()
                for pain_id in leftover:
                    session.get(PainPoint, pain_id).cluster_id = record.id
                created += 1

        logger.info("cluster: created %d clusters", created)
        return {"clusters_created": created, "errors": errors}

    return cluster
