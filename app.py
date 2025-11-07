from flask import Flask, render_template, request, redirect, url_for, session, flash
from datetime import datetime, timedelta, time as dtime
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # For Python < 3.9
import yaml
import os
import threading

# --- Config ---
APP_TZ = ZoneInfo(os.environ.get("APP_TZ", "Europe/Copenhagen"))
BASE_DIR = os.path.dirname(__file__)
USERS_FILE = os.path.join(BASE_DIR, "users.yaml")
RESOURCES_FILE = os.path.join(BASE_DIR, "resources.yaml")
STATE_FILE = os.getenv("STATE_FILE", os.path.join(BASE_DIR, "state.yaml"))

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-me-please")
_state_lock = threading.Lock()

# --- Load static config ---
with open(USERS_FILE, "r", encoding="utf-8") as f:
    USERS = yaml.safe_load(f).get("users", [])

with open(RESOURCES_FILE, "r", encoding="utf-8") as f:
    RESOURCES = yaml.safe_load(f).get("resources", [])

# --- State helpers ---
def _read_state():
    if not os.path.exists(STATE_FILE):
        return {"reservations": {}}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
        data.setdefault("reservations", {})
        return data


def _write_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(state, f, sort_keys=True)
    os.replace(tmp, STATE_FILE)


def _iso_now():
    return datetime.now(APP_TZ)

from typing import Optional
def _parse_hhmm(hhmm: str) -> Optional[datetime]:
    try:
        hh, mm = [int(x) for x in hhmm.split(":", 1)]
        today = _iso_now().date()
        dt = datetime.combine(today, dtime(hh, mm, tzinfo=APP_TZ))
        # If time already passed today, interpret as tomorrow at that time
        if dt <= _iso_now():
            dt = dt + timedelta(days=1)
        return dt
    except Exception:
        return None


def _midnight_default() -> datetime:
    now = _iso_now()
    # Next midnight
    midnight_tomorrow = datetime.combine(now.date() + timedelta(days=1), dtime(0, 0, tzinfo=APP_TZ))
    return midnight_tomorrow


def _cleanup_expired(state):
    now = _iso_now()
    changed = False
    for rid, info in list(state.get("reservations", {}).items()):
        exp = info.get("expires_at")
        if exp:
            try:
                exp_dt = datetime.fromisoformat(exp)
            except Exception:
                exp_dt = None
        else:
            exp_dt = None
        if exp_dt and exp_dt <= now:
            state["reservations"].pop(rid, None)
            changed = True
    return changed


# --- Auth helpers ---
def current_user():
    return session.get("user")


@app.before_request
def require_login_and_cleanup():
    # Allow login page and static files without auth
    if request.endpoint in {"login", "static"}:
        return
    if not current_user():
        return redirect(url_for("login"))
    # Expiry cleanup before each request
    with _state_lock:
        state = _read_state()
        if _cleanup_expired(state):
            _write_state(state)


# --- Routes ---
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        name = request.form.get("name")
        if name in USERS:
            session["user"] = name
            return redirect(url_for("home"))
        flash("Please select a valid user.")
    return render_template("login.html", users=USERS, error=None)


@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect(url_for("login"))


@app.route("/")
def home():
    with _state_lock:
        state = _read_state()
        reservations = state.get("reservations", {})
    # Prepare view model: merge resources + reservation info
    view_resources = []
    for r in RESOURCES:
        rid = r.get("id")
        resv = reservations.get(rid)
        view_resources.append({
            "id": rid,
            "name": r.get("name", rid),
            "meta": r.get("meta", {}),
            "reserved_by": resv.get("user") if resv else None,
            "reserved_at": resv.get("reserved_at") if resv else None,
            "expires_at": resv.get("expires_at") if resv else None,
        })
    now = _iso_now().strftime("%Y-%m-%d %H:%M:%S %Z")
    return render_template("index.html", time=now, user=current_user(), resources=view_resources)


@app.post("/reserve")
def reserve():
    rid = request.form.get("resource_id")
    expires_hhmm = request.form.get("expires_hhmm")
    if not rid:
        return ("Missing resource_id", 400)

    with _state_lock:
        state = _read_state()
        reservations = state.setdefault("reservations", {})
        if rid in reservations:
            flash("Resource is already reserved.")
            return redirect(url_for("home"))
        # Determine expiration
        if expires_hhmm:
            exp_dt = _parse_hhmm(expires_hhmm)
            if not exp_dt:
                flash("Invalid time format. Use HH:MM.")
                return redirect(url_for("home"))
        else:
            exp_dt = _midnight_default()
        now = _iso_now()
        reservations[rid] = {
            "user": current_user(),
            "reserved_at": now.isoformat(),
            "expires_at": exp_dt.isoformat(),
        }
        _write_state(state)

    return redirect(url_for("home"))


@app.post("/release")
def release():
    rid = request.form.get("resource_id")
    if not rid:
        return ("Missing resource_id", 400)
    with _state_lock:
        state = _read_state()
        resv = state.get("reservations", {}).get(rid)
        if resv:
            # Simple rule: only owner can release
            if resv.get("user") != current_user():
                flash("Only the reserver can release this resource.")
                return redirect(url_for("home"))
            state["reservations"].pop(rid, None)
            _write_state(state)
    return redirect(url_for("home"))


# --- JSON API (optional/simple) ---
@app.get("/resources")
def get_resources():
    with _state_lock:
        state = _read_state()
        reservations = state.get("reservations", {})
    enriched = []
    for r in RESOURCES:
        rid = r.get("id")
        data = {
            "id": rid,
            "name": r.get("name", rid),
            "meta": r.get("meta", {}),
        }
        data.update(reservations.get(rid, {}))
        enriched.append(data)
    return {"resources": enriched}


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
