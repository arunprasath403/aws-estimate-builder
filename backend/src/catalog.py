"""Access to the compressed calculator catalog (data/catalog.json, built by catalog/build_catalog.py)."""
import difflib
import json
import os
import re
from functools import lru_cache

from regions import REGIONS

# EC2 uses custom calculator widgets, so it gets a hand-written template (built in estimate.py).
EC2 = "ec2Enhancement"
EC2_TEMPLATE = {
    "id": "template",
    "title": "Amazon EC2",
    "fields": [
        {"id": "instanceType", "label": "Instance type", "kind": "text", "required": True,
         "help": "Exact API name, e.g. t4g.nano, m6i.large"},
        {"id": "quantity", "label": "Number of instances", "kind": "number", "required": True, "min": 1},
        {"id": "os", "label": "Operating system", "kind": "dropdown", "required": True,
         "options": [{"id": "linux", "label": "Linux"}, {"id": "windows", "label": "Windows"}]},
        {"id": "hours", "label": "Hours per month (730 = always on)", "kind": "number", "required": True,
         "min": 0, "max": 730},
        {"id": "storageGb", "label": "EBS gp3 storage per instance (GB)", "kind": "number", "min": 0},
    ],
}
UNSUPPORTED = {"windowsWorkloads"}
ROW_KEYS = {"Number of Nodes", "Instance Type", "undefined", "TermType", "Deployment Option",
            "LeaseContractLength", "PurchaseOption"}


@lru_cache(maxsize=1)
def services() -> dict:
    path = os.path.join(os.path.dirname(__file__), "data", "catalog.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)["services"]
    if EC2 in data:
        data[EC2]["templates"] = [EC2_TEMPLATE]
        data[EC2].pop("custom", None)
    for code in UNSUPPORTED:
        data.pop(code, None)
    return data


def get(code: str):
    return services().get(code)


def _key(text: str) -> str:
    return re.sub(r"^(amazon|aws)", "", re.sub(r"[^a-z0-9]", "", text.lower()))


@lru_cache(maxsize=1)
def _by_key() -> dict:
    out = {}
    for s in selectable():
        for text in (s["code"], display_name(s), s["name"]):
            out.setdefault(_key(text), s["code"])
    return out


def resolve(code_or_name: str) -> str | None:
    """Map what the model wrote (exact code, wrong case/prefix, or service name) to a selectable service code."""
    s = get(code_or_name)
    if s and not s.get("group"):
        return code_or_name
    k = _key(code_or_name)
    if k in _by_key():
        return _by_key()[k]
    close = difflib.get_close_matches(k, list(_by_key()), n=1, cutoff=0.88)
    return _by_key()[close[0]] if close else None


def selectable() -> list[dict]:
    return [s for s in services().values() if not s.get("group")]


def display_name(s: dict) -> str:
    return s.get("displayName") or s["name"]


def regions_for(s: dict) -> list[str]:
    allowed = [r for r in (s.get("regions") or []) if r in REGIONS]
    return allowed or list(REGIONS)


@lru_cache(maxsize=1)
def service_index_text() -> str:
    """One line per selectable service, for the 'which services are these?' prompt."""
    lines = []
    for s in sorted(selectable(), key=display_name):
        kw = ", ".join(k for k in s.get("keywords", [])[:5] if k.lower() not in display_name(s).lower())
        lines.append(f"{s['code']} | {display_name(s)}" + (f" | {kw}" if kw else ""))
    return "\n".join(lines)


def template(s: dict, template_id: str | None) -> dict:
    for t in s["templates"]:
        if t["id"] == template_id:
            return t
    return s["templates"][0]


def cond_text(c) -> str:
    if "and" in c or "or" in c:
        k = "and" if "and" in c else "or"
        return "(" + f" {k} ".join(cond_text(x) for x in c[k]) + ")"
    if "not" in c:
        return "not " + cond_text(c["not"])
    return f"{c['id']} {c['op']} {c['value']}"


def cond_ok(c, values: dict) -> bool:
    """Evaluate a field's showIf against the item's values; unknown inputs count as satisfied."""
    if not c:
        return True
    if "and" in c:
        return all(cond_ok(x, values) for x in c["and"])
    if "or" in c:
        return any(cond_ok(x, values) for x in c["or"])
    if "not" in c:
        return not cond_ok(c["not"], values)
    v = (values.get(c["id"]) or {}).get("value")
    if v is None:
        return True
    want = c["value"]
    try:
        a, b = float(v), float(want)
    except (TypeError, ValueError):
        a, b = str(v).lower(), str(want).lower()
    op = c["op"]
    return {"==": a == b, "!=": a != b, ">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b}[op]


def schema_text(s: dict) -> str:
    """Compact description of a service's fields, for the 'fill the fields' prompt."""
    out = [f"SERVICE {s['code']}: {display_name(s)}"]
    for t in s["templates"]:
        out.append(f"TEMPLATE {t['id']}: {t['title']}")
        for f in t["fields"]:
            parts = [f["id"], f["kind"], f["label"]]
            if f.get("help"):
                parts.append("help: " + f["help"])
            if f.get("options"):
                parts.append("options: " + "; ".join(f"{o['id']}={o['label']}" for o in f["options"][:40]))
            if f.get("units"):
                labels = f.get("unitLabels") or {}
                parts.append("units: " + ", ".join(f"{u} ({labels[u]})" if labels.get(u) and labels[u] != u else u
                                                    for u in f["units"]))
            if f.get("columns"):
                keys = [c["key"] for c in f["columns"] if c["key"] in ROW_KEYS]
                parts.append("row columns: " + ", ".join(k if k != "undefined" else "utilization" for k in keys))
            if f.get("types"):
                parts.append("types: " + ", ".join(f["types"]))
            if "default" in f:
                parts.append(f"default: {f['default']}")
            if f.get("required"):
                parts.append("required")
            if f.get("showIf"):
                parts.append("ONLY applies when " + cond_text(f["showIf"]))
            out.append("  - " + " | ".join(str(p) for p in parts))
    return "\n".join(out)
