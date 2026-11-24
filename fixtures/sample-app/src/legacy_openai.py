"""Legacy summarizer - still pinned to models OpenAI has retired or dated."""
from openai import OpenAI

client = OpenAI()

PRIMARY = "gpt-4"  # retired 2026-10-23
SNAPSHOT = "gpt-4o-2024-08-06"  # dated snapshot, retires 2027-01-14


def summarize(text: str) -> str:
    resp = client.chat.completions.create(
        model=PRIMARY,
        messages=[{"role": "user", "content": f"Summarize: {text}"}],
    )
    return resp.choices[0].message.content
