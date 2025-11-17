from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import pytz
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

# Add the parent directory to Python path so we can import config
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.config_loader import Config, ConfigError

logger = logging.getLogger(__name__)

# Try to load config but be resilient: Config may validate GEMINI_API_KEY and raise when
# running this server in isolation. Fall back to sane defaults and environment variables.
try:
    config = Config()
except ConfigError as e:
    logger.warning(
        "Config load warning: %s. Falling back to defaults and environment variables.",
        e,
    )
    config = None
except Exception as e:
    # Unexpected error - log and continue with defaults
    logger.exception("Unexpected error loading config: %s", e)
    config = None


def _config_get(path, default=None):
    """Utility to safely get nested config values from Config when available."""
    if not config:
        return default
    try:
        # accept dot-separated path
        parts = path.split(".")
        cur = config._config
        for p in parts:
            cur = cur.get(p, {})
        return cur or default
    except Exception:
        return default


# Get the project root directory (parent of src/)
PROJECT_ROOT = Path(__file__).parent.parent.absolute()

OUTPUT_DIR = Path(
    str(
        _config_get(
            "cmsd_xml.output_dir", os.getenv("CMSD_OUTPUT_DIR", "data/CMSD_XML")
        )
    )
)

# If OUTPUT_DIR is relative, resolve it relative to project root
if not OUTPUT_DIR.is_absolute():
    OUTPUT_DIR = PROJECT_ROOT / OUTPUT_DIR

FILE_PREFIX = str(
    _config_get("cmsd_xml.file_prefix", os.getenv("CMSD_FILE_PREFIX", "cmsd_xml_"))
)
FILE_EXTENSION = str(
    _config_get("cmsd_xml.file_extension", os.getenv("CMSD_FILE_EXTENSION", ".xml"))
)

# Shared secret for simple auth. Recommended to store in .env and load into environment.
SHARED_SECRET = os.getenv("LOCAL_SERVER_SHARED_SECRET") or _config_get(
    "server.shared_secret", None
)

app = FastAPI(title="ASMG Local Receiver")


def _timestamped_filename(prefix: str, extension: str) -> str:
    tz = pytz.timezone("CET")
    now = datetime.now(tz)
    ts = now.strftime("%d%m%Y%H%M%S")
    return f"{prefix}{ts}{extension}"


def save_xml_to_disk(
    xml_text: str, directory: Path, prefix: str, extension: str
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    filename = _timestamped_filename(prefix, extension)
    filepath = directory / filename
    filepath.write_text(xml_text, encoding="utf-8")
    logger.info("Saved XML to %s", filepath)
    return filepath


@app.post("/save-xml")
@app.post("/process_xml")  # Alias for consistency with documentation
async def save_xml(request: Request, x_shared_secret: Optional[str] = Header(None)):
    # Simple auth
    if SHARED_SECRET:
        if not x_shared_secret or x_shared_secret != SHARED_SECRET:
            logger.warning("Unauthorized request to /save-xml")
            raise HTTPException(status_code=401, detail="Unauthorized")

    content_type = request.headers.get("content-type", "")
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty request body")

    # Accept raw XML (text/xml or application/xml) or JSON {"xml": "..."}
    xml_text = None
    if "application/json" in content_type:
        try:
            body_json = await request.json()
            xml_text = body_json.get("xml")
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON payload")
    else:
        # treat as raw XML text
        try:
            xml_text = body.decode("utf-8")
        except Exception:
            raise HTTPException(
                status_code=400, detail="Cannot decode request body as utf-8"
            )

    if not xml_text or not xml_text.strip():
        raise HTTPException(status_code=400, detail="No XML content provided")

    # Handle markdown-wrapped XML from AI tools like Dify
    xml_text = xml_text.strip()
    if xml_text.startswith("```xml") and xml_text.endswith("```"):
        # Extract XML from markdown code block
        lines = xml_text.split("\n")
        if len(lines) >= 3:
            # Remove first line (```xml) and last line (```)
            xml_text = "\n".join(lines[1:-1])

    # Optional: lightweight well-formedness check
    try:
        # ElementTree is in stdlib; import locally to avoid top-level overhead
        import xml.etree.ElementTree as ET

        ET.fromstring(xml_text)
    except Exception as e:
        logger.exception("XML validation failed: %s", e)
        raise HTTPException(status_code=400, detail=f"Invalid XML: {e}")

    try:
        saved_path = save_xml_to_disk(xml_text, OUTPUT_DIR, FILE_PREFIX, FILE_EXTENSION)
    except OSError as e:
        logger.exception("Failed to save XML to disk: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to save file: {e}")

    # Trigger the existing pipeline using subprocess for isolation (PlantSim COM interactions prefer separate process)
    try:
        # Use sys.executable to launch the same Python interpreter
        import subprocess
        import sys

        cmd = [
            sys.executable,
            str(Path(__file__).parent.parent / "main.py"),
            "--from-file",
            str(saved_path),
        ]
        # Pass environment variables to subprocess
        env = os.environ.copy()
        # Only set DRY_RUN if explicitly requested via environment variable
        # Remove the hardcoded DRY_RUN for production use

        # Redirect stdout/stderr to a timestamped log file for debugging
        log_dir = Path("logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(pytz.timezone("CET")).strftime("%d%m%Y%H%M%S")
        log_file = log_dir / f"subprocess_{ts}.log"

        with log_file.open("w", encoding="utf-8") as lf:
            # Use DETACHED_PROCESS on Windows if available to avoid keeping a console open
            creationflags = 0
            try:
                # Only available on Windows
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
            except Exception:
                creationflags = 0

            subprocess.Popen(
                cmd, env=env, stdout=lf, stderr=lf, creationflags=creationflags
            )
        logger.info(
            "Launched subprocess to process file: %s; log: %s", saved_path, log_file
        )
    except Exception as e:
        logger.exception("Failed to launch processing subprocess: %s", e)
        # We still return success for write, but warn the client
        return JSONResponse(
            status_code=200,
            content={
                "status": "saved_but_processing_failed",
                "path": str(saved_path),
                "error": str(e),
            },
        )

    return JSONResponse(
        status_code=200, content={"status": "ok", "path": str(saved_path)}
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    port_value = _config_get("server.port", 5000)
    try:
        port = int(os.getenv("LOCAL_SERVER_PORT", str(port_value)))
    except Exception:
        port = 5000
    host = str(
        os.getenv("LOCAL_SERVER_HOST", str(_config_get("server.host", "0.0.0.0")))
    )
    uvicorn.run("src.local_http_server:app", host=host, port=port, log_level="info")
