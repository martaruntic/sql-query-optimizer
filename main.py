
import json
import os
import sys

from optimizer import _fmt, build_plan
from query_parser import QuerySyntaxError, parse_query
from schema import Schema
from validator import SemanticError, validate_query


def print_plan(steps, total_cost, query_text):
    print("=" * 70)
    print("UPIT:")
    print(" ", query_text)
    print("=" * 70)
    for i, step in enumerate(steps, 1):
        print(f"{i}. {step.detail}")
        print(f"   Algoritam: {step.algorithm}, cena: {_fmt(step.cost)} blok transfera")
        if step.explanation:
            print(f"   Obrazlozenje: {step.explanation}")
        print(f"   Rezultat koraka: ~{step.output_rows} redova (~{step.output_blocks} blokova)")
    print("-" * 70)
    final = steps[-1]
    print(f"UKUPNA CENA: {_fmt(total_cost)} blok transfera")
    print(f"Rezultat: ~{final.output_rows} redova (~{final.output_blocks} blokova)")


def resolve_query_text(query_arg, schema_json):
    if query_arg:
        if os.path.isfile(query_arg):
            with open(query_arg, "r", encoding="utf-8") as f:
                return f.read().strip()
        return query_arg
    return schema_json.get("query")


def run(schema_path, query_arg=None):
    with open(schema_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    schema = Schema.from_json(raw)
    query = resolve_query_text(query_arg, raw)
    if not query:
        raise SystemExit('Upotreba: python main.py sema.json "SELECT ..."')
    try:
        validated = validate_query(parse_query(query), schema)
        steps, total = build_plan(validated, schema.buffer_blocks)
    except QuerySyntaxError as exc:
        raise SystemExit(f"Greska pri parsiranju upita: {exc}")
    except SemanticError as exc:
        raise SystemExit(f"Neispravan ulazni upit: {exc}")
    print_plan(steps, total, query)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit('Upotreba: python main.py sema.json ["SELECT ..." | upit.sql]')
    run(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
