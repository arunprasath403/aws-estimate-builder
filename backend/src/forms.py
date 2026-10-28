"""Validate an item's field values against its service schema and store them in calculator format.

Item shape (shared with the web page):
  {id, service, serviceName, name, region, template, values: {fieldId: <calculator value>},
   questions: [fieldId], errors: [str], notes, original_text, details, status}
Statuses: ready | needs_input | unsupported
"""
import re

import catalog
from regions import REGIONS

UTIL_KEY = "undefined"  # most calculator tables store instance-row utilization under this key
COUNT_WORDS = ("number of", "count", "quantity", "nodes", "instances", "brokers")
INSTANCE_WORDS = ("instance", "compute", "node type", "family", "type", "name", "size")


# Service prefixes the calculator's price lists leave out (checked against calculator.aws).
UNPREFIXED = ("kafka.", "dms.")
# Row values the model copies from a dropdown label but the price lists spell differently.
VALUE_ALIASES = {"Cache Engine": {"memcache": "Memcached", "redis oss": "Redis"}}


def _like_example(value: str, example) -> str:
    """Drop a service prefix the calculator does not use: MSK lists brokers as "m5.large", not "kafka.m5.large"."""
    if isinstance(example, str) and value.count(".") > example.count(".") >= 1:
        return value.split(".", 1)[1]
    if example is None and value.lower().startswith(UNPREFIXED):
        return value.split(".", 1)[1]
    return value


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
        cols = {c["key"]: c for c in catalog.row_columns(f)}
        by_lower = {k.lower(): k for k in cols}
        util = next((k for k, c in cols.items() if c.get("type") == "utilization"), None)
        count = next((k for k, c in cols.items() if c.get("count")), None)
        lookup = next((k for k, c in cols.items() if c.get("lookup") and any(w in k.lower() for w in INSTANCE_WORDS)),
                      None)
        out = []
        for r in rows[:50]:
            if not isinstance(r, dict):
                continue
            row = {}
            for k, v in r.items():
                kl = k.lower().strip()
                if util and kl in ("utilization", UTIL_KEY, util.lower()):
                    key = util
                elif kl in by_lower:
                    key = by_lower[kl]
                elif count and any(w in kl for w in COUNT_WORDS):
                    key = count  # e.g. "Number of Nodes" written for MSK's "Number of Kafka broker nodes"
                elif lookup and lookup not in r and any(w in kl for w in INSTANCE_WORDS):
                    key = lookup  # e.g. "Instance Type" written for MSK's "Compute Family"
                else:
                    continue
                v = _scalar(v)
                if key == util:
                    if isinstance(v, dict):
                        v = v.get("unit")
                    default = cols[util].get("default")
                    custom = isinstance(default, dict) and default.get("selectedId") != "%Utilized/Month"
                    if v in (None, "") or custom:
                        continue  # e.g. WorkSpaces "Billing option": keep the calculator's Monthly/Hourly default
                    row[key] = {"value": {"unit": _fmt(_num(v)), "selectedId": "%Utilized/Month"}}
                elif v in (None, ""):
                    continue
                elif key == count:
                    row[key] = {"value": _fmt(_num(v))}
                elif cols[key].get("lookup"):
                    row[key] = {"value": _like_example(str(v).strip(), cols[key].get("default"))}
                else:
                    v = str(v).strip()
                    row[key] = {"value": VALUE_ALIASES.get(key, {}).get(v.lower(), v)}
            if not row:
                continue  # nothing from the document in this row
            if "Instance Type" in cols and "Instance Type" not in row:
                raise ValueError("Each row needs 'Instance Type'")
            if "TermType" in cols and "TermType" not in row:
                row["TermType"] = {"value": "OnDemand"}
            # Start the rest of the row the way the calculator's form does: from each column's default. Family
            # lookups without a default stay out (the calculator derives them from the instance type).
            for c in f.get("columns") or []:
                k, d = c["key"], c.get("default")
                if k in row or c.get("count") or d in (None, ""):
                    continue
                if c.get("type") == "utilization" or not isinstance(d, dict):
                    row[k] = {"value": d}
            if util and util not in row:
                row[util] = {"value": {"unit": "100", "selectedId": "%Utilized/Month"}}
            out.append(row)
        if not out:
            raise ValueError(f"{f['label'] or 'Instances'}: add at least one row")
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


def _unseen_instances(fields, values, source, notes):
    """Flag instance names that are not in the document: the model sometimes mistypes them (m7b for m7g), and an
    unknown name prices the row at $0 without any other sign."""
    if not source:
        return
    text = source.lower()
    for fid, v in values.items():
        f = fields.get(fid)
        if not f or f["kind"] != "instances":
            continue
        for c in catalog.row_columns(f):
            if not (c.get("lookup") and any(w in c["key"].lower() for w in INSTANCE_WORDS)):
                continue
            for r in v["value"]:
                name = str((r.get(c["key"]) or {}).get("value") or "")
                if "." in name and name.lower() not in text:
                    notes.append(f"{c['label']} '{name}' is not in your file. Check it before pricing.")


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
    _unseen_instances(fields, values, " ".join(str(it.get(k) or "") for k in ("original_text", "details")),
                      it["warnings"])

    # Optional add-ons default to on in the calculator and add cost: yes/no questions ("Would you be creating an
    # RDS Proxy...?") and on/off features ("Advanced security features: Enabled"). Leave them off unless the
    # document or the user turns them on. Toggles that describe usage ("Registered via ECS") are left alone.
    for fid, f in fields.items():
        if fid in values or f["kind"] != "dropdown" or f.get("conditional"):
            continue
        label = f["label"].strip().lower()
        by_label = {o["label"].strip().lower(): o["id"] for o in f["options"]}
        off = next((by_label[w] for w in ("no", "disabled", "disable", "off") if w in by_label), None)
        if off is None:
            continue
        default_on = str(f.get("default")) in {str(by_label.get(w)) for w in ("yes", "enabled", "enable", "on")}
        if re.match(r"(would|do|will|are|is|does)\b", label) or (
                default_on and not re.search(r"registered|free tier|included", label)):
            values[fid] = {"value": off}
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
