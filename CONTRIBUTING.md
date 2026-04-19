# Contributing

Thanks for contributing to AgenticQueue.

## Ground Rules

- Keep changes scoped to the ticket or issue you are solving.
- Prefer small PRs with clear verification notes.
- Do not commit secrets, credentials, or generated local state.
- Follow existing repo conventions before introducing new patterns.

## DCO Sign-Off

AgenticQueue uses the Developer Certificate of Origin instead of a CLA.

Every commit must be signed off:

```bash
git commit -s -m "Your message"
```

By signing off, you certify that you have the right to submit the work under the repository license.

## Pull Request Flow

1. Branch from `main`.
2. Make the minimum change that satisfies the ticket.
3. Run the relevant checks for the files you touched.
4. Push the branch and open a PR against `main`.
5. Include verification notes and any follow-up work that should be tracked separately.

## Learnings Guidance

Some tickets require a learnings entry at closeout. Add one when the work involved a failure, block, retry, or reviewer correction.

Use this structure in the ticket, PR, or requested closeout note:

```markdown
- Title:
- Type: pitfall | pattern | decision-followup | tooling | repo-behavior | user-preference | process-rule
- What happened:
- What we learned:
- Action rule:
- Applies when:
- Does not apply when:
- Evidence:
- Scope: task | project | global
- Confidence: tentative | confirmed | validated
```

Keep the learning specific enough that the next agent or contributor can act on it.
