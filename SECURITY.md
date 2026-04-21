# Security Policy

AgenticQueue takes security reports seriously. Please report suspected
vulnerabilities privately and do not open a public GitHub issue for them.

## Supported Versions

AgenticQueue is still pre-1.0. Until a stable release exists, only the current
`main` branch is considered supported for coordinated security fixes.

| Version | Supported |
| --- | --- |
| `main` (current pre-1.0 development branch) | Yes |
| Older unpublished snapshots, stale clones, and superseded commits | No |

## Reporting a Vulnerability

Use one of these private channels:

1. Preferred: GitHub Private Vulnerability Reporting for this repository.
2. Fallback: email `security@agenticqueue.ai`.

Please include:

- A clear description of the issue and impacted component.
- Reproduction steps or a proof of concept when available.
- The affected commit, tag, or deployment context.
- Any impact analysis, exploitability notes, or mitigations you already tested.

Do not include secrets, production credentials, or personal data unless it is
strictly necessary to reproduce the issue.

## Encryption

PGP key status: a dedicated public key is not published yet. If you need
encrypted coordination before one is posted, open a private vulnerability report
or email `security@agenticqueue.ai` and request an encrypted follow-up channel.

## Response SLO

- We aim to acknowledge new reports within 5 business days.
- We may ask follow-up questions to confirm scope, impact, and reproduction.
- We will keep reporters informed as triage and remediation progress.

## Coordinated Disclosure

AgenticQueue follows a 90 day coordinated disclosure window by default. We may
shorten or extend that window when user safety, active exploitation, legal
constraints, or upstream dependency timelines require it, but we will try to
coordinate any change with the reporter.

Please avoid public disclosure until:

- A fix or mitigation is available, or
- We mutually agree that earlier disclosure is necessary for user safety.

## CVE Handling

When a report is confirmed, we will determine whether the issue warrants a CVE.
If it does, AgenticQueue will request or coordinate CVE assignment through the
appropriate channel before or alongside the public fix advisory. Security fixes,
severity notes, and remediation guidance will be documented in the release notes
or security advisory that ships the patch.

## Disclosure Process

1. Private report received through GitHub Private Vulnerability Reporting or
   `security@agenticqueue.ai`.
2. Triage confirms validity, affected scope, and immediate mitigation needs.
3. A fix is prepared and validated privately.
4. A coordinated public advisory, release note, or equivalent disclosure is
   published once the fix or mitigation is ready.

## Hall of Fame

We will recognize reporters here after a coordinated disclosure is complete,
unless they prefer to remain anonymous.

No public acknowledgements yet.

## Threat Model

See [docs/security/threat-model.md](docs/security/threat-model.md) for the
living STRIDE threat model, trust boundaries, shipped mitigations, and known
gaps that still need hardening before public launch.

## Scope

This policy applies to this repository and the official AgenticQueue
project-maintained infrastructure.
