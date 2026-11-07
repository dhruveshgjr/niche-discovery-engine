"""Niche Discovery Engine — Full Control Center (Streamlit).

Run with: uv run streamlit run ui.py
"""
from __future__ import annotations

import json
from collections import deque

import pandas as pd
import streamlit as st
from sqlmodel import Session, func, select

from agents.graph import run_pipeline
from config import ALL_SOURCES, PROJECT_ROOT, Settings
from database.db import create_db_engine, init_db
from database.models import OpportunityCluster, PainPoint, RawPost, Run
from export import export_csv, export_json
from logging_setup import setup_logging

st.set_page_config(page_title="Niche Discovery Engine", layout="wide")

SOURCE_LABELS = {
    "hacker_news": "Hacker News (Algolia)",
    "reddit": "Reddit (public JSON)",
    "job_boards": "Job Boards (RSS/API)",
    "yc_rfs": "YC Requests for Startups",
    "x": "X / Twitter (session cookies)",
    "facebook": "Facebook (session cookies)",
}


@st.cache_resource
def get_engine(database_url: str):
    engine = create_db_engine(database_url)
    init_db(engine)
    return engine


@st.cache_data(ttl=30)
def probe_demo_mode(base_url: str, explicit: bool | None) -> bool:
    from config import resolve_demo_mode
    settings = Settings()
    settings.lm_studio_base_url = base_url
    settings.demo_mode = explicit
    return resolve_demo_mode(settings)


def read_log_tail(lines: int = 60) -> str:
    path = PROJECT_ROOT / "logs" / "engine.log"
    if not path.exists():
        return "No log file yet — run the pipeline to generate logs."
    with path.open(encoding="utf-8") as handle:
        return "\n".join(deque(handle, maxlen=lines))


def counts(engine) -> dict:
    with Session(engine) as session:
        return {
            "posts": session.exec(select(func.count()).select_from(RawPost)).one(),
            "pains": session.exec(select(func.count()).select_from(PainPoint)).one(),
            "clusters": session.exec(select(func.count()).select_from(OpportunityCluster)).one(),
            "approved": session.exec(
                select(func.count()).select_from(OpportunityCluster)
                .where(OpportunityCluster.is_approved == True)  # noqa: E712
            ).one(),
        }


settings = Settings()
engine = get_engine(settings.resolved_database_url)
demo = probe_demo_mode(settings.lm_studio_base_url, settings.demo_mode)

# ---------------- Sidebar ----------------
with st.sidebar:
    st.title("Control Center")
    if demo:
        st.info("DEMO MODE — fixtures + heuristic LLM (LM Studio not detected)")
    else:
        st.success("LIVE MODE — LM Studio connected")

    st.subheader("Sources")
    selected_sources = []
    for name in ALL_SOURCES:
        default = name in settings.enabled_source_list
        if name in ("x", "facebook"):
            default = default and bool(
                (settings.x_auth_token and settings.x_ct0)
                if name == "x" else settings.facebook_cookie
            )
        if st.checkbox(SOURCE_LABELS.get(name, name), value=default, key=f"src_{name}"):
            selected_sources.append(name)

    limit = st.slider("Items per source", 10, 200, settings.items_per_source, step=10)

    if st.button("Run Overnight Discovery", type="primary", width="stretch"):
        setup_logging(settings.log_level)
        with st.status("Running discovery pipeline...", expanded=True) as status:
            st.write(f"Mode: {'demo' if demo else 'live'} | sources: {', '.join(selected_sources) or 'none'}")
            result = run_pipeline(
                settings,
                sources=selected_sources,
                limit_per_source=limit,
                demo=demo,
            )
            st.write(
                f"Posts: {result.get('posts_collected', 0)} | "
                f"Pains: {result.get('pains_extracted', 0)} | "
                f"Clusters: {result.get('clusters_created', 0)}"
            )
            for error in result.get("errors", []):
                st.write(f"Warning: {error}")
            status.update(
                label=f"Run {result.get('status')}",
                state="complete" if result.get("status") == "completed" else "error",
            )

    with st.expander("Log viewer"):
        st.code(read_log_tail(), language="json")

    st.caption("System 1 of 2 — approved opportunities export to System 2 via the Export tab.")

# ---------------- Tabs ----------------
tab_dashboard, tab_vault, tab_war, tab_approvals, tab_export = st.tabs(
    ["Dashboard", "Evidence Vault", "War Room", "Approvals", "Export"]
)

with tab_dashboard:
    st.header("Dashboard")
    metrics = counts(engine)
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Posts scanned", metrics["posts"])
    col2.metric("Pain points found", metrics["pains"])
    col3.metric("Opportunities", metrics["clusters"])
    col4.metric("Approved", metrics["approved"])

    st.subheader("Recent runs")
    with Session(engine) as session:
        runs = session.exec(select(Run).order_by(Run.started_at.desc()).limit(10)).all()
    if runs:
        st.dataframe(
            pd.DataFrame([{
                "run": r.id[:8], "mode": r.mode, "status": r.status,
                "started": r.started_at, "posts": r.posts_collected,
                "pains": r.pains_extracted, "clusters": r.clusters_created,
            } for r in runs]),
            width="stretch", hide_index=True,
        )
    else:
        st.caption("No runs yet. Use the sidebar button to start discovery.")

    st.subheader("Top opportunities by score")
    with Session(engine) as session:
        top = session.exec(
            select(OpportunityCluster).order_by(OpportunityCluster.pain_score.desc()).limit(10)
        ).all()
    if top:
        chart_df = pd.DataFrame(
            {"cluster": c.cluster_name, "pain_score": c.pain_score} for c in top
        ).set_index("cluster")
        st.bar_chart(chart_df)

with tab_vault:
    st.header("Evidence Vault")
    search = st.text_input("Search posts and pain points", placeholder="e.g. no API, spreadsheet, manual")
    source_filter = st.multiselect("Filter by source", ALL_SOURCES, default=[])

    with Session(engine) as session:
        query = select(RawPost).order_by(RawPost.collected_at.desc()).limit(300)
        if source_filter:
            query = query.where(RawPost.source.in_(source_filter))
        posts = session.exec(query).all()
        pain_rows = session.exec(
            select(PainPoint, RawPost.source)
            .join(RawPost, PainPoint.raw_post_id == RawPost.id)
            .order_by(PainPoint.created_at.desc())
            .limit(300)
        ).all()
    pains = [pain for pain, _source in pain_rows]
    pain_source = {pain.id: source for pain, source in pain_rows}

    if search:
        needle = search.lower()
        posts = [p for p in posts if needle in f"{p.title} {p.body}".lower()]
        pains = [p for p in pains if needle in p.pain_statement.lower()]

    st.subheader(f"Raw posts ({len(posts)})")
    if posts:
        st.dataframe(
            pd.DataFrame([{
                "id": p.id, "source": p.source, "title": p.title,
                "engagement": p.engagement, "date_posted": p.date_posted, "url": p.url,
            } for p in posts]),
            width="stretch", hide_index=True,
            column_config={"url": st.column_config.LinkColumn("url")},
        )
        inspect_id = st.selectbox("Inspect post", [p.id for p in posts],
                                  format_func=lambda i: next(p.title[:80] for p in posts if p.id == i))
        inspected = next(p for p in posts if p.id == inspect_id)
        st.markdown(f"**Body:** {inspected.body}")
        related = [p for p in pains if p.raw_post_id == inspect_id]
        if related:
            st.markdown("**Extracted pain points:**")
            for pain in related:
                st.markdown(f"- {pain.pain_statement} *(persona: {pain.persona}, "
                            f"buyer intent: {pain.buyer_intent}/10)*")
    else:
        st.caption("No posts match.")

    st.subheader(f"Pain points ({len(pains)})")
    if pains:
        st.dataframe(
            pd.DataFrame([{
                "id": p.id, "pain_statement": p.pain_statement, "persona": p.persona,
                "buyer_intent": p.buyer_intent, "severity": p.severity,
                "extraction_fit": p.extraction_fit,
                "source": pain_source.get(p.id, ""),
            } for p in pains]),
            width="stretch", hide_index=True,
        )

with tab_war:
    st.header("War Room")
    with Session(engine) as session:
        clusters = session.exec(
            select(OpportunityCluster).order_by(OpportunityCluster.pain_score.desc())
        ).all()
    if not clusters:
        st.caption("No opportunities yet — run discovery first.")
    for cluster in clusters:
        with st.container(border=True):
            header_col, score_col = st.columns([4, 1])
            header_col.subheader(cluster.cluster_name)
            status = "approved" if cluster.is_approved else "rejected" if cluster.is_rejected else "pending"
            header_col.caption(f"{cluster.total_evidence_count} evidence items | status: {status}")
            score_col.metric("Pain score", f"{cluster.pain_score:.2f}")
            st.markdown(f"**Product idea:** {cluster.product_idea}")
            st.markdown(f"**Validation plan:**\n\n{cluster.validation_plan}")
            with st.expander("Score breakdown"):
                st.json(json.loads(cluster.score_breakdown or "{}"))

with tab_approvals:
    st.header("Approvals")
    with Session(engine) as session:
        pending = session.exec(
            select(OpportunityCluster)
            .where(OpportunityCluster.is_approved == False)  # noqa: E712
            .where(OpportunityCluster.is_rejected == False)  # noqa: E712
            .order_by(OpportunityCluster.pain_score.desc())
        ).all()
    st.caption("Approve opportunities to queue them for System 2 export.")
    if not pending:
        st.info("Nothing awaiting review.")
    for cluster in pending:
        with st.expander(f"{cluster.cluster_name} — score {cluster.pain_score:.2f} "
                         f"({cluster.total_evidence_count} evidence)"):
            st.markdown(f"**Product idea:** {cluster.product_idea}")
            st.markdown(f"**Validation plan:**\n\n{cluster.validation_plan}")
            approve_col, reject_col, _ = st.columns([1, 1, 4])
            if approve_col.button("Approve", key=f"approve_{cluster.id}", type="primary"):
                with Session(engine) as session:
                    record = session.get(OpportunityCluster, cluster.id)
                    record.is_approved = True
                    record.is_rejected = False
                    session.add(record)
                    session.commit()
                st.rerun()
            if reject_col.button("Reject", key=f"reject_{cluster.id}"):
                with Session(engine) as session:
                    record = session.get(OpportunityCluster, cluster.id)
                    record.is_approved = False
                    record.is_rejected = True
                    session.add(record)
                    session.commit()
                st.rerun()

    st.subheader("Decision history")
    with Session(engine) as session:
        decided = session.exec(
            select(OpportunityCluster)
            .where((OpportunityCluster.is_approved == True) | (OpportunityCluster.is_rejected == True))  # noqa: E712
        ).all()
    if decided:
        st.dataframe(
            pd.DataFrame([{
                "cluster": c.cluster_name, "score": c.pain_score,
                "decision": "approved" if c.is_approved else "rejected",
            } for c in decided]),
            width="stretch", hide_index=True,
        )

with tab_export:
    st.header("Export — System 2 Handoff")
    st.caption("Schema v1.0: approved opportunities with scores, ideas, validation plans, and evidence links.")
    approved_count = counts(engine)["approved"]
    if approved_count == 0:
        st.warning("No approved opportunities yet. Approve some in the Approvals tab first.")
    else:
        st.success(f"{approved_count} approved opportunities ready for handoff.")
        json_col, csv_col = st.columns(2)
        json_col.download_button(
            "Download JSON", export_json(engine),
            file_name="opportunities.json", mime="application/json",
            width="stretch",
        )
        csv_col.download_button(
            "Download CSV", export_csv(engine),
            file_name="opportunities.csv", mime="text/csv",
            width="stretch",
        )
        with st.expander("Preview handoff payload"):
            st.json(json.loads(export_json(engine)))