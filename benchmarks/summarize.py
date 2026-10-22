"""Summarize cached-description benchmark timings and manually reviewed labels."""

import json
import math
import argparse
from pathlib import Path
import statistics


def main():
    """Write aggregate scores and preserve every mismatch for source review."""
    directory = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=directory / "results.jsonl")
    parser.add_argument("--output", type=Path, default=directory / "summary.json")
    args = parser.parse_args()
    labels = json.loads((directory / "labels.json").read_text())["labels"]
    results = [json.loads(line) for line in args.results.read_text().splitlines()]
    summary = {}
    for model in dict.fromkeys(row["model"] for row in results):
        rows = [row for row in results if row["model"] == model]
        ready = [row for row in rows if "facts" in row]
        times = sorted(row["seconds"] for row in rows)
        scores = {}
        for group in ["all", "random", "challenge", "long"]:
            subset = [row for row in ready if group == "all" or row["group"] == group]
            fields = {}
            for key in next(iter(labels.values())):
                correct = sum(row["facts"][key]["value"] == labels[row["id"]][key] for row in subset)
                tp = sum(row["facts"][key]["value"] is True and labels[row["id"]][key] is True for row in subset)
                fp = sum(row["facts"][key]["value"] is True and labels[row["id"]][key] is not True for row in subset)
                fn = sum(row["facts"][key]["value"] is not True and labels[row["id"]][key] is True for row in subset)
                fields[key] = {"correct": correct, "n": len(subset), "tp": tp, "fp": fp, "fn": fn}
            scores[group] = {"fields": fields, "exact_adverts": sum(all(
                row["facts"][key]["value"] == value for key, value in labels[row["id"]].items()) for row in subset)}
        mismatches = [{"id": row["id"], "fields": {key: {
            "expected": value, "actual": row["facts"][key], "raw": row["raw"][key]}
            for key, value in labels[row["id"]].items() if row["facts"][key]["value"] != value}}
            for row in ready if any(row["facts"][key]["value"] != value
                                   for key, value in labels[row["id"]].items())]
        summary[model] = {"requests": len(rows), "errors": len(rows) - len(ready),
            "mean_seconds": statistics.mean(times), "median_seconds": statistics.median(times),
            "p95_seconds": times[math.ceil(len(times) * 0.95) - 1],
            "total_seconds": sum(times), "scores": scores, "mismatches": mismatches}
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    for model, value in summary.items():
        print(model, json.dumps({key: val for key, val in value.items() if key not in {"mismatches", "scores"}}))
        for group, score in value["scores"].items():
            print(group, "exact", score["exact_adverts"], "fields", score["fields"])


if __name__ == "__main__":
    main()
