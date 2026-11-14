"""Pipeline configuration and API credentials."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_TAG_MODEL = "deepseek/deepseek-v4.1-flash-20261017"
# DEFAULT_REL_MODEL = "~deepseek/deepseek-pro-latest"
# DEFAULT_REL_MODEL = "deepseek/deepseek-v4.1-flash-20261017"
DEFAULT_REL_MODEL = "deepseek/deepseek-v4-pro-20260813"


def _model_from_env(name: str, default: str) -> str:
    load_dotenv()
    return os.environ.get(name, default)


#: folder name -> role
ROLE_DIRS = {"данные": "data", "card": "card", "request": "request"}

ID_STYLES = ("mnemonic", "numeric")
RELATION_FILENAME_MODES = ("tagged", "raw", "none")


@dataclass
class PipelineConfig:
    data_dir: Path
    out_dir: Path
    id_style: str = "mnemonic"
    tag_model: str = field(default_factory=lambda: _model_from_env("AI_TAG_MODEL", DEFAULT_TAG_MODEL))
    rel_model: str = field(default_factory=lambda: _model_from_env("AI_REL_MODEL", DEFAULT_REL_MODEL))
    seed: int = 20261112
    max_retries: int = 2
    request_timeout: float = 300.0
    tag_workers: int = 8
    # "disabled" requests low/minimum effort; "enabled" uses the model default.
    # "exclude" relies on the client always filtering reasoning from output.
    tag_reasoning: str = "disabled"
    rel_reasoning: str = "enabled"
    # Relation prompts: processed filename tags, raw original names, or neutral labels.
    relation_filename_mode: str = "tagged"
    # Stage 5 only: omit entity text and normalized values from the model input.
    relation_hide_entity_values: bool = False
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        self.out_dir = Path(self.out_dir)
        if self.id_style not in ID_STYLES:
            raise ValueError(f"id_style must be one of {ID_STYLES}, got {self.id_style!r}")
        if self.relation_filename_mode not in RELATION_FILENAME_MODES:
            raise ValueError(
                f"relation_filename_mode must be one of {RELATION_FILENAME_MODES}, "
                f"got {self.relation_filename_mode!r}"
            )

    # -- artifact paths -----------------------------------------------------
    @property
    def files_dir(self) -> Path:
        return self.out_dir / "files"

    @property
    def tagged_dir(self) -> Path:
        return self.out_dir / "tagged"

    @property
    def replaced_dir(self) -> Path:
        return self.out_dir / "replaced"

    @property
    def mapping_path(self) -> Path:
        return self.out_dir / "mapping.json"

    @property
    def entities_path(self) -> Path:
        return self.out_dir / "entities.json"

    @property
    def relations_path(self) -> Path:
        return self.out_dir / "relations.json"

    @property
    def verdicts_path(self) -> Path:
        return self.out_dir / "verdicts.json"

    @property
    def report_path(self) -> Path:
        return self.out_dir / "report.json"


def load_api_credentials(env_file: str | os.PathLike | None = None) -> tuple[str, str]:
    """Return (AI_API_URL base URL, AI_API_KEY) from environment / .env file."""
    load_dotenv(env_file)
    url = os.environ.get("AI_API_URL")
    key = os.environ.get("AI_API_KEY")
    if not url or not key:
        raise RuntimeError(
            "AI_API_URL / AI_API_KEY are not set; copy .env.example to .env and fill them in"
        )
    return url, key
