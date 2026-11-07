"""CLI entry point: run the full discovery pipeline once (used by run_overnight.sh)."""
from __future__ import annotations

import argparse
import sys

from agents.graph import run_pipeline
from config import ALL_SOURCES, Settings
from logging_setup import setup_logging


def main() -> int:
    parser = argparse.ArgumentParser(description="Niche Discovery Engine — System 1")
    parser.add_argument("--sources", help=f"comma-separated subset of {','.join(ALL_SOURCES)}")
    parser.add_argument("--limit", type=int, help="max items per source (default from .env)")
    parser.add_argument("--db", help="database URL override")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--demo", action="store_true", help="force demo mode (fixtures + heuristics)")
    mode.add_argument("--live", action="store_true", help="force live mode (LM Studio + real sources)")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 0:
        parser.error("--limit must be >= 0")

    settings = Settings()
    if args.db:
        settings.database_url = args.db
    setup_logging(settings.log_level)

    sources = [s.strip() for s in args.sources.split(",")] if args.sources else None
    demo = True if args.demo else False if args.live else None
    result = run_pipeline(settings, sources=sources, limit_per_source=args.limit, demo=demo)

    print("\n=== Run summary ===")
    print(f"Run ID:          {result['run_id']}")
    print(f"Status:          {result.get('status')}")
    print(f"Mode:            {'demo' if result.get('demo_mode') else 'live'}")
    print(f"Posts collected: {result.get('posts_collected', 0)}")
    print(f"Pain points:     {result.get('pains_extracted', 0)}")
    print(f"Clusters:        {result.get('clusters_created', 0)}")
    if result.get("errors"):
        print("Non-fatal errors:")
        for error in result["errors"]:
            print(f"  - {error}")
    return 0 if result.get("status") == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())