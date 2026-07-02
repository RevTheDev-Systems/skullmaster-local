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


def chunk_segments(segments: list[tuple[int | None, str]]) -> list[dict]:
    """Turn (page, text) segments into chunk dicts: {page, seq, text}.

    Paragraphs are packed into chunks up to CHUNK_CHARS; a tail of the previous
    chunk is carried forward as overlap so retrieval doesn't lose boundary context.
    """
    chunks: list[dict] = []
    seq = 0
    for page, text in segments:
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        blocks: list[str] = []
        for p in paragraphs:
            if len(p) > CHUNK_CHARS:
                blocks.extend(_split_long(p, CHUNK_CHARS))
            else:
                blocks.append(p)

        buf = ""
        for block in blocks:
            candidate = f"{buf}\n\n{block}" if buf else block
            if len(candidate) > CHUNK_CHARS and buf:
                chunks.append({"page": page, "seq": seq, "text": buf.strip()})
                seq += 1
                overlap = buf[-CHUNK_OVERLAP:] if CHUNK_OVERLAP else ""
                buf = f"{overlap}\n\n{block}" if overlap else block
            else:
                buf = candidate
        if buf.strip():
            chunks.append({"page": page, "seq": seq, "text": buf.strip()})
            seq += 1
    return chunks
