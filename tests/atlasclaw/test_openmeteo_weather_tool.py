# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.atlasclaw.tools.web import openmeteo_weather_tool as weather_module


@pytest.mark.asyncio
async def test_openmeteo_weather_tool_returns_forecast_markdown(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []

    async def _fake_request_json(*, url: str, params: dict, timeout_seconds: float = 12.0):
        _ = timeout_seconds
        calls.append((url, params))
        if "geocoding-api" in url:
            return {
                "results": [
                    {
                        "name": "Shanghai",
                        "country": "China",
                        "admin1": "Shanghai",
                        "latitude": 31.23,
                        "longitude": 121.47,
                    }
                ]
            }
        return {
            "current": {
                "time": "2026-04-03T09:00",
                "temperature_2m": 18.2,
                "weather_code": 3,
                "wind_speed_10m": 12.0,
            },
            "current_units": {
                "temperature_2m": "°C",
                "wind_speed_10m": "km/h",
            },
            "daily": {
                "time": ["2026-04-03", "2026-04-04"],
                "weather_code": [3, 61],
                "temperature_2m_max": [22.1, 19.0],
                "temperature_2m_min": [14.0, 12.5],
                "precipitation_sum": [0.0, 4.2],
                "precipitation_probability_max": [20, 75],
                "wind_speed_10m_max": [18.0, 21.0],
            },
            "daily_units": {
                "temperature_2m_max": "°C",
                "precipitation_sum": "mm",
                "precipitation_probability_max": "%",
                "wind_speed_10m_max": "km/h",
            },
        }

    monkeypatch.setattr(weather_module, "_request_json", _fake_request_json)

    result = await weather_module.openmeteo_weather_tool(
        ctx=SimpleNamespace(),
        location="Shanghai",
        days=2,
    )

    assert result["is_error"] is False
    text = result["content"][0]["text"]
    assert "Weather for Shanghai, China" in text
    assert "Daily forecast:" in text
    assert "| 2026-04-04 | Slight rain" in text
    assert result["details"]["provider"] == "open-meteo"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_openmeteo_weather_tool_uses_start_end_without_forecast_days_when_target_date(monkeypatch) -> None:
    observed_forecast_params: dict = {}

    async def _fake_request_json(*, url: str, params: dict, timeout_seconds: float = 12.0):
        _ = timeout_seconds
        if "geocoding-api" in url:
            return {
                "results": [
                    {
                        "name": "Shanghai",
                        "country": "China",
                        "admin1": "Shanghai",
                        "latitude": 31.23,
                        "longitude": 121.47,
                    }
                ]
            }
        observed_forecast_params.update(params)
        return {
            "daily": {
                "time": ["2026-04-04"],
                "weather_code": [3],
                "temperature_2m_max": [23.0],
                "temperature_2m_min": [14.0],
                "precipitation_sum": [0.0],
                "precipitation_probability_max": [20],
                "wind_speed_10m_max": [15.0],
            },
            "daily_units": {
                "temperature_2m_max": "°C",
                "precipitation_sum": "mm",
                "precipitation_probability_max": "%",
                "wind_speed_10m_max": "km/h",
            },
        }

    monkeypatch.setattr(weather_module, "_request_json", _fake_request_json)

    result = await weather_module.openmeteo_weather_tool(
        ctx=SimpleNamespace(),
        location="Shanghai",
        target_date="2026-04-04",
        days=1,
    )

    assert result["is_error"] is False
    assert observed_forecast_params.get("start_date") == "2026-04-04"
    assert observed_forecast_params.get("end_date") == "2026-04-04"
    assert "forecast_days" not in observed_forecast_params


@pytest.mark.asyncio
async def test_openmeteo_weather_tool_returns_error_when_location_not_found(monkeypatch) -> None:
    async def _fake_request_json(*, url: str, params: dict, timeout_seconds: float = 12.0):
        _ = (url, params, timeout_seconds)
        return {"results": []}

    monkeypatch.setattr(weather_module, "_request_json", _fake_request_json)

    result = await weather_module.openmeteo_weather_tool(
        ctx=SimpleNamespace(),
        location="UnknownCity",
    )

    assert result["is_error"] is True
    assert "No location found" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_openmeteo_weather_tool_validates_target_date_format() -> None:
    result = await weather_module.openmeteo_weather_tool(
        ctx=SimpleNamespace(),
        location="Shanghai",
        target_date="2026/04/03",
    )

    assert result["is_error"] is True
    assert "YYYY-MM-DD" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_openmeteo_weather_tool_prefers_exact_admin_match_over_first_geocoding_hit(
    monkeypatch,
) -> None:
    observed_forecast_params: dict = {}
    observed_geocoding_queries: list[dict] = []

    async def _fake_request_json(*, url: str, params: dict, timeout_seconds: float = 12.0):
        _ = timeout_seconds
        if "geocoding-api" in url:
            observed_geocoding_queries.append(dict(params))
            if params.get("name") == "北京":
                return {
                    "results": [
                        {
                            "name": "北京",
                            "country": "中国",
                            "country_code": "CN",
                            "admin1": "重庆市",
                            "latitude": 29.4316,
                            "longitude": 106.9123,
                            "population": 5000,
                        }
                    ]
                }
            return {
                "results": [
                    {
                        "name": "北京市",
                        "country": "中国",
                        "country_code": "CN",
                        "admin1": "北京市",
                        "latitude": 39.9042,
                        "longitude": 116.4074,
                        "population": 21893095,
                    }
                ]
            }
        observed_forecast_params.update(params)
        return {
            "daily": {
                "time": ["2026-04-15"],
                "weather_code": [3],
                "temperature_2m_max": [26.0],
                "temperature_2m_min": [14.0],
                "precipitation_sum": [0.0],
                "precipitation_probability_max": [15],
                "wind_speed_10m_max": [12.0],
            },
            "daily_units": {
                "temperature_2m_max": "°C",
                "precipitation_sum": "mm",
                "precipitation_probability_max": "%",
                "wind_speed_10m_max": "km/h",
            },
        }

    monkeypatch.setattr(weather_module, "_request_json", _fake_request_json)

    result = await weather_module.openmeteo_weather_tool(
        ctx=SimpleNamespace(),
        location="北京",
        days=1,
    )

    assert result["is_error"] is False
    assert [query["name"] for query in observed_geocoding_queries] == ["北京", "北京市"]
    assert all(query["count"] == 8 for query in observed_geocoding_queries)
    assert observed_forecast_params["latitude"] == 39.9042
    assert observed_forecast_params["longitude"] == 116.4074
    assert result["details"]["resolved_location"]["admin1"] == "北京市"
    assert result["details"]["resolved_location"]["name"] == "北京市"
