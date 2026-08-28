"""Sanitizing HTML/SVG preview pipeline for the dashboard file viewer.

THREAT MODEL (see also `files.py`'s module docstring): the workspace this dashboard
browses is where AI agents write files, and benchmark tiers import third-party repos into
it. Workspace `.html`/`.svg` content is therefore attacker-controlled, and the dashboard
has no authentication. Previewed markup must never execute in the dashboard's origin and
must have no network egress channel — that is the design constraint behind every rule
below, not just defense against a hypothetical.

Design: parse with stdlib `html.parser.HTMLParser` and re-serialize deliberately, never
regex-strip the source string (that class of "sanitizer" is trivially bypassed by malformed
markup, e.g. `<scr<script>ipt>`, comment tricks, or broken attribute quoting). CSS *is*
processed with regex (`_process_stylesheet_css` et al.) — there is no stdlib CSS parser and
adding one is out of scope; CSS has no comparable "tag soup" ambiguity that would make
regex substitution unsafe here.

Element policy is a BLOCKLIST (`DROPPED_ELEMENTS_WITH_SUBTREE`): the handful of genuinely
dangerous elements are dropped with their entire subtree, and script content is counted.
Attribute policy is a mix of a hard blocklist (`HARD_DROP_ATTRS`, plus any `on*` handler)
and special processing for URL-bearing attributes; everything else passes through, escaped.
This is a deliberate reading of the contract's "drop these" rule lists (as opposed to a
strict keep-only allowlist) — see the design note by the reviewer's contract for the exact
rules this implements.

Every text node AND every attribute value is escaped with `html.escape(..., quote=True)` on
re-serialization — this is the exact bug class recorded in `meta/learnings.md` ("template
render escaping/containment"): a sanitizer that forgets to escape on the way OUT is not a
sanitizer.
"""

from __future__ import annotations

import base64
import html
import re
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import SplitResult, unquote, urlsplit

from .files import (
    IMAGE_MIME_BY_EXT,
    MARKUP_EXTENSIONS,
    MAX_READ_BYTES,
    FileBrowser,
    PathNotAllowedError,
    PathNotFoundError,
    Root,
)

# -- budgets & limits (named constants; see contract 1A.2) ---------------------------

# Total bytes across every asset (image or stylesheet text) inlined into one preview.
HTML_INLINE_BUDGET_BYTES = 10_000_000

# Per-asset cap. An asset over this is dropped whole rather than truncated mid-stream — a
# half-written image/stylesheet is worse than a missing one.
HTML_ASSET_MAX_BYTES = 5_000_000

# Max `@import` nesting depth before a chain is refused (with a visited-set on top, so a
# genuine cycle terminates rather than recursing forever even inside the depth budget).
HTML_MAX_IMPORT_DEPTH = 3

# How much of an over-long ref URL survives into a DroppedRef for display.
DROPPED_REF_URL_MAX_CHARS = 200

# Font extensions, merged with `files.IMAGE_MIME_BY_EXT` to form the allowlist for assets
# inlined here (images AND fonts can legitimately appear via `src`/`srcset`/CSS `url()`).
_FONT_MIME_BY_EXT: dict[str, str] = {
    "woff": "font/woff",
    "woff2": "font/woff2",
    "ttf": "font/ttf",
    "otf": "font/otf",
    "eot": "application/vnd.ms-fontobject",
}
HTML_ASSET_MIME_BY_EXT: dict[str, str] = {**IMAGE_MIME_BY_EXT, **_FONT_MIME_BY_EXT}

# MIME allowlist for *existing* `data:` URIs already present in the source (contract:
# "Existing data: URIs -> keep only if the MIME is in the image/font allowlist"). Reuses
# the same table as inlined assets. `image/svg+xml` is deliberately absent — SVG is markup
# everywhere in this pipeline, never treated as an opaque image, including inside a data URI
# (a `data:image/svg+xml,<svg onload=...>` navigated to directly executes its script; we
# would rather lose the rare legitimate inline SVG data URI than open that door).
HTML_DATA_URI_MIME_ALLOWLIST = frozenset(HTML_ASSET_MIME_BY_EXT.values())

# Elements dropped entirely, subtree included. `script` is here too (and is what drives
# `scripts_removed`) — it gets no special-case beyond incrementing that counter.
#
# SMIL animation elements (H1, security audit finding): `<animate>`/`<set>`/
# `<animateTransform>`/`<animateMotion>`/`<discard>` can retarget ANY attribute
# (`attributeName="href"`, `attributeName="src"`, ...) to an ATTACKER-CHOSEN VALUE at
# render time, on a timer, with no user interaction required beyond the page rendering.
# This defeats `<a href>`/`<image href>` stripping entirely: the emitted markup has no
# live `href`, but `<animate attributeName="href" values="http://attacker.example/...">`
# inside the SAME element retargets it live in the renderer. Confirmed in real headless
# Chrome: a synthetic click on the animated `<a>` made the sandboxed iframe navigate
# (self-navigate — sandboxed iframes without `allow-top-navigation` can still navigate
# THEMSELVES, just not other frames) to the attacker URL, with the request observed on a
# listener. That is a live one-click network-egress/malicious-redirect channel, straight
# through the "no egress channel" invariant this whole module exists to hold. There is no
# legitimate use for live attribute retargeting in a static preview, so these are dropped
# outright — no functionality tradeoff, no special-case attribute allowlisting needed for
# them specifically (unlike `href`/`src`, which have real static content to inline).
DROPPED_ELEMENTS_WITH_SUBTREE = frozenset(
    {
        "script",
        "iframe",
        "frame",
        "frameset",
        "object",
        "embed",
        "applet",
        "base",
        "form",
        "noscript",
        "animate",
        "animatetransform",
        "animatemotion",
        "set",
        "discard",
    }
)

# Void (self-closing, no matching end tag) HTML elements. Matters two ways here: (1) we
# must not wait for an end tag that will never come before emitting one on serialization,
# and (2) `base`/`embed` are *dropped* elements that are ALSO void — for those we must drop
# the single tag and NOT enter subtree-skip mode (which would wait forever for a `</base>`
# that a void element never has, silently eating the rest of the document).
VOID_ELEMENTS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)

# Attributes dropped unconditionally, regardless of element or value. `xlink:href` is kept
# here explicitly for documentation even though the generic `name_l.endswith(":href")`
# check in `_render_attrs` (L4, security audit) already covers it and any rebound-prefix
# variant (`foo:href` after `xmlns:foo=".../xlink"`) — the name is what security-audit
# reviewers (and the contract this module implements) will grep for.
HARD_DROP_ATTRS = frozenset(
    {
        "srcdoc",
        "formaction",
        "xlink:href",
        "action",
        "background",
        "dynsrc",
        "lowsrc",
        "ping",
        "http-equiv",
    }
)

# URL-bearing attributes resolved/inlined like an image asset. `href` is handled separately
# (special-cased per element: stripped-and-noted on `<a>`, drives stylesheet inlining on
# `<link>`, and treated the same as `src` everywhere else, e.g. SVG `<use href>`/`<image
# href>` — the contract's "no network egress channel" rule would otherwise have a hole
# there). `srcset` is handled separately too (multi-value).
URL_ATTRS_TO_INLINE = frozenset({"src", "poster"})

_CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE)
_CSS_IMPORT_RE = re.compile(
    r"@import\s+(?:url\(\s*(['\"]?)(.*?)\1\s*\)|(['\"])(.*?)\3)[^;]*;?",
    re.IGNORECASE,
)
_CSS_DANGEROUS_TOKEN_RE = re.compile(
    r"expression\s*\(|behavior\s*:|-moz-binding\s*:?", re.IGNORECASE
)
_CSS_IMPORT_TOKEN_RE = re.compile(r"@import", re.IGNORECASE)
# L1 (security audit): CSS comments are stripped before dangerous-token matching, not
# after -- `width:exp/**/ression(alert(1))` and `-moz/**/-binding:url(...)` both defeat
# `_CSS_DANGEROUS_TOKEN_RE` if the comment is still present when it runs.
_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
# M1 (security audit): `image-set()`/`-webkit-image-set()` is a distinct CSS <image> value
# from `url(...)` and was bypassing the URL pipeline entirely. Only the function's OPENING
# paren is matched here -- `_process_image_set` finds the matching close paren itself
# (parens can nest, e.g. `image-set(url(a.png) 1x, ...)`), which a single regex can't do
# reliably.
_CSS_IMAGE_SET_FN_RE = re.compile(r"(?:-webkit-)?image-set\(", re.IGNORECASE)
_CSS_IMAGE_SET_CANDIDATE_URL_FORM_RE = re.compile(
    r"^\s*url\(\s*(['\"]?)(.*?)\1\s*\)(?P<rest>.*)$", re.IGNORECASE | re.DOTALL
)
_CSS_IMAGE_SET_CANDIDATE_STRING_FORM_RE = re.compile(r"^\s*(['\"])(.*?)\1(?P<rest>.*)$", re.DOTALL)


@dataclass(frozen=True)
class DroppedRef:
    """One reference the sanitizer would not inline, kept for honest disclosure to the UI."""

    url: str
    """The original ref, truncated to :data:`DROPPED_REF_URL_MAX_CHARS` for display safety
    (an attacker-controlled ref should not itself become an unbounded response field)."""
    reason: str
    """One of ``"external" | "outside-root" | "not-found" | "too-large" |
    "budget-exhausted" | "unsupported-scheme" | "depth-exceeded"``."""


@dataclass(frozen=True)
class HtmlPreview:
    """Sanitized, self-contained HTML safe to drop into a sandboxed `iframe srcdoc`."""

    path: str
    root: str
    html: str
    inlined: int
    dropped: list[DroppedRef]
    scripts_removed: int
    truncated: bool
    """Whether the SOURCE html read hit :data:`~agent_orchestrator.ui.files.MAX_READ_BYTES`."""
    budget_bytes: int
    budget_used: int


class NotMarkupError(Exception):
    """Raised when `/api/files/html` is asked to preview a non-markup file (-> 400)."""


def _truncate(url: str) -> str:
    return url[:DROPPED_REF_URL_MAX_CHARS]


def _strip_ws_control(value: str) -> str:
    """Strip every whitespace AND C0/DEL control character, anywhere in the string.

    Not just the ends: the documented bypass this defends against is a scheme with a
    control character injected *inside* it, e.g. ``java\\tscript:alert(1)`` — stripped,
    that becomes ``javascript:alert(1)``, which the scheme check below then correctly
    rejects. Must run BEFORE scheme detection.
    """
    return "".join(ch for ch in value if not ch.isspace() and 0x20 <= ord(ch) != 0x7F)


class _Counter:
    """A plain int would be copied (not shared) by `dataclasses.replace()`; this isn't."""

    def __init__(self) -> None:
        self.value = 0

    def inc(self, n: int = 1) -> None:
        self.value += n


@dataclass
class _Budget:
    limit: int
    used: int = 0

    def try_reserve(self, n: int) -> bool:
        if self.used + n > self.limit:
            return False
        self.used += n
        return True


@dataclass
class _Context:
    """Shared state threaded through one preview build.

    `budget`/`dropped`/`inlined` are mutable objects held by reference, so
    `dataclasses.replace()` (used to descend into an imported stylesheet's own directory)
    keeps them shared across the copy — only `html_dir` is meant to vary per nesting level.
    """

    browser: FileBrowser
    root: Root
    root_name: str
    root_path: Path
    html_dir: Path
    budget: _Budget
    dropped: list[DroppedRef]
    inlined: _Counter


def _split_url(raw_url: str) -> tuple[str, SplitResult]:
    cleaned = _strip_ws_control(raw_url)
    return cleaned, urlsplit(cleaned)


def _classify_and_locate(
    cleaned: str, parsed: SplitResult, ctx: _Context
) -> tuple[Path | None, str | None]:
    """Resolve a non-``data:`` ref to an in-root path, or a drop reason.

    Reuses `FileBrowser.resolve`'s containment guard (resolve() then prefix-check) rather
    than a second path resolver — this function only computes WHICH candidate path to hand
    that guard, for both relative refs (resolved against the HTML file's own directory) and
    in-root-absolute refs (resolved against the root itself).
    """
    if cleaned.startswith("//") or cleaned.startswith("\\\\"):
        # Protocol-relative (`//host/...`) and UNC-style (`\\host\...`) refs are both,
        # functionally, a fetch of another host — treated the same as http(s).
        return None, "external"

    scheme = parsed.scheme.lower()
    if scheme not in ("",):
        return None, ("external" if scheme in ("http", "https") else "unsupported-scheme")
    if parsed.netloc:
        return None, "external"

    raw_path = unquote(parsed.path)
    if "\x00" in raw_path:
        # No dedicated reason in the enum for this; closest fit is "there is nothing valid
        # here to resolve to".
        return None, "not-found"

    candidate = (
        ctx.root_path / raw_path.lstrip("/")
        if raw_path.startswith("/")
        else ctx.html_dir / raw_path
    )
    try:
        _, resolved = ctx.browser.resolve(ctx.root_name, str(candidate))
    except PathNotAllowedError:
        return None, "outside-root"
    if not resolved.is_file():
        return None, "not-found"
    return resolved, None


def _data_uri_mime(data_url: str) -> str | None:
    if not data_url.lower().startswith("data:"):
        return None
    header = data_url[5:].split(",", 1)[0]
    mime = header.split(";", 1)[0].strip().lower()
    return mime or None


def _resolve_binary_asset(raw_url: str, ctx: _Context) -> tuple[str | None, str | None]:
    """Resolve *raw_url* to a `data:` URI, or ``(None, reason)`` if it must be dropped."""
    cleaned, parsed = _split_url(raw_url)

    if parsed.scheme.lower() == "data":
        mime = _data_uri_mime(cleaned)
        if mime in HTML_DATA_URI_MIME_ALLOWLIST:
            # Already inline in the source; not something *we* read/inlined, so it is not
            # counted against the inline budget or the `inlined` counter.
            return cleaned, None
        return None, "unsupported-scheme"

    resolved, reason = _classify_and_locate(cleaned, parsed, ctx)
    if reason is not None or resolved is None:
        return None, reason
    size = resolved.stat().st_size
    if size > HTML_ASSET_MAX_BYTES:
        return None, "too-large"
    ext = resolved.suffix.lower().lstrip(".")
    mime = HTML_ASSET_MIME_BY_EXT.get(ext)
    if mime is None:
        # Extension-only lookup (never sniffed) — an asset of a type we don't recognize as
        # an image/font is treated the same as "nothing usable here", not inlined blind.
        return None, "not-found"
    if not ctx.budget.try_reserve(size):
        return None, "budget-exhausted"
    data = resolved.read_bytes()
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}", None


def _resolve_stylesheet_text(
    raw_url: str, ctx: _Context, depth: int, visited: frozenset[str]
) -> tuple[str | None, str | None]:
    """Resolve *raw_url* (a `<link rel=stylesheet>` href or `@import` target) to processed
    CSS text, or ``(None, reason)``."""
    cleaned, parsed = _split_url(raw_url)
    if parsed.scheme.lower() == "data":
        return None, "unsupported-scheme"

    resolved, reason = _classify_and_locate(cleaned, parsed, ctx)
    if reason is not None or resolved is None:
        return None, reason

    key = str(resolved)
    if key in visited:
        # Import cycle: terminate quietly by contributing empty content, rather than
        # re-descending into a file already on the current import chain.
        return "", None

    size = resolved.stat().st_size
    if size > HTML_ASSET_MAX_BYTES:
        return None, "too-large"
    if not ctx.budget.try_reserve(size):
        return None, "budget-exhausted"

    text = resolved.read_text(encoding="utf-8", errors="replace")
    sub_ctx = replace(ctx, html_dir=resolved.parent)
    processed = _process_stylesheet_css(text, sub_ctx, depth=depth + 1, visited=visited | {key})
    return processed, None


def _parse_srcset(value: str) -> list[tuple[str, str]]:
    """Parse a `srcset` attribute value into `(url, descriptor)` candidates.

    A simplified version of the WHATWG "parse a srcset attribute" algorithm
    (https://html.spec.whatwg.org/multipage/images.html#parsing-a-srcset-attribute).

    L2 fix (security audit): the URL token is delimited by WHITESPACE, not commas — a
    naive `value.split(",")` mangles a `data:image/png;base64,<payload>` candidate at its
    own mandatory comma, truncating it into a broken fragment (not a scheme bypass, just a
    broken image, but still wrong). Per spec, a comma only ends a candidate either (a)
    immediately after the URL token when there is no descriptor (the comma got swept into
    the whitespace-delimited token and is stripped back off), or (b) after a separately
    parsed descriptor. A `data:` URI's internal comma is never adjacent to whitespace, so
    it never gets treated as either of those boundaries.
    """
    candidates: list[tuple[str, str]] = []
    i = 0
    n = len(value)
    while i < n:
        while i < n and (value[i].isspace() or value[i] == ","):
            i += 1
        if i >= n:
            break
        start = i
        while i < n and not value[i].isspace():
            i += 1
        url = value[start:i]
        stripped = url.rstrip(",")
        if stripped != url:
            # The URL token swept up its own terminating comma(s) -- no descriptor.
            if stripped:
                candidates.append((stripped, ""))
            continue
        while i < n and value[i].isspace():
            i += 1
        desc_start = i
        while i < n and value[i] != ",":
            i += 1
        descriptor = value[desc_start:i].strip()
        candidates.append((url, descriptor))
        i += 1  # skip the candidate-terminating comma
    return candidates


def _strip_css_comments(text: str) -> str:
    """Remove `/* ... */` CSS comments — MUST run before any dangerous-token or URL
    matching (L1, security audit): a comment split inside a token, e.g.
    `width:exp/**/ression(alert(1))` or `-moz/**/-binding:url(...)`, defeats
    `_CSS_DANGEROUS_TOKEN_RE` if the comment is still present when it runs. Not exploitable
    in any current mainstream browser (`expression()` is IE-only; `-moz-binding` was
    removed from Firefox in 2022) but it's the same blocklist-vs-obfuscation pattern as
    every other rule here, so it gets closed the same way.
    """
    return _CSS_COMMENT_RE.sub("", text)


def _strip_dangerous_css_tokens(text: str, *, strip_import: bool) -> str:
    """Neutralize legacy CSS XSS vectors (`expression()`, `behavior:`, `-moz-binding`).

    `strip_import` additionally removes literal `@import` text — used for the `style="..."`
    ATTRIBUTE context, where `@import` has no effect anyway (only valid at the top of a
    stylesheet) and is simply removed defensively. `<style>` TAG / stylesheet content must
    NOT strip it here — that content's `@import` statements are deliberately resolved by
    `_process_stylesheet_css`, not neutralized.
    """
    text = _CSS_DANGEROUS_TOKEN_RE.sub("", text)
    if strip_import:
        text = _CSS_IMPORT_TOKEN_RE.sub("", text)
    return text


def _escape_style_content(css_text: str) -> str:
    """Guard against a `<style>` tag's own text content smuggling a premature `</style>`.

    Attribute values go through `html.escape(..., quote=True)` and are safe by construction.
    `<style>` TEXT CONTENT is emitted close to verbatim (CSS, not escaped HTML), so if the
    CSS text itself contains the literal substring `</style` — e.g. inside a `content:
    "</style><script>..."` declaration — re-embedding it verbatim would let the browser's
    tokenizer close our `<style>` early and parse the rest as raw markup. This is exactly
    the escaping/containment bug class in `meta/learnings.md`; the fix is a targeted,
    conservative rewrite of every `</` (not just ones followed by "style") so no closing
    sequence can ever appear in emitted style text.
    """
    return css_text.replace("</", "<\\/")


def _find_matching_paren(text: str, open_idx: int) -> int | None:
    """Index of the `)` matching the `(` at *open_idx*, respecting quoted strings.

    Returns ``None`` if the parens never balance (malformed/truncated CSS) — callers treat
    that as "nothing safe to rewrite here" rather than guessing at a boundary.
    """
    depth = 0
    quote: str | None = None
    i = open_idx
    n = len(text)
    while i < n:
        ch = text[i]
        if quote is not None:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _split_top_level_commas(text: str) -> list[str]:
    """Split *text* on commas that are NOT inside a quoted string or nested parens.

    Used for `image-set()`'s candidate list, where one candidate can itself be a
    `url(...)` containing an arbitrary (possibly comma-bearing) ref.
    """
    parts: list[str] = []
    depth = 0
    quote: str | None = None
    start = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if quote is not None:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            parts.append(text[start:i])
            start = i + 1
        i += 1
    parts.append(text[start:])
    return parts


def _rewrite_image_set_candidate(candidate: str, ctx: _Context) -> str:
    """Resolve one `image-set()` candidate (`url(...) 1x` or `"..." 1x` form) to a
    `"data:...;base64,..." 1x` string, or `""` (dropped) if it can't be resolved."""
    match = _CSS_IMAGE_SET_CANDIDATE_URL_FORM_RE.match(
        candidate
    ) or _CSS_IMAGE_SET_CANDIDATE_STRING_FORM_RE.match(candidate)
    if match is None:
        return ""  # not a recognizable <image> form -- drop rather than pass through raw
    url = match.group(2)
    rest = match.group("rest").strip()
    data_uri, reason = _resolve_binary_asset(url, ctx)
    if reason is not None or data_uri is None:
        ctx.dropped.append(DroppedRef(url=_truncate(url), reason=reason or "not-found"))
        return ""
    ctx.inlined.inc()
    return f'"{data_uri}" {rest}'.strip()


def _process_image_set(css_text: str, ctx: _Context) -> str:
    """Rewrite `image-set()`/`-webkit-image-set()` argument lists through the same URL
    pipeline as `url()` (M1, security audit): `_CSS_URL_RE` only matches the literal
    `url(` token, so `image-set("http://attacker.example/beacon.png" 1x)` — a valid CSS
    <image> value with no `url(` in it at all — passed through unrewritten and uncounted.
    Every recognized candidate is normalized to a quoted `"data:...;base64,..."` string on
    output (regardless of whether it started as `url(...)` or a quoted string), so this
    must run BEFORE `_CSS_URL_RE.sub` — otherwise a `url(...)`-form candidate would be
    double-processed by both passes.
    """
    out: list[str] = []
    pos = 0
    for m in _CSS_IMAGE_SET_FN_RE.finditer(css_text):
        if m.start() < pos:
            continue  # inside a span already consumed by a previous replacement
        open_idx = m.end() - 1
        close_idx = _find_matching_paren(css_text, open_idx)
        if close_idx is None:
            continue  # unbalanced -- leave as-is, nothing safe to rewrite
        out.append(css_text[pos : m.start()])
        args = css_text[open_idx + 1 : close_idx]
        candidates = [
            rewritten
            for c in _split_top_level_commas(args)
            if (rewritten := _rewrite_image_set_candidate(c, ctx))
        ]
        out.append(f"image-set({', '.join(candidates)})")
        pos = close_idx + 1
    out.append(css_text[pos:])
    return "".join(out)


def _process_stylesheet_css(
    css_text: str, ctx: _Context, *, depth: int, visited: frozenset[str]
) -> str:
    """Process `<style>` tag content or an inlined stylesheet: resolve `@import` (depth- and
    cycle-limited) and rewrite every `url(...)`/`image-set(...)`, dropping refs the URL
    rules reject."""
    css_text = _strip_css_comments(css_text)
    css_text = _strip_dangerous_css_tokens(css_text, strip_import=False)

    def _import_sub(m: re.Match[str]) -> str:
        ref = m.group(2) or m.group(4) or ""
        if depth + 1 > HTML_MAX_IMPORT_DEPTH:
            ctx.dropped.append(DroppedRef(url=_truncate(ref), reason="depth-exceeded"))
            return ""
        text, reason = _resolve_stylesheet_text(ref, ctx, depth, visited)
        if reason is not None:
            ctx.dropped.append(DroppedRef(url=_truncate(ref), reason=reason))
            return ""
        if text:
            ctx.inlined.inc()
        return text or ""

    css_text = _CSS_IMPORT_RE.sub(_import_sub, css_text)
    css_text = _process_image_set(css_text, ctx)

    def _url_sub(m: re.Match[str]) -> str:
        ref = m.group(2)
        data_uri, reason = _resolve_binary_asset(ref, ctx)
        if reason is not None or data_uri is None:
            ctx.dropped.append(DroppedRef(url=_truncate(ref), reason=reason or "not-found"))
            return "url()"
        ctx.inlined.inc()
        return f"url({data_uri})"

    css_text = _CSS_URL_RE.sub(_url_sub, css_text)
    return _escape_style_content(css_text)


def _sanitize_style_attr(value: str, ctx: _Context) -> str:
    """Process a `style="..."` attribute value: strip comments/dangerous tokens, rewrite
    `url(...)`/`image-set(...)`."""
    value = _strip_css_comments(value)
    value = _strip_dangerous_css_tokens(value, strip_import=True)
    value = _process_image_set(value, ctx)

    def _url_sub(m: re.Match[str]) -> str:
        ref = m.group(2)
        data_uri, reason = _resolve_binary_asset(ref, ctx)
        if reason is not None or data_uri is None:
            ctx.dropped.append(DroppedRef(url=_truncate(ref), reason=reason or "not-found"))
            return "url()"
        ctx.inlined.inc()
        return f"url({data_uri})"

    return _CSS_URL_RE.sub(_url_sub, value)


class _SanitizingParser(HTMLParser):
    """Parses source HTML and re-serializes a sanitized version into ``self.out``.

    Never regex-strips: every emitted tag/attribute/text fragment is built explicitly from
    parser callbacks, so malformed input (broken quoting, nested-looking tag tricks) is
    handled exactly the way the parser's own tokenizer resolves it, not by pattern-matching
    the raw source string.
    """

    def __init__(self, ctx: _Context) -> None:
        super().__init__(convert_charrefs=True)
        self._ctx = ctx
        self.out: list[str] = []
        self.scripts_removed = 0
        self._skip_tag: str | None = None
        self._skip_depth = 0
        self._in_style = False
        self._style_attrs: list[tuple[str, str | None]] = []
        self._style_buffer: list[str] = []

    # -- element open/close -------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._handle_open(tag, attrs, self_closing=False)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._handle_open(tag, attrs, self_closing=True)

    def _handle_open(
        self, tag: str, attrs: list[tuple[str, str | None]], *, self_closing: bool
    ) -> None:
        if self._skip_tag is not None:
            if tag == self._skip_tag and not self_closing:
                self._skip_depth += 1
            return

        if tag in DROPPED_ELEMENTS_WITH_SUBTREE:
            if tag == "script":
                self.scripts_removed += 1
            # Void dropped elements (base, embed) have no subtree and no matching end tag
            # to wait for — entering skip mode for one would silently eat the rest of the
            # document. Just drop the single tag.
            if not self_closing and tag not in VOID_ELEMENTS:
                self._skip_tag = tag
                self._skip_depth = 1
            return

        if tag == "style":
            if self_closing:
                return
            self._in_style = True
            self._style_attrs = attrs
            self._style_buffer = []
            return

        if tag == "meta":
            self._emit_meta(attrs)
            return

        if tag == "link":
            self._emit_link(attrs)
            return

        attr_str = self._render_attrs(tag, attrs)
        self.out.append(f"<{tag}{attr_str}{' /' if self_closing else ''}>")

    def handle_endtag(self, tag: str) -> None:
        if self._skip_tag is not None:
            if tag == self._skip_tag:
                self._skip_depth -= 1
                if self._skip_depth <= 0:
                    self._skip_tag = None
            return
        if tag == "style" and self._in_style:
            self._flush_style()
            return
        if tag in ("meta", "link"):
            return
        if tag not in VOID_ELEMENTS:
            self.out.append(f"</{tag}>")

    # -- text / comments / decl -----------------------------------------------------

    def handle_data(self, data: str) -> None:
        if self._skip_tag is not None:
            return
        if self._in_style:
            self._style_buffer.append(data)
            return
        self.out.append(html.escape(data, quote=True))

    def handle_comment(self, data: str) -> None:
        # Comments are dropped unconditionally — IE-style conditional comments can smuggle
        # markup that must never be re-emitted, even escaped.
        return

    def handle_decl(self, decl: str) -> None:
        if self._skip_tag is not None:
            return
        # Only a plain DOCTYPE declaration is passed through (affects rendering mode, not a
        # known XSS vector); anything else is dropped rather than guessed at.
        if decl.strip().lower().startswith("doctype"):
            self.out.append(f"<!{decl}>")

    def handle_pi(self, data: str) -> None:
        # Processing instructions (e.g. an XML prolog atop an SVG file) are dropped — not
        # needed to render a fragment inside an HTML5 srcdoc document.
        return

    # -- element-specific emission ---------------------------------------------------

    def _emit_meta(self, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {name.lower(): (value or "") for name, value in attrs}
        # Keep ONLY the bare `<meta charset="...">` form. Anything carrying `http-equiv`
        # or `content` is dropped whole rather than partially filtered — a meta refresh
        # (`http-equiv="refresh"`) or a `Content-Security-Policy` override attempt must not
        # survive even in mutilated form.
        if "charset" in attr_map and "http-equiv" not in attr_map and "content" not in attr_map:
            charset = html.escape(attr_map["charset"], quote=True)
            self.out.append(f'<meta charset="{charset}">')

    def _emit_link(self, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {name.lower(): (value or "") for name, value in attrs}
        rel_values = attr_map.get("rel", "").lower().split()
        href = attr_map.get("href")
        if "stylesheet" not in rel_values or not href:
            # Policy drop (unsupported link relation), not a URL-resolution failure — not
            # recorded in `dropped`, same treatment as the other structurally-dropped
            # elements (iframe/object/...).
            return
        text, reason = _resolve_stylesheet_text(href, self._ctx, depth=0, visited=frozenset())
        if reason is not None or text is None:
            self._ctx.dropped.append(DroppedRef(url=_truncate(href), reason=reason or "not-found"))
            return
        self._ctx.inlined.inc()
        self.out.append(f"<style>{text}</style>")

    def _flush_style(self) -> None:
        css = "".join(self._style_buffer)
        processed = _process_stylesheet_css(css, self._ctx, depth=0, visited=frozenset())
        attr_str = self._render_attrs("style", self._style_attrs)
        self.out.append(f"<style{attr_str}>{processed}</style>")
        self._in_style = False
        self._style_buffer = []
        self._style_attrs = []

    def _render_attrs(self, tag: str, attrs: list[tuple[str, str | None]]) -> str:
        parts: list[str] = []
        pending_href_display: str | None = None

        for name, raw_value in attrs:
            name_l = name.lower()
            value = raw_value if raw_value is not None else ""

            if name_l.startswith("on"):
                continue
            if name_l in HARD_DROP_ATTRS or name_l.endswith(":href"):
                # L4 (security audit): `HARD_DROP_ATTRS` used to list only the literal
                # `xlink:href`. A rebound namespace prefix (e.g. `xmlns:foo=".../xlink"`
                # then `foo:href="javascript:..."`) would carry the exact same live-href
                # semantics under a different spelling and slip through unmatched. The
                # parser's own foreign-content handling likely only ever produces the
                # fixed `xlink:href` spelling (making a rebound prefix probably
                # unreachable in practice), but matching any `*:href` suffix is a cheap,
                # robust generalization regardless of whether that path is reachable.
                continue

            if name_l == "style":
                sanitized = _sanitize_style_attr(value, self._ctx)
                parts.append(f'style="{html.escape(sanitized, quote=True)}"')
                continue

            if name_l == "href":
                if tag == "a":
                    # `<a href>` is stripped for UX, NOT as a security control: a live
                    # href here would be dead-looking-clickable UI (the sandbox blocks
                    # navigating OTHER frames), but that is not the same as harmless —
                    # a sandboxed iframe with no `allow-top-navigation` can still
                    # navigate ITSELF, so a real `href` (or anything that can retarget
                    # one live, e.g. an SVG `<animate>` — see `DROPPED_ELEMENTS_WITH_
                    # SUBTREE`'s comment, H1) is a genuine one-click egress/redirect
                    # channel, not "dead UI". The actual security control against THAT
                    # is dropping the retargeting elements outright; stripping `href`
                    # here just keeps the static preview from displaying a misleading
                    # live-looking link.
                    pending_href_display = value
                    continue
                # href elsewhere (SVG `<use>`/`<image>`, `<area>`, ...) is a URL-bearing
                # asset ref like `src` — NOT navigation, so it goes through the same
                # resolve-or-drop rules rather than surviving untouched.
                data_uri, reason = _resolve_binary_asset(value, self._ctx)
                if reason is not None or data_uri is None:
                    self._ctx.dropped.append(
                        DroppedRef(url=_truncate(value), reason=reason or "not-found")
                    )
                    continue
                self._ctx.inlined.inc()
                parts.append(f'href="{html.escape(data_uri, quote=True)}"')
                continue

            if name_l in URL_ATTRS_TO_INLINE:
                data_uri, reason = _resolve_binary_asset(value, self._ctx)
                if reason is not None or data_uri is None:
                    self._ctx.dropped.append(
                        DroppedRef(url=_truncate(value), reason=reason or "not-found")
                    )
                    continue
                self._ctx.inlined.inc()
                parts.append(f'{name_l}="{html.escape(data_uri, quote=True)}"')
                continue

            if name_l == "srcset":
                rewritten = self._rewrite_srcset(value)
                if rewritten:
                    parts.append(f'srcset="{html.escape(rewritten, quote=True)}"')
                continue

            parts.append(f'{name_l}="{html.escape(value, quote=True)}"')

        if tag == "a" and pending_href_display is not None:
            display = html.escape(_truncate(pending_href_display), quote=True)
            parts.append(f'data-ao-href="{display}"')

        return "".join(f" {p}" for p in parts)

    def _rewrite_srcset(self, value: str) -> str:
        candidates: list[str] = []
        for url, descriptor in _parse_srcset(value):
            data_uri, reason = _resolve_binary_asset(url, self._ctx)
            if reason is not None or data_uri is None:
                self._ctx.dropped.append(
                    DroppedRef(url=_truncate(url), reason=reason or "not-found")
                )
                continue
            self._ctx.inlined.inc()
            candidates.append(f"{data_uri} {descriptor}".strip())
        return ", ".join(candidates)


def build_html_preview(browser: FileBrowser, root_name: str | None, rel_path: str) -> HtmlPreview:
    """Build a sanitized, self-contained preview of the markup file at *rel_path*.

    Raises:
        PathNotAllowedError: If the path escapes the root.
        PathNotFoundError: If the path does not exist or is a directory.
        NotMarkupError: If the file's extension is not in ``MARKUP_EXTENSIONS``.
    """
    root, resolved = browser.resolve(root_name, rel_path)
    if not resolved.exists():
        raise PathNotFoundError(f"no such file: {rel_path}")
    if resolved.is_dir():
        raise PathNotFoundError(f"is a directory: {rel_path}")

    ext = resolved.suffix.lower().lstrip(".")
    if ext not in MARKUP_EXTENSIONS:
        raise NotMarkupError(f"not a markup file: {rel_path}")

    size = resolved.stat().st_size
    with open(resolved, "rb") as fh:
        raw = fh.read(MAX_READ_BYTES)
    truncated = size > len(raw)
    source = raw.decode("utf-8", errors="replace")

    root_path = Path(root.path)
    ctx = _Context(
        browser=browser,
        root=root,
        root_name=root.name,
        root_path=root_path,
        html_dir=resolved.parent,
        budget=_Budget(limit=HTML_INLINE_BUDGET_BYTES),
        dropped=[],
        inlined=_Counter(),
    )

    parser = _SanitizingParser(ctx)
    parser.feed(source)
    parser.close()

    return HtmlPreview(
        path=browser.relative(root, resolved),
        root=root.name,
        html="".join(parser.out),
        inlined=ctx.inlined.value,
        dropped=ctx.dropped,
        scripts_removed=parser.scripts_removed,
        truncated=truncated,
        budget_bytes=HTML_INLINE_BUDGET_BYTES,
        budget_used=ctx.budget.used,
    )


__all__ = [
    "DROPPED_ELEMENTS_WITH_SUBTREE",
    "HARD_DROP_ATTRS",
    "HTML_ASSET_MAX_BYTES",
    "HTML_ASSET_MIME_BY_EXT",
    "HTML_DATA_URI_MIME_ALLOWLIST",
    "HTML_INLINE_BUDGET_BYTES",
    "HTML_MAX_IMPORT_DEPTH",
    "DroppedRef",
    "HtmlPreview",
    "NotMarkupError",
    "build_html_preview",
]
