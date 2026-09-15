# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from app.atlasclaw.tools.web.provider_models import (
    GroundedSearchResponse,
    NormalizedSearchResult,
    SearchCitation,
    SearchProviderCapabilities,
    SearchProviderType,
    SourceTier,
)
from app.atlasclaw.tools.web.provider_runtime import (
    SearchProviderRegistry,
    SearchProviderSelector,
    SearchQueryPlanner,
    SearchExecutionRuntime,
    SourcePrioritizer,
    _sanitize_snippet,
)


def test_selector_prefers_grounding_provider_when_grounding_is_required() -> None:
    registry = SearchProviderRegistry()
    registry.register(
        'perplexity',
        SearchProviderCapabilities(
            provider_type=SearchProviderType.GROUNDING,
            supports_grounded_summary=True,
            supports_citations=True,
        ),
    )
    registry.register(
        'bing_html_fallback',
        SearchProviderCapabilities(provider_type=SearchProviderType.SEARCH),
    )
    selector = SearchProviderSelector(registry)
    selected = selector.select(
        required_grounding=True,
        requested_provider=None,
        degraded_providers=set(),
    )
    assert selected == 'perplexity'


def test_query_planner_keeps_only_initial_query_without_automatic_expansion() -> None:
    planner = SearchQueryPlanner(official_domains=['docs.openclaw.ai', 'github.com'])
    plan = planner.expand('OpenClaw', previous_attempts=[])
    assert plan[0].query == 'OpenClaw'
    assert len(plan) == 1
    assert not any('site:' in attempt.query for attempt in plan)
    assert not any('GitHub' in attempt.query for attempt in plan)


def test_sanitize_snippet_trims_navigation_tail() -> None:
    raw = (
        '上海天气预报,上海7天天气预报 --> 首页 预报 预警 雷达 云图 天气地图 '
        '更多 台风路径 图片 专题'
    )
    cleaned = _sanitize_snippet(raw)
    assert cleaned == '上海天气预报,上海7天天气预报'


def test_query_planner_skips_github_expansion_for_non_technical_queries() -> None:
    planner = SearchQueryPlanner(official_domains=['docs.openclaw.ai', 'github.com'])
    plan = planner.expand('苏州周边徒步路线推荐', previous_attempts=[])
    assert not any('site:docs.openclaw.ai' in attempt.query for attempt in plan)
    assert not any('site:github.com' in attempt.query for attempt in plan)
    assert not any('GitHub' in attempt.query for attempt in plan)


def test_query_planner_skips_github_expansion_for_natural_language_english_queries() -> None:
    planner = SearchQueryPlanner(official_domains=['docs.openclaw.ai', 'github.com'])
    plan = planner.expand('Shanghai weather tomorrow', previous_attempts=[])
    assert plan[0].query == 'Shanghai weather tomorrow'
    assert not any('site:docs.openclaw.ai' in attempt.query for attempt in plan)
    assert not any('site:github.com' in attempt.query for attempt in plan)
    assert not any('GitHub' in attempt.query for attempt in plan)


def test_query_planner_skips_github_expansion_for_non_identifier_symbols() -> None:
    planner = SearchQueryPlanner(official_domains=['docs.openclaw.ai', 'github.com'])
    plan = planner.expand('??????', previous_attempts=[])
    assert len(plan) == 1
    assert plan[0].query == '??????'
    assert not any('GitHub' in attempt.query for attempt in plan)


def test_source_prioritizer_sets_source_tier_metadata_without_reordering_rank() -> None:
    prioritizer = SourcePrioritizer(
        official_domains=['docs.openclaw.ai'],
        trusted_domains=['github.com'],
    )
    results = prioritizer.prioritize([
        NormalizedSearchResult(
            title='Repo',
            url='https://github.com/openclaw/openclaw',
            provider='x',
            rank=2,
            source_tier=SourceTier.UNKNOWN,
        ),
        NormalizedSearchResult(
            title='Docs',
            url='https://docs.openclaw.ai/tools/web',
            provider='x',
            rank=3,
            source_tier=SourceTier.UNKNOWN,
        ),
    ])
    assert results[0].title == 'Repo'
    assert results[0].source_tier is SourceTier.TRUSTED
    assert results[1].title == 'Docs'
    assert results[1].source_tier is SourceTier.OFFICIAL



def test_source_prioritizer_does_not_promote_domains_without_explicit_config() -> None:
    prioritizer = SourcePrioritizer(
        official_domains=[],
        trusted_domains=[],
    )
    results = prioritizer.prioritize([
        NormalizedSearchResult(
            title='上海天气预报',
            url='https://www.weather.com.cn/weather/101020100.shtml',
            provider='x',
            rank=3,
            source_tier=SourceTier.UNKNOWN,
        ),
        NormalizedSearchResult(
            title='上海-天气预报',
            url='https://www.nmc.cn/publish/forecast/ASH/shanghai.html',
            provider='x',
            rank=2,
            source_tier=SourceTier.UNKNOWN,
        ),
        NormalizedSearchResult(
            title='上海徒步路线',
            url='https://travel.example.com/shanghai-hiking',
            provider='x',
            rank=1,
            source_tier=SourceTier.UNKNOWN,
        ),
    ])
    assert all(item.source_tier is SourceTier.UNKNOWN for item in results)


class _FakeAdapter:
    def __init__(self, results_by_query):
        self.capabilities = SearchProviderCapabilities(provider_type=SearchProviderType.SEARCH)
        self._results_by_query = results_by_query
        self.calls = []

    async def search(self, query: str, limit: int = 10):
        self.calls.append(query)
        return self._results_by_query.get(query, [])


class _FakeGroundingAdapter:
    def __init__(self, response_by_query):
        self.capabilities = SearchProviderCapabilities(
            provider_type=SearchProviderType.GROUNDING,
            supports_grounded_summary=True,
            supports_citations=True,
        )
        self._response_by_query = response_by_query
        self.calls = []

    async def search_grounded(self, query: str, limit: int = 10):
        _ = limit
        self.calls.append(query)
        return self._response_by_query.get(query)

    async def search(self, query: str, limit: int = 10):
        _ = limit
        self.calls.append(f"search:{query}")
        return []


def test_runtime_does_not_rewrite_query_when_initial_results_are_empty() -> None:
    registry = SearchProviderRegistry()
    adapter = _FakeAdapter({
        'OpenClaw': [],
    })
    registry.register_adapter('fake', adapter)
    runtime = SearchExecutionRuntime(
        registry=registry,
        selector=SearchProviderSelector(registry),
        planner=SearchQueryPlanner(official_domains=[]),
        prioritizer=SourcePrioritizer(official_domains=[], trusted_domains=['github.com']),
        default_provider='fake',
    )

    import asyncio
    response = asyncio.run(runtime.execute(query='OpenClaw', provider_override=None, limit=5))

    assert not response.results
    assert response.query == 'OpenClaw'
    assert adapter.calls == ['OpenClaw']


def test_runtime_fails_over_to_next_provider_when_selected_provider_returns_empty_results() -> None:
    registry = SearchProviderRegistry()
    first = _FakeAdapter({'OpenClaw': []})
    second = _FakeAdapter({
        'OpenClaw': [
            NormalizedSearchResult(
                title='OpenClaw Docs',
                url='https://docs.openclaw.ai/tools/web',
                provider='fallback',
                rank=1,
            )
        ]
    })
    registry.register_adapter('bing_html_fallback', first)
    registry.register_adapter('google_html_fallback', second)
    runtime = SearchExecutionRuntime(
        registry=registry,
        selector=SearchProviderSelector(registry),
        planner=SearchQueryPlanner(official_domains=[]),
        prioritizer=SourcePrioritizer(official_domains=['docs.openclaw.ai'], trusted_domains=[]),
        default_provider='bing_html_fallback',
    )

    import asyncio
    response = asyncio.run(runtime.execute(query='OpenClaw', provider_override=None, limit=5))

    assert response.provider == 'google_html_fallback'
    assert response.results[0].title == 'OpenClaw Docs'
    assert first.calls == ['OpenClaw']
    assert second.calls == ['OpenClaw']


def test_runtime_can_accept_relevant_unknown_sources_when_grounding_enabled() -> None:
    registry = SearchProviderRegistry()
    first = _FakeAdapter({
        '明天上海天气': [
            NormalizedSearchResult(
                title='上海明天天气预报',
                url='https://weather.example.com/shanghai-tomorrow',
                snippet='上海明天多云，气温12℃-19℃，降雨概率20%。',
                provider='bing_html_fallback',
                rank=1,
                source_tier=SourceTier.UNKNOWN,
            ),
        ]
    })
    second = _FakeAdapter({
        '明天上海天气': [
            NormalizedSearchResult(
                title='上海天气预报',
                url='https://www.weather.com.cn/weather/101020100.shtml',
                snippet='上海明日天气、气温与降水概率。',
                provider='duckduckgo_html_fallback',
                rank=1,
                source_tier=SourceTier.OFFICIAL,
            ),
        ]
    })
    registry.register_adapter('bing_html_fallback', first)
    registry.register_adapter('duckduckgo_html_fallback', second)
    runtime = SearchExecutionRuntime(
        registry=registry,
        selector=SearchProviderSelector(registry),
        planner=SearchQueryPlanner(official_domains=['weather.com.cn']),
        prioritizer=SourcePrioritizer(official_domains=['weather.com.cn'], trusted_domains=[]),
        default_provider='bing_html_fallback',
    )

    import asyncio
    response = asyncio.run(runtime.execute(query='明天上海天气', provider_override=None, limit=5, require_grounding=True))

    assert response.provider == 'bing_html_fallback'
    assert response.results[0].title == '上海明天天气预报'
    assert first.calls == ['明天上海天气']
    assert second.calls == ['明天上海天气']


def test_runtime_prefers_grounding_provider_with_citations_when_available() -> None:
    registry = SearchProviderRegistry()
    grounded = _FakeGroundingAdapter(
        {
            '明天上海天气': GroundedSearchResponse(
                provider='openrouter_grounding',
                query='明天上海天气',
                summary='明天上海多云转小雨，14℃到23℃。',
                citations=[
                    SearchCitation(
                        title='上海天气预报',
                        url='https://www.weather.com.cn/weather/101020100.shtml',
                    )
                ],
                results=[
                    NormalizedSearchResult(
                        title='上海天气预报',
                        url='https://www.weather.com.cn/weather/101020100.shtml',
                        snippet='2日（明天）多云转小雨，23℃/14℃。',
                        provider='openrouter_grounding',
                        rank=1,
                    )
                ],
                confidence=0.92,
            )
        }
    )
    fallback = _FakeAdapter({'明天上海天气': []})
    registry.register_adapter('openrouter_grounding', grounded)
    registry.register_adapter('bing_html_fallback', fallback)
    runtime = SearchExecutionRuntime(
        registry=registry,
        selector=SearchProviderSelector(registry),
        planner=SearchQueryPlanner(official_domains=['weather.com.cn']),
        prioritizer=SourcePrioritizer(official_domains=['weather.com.cn'], trusted_domains=[]),
        default_provider='openrouter_grounding',
    )

    import asyncio
    response = asyncio.run(
        runtime.execute(
            query='明天上海天气',
            provider_override=None,
            limit=5,
            require_grounding=True,
            overall_timeout_seconds=8.0,
        )
    )

    assert response.provider == 'openrouter_grounding'
    assert '23℃' in response.summary
    assert response.citations and response.citations[0]['url'].startswith('https://www.weather.com.cn')
    assert grounded.calls == ['明天上海天气']
