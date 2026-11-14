"""Shared input for requirement generation and verification."""

from dataclasses import dataclass

from checkier import PipelineConfig
from processing.common.registry import EntityRegistry

from .models import Report


@dataclass
class VerificationContext:
    config: PipelineConfig
    documents: dict[str, str]
    registry: EntityRegistry
    scope: dict
    mapping: dict
    report: Report
