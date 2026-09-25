"""End-to-end: upload the sample PDF, generate cards, export .apkg, inspect it.

Runs against Claude when ANTHROPIC_API_KEY is set, otherwise the offline mock.
    .venv/bin/python -m pytest tests -s
"""
import json
import sqlite3
import time
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from tests.make_sample import main as make_sample

client = TestClient(app)


def run_job(tmp_path):
    pdf = make_sample(tmp_path / "Lecture3.pdf")
    with pdf.open("rb") as f:
        res = client.post("/api/jobs", files={"file": ("Lecture3.pdf", f, "application/pdf")})
    job_id = res.json()["id"]
    for _ in range(600):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] != "running":
            break
        time.sleep(1)
    assert job["status"] == "done", job["message"]
    return job


def test_end_to_end(tmp_path):
    job = run_job(tmp_path)
    cards = job["cards"]
    print("\nCards:")
    for c in cards:
        print(f"  slide {c['slide']} [{c['type']}] {c['front']!r} -> {c['back']!r} "
              f"(front img {c['front_image']}, back img {c['back_image']})")
    print(f"Removed as duplicates: {[r['front'] for r in job['removed']]}")

    # Logo on every slide is filtered; the cell diagram is kept.
    figures = {s["number"]: s["figures"] for s in job["slides"]}
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

    # (b) the mitochondria/ATP fact appears once
    atp = [c for c in cards if c["type"] == "basic" and "mitochondri" in (c["front"] + c["back"]).lower()
           and "atp" in (c["front"] + c["back"]).lower()]
    assert len(atp) == 1, atp

    # (c) slide order
    slides = [c["slide"] for c in cards]
    assert slides == sorted(slides)

    # (d) export and check the .apkg has the images wired up
    res = client.post(f"/api/jobs/{job['id']}/export", json={"deck_name": "Lecture3", "cards": cards})
    assert res.status_code == 200
    apkg = tmp_path / "out.apkg"
    apkg.write_bytes(res.content)
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
    Path("tests/output/Lecture3.apkg").write_bytes(res.content)
