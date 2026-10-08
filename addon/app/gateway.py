"""Leapmotor Gateway: this app is the only component that talks to the Leapmotor cloud.

Accounts, vehicles, PINs and the app certificate live here (accounts.py), never in Home
Assistant. The integration "leapmotor_gateway" reads states through /api/v2 and sends
commands together with the Home Assistant user who triggered them. Every command passes
the allowlist (commands.py), the role of the user and, where required, an approval by
push notification (only on an unlocked phone). What is not on the list does not exist.

Several accounts and vehicles: one cloud connection per account, one state and polling
rhythm per vehicle. The PIN is set only right before a command for exactly that vehicle.

Two safeguards stay in the app options:
  cloud_enabled = false  no login to Leapmotor, no polling
  dry_run = true         commands are checked and logged, not sent
"""
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Callable, Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests
from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

import accounts as ac
import commands as cmd
import migration
import plate as pl
import state as st
import trip_computer as tc
import vehicle_image as vi

log = logging.getLogger("leapmotor_gateway")
logging.basicConfig(level=os.environ.get("LG_LOG_LEVEL", "info").upper(),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name, "")
    return value if value not in ("", "null") else default


def _bool(name: str, default: bool) -> bool:
    return _env(name, str(default)).lower() in ("1", "true", "yes", "on")


def _find_key(obj) -> Optional[str]:
    """Image key from the get_car_picture response (layout differs per model): the first
    field whose name ends with 'key'."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and v and k.lower().endswith("key"):
                return v
        for v in obj.values():
            found = _find_key(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_key(v)
            if found:
                return found
    return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Two requests that leapmotor-api 0.3.2 does not offer (correctly). Modelled on
# kerniger/leapmotor-ha (api.py: get_consumption_today_breakdown and
# get_mileage_energy_detail with a time window). Read only.
def _today_breakdown(c, lib_vehicle, time_zone: str = "UTC"):
    """Energy shares today: the same endpoint as the weekly shares (getLastweekEC), with the
    window from midnight (Home Assistant time zone) until now."""
    from leapmotor_api.crypto import build_consumption_last_week_headers
    from leapmotor_api.models import ConsumptionLastWeekBreakdown
    now = datetime.now(ZoneInfo(time_zone))
    begin = str(int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()))
    end = str(int(now.timestamp()))
    headers = build_consumption_last_week_headers(sign_key=c.sign_key, device_id=c.device_id, carvin=lib_vehicle.vin,
                                                  begintime=begin, endtime=end, language=c.language).to_dict()
    headers.update(c._auth_headers())
    r = c._post(path="/carownerservice/oversea/drivingRecord/v1/getLastweekEC", headers=headers,
                data=f"endtime={end}&begintime={begin}&carvin={quote(lib_vehicle.vin, safe='')}", cert=c.account_cert)
    return ConsumptionLastWeekBreakdown.from_dict(c._parse_api_body(r["status_code"], r["body"], "today").get("data") or {})


def _distance_energy_7_days(c, lib_vehicle) -> dict:
    """mileage/energy/detail with a 7 day window in ms. Only with it the cloud returns
    totalEnergy. The fields are signed alphabetically, as build_signed_headers does with
    body_params."""
    from leapmotor_api.crypto import build_signed_headers
    end = int(time.time() * 1000)
    start = end - 7 * 24 * 3600 * 1000
    headers = build_signed_headers(sign_key=c.sign_key, device_id=c.device_id, vin=lib_vehicle.vin, language=c.language,
                                   body_params={"begintime": str(start), "endtime": str(end)}).to_dict()
    headers.update(c._auth_headers())
    r = c._post(path="/carownerservice/oversea/drivingRecord/v1/mileage/energy/detail", headers=headers,
                data=f"endtime={end}&begintime={start}&vin={quote(lib_vehicle.vin, safe='')}", cert=c.account_cert)
    return c._parse_api_body(r["status_code"], r["body"], "mileage energy detail")


class Settings:
    def __init__(self):
        # Demo mode talks to a simulated car only, so commands may run there without a dry run.
        self.demo_mode = _bool("LG_DEMO_MODE", False)
        self.cloud_enabled = self.demo_mode or _bool("LG_CLOUD_ENABLED", False)
        self.dry_run = False if self.demo_mode else _bool("LG_DRY_RUN", True)
        self.poll_active = max(30, int(_env("LG_POLL_ACTIVE_S", "60")))
        self.poll_idle = max(60, int(_env("LG_POLL_IDLE_MIN", "15")) * 60)
        # How long a stop may last for the trip to continue. The gateway keeps polling fast
        # for that long after the car was parked.
        self.trip_end_min = int(_env("LG_TRIP_END_MIN", "10"))
        # Level per command, only deviations from the default. Maintained in the app (settings.json).
        self.deviations: dict[str, str] = {}
        self.notify_targets = [x.strip() for x in _env("LG_NOTIFY").split(",") if x.strip()]
        self.approval_timeout_s = int(_env("LG_APPROVAL_TIMEOUT_S", "120"))
        # Without push, commands with level approval run without asking but still only for the
        # role full, for example without the companion app or on Android below 12, where
        # authenticationRequired has no effect.
        self.approval_push = _bool("LG_APPROVAL_PUSH", True)
        self.state_dir = _env("LG_STATE_DIR", ".")
        self.roles: dict[str, str] = {}                 # Home Assistant user id -> full / limited / read
        self.ha_url = _env("LG_HA_URL", "http://supervisor/core")
        self.ha_token = _env("SUPERVISOR_TOKEN")
        self.cooldown_s = 10
        self.max_per_hour = 30
        migration.run(self.state_dir)
        self.ui_path = os.path.join(self.state_dir, "settings.json")
        self.ui_load()

    # Settings from the app page override the app options. Deliberately not here:
    # credentials, PIN, cloud_enabled, dry_run. These safeguards stay in the app options.
    UI_FIELDS = ("notify_targets", "approval_timeout_s", "levels", "poll_active_s", "poll_idle_min",
                 "trip_end_min", "approval_push", "roles")
    ROLES = ("full", "limited", "read")

    def ui_load(self):
        """Fields that no longer exist and commands that are gone must not stop the start."""
        try:
            with open(self.ui_path, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return
        old = set(data) - set(self.UI_FIELDS)
        if old:
            log.info("Settings: obsolete fields skipped: %s", ", ".join(sorted(old)))
        data = {k: v for k, v in data.items() if k in self.UI_FIELDS}
        if isinstance(data.get("levels"), dict):
            data["levels"] = {n: lv for n, lv in data["levels"].items() if n in cmd.COMMANDS}
        self.ui_apply(data, save=False)

    def _stored(self) -> dict:
        """What settings.json holds: levels only where they differ from the default. A changed
        default level in a new version then also reaches installations that never changed anything."""
        values = self.ui_values()
        values["levels"] = {n: lv for n, lv in self.deviations.items() if lv != cmd.COMMANDS[n].level}
        return values

    def ui_values(self) -> dict:
        return {"notify_targets": self.notify_targets, "approval_timeout_s": self.approval_timeout_s,
                "levels": {n: cmd.level(n, self.deviations) for n in cmd.COMMANDS},
                "poll_active_s": self.poll_active, "poll_idle_min": self.poll_idle // 60,
                "trip_end_min": self.trip_end_min, "approval_push": self.approval_push, "roles": dict(self.roles)}

    def role(self, user_id: Optional[str]) -> str:
        """Without a user (automation, script without a trigger) and for unknown users: limited.
        'full' has to be granted explicitly."""
        return self.roles.get(user_id or "", "limited")

    def ui_apply(self, new: dict, save: bool = True, allowed_targets: Optional[set] = None) -> dict:
        """Checks everything before anything is taken over. There are no half changes."""
        unknown = set(new) - set(self.UI_FIELDS)
        if unknown:
            raise ValueError("unknown setting: " + ", ".join(sorted(unknown)))
        targets = [str(t) for t in new.get("notify_targets", self.notify_targets)]
        push = new.get("approval_push", self.approval_push)
        if not isinstance(push, bool):
            raise ValueError("approval by push: on or off")
        if push and not targets:
            raise ValueError("at least one device is needed for approvals (or switch approval by push off)")
        for t in targets:
            if not t.startswith("notify.") or (allowed_targets is not None and t not in allowed_targets):
                raise ValueError(f"unknown push target: {t}")
        timeout = int(new.get("approval_timeout_s", self.approval_timeout_s))
        if not 30 <= timeout <= 900:
            raise ValueError("approval validity 30 to 900 s")
        active = int(new.get("poll_active_s", self.poll_active))
        idle = int(new.get("poll_idle_min", self.poll_idle // 60))
        if not 30 <= active <= 3600 or not 1 <= idle <= 240:
            raise ValueError("polling: active 30 to 3600 s, idle 1 to 240 min")
        trip_end = int(new.get("trip_end_min", self.trip_end_min))
        if not 1 <= trip_end <= 60:
            raise ValueError("trip end after 1 to 60 min")
        roles = {str(k): str(v) for k, v in (new.get("roles", self.roles) or {}).items()}
        wrong = {k: v for k, v in roles.items() if v not in self.ROLES}
        if wrong:
            raise ValueError("unknown role: " + ", ".join(f"{k}={v}" for k, v in wrong.items()))
        levels = new.get("levels")
        deviations = self.deviations
        if levels is not None:
            if not isinstance(levels, dict):
                raise ValueError("levels must be an object")
            deviations = cmd.check_levels(levels)
        self.notify_targets, self.approval_timeout_s = targets, timeout
        self.poll_active, self.poll_idle, self.deviations = active, idle * 60, deviations
        self.trip_end_min = trip_end
        self.approval_push = push
        self.roles = roles
        if save:
            os.makedirs(self.state_dir, exist_ok=True)
            tmp = self.ui_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._stored(), fh, ensure_ascii=False, indent=1)
            os.replace(tmp, self.ui_path)
        return self.ui_values()


# Log: every request with its result. The file is the truth, Home Assistant gets the last
# entry as a sensor.
class CommandLog:
    def __init__(self, path: str, max_bytes: int = 2_000_000):
        self.path, self.max_bytes, self._lock = path, max_bytes, threading.Lock()

    def write(self, **entry) -> dict:
        entry = {"time": datetime.now(timezone.utc).isoformat(), **entry}
        with self._lock:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            if os.path.exists(self.path) and os.path.getsize(self.path) > self.max_bytes:
                os.replace(self.path, self.path + ".1")
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        log.info("Command %s: %s (%s)", entry.get("action"), entry.get("result"), entry.get("source"))
        return entry

    def recent(self, n: int = 50) -> list:
        try:
            with open(self.path, encoding="utf-8") as fh:
                lines = fh.readlines()[-n:]
        except FileNotFoundError:
            return []
        entries = []
        for line in lines:
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue                            # a damaged line must not hide the others
        return entries[::-1]


# Approval on the phone. The answer arrives as the Home Assistant event
# mobile_app_notification_action. Only someone who may fire events in Home Assistant could
# forge it. That is why the action name carries a random one time id and the user id from
# the event context is checked.
APPROVAL_ACTION = re.compile(r"^LMGW_(YES|NO)_([0-9a-f]{16})$")

PUSH_TEXTS = {
    "en": {"title": "{vehicle}: approval needed", "message": "{who}{command}. Run now? Valid for {minutes} min.",
           "approve": "Approve", "reject": "Reject", "car": "Car"},
    "de": {"title": "{vehicle}: Freigabe nötig", "message": "{who}{command}. Jetzt ausführen? Gilt {minutes} min.",
           "approve": "Freigeben", "reject": "Ablehnen", "car": "Auto"},
}


def approval_ref(aid: str) -> str:
    """What goes into the log (and so to Home Assistant): a reference from which the id cannot
    be derived. The id itself is the secret of the approval. Whoever knows it could answer."""
    return hashlib.sha256(aid.encode()).hexdigest()[:10]


class Approvals:
    def __init__(self, s: Settings, ha, execute, notify_change):
        self.s, self.ha, self._execute, self._changed = s, ha, execute, notify_change
        self.pending: dict[str, dict] = {}
        self._lock = threading.Lock()
        # Home Assistant admins may approve as well (set by the gateway).
        self.allowed: Callable[[Optional[str]], bool] = lambda user_id: False

    def request(self, action: str, params: dict, source: str, who: str = "", vin: str = "", vehicle: str = "") -> dict:
        aid = secrets.token_hex(8)
        language = getattr(self.ha, "language", lambda: "en")()
        texts = PUSH_TEXTS["de" if language.lower().startswith("de") else "en"]
        entry = {"id": aid, "action": action, "title": cmd.COMMANDS[action].title, "params": params, "source": source,
                 "vin": vin, "vehicle": vehicle, "expires": time.time() + self.s.approval_timeout_s}
        with self._lock:                         # register first: a quick tap already finds it
            self.pending[aid] = entry
        delivered = 0
        for target in self.s.notify_targets:
            domain, _, service = target.partition(".")
            try:
                self.ha.service(domain, service, {
                    "title": texts["title"].format(vehicle=vehicle or texts["car"]),
                    "message": texts["message"].format(who=f"{who}: " if who else "", command=cmd.title(action, language),
                                                       minutes=max(1, self.s.approval_timeout_s // 60)),
                    "data": {"tag": f"lmgw-{aid}", "push": {"interruption-level": "time-sensitive"},
                             "actions": [{"action": f"LMGW_YES_{aid}", "title": texts["approve"], "destructive": True,
                                          "authenticationRequired": True},
                                         {"action": f"LMGW_NO_{aid}", "title": texts["reject"]}]}})
                delivered += 1
            except Exception as error:
                log.warning("Approval could not be delivered to %s: %s", target, error)
        if not delivered:
            with self._lock:
                self.pending.pop(aid, None)
            raise RuntimeError("not deliverable to any device")
        self._changed()
        return entry

    def answer(self, event: dict) -> Optional[str]:
        action = str((event.get("data") or {}).get("action") or "")
        if not action.startswith("LMGW_"):
            return None
        match = APPROVAL_ACTION.match(action)
        if not match:
            log.warning("Approval: unknown action ignored")
            return "invalid"
        yes, aid = match.group(1) == "YES", match.group(2)
        # Check who answers first: someone without permission must not cancel the pending approval either.
        user = (event.get("context") or {}).get("user_id")
        # Allowed to confirm: whoever could trigger the command (role full) and every Home Assistant admin.
        permitted = bool(user) and (self.s.role(user) == "full" or self.allowed(user))
        if not permitted:
            log.warning("Approval: answer from a user without permission ignored")
            return "wrong user"
        with self._lock:
            entry = self.pending.pop(aid, None)
        if not entry:
            return "unknown"
        self._clear(aid)
        if time.time() > entry["expires"]:
            self._changed()
            self._execute(entry, "expired")
            return "expired"
        self._changed()
        self._execute(entry, None if yes else "rejected")
        return "approved" if yes else "rejected"

    def _clear(self, aid: str):
        """Remove the notification from the other devices (iOS: clear_notification)."""
        for target in self.s.notify_targets:
            domain, _, service = target.partition(".")
            try:
                self.ha.service(domain, service, {"message": "clear_notification", "data": {"tag": f"lmgw-{aid}"}})
            except Exception:
                pass

    def reject(self, aid: str) -> bool:
        with self._lock:
            entry = self.pending.pop(aid, None)
        if entry:
            self._changed()
            self._execute(entry, "rejected (gateway page)")
        return bool(entry)

    def cleanup(self):
        now = time.time()
        with self._lock:
            old = [a for a, x in self.pending.items() if x["expires"] < now]
            gone = [self.pending.pop(a) for a in old]
        for entry in gone:
            self._execute(entry, "expired")
        if gone:
            self._changed()


# Home Assistant through the Supervisor proxy
class HA:
    def __init__(self, s: Settings):
        self.s = s
        self._config: Optional[dict] = None

    def _core_config(self) -> dict:
        """Home Assistant configuration (time zone, language). Read once successfully, then kept."""
        if self._config is None:
            r = requests.get(f"{self.s.ha_url}/api/config", timeout=15,
                             headers={"Authorization": f"Bearer {self.s.ha_token}"})
            r.raise_for_status()
            self._config = r.json()
        return self._config

    def integration_loaded(self) -> bool:
        """Does Home Assistant know the integration? Custom integrations are found at its start, so
        right after the first copy the answer is no until the restart. Not the list of loaded
        components: the integration only appears there once it is set up, after the discovery."""
        reply = self._ws_call({"type": "manifest/get", "integration": "leapmotor_gateway"})
        if reply.get("success"):
            return True
        if (reply.get("error") or {}).get("code") == "not_found":
            return False
        raise RuntimeError(str(reply.get("error")))

    def ws_url(self) -> str:
        """The Supervisor proxy serves the WebSocket at /core/websocket, Home Assistant itself at /api/websocket."""
        base = self.s.ha_url.rstrip("/")
        return base.replace("http", "ws", 1) + ("/websocket" if base.endswith("/core") else "/api/websocket")

    def time_zone(self) -> str:
        """Time zone for daily values. Until it can be read, the one of the container (TZ, set by
        the Supervisor) or UTC."""
        try:
            name = self._core_config().get("time_zone") or ""
            ZoneInfo(name)
            return name
        except Exception as error:
            self._config = None                   # read again next time, nothing unusable is kept
            log.info("Time zone of Home Assistant not readable: %s", error)
            return os.environ.get("TZ") or "UTC"

    def language(self) -> str:
        try:
            return str(self._core_config().get("language") or "en")
        except Exception:
            return "en"

    def service(self, domain: str, service: str, data: dict):
        r = requests.post(f"{self.s.ha_url}/api/services/{domain}/{service}", json=data, timeout=20,
                          headers={"Authorization": f"Bearer {self.s.ha_token}"})
        r.raise_for_status()

    def _ws_call(self, message: dict) -> dict:
        """One WebSocket command, the whole reply (success, result or error)."""
        import websocket
        ws = websocket.create_connection(self.ws_url(), timeout=15)
        try:
            ws.recv()
            ws.send(json.dumps({"type": "auth", "access_token": self.s.ha_token}))
            if json.loads(ws.recv()).get("type") != "auth_ok":
                raise RuntimeError("authentication rejected")
            ws.send(json.dumps({"id": 1, **message}))
            return json.loads(ws.recv())
        finally:
            ws.close()

    def _auth_list(self) -> list:
        reply = self._ws_call({"type": "config/auth/list"})
        if not reply.get("success"):
            raise RuntimeError(str(reply.get("error")))
        return reply["result"]

    def users(self) -> list:
        """Active Home Assistant users not created by the system (for the role table)."""
        return [{"id": u["id"], "name": u["name"]} for u in self._auth_list()
                if u.get("is_active") and not u.get("system_generated")]

    def admin_ids(self) -> set:
        """Ids of the active Home Assistant admins (group system-admin)."""
        return {u["id"] for u in self._auth_list() if u.get("is_active") and "system-admin" in (u.get("group_ids") or [])}

    def events(self, event_type: str, callback, stop: threading.Event):
        """WebSocket subscription with reconnect. Runs in its own thread."""
        import websocket
        url = self.ws_url()
        while not stop.is_set():
            try:
                ws = websocket.create_connection(url, timeout=60)
                ws.recv()
                ws.send(json.dumps({"type": "auth", "access_token": self.s.ha_token}))
                if json.loads(ws.recv()).get("type") != "auth_ok":
                    raise RuntimeError("Home Assistant WebSocket: authentication rejected")
                ws.send(json.dumps({"id": 1, "type": "subscribe_events", "event_type": event_type}))
                ws.settimeout(None)
                while not stop.is_set():
                    message = json.loads(ws.recv())
                    if message.get("type") == "event":
                        callback(message["event"])
            except Exception as error:
                log.warning("Home Assistant WebSocket: %s, next attempt in 15 s", error)
                stop.wait(15)


# Who may use the interface. Home Assistant gives every logged in user an ingress session, so
# the source address 172.30.32.2 only means "through Home Assistant", not "admin". The
# Supervisor passes the user as X-Remote-User-Id. Only admins (group system-admin) get in.
# Fail closed: if the list cannot be fetched and is still empty, nobody gets in.
class Admins:
    def __init__(self, ha):
        self.ha, self.ids, self.time, self._lock = ha, set(), 0.0, threading.Lock()

    def check(self, user_id: Optional[str]) -> bool:
        if not user_id:
            return False
        with self._lock:
            age = time.time() - self.time
            if age > 300 or (user_id not in self.ids and age > 30):
                try:
                    self.ids, self.time = set(self.ha.admin_ids()), time.time()
                except Exception as error:
                    log.warning("Admin list not available: %s", error)
            return user_id in self.ids


SECRET_PATTERN = re.compile(r"(?i)(pass(?:wor[dt])?|pin|operat\w*|token|secret)([\"']?\s*[:=]\s*[\"']?)([^\"'&,\s}]+)")


# Redact every log line in the process, not only our own: leapmotor_api logs the whole request
# body of every remote command, including operatePassword (the encrypted PIN). A record factory
# applies to all loggers and handlers, also to those added later.
def _redacting_factory(factory):
    def new(*args, **kwargs):
        record = factory(*args, **kwargs)
        try:
            text = record.getMessage()
        except Exception:
            return record
        redacted = SECRET_PATTERN.sub(lambda m: m.group(1) + m.group(2) + "***", text)
        if redacted != text:
            record.msg, record.args = redacted, None
        return record
    new.lmgw_redact = True
    return new


if not getattr(logging.getLogRecordFactory(), "lmgw_redact", False):
    logging.setLogRecordFactory(_redacting_factory(logging.getLogRecordFactory()))


class VehicleRuntime:
    def __init__(self, vin: str):
        self.vin = vin
        self.lib_vehicle = None           # vehicle object of the library
        self.values: dict = {}
        self.extras: dict = {}
        self.extras_time = 0.0
        self.image_hash: Optional[str] = None
        self.image_pack = None
        self.trips: Optional[dict] = None    # trip computer data, from trip_computer_<VIN>.json
        self.last_poll: Optional[str] = None
        self.next_poll = 0.0              # time.time() of the next poll
        self.status = "waiting"
        self.errors_in_row = 0
        self.was_ready = False
        self.ready_off_since: Optional[float] = None   # time.time() when READY went off


class Gateway:
    def __init__(self, s: Settings, client_factory=None, ha: Optional[HA] = None):
        self.s = s
        self.ha = ha or HA(s)
        self.store = ac.Store(s.state_dir)
        self.log = CommandLog(os.path.join(s.state_dir, "commands.jsonl"))
        self.approvals = Approvals(s, self.ha, self._after_approval, lambda: None)
        self._client_factory = client_factory or self._build_client
        self.clients: dict[str, object] = {}              # account id -> LeapmotorApiClient
        self._locks: dict[str, threading.Lock] = {}       # per account: one call at a time
        self._list_time: dict[str, float] = {}            # last vehicle list per account
        self.vehicles: dict[str, VehicleRuntime] = {}
        self._image_lock = threading.Lock()               # polling and app do not build the image at the same time
        self.status = "cloud off (option cloud_enabled)" if not s.cloud_enabled else "starting"
        self._last: dict[tuple, float] = {}
        self._hour: list[float] = []
        self._limit_lock = threading.Lock()               # cooldown and hourly limit are hit from several requests
        self._trip_lock = threading.Lock()                # polling writes, the app reads the trip computer
        self._refresh_last: dict[str, float] = {}         # "refresh" per vehicle
        self._wake = threading.Event()
        self._stop = threading.Event()
        self.login_refused: dict[str, str] = {}           # account id -> reason. No new login until changed.
        self.admins = Admins(self.ha)
        self.approvals.allowed = self.admins.check
        for vin in self.store.vehicles():
            self.vehicles[vin] = VehicleRuntime(vin)
        if s.demo_mode and client_factory is None:
            self._client_factory = self._demo_setup()

    def _demo_setup(self):
        import demo
        if not self.store.account_ids():
            aid = self.store.add_account(demo.DEMO_ACCOUNT, "demo")
            self.store.report_vehicle(demo.DEMO_VIN, aid, name=demo.DEMO_NAME, model="T03")
            self.store.update_vehicle(demo.DEMO_VIN, plate=demo.DEMO_PLATE)
            self.vehicles[demo.DEMO_VIN] = VehicleRuntime(demo.DEMO_VIN)
        demo.seed_trip_computer(self.s.state_dir)
        cloud = demo.DemoCloud()
        return lambda aid: cloud

    # Cloud connections
    def _build_client(self, aid: str):
        from leapmotor_api import LeapmotorApiClient
        a = self.store.account(aid)
        cert, key = ac.certificate_paths(self.s.state_dir)
        return LeapmotorApiClient(username=a["username"], password=a["password"], app_cert_path=cert,
                                  app_key_path=key, operation_password=None, device_id=a["device_id"])

    def _account_lock(self, aid: str) -> threading.Lock:
        return self._locks.setdefault(aid, threading.Lock())

    def _client(self, aid: str):
        """Log in only when the account is not refused. A rejected login is not repeated until
        password or certificate change in the app. Otherwise Leapmotor might lock the account."""
        c = self.clients.get(aid)
        if c is None:
            if aid in self.login_refused:
                raise RuntimeError(self.login_refused[aid])
            c = self._client_factory(aid)
            try:
                c.login()
            except Exception as error:
                from leapmotor_api.exceptions import LeapmotorAuthError
                if isinstance(error, LeapmotorAuthError):
                    reason = ("certificate rejected, check it in the app" if "cert" in type(error).__name__.lower()
                              else "login rejected, check the password in the app")
                    self.login_refused[aid] = reason
                    self.log.write(action="login", source="cloud", level="-", result=reason,
                                   params={"account": aid, "error": type(error).__name__})
                raise
            self.clients[aid] = c
        return c

    def redact(self, text) -> str:
        """Everything that leaves the gateway (log, sensors, messages) without passwords and PINs,
        also when an exception of the library echoes them."""
        text = str(text)
        secret = [self.store.account(a).get("password", "") for a in self.store.account_ids()]
        secret += [v.get("pin", "") for v in self.store.vehicles().values()]
        for s in sorted({s for s in secret if s and len(s) >= 4}, key=len, reverse=True):
            text = text.replace(s, "***")
        return SECRET_PATTERN.sub(lambda m: m.group(1) + m.group(2) + "***", text)[:240]

    def allow_after_change(self, aid: Optional[str] = None):
        """After a new password (one account) or certificate (all): logging in is allowed again."""
        for a in ([aid] if aid else list(self.login_refused)):
            self.login_refused.pop(a, None)
            self.clients.pop(a, None)

    def vehicle_list(self, aid: str) -> list[str]:
        """Fetch the vehicles of an account (read). New ones are added, enabled, without PIN."""
        with self._account_lock(aid):
            listed = self._client(aid).get_vehicle_list()
        vins = []
        for v in listed:
            model = str(getattr(v.car_type, "value", v.car_type) or "").upper()
            name = getattr(v, "vehicle_nickname", None) or getattr(v, "plate_number", None) or model
            if self.store.report_vehicle(v.vin, aid, name=name, model=model):
                self.log.write(action="vehicle_new", source="cloud", level="-", result="added",
                               vin=v.vin, params={"account": aid, "model": model})
            r = self.vehicles.setdefault(v.vin, VehicleRuntime(v.vin))
            r.lib_vehicle = v
            vins.append(v.vin)
        self._list_time[aid] = time.time()
        return vins

    # Polling
    def poll(self, vin: str) -> dict:
        info = self.store.vehicle(vin)
        if info is None:
            raise KeyError("unknown vehicle")
        aid, r = info["account"], self.vehicles.setdefault(vin, VehicleRuntime(vin))
        if r.lib_vehicle is None or time.time() - self._list_time.get(aid, 0) > 6 * 3600:
            self.vehicle_list(aid)
            if r.lib_vehicle is None:
                raise RuntimeError("vehicle no longer in the account")
        with self._account_lock(aid):
            status = self._client(aid).get_vehicle_status(r.lib_vehicle)
        if time.time() - r.extras_time > 3600:
            self._fetch_extras(aid, r)
        self._build_image(aid, r, status)
        r.values, r.last_poll = st.values(status), _now()
        self._update_trips(r)
        return r.values

    def _trip_data(self, r: VehicleRuntime) -> dict:
        """Call only while holding self._trip_lock."""
        if r.trips is None:
            r.trips = tc.Store(self.s.state_dir, r.vin).load()
        return r.trips

    def _update_trips(self, r: VehicleRuntime):
        """Update trips and charges. An error here does not disturb polling."""
        try:
            with self._trip_lock:
                if tc.process(self._trip_data(r), r.values, datetime.now(timezone.utc), self.s.trip_end_min):
                    tc.Store(self.s.state_dir, r.vin).save(r.trips)
        except Exception as error:
            log.info("%s trip computer: %s", r.vin[-6:], error)

    def _fetch_extras(self, aid: str, r: VehicleRuntime):
        """Rarely needed, so hourly. Every part may fail on its own."""
        ex, c = dict(r.extras), self._client(aid)
        with self._account_lock(aid):
            try:
                w = c.get_consumption_last_week_breakdown(r.lib_vehicle)
                total = (w.driver_ec or 0) + (w.ac_ec or 0) + (w.other_ec or 0)
                ex.update(week_driving_kwh=w.driver_ec, week_climate_kwh=w.ac_ec, week_other_kwh=w.other_ec)
                if total:
                    ex.update(week_driving_percent=round(w.driver_ec / total * 100, 1),
                              week_climate_percent=round(w.ac_ec / total * 100, 1),
                              week_other_percent=round(w.other_ec / total * 100, 1))
            except Exception as error:
                log.info("%s weekly shares: %s", r.vin[-6:], error)
            try:
                h = (c.today_breakdown(r.lib_vehicle, self.ha.time_zone()) if getattr(c, "demo", False)
                     else _today_breakdown(c, r.lib_vehicle, self.ha.time_zone()))
                total = (h.driver_ec or 0) + (h.ac_ec or 0) + (h.other_ec or 0)
                ex.update(today_driving_kwh=h.driver_ec, today_climate_kwh=h.ac_ec, today_other_kwh=h.other_ec)
                if total:
                    ex.update(today_driving_percent=round(h.driver_ec / total * 100, 1),
                              today_climate_percent=round(h.ac_ec / total * 100, 1),
                              today_other_percent=round(h.other_ec / total * 100, 1))
            except Exception as error:
                log.info("%s daily shares: %s", r.vin[-6:], self.redact(error))
            try:
                d = ((c.distance_energy_7_days(r.lib_vehicle) if getattr(c, "demo", False)
                      else _distance_energy_7_days(c, r.lib_vehicle)) or {}).get("data") or {}
                if d.get("totalEnergy") is None:          # fallback: library call without a time window
                    d = (c.get_mileage_energy_detail(r.lib_vehicle) or {}).get("data") or {}
                ex.update(total_energy_kwh=d.get("totalEnergy"), distance_7_days_km=d.get("totalAccumulatedMileage"))
            except Exception as error:
                log.info("%s energy detail: %s", r.vin[-6:], self.redact(error))
            try:
                rank = c.get_consumption_weekly_rank(r.lib_vehicle)
                weekly = [x.hundred_km_ec for x in (rank.weekly or []) if x.hundred_km_ec]
                ex["consumption_6_weeks"] = round(sum(weekly) / len(weekly), 1) if weekly else None
            except Exception as error:
                log.info("%s 6 week consumption: %s", r.vin[-6:], error)
            try:
                ml = c.get_message_list(page_no=1, page_size=10)
                own = [m for m in ml.messages if not m.vin or m.vin == r.vin]
                ex["messages_unread"] = sum(1 for m in own if not m.read_flag)
                if own:
                    ex.update(last_message=own[0].title or own[0].message, last_message_time=st.to_utc(own[0].send_time))
            except Exception as error:
                log.info("%s messages: %s", r.vin[-6:], error)
        r.extras, r.extras_time = ex, time.time()

    def _build_image(self, aid: str, r: VehicleRuntime, status):
        """Vehicle image matching the status. The layer pack is loaded once per vehicle."""
        with self._image_lock:
            self._build_image_locked(aid, r, status)

    def _build_image_locked(self, aid: str, r: VehicleRuntime, status):
        try:
            from leapmotor_api.image import CarImagePackage
            if r.image_pack is None:
                path = os.path.join(self.s.state_dir, f"image_pack_{r.vin}.zip")
                if not os.path.exists(path):
                    with self._account_lock(aid):
                        c = self._client(aid)
                        key = _find_key(c.get_car_picture(r.lib_vehicle))
                        if not key:
                            raise RuntimeError("no image key in the response")
                        data = c.download_car_picture_package(picture_key=key)
                    with open(path, "wb") as fh:
                        fh.write(data)
                with open(path, "rb") as fh:
                    r.image_pack = CarImagePackage.from_zip(fh.read())
            png = vi.remove_edge_lines(r.image_pack.compose(status))
            if isinstance(png, (bytes, bytearray)):
                h = hashlib.sha256(png).hexdigest()[:16]
                if h != r.image_hash:
                    with open(os.path.join(self.s.state_dir, f"vehicle_{r.vin}.png"), "wb") as fh:
                        fh.write(png)
                    r.image_hash = h
        except Exception as error:
            log.info("%s vehicle image: %s", r.vin[-6:], error)

    def plate(self, vin: str) -> str:
        """Set in the app, otherwise the one from the Leapmotor app (vehicle list)."""
        own = (self.store.vehicle(vin) or {}).get("plate")
        if own:
            return own
        try:
            return pl.normalize(getattr(getattr(self.vehicles.get(vin), "lib_vehicle", None), "plate_number", None))
        except ValueError:
            return ""

    def image_path(self, vin: str) -> Optional[str]:
        """Only for known vehicles. The file name comes from the stored VIN, never from the request."""
        known = next((v for v in self.store.vehicles() if v == vin), None)
        if known is None:
            return None
        p = os.path.join(self.s.state_dir, f"vehicle_{known}.png")
        return p if os.path.exists(p) else None

    def charge_history(self, vin: str, days: int = 90) -> list:
        from datetime import date, timedelta
        info, r = self.store.vehicle(vin), self.vehicles.get(vin)
        if not info or not r or r.lib_vehicle is None:
            raise RuntimeError("vehicle not polled yet")
        end, start, result, page = date.today(), date.today() - timedelta(days=days), [], 1
        with self._account_lock(info["account"]):
            c = self._client(info["account"])
            while page < 20:
                s = c.get_charging_daily_detail(vin, start_time=start, end_time=end, page_num=page, page_size=50)
                result += [{"start": datetime.fromtimestamp(x.start_ts / 1000, timezone.utc).isoformat(),
                            "end": datetime.fromtimestamp(x.end_ts / 1000, timezone.utc).isoformat(),
                            "type": "DC" if str(getattr(x.charge_type, "value", x.charge_type)) == "2" else "AC",
                            "kwh": x.energy_kwh, "lat": x.latitude, "lon": x.longitude} for x in s.records]
                if len(s.records) < 50:
                    break
                page += 1
        return result

    def loop(self):
        """An unexpected error in one pass must not end polling for good."""
        while not self._stop.is_set():
            try:
                self._pass()
            except Exception as error:
                log.error("Polling loop: %s: %s", type(error).__name__, self.redact(error))
                self.status = "error while polling, next attempt in 60 s"
                self._stop.wait(60)

    def _pass(self):
        self.approvals.cleanup()
        if not self.s.cloud_enabled:
            self.status = "cloud off (option cloud_enabled)"
            self._wake.wait(60)
            self._wake.clear()
            return
        if not self.s.demo_mode and not ac.certificate_info(self.s.state_dir)["present"]:
            self.status = "app certificate missing, upload it in the app"
            self._wake.wait(60)
            self._wake.clear()
            return
        # Accounts without known vehicles: fetch the list
        for aid in self.store.account_ids():
            if not any(i["account"] == aid for i in self.store.vehicles().values()) and \
                    time.time() - self._list_time.get(aid, 0) > 600:
                try:
                    self.vehicle_list(aid)
                except Exception as error:
                    self._list_time[aid] = time.time()
                    log.warning("Vehicle list %s: %s", aid, self.redact(error))
        now = time.time()
        for vin, info in list(self.store.vehicles().items()):
            r = self.vehicles.setdefault(vin, VehicleRuntime(vin))
            if not info.get("enabled", True):
                r.status = "disabled"
                continue
            if r.next_poll > now:
                continue
            try:
                v = self.poll(vin)
                r.errors_in_row = 0
                wait = self.poll_interval(r, v)
                r.status = ("dry run · " if self.s.dry_run else "") + \
                    f"ok · next poll in {wait // 60 or wait} {'min' if wait >= 60 else 's'}"
            except Exception as error:
                r.errors_in_row += 1
                if r.errors_in_row >= 2:                         # log in again from the second error on
                    self.clients.pop(info["account"], None)
                wait = min(60 * 2 ** r.errors_in_row, 1800)      # up to 30 min, so the account is not locked
                if info["account"] in self.login_refused:
                    wait = 600
                    r.status = self.login_refused[info["account"]]
                else:
                    r.status = self.redact(f"Error: {error}")[:140] + f" · next attempt in {wait // 60} min"
                log.warning("%s poll failed (%d.): %s", vin[-6:], r.errors_in_row, self.redact(error))
            r.next_poll = time.time() + wait
        active = [r for v, r in list(self.vehicles.items()) if (self.store.vehicle(v) or {}).get("enabled", False)]
        self.status = ("dry run · " if self.s.dry_run else "") + f"{len(active)} vehicle(s) active"
        next_poll = min([r.next_poll for r in active], default=time.time() + 60)
        end = min(next_poll, time.time() + 300)
        while time.time() < end and not self._stop.is_set():
            if self._wake.wait(min(15, max(1, end - time.time()))):
                self._wake.clear()
                break
            self.approvals.cleanup()

    def wake(self, vin: Optional[str] = None):
        for v, r in list(self.vehicles.items()):
            if vin is None or v == vin:
                r.next_poll = 0
        self._wake.set()

    # Commands
    def _pick_vin(self, vin: Optional[str]) -> Optional[str]:
        known = self.store.vehicles()
        if vin:
            return vin if vin in known else None
        return next(iter(known)) if len(known) == 1 else None

    def trigger(self, action: str, params: Optional[dict] = None, source: str = "ha",
                user_id: Optional[str] = None, user_name: str = "", vin: Optional[str] = None) -> dict:
        params = params or {}
        who = {"user_id": user_id, "user": user_name or ("automation" if not user_id else user_id)}
        target = self._pick_vin(vin)
        if target is None:
            return self._finish(action, params, source, "-", "vehicle unknown or ambiguous (specify vin)", vin=vin, **who)
        who["vin"] = target
        if action == "refresh":
            # Only reads, but costs a cloud request: at most one per minute and vehicle, so a loop in
            # an automation cannot get the account locked.
            if self.s.role(user_id) == "read":
                return {"result": "no permission (role read)", "ok": False, "vin": target}
            with self._limit_lock:
                if time.time() - self._refresh_last.get(target, 0) < 60:
                    return {"result": "repeated too fast", "ok": False, "vin": target}
                self._refresh_last[target] = time.time()
            self.wake(target)
            return {"result": "refresh requested", "ok": True, "vin": target}
        level = cmd.level(action, self.s.deviations)
        if action not in cmd.COMMANDS:
            return self._finish(action, params, source, level, "unknown, not in the allowlist", **who)
        if level == cmd.BLOCKED:
            return self._finish(action, params, source, level, "blocked", **who)
        role = self.s.role(user_id)
        if role == "read" or (role == "limited" and level != cmd.FREE):
            return self._finish(action, params, source, level, f"no permission (role {role})", **who)
        if action in ("climate_on", "climate_off"):
            try:                                  # check values before anyone is asked and before
                                                  # cooldown and hourly limit count
                (cmd.climate_payload if action == "climate_on" else cmd.t03_off_payload)(params)
            except ValueError as error:
                return self._finish(action, params, source, level, f"invalid: {error}", **who)
        with self._limit_lock:
            now = time.time()
            reason = None
            self._hour = [t for t in self._hour if now - t < 3600]
            if now - self._last.get((target, action), 0) < self.s.cooldown_s:
                reason = "repeated too fast"
            elif len(self._hour) >= self.s.max_per_hour:
                reason = "hourly limit reached"
            else:
                self._last[(target, action)] = now
                self._hour.append(now)
        if reason:
            return self._finish(action, params, source, level, reason, **who)
        # Without push the role full (checked above) is enough.
        if level == cmd.APPROVAL and self.s.approval_push:
            name = (self.store.vehicle(target) or {}).get("name", "")
            try:
                a = self.approvals.request(action, params, source, who["user"], vin=target, vehicle=name)
                a.update(who)
            except Exception as error:
                return self._finish(action, params, source, level, f"approval not deliverable: {error}", **who)
            return self._finish(action, params, source, level, "waiting for approval", approval=approval_ref(a["id"]), **who)
        return self._send_to_car(action, params, source, level, **who)

    def _after_approval(self, entry: dict, reason: Optional[str]):
        who = {k: entry[k] for k in ("user_id", "user", "vin") if k in entry}
        if reason:
            self._finish(entry["action"], entry["params"], entry["source"], cmd.APPROVAL, reason,
                         approval=approval_ref(entry["id"]), **who)
        else:
            self._send_to_car(entry["action"], entry["params"], entry["source"], cmd.APPROVAL,
                              approval=approval_ref(entry["id"]), **who)

    def _send_to_car(self, action, params, source, level, vin: str, **extra) -> dict:
        extra["vin"] = vin
        if self.s.dry_run:
            return self._finish(action, params, source, level, "dry run, not sent", **extra)
        info, r = self.store.vehicle(vin), self.vehicles.get(vin)
        if not self.s.cloud_enabled or not info or not r or r.lib_vehicle is None:
            return self._finish(action, params, source, level, "not sent, cloud off or vehicle not connected", **extra)
        try:
            with self._account_lock(info["account"]):
                c = self._client(info["account"])
                # PIN only for exactly this command and vehicle, removed right after.
                c.operation_password = info.get("pin") or None
                try:
                    model = (info.get("model") or "T03").upper()
                    response = cmd.COMMANDS[action].run(c, vin, params, model)
                finally:
                    c.operation_password = None
        except Exception as error:
            return self._finish(action, params, source, level, f"error: {type(error).__name__}: {error}", **extra)
        threading.Timer(8, self.wake, args=(vin,)).start()       # see the effect soon
        return self._finish(action, params, source, level, "sent", response=str(response)[:300], **extra)

    # Results that count as success. The integration decides by `ok`, not by the text.
    OK_RESULTS = ("sent", "dry run, not sent", "waiting for approval", "refresh requested")

    def _finish(self, action, params, source, level, result, **extra) -> dict:
        if "response" in extra:
            extra["response"] = self.redact(extra["response"])
        return self.log.write(action=action, params=params, source=source, level=level,
                              result=self.redact(result), ok=result in self.OK_RESULTS, **extra)

    # For the interface and the integration
    def poll_interval(self, r: VehicleRuntime, v: dict) -> int:
        """Fast while something happens, and after parking for as long as a trip counts as
        interrupted (trip_end_min). Otherwise detecting a continued trip would depend on whether
        the car happened to be unlocked."""
        now, ready = time.time(), bool(v.get("ready"))
        if r.was_ready and not ready:
            r.ready_off_since = now
        elif ready:
            r.ready_off_since = None
        r.was_ready = ready
        afterrun = r.ready_off_since is not None and now - r.ready_off_since < self.s.trip_end_min * 60
        return self.s.poll_active if st.active(v) or afterrun else self.s.poll_idle

    def vehicle_data(self, vin: str) -> dict:
        r, info = self.vehicles.get(vin) or VehicleRuntime(vin), self.store.vehicle(vin) or {}
        last = next((e for e in self.log.recent(200) if e.get("vin") == vin), None)
        with self._trip_lock:
            trips = tc.summary(self._trip_data(r), r.values, datetime.now(timezone.utc))
        return {"vin": vin, "name": info.get("name"), "model": info.get("model"), "enabled": info.get("enabled", True),
                "pin_set": bool(info.get("pin")), "status": r.status, "last_poll": r.last_poll,
                "values": r.values, "extras": r.extras, "image_hash": r.image_hash, "last_command": last,
                "approvals_pending": sum(1 for x in self.approvals.pending.values() if x.get("vin") == vin),
                "trip_end_min": self.s.trip_end_min, "plate": self.plate(vin) or None,
                "trip_computer": trips}

    def summary(self) -> dict:
        return {"status": self.status, "cloud_enabled": self.s.cloud_enabled, "dry_run": self.s.dry_run,
                "demo_mode": self.s.demo_mode,
                "certificate": ac.certificate_info(self.s.state_dir),
                "levels": {n: cmd.level(n, self.s.deviations) for n in cmd.COMMANDS},
                "approval_push": self.s.approval_push}

    def command_list(self) -> dict:
        return {n: {"title": c.title, "icon": c.icon, "button": c.button, "level": cmd.level(n, self.s.deviations)}
                for n, c in cmd.COMMANDS.items()}

    def token(self) -> str:
        """Shared secret with the integration, created once."""
        path = os.path.join(self.s.state_dir, "integration_token")
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read().strip()
        except FileNotFoundError:
            t = secrets.token_urlsafe(32)
            os.makedirs(self.s.state_dir, exist_ok=True)
            with open(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600), "w", encoding="utf-8") as fh:
                fh.write(t)
            os.chmod(path, 0o600)
            return t

    def announce(self):
        """Like Music Assistant: announce through the Supervisor. The integration then appears
        under Discovered and knows address and token."""
        if not self.s.ha_token:
            return
        try:
            r = requests.post("http://supervisor/discovery", timeout=15,
                              headers={"Authorization": f"Bearer {self.s.ha_token}"},
                              json={"service": "leapmotor_gateway",
                                    "config": {"host": _env("LG_HOSTNAME", "local-leapmotor-gateway"), "port": 8789,
                                               "token": self.token()}})
            log.info("Supervisor discovery: HTTP %s", r.status_code)
        except Exception as error:
            log.warning("Supervisor discovery: %s", error)

    def announce_when_loaded(self):
        """Announce only once Home Assistant has loaded the integration. Right after the first
        install it is only copied, and Home Assistant would log "Cannot find integration"."""
        while not self._stop.is_set():
            try:
                if self.ha.integration_loaded():
                    self.announce()
                    return
            except Exception as error:
                log.info("Integration check: %s", error)
            self._stop.wait(60)

    def start(self):
        self.token()
        if self.s.ha_token:
            threading.Thread(target=self.announce_when_loaded, daemon=True, name="announce").start()
        if self.s.ha_token:
            threading.Thread(target=self.ha.events, daemon=True, name="ha-events",
                             args=("mobile_app_notification_action", self.approvals.answer, self._stop)).start()
        threading.Thread(target=self.loop, daemon=True, name="polling").start()


# Web: app page (ingress) and API for the integration
gateway: Optional["Gateway"] = None


@asynccontextmanager
async def _lifespan(_app):
    global gateway
    if gateway is None:
        gateway = Gateway(Settings())
        gateway.start()
    yield


app = FastAPI(title="Leapmotor Gateway", docs_url=None, redoc_url=None, lifespan=_lifespan)
# Narrower than the whole Supervisor network (/23), which also holds all other apps
# (172.30.33.x). The interface only from the ingress proxy, the API for the integration only
# from 172.30.32.1 and there with a token as well. Apps with host networking share that
# address, so the token is the actual protection.
INGRESS = ipaddress.ip_address(_env("LG_INGRESS_IP", "172.30.32.2"))
CORE = ipaddress.ip_address(_env("LG_CORE_IP", "172.30.32.1"))


def _source(request: Request):
    try:
        return ipaddress.ip_address(request.client.host)
    except (AttributeError, ValueError):
        return None


@app.middleware("http")
async def _auth(request: Request, call_next):
    """Fail closed. X-Forwarded-For does not count, anyone can set that header."""
    path, source = request.url.path, _source(request)
    if path == "/health":
        return await call_next(request)
    if path.startswith("/api/v2/"):
        header = request.headers.get("authorization", "")
        if source != CORE or gateway is None or not hmac.compare_digest(header.encode(), f"Bearer {gateway.token()}".encode()):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)
    if source != INGRESS:
        return JSONResponse({"detail": "unauthorized"}, status_code=401)
    user = request.headers.get("x-remote-user-id", "")
    if gateway is None or not await run_in_threadpool(gateway.admins.check, user):
        if path == "/":
            return HTMLResponse("<!doctype html><meta charset=utf-8><p style='font-family:system-ui;padding:2rem'>"
                                "The Leapmotor Gateway is available to Home Assistant admins only.</p>", status_code=403)
        return JSONResponse({"detail": "Home Assistant admins only"}, status_code=403)
    request.state.user = (request.headers.get("x-remote-user-display-name")
                          or request.headers.get("x-remote-user-name") or user)
    return await call_next(request)


@app.get("/health")
def health():
    return {"ok": True}


def _user(request: Request) -> str:
    return getattr(request.state, "user", "")


# App page (ingress)
@app.get("/api/status")
def api_status():
    return {**gateway.summary(), "approvals": list(gateway.approvals.pending.values()),
            "vehicles": [gateway.vehicle_data(v) for v in gateway.store.vehicles()]}


@app.get("/api/image/{vin}")
def api_image(vin: str):
    path = gateway.image_path(vin)
    if not path:
        raise HTTPException(404, "no image yet")
    return FileResponse(path, media_type="image/png")


@app.get("/api/log")
def api_log():
    return gateway.log.recent(100)


def _ha_choices() -> dict:
    """Choices for the settings page: push services of the companion app and users."""
    choices = {"notify": [], "users": [], "users_error": None}
    try:
        r = requests.get(f"{gateway.s.ha_url}/api/services", timeout=15,
                         headers={"Authorization": f"Bearer {gateway.s.ha_token}"})
        r.raise_for_status()
        for d in r.json():
            if d["domain"] == "notify":
                choices["notify"] = sorted(f"notify.{n}" for n in d["services"] if n.startswith("mobile_app_"))
    except Exception as error:
        log.warning("Notify services: %s", error)
        choices["notify_error"] = "Home Assistant services not available, see the app log"
    try:
        choices["users"] = gateway.ha.users()
    except Exception as error:
        log.warning("User list: %s", error)
        choices["users_error"] = "user list not available, see the app log"
    return choices


@app.get("/api/settings")
def api_settings():
    return {"values": gateway.s.ui_values(), "choices": _ha_choices(),
            "commands": {n: c.title for n, c in cmd.COMMANDS.items()},
            "fixed": {"cloud_enabled": gateway.s.cloud_enabled, "dry_run": gateway.s.dry_run}}


@app.put("/api/settings")
def api_settings_update(request: Request, new: dict = Body(...)):
    before = gateway.s.ui_values()
    allowed = set(_ha_choices()["notify"])
    if "notify_targets" in new and not allowed:
        # Home Assistant unreachable: rather no change of the push targets than an unchecked one
        # (otherwise the approval could be redirected).
        raise HTTPException(503, "push services of Home Assistant not available right now, please try again")
    try:
        after = gateway.s.ui_apply(new, allowed_targets=allowed)
    except (ValueError, TypeError) as error:
        raise HTTPException(422, str(error))
    changed = {k: {"before": before[k], "after": after[k]} for k in after if before[k] != after[k]}
    gateway.log.write(action="settings", source="app", level="-", result="changed" if changed else "unchanged",
                      params=changed, user=_user(request))
    gateway.wake()
    return after


@app.post("/api/approvals/{aid}/reject")
def api_reject(aid: str):
    # Only rejecting on purpose: approving works only on the phone.
    if not gateway.approvals.reject(aid):
        raise HTTPException(404, "no pending approval")
    return {"rejected": aid}


# Accounts, vehicles, certificate. Secrets only go in, never out. The log notes changes
# without values.
@app.get("/api/accounts")
def api_accounts():
    return {**gateway.store.public(), "certificate": ac.certificate_info(gateway.s.state_dir)}


@app.post("/api/accounts")
def api_account_add(request: Request, d: dict = Body(...)):
    try:
        aid = gateway.store.add_account(str(d.get("username", "")), str(d.get("password", "")))
    except ValueError as error:
        raise HTTPException(422, str(error))
    gateway.log.write(action="account_add", source="app", level="-", result="added", params={"account": aid},
                      user=_user(request))
    gateway.wake()
    return {"id": aid}


@app.put("/api/accounts/{aid}")
def api_account_update(aid: str, request: Request, d: dict = Body(...)):
    try:
        gateway.store.set_password(aid, str(d.get("password", "")))
    except (KeyError, ValueError) as error:
        raise HTTPException(422, str(error))
    gateway.allow_after_change(aid)
    gateway.log.write(action="account_password", source="app", level="-", result="changed", params={"account": aid},
                      user=_user(request))
    gateway.wake()
    return {"ok": True}


@app.delete("/api/accounts/{aid}")
def api_account_remove(aid: str, request: Request):
    try:
        gone = gateway.store.remove_account(aid)
    except KeyError as error:
        raise HTTPException(404, str(error))
    gateway.clients.pop(aid, None)
    for vin in gone:
        gateway.vehicles.pop(vin, None)
    gateway.login_refused.pop(aid, None)
    gateway.log.write(action="account_remove", source="app", level="-", result="removed",
                      params={"account": aid, "vehicles": gone}, user=_user(request))
    return {"removed": aid, "vehicles": gone}


@app.post("/api/accounts/{aid}/fetch")
def api_account_fetch(aid: str):
    if not gateway.s.cloud_enabled:
        raise HTTPException(409, "cloud is off (app option cloud_enabled)")
    try:
        return {"vehicles": gateway.vehicle_list(aid)}
    except KeyError as error:
        raise HTTPException(404, str(error))
    except Exception as error:
        gateway.clients.pop(aid, None)
        raise HTTPException(502, gateway.redact(f"Leapmotor: {error}"))


@app.put("/api/vehicles/{vin}")
def api_vehicle_update(vin: str, request: Request, d: dict = Body(...)):
    try:
        new = gateway.store.update_vehicle(vin, name=d.get("name"), enabled=d.get("enabled"),
                                           pin=None if d.get("pin") is None else str(d.get("pin")),
                                           plate=None if d.get("plate") is None else str(d.get("plate")))
    except KeyError as error:
        raise HTTPException(404, str(error))
    except ValueError as error:
        raise HTTPException(422, str(error))
    what = {k: ("set" if k == "pin" and d[k] else "deleted" if k == "pin" else d[k])
            for k in d if k in ("name", "enabled", "pin", "plate")}
    gateway.log.write(action="vehicle_update", source="app", level="-", result="changed", vin=vin, params=what,
                      user=_user(request))
    gateway.wake(vin)
    return new


@app.post("/api/certificate")
def api_certificate(request: Request, certificate: UploadFile = File(...), key: UploadFile = File(...)):
    cert_pem, key_pem = certificate.file.read(64 * 1024 + 1), key.file.read(64 * 1024 + 1)
    if len(cert_pem) > 64 * 1024 or len(key_pem) > 64 * 1024:
        raise HTTPException(413, "file too large (at most 64 KB)")
    try:
        info = ac.save_certificate(gateway.s.state_dir, cert_pem, key_pem)
    except ValueError as error:
        raise HTTPException(422, str(error))
    gateway.clients.clear()
    gateway.allow_after_change()
    gateway.log.write(action="certificate", source="app", level="-", result="uploaded",
                      params={"valid_until": info.get("valid_until")}, user=_user(request))
    gateway.wake()
    return info


# API for the integration
@app.get("/api/v2/vehicles")
def v2_vehicles():
    return {"gateway": {**gateway.summary(), "approvals_pending": len(gateway.approvals.pending),
                        "commands": gateway.command_list()},
            "vehicles": {v: gateway.vehicle_data(v) for v in gateway.store.vehicles()}}


@app.post("/api/v2/command")
def v2_command(d: dict = Body(...)):
    params = d.get("params") or {}
    if not isinstance(params, dict):
        raise HTTPException(422, "params must be an object")
    return gateway.trigger(str(d.get("action", "")), params, source="ha", user_id=d.get("user_id") or None,
                           user_name=str(d.get("user_name") or ""), vin=d.get("vin") or None)


@app.get("/api/v2/image")
def v2_image(vin: Optional[str] = None):
    target = gateway._pick_vin(vin)
    path = gateway.image_path(target) if target else None
    if not path:
        raise HTTPException(404, "no image yet")
    return FileResponse(path, media_type="image/png")


@app.get("/api/v2/charge_history")
def v2_charge_history(vin: Optional[str] = None, days: int = 90):
    target = gateway._pick_vin(vin)
    if not target:
        raise HTTPException(422, "specify vin")
    try:
        return gateway.charge_history(target, max(1, min(days, 365)))
    except Exception as error:
        raise HTTPException(503, str(error)[:200])


@app.get("/", response_class=HTMLResponse)
def page():
    with open(os.path.join(os.path.dirname(__file__), "web", "index.html"), encoding="utf-8") as fh:
        return fh.read()
