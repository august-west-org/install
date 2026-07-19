"""Reads the KEY='value' shell-sourceable secrets files the install script writes."""
import re

_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)='(.*)'$")


def load_env_file(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                m = _LINE_RE.match(line)
                if m:
                    values[m.group(1)] = m.group(2)
    except FileNotFoundError:
        pass
    return values


SECRETS = load_env_file("/etc/augustwest/secrets.env")
NEXTCLOUD_ADMIN_PASSWORD = SECRETS.get("NEXTCLOUD_ADMIN_PASSWORD", "")
