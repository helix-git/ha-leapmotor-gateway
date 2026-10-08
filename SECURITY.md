# Security policy

This is a hobby project, provided as is. It can send remote commands to a real car, so
vulnerabilities are taken seriously and fixed as soon as possible, on a best effort basis.
Please report them privately.

## Reporting a vulnerability

Report it through GitHub private vulnerability reporting:
<https://github.com/helix-git/ha-leapmotor-gateway/security/advisories/new>

Please include the version, the steps to reproduce and the impact. Do not open a public issue
for security problems.

## What happens next

- Reports are answered and fixed as soon as possible. There are no guaranteed response times.
- Coordinated disclosure: the advisory is published together with the fix, with credit to the
  reporter unless they prefer otherwise.

## Supported versions

Only the latest release receives security fixes.

## Scope

In scope: the app (`addon/`), the integration (`addon/integration/leapmotor_gateway`), the
dashboard strategy and the release workflow with its images.

Out of scope: the Leapmotor cloud and the
[leapmotor-api](https://github.com/markoceri/leapmotor-api) library. Please report those to their
owners.
