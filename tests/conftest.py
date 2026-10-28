import os
import sys

# Backend modules import as top-level names inside Lambda; mirror that for tests.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend", "src"))
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
