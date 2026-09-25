# Slide2Anki

Turn lecture slides (PDF or PPTX) into an Anki deck. Upload the slides, review and edit the cards in your browser, then download a `.apkg` with the images included.

## How the cards are made

- **Photos included.** Diagrams, charts and photos are attached to the card. Logos, icons and backgrounds are filtered out: images that are too small, that cover under 3% of the slide, or that repeat on half or more of the slides are skipped.
- **One card per labeled diagram.** A diagram with labels becomes a single "Identify the labeled structures" card with every label listed on the back. For PDFs, the front shows the diagram with its labels covered and the back shows the labeled original.
- **No duplicates.** Claude gets the list of questions already written and is told to skip covered facts. The code then drops near-duplicates (normalized text plus RapidFuzz similarity, threshold 85). Removed cards are listed on the review screen, where you can restore them.
- **Slide order.** Cards are sorted by slide and tagged `DeckName::Slide_07` so you can find the source slide in Anki.
- Title, agenda and "Questions?" slides give no cards.

## Setup

Requires Python 3.9+.

```bash
cd slide2anki
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env    # then paste your Anthropic API key into .env
```

## Run

```bash
.venv/bin/uvicorn app.main:app --port 8000
```

Open http://localhost:8000.

Without an API key the app runs in **mock mode**: cards come from a simple offline rule instead of Claude. Mock mode is only for testing the pipeline.

## Test

```bash
.venv/bin/python -m pytest tests -s
```

The test builds a 7-slide sample PDF (`tests/make_sample.py`) with a title slide, an agenda, a labeled cell diagram, a summary slide that repeats a fact, a "Questions?" slide, and a logo on every page. It checks that:

1. the diagram becomes one card
2. the repeated fact appears once
3. cards are in slide order
4. the `.apkg` contains the images and every card references them

The finished deck is saved to `tests/output/Lecture3.apkg`. If `ANTHROPIC_API_KEY` is set, the test calls Claude for real.

## Limits

- If a diagram's labels are part of the picture itself (baked into the pixels), they can't be hidden, so the front of the card will show them.
- PPTX slides aren't rendered, so vector shapes and SmartArt drawn directly in PowerPoint aren't captured as images. Exporting the deck to PDF first gives better results.
- Jobs are kept in memory. Restarting the server clears them.
