"""FastAPI app: upload a deck, watch progress, review cards, export .apkg."""
from __future__ import annotations

import shutil
import threading
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from fastapi import FastAPI, File, HTTPException, UploadFile  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from .dedupe import dedupe  # noqa: E402
from .export import build_apkg, deck_slug  # noqa: E402
from .generate import GenerationError, generate_cards  # noqa: E402
from .parsing import parse_deck  # noqa: E402

DATA = ROOT / "data" / "jobs"
DATA.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Slide2Anki")
JOBS: Dict[str, Dict] = {}


def _run_job(job_id: str, deck_path: Path) -> None:
    job = JOBS[job_id]
    media_dir = DATA / job_id / "media"
    try:
        job["message"] = "Reading slides"
        slides = parse_deck(deck_path, media_dir)
        job["slide_count"] = len(slides)
        job["slides"] = [{"number": s.number, "figures": s.figure_names} for s in slides]

        def progress(msg: str) -> None:
            job["message"] = msg

        cards = generate_cards(slides, media_dir, progress)
        cards.sort(key=lambda c: c["slide"])  # stable: keeps Claude's order within a slide
        for i, card in enumerate(cards):
            card["id"] = f"c{i}"
            card["order"] = i
        kept, removed = dedupe(cards)
        job.update(status="done", cards=kept, removed=removed, message="Done")
    except (GenerationError, ValueError) as e:
        job.update(status="error", message=str(e))
    except Exception as e:  # keep the server alive and show something useful
        job.update(status="error", message=f"Something went wrong: {e}")


@app.post("/api/jobs")
async def create_job(file: UploadFile = File(...)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".pdf", ".pptx"):
        raise HTTPException(400, "Upload a .pdf or .pptx file.")
    job_id = uuid.uuid4().hex[:12]
    job_dir = DATA / job_id
    job_dir.mkdir(parents=True)
    deck_path = job_dir / f"deck{suffix}"
    with deck_path.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    deck_name = deck_slug(Path(file.filename).stem)
    JOBS[job_id] = {
        "id": job_id, "status": "running", "message": "Uploading",
        "deck_name": deck_name, "slide_count": 0, "slides": [], "cards": [], "removed": [],
    }
    threading.Thread(target=_run_job, args=(job_id, deck_path), daemon=True).start()
    return {"id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "That job no longer exists. Upload the deck again.")
    return job


@app.get("/api/jobs/{job_id}/media/{name}")
def get_media(job_id: str, name: str):
    path = DATA / job_id / "media" / Path(name).name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path)


class Card(BaseModel):
    slide: int
    front: str
    back: str
    type: str = "basic"
    front_image: Optional[str] = None
    back_image: Optional[str] = None
    order: int = 0


class ExportRequest(BaseModel):
    deck_name: str
    cards: List[Card]


@app.post("/api/jobs/{job_id}/export")
def export(job_id: str, req: ExportRequest):
    if job_id not in JOBS:
        raise HTTPException(404, "That job no longer exists. Upload the deck again.")
    if not req.cards:
        raise HTTPException(400, "There are no cards to export.")
    out = DATA / job_id / f"{deck_slug(req.deck_name)}.apkg"
    build_apkg([c.model_dump() for c in req.cards], req.deck_name.strip() or "Slides",
               DATA / job_id / "media", out)
    return FileResponse(out, filename=out.name, media_type="application/octet-stream")


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")
