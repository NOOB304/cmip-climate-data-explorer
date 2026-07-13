from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import dataclass
from importlib.resources import as_file, files
from pathlib import Path

from rapidfuzz import fuzz

from cmip_explorer.domain.models import VariableDefinition


@dataclass(frozen=True, slots=True)
class VariableOption:
    variable_ids: tuple[str, ...]
    definitions: tuple[VariableDefinition, ...]

    @property
    def variable_id(self) -> str:
        return min(self.variable_ids, key=lambda value: (len(value), value))

    @property
    def preferred(self) -> VariableDefinition:
        return next(
            (definition for definition in self.definitions if definition.chinese_name),
            next(
                (definition for definition in self.definitions if definition.table_id == "Amon"),
                self.definitions[0],
            ),
        )

    @property
    def frequencies(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    definition.frequency
                    for definition in self.definitions
                    if definition.frequency
                }
            )
        )

    @property
    def table_ids(self) -> tuple[str, ...]:
        return tuple(sorted({definition.table_id for definition in self.definitions}))


class VariableCatalog:
    def __init__(self, path: Path) -> None:
        self.path = path

    def search(self, query: str, limit: int = 50) -> tuple[VariableDefinition, ...]:
        normalized = query.strip().casefold()
        if not normalized:
            sql = "SELECT * FROM variable_definitions ORDER BY table_id, variable_id LIMIT ?"
            parameters: tuple[object, ...] = (limit,)
        else:
            pattern = f"%{normalized}%"
            sql = """
                SELECT DISTINCT v.*
                FROM variable_definitions v
                LEFT JOIN variable_aliases a ON a.variable_key = v.variable_key
                WHERE lower(v.variable_id) LIKE ?
                   OR lower(v.long_name) LIKE ?
                   OR lower(coalesce(v.standard_name, '')) LIKE ?
                   OR lower(coalesce(v.chinese_name, '')) LIKE ?
                   OR lower(coalesce(v.chinese_description, '')) LIKE ?
                   OR lower(coalesce(a.alias, '')) LIKE ?
                LIMIT 500
            """
            parameters = (pattern,) * 6
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(sql, parameters).fetchall()
            aliases = self._aliases(connection, [row["variable_key"] for row in rows])
        scored = [
            (self._score(row, aliases.get(row["variable_key"], ()), normalized), row)
            for row in rows
        ]
        scored.sort(key=lambda item: (-item[0], item[1]["table_id"], item[1]["variable_id"]))
        return tuple(
            self._to_definition(row, aliases.get(row["variable_key"], ()))
            for _, row in scored[:limit]
        )

    def search_grouped(self, query: str, limit: int = 100) -> tuple[VariableOption, ...]:
        definitions = self.search(query, limit=5000)
        parents = {definition.variable_id: definition.variable_id for definition in definitions}

        def find(value: str) -> str:
            while parents[value] != value:
                parents[value] = parents[parents[value]]
                value = parents[value]
            return value

        def union(left: str, right: str) -> None:
            left_root = find(left)
            right_root = find(right)
            if left_root != right_root:
                parents[right_root] = left_root

        semantic_groups: dict[tuple[str, str], list[str]] = {}
        for definition in definitions:
            label = (definition.chinese_name or definition.long_name).strip().casefold()
            standard_name = (definition.standard_name or "").strip().casefold()
            semantic_groups.setdefault((label, standard_name), []).append(
                definition.variable_id
            )
        for variable_ids in semantic_groups.values():
            first = variable_ids[0]
            for variable_id in variable_ids[1:]:
                union(first, variable_id)

        grouped: dict[str, list[VariableDefinition]] = {}
        for definition in definitions:
            grouped.setdefault(find(definition.variable_id), []).append(definition)
        return tuple(
            VariableOption(
                tuple(sorted({definition.variable_id for definition in definitions})),
                tuple(definitions),
            )
            for definitions in list(grouped.values())[:limit]
        )

    @staticmethod
    def _aliases(
        connection: sqlite3.Connection, variable_keys: list[str]
    ) -> dict[str, tuple[str, ...]]:
        if not variable_keys:
            return {}
        placeholders = ",".join("?" for _ in variable_keys)
        result: dict[str, list[str]] = {}
        sql = (
            "SELECT variable_key, alias FROM variable_aliases "
            f"WHERE variable_key IN ({placeholders})"
        )
        for key, alias in connection.execute(
            sql,
            variable_keys,
        ):
            result.setdefault(key, []).append(alias)
        return {key: tuple(values) for key, values in result.items()}

    @staticmethod
    def _score(row: sqlite3.Row, aliases: tuple[str, ...], query: str) -> float:
        if not query:
            return 1.0
        fields = [
            row["variable_id"],
            row["long_name"],
            row["standard_name"] or "",
            row["chinese_name"] or "",
            row["chinese_description"] or "",
            *aliases,
        ]
        lowered = [str(field).casefold() for field in fields]
        if query == lowered[0]:
            return 1000
        if query in lowered:
            return 900
        if any(field.startswith(query) for field in lowered):
            return 800
        if any(query in field for field in lowered):
            return 700
        return max(fuzz.WRatio(query, field) for field in lowered)

    @staticmethod
    def _to_definition(row: sqlite3.Row, aliases: tuple[str, ...]) -> VariableDefinition:
        return VariableDefinition(
            project=row["project"],
            table_id=row["table_id"],
            variable_id=row["variable_id"],
            frequency=row["frequency"],
            modeling_realm=row["modeling_realm"],
            standard_name=row["standard_name"],
            long_name=row["long_name"],
            units=row["units"],
            cell_methods=row["cell_methods"],
            dimensions=row["dimensions"],
            comment=row["comment"],
            chinese_name=row["chinese_name"],
            chinese_description=row["chinese_description"],
            aliases=aliases,
            source_version=row["source_version"],
        )


def install_packaged_catalog(target: Path, force: bool = False) -> Path:
    if target.exists() and not force:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    packaged = files("cmip_explorer.resources").joinpath("catalog.db")
    temporary = target.with_suffix(target.suffix + ".part")
    with as_file(packaged) as source:
        shutil.copyfile(source, temporary)
    os.replace(temporary, target)
    return target
