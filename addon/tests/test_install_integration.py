"""The app installs its integration by itself (no HACS): copy only on changes, then notify once."""
import json
import os
import pathlib
import subprocess

SCRIPT = pathlib.Path(__file__).parents[1] / "install_integration.sh"
INTEGRATION = pathlib.Path(__file__).parents[1] / "integration" / "leapmotor_gateway"


def run(tmp_path, target, path=None):
    notes = tmp_path / "notes.txt"
    notifier = tmp_path / "notify.sh"
    notifier.write_text(f'#!/usr/bin/env bash\necho "$1|$2" >> {notes}\n')
    notifier.chmod(0o755)
    r = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True,
                       env={**os.environ, "SOURCE": str(INTEGRATION), "TARGET": str(target), "NOTIFY": str(notifier),
                            **({"PATH": path} if path else {})})
    assert r.returncode == 0, r.stderr
    return notes.read_text().splitlines() if notes.exists() else []


def test_install_once_then_nothing(tmp_path):
    ha = tmp_path / "homeassistant"
    ha.mkdir()
    target = ha / "custom_components" / "leapmotor_gateway"
    version = json.loads((INTEGRATION / "manifest.json").read_text())["version"]
    message = (f"Leapmotor Gateway|Integration {version} installed. Restart Home Assistant to load it. After the first "
               "installation, add it under Settings > Devices & services, where it appears as discovered.")
    assert run(tmp_path, target) == [message]
    assert (target / "manifest.json").read_text() == (INTEGRATION / "manifest.json").read_text()
    assert not (target / "__pycache__").exists()          # not copied
    (target / "__pycache__").mkdir()                       # created by Home Assistant, not a change
    assert run(tmp_path, target) == [message]
    (target / "manifest.json").write_text("{}\n")          # outdated file: replaced and notified
    assert len(run(tmp_path, target)) == 2
    assert (target / "manifest.json").read_text() == (INTEGRATION / "manifest.json").read_text()
    assert not (target.parent / "leapmotor_gateway.new").exists()


def test_without_home_assistant_folder_nothing(tmp_path):
    assert run(tmp_path, tmp_path / "missing" / "custom_components" / "leapmotor_gateway") == []


def test_without_gnu_diff(tmp_path):
    """The image has the BusyBox diff (no -x). A diff that always fails must change nothing."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "diff").write_text("#!/bin/sh\necho 'diff: unrecognized option: x' >&2\nexit 2\n")
    (bin_ / "diff").chmod(0o755)
    ha = tmp_path / "homeassistant"
    ha.mkdir()
    target = ha / "custom_components" / "leapmotor_gateway"
    path = f"{bin_}:{os.environ['PATH']}"
    assert len(run(tmp_path, target, path)) == 1
    assert len(run(tmp_path, target, path)) == 1           # second start: nothing new


def test_apparmor_allows_exactly_the_folders_the_script_writes():
    """The install step runs as root. AppArmor limits it to the integration folder and the temporary
    copy next to it, so the suffix in the script and in the profile must match."""
    import re
    script = SCRIPT.read_text()
    profile = (SCRIPT.parent / "apparmor.txt").read_text()
    suffix = re.search(r'"\$TARGET(\.\w+)"', script).group(1)
    assert f"/homeassistant/custom_components/leapmotor_gateway{{,{suffix}}}/** rwl," in profile
    assert not re.search(r"^\s*file,", profile, re.M)                 # no blanket file access any more
    assert "/homeassistant/** " not in profile
