import json

import pytest
from sqlmodel import Session, select

from agents.cluster import make_cluster_node
from agents.collector import make_collector_node, save_posts
from agents.extractor import make_extractor_node
from agents.scout import make_scout_node
from agents.state import Deps
from agents.strategist import compute_score, make_strategist_node
from database.models import OpportunityCluster, PainPoint, RawPost
from llm_client import LLMClient


def make_deps(settings, engine, llm=None):
    return Deps(settings=settings, llm=llm or LLMClient(settings, demo=True), engine=engine)


def base_state(**overrides):
    state = {
        "run_id": "test-run", "demo_mode": False, "sources": ["hacker_news"],
        "limit_per_source": 5, "queries": ["no api"], "errors": [],
    }
    state.update(overrides)
    return state


class FakeSource:
    def __init__(self, name, posts=None, fail=False):
        self.name = name
        self.posts = posts or []
        self.fail = fail

    def collect(self, queries, limit):
        if self.fail:
            raise ConnectionError("source exploded")
        return self.posts


def post(url, title="Manual work", body="I copy data by hand every week", source="hacker_news"):
    return {"source": source, "url": url, "title": title, "body": body, "engagement": 3}


def test_scout_generates_20_unique_queries(settings, engine):
    node = make_scout_node(make_deps(settings, engine))
    result = node(base_state())
    assert len(result["queries"]) == 20
    assert len(set(result["queries"])) == 20


def test_collector_isolates_failing_source(settings, engine):
    good = FakeSource("good", [post("https://a.com/1"), post("https://a.com/2")])
    bad = FakeSource("bad", fail=True)
    node = make_collector_node(make_deps(settings, engine),
                               source_factory=lambda names: [bad, good])
    result = node(base_state())
    assert result["posts_collected"] == 2
    assert len(result["errors"]) == 1
    assert "source exploded" in result["errors"][0]


def test_collector_dedupes_within_run(settings, engine):
    node = make_collector_node(
        make_deps(settings, engine),
        source_factory=lambda names: [FakeSource("s", [post("https://a.com/1"), post("https://a.com/1")])],
    )
    assert node(base_state())["posts_collected"] == 1


def test_collector_filters_unsafe_content(settings, engine):
    posts = [post("https://a.com/ok"), post("https://a.com/bad", body="cheap guns for sale here")]
    node = make_collector_node(make_deps(settings, engine),
                               source_factory=lambda names: [FakeSource("s", posts)])
    assert node(base_state())["posts_collected"] == 1


def test_collector_demo_loads_fixtures(settings, engine):
    node = make_collector_node(make_deps(settings, engine))
    result = node(base_state(demo_mode=True))
    assert result["posts_collected"] > 0
    assert result["errors"] == []


def test_collector_respects_per_source_limit(settings, engine):
    posts = [post(f"https://a.com/{i}") for i in range(10)]
    saved = save_posts(engine, "r-limit", posts, limit_per_source=3)
    assert saved == 3


def test_save_posts_dedupes_across_runs(settings, engine):
    posts = [post("https://a.com/1"), post("https://a.com/2")]
    assert save_posts(engine, "run-a", posts, limit_per_source=5) == 2
    assert save_posts(engine, "run-b", posts, limit_per_source=5) == 0
    assert save_posts(engine, "run-c", [post("https://a.com/3")], limit_per_source=5) == 1


def test_save_posts_survives_url_collision(settings, engine, monkeypatch):
    # cross-process race against the unique index must not lose the batch
    from database.models import RawPost as RawPostModel
    with Session(engine) as session:
        session.add(RawPostModel(run_id="seed", source="hn", url="https://a.com/1", title="t"))
        session.commit()
    posts = [post("https://a.com/1"), post("https://a.com/2")]
    saved = save_posts(engine, "run-race", posts, limit_per_source=5)
    assert saved == 1  # /2 saved; /1 skipped as duplicate regardless of index


def test_extractor_discards_noise_drafts(settings, engine):
    class NoisyLLM:
        def extract_pain_points(self, post):
            return [
                {"pain_statement": "No API means manual spreadsheet exports every week",
                 "persona": "Developer", "buyer_intent": 8, "severity": 9,
                 "current_solution": "Manual exports", "extraction_fit": True},
                {"pain_statement": "none"},
                {"pain_statement": "x"},
            ]

    with Session(engine) as session:
        session.add(RawPost(run_id="test-run", source="hn", url="https://a.com/1", title="t"))
        session.commit()
    node = make_extractor_node(make_deps(settings, engine, llm=NoisyLLM()))
    result = node(base_state())
    assert result["pains_extracted"] == 1
    with Session(engine) as session:
        pain = session.exec(select(PainPoint)).one()
        assert pain.buyer_intent == 8.0
        assert pain.extraction_fit is True


def test_extractor_clamps_out_of_range_scores(settings, engine):
    class WildLLM:
        def extract_pain_points(self, post):
            return [{"pain_statement": "Manual data entry is a daily nightmare",
                     "buyer_intent": 99, "severity": -5, "extraction_fit": "yes"}]

    with Session(engine) as session:
        session.add(RawPost(run_id="test-run", source="hn", url="https://a.com/1", title="t"))
        session.commit()
    node = make_extractor_node(make_deps(settings, engine, llm=WildLLM()))
    node(base_state())
    with Session(engine) as session:
        pain = session.exec(select(PainPoint)).one()
        assert pain.buyer_intent == 10.0
        assert pain.severity == 0.0
        assert pain.extraction_fit is True


def seed_pains(engine, run_id="test-run"):
    with Session(engine) as session:
        post = RawPost(run_id=run_id, source="hn", url="https://a.com/1", title="t")
        session.add(post)
        session.flush()
        for statement in [
            "No API forces manual CSV exports every week",
            "Copy paste from dashboards into spreadsheets daily",
            "Web scraping selectors break and need maintenance",
        ]:
            session.add(PainPoint(raw_post_id=post.id, pain_statement=statement,
                                  persona="Developer", buyer_intent=6, severity=7,
                                  extraction_fit=True))
        session.commit()


def test_cluster_assigns_every_pain(settings, engine):
    seed_pains(engine)
    node = make_cluster_node(make_deps(settings, engine))
    result = node(base_state())
    assert result["clusters_created"] >= 2
    with Session(engine) as session:
        pains = session.exec(select(PainPoint)).all()
        assert all(p.cluster_id is not None for p in pains)
        clusters = session.exec(select(OpportunityCluster)).all()
        assert sum(c.total_evidence_count for c in clusters) == 3


def test_scoring_formula_exact():
    score, breakdown = compute_score(
        severity=8, buyer_intent=6, evidence_count=3, extraction_ratio=0.5,
        reachability=7, legal_risk=4, competition=6,
    )
    # weighted = 2.0 + 1.5 + 0.9 + 1.0 + 1.05 = 6.45; penalties = 0.8 + 1.2
    assert score == 4.45
    assert breakdown["frequency"] == 6.0
    assert breakdown["extraction_fit"] == 5.0
    assert breakdown["legal_risk_penalty"] == 0.8
    assert breakdown["competition_penalty"] == 1.2


def test_scoring_floor_at_zero():
    score, _ = compute_score(0, 0, 0, 0.0, 0, 10, 10)
    assert score == 0.0


def test_strategist_writes_ideas_and_scores(settings, engine):
    seed_pains(engine)
    make_cluster_node(make_deps(settings, engine))(base_state())
    result = make_strategist_node(make_deps(settings, engine))(base_state())
    assert result["status"] == "completed"
    with Session(engine) as session:
        clusters = session.exec(select(OpportunityCluster)).all()
        assert all(c.product_idea for c in clusters)
        assert all(c.validation_plan for c in clusters)
        assert all(c.pain_score > 0 for c in clusters)
        assert all(c.score_breakdown != "{}" for c in clusters)


class MalformedValueLLM:
    def strategize(self, cluster_name, pains):
        return {"product_idea": "x", "validation_plan": "y", "reachability": "high"}


def test_strategist_survives_malformed_model_numbers(settings, engine):
    seed_pains(engine)
    make_cluster_node(make_deps(settings, engine))(base_state())
    node = make_strategist_node(make_deps(settings, engine, llm=MalformedValueLLM()))
    with pytest.raises(ValueError):
        float("high")  # sanity: the value would crash an unguarded implementation
    result = node(base_state())
    assert result["status"] == "completed"
    with Session(engine) as session:
        clusters = session.exec(select(OpportunityCluster)).all()
        assert all(c.pain_score > 0 for c in clusters)
        breakdowns = [json.loads(c.score_breakdown) for c in clusters]
        assert all(b["reachability"] == 6.5 for b in breakdowns)  # defaulted safely


def test_extractor_keeps_partial_results_on_llm_failure(settings, engine):
    class PartlyBrokenLLM:
        def __init__(self):
            self.calls = 0

        def extract_pain_points(self, post):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("boom")
            return [{"pain_statement": "Manual CSV exports every single week",
                     "buyer_intent": 5, "severity": 6}]

    with Session(engine) as session:
        session.add(RawPost(run_id="test-run", source="hn", url="https://a.com/1", title="t1"))
        session.add(RawPost(run_id="test-run", source="hn", url="https://a.com/2", title="t2"))
        session.commit()
    result = make_extractor_node(make_deps(settings, engine, llm=PartlyBrokenLLM()))(base_state())
    assert result["pains_extracted"] == 1
    assert len(result["errors"]) == 1