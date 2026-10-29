"""把笔记切成检索片段：先按标题分节，再在节内按段落滑窗。"""
import re

TARGET_CHARS = 700
OVERLAP_CHARS = 100


def split_sections(text: str):
    """按 Markdown 标题切分，返回 [(章节路径, 正文)]。"""
    sections = []
    stack = []
    buffer = []

    def flush():
        body = "\n".join(buffer).strip()
        if body:
            sections.append((" / ".join(stack), body))
        buffer.clear()

    for line in text.splitlines():
        match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if match:
            flush()
            level = len(match.group(1))
            stack = stack[: level - 1] + [match.group(2).strip()]
        else:
            buffer.append(line)
    flush()
    return sections


def window_text(text: str, target: int = TARGET_CHARS, overlap: int = OVERLAP_CHARS):
    """按段落聚合成接近 target 长度的块，块间保留 overlap 字符重叠。"""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return []

    chunks, current = [], ""
    for para in paragraphs:
        if len(current) + len(para) + 1 <= target:
            current = f"{current}\n{para}".strip()
            continue
        if current:
            chunks.append(current)
            tail = current[-overlap:] if overlap and len(current) > overlap else ""
            current = f"{tail}\n{para}".strip()
        else:
            # 单段就超长，硬切
            for i in range(0, len(para), target - overlap):
                piece = para[i:i + target]
                if piece:
                    chunks.append(piece)
            current = ""
    if current:
        chunks.append(current)
    return chunks


def chunk_document(text: str):
    """返回 [(章节路径, 片段文本)]。"""
    result = []
    for chapter, body in split_sections(text):
        for piece in window_text(body):
            result.append((chapter, piece))
    if not result and text.strip():
        result = [("", text.strip())]
    return result
