"""Default model processor; alternative extraction strategies share the interface."""

from processing.common.errors import ProcessingValidationError
from processing.common.llm import LoggerLike, ModelClient, TokenCallback

from .corpus import parse_drafts
from .identifiers import read_identities
from .interface import EntityInput, EntityOutput, EntityProcessor
from .normalization import normalize_document
from .options import EntityOptions
from .tagging import tag_document


class LLMEntityProcessor(EntityProcessor):
    def __init__(self, client: ModelClient, options: EntityOptions = EntityOptions()):
        self.client = client
        self.options = options

    def process(self, source: EntityInput, *, logger: LoggerLike,
                on_token: TokenCallback | None = None) -> EntityOutput:
        tagged, retries = tag_document(self.options, source.filename, source.markdown,
                                       client=self.client, logger=logger, on_token=on_token)
        if read_identities(tagged):
            raise ProcessingValidationError("Extraction must return drafts without identities.")
        tagged = normalize_document(self.options, tagged, client=self.client, logger=logger, on_token=on_token)
        if read_identities(tagged):
            raise ProcessingValidationError("Normalization must preserve drafts without identities.")
        return EntityOutput(markdown=tagged, drafts=parse_drafts(tagged), retries=retries)
