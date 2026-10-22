"""Benchmark the app's exact Ollama request against a frozen cached-text sample.

Run from the project root with its virtualenv Python. This script never writes to
the application database. Results are appended after each request so interrupted
runs can resume. Sample rows marked random are a seeded population sample; the
remaining rows deliberately cover difficult wording and long inputs.
"""

import argparse
import ast
import hashlib
import json
from pathlib import Path
import sys
import time

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.description import Extraction, source_sentences, validate_extraction
from app.config import LLM_THREADS, LLM_TIMEOUT


def request_template():
    """Read the current production prompt and request without changing the app."""
    tree = ast.parse(Path("app/description.py").read_text())
    analyze = next(node for node in ast.walk(tree)
                   if isinstance(node, ast.AsyncFunctionDef) and node.name == "analyze")
    prompt = next(node.value.value for node in ast.walk(analyze)
                  if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Name) and target.id == "prompt"
                          for target in node.targets))
    post = next(node for node in ast.walk(analyze) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute) and node.func.attr == "post")
    payload = next(keyword.value for keyword in post.keywords if keyword.arg == "json")
    return prompt, compile(ast.Expression(payload), "production_request", "eval")


def main():
    """Warm each model, then measure sequential uncached extraction requests."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("models", nargs="+")
    parser.add_argument("--url", default="http://localhost:11434")
    parser.add_argument("--sample", default="benchmarks/cached_sample.json")
    parser.add_argument("--output", default="benchmarks/results.jsonl")
    args = parser.parse_args()
    sample = json.loads(Path(args.sample).read_text())
    output = Path(args.output)
    done = {(row["model"], row["id"]) for line in output.read_text().splitlines()
            for row in [json.loads(line)]} if output.exists() else set()
    prompt, template = request_template()
    with httpx.Client(timeout=LLM_TIMEOUT) as client:
        metadata = {"tags": client.get(args.url + "/api/tags").json(),
                    "version": client.get(args.url + "/api/version").json(),
                    "threads": LLM_THREADS, "prompt": prompt,
                    "description_module_sha256": hashlib.sha256(
                        Path("app/description.py").read_bytes()).hexdigest()}
        output.with_suffix(".metadata.json").write_text(json.dumps(metadata, indent=2))
        for model in args.models:
            if all((model, row["id"]) in done for row in sample):
                continue
            started = time.perf_counter()
            warm = client.post(args.url + "/api/chat", json={"model": model,
                "messages": [{"role": "user", "content": "Reply OK"}],
                "think": False, "stream": False, "options": {"num_predict": 8},
                "keep_alive": "5m"})
            warm.raise_for_status()
            warm_seconds = time.perf_counter() - started
            resident = {"warmup_seconds": warm_seconds,
                        "ps": client.get(args.url + "/api/ps").json()}
            output.with_name(model.replace(":", "_") + ".resident.json").write_text(
                json.dumps(resident, indent=2))
            print(model, "warmup seconds", round(warm_seconds, 2), flush=True)
            for index, row in enumerate(sample, 1):
                if (model, row["id"]) in done:
                    continue
                source = row["description"][:12000]
                payload = eval(template, {"LLM_MODEL": model, "LLM_THREADS": LLM_THREADS,
                    "Extraction": Extraction, "prompt": prompt, "source": source,
                    "source_sentences": source_sentences, "json": json})
                result = {"model": model, "id": row["id"], "group": row["group"]}
                started = time.perf_counter()
                try:
                    response = client.post(args.url + "/api/chat", json=payload)
                    response.raise_for_status()
                    data = response.json()
                    result["content"] = data["message"]["content"]
                    result["raw"] = Extraction.model_validate_json(result["content"]).model_dump()
                    result["facts"] = validate_extraction(result["content"], source)
                    result["timings"] = {key: data[key] for key in (
                        "total_duration", "load_duration", "prompt_eval_count",
                        "prompt_eval_duration", "eval_count", "eval_duration", "done_reason")
                        if key in data}
                except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
                    result["error"] = str(error)
                result["seconds"] = time.perf_counter() - started
                with output.open("a") as handle:
                    handle.write(json.dumps(result, ensure_ascii=False) + "\n")
                print(model, index, "/", len(sample), row["id"],
                      round(result["seconds"], 2), result.get("error", "ok"), flush=True)


if __name__ == "__main__":
    main()
