"""FastAPI app. Runs locally with uvicorn and on Vercel as one function.

Every request is short and stateless so it fits Vercel's limits:
the browser cuts the deck into pieces of a few slides and sends them one at a
time; decks, images and exports live in shared storage (see storage.py).
"""
from __future__ import annotations

import hmac
import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, RedirectResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from .dedupe import dedupe  # noqa: E402
from .export import build_apkg, deck_slug  # noqa: E402
from .generate import GenerationError, generate_cards  # noqa: E402
from .parsing import parse_deck  # noqa: E402
from .storage import LOCAL_DIR, LocalStore, get_store  # noqa: E402

app = FastAPI(title="Slide2Anki")


# ---------------------------------------------------------------- password

ON_VERCEL = bool(os.environ.get("VERCEL"))


def check_password(x_password: Optional[str] = Header(default=None)) -> None:
    expected = os.environ.get("APP_PASSWORD", "")
    if ON_VERCEL and not (expected and os.environ.get("ANTHROPIC_API_KEY")):
        # Never run the hosted site open to everyone or on the offline mock.
        raise HTTPException(503, "This site isn't set up yet: APP_PASSWORD and ANTHROPIC_API_KEY need to be added in Vercel.")
    if expected and not hmac.compare_digest((x_password or "").encode(), expected.encode()):
        raise HTTPException(401, "Wrong password.")


@app.get("/api/config")
def config():
    return {"password_required": bool(os.environ.get("APP_PASSWORD")) or ON_VERCEL}


@app.post("/api/login", dependencies=[Depends(check_password)])
def login():
    return {"ok": True}


# ---------------------------------------------------------------- decks

def _deck_path(deck_id: str) -> str:
    if not deck_id.isalnum():
        raise HTTPException(404, "Deck not found.")
    return f"decks/{deck_id}.json"


def _load(deck_id: str) -> Dict:
    try:
        return json.loads(get_store().get(_deck_path(deck_id)))
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(404, "That deck doesn't exist anymore.")


def _save(deck: Dict) -> None:
    deck["updated"] = time.time()
    get_store().put(_deck_path(deck["id"]), json.dumps(deck).encode(), "application/json")


class NewDeck(BaseModel):
    name: str
    filename: str = ""
    slide_count: int


@app.post("/api/decks", dependencies=[Depends(check_password)])
def create_deck(req: NewDeck):
    deck = {
        "id": uuid.uuid4().hex[:12], "name": req.name.strip() or "Slides", "filename": req.filename,
        "created": time.time(), "slide_count": req.slide_count, "status": "generating",
        "slides": [], "media": {}, "cards": [], "removed": [],
    }
    _save(deck)
    return {"id": deck["id"]}


@app.post("/api/decks/{deck_id}/batch", dependencies=[Depends(check_password)])
def process_batch(
    deck_id: str,
    file: UploadFile = File(...),
    first_slide: int = Form(...),
    last_slide: int = Form(...),
    existing: str = Form("[]"),
    known_repeated: str = Form("[]"),
):
    """Parse one piece of the deck and write its cards.

    For a PDF the browser sends just these pages; for a PPTX it sends the whole
    file and we pick slides first_slide..last_slide.
    """
    _deck_path(deck_id)
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".pdf", ".pptx"):
        raise HTTPException(400, "Upload a .pdf or .pptx file.")
    store = get_store()
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"piece{suffix}"
        src.write_bytes(file.file.read())
        media_dir = Path(tmp) / "media"
        try:
            slides, hash_counts = parse_deck(
                src, media_dir,
                first_number=first_slide,
                pages=range(first_slide, last_slide + 1) if suffix == ".pptx" else None,
                known_repeated=json.loads(known_repeated),
            )
        except Exception:
            raise HTTPException(400, "Couldn't read these slides. If it's a PowerPoint, try saving it as PDF first.")

        media = {}
        for f in media_dir.iterdir():
            media[f.name] = store.put(f"decks/{deck_id}/media/{f.name}", f.read_bytes(), "image/png")

        try:
            cards = generate_cards(slides, media_dir, existing=json.loads(existing))
        except GenerationError as e:
            raise HTTPException(502, str(e))

    return {
        "slides": [{"number": s.number, "figures": s.figure_names} for s in slides],
        "media": media,
        "cards": cards,
        "hash_counts": hash_counts,
    }


class Card(BaseModel):
    id: Optional[str] = None
    slide: int
    front: str
    back: str
    type: str = "basic"
    front_image: Optional[str] = None
    back_image: Optional[str] = None
    order: int = 0
    duplicate_of: Optional[str] = None


class FinishDeck(BaseModel):
    slides: List[Dict]
    media: Dict[str, str]
    cards: List[Card]


@app.post("/api/decks/{deck_id}/finish", dependencies=[Depends(check_password)])
def finish_deck(deck_id: str, req: FinishDeck):
    """Sort, remove duplicates, and save the finished deck to the library."""
    deck = _load(deck_id)
    cards = sorted((c.model_dump() for c in req.cards), key=lambda c: (c["slide"], c["order"]))
    for i, card in enumerate(cards):
        card["id"] = f"c{i}"
        card["order"] = i
    kept, removed = dedupe(cards)
    deck.update(status="done", slides=sorted(req.slides, key=lambda s: s["number"]),
                media=req.media, cards=kept, removed=removed)
    _save(deck)
    return deck


@app.get("/api/decks", dependencies=[Depends(check_password)])
def list_decks():
    store = get_store()
    summaries = []
    for item in sorted(store.list("decks/"), key=lambda i: -i["uploaded_at"])[:100]:
        if not item["path"].endswith(".json"):
            continue
        try:
            deck = json.loads(store.get(item["path"]))
        except Exception:
            continue
        if deck.get("status") != "done":
            continue
        summaries.append({
            "id": deck["id"], "name": deck["name"], "filename": deck.get("filename", ""),
            "created": deck["created"], "updated": deck.get("updated", deck["created"]),
            "slide_count": deck["slide_count"], "card_count": len(deck["cards"]),
        })
    summaries.sort(key=lambda d: -d["updated"])
    return summaries


@app.get("/api/decks/{deck_id}", dependencies=[Depends(check_password)])
def get_deck(deck_id: str):
    return _load(deck_id)


class SaveDeck(BaseModel):
    name: str
    cards: List[Card]
    removed: List[Card] = []


@app.put("/api/decks/{deck_id}", dependencies=[Depends(check_password)])
def save_deck(deck_id: str, req: SaveDeck):
    deck = _load(deck_id)
    deck.update(name=req.name.strip() or deck["name"],
                cards=[c.model_dump() for c in req.cards],
                removed=[c.model_dump() for c in req.removed])
    _save(deck)
    return {"ok": True, "updated": deck["updated"]}


class ExportRequest(BaseModel):
    deck_name: str
    cards: List[Card]


@app.post("/api/decks/{deck_id}/export", dependencies=[Depends(check_password)])
def export(deck_id: str, req: ExportRequest):
    _deck_path(deck_id)
    if not req.cards:
        raise HTTPException(400, "There are no cards to export.")
    store = get_store()
    name = req.deck_name.strip() or "Slides"
    with tempfile.TemporaryDirectory() as tmp:
        media_dir = Path(tmp) / "media"
        media_dir.mkdir()
        for card in req.cards:
            for image in (card.front_image, card.back_image):
                if image and not (media_dir / image).exists():
                    try:
                        (media_dir / Path(image).name).write_bytes(
                            store.get(f"decks/{deck_id}/media/{Path(image).name}"))
                    except Exception:
                        pass  # a missing image just leaves the card without it
        out = Path(tmp) / f"{deck_slug(name)}.apkg"
        build_apkg([c.model_dump() for c in req.cards], name, media_dir, out)
        url = store.put(f"exports/{deck_id}/{out.name}", out.read_bytes(), "application/octet-stream")
    return {"url": url, "filename": out.name}


# ---------------------------------------------------------------- local-only files

@app.get("/store/{path:path}")
def local_store(path: str):
    if not isinstance(get_store(), LocalStore):
        raise HTTPException(404)
    target = (LOCAL_DIR / path).resolve()
    if LOCAL_DIR.resolve() not in target.parents or not target.exists():
        raise HTTPException(404)
    return FileResponse(target, filename=target.name if target.suffix == ".apkg" else None)


@app.get("/")
def index():
    page = ROOT / "public" / "index.html"
    if page.exists():
        return FileResponse(page)
    return RedirectResponse("/index.html")
