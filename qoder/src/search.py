"""混合检索：向量召回 + 全文召回，再用 RRF 融合。"""
import argparse
import re

from . import db, embedding

RRF_K = 60
RECALL_LIMIT = 40


def _vector_recall(cur, vec_literal, course, limit):
    sql = (
        "SELECT id, title, course, chapter, page_no, content,"
        " cosine_distance(content_vec, %s) AS dist FROM doc_chunks"
    )
    params = [vec_literal]
    if course:
        sql += " WHERE course=%s"
        params.append(course)
    sql += " ORDER BY cosine_distance(content_vec, %s) APPROXIMATE LIMIT %s"
    params.extend([vec_literal, limit])
    cur.execute(sql, params)
    return cur.fetchall()


def _tokenize(text: str):
    return [tok for tok in re.split(r"[\s，。、；：？！,.;:?!（）()「」“”\"'《》]+", text) if tok]


def _fulltext_query(query: str) -> str:
    """去掉「和」「的」这类单字，避免全文通道被虚词撑满。"""
    tokens = [tok for tok in _tokenize(query) if len(tok) >= 2]
    return " ".join(tokens) or query


def _fulltext_recall(cur, query, course, limit):
    query = _fulltext_query(query)
    sql = (
        "SELECT id, title, course, chapter, page_no, content,"
        " MATCH(content) AGAINST(%s) AS score FROM doc_chunks"
        " WHERE MATCH(content) AGAINST(%s)"
    )
    params = [query, query]
    if course:
        sql += " AND course=%s"
        params.append(course)
    sql += " ORDER BY score DESC LIMIT %s"
    params.append(limit)
    cur.execute(sql, params)
    return cur.fetchall()


def search(query: str, course: str = "", limit: int = 8):
    vec = db.vector_literal(embedding.embed_query(query))
    fused = {}
    with db.connect() as conn, conn.cursor() as cur:
        for rank, row in enumerate(_vector_recall(cur, vec, course or None, RECALL_LIMIT)):
            fused.setdefault(row[0], {"row": row, "score": 0.0, "sources": []})
            fused[row[0]]["score"] += 1.0 / (RRF_K + rank + 1)
            fused[row[0]]["sources"].append("vector")
        for rank, row in enumerate(_fulltext_recall(cur, query, course or None, RECALL_LIMIT)):
            fused.setdefault(row[0], {"row": row, "score": 0.0, "sources": []})
            fused[row[0]]["score"] += 1.0 / (RRF_K + rank + 1)
            fused[row[0]]["sources"].append("fulltext")

    ranked = sorted(fused.values(), key=lambda item: item["score"], reverse=True)[:limit]
    results = []
    for item in ranked:
        chunk_id, title, course_name, chapter, page_no, content, _ = item["row"]
        results.append({
            "id": chunk_id,
            "title": title,
            "course": course_name,
            "chapter": chapter,
            "page": page_no,
            "snippet": _highlight(content, query),
            "score": round(item["score"], 6),
            "sources": item["sources"],
        })
    return results


def _highlight(text: str, query: str, width: int = 260) -> str:
    plain = text.replace("\n", " ").strip()
    hit = plain.find(query)
    if hit >= 0:
        start = max(0, hit - 60)
        return plain[start:start + width]
    tokens = [tok for tok in _tokenize(query) if len(tok) >= 2]
    for token in sorted(tokens, key=len, reverse=True):
        hit = plain.find(token)
        if hit >= 0:
            start = max(0, hit - 60)
            return plain[start:start + width]
    return plain[:width]


def main():
    parser = argparse.ArgumentParser(description="检索课程笔记")
    parser.add_argument("query")
    parser.add_argument("--course", default="")
    parser.add_argument("--limit", type=int, default=8)
    args = parser.parse_args()
    for i, item in enumerate(search(args.query, args.course, args.limit), 1):
        origin = f"{item['course']} / {item['chapter'] or '正文'}"
        page = f" 第{item['page']}页" if item["page"] else ""
        print(f"[{i}] {item['title']} | {origin}{page} | {item['sources']} | {item['score']}")
        print(f"    {item['snippet']}\n")


if __name__ == "__main__":
    main()
