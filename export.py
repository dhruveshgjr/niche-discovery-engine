"""System 2 handoff: approved opportunities as JSON/CSV (schema_version 1.0)."""
from __future__ import annotations

import csv
import io
import json

from sqlmodel import Session, select

from database.models import OpportunityCluster, PainPoint, RawPost
from utils import utcnow

HANDOFF_SCHEMA_VERSION = "1.0"


def approved_opportunities(engine) -> list[dict]:
    with Session(engine) as session:
        clusters = session.exec(
            select(OpportunityCluster)
            .where(OpportunityCluster.is_approved == True)  # noqa: E712
            .order_by(OpportunityCluster.pain_score.desc())
        ).all()
        opportunities = []
        for cluster in clusters:
            rows = session.exec(
                select(PainPoint, RawPost)
                .join(RawPost, PainPoint.raw_post_id == RawPost.id)
                .where(PainPoint.cluster_id == cluster.id)
            ).all()
            opportunities.append({
                "id": cluster.id,
                "cluster_name": cluster.cluster_name,
                "pain_score": cluster.pain_score,
                "score_breakdown": json.loads(cluster.score_breakdown or "{}"),
                "product_idea": cluster.product_idea,
                "validation_plan": cluster.validation_plan,
                "total_evidence_count": cluster.total_evidence_count,
                "evidence": [
                    {
                        "pain_statement": pain.pain_statement,
                        "persona": pain.persona,
                        "buyer_intent": pain.buyer_intent,
                        "source": post.source,
                        "url": post.url,
                        "title": post.title,
                    }
                    for pain, post in rows
                ],
            })
        return opportunities


def export_json(engine) -> str:
    return json.dumps(
        {
            "schema_version": HANDOFF_SCHEMA_VERSION,
            "exported_at": utcnow().isoformat(),
            "opportunities": approved_opportunities(engine),
        },
        indent=2,
        ensure_ascii=False,
    )


CSV_FIELDS = [
    "id", "cluster_name", "pain_score", "total_evidence_count",
    "product_idea", "validation_plan", "evidence_urls",
]


def export_csv(engine) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS)
    writer.writeheader()
    for opp in approved_opportunities(engine):
        writer.writerow({
            "id": opp["id"],
            "cluster_name": opp["cluster_name"],
            "pain_score": opp["pain_score"],
            "total_evidence_count": opp["total_evidence_count"],
            "product_idea": opp["product_idea"],
            "validation_plan": opp["validation_plan"],
            "evidence_urls": " | ".join(e["url"] for e in opp["evidence"]),
        })
    return buffer.getvalue()