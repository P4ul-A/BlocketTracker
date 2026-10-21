"""Consistent vehicle identity for matching, training groups, and diagnostics."""

import re
import unicodedata


def name_key(value):
    """Normalize make names across enum separators, punctuation, and accents."""
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", text.casefold())


def model_key(value):
    """Ignore model spacing and case while preserving meaningful punctuation/suffixes."""
    return re.sub(r"\s+", "", value.casefold())


def vehicle_group(make, model):
    """Return the same key for labels such as XC 90 and XC90."""
    return name_key(make) + "/" + model_key(model)


def model_query(value):
    """Join a short model prefix and number for upstream queries, e.g. XC 90."""
    match = re.fullmatch(r"([a-zA-Z]{1,3})\s+(\d+)", value.strip())
    return "".join(match.groups()) if match else value.strip()
