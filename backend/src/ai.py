"""Two-stage extraction with Bedrock (gpt-oss-120b).

Stage 1: read the document, list the AWS resources and match each to a calculator service code.
Stage 2: for each item, fill that service's calculator fields from the document; list missing ones.
"""
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor

import boto3
from botocore.config import Config

import catalog
from regions import REGIONS

MODEL_ID = os.environ.get("MODEL_ID", "openai.gpt-oss-120b-1:0")
_bedrock = boto3.client("bedrock-runtime", region_name=os.environ.get("BEDROCK_REGION", "us-east-1"),
                        config=Config(read_timeout=120, retries={"max_attempts": 3, "mode": "adaptive"}))
MAX_ITEMS = 100
CHUNK_LINES = 25      # stage 1 reads long documents in parts so each answer stays well under maxTokens
CHUNK_CHARS = 5000
# Resources that have no AWS charge; matched in code (not by the model) so a paid service is never dropped.
FREE = re.compile(r"shield standard|public certificates?|certificate manager.{0,40}public|\biam\b|identity and access"
                  r"|security groups?|cloudformation|aws organizations", re.I)
PAID = re.compile(r"shield advanced|private ca|private certificate|access analyzer|identity center|roles anywhere", re.I)

REGION_HINTS = ("Mumbai=ap-south-1, Hyderabad=ap-south-2, Singapore=ap-southeast-1, Sydney=ap-southeast-2, "
                "Jakarta=ap-southeast-3, Tokyo=ap-northeast-1, Seoul=ap-northeast-2, N. Virginia=us-east-1, "
                "Ohio=us-east-2, Oregon=us-west-2, N. California=us-west-1, Ireland=eu-west-1, London=eu-west-2, "
                "Paris=eu-west-3, Frankfurt=eu-central-1, Stockholm=eu-north-1, Bahrain=me-south-1, UAE=me-central-1, "
                "Sao Paulo=sa-east-1, Canada=ca-central-1")

STAGE1 = """You read project documents (sizing sheets, architecture docs, BOMs) and list every AWS resource that
should appear in an AWS Pricing Calculator estimate.

Return ONLY JSON, no prose:
{{"default_region": "<region code or null>",
  "items": [{{"service": "<service code from the list>", "name": "<short label>", "region": "<region code or null>",
              "details": "<every fact from the document about this resource: type, size, count, usage, hours, storage,
                          requests, data transfer, engine, OS, pricing model...>",
              "original_text": "<exact source line/phrase>"}}]}}

Rules:
- "service" MUST be a code from SERVICES below. Pick the most specific match (e.g. a specific S3 storage class,
  "Amazon RDS for PostgreSQL" rather than Aurora unless Aurora is stated). Virtual servers/VMs => ec2Enhancement.
- One item per distinct resource line. Keep different instance types / sizes as separate items.
- EBS volumes attached to EC2 servers belong in that EC2 item's details, not a separate item.
- Region codes: {regions}. Valid codes: {codes}. If the document states one region for everything, put it in
  default_region and leave item regions null. Never guess a region.
- Never leave a resource out. If no code in SERVICES fits, set "service" to the AWS service name as the document
  writes it (e.g. "Amazon MSK"); the user will pick the service.
- Do not invent resources the document does not mention. If there are none, return {{"default_region": null, "items": []}}.

SERVICES (code | name | keywords):
{index}"""

STAGE2 = """You fill AWS Pricing Calculator fields for ONE resource, using only facts from the document.

{schema}

Return ONLY JSON, no prose:
{{"template": "<TEMPLATE id>",
  "values": {{"<field id>": <value>}},
  "questions": ["<field id>", ...],
  "notes": "<one short sentence on assumptions, or empty>"}}

Value format by field kind:
- number / percent / text: a number or string, e.g. 3
- dropdown: one option id exactly as listed (left of '='), e.g. "1000000"
- checkbox: true or false
- frequency / size / duration / throughput: {{"value": <number>, "unit": "<unit id, left of the brackets>"}}.
  READ THE UNIT LABEL: if it says "million per month", 100 million requests is value 100, not 100000000.
  Always-on resources (24x7): count = the number running (per month), duration = 730 with the "hr" unit
  (never 730 days). Double-check every unit: value x unit must equal what the document says.
  Sizes: pick the unit matching the document (1 TB => value 1 with the tb unit if listed, else 1024 GB).
- instances: [{{"<row column>": <value>, ...}}] one object per instance row, keyed by the exact row column
  names listed. The column marked "count" is how many run (brokers, nodes, instances). Write instance names in
  the same format as the column's example: if the example is "m5.large", write "m7g.large" (not
  "kafka.m7g.large"); if it is "cache.m5.xlarge", keep the prefix. "utilization" = percent of the month it runs
  (100 = always on). "TermType" = "OnDemand" unless the document says Reserved. "Deployment Option" = e.g.
  "Single-AZ" / "Multi-AZ" when stated. Leave out columns the document says nothing about.
- datatransfer: [{{"type": "OUTBOUND"|"INBOUND"|"INTRA_REGION", "gb_per_month": <number>}}]

Rules:
- Only set fields the document gives (directly or by simple conversion: 24x7 = 730 hours = 100% utilization,
  1 TB = 1024 GB, "2M requests" = 2 with a "millions" units dropdown or 2000000 exact). Never invent values.
- If the service has an "instances" field and the document names an instance type, you MUST fill that field
  with at least one row (count, exact instance type, utilization, deployment option when stated).
- When the document gives a total in a different shape than the fields, convert it instead of leaving the
  fields empty, and say so in "notes". Examples: "10 TB scanned per month" => 1 query per month scanning 10 TB;
  "3 TB ingested per month" with record count and record size fields => records per month with a record size
  that multiplies to 3 TB (e.g. 5 KB records); "3 TB of snapshots" => fill the snapshot storage fields;
  "2 million state transitions per month" with workflow requests and transitions-per-workflow fields =>
  2000000 requests per month with 1 transition per workflow; "10 million characters per month" with requests
  and characters-per-request fields => 1 request per month of 10000000 characters.
  Only convert numbers the document states; never make up usage it does not mention.
- "questions": at most 3 field ids that drive most of this resource's cost but are NOT stated in the document
  (e.g. instance type, count, storage size, request volume). Never list optional add-on features (provisioned
  concurrency, snapshots, savings plans, extended support, proxies) or fields with sensible defaults.
- Optional add-ons that default to "Yes" in the calculator (e.g. RDS Proxy, Database Insights, Extended Support)
  should be set to their "No" option unless the document asks for them.
- If several TEMPLATEs exist, choose the one that matches the document; default to the first.

RESOURCE: {name}
REGION: {region}
FACTS: {details}
SOURCE TEXT: {original}"""


class AIError(Exception):
    pass


def _ask(system: str, user: str, max_tokens: int) -> dict:
    resp = _bedrock.converse(
        modelId=MODEL_ID,
        system=[{"text": system}],
        messages=[{"role": "user", "content": [{"text": user}]}],
        inferenceConfig={"maxTokens": max_tokens, "temperature": 0},
    )
    text = "".join(c.get("text", "") for c in resp["output"]["message"]["content"])
    return _parse_json(text)


def _parse_json(s: str):
    s = re.sub(r"<reasoning>.*?</reasoning>", "", s, flags=re.S).strip()
    s = re.sub(r"^```(?:json)?|```$", "", s, flags=re.M).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        a, b = s.find("{"), s.rfind("}")
        if a >= 0 and b > a:
            try:
                return json.loads(s[a:b + 1])
            except json.JSONDecodeError:
                pass
    raise AIError("Could not read the model's answer as JSON.")


def _chunks(text: str) -> list[str]:
    """Split a long document into parts; table rows ("a | b | c") keep their header line in every part."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) <= CHUNK_LINES and len(text) <= CHUNK_CHARS:
        return [text]
    header = lines[0] if " | " in lines[0] else None
    body = lines[1:] if header else lines
    parts, cur, size = [], [], 0
    for ln in body:
        if cur and (len(cur) >= CHUNK_LINES or size + len(ln) > CHUNK_CHARS):
            parts.append(cur)
            cur, size = [], 0
        cur.append(ln)
        size += len(ln) + 1
    if cur:
        parts.append(cur)
    return ["\n".join(([header] if header else []) + p) for p in parts]


def identify(text: str) -> tuple[str | None, list[dict], list[dict]]:
    system = STAGE1.format(regions=REGION_HINTS, codes=", ".join(REGIONS), index=catalog.service_index_text())
    parts = _chunks(text)
    note = "" if len(parts) == 1 else "This is one part of a longer document; list only the resources in this part.\n\n"
    with ThreadPoolExecutor(max_workers=8) as ex:
        answers = list(ex.map(lambda p: _ask(system, note + "DOCUMENT:\n\n" + p, 8000), parts))
    regions = [a.get("default_region") for a in answers if a.get("default_region") in REGIONS]
    region = max(set(regions), key=regions.count) if regions else None
    found = []
    for a in answers:
        # A part's own default region only matters when it differs from the document-wide one.
        part_region = a.get("default_region") if a.get("default_region") in REGIONS else None
        found += [(i, part_region if part_region != region else None) for i in a.get("items") or []]
    items = []
    not_priced, kept = [], []
    for it, part_region in found:
        text = f"{it.get('name')} {it.get('original_text')} {it.get('details')}" if isinstance(it, dict) else ""
        if FREE.search(text) and not PAID.search(text):
            not_priced.append({"name": str(it.get("name") or "")[:120]})
        else:
            kept.append((it, part_region))
    found = kept
    for it, part_region in found[:MAX_ITEMS]:
        if not isinstance(it, dict):
            continue
        raw = str(it.get("service") or "").strip()
        code = catalog.resolve(raw)
        items.append({
            "service": code,
            "guess": None if code else raw,
            "name": str(it.get("name") or "")[:120],
            "region": it.get("region") if it.get("region") in REGIONS else part_region,
            "details": str(it.get("details") or "")[:1500],
            "original_text": str(it.get("original_text") or "")[:400],
        })
    return region, items, not_priced


def fill(item: dict) -> dict:
    svc = catalog.get(item["service"])
    prompt = STAGE2.format(schema=catalog.schema_text(svc), name=item["name"] or svc["name"],
                           region=item.get("region") or "not stated", details=item["details"] or "-",
                           original=item["original_text"] or "-")
    try:
        data = _ask("You are precise and never invent values.", prompt, 4000)
    except AIError:
        data = {}
    return {
        "template": data.get("template"),
        "values": data.get("values") if isinstance(data.get("values"), dict) else {},
        "questions": [q for q in data.get("questions") or [] if isinstance(q, str)],
        "notes": str(data.get("notes") or "")[:300],
    }


def fill_all(items: list[dict]) -> list[dict]:
    todo = [it for it in items if it["service"]]
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(fill, todo))
    for it, r in zip(todo, results):
        it.update(r)
    return items
