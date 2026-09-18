# Vision-document understanding

Diagrams, charts, screenshots, and photos are read by a vision-capable local
model and turned into plain text that flows through the normal
chunk/retrieval/citation pipeline — so image content becomes searchable and
citable like any other source.

## Model

The capability router picks the model (`route_plan("vision")`); install one with:

```bash
ollama pull qwen2.5vl:7b      # 6.0 GB, strong at documents/OCR
```

If no vision-capable model is installed, images fail cleanly with a clear
message (`python -m app.diagnostics` reports it as an optional WARN). The vision
model is invoked **without changing the active chat model**
(`RoutingProvider.chat_with` restores it afterwards).

## What it reads

- **Standalone image sources** — `.png .jpg .jpeg .webp .gif .bmp .tiff .tif`
  upload like any other file and become an `image` source.
- **Images embedded in PDFs** — up to 5 images per document that are at least
  256×256 are transcribed/described and attached to their page, so diagrams on
  text pages are captured too. One unreadable image never fails the document.

The prompt asks for a verbatim transcription of all visible text, numbers,
labels, axis values, and units, plus a one-to-two-sentence description, and
forbids inventing content.

## Citations

Image-derived text is chunked and cited by passage. In the UI, a citation opens
a **🖼️ View image** button that shows the original file; images are served from
`/api/media/{source_id}` with the correct content type.

## Scope and limits

- Reads image content only; it does not reason about the image beyond
  transcription/description (grounding stays in the sources).
- Bounded to limit cost: standalone images are one vision call; PDFs are capped
  at five captioned images.
- PDF pages *without* any text layer still use OCR (Tesseract) when available;
  the vision pass adds diagram/image understanding on top.
