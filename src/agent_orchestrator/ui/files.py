"""Root-scoped filesystem browsing for the dashboard (E-Ui7Kq2 FR-B1..FR-B4).

The dashboard deliberately shows **everything** under its configured roots — hidden
dotfiles and binary files included — because the whole point is inspecting a real
workspace, where `.ao/`, `.orchestrator/`, and `.git/` are exactly what an operator needs
to see. What it does NOT do is let a request escape those roots.

Path safety model
-----------------
Every request path is resolved with :meth:`pathlib.Path.resolve`, which collapses ``..``
**and** follows symlinks, and the result must still live under one of the configured roots.
Resolving before comparing is what makes a symlink pointing outside the workspace fail
closed rather than becoming an exfiltration primitive — the same guard
:class:`~agent_orchestrator.artifacts.LocalFsArtifactStore` applies to artifacts, extended
to multiple roots.

Reads are additionally bounded by :data:`MAX_READ_BYTES` so a browser request for a
multi-gigabyte log cannot exhaust server memory.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

# Cap on bytes returned for a single file view. A code/log browser needs enough to be
# useful but must never stream an unbounded file into memory; larger files are returned
# truncated with `truncated=True` so the UI can say so honestly.
MAX_READ_BYTES = 1_000_000

# Bytes sampled from the head of a file when classifying text vs binary. Matches the
# heuristic git uses: a NUL byte in the first block means binary.
BINARY_SNIFF_BYTES = 8192

# Cap on bytes inlined as a `data:` URI for an image preview (E-Tpl3x9 follow-on: dashboard
# file preview). Separate from MAX_READ_BYTES/BINARY_SNIFF_BYTES -- an image is either
# inlined whole or not at all (never a truncated, half-decoded image), so it gets its own,
# larger budget.
IMAGE_INLINE_MAX_BYTES = 5_000_000

# Extension -> MIME allowlist for image classification/inlining. MIME for a response is
# ALWAYS taken from this table (never sniffed, never echoed from the request) -- content
# sniffing only decides *whether* a file is really the image its extension claims to be,
# never what MIME to report.
IMAGE_MIME_BY_EXT: dict[str, str] = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
    "ico": "image/x-icon",
}

# Magic-byte signatures checked against the head of a file before trusting its extension.
# Deliberately per-extension (not "any known image signature") so a `.png` whose bytes are
# actually something else (HTML, say) fails the PNG check specifically rather than being
# accepted because it happens to look like *some* image format. WEBP is handled separately
# (RIFF container + a "WEBP" tag at a fixed offset) since it has no single fixed prefix.
IMAGE_MAGIC_PREFIXES: dict[str, tuple[bytes, ...]] = {
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpg": (b"\xff\xd8\xff",),
    "jpeg": (b"\xff\xd8\xff",),
    "gif": (b"GIF87a", b"GIF89a"),
    "bmp": (b"BM",),
    "ico": (b"\x00\x00\x01\x00",),
}

# Extensions classified as `kind == "markup"`. SVG is deliberately here and NOT in the image
# tables above -- SVG can carry `<script>`, so it must go through the HTML sanitize pipeline
# (see `htmlpreview.py`) rather than ever being inlined as an opaque image `data:` URI.
MARKUP_EXTENSIONS = frozenset({"html", "htm", "svg", "xhtml"})


def _is_image_magic(ext: str, head: bytes) -> bool:
    """Whether *head* starts with the magic bytes for image extension *ext*."""
    if ext == "webp":
        # RIFF <4-byte size> WEBP -- the size field is skipped, only the two fixed tags
        # at offsets 0 and 8 are checked.
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    prefixes = IMAGE_MAGIC_PREFIXES.get(ext)
    if not prefixes:
        return False
    return any(head.startswith(prefix) for prefix in prefixes)


class PathNotAllowedError(Exception):
    """Raised when a requested path resolves outside every configured root."""


class PathNotFoundError(Exception):
    """Raised when a requested path is inside a root but does not exist."""


@dataclass(frozen=True)
class DirEntry:
    """One entry in a directory listing."""

    name: str
    path: str
    """Path relative to the owning root, using forward slashes ('' for the root itself)."""
    root: str
    """Name of the root this entry belongs to."""
    is_dir: bool
    size: int
    modified: float
    is_symlink: bool
    hidden: bool


@dataclass(frozen=True)
class FileContent:
    """Result of reading a single file."""

    path: str
    root: str
    size: int
    is_binary: bool
    truncated: bool
    text: str | None
    """Decoded text, or ``None`` when ``is_binary`` — binary payloads are never inlined."""
    kind: str = "text"
    """One of ``"text" | "image" | "markup" | "binary"`` — see :func:`FileBrowser.read_file`
    for the classification rules. Additive field; existing consumers keying off
    ``is_binary``/``text``/``truncated`` are unaffected."""
    mime: str | None = None
    """Allowlisted MIME type when ``kind == "image"``, else ``None``."""
    data_uri: str | None = None
    """``data:<mime>;base64,<...>`` when ``kind == "image"`` and the file is small enough
    to inline (see ``IMAGE_INLINE_MAX_BYTES``); ``None`` otherwise, including when the
    image is simply too large — the UI falls back to showing size/reason in that case."""


@dataclass(frozen=True)
class Root:
    """A named browsable root directory."""

    name: str
    path: str
    role: str = "workspace"


@dataclass
class FileBrowser:
    """Browses a fixed set of named roots, refusing anything that escapes them.

    Args:
        roots: Browsable roots. The first is treated as the default when a request names
            no root. Roots whose directory does not exist are kept (so the UI can show why
            a configured root is unavailable) but always list as empty.
    """

    roots: list[Root] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Resolve once, up front: every later comparison is then a cheap prefix check
        # against an already-canonical path.
        self.roots = [
            Root(name=r.name, path=str(Path(r.path).resolve()), role=r.role) for r in self.roots
        ]

    def _root_by_name(self, name: str | None) -> Root:
        if not self.roots:
            raise PathNotAllowedError("no browsable roots are configured")
        if name is None:
            return self.roots[0]
        for r in self.roots:
            if r.name == name:
                return r
        raise PathNotAllowedError(f"unknown root: {name}")

    def resolve(self, root_name: str | None, rel_path: str) -> tuple[Root, Path]:
        """Resolve *rel_path* within *root_name* and prove it stays inside that root.

        Args:
            root_name: Name of the root, or ``None`` for the default (first) root.
            rel_path: Path relative to the root. Absolute paths are accepted only when
                they already fall inside the root, which keeps links copied out of the UI
                usable without widening the guard.

        Returns:
            ``(root, absolute_path)``.

        Raises:
            PathNotAllowedError: If the resolved path escapes the root.
        """
        root = self._root_by_name(root_name)
        root_path = Path(root.path)

        cleaned = (rel_path or "").strip().lstrip("/")
        target = Path(rel_path) if os.path.isabs(rel_path or "") else root_path / cleaned

        # resolve() collapses `..` AND follows symlinks, so the containment check below
        # holds for both traversal strings and symlink escapes.
        resolved = target.resolve()

        if resolved != root_path and root_path not in resolved.parents:
            raise PathNotAllowedError(f"path escapes root {root.name!r}: {rel_path}")

        return root, resolved

    def relative(self, root: Root, path: Path) -> str:
        """Return *path* expressed relative to *root*, forward-slashed ('' for the root)."""
        rel = path.relative_to(Path(root.path))
        return "" if str(rel) == "." else rel.as_posix()

    def list_dir(self, root_name: str | None = None, rel_path: str = "") -> list[DirEntry]:
        """List *rel_path* inside *root_name*, including hidden and binary files.

        Directories sort before files, then case-insensitively by name — the ordering a
        file explorer is expected to have.

        Raises:
            PathNotAllowedError: If the path escapes the root.
            PathNotFoundError: If the path does not exist or is not a directory.
        """
        root, resolved = self.resolve(root_name, rel_path)
        if not resolved.exists():
            raise PathNotFoundError(f"no such directory: {rel_path}")
        if not resolved.is_dir():
            raise PathNotFoundError(f"not a directory: {rel_path}")

        entries: list[DirEntry] = []
        with os.scandir(resolved) as it:
            for dirent in it:
                entries.append(self._entry(root, Path(dirent.path), dirent))

        entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
        return entries

    def _entry(self, root: Root, path: Path, dirent: os.DirEntry | None = None) -> DirEntry:
        # A broken symlink still deserves a row in the listing, so every stat is guarded:
        # the UI should show the dangling entry rather than 500 on the whole directory.
        try:
            is_dir = dirent.is_dir(follow_symlinks=True) if dirent else path.is_dir()
        except OSError:
            is_dir = False
        try:
            stat = path.stat()
            size, modified = stat.st_size, stat.st_mtime
        except OSError:
            size, modified = 0, 0.0
        try:
            is_symlink = dirent.is_symlink() if dirent else path.is_symlink()
        except OSError:
            is_symlink = False

        return DirEntry(
            name=path.name,
            path=self.relative(root, path),
            root=root.name,
            is_dir=is_dir,
            size=size,
            modified=modified,
            is_symlink=is_symlink,
            hidden=path.name.startswith("."),
        )

    def read_file(
        self,
        root_name: str | None,
        rel_path: str,
        max_bytes: int = MAX_READ_BYTES,
    ) -> FileContent:
        """Read a file for display, classifying it as text, image, markup, or binary.

        Classification (checked in this order — see module contract):
        - ``image``: extension is in :data:`IMAGE_MIME_BY_EXT` AND the head bytes match
          that extension's magic number (:func:`_is_image_magic`). A mismatched extension
          (e.g. a ``.png`` that is actually HTML) falls through to the next rule instead.
        - ``markup``: extension is in :data:`MARKUP_EXTENSIONS` (``.html``/``.htm``/``.svg``/
          ``.xhtml``). SVG is markup, never image — it can carry ``<script>``.
        - ``binary``: a NUL byte appears in the sniffed head and the file was not
          classified as an image.
        - ``text``: everything else.

        ``is_binary``/``text``/``truncated`` keep their original, pre-existing semantics
        (NUL-byte sniff only) for backward compatibility — ``kind``/``mime``/``data_uri``
        are purely additive. Binary files (including images) return ``text=None`` rather
        than mojibake. Text files are decoded as UTF-8 with ``errors="replace"`` so a file
        with a few bad bytes still renders instead of failing the request.

        Raises:
            PathNotAllowedError: If the path escapes the root.
            PathNotFoundError: If the path does not exist or is a directory.
        """
        root, resolved = self.resolve(root_name, rel_path)
        if not resolved.exists():
            raise PathNotFoundError(f"no such file: {rel_path}")
        if resolved.is_dir():
            raise PathNotFoundError(f"is a directory: {rel_path}")

        ext = resolved.suffix.lower().lstrip(".")
        size = resolved.stat().st_size
        with open(resolved, "rb") as fh:
            head = fh.read(min(max_bytes, BINARY_SNIFF_BYTES))
            is_binary = b"\x00" in head

            if ext in IMAGE_MIME_BY_EXT and _is_image_magic(ext, head):
                return self._read_image(root, resolved, ext, size, head, fh)

            if ext in MARKUP_EXTENSIONS:
                kind = "markup"
            elif is_binary:
                kind = "binary"
            else:
                kind = "text"

            if is_binary:
                return FileContent(
                    path=self.relative(root, resolved),
                    root=root.name,
                    size=size,
                    is_binary=True,
                    truncated=size > max_bytes,
                    text=None,
                    kind=kind,
                )
            rest = fh.read(max(0, max_bytes - len(head)))

        raw = head + rest
        return FileContent(
            path=self.relative(root, resolved),
            root=root.name,
            size=size,
            is_binary=False,
            truncated=size > len(raw),
            text=raw.decode("utf-8", errors="replace"),
            kind=kind,
        )

    def _read_image(
        self,
        root: Root,
        resolved: Path,
        ext: str,
        size: int,
        head: bytes,
        fh: BinaryIO,
    ) -> FileContent:
        """Finish reading a confirmed image file, inlining it if it fits the cap.

        Split out of :meth:`read_file` purely to keep that method's branching readable;
        not part of the public contract.
        """
        mime = IMAGE_MIME_BY_EXT[ext]
        data_uri: str | None = None
        if size <= IMAGE_INLINE_MAX_BYTES:
            # `fh` is still open and positioned right after `head` — read the rest of the
            # (capped-size) file so the whole thing can be inlined as one data: URI. Never
            # inline a partial image: if for some reason fewer bytes came back than `size`
            # promised, leave data_uri unset rather than ship a corrupt image.
            rest = fh.read(IMAGE_INLINE_MAX_BYTES - len(head))
            raw = head + rest
            if len(raw) >= size:
                encoded = base64.b64encode(raw[:size]).decode("ascii")
                data_uri = f"data:{mime};base64,{encoded}"

        return FileContent(
            path=self.relative(root, resolved),
            root=root.name,
            size=size,
            is_binary=True,
            truncated=data_uri is None,
            text=None,
            kind="image",
            mime=mime,
            data_uri=data_uri,
        )
