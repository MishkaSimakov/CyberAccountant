# Cyber Accountant

- [TASK.md](docs/TASK.md) - описание задачи.
- [DESIGN.md](docs/DESIGN.md) - описание дизайна системы.

## Pipeline

Library package `checkier` (under `server/`) that verifies the КФЗ card
against primary documents via entity tagging, LLM-extracted relations and a
deterministic equation solver.

```python
from pathlib import Path
from checkier import Pipeline, PipelineConfig

cfg = PipelineConfig(
    data_dir=Path("data_md"),
    out_dir=Path("out"),
    id_style="mnemonic",          # or "numeric"
    tag_model="deepseek/deepseek-v4.1-flash-20261017",
    rel_model="~deepseek/deepseek-pro-latest",
)
p = Pipeline(cfg)
report = p.run_all()
```

An interactive driver is available in
[notebooks/pipeline.ipynb](notebooks/pipeline.ipynb).

## Stages

| Stage | Module | What it does |
|-------|--------|--------------|
| 0 | `stage0_ingest.py` | Copy docs to `out/files/<h8>.md`, random obfuscated names, sha256 duplicate detection |
| 1 | `stage1_tag.py` | LLM wraps entities in `<ent t=... u=... n=...>` tags (per file, parallel) |
| 2 | `stage2_registry.py` | Global registry: numeric id + mnemonic, normalized value, unit, norm multiplier |
| 3 | `stage3_replace.py` | Rewrite tags to `<e id="...">` (mnemonic or numeric per `id_style`) |
| 4 | `stage4_scope.py` | Deterministic contract / period checks, tags out-of-scope docs |
| 5 | `stage5_relations.py` | LLM extracts equations + fishy evidence over the whole corpus |
| 6 | `solver.py` | Solve the system (данные = constants, card = variables) |
| 7 | `report.py` | Final report: ok / mismatch / unverified per card cell, dup/oos/dangle files |

## Setup

```bash
uv sync                     # install project deps into .venv
cp .env.example .env        # fill in AI_API_URL / AI_API_KEY
python scripts/fetch_bip39.py   # optional: canonical BIP39 wordlist
uv run pytest               # offline tests (formula / solver / ingest)
```

## Models

Configured via `PipelineConfig`. Defaults:
- tagging: `deepseek/deepseek-v4.1-flash-20261017`
- relations: `~deepseek/deepseek-pro-latest`

## Environment

- `AI_API_URL`, `AI_API_KEY` — OpenRouter endpoint and key (see `.env.example`).
