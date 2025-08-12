import os
import json
import glob
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from .config import load_config, get_ollama_base_url, get_vision_model


# -------------------------------
# Simple in-memory RAG index (no extra deps)
# -------------------------------

@dataclass
class Chunk:
    source: str
    heading: str
    text: str
    mtime: float


_INDEX: List[Chunk] | None = None
_MTIMES: dict[str, float] = {}
_SETTINGS_FINGERPRINT: str | None = None


def _project_root() -> Path:
    """Best-effort project root: prefer directory containing .smallfactory.yml, else cwd."""
    cwd = Path.cwd()
    # Walk up to find .smallfactory.yml
    for p in [cwd] + list(cwd.parents):
        if (p / ".smallfactory.yml").exists():
            return p
    return cwd


def _normalize_settings(cfg: dict) -> dict:
    assistant = cfg.get("assistant") or {}
    docs = assistant.get("docs") or {}
    rag = assistant.get("rag") or {}
    include = docs.get("include") or [
        "README.md",
        "web/README.md",
        "smallfactory/core/v1/PLM_SPECIFICATION.md",
        "docs/**/*.md",
    ]
    exclude = docs.get("exclude") or []
    types = docs.get("types") or ["md", "mdx", "txt"]
    max_bytes = int(docs.get("max_bytes") or 500_000)

    chunk_size = int(rag.get("chunk_size") or 1000)
    chunk_overlap = int(rag.get("chunk_overlap") or 150)
    max_chunks_per_query = int(rag.get("max_chunks_per_query") or 3)
    persist_index = bool(rag.get("persist_index") or True)
    index_path = rag.get("index_path") or "web/.assistant_index.json"
    watch_mtime = bool(rag.get("watch_mtime") or True)

    # Env overrides (comma-separated include patterns, etc.)
    env_inc = os.environ.get("SF_ASSISTANT_DOCS")
    if env_inc:
        include = [s.strip() for s in env_inc.split(",") if s.strip()]
    chunk_size = int(os.environ.get("SF_ASSISTANT_CHUNK_SIZE", chunk_size))
    chunk_overlap = int(os.environ.get("SF_ASSISTANT_CHUNK_OVERLAP", chunk_overlap))
    max_chunks_per_query = int(os.environ.get("SF_ASSISTANT_MAX_CHUNKS", max_chunks_per_query))
    persist_index = os.environ.get("SF_ASSISTANT_PERSIST_INDEX", str(int(persist_index))) in ("1", "true", "True")
    index_path = os.environ.get("SF_ASSISTANT_INDEX_PATH", index_path)
    watch_mtime = os.environ.get("SF_ASSISTANT_WATCH_MTIME", str(int(watch_mtime))) in ("1", "true", "True")

    return {
        "include": include,
        "exclude": exclude,
        "types": types,
        "max_bytes": max_bytes,
        "chunk_size": chunk_size,
        "chunk_overlap": chunk_overlap,
        "max_chunks_per_query": max_chunks_per_query,
        "persist_index": persist_index,
        "index_path": index_path,
        "watch_mtime": watch_mtime,
    }


def _settings_fingerprint(settings: dict) -> str:
    return json.dumps(settings, sort_keys=True)


def _iter_source_files(root: Path, include: List[str], exclude: List[str], types: List[str], max_bytes: int) -> List[Path]:
    # Expand include globs, then filter by exclude globs and extensions
    files: List[Path] = []
    for pat in include:
        for p in root.glob(pat):
            if p.is_file():
                files.append(p)
            elif p.is_dir():
                # include dir means recursive include of allowed types
                for ext in types:
                    files.extend(p.rglob(f"*.{ext}"))
    # Deduplicate
    uniq: dict[str, Path] = {}
    for f in files:
        uniq[str(f.resolve())] = f
    files = list(uniq.values())
    # Apply excludes
    if exclude:
        excl_paths: set[str] = set()
        for pat in exclude:
            for p in root.glob(pat):
                excl_paths.add(str(p.resolve()))
                if p.is_dir():
                    for sub in p.rglob("*"):
                        excl_paths.add(str(sub.resolve()))
        files = [f for f in files if str(f.resolve()) not in excl_paths]
    # Filter by types and size
    out: List[Path] = []
    for f in files:
        try:
            if f.suffix.lower().lstrip(".") not in types:
                continue
            if f.stat().st_size > max_bytes:
                continue
            out.append(f)
        except Exception:
            continue
    return out


def _split_into_chunks(text: str, chunk_size: int, chunk_overlap: int) -> List[str]:
    # Simple paragraph/heading-aware splitter
    lines = text.splitlines()
    chunks: List[str] = []
    buf: List[str] = []
    cur_len = 0
    def flush():
        nonlocal buf, cur_len
        if buf:
            chunks.append("\n".join(buf).strip())
            buf = []
            cur_len = 0
    for ln in lines:
        # Start new chunk on top-level heading
        if ln.strip().startswith("# ") and cur_len > 0:
            flush()
        buf.append(ln)
        cur_len += len(ln) + 1
        if cur_len >= chunk_size:
            flush()
            if chunk_overlap > 0 and chunks:
                # carry last N chars into next buffer
                tail = chunks[-1][-chunk_overlap:]
                buf = [tail]
                cur_len = len(tail)
    flush()
    # Drop empties
    return [c for c in chunks if c]


def _extract_heading(chunk_text: str) -> str:
    for ln in chunk_text.splitlines():
        s = ln.strip()
        if s.startswith("#"):
            return s.lstrip("# ").strip()
    return ""


def _tokenize(s: str) -> List[str]:
    import re
    return re.findall(r"[a-z0-9]+", s.lower())


def _build_idf(chunks: List[Chunk]) -> dict[str, float]:
    import math
    df: dict[str, int] = {}
    for c in chunks:
        seen = set(_tokenize(c.text))
        for t in seen:
            df[t] = df.get(t, 0) + 1
    N = max(1, len(chunks))
    return {t: math.log((N + 1) / (df_t + 1)) + 1.0 for t, df_t in df.items()}


def _score(text_tokens: List[str], query_tokens: List[str], idf: dict[str, float]) -> float:
    # Simple IDF-weighted overlap
    score = 0.0
    ts = set(text_tokens)
    for qt in query_tokens:
        if qt in ts:
            score += idf.get(qt, 1.0)
    return score


def _load_persisted_index(index_file: Path) -> Optional[Tuple[List[Chunk], dict[str, float]]]:
    try:
        if not index_file.exists():
            return None
        data = json.loads(index_file.read_text(encoding="utf-8"))
        chunks = [Chunk(**c) for c in data.get("chunks", [])]
        idf = {k: float(v) for k, v in data.get("idf", {}).items()}
        return chunks, idf
    except Exception:
        return None


def _save_persisted_index(index_file: Path, chunks: List[Chunk], idf: dict[str, float]) -> None:
    try:
        index_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "chunks": [c.__dict__ for c in chunks],
            "idf": idf,
        }
        index_file.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass


def _build_index_internal(settings: dict) -> Tuple[List[Chunk], dict[str, float]]:
    root = _project_root()
    files = _iter_source_files(root, settings["include"], settings["exclude"], settings["types"], settings["max_bytes"])
    chunks: List[Chunk] = []
    for f in files:
        try:
            txt = f.read_text(encoding="utf-8")
        except Exception:
            continue
        for ch in _split_into_chunks(txt, settings["chunk_size"], settings["chunk_overlap"]):
            chunks.append(Chunk(source=str(f.relative_to(root)), heading=_extract_heading(ch), text=ch, mtime=f.stat().st_mtime))
    idf = _build_idf(chunks)
    return chunks, idf


def _ensure_index(force: bool = False) -> Tuple[List[Chunk], dict[str, float], dict]:
    global _INDEX, _MTIMES, _SETTINGS_FINGERPRINT
    cfg = load_config() or {}
    settings = _normalize_settings(cfg)
    fp = _settings_fingerprint(settings)

    # Rebuild on settings change or force
    if force or _INDEX is None or _SETTINGS_FINGERPRINT != fp:
        # Try persisted index first
        if settings.get("persist_index"):
            persisted = _load_persisted_index(_project_root() / settings["index_path"])
            if persisted:
                _INDEX, idf = persisted
                _MTIMES = {c.source: c.mtime for c in _INDEX}
                _SETTINGS_FINGERPRINT = fp
                return _INDEX, idf, settings
        # Build fresh
        _INDEX, idf = _build_index_internal(settings)
        _MTIMES = {c.source: c.mtime for c in _INDEX}
        _SETTINGS_FINGERPRINT = fp
        if settings.get("persist_index"):
            _save_persisted_index(_project_root() / settings["index_path"], _INDEX, idf)
        return _INDEX, idf, settings

    # Optional mtime watch
    if settings.get("watch_mtime"):
        root = _project_root()
        changed = False
        for c in (_INDEX or []):
            p = root / c.source
            try:
                mt = p.stat().st_mtime
            except Exception:
                mt = 0.0
            if mt != _MTIMES.get(c.source):
                changed = True
                break
        if changed:
            _INDEX, idf = _build_index_internal(settings)
            _MTIMES = {c.source: c.mtime for c in _INDEX}
            if settings.get("persist_index"):
                _save_persisted_index(_project_root() / settings["index_path"], _INDEX, idf)
            return _INDEX, idf, settings

    # Compute IDF for current index
    idf = _build_idf(_INDEX or [])
    return _INDEX or [], idf, settings


def reindex() -> dict:
    _ensure_index(force=True)
    return {"ok": True, "chunks": len(_INDEX or [])}


def retrieve(query: str, extra_context: Optional[List[str]] = None, k: Optional[int] = None) -> Tuple[List[Chunk], List[Tuple[str, str]]]:
    index, idf, settings = _ensure_index()
    if k is None:
        k = int(settings.get("max_chunks_per_query", 3))
    tokens_q = _tokenize(query or "")
    if extra_context:
        for c in extra_context:
            tokens_q.extend(_tokenize(c))
    scored: List[Tuple[float, Chunk]] = []
    for c in index:
        sc = _score(_tokenize(c.text), tokens_q, idf)
        if sc > 0:
            scored.append((sc, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    top = [c for _, c in scored[:k]]
    citations = [(c.source, c.heading) for c in top]
    return top, citations


def compose_prompt(chunks: List[Chunk], question: str, context_note: Optional[str] = None) -> str:
    # Limit total context size
    max_ctx_chars = 2500
    assembled = []
    total = 0
    for i, c in enumerate(chunks, 1):
        header = f"[{i}] {c.source} §{c.heading}\n"
        body = c.text.strip()
        piece = header + body + "\n\n"
        if total + len(piece) > max_ctx_chars:
            break
        assembled.append(piece)
        total += len(piece)
    context_block = "".join(assembled) if assembled else "(no retrieved context)\n"

    note = context_note or ""

    return (
        "You are the smallFactory in-app assistant.\n"
        "Answer concisely and accurately. Prefer the provided CONTEXT.\n"
        "If the answer is not in CONTEXT, say you are unsure and suggest the most relevant doc section.\n"
        "Cite sources as [file §heading]. Avoid speculation.\n\n"
        f"CONTEXT:\n{context_block}"
        f"Page/context: {note}\n\n"
        f"Question: {question}\n"
    )


def ask_text(prompt: str, *, model: Optional[str] = None, base_url: Optional[str] = None, temperature: float = 0.2) -> str:
    try:
        import ollama
        from ollama import Client
    except Exception as e:
        raise RuntimeError("Ollama client is not installed. Install with: pip install ollama") from e

    model_name = model or get_vision_model()
    host = base_url or get_ollama_base_url()
    client = Client(host=host)

    messages = [
        {"role": "system", "content": "You are a helpful assistant for smallFactory users."},
        {"role": "user", "content": prompt},
    ]

    resp = client.chat(model=model_name, messages=messages, options={"temperature": float(temperature)})
    content = (resp or {}).get("message", {}).get("content", "").strip()
    return content


def chat(messages: List[dict], context: Optional[dict] = None) -> dict:
    """High-level chat handler used by the web API.

    Request shape:
      messages: [{role: 'user'|'assistant'|'system', content: str}, ...]
      context: { route, sfid, page_title }
    """
    last_user = next((m for m in reversed(messages or []) if m.get("role") == "user" and m.get("content")), None)
    question = (last_user or {}).get("content", "")
    route = (context or {}).get("route") or ""
    sfid = (context or {}).get("sfid") or ""
    page_title = (context or {}).get("page_title") or ""

    extra = [s for s in [route, sfid, page_title] if s]
    chunks, citations = retrieve(question, extra_context=extra)
    prompt = compose_prompt(chunks, question, context_note=f"route={route} sfid={sfid} title={page_title}")
    answer = ask_text(prompt)
    return {"reply": answer, "citations": [{"source": s, "heading": h} for s, h in citations]}
