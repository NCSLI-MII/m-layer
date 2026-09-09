#! /usr/bin/env python3
# vim:fenc=utf-8
#
# Copyright © 2026 Ryan Mackenzie White <ryan.white4@canada.ca>
#
# Distributed under terms of the Copyright © Her Majesty the Queen in Right of Canada, as represented by the Minister of Statistics Canada, 2019. license.

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


LOG = logging.getLogger("mlayer_source_audit")
# =============================================================================
# Data structures
# =============================================================================


@dataclass
class CopyBlock:
    table_name: str
    columns: list[str]
    rows: list[dict[str, Any]]


@dataclass
class SourceTableSummary:
    name: str
    row_count: int
    columns: list[str] = field(default_factory=list)
    null_counts: dict[str, int] = field(default_factory=dict)
    distinct_counts: dict[str, int] = field(default_factory=dict)
    sample_rows: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ConversionCastSummary:
    total: int = 0
    conversions: int = 0
    casts: int = 0
    missing_required_fields: dict[str, int] = field(default_factory=dict)
    duplicate_semantic_keys: int = 0
    unique_semantic_keys: int = 0


@dataclass
class KeyComparison:
    name: str
    sql_count: int
    json_count: int
    common_count: int
    only_sql_count: int
    only_json_count: int
    only_sql_sample: list[Any] = field(default_factory=list)
    only_json_sample: list[Any] = field(default_factory=list)


@dataclass
class SourceAuditReport:
    sql_tables: dict[str, SourceTableSummary] = field(default_factory=dict)
    json_collections: dict[str, SourceTableSummary] = field(default_factory=dict)
    sql_conversion_cast: ConversionCastSummary = field(default_factory=ConversionCastSummary)
    json_conversion_cast: ConversionCastSummary = field(default_factory=ConversionCastSummary)
    count_comparisons: list[KeyComparison] = field(default_factory=list)
    key_comparisons: list[KeyComparison] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# =============================================================================
# PostgreSQL dump parser
# =============================================================================


class PgDumpCopyParser:
    """
    Minimal parser for PostgreSQL plain-text pg_dump COPY data blocks.

    It ignores DDL and only reads sections like:

        COPY public.aspect (id, ml_name, name, symbol, sources) FROM stdin;
        ...
        \\.
    """

    COPY_RE = re.compile(
        r"^COPY\s+(?P<table>[^\s(]+)\s*\((?P<columns>[^)]*)\)\s+FROM\s+stdin;",
        re.IGNORECASE,
    )

    def __init__(self, tolerant: bool = True) -> None:
        self.tolerant = tolerant

    def parse(self, dump_path: Path) -> dict[str, list[dict[str, Any]]]:
        blocks = self.parse_blocks(dump_path)

        tables: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for block in blocks:
            tables[block.table_name].extend(block.rows)

        return dict(tables)

    def parse_blocks(self, dump_path: Path) -> list[CopyBlock]:
        blocks: list[CopyBlock] = []

        in_copy = False
        current_table: str | None = None
        current_columns: list[str] = []
        current_rows: list[dict[str, Any]] = []

        def finish_current() -> None:
            nonlocal in_copy, current_table, current_columns, current_rows

            if in_copy and current_table is not None:
                blocks.append(
                    CopyBlock(
                        table_name=current_table,
                        columns=current_columns,
                        rows=current_rows,
                    )
                )

            in_copy = False
            current_table = None
            current_columns = []
            current_rows = []

        with dump_path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
            for line_number, raw_line in enumerate(fh, start=1):
                line = raw_line.rstrip("\n")

                header = self.parse_copy_header(line)

                if header:
                    if in_copy:
                        message = (
                            f"COPY block for {current_table!r} was not terminated "
                            f"before line {line_number}; closing it"
                        )

                        if self.tolerant:
                            LOG.warning(message)
                            finish_current()
                        else:
                            raise ValueError(message)

                    current_table, current_columns = header
                    current_rows = []
                    in_copy = True
                    continue

                if not in_copy:
                    continue

                if line == r"\.":
                    finish_current()
                    continue

                if not line:
                    continue

                if line.startswith("--"):
                    if self.tolerant:
                        LOG.warning(
                            "Comment encountered inside COPY block %s at line %s; closing block",
                            current_table,
                            line_number,
                        )
                        finish_current()
                        continue

                    raise ValueError(
                        f"Comment encountered inside COPY block {current_table!r} "
                        f"at line {line_number}"
                    )

                values = line.split("\t")

                if len(values) != len(current_columns):
                    message = (
                        f"Malformed COPY row at line {line_number} for table "
                        f"{current_table!r}: expected {len(current_columns)} columns, "
                        f"got {len(values)}"
                    )

                    if self.tolerant:
                        LOG.warning("%s; skipping row", message)
                        continue

                    raise ValueError(message)

                row = {
                    col: self.pg_unescape_copy_value(value)
                    for col, value in zip(current_columns, values)
                }

                current_rows.append(row)

        finish_current()
        return blocks

    @classmethod
    def parse_copy_header(cls, line: str) -> tuple[str, list[str]] | None:
        match = cls.COPY_RE.match(line)

        if not match:
            return None

        table_name = cls.normalize_table_name(match.group("table"))
        columns = [col.strip().strip('"') for col in match.group("columns").split(",")]

        return table_name, columns

    @staticmethod
    def normalize_table_name(raw_name: str) -> str:
        name = raw_name.strip()
        parts = [part.strip().strip('"') for part in name.split(".")]
        return parts[-1]

    @staticmethod
    def pg_unescape_copy_value(value: str | None) -> Any:
        if value is None:
            return None

        if value == r"\N":
            return None

        replacements = {
            r"\b": "\b",
            r"\f": "\f",
            r"\n": "\n",
            r"\r": "\r",
            r"\t": "\t",
            r"\v": "\v",
            r"\\": "\\",
        }

        for src, dst in replacements.items():
            value = value.replace(src, dst)

        return value


# =============================================================================
# JSON source parser
# =============================================================================


class JsonSourceParser:
    """
    Parses JSON source collections from a directory.

    Defaults expect files:
        prefixes.json
        systems.json
        dimensions.json
        aspects.json
        units.json
        scales.json
        functions.json
        conversions.json
        casts.json

    Each file may contain:
        [...]
    or:
        {"data": [...]}
    or:
        {"results": [...]}
    or:
        {"items": [...]}
    or:
        {"<collection_name>": [...]}
    """

    DEFAULT_COLLECTION_FILES = {
        "prefixes": "prefixes.json",
        "systems": "systems.json",
        "dimensions": "dimensions.json",
        "aspects": "aspects.json",
        "units": "units.json",
        "scales": "scales.json",
        "functions": "functions.json",
        "conversions": "conversions.json",
        "casts": "casts.json",
    }

    def __init__(
        self,
        collection_files: dict[str, str] | None = None,
        strict: bool = False,
    ) -> None:
        self.collection_files = collection_files or dict(self.DEFAULT_COLLECTION_FILES)
        self.strict = strict

    def parse(self, json_dir: Path) -> dict[str, list[dict[str, Any]]]:
        collections: dict[str, list[dict[str, Any]]] = {}

        for collection_name, filename in self.collection_files.items():
            path = json_dir / filename

            if not path.exists():
                message = f"JSON collection file not found for {collection_name}: {path}"

                if self.strict:
                    raise FileNotFoundError(message)

                LOG.warning(message)
                collections[collection_name] = []
                continue

            with path.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)

            rows = self.extract_rows(payload, collection_name)
            collections[collection_name] = rows

        return collections

    @staticmethod
    def extract_rows(payload: Any, collection_name: str) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return payload

        if isinstance(payload, dict):
            for key in [collection_name, "data", "results", "items"]:
                value = payload.get(key)

                if isinstance(value, list):
                    return value

        raise ValueError(
            f"Could not extract rows for JSON collection {collection_name!r}"
        )


# =============================================================================
# Source summarizer and comparator
# =============================================================================


class MlayerSourceAuditor:
    """
    Summarizes and compares SQL dump source data and JSON source data without
    relying on application ORM/database models.
    """

    SQL_TO_JSON_COUNT_MAP = {
        "prefix": "prefixes",
        "system": "systems",
        "dimension": "dimensions",
        "aspect": "aspects",
        "unit": "units",
        "scale": "scales",
        "function": "functions",
        "aspect_scale": None,
        "conversion_cast": None,
        "reference": None,
    }

    SQL_TO_JSON_ENTITY_KEY_MAP = {
        "prefix": "prefixes",
        "system": "systems",
        "dimension": "dimensions",
        "aspect": "aspects",
        "unit": "units",
        "scale": "scales",
        "function": "functions",
    }

    def __init__(
        self,
        *,
        dump_path: Path | None = None,
        json_dir: Path | None = None,
        sample_limit: int = 5,
        distinct_limit_columns: int = 25,
        tolerant_dump_parser: bool = True,
    ) -> None:
        self.dump_path = dump_path
        self.json_dir = json_dir
        self.sample_limit = sample_limit
        self.distinct_limit_columns = distinct_limit_columns
        self.tolerant_dump_parser = tolerant_dump_parser

        self.sql_tables: dict[str, list[dict[str, Any]]] = {}
        self.json_collections: dict[str, list[dict[str, Any]]] = {}

    def run(self) -> SourceAuditReport:
        report = SourceAuditReport()

        if self.dump_path:
            self.sql_tables = PgDumpCopyParser(
                tolerant=self.tolerant_dump_parser
            ).parse(self.dump_path)

            report.sql_tables = self.summarize_source(self.sql_tables)

            report.sql_conversion_cast = self.summarize_sql_conversion_cast(
                self.sql_tables.get("conversion_cast", [])
            )

        if self.json_dir:
            self.json_collections = JsonSourceParser().parse(self.json_dir)

            report.json_collections = self.summarize_source(self.json_collections)

            report.json_conversion_cast = self.summarize_json_conversion_cast(
                conversions=self.json_collections.get("conversions", []),
                casts=self.json_collections.get("casts", []),
            )

        if self.dump_path and self.json_dir:
            report.count_comparisons = self.compare_counts()
            report.key_comparisons = self.compare_entity_keys()
            report.key_comparisons.append(self.compare_conversion_cast_keys())

        report.warnings = self.collect_warnings(report)

        return report

    # -------------------------------------------------------------------------
    # Generic summaries
    # -------------------------------------------------------------------------

    def summarize_source(
        self,
        source: dict[str, list[dict[str, Any]]],
    ) -> dict[str, SourceTableSummary]:
        summaries: dict[str, SourceTableSummary] = {}

        for name, rows in sorted(source.items()):
            columns = self.collect_columns(rows)
            null_counts = self.compute_null_counts(rows, columns)
            distinct_counts = self.compute_distinct_counts(rows, columns)

            summaries[name] = SourceTableSummary(
                name=name,
                row_count=len(rows),
                columns=columns,
                null_counts=null_counts,
                distinct_counts=distinct_counts,
                sample_rows=rows[: self.sample_limit],
            )

        return summaries

    @staticmethod
    def collect_columns(rows: list[dict[str, Any]]) -> list[str]:
        columns: list[str] = []
        seen = set()

        for row in rows:
            for key in row.keys():
                if key not in seen:
                    columns.append(key)
                    seen.add(key)

        return columns

    @staticmethod
    def compute_null_counts(
        rows: list[dict[str, Any]],
        columns: list[str],
    ) -> dict[str, int]:
        result: dict[str, int] = {}

        for column in columns:
            result[column] = sum(
                1
                for row in rows
                if row.get(column) is None or row.get(column) == ""
            )

        return result

    def compute_distinct_counts(
        self,
        rows: list[dict[str, Any]],
        columns: list[str],
    ) -> dict[str, int]:
        result: dict[str, int] = {}

        for column in columns[: self.distinct_limit_columns]:
            values = {
                self.normalise_hashable(row.get(column))
                for row in rows
                if row.get(column) is not None and row.get(column) != ""
            }
            result[column] = len(values)

        return result

    @staticmethod
    def normalise_hashable(value: Any) -> Any:
        if isinstance(value, (dict, list)):
            return json.dumps(value, sort_keys=True)

        return value

    # -------------------------------------------------------------------------
    # Conversion/cast summaries
    # -------------------------------------------------------------------------

    def summarize_sql_conversion_cast(
        self,
        rows: list[dict[str, Any]],
    ) -> ConversionCastSummary:
        summary = ConversionCastSummary(total=len(rows))

        semantic_keys = []

        missing_counter: Counter[str] = Counter()

        for row in rows:
            is_cast = self.coerce_bool(row.get("is_cast"))

            if is_cast:
                summary.casts += 1
            else:
                summary.conversions += 1

            required = self.required_conversion_cast_fields_for_sql(row)

            for field_name in required:
                if not row.get(field_name):
                    missing_counter[field_name] += 1

            semantic_keys.append(self.sql_conversion_cast_key(row))

        summary.missing_required_fields = dict(missing_counter)

        key_counts = Counter(semantic_keys)
        summary.unique_semantic_keys = len(key_counts)
        summary.duplicate_semantic_keys = sum(
            count - 1
            for count in key_counts.values()
            if count > 1
        )

        return summary

    def summarize_json_conversion_cast(
        self,
        *,
        conversions: list[dict[str, Any]],
        casts: list[dict[str, Any]],
    ) -> ConversionCastSummary:
        summary = ConversionCastSummary(
            total=len(conversions) + len(casts),
            conversions=len(conversions),
            casts=len(casts),
        )

        semantic_keys = []
        missing_counter: Counter[str] = Counter()

        for row in conversions:
            required = [
                "src_scale_id",
                "dst_scale_id",
                "aspect_id",
                "function_id",
            ]

            for field_name in required:
                if not row.get(field_name):
                    missing_counter[f"conversion.{field_name}"] += 1

            semantic_keys.append(self.json_conversion_key(row))

        for row in casts:
            required = [
                "src_scale_id",
                "dst_scale_id",
                "src_aspect_id",
                "dst_aspect_id",
                "function_id",
            ]

            for field_name in required:
                if not row.get(field_name):
                    missing_counter[f"cast.{field_name}"] += 1

            semantic_keys.append(self.json_cast_key(row))

        summary.missing_required_fields = dict(missing_counter)

        key_counts = Counter(semantic_keys)
        summary.unique_semantic_keys = len(key_counts)
        summary.duplicate_semantic_keys = sum(
            count - 1
            for count in key_counts.values()
            if count > 1
        )

        return summary

    @staticmethod
    def required_conversion_cast_fields_for_sql(row: dict[str, Any]) -> list[str]:
        is_cast = MlayerSourceAuditor.coerce_bool(row.get("is_cast"))

        if is_cast:
            return [
                "src_scale_id",
                "dst_scale_id",
                "src_aspect_id",
                "dst_aspect_id",
                "function_id",
            ]

        if row.get("aspect_id"):
            return [
                "src_scale_id",
                "dst_scale_id",
                "aspect_id",
                "function_id",
            ]

        return [
            "src_scale_id",
            "dst_scale_id",
            "src_aspect_id",
            "dst_aspect_id",
            "function_id",
        ]

    # -------------------------------------------------------------------------
    # Comparisons
    # -------------------------------------------------------------------------

    def compare_counts(self) -> list[KeyComparison]:
        comparisons: list[KeyComparison] = []

        for sql_table, json_collection in self.SQL_TO_JSON_COUNT_MAP.items():
            if json_collection is None:
                continue

            sql_count = len(self.sql_tables.get(sql_table, []))
            json_count = len(self.json_collections.get(json_collection, []))

            common = min(sql_count, json_count)

            comparisons.append(
                KeyComparison(
                    name=f"count:{sql_table}~{json_collection}",
                    sql_count=sql_count,
                    json_count=json_count,
                    common_count=common,
                    only_sql_count=max(sql_count - json_count, 0),
                    only_json_count=max(json_count - sql_count, 0),
                )
            )

        # Special comparison: SQL conversion_cast vs JSON conversions + casts.
        sql_count = len(self.sql_tables.get("conversion_cast", []))
        json_count = len(self.json_collections.get("conversions", [])) + len(
            self.json_collections.get("casts", [])
        )

        comparisons.append(
            KeyComparison(
                name="count:conversion_cast~conversions+casts",
                sql_count=sql_count,
                json_count=json_count,
                common_count=min(sql_count, json_count),
                only_sql_count=max(sql_count - json_count, 0),
                only_json_count=max(json_count - sql_count, 0),
            )
        )

        return comparisons

    def compare_entity_keys(self) -> list[KeyComparison]:
        comparisons: list[KeyComparison] = []

        for sql_table, json_collection in self.SQL_TO_JSON_ENTITY_KEY_MAP.items():
            sql_keys = {
                row.get("id")
                for row in self.sql_tables.get(sql_table, [])
                if row.get("id")
            }

            json_keys = {
                row.get("id")
                for row in self.json_collections.get(json_collection, [])
                if row.get("id")
            }

            comparisons.append(
                self.compare_key_sets(
                    name=f"keys:{sql_table}~{json_collection}",
                    sql_keys=sql_keys,
                    json_keys=json_keys,
                )
            )

        return comparisons

    def compare_conversion_cast_keys(self) -> KeyComparison:
        sql_keys = {
            self.sql_conversion_cast_key(row)
            for row in self.sql_tables.get("conversion_cast", [])
        }

        json_keys = set()

        for row in self.json_collections.get("conversions", []):
            json_keys.add(self.json_conversion_key(row))

        for row in self.json_collections.get("casts", []):
            json_keys.add(self.json_cast_key(row))

        return self.compare_key_sets(
            name="keys:conversion_cast~conversions+casts",
            sql_keys=sql_keys,
            json_keys=json_keys,
        )

    def compare_key_sets(
        self,
        *,
        name: str,
        sql_keys: set[Any],
        json_keys: set[Any],
    ) -> KeyComparison:
        only_sql = sorted(sql_keys - json_keys, key=lambda item: repr(item))
        only_json = sorted(json_keys - sql_keys, key=lambda item: repr(item))

        return KeyComparison(
            name=name,
            sql_count=len(sql_keys),
            json_count=len(json_keys),
            common_count=len(sql_keys & json_keys),
            only_sql_count=len(only_sql),
            only_json_count=len(only_json),
            only_sql_sample=only_sql[: self.sample_limit],
            only_json_sample=only_json[: self.sample_limit],
        )

    # -------------------------------------------------------------------------
    # Semantic keys
    # -------------------------------------------------------------------------

    @classmethod
    def sql_conversion_cast_key(cls, row: dict[str, Any]) -> tuple[Any, ...]:
        is_cast = cls.coerce_bool(row.get("is_cast"))

        if is_cast:
            src_aspect_id = row.get("src_aspect_id")
            dst_aspect_id = row.get("dst_aspect_id")
        else:
            aspect_id = row.get("aspect_id")
            src_aspect_id = row.get("src_aspect_id") or aspect_id
            dst_aspect_id = row.get("dst_aspect_id") or aspect_id

        return (
            is_cast,
            row.get("src_scale_id"),
            row.get("dst_scale_id"),
            src_aspect_id,
            dst_aspect_id,
            row.get("function_id") or row.get("transform_id"),
            cls.normalise_parameters(row.get("parameters")),
        )

    @classmethod
    def json_conversion_key(cls, row: dict[str, Any]) -> tuple[Any, ...]:
        aspect_id = row.get("aspect_id")

        return (
            False,
            row.get("src_scale_id"),
            row.get("dst_scale_id"),
            row.get("src_aspect_id") or aspect_id,
            row.get("dst_aspect_id") or aspect_id,
            row.get("function_id") or row.get("transform_id"),
            cls.normalise_parameters(row.get("parameters")),
        )

    @classmethod
    def json_cast_key(cls, row: dict[str, Any]) -> tuple[Any, ...]:
        return (
            True,
            row.get("src_scale_id"),
            row.get("dst_scale_id"),
            row.get("src_aspect_id"),
            row.get("dst_aspect_id"),
            row.get("function_id") or row.get("transform_id"),
            cls.normalise_parameters(row.get("parameters")),
        )

    @staticmethod
    def normalise_parameters(value: Any) -> str | None:
        """
        Normalize parameters for comparison.

        This intentionally stays conservative. If the two sources format
        parameters differently, this may need to be expanded.
        """
        if value is None:
            return None

        if isinstance(value, str):
            text = value.strip()

            if text == "":
                return None

            if text == "{}":
                return "{}"

            # Try JSON object normalization.
            try:
                parsed = json.loads(text)
                return json.dumps(parsed, sort_keys=True, separators=(",", ":"))
            except Exception:
                return text

        if isinstance(value, (dict, list)):
            return json.dumps(value, sort_keys=True, separators=(",", ":"))

        return str(value)

    # -------------------------------------------------------------------------
    # Warnings
    # -------------------------------------------------------------------------

    def collect_warnings(self, report: SourceAuditReport) -> list[str]:
        warnings: list[str] = []

        if report.sql_conversion_cast.duplicate_semantic_keys:
            warnings.append(
                f"SQL conversion_cast has "
                f"{report.sql_conversion_cast.duplicate_semantic_keys} duplicate semantic keys"
            )

        if report.json_conversion_cast.duplicate_semantic_keys:
            warnings.append(
                f"JSON conversions/casts have "
                f"{report.json_conversion_cast.duplicate_semantic_keys} duplicate semantic keys"
            )

        for comparison in report.count_comparisons:
            if comparison.only_sql_count or comparison.only_json_count:
                warnings.append(
                    f"Count mismatch for {comparison.name}: "
                    f"sql={comparison.sql_count}, json={comparison.json_count}"
                )

        for comparison in report.key_comparisons:
            if comparison.only_sql_count or comparison.only_json_count:
                warnings.append(
                    f"Key mismatch for {comparison.name}: "
                    f"only_sql={comparison.only_sql_count}, "
                    f"only_json={comparison.only_json_count}"
                )

        return warnings

    # -------------------------------------------------------------------------
    # Basic coercion
    # -------------------------------------------------------------------------

    @staticmethod
    def coerce_bool(value: Any) -> bool:
        if value is None or value == "":
            return False

        if isinstance(value, bool):
            return value

        text = str(value).strip().lower()

        if text in {"t", "true", "1", "yes", "y"}:
            return True

        if text in {"f", "false", "0", "no", "n"}:
            return False

        raise ValueError(f"Cannot coerce to bool: {value!r}")


# =============================================================================
# Report printing
# =============================================================================


class ReportPrinter:
    def __init__(
        self,
        *,
        sample_limit: int = 5,
        show_columns: bool = True,
        show_nulls: bool = False,
        show_distincts: bool = False,
        show_samples: bool = False,
    ) -> None:
        self.sample_limit = sample_limit
        self.show_columns = show_columns
        self.show_nulls = show_nulls
        self.show_distincts = show_distincts
        self.show_samples = show_samples

    def print(self, report: SourceAuditReport) -> None:
        self.print_source_summary("SQL dump tables", report.sql_tables)
        self.print_source_summary("JSON collections", report.json_collections)

        self.print_conversion_cast_summary(
            "SQL conversion_cast",
            report.sql_conversion_cast,
        )

        self.print_conversion_cast_summary(
            "JSON conversions + casts",
            report.json_conversion_cast,
        )

        self.print_comparisons("Count comparisons", report.count_comparisons)
        self.print_comparisons("Key comparisons", report.key_comparisons)
        self.print_warnings(report.warnings)

    def print_source_summary(
        self,
        title: str,
        summaries: dict[str, SourceTableSummary],
    ) -> None:
        print()
        print("=" * 80)
        print(title)
        print("=" * 80)

        if not summaries:
            print("No data.")
            return

        for name, summary in sorted(summaries.items()):
            print(f"- {name}: {summary.row_count} rows")

            if self.show_columns:
                print(f"  columns: {', '.join(summary.columns)}")

            if self.show_nulls:
                nulls = {
                    key: value
                    for key, value in summary.null_counts.items()
                    if value
                }
                print(f"  null/empty counts: {nulls}")

            if self.show_distincts:
                print(f"  distinct counts: {summary.distinct_counts}")

            if self.show_samples:
                print("  sample rows:")
                for row in summary.sample_rows[: self.sample_limit]:
                    print(f"    {row}")

    @staticmethod
    def print_conversion_cast_summary(
        title: str,
        summary: ConversionCastSummary,
    ) -> None:
        print()
        print("=" * 80)
        print(title)
        print("=" * 80)
        print(f"total:                   {summary.total}")
        print(f"conversions:             {summary.conversions}")
        print(f"casts:                   {summary.casts}")
        print(f"unique semantic keys:    {summary.unique_semantic_keys}")
        print(f"duplicate semantic keys: {summary.duplicate_semantic_keys}")

        if summary.missing_required_fields:
            print("missing required fields:")
            for field_name, count in sorted(summary.missing_required_fields.items()):
                print(f"  {field_name}: {count}")

    @staticmethod
    def print_comparisons(
        title: str,
        comparisons: list[KeyComparison],
    ) -> None:
        print()
        print("=" * 80)
        print(title)
        print("=" * 80)

        if not comparisons:
            print("No comparisons.")
            return

        for comparison in comparisons:
            status = (
                "OK"
                if comparison.only_sql_count == 0 and comparison.only_json_count == 0
                else "DIFF"
            )

            print(
                f"- {comparison.name}: "
                f"sql={comparison.sql_count}, "
                f"json={comparison.json_count}, "
                f"common={comparison.common_count}, "
                f"only_sql={comparison.only_sql_count}, "
                f"only_json={comparison.only_json_count} "
                f"[{status}]"
            )

            if comparison.only_sql_sample:
                print(f"  only SQL sample: {comparison.only_sql_sample}")

            if comparison.only_json_sample:
                print(f"  only JSON sample: {comparison.only_json_sample}")

    @staticmethod
    def print_warnings(warnings: list[str]) -> None:
        print()
        print("=" * 80)
        print("Warnings")
        print("=" * 80)

        if not warnings:
            print("No warnings.")
            return

        for warning in warnings:
            print(f"- {warning}")
