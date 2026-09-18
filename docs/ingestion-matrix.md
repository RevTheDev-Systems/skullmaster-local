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
| Audio             | Yes   | timestamp        | seek     | Yes             |
| Video             | Yes   | timestamp        | seek     | Yes             |

## Policies

- **Single fetch.** A URL is fetched exactly once, when submitted. Only
  `http`/`https` are allowed (no `file://`), with a hard timeout and a 10 MB
  size cap. Nothing is re-fetched at runtime.
- **Fail closed.** Any parse failure raises `IngestError`, which the API maps
  to HTTP `422` — corrupt/empty/unsupported inputs never 500.
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
