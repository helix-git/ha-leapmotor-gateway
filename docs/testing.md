# Testing

Every pull request and every push to `main` runs all of the following. Dependabot proposes updates
after a waiting time of 7 days (14 for major versions, security updates at once). They are merged by
hand after a review and only when every check is green. Tests show that an update works, not that
it is benign. The results of each run are in the run summary, screenshots and
logs are attached as artifacts.

| Workflow | What it runs | When |
|---|---|---|
| Tests | Unit tests of the app (`addon/tests`), the integration (`tests_integration`, pytest-homeassistant-custom-component) and the dashboard strategy in a browser (`tests_frontend`). App linter and hassfest. | pull request, push |
| Stack | A real Home Assistant in Docker with the app image in demo mode (`e2e/run.py`). | pull request, push, nightly also the Home Assistant beta |
| HAOS | Home Assistant OS in QEMU with KVM on the runner, the app installed through the real Supervisor (`e2e/haos.py`). | pull request, push, nightly also the newest HAOS beta |
| CodeQL, Scorecard | Static analysis and supply chain checks. | push, weekly |
| Nightly review | Claude reviews open Dependabot pull requests for supply chain risks and comments a recommendation. It merges nothing. | nightly |

Both end to end tests start from nothing: a fresh Home Assistant, onboarding through its API and a
first install of the app.

## Stack test

- Integration set up through the real config flow
- All entities present with the expected values of the simulated car
- A command reaches the simulated car and the new state arrives in Home Assistant
- Role limited cannot unlock, a second refresh within a minute is refused
- The app page only for admins, the integration API only from Home Assistant with its token
- Dashboard strategy renders all views in a browser without errors
- No errors of the integration in the Home Assistant log

## HAOS test

- The app is built from the commit by the Supervisor and starts
- Security rating 8, own AppArmor profile
- The start step copies the integration into `custom_components`
- AppArmor: writing outside the integration folder and reading `configuration.yaml` are denied,
  the integration folder is writable (checked with `docker exec`, which runs in the app profile)
- After the restart Home Assistant discovers the integration and it is confirmed
- Entities, commands and roles as in the stack test
- The app page through a real ingress session, with the admin passed on to the app
- An app update without a false restart notice
- Dashboard in a browser, no errors in the Home Assistant log

## Running locally

```sh
docker build -t lmgw-e2e-gateway addon
pip install --require-hashes -r .github/requirements/e2e.txt && python -m playwright install chromium
python e2e/run.py
```

The HAOS test needs QEMU with KVM, see `.github/workflows/haos.yml` for the steps.
