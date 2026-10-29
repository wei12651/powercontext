"""跑一遍验证样例，打印每条查询的前 3 条命中与通过情况。"""
import json
from pathlib import Path

from src import search

CASES = Path(__file__).resolve().parent / "sample_queries.json"


def main():
    cases = json.loads(CASES.read_text(encoding="utf-8"))["queries"]
    passed = 0
    for case in cases:
        results = search.search(case["q"], case.get("course", ""), max(case["top_k"], 3))
        hit = next(
            (
                (i, r)
                for i, r in enumerate(results[: case["top_k"]], 1)
                if r["title"] == case["expect"]["title"] and case["expect"]["chapter"] in (r["chapter"] or "")
            ),
            None,
        )
        if hit:
            passed += 1
        mark = "PASS" if hit else "FAIL"
        print(f"[{mark}] {case['q']}  (top{case['top_k']})")
        for i, r in enumerate(results[:3], 1):
            flag = " <=" if hit and i == hit[0] else ""
            print(f"    {i}. {r['title']} | {r['chapter'] or '正文'} | {r['sources']}{flag}")
        print()
    print(f"通过 {passed}/{len(cases)}")


if __name__ == "__main__":
    main()
