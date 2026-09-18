"""Structure-aware chunking: split on paragraph boundaries, keep page metadata."""

from .config import CHUNK_CHARS, CHUNK_OVERLAP


def _split_long(text: str, limit: int) -> list[str]:
    """Split an over-long block on sentence-ish boundaries, hard-wrap as last resort."""
    out, buf = [], ""
    for sentence in text.replace("\n", " ").split(". "):
        candidate = f"{buf}. {sentence}" if buf else sentence
        if len(candidate) > limit and buf:
            out.append(buf.strip())
            buf = sentence
        else:
            buf = candidate
    if buf.strip():
        out.append(buf.strip())
    # hard wrap anything still too long (no sentence boundaries)
    final = []
    for piece in out:
        while len(piece) > limit:
            final.append(piece[:limit])
            piece = piece[limit:]
        if piece:
            final.append(piece)
    return final


def chunk_segments(
    segments: list[tuple[int | None, str]],
    *,
    chunk_chars: int = CHUNK_CHARS,
    overlap: int = CHUNK_OVERLAP,
) -> list[dict]:
    """Turn (page, text) segments into chunk dicts: {page, seq, text}.

    Paragraphs are packed into chunks up to `chunk_chars`; a tail of the previous
    chunk is carried forward as `overlap` so retrieval doesn't lose boundary
    context. Both default to the configured values and are injectable so the
    evaluation harness can sweep them.
    """
    chunks: list[dict] = []
    seq = 0
    for page, text in segments:
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        blocks: list[str] = []
        for p in paragraphs:
            if len(p) > chunk_chars:
                blocks.extend(_split_long(p, chunk_chars))
            else:
                blocks.append(p)

        buf = ""
        for block in blocks:
            candidate = f"{buf}\n\n{block}" if buf else block
            if len(candidate) > chunk_chars and buf:
                chunks.append({"page": page, "seq": seq, "text": buf.strip()})
                seq += 1
                tail = buf[-overlap:] if overlap else ""
                buf = f"{tail}\n\n{block}" if tail else block
            else:
                buf = candidate
        if buf.strip():
            chunks.append({"page": page, "seq": seq, "text": buf.strip()})
            seq += 1
    return chunks
