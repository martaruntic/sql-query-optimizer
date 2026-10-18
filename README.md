# SQL Query Optimizer

A cost-based SQL query optimizer implemented in Python. Given a SQL query and database statistics in JSON format, the program builds a physical execution plan and estimates its I/O cost in block transfers.

The optimizer models query execution; it does not connect to a database or execute the SQL against stored records.

## Features

- Parses a supported subset of `SELECT` queries with `FROM`, `WHERE`, and optional `ORDER BY`.
- Validates table and attribute references against the supplied schema.
- Estimates selection cardinalities using attribute statistics.
- Compares access paths and join algorithms using estimated block I/O costs.
- Considers available indexes, buffer capacity, and intermediate relation sizes.
- Produces a step-by-step execution plan with the selected algorithm, estimated cost, and intermediate result size.

## Query support

The parser accepts:

- Column projections with `SELECT`.
- Up to four tables in `FROM`, including table aliases.
- Up to six `WHERE` conditions connected with `AND`.
- Comparisons using `=`, `<>`, `<`, `<=`, `>`, and `>=`.
- Join conditions expressed in `WHERE`.
- `ORDER BY` on one attribute, ascending or descending.

Explicit `JOIN` syntax, subqueries, `GROUP BY`, aggregates, and `OR` expressions are not supported.

## How it works

1. **Parsing:** `query_parser.py` converts SQL text into a structured representation of selected attributes, tables, predicates, and ordering.
2. **Validation:** `validator.py` resolves table and attribute references and checks the query against the supplied schema.
3. **Cost estimation:** `cost_formulas.py` estimates the I/O cost of alternative selection and join algorithms.
4. **Plan construction:** `optimizer.py` constructs a plan, tracks estimated intermediate relation sizes, and selects execution strategies based on estimated cost.
5. **Output:** `main.py` prints the plan steps, their algorithms and costs, and the estimated total cost.

The cost model uses metadata such as table cardinality, number of disk blocks, distinct attribute values, available indexes, and buffer size. Estimates are expressed in block transfers rather than measured execution time.

## Running the optimizer

Requires Python 3 and uses the Python standard library.

From the repository directory:

```bash
python main.py primer_ulaza.json "SELECT prosek FROM Student"
```

A query with a filter:

```bash
python main.py primer_ulaza.json "SELECT ime, smer FROM Student WHERE indeks = 'IS2022456'"
```

A query involving two tables:

```bash
python main.py primer_ulaza.json "SELECT i.ispitId, s.prosek FROM Ispit i, Student s WHERE i.studentIndeks = s.indeks"
```

The first argument is the JSON schema/statistics file. The second can be a SQL query string or the path to a text file containing a query. If omitted, the program reads the `query` field from the JSON input when present.

Additional commands and valid/invalid query examples are available in [`upiti.txt`](upiti.txt).

## Input data

The repository includes two example datasets:

- [`primer_ulaza.json`](primer_ulaza.json) — university-related tables
- [`primer.json`](primer.json) — airport, aircraft, and flight tables

Each input describes the buffer size and table metadata, including row and block counts, attributes, distinct-value estimates, and indexes. Index metadata includes the index type, indexed attributes, clustering information, and B+ tree height where applicable.

## Repository structure

| File | Purpose |
| --- | --- |
| `main.py` | Command-line entry point and plan output |
| `query_parser.py` | SQL parsing |
| `validator.py` | Semantic validation |
| `schema.py` | Database statistics and index metadata |
| `cost_formulas.py` | I/O cost calculations and algorithm candidates |
| `optimizer.py` | Execution-plan construction and cost-based choices |
| `primer.json`, `primer_ulaza.json` | Example input schemas |
| `upiti.txt` | Sample commands and query cases |
