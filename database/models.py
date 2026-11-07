"""SQLModel tables: evidence vault (RawPost), extracted PainPoints, scored clusters, run log."""
from datetime import datetime
from typing import Optional

from sqlmodel import Field, Relationship, SQLModel

from utils import utcnow


class RawPost(SQLModel, table=True):
    __tablename__ = "raw_posts"

    id: int | None = Field(default=None, primary_key=True)
    run_id: str = Field(index=True)
    source: str = Field(index=True)
    url: str = Field(index=True, unique=True)
    title: str
    body: str = ""
    date_posted: datetime | None = None
    engagement: int = 0
    collected_at: datetime = Field(default_factory=utcnow)

    pain_points: list["PainPoint"] = Relationship(back_populates="raw_post")


class PainPoint(SQLModel, table=True):
    __tablename__ = "pain_points"

    id: int | None = Field(default=None, primary_key=True)
    raw_post_id: int = Field(foreign_key="raw_posts.id", index=True)
    cluster_id: int | None = Field(
        default=None, foreign_key="opportunity_clusters.id", index=True
    )
    pain_statement: str
    persona: str = ""
    buyer_intent: float = 0.0
    severity: float = 0.0
    current_solution: str = ""
    extraction_fit: bool = False
    created_at: datetime = Field(default_factory=utcnow)

    raw_post: RawPost | None = Relationship(back_populates="pain_points")
    cluster: Optional["OpportunityCluster"] = Relationship(back_populates="pain_points")


class OpportunityCluster(SQLModel, table=True):
    __tablename__ = "opportunity_clusters"

    id: int | None = Field(default=None, primary_key=True)
    run_id: str = Field(index=True)
    cluster_name: str
    total_evidence_count: int = 0
    pain_score: float = 0.0
    score_breakdown: str = "{}"
    product_idea: str = ""
    validation_plan: str = ""
    is_approved: bool = False
    is_rejected: bool = False
    created_at: datetime = Field(default_factory=utcnow)

    pain_points: list[PainPoint] = Relationship(back_populates="cluster")


class Run(SQLModel, table=True):
    __tablename__ = "runs"

    id: str = Field(primary_key=True)
    mode: str = "demo"
    status: str = "running"
    sources: str = ""
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    posts_collected: int = 0
    pains_extracted: int = 0
    clusters_created: int = 0
    errors: str = "[]"