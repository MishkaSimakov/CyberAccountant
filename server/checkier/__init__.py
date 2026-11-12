"""CyberAccountant: entity-relation verification pipeline.

Verifies a summary report (card) against primary documents by
tagging numeric entities, extracting relations between them with an
LLM and solving the resulting equation system deterministically.
"""

from checkier.config import PipelineConfig
from checkier.pipeline import Pipeline
from checkier.stage1_tag import tag_document

__all__ = ["Pipeline", "PipelineConfig", "tag_document"]
