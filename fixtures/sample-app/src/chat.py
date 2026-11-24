"""Support chat helper - uses a retiring Claude model and a removed param."""
import anthropic

client = anthropic.Anthropic()

MODEL = "claude-sonnet-4-5"  # retires 2026-11-30 on the Claude API


def reply(prompt: str) -> str:
    msg = client.messages.create(
        model=MODEL,
        max_tokens=512,
        temperature=0.7,  # removed in Anthropic API version 2026-10-21
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text
