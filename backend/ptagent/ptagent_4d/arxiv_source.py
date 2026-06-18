from __future__ import annotations

import gzip
import json
import re
import shutil
import tarfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable

from .config import Settings
from .schemas import SourceSpan
from .source_reader import SourceDocument, read_source


ARXIV_ID_RE = re.compile(
    r"^(?:arxiv:)?(?P<base>(?:\d{4}\.\d{4,5})|(?:[a-z-]+(?:\.[A-Z]{2})?/\d{7}))(?:v(?P<version>\d+))?$",
    re.IGNORECASE,
)

ProgressCallback = Callable[[str, float | None], None]

ARXIV_SOURCE_ARCHIVE_SUFFIXES = {".zip", ".tar", ".tgz", ".gz"}


@dataclass(frozen=True)
class ArxivMetadata:
    requested_id: str
    base_id: str
    canonical_id: str
    latest_version: str
    title: str = ""
    updated: str = ""
    abs_url: str = ""
    pdf_url: str = ""

    @property
    def paper_id(self) -> str:
        return arxiv_paper_id(self.canonical_id)


@dataclass(frozen=True)
class ArxivPreparedSource:
    metadata: ArxivMetadata
    paper_root: Path
    pdf_path: Path
    source_archive_path: Path
    tex_root: Path
    main_tex_path: Path | None
    bundled_tex_path: Path | None
    manifest_path: Path
    diagnostics: dict[str, str]


@dataclass(frozen=True)
class PdfTitleCheck:
    arxiv_title: str
    pdf_title: str
    similarity: float
    status: str
    message: str

    @property
    def is_mismatch(self) -> bool:
        return self.status == "mismatch"


def read_arxiv_source(
    arxiv_id: str,
    settings: Settings,
    *,
    refresh: bool = False,
    progress: ProgressCallback | None = None,
) -> SourceDocument:
    prepared = prepare_arxiv_source(arxiv_id, settings, refresh=refresh, progress=progress)
    spans: list[SourceSpan] = []
    paper_markdown_parts: list[str] = []
    diagnostics = dict(prepared.diagnostics)
    diagnostics.update(
        {
            "source_kind": "arxiv",
            "requested_arxiv_id": prepared.metadata.requested_id,
            "canonical_arxiv_id": prepared.metadata.canonical_id,
            "latest_version": prepared.metadata.latest_version,
            "paper_root": str(prepared.paper_root.resolve()),
        }
    )

    if prepared.bundled_tex_path and prepared.bundled_tex_path.exists():
        _emit_progress(progress, "Reading arXiv TeX source with Codex parser", 0.52)
        tex_text = prepared.bundled_tex_path.read_text(encoding="utf-8", errors="replace")
        spans.extend(_tex_context_spans(prepared.metadata.paper_id, tex_text, settings.source_span_limit * 3))
        paper_markdown_parts.append(_tex_material_markdown(prepared, tex_text))
        diagnostics["primary_reading_layer"] = "tex_source"
        diagnostics["formula_parser"] = "codex_tex_parser"
        diagnostics["tex_formula_path"] = str(prepared.bundled_tex_path.resolve())
    else:
        diagnostics["primary_reading_layer"] = "pdf_fallback"

    if prepared.pdf_path.exists():
        _emit_progress(progress, "Reading PDF fallback layer and preserving page-number evidence", 0.58)
        pdf_doc = read_source(prepared.pdf_path, settings)
        spans.extend(_retag_spans(pdf_doc.spans, old_id=prepared.pdf_path.stem, new_id=prepared.metadata.paper_id))
        if pdf_doc.paper_markdown:
            paper_markdown_parts.append(
                pdf_doc.paper_markdown.replace(
                    f"# Paper Markdown: {prepared.pdf_path.stem}",
                    f"# PDF Reading Layer: {prepared.metadata.paper_id}",
                    1,
                )
            )
        diagnostics.update({f"pdf_{key}": value for key, value in pdf_doc.diagnostics.items()})

    if not spans:
        raise ValueError(f"No readable TeX or PDF content was prepared for arXiv {arxiv_id}.")

    _emit_progress(progress, "arXiv source reading complete", 0.62)
    return SourceDocument(
        paper_id=prepared.metadata.paper_id,
        path=prepared.manifest_path,
        suffix=".arxiv",
        spans=spans,
        diagnostics=diagnostics,
        paper_markdown="\n\n---\n\n".join(part for part in paper_markdown_parts if part.strip()),
    )


def is_arxiv_source_archive(path: str | Path) -> bool:
    source_path = Path(path)
    suffixes = [suffix.lower() for suffix in source_path.suffixes]
    if suffixes[-2:] == [".tar", ".gz"]:
        return True
    if suffixes and suffixes[-1] in ARXIV_SOURCE_ARCHIVE_SUFFIXES:
        return True
    try:
        if tarfile.is_tarfile(source_path) or zipfile.is_zipfile(source_path):
            return True
        with source_path.open("rb") as handle:
            return handle.read(2) == b"\x1f\x8b"
    except OSError:
        return False


def read_arxiv_archive_source(
    archive_path: str | Path,
    settings: Settings,
    *,
    progress: ProgressCallback | None = None,
) -> SourceDocument:
    original_archive_path = Path(archive_path)
    if not is_arxiv_source_archive(original_archive_path):
        raise ValueError(f"Unsupported arXiv source archive suffix: {original_archive_path.name}")

    paper_id = _archive_paper_id(original_archive_path)
    paper_root = settings.input_root / paper_id
    input_dir = paper_root
    archive_root = input_dir / "source"
    bundled_tex_path = input_dir / "source_expanded.tex"
    input_dir.mkdir(parents=True, exist_ok=True)
    source_archive_path = input_dir / original_archive_path.name
    if original_archive_path.resolve() != source_archive_path.resolve():
        shutil.copy2(original_archive_path, source_archive_path)

    _emit_progress(progress, "Unpacking uploaded arXiv source archive", 0.18)
    _extract_eprint(source_archive_path, archive_root, refresh=True)
    _emit_progress(progress, "Locating readable source file in uploaded archive", 0.28)
    main_tex_path = _find_main_tex(archive_root)
    diagnostics = {
        "source_kind": "uploaded_arxiv_source_archive",
        "archive_path": str(original_archive_path.resolve()),
        "cached_archive_path": str(source_archive_path.resolve()),
        "paper_root": str(paper_root.resolve()),
        "archive_root": str(archive_root.resolve()),
        "source_priority": "tex > markdown > pdf",
    }

    if main_tex_path:
        _emit_progress(progress, f"Expanding TeX include/input files from {main_tex_path.name}", 0.36)
        tex_text = _expand_tex_file(main_tex_path)
        bundled_tex_path.write_text(tex_text, encoding="utf-8")
        diagnostics["primary_reading_layer"] = "tex_source_archive"
        diagnostics["main_tex"] = str(main_tex_path)
        diagnostics["bundled_tex"] = str(bundled_tex_path)

        _emit_progress(progress, "Reading uploaded arXiv TeX source with Codex parser", 0.52)
        spans = _tex_context_spans(paper_id, tex_text, settings.source_span_limit * 3)
        if spans:
            paper_markdown = _archive_tex_material_markdown(paper_id, source_archive_path, main_tex_path, tex_text)
            diagnostics["formula_parser"] = "codex_tex_parser"
            diagnostics["tex_formula_path"] = str(bundled_tex_path.resolve())
            _emit_progress(progress, "Uploaded arXiv source archive reading complete", 0.62)
            return SourceDocument(
                paper_id=paper_id,
                path=bundled_tex_path,
                suffix=".arxiv-archive",
                spans=spans,
                diagnostics=diagnostics,
                paper_markdown=paper_markdown,
            )
        diagnostics["tex_error"] = "A main TeX file was found, but no formula-relevant TeX spans were produced."
    else:
        diagnostics["tex_error"] = "No .tex file with document structure was found in the uploaded arXiv source archive."

    markdown_path = _find_archive_markdown(archive_root)
    if markdown_path:
        return _read_archive_fallback_document(
            paper_id=paper_id,
            selected_path=markdown_path,
            archive_path=source_archive_path,
            settings=settings,
            diagnostics=diagnostics,
            layer="markdown_archive",
            progress=progress,
        )

    pdf_path = _find_archive_pdf(archive_root)
    if pdf_path:
        return _read_archive_fallback_document(
            paper_id=paper_id,
            selected_path=pdf_path,
            archive_path=source_archive_path,
            settings=settings,
            diagnostics=diagnostics,
            layer="pdf_archive",
            progress=progress,
        )

    raise ValueError(
        "No readable TeX, Markdown, or PDF source was found in the uploaded arXiv source archive."
    )


def prepare_arxiv_source(
    arxiv_id: str,
    settings: Settings,
    *,
    refresh: bool = False,
    progress: ProgressCallback | None = None,
) -> ArxivPreparedSource:
    _emit_progress(progress, "Querying latest arXiv version metadata", 0.04)
    metadata = resolve_latest_arxiv_metadata(arxiv_id)
    _emit_progress(progress, f"Using arXiv version: {metadata.canonical_id}", 0.10)
    paper_root = settings.input_root / metadata.paper_id
    input_dir = paper_root
    source_dir = input_dir / "source"
    tex_root = input_dir / "tex"
    pdf_path = input_dir / "source.pdf"
    source_archive_path = input_dir / "e-print"
    manifest_path = paper_root / "manifest.json"
    for path in (paper_root, input_dir, source_dir, tex_root):
        path.mkdir(parents=True, exist_ok=True)

    diagnostics: dict[str, str] = {}

    pdf_url = metadata.pdf_url or f"https://arxiv.org/pdf/{metadata.canonical_id}"
    source_url = f"https://arxiv.org/e-print/{metadata.canonical_id}"
    try:
        pdf_cached = pdf_path.exists() and pdf_path.stat().st_size > 0 and not refresh
        _download_file(
            pdf_url,
            pdf_path,
            refresh=refresh,
            progress=progress,
            label="PDF",
            start=0.12,
            end=0.24,
        )
        diagnostics["pdf_status"] = "cached" if pdf_cached else "downloaded"
    except Exception as exc:
        diagnostics["pdf_error"] = f"{type(exc).__name__}: {exc}"

    try:
        source_cached = source_archive_path.exists() and source_archive_path.stat().st_size > 0 and not refresh
        _download_file(
            source_url,
            source_archive_path,
            refresh=refresh,
            progress=progress,
            label="TeX source/e-print",
            start=0.25,
            end=0.38,
        )
        diagnostics["source_status"] = "cached" if source_cached else "downloaded"
    except Exception as exc:
        diagnostics["source_error"] = f"{type(exc).__name__}: {exc}"

    main_tex_path: Path | None = None
    bundled_tex_path: Path | None = None
    if source_archive_path.exists():
        try:
            _emit_progress(progress, "Unpacking arXiv e-print source", 0.40)
            _extract_eprint(source_archive_path, tex_root, refresh=refresh)
            _emit_progress(progress, "Locating main TeX file", 0.44)
            main_tex_path = _find_main_tex(tex_root)
            if main_tex_path:
                bundled_tex_path = input_dir / "source_expanded.tex"
                _emit_progress(progress, f"Expanding TeX include/input files from {main_tex_path.name}", 0.48)
                bundled_tex_path.write_text(_expand_tex_file(main_tex_path), encoding="utf-8")
                diagnostics["main_tex"] = str(main_tex_path.relative_to(paper_root))
                diagnostics["tex_status"] = "expanded"
            else:
                diagnostics["tex_error"] = "No .tex file with document structure was found in the e-print source."
        except Exception as exc:
            diagnostics["tex_error"] = f"{type(exc).__name__}: {exc}"

    prepared = ArxivPreparedSource(
        metadata=metadata,
        paper_root=paper_root,
        pdf_path=pdf_path,
        source_archive_path=source_archive_path,
        tex_root=tex_root,
        main_tex_path=main_tex_path,
        bundled_tex_path=bundled_tex_path,
        manifest_path=manifest_path,
        diagnostics=diagnostics,
    )
    manifest = asdict(prepared)
    for key in ("paper_root", "pdf_path", "source_archive_path", "tex_root", "main_tex_path", "bundled_tex_path", "manifest_path"):
        value = manifest.get(key)
        manifest[key] = str(value) if value else ""
    manifest["metadata"] = asdict(metadata)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    _emit_progress(progress, "arXiv source/PDF preparation complete", 0.50)
    return prepared


def resolve_latest_arxiv_metadata(arxiv_id: str) -> ArxivMetadata:
    requested, base_id, _requested_version = parse_arxiv_id(arxiv_id)
    query = urllib.parse.urlencode({"id_list": base_id})
    url = f"https://export.arxiv.org/api/query?{query}"
    try:
        with urllib.request.urlopen(_request(url), timeout=30) as response:
            payload = response.read()
        return _metadata_from_api_payload(requested, base_id, payload)
    except Exception:
        # Direct arXiv PDF/e-print URLs without a version redirect to the latest
        # available version, but the exact version is unknown until metadata works.
        canonical = base_id
        return ArxivMetadata(
            requested_id=requested,
            base_id=base_id,
            canonical_id=canonical,
            latest_version="latest",
            abs_url=f"https://arxiv.org/abs/{canonical}",
            pdf_url=f"https://arxiv.org/pdf/{canonical}",
        )


def parse_arxiv_id(value: str) -> tuple[str, str, str]:
    requested = value.strip()
    requested = requested.replace("https://arxiv.org/abs/", "").replace("http://arxiv.org/abs/", "")
    requested = requested.replace("https://arxiv.org/pdf/", "").replace("http://arxiv.org/pdf/", "")
    requested = requested.removesuffix(".pdf")
    match = ARXIV_ID_RE.match(requested)
    if not match:
        raise ValueError(f"Invalid arXiv identifier: {value!r}")
    base_id = match.group("base")
    version = match.group("version") or ""
    return requested, base_id, version


def arxiv_paper_id(canonical_id: str) -> str:
    clean = canonical_id.strip().replace("/", "_").replace(".", "_")
    clean = re.sub(r"[^A-Za-z0-9_]+", "_", clean).strip("_")
    return f"arxiv_{clean}"


def _archive_paper_id(path: Path) -> str:
    name = path.name
    lowered = name.lower()
    for ending in (".tar.gz", ".tgz", ".zip", ".tar", ".gz"):
        if lowered.endswith(ending):
            name = name[: -len(ending)]
            break
    clean = re.sub(r"[^A-Za-z0-9_]+", "_", name.replace(".", "_").replace("/", "_")).strip("_")
    return f"upload_arxiv_source_{clean or 'archive'}"


def check_pdf_title_against_arxiv(
    pdf_path: str | Path,
    metadata: ArxivMetadata,
    *,
    threshold: float = 0.72,
) -> PdfTitleCheck:
    pdf_title = extract_pdf_title(pdf_path)
    arxiv_title = metadata.title.strip()
    if not arxiv_title:
        return PdfTitleCheck(
            arxiv_title=arxiv_title,
            pdf_title=pdf_title,
            similarity=0.0,
            status="unknown",
            message="arXiv metadata did not provide a title, so the uploaded PDF title could not be checked.",
        )
    if not pdf_title:
        return PdfTitleCheck(
            arxiv_title=arxiv_title,
            pdf_title=pdf_title,
            similarity=0.0,
            status="unknown",
            message="The uploaded PDF title could not be read reliably, so title consistency could not be checked.",
        )

    similarity = title_similarity(pdf_title, arxiv_title)
    status = "match" if similarity >= threshold else "mismatch"
    message = (
        f"PDF title matches arXiv metadata (similarity={similarity:.2f})."
        if status == "match"
        else f"Uploaded PDF title appears inconsistent with arXiv metadata (similarity={similarity:.2f})."
    )
    return PdfTitleCheck(
        arxiv_title=arxiv_title,
        pdf_title=pdf_title,
        similarity=similarity,
        status=status,
        message=message,
    )


def extract_pdf_title(pdf_path: str | Path) -> str:
    path = Path(pdf_path)
    metadata_title = _extract_pdf_metadata_title(path)
    if _looks_like_real_title(metadata_title):
        return _clean_title(metadata_title)
    first_page_text = _extract_first_pdf_page_text(path)
    return _guess_title_from_first_page(first_page_text)


def title_similarity(left: str, right: str) -> float:
    norm_left = _normalize_title_for_compare(left)
    norm_right = _normalize_title_for_compare(right)
    if not norm_left or not norm_right:
        return 0.0
    if norm_left in norm_right or norm_right in norm_left:
        return 1.0
    return SequenceMatcher(None, norm_left, norm_right).ratio()


def _metadata_from_api_payload(requested: str, base_id: str, payload: bytes) -> ArxivMetadata:
    root = ET.fromstring(payload)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    entry = root.find("atom:entry", ns)
    if entry is None:
        raise ValueError(f"arXiv API returned no entry for {base_id}.")
    id_text = (entry.findtext("atom:id", default="", namespaces=ns) or "").strip()
    canonical_id = id_text.rsplit("/", 1)[-1] if id_text else base_id
    version_match = re.search(r"v(\d+)$", canonical_id)
    latest_version = version_match.group(0) if version_match else "latest"
    title = " ".join((entry.findtext("atom:title", default="", namespaces=ns) or "").split())
    updated = (entry.findtext("atom:updated", default="", namespaces=ns) or "").strip()
    pdf_url = f"https://arxiv.org/pdf/{canonical_id}"
    for link in entry.findall("atom:link", ns):
        if link.attrib.get("title") == "pdf" and link.attrib.get("href"):
            pdf_url = link.attrib["href"]
            break
    return ArxivMetadata(
        requested_id=requested,
        base_id=base_id,
        canonical_id=canonical_id,
        latest_version=latest_version,
        title=title,
        updated=updated,
        abs_url=id_text or f"https://arxiv.org/abs/{canonical_id}",
        pdf_url=pdf_url,
    )


def _extract_pdf_metadata_title(path: Path) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        metadata = reader.metadata
        title = getattr(metadata, "title", "") if metadata else ""
        return str(title or "")
    except Exception:
        return ""


def _extract_first_pdf_page_text(path: Path) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        if reader.pages:
            return reader.pages[0].extract_text() or ""
    except Exception:
        pass
    try:
        import fitz

        doc = fitz.open(str(path))
        try:
            if len(doc):
                return doc[0].get_text("text") or ""
        finally:
            doc.close()
    except Exception:
        return ""
    return ""


def _guess_title_from_first_page(text: str) -> str:
    lines = [_clean_title(line) for line in text.splitlines()]
    lines = [line for line in lines if line]
    filtered: list[str] = []
    for line in lines[:35]:
        lowered = line.lower()
        if lowered.startswith(("arxiv:", "preprint", "prepared for", "submitted to")):
            continue
        if lowered in {"abstract", "introduction"} or lowered.startswith("abstract "):
            break
        if re.fullmatch(r"\d+", line):
            continue
        if re.search(r"@|department|university|institute|collaboration", lowered):
            continue
        if len(line) < 8:
            continue
        filtered.append(line)
        if _looks_like_author_line(line):
            break

    title_lines: list[str] = []
    for line in filtered:
        if _looks_like_author_line(line) and title_lines:
            break
        title_lines.append(line)
        if len(" ".join(title_lines)) > 180:
            break
    return _clean_title(" ".join(title_lines[:4]))


def _looks_like_author_line(line: str) -> bool:
    if len(line) > 130:
        return False
    comma_count = line.count(",")
    has_initials = bool(re.search(r"\b[A-Z]\.\s*[A-Z]?\.\s*[A-Z][a-z]+", line))
    has_many_capitalized = len(re.findall(r"\b[A-Z][a-z]+(?:-[A-Z][a-z]+)?\b", line)) >= 3
    return comma_count >= 2 or has_initials or (" and " in line.lower() and has_many_capitalized)


def _looks_like_real_title(value: str) -> bool:
    title = _clean_title(value)
    if len(title) < 8:
        return False
    lowered = title.lower()
    if lowered.startswith(("arxiv", "untitled", "microsoft word")):
        return False
    return bool(re.search(r"[A-Za-z]", title))


def _clean_title(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value).replace("\x00", " ")).strip()
    return text.strip(" -:;,.")


def _normalize_title_for_compare(value: str) -> str:
    text = _clean_title(value).lower()
    text = re.sub(r"\barxiv\s*:\s*\S+", " ", text)
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    stopwords = {"the", "a", "an"}
    words = [word for word in text.split() if word not in stopwords]
    return " ".join(words)


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={"User-Agent": "PTagent/0.1 (finite-temperature-potential-extraction)"},
    )


def _emit_progress(progress: ProgressCallback | None, message: str, fraction: float | None = None) -> None:
    if progress is None:
        return
    progress(message, fraction)


def _download_file(
    url: str,
    path: Path,
    *,
    refresh: bool,
    progress: ProgressCallback | None = None,
    label: str = "file",
    start: float = 0.0,
    end: float = 1.0,
) -> None:
    if path.exists() and not refresh and path.stat().st_size > 0:
        _emit_progress(progress, f"Using cache: {label}", end)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    _emit_progress(progress, f"Downloading {label}...", start)
    with urllib.request.urlopen(_request(url), timeout=90) as response:
        total = int(response.headers.get("Content-Length") or 0)
        downloaded = 0
        last_bucket = -1
        with tmp_path.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                downloaded += len(chunk)
                if total > 0:
                    ratio = min(downloaded / total, 1.0)
                    bucket = int(ratio * 10)
                    if bucket != last_bucket:
                        last_bucket = bucket
                        fraction = start + (end - start) * ratio
                        _emit_progress(progress, f"Downloading {label}... {bucket * 10}%", fraction)
                elif downloaded and last_bucket < 0:
                    last_bucket = 0
                    _emit_progress(progress, f"Downloading {label}... receiving data", start)
    tmp_path.replace(path)
    _emit_progress(progress, f"Finished downloading {label}", end)


def _extract_eprint(archive_path: Path, tex_root: Path, *, refresh: bool) -> None:
    if refresh and tex_root.exists():
        shutil.rmtree(tex_root)
    tex_root.mkdir(parents=True, exist_ok=True)
    if any(tex_root.rglob("*.tex")) and not refresh:
        return
    if tarfile.is_tarfile(archive_path):
        with tarfile.open(archive_path) as archive:
            _safe_extract_tar(archive, tex_root)
        return
    if zipfile.is_zipfile(archive_path):
        with zipfile.ZipFile(archive_path) as archive:
            _safe_extract_zip(archive, tex_root)
        return
    data = archive_path.read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    target = tex_root / "source.tex"
    if _looks_like_tex_bytes(data):
        target.write_bytes(data)
    else:
        (tex_root / "source_payload").write_bytes(data)


def _safe_extract_tar(archive: tarfile.TarFile, destination: Path) -> None:
    root = destination.resolve()
    for member in archive.getmembers():
        target = (destination / member.name).resolve()
        if not _is_relative_to(target, root):
            raise ValueError(f"Unsafe path in arXiv source archive: {member.name}")
    try:
        archive.extractall(destination, filter="data")
    except TypeError:
        archive.extractall(destination)


def _safe_extract_zip(archive: zipfile.ZipFile, destination: Path) -> None:
    root = destination.resolve()
    for member in archive.namelist():
        target = (destination / member).resolve()
        if not _is_relative_to(target, root):
            raise ValueError(f"Unsafe path in arXiv source archive: {member}")
    archive.extractall(destination)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _looks_like_tex_bytes(data: bytes) -> bool:
    sample = data[:20000].decode("utf-8", errors="ignore")
    return any(token in sample for token in (r"\documentclass", r"\begin{document}", r"\begin{equation}", r"\section"))


def _find_main_tex(tex_root: Path) -> Path | None:
    candidates = [path for path in tex_root.rglob("*.tex") if path.is_file()]
    if not candidates:
        return None

    def score(path: Path) -> tuple[int, int]:
        text = path.read_text(encoding="utf-8", errors="replace")[:200000]
        value = 0
        value += 100 if r"\documentclass" in text else 0
        value += 60 if r"\begin{document}" in text else 0
        value += 15 if re.search(r"\\(?:input|include)\{", text) else 0
        value += min(path.stat().st_size // 2000, 30)
        return value, path.stat().st_size

    return max(candidates, key=score)


def _tex_context_spans(paper_id: str, tex_text: str, limit: int) -> list[SourceSpan]:
    """Build TeX context spans without doing formula parsing."""

    from .source_reader import _clean_text, _score_text, _split_latex_sections

    chunks = _split_latex_sections(tex_text)
    spans = [
        SourceSpan(
            source_id=f"{paper_id}:texctx{index}",
            page_number=0,
            heading=heading,
            text=_clean_text(body),
            score=_score_text(f"{heading}\n{body}") + 5.0,
            parser="tex_context",
        )
        for index, (heading, body) in enumerate(chunks, start=1)
        if body.strip()
    ]
    spans.sort(key=lambda item: item.score, reverse=True)
    selected = spans[: max(1, limit)]
    selected.sort(key=lambda item: item.source_id)
    return selected


def _find_archive_markdown(archive_root: Path) -> Path | None:
    candidates = [
        path
        for path in archive_root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".md", ".markdown"}
    ]
    if not candidates:
        return None

    def score(path: Path) -> tuple[float, int]:
        from .source_reader import _score_text

        text = path.read_text(encoding="utf-8", errors="replace")[:200000]
        name = path.name.lower()
        value = _score_text(text)
        if re.search(r"paper|main|source|manuscript|ms", name):
            value += 10.0
        if re.search(r"readme|supp|appendix|license", name):
            value -= 4.0
        return value, path.stat().st_size

    return max(candidates, key=score)


def _find_archive_pdf(archive_root: Path) -> Path | None:
    candidates = [
        path
        for path in archive_root.rglob("*")
        if path.is_file() and path.suffix.lower() == ".pdf"
    ]
    if not candidates:
        return None

    def score(path: Path) -> tuple[int, int]:
        name = path.name.lower()
        value = 0
        if re.search(r"paper|main|source|manuscript|ms", name):
            value += 10
        if re.search(r"supp|appendix|figure|fig|table", name):
            value -= 6
        return value, path.stat().st_size

    return max(candidates, key=score)


def _read_archive_fallback_document(
    *,
    paper_id: str,
    selected_path: Path,
    archive_path: Path,
    settings: Settings,
    diagnostics: dict[str, str],
    layer: str,
    progress: ProgressCallback | None,
) -> SourceDocument:
    label = "Markdown" if layer == "markdown_archive" else "PDF"
    _emit_progress(progress, f"Reading uploaded arXiv archive {label} fallback layer", 0.52)
    doc = read_source(selected_path, settings)
    merged_diagnostics = dict(diagnostics)
    merged_diagnostics["primary_reading_layer"] = layer
    merged_diagnostics["selected_source"] = str(selected_path.resolve())
    merged_diagnostics.update({f"{layer}_{key}": value for key, value in doc.diagnostics.items()})
    _emit_progress(progress, "Uploaded arXiv source archive reading complete", 0.62)
    return SourceDocument(
        paper_id=paper_id,
        path=selected_path,
        suffix=".arxiv-archive",
        spans=_retag_spans(doc.spans, old_id=selected_path.stem, new_id=paper_id),
        diagnostics=merged_diagnostics,
        paper_markdown=_archive_fallback_material_markdown(
            paper_id=paper_id,
            archive_path=archive_path,
            selected_path=selected_path,
            layer=layer,
            body=doc.paper_markdown,
        ),
    )


def _expand_tex_file(path: Path, seen: set[Path] | None = None) -> str:
    seen = seen or set()
    resolved = path.resolve()
    if resolved in seen:
        return f"\n% PTAGENT skipped recursive input: {path.name}\n"
    seen.add(resolved)
    text = path.read_text(encoding="utf-8", errors="replace")

    def replace_include(match: re.Match[str]) -> str:
        target = match.group(1).strip()
        if not target or target.startswith("|"):
            return match.group(0)
        target_path = (path.parent / target)
        if target_path.suffix == "":
            target_path = target_path.with_suffix(".tex")
        if not target_path.exists():
            return match.group(0)
        return "\n" + _expand_tex_file(target_path, seen) + "\n"

    return re.sub(r"\\(?:input|include)\{([^}]+)\}", replace_include, text)


def _tex_material_markdown(prepared: ArxivPreparedSource, tex_text: str) -> str:
    lines = [
        f"# TeX Source Material: {prepared.metadata.paper_id}",
        "",
        f"<!-- PTAGENT arxiv_requested: {prepared.metadata.requested_id} -->",
        f"<!-- PTAGENT arxiv_canonical: {prepared.metadata.canonical_id} -->",
        f"<!-- PTAGENT source_priority: tex_source > pdf_fallback -->",
        "",
        f"- Title: {prepared.metadata.title or 'unknown'}",
        f"- Main TeX: `{prepared.main_tex_path}`",
        f"- PDF: `{prepared.pdf_path}`",
        "",
        "```tex",
        tex_text,
        "```",
    ]
    return "\n".join(lines).strip() + "\n"


def _archive_tex_material_markdown(paper_id: str, archive_path: Path, main_tex_path: Path, tex_text: str) -> str:
    lines = [
        f"# Uploaded arXiv Source Material: {paper_id}",
        "",
        f"<!-- PTAGENT source_kind: uploaded_arxiv_source_archive -->",
        f"<!-- PTAGENT source_priority: tex_source_archive > markdown_archive > pdf_archive -->",
        "",
        f"- Archive: `{archive_path}`",
        f"- Main TeX: `{main_tex_path}`",
        "",
        "```tex",
        tex_text,
        "```",
    ]
    return "\n".join(lines).strip() + "\n"


def _archive_fallback_material_markdown(
    *,
    paper_id: str,
    archive_path: Path,
    selected_path: Path,
    layer: str,
    body: str,
) -> str:
    lines = [
        f"# Uploaded arXiv Source Material: {paper_id}",
        "",
        f"<!-- PTAGENT source_kind: uploaded_arxiv_source_archive -->",
        f"<!-- PTAGENT source_priority: tex_source_archive > markdown_archive > pdf_archive -->",
        f"<!-- PTAGENT selected_layer: {layer} -->",
        "",
        f"- Archive: `{archive_path}`",
        f"- Selected source: `{selected_path}`",
        "",
    ]
    if body.strip():
        lines.append(body.strip())
    else:
        lines.append("No projected Markdown layer was available for the selected source.")
    return "\n".join(lines).strip() + "\n"


def _retag_spans(spans: list[SourceSpan], *, old_id: str, new_id: str) -> list[SourceSpan]:
    retagged: list[SourceSpan] = []
    for span in spans:
        source_id = span.source_id
        if source_id.startswith(old_id):
            source_id = new_id + source_id[len(old_id) :]
        retagged.append(
            SourceSpan(
                source_id=source_id,
                page_number=span.page_number,
                heading=span.heading,
                text=span.text,
                score=span.score,
                parser=span.parser,
            )
        )
    return retagged
