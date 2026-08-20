from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from schema import Schema, Table
from query_parser import AttrRef, ParsedQuery


class SemanticError(Exception):
    pass


@dataclass
class ResolvedTableRef:
    ref_name: str
    table: Table


@dataclass
class ResolvedAttrRef:
    # table cuva ime reference iz upita: alias ako postoji, inace ime tabele.
    table: str
    name: str
    type: str
    base_table: str

    def __str__(self) -> str:
        return f"{self.table}.{self.name}"


@dataclass
class ResolvedCondition:
    left: ResolvedAttrRef
    operator: str
    right: object
    is_join: bool


@dataclass
class ResolvedOrderBy:
    attr: ResolvedAttrRef
    descending: bool


@dataclass
class ValidatedQuery:
    select_attrs: List[ResolvedAttrRef]
    tables: List[ResolvedTableRef]
    selection_conditions: List[ResolvedCondition]
    join_conditions: List[ResolvedCondition]
    order_by: Optional[ResolvedOrderBy]
    raw: str


_TYPE_GROUPS = {
    "STRING": "STRING",
    "INT": "NUMBER",
    "DOUBLE": "NUMBER",
    "DATE": "DATE",
}


def _resolve_attr(
    attr_ref: AttrRef,
    alias_map: Dict[str, Table],
    resolved_tables: List[ResolvedTableRef],
) -> ResolvedAttrRef:
    """Razresava atribut na konkretnu referencu tabele iz FROM klauzule."""

    if attr_ref.table:
        table = alias_map.get(attr_ref.table)
        if table is None:
            raise SemanticError(
                f"Tabela/alias '{attr_ref.table}' iz reference '{attr_ref}' "
                f"nije naveden u FROM klauzuli."
            )

        attribute = table.get_attribute(attr_ref.name)
        if attribute is None:
            raise SemanticError(
                f"Atribut '{attr_ref.name}' ne postoji u tabeli '{table.name}'."
            )

        return ResolvedAttrRef(
            table=attr_ref.table,
            name=attribute.name,
            type=attribute.type,
            base_table=table.name,
        )

    matches = []
    for resolved_table in resolved_tables:
        attribute = resolved_table.table.get_attribute(attr_ref.name)
        if attribute is not None:
            matches.append((resolved_table, attribute))

    if not matches:
        raise SemanticError(
            f"Atribut '{attr_ref.name}' ne postoji ni u jednoj tabeli "
            f"iz FROM klauzule."
        )

    if len(matches) > 1:
        names = ", ".join(item.ref_name for item, _ in matches)
        raise SemanticError(
            f"Atribut '{attr_ref.name}' je viseznacan - postoji u vise "
            f"referenci tabela ({names}). Kvalifikujte ga imenom tabele "
            f"ili aliasom."
        )

    resolved_table, attribute = matches[0]
    return ResolvedAttrRef(
        table=resolved_table.ref_name,
        name=attribute.name,
        type=attribute.type,
        base_table=resolved_table.table.name,
    )


def _check_type_compat(left_type: str, right_type: str, context: str) -> None:
    left_group = _TYPE_GROUPS.get(left_type, left_type)
    right_group = _TYPE_GROUPS.get(right_type, right_type)

    if left_group != right_group:
        raise SemanticError(
            f"Tipovi nisu kompatibilni u uslovu {context}: "
            f"'{left_type}' naspram '{right_type}'."
        )


def _infer_constant_type(value: object) -> str:
    if isinstance(value, str):
        return "STRING"
    if isinstance(value, bool):
        raise SemanticError(f"Logicka konstanta nije podrzana: {value!r}")
    if isinstance(value, int):
        return "INT"
    if isinstance(value, float):
        return "DOUBLE"
    raise SemanticError(f"Ne mogu da odredim tip konstante: {value!r}")


def _validate_date_constant(value: object, context: str) -> None:
    if not isinstance(value, str):
        raise SemanticError(
            f"DATE konstanta u uslovu {context} mora biti zapisana "
            f"u navodnicima u formatu YYYY-MM-DD."
        )
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise SemanticError(
            f"Neispravna DATE konstanta u uslovu {context}: '{value}'. "
            f"Ocekivan format je YYYY-MM-DD."
        ) from exc


def validate_query(parsed: ParsedQuery, schema: Schema) -> ValidatedQuery:
    ref_names = [table_ref.ref_name for table_ref in parsed.tables]
    if len(set(ref_names)) != len(ref_names):
        raise SemanticError(
            "FROM klauzula sadrzi dupliranu referencu na tabelu "
            "(isto ime ili isti alias koriscen je vise puta)."
        )

    resolved_tables: List[ResolvedTableRef] = []
    alias_map: Dict[str, Table] = {}

    for table_ref in parsed.tables:
        table = schema.get_table(table_ref.name)
        if table is None:
            raise SemanticError(f"Tabela '{table_ref.name}' ne postoji u semi.")

        resolved_tables.append(
            ResolvedTableRef(ref_name=table_ref.ref_name, table=table)
        )
        alias_map[table_ref.ref_name] = table

    select_attrs = [
        _resolve_attr(attribute, alias_map, resolved_tables)
        for attribute in parsed.select_attrs
    ]

    selection_conditions: List[ResolvedCondition] = []
    join_conditions: List[ResolvedCondition] = []

    for condition in parsed.conditions:
        left = _resolve_attr(condition.left, alias_map, resolved_tables)

        if condition.is_join:
            right = _resolve_attr(condition.right, alias_map, resolved_tables)
            _check_type_compat(left.type, right.type, str(condition))

            if left.table == right.table:
                raise SemanticError(
                    f"Uslov '{condition}' poredi dva atributa iste reference "
                    f"tabele ('{left.table}'). Takav lokalni uslov atribut-atribut "
                    f"nije podrzan; za self-join koristite dva razlicita aliasa."
                )

            join_conditions.append(
                ResolvedCondition(
                    left=left,
                    operator=condition.operator,
                    right=right,
                    is_join=True,
                )
            )
        else:
            if left.type == "DATE":
                _validate_date_constant(condition.right, str(condition))
                constant_type = "DATE"
            else:
                constant_type = _infer_constant_type(condition.right)

            _check_type_compat(left.type, constant_type, str(condition))
            selection_conditions.append(
                ResolvedCondition(
                    left=left,
                    operator=condition.operator,
                    right=condition.right,
                    is_join=False,
                )
            )

    order_by = None
    if parsed.order_by:
        order_by = ResolvedOrderBy(
            attr=_resolve_attr(parsed.order_by.attr, alias_map, resolved_tables),
            descending=parsed.order_by.descending,
        )

    return ValidatedQuery(
        select_attrs=select_attrs,
        tables=resolved_tables,
        selection_conditions=selection_conditions,
        join_conditions=join_conditions,
        order_by=order_by,
        raw=parsed.raw,
    )
