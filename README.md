# M-Layer Source Data Tools

This repository contains versioned M-Layer source data and standalone tools for extracting, auditing, and comparing M-Layer reference data.

The repository is intended to support a controlled source-data update workflow:

1. Create an issue describing the proposed source-data update.
2. Create a corresponding branch.
3. Update the source data:
   - run the JSON extractor when updating from the M-Layer API; or
   - add/update the SQL dump under `source/sqldata`.
4. Run the source auditor to compare JSON and SQL data.
5. Review the generated audit summary.
6. If the changes are expected, merge the branch.
7. Create a release tag and include the audit summary in the release notes.

The tools are intentionally standalone. They do not depend on the application database, Flask, SQLAlchemy, SQLite, or the M-Layer ORM.

---

## Source-data update process
1. Create an issue
Create a GitHub issue describing:
the source being updated;
whether the update is from the M-Layer API JSON endpoint or from SQL dump data;
the reason for the update;
any known expected differences;
the target release, if known.

2. Create a branch
Create a branch linked to the issue.
Recommended branch naming pattern:

`source/update-mlayer-data-<issue-number>`

Example
`git checkout -b source/update-mlayer-data-123`

3. Update Json source data

```bash
python extract_mlayer_json.py \
  --output-dir ./source/json \
  --manifest ./source/json/manifest.json \
  --log-level INFO
```

Review changed files:
```bash
git status
git diff -- source/json
```

Commit the changes:

```bash
git add source/json
git commit -m "Update M-Layer JSON source data"
```

4. Update SQL source data
If updating from SQL, place the PostgreSQL plain-text dump under:
`source/sqldump`

Review and commit changes
```bash
git add source/sqldata
git commit -m "Update M-Layer SQL source data"
```

Run the auditor locally
```bash
mkdir -p reports

python tools/summarize_mlayer_sources.py \
  --dump ./source/sqldata/m_layer_current.dmp \
  --json-dir ./source/json \
  --json-report ./reports/mlayer_source_audit.json \
  --show-null-counts \
  --show-distinct-counts \
  --show-samples
```

Or run with the SQL filtering
```
python tools/summarize_mlayer_sources.py \
  --dump ./source/sqldata/m_layer_current.dmp \
  --json-dir ./source/json \
  --filter-sql-conversion-cast \
  --json-report ./reports/mlayer_source_audit.json \
  --show-null-counts \
  --show-distinct-counts \
  --show-samples
```

Review the output

6. Open the pull request
The GitHub Actions workflow will run the auditor and upload the report artifacts.
The pull request should summarize:
files changed;
source of the update;
whether auditor warnings are expected;
any differences that require reviewer attention.

7. Merge and tag
```bash
git checkout main
git pull
git tag vYYYY.MM.DD
git push origin vYYYY.MM.DD
```

## Repository layout

```text
.
├── source/
│   ├── json/
│   │   ├── systems.json
│   │   ├── dimensions.json
│   │   ├── aspects.json
│   │   ├── units.json
│   │   ├── scales.json
│   │   ├── functions.json
│   │   ├── conversions.json
│   │   └── casts.json
│   │   └── prefixes.json
│   └── sqldata/
│       └── m_layer_current.dmp
├── tools/
│   ├── extract_mlayer_json.py
│   ├── mlayer_api_extractor.py
│   ├── summarize_mlayer_sources.py
│   └── mlayer_source_auditor.py
└── .github/
    └── workflows/
        └── mlayer-source-audit.yml
```

### `source/json`
Expected collections retrieved from the API at api.mlayer.org
- `systems.json`
- `dimensions.json`
- `aspects.json`
- `units.json`
- `scales.json`
- `functions.json`
- `conversions.json`
- `casts.json`
- `prefixes.json`

### `source/sqldata`

Contains PostgreSQL plain-text `pg_dump` data.

The auditor reads `COPY ... FROM stdin;` blocks and extracts table rows from the dump. It ignores DDL and does not require a running database.

---
## Tools
The repository includes tools for 
- extracting data from the mlayer api at api.mlayer.org
- parsing sql dump data in text format
- comparing json and sql data

### JSON API extractor

Entry point:

```bash
python tools/extract_mlayer_json.py
```

Explicit API and manifest path:
```bash
python extract_mlayer_json.py \
  --base-url https://api.mlayer.org \
  --output-dir ./data/json \
  --manifest ./data/json/manifest.json
  ```

Dry-run
```bash
python extract_mlayer_json.py \
  --output-dir ./data/json \
  --dry-run
```

Rewrite files even if unchanged
```bash
python scripts/extract_mlayer_json.py \
  --output-dir ./data/json \
  --rewrite-unchanged
```

Useful options
```text
--base-url URL             Base URL for the M-Layer API.
                           Default: https://api.mlayer.org

--output-dir PATH          Directory where JSON files will be written.
                           Default: ./data/json

--manifest PATH            Path to manifest JSON.
                           Defaults to <output-dir>/manifest.json.

--token TOKEN              Optional bearer token.
                           If omitted, MLAYER_API_TOKEN is used if set.

--timeout SECONDS          HTTP request timeout.
                           Default: 30

--retries COUNT            Number of retries for transient HTTP errors.
                           Default: 3

--dry-run                  Fetch and summarize data, but do not write files.

--no-sort                  Do not sort rows before writing JSON.

--rewrite-unchanged        Rewrite JSON files even when content is unchanged.

--no-overwrite             Fail if an output file already exists.

--log-level LEVEL          Logging level.
                           Example: DEBUG, INFO, WARNING.
```

### Data source auditor 

Entry point
```bash
python summarize_mlayer_sources.py
```
The auditor compares:
SQL dump source data; and
JSON source data.
It produces:
SQL dump summary
JSON source summary
high-level count comparison
entity key comparison
conversion/cast semantic comparison
optional machine-readable JSON report

Typical usage:

```bash
    python summarize_mlayer_sources.py \
        --dump ./data/m_layer_v5.86.dmp \
        --json-dir ./data/json
```

JSON report:

```bash
    python summarize_mlayer_sources.py \
        --dump ./data/m_layer_v5.86.dmp \
        --json-dir ./data/json \
        --json-report ./reports/mlayer_source_audit.json
```

Generate a more human readable report

```bash
python tools/summarize_mlayer_sources.py \
  --dump ./source/sqldata/m_layer_current.dmp \
  --json-dir ./source/json \
  --show-null-counts \
  --show-distinct-counts \
  --show-samples
```

Useful options
```text
--dump PATH                PostgreSQL plain-text pg_dump file to audit.

--json-dir PATH            Directory containing JSON source files.

--json-report PATH         Optional machine-readable JSON report output path.

--sample-limit COUNT       Number of sample rows or keys to include.
                           Default: 5

--log-level LEVEL          Logging level.
                           Default: WARNING

--show-null-counts         Show null/empty counts by column.

--show-distinct-counts     Show distinct counts by column.

--show-samples             Show sample rows and key samples.

--hide-columns             Hide column lists in the text report.

--strict-dump-parser       Fail on malformed dump rows instead of logging and
                           skipping them.
```

#### SQL Conversion/Cast filter on null aspect
The auditor can be extended to produce before-and-after comparisons for SQL conversion_cast data.
The intended filter removes SQL conversion_cast rows where:
`aspect_id = AS1` or `scale_id=SC1018`


The before-and-after report should include:

1. Raw SQL versus JSON comparison.
2. Filtered SQL versus JSON comparison.
3. A filter summary showing:
   - original SQL `conversion_cast` row count;
   - filtered SQL `conversion_cast` row count;
   - removed row count;
   - removal reasons;
   - optional sample of removed rows.

Recommended command after implementing the filter flag:

```bash
python tools/summarize_mlayer_sources.py \
  --dump ./source/sqldata/m_layer_current.dmp \
  --json-dir ./source/json \
  --filter-sql-conversion-cast \
  --json-report ./reports/mlayer_source_audit.json
```

