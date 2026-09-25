"""End-to-end through the API, the way the browser drives it:
create a deck, send it in pieces of 4 slides, finish, export, inspect the .apkg.

Runs against Claude when ANTHROPIC_API_KEY is set, otherwise the offline mock.
    .venv/bin/python -m pytest tests -s
"""
import json
import sqlite3
import zipfile
from pathlib import Path

import fitz
from fastapi.testclient import TestClient

from app.main import app
from tests.make_sample import main as make_sample

client = TestClient(app)
PASSWORD = "test-password"
H = {"X-Password": PASSWORD}


def split_pdf(pdf: Path, size: int):
    """Cut the PDF into pieces the way the browser does."""
    src = fitz.open(pdf)
    for start in range(0, len(src), size):
        piece = fitz.open()
        piece.insert_pdf(src, from_page=start, to_page=min(len(src), start + size) - 1)
        yield start + 1, start + len(piece), piece.tobytes()


def run_deck(tmp_path):
    pdf = make_sample(tmp_path / "Lecture3.pdf")
    total = len(fitz.open(pdf))
    deck_id = client.post("/api/decks", json={"name": "Lecture3", "slide_count": total}, headers=H).json()["id"]

    slides, media, cards, counts = [], {}, [], {}
    for first, last, data in split_pdf(pdf, 4):
        seen = first - 1
        repeated = [h for h, n in counts.items() if seen >= 3 and n / seen >= 0.5]
        res = client.post(
            f"/api/decks/{deck_id}/batch", headers=H,
            files={"file": ("piece.pdf", data, "application/pdf")},
            data={"first_slide": first, "last_slide": last,
                  "existing": json.dumps([c["front"] for c in cards]),
                  "known_repeated": json.dumps(repeated)},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        slides += body["slides"]
        media.update(body["media"])
        for h, n in body["hash_counts"].items():
            counts[h] = counts.get(h, 0) + n
        cards += [{**c, "order": len(cards) + i} for i, c in enumerate(body["cards"])]

    res = client.post(f"/api/decks/{deck_id}/finish", headers=H,
                      json={"slides": slides, "media": media, "cards": cards})
    assert res.status_code == 200, res.text
    return res.json()


def test_password_required(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", PASSWORD)
    assert client.get("/api/decks").status_code == 401
    assert client.get("/api/decks", headers={"X-Password": "nope"}).status_code == 401
    assert client.get("/api/decks", headers=H).status_code == 200


def test_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", PASSWORD)
    deck = run_deck(tmp_path)
    cards = deck["cards"]
    print("\nCards:")
    for c in cards:
        print(f"  slide {c['slide']} [{c['type']}] {c['front']!r} -> {c['back']!r} "
              f"(front img {c['front_image']}, back img {c['back_image']})")
    print(f"Removed as duplicates: {[r['front'] for r in deck['removed']]}")

    # Logo on every slide is filtered (even across pieces); the cell diagram is kept.
    figures = {s["number"]: s["figures"] for s in deck["slides"]}
    assert all(len(f) == 0 for n, f in figures.items() if n != 4), figures
    assert len(figures[4]) == 1

    # Title, agenda and "Questions?" slides give no cards.
    assert not [c for c in cards if c["slide"] in (1, 2, 7)]

    # (a) the labeled diagram is ONE card, with all labels on the back
    diagram_slide = [c for c in cards if c["slide"] == 4]
    assert len(diagram_slide) == 1, diagram_slide
    diagram = diagram_slide[0]
    for label in ("Nucleus", "Mitochondri", "Ribosome", "membrane"):
        assert label.lower() in diagram["back"].lower()
    assert diagram["front_image"] and diagram["front_image"].endswith("_masked.png")

    # (b) the mitochondria/ATP fact appears once (slide 3 and the slide 6 summary are in different pieces)
    atp = [c for c in cards if c["type"] == "basic" and "mitochondri" in (c["front"] + c["back"]).lower()
           and "atp" in (c["front"] + c["back"]).lower()]
    assert len(atp) == 1, atp

    # (c) slide order
    slides = [c["slide"] for c in cards]
    assert slides == sorted(slides)

    # The deck shows up in the shared library.
    library = client.get("/api/decks", headers=H).json()
    assert any(d["id"] == deck["id"] and d["card_count"] == len(cards) for d in library)

    # Edits are saved for everyone.
    edited = [dict(c) for c in cards]
    edited[0]["front"] = "Edited question?"
    assert client.put(f"/api/decks/{deck['id']}", headers=H,
                      json={"name": "Lecture3", "cards": edited, "removed": deck["removed"]}).status_code == 200
    assert client.get(f"/api/decks/{deck['id']}", headers=H).json()["cards"][0]["front"] == "Edited question?"

    # (d) export and check the .apkg has the images wired up
    res = client.post(f"/api/decks/{deck['id']}/export", headers=H, json={"deck_name": "Lecture3", "cards": cards})
    assert res.status_code == 200, res.text
    apkg_bytes = client.get(res.json()["url"]).content
    apkg = tmp_path / "out.apkg"
    apkg.write_bytes(apkg_bytes)
    with zipfile.ZipFile(apkg) as z:
        media_map = json.loads(z.read("media"))
        names = set(media_map.values())
        for key in media_map:
            assert z.read(key)[:8] == b"\x89PNG\r\n\x1a\n"
        z.extract("collection.anki2", tmp_path)
    db = sqlite3.connect(tmp_path / "collection.anki2")
    notes = db.execute("select flds, tags from notes").fetchall()
    assert len(notes) == len(cards)
    referenced = set()
    for flds, tags in notes:
        referenced |= {part.split('"')[0] for part in flds.split('<img src="')[1:]}
        assert "Lecture3::Slide_" in tags
    assert referenced and referenced <= names, (referenced, names)
    print(f"apkg: {len(notes)} notes, media {sorted(names)}")
    Path("tests/output").mkdir(exist_ok=True)
    Path("tests/output/Lecture3.apkg").write_bytes(apkg_bytes)
