#!/usr/bin/env python3
"""Generate docs/API_REFERENCE.md and docs/RBAC_MATRIX.md from the code (run from anywhere: python scripts/gen-docs.py)."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://x:x@localhost/pulse_test")

from app.main import create_app  # noqa: E402
from app.rbac import PERMISSIONS, ROLES  # noqa: E402

spec = create_app().openapi()
by_tag: dict[str, list] = {}
for path, ops in sorted(spec["paths"].items()):
    for method, op in ops.items():
        by_tag.setdefault((op.get("tags") or ["other"])[0], []).append((path, method.upper(), op))

lines = ["# API reference", "", "Generated from the OpenAPI schema by `scripts/gen-docs.py`. Interactive docs: `/api/docs` (Swagger) and `/api/redoc`.",
         "All paths are under `/api/v1`. See [API.md](API.md) for authentication, errors and pagination.", ""]
n = 0
for tag in sorted(by_tag):
    lines += [f"## {tag}", "", "| Method | Path | Summary |", "|---|---|---|"]
    for path, method, op in by_tag[tag]:
        n += 1
        lines.append(f"| `{method}` | `{path}` | {(op.get('summary') or '').replace('|', '/')} |")
    lines.append("")
lines.insert(3, f"**{n} operations.**")
open(os.path.join(ROOT, "docs", "API_REFERENCE.md"), "w").write("\n".join(lines))

roles = list(ROLES)
m = ["# Role × permission matrix", "", "Generated from `backend/app/rbac.py`. Permissions gate *which endpoints* a role may call; row-level rules",
     "(own records, own department, consent, break-glass) then decide *which records*. See ARCHITECTURE.md. Administrators can edit these grants at runtime (Admin → Permissions).", "",
     "| Permission | " + " | ".join(roles) + " |", "|---|" + "---|" * len(roles)]
for code, desc in PERMISSIONS.items():
    m.append(f"| `{code}` — {desc} | " + " | ".join("✔" if code in ROLES[r]["permissions"] else "" for r in roles) + " |")
open(os.path.join(ROOT, "docs", "RBAC_MATRIX.md"), "w").write("\n".join(m) + "\n")
print(f"{n} operations, {len(PERMISSIONS)} permissions, {len(roles)} roles documented")
