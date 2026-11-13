"""Application construction of the external model adapter."""

from checkier.config import load_api_credentials
from infrastructure.llm import HttpModelAdapter
from processing.common.llm import ModelClient


def create_model_client() -> ModelClient:
    base_url, api_key = load_api_credentials()
    return ModelClient(HttpModelAdapter(base_url, api_key))
