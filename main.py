# main.py
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from dotenv import load_dotenv
import httpx
import os
import json
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
import logging
import re
from urllib.parse import urljoin
import mimetypes

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
app = FastAPI()

# --------------------------------------------------------------------------- #
#  Environment
# --------------------------------------------------------------------------- #
load_dotenv()

DISCORD_WEBHOOK_URL = os.getenv(
    "DISCORD_WEBHOOK_URL",
    "https://discord.com/api/webhooks/your_webhook_id/your_webhook_token",
)
PLANE_BASE_URL = os.getenv("PLANE_BASE_URL", "").rstrip("/")
PLANE_API_TOKEN = os.getenv("PLANE_API_TOKEN")

# --------------------------------------------------------------------------- #
#  Models – accept anything Plane sends
# --------------------------------------------------------------------------- #
class Actor(BaseModel):
    id: str
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None


class Activity(BaseModel):
    field: Optional[str] = None
    new_value: Any = None
    old_value: Any = None
    actor: Optional[Actor] = None


class PlaneWebhook(BaseModel):
    event: str
    action: str = "unknown"
    data: Dict[str, Any] = Field(default_factory=dict)
    activity: Optional[Activity] = None


# --------------------------------------------------------------------------- #
#  Utility – archive every payload locally (comment‑out to disable)
# --------------------------------------------------------------------------- #
def save_plane_request(payload: Dict[str, Any]) -> None:
    folder = "plane_requests"
    os.makedirs(folder, exist_ok=True)
    filename = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json"
    path = os.path.join(folder, filename)
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- #
#  Discord helpers
# --------------------------------------------------------------------------- #
ACTION_COLOR = {
    "create": 0x2ECC71,   # green
    "created": 0x2ECC71,
    "update": 0xF1C40F,   # yellow
    "updated": 0xF1C40F,
    "delete": 0xE74C3C,   # red
    "deleted": 0xE74C3C,
}

EVENT_ICON = {
    "issue": "🐛",
    "project": "📁",
    "cycle": "🗓️",
    "comment": "💬",
}

_URL_RE = re.compile(r"^(https?://|attachment://)", re.I)
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)


def _is_valid_url(url: str | None) -> bool:
    return bool(url and isinstance(url, str) and _URL_RE.match(url))


def _sanitize_value(value: Any) -> str:
    """
    Remove technical clutter (UUIDs, empty values) and return a human‑friendly string.
    """
    if value in (None, "", [], {}):
        return "—"

    if isinstance(value, list):
        pretty = [_sanitize_value(v) for v in value]
        return ", ".join(p for p in pretty if p != "—") or "—"

    if isinstance(value, dict):
        # try common name keys
        for k in ("display_name", "name", "title"):
            if k in value and value[k]:
                return str(value[k])
        # fall back to str(dict) – should be fine, callers rarely pass dicts
        return str(value)

    if isinstance(value, str) and _UUID_RE.match(value):
        return "—"

    return str(value)


def _make_field(name: str, value: Any, inline: bool = True) -> Dict[str, Any]:
    return {"name": name, "value": _sanitize_value(value), "inline": inline}


def _arrow_change(old: Any, new: Any) -> str:
    """
    Format a change as “old ➜ new”, omitting UUID noise.
    """
    return f"{_sanitize_value(old)} ➜ {_sanitize_value(new)}"


def build_discord_embed(
    p: PlaneWebhook, author_icon_url: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Create a Discord embed that is focused on *human readable* information.

    • Never show raw IDs or UUIDs
    • Represent changes with arrows, e.g.  “Backlog ➜ Todo”
    """
    action = p.action.lower()
    color = ACTION_COLOR.get(action, 0x3498DB)  # default blue
    icon = EVENT_ICON.get(p.event, "ℹ️")

    actor_name = (
        p.activity.actor.display_name if p.activity and p.activity.actor else "Unknown"
    )

    # ---------- common fields ----------
    fields: List[Dict[str, Any]] = [
        _make_field("Event", p.event.capitalize()),
        _make_field("Action", p.action.capitalize()),
        _make_field("By", actor_name),
    ]

    # ---------- event specifics ----------
    if p.event == "issue":
        # Current state name (never the ID)
        fields.append(_make_field("State", p.data.get("state", {}).get("name")))
        assignees = [a.get("display_name") or a.get("name") for a in p.data.get("assignees", [])]
        fields.append(_make_field("Assignees", assignees))
        title = p.data.get("name") or "<untitled>"
    elif p.event == "project":
        title = p.data.get("name") or "<untitled>"
    else:
        title = p.event.capitalize()

    # ---------- show what changed ----------
    if p.activity and p.activity.field:
        # if p.activity.field ends with "_id", skip and return None
        if p.activity.field.endswith("_id"):
            return None

        field_name = p.activity.field.replace("_id", "").replace("_", " ").capitalize()

        # Special handling for state changes: we can map new ID → current state name
        if p.activity.field == "state_id":
            old_val = p.activity.old_value
            new_val = p.data.get("state", {}).get("name") or p.activity.new_value
        else:
            old_val = p.activity.old_value
            new_val = p.activity.new_value

        fields.append(
            _make_field(field_name, _arrow_change(old_val, new_val), inline=False)
        )

    embed: Dict[str, Any] = {
        "title": f"{icon}  {title}",
        "color": color,
        "fields": fields,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # ---------- optional visuals ----------
    thumb = p.data.get("cover_image_url") or p.data.get("cover_image")
    if _is_valid_url(thumb):
        embed["thumbnail"] = {"url": thumb}

    if author_icon_url:
        embed["author"] = {"name": actor_name, "icon_url": author_icon_url}
    else:
        embed["author"] = {"name": actor_name}

    return embed


# --------------------------------------------------------------------------- #
#  Avatar‑helpers
# --------------------------------------------------------------------------- #
async def _download_avatar(avatar_path: str) -> Optional[Tuple[bytes, str, str]]:
    """
    Return (bytes, filename, mime) or None on failure.

    • If avatar_path is already an absolute URL → use it.
    • Otherwise prepend PLANE_BASE_URL.
    • If PLANE_API_TOKEN is set → send Bearer token (only on original URL).
    """
    if not avatar_path:
        return None

    if _is_valid_url(avatar_path):
        url = avatar_path
    else:
        if not PLANE_BASE_URL:
            return None
        url = urljoin(PLANE_BASE_URL + "/", avatar_path.lstrip("/"))

    headers = {}
    if PLANE_API_TOKEN:
        headers["Authorization"] = f"Bearer {PLANE_API_TOKEN}"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # First request without following redirects automatically.
            r = await client.get(url, headers=headers, follow_redirects=False)
            # If a redirect is issued (e.g. 302), try to follow it manually.
            if r.status_code in (301, 302, 303, 307, 308):
                redirect_url = r.headers.get("Location")
                if redirect_url:
                    # Pre‑signed URLs (with query params like X‑Amz‑*) may break with Authorization.
                    # Remove the Authorization header on the redirect request.
                    new_headers = headers.copy()
                    new_headers.pop("Authorization", None)
                    r = await client.get(
                        redirect_url, headers=new_headers, follow_redirects=False
                    )
            r.raise_for_status()
            content_type = r.headers.get("Content-Type", "").lower()
            # Guess extension from Content‑Type header; default to png.
            ext = mimetypes.guess_extension(content_type.split(";")[0]) or ".png"
            mime = content_type.split(";")[0] or "image/png"
            filename = f"avatar{ext}"
            return r.content, filename, mime
    except Exception as exc:
        logging.warning("Could not fetch avatar from %s: %s", url, exc)
        return None


# --------------------------------------------------------------------------- #
#  Endpoint
# --------------------------------------------------------------------------- #
@app.post("/plane-webhook", response_model=dict[str, str])
async def handle_plane_webhook(payload: PlaneWebhook, request: Request) -> dict[str, str]:
    # Archive for debugging
    raw_json = await request.json()
    # save_plane_request(raw_json)

    avatar_job: Optional[Tuple[bytes, str, str]] = None
    if payload.activity and payload.activity.actor and payload.activity.actor.avatar_url:
        avatar_job = await _download_avatar(payload.activity.actor.avatar_url)

    # Build embed (use attachment:// if we have an avatar to upload)
    author_icon = f"attachment://{avatar_job[1]}" if avatar_job else None
    embed = build_discord_embed(payload, author_icon_url=author_icon)

    if not embed:
        logging.info("No relevant changes to report for event: %s", payload.event)
        return {"status": "No relevant changes to report to discord"}

    discord_payload = {
        "embeds": [embed],
        "allowed_mentions": {"parse": []},  # avoid accidental pings
    }

    async with httpx.AsyncClient(timeout=20) as client:
        if avatar_job:
            avatar_bytes, filename, mime = avatar_job
            files = {
                "payload_json": (
                    None,
                    json.dumps(discord_payload),
                    "application/json",
                ),
                "files[0]": (filename, avatar_bytes, mime),
            }
            resp = await client.post(DISCORD_WEBHOOK_URL, files=files)
        else:
            resp = await client.post(DISCORD_WEBHOOK_URL, json=discord_payload)

    # Discord returns 204 on success but some cases it may return 200,
    # so we consider both as success
    if resp.status_code not in (200, 204):
        try:
            err_info = resp.json()
        except Exception:
            err_info = resp.text
        logging.error("Discord webhook failed (%s): %s", resp.status_code, err_info)
        raise HTTPException(
            status_code=500,
            detail=f"Discord webhook failed ({resp.status_code}): {err_info}",
        )

    return {"status": "Message forwarded to Discord successfully"}


# --------------------------------------------------------------------------- #
#  Local dev entry-point
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host=host, port=port)
