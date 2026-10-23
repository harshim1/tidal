"""Runtime configuration, all from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass
class Config:
    # Ring Partner API (api.amazonvision.com) — or the local ring-sandbox emulator.
    ring_token: str = field(default_factory=lambda: _env("RING_ACCESS_TOKEN", "sandbox-token"))
    ring_base_url: str = field(default_factory=lambda: _env("RING_BASE_URL", "https://api.amazonvision.com"))
    # No default: an unset key must reject every webhook, never verify against a known value.
    ring_hmac_key: str = field(default_factory=lambda: _env("RING_HMAC_KEY"))
    media_origins: list[str] = field(
        default_factory=lambda: [o for o in _env("RING_MEDIA_ORIGINS", "*.amazonaws.com").split(",") if o]
    )
    # Which cameras to watch (comma-separated device ids). Empty = every camera.
    watch_devices: list[str] = field(default_factory=lambda: [d for d in _env("TIDAL_DEVICES").split(",") if d])
    clip_ms: int = field(default_factory=lambda: int(_env("TIDAL_CLIP_MS", "60000")))
    settle_s: float = field(default_factory=lambda: float(_env("TIDAL_SETTLE_S", "0")))
    # Scheduled live check-ins (WHEP) — minutes between check-ins; 0 disables.
    checkin_minutes: int = field(default_factory=lambda: int(_env("TIDAL_CHECKIN_MINUTES", "0")))
    checkin_seconds: int = field(default_factory=lambda: int(_env("TIDAL_CHECKIN_SECONDS", "60")))
    night_start_hour: int = field(default_factory=lambda: int(_env("TIDAL_NIGHT_START", "21")))
    night_end_hour: int = field(default_factory=lambda: int(_env("TIDAL_NIGHT_END", "8")))

    person_name: str = field(default_factory=lambda: _env("TIDAL_PERSON_NAME", "Dad"))
    render_video: bool = field(default_factory=lambda: _env("TIDAL_RENDER", "1") == "1")
    db_path: Path = field(default_factory=lambda: Path(_env("TIDAL_DB", str(ROOT / "data" / "tidal.db"))))
    media_dir: Path = field(default_factory=lambda: Path(_env("TIDAL_MEDIA_DIR", str(ROOT / "media"))))
    api_url: str = field(default_factory=lambda: _env("TIDAL_API_URL", "http://127.0.0.1:8000"))

    # Amazon Bedrock (AWS Builder). Off unless TIDAL_BEDROCK=1 and AWS creds exist.
    use_bedrock: bool = field(default_factory=lambda: _env("TIDAL_BEDROCK", "0") == "1")
    bedrock_model_id: str = field(
        default_factory=lambda: _env("BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
    )
    aws_region: str = field(default_factory=lambda: _env("AWS_REGION", "us-east-1"))

    # Public hosting. TIDAL_PUBLIC=1 (the Docker default) refuses to start without a
    # dashboard password and an MCP token — an open URL would expose someone's bedroom.
    public: bool = field(default_factory=lambda: _env("TIDAL_PUBLIC", "0") == "1")
    password: str = field(default_factory=lambda: _env("TIDAL_PASSWORD"))
    mcp_token: str = field(default_factory=lambda: _env("TIDAL_MCP_TOKEN"))
    # Demo mode: serve the bundled synthetic night (re-dated to "last night"), no camera calls.
    demo: bool = field(default_factory=lambda: _env("TIDAL_DEMO", "0") == "1")
    uploads: bool = field(default_factory=lambda: _env("TIDAL_UPLOADS", "1") == "1")
    upload_max_s: float = field(default_factory=lambda: float(_env("TIDAL_UPLOAD_MAX_S", "180")))

    def check_public(self) -> None:
        missing = [n for n, v in (("TIDAL_PASSWORD", self.password), ("TIDAL_MCP_TOKEN", self.mcp_token)) if not v]
        if self.public and missing:
            raise RuntimeError(f"TIDAL_PUBLIC=1 needs {' and '.join(missing)} set; refusing to start unprotected")


cfg = Config()
cfg.media_dir.mkdir(parents=True, exist_ok=True)
cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
