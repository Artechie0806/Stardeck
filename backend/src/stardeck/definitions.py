"""User-provided definitions: the optional semantic layer that explains a database to the AI.

The same YAML format is used for import/export, so definitions can live in git.

    database:
      description: Retail sales data. Money columns are in paise.
    tables:
      customers:
        description: People who placed orders.
        columns:
          col1: {description: Customer age, unit: years}
          sgmt: {description: Segment, values: {1: Retail, 2: Wholesale}}
          email: {hidden: true}          # never sent to the model
    glossary:
      - term: revenue
        definition: Sum of completed order amounts, in INR.
    measures:                            # reusable calculations: MEASURE(revenue) in any widget
      - name: revenue
        label: Revenue
        table: orders
        expression: SUM(CASE WHEN sts = 1 THEN amt END) / 100.0
        format: currency
        currency: INR
    relationships:                       # links the database doesn't declare (for filters and security)
      - {from: orders.customer_id, to: customers.id}
"""

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

DefinitionSource = Literal["user", "chat", "ai_suggested"]


class ColumnDef(BaseModel):
    description: str | None = None
    display_name: str | None = None
    unit: str | None = None
    values: dict[str, str] = Field(default_factory=dict)
    hidden: bool = False
    # Who wrote it. AI suggestions are shown to the user for review but still used in prompts.
    source: DefinitionSource = "user"


class TableDef(BaseModel):
    description: str | None = None
    display_name: str | None = None
    synonyms: list[str] = Field(default_factory=list)
    columns: dict[str, ColumnDef] = Field(default_factory=dict)


class DatabaseDef(BaseModel):
    description: str | None = None


class GlossaryTerm(BaseModel):
    term: str
    definition: str


MEASURE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


class Measure(BaseModel):
    """A named calculation defined once and used everywhere as MEASURE(name).

    `expression` is an aggregate over `table`'s own columns, written without table names, e.g.
    `SUM(CASE WHEN sts = 1 THEN amt END) / 100.0`. Filters go inside the aggregate (CASE WHEN), so
    one query can show several measures side by side.
    """

    name: str
    label: str | None = None
    description: str | None = None
    table: str
    expression: str
    format: Literal["number", "currency", "percent"] = "number"
    currency: str | None = None
    up_is_good: bool = True
    source: DefinitionSource = "user"

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip().lower()
        if not MEASURE_NAME.match(v):
            raise ValueError("Use lowercase letters, digits and _ for a measure name (e.g. revenue, refund_rate).")
        return v


class RelationshipDef(BaseModel):
    """`from` (e.g. orders.customer_id) points at `to` (e.g. customers.id)."""

    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")
    to: str

    @field_validator("from_", "to")
    @classmethod
    def _dotted(cls, v: str) -> str:
        v = v.strip()
        if v.count(".") != 1 or not all(v.split(".")):
            raise ValueError("Write relationship ends as table.column")
        return v


class Definitions(BaseModel):
    database: DatabaseDef = Field(default_factory=DatabaseDef)
    tables: dict[str, TableDef] = Field(default_factory=dict)
    glossary: list[GlossaryTerm] = Field(default_factory=list)
    measures: list[Measure] = Field(default_factory=list)
    relationships: list[RelationshipDef] = Field(default_factory=list)
    # Detected or inferred relationships the user switched off ("orders.customer_id->customers.id").
    ignored_relationships: list[str] = Field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> "Definitions":
        return cls.from_yaml(Path(path).read_text())

    @classmethod
    def from_yaml(cls, text: str) -> "Definitions":
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise ValueError("Definitions YAML must be a mapping")  # noqa: TRY004 - callers handle ValueError
        # YAML keys like `1:` parse as ints; value maps are always str -> str.
        for table in (data.get("tables") or {}).values():
            for col in (table.get("columns") or {}).values():
                if col and col.get("values"):
                    col["values"] = {str(k): str(v) for k, v in col["values"].items()}
        return cls.model_validate(data)

    def to_yaml(self) -> str:
        data = self.model_dump(exclude_defaults=True, by_alias=True)
        for table in data.get("tables", {}).values():
            for col in table.get("columns", {}).values():
                col.pop("source", None)
        for m in data.get("measures", []):
            m.pop("source", None)
        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)

    def column(self, table: str, column: str) -> ColumnDef | None:
        t = self.tables.get(table)
        return t.columns.get(column) if t else None
