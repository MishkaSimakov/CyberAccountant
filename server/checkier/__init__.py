"""CyberAccountant: entity-relation verification pipeline.

Verifies a summary report (card) against primary documents by
tagging numeric entities, extracting relations between them with an
LLM and solving the resulting equation system deterministically.
"""

from cyberaccountant.config import PipelineConfig
from cyberaccountant.pipeline import Pipeline

__all__ = ["Pipeline", "PipelineConfig"]
