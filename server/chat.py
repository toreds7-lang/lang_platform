import os
from openai import OpenAI

from dictionary import _SIMPLE_ENGLISH_RULE

_client = None


class ChatError(Exception):
    """The chat call failed, or came back empty."""


# gpt-4o-mini's window is 128k. Staying well under it leaves room for the
# conversation, the reply, and the slack in the estimator below.
MAX_CONTEXT_TOKENS = 90_000

# Deliberately crude: tokenizing the transcript to count it exactly would cost
# more than the guess is worth, and the only decision it drives is where to cut.
CHARS_PER_TOKEN = 4

# The transcript dwarfs the conversation, so history is capped for tidiness
# rather than for cost: twelve turns is far more context than a follow-up needs.
MAX_HISTORY_TURNS = 12


def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


def _stamp(seconds: float) -> str:
    """[mm:ss], with minutes running past 59 on long videos rather than h:mm:ss.

    Keeping it two-part means the client needs one regex, not two.
    """
    total = int(seconds)
    return f"[{total // 60:02d}:{total % 60:02d}]"


def build_transcript_block(sentences: list[dict]) -> tuple[str, float, bool]:
    """The transcript as timestamped lines, cut to fit the context window.

    Returns the block, the timestamp coverage ends at, and whether anything was
    dropped -- the caller surfaces that last flag, because a summary of two
    thirds of a video should never be presented as a summary of the whole one.
    """
    budget = MAX_CONTEXT_TOKENS * CHARS_PER_TOKEN
    lines: list[str] = []
    used = 0
    covered_until = 0.0
    truncated = False

    for sentence in sentences:
        text = (sentence.get("text") or "").strip()
        if not text:
            continue
        line = f"{_stamp(sentence.get('start', 0))} {text}"
        if used + len(line) + 1 > budget:
            truncated = True
            break
        lines.append(line)
        used += len(line) + 1
        covered_until = sentence.get("end", sentence.get("start", 0))

    return "\n".join(lines), covered_until, truncated


def _system_prompt(transcript_block: str) -> str:
    """Byte-identical across turns for a given video.

    That matters: OpenAI caches long identical prefixes automatically, so
    holding this stable discounts the repeated transcript at no cost in code.
    """
    return (
        "You are helping an English learner who is shadow-practicing a YouTube video. "
        "Answer questions about the video using ONLY the transcript below. If the "
        "transcript does not contain the answer, say so plainly instead of guessing.\n\n"
        f"{_SIMPLE_ENGLISH_RULE}\n\n"
        "Always answer in English, whatever language the question is in.\n\n"
        "When you refer to a moment in the video, cite it as [mm:ss] using the "
        "timestamps in the transcript. Only cite timestamps that actually appear "
        "there.\n\n"
        "Use short markdown: **bold**, bullet lists, numbered lists, and ### headings. "
        "Do not use tables or links.\n\n"
        "When a diagram genuinely helps -- a sequence of steps, a branching argument -- "
        "emit a fenced ```mermaid block using `flowchart TD` syntax. Keep node labels "
        "under six words, and wrap any label containing punctuation in double quotes. "
        "Do not draw a diagram when prose would be clearer.\n\n"
        f"TRANSCRIPT:\n{transcript_block}"
    )


def answer(sentences: list[dict], messages: list[dict]) -> dict:
    transcript_block, covered_until, truncated = build_transcript_block(sentences)

    history = [
        {"role": m["role"], "content": m["content"]}
        for m in messages[-MAX_HISTORY_TURNS:]
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]
    if not history:
        raise ChatError("There was no question to answer.")

    system = _system_prompt(transcript_block)
    model = os.environ.get("LLM_MODEL", "gpt-4o-mini")

    try:
        resp = _get_client().chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system}] + history,
            temperature=0.4,
            max_tokens=1200,
        )
        reply = (resp.choices[0].message.content or "").strip()
    except KeyError as exc:
        raise ChatError("OPENAI_API_KEY is not set. Add it to the .env file.") from exc
    except Exception as exc:
        raise ChatError(f"The chat service failed: {exc}") from exc

    if not reply:
        raise ChatError("The model returned an empty answer. Try again.")

    return {
        "reply": reply,
        "truncated": truncated,
        "covered_until": covered_until,
        "context_tokens": len(system) // CHARS_PER_TOKEN,
    }
