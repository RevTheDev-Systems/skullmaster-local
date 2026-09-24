# Ingestion matrix

The supported-source contract, mirrored by `INGESTION_MATRIX` in
`app/ingest.py` and asserted by `tests/test_ingest.py` so code and docs cannot
drift. Every row has both a positive and a negative fixture.

| Source            | Parse | Metadata         | Citation | Failure / retry |
|-------------------|-------|------------------|----------|-----------------|
| PDF (`.pdf`)      | Yes   | page number      | page     | Yes             |
| DOCX (`.docx`)    | Yes   | heading structure| passage  | Yes             |
| XLSX/XLSM         | Yes   | sheet / row      | passage  | Yes             |
| TXT/MD/RST/CSV/TSV/JSON | Yes | none        | passage  | Yes             |
| HTML (`.html/.htm`)| Yes  | heading structure| passage  | Yes             |
| URL (http/https)  | Yes   | origin URL       | passage  | Yes             |
| YouTube URL       | Yes   | origin URL + timestamp | seek | Yes           |
| Audio             | Yes   | timestamp        | seek     | Yes             |
| Video             | Yes   | timestamp        | seek     | Yes             |
| Image             | Yes   | vision text      | passage  | Yes             |

## YouTube videos

A YouTube watch page is a JavaScript app shell: scraping it yields only the
site chrome ("About · Press · Copyright…") — never the video. So YouTube URLs
take a **caption-track** route instead (`app/ingest.py: parse_youtube`):

- The 11-character video id is recognised from every common form
  (`watch?v=`, `youtu.be/`, `/shorts/`, `/embed/`, `/live/`, `m.`/`music.`).
- The transcript is pulled with `youtube-transcript-api` (manual **and**
  auto-generated captions) and grouped into ~45-second / ~900-character blocks,
  each carrying its **start second** — so citations are timestamped and the
  citation's play action opens the video at the cited moment.
- The title comes from YouTube's oEmbed endpoint (no API key, no scraping).
- **Captions disabled** → the **audio fallback** runs: `yt-dlp` downloads just
  the audio track and the same local Whisper pipeline used for uploaded media
  transcribes it, producing timestamped blocks. The temporary download is
  deleted immediately; only the transcript is kept.
  - Controlled by `YOUTUBE_TRANSCRIBE_FALLBACK` (`auto` = only when `yt-dlp` is
    installed, `true` = insist, `false` = never download). With it disabled, a
    captions-free video returns a clear `IngestError` (HTTP 422).
  - **Bounded before download:** the video's duration is checked first, and a
    video longer than `YOUTUBE_MAX_MINUTES` (default 120) is refused — no bytes
    are fetched. Playlists are never expanded (`noplaylist`), so a playlist link
    cannot pull gigabytes.
- **Private / region-blocked** → a clear `IngestError` (HTTP 422).
- A video with no captions is *not* silently ingested as boilerplate.

## Policies

- **Single fetch.** A URL is fetched exactly once, when submitted. Only
  `http`/`https` are allowed (no `file://`), with a hard timeout and a 10 MB
  size cap. Nothing is re-fetched at runtime.
- **Fail closed.** Any parse failure raises `IngestError`, which the API maps
  to HTTP `422` — corrupt/empty/unsupported inputs never 500.
- **No boilerplate as a source.** The general URL path refuses any page whose
  readable extraction is under 200 characters (`MIN_ARTICLE_CHARS`). A
  JavaScript-rendered page, a video site, or a sign-in wall yields only
  navigation/footer chrome, and ingesting that would let the model "cite" junk.
  It fails honestly instead.
- **Retry.** A source that failed at the embedding/indexing step is stored as
  `status=failed` and can be re-indexed from the original file/URL
  (`POST /api/notebooks/{id}/sources/{sid}/retry`).
- **Deduplication.** Content is hashed; re-uploading identical material into a
  notebook is rejected with HTTP `409`.
- **Unicode.** Files are decoded UTF-8 with replacement, so non-UTF-8 bytes do
  not abort ingestion; Unicode content and filenames are preserved.
- **Media.** Transcript segments carry the start second in the page slot so
  citations can seek playback.

## Optional: OCR for scanned PDFs

OCR is an **off-by-default capability**, not a general PDF path:

- It runs only for **individual pages with no text layer**, and only when a PDF's
  average extractable text is below `OCR_MIN_CHARS_PER_PAGE` (default 40). Text
  pages are never re-OCR'd.
- It requires an engine: Tesseract via `pytesseract` (+ Pillow). Install with
  `brew install tesseract` and `uv pip install pytesseract pillow`. Without an
  engine, scanned PDFs are still rejected with a clear "is it scanned images?"
  message, exactly as before.
- Controls: `OCR_ENABLED=auto|true|false`, `OCR_LANG`, `OCR_MIN_CHARS_PER_PAGE`,
  `OCR_DPI`. `python -m app.diagnostics` reports OCR availability (WARN when no
  engine is installed).
- OCR text is normalized into pages and fed through the existing chunk/retrieval
  pipeline, so citations keep their page numbers.

## Optional: vision for diagrams/charts/images

Standalone image uploads and images embedded in PDFs are read by a
vision-capable model and turned into text (see `docs/vision.md`). It is opt-in in
the same way: it needs a vision model installed, it is bounded (one call per
image; up to five PDF images), and one unreadable image never fails the document.
