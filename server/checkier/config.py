"""Pipeline configuration and API credentials."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_TAG_MODEL = "deepseek/deepseek-v4.1-flash-20261017"
# DEFAULT_REL_MODEL = "~deepseek/deepseek-pro-latest"
DEFAULT_REL_MODEL = "deepseek/deepseek-v4.1-flash-20261017"

#: folder name -> role
ROLE_DIRS = {"данные": "data", "card": "card", "request": "request"}

ID_STYLES = ("mnemonic", "numeric")


@dataclass
class PipelineConfig:
    data_dir: Path
    out_dir: Path
    id_style: str = "mnemonic"
    tag_model: str = DEFAULT_TAG_MODEL
    rel_model: str = DEFAULT_REL_MODEL
    seed: int = 20261112
    max_retries: int = 2
    request_timeout: float = 300.0
    tag_workers: int = 8
    # LLM reasoning control: "disabled" (fast, no thinking tokens), "enabled",
    # or "exclude" (reason but strip from output). See OpenRouter reasoning docs.
    reasoning: str = "disabled"
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        self.out_dir = Path(self.out_dir)
        if self.id_style not in ID_STYLES:
            raise ValueError(f"id_style must be one of {ID_STYLES}, got {self.id_style!r}")

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
    def scope_path(self) -> Path:
        return self.out_dir / "scope.json"

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
    """Return (AI_API_URL, AI_API_KEY) from environment / .env file."""
    load_dotenv(env_file)
    url = os.environ.get("AI_API_URL")
    key = os.environ.get("AI_API_KEY")
    if not url or not key:
        raise RuntimeError(
            "AI_API_URL / AI_API_KEY are not set; copy .env.example to .env and fill them in"
        )
    return url, key
