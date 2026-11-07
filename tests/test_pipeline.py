import csv
import io
import json

from sqlmodel import Session, select

from agents.graph import run_pipeline
from database.models import OpportunityCluster, PainPoint, RawPost, Run
from export import CSV_FIELDS, approved_opportunities, export_csv, export_json


def test_end_to_end_demo_run(settings):
    result = run_pipeline(settings, sources=["hacker_news", "reddit"],
                          limit_per_source=10, demo=True)
    assert result["status"] == "completed"
    assert result["posts_collected"] > 0
    assert result["pains_extracted"] > 0
    assert result["clusters_created"] > 0

    from database.db import create_db_engine
    engine = create_db_engine(settings.resolved_database_url)
    with Session(engine) as session:
        run = session.get(Run, result["run_id"])
        assert run.status == "completed"
        assert run.mode == "demo"
        assert run.finished_at is not None
        clusters = session.exec(select(OpportunityCluster)).all()
        assert all(c.pain_score >= 0 for c in clusters)
        assert all(c.product_idea for c in clusters)
        # noise fixture (library release) must not produce a pain point
        posts = session.exec(select(RawPost)).all()
        pains = session.exec(select(PainPoint)).all()
        assert len(pains) < len(posts)


def test_rerun_does_not_duplicate_posts(settings):
    first = run_pipeline(settings, sources=["hacker_news"], limit_per_source=10, demo=True)
    second = run_pipeline(settings, sources=["hacker_news"], limit_per_source=10, demo=True)
    assert first["run_id"] != second["run_id"]
    assert first["posts_collected"] > 0
    # global URL dedupe: nightly re-scans attach nothing new to the vault
    assert second["posts_collected"] == 0
    assert second["pains_extracted"] == 0
    assert second["status"] == "completed"


def test_limit_zero_yields_empty_but_complete_run(settings):
    result = run_pipeline(settings, sources=["hacker_news"], limit_per_source=0, demo=True)
    assert result["status"] == "completed"
    assert result["posts_collected"] == 0
    assert result["pains_extracted"] == 0
    assert result["clusters_created"] == 0


def test_export_handoff_schema(settings):
    run_pipeline(settings, sources=["hacker_news"], limit_per_source=10, demo=True)
    from database.db import create_db_engine
    engine = create_db_engine(settings.resolved_database_url)

    assert approved_opportunities(engine) == []
    assert json.loads(export_json(engine))["opportunities"] == []

    with Session(engine) as session:
        cluster = session.exec(select(OpportunityCluster)).first()
        cluster.is_approved = True
        session.add(cluster)
        session.commit()
        cluster_id = cluster.id

    payload = json.loads(export_json(engine))
    assert payload["schema_version"] == "1.0"
    assert len(payload["opportunities"]) == 1
    opp = payload["opportunities"][0]
    assert opp["id"] == cluster_id
    assert opp["product_idea"]
    assert opp["validation_plan"]
    assert opp["score_breakdown"]["weighted_sum"] >= 0
    assert len(opp["evidence"]) == opp["total_evidence_count"]
    assert {"pain_statement", "persona", "source", "url"} <= set(opp["evidence"][0])

    rows = list(csv.DictReader(io.StringIO(export_csv(engine))))
    assert len(rows) == 1
    assert list(rows[0].keys()) == CSV_FIELDS
    assert rows[0]["cluster_name"] == opp["cluster_name"]


def test_rejected_clusters_are_not_exported(settings):
    run_pipeline(settings, sources=["hacker_news"], limit_per_source=5, demo=True)
    from database.db import create_db_engine
    engine = create_db_engine(settings.resolved_database_url)
    with Session(engine) as session:
        cluster = session.exec(select(OpportunityCluster)).first()
        cluster.is_rejected = True
        session.add(cluster)
        session.commit()
    assert approved_opportunities(engine) == []