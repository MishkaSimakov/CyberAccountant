"""Standalone file loading and batching for the pure entity tagger."""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from checkier.config import PipelineConfig
from processing.common.llm import ModelClient
from processing.entities.options import EntityOptions
from processing.entities import tagging

@dataclass
class TagProgress:
    """Per-file tagging outcome, reported to the progress callback."""

    file: str  # obfuscated h8
    original: str
    ok: bool
    entities: int = 0
    retries: int = 0
    error: str | None = None


@dataclass
class TagStats:
    """Running totals across all files."""

    total: int = 0
    done: int = 0
    ok: int = 0
    failed: int = 0
    entities: int = 0
    retries: int = 0
    per_file: list[TagProgress] = field(default_factory=list)


TagCallback = Callable[[TagProgress, TagStats], None]

def _tag_one(
        cfg: PipelineConfig,
        h8: str,
        original_name: str,
        on_token: Callable[[str], None] | None = None,
        *, client: ModelClient,
        logger: logging.Logger | logging.LoggerAdapter | None = None,
) -> tuple[str, int]:
    """Tag one file. Returns (tagged_text, retries_used)."""
    src = cfg.files_dir / f"{h8}.md"
    return tagging.tag_document(EntityOptions.from_settings(cfg), original_name, src.read_text(encoding="utf-8"), on_token, client=client, logger=logger)


def run_tag(
        cfg: PipelineConfig,
        mapping: dict,
        on_progress: TagCallback | None = None,
        on_token: Callable[[str, str], None] | None = None,
        *, client: ModelClient,
        logger: logging.Logger | logging.LoggerAdapter | None = None,
) -> dict[str, tuple[str, str]]:
    """Tag all non-duplicate files. Returns {h8: (role, tagged_text)}.

    on_progress: called after each file finishes (TagProgress, TagStats).
    on_token: called with (file, token) for every streamed LLM token so the
    caller can show live output while a file is being generated.
    """
    logger = logger if logger is not None else logging.getLogger(__name__)
    cfg.tagged_dir.mkdir(parents=True, exist_ok=True)
    todo = [
        (h8, entry["role"], Path(entry["original"]).name)
        for h8, entry in mapping.items()
        if not entry.get("duplicate_of")
    ]
    stats = TagStats(total=len(todo))
    results: dict[str, tuple[str, str]] = {}

    def _work(h8: str, role: str, name: str) -> tuple[str, str, str, int, str]:
        def _tok(tok: str, _h8: str = h8) -> None:
            if on_token:
                on_token(_h8, tok)

        tagged, retries = _tag_one(cfg, h8, name, on_token=_tok, client=client, logger=logger)
        return h8, role, tagged, retries, name

    with ThreadPoolExecutor(max_workers=cfg.tag_workers) as pool:
        futs = {pool.submit(_work, h8, role, name): h8 for h8, role, name in todo}
        for fut in as_completed(futs):
            h8 = futs[fut]
            try:
                h8, role, tagged, retries, name = fut.result()
                (cfg.tagged_dir / f"{h8}.md").write_text(tagged, encoding="utf-8")
                results[h8] = (role, tagged)
                ent_count = tagged.count("<ent ")
                prog = TagProgress(
                    file=h8, original=name, ok=True,
                    entities=ent_count, retries=retries,
                )
                stats.done += 1
                stats.ok += 1
                stats.entities += ent_count
                stats.retries += retries
            except Exception as exc:  # noqa: BLE001
                logger.exception("Tagging failed for document %s", h8)
                name = next(n for hh, _, n in todo if hh == h8)
                prog = TagProgress(
                    file=h8, original=name, ok=False,
                    error=str(exc)[:300],
                )
                stats.done += 1
                stats.failed += 1
            stats.per_file.append(prog)
            if on_progress:
                on_progress(prog, stats)
    return results
