"""
Document chunker — splits PDFs, DOCX, TXT into overlapping text chunks.

Handles three source formats:
  .pdf  — uses pypdf (already a project dependency)
  .docx — uses python-docx (already a project dependency)
  .txt  / .md / .rst — plain text read

Each chunk is tagged with its source file and a sequential index so that
retrieval results can be attributed back to the originating document.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Iterable

logger = logging.getLogger(__name__)


@dataclass
class Chunk:
    """A single text fragment ready for embedding."""
    text: str
    source_file: str       # relative path within the knowledge base
    chunk_index: int
    char_start: int = 0
    char_end: int = 0

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "source_file": self.source_file,
            "chunk_index": self.chunk_index,
            "char_start": self.char_start,
            "char_end": self.char_end,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Chunk":
        return cls(**d)


def _read_text(path: Path) -> str:
    """Read a plain-text file and normalise whitespace."""
    text = path.read_text(encoding="utf-8", errors="replace")
    # collapse runs of whitespace to single spaces, strip leading/trailing
    import re
    return re.sub(r"\s+", " ", text).strip()


def _read_pdf(path: Path) -> str:
    """Extract text from a PDF using pypdf."""
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    pages = []
    for page in reader.pages:
        t = page.extract_text()
        if t:
            pages.append(t)
    return "\n\n".join(pages)


def _read_docx(path: Path) -> str:
    """Extract text from a DOCX using python-docx."""
    from docx import Document
    doc = Document(str(path))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)


READERS = {
    ".pdf": _read_pdf,
    ".docx": _read_docx,
    ".txt": _read_text,
    ".md": _read_text,
    ".rst": _read_text,
}


def chunk_text(
    text: str,
    source_file: str,
    chunk_size: int = 300,
    chunk_overlap: int = 50,
    max_chunks: int = 200,
) -> List[Chunk]:
    """
    Split *text* into overlapping chunks of roughly *chunk_size* characters.

    Chunks are aligned to paragraph boundaries where possible (we split on
    double-newlines first, then fit sentences into each window).
    """
    # First split on paragraph boundaries
    paragraphs = text.split("\n\n")
    chunks: List[Chunk] = []
    buf = ""
    idx = 0
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        candidate = (buf + "\n\n" + para).strip() if buf else para
        while len(candidate) > chunk_size:
            # Find a good break point near chunk_size
            break_at = max(candidate.rfind(".", 0, chunk_size + 20),
                            candidate.rfind("\n", 0, chunk_size + 20))
            if break_at < chunk_size // 2:
                break_at = chunk_size  # hard cut
            chunks.append(Chunk(
                text=candidate[:break_at].strip(),
                source_file=source_file,
                chunk_index=idx,
                char_start=sum(len(c.text) + 2 for c in chunks),
                char_end=sum(len(c.text) + 2 for c in chunks) + break_at,
            ))
            idx += 1
            if idx >= max_chunks:
                return chunks
            # Overlap: keep last ~overlap chars for next iteration
            candidate = candidate[break_at - chunk_overlap:].strip()
        buf = candidate

    if buf.strip():
        chunks.append(Chunk(
            text=buf.strip(),
            source_file=source_file,
            chunk_index=idx,
            char_start=sum(len(c.text) + 2 for c in chunks),
            char_end=sum(len(c.text) + 2 for c in chunks) + len(buf),
        ))

    return chunks


def ingest_documents(
    paths: Iterable[Path],
    chunk_size: int = 300,
    chunk_overlap: int = 50,
    max_chunks_per_doc: int = 200,
) -> List[Chunk]:
    """
    Read documents from *paths*, chunk them, and return a flat list of
    :class:`Chunk` objects ready for embedding.

    Supported extensions: .pdf, .docx, .txt, .md, .rst
    """
    all_chunks: List[Chunk] = []
    for path in paths:
        path = Path(path)
        ext = path.suffix.lower()
        reader = READERS.get(ext)
        if reader is None:
            logger.warning("Skipping unsupported file type: %s", path.name)
            continue
        rel = ""
        try:
            rel = str(path.relative_to(Path.cwd()))
        except ValueError:
            # tmp_path or other location outside cwd — fall back to basename
            rel = path.name
        try:
            text = reader(path)
        except Exception as exc:
            logger.error("Failed to read %s: %s", path.name, exc)
            continue
        chunks = chunk_text(
            text,
            source_file=rel,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            max_chunks=max_chunks_per_doc,
        )
        logger.info("Ingester %s → %d chunks", path.name, len(chunks))
        all_chunks.extend(chunks)
    return all_chunks
