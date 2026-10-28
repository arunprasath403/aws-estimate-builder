"""Background jobs: extract (file -> items) and price/link (items -> calculator prices, optional public link).

Jobs run asynchronously because they take 10-60 s, longer than API Gateway's 29 s limit. The API stores the
request and starts the worker; the page polls GET /jobs/{id}. State lives in S3 (JOBS_BUCKET) on AWS and in
memory for local development.
"""
import base64
import json
import os
import subprocess
import time
import uuid

import boto3
from botocore.config import Config

import ai
import catalog
import estimate
import forms
from parse import to_text

JOBS_BUCKET = os.environ.get("JOBS_BUCKET")
RENDERER_FUNCTION = os.environ.get("RENDERER_FUNCTION")
RENDERER_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "renderer")
_s3 = boto3.client("s3") if JOBS_BUCKET else None
# Pricing large estimates can take minutes: wait for the renderer (300 s timeout) and never retry, since a
# retried invoke would start a second, parallel render.
_lambda = (boto3.client("lambda", config=Config(read_timeout=310, retries={"total_max_attempts": 1}))
           if RENDERER_FUNCTION else None)
_memory: dict[str, dict] = {}


def new_id() -> str:
    return uuid.uuid4().hex


def put(job_id: str, name: str, obj) -> None:
    if _s3:
        _s3.put_object(Bucket=JOBS_BUCKET, Key=f"jobs/{job_id}/{name}.json", Body=json.dumps(obj).encode(),
                       ContentType="application/json")
    else:
        _memory[f"{job_id}/{name}"] = json.loads(json.dumps(obj))


def get(job_id: str, name: str):
    if _s3:
        try:
            body = _s3.get_object(Bucket=JOBS_BUCKET, Key=f"jobs/{job_id}/{name}.json")["Body"].read()
        except _s3.exceptions.NoSuchKey:
            return None
        return json.loads(body)
    return _memory.get(f"{job_id}/{name}")


def run(job_id: str) -> None:
    req = get(job_id, "request")
    put(job_id, "status", {"status": "running", "started": time.time()})
    try:
        if req["type"] == "extract":
            result = _extract(req)
        else:
            result = _price(req, save=req["type"] == "link")
        put(job_id, "status", {"status": "done", "result": result})
    except UserError as e:
        put(job_id, "status", {"status": "error", "error": str(e)})
    except Exception as e:  # noqa: BLE001 - report every failure to the page
        print(f"job {job_id} failed: {e!r}")
        put(job_id, "status", {"status": "error", "error": "Something went wrong. Please try again."})


class UserError(Exception):
    pass


def _extract(req) -> dict:
    data = base64.b64decode(req["content_b64"])
    text = to_text(req["filename"], data)
    default_region, items, not_priced = ai.identify(text)
    for i, it in enumerate(items):
        it["id"] = f"i{i + 1}"
        it["region"] = it["region"] or default_region
    ai.fill_all(items)
    return {"items": [forms.normalize(it) for it in items], "default_region": default_region,
            "not_priced": not_priced}


def _price(req, save: bool) -> dict:
    items = [forms.normalize(it) for it in req["items"]]
    if save and any(it["status"] == "needs_input" for it in items):
        raise UserError("Answer the highlighted questions before generating the link.")
    est, keys = estimate.build(items, req.get("name") or "Project estimate")
    if not keys:
        raise UserError("None of the items can be priced yet.")
    out = render({"estimate": est, "save": save, "name": req.get("name") or "Project estimate",
                  "timeoutMs": min(280000, 40000 + 6000 * len(keys))})
    if out.get("error"):
        raise UserError("The AWS Pricing Calculator could not price this estimate: " + out["error"])
    for it in items:
        s = out["services"].get(keys.get(it["id"], ""))
        if s:
            it.update(monthly=s["monthly"], upfront=s["upfront"], configSummary=s["configSummary"])
            if not s["monthly"] and not s["upfront"]:
                it["warning"] = "The calculator priced this at $0. Check the inputs (instance type, usage)."
    return {"items": items, "total": out["total"], "url": out.get("url")}


def render(event: dict) -> dict:
    if _lambda:
        resp = _lambda.invoke(FunctionName=RENDERER_FUNCTION, Payload=json.dumps(event).encode())
        return json.loads(resp["Payload"].read())
    proc = subprocess.run(["node", "cli.mjs"], cwd=RENDERER_DIR, input=json.dumps(event), capture_output=True,
                          text=True, timeout=320, shell=os.name == "nt")
    if proc.returncode:
        return {"error": proc.stderr[-300:]}
    return json.loads(proc.stdout)


def services_list() -> list[dict]:
    return sorted(({"code": s["code"], "name": catalog.display_name(s)} for s in catalog.selectable()),
                  key=lambda s: s["name"].lower())
