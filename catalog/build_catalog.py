"""Compress the raw calculator definitions (catalog/raw) into backend/src/data/catalog.json.

For every service it keeps only what the AI and the review form need: templates, and per template the
input fields (id, label, kind, options, units, default, limits). Saved-value formats per kind:
  number / percent       {"value": "7"}
  dropdown               {"value": "<option id>"}
  checkbox               {"value": true|false}
  frequency              {"value": "7", "unit": "perMonth"}
  size                   {"value": "14", "unit": "gb|month"}   (size unit | frequency)
  duration               {"value": "42", "unit": "sec"}
  throughput             {"value": "125", "unit": "mbps"}
  instances              {"value": [ {"<selectorId>": {"value": ...}, ...} ]}   (columnFormIPM rows)
  datatransfer           {"value": [ {"entryType": "OUTBOUND", "value": "1", "unit": "tb_month", "toRegion": ""} ]}

Usage: python catalog/build_catalog.py
"""
import json
import os
import re

HERE = os.path.dirname(__file__)
RAW = os.path.join(HERE, "raw")
OUT = os.path.join(HERE, "..", "backend", "src", "data", "catalog.json")

SIMPLE = {"numericInput": "number", "percentInput": "percent", "dropdown": "dropdown", "checkbox": "checkbox",
          "frequency": "frequency", "fileSize": "size", "durationInput": "duration", "throughput": "throughput",
          "utilization": "number", "columnFormIPM": "instances", "dataTransferV2": "datatransfer"}
# Widgets with their own UI logic; services that need them are handled by hand-written builders.
CUSTOM = {"ec2InstanceSearch", "instanceFinder", "pricingStrategy", "ec2AdvPricingStrategy", "ec2AdvPricingStrategyV2",
          "ec2AdvPricingMetrics", "workload", "radioTiles", "priceSelector", "workloadsConditionalComponent",
          "workloadDropdown", "workloadPriceSetFetcher", "workflowInstruction", "uploadTable",
          "dedicatedHostsReviewTable", "dedicatedHostsCostTable", "windowsWorkloadStorage", "workloadsInstanceSearch",
          "sqlPassiveNode", "conditionalCheckboxes", "inputField"}
MAX_OPTIONS = 60


def clean(text, n=160):
    text = re.sub(r"<[^>]+>", "", str(text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:n]


def walk_inputs(node, out):
    if isinstance(node, dict):
        if node.get("type") == "input" and node.get("id"):
            out.append(node)
            if node.get("subType") == "columnFormIPM":
                return  # its rows are handled as one field
        for v in node.values():
            walk_inputs(v, out)
    elif isinstance(node, list):
        for v in node:
            walk_inputs(v, out)
    return out


OPS = {"==", "!=", ">", "<", ">=", "<="}


def condition(c):
    """Reduce a displayIf tree to the parts that depend on other inputs.

    Returns {"and"|"or": [...]}, {"not": x}, {"op", "id", "value"}, or None when the condition only checks
    price availability ("exists meteredUnit") and so is effectively always true where the service is priced.
    """
    if isinstance(c, list):
        parts = [p for p in (condition(x) for x in c) if p]
        return {"and": parts} if len(parts) > 1 else (parts[0] if parts else None)
    if not isinstance(c, dict):
        return None
    for k, v in c.items():
        if k in ("and", "or"):
            parts = [p for p in (condition(x) for x in v or []) if p]
            if k == "or" and len(parts) < len(v or []):
                return None  # one branch is a price-availability check: treat the whole OR as true
            return {k: parts} if len(parts) > 1 else (parts[0] if parts else None)
        if k == "not":
            inner = condition(v)
            return {"not": inner} if inner else None
        if k in OPS and isinstance(v, list) and len(v) == 2:
            a, b = v
            if isinstance(a, dict) and a.get("type") == "component" and a.get("id") and not isinstance(b, (dict, list)):
                return {"op": k, "id": a["id"], "value": b}
            return None
        if k == "checkboxChecked" and isinstance(v, dict) and v.get("id"):
            return {"op": "==", "id": v["id"], "value": True}
    return None


def field_from(node):
    st = node.get("subType")
    kind = SIMPLE.get(st)
    if not kind:
        return None
    v = node.get("validations") or {}
    f = {"id": node["id"], "label": clean(node.get("label") or node.get("title") or node["id"], 140), "kind": kind}
    desc = clean(node.get("description") or node.get("placeholder"), 160)
    if desc:
        f["help"] = desc
    if v.get("required"):
        f["required"] = True
    for k_src, k_dst in (("minValue", "min"), ("maxValue", "max")):
        if isinstance(v.get(k_src), (int, float)) and abs(v[k_src]) < 1e15:
            f[k_dst] = v[k_src]
    cond = condition(node.get("displayIf"))
    if cond:
        f["conditional"] = True
        f["showIf"] = cond

    if kind == "dropdown":
        opts = []
        for o in node.get("options") or []:
            if isinstance(o, dict) and o.get("id") is not None:
                opts.append({"id": str(o["id"]), "label": clean(o.get("label") or o["id"], 90)})
        if not opts:
            return None
        f["options"] = opts[:MAX_OPTIONS]
        d = node.get("defaultDropDownItem") or node.get("defaultOption")
        if isinstance(d, str) and any(o["id"] == d for o in opts):
            f["default"] = d
    elif kind == "frequency":
        # Labels matter: some fields count in millions ("million per month") although the id is "perMonth".
        opts = [o for o in node.get("options") or [] if isinstance(o, dict) and o.get("id")]
        f["units"] = [o["id"] for o in opts]
        f["unitLabels"] = {o["id"]: clean(o.get("label") or o["id"], 40) for o in opts}
        f["defaultUnit"] = node.get("defaultFrequency") or (f["units"][0] if f["units"] else "perMonth")
    elif kind == "size":
        opts = [o for o in node.get("dropDownSize") or [] if isinstance(o, dict) and o.get("id")]
        d = node.get("defaultOption") or {}
        freq = d.get("frequency") or node.get("outputFrequency") or "NA"
        f["units"] = [f"{o['id']}|{freq}" for o in opts] or [f"gb|{freq}"]
        f["unitLabels"] = {f"{o['id']}|{freq}": clean(o.get("label") or o["id"], 40) for o in opts}
        f["defaultUnit"] = f"{d.get('size') or (opts[0]['id'] if opts else 'gb')}|{freq}"
    elif kind == "duration":
        opts = [o for o in node.get("dropDownDuration") or [] if isinstance(o, dict) and o.get("value")]
        f["units"] = [o["value"] for o in opts]
        f["unitLabels"] = {o["value"]: clean(o.get("label") or o["value"], 40) for o in opts}
        f["defaultUnit"] = node.get("outputDurationUnit") or (f["units"][0] if f["units"] else "hr")
    elif kind == "throughput":
        opts = [o for o in node.get("options") or [] if isinstance(o, dict) and o.get("value")]
        f["units"] = [o["value"] for o in opts]
        f["unitLabels"] = {o["value"]: clean(o.get("label") or o["value"], 40) for o in opts}
        f["defaultUnit"] = node.get("defaultThroughput") or (f["units"][0] if f["units"] else "mbps")
    elif kind == "instances":
        if f["label"] == node["id"]:
            f["label"] = "Instances (one row per instance type)"
        cols = []
        for c in node.get("row") or []:
            key = c.get("selectorId") or "undefined"  # the calculator stores utilization under "undefined"
            cols.append({"key": key, "label": clean(c.get("label") or key, 60), "type": c.get("type"),
                         **({"dependsOn": c["dependent"]} if c.get("dependent") else {})})
        f["columns"] = cols
    elif kind == "datatransfer":
        f["types"] = node.get("dataTransferTypes") or ["INBOUND", "OUTBOUND"]
    if node.get("defaultValue") is not None and kind in ("number", "percent", "checkbox", "throughput", "duration"):
        f["default"] = node["defaultValue"]
    return f


def merge(fields, f):
    """The same id can appear several times (one per displayIf branch); keep one entry with all options."""
    old = fields.get(f["id"])
    if not old:
        fields[f["id"]] = f
        return
    # Shown under either branch's condition (or always, if either branch is unconditional).
    if old.get("showIf") and f.get("showIf"):
        old["showIf"] = {"or": [old["showIf"], f["showIf"]]}
    else:
        old.pop("showIf", None)
        old.pop("conditional", None)
    if f.get("options"):
        seen = {o["id"] for o in old.get("options", [])}
        old.setdefault("options", []).extend(o for o in f["options"] if o["id"] not in seen)
        old["options"] = old["options"][:MAX_OPTIONS]
    if f.get("required"):
        old["required"] = True


def main():
    manifest = json.load(open(os.path.join(RAW, "_manifest.json"), encoding="utf-8"))["awsServices"]
    man = {s["serviceCode"]: s for s in manifest}
    defs = {}
    for code in man:
        p = os.path.join(RAW, f"{code}.json")
        if os.path.exists(p):
            defs[code] = json.load(open(p, encoding="utf-8"))

    parent_of = {}
    for code, d in defs.items():
        if d.get("subType") == "subServiceSelector":
            for t in d.get("templates") or []:
                if isinstance(t, str):
                    parent_of[t] = code

    services = {}
    for code, d in defs.items():
        m = man[code]
        if m.get("disableConfigure") or str(m.get("isActive")).lower() == "false" or d.get("type") == "static":
            continue
        if d.get("subType") == "subServiceSelector":
            services[code] = {"code": code, "name": clean(d.get("serviceName") or m["name"], 120), "group": True,
                              "children": [t for t in d.get("templates") or [] if isinstance(t, str) and t in defs],
                              "version": d.get("version"), "templateId": d.get("templateId"),
                              "regions": m.get("regions") or d.get("regions") or []}
            continue
        templates, custom = [], set()
        for t in d.get("templates") or []:
            if not isinstance(t, dict) or not t.get("id"):
                continue
            fields = {}
            for node in walk_inputs(t, []):
                st = node.get("subType")
                if st in CUSTOM:
                    custom.add(st)
                    continue
                f = field_from(node)
                if f:
                    merge(fields, f)
            templates.append({"id": t["id"], "title": clean(t.get("title") or d.get("serviceName"), 120),
                              "fields": list(fields.values())})
        if not templates:
            continue
        parent = parent_of.get(code)
        services[code] = {
            "code": code,
            "name": clean(d.get("serviceName") or m["name"], 120),
            "description": clean(m.get("description") or d.get("serviceDescription"), 220),
            "keywords": [clean(k, 40) for k in (m.get("searchKeywords") or d.get("searchKeywords") or [])][:12],
            "version": d.get("version"),
            "regions": m.get("regions") or d.get("regions") or [],
            "templates": templates,
            **({"parent": parent} if parent else {}),
            **({"custom": sorted(custom)} if custom else {}),
        }
    # Children inherit their group's regions and get a "Group: Child" display name.
    for code, s in services.items():
        p = services.get(s.get("parent") or "")
        if p:
            s["regions"] = s["regions"] or p["regions"]
            s["displayName"] = f"{p['name']}: {s['name']}"
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"services": services}, f, separators=(",", ":"))
    pick = [s for s in services.values() if not s.get("group")]
    print(f"services: {len(services)} (groups {len(services) - len(pick)}, selectable {len(pick)}), "
          f"with custom widgets: {sum(1 for s in pick if s.get('custom'))}, "
          f"size {os.path.getsize(OUT) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
