#! /usr/bin/env python3
# vim:fenc=utf-8
#
# Copyright © 2026 Ryan Mackenzie White <ryan.white4@canada.ca>
#
# Distributed under terms of the Copyright © Her Majesty the Queen in Right of Canada, as represented by the Minister of Statistics Canada, 2019. license.
#
# AI Zone assistance disclosure:
#
# Portions of this file were generated or refined with the assistance of
# NRC's AI Zone, using user-provided requirements, existing project code,
# and supporting context.
#
# AI-generated suggestions have been reviewed and adapted by the responsible
# developer/team. The responsible developer/team remains accountable for
# validating correctness, security, privacy, accessibility, licensing,
# maintainability, and compliance with applicable NRC and Government of Canada
# policies, standards, and procedures before deployment or operational use.

"""
Standalone M-Layer source-data audit and comparison tool.

This script does NOT depend on:
- the mlayer SQLAlchemy model
- SQLAlchemy
- Flask
- SQLite
- the application database

It only reads:
- a PostgreSQL plain-text pg_dump containing COPY blocks
- a directory of JSON source files

It produces:
- SQL dump summary
- JSON source summary
- high-level comparison
- conversion/cast semantic comparison
- optional machine-readable JSON report

Typical usage:

    python summarize_mlayer_sources.py \
        --dump ./data/m_layer_v5.86.dmp \
        --json-dir ./data/json

JSON report:

    python summarize_mlayer_sources.py \
        --dump ./data/m_layer_v5.86.dmp \
        --json-dir ./data/json \
        --json-report ./reports/mlayer_source_audit.json
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from tools.mlayer_source_auditor import MlayerSourceAuditor
from tools.mlayer_source_auditor import ReportPrinter

LOG = logging.getLogger("mlayer_source_audit")




# =============================================================================
# CLI
# =============================================================================


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize and compare M-Layer SQL dump and JSON source data."
    )

    parser.add_argument("--dump", type=Path)
    parser.add_argument("--json-dir", type=Path)
    parser.add_argument("--json-report", type=Path)
    parser.add_argument("--sample-limit", type=int, default=5)
    parser.add_argument("--log-level", default="WARNING")

    parser.add_argument("--show-null-counts", action="store_true")
    parser.add_argument("--show-distinct-counts", action="store_true")
    parser.add_argument("--show-samples", action="store_true")
    parser.add_argument("--hide-columns", action="store_true")

    parser.add_argument(
        "--strict-dump-parser",
        action="store_true",
        help="Fail on malformed dump rows instead of logging/skipping them.",
    )

    args = parser.parse_args(argv)

    if not args.dump and not args.json_dir:
        parser.error("Provide at least one of --dump or --json-dir")

    return args


def write_json_report(path: Path, report: SourceAuditReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as fh:
        json.dump(
            asdict(report),
            fh,
            indent=2,
            sort_keys=True,
            default=str,
        )


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(levelname)s %(name)s: %(message)s",
    )

    auditor = MlayerSourceAuditor(
        dump_path=args.dump,
        json_dir=args.json_dir,
        sample_limit=args.sample_limit,
        tolerant_dump_parser=not args.strict_dump_parser,
    )

    report = auditor.run()

    printer = ReportPrinter(
        sample_limit=args.sample_limit,
        show_columns=not args.hide_columns,
        show_nulls=args.show_null_counts,
        show_distincts=args.show_distinct_counts,
        show_samples=args.show_samples,
    )

    printer.print(report)

    if args.json_report:
        write_json_report(args.json_report, report)
        print()
        print(f"Wrote JSON report: {args.json_report}")

    return 0 if not report.warnings else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

