"""Pull text and teaching images out of a slide deck, one slide at a time, in order.

Each slide becomes a `Slide` with its text and a list of candidate `Figure`s saved
as PNG/JPEG files in the job's media folder. Decorative images (logos, icons,
backgrounds, anything repeated on most slides) are filtered out here so they
never reach Claude or the cards.
"""
from __future__ import annotations

import hashlib
import io
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import fitz  # PyMuPDF
from PIL import Image

# An image must be at least this many pixels on its shorter side...
MIN_PIXELS = 120
# ...and cover at least this share of the slide to count as a figure.
MIN_AREA_FRACTION = 0.03
# An image found on at least this share of slides is decoration (logo, banner).
REPEAT_FRACTION = 0.5
# Largest side, in pixels, of the images we save and send to Claude.
MAX_SIDE = 1600


@dataclass
class Figure:
    name: str                          # file name in the media folder, e.g. s04_fig1.png
    width: int
    height: int
    masked_name: Optional[str] = None  # same figure with its text labels covered
    labels: List[str] = field(default_factory=list)


@dataclass
class Slide:
    number: int
    text: str
    figures: List[Figure] = field(default_factory=list)

    @property
    def figure_names(self) -> List[str]:
        return [f.name for f in self.figures]


def parse_deck(path: Path, media_dir: Path) -> List[Slide]:
    media_dir.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _parse_pdf(path, media_dir)
    if suffix == ".pptx":
        return _parse_pptx(path, media_dir)
    raise ValueError("Upload a .pdf or .pptx file.")


# ---------------------------------------------------------------- helpers

def _save_png(img: Image.Image, dest: Path) -> Image.Image:
    img = img.convert("RGBA") if img.mode in ("P", "LA") else img
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    img.save(dest, "PNG", optimize=True)
    return img


def _repeated_hashes(per_slide_hashes: List[set], n_slides: int) -> set:
    """Hashes that show up on most slides are logos/backgrounds."""
    if n_slides < 3:
        return set()
    counts = Counter(h for hashes in per_slide_hashes for h in hashes)
    return {h for h, c in counts.items() if c / n_slides >= REPEAT_FRACTION}


def _is_label(text: str) -> bool:
    text = text.strip()
    return 0 < len(text) <= 40 and len(text.split()) <= 5


# ---------------------------------------------------------------- PDF

def _parse_pdf(path: Path, media_dir: Path) -> List[Slide]:
    doc = fitz.open(path)

    # First pass: hash every embedded image per page so repeats can be spotted.
    per_page_hashes = []
    for page in doc:
        hashes = set()
        for info in page.get_images(full=True):
            try:
                data = doc.extract_image(info[0])["image"]
            except Exception:
                continue
            hashes.add(hashlib.sha1(data).hexdigest())
        per_page_hashes.append(hashes)
    repeated = _repeated_hashes(per_page_hashes, len(doc))

    slides = []
    for index, page in enumerate(doc):
        number = index + 1
        page_area = page.rect.width * page.rect.height
        spans = _text_spans(page)
        slide = Slide(number=number, text=page.get_text("text").strip())

        seen = set()
        for info in page.get_images(full=True):
            xref = info[0]
            try:
                data = doc.extract_image(xref)
            except Exception:
                continue
            digest = hashlib.sha1(data["image"]).hexdigest()
            if digest in repeated or digest in seen:
                continue
            if min(data["width"], data["height"]) < MIN_PIXELS:
                continue
            rects = page.get_image_rects(xref)
            if not rects:
                continue
            rect = rects[0]
            if rect.width * rect.height / page_area < MIN_AREA_FRACTION:
                continue
            seen.add(digest)
            slide.figures.append(
                _pdf_figure(page, rect, spans, number, len(slide.figures) + 1, media_dir)
            )

        # Vector-drawn diagrams have no embedded image: use the whole slide.
        if not slide.figures and len(page.get_drawings()) >= 15:
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            name = f"s{number:02d}_slide.png"
            img = _save_png(Image.open(io.BytesIO(pix.tobytes("png"))), media_dir / name)
            slide.figures.append(Figure(name=name, width=img.width, height=img.height))

        slides.append(slide)
    return slides


def _text_spans(page):
    spans = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                if span["text"].strip():
                    spans.append((fitz.Rect(span["bbox"]), span["text"].strip(), span["size"]))
    # Titles and headings are set larger than labels; keep only body-sized text.
    if spans:
        sizes = sorted(size for _, _, size in spans)
        cutoff = sizes[len(sizes) // 2] * 1.3
        spans = [(box, text, size) for box, text, size in spans if size <= cutoff]
    return [(box, text) for box, text, _ in spans]


def _pdf_figure(page, rect, spans, number, k, media_dir) -> Figure:
    """Render the figure together with the short labels placed on or around it.

    Also writes a masked copy with those labels covered, so a labeled-diagram
    card can show the unlabeled figure on the front and the labels on the back.
    """
    margin = page.rect.width * 0.12
    zone = fitz.Rect(rect.x0 - margin, rect.y0 - margin, rect.x1 + margin, rect.y1 + margin)
    label_spans = []
    for box, text in spans:
        centre = fitz.Point((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2)
        if _is_label(text) and zone.contains(centre):
            label_spans.append((box, text))

    clip = fitz.Rect(rect)
    for box, _ in label_spans:
        clip |= box
    clip = (clip + (-6, -6, 6, 6)) & page.rect

    zoom = fitz.Matrix(2, 2)
    name = f"s{number:02d}_fig{k}.png"
    pix = page.get_pixmap(matrix=zoom, clip=clip)
    img = _save_png(Image.open(io.BytesIO(pix.tobytes("png"))), media_dir / name)
    figure = Figure(name=name, width=img.width, height=img.height,
                    labels=[t for _, t in label_spans])

    if label_spans:
        masked = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
        from PIL import ImageDraw
        draw = ImageDraw.Draw(masked)
        for box, _ in label_spans:
            x0 = (box.x0 - clip.x0 - 2) * 2
            y0 = (box.y0 - clip.y0 - 2) * 2
            x1 = (box.x1 - clip.x0 + 2) * 2
            y1 = (box.y1 - clip.y0 + 2) * 2
            draw.rectangle([x0, y0, x1, y1], fill=(236, 240, 244), outline=(120, 130, 140), width=2)
        masked_name = f"s{number:02d}_fig{k}_masked.png"
        _save_png(masked, media_dir / masked_name)
        figure.masked_name = masked_name
    return figure


# ---------------------------------------------------------------- PPTX

def _parse_pptx(path: Path, media_dir: Path) -> List[Slide]:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    prs = Presentation(str(path))
    slide_area = prs.slide_width * prs.slide_height

    def walk(shapes):
        for shape in shapes:
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                yield from walk(shape.shapes)
            else:
                yield shape

    # Sort shapes top-to-bottom, left-to-right so text reads in slide order.
    def ordered(shapes):
        return sorted(walk(shapes), key=lambda s: ((s.top or 0), (s.left or 0)))

    per_slide_hashes = []
    for s in prs.slides:
        hashes = set()
        for shape in walk(s.shapes):
            if hasattr(shape, "image"):
                try:
                    hashes.add(hashlib.sha1(shape.image.blob).hexdigest())
                except Exception:
                    pass
        per_slide_hashes.append(hashes)
    repeated = _repeated_hashes(per_slide_hashes, len(prs.slides))

    slides = []
    for index, s in enumerate(prs.slides):
        number = index + 1
        lines, figures, seen = [], [], set()
        for shape in ordered(s.shapes):
            if shape.has_text_frame and shape.text_frame.text.strip():
                lines.append(shape.text_frame.text.strip())
            if getattr(shape, "has_table", False) and shape.has_table:
                for row in shape.table.rows:
                    lines.append(" | ".join(c.text.strip() for c in row.cells))
            if not hasattr(shape, "image"):
                continue
            try:
                blob = shape.image.blob
            except Exception:
                continue
            digest = hashlib.sha1(blob).hexdigest()
            if digest in repeated or digest in seen:
                continue
            if (shape.width or 0) * (shape.height or 0) / slide_area < MIN_AREA_FRACTION:
                continue
            try:
                img = Image.open(io.BytesIO(blob))
                img.load()
            except Exception:
                continue  # EMF/WMF and other formats Pillow can't read
            if min(img.size) < MIN_PIXELS:
                continue
            seen.add(digest)
            name = f"s{number:02d}_fig{len(figures) + 1}.png"
            img = _save_png(img, media_dir / name)
            figures.append(Figure(name=name, width=img.width, height=img.height))

        if s.has_notes_slide and s.notes_slide.notes_text_frame is not None:
            notes = s.notes_slide.notes_text_frame.text.strip()
            if notes:
                lines.append("Speaker notes: " + notes)
        slides.append(Slide(number=number, text="\n".join(lines), figures=figures))
    return slides
