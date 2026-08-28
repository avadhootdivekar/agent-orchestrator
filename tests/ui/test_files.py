"""Unit tests for the dashboard file browser (E-Ui7Kq2 FR-B1..FR-B4).

Two themes: it must show EVERYTHING inside a root (hidden files, binaries, symlinks,
dangling entries), and it must show NOTHING outside one.
"""

from __future__ import annotations

import base64
import os
import struct
import zlib
from pathlib import Path

import pytest

from agent_orchestrator.ui.files import (
    BINARY_SNIFF_BYTES,
    IMAGE_INLINE_MAX_BYTES,
    FileBrowser,
    PathNotAllowedError,
    PathNotFoundError,
    Root,
)


def _make_png_bytes(width: int = 2, height: int = 2) -> bytes:
    """A minimal, valid, single-color PNG — real magic bytes, not a placeholder."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    raw = (b"\x00" + b"\xff\x00\x00" * width) * height
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw))
    png += chunk(b"IEND", b"")
    return png


@pytest.fixture()
def browser(tmp_path: Path) -> FileBrowser:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# readme\n", encoding="utf-8")
    (tmp_path / ".hidden").write_text("dotfile\n", encoding="utf-8")
    (tmp_path / ".ao").mkdir()
    (tmp_path / "image.bin").write_bytes(b"\x89PNG\x00\x01\x02binary")
    return FileBrowser(roots=[Root(name="workspace", path=str(tmp_path))])


class TestListing:
    def test_lists_hidden_files_and_directories(self, browser: FileBrowser) -> None:
        names = {e.name for e in browser.list_dir("workspace", "")}
        assert ".hidden" in names, "hidden files must be visible — inspecting them is the point"
        assert ".ao" in names

    def test_flags_hidden_entries_so_the_ui_can_dim_them(self, browser: FileBrowser) -> None:
        entries = {e.name: e for e in browser.list_dir("workspace", "")}
        assert entries[".hidden"].hidden is True
        assert entries["README.md"].hidden is False

    def test_lists_binary_files_alongside_text(self, browser: FileBrowser) -> None:
        names = {e.name for e in browser.list_dir("workspace", "")}
        assert "image.bin" in names

    def test_directories_sort_before_files_case_insensitively(self, browser: FileBrowser) -> None:
        entries = browser.list_dir("workspace", "")
        dir_flags = [e.is_dir for e in entries]
        assert dir_flags == sorted(dir_flags, reverse=True)
        dir_names = [e.name for e in entries if e.is_dir]
        assert dir_names == sorted(dir_names, key=str.lower)

    def test_descends_into_subdirectories(self, browser: FileBrowser) -> None:
        entries = browser.list_dir("workspace", "src")
        assert [e.name for e in entries] == ["main.py"]
        assert entries[0].path == "src/main.py"

    def test_reports_size_and_mtime(self, browser: FileBrowser, tmp_path: Path) -> None:
        entry = next(e for e in browser.list_dir("workspace", "") if e.name == "README.md")
        assert entry.size == (tmp_path / "README.md").stat().st_size
        assert entry.modified > 0

    def test_missing_directory_raises_not_found(self, browser: FileBrowser) -> None:
        with pytest.raises(PathNotFoundError):
            browser.list_dir("workspace", "nope")

    def test_listing_a_file_raises_not_found(self, browser: FileBrowser) -> None:
        with pytest.raises(PathNotFoundError):
            browser.list_dir("workspace", "README.md")


class TestPathGuard:
    """The security boundary: nothing outside a configured root is reachable."""

    @pytest.mark.parametrize(
        "escape",
        [
            "..",
            "../..",
            "../../etc",
            "src/../../..",
            "/etc",
            "/etc/passwd",
        ],
    )
    def test_traversal_attempts_are_refused(self, browser: FileBrowser, escape: str) -> None:
        with pytest.raises(PathNotAllowedError):
            browser.list_dir("workspace", escape)

    def test_symlink_pointing_outside_the_root_is_refused(self, tmp_path: Path) -> None:
        # resolve() follows symlinks, so a link out of the workspace must fail the
        # containment check rather than become an arbitrary-file read.
        root = tmp_path / "ws"
        outside = tmp_path / "outside"
        root.mkdir()
        outside.mkdir()
        (outside / "secret.txt").write_text("classified", encoding="utf-8")
        os.symlink(outside / "secret.txt", root / "escape.txt")

        browser = FileBrowser(roots=[Root(name="workspace", path=str(root))])
        with pytest.raises(PathNotAllowedError):
            browser.read_file("workspace", "escape.txt")

    def test_symlink_inside_the_root_is_allowed(self, tmp_path: Path) -> None:
        root = tmp_path / "ws"
        root.mkdir()
        (root / "real.txt").write_text("fine", encoding="utf-8")
        os.symlink(root / "real.txt", root / "link.txt")

        browser = FileBrowser(roots=[Root(name="workspace", path=str(root))])
        assert browser.read_file("workspace", "link.txt").text == "fine"

    def test_dangling_symlink_still_appears_in_the_listing(self, tmp_path: Path) -> None:
        root = tmp_path / "ws"
        root.mkdir()
        os.symlink(root / "missing-target", root / "broken")

        browser = FileBrowser(roots=[Root(name="workspace", path=str(root))])
        entries = {e.name: e for e in browser.list_dir("workspace", "")}
        assert "broken" in entries, "a broken link must not blow up the whole directory"
        assert entries["broken"].is_symlink is True

    def test_unknown_root_is_refused(self, browser: FileBrowser) -> None:
        with pytest.raises(PathNotAllowedError):
            browser.list_dir("nonexistent-root", "")

    def test_no_roots_configured_refuses_everything(self) -> None:
        with pytest.raises(PathNotAllowedError):
            FileBrowser(roots=[]).list_dir(None, "")

    def test_absolute_path_inside_the_root_is_accepted(
        self, browser: FileBrowser, tmp_path: Path
    ) -> None:
        content = browser.read_file("workspace", str(tmp_path / "README.md"))
        assert content.text == "# readme\n"


class TestMultipleRoots:
    def test_first_root_is_the_default(self, tmp_path: Path) -> None:
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir()
        b.mkdir()
        (a / "in-a.txt").write_text("a", encoding="utf-8")
        (b / "in-b.txt").write_text("b", encoding="utf-8")

        browser = FileBrowser(roots=[Root(name="a", path=str(a)), Root(name="b", path=str(b))])
        assert [e.name for e in browser.list_dir(None, "")] == ["in-a.txt"]
        assert [e.name for e in browser.list_dir("b", "")] == ["in-b.txt"]

    def test_a_root_cannot_read_a_sibling_root(self, tmp_path: Path) -> None:
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir()
        b.mkdir()
        (b / "in-b.txt").write_text("b", encoding="utf-8")

        browser = FileBrowser(roots=[Root(name="a", path=str(a)), Root(name="b", path=str(b))])
        with pytest.raises(PathNotAllowedError):
            browser.read_file("a", "../b/in-b.txt")


class TestReadFile:
    def test_reads_text(self, browser: FileBrowser) -> None:
        content = browser.read_file("workspace", "src/main.py")
        assert content.text == "print('hi')\n"
        assert content.is_binary is False
        assert content.truncated is False

    def test_classifies_nul_bearing_files_as_binary_without_inlining_them(
        self, browser: FileBrowser
    ) -> None:
        content = browser.read_file("workspace", "image.bin")
        assert content.is_binary is True
        assert content.text is None, "binary payloads must never be inlined as mojibake"
        assert content.size > 0

    def test_truncates_large_files_and_says_so(self, tmp_path: Path) -> None:
        big = tmp_path / "big.log"
        big.write_text("x" * 5000, encoding="utf-8")
        browser = FileBrowser(roots=[Root(name="workspace", path=str(tmp_path))])

        content = browser.read_file("workspace", "big.log", max_bytes=1000)
        assert content.truncated is True
        assert content.text is not None
        assert len(content.text) == 1000
        assert content.size == 5000

    def test_binary_detection_only_sniffs_the_head(self, tmp_path: Path) -> None:
        # A NUL past the sniff window is not detected — documented heuristic (same as git),
        # asserted so a change in the constant is a deliberate decision, not a surprise.
        path = tmp_path / "late-nul.bin"
        path.write_bytes(b"a" * (BINARY_SNIFF_BYTES + 10) + b"\x00")
        browser = FileBrowser(roots=[Root(name="workspace", path=str(tmp_path))])
        assert browser.read_file("workspace", "late-nul.bin").is_binary is False

    def test_invalid_utf8_is_replaced_rather_than_failing(self, tmp_path: Path) -> None:
        path = tmp_path / "latin1.txt"
        path.write_bytes("café".encode("latin-1"))  # invalid as UTF-8, but no NUL
        browser = FileBrowser(roots=[Root(name="workspace", path=str(tmp_path))])

        content = browser.read_file("workspace", "latin1.txt")
        assert content.is_binary is False
        assert content.text is not None and "caf" in content.text

    def test_missing_file_raises_not_found(self, browser: FileBrowser) -> None:
        with pytest.raises(PathNotFoundError):
            browser.read_file("workspace", "nope.txt")

    def test_reading_a_directory_raises_not_found(self, browser: FileBrowser) -> None:
        with pytest.raises(PathNotFoundError):
            browser.read_file("workspace", "src")

    def test_empty_file_reads_as_empty_text(self, tmp_path: Path) -> None:
        (tmp_path / "empty.txt").write_text("", encoding="utf-8")
        browser = FileBrowser(roots=[Root(name="workspace", path=str(tmp_path))])
        content = browser.read_file("workspace", "empty.txt")
        assert content.text == ""
        assert content.is_binary is False


class TestClassification:
    """`kind`/`mime`/`data_uri` (1A.1) — additive fields on top of the pre-existing
    `is_binary`/`text`/`truncated` contract, which the tests above still exercise
    unchanged."""

    def test_plain_text_file_is_kind_text(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("print(1)\n", encoding="utf-8")
        browser = FileBrowser(roots=[Root(name="workspace", path=str(tmp_path))])
        content = browser.read_file("workspace", "a.py")
        assert content.kind == "text"
        assert content.mime is None
        assert content.data_uri is None

    def test_nul_bearing_non_image_file_is_kind_binary(self, tmp_path: Path) -> None:
        (tmp_path / "blob.dat").write_bytes(b"\x00\x01\x02")
        browser = FileBrowser(roots=[Root(name="workspace", path=str(tmp_path))])
        content = browser.read_file("workspace", "blob.dat")
        assert content.kind == "binary"
        assert content.is_binary is True
        assert content.text is None

    @pytest.mark.parametrize("ext", ["html", "htm", "svg", "xhtml"])
    def test_markup_extensions_are_kind_markup(self, tmp_path: Path, ext: str) -> None:
        path = tmp_path / f"page.{ext}"
        path.write_text("<p>hi</p>", encoding="utf-8")
        browser = FileBrowser(roots=[Root(name="workspace", path=str(tmp_path))])
        content = browser.read_file("workspace", path.name)
        assert content.kind == "markup"

    def test_svg_with_script_is_still_kind_markup_not_image(self, tmp_path: Path) -> None:
        path = tmp_path / "evil.svg"
        path.write_text("<svg><script>alert(1)</script></svg>", encoding="utf-8")
        browser = FileBrowser(roots=[Root(name="workspace", path=str(tmp_path))])
        content = browser.read_file("workspace", "evil.svg")
        assert content.kind == "markup"
        assert content.mime is None
        assert content.data_uri is None

    def test_real_png_is_classified_as_image_and_inlined(self, tmp_path: Path) -> None:
        png_bytes = _make_png_bytes()
        (tmp_path / "pic.png").write_bytes(png_bytes)
        browser = FileBrowser(roots=[Root(name="workspace", path=str(tmp_path))])
        content = browser.read_file("workspace", "pic.png")

        assert content.kind == "image"
        assert content.mime == "image/png"
        assert content.is_binary is True
        assert content.text is None
        assert content.data_uri is not None
        prefix = "data:image/png;base64,"
        assert content.data_uri.startswith(prefix)
        decoded = base64.b64decode(content.data_uri[len(prefix) :])
        assert decoded == png_bytes

    def test_png_containing_html_bytes_is_not_classified_as_image(self, tmp_path: Path) -> None:
        # The core anti-spoofing case: a mismatched extension must not be trusted just
        # because the request says ".png".
        (tmp_path / "fake.png").write_text("<html><body>not a png</body></html>", encoding="utf-8")
        browser = FileBrowser(roots=[Root(name="workspace", path=str(tmp_path))])
        content = browser.read_file("workspace", "fake.png")
        assert content.kind != "image"
        assert content.data_uri is None
        assert content.mime is None

    def test_oversized_image_is_still_kind_image_but_not_inlined(self, tmp_path: Path) -> None:
        # Real PNG magic bytes, but padded (via a large IDAT/garbage tail is unnecessary --
        # the size check happens before any content validation past the header) past the
        # inline cap.
        png_bytes = _make_png_bytes()
        padded = png_bytes + b"\x00" * (IMAGE_INLINE_MAX_BYTES + 1 - len(png_bytes))
        (tmp_path / "big.png").write_bytes(padded)
        browser = FileBrowser(roots=[Root(name="workspace", path=str(tmp_path))])
        content = browser.read_file("workspace", "big.png")

        assert content.kind == "image"
        assert content.data_uri is None
        assert content.truncated is True
        assert content.size == len(padded)

    def test_webp_magic_bytes_are_recognized(self, tmp_path: Path) -> None:
        # RIFF <size> WEBP -- minimal container header, no need for a real VP8 payload
        # since only the two fixed tags are checked.
        head = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"more-bytes-after-the-header"
        (tmp_path / "pic.webp").write_bytes(head)
        browser = FileBrowser(roots=[Root(name="workspace", path=str(tmp_path))])
        content = browser.read_file("workspace", "pic.webp")
        assert content.kind == "image"
        assert content.mime == "image/webp"

    def test_jpeg_extension_maps_to_the_jpeg_mime(self, tmp_path: Path) -> None:
        jpeg_bytes = b"\xff\xd8\xff" + b"\x00" * 20
        (tmp_path / "x.jpg").write_bytes(jpeg_bytes)
        browser = FileBrowser(roots=[Root(name="workspace", path=str(tmp_path))])
        content = browser.read_file("workspace", "x.jpg")
        assert content.mime == "image/jpeg"
