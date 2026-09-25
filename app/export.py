"""Build a ready-to-import .apkg with genanki, images packed in as media."""
from __future__ import annotations

import html
import re
import zlib
from pathlib import Path
from typing import Dict, List

import genanki

CSS = """
.card { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; font-size: 20px;
        line-height: 1.45; color: #1b2430; background: #fff; text-align: left;
        max-width: 720px; margin: 0 auto; padding: 8px; }
.nightMode .card, .night_mode .card { color: #e6e9ee; background: #1d2127; }
img { max-width: 100%; max-height: 60vh; display: block; margin: 12px auto; border-radius: 6px; }
hr#answer { margin: 18px 0; }
.source { margin-top: 20px; font-size: 12px; color: #7a8591; }
ul { padding-left: 1.2em; margin: 0; }
"""


def deck_slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
    return slug or "Deck"


def _stable_id(text: str) -> int:
    return 1_000_000_000 + zlib.crc32(text.encode()) % 1_000_000_000


def _model() -> genanki.Model:
    return genanki.Model(
        _stable_id("slide2anki-model-v1"),
        "Slide2Anki Basic",
        fields=[{"name": "Front"}, {"name": "Back"}, {"name": "Source"}],
        templates=[{
            "name": "Card 1",
            "qfmt": "{{Front}}",
            "afmt": '{{FrontSide}}<hr id="answer">{{Back}}<div class="source">{{Source}}</div>',
        }],
        css=CSS,
    )


def _text_to_html(text: str) -> str:
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    if len(lines) > 1:
        items = "".join(f"<li>{html.escape(l.lstrip('•-– '))}</li>" for l in lines)
        return f"<ul>{items}</ul>"
    return html.escape(text.strip())


def build_apkg(cards: List[Dict], deck_name: str, media_dir: Path, out_path: Path) -> Path:
    slug = deck_slug(deck_name)
    deck = genanki.Deck(_stable_id("slide2anki-deck-" + deck_name), deck_name)
    model = _model()
    media_files: Dict[str, Path] = {}

    def image_tag(name: str) -> str:
        if not name:
            return ""
        src = media_dir / Path(name).name
        if not src.exists():
            return ""
        # Prefix with the deck name so files never collide in Anki's shared media folder.
        packed = f"{slug}_{src.name}"
        if packed not in media_files:
            dest = out_path.parent / "packed_media" / packed
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(src.read_bytes())
            media_files[packed] = dest
        return f'<img src="{packed}">'

    ordered = sorted(cards, key=lambda c: (int(c["slide"]), c.get("order", 0)))
    for card in ordered:
        slide_no = int(card["slide"])
        front = _text_to_html(card["front"]) + image_tag(card.get("front_image"))
        back = _text_to_html(card["back"]) + image_tag(card.get("back_image"))
        source = f"{html.escape(deck_name)} · Slide {slide_no}"
        deck.add_note(genanki.Note(
            model=model,
            fields=[front, back, source],
            tags=[f"{slug}::Slide_{slide_no:02d}"],
            # Deterministic id so re-importing an edited deck updates notes instead of doubling them.
            guid=genanki.guid_for(slug, slide_no, card["front"]),
        ))

    package = genanki.Package(deck)
    package.media_files = [str(p) for p in media_files.values()]
    package.write_to_file(str(out_path))
    return out_path
