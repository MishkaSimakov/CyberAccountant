"""Generate contract and period requirements without evaluating them."""

import json

from processing.common.llm import LoggerLike
from processing.common.requirements import FormulaDefinition
from ..interface import RequirementGenerator, RequirementInput
from ..options import RequirementOptions


class ScopeGenerator(RequirementGenerator):
    def __init__(self, options: RequirementOptions = RequirementOptions()):
        self.options = options

    def generate(self, source: RequirementInput, *, logger: LoggerLike) -> list[FormulaDefinition]:
        scope = source.scope
        requirements = []
        for entity in source.registry.entities:
            if entity.role != "data":
                continue
            key = entity.mnemonic if self.options.id_style == "mnemonic" else f"#{entity.num}"
            if entity.type == "contract" and scope.contract_number:
                equation = f"{key} == {json.dumps(scope.contract_number.strip(), ensure_ascii=False)}"
            elif (entity.type == "date" and entity.loc == "body" and entity.value is not None
                  and scope.period_start and scope.period_end):
                equation = f"{scope.period_start:%d.%m.%Y} <= {key} && {key} <= {scope.period_end:%d.%m.%Y}"
            else:
                continue
            requirements.append(FormulaDefinition(equation=equation, tag="scope", source="scope"))
        return requirements
