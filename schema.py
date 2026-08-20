

import json
from dataclasses import dataclass, field
from typing import List, Optional, Dict


@dataclass
class Attribute:
    name: str
    type: str
    unique: bool
    distinct_values: int

    @staticmethod
    def from_json(d: dict) -> "Attribute":
        return Attribute(
            name=d["name"],
            type=d["type"],
            unique=d.get("unique", False),
            distinct_values=d["distinctValues"],
        )


@dataclass
class Index:
    name: str
    attributes: List[str]
    # jedan ili vise atributa - kompozitni indeks
    type: str
    #da li je hash ili stablo
    clustered: bool
    tree_height: Optional[int] = None

    @staticmethod
    def from_json(d: dict) -> "Index":
        return Index(
            name=d["name"],
            attributes=d["attributes"],
            type=d["type"],
            clustered=d.get("clustered", False),
            tree_height=d.get("treeHeight"),
        )

    def is_btree(self) -> bool:
        return self.type == "B_PLUS_TREE"

    def is_hash(self) -> bool:
        return self.type == "HASH"

    # proveri je l vodeci, ako da onda moze da se iskoristi
    def covers(self, attr_name: str) -> bool:

        return len(self.attributes) > 0 and self.attributes[0] == attr_name

    def is_composite(self) -> bool:
        return len(self.attributes) > 1


@dataclass
class Table:
    name: str
    row_count: int
    block_count: int
    rows_per_block: int
    attributes: List[Attribute]
    indexes: List[Index]

    @staticmethod
    def from_json(d: dict) -> "Table":
        return Table(
            name=d["name"],
            row_count=d["rowCount"],
            block_count=d["blockCount"],
            rows_per_block=d["rowsPerBlock"],
            attributes=[Attribute.from_json(a) for a in d["attributes"]],
            indexes=[Index.from_json(i) for i in d.get("indexes", [])],
        )

    def get_attribute(self, name: str) -> Optional[Attribute]:
        for a in self.attributes:
            if a.name == name:
                return a
        return None

    def has_attribute(self, name: str) -> bool:
        return self.get_attribute(name) is not None

    def indexes_on(self, attr_name: str) -> List[Index]:
      #vrati indekse gde jeatribut  vodeci atribut
        return [i for i in self.indexes if i.covers(attr_name)]

    def clustered_index(self) -> Optional[Index]:
        for i in self.indexes:
            if i.clustered:
                return i
        return None


@dataclass
class Schema:
    buffer_blocks: int
    tables: Dict[str, Table] = field(default_factory=dict)

    @staticmethod
    def from_json_file(path: str) -> "Schema":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return Schema.from_json(data)

    @staticmethod
    def from_json(data: dict) -> "Schema":
        buffer_blocks = data["bufferBlocks"]
        tables = {}
        for t in data["schema"]["tables"]:
            table = Table.from_json(t)
            tables[table.name] = table
        return Schema(buffer_blocks=buffer_blocks, tables=tables)

    def get_table(self, name: str) -> Optional[Table]:
        return self.tables.get(name)

    def has_table(self, name: str) -> bool:
        return name in self.tables


if __name__ == "__main__":
    # brzi test na ulaznom primeru
    schema = Schema.from_json_file("data/primer_sema.json")
    print(f"Bafer: {schema.buffer_blocks} blokova")
    for name, t in schema.tables.items():
        print(f"\nTabela {name}: {t.row_count} redova, {t.block_count} blokova, "
              f"{t.rows_per_block} redova/bloku")
        for a in t.attributes:
            print(f"  - {a.name} ({a.type}) unique={a.unique} distinct={a.distinct_values}")
        for idx in t.indexes:
            print(f"  [INDEX] {idx.name}: {idx.attributes} tip={idx.type} "
                  f"clustered={idx.clustered} height={idx.tree_height}")
