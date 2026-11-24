"""Embeddings helper - model is not in the POC seed registry (unknown)."""
from openai import OpenAI

client = OpenAI()
EMBED_MODEL = "text-embedding-3-small"


def embed(text: str):
    return client.embeddings.create(model=EMBED_MODEL, input=text).data[0].embedding
