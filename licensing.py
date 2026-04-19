"""Lemon Squeezy licensing helper for Quick Camera Profile.

No-trial policy:
- If no valid activation exists, protected actions are blocked.
- A successful activation stores license key + instance ID locally.
- Online validation is attempted on startup; short offline grace is
  allowed only for previously validated activations.
"""

from __future__ import annotations

import json
import os
import platform
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


LEMON_API_BASE = "https://api.lemonsqueezy.com/v1/licenses"
DEFAULT_OFFLINE_GRACE_DAYS = 14


@dataclass
class LicenseStatus:
    licensed: bool
    message: str
    product_name: str = ""
    customer_email: str = ""


class LemonLicenseManager:
    def __init__(self, app_name: str = "Quick Camera Profile"):
        self.app_name = app_name
        self.product_id = self._read_int_env("QCP_LEMON_PRODUCT_ID")
        self.offline_grace_days = self._read_int_env(
            "QCP_LICENSE_OFFLINE_GRACE_DAYS", DEFAULT_OFFLINE_GRACE_DAYS
        )
        self.data_path = self._license_path()
        self.data = self._load_data()

    @staticmethod
    def _read_int_env(name: str, default: int | None = None) -> int | None:
        raw = os.environ.get(name, "").strip()
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    def _license_path(self) -> Path:
        if os.name == "nt":
            base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
            return base / "TactileBridge" / "QuickCameraProfile" / "license.json"
        return Path.home() / ".quick-camera-profile" / "license.json"

    def _load_data(self) -> dict:
        try:
            if self.data_path.is_file():
                return json.loads(self.data_path.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _save_data(self):
        self.data_path.parent.mkdir(parents=True, exist_ok=True)
        self.data_path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    def _instance_name(self) -> str:
        host = socket.gethostname().strip() or "host"
        user = os.environ.get("USERNAME") or os.environ.get("USER") or "user"
        sys_name = platform.system()
        return f"{self.app_name} | {user}@{host} | {sys_name}"

    def _post_form(self, endpoint: str, fields: dict) -> dict:
        body = urllib.parse.urlencode(fields).encode("utf-8")
        req = urllib.request.Request(
            f"{LEMON_API_BASE}/{endpoint}",
            data=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = resp.read().decode("utf-8")
            return json.loads(payload)

    def _last_validated_at(self) -> datetime | None:
        raw = self.data.get("last_validated_at")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None

    def _offline_grace_ok(self) -> bool:
        ts = self._last_validated_at()
        if ts is None:
            return False
        now = datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return now - ts <= timedelta(days=self.offline_grace_days)

    def _product_check(self, meta: dict) -> tuple[bool, str]:
        if not self.product_id:
            return True, ""
        got = meta.get("product_id")
        if got == self.product_id:
            return True, ""
        return False, (
            f"This key is for product ID {got}, expected {self.product_id}."
        )

    def activate(self, license_key: str) -> LicenseStatus:
        key = license_key.strip()
        if not key:
            return LicenseStatus(False, "Enter a license key.")
        try:
            result = self._post_form(
                "activate",
                {
                    "license_key": key,
                    "instance_name": self._instance_name(),
                },
            )
        except urllib.error.HTTPError as e:
            return LicenseStatus(False, f"Activation failed ({e.code}).")
        except Exception as e:
            return LicenseStatus(False, f"Activation failed: {e}")

        if not result.get("activated"):
            return LicenseStatus(False, result.get("error") or "Activation failed.")

        meta = result.get("meta") or {}
        ok, msg = self._product_check(meta)
        if not ok:
            return LicenseStatus(False, msg)

        instance = result.get("instance") or {}
        now = datetime.now(timezone.utc).isoformat()
        self.data = {
            "license_key": key,
            "instance_id": instance.get("id", ""),
            "instance_name": instance.get("name", ""),
            "last_validated_at": now,
            "product_name": meta.get("product_name", ""),
            "customer_email": meta.get("customer_email", ""),
        }
        self._save_data()
        return LicenseStatus(
            True,
            "License activated.",
            product_name=self.data.get("product_name", ""),
            customer_email=self.data.get("customer_email", ""),
        )

    def validate(self) -> LicenseStatus:
        key = (self.data.get("license_key") or "").strip()
        if not key:
            return LicenseStatus(False, "No license activated on this device.")

        fields = {"license_key": key}
        instance_id = (self.data.get("instance_id") or "").strip()
        if instance_id:
            fields["instance_id"] = instance_id

        try:
            result = self._post_form("validate", fields)
        except Exception:
            if self._offline_grace_ok():
                return LicenseStatus(True, "Offline grace period active.")
            return LicenseStatus(False, "License validation failed (offline).")

        if not result.get("valid"):
            return LicenseStatus(False, result.get("error") or "License is not valid.")

        meta = result.get("meta") or {}
        ok, msg = self._product_check(meta)
        if not ok:
            return LicenseStatus(False, msg)

        now = datetime.now(timezone.utc).isoformat()
        self.data["last_validated_at"] = now
        if meta.get("product_name"):
            self.data["product_name"] = meta.get("product_name")
        if meta.get("customer_email"):
            self.data["customer_email"] = meta.get("customer_email")
        self._save_data()

        return LicenseStatus(
            True,
            "License valid.",
            product_name=self.data.get("product_name", ""),
            customer_email=self.data.get("customer_email", ""),
        )

    def deactivate(self) -> LicenseStatus:
        key = (self.data.get("license_key") or "").strip()
        instance_id = (self.data.get("instance_id") or "").strip()
        if not key or not instance_id:
            self.data = {}
            if self.data_path.exists():
                self.data_path.unlink(missing_ok=True)
            return LicenseStatus(False, "No active license instance found.")

        try:
            result = self._post_form(
                "deactivate",
                {
                    "license_key": key,
                    "instance_id": instance_id,
                },
            )
        except Exception as e:
            return LicenseStatus(False, f"Deactivate failed: {e}")

        if not result.get("deactivated"):
            return LicenseStatus(False, result.get("error") or "Deactivate failed.")

        self.data = {}
        if self.data_path.exists():
            self.data_path.unlink(missing_ok=True)
        return LicenseStatus(True, "License deactivated on this device.")

    def current_key(self) -> str:
        return (self.data.get("license_key") or "").strip()
