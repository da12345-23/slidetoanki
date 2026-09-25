"""Generate a small 7-slide lecture PDF for testing.

- Slide 1: title (should give 0 cards)
- Slide 2: agenda (0 cards)
- Slide 3: facts, including "Mitochondria produce ATP..."
- Slide 4: a labeled cell diagram (should give exactly 1 card)
- Slide 5: more facts
- Slide 6: summary that repeats the mitochondria fact (should not duplicate)
- Slide 7: "Questions?" (0 cards)
A small logo sits on every slide and must be filtered out.
"""
import io
import sys
from pathlib import Path

import fitz
from PIL import Image, ImageDraw

W, H = 960, 540


def png(img):
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def logo():
    img = Image.new("RGB", (80, 80), (14, 124, 116))
    ImageDraw.Draw(img).ellipse([15, 15, 65, 65], fill="white")
    return png(img)


def cell():
    img = Image.new("RGB", (500, 380), "white")
    d = ImageDraw.Draw(img)
    d.ellipse([20, 20, 480, 360], fill=(250, 226, 214), outline=(150, 60, 50), width=6)   # membrane
    d.ellipse([190, 130, 310, 240], fill=(160, 120, 200), outline=(90, 50, 130), width=4) # nucleus
    d.ellipse([90, 90, 170, 130], fill=(240, 150, 80), outline=(160, 80, 30), width=3)    # mitochondrion
    for x, y in [(360, 120), (380, 150), (350, 170), (390, 200)]:
        d.ellipse([x, y, x + 12, y + 12], fill=(40, 90, 160))                             # ribosomes
    return png(img)


def add_text(page, x, y, text, size=22, bold=False):
    page.insert_text((x, y), text, fontsize=size, fontname="helv" if not bold else "hebo")


def main(out: Path):
    doc = fitz.open()
    logo_png, cell_png = logo(), cell()

    def new_slide(title):
        page = doc.new_page(width=W, height=H)
        page.insert_image(fitz.Rect(W - 60, 10, W - 20, 50), stream=logo_png)
        add_text(page, 50, 70, title, size=34, bold=True)
        return page

    p = new_slide("Lecture 3: The Cell")
    add_text(p, 50, 130, "BIOL 101 - Dr. Rahman")

    p = new_slide("Agenda")
    for i, line in enumerate(["Organelles", "The cell diagram", "Summary"]):
        add_text(p, 70, 140 + i * 40, "- " + line)

    p = new_slide("Organelles and energy")
    for i, line in enumerate([
        "Mitochondria produce ATP through oxidative phosphorylation.",
        "Ribosomes translate messenger RNA into protein chains.",
        "The nucleus stores DNA and controls gene expression.",
    ]):
        add_text(p, 50, 140 + i * 45, line, size=20)

    p = new_slide("Parts of an animal cell")
    img_rect = fitz.Rect(230, 110, 730, 490)
    p.insert_image(img_rect, stream=cell_png)
    labels = [  # label, text position, arrow target (in image coordinates)
        ("Cell membrane", (60, 150), (250, 200)),
        ("Nucleus", (760, 290), (480, 290)),
        ("Mitochondrion", (60, 250), (330, 220)),
        ("Ribosome", (760, 200), (600, 250)),
    ]
    for text, (tx, ty), target in labels:
        add_text(p, tx, ty, text, size=18)
        start = (tx + (130 if tx < 400 else -8), ty - 6)
        p.draw_line(start, target, color=(0.2, 0.2, 0.2), width=1.2)

    p = new_slide("Membranes and transport")
    for i, line in enumerate([
        "The cell membrane is a selectively permeable phospholipid bilayer.",
        "Active transport moves ions against their gradient using ATP.",
    ]):
        add_text(p, 50, 140 + i * 45, line, size=20)

    p = new_slide("Summary")
    for i, line in enumerate([
        "Mitochondria produce ATP through oxidative phosphorylation.",
        "Membranes control what enters and leaves the cell.",
    ]):
        add_text(p, 50, 140 + i * 45, "- " + line, size=20)

    p = new_slide("Questions?")

    doc.save(out)
    return out


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "tests/sample_lecture.pdf")
    print(main(target))
