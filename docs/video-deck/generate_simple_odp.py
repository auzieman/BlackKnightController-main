#!/usr/bin/env python3
"""Generate the bare-metal demo ODP without adding external dependencies."""

from __future__ import annotations

import html
import re
import sys
import zipfile
from pathlib import Path


NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "presentation": "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0",
    "svg": "urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0",
    "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
    "xlink": "http://www.w3.org/1999/xlink",
    "manifest": "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0",
}


def parse_slides(markdown: str) -> list[tuple[str, list[str]]]:
    slides: list[tuple[str, list[str]]] = []
    title = ""
    body: list[str] = []
    for raw in markdown.splitlines():
        line = raw.rstrip()
        if line.startswith("# "):
            if title:
                slides.append((title, body))
            title = line[2:].strip()
            body = []
        elif line.startswith("## "):
            if title:
                slides.append((title, body))
            title = line[3:].strip()
            body = []
        elif line.strip():
            body.append(line)
    if title:
        slides.append((title, body))
    return slides


def text_lines(lines: list[str]) -> str:
    rendered: list[str] = []
    in_code = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if stripped.startswith("- "):
            rendered.append(f"<text:p text:style-name=\"Bullet\">• {html.escape(stripped[2:])}</text:p>")
        elif re.match(r"^[0-9]+\\. ", stripped):
            rendered.append(f"<text:p text:style-name=\"Bullet\">{html.escape(stripped)}</text:p>")
        elif in_code:
            rendered.append(f"<text:p text:style-name=\"Code\">{html.escape(stripped)}</text:p>")
        else:
            rendered.append(f"<text:p text:style-name=\"Body\">{html.escape(stripped)}</text:p>")
    return "\n".join(rendered)


def content_xml(slides: list[tuple[str, list[str]]]) -> str:
    ns_attrs = " ".join(f'xmlns:{k}="{v}"' for k, v in NS.items() if k != "manifest")
    pages = []
    for index, (title, body) in enumerate(slides, 1):
        pages.append(
            f"""
<draw:page draw:name="Slide {index}" draw:style-name="dp1" draw:master-page-name="Default" presentation:presentation-page-layout-name="AL1T2">
  <draw:frame draw:style-name="gr1" draw:text-style-name="P1" svg:x="0.6in" svg:y="0.35in" svg:width="9.8in" svg:height="0.8in">
    <draw:text-box><text:p text:style-name="Title">{html.escape(title)}</text:p></draw:text-box>
  </draw:frame>
  <draw:frame draw:style-name="gr2" draw:text-style-name="P2" svg:x="0.85in" svg:y="1.35in" svg:width="9.35in" svg:height="5.6in">
    <draw:text-box>{text_lines(body)}</draw:text-box>
  </draw:frame>
</draw:page>
"""
        )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document-content {ns_attrs} office:version="1.2">
  <office:automatic-styles>
    <style:style style:name="dp1" style:family="drawing-page"/>
    <style:style style:name="gr1" style:family="graphic"><style:graphic-properties draw:fill="none" draw:stroke="none"/></style:style>
    <style:style style:name="gr2" style:family="graphic"><style:graphic-properties draw:fill="none" draw:stroke="none"/></style:style>
    <style:style style:name="Title" style:family="paragraph"><style:text-properties fo:font-size="30pt" fo:font-weight="bold"/></style:style>
    <style:style style:name="Body" style:family="paragraph"><style:text-properties fo:font-size="18pt"/></style:style>
    <style:style style:name="Bullet" style:family="paragraph"><style:text-properties fo:font-size="17pt"/></style:style>
    <style:style style:name="Code" style:family="paragraph"><style:text-properties fo:font-size="15pt" fo:font-family="monospace"/></style:style>
    <style:style style:name="P1" style:family="presentation"/>
    <style:style style:name="P2" style:family="presentation"/>
  </office:automatic-styles>
  <office:body><office:presentation>{''.join(pages)}</office:presentation></office:body>
</office:document-content>
"""


def styles_xml() -> str:
    ns_attrs = " ".join(f'xmlns:{k}="{v}"' for k, v in NS.items() if k != "manifest")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document-styles {ns_attrs} office:version="1.2">
  <office:styles/>
  <office:master-styles>
    <style:master-page style:name="Default"/>
  </office:master-styles>
</office:document-styles>
"""


def manifest_xml() -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<manifest:manifest xmlns:manifest="{NS['manifest']}" manifest:version="1.2">
  <manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.presentation"/>
  <manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>
  <manifest:file-entry manifest:full-path="styles.xml" manifest:media-type="text/xml"/>
  <manifest:file-entry manifest:full-path="meta.xml" manifest:media-type="text/xml"/>
</manifest:manifest>
"""


def main() -> int:
    source = Path(sys.argv[1])
    output = Path(sys.argv[2])
    slides = parse_slides(source.read_text(encoding="utf-8"))
    with zipfile.ZipFile(output, "w") as odp:
        odp.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.oasis.opendocument.presentation", compress_type=zipfile.ZIP_STORED)
        odp.writestr("content.xml", content_xml(slides))
        odp.writestr("styles.xml", styles_xml())
        odp.writestr("meta.xml", "<?xml version=\"1.0\" encoding=\"UTF-8\"?><office:document-meta xmlns:office=\"urn:oasis:names:tc:opendocument:xmlns:office:1.0\" office:version=\"1.2\"/>")
        odp.writestr("META-INF/manifest.xml", manifest_xml())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
