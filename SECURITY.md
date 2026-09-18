# Security policy

SkullMaster iQ is a **local-first** application: it runs on your machine, binds
to loopback by default, and makes no external calls at runtime except the URLs
you explicitly add and one-time model downloads. There is no telemetry.

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Instead:

- Use GitHub's **private vulnerability reporting** (Security → Advisories →
  "Report a vulnerability") on this repository, or
- Contact the maintainer directly.

Include: affected version/commit, a description, and a minimal reproduction.
We aim to acknowledge reports promptly and will coordinate a fix and disclosure.

## Scope and expectations

The threat model is a single owner on a trusted workstation. Relevant surfaces
include: authentication/sessions, the route guard, path handling for uploads and
media, prompt injection from untrusted sources, archive handling, and the
optional OCR/vision/ffmpeg subprocess paths. These are reviewed in
[`docs/security.md`](docs/security.md).

Out of scope for this project: multi-user/hosted deployments, and any network
exposure beyond loopback (binding elsewhere logs a warning that the owner
password is the only guard).
