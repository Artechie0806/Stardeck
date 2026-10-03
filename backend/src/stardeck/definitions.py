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
"""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

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


class Definitions(BaseModel):
    database: DatabaseDef = Field(default_factory=DatabaseDef)
    tables: dict[str, TableDef] = Field(default_factory=dict)
    glossary: list[GlossaryTerm] = Field(default_factory=list)

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
        data = self.model_dump(exclude_defaults=True)
        for table in data.get("tables", {}).values():
            for col in table.get("columns", {}).values():
                col.pop("source", None)
        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)

    def column(self, table: str, column: str) -> ColumnDef | None:
        t = self.tables.get(table)
        return t.columns.get(column) if t else None
