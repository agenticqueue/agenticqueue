# Cloud OIDC for future GitHub Actions integrations

AgenticQueue should authenticate from GitHub Actions to cloud providers with
OpenID Connect (OIDC) and short-lived tokens. Do not add long-lived cloud keys
to GitHub Actions secrets for deploy, release, or maintenance jobs.

## Baseline rules

- Keep workflow-level permissions read-only with `permissions: read-all` or
  `contents: read`.
- Grant `id-token: write` only on the job that exchanges GitHub's OIDC token
  for a cloud credential.
- Keep any additional write permissions job-scoped and minimal.
- Scope the cloud trust policy to the canonical production ref:
  `repo:agenticqueue/agenticqueue:ref:refs/heads/main`.
- Prefer provider-issued short-lived credentials over static API tokens.

## Workflow pattern

```yaml
permissions: read-all

jobs:
  deploy:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      id-token: write
    steps:
      - uses: actions/checkout@v4
      - name: Exchange GitHub OIDC token for cloud credentials
        run: ./scripts/login-to-cloud.sh
```

## Trust policy shape

Use the GitHub OIDC issuer and restrict the subject to `main`.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::<account-id>:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
          "token.actions.githubusercontent.com:sub": "repo:agenticqueue/agenticqueue:ref:refs/heads/main"
        }
      }
    }
  ]
}
```

For GCP, Azure, or Cloudflare, keep the same issuer and subject restriction.
Only the provider-specific audience, workload identity pool, or service
principal wiring should change.

## Review checklist

- No cloud access keys, client secrets, or long-lived tokens in repository or
  environment secrets.
- Workflow permissions are read-only at the top level.
- Only the cloud-auth job has `id-token: write`.
- The trust policy is pinned to
  `repo:agenticqueue/agenticqueue:ref:refs/heads/main`.
- Provider-side role or service account grants only the exact deploy actions
  required by that workflow.
