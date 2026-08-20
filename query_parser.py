

import re
from dataclasses import dataclass, field
from typing import List, Optional, Union

OPERATORS = ["<=", ">=", "<>", "=", "<", ">"]


class QuerySyntaxError(Exception):
    pass


@dataclass
class AttrRef:

    table: Optional[str]
    name: str

    def __str__(self):
        return f"{self.table}.{self.name}" if self.table else self.name


@dataclass
class Condition:
    left: AttrRef
    operator: str
    right: Union[AttrRef, str, int, float]
    is_join: bool = field(init=False)

    def __post_init__(self):
        self.is_join = isinstance(self.right, AttrRef)

    def __str__(self):
        r = str(self.right) if isinstance(self.right, AttrRef) else repr(self.right)
        return f"{self.left} {self.operator} {r}"


@dataclass
class OrderBy:
    attr: AttrRef
    descending: bool = False


@dataclass
class TableRef:

    name: str
    alias: Optional[str] = None #ne mora

    @property
    def ref_name(self) -> str:
        #vrati ime koje cemo koristiit
        return self.alias if self.alias else self.name

    def __str__(self):
        return f"{self.name} {self.alias}" if self.alias else self.name


@dataclass
class ParsedQuery:
    select_attrs: List[AttrRef]
    tables: List[TableRef]
    conditions: List[Condition]
    order_by: Optional[OrderBy]
    raw: str


def _split_top_level(text: str, sep: str) -> List[str]:
    #separator van navodnia
    parts = []
    current = ""
    in_quotes = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "'":
            in_quotes = not in_quotes
            current += ch
        elif not in_quotes and text[i:i + len(sep)].upper() == sep.upper() and \
                (i == 0 or text[i - 1] == " ") and \
                (i + len(sep) >= len(text) or text[i + len(sep)] in " \t"):
            parts.append(current.strip())
            current = ""
            i += len(sep)
            continue
        else:
            current += ch
        i += 1
    parts.append(current.strip())
    return parts


def _parse_attr_ref(token: str) -> AttrRef:
    token = token.strip()
    if "." in token:
        table, name = token.split(".", 1)
        return AttrRef(table=table.strip(), name=name.strip())
    return AttrRef(table=None, name=token)


def _parse_value(token: str):
    token = token.strip()
    if token.startswith("'") and token.endswith("'"):
        return token[1:-1]
    try:
        if "." in token:
            return float(token)
        return int(token)
    except ValueError:

        return _parse_attr_ref(token)


def _parse_condition(text: str) -> Condition:
    text = text.strip()
    for op in OPERATORS:

        pattern = re.escape(op)
        m = re.search(pattern, text)
        if m:
            left_raw = text[:m.start()].strip()
            right_raw = text[m.end():].strip()
            if not left_raw or not right_raw:
                continue
            left = _parse_attr_ref(left_raw)
            right = _parse_value(right_raw)
            return Condition(left=left, operator=op, right=right)
    raise QuerySyntaxError(f"Ne mogu da prepoznam operator u uslovu: '{text}'")

#podeli upit
def parse_query(sql: str) -> ParsedQuery:
    raw = sql.strip().rstrip(";")
    original = raw


    sql_upper_check = raw.upper()
    if not sql_upper_check.startswith("SELECT"):
        raise QuerySyntaxError("Upit mora da pocne sa SELECT.")

    # podela upita na delove
    pattern = re.compile(
        r"SELECT\s+(?P<select>.+?)\s+FROM\s+(?P<from>.+?)"
        r"(?:\s+WHERE\s+(?P<where>.+?))?"
        r"(?:\s+ORDER\s+BY\s+(?P<orderby>.+?))?$",
        re.IGNORECASE | re.DOTALL,
    )
    m = pattern.match(raw)
    if not m:
        raise QuerySyntaxError("Upit ne odgovara ocekivanoj gramatici "
                                "(SELECT ... FROM ... [WHERE ...] [ORDER BY ...]).")

    select_part = m.group("select")
    from_part = m.group("from")
    where_part = m.group("where")
    orderby_part = m.group("orderby")

    # pravila sintakse
    if re.search(r"\bGROUP\s+BY\b", raw, re.IGNORECASE):
        raise QuerySyntaxError("GROUP BY nije podrzan.")
    if re.search(r"\bJOIN\b", raw, re.IGNORECASE):
        raise QuerySyntaxError("Eksplicitni JOIN operator nije podrzan "
                                "(tabele se navode u FROM odvojene zarezom).")
    if re.search(r"\bSELECT\b", raw[6:], re.IGNORECASE):
        raise QuerySyntaxError("Podupiti nisu podrzani.")

    # SELECT lista - samo atributi, bez agregata/operacija
    select_tokens = [t.strip() for t in select_part.split(",")]
    if any(re.search(r"[()+\-*/]", t) for t in select_tokens):
        raise QuerySyntaxError("SELECT lista sme sadrzati samo atribute "
                                "(bez agregatnih funkcija i izraza).")
    select_attrs = [_parse_attr_ref(t) for t in select_tokens]

    # from
    tables: List[TableRef] = [] #tabela lista
    for token in from_part.split(","):
        parts = token.strip().split()
        if len(parts) == 1:
            tables.append(TableRef(name=parts[0]))
        elif len(parts) == 2:
            tables.append(TableRef(name=parts[0], alias=parts[1]))
        elif len(parts) == 3 and parts[1].upper() == "AS":
            tables.append(TableRef(name=parts[0], alias=parts[2]))
        else:
            raise QuerySyntaxError(
                f"Ne mogu da protumacim stavku FROM klauzule: '{token.strip()}' "
                f"(ocekivano: 'Tabela' ili 'Tabela Alias' ili 'Tabela AS Alias').")
    if len(tables) > 4:
        raise QuerySyntaxError("Upit moze biti najvise nad 4 tabele.")

    # where uslov
    conditions: List[Condition] = []
    if where_part:
        if re.search(r"\bOR\b", where_part, re.IGNORECASE):
            raise QuerySyntaxError("WHERE klauzula podrzava samo konjunkciju (AND).")
        cond_texts = _split_top_level(where_part, "AND")
        if len(cond_texts) > 6:
            raise QuerySyntaxError("WHERE klauzula sme imati najvise 6 uslova.")
        conditions = [_parse_condition(c) for c in cond_texts]

    # order by
    order_by = None
    if orderby_part: #max jedan
        ob_tokens = orderby_part.strip().split(",")
        if len(ob_tokens) > 1:
            raise QuerySyntaxError("ORDER BY sme sadrzati najvise jedan atribut.")
        ob = ob_tokens[0].strip()
        descending = False
        if re.search(r"\bDESC\b", ob, re.IGNORECASE):
            descending = True
            ob = re.sub(r"\bDESC\b", "", ob, flags=re.IGNORECASE).strip()
        elif re.search(r"\bASC\b", ob, re.IGNORECASE):
            ob = re.sub(r"\bASC\b", "", ob, flags=re.IGNORECASE).strip()
        order_by = OrderBy(attr=_parse_attr_ref(ob), descending=descending)

    return ParsedQuery(
        select_attrs=select_attrs,
        tables=tables,
        conditions=conditions,
        order_by=order_by,
        raw=original,
    )


if __name__ == "__main__":
    q = ("SELECT indeks, ime, ocena FROM Student, Ispit "
         "WHERE Student.indeks = Ispit.studentIndeks AND smer = 'IS' AND ocena >= 8 "
         "ORDER BY prosek")
    parsed = parse_query(q)
    print("SELECT:", [str(a) for a in parsed.select_attrs])
    print("FROM:", parsed.tables)
    print("WHERE:")
    for c in parsed.conditions:
        print("  ", c, " | join uslov:" , c.is_join)
    print("ORDER BY:", parsed.order_by)
