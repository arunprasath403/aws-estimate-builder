"""Turn validated items into an AWS Pricing Calculator estimate (inputs only; the calculator prices it)."""
import uuid
from datetime import datetime, timezone

import catalog
import regions

_EMPTY_DT = [
    {"entryType": "INBOUND", "value": "", "unit": "tb_month", "fromRegion": ""},
    {"entryType": "OUTBOUND", "value": "", "unit": "tb_month", "toRegion": ""},
    {"entryType": "INTRA_REGION", "value": "", "unit": "tb_month"},
]


def _v(values, key, default=None):
    return (values.get(key) or {}).get("value", default)


def _ec2_components(values):
    qty = int(float(_v(values, "quantity", 1)))
    hours = float(_v(values, "hours", 730))
    util = f"{min(100.0, hours / 730 * 100):.2f}".rstrip("0").rstrip(".")
    return {
        "tenancy": {"value": "shared"},
        "selectedOS": {"value": _v(values, "os", "linux")},
        "workloadSelection": {"value": "consistent"},
        "storageType": {"value": "Storage General Purpose gp3 GB Mo"},
        "dataTransferForEC2": {"value": _EMPTY_DT},
        "workload": {"value": {"workloadType": "consistent", "data": str(qty)}},
        "snapshotFrequency": {"value": "0"},
        "detailedMonitoringCheckbox": {"value": False},
        "ec2AdvancedPricingMetrics": {"value": qty},
        "instanceType": {"value": _v(values, "instanceType")},
        "pricingStrategy": {"value": {"selectedOption": "on-demand", "term": "1 year",
                                      "utilizationValue": util, "utilizationUnit": "%Utilized/Month"}},
        "storageAmount": {"value": _v(values, "storageGb", "0"), "unit": "gb|NA"},
    }


def _defaults(svc, it):
    """Field defaults the calculator's own form would have stored; its formulas return $0 without them."""
    out = {}
    for f in catalog.template(svc, it["template"])["fields"]:
        if "default" not in f or not catalog.cond_ok(f.get("showIf"), it["values"]):
            continue
        d = f["default"]
        if f["kind"] in ("number", "percent"):
            out[f["id"]] = {"value": str(d)}
        elif f["kind"] == "dropdown":
            out[f["id"]] = {"value": d}
        elif f["kind"] == "checkbox":
            out[f["id"]] = {"value": bool(d)}
        elif f["kind"] in ("duration", "throughput"):
            out[f["id"]] = {"value": str(d), "unit": f.get("defaultUnit")}
    return out


def _entry(svc, it):
    if svc["code"] == catalog.EC2:
        comps = _ec2_components(it["values"])
    else:
        # Drop values for fields hidden by the user's other choices (e.g. memory options for another vCPU size).
        fields = {f["id"]: f for f in catalog.template(svc, it["template"])["fields"]}
        visible = {k: v for k, v in it["values"].items()
                   if catalog.cond_ok((fields.get(k) or {}).get("showIf"), it["values"])}
        comps = {**_defaults(svc, it), **visible}
    return {
        "calculationComponents": comps,
        "serviceCode": svc["code"], "region": it["region"], "estimateFor": it["template"],
        "version": svc.get("version"), "description": it.get("name") or None,
        "serviceCost": {"monthly": 0, "upfront": 0},
        "serviceName": svc["name"], "regionName": regions.name(it["region"]), "configSummary": "",
    }


def build(items: list[dict], name: str) -> tuple[dict, dict]:
    """Return (estimate, {item id: service key}) for all ready items."""
    services, keys = {}, {}
    for it in items:
        if it.get("status") != "ready":
            continue
        svc = catalog.get(it["service"])
        entry = _entry(svc, it)
        parent = catalog.get(svc.get("parent") or "")
        if parent:
            child = {k: entry[k] for k in ("calculationComponents", "serviceCode", "region", "estimateFor",
                                           "version", "description", "serviceCost")}
            entry = {
                "version": parent.get("version"), "serviceCode": parent["code"],
                "estimateFor": parent.get("templateId"), "region": it["region"], "description": it.get("name") or None,
                "subServices": [child], "serviceCost": {"monthly": 0, "upfront": 0},
                "serviceName": parent["name"], "regionName": regions.name(it["region"]), "configSummary": "",
            }
        key = f"{entry['serviceCode']}-{uuid.uuid4()}"
        services[key] = entry
        keys[it["id"]] = key
    return {
        "name": name, "services": services, "groups": {},
        "groupSubtotal": {"monthly": 0, "upfront": 0}, "totalCost": {"monthly": 0, "upfront": 0}, "support": {},
        "metaData": {"locale": "en_US", "currency": "USD", "source": "calculator-platform",
                     "createdOn": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")},
    }, keys
