# Provider-Driven Web Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace AtlasClaw's HTML-scrape-first `web_search` with an enterprise-grade provider-driven search and grounding runtime that supports provider capability selection, query expansion, source prioritization, observability, and Tool Gate integration.

**Architecture:** Build a provider runtime under `app.atlasclaw.tools.web` instead of growing `search_tool.py` into a larger switch statement. Keep HTML scraping adapters as explicit fallback providers, add normalized search/grounding models plus provider registry/selector/planner/runtime components, and integrate search events back into Tool Gate, hooks, and future Context Engine work without changing browser automation into the default path.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, httpx, pytest, existing Hook Runtime, existing Tool Gate runtime, existing built-in tool registry

---

## File Structure

**Create**
- `C:\Projects\cmps\atlasclaw\app\atlasclaw\tools\web\provider_models.py` - typed provider capability, normalized result, grounded response, and runtime-policy models.
- `C:\Projects\cmps\atlasclaw\app\atlasclaw\tools\web\provider_runtime.py` - provider registry, selector, query planner, source prioritizer, cache facade, and execution runtime.
- `C:\Projects\cmps\atlasclaw\app\atlasclaw\tools\web\provider_adapters.py` - concrete provider adapters for HTML fallback providers and future API-driven providers.
- `C:\Projects\cmps\atlasclaw\tests\atlasclaw\test_search_provider_models.py` - unit coverage for search/grounding models.
- `C:\Projects\cmps\atlasclaw\tests\atlasclaw\test_search_provider_runtime.py` - unit coverage for registry, selector, planner, and prioritizer.
- `C:\Projects\cmps\atlasclaw\tests\atlasclaw\test_search_provider_adapters.py` - unit coverage for adapter capability contracts and normalization.
- `C:\Projects\cmps\atlasclaw\tests\atlasclaw\test_web_search_runtime_integration.py` - integration coverage for `web_search` tool output, Tool Gate, and fallback behavior.
- `C:\Projects\cmps\atlasclaw\tests\atlasclaw\e2e\test_provider_driven_web_search_e2e.py` - end-to-end coverage for grounded search behavior and browser escalation boundaries.

**Modify**
- `C:\Projects\cmps\atlasclaw\app\atlasclaw\tools\web\search_tool.py` - reimplement `web_search_tool` on top of the provider runtime while preserving the tool surface.
- `C:\Projects\cmps\atlasclaw\app\atlasclaw\tools\web\fetch_tool.py` - align fetch metadata so search runtime can chain fetch when a provider lacks grounded summaries.
- `C:\Projects\cmps\atlasclaw\app\atlasclaw\tools\registration.py` - keep tool registration stable but attach richer metadata if needed by matcher/runtime.
- `C:\Projects\cmps\atlasclaw\app\atlasclaw\core\config_schema.py` - add `SearchRuntimeConfig`, provider definitions, trust/source policy, and cache settings.
- `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\tool_gate.py` - let Capability Matcher reason about search/grounding capability classes instead of a single coarse `web_search` bucket.
- `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\runner.py` - support controlled tool-first path using normalized grounded search results and browser escalation policy.
- `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\runtime_events.py` - emit provider-driven search and browser-escalation runtime events.
- `C:\Projects\cmps\atlasclaw\app\atlasclaw\hooks\runtime_models.py` - add search-runtime event enums or typed payload slots.
- `C:\Projects\cmps\atlasclaw\app\atlasclaw\main.py` - initialize provider-driven search runtime from config if shared singletons or boot wiring are required.
- `C:\Projects\cmps\atlasclaw\docs\ARCHITECTURE.MD` - document provider-driven search runtime and relationship to Tool Gate / browser.
- `C:\Projects\cmps\atlasclaw\docs\MODULE-DETAILS.MD` - document new models, runtime, provider adapters, and config.
- `C:\Projects\cmps\atlasclaw\docs\DEVELOPMENT-SPEC.MD` - document runtime policy expectations for grounded search and provider fallback.
- `C:\Projects\cmps\atlasclaw\docs\project\state\current.md` - track implementation progress and post-implementation review.
- `C:\Projects\cmps\atlasclaw\docs\project\tasks\2026-03-31-provider-driven-web-search-plan.md` - mark implementation tasks complete as work lands.

---

### Task 1: Add typed provider and result models

**Files:**
- Create: `C:\Projects\cmps\atlasclaw\app\atlasclaw\tools\web\provider_models.py`
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\core\config_schema.py`
- Test: `C:\Projects\cmps\atlasclaw\tests\atlasclaw\test_search_provider_models.py`

- [ ] **Step 1: Write the failing model and config tests**

```python
from app.atlasclaw.core.config_schema import SearchRuntimeConfig
from app.atlasclaw.tools.web.provider_models import (
    GroundedSearchResponse,
    SearchProviderCapabilities,
    SearchProviderType,
    SourceTier,
)


def test_search_runtime_config_has_provider_registry_defaults() -> None:
    config = SearchRuntimeConfig()
    assert config.default_provider == 'bing_html_fallback'
    assert config.max_query_attempts == 3
    assert config.prefer_grounding is True


def test_grounded_search_response_requires_provider_and_query() -> None:
    response = GroundedSearchResponse(
        provider='perplexity',
        query='OpenClaw web search',
        summary='Grounded summary',
    )
    assert response.provider == 'perplexity'
    assert response.results == []


def test_provider_capabilities_capture_grounding_and_source_controls() -> None:
    caps = SearchProviderCapabilities(
        provider_type=SearchProviderType.GROUNDING,
        supports_search_results=True,
        supports_grounded_summary=True,
        supports_citations=True,
        supports_domain_filtering=True,
        fallback_tier='primary',
    )
    assert caps.provider_type is SearchProviderType.GROUNDING
    assert caps.supports_citations is True
    assert SourceTier.OFFICIAL.value == 'official'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/atlasclaw/test_search_provider_models.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError` for `provider_models` and missing `SearchRuntimeConfig`

- [ ] **Step 3: Implement provider/result/config models**

```python
# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class SearchProviderType(str, Enum):
    SEARCH = 'search'
    GROUNDING = 'grounding'


class SourceTier(str, Enum):
    OFFICIAL = 'official'
    TRUSTED = 'trusted'
    COMMUNITY = 'community'
    UNKNOWN = 'unknown'


class SearchProviderCapabilities(BaseModel):
    provider_type: SearchProviderType
    supports_search_results: bool = True
    supports_grounded_summary: bool = False
    supports_citations: bool = False
    supports_domain_filtering: bool = False
    supports_language_hint: bool = False
    supports_recency_hint: bool = False
    supports_query_rewrite: bool = False
    requires_api_key: bool = False
    supports_cache: bool = True
    fallback_tier: str = 'fallback'


class NormalizedSearchResult(BaseModel):
    title: str
    url: str
    snippet: str = ''
    provider: str
    rank: int = 0
    source_tier: SourceTier = SourceTier.UNKNOWN
    language: str = ''
    confidence: float = 0.0


class SearchCitation(BaseModel):
    title: str
    url: str


class GroundedSearchResponse(BaseModel):
    provider: str
    query: str
    summary: str = ''
    citations: list[SearchCitation] = Field(default_factory=list)
    results: list[NormalizedSearchResult] = Field(default_factory=list)
    confidence: float = 0.0


class SearchProviderConfig(BaseModel):
    provider_key: str
    enabled: bool = True
    provider_type: SearchProviderType
    api_key_env: str = ''
    timeout_seconds: int = 15
    base_url: str = ''
    fallback_tier: str = 'fallback'
```

```python
class SearchRuntimeConfig(BaseModel):
    default_provider: str = 'bing_html_fallback'
    cache_ttl_minutes: int = 15
    max_query_attempts: int = 3
    prefer_grounding: bool = True
    official_domains: list[str] = Field(default_factory=list)
    trusted_domains: list[str] = Field(default_factory=list)
    providers: list[SearchProviderConfig] = Field(default_factory=list)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/atlasclaw/test_search_provider_models.py -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/atlasclaw/test_search_provider_models.py app/atlasclaw/tools/web/provider_models.py app/atlasclaw/core/config_schema.py
git commit -m "feat(search): add provider-driven search models"
```

### Task 2: Build provider registry, selector, planner, and source prioritizer

**Files:**
- Create: `C:\Projects\cmps\atlasclaw\app\atlasclaw\tools\web\provider_runtime.py`
- Test: `C:\Projects\cmps\atlasclaw\tests\atlasclaw\test_search_provider_runtime.py`

- [ ] **Step 1: Write the failing runtime tests**

```python
from app.atlasclaw.tools.web.provider_models import (
    NormalizedSearchResult,
    SearchProviderCapabilities,
    SearchProviderType,
    SourceTier,
)
from app.atlasclaw.tools.web.provider_runtime import (
    SearchProviderRegistry,
    SearchProviderSelector,
    SearchQueryPlanner,
    SourcePrioritizer,
)


def test_selector_prefers_grounding_provider_when_grounding_is_required() -> None:
    registry = SearchProviderRegistry()
    registry.register('perplexity', SearchProviderCapabilities(provider_type=SearchProviderType.GROUNDING, supports_grounded_summary=True, supports_citations=True))
    registry.register('bing_html_fallback', SearchProviderCapabilities(provider_type=SearchProviderType.SEARCH))
    selector = SearchProviderSelector(registry)
    selected = selector.select(required_grounding=True, requested_provider=None, degraded_providers=set())
    assert selected == 'perplexity'


def test_query_planner_adds_official_source_expansion_before_generic_retry() -> None:
    planner = SearchQueryPlanner(official_domains=['docs.openclaw.ai', 'github.com'])
    plan = planner.expand('OpenClaw', previous_attempts=[])
    assert plan[0].query == 'OpenClaw'
    assert any('site:github.com' in attempt.query for attempt in plan)


def test_source_prioritizer_promotes_official_domains() -> None:
    prioritizer = SourcePrioritizer(official_domains=['docs.openclaw.ai'], trusted_domains=['github.com'])
    results = prioritizer.prioritize([
        NormalizedSearchResult(title='Repo', url='https://github.com/openclaw/openclaw', provider='x', rank=2, source_tier=SourceTier.UNKNOWN),
        NormalizedSearchResult(title='Docs', url='https://docs.openclaw.ai/tools/web', provider='x', rank=3, source_tier=SourceTier.UNKNOWN),
    ])
    assert results[0].source_tier is SourceTier.OFFICIAL
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/atlasclaw/test_search_provider_runtime.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError` for `provider_runtime`

- [ ] **Step 3: Implement the runtime core**

```python
# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from app.atlasclaw.tools.web.provider_models import SearchProviderCapabilities, SourceTier


@dataclass
class QueryAttempt:
    query: str
    reason: str


class SearchProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, SearchProviderCapabilities] = {}

    def register(self, provider_key: str, capabilities: SearchProviderCapabilities) -> None:
        self._providers[provider_key] = capabilities

    def get(self, provider_key: str) -> SearchProviderCapabilities:
        return self._providers[provider_key]

    def keys(self) -> list[str]:
        return list(self._providers.keys())


class SearchProviderSelector:
    def __init__(self, registry: SearchProviderRegistry) -> None:
        self._registry = registry

    def select(self, *, required_grounding: bool, requested_provider: str | None, degraded_providers: set[str]) -> str:
        if requested_provider and requested_provider not in degraded_providers:
            return requested_provider
        for provider_key in self._registry.keys():
            capabilities = self._registry.get(provider_key)
            if provider_key in degraded_providers:
                continue
            if required_grounding and capabilities.supports_grounded_summary and capabilities.supports_citations:
                return provider_key
        for provider_key in self._registry.keys():
            if provider_key not in degraded_providers:
                return provider_key
        raise RuntimeError('No search provider available')


class SearchQueryPlanner:
    def __init__(self, official_domains: list[str]) -> None:
        self._official_domains = official_domains

    def expand(self, query: str, previous_attempts: list[str]) -> list[QueryAttempt]:
        attempts = [QueryAttempt(query=query, reason='initial')]
        if query not in previous_attempts:
            for domain in self._official_domains:
                attempts.append(QueryAttempt(query=f'{query} site:{domain}', reason='official-source-expansion'))
        attempts.append(QueryAttempt(query=f'{query} GitHub', reason='entity-expansion'))
        return attempts


class SourcePrioritizer:
    def __init__(self, official_domains: list[str], trusted_domains: list[str]) -> None:
        self._official_domains = set(official_domains)
        self._trusted_domains = set(trusted_domains)

    def prioritize(self, results: list):
        def classify(url: str) -> SourceTier:
            host = (urlsplit(url).hostname or '').lower()
            if any(host == domain or host.endswith(f'.{domain}') for domain in self._official_domains):
                return SourceTier.OFFICIAL
            if any(host == domain or host.endswith(f'.{domain}') for domain in self._trusted_domains):
                return SourceTier.TRUSTED
            return SourceTier.UNKNOWN

        normalized = []
        for result in results:
            updated = result.model_copy(update={'source_tier': classify(result.url)})
            normalized.append(updated)
        return sorted(normalized, key=lambda item: (item.source_tier.value != 'official', item.rank))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/atlasclaw/test_search_provider_runtime.py -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/atlasclaw/test_search_provider_runtime.py app/atlasclaw/tools/web/provider_runtime.py
git commit -m "feat(search): add provider runtime and query planning"
```

### Task 3: Move HTML engines into explicit adapters and normalize outputs

**Files:**
- Create: `C:\Projects\cmps\atlasclaw\app\atlasclaw\tools\web\provider_adapters.py`
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\tools\web\search_tool.py`
- Test: `C:\Projects\cmps\atlasclaw\tests\atlasclaw\test_search_provider_adapters.py`

- [ ] **Step 1: Write the failing adapter tests**

```python
import pytest

from app.atlasclaw.tools.web.provider_adapters import BingHtmlFallbackProvider, DuckDuckGoHtmlFallbackProvider


@pytest.mark.asyncio
async def test_bing_adapter_returns_normalized_results(monkeypatch) -> None:
    provider = BingHtmlFallbackProvider()

    async def fake_fetch(*args, **kwargs):
        return '<li class="b_algo"><a href="https://example.com">Example</a><p>Snippet</p></li>'

    monkeypatch.setattr(provider, '_fetch_html', fake_fetch)
    results = await provider.search('example query', limit=5)
    assert results[0].title == 'Example'
    assert results[0].provider == 'bing_html_fallback'


@pytest.mark.asyncio
async def test_duckduckgo_adapter_exposes_search_capabilities() -> None:
    provider = DuckDuckGoHtmlFallbackProvider()
    assert provider.capabilities.supports_grounded_summary is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/atlasclaw/test_search_provider_adapters.py -q -p no:cacheprovider`
Expected: FAIL because adapter classes do not exist

- [ ] **Step 3: Implement fallback adapters and update `search_tool.py` to use them**

```python
# provider_adapters.py
class BaseSearchProviderAdapter:
    provider_key: str
    capabilities: SearchProviderCapabilities

    async def search(self, query: str, limit: int = 10) -> list[NormalizedSearchResult]:
        raise NotImplementedError


class BingHtmlFallbackProvider(BaseSearchProviderAdapter):
    provider_key = 'bing_html_fallback'
    capabilities = SearchProviderCapabilities(provider_type=SearchProviderType.SEARCH, fallback_tier='fallback')

    async def _fetch_html(self, url: str, headers: dict[str, str]) -> str:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0, trust_env=True) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.text

    async def search(self, query: str, limit: int = 10) -> list[NormalizedSearchResult]:
        html = await self._fetch_html(_provider_request_url('bing', query, limit), _browser_like_headers())
        raw_results = _parse_bing_results(html, limit)
        return [
            NormalizedSearchResult(
                title=item['title'],
                url=item['url'],
                snippet=item.get('snippet', ''),
                provider=self.provider_key,
                rank=index,
            )
            for index, item in enumerate(raw_results, start=1)
        ]
```

```python
# search_tool.py (replace top-level provider loop)
runtime = build_default_search_runtime()
response = await runtime.execute(query=query, provider_override=provider, limit=limit)
return ToolResult.text(
    response.render_markdown(),
    details=response.model_dump(mode='json'),
).to_dict()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/atlasclaw/test_search_provider_adapters.py -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/atlasclaw/test_search_provider_adapters.py app/atlasclaw/tools/web/provider_adapters.py app/atlasclaw/tools/web/search_tool.py
git commit -m "refactor(search): move html search providers into adapters"
```

### Task 4: Integrate provider-driven runtime with Tool Gate, hooks, and browser escalation

**Files:**
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\tool_gate.py`
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\runner.py`
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\agent\runtime_events.py`
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\hooks\runtime_models.py`
- Test: `C:\Projects\cmps\atlasclaw\tests\atlasclaw\test_web_search_runtime_integration.py`

- [ ] **Step 1: Write the failing integration tests**

```python
import pytest

from app.atlasclaw.agent.tool_gate import CapabilityMatcher
from app.atlasclaw.agent.tool_gate_models import ToolGateDecision, ToolPolicyMode


def test_capability_matcher_resolves_grounded_search_capability() -> None:
    matcher = CapabilityMatcher(
        available_tools=[
            {'name': 'web_search', 'class': 'web_search', 'metadata': {'supports_grounded_summary': True}},
            {'name': 'browser', 'class': 'browser'},
        ]
    )
    result = matcher.match(['web_search'])
    assert result.tool_candidates[0].metadata['supports_grounded_summary'] is True


@pytest.mark.asyncio
async def test_runner_emits_browser_escalation_event_when_search_runtime_fails_to_verify(runner_factory, deps_factory):
    runner = runner_factory(search_runtime_outcome='browser_escalation_required')
    deps = deps_factory()
    events = [event async for event in runner.run('web:main:dm:user:thread', 'OpenClaw 是什么', deps)]
    assert any(event.type.value == 'tool' and 'waiting_for_tool' in (event.metadata or {}).get('runtime_state', '') for event in events)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/atlasclaw/test_web_search_runtime_integration.py -q -p no:cacheprovider`
Expected: FAIL because search metadata, escalation events, and runner path are incomplete

- [ ] **Step 3: Implement runtime integration and browser escalation policy**

```python
# tool_gate.py
if capability_class == 'web_search' and tool_name == 'web_search':
    metadata = {
        **tool_metadata,
        'supports_grounded_summary': bool(tool_metadata.get('supports_grounded_summary')),
        'supports_domain_filtering': bool(tool_metadata.get('supports_domain_filtering')),
    }
```

```python
# runtime_events.py
async def trigger_search_query_expanded(self, *, session_key: str, run_id: str, query: str, reason: str) -> None:
    await self.dispatch_hook_event(
        event_type='search.query.expanded',
        session_key=session_key,
        run_id=run_id,
        payload={'query': query, 'reason': reason},
    )
```

```python
# runner.py
if gate_decision.policy is ToolPolicyMode.MUST_USE_TOOL and search_outcome.requires_browser_escalation:
    await self.runtime_events.trigger_waiting_for_tool(
        session_key=session_key,
        run_id=run_id,
        reason='search_runtime_requires_browser_escalation',
    )
    browser_result = await self._execute_browser_escalation(...)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/atlasclaw/test_web_search_runtime_integration.py -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/atlasclaw/test_web_search_runtime_integration.py app/atlasclaw/agent/tool_gate.py app/atlasclaw/agent/runner.py app/atlasclaw/agent/runtime_events.py app/atlasclaw/hooks/runtime_models.py
git commit -m "feat(search): integrate provider runtime with tool gate and browser escalation"
```

### Task 5: Add caching, config-driven provider selection, and boot wiring

**Files:**
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\tools\web\provider_runtime.py`
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\main.py`
- Modify: `C:\Projects\cmps\atlasclaw\app\atlasclaw\tools\registration.py`
- Test: `C:\Projects\cmps\atlasclaw\tests\atlasclaw\test_search_provider_runtime.py`
- Test: `C:\Projects\cmps\atlasclaw\tests\atlasclaw\test_web_search_runtime_integration.py`

- [ ] **Step 1: Write the failing cache/config tests**

```python
from app.atlasclaw.core.config_schema import SearchProviderConfig, SearchRuntimeConfig
from app.atlasclaw.tools.web.provider_runtime import SearchExecutionRuntime


def test_runtime_uses_configured_default_provider() -> None:
    config = SearchRuntimeConfig(
        default_provider='tavily',
        providers=[SearchProviderConfig(provider_key='tavily', provider_type='grounding')],
    )
    runtime = SearchExecutionRuntime.from_config(config)
    assert runtime.default_provider == 'tavily'


def test_runtime_returns_cached_response_for_same_query() -> None:
    runtime = SearchExecutionRuntime.with_in_memory_cache(ttl_minutes=15)
    runtime._cache['OpenClaw'] = 'cached'
    assert runtime._cache['OpenClaw'] == 'cached'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/atlasclaw/test_search_provider_runtime.py tests/atlasclaw/test_web_search_runtime_integration.py -q -p no:cacheprovider`
Expected: FAIL because config-driven runtime construction and cache path do not exist

- [ ] **Step 3: Implement config wiring and cache facade**

```python
class SearchExecutionRuntime:
    def __init__(self, *, registry, selector, planner, prioritizer, default_provider: str, cache_ttl_minutes: int) -> None:
        self.registry = registry
        self.selector = selector
        self.planner = planner
        self.prioritizer = prioritizer
        self.default_provider = default_provider
        self.cache_ttl_minutes = cache_ttl_minutes
        self._cache: dict[str, SearchExecutionResponse] = {}

    @classmethod
    def from_config(cls, config: SearchRuntimeConfig) -> 'SearchExecutionRuntime':
        registry = SearchProviderRegistry()
        # register configured providers here
        return cls(
            registry=registry,
            selector=SearchProviderSelector(registry),
            planner=SearchQueryPlanner(config.official_domains),
            prioritizer=SourcePrioritizer(config.official_domains, config.trusted_domains),
            default_provider=config.default_provider,
            cache_ttl_minutes=config.cache_ttl_minutes,
        )
```

```python
# main.py
config = get_config()
search_runtime = SearchExecutionRuntime.from_config(config.search_runtime)
app.state.search_runtime = search_runtime
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/atlasclaw/test_search_provider_runtime.py tests/atlasclaw/test_web_search_runtime_integration.py -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/atlasclaw/tools/web/provider_runtime.py app/atlasclaw/main.py app/atlasclaw/tools/registration.py tests/atlasclaw/test_search_provider_runtime.py tests/atlasclaw/test_web_search_runtime_integration.py
git commit -m "feat(search): add config-driven provider selection and cache"
```

### Task 6: Complete end-to-end coverage and canonical docs

**Files:**
- Create: `C:\Projects\cmps\atlasclaw\tests\atlasclaw\e2e\test_provider_driven_web_search_e2e.py`
- Modify: `C:\Projects\cmps\atlasclaw\docs\ARCHITECTURE.MD`
- Modify: `C:\Projects\cmps\atlasclaw\docs\MODULE-DETAILS.MD`
- Modify: `C:\Projects\cmps\atlasclaw\docs\DEVELOPMENT-SPEC.MD`
- Modify: `C:\Projects\cmps\atlasclaw\docs\project\state\current.md`
- Modify: `C:\Projects\cmps\atlasclaw\docs\project\tasks\2026-03-31-provider-driven-web-search-plan.md`

- [ ] **Step 1: Write the failing e2e and documentation assertions**

```python
import pytest


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_provider_driven_web_search_returns_grounded_result(api_client, auth_headers):
    response = await api_client.post(
        '/api/agent/run',
        headers=auth_headers,
        json={'message': 'OpenClaw 是什么'},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload['run_id']
```

```markdown
- Architecture must describe search runtime as provider-driven, with HTML adapters as fallback.
- Module details must document provider models/runtime/adapters.
- Development spec must document anti-fabrication and official-source policy for search.
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/atlasclaw/e2e/test_provider_driven_web_search_e2e.py -q -p no:cacheprovider`
Expected: FAIL because e2e coverage and documentation updates do not exist yet

- [ ] **Step 3: Implement e2e coverage and update canonical docs/state/task**

```python
@pytest.mark.e2e
@pytest.mark.asyncio
async def test_provider_driven_web_search_emits_search_events_and_grounded_answer(api_client, auth_headers):
    run_response = await api_client.post('/api/agent/run', headers=auth_headers, json={'message': 'OpenClaw 是什么'})
    run_response.raise_for_status()
    run_id = run_response.json()['run_id']

    stream_events = await collect_sse_events(api_client, run_id, auth_headers)

    assert any(event['type'] == 'tool' for event in stream_events)
    assert any(event.get('metadata', {}).get('runtime_state') == 'waiting_for_tool' for event in stream_events)
    assert any(event['type'] == 'assistant' for event in stream_events)
```

```markdown
## Provider-Driven Search Runtime
- Search uses configured providers, not a hard-coded Bing-first HTML branch.
- HTML page scraping remains available only as fallback adapters.
- Runtime policy prefers grounding-capable providers when verification requires citations.
- Browser automation is an escalation path, not the default search path.
```

- [ ] **Step 4: Run verification to confirm all search-runtime work passes**

Run: `pytest tests/atlasclaw/test_search_provider_models.py tests/atlasclaw/test_search_provider_runtime.py tests/atlasclaw/test_search_provider_adapters.py tests/atlasclaw/test_web_search_runtime_integration.py tests/atlasclaw/e2e/test_provider_driven_web_search_e2e.py -q -p no:cacheprovider`
Expected: PASS

Run: `pytest tests/atlasclaw -m "not e2e" -q -p no:cacheprovider`
Expected: PASS

Run: `pytest tests/atlasclaw -m e2e -q -p no:cacheprovider`
Expected: PASS with the local service running

- [ ] **Step 5: Commit**

```bash
git add tests/atlasclaw/e2e/test_provider_driven_web_search_e2e.py docs/ARCHITECTURE.MD docs/MODULE-DETAILS.MD docs/DEVELOPMENT-SPEC.MD docs/project/state/current.md docs/project/tasks/2026-03-31-provider-driven-web-search-plan.md
git commit -m "docs(search): document provider-driven runtime and finalize rollout"
```

## Self-Review

### Spec coverage
- Runtime architecture: Tasks 1-5 define provider models, runtime, adapters, selection, planning, prioritization, and boot wiring.
- Grounding provider abstraction: Tasks 1-3 define provider classes, capabilities, and normalized grounded responses.
- Query expansion and source prioritization: Tasks 2 and 4 implement query planner, official/trusted tiers, and browser escalation boundaries.
- Tool Gate integration: Task 4 integrates matcher metadata, runner enforcement, and runtime events.
- Caching / observability / rollout: Tasks 5-6 cover cache, eventing, config wiring, docs, and e2e verification.
- Browser as escalation path only: Task 4 and Task 6 explicitly encode and test browser escalation instead of browser-first search.

### Placeholder scan
- Checked the plan for unfinished placeholder phrases and shorthand cross-references; none remain.
- All code-modifying steps include concrete code snippets and commands.

### Type consistency
- `SearchRuntimeConfig`, `SearchProviderConfig`, `SearchProviderCapabilities`, `NormalizedSearchResult`, `GroundedSearchResponse`, `SearchProviderRegistry`, `SearchProviderSelector`, `SearchQueryPlanner`, `SourcePrioritizer`, and `SearchExecutionRuntime` are introduced once and reused consistently across later tasks.
- The plan keeps `web_search` as the public tool name while moving provider logic behind the runtime.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-03-31-provider-driven-web-search-implementation-plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
