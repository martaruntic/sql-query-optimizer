
import math
from dataclasses import dataclass
from typing import Optional, List


HASH_ACCESS_COST = 1.2


@dataclass
class CostResult:
    algorithm: str
    cost: float
    explanation: str



# SELECT

#a1
def select_linear_scan(b: int, is_key_equality: bool = False) -> CostResult:

    if is_key_equality:
        cost = math.ceil(b / 2)
        return CostResult("Linearna pretraga (A1, jednakost nad kljucem)", cost,
                           f"ceil(b/2) = ceil({b}/2) = {cost}")
    return CostResult("Linearna pretraga (A1)", b, f"b = {b}")

#a2
def select_primary_btree_key_equality(height: int) -> CostResult:

    cost = height + 1
    return CostResult("Primarni B+ indeks, jednakost nad kljucem (A2)", cost,
                       f"hi + 1 = {height} + 1 = {cost}")

#a3
def select_primary_btree_nonkey_equality(height: int, matching_blocks: int) -> CostResult:

    cost = height + matching_blocks
    return CostResult("Primarni B+ indeks, jednakost nad ne-kljucnim atributom (A3)", cost,
                       f"hi + b = {height} + {matching_blocks} = {cost}")

#a4
def select_secondary_index_key_equality(height_or_const: int, is_hash: bool = False) -> CostResult:

    if is_hash:
        return CostResult("Sekundarni hes indeks, jednakost nad kljucem (A4)", HASH_ACCESS_COST,
                           f"c = {HASH_ACCESS_COST} (konstanta za pristup baketu heš indeksa, "
                           f"prema primeru sa predavanja)")
    cost = height_or_const + 1
    return CostResult("Sekundarni B+ indeks, jednakost nad kljucem (A4)", cost,
                       f"hi + 1 = {height_or_const} + 1 = {cost}")

#a4
def select_secondary_index_nonkey_equality(height_or_const: int, matching_records: int,
                                            is_hash: bool = False) -> CostResult:

    base = HASH_ACCESS_COST if is_hash else height_or_const
    label = "hes" if is_hash else "B+"
    cost = base + matching_records
    return CostResult(f"Sekundarni {label} indeks, jednakost nad ne-kljucnim atributom (A4)", cost,
                       f"{'c' if is_hash else 'hi'} + n = {base} + {matching_records} = {cost}"
                       f"  (n = broj odgovarajucih slogova, nije klasterovan)")

#a5
def select_primary_btree_comparison(height: int, matching_blocks: int) -> CostResult:

    cost = height + matching_blocks
    return CostResult("Primarni B+ indeks, poredjenje (A5)", cost,
                       f"hi + b = {height} + {matching_blocks} = {cost}")

#a6
def select_secondary_btree_comparison(height: int, matching_records: int) -> CostResult:

    cost = height + matching_records
    return CostResult("Sekundarni B+ indeks, poredjenje (A6)", cost,
                       f"hi + n = {height} + {matching_records} = {cost}")

#pomocna funkcija za a7 i a8
def select_best_for_condition(table, condition, buffer_blocks: int) -> CostResult:

    attr_name = condition.left.name
    attr = table.get_attribute(attr_name)
    op = condition.operator
    is_equality = (op == "=")

    candidates = []

    # a1 difolt
    candidates.append(select_linear_scan(table.block_count,
                                          is_key_equality=(is_equality and attr.unique)))


    if is_equality: #uniformno
        sel_fraction = 1.0 / attr.distinct_values if attr.distinct_values else 1.0
    else:
        # poredjenje (>,>=,<,<=) - bez min/max ili histograma koristi se procena 1/2 tabele
        sel_fraction = 1.0 / 2.0

    matching_records = max(1, math.ceil(table.row_count * sel_fraction))
    matching_blocks = max(1, math.ceil(table.block_count * sel_fraction))

    for idx in table.indexes_on(attr_name):
        if idx.is_composite() and idx.is_hash():
            continue
        if idx.clustered:
            if is_equality and attr.unique:
                r = select_primary_btree_key_equality(idx.tree_height)
            elif is_equality:
                r = select_primary_btree_nonkey_equality(idx.tree_height, matching_blocks)
            else:
                r = select_primary_btree_comparison(idx.tree_height, matching_blocks)
        else:
            if is_equality and attr.unique:
                r = select_secondary_index_key_equality(
                    idx.tree_height if idx.is_btree() else 0, is_hash=idx.is_hash())
            elif is_equality:
                r = select_secondary_index_nonkey_equality(
                    idx.tree_height if idx.is_btree() else 0, matching_records,
                    is_hash=idx.is_hash())
            elif idx.is_btree():
                r = select_secondary_btree_comparison(idx.tree_height, matching_records)
            else:
                continue
        if idx.is_composite():
            r = CostResult(
                f"Kompozitni B+ indeks {idx.attributes} - prefiks pretraga preko "
                f"vodeceg atributa '{attr_name}' ({r.algorithm.split('(')[-1].rstrip(')')})",
                r.cost,
                r.explanation + f" (koristi se samo vodeci atribut kompozitnog "
                f"indeksa '{idx.name}': {idx.attributes}; preostali atributi "
                f"indeksa se ne koriste jer nisu deo ovog uslova)"
            )
        candidates.append(r)

    return min(candidates, key=lambda c: c.cost)


#a7 i a8
def select_best_for_conditions(table, conditions, buffer_blocks: int) -> CostResult:

    if not conditions:
        return select_linear_scan(table.block_count)

    # a7
    candidates = [select_best_for_condition(table, c, buffer_blocks) for c in conditions]


    equalities = {c.left.name: c for c in conditions if c.operator == "="}

    for idx in table.indexes:
        if not idx.is_composite():
            continue
        if not all(attr_name in equalities for attr_name in idx.attributes):
            continue


        denominator = 1
        for attr_name in idx.attributes:
            attr = table.get_attribute(attr_name)
            denominator *= max(1, attr.distinct_values)

        matching_records = max(1, math.ceil(table.row_count / denominator))
        matching_blocks = max(1, math.ceil(matching_records / table.rows_per_block))

        if idx.is_hash():
            bucket_access = HASH_ACCESS_COST
            if idx.clustered:
                cost = bucket_access + matching_blocks
                placement = "grupisuci"
                data_part = f"{matching_blocks} blokova sa odgovarajucim zapisima"
            else:
                cost = bucket_access + matching_records
                placement = "negrupisuci"
                data_part = (
                    f"{matching_records} odgovarajucih zapisa "
                    f"(najvise po jedan blok po zapisu)"
                )

            candidates.append(CostResult(
                f"Kompozitni HASH indeks '{idx.name}' nad {idx.attributes} (A8)",
                cost,
                f"Svi atributi kompozitnog kljuca zadati su jednakostima; "
                f"procena pogodaka = ceil({table.row_count}/{denominator}) "
                f"= {matching_records}. Cena = {HASH_ACCESS_COST} pristupa "
                f"baketu + {data_part} = {cost} ({placement} indeks)."
            ))

        elif idx.is_btree():

            if idx.clustered:
                cost = idx.tree_height + matching_blocks
                placement = "grupisuci"
                data_part = f"{matching_blocks} blokova sa odgovarajucim zapisima"
                formula = "hi + b"
            else:
                cost = idx.tree_height + matching_records
                placement = "negrupisuci"
                data_part = (
                    f"{matching_records} odgovarajucih zapisa "
                    f"(najvise po jedan blok po zapisu)"
                )
                formula = "hi + n"

            candidates.append(CostResult(
                f"Kompozitni B+ indeks '{idx.name}' nad {idx.attributes} (A8)",
                cost,
                f"Sve komponente kompozitnog kljuca zadate su jednakostima; "
                f"procena pogodaka = ceil({table.row_count}/{denominator}) "
                f"= {matching_records}. Cena = {formula} = {idx.tree_height} + "
                f"{matching_blocks if idx.clustered else matching_records} = {cost} "
                f"({placement} indeks; {data_part})."
            ))

    return min(candidates, key=lambda c: c.cost)





# SPOLJNO OBRADJENO SORTIRANJE


def external_merge_sort_cost(b: int, buffer_blocks: int) -> CostResult:
    # 2*b*(1 + ceil(log_(M-1)(ceil(b/M))))
    M = buffer_blocks
    if b <= M:
        cost = 2 * b
        return CostResult("Spoljno sortiranje (staje u bafer, 1 faza)", cost,
                           f"2*b = 2*{b} = {cost}")

    initial_runs = math.ceil(b / M)
    merge_passes = math.ceil(math.log(initial_runs, M - 1)) if initial_runs > 1 else 0
    cost = 2 * b * (merge_passes + 1)
    return CostResult("Spoljno obradjeno sortiranje (obedinjavanjem)", cost,
                       f"pocetnih nizova = ceil(b/M) = ceil({b}/{M}) = {initial_runs}; "
                       f"broj faza obedinjavanja = ceil(log_(M-1)({initial_runs})) = {merge_passes}; "
                       f"2*b*(faze+1) = 2*{b}*({merge_passes}+1) = {cost}")


# SPAJANJE


def join_nested_loop(nr: int, br: int, bs: int) -> CostResult:

    cost = nr * bs + br
    return CostResult("Spajanje ugnjezdenom petljom", cost,
                       f"nr*bs + br = {nr}*{bs} + {br} = {cost}  (r je spoljna relacija)")


def join_block_nested_loop(br: int, bs: int, buffer_blocks: int) -> CostResult:
    M = buffer_blocks
    usable = max(1, M - 2)
    outer_passes = math.ceil(br / usable)
    cost = outer_passes * bs + br
    return CostResult("Spajanje blok ugnjezdenom petljom", cost,
                       f"ceil(br/(M-2))*bs + br = ceil({br}/{usable})*{bs} + {br} "
                       f"= {outer_passes}*{bs} + {br} = {cost}  (r je spoljna relacija)")


def join_indexed_nested_loop(br: int, nr: int, index_lookup_cost: float) -> CostResult:
    cost = math.ceil(br + nr * index_lookup_cost)
    return CostResult("Spajanje indeksnom ugnjezdenom petljom", cost,
                       f"br + nr*c = {br} + {nr}*{index_lookup_cost} = {cost}  "
                       f"(c = cena jedne pretrage indeksa nad unutrasnjom relacijom)")


def join_merge(br: int, bs: int, sort_cost_r: int = 0, sort_cost_s: int = 0) -> CostResult:

    cost = br + bs + sort_cost_r + sort_cost_s
    detail = f"br + bs = {br} + {bs}"
    if sort_cost_r or sort_cost_s:
        detail += f" + cena_sortiranja_r({sort_cost_r}) + cena_sortiranja_s({sort_cost_s})"
    detail += f" = {cost}"
    return CostResult("Obedinjeno (merge) spajanje", cost, detail)


def join_hash(br: int, bs: int, buffer_blocks: int) -> CostResult:

    M = buffer_blocks
    build = min(br, bs)
    nh = max(1, M - 1)      # broj particije
    levels = 0
    size = build
    while size > max(1, M - 2) and levels < 30:
        size = size / nh
        levels += 1

    if levels <= 1:
        cost = 3 * (br + bs)
        return CostResult(
            "Hes spajanje", cost,
            f"3*(br+bs) = 3*({br}+{bs}) = {cost}  (M={M}: manja relacija "
            f"({build} blokova, particionisana u nh={nh} particija) staje u "
            f"M-2={max(1, M-2)} blokova bafera - BEZ rekurzivnog particionisanja)"
        )

    cost = 2 * (br + bs) * (levels - 1) + 3 * (br + bs)
    return CostResult(
        "Hes spajanje (rekurzivno particionisanje)", cost,
        f"2*(br+bs)*(nivoi-1) + 3*(br+bs) = 2*({br}+{bs})*{levels - 1} + "
        f"3*({br}+{bs}) = {cost}  (M={M}: manja relacija od {build} blokova "
        f"NE staje u M-2={max(1, M-2)} nakon deljenja na nh={nh} particija, "
        f"pa je potrebno rekurzivno particionisanje kroz {levels} nivoa)"
    )


def list_join_candidates(nr: int, br: int, ns: int, bs: int, buffer_blocks: int,
                          s_index_lookup_cost: Optional[float] = None,
                          r_index_lookup_cost: Optional[float] = None,
                          can_merge_sorted: bool = False,
                          sort_cost_r: int = 0, sort_cost_s: int = 0) -> List[CostResult]:


    candidates = []


    candidates.append(join_nested_loop(nr, br, bs))
    candidates.append(join_nested_loop(ns, bs, br))

    candidates.append(join_block_nested_loop(br, bs, buffer_blocks))
    candidates.append(join_block_nested_loop(bs, br, buffer_blocks))

    if s_index_lookup_cost is not None:
        candidates.append(join_indexed_nested_loop(br, nr, s_index_lookup_cost)) #indeksna ugnjezdena petlja
    if r_index_lookup_cost is not None:
        candidates.append(join_indexed_nested_loop(bs, ns, r_index_lookup_cost))

    # obedinjeno spajanje
    candidates.append(join_merge(br, bs,
                                  0 if can_merge_sorted else sort_cost_r,
                                  0 if can_merge_sorted else sort_cost_s))

    # hes spajanje
    candidates.append(join_hash(br, bs, buffer_blocks))
    return candidates


def choose_best_join(nr: int, br: int, ns: int, bs: int, buffer_blocks: int,
                      s_index_lookup_cost: Optional[float] = None,
                      r_index_lookup_cost: Optional[float] = None,
                      can_merge_sorted: bool = False,
                      sort_cost_r: int = 0, sort_cost_s: int = 0) -> CostResult:

    candidates = list_join_candidates(
        nr, br, ns, bs, buffer_blocks, s_index_lookup_cost, r_index_lookup_cost,
        can_merge_sorted, sort_cost_r, sort_cost_s)
    #vraca najjeftiniji
    return min(candidates, key=lambda c: c.cost)


if __name__ == "__main__":
    print(select_linear_scan(100))
    print(select_primary_btree_key_equality(3))
    print(external_merge_sort_cost(800, 10))
    print(join_block_nested_loop(100, 800, 10))
    print(join_hash(100, 800, 10))
