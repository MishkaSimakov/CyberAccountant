"""Calculation constraints and field comparison data; never requirement verdicts."""

from dataclasses import dataclass, field
from processing.common.formula import Node


@dataclass
class Relation:
    index: int
    equation: str
    tag: str | None = None
    source: str = "llm"
    ast: Node | None = None
    status: str = "pending"  # Diagnostic status during solving; direct verdict in result relations.
    error: str | None = None
    unknowns: list = field(default_factory=list)
    involved_files: list = field(default_factory=list)
    touches_card: bool = False  # references at least one card entity
    requirement_id: int | None = None


@dataclass
class Fishy:
    index: int
    evidence: str
    ids_related: list = field(default_factory=list)
    verdict: str = "false"


@dataclass
class Verdict:
    key: str | int | None = None
    num: int = 0
    mnemonic: str = ""
    role: str = ""
    file: str = ""
    raw: str = ""
    type: str = ""
    status: str = ""  # ok | mismatch | unverified | constant
    stated: str | None = None
    expected: str | None = None
    diff: str | None = None
    target: bool = True  # False for non-target card cells (trusted constants)
