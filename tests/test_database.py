from sqlmodel import Session, select

from database.db import create_db_engine
from database.models import OpportunityCluster, PainPoint, RawPost, Run


def test_sqlite_runs_in_wal_mode_with_busy_timeout(settings):
    engine = create_db_engine(settings.resolved_database_url)
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar() == 5000


def test_relationships(engine):
    with Session(engine) as session:
        post = RawPost(run_id="r1", source="hacker_news",
                       url="https://example.com/1", title="No API pain", body="manual work")
        session.add(post)
        session.flush()
        pain = PainPoint(raw_post_id=post.id, pain_statement="No export API forces manual work",
                         persona="Developer", buyer_intent=7.0, severity=8.0, extraction_fit=True)
        session.add(pain)
        session.flush()
        cluster = OpportunityCluster(run_id="r1", cluster_name="Missing APIs",
                                     total_evidence_count=1, pain_score=6.5)
        session.add(cluster)
        session.flush()
        pain.cluster_id = cluster.id
        session.commit()

    with Session(engine) as session:
        post = session.exec(select(RawPost)).one()
        assert post.pain_points[0].pain_statement.startswith("No export API")
        pain = session.exec(select(PainPoint)).one()
        assert pain.raw_post.url == "https://example.com/1"
        assert pain.cluster.cluster_name == "Missing APIs"
        cluster = session.exec(select(OpportunityCluster)).one()
        assert cluster.pain_points[0].id == pain.id
        assert cluster.is_approved is False
        assert cluster.is_rejected is False


def test_run_defaults(engine):
    with Session(engine) as session:
        session.add(Run(id="abc", mode="demo", sources="hacker_news"))
        session.commit()
    with Session(engine) as session:
        run = session.get(Run, "abc")
        assert run.status == "running"
        assert run.posts_collected == 0
        assert run.errors == "[]"
        assert run.started_at is not None