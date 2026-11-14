"""Compose generators over one reviewed corpus without application dependencies."""

from collections.abc import Sequence

from processing.common.llm import LoggerLike, ModelClient
from processing.common.errors import ProcessingValidationError
from .generators.insurance import InsuranceGenerator
from .generators.llm import LLMRequirementGenerator
from .generators.scope import ScopeGenerator
from .interface import RequirementGenerator, RequirementInput, RequirementOutput, RequirementProcessor
from .options import RequirementOptions
from .validation import validate_definition


class RequirementsProcessor(RequirementProcessor):
    def __init__(self, client: ModelClient | None = None, options: RequirementOptions = RequirementOptions(), *,
                 generators: Sequence[RequirementGenerator] | None = None):
        if generators is None:
            if client is None:
                raise ValueError("Default requirement generators need a model client.")
            generators = (ScopeGenerator(options), LLMRequirementGenerator(client, options),
                          InsuranceGenerator(options))
        self.generators = tuple(generators)

    def process(self, source: RequirementInput, *, logger: LoggerLike) -> RequirementOutput:
        registry = source.registry
        if source.scope.target_row is not None:
            card_ids = {entity.num for entity in registry.entities if entity.role == "card"}
            if not set(source.scope.target_row) <= card_ids:
                raise ProcessingValidationError("Target row references unknown report entities.")
        definitions = []
        for generator in self.generators:
            logger.info("Generating requirements with %s", type(generator).__name__)
            definitions.extend(validate_definition(definition, registry)
                               for definition in generator.generate(source, logger=logger))
            logger.info("Generator %s completed", type(generator).__name__)
        return RequirementOutput(tuple(definitions))
