"""HAOS test: the app on Home Assistant OS with the real Supervisor, as users install it.

Expects HAOS running in QEMU (see .github/workflows/haos.yml) with Home Assistant forwarded to
HAOS_URL and developer SSH on port 22222 (key HAOS_KEY). Builds an app repository from this
checkout, serves it to the VM over HTTP and checks: local build and install, security rating,
AppArmor profile, the integration copied into custom_components, discovery and confirmation,
entities and commands, ingress, the AppArmor limits of the start step, an app update without a
false restart notice, the dashboard and the Home Assistant log.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import urllib.request

import run                       # helpers of the Docker stack test
from run import check, wait

HERE = pathlib.Path(__file__).resolve().parent
WORK = HERE / "work-haos"
REPO = WORK / "repo"
URL = os.environ.get("HAOS_URL", "http://127.0.0.1:8080")
REPO_URL = os.environ.get("HAOS_REPO_URL", "git://10.0.2.2/repo")
KEY = os.environ.get("HAOS_KEY", "haos_key")
run.HA, run.WORK = URL, WORK


def ssh(command: str, timeout: int = 120) -> str:
    r = subprocess.run(["ssh", "-q", "-i", KEY, "-p", "22222", "-o", "StrictHostKeyChecking=no",
                        "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=10", "root@127.0.0.1", command],
                       capture_output=True, text=True, timeout=timeout)
    return (r.stdout + r.stderr).strip()


def supervisor(token: str, endpoint: str, method: str = "get", data: dict | None = None, timeout: int = 60):
    msg = {"type": "supervisor/api", "endpoint": endpoint, "method": method, "timeout": timeout}
    if data is not None:
        msg["data"] = data
    (r,) = asyncio.run(run.ws(token, msg))
    if not r.get("success"):
        raise RuntimeError(f"{endpoint}: {r.get('error')}")
    return r.get("result")


def app_repository(version: str):
    """An app repository with this checkout, built locally by the Supervisor (no image key)."""
    if not REPO.exists():
        REPO.mkdir(parents=True)
        (REPO / "repository.yaml").write_text("name: Stack test\nurl: https://example.invalid\nmaintainer: CI\n")
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=REPO, check=True)
    target = REPO / "leapmotor_gateway"
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(HERE.parent / "addon", target, ignore=shutil.ignore_patterns("tests", "__pycache__"))
    cfg = target / "config.yaml"
    lines = [l for l in cfg.read_text().splitlines() if not l.startswith("image:")]
    cfg.write_text("\n".join(f'version: "{version}"' if l.startswith("version:") else l for l in lines) + "\n")
    git = ["git", "-c", "user.name=ci", "-c", "user.email=ci@example.invalid", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "add", "-A"], cwd=REPO, check=True)
    subprocess.run(git + ["commit", "-q", "-m", version], cwd=REPO, check=True)


def onboarding() -> tuple[str, str, dict]:
    wait("Home Assistant", lambda: run.http("GET", "/api/onboarding")[0] == 200, timeout=900, every=10)
    import secrets
    password, client = secrets.token_urlsafe(16), URL + "/"
    _, user = run.http("POST", "/api/onboarding/users", data={"client_id": client, "name": "Owner",
                                                             "username": "owner", "password": password, "language": "en"})
    _, tok = run.http("POST", "/auth/token", data={"grant_type": "authorization_code", "code": user["auth_code"],
                                                   "client_id": client}, form=True)
    access = tok["access_token"]
    for step in ("core_config", "analytics"):
        run.http("POST", f"/api/onboarding/{step}", access, {})
    run.http("POST", "/api/onboarding/integration", access, {"client_id": client, "redirect_uri": client + "?auth_callback=1"})
    r = asyncio.run(run.ws(access, {"type": "config/core/update", "latitude": 52.5163, "longitude": 13.3777,
                                    "unit_system": "metric", "currency": "EUR", "time_zone": "Europe/Berlin",
                                    "country": "DE", "language": "en"},
                           {"type": "auth/long_lived_access_token", "client_name": "haos-test", "lifespan": 1},
                           {"type": "auth/current_user"}))
    check("onboarding", r[1].get("success"))
    return r[1]["result"], password, r[2]["result"]


def restart_core(token: str):
    try:
        supervisor(token, "/core/restart", "post", timeout=300)
    except Exception:
        pass                                  # the connection may drop while Core restarts
    time.sleep(20)
    wait("Home Assistant after restart", lambda: run.http("GET", "/api/", token)[0] == 200, timeout=600, every=10)


def main_steps():
    app_repository("0.0.1")
    token, password, owner = onboarding()

    supervisor(token, "/store/repositories", "post", {"repository": REPO_URL}, timeout=180)
    apps = supervisor(token, "/store/addons")
    apps = apps.get("addons") or apps.get("apps") or []
    slug = next(a["slug"] for a in apps if a.get("name") == "Leapmotor Gateway")
    check("app offered by the repository", bool(slug), slug)

    supervisor(token, f"/store/addons/{slug}/install", "post", timeout=1800)
    supervisor(token, f"/addons/{slug}/options", "post", {"options": {
        "cloud_enabled": False, "dry_run": True, "demo_mode": True, "poll_active_s": 60, "poll_idle_min": 15,
        "notify": "", "approval_timeout_s": 120, "log_level": "info"}})
    supervisor(token, f"/addons/{slug}/start", "post", timeout=300)
    info = wait("app started", lambda: (lambda i: i if i.get("state") == "started" else None)(
        supervisor(token, f"/addons/{slug}/info")), timeout=180, every=5)
    check("app started after local build", info.get("state") == "started", info.get("version"))
    check("security rating 8", info.get("rating") == 8, str(info.get("rating")))
    check("own AppArmor profile", info.get("apparmor") == "profile", str(info.get("apparmor")))
    copied = wait("integration copied", lambda: "manifest.json" in ssh(
        "ls /mnt/data/supervisor/homeassistant/custom_components/leapmotor_gateway"), timeout=120, every=5)
    check("integration copied into custom_components", bool(copied))

    # The container name changed with the rename from add-ons to apps, so look it up.
    container = ssh("docker ps --format '{{.Names}}' | grep leapmotor_gateway | head -1")
    check("app container found", bool(container), container)
    probe = (f"docker exec {container} sh -c '"
             "touch /homeassistant/.probe 2>/dev/null && echo outside=allowed || echo outside=denied; "
             "head -c1 /homeassistant/configuration.yaml >/dev/null 2>&1 && echo read=allowed || echo read=denied; "
             "touch /homeassistant/custom_components/leapmotor_gateway/.probe 2>/dev/null "
             "&& rm -f /homeassistant/custom_components/leapmotor_gateway/.probe && echo own=allowed || echo own=denied'")
    out = ssh(probe)
    check("AppArmor: no write outside the integration folder", "outside=denied" in out, out.replace("\n", " "))
    check("AppArmor: no read of configuration.yaml", "read=denied" in out)
    check("AppArmor: own folder writable", "own=allowed" in out)

    restart_core(token)
    flow = wait("discovered integration", lambda: next(
        (f for f in asyncio.run(run.ws(token, {"type": "config_entries/flow/progress"}))[0]["result"]
         if f["handler"] == "leapmotor_gateway"), None), timeout=300, every=10)
    _, done = run.http("POST", f"/api/config/config_entries/flow/{flow['flow_id']}", token, {})
    check("integration discovered and confirmed", done.get("type") == "create_entry", str(done.get("type")))

    ids = wait("entities with values", lambda: (lambda m: m if len(m) >= 80 and
               run.state(token, m.get("battery", "x")).get("state") == "68" else None)(run.entity_map(token)),
               timeout=240)
    check("entities", len(ids) >= 80, f"{len(ids)} entities")
    status, body = run.command(token, "lock")
    check("command lock accepted", status == 200 and body["service_response"].get("ok") is True, f"{status}")
    status, body = run.command(token, "unlock")
    check("unlock refused for role limited", status == 400 and "no permission" in json.dumps(body), f"{status}")

    entry = (info.get("ingress_entry") or info.get("ingress_url") or "").rstrip("/")
    session = supervisor(token, "/ingress/session", "post", {})["session"]
    req = urllib.request.Request(URL + entry + "/", headers={"Cookie": f"ingress_session={session}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        page = r.read().decode(errors="replace")
    check("app page through ingress", r.status == 200 and "<html" in page.lower(), f"{r.status}")
    req = urllib.request.Request(URL + entry + "/api/status", headers={"Cookie": f"ingress_session={session}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        status_json = json.loads(r.read())
    check("ingress passes the admin to the app", status_json.get("demo_mode") is True)

    app_repository("0.0.2")
    supervisor(token, "/store/reload", "post", timeout=180)
    supervisor(token, f"/store/addons/{slug}/update", "post", {"backup": False}, timeout=1800)
    info = wait("app updated", lambda: (lambda i: i if i.get("version") == "0.0.2" and i.get("state") == "started"
                                        else None)(supervisor(token, f"/addons/{slug}/info")), timeout=300, every=5)
    check("app update", bool(info))
    time.sleep(20)
    logs = ssh(f"docker logs {container} 2>&1 | tail -60")
    check("no false restart notice after the update", "installed, Home Assistant restart required" not in logs,
          logs.splitlines()[-1] if logs else "")

    asyncio.run(run.ws(token, {"type": "lovelace/dashboards/create", "url_path": "leapmotor-e2e", "title": "Leapmotor",
                               "mode": "storage", "show_in_sidebar": True, "require_admin": False},
                       {"type": "lovelace/config/save", "url_path": "leapmotor-e2e",
                        "config": {"strategy": {"type": "custom:leapmotor-gateway"}}}))
    run.dashboard(password)
    core_log = ssh("docker logs homeassistant 2>&1")
    bad = [l for l in core_log.splitlines() if "leapmotor_gateway" in l and ("ERROR" in l or "Traceback" in l)]
    check("no integration errors in the Home Assistant log", not bad, " | ".join(bad)[:400])


def main() -> int:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    # The Supervisor clones shallow, which plain HTTP cannot serve. git daemon can. 10.0.2.2 is the
    # runner as seen from the QEMU guest.
    server = subprocess.Popen(["git", "daemon", "--reuseaddr", "--export-all", f"--base-path={WORK}",
                               "--listen=0.0.0.0", "--port=9418", str(WORK)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        main_steps()
    except Exception as error:
        check("HAOS test ran to the end", False, f"{type(error).__name__}: {error}")
    finally:
        server.terminate()
        (WORK / "supervisor.log").write_text(ssh("docker logs hassio_supervisor 2>&1 | tail -300"))
        (WORK / "homeassistant.log").write_text(ssh("docker logs homeassistant 2>&1 | tail -300"))
        (WORK / "app.log").write_text(ssh("for c in $(docker ps -a --format '{{.Names}}' | grep leapmotor); "
                                          "do echo == $c; docker logs $c 2>&1 | tail -100; done"))
    failed = [n for n, ok, _ in run.results if not ok]
    print(f"\n{len(run.results) - len(failed)} passed, {len(failed)} failed" + (f": {', '.join(failed)}" if failed else ""))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("| Check | Result | Detail |\n|---|---|---|\n")
            fh.writelines(f"| {n} | {'pass' if ok else 'FAIL'} | {d.replace('|', '/')[:120]} |\n" for n, ok, d in run.results)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
