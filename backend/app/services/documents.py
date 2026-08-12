"""Document intelligence.

Upload a file, get answers from it. Text is extracted, split into overlapping
chunks, embedded, and retrieved by meaning — the same retrieval path as memory,
so there's one implementation to reason about rather than two.

Chunking is paragraph-aware rather than fixed-width: splitting mid-sentence
produces chunks that retrieve badly, and the cost of respecting paragraph
boundaries is a few lines.
"""

from __future__ import annotations

import io
import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Document, DocumentChunk
from app.services.memory import _cosine, _lexical, embed_text

log = logging.getLogger("aura.documents")

CHUNK_TARGET = 1100      # characters
CHUNK_OVERLAP = 180
MAX_CHUNKS = 400


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def extract_text(filename: str, data: bytes, mime: str = "") -> tuple[str, str]:
    """Return (text, resolved_mime). Raises ValueError on unsupported types."""
    lower = filename.lower()

    if lower.endswith(".pdf") or mime == "application/pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ValueError(
                "PDF support needs pypdf — run `pip install pypdf`."
            ) from exc
        reader = PdfReader(io.BytesIO(data))
        pages = [(page.extract_text() or "") for page in reader.pages]
        return "\n\n".join(pages), "application/pdf"

    if lower.endswith(".docx"):
        try:
            import zipfile
            from xml.etree import ElementTree
        except ImportError as exc:  # pragma: no cover
            raise ValueError("Could not read .docx") from exc
        # A .docx is a zip; the body lives in word/document.xml. Pulling the
        # text nodes out directly avoids a heavyweight dependency.
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("word/document.xml")
        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        tree = ElementTree.fromstring(xml)
        paragraphs = [
            "".join(node.text or "" for node in para.iter(f"{ns}t"))
            for para in tree.iter(f"{ns}p")
        ]
        return "\n\n".join(p for p in paragraphs if p.strip()), (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )

    for suffix, resolved in (
        (".md", "text/markdown"),
        (".txt", "text/plain"),
        (".csv", "text/csv"),
        (".tsv", "text/tab-separated-values"),
        (".json", "application/json"),
        (".log", "text/plain"),
        (".html", "text/html"),
    ):
        if lower.endswith(suffix):
            return data.decode("utf-8", errors="replace"), resolved

    # Last resort: if it decodes cleanly as text, treat it as text.
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"Can't read {filename}. Supported: pdf, docx, txt, md, csv, tsv, json, html."
        ) from exc
    return text, mime or "text/plain"


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def chunk_text(text: str) -> list[str]:
    """Split on paragraph boundaries, packing up to the target size."""
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    if not text:
        return []

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        # A single oversized paragraph gets split on sentence boundaries.
        if len(para) > CHUNK_TARGET:
            if current:
                chunks.append(current)
                current = ""
            for sentence in re.split(r"(?<=[.!?])\s+", para):
                if len(current) + len(sentence) + 1 > CHUNK_TARGET and current:
                    chunks.append(current)
                    current = current[-CHUNK_OVERLAP:] if CHUNK_OVERLAP else ""
                current = f"{current} {sentence}".strip()
            continue

        if len(current) + len(para) + 2 > CHUNK_TARGET and current:
            chunks.append(current)
            current = current[-CHUNK_OVERLAP:] if CHUNK_OVERLAP else ""
        current = f"{current}\n\n{para}".strip()

    if current:
        chunks.append(current)
    return chunks[:MAX_CHUNKS]


# ---------------------------------------------------------------------------
# Ingest + retrieval
# ---------------------------------------------------------------------------


def ingest(
    db: Session,
    user_id: str,
    title: str,
    text: str,
    mime: str = "text/plain",
    size_bytes: int = 0,
    source: str = "upload",
) -> Document:
    document = Document(
        user_id=user_id,
        title=title[:500],
        mime_type=mime,
        size_bytes=size_bytes or len(text.encode("utf-8")),
        content=text[:200_000],
        source=source,
    )
    db.add(document)
    db.commit()
    db.refresh(document)

    for ordinal, chunk in enumerate(chunk_text(text)):
        db.add(
            DocumentChunk(
                document_id=document.id,
                user_id=user_id,
                ordinal=ordinal,
                content=chunk,
                embedding=embed_text(chunk),
            )
        )
    db.commit()
    db.refresh(document)
    return document


def search(db: Session, user_id: str, query: str, limit: int = 5) -> list[dict]:
    """Semantic search across every chunk the user owns."""
    chunks = db.scalars(
        select(DocumentChunk).where(DocumentChunk.user_id == user_id)
    ).all()
    if not chunks:
        return []

    q_vec = embed_text(query)
    titles = {
        d.id: d.title
        for d in db.scalars(select(Document).where(Document.user_id == user_id)).all()
    }

    scored = []
    for chunk in chunks:
        if q_vec and chunk.embedding:
            score = _cosine(q_vec, list(chunk.embedding))
        else:
            score = _lexical(query, chunk.content)
        if score > 0.02:
            scored.append((score, chunk))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [
        {
            "document_id": chunk.document_id,
            "title": titles.get(chunk.document_id, "Untitled"),
            "ordinal": chunk.ordinal,
            "excerpt": chunk.content[:700],
            "score": round(score, 3),
        }
        for score, chunk in scored[:limit]
    ]


def summarise(db: Session, user_id: str, document: Document) -> str:
    """Summarise on demand and cache the result on the row."""
    if document.summary:
        return document.summary

    from app.llm import get_provider

    provider = get_provider()
    body = document.content[:12_000]

    if provider.name == "mock":
        first = next((p for p in body.split("\n\n") if len(p.strip()) > 60), body[:400])
        summary = f"(demo mode — no model configured)\n\n{first.strip()[:400]}"
    else:
        try:
            resp = provider.complete(
                [
                    {
                        "role": "system",
                        "content": (
                            "Summarise documents for a busy executive. Lead with what "
                            "they must decide or do. Under 150 words. No preamble."
                        ),
                    },
                    {"role": "user", "content": f"Title: {document.title}\n\n{body}"},
                ]
            )
            summary = resp.text.strip()
        except Exception as exc:
            log.warning("Document summary failed: %s", exc)
            summary = body[:400]

    document.summary = summary
    db.commit()
    return summary


def compare(db: Session, user_id: str, left: Document, right: Document) -> dict:
    """Diff two documents at the meaning level, not the character level."""
    from app.llm import get_provider

    provider = get_provider()
    if provider.name == "mock":
        left_paras = {p.strip() for p in left.content.split("\n\n") if p.strip()}
        right_paras = {p.strip() for p in right.content.split("\n\n") if p.strip()}
        return {
            "title": f"{left.title} vs {right.title}",
            "summary": "(demo mode) Structural comparison only.",
            "only_in_left": sorted(left_paras - right_paras)[:5],
            "only_in_right": sorted(right_paras - left_paras)[:5],
        }

    resp = provider.complete(
        [
            {
                "role": "system",
                "content": (
                    "Compare two documents. Report only material differences — terms, "
                    "numbers, obligations, dates. Ignore rewording. Bullet points."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"--- A: {left.title} ---\n{left.content[:8000]}\n\n"
                    f"--- B: {right.title} ---\n{right.content[:8000]}"
                ),
            },
        ]
    )
    return {
        "title": f"{left.title} vs {right.title}",
        "summary": resp.text.strip(),
        "only_in_left": [],
        "only_in_right": [],
    }


def delete(db: Session, user_id: str, document_id: str) -> bool:
    document = db.get(Document, document_id)
    if not document or document.user_id != user_id:
        return False
    db.delete(document)  # chunks cascade
    db.commit()
    return True
