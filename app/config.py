"""Environment configuration shared by the web server and collector."""

import os
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
API_BASE = os.getenv("BLOCKET_API_URL", "https://blocket-api.se").rstrip("/")
MAX_PAGES = max(1, min(50, int(os.getenv("MAX_PAGES", "50"))))
DETAIL_LIMIT = max(0, int(os.getenv("DETAIL_LIMIT", "100")))
REQUEST_DELAY = max(0.2, float(os.getenv("REQUEST_DELAY", "0.5")))
GEOCODING = os.getenv("GEOCODING", "true").lower() == "true"
GEOCODER_USER_AGENT = os.getenv("GEOCODER_USER_AGENT", "BlocketTracker/1.0 personal NAS car tracker")
GEOCODER_URL = os.getenv("GEOCODER_URL", "https://nominatim.openstreetmap.org/search")

# External Ollama service; localhost is appropriate when running Python natively.
LLM_ENABLED = os.getenv("LLM_ENABLED", "false").lower() == "true"
LLM_URL = os.getenv("LLM_URL", "http://localhost:11434").rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen3:1.7b")
LLM_TIMEOUT = max(10, float(os.getenv("LLM_TIMEOUT", "180")))
LLM_THREADS = max(1, int(os.getenv("LLM_THREADS", "2")))
