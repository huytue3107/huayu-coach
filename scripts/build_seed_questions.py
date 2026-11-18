"""One-off import: MVP Database V2 spreadsheet (xlsx export) -> seeds/questions.json.

Usage: python scripts/build_seed_questions.py <db.xlsx> seeds/questions.json
Normalises what the audit found wrong in the sheet:
  * the answer letter is turned into correct_index (the app shuffles options, so letters in explanations are stripped);
  * the template note "請確認題目語意與其他選項" that sat in 23 learner-facing explanations is dropped;
  * a grammar `topic` is added so wrong answers can be tied to "了", "在", 量詞 ... (used for review suggestions);
  * six questions that have a second defensible answer are marked needs_edit and are never served until a teacher fixes them.
"""
import json, re, sys, zipfile
import xml.etree.ElementTree as ET

M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"

TOPICS = {"Q002": "是", "Q004": "在", "Q006": "量詞", "Q007": "動詞", "Q009": "如果…就…", "Q012": "有", "Q014": "是",
          "Q016": "在", "Q018": "去", "Q020": "不／沒", "Q022": "很", "Q024": "動詞", "Q025": "完", "Q028": "完",
          "Q030": "要", "Q032": "雖然…還是…", "Q034": "動詞", "Q036": "一下", "Q038": "動詞", "Q040": "完",
          "Q042": "是", "Q044": "在", "Q046": "量詞", "Q047": "了", "Q049": "如果…就…"}
NEEDS_EDIT = {
    "Q004": "有 also works: 他有喝水. Give a time cue or remove 有 from the options.",
    "Q009": "因為 is also acceptable here. Rewrite the stem so only 如果 fits.",
    "Q020": "沒 and 有 also work depending on meaning (the explanation admits it). Add context.",
    "Q030": "有 is very common in Taiwan Mandarin (你有去圖書館嗎). Replace the 有 option.",
    "Q032": "如果 also works (even if it rains). Make 雖然 the only connector that fits.",
    "Q044": "有 is natural (我有看書). Add a progressive cue such as 現在 or remove 有.",
}


def col(ref):
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n


def read_sheet(path, name):
    with zipfile.ZipFile(path) as z:
        ss = [''.join(t.text or '' for t in si.iter(M + 't')) for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(M + "si")]
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")).iter(PR + "Relationship")}
        sheet = next(s for s in wb.find(M + "sheets") if s.get("name") == name)
        target = rels[sheet.get(R + "id")]
        root = ET.fromstring(z.read(target.lstrip("/") if target.startswith("/") else "xl/" + target))
        rows = []
        for row in root.find(M + "sheetData").findall(M + "row"):
            cells = {}
            for c in row.findall(M + "c"):
                v = c.find(M + "v")
                if c.get("t") == "s" and v is not None:
                    cells[col(c.get("r"))] = ss[int(v.text)]
                elif v is not None:
                    cells[col(c.get("r"))] = v.text
            rows.append(cells)
    head = [rows[0].get(i, "") for i in range(1, max(rows[0]) + 1)]
    return [{head[i - 1]: r.get(i, "") for i in range(1, len(head) + 1)} for r in rows[1:] if r]


def main(src, dst):
    out = []
    for r in read_sheet(src, "Questions"):
        qid = r["question_id"]
        options = [r["option_A"], r["option_B"], r["option_C"], r["option_D"]]
        idx = "ABCD".index(r["correct_answer"])
        vi = re.sub(r"^Đáp án [A-D]\.\s*", "", r["explanation_vi"]).strip()
        zh = re.sub(r"^答案 [A-D]。", "", r["explanation_zh"]).strip()
        if "請確認題目語意" in zh:
            zh = ""
        out.append({
            "id": qid, "stage": r["stage"], "day": int(float(r["day"])), "level": r["level"], "skill": r["skill"],
            "topic": TOPICS.get(qid), "question_zh": r["question_zh"], "options": options, "correct_index": idx,
            "explanation_vi": vi or None, "explanation_zh": zh or None,
            "review_status": "needs_edit" if qid in NEEDS_EDIT else "pending_review",
            "review_note": NEEDS_EDIT.get(qid), "source": "self-authored demo (MVP sheet V2)",
        })
    assert len(out) == 50 and len({q["id"] for q in out}) == 50
    for q in out:
        assert len(q["options"]) == 4 and len(set(q["options"])) == 4, q["id"]
        assert q["options"][q["correct_index"]], q["id"]
    json.dump(out, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("wrote", len(out), "questions ->", dst)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
