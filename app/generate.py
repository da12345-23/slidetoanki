"""Turn parsed slides into flashcards with Claude, a few slides at a time, in order."""
from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional

import anthropic

from .parsing import Slide

BATCH_SIZE = 4
MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5")

SYSTEM_PROMPT = """You write Anki flashcards from lecture slides for a student.

Rules:
1. One idea per card. Questions test understanding (why, how, what happens if, compare), not copy-paste of a bullet. Answers are short: a phrase or one or two sentences.
2. Images: when a slide has a diagram, figure, chart or photo that helps, attach it by its exact file name. Use "image_side": "front" when the question is about the image, "back" when the image explains the answer. Ignore decoration.
3. Labeled diagrams: make ONE card for the whole diagram, never one card per label. Front: a prompt like "Identify the labeled structures in this diagram of <subject>." Back: every label as a list, one per line, with a few words of function if the slide gives it. Set "type": "diagram" and "image_side": "front".
4. No duplicates. Skip any fact already covered by the questions listed under "Already covered", and don't repeat a fact within your own answer. Recap and summary slides usually add nothing new.
5. Title slides, agenda/outline slides, and "Questions?"/"Thank you" slides get zero cards.
6. Every card's "slide" must be the number of the slide its fact comes from.

Reply with only a JSON array, no prose. Each item:
{"slide": 7, "front": "...", "back": "...", "image": "s07_fig1.png" or null, "image_side": "front" | "back" | null, "type": "basic" | "diagram"}
Return [] if these slides deserve no cards."""


class GenerationError(Exception):
    pass


def generate_cards(
    slides: List[Slide],
    media_dir: Path,
    on_progress: Callable[[str], None] = lambda msg: None,
) -> List[Dict]:
    use_mock = not os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("SLIDE2ANKI_MOCK") == "1"
    client = None if use_mock else anthropic.Anthropic()

    cards: List[Dict] = []
    for start in range(0, len(slides), BATCH_SIZE):
        batch = slides[start:start + BATCH_SIZE]
        on_progress(f"Writing cards for slides {batch[0].number}–{batch[-1].number} of {len(slides)}")
        existing = [c["front"] for c in cards]
        if use_mock:
            raw = _mock_cards(batch)
        else:
            raw = _ask_claude(client, batch, existing, media_dir)
        cards.extend(_validate(raw, batch))
    return cards


# ---------------------------------------------------------------- Claude

def _build_content(batch: List[Slide], existing: List[str], media_dir: Path) -> List[Dict]:
    covered = "\n".join(f"- {q}" for q in existing[-300:]) or "(none yet)"
    content: List[Dict] = [{"type": "text", "text": f"Already covered:\n{covered}"}]
    for slide in batch:
        content.append({
            "type": "text",
            "text": f"=== Slide {slide.number} ===\n{slide.text or '(no text)'}",
        })
        for fig in slide.figures:
            data = base64.standard_b64encode((media_dir / fig.name).read_bytes()).decode()
            content.append({"type": "text", "text": f"Image on slide {slide.number}: {fig.name}"})
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": data},
            })
    content.append({"type": "text", "text": "Write the cards for these slides as a JSON array."})
    return content


def _ask_claude(client, batch: List[Slide], existing: List[str], media_dir: Path) -> List[Dict]:
    messages = [{"role": "user", "content": _build_content(batch, existing, media_dir)}]
    last_text = ""
    for attempt in range(2):  # one retry if the JSON doesn't parse
        try:
            response = client.beta.messages.create(
                model=MODEL,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                messages=messages,
                thinking={"type": "adaptive"},
                output_config={"effort": "medium"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.AuthenticationError:
            raise GenerationError("Claude rejected the API key. Check ANTHROPIC_API_KEY in .env.")
        except anthropic.RateLimitError:
            raise GenerationError("Hit the Claude rate limit. Wait a minute and try again.")
        except anthropic.APIStatusError as e:
            raise GenerationError(f"Claude API error {e.status_code}: {e.message}")
        except anthropic.APIConnectionError:
            raise GenerationError("Couldn't reach the Claude API. Check your internet connection.")

        if response.stop_reason == "refusal":
            return []
        last_text = "".join(b.text for b in response.content if b.type == "text")
        parsed = _parse_json(last_text)
        if parsed is not None:
            return parsed
        if attempt == 0:
            messages = messages + [
                {"role": "assistant", "content": last_text or "(empty)"},
                {"role": "user", "content": "That wasn't a valid JSON array. Reply with only the JSON array."},
            ]
    raise GenerationError(
        f"Claude's reply for slides {batch[0].number}–{batch[-1].number} wasn't valid JSON after a retry."
    )


def _parse_json(text: str) -> Optional[List[Dict]]:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    for candidate in (text, text[text.find("["):text.rfind("]") + 1]):
        try:
            value = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(value, list):
            return value
    return None


# ---------------------------------------------------------------- validation

def _validate(raw: List[Dict], batch: List[Slide]) -> List[Dict]:
    by_number = {s.number: s for s in batch}
    cards = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            number = int(item.get("slide"))
        except (TypeError, ValueError):
            continue
        slide = by_number.get(number)
        front = str(item.get("front") or "").strip()
        back = str(item.get("back") or "").strip()
        if slide is None or not front or not back:
            continue

        figures = {f.name: f for f in slide.figures}
        image = item.get("image")
        figure = figures.get(image) if isinstance(image, str) else None
        card_type = "diagram" if item.get("type") == "diagram" else "basic"
        side = item.get("image_side") if item.get("image_side") in ("front", "back") else "back"

        front_image = back_image = None
        if figure is not None:
            if card_type == "diagram":
                # Hide the labels on the front when we have a masked copy.
                front_image = figure.masked_name or figure.name
                back_image = figure.name
            elif side == "front":
                front_image = figure.name
            else:
                back_image = figure.name

        cards.append({
            "slide": number,
            "front": front,
            "back": back,
            "type": card_type,
            "front_image": front_image,
            "back_image": back_image,
        })
    return cards


# ---------------------------------------------------------------- offline mock

def _mock_cards(batch: List[Slide]) -> List[Dict]:
    """Deterministic stand-in for Claude, used when no API key is set.

    It only exists so the pipeline (parsing, ordering, dedupe, export) can be
    tested offline. It is deliberately naive: it does NOT skip repeated facts,
    so the code-side dedupe has something to catch.
    """
    cards = []
    for slide in batch:
        lines = [l.strip("•-– ").strip() for l in slide.text.splitlines() if l.strip()]
        title = lines[0] if lines else ""
        lowered = title.lower()
        if slide.number == 1 or any(w in lowered for w in ("agenda", "outline", "questions", "thank")):
            continue
        for fig in slide.figures:
            if fig.labels:
                cards.append({
                    "slide": slide.number, "type": "diagram", "image": fig.name, "image_side": "front",
                    "front": f"Identify the labeled structures in this diagram ({title}).",
                    "back": "\n".join(fig.labels),
                })
        labels = {l for f in slide.figures for l in f.labels}
        for line in lines[1:]:
            if line in labels or len(line.split()) < 4:
                continue
            words = line.rstrip(".").split()
            half = max(2, len(words) // 2)
            cards.append({
                "slide": slide.number, "type": "basic", "image": None, "image_side": None,
                "front": "Complete: " + " ".join(words[:half]) + " ___",
                "back": " ".join(words[half:]),
            })
    return cards
