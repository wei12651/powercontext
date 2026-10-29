"""把语料和验证样例的真实检索结果导出成站点用的 demo-data.js。"""
import json
from pathlib import Path

from src import db, search

ROOT = Path(__file__).resolve().parent.parent
CASES = Path(__file__).resolve().parent / "sample_queries.json"
OUT = ROOT / "site" / "demo-data.js"


def main():
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, title, course, chapter, page_no, content FROM doc_chunks ORDER BY id")
        corpus = [
            {
                "id": row[0],
                "title": row[1],
                "course": row[2],
                "chapter": row[3],
                "page": row[4],
                "content": row[5],
            }
            for row in cur.fetchall()
        ]

    cases = []
    for case in json.loads(CASES.read_text(encoding="utf-8"))["queries"]:
        results = search.search(case["q"], case.get("course", ""), 5)
        cases.append(
            {
                "q": case["q"],
                "course": case.get("course", ""),
                "expect": case["expect"],
                "topK": case["top_k"],
                "results": results,
            }
        )

    payload = {
        "generatedAt": "2026-10-29",
        "chunkCount": len(corpus),
        "corpus": corpus,
        "cases": cases,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        "window.DEMO = " + json.dumps(payload, ensure_ascii=False, indent=1) + ";\n",
        encoding="utf-8",
    )
    print(f"写入 {OUT}：{len(corpus)} 块，{len(cases)} 条样例")


if __name__ == "__main__":
    main()
