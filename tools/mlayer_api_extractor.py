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
Class-based M-Layer API extraction tool.

This module extracts M-Layer reference-data collections from the public API and
writes stable, versionable JSON files suitable for tracking in Git.

It intentionally does not depend on Flask, SQLAlchemy, or the application
database. Its purpose is to create source JSON data that can later be:

- reviewed in GitHub;
- audited with the standalone source-audit tool;
- imported into the application database using the JSON mapper.

Typical programmatic usage:

    from pathlib import Path

    from miiflask.tools.mlayer_api_extractor import (
        MLayerApiExtractConfig,
        MLayerApiExtractor,
    )

    config = MLayerApiExtractConfig(
        output_dir=Path("./data/json"),
        manifest_path=Path("./data/json/manifest.json"),
    )

    extractor = MLayerApiExtractor(config)
    result = extractor.run()
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


LOG = logging.getLogger("mlayer_api_extractor")


# =============================================================================
# Data structures
# =============================================================================


@dataclass
class CollectionConfig:
    """
    Configuration for one API collection.
    """

    name: str
    endpoint: str
    output_file: str


@dataclass
class CollectionExtractResult:
    """
    Result for one extracted collection.
    """

    name: str
    endpoint: str
    output_file: str
    row_count: int
    sha256: str | None = None
    changed: bool = False
    skipped_write: bool = False
    status_code: int | None = None


@dataclass
class MLayerApiExtractResult:
    """
    Result returned by MLayerApiExtractor.run().
    """

    generated_at_utc: str
    base_url: str
    output_dir: str
    manifest_path: str | None
    dry_run: bool
    collections: dict[str, CollectionExtractResult] = field(default_factory=dict)

    @property
    def total_rows(self) -> int:
        return sum(item.row_count for item in self.collections.values())

    @property
    def changed_files(self) -> list[str]:
        return [
            item.output_file
            for item in self.collections.values()
            if item.changed
        ]


@dataclass
class MLayerApiExtractConfig:
    """
    Configuration for API extraction.

    base_url:
        Base URL for the M-Layer API.

    output_dir:
        Directory where JSON collection files will be written.

    manifest_path:
        Optional manifest path. If omitted, defaults to output_dir / "manifest.json".

    token:
        Optional bearer token. If omitted, environment variable MLAYER_API_TOKEN
        is used if available.

    dry_run:
        Fetch and summarize data, but do not write collection files or manifest.

    overwrite:
        If True, write files. If False, do not overwrite existing files.

    skip_unchanged:
        If True, avoid rewriting files whose content is unchanged.

    sort_rows:
        If True, sort rows before writing to produce stable Git diffs.

    timeout_seconds:
        Per-request timeout.

    retries:
        Number of retry attempts for transient HTTP failures.

    collection_configs:
        Collection definitions. Defaults match the original script.
    """

    base_url: str = "https://api.mlayer.org"
    output_dir: Path = Path("/tmp/mlayer")
    manifest_path: Path | None = None

    token: str | None = None
    timeout_seconds: int = 30
    retries: int = 3

    dry_run: bool = False
    overwrite: bool = True
    skip_unchanged: bool = True
    sort_rows: bool = True

    user_agent: str = "mlayer-api-extractor/1.0"

    collection_configs: list[CollectionConfig] = field(
        default_factory=lambda: [
            CollectionConfig("systems", "/systems", "systems.json"),
            #CollectionConfig("prefixes", "/prefixes", "prefixes.json"),
            CollectionConfig("dimensions", "/dimensions", "dimensions.json"),
            CollectionConfig("functions", "/functions", "functions.json"),
            CollectionConfig("aspects", "/aspects", "aspects.json"),
            CollectionConfig("units", "/units", "units.json"),
            CollectionConfig("scales", "/scales", "scales.json"),
            CollectionConfig("scaletypes", "/scaletypes", "scaletypes.json"),
            CollectionConfig("conversions", "/conversions", "conversions.json"),
            CollectionConfig("casts", "/casts", "casts.json"),
        ]
    )


# =============================================================================
# API client
# =============================================================================


class MLayerApiClient:
    """
    Small HTTP client for the M-Layer API.
    """

    def __init__(
        self,
        *,
        base_url: str,
        token: str | None = None,
        timeout_seconds: int = 30,
        retries: int = 3,
        user_agent: str = "mlayer-api-extractor/1.0",
        logger: logging.Logger | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.user_agent = user_agent
        self.logger = logger or LOG

        self.session = self._build_session()

    def _build_session(self) -> requests.Session:
        session = requests.Session()

        retry = Retry(
            total=self.retries,
            connect=self.retries,
            read=self.retries,
            status=self.retries,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET"]),
        )

        adapter = HTTPAdapter(max_retries=retry)
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": self.user_agent,
            }
        )

        if self.token:
            session.headers.update(
                {
                    "Authorization": f"Bearer {self.token}",
                }
            )

        return session

    def get_collection(self, endpoint: str) -> tuple[list[dict[str, Any]], int]:
        url = self.build_url(endpoint)

        self.logger.info("Fetching %s", url)

        response = self.session.get(url, timeout=self.timeout_seconds)
        status_code = response.status_code

        response.raise_for_status()

        payload = response.json()
        rows = self.extract_rows(payload)

        return rows, status_code

    def build_url(self, endpoint: str) -> str:
        endpoint = endpoint.strip()

        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            return endpoint

        if not endpoint.startswith("/"):
            endpoint = f"/{endpoint}"

        return f"{self.base_url}{endpoint}"

    @staticmethod
    def extract_rows(payload: Any) -> list[dict[str, Any]]:
        """
        Accepts either:
        - a top-level list;
        - or a wrapper object with data/results/items.
        """

        if isinstance(payload, list):
            return payload

        if isinstance(payload, dict):
            for key in ["data", "results", "items"]:
                value = payload.get(key)
                if isinstance(value, list):
                    return value

        raise ValueError(
            "API response did not contain a list or a supported collection wrapper"
        )


# =============================================================================
# Writer
# =============================================================================


class StableJsonDatasetWriter:
    """
    Writes stable JSON files and a manifest.
    """

    def __init__(
        self,
        *,
        output_dir: Path,
        skip_unchanged: bool = True,
        overwrite: bool = True,
        logger: logging.Logger | None = None,
    ) -> None:
        self.output_dir = output_dir
        self.skip_unchanged = skip_unchanged
        self.overwrite = overwrite
        self.logger = logger or LOG

    def write_collection(
        self,
        *,
        filename: str,
        rows: list[dict[str, Any]],
    ) -> tuple[Path, str, bool, bool]:
        """
        Writes one JSON collection.

        Returns:
            path, sha256, changed, skipped_write
        """

        self.output_dir.mkdir(parents=True, exist_ok=True)

        path = self.output_dir / filename
        content = self.render_json(rows)
        new_hash = self.sha256_bytes(content)

        if path.exists():
            old_hash = self.sha256_file(path)

            if old_hash == new_hash and self.skip_unchanged:
                self.logger.info("Unchanged: %s", path)
                return path, new_hash, False, True

            if not self.overwrite:
                raise FileExistsError(
                    f"Output file already exists and overwrite=False: {path}"
                )

        self.atomic_write_bytes(path, content)
        self.logger.info("Wrote %s", path)

        return path, new_hash, True, False

    def write_manifest(
        self,
        *,
        manifest_path: Path,
        manifest: dict[str, Any],
    ) -> tuple[str, bool, bool]:
        """
        Writes manifest JSON.

        Returns:
            sha256, changed, skipped_write
        """

        manifest_path.parent.mkdir(parents=True, exist_ok=True)

        content = self.render_json(manifest)
        new_hash = self.sha256_bytes(content)

        if manifest_path.exists():
            old_hash = self.sha256_file(manifest_path)

            if old_hash == new_hash and self.skip_unchanged:
                self.logger.info("Manifest unchanged: %s", manifest_path)
                return new_hash, False, True

            if not self.overwrite:
                raise FileExistsError(
                    f"Manifest already exists and overwrite=False: {manifest_path}"
                )

        self.atomic_write_bytes(manifest_path, content)
        self.logger.info("Wrote manifest %s", manifest_path)

        return new_hash, True, False

    @staticmethod
    def render_json(data: Any) -> bytes:
        return (
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")

    @staticmethod
    def atomic_write_bytes(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=str(path.parent),
            delete=False,
        ) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)

        tmp_path.replace(path)

    @staticmethod
    def sha256_file(path: Path) -> str:
        digest = hashlib.sha256()

        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)

        return digest.hexdigest()

    @staticmethod
    def sha256_bytes(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()


# =============================================================================
# Extractor
# =============================================================================


class MLayerApiExtractor:
    """
    Main class-based extraction tool.

    This replaces the original MLayerCollections class while preserving the
    same basic purpose: fetch the core M-Layer API collections and write them as
    JSON files.
    """

    def __init__(
        self,
        config: MLayerApiExtractConfig,
        *,
        client: MLayerApiClient | None = None,
        writer: StableJsonDatasetWriter | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.logger = logger or LOG

        token = config.token or os.getenv("MLAYER_API_TOKEN")

        self.client = client or MLayerApiClient(
            base_url=config.base_url,
            token=token,
            timeout_seconds=config.timeout_seconds,
            retries=config.retries,
            user_agent=config.user_agent,
            logger=self.logger,
        )

        self.writer = writer or StableJsonDatasetWriter(
            output_dir=config.output_dir,
            skip_unchanged=config.skip_unchanged,
            overwrite=config.overwrite,
            logger=self.logger,
        )

    def run(self) -> MLayerApiExtractResult:
        generated_at_utc = self.now_utc_iso()

        manifest_path = (
            self.config.manifest_path
            if self.config.manifest_path is not None
            else self.config.output_dir / "manifest.json"
        )

        result = MLayerApiExtractResult(
            generated_at_utc=generated_at_utc,
            base_url=self.config.base_url,
            output_dir=str(self.config.output_dir),
            manifest_path=str(manifest_path),
            dry_run=self.config.dry_run,
        )

        for collection in self.config.collection_configs:
            collection_result = self.extract_collection(collection)
            result.collections[collection.name] = collection_result

        if not self.config.dry_run:
            self.write_manifest(result, manifest_path)

        self.log_result_summary(result)

        return result

    def extract_collection(
        self,
        collection: CollectionConfig,
    ) -> CollectionExtractResult:
        rows, status_code = self.client.get_collection(collection.endpoint)
        rows = self.normalize_collection(collection.name, rows)

        output_path = self.config.output_dir / collection.output_file

        if self.config.dry_run:
            self.logger.info(
                "Dry run: would write %s rows to %s",
                len(rows),
                output_path,
            )

            return CollectionExtractResult(
                name=collection.name,
                endpoint=collection.endpoint,
                output_file=str(output_path),
                row_count=len(rows),
                sha256=None,
                changed=False,
                skipped_write=True,
                status_code=status_code,
            )

        path, sha256, changed, skipped_write = self.writer.write_collection(
            filename=collection.output_file,
            rows=rows,
        )

        return CollectionExtractResult(
            name=collection.name,
            endpoint=collection.endpoint,
            output_file=str(path),
            row_count=len(rows),
            sha256=sha256,
            changed=changed,
            skipped_write=skipped_write,
            status_code=status_code,
        )

    def normalize_collection(
        self,
        collection_name: str,
        rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Normalize rows before writing.

        At the moment this mainly provides stable sorting. Additional
        collection-specific cleanup can be added here later if needed.
        """

        normalized = list(rows)

        if self.config.sort_rows:
            normalized.sort(key=lambda row: self.sort_key(collection_name, row))

        return normalized

    @staticmethod
    def sort_key(collection_name: str, row: dict[str, Any]) -> tuple[Any, ...]:
        """
        Stable per-collection sort key.

        Most collections have an id. Conversions and casts may not, so they use
        a semantic key to reduce noisy Git diffs.
        """

        if collection_name == "conversions":
            return (
                str(row.get("src_scale_id", "")),
                str(row.get("dst_scale_id", "")),
                str(row.get("aspect_id", "")),
                str(row.get("function_id", "")),
                json.dumps(row.get("parameters", ""), sort_keys=True, default=str),
            )

        if collection_name == "casts":
            return (
                str(row.get("src_scale_id", "")),
                str(row.get("dst_scale_id", "")),
                str(row.get("src_aspect_id", "")),
                str(row.get("dst_aspect_id", "")),
                str(row.get("function_id", "")),
                json.dumps(row.get("parameters", ""), sort_keys=True, default=str),
            )

        return (
            str(row.get("id", "")),
            str(row.get("ml_name", "")),
            str(row.get("name", "")),
            str(row.get("symbol", "")),
        )

    def write_manifest(
        self,
        result: MLayerApiExtractResult,
        manifest_path: Path,
    ) -> None:
        manifest = self.build_manifest(result)
        self.writer.write_manifest(
            manifest_path=manifest_path,
            manifest=manifest,
        )

    def build_manifest(self, result: MLayerApiExtractResult) -> dict[str, Any]:
        collections = {}

        for name, item in result.collections.items():
            collections[name] = {
                "endpoint": item.endpoint,
                "file": str(Path(item.output_file).name),
                "row_count": item.row_count,
                "sha256": item.sha256,
                "changed": item.changed,
                "skipped_write": item.skipped_write,
                "status_code": item.status_code,
            }

        return {
            "dataset_name": "mlayer",
            "generated_at_utc": result.generated_at_utc,
            "source": {
                "type": "api",
                "base_url": result.base_url,
            },
            "tool": {
                "name": "mlayer-api-extractor",
                "version": "1.0.0",
            },
            "collections": collections,
            "summary": {
                "collection_count": len(collections),
                "total_rows": result.total_rows,
                "changed_files": result.changed_files,
            },
        }

    def log_result_summary(self, result: MLayerApiExtractResult) -> None:
        self.logger.info("=" * 72)
        self.logger.info("M-Layer API extraction summary")
        self.logger.info("=" * 72)
        self.logger.info("Output path: %s", result.output_dir)
        self.logger.info("Base URL: %s", result.base_url)
        self.logger.info("Dry run: %s", result.dry_run)
        self.logger.info("Total rows: %s", result.total_rows)

        for name, item in result.collections.items():
            status = "changed" if item.changed else "unchanged/skipped"

            self.logger.info(
                "  %-15s rows=%s status=%s file=%s",
                name,
                item.row_count,
                status,
                item.output_file,
            )

    @staticmethod
    def now_utc_iso() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

