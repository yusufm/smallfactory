"""
Smoke test for /api/entities/search endpoint using Flask test client.
Run: python debug/smoke_entities_search.py
"""
from pprint import pprint
import sys
from pathlib import Path
from importlib.util import spec_from_file_location, module_from_spec

# Ensure project root (containing 'smallfactory' package) is importable
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Load Flask app from file path (web/app.py) without requiring 'web' as a package
APP_PATH = ROOT / "web" / "app.py"
spec = spec_from_file_location("sf_web_app", str(APP_PATH))
mod = module_from_spec(spec)
assert spec and spec.loader, "Failed to load spec for web/app.py"
spec.loader.exec_module(mod)  # type: ignore[attr-defined]
app = getattr(mod, "app")


def check(query, type_prefix=None, limit=5):
    qs = {"q": query, "limit": str(limit)}
    if type_prefix:
        qs["type"] = type_prefix
    with app.test_client() as c:
        resp = c.get("/api/entities/search", query_string=qs)
    print(f"GET /api/entities/search {qs} -> {resp.status_code}")
    try:
        data = resp.get_json() or {}
    except Exception:
        data = {"parse_error": resp.data.decode("utf-8", errors="ignore")}
    pprint(data)
    print("-" * 60)


if __name__ == "__main__":
    # 1) Empty query -> should succeed with empty results
    check("")
    # 2) Generic queries; results may be empty if your data repo has no entities
    check("p_", type_prefix="p")
    check("l_", type_prefix="l")
    check("resistor")
