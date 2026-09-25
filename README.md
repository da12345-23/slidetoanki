# Slide2Anki

Turn lecture slides (PDF or PPTX) into an Anki deck. Upload the slides, review and edit the cards in your browser, then download a `.apkg` with the images included.

## How the cards are made

- **Photos included.** Diagrams, charts and photos are attached to the card. Logos, icons and backgrounds are filtered out: images that are too small, that cover under 3% of the slide, or that repeat on half or more of the slides are skipped.
- **One card per labeled diagram.** A diagram with labels becomes a single "Identify the labeled structures" card with every label listed on the back. For PDFs, the front shows the diagram with its labels covered and the back shows the labeled original.
- **No duplicates.** Claude gets the list of questions already written and is told to skip covered facts. The code then drops near-duplicates (normalized text plus RapidFuzz similarity, threshold 85). Removed cards are listed on the review screen, where you can restore them.
- **Slide order.** Cards are sorted by slide and tagged `DeckName::Slide_07` so you can find the source slide in Anki.
- Title, agenda and "Questions?" slides give no cards.

## How it runs

The browser cuts the deck into pieces of 4 slides and sends them to the server one at a time. For each piece the server pulls out the text and images, asks Claude for cards, and returns them. When every piece is done, the server removes duplicates and saves the deck to the **shared library**. Anyone with the password can open, edit and export every deck in the library.

Decks, images and exported `.apkg` files are stored in **Vercel Blob** when the app runs on Vercel, and in `data/store/` when it runs on your computer.

## Setup (on your computer)

Requires Python 3.9+.

```bash
cd slide2anki
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env    # then paste your Anthropic API key into .env
```

## Run (on your computer)

```bash
.venv/bin/uvicorn app.main:app --port 8000
```

Open http://localhost:8000. Locally there's no password unless you add `APP_PASSWORD=...` to `.env`.

Without an API key the app runs in **mock mode**: cards come from a simple offline rule instead of Claude. Mock mode is only for testing the pipeline and never runs on Vercel.

## Deploy on Vercel

1. Import the GitHub repo in Vercel. It's detected as FastAPI automatically; the page in `public/` is served from the CDN.
2. Under **Storage**, create a **Blob** store and connect it to the project. This adds `BLOB_READ_WRITE_TOKEN`.
3. Under **Settings → Environment Variables**, add `ANTHROPIC_API_KEY` and `APP_PASSWORD`.
4. Redeploy.

Until both variables are set, the hosted site refuses every request. It never runs open to everyone or in mock mode.

Uploaded slide images are stored as public Blob files at long random-looking addresses. Only people with the password can see the library that lists them.

## Test

```bash
.venv/bin/python -m pytest tests -s
```

The test builds a 7-slide sample PDF (`tests/make_sample.py`) with a title slide, an agenda, a labeled cell diagram, a summary slide that repeats a fact, a "Questions?" slide, and a logo on every page. It sends the PDF in pieces, the same way the browser does, and checks that:

1. the diagram becomes one card
2. the repeated fact appears once, even when it's in a different piece
3. cards are in slide order
4. the `.apkg` contains the images and every card references them

It also checks the password, the shared library, and saving edits. If `ANTHROPIC_API_KEY` is set, the test calls Claude for real.

## Limits

- If a diagram's labels are part of the picture itself (baked into the pixels), they can't be hidden, so the front of the card will show them.
- PowerPoint files must be under about 4 MB, because they're sent whole with every piece. Bigger decks should be saved as PDF first. PDFs of any size work.
- PPTX slides aren't rendered, so vector shapes and SmartArt drawn directly in PowerPoint aren't captured as images. Exporting to PDF gives better results.
