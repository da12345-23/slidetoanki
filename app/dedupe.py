"""Drop near-duplicate cards, keeping the one from the earliest slide."""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

from rapidfuzz import fuzz

FRONT_THRESHOLD = 85   # questions this similar may be duplicates...
BACK_THRESHOLD = 60    # ...if their answers agree this much too
NEAR_IDENTICAL = 95    # or if the questions are basically the same text


def normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _is_duplicate(a: Dict, b: Dict) -> bool:
    if a["type"] == "diagram" or b["type"] == "diagram":
        # Diagram prompts all read alike ("Identify the labeled structures"),
        # so compare them by picture and by their label lists instead.
        if a["type"] != b["type"]:
            return False
        if a.get("back_image") and a.get("back_image") == b.get("back_image"):
            return True
        return fuzz.token_sort_ratio(normalize(a["back"]), normalize(b["back"])) >= FRONT_THRESHOLD

    front_a, front_b = normalize(a["front"]), normalize(b["front"])
    if front_a == front_b:
        return True
    front_score = fuzz.token_sort_ratio(front_a, front_b)
    if front_score >= NEAR_IDENTICAL:
        return True
    if front_score >= FRONT_THRESHOLD:
        return fuzz.token_sort_ratio(normalize(a["back"]), normalize(b["back"])) >= BACK_THRESHOLD
    return False


def dedupe(cards: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    """Return (kept, removed). Cards must already be in slide order."""
    kept: List[Dict] = []
    removed: List[Dict] = []
    for card in cards:
        match = next((k for k in kept if _is_duplicate(k, card)), None)
        if match is None:
            kept.append(card)
        else:
            removed.append({**card, "duplicate_of": match["id"]})
    return kept, removed
