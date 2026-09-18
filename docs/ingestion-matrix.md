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

## Out of scope: OCR

OCR is a **future capability, not a bug fix**, and is never applied by default.
A PDF with no extractable text layer (e.g. a scan) is rejected with a clear
"is it scanned images?" message rather than silently OCR'd. When introduced it
will run only for PDFs whose text layer is insufficient, feeding normalized
blocks into the existing chunk/retrieval pipeline.
