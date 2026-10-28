"""API Gateway (REST, proxy) entry point and background worker.

POST /jobs        {"type": "extract", "filename", "content_b64"} | {"type": "price"|"link", "items", "name"}
                  -> {"job_id"}
GET  /jobs/{id}   -> {"status": "queued"|"running"|"done"|"error", "result"?, "error"?}
GET  /services    -> [{"code", "name"}]           (all calculator services, for changing an item's service)
GET  /services/{code} -> service schema (templates, fields, regions)
"""
import base64
import binascii
import json
import logging
import os
import re

import boto3

import catalog
import jobs

log = logging.getLogger()
log.setLevel(logging.INFO)

MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_ITEMS = 100
WORKER_FUNCTION = os.environ.get("WORKER_FUNCTION")
_lambda = boto3.client("lambda") if WORKER_FUNCTION else None
HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
}


class BadRequest(Exception):
    pass


def _resp(status, body, cache=False):
    headers = dict(HEADERS, **({"Cache-Control": "public, max-age=3600"} if cache else {}))
    return {"statusCode": status, "headers": headers, "body": json.dumps(body)}


def lambda_handler(event, _context, start=None):
    method, path = event.get("httpMethod", ""), event.get("path", "")
    if method == "OPTIONS":
        return _resp(204, {})
    try:
        if method == "POST" and path.endswith("/jobs"):
            return _resp(202, {"job_id": create_job(json.loads(event.get("body") or "{}"), start)})
        m = re.search(r"/jobs/([0-9a-f]{32})$", path)
        if method == "GET" and m:
            st = jobs.get(m.group(1), "status")
            return _resp(200, st) if st else _resp(404, {"error": "Unknown job"})
        if method == "GET" and path.endswith("/services"):
            return _resp(200, jobs.services_list(), cache=True)
        m = re.search(r"/services/([A-Za-z0-9_-]+)$", path)
        if method == "GET" and m:
            s = catalog.get(m.group(1))
            if not s or s.get("group"):
                return _resp(404, {"error": "Unknown service"})
            return _resp(200, {"code": s["code"], "name": catalog.display_name(s), "templates": s["templates"],
                               "regions": catalog.regions_for(s)}, cache=True)
        return _resp(404, {"error": "Not found"})
    except (BadRequest, ValueError) as e:
        return _resp(400, {"error": str(e)})
    except Exception:
        log.exception("unhandled")
        return _resp(500, {"error": "Something went wrong. Please try again."})


def create_job(body: dict, start=None) -> str:
    t = body.get("type")
    if t == "extract":
        try:
            data = base64.b64decode(body.get("content_b64") or "", validate=True)
        except (binascii.Error, ValueError):
            raise BadRequest("File content is not valid base64.")
        if not data:
            raise BadRequest("The file is empty.")
        if len(data) > MAX_FILE_BYTES:
            raise BadRequest("File is larger than 4 MB.")
        req = {"type": t, "filename": str(body.get("filename") or "")[:200], "content_b64": body["content_b64"]}
    elif t in ("price", "link"):
        items = body.get("items")
        if not isinstance(items, list) or not items:
            raise BadRequest("No items to price.")
        if len(items) > MAX_ITEMS:
            raise BadRequest(f"At most {MAX_ITEMS} items per estimate.")
        req = {"type": t, "items": [i for i in items if isinstance(i, dict)],
               "name": (str(body.get("name") or "").strip() or "Project estimate")[:100]}
    else:
        raise BadRequest("Unknown job type.")
    job_id = jobs.new_id()
    jobs.put(job_id, "request", req)
    jobs.put(job_id, "status", {"status": "queued"})
    log.info("job %s type=%s", job_id, t)
    if _lambda:
        _lambda.invoke(FunctionName=WORKER_FUNCTION, InvocationType="Event", Payload=json.dumps({"job_id": job_id}))
    elif start:
        start(job_id)  # local dev server runs it in a thread
    return job_id


def worker_handler(event, _context):
    jobs.run(event["job_id"])
