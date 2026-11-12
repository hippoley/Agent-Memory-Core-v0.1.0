"""Compare the four LongMemEval runs on the same frozen 24-question subset."""
import json

RUNS = [
    ("A  local 7B, lexical only", "data/eval/longmemeval_24.json"),
    ("B  cloud qwen-plus, lexical", "data/eval/longmemeval_cloud24_serial.json"),
    ("C  B + projection + fts-or (equal weights)", "data/eval/longmemeval_cloud24_fts.json"),
    ("D  C + vector + weighted + assistant + context", "data/eval/longmemeval_cloud24_p012.json"),
    ("E  D + answer-side fixes", "data/eval/longmemeval_cloud24_final.json"),
]


def load(path):
    data = json.load(open(path, encoding="utf-8"))
    summary = data["summary"]
    rows = data["results"]
    return summary, rows


print(f"{'run':46} {'acc':>7} {'turns':>6} {'claims':>7} {'errors':>6}")
for label, path in RUNS:
    try:
        summary, rows = load(path)
    except FileNotFoundError:
        print(f"{label:46} (not run)")
        continue
    print(f"{label:46} {summary['accuracy']:7.3f} "
          f"{summary.get('turns_ingested', 0):6} "
          f"{summary.get('claims_created', 0):7} "
          f"{summary.get('ingest_errors', 0):6}")

# per-question evolution matrix
try:
    matrix = {}
    for label, path in RUNS:
        try:
            _, rows = load(path)
        except FileNotFoundError:
            continue
        for row in rows:
            matrix.setdefault(row["question"], {})[label[0]] = (
                "Y" if row["correct"] else ".")
    print("\nper-question (Y=correct):")
    header = "question".ljust(52) + " " + " ".join(r[0][0] for r in RUNS)
    print(header)
    for question in sorted(matrix):
        marks = matrix[question]
        print(question[:50].ljust(52) + " " + " ".join(
            marks.get(label[0][0], "-") for label, _ in RUNS))
except Exception as exc:
    print("matrix skipped:", exc)
