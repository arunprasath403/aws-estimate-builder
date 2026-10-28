"""Build deployment zips into dist/:
  backend.zip   Python API + worker (src, data/catalog.json, Linux arm64 wheels)
  renderer.zip  Node renderer with node_modules (headless Chromium)
  frontend.zip  static web page

Uses Python's zipfile so entries get forward slashes (PowerShell's Compress-Archive breaks Lambda imports).
Prints "<backend hash> <renderer hash>" on the last line.
"""
import hashlib
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DIST = os.path.join(ROOT, "dist")
DEPS = os.path.join(DIST, "deps")
SKIP_FILES = (".d.ts", ".map", ".md", ".ts")
SKIP_RENDERER = {"local_test.mjs", "test_estimate.json", "package-lock.json"}


def zip_dir(zf, folder, prefix="", skip=()):
    for base, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if f in skip or f.endswith(SKIP_FILES) and "node_modules" in base:
                continue
            full = os.path.join(base, f)
            arc = os.path.join(prefix, os.path.relpath(full, folder)).replace(os.sep, "/")
            zf.write(full, arc)


def digest(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()[:12]


def main():
    shutil.rmtree(DIST, ignore_errors=True)
    os.makedirs(DEPS)
    subprocess.check_call([
        sys.executable, "-m", "pip", "install", "-q", "-r", os.path.join(ROOT, "backend", "requirements.txt"),
        "--target", DEPS, "--platform", "manylinux2014_aarch64", "--implementation", "cp",
        "--python-version", "3.12", "--only-binary=:all:", "--upgrade",
    ])
    backend = os.path.join(DIST, "backend.zip")
    with zipfile.ZipFile(backend, "w", zipfile.ZIP_DEFLATED) as zf:
        zip_dir(zf, DEPS)
        zip_dir(zf, os.path.join(ROOT, "backend", "src"))
    renderer = os.path.join(DIST, "renderer.zip")
    with zipfile.ZipFile(renderer, "w", zipfile.ZIP_DEFLATED) as zf:
        zip_dir(zf, os.path.join(ROOT, "renderer"), skip=SKIP_RENDERER)
    with zipfile.ZipFile(os.path.join(DIST, "frontend.zip"), "w", zipfile.ZIP_DEFLATED) as zf:
        zip_dir(zf, os.path.join(ROOT, "frontend"))
    shutil.rmtree(DEPS)
    print(f"{digest(backend)} {digest(renderer)}")


if __name__ == "__main__":
    main()
