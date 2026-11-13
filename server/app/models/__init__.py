"""SQLAlchemy storage models; importing this module registers all tables."""

from .entity_file import EntityFile
from .file import File
from .report import Report
from .requirement import Requirement
from .verification_result import VerificationResult

__all__ = ["EntityFile", "File", "Report", "Requirement", "VerificationResult"]
