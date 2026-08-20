
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import cost_formulas as cf
from schema import Table
from validator import ResolvedCondition, ResolvedTableRef, ValidatedQuery


@dataclass
class PlanStep:
    operation: str
    detail: str
    algorithm: str
    cost: float
    output_rows: int
    output_blocks: int
    explanation: str = ""

    @property
    def total_cost(self) -> float:
        return self.cost


@dataclass
class Relation:
    label: str
    row_count: int
    block_count: int
    rows_per_block: int
    source_tables: List[str] = field(default_factory=list)
    distinct_values: Dict[str, int] = field(default_factory=dict)
    # kljuc je Tabela.atribut
    base_table: Optional[Table] = None
    materialized: bool = False
    sorted_by: Optional[str] = None


def _fmt(x: float) -> str:
    return str(math.ceil(x - 1e-12))


def _blocks(rows: int, rows_per_block: int) -> int:
    if rows <= 0:
        return 0
    return math.ceil(rows / max(1, rows_per_block))


def _attr_key(table: str, attr: str) -> str:
    return f"{table}.{attr}"


def _base_relation(table: Table, ref_name: str) -> Relation:
    return Relation(
        label=ref_name,
        row_count=table.row_count,
        block_count=table.block_count,
        rows_per_block=table.rows_per_block,
        source_tables=[ref_name],
        distinct_values={
            _attr_key(ref_name, a.name): min(a.distinct_values, table.row_count)
            for a in table.attributes
        },
        base_table=table,
        materialized=False,
        sorted_by=(
            _attr_key(ref_name, table.clustered_index().attributes[0])
            if table.clustered_index() is not None
            else None
        ),
    )


def _condition_selectivity(table: Table, cond: ResolvedCondition) -> float:
    attr = table.get_attribute(cond.left.name)
    if cond.operator == "=":
        return 1.0 / max(1, attr.distinct_values)
    if cond.operator == "<>":
        return 1.0 - 1.0 / max(1, attr.distinct_values)
    return 0.5


def _update_distinct_after_selection(
    table: Table,
    conditions: List[ResolvedCondition],
    out_rows: int,
    ref_name: str,
) -> Dict[str, int]:

    result: Dict[str, int] = {}
    by_attr = {c.left.name: c for c in conditions}
    for attr in table.attributes:
        key = _attr_key(ref_name, attr.name)
        cond = by_attr.get(attr.name)
        if cond is not None and cond.operator == "=":
            result[key] = 1
        elif cond is not None:
            s = _condition_selectivity(table, cond)
            result[key] = max(1, min(out_rows, math.ceil(attr.distinct_values * s)))
        else:
            result[key] = max(1, min(attr.distinct_values, out_rows))
    return result


def plan_selection(
    table: Table,
    ref_name: str,
    conditions: List[ResolvedCondition],
    buffer_blocks: int,
    charge_full_scan: bool = False,
) -> Tuple[List[PlanStep], Relation]:

    if not conditions:
        rel = _base_relation(table, ref_name)
        if not charge_full_scan:
            return [], rel

        scan = PlanStep(
            operation="PRISTUP",
            detail=f"Pun pristup relaciji {ref_name} (bez lokalnog WHERE uslova)",
            algorithm="Linearno skeniranje",
            cost=table.block_count,
            output_rows=table.row_count,
            output_blocks=table.block_count,
            explanation=(
                f"Potrebno je pročitati svih b={table.block_count} blokova "
                f"bazne relacije {table.name} (referenca {ref_name})."
            ),
        )
        return [scan], rel

    best = cf.select_best_for_conditions(table, conditions, buffer_blocks)
    selectivity = 1.0
    for c in conditions:
        selectivity *= _condition_selectivity(table, c)
    out_rows = max(1, math.ceil(table.row_count * selectivity))
    out_blocks = _blocks(out_rows, table.rows_per_block)
    cond_text = " AND ".join(f"{c.left} {c.operator} {c.right!r}" for c in conditions)

    selection = PlanStep(
        operation="SELEKCIJA",
        detail=f"Selekcija nad {ref_name} ({cond_text})",
        algorithm=best.algorithm,
        cost=best.cost,
        output_rows=out_rows,
        output_blocks=out_blocks,
        explanation=(
            f"{best.explanation}. Selektivnost konjunkcije = {selectivity:.4f}; "
            f"procenjena kardinalnost = ceil({table.row_count} * {selectivity:.4f}) "
            f"= {out_rows} redova."
        ),
    )
    materialization = PlanStep(
        operation="MATERIJALIZACIJA",
        detail=f"Upis rezultata selekcije nad {ref_name} na disk ({out_blocks} blokova)",
        algorithm="Materijalizacija",
        cost=out_blocks,
        output_rows=out_rows,
        output_blocks=out_blocks,
        explanation=f"ceil({out_rows}/{table.rows_per_block}) = {out_blocks} blokova upisa.",
    )
    rel = Relation(
        label=f"σ({ref_name})",
        row_count=out_rows,
        block_count=out_blocks,
        rows_per_block=table.rows_per_block,
        source_tables=[ref_name],
        distinct_values=_update_distinct_after_selection(table, conditions, out_rows, ref_name),
        base_table=None,
        materialized=True,
    )
    return [selection, materialization], rel


def _index_lookup_cost_for_relation(rel: Relation, attr_name: str) -> Optional[float]:
    table = rel.base_table
    if table is None or rel.materialized:
        return None
    attr = table.get_attribute(attr_name)
    if attr is None:
        return None
    candidates: List[float] = []
    for idx in table.indexes_on(attr_name):
        if idx.is_composite():
            continue
        if idx.is_hash():
            avg = max(1, table.row_count / max(1, attr.distinct_values))
            candidates.append(cf.HASH_ACCESS_COST if attr.unique else cf.HASH_ACCESS_COST + avg)
        elif idx.is_btree():
            if attr.unique:
                candidates.append(idx.tree_height + 1)
            elif idx.clustered:
                avg_blocks = max(1, table.block_count / max(1, attr.distinct_values))
                candidates.append(idx.tree_height + avg_blocks)
            else:
                avg_rows = max(1, table.row_count / max(1, attr.distinct_values))
                candidates.append(idx.tree_height + avg_rows)
    return min(candidates) if candidates else None


def _join_attrs(
    left: Relation,
    right: Relation,
    cond: ResolvedCondition,
) -> Tuple[str, str]:
    left_tables = set(left.source_tables)
    if cond.left.table in left_tables and cond.right.table in right.source_tables:
        return _attr_key(cond.left.table, cond.left.name), _attr_key(cond.right.table, cond.right.name)
    if cond.right.table in left_tables and cond.left.table in right.source_tables:
        return _attr_key(cond.right.table, cond.right.name), _attr_key(cond.left.table, cond.left.name)
    raise ValueError("Uslov spajanja ne povezuje zadate relacije.")


def _estimate_join_rows(
    left: Relation,
    right: Relation,
    left_key: str,
    right_key: str,
    operator: str,
) -> Tuple[int, int, int]:
    v_left = max(1, left.distinct_values.get(left_key, left.row_count))
    v_right = max(1, right.distinct_values.get(right_key, right.row_count))

    if operator == "=":
        #  procena za ekvi-spajanje
        rows = max(
            1,
            math.ceil(
                left.row_count * right.row_count / max(v_left, v_right)
            ),
        )
    elif operator == "<>":
        equality_fraction = 1.0 / max(v_left, v_right)
        rows = max(
            1,
            math.ceil(
                left.row_count * right.row_count * (1.0 - equality_fraction)
            ),
        )
    else:

        # heuristika da polovina parova zadovoljava uslov
        rows = max(1, math.ceil(left.row_count * right.row_count * 0.5))

    return rows, v_left, v_right


def _sort_cost_final_output(b: int, M: int) -> cf.CostResult:
    if b <= M:
        return cf.CostResult(
            "Sortiranje u memoriji",
            b,
            f"Relacija staje u M={M} blokova; čitanje b={b}, finalni upis se ne računa.",
        )
    runs = math.ceil(b / M)
    merge_passes = math.ceil(math.log(runs, M - 1))
    # početna faza: 2b; svi međuprolazi 2b; poslednji prolaz samo b
    cost = 2 * b + max(0, merge_passes - 1) * 2 * b + b
    return cf.CostResult(
        "Spoljno sort-merge sortiranje",
        cost,
        f"početnih delova = ceil({b}/{M}) = {runs}; faza objedinjavanja = {merge_passes}; "
        f"cena = 2*{b} + {max(0, merge_passes-1)}*2*{b} + {b} = {cost} "
        f"(završni upis se ne računa posebno)",
    )


def plan_join(
    left: Relation,
    right: Relation,
    cond: ResolvedCondition,
    buffer_blocks: int,
) -> Tuple[List[PlanStep], Relation]:
    left_key, right_key = _join_attrs(left, right, cond)
    right_attr_name = right_key.split(".", 1)[1]
    index_lookup = _index_lookup_cost_for_relation(right, right_attr_name)

    left_sorted = left.sorted_by == left_key
    right_sorted = right.sorted_by == right_key
    sort_l = 0 if left_sorted else _sort_cost_final_output(left.block_count, buffer_blocks).cost
    sort_r = 0 if right_sorted else _sort_cost_final_output(right.block_count, buffer_blocks).cost

    candidates = cf.list_join_candidates(
        nr=left.row_count,
        br=left.block_count,
        ns=right.row_count,
        bs=right.block_count,
        buffer_blocks=buffer_blocks,
        s_index_lookup_cost=index_lookup,
        can_merge_sorted=left_sorted and right_sorted,
        sort_cost_r=sort_l,
        sort_cost_s=sort_r,
    )

    if cond.operator != "=":
        # za teta spajanje bez jednakosti nisu primenljivi hash, merge
        # ni indeksna ugnjezdena petlja, ostaju obicna i blok petlja
        candidates = [
            candidate
            for candidate in candidates
            if candidate.algorithm in (
                "Spajanje ugnjezdenom petljom",
                "Spajanje blok ugnjezdenom petljom",
            )
        ]

    best = min(candidates, key=lambda x: x.cost)
    summary = "; ".join(
        f"{c.algorithm}: {_fmt(c.cost)}" + (" <- izabrano" if c is best else "")
        for c in sorted(candidates, key=lambda x: x.cost)
    )

    out_rows, v_left, v_right = _estimate_join_rows(left, right, left_key, right_key, cond.operator)
    rows_per_block = min(left.rows_per_block, right.rows_per_block)
    out_blocks = _blocks(out_rows, rows_per_block)

    distinct = dict(left.distinct_values)
    distinct.update(right.distinct_values)
    joined_v = min(v_left, v_right, out_rows)
    distinct[left_key] = joined_v
    distinct[right_key] = joined_v
    for key in list(distinct):
        distinct[key] = min(distinct[key], out_rows)

    join_step = PlanStep(
        operation="SPAJANJE",
        detail=(f"Spajanje {{{','.join(left.source_tables)}}} i "
                f"{{{','.join(right.source_tables)}}} po: "
                f"{cond.left} {cond.operator} {cond.right}"),
        algorithm=best.algorithm,
        cost=best.cost,
        output_rows=out_rows,
        output_blocks=out_blocks,
        explanation=(
            f"Razmatrani algoritmi: {summary}. {best.explanation}. "
            + (
                f"Procena kardinalnosti ekvi-spajanja: "
                f"({left.row_count}*{right.row_count})/"
                f"max({v_left},{v_right}) = {out_rows} redova."
                if cond.operator == "="
                else f"Procena kardinalnosti teta-spajanja za operator "
                     f"'{cond.operator}' = {out_rows} redova, uz deklarisanu "
                     f"heuristiku zbog nedostatka zajednickih statistika."
            )
        ),
    )
    mat_step = PlanStep(
        operation="MATERIJALIZACIJA",
        detail=f"Upis rezultata spajanja na disk ({out_blocks} blokova)",
        algorithm="Materijalizacija",
        cost=out_blocks,
        output_rows=out_rows,
        output_blocks=out_blocks,
        explanation=f"ceil({out_rows}/{rows_per_block}) = {out_blocks} blokova upisa.",
    )
    rel = Relation(
        label=f"({left.label} ⋈ {right.label})",
        row_count=out_rows,
        block_count=out_blocks,
        rows_per_block=rows_per_block,
        source_tables=left.source_tables + right.source_tables,
        distinct_values=distinct,
        base_table=None,
        materialized=True,
    )
    return [join_step, mat_step], rel


def plan_cartesian(
    left: Relation,
    right: Relation,
    buffer_blocks: int,
) -> Tuple[List[PlanStep], Relation]:

    candidates = [
        cf.join_nested_loop(left.row_count, left.block_count, right.block_count),
        cf.join_nested_loop(right.row_count, right.block_count, left.block_count),
        cf.join_block_nested_loop(left.block_count, right.block_count, buffer_blocks),
        cf.join_block_nested_loop(right.block_count, left.block_count, buffer_blocks),
    ]
    best = min(candidates, key=lambda c: c.cost)
    summary = "; ".join(
        f"{c.algorithm}: {_fmt(c.cost)}" + (" <- izabrano" if c is best else "")
        for c in sorted(candidates, key=lambda c: c.cost)
    )

    out_rows = max(1, left.row_count * right.row_count)
    rows_per_block = min(left.rows_per_block, right.rows_per_block)
    out_blocks = _blocks(out_rows, rows_per_block)

    distinct = dict(left.distinct_values)
    distinct.update(right.distinct_values)
    for key in list(distinct):
        distinct[key] = min(distinct[key], out_rows)

    step = PlanStep(
        operation="DEKARTOV PROIZVOD",
        detail=(f"Dekartov proizvod {{{','.join(left.source_tables)}}} x "
                f"{{{','.join(right.source_tables)}}} (nema uslova spajanja)"),
        algorithm=best.algorithm,
        cost=best.cost,
        output_rows=out_rows,
        output_blocks=out_blocks,
        explanation=(
            f"Razmatrani algoritmi (samo ugnjezdena/blok ugnjezdena petlja - "
            f"nema atributa spajanja za merge/hes/indeksnu petlju): {summary}. "
            f"{best.explanation}. Kardinalnost Dekartovog proizvoda = |R|*|S| = "
            f"{left.row_count}*{right.row_count} = {out_rows} redova."
        ),
    )
    mat_step = PlanStep(
        operation="MATERIJALIZACIJA",
        detail=f"Upis rezultata Dekartovog proizvoda na disk ({out_blocks} blokova)",
        algorithm="Materijalizacija",
        cost=out_blocks,
        output_rows=out_rows,
        output_blocks=out_blocks,
        explanation=f"ceil({out_rows}/{rows_per_block}) = {out_blocks} blokova upisa.",
    )
    rel = Relation(
        label=f"({left.label} × {right.label})",
        row_count=out_rows,
        block_count=out_blocks,
        rows_per_block=rows_per_block,
        source_tables=left.source_tables + right.source_tables,
        distinct_values=distinct,
        base_table=None,
        materialized=True,
    )
    return [step, mat_step], rel


def _valid_join_orders(names: List[str]):

    def rec(remaining: set, order: list):
        if not remaining:
            yield list(order)
            return
        for name in sorted(remaining):
            yield from rec(remaining - {name}, order + [name])
    yield from rec(set(names), [])


def _evaluate_order(
    order: List[str],
    relations: Dict[str, Relation],
    joins: Dict[frozenset, ResolvedCondition],
    M: int,
):
    current = relations[order[0]]
    steps: List[PlanStep] = []
    for name in order[1:]:

        pair_cond = None
        for old in current.source_tables:
            key = frozenset((name, old))
            if key in joins:
                pair_cond = joins[key]
                break
        if pair_cond is not None:
            new_steps, current = plan_join(current, relations[name], pair_cond, M)
        else:
            new_steps, current = plan_cartesian(current, relations[name], M)
        steps.extend(new_steps)
    return steps, current, sum(s.cost for s in steps)


def build_plan(vq: ValidatedQuery, buffer_blocks: int) -> Tuple[List[PlanStep], float]:
    prefix_steps: List[PlanStep] = []
    relations: Dict[str, Relation] = {}

    single_table_query = len(vq.tables) == 1
    for table_ref in vq.tables:
        table = table_ref.table
        ref_name = table_ref.ref_name
        conditions = [
            c for c in vq.selection_conditions if c.left.table == ref_name
        ]

        charge_full_scan = (
            single_table_query and not conditions and vq.order_by is None
        )
        table_steps, rel = plan_selection(
            table,
            ref_name,
            conditions,
            buffer_blocks,
            charge_full_scan=charge_full_scan,
        )
        prefix_steps.extend(table_steps)
        relations[ref_name] = rel

    joins: Dict[frozenset, ResolvedCondition] = {
        frozenset((c.left.table, c.right.table)): c for c in vq.join_conditions
    }
    names = list(relations)

    if len(names) == 1:
        join_steps: List[PlanStep] = []
        current = relations[names[0]]
    else:
        best = None
        for order in _valid_join_orders(names):
            candidate_steps, candidate_rel, candidate_cost = _evaluate_order(
                order, relations, joins, buffer_blocks
            )
            if best is None or candidate_cost < best[2]:
                best = (candidate_steps, candidate_rel, candidate_cost)

        join_steps, current, _ = best

    steps = prefix_steps + join_steps

    if vq.order_by:
        order_key = _attr_key(vq.order_by.attr.table, vq.order_by.attr.name)
        already_sorted = (
            not vq.order_by.descending
            and current.sorted_by == order_key
        )
        if already_sorted:

            result = cf.CostResult(
                "Sekvencijalni pristup preko grupišućeg B+ indeksa",
                current.block_count,
                f"Ulaz je već fizički uređen po {order_key}; dodatno sortiranje "
                f"nije potrebno. Sekvencijalno se čita "
                f"{current.block_count} blokova, a završni upis se ne računa posebno.",
            )
        else:
            result = _sort_cost_final_output(current.block_count, buffer_blocks)
        steps.append(PlanStep(
            operation="SORTIRANJE",
            detail=f"ORDER BY {vq.order_by.attr}" + (" DESC" if vq.order_by.descending else ""),
            algorithm=result.algorithm,
            cost=result.cost,
            output_rows=current.row_count,
            output_blocks=current.block_count,
            explanation=result.explanation,
        ))
        current.sorted_by = order_key

    attrs = ", ".join(str(a) for a in vq.select_attrs)

    has_join = bool(join_steps)
    projection_reads_materialized = has_join and current.materialized and not vq.order_by
    projection_cost = current.block_count if projection_reads_materialized else 0

    if projection_reads_materialized:
        projection_algorithm = "Sekvencijalno čitanje materijalizovanog rezultata, bez eliminacije duplikata"
        projection_explanation = (
            f"Poslednji rezultat binarne operacije je materijalizovan na disku, pa projekcija "
            f"mora da pročita svih {current.block_count} blokova. Završni upis se "
            f"ne računa posebno; upit nema DISTINCT."
        )
    else:
        projection_algorithm = "Bez eliminacije duplikata (upit nema DISTINCT), bez dodatne cene"
        projection_explanation = (
            "Projekcija se primenjuje tokom već obračunatog čitanja ulaza ili "
            "tokom formiranja izlaza sortiranja; nema dodatnog upisa međurezultata."
        )

    steps.append(PlanStep(
        operation="PROJEKCIJA",
        detail=f"Projekcija: {attrs}",
        algorithm=projection_algorithm,
        cost=projection_cost,
        output_rows=current.row_count,
        output_blocks=current.block_count,
        explanation=projection_explanation,
    ))

    return steps, sum(s.cost for s in steps)
