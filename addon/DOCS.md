# Leapmotor Gateway

This app is the only component that talks to the Leapmotor cloud. Home Assistant reads the car
and sends commands through the bundled integration. Every command passes an allowlist with the
levels free, approval and blocked.

## Setup

1. Start the app. It installs its integration and asks for one restart of Home Assistant.
2. Open the app, add your Leapmotor account and upload the app certificate (`app_cert.pem`,
   `app_key.pem`).
3. Turn on `cloud_enabled`. Keep `dry_run` on for the first tests.
4. Confirm the integration under Settings > Devices & services > Discovered.
5. Under Security choose the phone for approvals and give yourself the role full.
6. Try an approval, then turn `dry_run` off. Enter the vehicle PIN last.

Without an account, `demo_mode` shows a simulated car.

## Options

| Option | Default | Meaning |
|---|---|---|
| `cloud_enabled` | off | Log in to Leapmotor and poll the car |
| `dry_run` | on | Commands are checked and logged but not sent |
| `demo_mode` | off | Simulated car, no account needed, nothing is sent |
| `poll_active_s` | 60 | Poll interval while driving, charging, plugged in or unlocked |
| `poll_idle_min` | 15 | Poll interval while parked |
| `notify` | | Notify service for approvals, for example `notify.mobile_app_myphone` |
| `approval_timeout_s` | 120 | How long an approval stays valid |
| `log_level` | info | Log detail |

Command levels, roles, approval targets and the trip end delay are set in the app under
Security.

## Security

No exposed port, interface only via ingress and only for admins. The service runs as its own
user under its own AppArmor profile, without access to the Home Assistant configuration. Only
the install step at start runs as root, and AppArmor limits it to
`custom_components/leapmotor_gateway`. Every request is logged with its result in `commands.jsonl`.
Credentials are stored unencrypted in the app data folder, which is part of Home Assistant
backups.
