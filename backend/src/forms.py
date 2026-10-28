"""Validate an item's field values against its service schema and store them in calculator format.

Item shape (shared with the web page):
  {id, service, serviceName, name, region, template, values: {fieldId: <calculator value>},
   questions: [fieldId], errors: [str], notes, original_text, details, status}
Statuses: ready | needs_input | unsupported
"""
import re

import catalog
from regions import REGIONS

UTIL_KEY = "undefined"  # the calculator stores instance-row utilization under this key
# Instance-table columns we fill. Other lookup columns (e.g. ElastiCache "Instance Family") take values from AWS
# price data; a wrong guess prices the row at $0, and leaving them out lets the calculator derive them.
ROW_KEYS = catalog.ROW_KEYS


def _num(v):
    if isinstance(v, bool):
        raise ValueError
    if isinstance(v, (int, float)):
        return float(v)
    return float(str(v).replace(",", "").strip())


def _fmt(n: float) -> str:
    return f"{n:.6f}".rstrip("0").rstrip(".") if n % 1 else str(int(n))


def _scalar(raw):
    return raw.get("value") if isinstance(raw, dict) else raw


def _unit(raw, f):
    unit = raw.get("unit") if isinstance(raw, dict) else None
    units = f.get("units") or []
    if unit in units:
        return unit
    if unit:
        u_low = str(unit).lower().replace(" ", "")
        for u in units:
            if u.lower() == u_low:
                return u
        # "tb" / "TB" / "tb/month" -> "tb|NA" or "tb|month": match on the size part before "|"
        size = re.split(r"[|/]", u_low)[0]
        for u in units:
            if u.split("|")[0].lower() == size:
                return u
    return f.get("defaultUnit") or (units[0] if units else None)


def _value(f, raw, notes):
    """Return the calculator-format value, or raise ValueError with a message. May append to notes."""
    kind = f["kind"]
    if kind in ("number", "percent"):
        n = _num(_scalar(raw))
        if "min" in f and n < f["min"] or "max" in f and n > f["max"]:
            raise ValueError(f"{f['label']} must be between {f.get('min', 0)} and {f.get('max', '∞')}")
        return {"value": _fmt(n)}
    if kind == "text":
        v = re.sub(r"\s+", "", str(_scalar(raw) or "")).lower()
        if not v:
            raise ValueError(f"{f['label']} is empty")
        return {"value": v}
    if kind == "checkbox":
        v = _scalar(raw)
        return {"value": v if isinstance(v, bool) else str(v).lower() in ("true", "yes", "1")}
    if kind == "dropdown":
        v = str(_scalar(raw))
        for o in f["options"]:
            if o["id"] == v:
                return {"value": o["id"]}
        for o in f["options"]:
            if o["label"].lower() == v.lower() or o["id"].lower() == v.lower():
                return {"value": o["id"]}
        raise ValueError(f"'{v}' is not an option for {f['label']}")
    if kind in ("frequency", "size", "duration", "throughput"):
        n = _num(_scalar(raw))
        if n < 0:
            raise ValueError(f"{f['label']} cannot be negative")
        unit = _unit(raw, f)
        # Some fields count in millions/thousands ("million per month"). A raw count such as 30000000 there
        # is a units mistake: convert it, and say so.
        label = ((f.get("unitLabels") or {}).get(unit) or "").lower()
        for word, scale, raw_from in (("billion", 1e9, 1e8), ("million", 1e6, 1e5), ("thousand", 1e3, 1e4)):
            if word in label and n >= raw_from:
                notes.append(f"{f['label']}: {n:,.0f} converted to {n / scale:g} {word}")
                n = n / scale
                break
        out = {"value": _fmt(n)}
        if unit:
            out["unit"] = unit
        return out
    if kind == "instances":
        rows = raw.get("value") if isinstance(raw, dict) else raw
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"{f['label'] or 'Instances'}: add at least one row")
        cols = {c["key"] for c in f.get("columns", [])} & ROW_KEYS
        out = []
        for r in rows[:50]:
            if not isinstance(r, dict):
                continue
            row = {}
            for k, v in r.items():
                key = UTIL_KEY if k.lower() in ("utilization", UTIL_KEY) else k
                if key not in cols:
                    continue
                v = _scalar(v)
                if key == UTIL_KEY:
                    if isinstance(v, dict):
                        v = v.get("unit")
                    if v in (None, ""):
                        continue
                    row[key] = {"value": {"unit": _fmt(_num(v)), "selectedId": "%Utilized/Month"}}
                elif v not in (None, ""):
                    row[key] = {"value": str(v).strip()}
            if "TermType" in cols and "TermType" not in row:
                row["TermType"] = {"value": "OnDemand"}
            if UTIL_KEY in cols and UTIL_KEY not in row:
                row[UTIL_KEY] = {"value": {"unit": "100", "selectedId": "%Utilized/Month"}}
            for need in ("Number of Nodes", "Instance Type"):
                if need in cols and need not in row:
                    raise ValueError(f"Each row needs '{need}'")
            out.append(row)
        if not out:
            raise ValueError("No valid instance rows")
        return {"value": out}
    if kind == "datatransfer":
        rows = raw.get("value") if isinstance(raw, dict) else raw
        out = []
        for r in rows if isinstance(rows, list) else []:
            if not isinstance(r, dict):
                continue
            t = str(r.get("type") or r.get("entryType") or "").upper()
            if t not in (f.get("types") or ["INBOUND", "OUTBOUND"]):
                continue
            if "gb_per_month" in r:
                tb = _num(r["gb_per_month"]) / 1024
            else:
                tb = _num(r.get("value") or 0) * (1 / 1024 if str(r.get("unit", "")).startswith("gb") else 1)
            e = {"entryType": t, "value": _fmt(round(tb, 6)), "unit": "tb_month"}
            if t == "INBOUND":
                e["fromRegion"] = ""
            elif t == "OUTBOUND":
                e["toRegion"] = ""
            out.append(e)
        if not out:
            raise ValueError("No valid data transfer rows")
        return {"value": out}
    raise ValueError(f"Unsupported field kind {kind}")


HOURS = {"sec": 1 / 3600, "min": 1 / 60, "hr": 1, "hours": 1, "day": 24, "week": 168, "month": 730}
SHORT_PERIODS = {"perSecond", "perMinute", "perHour", "perDay", "perWeek"}


def _always_on_counts(fields, values, notes):
    """'4 tasks per hour, each running 730 hours' is a units mistake (a task cannot start every hour and also
    run all month): when an item's duration is about a month, its 'Number of ...' counts are per month."""
    long_run = False
    for fid, v in values.items():
        f = fields.get(fid)
        if f and f["kind"] == "duration":
            try:
                long_run |= float(v["value"]) * HOURS.get(v.get("unit"), 0) >= 672
            except (TypeError, ValueError):
                pass
    if not long_run:
        return
    for fid, v in values.items():
        f = fields.get(fid)
        if (f and f["kind"] == "frequency" and v.get("unit") in SHORT_PERIODS
                and "perMonth" in (f.get("units") or []) and f["label"].lower().startswith("number of")):
            notes.append(f"{f['label']}: changed from {v['unit']} to per month because each one runs all month")
            v["unit"] = "perMonth"


def normalize(raw: dict) -> dict:
    it = {k: raw.get(k) for k in ("id", "service", "name", "region", "template", "values", "questions", "notes",
                                  "original_text", "details", "guess", "skipped")}
    it["errors"], it["warnings"] = [], []
    it["skipped"] = [s for s in (it.get("skipped") or []) if isinstance(s, str)]
    svc = catalog.get(it["service"] or "")
    if not svc or svc.get("group"):
        it.update(service=None, status="unsupported", values={}, questions=[], serviceName=it.get("guess") or "Unknown service")
        return it
    it["serviceName"] = catalog.display_name(svc)
    if it["region"] not in REGIONS:
        it["region"] = None
    elif svc["code"] != catalog.EC2 and it["region"] not in catalog.regions_for(svc):
        # The calculator's region lists are not always complete, so this only warns.
        it["warnings"].append(f"{it['serviceName']} may not be available in {it['region']}")
    tpl = catalog.template(svc, it.get("template"))
    it["template"] = tpl["id"]
    fields = {f["id"]: f for f in tpl["fields"]}

    values = {}
    bad = []
    # Main instance table first: the one with an "Instance Type" column (others are e.g. savings-plan tables).
    tables = sorted((fid for fid, f in fields.items() if f["kind"] == "instances" and not f.get("conditional")),
                    key=lambda fid: not any(c["key"] == "Instance Type" for c in fields[fid].get("columns", [])))
    for fid, rawv in (it.get("values") or {}).items():
        if fid not in fields and fid.lower() in ("instances", "instance", "rows") and tables:
            fid = tables[0]  # the model sometimes names the instance table by its kind
        f = fields.get(fid)
        if not f or rawv in (None, "", [], {}):
            continue
        try:
            values[fid] = _value(f, rawv, it["warnings"])
        except (ValueError, TypeError) as e:
            bad.append(fid)
            it["errors"].append(str(e) or f"{f['label']}: invalid value")
    _always_on_counts(fields, values, it["warnings"])

    # Optional add-ons asked as yes/no ("Would you be creating an RDS Proxy...?") default to Yes in the
    # calculator and add cost; leave them off unless the user turns them on.
    for fid, f in fields.items():
        if fid in values or f["kind"] != "dropdown" or f.get("conditional"):
            continue
        no = next((o["id"] for o in f["options"] if o["label"].strip().lower() == "no"), None)
        if no is not None and re.match(r"(would|do|will|are|is|does)\b", f["label"].strip().lower()):
            values[fid] = {"value": no}
    it["values"] = values

    # Questions: what the AI flagged as price-relevant, invalid values, and the core inputs without which the
    # service costs nothing (an instance table, or EC2's required fields). Anything the user chose to leave at
    # the calculator default ("skipped") is not asked again.
    asked = [q for q in (it.get("questions") or []) if q in fields][:3]
    must = []
    if tables and not any(t in values for t in tables):
        must.append(tables[0])
    if svc["code"] == catalog.EC2:
        must += [fid for fid, f in fields.items() if f.get("required")]
    it["questions"] = [q for q in dict.fromkeys(must + bad + asked) if q not in values and q not in it["skipped"]]
    if not it["region"]:
        it["questions"].insert(0, "region")
    blocking = [q for q in it["questions"] if q == "region" or q in must]
    it["status"] = "needs_input" if it["questions"] or it["errors"] else "ready"
    it["blocking"] = blocking
    return it
