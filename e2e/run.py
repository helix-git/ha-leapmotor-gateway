"""Stack test: build nothing, run everything. Starts Home Assistant and the app (demo mode) with
e2e/compose.yaml, sets up the integration through the real config flow and checks entities,
commands, permissions, the app API and the dashboard in a browser.

Needs: docker compose, the app image (GATEWAY_IMAGE), python packages from
.github/requirements/e2e.txt. Exit code 0 when every check passed. Artifacts in e2e/work/.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import websockets

HERE = pathlib.Path(__file__).resolve().parent
WORK = HERE / "work"
HA = f"http://127.0.0.1:{os.environ.get('E2E_PORT', '8123')}"
VIN = "LFZ00000000000T03"
INGRESS_IP, GATEWAY = "172.31.98.30", "http://172.31.98.20:8789"
COMPOSE = ["docker", "compose", "-f", str(HERE / "compose.yaml")]
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f": {detail}" if detail else ""), flush=True)
    return bool(ok)


def wait(what: str, probe, timeout: float = 180, every: float = 3):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            last = probe()
            if last:
                return last
        except Exception as error:          # still starting
            last = error
        time.sleep(every)
    raise TimeoutError(f"{what}: {last}")


def http(method: str, path: str, token: str | None = None, data=None, form: bool = False):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    body = None
    if data is not None:
        if form:
            body, headers["Content-Type"] = urllib.parse.urlencode(data).encode(), "application/x-www-form-urlencoded"
        else:
            body, headers["Content-Type"] = json.dumps(data).encode(), "application/json"
    req = urllib.request.Request(HA + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            return error.code, json.loads(raw)
        except ValueError:
            return error.code, raw.decode(errors="replace")


async def ws(token: str, *commands: dict) -> list:
    out = []
    async with websockets.connect(HA.replace("http", "ws") + "/api/websocket", max_size=None) as s:
        await s.recv()
        await s.send(json.dumps({"type": "auth", "access_token": token}))
        assert json.loads(await s.recv())["type"] == "auth_ok"
        for i, cmd in enumerate(commands, 1):
            await s.send(json.dumps({"id": i, **cmd}))
            while True:
                r = json.loads(await s.recv())
                if r.get("id") == i and r.get("type") == "result":
                    out.append(r)
                    break
    return out


def in_network(code: str) -> str:
    """Run Python inside the app image at the ingress address, as the Supervisor proxy would."""
    image = os.environ.get("GATEWAY_IMAGE", "lmgw-e2e-gateway")
    r = subprocess.run(["docker", "run", "--rm", "--network", "lmgw-e2e_e2e", "--ip", INGRESS_IP,
                        "--entrypoint", "python3", image, "-c", code], capture_output=True, text=True, timeout=60)
    return (r.stdout + r.stderr).strip()


def setup() -> tuple[str, str, dict]:
    subprocess.run(COMPOSE + ["--profile", "gateway", "down", "-v"], capture_output=True)
    if WORK.exists():                     # Home Assistant writes as root, so clean up from a container
        subprocess.run(["docker", "run", "--rm", "-v", f"{WORK}:/w", "--entrypoint", "sh",
                        os.environ.get("GATEWAY_IMAGE", "lmgw-e2e-gateway"), "-c", "rm -rf /w/* /w/.[!.]*"],
                       capture_output=True)
        shutil.rmtree(WORK, ignore_errors=True)
    (WORK / "config" / "custom_components").mkdir(parents=True)
    (WORK / "data").mkdir()
    shutil.copytree(HERE.parent / "addon" / "integration" / "leapmotor_gateway",
                    WORK / "config" / "custom_components" / "leapmotor_gateway")
    (WORK / "gateway.env").write_text("")
    subprocess.run(COMPOSE + ["up", "-d", "homeassistant"], check=True)
    wait("Home Assistant onboarding", lambda: http("GET", "/api/onboarding")[0] == 200, timeout=300)

    password = secrets.token_urlsafe(16)
    client = HA + "/"
    _, user = http("POST", "/api/onboarding/users", data={"client_id": client, "name": "Owner", "username": "owner",
                                                         "password": password, "language": "en"})
    _, tok = http("POST", "/auth/token", data={"grant_type": "authorization_code", "code": user["auth_code"],
                                               "client_id": client}, form=True)
    access = tok["access_token"]
    for step in ("core_config", "analytics"):
        http("POST", f"/api/onboarding/{step}", access, {})
    http("POST", "/api/onboarding/integration", access, {"client_id": client, "redirect_uri": client + "?auth_callback=1"})
    r = asyncio.run(ws(access, {"type": "config/core/update", "latitude": 52.5163, "longitude": 13.3777,
                                "unit_system": "metric", "currency": "EUR", "time_zone": "Europe/Berlin",
                                "country": "DE", "language": "en"},
                       {"type": "auth/long_lived_access_token", "client_name": "e2e", "lifespan": 1},
                       {"type": "auth/current_user"}))
    long_lived, owner = r[1]["result"], r[2]["result"]
    check("onboarding", bool(long_lived and owner.get("is_admin")))

    (WORK / "gateway.env").write_text(f"SUPERVISOR_TOKEN={long_lived}\n")
    os.chmod(WORK / "gateway.env", 0o600)
    subprocess.run(COMPOSE + ["--profile", "gateway", "up", "-d", "gateway"], check=True)
    gw_token = wait("integration token", lambda: subprocess.run(
        ["docker", "exec", "lmgw-e2e-gateway-1", "cat", "/data/integration_token"],
        capture_output=True, text=True).stdout.strip(), timeout=60)

    # The token is written before the web server listens. Wait until it answers, as seen from the network.
    wait("app health", lambda: '"ok": true' in in_network(
        f"import urllib.request; print(urllib.request.urlopen('{GATEWAY}/health', timeout=5).read().decode())"
    ).replace('"ok":true', '"ok": true'), timeout=120, every=3)

    def flow():
        _, f = http("POST", "/api/config/config_entries/flow", long_lived, {"handler": "leapmotor_gateway"})
        _, d = http("POST", f"/api/config/config_entries/flow/{f['flow_id']}", long_lived,
                    {"host": "172.31.98.20", "port": 8789, "token": gw_token})
        if d.get("type") != "create_entry":
            http("DELETE", f"/api/config/config_entries/flow/{f['flow_id']}", long_lived)
            print("config flow:", d.get("errors"), flush=True)
            return None
        return d
    done = None
    try:
        done = wait("config flow", flow, timeout=90, every=5)
    finally:
        check("config flow", bool(done), "create_entry" if done else "no entry")
    return long_lived, password, owner


def entity_map(token: str) -> dict:
    (reg,) = asyncio.run(ws(token, {"type": "config/entity_registry/list"}))
    return {e["unique_id"][len(VIN) + 1:]: e["entity_id"] for e in reg["result"]
            if e["platform"] == "leapmotor_gateway" and e["unique_id"].startswith(VIN + "_")}


def state(token: str, entity_id: str) -> dict:
    return http("GET", f"/api/states/{entity_id}", token)[1] or {}


def command(token: str, action: str, params: dict | None = None):
    return http("POST", "/api/services/leapmotor_gateway/command?return_response", token,
                {"action": action, **({"params": params} if params else {})})


def checks(token: str, password: str, owner: dict):
    ids = wait("entities with values", lambda: (lambda m: m if len(m) >= 80 and
               state(token, m.get("battery", "x")).get("state") == "68" else None)(entity_map(token)), timeout=180)
    check("entities", len(ids) >= 80, f"{len(ids)} entities")
    expected = {"battery": "68", "lock": "locked", "state": "parked", "trips": "8", "plate": "B-LM 303E",
                "odometer": "4312", "location": "home"}
    for key, value in expected.items():
        got = state(token, ids.get(key, "missing")).get("state")
        check(f"state {key}", got == value, f"{got!r}")
    trips = state(token, ids["trips"]).get("attributes", {}).get("items") or []
    check("trip list", len(trips) == 8 and {"start", "km", "kwh"} <= set(trips[0]), f"{len(trips)} items")
    unavailable = [k for k, e in ids.items() if state(token, e).get("state") == "unavailable" and k != "image"]
    check("nothing unavailable", not unavailable, ", ".join(unavailable))

    status, body = command(token, "climate_on", {"mode": "hot", "temperature": 22, "fan_speed": 3, "recirculation": False})
    check("command climate_on accepted", status == 200 and (body or {}).get("service_response", {}).get("ok") is True,
          f"{status} {str(body)[:160]}")
    try:
        wait("climate on", lambda: state(token, ids["climate"]).get("state") == "on", timeout=120, every=5)
        check("command reaches the car", True)
    except TimeoutError as error:
        check("command reaches the car", False, str(error))
    status, body = command(token, "unlock")
    check("unlock refused for role limited", status == 400 and "no permission" in json.dumps(body), f"{status} {str(body)[:160]}")
    status, body = command(token, "refresh")
    status2, body2 = command(token, "refresh")
    check("refresh rate limit", status2 == 400 and "too fast" in json.dumps(body2), f"{status}/{status2}")

    probe = ("import json, urllib.request\n"
             "def get(p, h=None):\n"
             "    try:\n"
             f"        r = urllib.request.urlopen(urllib.request.Request('{GATEWAY}' + p, headers=h or {{}}), timeout=10)\n"
             "        return r.status, json.loads(r.read())\n"
             "    except urllib.error.HTTPError as e:\n"
             "        return e.code, None\n"
             f"print(json.dumps([get('/api/status', {{'X-Remote-User-Id': '{owner['id']}'}}), get('/api/status', {{'X-Remote-User-Id': 'nobody'}}), get('/api/v2/vehicles')]))")
    out = in_network(probe)
    try:
        (s1, st), (s2, _), (s3, _) = json.loads(out.splitlines()[-1])
        check("app page for admins", s1 == 200 and st.get("demo_mode") is True, str(s1))
        check("app page refused for non admins", s2 in (401, 403), str(s2))
        check("integration API needs core address and token", s3 in (401, 403), str(s3))
    except Exception as error:
        check("app API probes", False, f"{error}: {out[-300:]}")

    asyncio.run(ws(token, {"type": "lovelace/dashboards/create", "url_path": "leapmotor-e2e", "title": "Leapmotor",
                           "mode": "storage", "show_in_sidebar": True, "require_admin": False},
                   {"type": "lovelace/config/save", "url_path": "leapmotor-e2e",
                    "config": {"strategy": {"type": "custom:leapmotor-gateway"}}}))
    dashboard(password)


def dashboard(password: str):
    from playwright.sync_api import sync_playwright
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(HA + "/")
        # Wait for the login form, then for the stored tokens: the URL check alone raced the redirect.
        page.wait_for_selector("input[name=username]", timeout=60000)
        page.fill("input[name=username]", "owner")
        page.fill("input[name=password]", password)
        page.keyboard.press("Enter")
        page.wait_for_function("() => !!localStorage.getItem('hassTokens')", timeout=60000)
        for view, text in (("vehicle-1-trip-computer", "Trip computer"), ("vehicle-1-status", "Leapmotor T03"),
                           ("vehicle-1-climate", "Climate")):
            page.goto(f"{HA}/leapmotor-e2e/{view}")
            page.wait_for_timeout(5000)
            page.screenshot(path=str(WORK / f"{view}.png"))
            check(f"dashboard {view}", page.get_by_text(text).count() > 0, text)
        browser.close()
    check("no browser errors", not errors, "; ".join(errors)[:300])


def logs():
    for service in ("homeassistant", "gateway"):
        r = subprocess.run(COMPOSE + ["--profile", "gateway", "logs", "--no-color", service], capture_output=True, text=True)
        (WORK / f"{service}.log").write_text(r.stdout + r.stderr)
    ha = (WORK / "homeassistant.log").read_text()
    bad = [line for line in ha.splitlines() if "leapmotor_gateway" in line and ("ERROR" in line or "Traceback" in line)]
    check("no integration errors in the Home Assistant log", not bad, " | ".join(bad)[:400])


def main() -> int:
    try:
        token, password, owner = setup()
        checks(token, password, owner)
    except Exception as error:
        check("stack test ran to the end", False, f"{type(error).__name__}: {error}")
    finally:
        logs()
        if os.environ.get("E2E_KEEP") != "1":
            subprocess.run(COMPOSE + ["--profile", "gateway", "down", "-v"], capture_output=True)
    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed" + (f": {', '.join(failed)}" if failed else ""))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("| Check | Result | Detail |\n|---|---|---|\n")
            fh.writelines(f"| {n} | {'pass' if ok else 'FAIL'} | {d.replace('|', '/')[:120]} |\n" for n, ok, d in results)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
