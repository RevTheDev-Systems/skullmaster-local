# Slides and Video Overviews

Two pipelines kept deliberately separate from `studio.py`.

## Slides (`app/slides.py`)

```
notebook chunks ──▶ grounded deck prompt ──▶ validate (3–15 slides) ──▶ UI navigator / Markdown
```

- `generate_deck()` produces `{title, slides: [{title, bullets, notes}]}` from the
  notebook's sources (cite-or-refuse, one retry on malformed output), using the
  shared grounded-generation helpers and the untrusted-source rule.
- `render_slide_png()` rasterises any slide to a PNG with **PyMuPDF** (no Pillow
  or other imaging dependency); `deck_markdown()` exports the deck.
- UI: `🖼️ Slides` in Studio opens a navigator (◀ ▶ and arrow keys) with a
  Markdown download. Persisted as an artifact of kind `slides`.

## Video Overview (`app/video.py`)

```
deck ──▶ render each slide to PNG ──▶ voice the notes with local TTS ──▶ ffmpeg mux ──▶ MP4
```

- Each slide is rendered to an image and its `notes` (or bullets) are spoken by
  the configured TTS provider; each still is shown for its narration length plus
  a short pause, and the narration track is padded to match, so audio and slides
  stay in sync.
- `ffmpeg` muxes the image sequence and narration into an H.264/AAC MP4 stored in
  `data/artifacts/` and served with `video/mp4`. **ffmpeg must be on PATH**;
  otherwise the endpoint returns 422 with a clear message.
- UI: `🎬 Video Overview` generates the MP4 and plays it inline with a download
  link. Persisted as an artifact of kind `video`.

## Notes

- Both are grounded: generation refuses when the notebook lacks usable material,
  and malformed model output is retried once.
- Rendering is deterministic and local; the video pipeline needs no model beyond
  the existing chat LLM and TTS.
