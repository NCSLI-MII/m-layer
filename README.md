# Mlayer reference data

## Tools
The repository includes tools for 
- extracting data from the mlayer api at api.mlayer.org
- parsing sql dump data in text format
- comparing json and sql data

### API extractor
To update the data from source

Explicit API and manifest path:
```
python scripts/extract_mlayer_json.py \
  --base-url https://api.mlayer.org \
  --output-dir ./data/json \
  --manifest ./data/json/manifest.json
  ```

Dry-run
```
python scripts/extract_mlayer_json.py \
  --output-dir ./data/json \
  --dry-run
```

Rewrite files even if unchanged
```
python scripts/extract_mlayer_json.py \
  --output-dir ./data/json \
  --rewrite-unchanged
```

### Data summary

Typical usage:

```
    python summarize_mlayer_sources.py \
        --dump ./data/m_layer_v5.86.dmp \
        --json-dir ./data/json
```

JSON report:

```
    python summarize_mlayer_sources.py \
        --dump ./data/m_layer_v5.86.dmp \
        --json-dir ./data/json \
        --json-report ./reports/mlayer_source_audit.json
```
