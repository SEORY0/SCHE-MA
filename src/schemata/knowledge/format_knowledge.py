"""Format-aware knowledge: harness conventions and input format templates.

Separate from atomic_vulns.py by concern: atomic_vulns tells the agent WHAT invariant to
violate; this module tells it HOW to construct bytes the harness actually accepts (valid
magic, header structure, FDP consumption order, etc.).
"""
from __future__ import annotations

import json
from functools import lru_cache

from ..core.config import SKILLS_DIR

_HARNESS_PATH = SKILLS_DIR / "knowledge" / "harness_conventions.json"
_FORMAT_PATH = SKILLS_DIR / "knowledge" / "format_templates.json"

_PROJECT_TO_FORMAT: dict[str, str] = {
    "binutils": "elf",
    "ghostscript": "postscript",
    "ffmpeg": "media-container",
    "opensc": "asn1-smartcard",
    "wireshark": "pcap-network",
    "librawspeed": "raw-image",
    "mruby": "ruby-source",
    "libxml2": "xml",
    "freetype2": "font",
    "freetype": "font",
    "harfbuzz": "font",
    "mupdf": "pdf",
    "ndpi": "network-dpi",
    "libredwg": "dwg",
    "graphicsmagick": "raster-image",
    "imagemagick": "raster-image",
    "gpac": "mp4-isobmff",
    "libdwarf": "dwarf-debug",
    "c-blosc2": "dwarf-debug",
    "blosc2": "dwarf-debug",
    "libtiff": "raster-image",
    "libpng": "raster-image",
    "libjpeg-turbo": "raster-image",
    "openjpeg": "raster-image",
    "libheif": "mp4-isobmff",
}

_INPUT_FORMAT_TO_FORMAT: dict[str, str] = {
    "elf": "elf",
    "pe": "elf",
    "postscript": "postscript",
    "ps": "postscript",
    "pdf": "pdf",
    "avi": "media-container",
    "mkv": "media-container",
    "mp4": "mp4-isobmff",
    "flv": "media-container",
    "mov": "mp4-isobmff",
    "wav": "media-container",
    "ogg": "media-container",
    "asn1": "asn1-smartcard",
    "pkcs": "asn1-smartcard",
    "pcap": "pcap-network",
    "pcapng": "pcap-network",
    "raw": "raw-image",
    "cr2": "raw-image",
    "nef": "raw-image",
    "arw": "raw-image",
    "dng": "raw-image",
    "ruby": "ruby-source",
    "rb": "ruby-source",
    "xml": "xml",
    "html": "xml",
    "svg": "xml",
    "ttf": "font",
    "otf": "font",
    "woff": "font",
    "woff2": "font",
    "dwg": "dwg",
    "dxf": "dwg",
    "tiff": "raster-image",
    "tif": "raster-image",
    "png": "raster-image",
    "jpeg": "raster-image",
    "jpg": "raster-image",
    "gif": "raster-image",
    "bmp": "raster-image",
    "webp": "raster-image",
    "isobmff": "mp4-isobmff",
    "dwarf": "dwarf-debug",
    "blosc": "dwarf-debug",
    "blosc2": "dwarf-debug",
}


@lru_cache(maxsize=1)
def load_harness_conventions() -> dict[str, dict]:
    with open(_HARNESS_PATH, encoding="utf-8") as f:
        return json.load(f)["conventions"]


@lru_cache(maxsize=1)
def load_format_templates() -> dict[str, dict]:
    with open(_FORMAT_PATH, encoding="utf-8") as f:
        return json.load(f)["formats"]


def harness_advice(convention: str | None) -> str:
    """Render harness convention advice for the generate prompt."""
    if not convention:
        return ""
    convs = load_harness_conventions()
    key = convention.lower().replace(" ", "-").replace("_", "-")
    entry = convs.get(key)
    if not entry:
        for k, v in convs.items():
            if key in k or k in key:
                entry = v
                key = k
                break
    if not entry:
        return ""
    lines = [
        f"<harness_convention type=\"{key}\">",
        f"**Input contract**: {entry['input_contract']}",
        f"**PoC shape**: {entry['poc_shape']}",
    ]
    if entry.get("common_gates"):
        lines.append(f"**Common gates**: {', '.join(entry['common_gates'])}")
    if entry.get("fdp_patterns"):
        lines.append("**FDP consumption patterns** (if FuzzedDataProvider is used):")
        for method, layout in entry["fdp_patterns"].items():
            lines.append(f"  - `{method}`: {layout}")
    lines.append("</harness_convention>")
    return "\n".join(lines)


def _resolve_format_key(input_format: str | None, project: str | None) -> str | None:
    """Map input_format or project name to a format template key."""
    if project:
        proj_lower = project.lower().replace(" ", "-").replace("_", "-")
        if proj_lower in _PROJECT_TO_FORMAT:
            return _PROJECT_TO_FORMAT[proj_lower]
    if input_format:
        fmt_lower = input_format.lower().replace(" ", "-").replace("_", "-")
        if fmt_lower in _INPUT_FORMAT_TO_FORMAT:
            return _INPUT_FORMAT_TO_FORMAT[fmt_lower]
        fmt_parts = fmt_lower.split("-")
        for part in fmt_parts:
            if part in _INPUT_FORMAT_TO_FORMAT:
                return _INPUT_FORMAT_TO_FORMAT[part]
    return None


def format_advice(input_format: str | None, project: str | None) -> str:
    """Render format template advice for the generate prompt."""
    key = _resolve_format_key(input_format, project)
    if not key:
        return ""
    templates = load_format_templates()
    entry = templates.get(key)
    if not entry:
        return ""
    lines = [
        f"<format_template family=\"{key}\">",
        f"**Format**: {entry.get('label', key)}",
        f"**Magic bytes**: `{entry['magic']}`" if entry.get("magic") else "",
        f"**Minimum header**: {entry['header_min_bytes']} bytes" if entry.get("header_min_bytes") else "",
        f"**Structure**: {entry['structure']}",
    ]
    if entry.get("key_fields"):
        lines.append("**Key fields** (use valid defaults for non-violation fields):")
        for field, desc in entry["key_fields"].items():
            lines.append(f"  - `{field}`: {desc}")
    if entry.get("seed_paths"):
        lines.append(f"**Seed search paths**: {', '.join(entry['seed_paths'])}")
    lines = [l for l in lines if l]
    lines.append("</format_template>")
    return "\n".join(lines)
