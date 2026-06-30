"""HTTP tests for /api/polymarket/* (mocked data source + analyzer)."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.utils.auth import generate_token


@pytest.fixture
def auth_headers(monkeypatch):
    monkeypatch.setattr("app.utils.auth._verify_token_version", lambda *_: True)
    token = generate_token(user_id=42, username="testuser", role="user")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


SAMPLE_MARKET = {
    "market_id": "12345",
    "question": "Will BTC reach 100k in 2026?",
    "category": "crypto",
    "current_probability": 62.5,
    "volume_24h": 50000.0,
    "liquidity": 12000.0,
    "polymarket_url": "https://polymarket.com/event/btc-100k-2026",
    "slug": "btc-100k-2026",
}

SAMPLE_ANALYSIS = {
    "market_id": "12345",
    "ai_predicted_probability": 70.0,
    "market_probability": 62.5,
    "divergence": 7.5,
    "recommendation": "YES",
    "confidence_score": 72.0,
    "reasoning": "Momentum and macro tailwinds.",
    "key_factors": ["ETF flows"],
    "risk_factors": ["Regulation"],
    "related_assets": ["BTC/USDT"],
    "risk_level": "medium",
    "opportunity_score": 65.0,
    "market": SAMPLE_MARKET,
}


@patch("app.services.polymarket_analyzer.PolymarketAnalyzer")
@patch("app.routes.polymarket.get_billing_service")
@patch("app.routes.polymarket.polymarket_source")
def test_analyze_polymarket_success(mock_source, mock_billing, mock_analyzer_cls, client, auth_headers):
    mock_source.get_market_details.return_value = SAMPLE_MARKET
    mock_analyzer_cls.return_value.analyze_market.return_value = SAMPLE_ANALYSIS

    billing = MagicMock()
    billing.is_billing_enabled.return_value = False
    mock_billing.return_value = billing

    resp = client.post(
        "/api/polymarket/analyze",
        json={"input": "https://polymarket.com/event/btc-100k-2026", "language": "en-US"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["code"] == 1
    assert body["data"]["market"]["market_id"] == "12345"
    assert body["data"]["analysis"]["recommendation"] == "YES"
    mock_analyzer_cls.return_value.analyze_market.assert_called_once()
    call_kw = mock_analyzer_cls.return_value.analyze_market.call_args.kwargs
    assert call_kw["use_cache"] is False
    assert call_kw["market_data"] == SAMPLE_MARKET
    billing.check_and_consume.assert_not_called()


@patch("app.services.polymarket_analyzer.PolymarketAnalyzer")
@patch("app.routes.polymarket.get_billing_service")
@patch("app.routes.polymarket.polymarket_source")
def test_analyze_polymarket_charges_only_after_success(
    mock_source, mock_billing, mock_analyzer_cls, client, auth_headers
):
    mock_source.get_market_details.return_value = SAMPLE_MARKET
    mock_analyzer_cls.return_value.analyze_market.return_value = SAMPLE_ANALYSIS

    billing = MagicMock()
    billing.is_billing_enabled.return_value = True
    billing.get_feature_cost.return_value = 15
    billing.get_user_credits.return_value = Decimal("100")
    billing.check_and_consume.return_value = (True, "consumed")
    mock_billing.return_value = billing

    resp = client.post(
        "/api/polymarket/analyze",
        json={"input": "https://polymarket.com/event/btc-100k-2026", "language": "en-US"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["code"] == 1
    assert body["data"]["credits_charged"] == 15
    billing.check_and_consume.assert_called_once_with(
        user_id=42,
        feature="polymarket_deep_analysis",
        reference_id="polymarket_12345",
    )


@patch("app.services.polymarket_analyzer.PolymarketAnalyzer")
@patch("app.routes.polymarket.get_billing_service")
@patch("app.routes.polymarket.polymarket_source")
def test_analyze_polymarket_failure_does_not_consume_credits(
    mock_source, mock_billing, mock_analyzer_cls, client, auth_headers
):
    mock_source.get_market_details.return_value = SAMPLE_MARKET
    mock_analyzer_cls.return_value.analyze_market.return_value = {
        "error": "LLM timeout",
        "market_id": "12345",
    }

    billing = MagicMock()
    billing.is_billing_enabled.return_value = True
    billing.get_feature_cost.return_value = 15
    billing.get_user_credits.return_value = Decimal("100")
    mock_billing.return_value = billing

    resp = client.post(
        "/api/polymarket/analyze",
        json={"input": "https://polymarket.com/event/btc-100k-2026", "language": "en-US"},
        headers=auth_headers,
    )
    assert resp.status_code == 500
    body = resp.get_json()
    assert body["code"] == 0
    billing.check_and_consume.assert_not_called()


@patch("app.routes.polymarket.polymarket_source")
def test_analyze_polymarket_ambiguous_title_returns_409(mock_source, client, auth_headers):
    mock_source.search_markets.return_value = [
        {**SAMPLE_MARKET, "market_id": "1", "question": "Market A"},
        {**SAMPLE_MARKET, "market_id": "2", "question": "Market B"},
    ]

    resp = client.post(
        "/api/polymarket/analyze",
        json={"input": "some vague title"},
        headers=auth_headers,
    )
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["code"] == 0
    assert "candidates" in (body.get("data") or {})


def test_analyze_polymarket_requires_auth(client):
    resp = client.post(
        "/api/polymarket/analyze",
        json={"input": "https://polymarket.com/event/btc-100k-2026"},
    )
    assert resp.status_code == 401


def test_openapi_includes_polymarket_paths(app):
    from app.openapi import get_openapi_api

    api = get_openapi_api(app)
    with app.app_context():
        spec = api.spec.to_dict()
    paths = spec.get("paths", {})
    assert "/api/polymarket/analyze" in paths
    assert "/api/polymarket/history" in paths
    assert "post" in paths["/api/polymarket/analyze"]
    assert "get" in paths["/api/polymarket/history"]
