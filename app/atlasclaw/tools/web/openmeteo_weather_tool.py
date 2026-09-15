# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Dedicated weather tool powered by Open-Meteo APIs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone
import re
from typing import Any, Optional, TYPE_CHECKING

import httpx

from app.atlasclaw.tools.base import ToolResult

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from app.atlasclaw.core.deps import SkillDeps


_GEOCODING_ENDPOINT = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_ENDPOINT = "https://api.open-meteo.com/v1/forecast"
_WEATHER_CODE_TEXT: dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snow fall",
    73: "Moderate snow fall",
    75: "Heavy snow fall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


def _weather_text(code: Any) -> str:
    try:
        value = int(code)
    except (TypeError, ValueError):
        return "Unknown"
    return _WEATHER_CODE_TEXT.get(value, f"Code {value}")


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_location_token(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return ""
    normalized = normalized.replace(" ", "")
    for suffix in (
        "特别行政区",
        "自治区",
        "自治州",
        "省",
        "市",
        "区",
        "县",
    ):
        if normalized.endswith(suffix) and len(normalized) > len(suffix):
            normalized = normalized[: -len(suffix)]
            break
    return normalized


def _is_cjk_location_query(value: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", str(value or "")))


def _needs_city_suffix_retry(*, query: str, results: list[dict[str, Any]]) -> bool:
    normalized_query = _normalize_location_token(query)
    if not normalized_query or str(query or "").strip().endswith("市"):
        return False
    if not _is_cjk_location_query(str(query or "")):
        return False
    for item in results:
        if not isinstance(item, dict):
            continue
        normalized_name = _normalize_location_token(item.get("name"))
        normalized_admin1 = _normalize_location_token(item.get("admin1"))
        if normalized_name == normalized_query and normalized_admin1 == normalized_query:
            return False
    return True


def _select_best_geocoding_result(
    *,
    results: list[dict[str, Any]],
    query: str,
    country_code: Optional[str],
) -> dict[str, Any]:
    normalized_query = _normalize_location_token(query)
    normalized_country_code = str(country_code or "").strip().upper()

    def _score(item: dict[str, Any]) -> tuple[int, int, int, int]:
        normalized_name = _normalize_location_token(item.get("name"))
        normalized_admin1 = _normalize_location_token(item.get("admin1"))
        score = 0
        if normalized_country_code and str(item.get("country_code", "") or "").strip().upper() == normalized_country_code:
            score += 30
        if normalized_name and normalized_name == normalized_query:
            score += 100
        elif normalized_query and normalized_query and normalized_query in normalized_name:
            score += 40
        if normalized_admin1 and normalized_admin1 == normalized_query:
            score += 80
        elif normalized_query and normalized_query in normalized_admin1:
            score += 20
        population = _safe_int(item.get("population")) or 0
        rank = _safe_int(item.get("rank")) or 0
        return (score, population, rank, -results.index(item))

    return max(results, key=_score)


async def _request_json(
    *,
    url: str,
    params: dict[str, Any],
    timeout_seconds: float = 12.0,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            return payload
        raise RuntimeError("Weather provider returned an invalid JSON payload.")


def _resolve_date_window(
    *,
    target_date: Optional[str],
    days: int,
) -> tuple[Optional[str], Optional[str], int]:
    normalized_days = max(1, min(int(days), 16))
    if not target_date:
        return None, None, normalized_days

    try:
        parsed_target = datetime.strptime(target_date, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("target_date must use YYYY-MM-DD format.") from exc

    end_date = parsed_target + timedelta(days=normalized_days - 1)
    return parsed_target.isoformat(), end_date.isoformat(), normalized_days


def _build_markdown_summary(
    *,
    location_label: str,
    forecast: dict[str, Any],
    temperature_unit: str,
    wind_speed_unit: str,
    precipitation_unit: str,
) -> str:
    lines: list[str] = [f"Weather for {location_label}", ""]

    current = forecast.get("current", {})
    current_units = forecast.get("current_units", {})
    if isinstance(current, dict) and current:
        current_temp = current.get("temperature_2m")
        current_wind = current.get("wind_speed_10m")
        current_code = current.get("weather_code")
        current_time = current.get("time")
        current_items: list[str] = []
        if current_time:
            current_items.append(f"time={current_time}")
        if current_temp is not None:
            unit = current_units.get("temperature_2m") or temperature_unit
            current_items.append(f"temp={current_temp}{unit}")
        if current_wind is not None:
            unit = current_units.get("wind_speed_10m") or wind_speed_unit
            current_items.append(f"wind={current_wind}{unit}")
        if current_code is not None:
            current_items.append(f"condition={_weather_text(current_code)}")
        if current_items:
            lines.append("Current:")
            lines.append(f"- {' | '.join(current_items)}")
            lines.append("")

    daily = forecast.get("daily", {})
    if not isinstance(daily, dict) or not daily:
        lines.append("No daily forecast data was returned.")
        return "\n".join(lines)

    dates = daily.get("time") or []
    max_temps = daily.get("temperature_2m_max") or []
    min_temps = daily.get("temperature_2m_min") or []
    weather_codes = daily.get("weather_code") or []
    precip_sum = daily.get("precipitation_sum") or []
    precip_prob = daily.get("precipitation_probability_max") or []
    wind_max = daily.get("wind_speed_10m_max") or []
    daily_units = forecast.get("daily_units", {})

    temp_unit = daily_units.get("temperature_2m_max") or temperature_unit
    precip_unit = daily_units.get("precipitation_sum") or precipitation_unit
    wind_unit = daily_units.get("wind_speed_10m_max") or wind_speed_unit
    precip_prob_unit = daily_units.get("precipitation_probability_max") or "%"

    lines.append("Daily forecast:")
    lines.append("| Date | Condition | Min | Max | Precipitation | Precip Prob | Wind Max |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")

    row_count = max(
        len(dates),
        len(max_temps),
        len(min_temps),
        len(weather_codes),
        len(precip_sum),
        len(precip_prob),
        len(wind_max),
    )
    for index in range(row_count):
        day = dates[index] if index < len(dates) else "-"
        condition = _weather_text(weather_codes[index] if index < len(weather_codes) else None)
        low = min_temps[index] if index < len(min_temps) else "-"
        high = max_temps[index] if index < len(max_temps) else "-"
        rain = precip_sum[index] if index < len(precip_sum) else "-"
        rain_prob = precip_prob[index] if index < len(precip_prob) else "-"
        wind = wind_max[index] if index < len(wind_max) else "-"
        lines.append(
            f"| {day} | {condition} | {low}{temp_unit if low != '-' else ''} | "
            f"{high}{temp_unit if high != '-' else ''} | {rain}{precip_unit if rain != '-' else ''} | "
            f"{rain_prob}{precip_prob_unit if rain_prob != '-' else ''} | {wind}{wind_unit if wind != '-' else ''} |"
        )

    return "\n".join(lines)


async def openmeteo_weather_tool(
    ctx: "RunContext[SkillDeps]",
    location: str,
    target_date: Optional[str] = None,
    days: int = 3,
    country_code: Optional[str] = None,
    timezone: str = "auto",
    temperature_unit: str = "celsius",
    wind_speed_unit: str = "kmh",
    precipitation_unit: str = "mm",
) -> dict:
    """Fetch weather forecast using Open-Meteo geocoding + forecast APIs.

    Args:
        location: City or location name, for example "Shanghai".
        target_date: Optional start date in YYYY-MM-DD.
        days: Number of days to return (1-16).
        country_code: Optional ISO 3166 alpha-2 hint such as "CN".
        timezone: IANA timezone or "auto".
        temperature_unit: celsius or fahrenheit.
        wind_speed_unit: kmh, ms, mph, or kn.
        precipitation_unit: mm or inch.
    """
    _ = ctx
    normalized_location = str(location or "").strip()
    if not normalized_location:
        return ToolResult.error(
            "location is required for weather lookup.",
            details={"tool": "openmeteo_weather"},
        ).to_dict()

    try:
        start_date, end_date, normalized_days = _resolve_date_window(
            target_date=target_date,
            days=days,
        )
    except ValueError as exc:
        return ToolResult.error(
            str(exc),
            details={"tool": "openmeteo_weather", "location": normalized_location},
        ).to_dict()

    geocoding_params: dict[str, Any] = {
        "name": normalized_location,
        "count": 8,
        "language": "zh",
        "format": "json",
    }
    if country_code:
        geocoding_params["countryCode"] = country_code

    try:
        geocoding_payload = await _request_json(
            url=_GEOCODING_ENDPOINT,
            params=geocoding_params,
        )
    except Exception as exc:
        return ToolResult.error(
            f"Geocoding failed: {type(exc).__name__}: {exc}",
            details={"tool": "openmeteo_weather", "location": normalized_location},
        ).to_dict()

    results = geocoding_payload.get("results")
    if not isinstance(results, list) or not results:
        return ToolResult.error(
            f"No location found for '{normalized_location}'.",
            details={"tool": "openmeteo_weather", "location": normalized_location},
        ).to_dict()

    normalized_results = [item for item in results if isinstance(item, dict)]
    if _needs_city_suffix_retry(query=normalized_location, results=normalized_results):
        retry_params = dict(geocoding_params)
        retry_params["name"] = f"{normalized_location}市"
        try:
            retry_payload = await _request_json(
                url=_GEOCODING_ENDPOINT,
                params=retry_params,
            )
        except Exception:
            retry_payload = {}
        retry_results = retry_payload.get("results")
        if isinstance(retry_results, list) and retry_results:
            normalized_results.extend(
                item for item in retry_results if isinstance(item, dict)
            )
    if not normalized_results:
        return ToolResult.error(
            f"No location found for '{normalized_location}'.",
            details={"tool": "openmeteo_weather", "location": normalized_location},
        ).to_dict()

    top_hit = _select_best_geocoding_result(
        results=normalized_results,
        query=normalized_location,
        country_code=country_code,
    )
    latitude = _safe_float(top_hit.get("latitude"))
    longitude = _safe_float(top_hit.get("longitude"))
    resolved_name = str(top_hit.get("name", normalized_location) or normalized_location)
    resolved_country = str(top_hit.get("country", "") or "")
    resolved_admin1 = str(top_hit.get("admin1", "") or "")

    if latitude is None or longitude is None:
        return ToolResult.error(
            f"Resolved location '{resolved_name}' does not include valid coordinates.",
            details={"tool": "openmeteo_weather", "location": normalized_location},
        ).to_dict()

    daily_params = ",".join(
        [
            "weather_code",
            "temperature_2m_max",
            "temperature_2m_min",
            "precipitation_sum",
            "precipitation_probability_max",
            "wind_speed_10m_max",
        ]
    )
    current_params = ",".join(
        [
            "temperature_2m",
            "apparent_temperature",
            "weather_code",
            "wind_speed_10m",
            "relative_humidity_2m",
        ]
    )

    forecast_params: dict[str, Any] = {
        "latitude": latitude,
        "longitude": longitude,
        "timezone": timezone,
        "temperature_unit": temperature_unit,
        "wind_speed_unit": wind_speed_unit,
        "precipitation_unit": precipitation_unit,
        "current": current_params,
        "daily": daily_params,
    }
    if start_date and end_date:
        forecast_params["start_date"] = start_date
        forecast_params["end_date"] = end_date
    else:
        forecast_params["forecast_days"] = normalized_days

    try:
        forecast_payload = await _request_json(
            url=_FORECAST_ENDPOINT,
            params=forecast_params,
        )
    except Exception as exc:
        return ToolResult.error(
            f"Forecast lookup failed: {type(exc).__name__}: {exc}",
            details={
                "tool": "openmeteo_weather",
                "location": normalized_location,
                "latitude": latitude,
                "longitude": longitude,
            },
        ).to_dict()

    location_parts = [resolved_name]
    if resolved_admin1 and resolved_admin1.lower() != resolved_name.lower():
        location_parts.append(resolved_admin1)
    if resolved_country:
        location_parts.append(resolved_country)
    location_label = ", ".join(location_parts)

    markdown = _build_markdown_summary(
        location_label=location_label,
        forecast=forecast_payload,
        temperature_unit=temperature_unit,
        wind_speed_unit=wind_speed_unit,
        precipitation_unit=precipitation_unit,
    )

    details = {
        "provider": "open-meteo",
        "query": {
            "location": normalized_location,
            "target_date": target_date,
            "days": normalized_days,
            "country_code": country_code,
            "timezone": timezone,
        },
        "resolved_location": {
            "name": resolved_name,
            "country": resolved_country,
            "admin1": resolved_admin1,
            "latitude": latitude,
            "longitude": longitude,
        },
        "sources": [
            {
                "label": "Open-Meteo Forecast API",
                "url": _FORECAST_ENDPOINT,
            },
            {
                "label": "Open-Meteo Geocoding API",
                "url": _GEOCODING_ENDPOINT,
            },
        ],
        "generated_at": datetime.now(dt_timezone.utc).isoformat(timespec="seconds"),
    }
    return ToolResult.text(markdown, details=details).to_dict()
