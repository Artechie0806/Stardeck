"""Who a request runs as: role and row-level security rules.

Roles, lowest to highest:
  viewer  - opens dashboards, uses filters, asks the data analyst; sees only rows their rules allow
  editor  - also builds and changes dashboards, definitions, measures and prepared tables
  admin   - also manages data sources, people and settings

As in Power BI, security rules apply to viewers. Editors and admins can write SQL and recipes, so
they always see all data; restricting them would only look safe.

Every principal belongs to one workspace: its requests only ever see that workspace's data.
"""

from dataclasses import dataclass
from typing import Literal

from stardeck.context import Restriction
from stardeck.db import DEFAULT_WORKSPACE

Role = Literal["viewer", "editor", "admin"]
Kind = Literal["user", "owner", "system", "share"]
_RANK = {"viewer": 0, "editor": 1, "admin": 2}


@dataclass(frozen=True)
class Principal:
    id: str
    username: str
    name: str
    role: Role
    # Row-level security: [{"sourceId", "table", "column", "values"}], viewers only.
    rules: tuple[dict, ...] = ()
    # None only for SYSTEM, which works on records that were already loaded within a workspace.
    workspace_id: str | None = DEFAULT_WORKSPACE
    kind: Kind = "user"
    # Runs the service (hosted): may see model and server details. Grants no access to other
    # workspaces' data.
    operator: bool = False

    def can(self, role: Role) -> bool:
        return _RANK[self.role] >= _RANK[role]

    def rules_for(self, source_id: str) -> tuple[Restriction, ...]:
        if self.role != "viewer":
            return ()
        return tuple(Restriction(table=r["table"], column=r["column"], values=tuple(r.get("values") or ()))
                     for r in self.rules if r.get("sourceId") == source_id)


# Internal jobs (profiling, prepared-table previews for editors), on records already loaded.
SYSTEM = Principal(id="system", username="system", name="Stardeck", role="admin", workspace_id=None, kind="system")
# Self-hosted installs without accounts (open or shared-password mode): the default workspace's admin.
OWNER = Principal(id="owner", username="owner", name="You", role="admin", workspace_id=DEFAULT_WORKSPACE,
                  kind="owner")
