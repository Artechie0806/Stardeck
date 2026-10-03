"""Widget-engine eval harness. Runs each question through the same pipeline the app uses
(table selection, prompt, self-correction, chart fixing) and compares the result rows with
gold SQL. Cases may also set `chart` to check the final chart type, and `also_accept` for other
defensible readings of an ambiguous question.

    uv run python evals/run_eval.py                       # with definitions
    uv run python evals/run_eval.py --no-definitions      # measure what definitions are worth
    uv run python evals/run_eval.py --model qwen3.5:9b    # compare models

Writes a JSON report to evals/reports/.
"""

import argparse
import json
import time
from datetime import datetime
from itertools import combinations
from pathlib import Path

import yaml
from sqlalchemy import create_engine, text

from stardeck.config import get_settings
from stardeck.definitions import Definitions
from stardeck.demo import retail
from stardeck.llm import LLMClient, LLMError
from stardeck.schema import introspect
from stardeck.widgets import WidgetError, generate_widget

EVALS_DIR = Path(__file__).parent


def _norm(value) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return f"{value:.2f}"  # 5 and 5.0 compare equal
    return str(value).strip()


def normalize(rows: list[tuple], ordered: bool) -> list[tuple]:
    """Make results comparable regardless of column aliases, column order and (optionally) row order."""
    out = [tuple(sorted(_norm(v) for v in row)) for row in rows]
    return out if ordered else sorted(out)


def results_match(got: list[tuple], gold: list[tuple], ordered: bool) -> bool:
    """True if some subset of the model's columns reproduces the gold result.

    Extra helper columns (e.g. a code next to its label) are fine; missing or wrong values are not.
    """
    target = normalize(gold, ordered)
    width = len(gold[0]) if gold else 0
    if not got or not gold:
        return normalize(got, ordered) == target
    for cols in combinations(range(len(got[0])), width):
        if normalize([tuple(r[i] for i in cols) for r in got], ordered) == target:
            return True
    return False


def run_query(engine, sql: str) -> list[tuple]:
    with engine.connect() as conn:
        return [tuple(r) for r in conn.execute(text(sql)).all()]


def run_case(client, engine, tables, defs, case: dict, max_fixes: int) -> dict:
    ordered = case.get("ordered", False)
    result = {"id": case["id"], "question": case["question"],
              "needs_definitions": case.get("needs_definitions", False), "passed": False}
    t0 = time.perf_counter()
    gold = run_query(engine, case["gold_sql"])
    # Ambiguous questions list other defensible readings (e.g. "orders" = all vs completed only).
    alternatives = [run_query(engine, sql) for sql in case.get("also_accept", [])]
    try:
        w = generate_widget(client, engine, "sqlite", tables, defs, case["question"], max_attempts=max_fixes + 1)
    except (LLMError, WidgetError) as exc:
        result.update(error=str(exc), sql=getattr(exc, "sql", None))
    else:
        got = [tuple(r) for r in w.result.rows]
        passed = any(results_match(got, g, ordered) for g in [gold, *alternatives])
        result.update(sql=w.sql, attempts=w.attempts, passed=passed, error=None, chart=w.spec["type"],
                      date_filter=w.date_filter)
        if "chart" in case:
            result["chart_ok"] = w.spec["type"] in case["chart"].split("|")
        if not passed:
            result["got_preview"] = normalize(got, ordered)[:5]
            result["gold_preview"] = normalize(gold, ordered)[:5]
    result["latency_s"] = round(time.perf_counter() - t0, 2)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cases", default=str(EVALS_DIR / "cases" / "retail.yaml"))
    parser.add_argument("--no-definitions", action="store_true")
    parser.add_argument("--model", help="Override STARDECK_LLM_MODEL")
    parser.add_argument("--base-url", help="Override STARDECK_LLM_BASE_URL")
    parser.add_argument("--only", help="Comma-separated case ids")
    parser.add_argument("--max-fixes", type=int, default=2, help="Self-correction retries on SQL errors")
    args = parser.parse_args()

    suite_path = Path(args.cases)
    suite = yaml.safe_load(suite_path.read_text())
    db_path = (EVALS_DIR / suite["database"]).resolve()
    retail.ensure(db_path)

    engine = create_engine(f"sqlite:///file:{db_path}?mode=ro&uri=true")
    defs = None
    if not args.no_definitions and suite.get("definitions"):
        defs = Definitions.load(EVALS_DIR / suite["definitions"])
    tables = introspect(engine)

    settings = get_settings().model_copy(update={
        k: v for k, v in {"llm_model": args.model, "llm_base_url": args.base_url}.items() if v
    })
    client = LLMClient(settings)

    cases = suite["cases"]
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in wanted]

    results = []
    for case in cases:
        r = run_case(client, engine, tables, defs, case, args.max_fixes)
        results.append(r)
        mark = "PASS" if r["passed"] else "FAIL"
        chart = r.get("chart", "")
        if r.get("chart_ok") is False:
            chart += " (unexpected)"
        print(f"[{mark}] {r['id']:<26} {r['latency_s']:>6.1f}s  {chart:<20} {r.get('error') or ''}")

    def score(rs):
        return f"{sum(r['passed'] for r in rs)}/{len(rs)}" if rs else "-"

    summary = {
        "model": settings.llm_model,
        "definitions": defs is not None,
        "overall": score(results),
        "needs_definitions": score([r for r in results if r["needs_definitions"]]),
        "plain": score([r for r in results if not r["needs_definitions"]]),
        "chart_ok": score([{"passed": r["chart_ok"]} for r in results if "chart_ok" in r]),
        "avg_latency_s": round(sum(r["latency_s"] for r in results) / max(len(results), 1), 2),
    }
    print("\n" + json.dumps(summary, indent=2))

    reports = EVALS_DIR / "reports"
    reports.mkdir(exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    tag = settings.llm_model.replace("/", "_").replace(":", "_")
    out = reports / f"{suite_path.stem}-{tag}-{'defs' if defs else 'nodefs'}-{stamp}.json"
    out.write_text(json.dumps({"summary": summary, "results": results}, indent=2, default=str))
    print(f"Report: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
