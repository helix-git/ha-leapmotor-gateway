"""Accounts, vehicles and the app certificate. Kept in the app, never in Home Assistant.

Why here and not in the config flow of the integration: whatever is entered there ends up
in .storage/core.config_entries of Home Assistant, in every backup and readable by every
integration. Here it lives in the app config folder, readable only by the service user and
shielded by the AppArmor profile. Passwords and PINs never leave this module, not even to
the app's own interface, which only sees "set".

accounts.json:
  {"accounts": {"<id>": {"username", "password", "device_id"}},
   "vehicles": {"<VIN>": {"account", "name", "model", "enabled", "pin", "plate"}}}
The app certificate (app_cert.pem, app_key.pem) applies to all accounts. It is the
certificate of the Leapmotor app, not of a user. It is not shipped but uploaded in the app.
"""
import json
import logging
import os
import re
import secrets
import threading
from datetime import timezone
from typing import Optional

import plate as pl

VIN_PATTERN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
FILE = "accounts.json"


class Store:
    def __init__(self, folder: str):
        self.folder = folder
        self.path = os.path.join(folder, FILE)
        self._lock = threading.RLock()
        self.data = {"accounts": {}, "vehicles": {}}
        try:
            with open(self.path, encoding="utf-8") as fh:
                loaded = json.load(fh)
            self.data["accounts"].update(loaded.get("accounts") or {})
            self.data["vehicles"].update(loaded.get("vehicles") or {})
        except FileNotFoundError:
            pass

    def _save(self):
        os.makedirs(self.folder, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, ensure_ascii=False, indent=1)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    # Accounts
    def add_account(self, username: str, password: str, device_id: Optional[str] = None) -> str:
        username, password = (username or "").strip(), password or ""
        if "@" not in username or not password:
            raise ValueError("e-mail and password required")
        with self._lock:
            if any(a["username"].lower() == username.lower() for a in self.data["accounts"].values()):
                raise ValueError("this account already exists")
            aid = secrets.token_hex(4)
            self.data["accounts"][aid] = {"username": username, "password": password,
                                          "device_id": device_id or secrets.token_hex(16)}
            self._save()
            return aid

    def set_password(self, aid: str, password: str):
        if not password:
            raise ValueError("password is empty")
        with self._lock:
            self._account(aid)["password"] = password
            self._save()

    def remove_account(self, aid: str) -> list:
        with self._lock:
            self._account(aid)
            del self.data["accounts"][aid]
            gone = [v for v, f in self.data["vehicles"].items() if f.get("account") == aid]
            for vin in gone:
                del self.data["vehicles"][vin]
            self._save()
            return gone

    def _account(self, aid: str) -> dict:
        if aid not in self.data["accounts"]:
            raise KeyError("unknown account")
        return self.data["accounts"][aid]

    def account(self, aid: str) -> dict:
        with self._lock:
            return dict(self._account(aid))

    def account_ids(self) -> list:
        with self._lock:
            return list(self.data["accounts"])

    # Vehicles
    def report_vehicle(self, vin: str, aid: str, name: str = "", model: str = "") -> bool:
        """From the vehicle list of an account. Returns True if newly added. A new vehicle is
        enabled (read) but has no PIN."""
        if not VIN_PATTERN.match(vin or ""):
            raise ValueError(f"invalid VIN: {vin}")
        with self._lock:
            v = self.data["vehicles"].get(vin)
            new = v is None
            if new:
                v = self.data["vehicles"][vin] = {"account": aid, "name": name or model or vin[-6:], "model": model,
                                                  "enabled": True, "pin": ""}
            else:
                v["account"] = aid
                v["model"] = model or v.get("model", "")
            self._save()
            return new

    def update_vehicle(self, vin: str, *, name: Optional[str] = None, enabled: Optional[bool] = None,
                       pin: Optional[str] = None, plate: Optional[str] = None) -> dict:
        """pin=None keeps it, pin="" deletes it, otherwise sets it (4 to 8 digits).
        Same for the plate: None keeps it, "" deletes it, otherwise checked and normalized."""
        with self._lock:
            v = self.data["vehicles"].get(vin)
            if v is None:
                raise KeyError("unknown vehicle")
            if name is not None:
                name = name.strip()
                if not name or len(name) > 40:
                    raise ValueError("name: 1 to 40 characters")
                v["name"] = name
            if enabled is not None:
                v["enabled"] = bool(enabled)
            if pin is not None:
                if pin and not re.fullmatch(r"\d{4,8}", pin):
                    raise ValueError("PIN: 4 to 8 digits")
                v["pin"] = pin
            if plate is not None:
                v["plate"] = pl.normalize(plate)
            self._save()
            return self._public_vehicle(vin, v)

    def vehicle(self, vin: str) -> Optional[dict]:
        with self._lock:
            v = self.data["vehicles"].get(vin)
            return dict(v) if v else None

    def vehicles(self) -> dict:
        with self._lock:
            return {vin: dict(v) for vin, v in self.data["vehicles"].items()}

    # For the interface and the log: without secrets
    @staticmethod
    def _public_vehicle(vin: str, v: dict) -> dict:
        return {"vin": vin, "account": v.get("account"), "name": v.get("name"), "model": v.get("model"),
                "enabled": v.get("enabled", True), "pin_set": bool(v.get("pin")), "plate": v.get("plate", "")}

    def public(self) -> dict:
        with self._lock:
            return {"accounts": [{"id": a, "username": d["username"], "password_set": bool(d.get("password"))}
                                 for a, d in self.data["accounts"].items()],
                    "vehicles": [self._public_vehicle(vin, v) for vin, v in self.data["vehicles"].items()]}


# App certificate
def certificate_paths(folder: str) -> tuple[str, str]:
    return os.path.join(folder, "app_cert.pem"), os.path.join(folder, "app_key.pem")


def certificate_info(folder: str) -> dict:
    cert_path, key_path = certificate_paths(folder)
    if not (os.path.exists(cert_path) and os.path.exists(key_path)):
        return {"present": False}
    try:
        from cryptography import x509
        with open(cert_path, "rb") as fh:
            c = x509.load_pem_x509_certificate(fh.read())
        return {"present": True, "subject": c.subject.rfc4514_string()[:120],
                "valid_until": c.not_valid_after_utc.astimezone(timezone.utc).isoformat()}
    except Exception as error:
        logging.getLogger("leapmotor_gateway").warning("Certificate cannot be read: %s", error)
        return {"present": True, "error": "certificate cannot be read, see the app log"}


def save_certificate(folder: str, cert_pem: bytes, key_pem: bytes) -> dict:
    """Check first, then write: valid PEM, and the key belongs to the certificate. A wrong pair
    overwrites nothing."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    try:
        c = x509.load_pem_x509_certificate(cert_pem)
    except Exception:
        raise ValueError("certificate is not valid PEM")
    try:
        k = serialization.load_pem_private_key(key_pem, password=None)
    except Exception:
        raise ValueError("key is not a valid unencrypted PEM")
    spki = serialization.PublicFormat.SubjectPublicKeyInfo
    if c.public_key().public_bytes(serialization.Encoding.PEM, spki) != k.public_key().public_bytes(serialization.Encoding.PEM, spki):
        raise ValueError("key does not match the certificate")
    os.makedirs(folder, exist_ok=True)
    cert_path, key_path = certificate_paths(folder)
    for path, content, mode in ((cert_path, cert_pem, 0o644), (key_path, key_pem, 0o600)):
        tmp = path + ".tmp"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode), "wb") as fh:
            fh.write(content)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    return certificate_info(folder)
