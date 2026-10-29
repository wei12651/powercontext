"""导入笔记：解析 -> 切块 -> 向量化 -> 写入 seekdb。"""
import argparse
import hashlib
from pathlib import Path

from . import chunking, config, db, embedding, parsers


def build_rows(path: Path, course: str):
    rows = []
    for page_no, text in parsers.parse_file(path):
        for chunk_no, (chapter, piece) in enumerate(chunking.chunk_document(text)):
            rows.append((chapter, page_no, chunk_no, piece))
    if not rows:
        return []

    vectors = embedding.embed_texts([row[3] for row in rows])
    doc_id = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:12]
    return [
        (doc_id, path.name, course, chapter, page_no, chunk_no, piece, db.vector_literal(vec))
        for (chapter, page_no, chunk_no, piece), vec in zip(rows, vectors)
    ]


def ingest_path(target: Path, course: str) -> int:
    files = [target] if target.is_file() else sorted(
        p for p in target.rglob("*") if p.suffix.lower() in parsers.SUPPORTED
    )
    db.ensure_schema()
    total = 0
    with db.connect() as conn, conn.cursor() as cur:
        for path in files:
            rows = build_rows(path, course)
            if not rows:
                print(f"跳过（无内容）: {path.name}")
                continue
            cur.execute("DELETE FROM doc_chunks WHERE doc_id=%s", (rows[0][0],))
            cur.executemany(
                "INSERT INTO doc_chunks (doc_id, title, course, chapter, page_no, chunk_no, content, content_vec)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                rows,
            )
            total += len(rows)
            print(f"已导入 {path.name}: {len(rows)} 块")
    return total


def main():
    parser = argparse.ArgumentParser(description="导入课程笔记到 seekdb")
    parser.add_argument("path", help="文件或目录")
    parser.add_argument("--course", default="默认课程", help="课程名，用于筛选")
    args = parser.parse_args()
    total = ingest_path(Path(args.path), args.course)
    print(f"完成，共 {total} 块")


if __name__ == "__main__":
    main()
