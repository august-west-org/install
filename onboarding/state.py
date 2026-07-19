"""Wizard state persistence. Single-tenant (one customer per device), so this
is just one JSON file with an atomic write -- no database needed."""
import json
import os
import tempfile
import threading

STATE_PATH = "/opt/augustwest/onboarding/state.json"

_lock = threading.Lock()

DEFAULT_STATE = {
    "completed": False,
    "primary": None,  # {"name", "email", "username"} -- never the password
    "steps": {
        "health": {"status": "pending", "result": None},
        "account": {"status": "pending", "result": None},
        "qr": {"status": "pending", "result": None},
        "icloud": {
            "status": "pending",
            "items": {
                "photos": False,
                "contacts_calendar": False,
                "files_documents": False,
                "passwords": False,
            },
        },
        "family": {"status": "pending", "members": []},
        "completion": {"status": "pending"},
    },
    "quick_access": {},
}


def load() -> dict:
    with _lock:
        if not os.path.exists(STATE_PATH):
            return json.loads(json.dumps(DEFAULT_STATE))
        with open(STATE_PATH) as f:
            return json.load(f)


def save(state: dict) -> None:
    with _lock:
        dir_ = os.path.dirname(STATE_PATH)
        fd, tmp_path = tempfile.mkstemp(dir=dir_, prefix=".state-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(state, f, indent=2)
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, STATE_PATH)
        except BaseException:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise
