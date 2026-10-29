"""把 md / txt / pdf / docx 解析成 (页码, 文本) 列表。"""
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

SUPPORTED = {".md", ".markdown", ".txt", ".pdf", ".docx"}


def parse_file(path: Path):
    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown", ".txt"}:
        return [(0, path.read_text(encoding="utf-8", errors="ignore"))]
    if suffix == ".pdf":
        return _parse_pdf(path)
    if suffix == ".docx":
        return [(0, _parse_docx(path))]
    raise ValueError(f"不支持的文件类型: {path.name}")


def _parse_pdf(path: Path):
    from pypdf import PdfReader

    pages = []
    for index, page in enumerate(PdfReader(str(path)).pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            pages.append((index, text))
    return pages


def _parse_docx(path: Path):
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    lines = []
    for para in root.iter(f"{ns}p"):
        text = "".join(node.text or "" for node in para.iter(f"{ns}t")).strip()
        if text:
            lines.append(text)
    return "\n\n".join(lines)


def html_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
