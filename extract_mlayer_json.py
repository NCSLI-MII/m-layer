#!/usr/bin/env python3
# vim:fenc=utf-8
#
# Copyright © 2025 Ryan Mackenzie White <ryan.white4@canada.ca>
#
# Distributed under terms of the Copyright © Her Majesty the Queen in Right of
# Canada, as represented by the Minister of Statistics Canada, 2019. license.
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
Command-line entry point for extracting M-Layer API data to JSON.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from tools.mlayer_api_extractor import (
    MLayerApiExtractConfig,
    MLayerApiExtractor,
)


LOG = logging.getLogger("extract_mlayer_json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract M-Layer API collections to stable JSON files."
    )

    parser.add_argument(
        "--base-url",
        default="https://api.mlayer.org",
        help="Base URL for the M-Layer API.",
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./data/json"),
        help="Directory where JSON files will be written.",
    )

    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help=(
            "Path to manifest JSON. Defaults to <output-dir>/manifest.json "
            "if omitted."
        ),
    )

    parser.add_argument(
        "--token",
        default=None,
        help=(
            "Optional bearer token. If omitted, MLAYER_API_TOKEN environment "
            "variable is used if set."
        ),
    )

    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="HTTP request timeout in seconds.",
    )

    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Number of retries for transient HTTP errors.",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and summarize data, but do not write files.",
    )

    parser.add_argument(
        "--no-sort",
        action="store_true",
        help="Do not sort rows before writing JSON.",
    )

    parser.add_argument(
        "--rewrite-unchanged",
        action="store_true",
        help="Rewrite JSON files even when content is unchanged.",
    )

    parser.add_argument(
        "--no-overwrite",
        action="store_true",
        help="Fail if an output file already exists.",
    )

    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level. Example: DEBUG, INFO, WARNING.",
    )

    return parser


def config_from_args(args: argparse.Namespace) -> MLayerApiExtractConfig:
    return MLayerApiExtractConfig(
        base_url=args.base_url,
        output_dir=args.output_dir,
        manifest_path=args.manifest,
        token=args.token,
        timeout_seconds=args.timeout,
        retries=args.retries,
        dry_run=args.dry_run,
        overwrite=not args.no_overwrite,
        skip_unchanged=not args.rewrite_unchanged,
        sort_rows=not args.no_sort,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(levelname)s %(name)s: %(message)s",
    )

    config = config_from_args(args)

    extractor = MLayerApiExtractor(config)

    try:
        result = extractor.run()
    except Exception:
        LOG.exception("M-Layer API extraction failed")
        return 1

    LOG.info("Extraction complete")
    LOG.info("Total rows: %s", result.total_rows)
    LOG.info("Changed files: %s", result.changed_files)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

