# Provider-Driven Web Search and Grounding Design

## 1. Overview

AtlasClaw currently exposes a `web_search` tool, but the implementation is still a lightweight HTML-scraping adapter over public search result pages. That is enough for basic experiments, but it is not strong enough for an enterprise platform that must be more complete and more reliable than OpenClaw.

This spec defines the next-stage search architecture for AtlasClaw:

1. **Provider-driven web search runtime**
   - A unified runtime that chooses among configured search providers rather than hard-coding scraped search pages as the primary path.
2. **Grounding provider abstraction**
   - A richer provider surface for providers that return evidence-backed summaries, citations, or grounded search output rather than plain result lists.
3. **Query expansion and source-prioritization**
   - Controlled multi-step retrieval behavior when an initial query fails or returns weak coverage.
4. **Runtime integration**
   - Clean integration with Tool Necessity Gate, Hook Runtime, browser escalation, and future Full Context Engine work.

The design aligns with OpenClaw's stronger provider-driven search direction while explicitly strengthening AtlasClaw for enterprise use:
- richer source governance,
- more explicit fallback policy,
- stronger observability,
- and cleaner integration with runtime policy enforcement.

---

## 2. Problem Statement

Today AtlasClaw `web_search`:
- defaults to Bing,
- can fall back to DuckDuckGo and Google,
- fetches public HTML result pages through `httpx`,
- and parses them with provider-specific regex patterns.

That creates several problems:

1. **Result instability**
- Search page HTML is not a stable API contract.

2. **Weak grounding**
- Search output is only a list of parsed links/snippets, not a first-class grounded evidence response.

3. **Poor enterprise control**
- No provider registry, provider capability model, or clean prioritization policy.

4. **Weak query recovery**
- When the first query fails, AtlasClaw lacks a structured query-expansion strategy.

5. **No official-source priority policy**
- Official docs, GitHub, or trusted sites are not strongly preferred by runtime policy.

6. **Insufficient integration**
- Tool Necessity Gate can decide a request needs live data, but search runtime is still too weak and too opaque.

---

## 3. Goals

### 3.1 Primary Goals

- Replace HTML-scrape-first search with a provider-driven search runtime.
- Support both classic search providers and richer grounding providers.
- Make provider selection configurable, observable, and extensible.
- Add structured query expansion and fallback behavior.
- Prioritize official or trusted sources where appropriate.
- Integrate with Tool Necessity Gate and future Full Context Engine work.

### 3.2 Non-Goals

This spec does not:
- implement the full Context Engine,
- replace browser automation,
- replace provider-specific business tools such as Jira or Confluence adapters,
- require all providers to support grounded summaries on day one.

---

## 4. OpenClaw Alignment and AtlasClaw Strengthening

OpenClaw already treats web search as a provider-driven runtime with:
- configured providers,
- provider auto-selection,
- support for richer grounding-capable providers,
- caching,
- and clear separation between `web_search`, `web_fetch`, and browser automation.

AtlasClaw should align with that direction, but strengthen it in several ways:

1. **Enterprise source governance**
- AtlasClaw should explicitly support official-source prioritization and trusted-domain policies.

2. **Runtime integration with Tool Gate**
- Search should not just be available; it should be the enforced grounding path when runtime policy requires it.

3. **Structured query recovery**
- AtlasClaw should not stop after one failed query if the runtime still has a good retrieval path.

4. **Unified eventing**
- Search and grounding behavior should emit standard events that can later be routed to Web chat, channels, Hooks, or dashboards.

---

## 5. Architecture Summary

```text
User Request
  -> Tool Necessity Gate
  -> Capability Matcher
  -> Search / Grounding Runtime
       -> Provider Selector
       -> Query Planner
       -> Search Adapter / Grounding Adapter
       -> Source Prioritizer
       -> Result Normalizer
  -> Mandatory Tool Enforcement
  -> Final grounded answer path
```

### 5.1 Runtime Components

1. `SearchProviderRegistry`
2. `SearchProviderSelector`
3. `SearchQueryPlanner`
4. `SearchExecutionRuntime`
5. `GroundingProviderAdapter`
6. `SearchResultNormalizer`
7. `SourcePrioritizer`
8. `SearchAuditEventBridge`

---

## 6. Provider Model

### 6.1 Provider Types

AtlasClaw should support two provider classes:

1. **Search providers**
- Return classic search results:
  - title
  - URL
  - snippet
  - ranking metadata

2. **Grounding providers**
- Return search-backed evidence and optionally:
  - grounded summary,
  - citations,
  - evidence blocks,
  - confidence,
  - provider-native result metadata.

### 6.2 Provider Capability Contract

Each provider should declare capabilities such as:

- `supports_search_results`
- `supports_grounded_summary`
- `supports_citations`
- `supports_domain_filtering`
- `supports_language_hint`
- `supports_recency_hint`
- `supports_query_rewrite`
- `requires_api_key`
- `supports_cache`

### 6.3 Provider Registry

The runtime should resolve providers through a registry instead of a hard-coded branch table.

Suggested provider keys:
- `bing_html_fallback`
- `duckduckgo_html_fallback`
- `google_html_fallback`
- `brave_api`
- `tavily`
- `perplexity`
- `exa`
- `firecrawl_search`
- `grok_search`
- future enterprise-specific internal search providers

The existing HTML-based engines should remain available only as lower-tier fallback providers, not as the platform's primary architecture.

---

## 7. Search Result Model

### 7.1 Normalized Search Result

```json
{
  "title": "OpenClaw Web Search",
  "url": "https://docs.openclaw.ai/tools/web",
  "snippet": "OpenClaw web_search uses configured providers...",
  "provider": "brave_api",
  "rank": 1,
  "source_tier": "official",
  "retrieved_at": "2026-03-31T10:00:00+08:00",
  "language": "en",
  "confidence": 0.88
}
```

### 7.2 Grounded Search Response

```json
{
  "provider": "perplexity",
  "query": "OpenClaw web search",
  "summary": "OpenClaw uses configured search providers with cached results...",
  "citations": [
    {
      "title": "Web Search",
      "url": "https://docs.openclaw.ai/tools/web"
    }
  ],
  "results": [],
  "confidence": 0.92
}
```

### 7.3 Unified Tool Output

The tool layer should be able to return:
- normalized search results only,
- grounded summary with citations,
- or both.

Mandatory Tool Enforcement and later Context Engine logic should not care which provider class produced the result, only that the normalized output is valid and grounded enough for the current policy.

---

## 8. Provider Selection Strategy

### 8.1 Inputs

Provider selection should consider:
- requested provider override,
- available configured providers,
- required capabilities from Tool Necessity Gate,
- trust / source policy,
- rate limit health,
- latency history,
- tenant / environment policy,
- current provider availability.

### 8.2 Default Selection Rules

1. If a request explicitly asks for grounded verification, prefer a provider with:
- `supports_grounded_summary`
- `supports_citations`

2. If no grounding-capable provider is available, fall back to classic search providers plus `web_fetch`.

3. HTML-based public search scraping should be fallback tier, not preferred tier.

4. If a provider is degraded or rate-limited, selector should fail over to the next viable provider.

### 8.3 Explicit Provider Override

The runtime should still allow:
- explicit provider selection by policy,
- per-tenant provider preference,
- runtime override for debugging or enterprise rollout.

---

## 9. Query Planning and Expansion

### 9.1 Query Planning Responsibility

Query planning should decide:
- the initial query,
- whether domain constraints should be applied,
- whether recency should be requested,
- whether a retry query should be broader or narrower,
- when to stop expanding.

### 9.2 Query Expansion Policy

For a failed or weak first query, the runtime may try:

1. **Official-source expansion**
- add `official`
- add `docs`
- add `GitHub`
- add `site:` constraints for trusted domains

2. **Language expansion**
- switch between localized and English query phrasing

3. **Entity simplification**
- reduce over-specific wording to core entity name

4. **Structured source expansion**
- try domain-targeted search such as:
  - `site:github.com`
  - `site:docs.<domain>`
  - `site:<official_domain>`

### 9.3 Query Expansion Guardrails

- Maximum attempts must be bounded.
- Expansion steps must be visible in runtime events.
- The runtime must stop expansion if confidence becomes adequate.
- Expansion must not loop indefinitely.

Recommended defaults:
- initial query + up to 2 expansion attempts
- one official-source attempt
- one language or simplification attempt

---

## 10. Official-Source Prioritization

### 10.1 Source Tiers

Each result should be classified into a source tier:

1. `official`
- vendor docs
- official product site
- official GitHub org / repository

2. `trusted`
- recognized authoritative sources
- enterprise-approved domains

3. `community`
- blog posts
- forums
- secondary references

4. `unknown`

### 10.2 Priority Rules

When the user asks:
- "what is X"
- "official docs"
- "how does X work"
- "latest documentation"

the runtime should strongly prefer:
- `official` first,
- `trusted` second,
- `community` only if higher tiers are missing.

### 10.3 Enterprise Policy

AtlasClaw should support configurable trust policy:
- per-installation trusted domains,
- official-domain allowlists,
- per-provider trust weighting.

---

## 11. Mandatory Tool Enforcement Integration

### 11.1 Tool Gate Relationship

When Tool Necessity Gate decides:
- `needs_live_data = true`
- or `needs_grounded_verification = true`

the Capability Matcher should prefer:
- grounding-capable provider if available,
- otherwise `web_search` + `web_fetch`,
- and browser escalation only when result extraction still fails or requires dynamic rendering/login.

### 11.2 Final Answer Policy

If search/grounding is mandatory:
- the runtime must not permit a final ungrounded answer.

If search provider execution fails:
- the runtime may retry with a different provider,
- or escalate query planning,
- or route to browser if warranted,
- or stop with explicit verification failure.

### 11.3 Anti-Fabrication Rule

The model must not claim:
- "I searched"
- "I checked"
- "results show"

unless the runtime has actual provider execution evidence.

---

## 12. Browser Escalation

### 12.1 When Browser Is Needed

Search runtime should escalate to browser only when:
- result pages require JS-heavy rendering,
- login or session context is required,
- site-level navigation is necessary,
- repeated search providers return weak/no results but browser execution may still succeed.

### 12.2 Browser Is Not Default Search

Browser automation must not become the primary replacement for search providers.
It is a heavier execution path used only when:
- normal provider-backed search is insufficient,
- and runtime policy justifies the cost.

---

## 13. Caching and Performance

### 13.1 Search Cache

The runtime should support provider-aware caching:
- query
- provider
- normalized parameters
- TTL
- tenant/user scope where required

### 13.2 Suggested Defaults

- classic public search cache: 10-15 minutes
- grounded provider response cache: short TTL unless provider itself already guarantees freshness metadata
- no caching for authenticated private browser-derived results unless explicitly allowed

### 13.3 Cache Safety

Private data must never leak across users or tenants through cache reuse.

---

## 14. Events and Observability

### 14.1 Event Types

The runtime should emit events such as:
- `search.provider.selected`
- `search.query.started`
- `search.query.completed`
- `search.query.failed`
- `search.query.expanded`
- `search.grounding.completed`
- `search.escalated_to_browser`
- `search.official_source_selected`
- `search.final_verification_failed`

### 14.2 Metrics

Suggested metrics:
- provider selection counts
- provider failure rates
- average query latency
- expansion rate
- fallback rate
- grounded-summary usage rate
- browser-escalation rate
- official-source hit rate

### 14.3 Hook Runtime Relationship

These events should be consumable by existing Hook Runtime so later systems can:
- notify Web chat,
- emit admin warnings,
- trigger script hooks,
- feed dashboards or audits.

---

## 15. Configuration Model

### 15.1 Top-Level Search Runtime Config

```json
{
  "search_runtime": {
    "enabled": true,
    "default_provider": "auto",
    "cache_ttl_minutes": 15,
    "max_query_attempts": 3,
    "prefer_grounding": true,
    "official_domains": [
      "github.com",
      "docs.openclaw.ai"
    ],
    "trusted_domains": [],
    "providers": {
      "bing_html_fallback": { "enabled": true },
      "duckduckgo_html_fallback": { "enabled": true },
      "google_html_fallback": { "enabled": false },
      "brave_api": { "enabled": false, "api_key": "${BRAVE_API_KEY}" },
      "tavily": { "enabled": false, "api_key": "${TAVILY_API_KEY}" }
    }
  }
}
```

### 15.2 Provider-Specific Config

Each provider entry should support:
- `enabled`
- `priority`
- `api_key`
- `timeout_seconds`
- `rate_limit_policy`
- `supports_grounding_override`

---

## 16. Testing Strategy

### 16.1 Unit Tests

- provider registry behavior
- provider selection logic
- query planner and expansion policy
- source-tier classification
- result normalization
- cache behavior

### 16.2 Integration Tests

- Tool Gate -> Capability Matcher -> Search Runtime path
- grounding-capable provider path
- fallback from grounding provider to classic search
- official-source prioritization
- browser escalation decision

### 16.3 E2E Tests

- time-sensitive public information query
- official-doc lookup query
- weak first query with successful expansion
- provider failure with fallback provider success
- total verification failure with explicit refusal to fabricate

---

## 17. Rollout Strategy

### Phase 1
- introduce provider registry,
- normalize search results,
- keep current HTML-based providers only as fallback adapters,
- add query expansion,
- add official-source prioritization.

### Phase 2
- add first-class grounding providers,
- add stronger cache and provider health policies,
- integrate with later Full Context Engine evidence prioritization.

---

## 18. Success Criteria

This design is successful when:
- AtlasClaw no longer relies on HTML-scrape search as the primary architecture,
- Tool Necessity Gate can route to a stronger provider-driven search layer,
- search runtime can expand queries and prioritize official sources,
- search and grounding become observable runtime capabilities,
- and the architecture is clearly stronger and more enterprise-ready than the current implementation.
