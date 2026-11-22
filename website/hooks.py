"""MkDocs hooks for the docs website.

The pages are the Markdown files in ``docs/``, written to read well on GitHub. These
hooks adapt them for MkDocs at build time, without changing the sources:

* Relative links that leave ``docs/`` (to ``contracts/``, ``services/`` …) or point at
  a file excluded from the site become absolute GitHub links at ``main``.
* GitHub alerts (``> [!WARNING]``) become Material admonitions.
* A region between ``<!-- tabs -->`` and ``<!-- /tabs -->`` whose items are each
  introduced by a line ``**Label**`` becomes a set of content tabs. On GitHub the
  markers are invisible and the labels read as bold captions.

For agents, the site also publishes, beside the HTML:

* each page's Markdown, at its source path (``usage.md`` next to ``usage/``), with the
  link rewriting above applied so its links resolve, and advertised from the HTML page
  by ``<link rel="alternate" type="text/markdown">``;
* ``llms.txt``, an index of those Markdown files (https://llmstxt.org);
* ``llms-full.txt``, the integration pages concatenated, for a single fetch.
"""

from __future__ import annotations

import posixpath
import re
from pathlib import Path

REPO_BLOB = "https://github.com/shtukaresearch/swarm-volume-registry/blob/main/"
DOCS_PREFIX = "docs"

# Pages concatenated into llms-full.txt, in this order.
FULL_PAGES = ["usage.md", "KEEPERS.md"]

_FENCE = re.compile(r"^\s*(```|~~~)")
_LINK = re.compile(r"(\]\()([^)\s]+)(\))")
_ALERT = re.compile(r"^>\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*$")
_ALERT_KIND = {
    "NOTE": "note",
    "TIP": "tip",
    "IMPORTANT": "info",
    "WARNING": "warning",
    "CAUTION": "danger",
}
_TAB_LABEL = re.compile(r"^\*\*(.+?)\*\*\s*$")

_markdown: dict[str, str] = {}  # src_uri -> Markdown as published
_order: list = []  # pages in nav order


def _outside_fences(lines):
    """Yield (index, line, in_fence) for each line."""
    in_fence = False
    for i, line in enumerate(lines):
        if _FENCE.match(line):
            yield i, line, True
            in_fence = not in_fence
            continue
        yield i, line, in_fence


def _rewrite_links(markdown: str, src_uri: str, site_pages: set[str]) -> str:
    base = posixpath.dirname(src_uri)

    def fix(match: re.Match) -> str:
        target = match.group(2)
        if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith(("#", "/")):
            return match.group(0)
        path, _, anchor = target.partition("#")
        if not path:
            return match.group(0)
        resolved = posixpath.normpath(posixpath.join(base, path))
        if not resolved.startswith("../") and resolved in site_pages:
            return match.group(0)
        if not resolved.startswith("../") and not resolved.endswith(".md"):
            # A non-page file inside docs/ (an image, say) is copied to the site.
            return match.group(0)
        repo_path = posixpath.normpath(posixpath.join(DOCS_PREFIX, resolved))
        url = REPO_BLOB + repo_path + (f"#{anchor}" if anchor else "")
        return f"{match.group(1)}{url}{match.group(3)}"

    lines = markdown.split("\n")
    for i, line, in_fence in _outside_fences(lines):
        if not in_fence:
            lines[i] = _LINK.sub(fix, line)
    return "\n".join(lines)


def _convert_alerts(markdown: str) -> str:
    lines = markdown.split("\n")
    out: list[str] = []
    i = 0
    in_fence = False
    while i < len(lines):
        line = lines[i]
        if _FENCE.match(line):
            in_fence = not in_fence
        m = None if in_fence else _ALERT.match(line)
        if not m:
            out.append(line)
            i += 1
            continue
        out.append(f'!!! {_ALERT_KIND[m.group(1)]} "{m.group(1).title()}"')
        out.append("")
        i += 1
        while i < len(lines) and lines[i].startswith(">"):
            body = lines[i][1:]
            body = body[1:] if body.startswith(" ") else body
            out.append(("    " + body) if body.strip() else "")
            i += 1
    return "\n".join(out)


def _convert_tabs(markdown: str) -> str:
    lines = markdown.split("\n")
    out: list[str] = []
    in_region = False
    in_fence = False
    in_tab = False
    for line in lines:
        stripped = line.strip()
        if not in_fence and stripped == "<!-- tabs -->":
            in_region, in_tab = True, False
            continue
        if not in_fence and stripped == "<!-- /tabs -->":
            in_region = False
            out.append("")
            continue
        if not in_region:
            if _FENCE.match(line):
                in_fence = not in_fence
            out.append(line)
            continue
        if _FENCE.match(line):
            in_fence = not in_fence
        label = None if in_fence else _TAB_LABEL.match(line)
        if label:
            out.append(f'=== "{label.group(1)}"')
            in_tab = True
            continue
        if in_tab and line.strip():
            out.append("    " + line)
        else:
            out.append(line)
    return "\n".join(out)


def on_nav(nav, config, files):
    _order[:] = list(nav.pages)
    return nav


def on_page_markdown(markdown, page, config, files):
    site_pages = {f.src_uri for f in files.documentation_pages()}
    published = _rewrite_links(markdown, page.file.src_uri, site_pages)
    _markdown[page.file.src_uri] = published
    return _convert_tabs(_convert_alerts(published))


def on_post_page(output, page, config):
    href = config["site_url"] + page.file.src_uri
    tag = f'<link rel="alternate" type="text/markdown" href="{href}">'
    return output.replace("</head>", f"  {tag}\n</head>", 1)


def _summary(markdown: str) -> str:
    """The first prose paragraph after the title, on one line."""
    para: list[str] = []
    for _, line, in_fence in _outside_fences(markdown.split("\n")):
        if in_fence:
            if para:
                break
            continue
        s = line.strip()
        if s.startswith(("#", ">", "|", "<!--", "-", "*", "```")) or not s:
            if para:
                break
            continue
        para.append(s)
    text = " ".join(para)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    return text if len(text) <= 240 else text[:237].rsplit(" ", 1)[0] + "…"


def on_post_build(config):
    site_dir = Path(config["site_dir"])
    for src_uri, md in _markdown.items():
        dest = site_dir / src_uri
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(md, encoding="utf-8")

    base = config["site_url"]
    lines = [
        f"# {config['site_name']}",
        "",
        f"> {' '.join(config['site_description'].split())}",
        "",
        "Every page of this site is also published as Markdown at the URLs below. "
        "Agents should prefer them to the HTML. `llms-full.txt` is the integration "
        "pages concatenated. The v2 deployment is on Sepolia testnet only.",
        "",
        "## Integration",
        "",
    ]
    design: list[str] = []
    for page in _order:
        src = page.file.src_uri
        if src not in _markdown or src == "index.md":
            continue
        summary = _summary(_markdown[src])
        entry = f"- [{page.title}]({base}{src})"
        if summary and not summary.startswith("Status:"):
            entry += f": {summary}"
        (lines if src in FULL_PAGES else design).append(entry)
    if design:
        lines += ["", "## Design and rationale", "", *design]
    lines += [""]
    lines += [
        "## Optional",
        "",
        f"- [All integration pages in one file]({base}llms-full.txt)",
        "- [Source repository](https://github.com/shtukaresearch/swarm-volume-registry)",
        "",
    ]
    (site_dir / "llms.txt").write_text("\n".join(lines), encoding="utf-8")

    full = []
    for src in FULL_PAGES:
        if src in _markdown:
            full.append(f"<!-- {base}{src} -->\n\n{_markdown[src].strip()}\n")
    (site_dir / "llms-full.txt").write_text("\n\n---\n\n".join(full), encoding="utf-8")
