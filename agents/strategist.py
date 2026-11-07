"""Strategist: score clusters and generate product ideas + validation plans.

pain_score = severity*0.25 + buyer_intent*0.25 + frequency*0.15
           + extraction_fit*0.20 + reachability*0.15
           - legal_risk_penalty - competition_penalty

All components are 0-10. frequency = min(10, 2*evidence_count). Penalties map
the model's 0-10 risk ratings onto 0-2 points each.
"""
from __future__ import annotations

import json

from sqlmodel import Session, select

import heuristics
from agents.state import Deps, PipelineState
from database.db import session_scope
from database.models import OpportunityCluster, PainPoint
from logging_setup import get_logger
from utils import clamp

logger = get_logger(__name__)


def compute_score(
    severity: float,
    buyer_intent: float,
    evidence_count: int,
    extraction_ratio: float,
    reachability: float,
    legal_risk: float,
    competition: float,
) -> tuple[float, dict]:
    frequency = min(10.0, 2.0 * evidence_count)
    components = {
        "pain_severity": clamp(severity),
        "buyer_intent": clamp(buyer_intent),
        "frequency": frequency,
        "extraction_fit": clamp(extraction_ratio * 10.0),
        "reachability": clamp(reachability),
    }
    weighted = (
        components["pain_severity"] * 0.25
        + components["buyer_intent"] * 0.25
        + components["frequency"] * 0.15
        + components["extraction_fit"] * 0.20
        + components["reachability"] * 0.15
    )
    legal_penalty = clamp(legal_risk) / 10.0 * 2.0
    competition_penalty = clamp(competition) / 10.0 * 2.0
    score = round(max(0.0, weighted - legal_penalty - competition_penalty), 2)
    breakdown = {
        **components,
        "legal_risk_penalty": round(legal_penalty, 2),
        "competition_penalty": round(competition_penalty, 2),
        "weighted_sum": round(weighted, 2),
    }
    return score, breakdown


def _strategy_number(strategy: dict, key: str, default: float) -> float:
    try:
        return float(strategy[key])
    except (KeyError, TypeError, ValueError):
        return default


def make_strategist_node(deps: Deps):
    def strategist(state: PipelineState) -> dict:
        errors = list(state.get("errors", []))
        with Session(deps.engine) as session:
            cluster_ids = session.exec(
                select(OpportunityCluster.id)
                .where(OpportunityCluster.run_id == state["run_id"])
            ).all()

        for cluster_id in cluster_ids:
            with Session(deps.engine) as session:
                record = session.get(OpportunityCluster, cluster_id)
                if record is None:
                    continue
                cluster_name = record.cluster_name
                evidence_count = record.total_evidence_count
                pains = session.exec(
                    select(PainPoint).where(PainPoint.cluster_id == cluster_id)
                ).all()
                count = len(pains) or 1
                severity = sum(p.severity for p in pains) / count
                buyer_intent = sum(p.buyer_intent for p in pains) / count
                extraction_ratio = sum(1.0 for p in pains if p.extraction_fit) / count
                payload = [
                    {"pain_statement": p.pain_statement, "persona": p.persona}
                    for p in pains
                ]

            try:
                strategy = deps.llm.strategize(cluster_name, payload)
            except Exception as exc:
                logger.warning("strategist: LLM failed for %r (%s); heuristic fallback",
                               cluster_name, exc)
                errors.append(f"strategize {cluster_name}: {exc}")
                strategy = heuristics.strategize(cluster_name, payload)

            score, breakdown = compute_score(
                severity=severity,
                buyer_intent=buyer_intent,
                evidence_count=evidence_count,
                extraction_ratio=extraction_ratio,
                reachability=_strategy_number(strategy, "reachability", 6.5),
                legal_risk=_strategy_number(strategy, "legal_risk", 5.0),
                competition=_strategy_number(strategy, "competition", 5.0),
            )
            with session_scope(deps.engine) as session:
                target = session.get(OpportunityCluster, cluster_id)
                if target is None:
                    continue
                target.pain_score = score
                target.score_breakdown = json.dumps(breakdown)
                target.product_idea = str(strategy.get("product_idea", ""))
                target.validation_plan = str(strategy.get("validation_plan", ""))
            logger.debug("strategist: cluster %r scored %.2f", cluster_name, score)

        logger.info("strategist: scored %d clusters", len(cluster_ids))
        return {"status": "completed", "errors": errors}

    return strategist
