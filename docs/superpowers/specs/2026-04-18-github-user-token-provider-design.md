# GitHub User-Token Provider Design

## Summary

This design introduces a `github` provider for AtlasClaw with one strict rule:

- every user must access GitHub with their own personal token

The provider will support a narrow read-oriented capability set for GitHub pull request checks,
workflow runs, failed workflow logs, and selected REST API queries. It will target `github.com`
only in phase 1 and will not support shared provider credentials.

The implementation should stay primarily inside `atlasclaw-providers`. AtlasClaw core should only
provide the minimum configuration surface needed to let users manage GitHub tokens in the same
provider-settings experience currently used for SmartCMP.

---

## Goals

- add a `github` provider that uses GitHub REST API instead of `gh`
- require each AtlasClaw user to provide and use their own GitHub token
- support `github.com` only in phase 1
- expose the GitHub provider in the providers page with the same user-facing configuration pattern
  used for SmartCMP
- keep AtlasClaw core changes limited to provider configuration and provider-settings UI support
- keep the agent runtime, routing flow, tool loop, and SSE flow unchanged

## Non-Goals

- no shared GitHub token configured in `atlasclaw.json`
- no GitHub Enterprise Server support in phase 1
- no OAuth browser authorization flow
- no write operations such as commenting, approving, merging, rerunning workflows, or updating PRs
- no repository search UX beyond listing recent accessible repositories
- no `gh` CLI dependency in provider execution

---

## Product Rules

### Authentication Model

The GitHub provider uses user-scoped credentials only.

Allowed:

- user-supplied GitHub personal access token stored in the authenticated user's provider settings

Not allowed:

- shared `token` configured under `service_providers.github.default`
- fallback to server-level GitHub credentials
- fallback to `deps.user_token`

The token is expected to be a GitHub PAT, with fine-grained PAT recommended.

### Fixed Provider Template

AtlasClaw system configuration must define a GitHub template instance:

```json
{
  "service_providers": {
    "github": {
      "default": {
        "base_url": "https://api.github.com",
        "auth_type": "user_token"
      }
    }
  }
}
```

This template exists only to:

- make the provider visible to AtlasClaw
- allow user-scoped provider settings validation
- supply fixed non-sensitive defaults

The template must not carry a shared credential.

### User Provider Settings

Each user stores their own GitHub credential under the existing
`/api/users/me/provider-settings` flow, bound to `github/default`.

Illustrative user-owned shape:

```json
{
  "providers": {
    "github": {
      "default": {
        "configured": true,
        "config": {
          "auth_type": "user_token",
          "user_token": "github_pat_xxx"
        },
        "updated_at": "2026-04-18T10:15:00Z"
      }
    }
  }
}
```

The user-editable field surface for GitHub is intentionally minimal:

- `user_token`

The following values are fixed and hidden from the user:

- `base_url=https://api.github.com`
- `auth_type=user_token`

---

## Information Architecture

### Providers Page Behavior

The GitHub provider must appear in the providers page alongside SmartCMP and other supported
providers.

The GitHub card should follow the same interaction model currently used for SmartCMP:

- visible as a provider card in the provider inventory
- selectable in the compact provider band
- configurable through the existing user-provider modal flow

The GitHub-specific modal should show only:

- `User Token`

The modal should not expose:

- `base_url`
- `auth_type`
- shared token options

### Unconfigured State

If the user has not configured `github/default`, the provider remains visible but unavailable.

The UI should clearly indicate:

- status is not configured
- access depends on the user's own GitHub token
- the next step is to configure the token

### Configured State

If the user has configured a token:

- the provider is available in runtime
- sensitive fields remain masked in the UI
- the provider can be selected by runtime and provider-bound skills

---

## Runtime Contract

### Provider Execution

The GitHub provider implementation lives in:

- `atlasclaw-providers/providers/github`

Provider skills read the resolved user-scoped provider config from:

- `SkillDeps.extra.provider_instances.github.default`

The provider code must not:

- read a shared token from `atlasclaw.json`
- rely on `gh auth login`
- rely on OS-level `gh` authentication state

### HTTP Contract

All GitHub calls use GitHub REST API with:

- `Authorization: Bearer <user_token>`
- `Accept: application/vnd.github+json`
- `X-GitHub-Api-Version: 2022-11-28`

The fixed API base is:

- `https://api.github.com`

### Repository Selection

The provider does not store a default repository in phase 1.

Each execution that needs a repository must work from a repository chosen in the current
interaction.

The provider should support a repository-list capability that returns a recent accessible list.
Higher-level user interaction can then ask the user to choose one `owner/repo`.

This design intentionally avoids:

- hard-coding repository defaults
- silently picking a repository without user choice

---

## Phase 1 Skill Set

The provider should implement the following executable skills:

### 1. `github_list_repos`

Purpose:

- list recent repositories accessible with the current user's token

Output:

- compact list of `owner/repo`
- enough metadata to let the user choose a repository

### 2. `github_pr_checks`

Equivalent user intent:

- check CI status on a PR

Example target behavior:

```bash
gh pr checks 55 --repo owner/repo
```

Inputs:

- `repo`
- `pr_number`

Output:

- check names
- states and statuses
- URLs when available

### 3. `github_run_list`

Equivalent user intent:

- list recent workflow runs

Example target behavior:

```bash
gh run list --repo owner/repo --limit 10
```

Inputs:

- `repo`
- `limit`

### 4. `github_run_view`

Equivalent user intent:

- inspect one run and identify failed jobs or steps

Example target behavior:

```bash
gh run view <run-id> --repo owner/repo
```

Inputs:

- `repo`
- `run_id`

### 5. `github_run_failed_logs`

Equivalent user intent:

- retrieve failed-step logs only

Example target behavior:

```bash
gh run view <run-id> --repo owner/repo --log-failed
```

Inputs:

- `repo`
- `run_id`

### 6. `github_api_query`

Equivalent user intent:

- issue a selected GitHub REST query not covered by the other skills

Example target behavior:

```bash
gh api repos/owner/repo/pulls/55
```

Inputs:

- `repo`
- `path`

Phase 1 should keep this capability read-only.

---

## AtlasClaw Core Changes

This design keeps AtlasClaw core changes minimal and configuration-focused.

### Required Core Changes

1. Add `github` provider schema definition so the providers page can render a GitHub card.
2. Expose a user-provider modal with only `user_token` as the editable field.
3. Ensure schema defaults provide:
   - `base_url=https://api.github.com`
   - `auth_type=user_token`

### Explicitly Out Of Scope For Core

The following must not be changed as part of this work:

- agent main loop
- LLM-first runtime routing
- tool gating logic
- tool loop sequencing
- SSE event model
- provider runtime selection rules outside normal provider visibility

---

## Provider Package Structure

Recommended layout:

```text
atlasclaw-providers/providers/github/
├── PROVIDER.md
├── README.md
└── skills/
   ├── github-repo/
   │  ├── SKILL.md
   │  └── scripts/
   │     ├── handler.py
   │     └── _github_client.py
   ├── github-pr-checks/
   ├── github-run-list/
   ├── github-run-view/
   ├── github-run-failed-logs/
   └── github-api-query/
```

The provider should follow the same conventions already used by provider packages such as Jira:

- `PROVIDER.md` defines provider identity and LLM context
- each skill has a `SKILL.md`
- implementation code stays under `scripts/`

---

## Error Handling

The provider must produce clear runtime errors for:

- missing GitHub token
- invalid token
- unauthorized repository access
- missing repository selection
- missing PR or run identifiers
- GitHub rate limiting

User-facing error copy should be explicit about configuration versus permission problems.

Examples:

- `GitHub provider is not configured for this account. Configure your personal GitHub token first.`
- `GitHub token is invalid or expired. Update your personal GitHub token.`
- `Repository 'owner/repo' is not accessible with your GitHub token.`

---

## Security Rules

- Never log raw GitHub tokens.
- Never expose GitHub tokens through provider listing endpoints.
- Never allow shared GitHub credentials in phase 1.
- Never persist workflow logs or API responses with embedded secrets unless existing redaction rules
  already apply.
- Use the user's GitHub permissions as the hard access boundary; AtlasClaw must not widen access.

---

## Testing Requirements

### Provider Tests

The provider package should include tests for:

- token-required behavior
- repository listing with mocked GitHub responses
- PR checks parsing
- run list parsing
- run detail parsing
- failed log retrieval
- API query behavior
- 401 and 403 responses

### AtlasClaw Tests

AtlasClaw-side tests should remain narrow:

- provider schema exposure for GitHub
- user provider setting validation against `github/default`
- providers page rendering of the GitHub card and hidden-field behavior

No new runtime-routing tests are required unless this work accidentally expands beyond the agreed
boundary.

---

## Open Questions Resolved

The following product choices are fixed for phase 1:

- repository is selected per interaction, not saved as a default
- `github.com` only
- REST API only, not `gh`
- user token only
- providers page card should behave like SmartCMP's configuration experience

---

## Final Recommendation

Implement the GitHub provider as a provider-package-first feature with only the minimum AtlasClaw
core support needed to surface GitHub in the user provider-settings UI.

This keeps the business capability where it belongs, avoids unnecessary changes to agent runtime,
and still gives users a complete configuration path consistent with existing provider UX.
