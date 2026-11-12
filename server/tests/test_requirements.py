"""Requirement verdicts use the same string values in storage and the API."""

import pytest
from pydantic import ValidationError

from app.models.requirement import RequirementVerdict
from app.schemas.requirements import RequirementResponse


@pytest.mark.parametrize("verdict", list(RequirementVerdict))
def test_requirement_verdict_serializes_as_string(verdict):
    result = RequirementResponse(id=1, verdict=verdict)
    assert result.model_dump(mode="json")["verdict"] == verdict.value


@pytest.mark.parametrize("verdict", [True, False, "unknown", "unverified", "ok", "mismatch", "constant"])
def test_requirement_verdict_rejects_other_values(verdict):
    with pytest.raises(ValidationError):
        RequirementResponse(id=1, verdict=verdict)
