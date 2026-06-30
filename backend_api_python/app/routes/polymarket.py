"""
Polymarket prediction market API routes (read-only analysis, no trading).
"""
from __future__ import annotations

import json
import re
from decimal import Decimal

from flask import g, jsonify, request

from app.openapi.blueprint import HumanBlueprint as Blueprint
from app.utils.auth import login_required
from app.utils.db import get_db_connection
from app.utils.logger import get_logger
from app.data_sources.polymarket import PolymarketDataSource
from app.services.billing_service import get_billing_service

logger = get_logger(__name__)

polymarket_blp = Blueprint("polymarket", __name__)

polymarket_source = PolymarketDataSource()

_URL_PATTERNS = [
    r"polymarket\.com(?:/[a-z]{2}(?:-[A-Z]{2})?)?/event/([^/?#]+)",
    r"polymarket\.com(?:/[a-z]{2}(?:-[A-Z]{2})?)?/markets/(\d+)",
    r"polymarket\.com(?:/[a-z]{2}(?:-[A-Z]{2})?)?/market/(\d+)",
]


def _parse_polymarket_input(input_text: str) -> tuple[str | None, str | None]:
    """Return (market_id, slug) parsed from URL or plain text."""
    for pattern in _URL_PATTERNS:
        match = re.search(pattern, input_text)
        if not match:
            continue
        extracted = match.group(1)
        if extracted.isdigit():
            return extracted, None
        return None, extracted
    return None, None


@polymarket_blp.route("/analyze", methods=["POST"])
@login_required
def analyze_polymarket():
    """
    Analyze a Polymarket prediction market from a URL or title.

    POST /api/polymarket/analyze
    Body: { "input": "<url or title>", "language": "zh-CN", "model": "<optional>" }
    """
    try:
        from app.services.polymarket_analyzer import PolymarketAnalyzer

        user_id = getattr(g, "user_id", None)
        if not user_id:
            return jsonify({"code": 0, "msg": "User not authenticated", "data": None}), 401

        data = request.get_json() or {}
        input_text = (data.get("input") or "").strip()
        language = data.get("language", "zh-CN")
        model = data.get("model")

        if not input_text:
            return jsonify(
                {
                    "code": 0,
                    "msg": "Input is required (Polymarket URL or market title)",
                    "data": None,
                }
            ), 400

        market_id, slug = _parse_polymarket_input(input_text)
        market = None

        if market_id:
            market = polymarket_source.get_market_details(market_id)
        elif slug:
            market = polymarket_source.get_market_details(slug)
            if market and not market_id:
                market_id = market.get("market_id")
        elif "polymarket.com" in input_text.lower():
            return jsonify(
                {
                    "code": 0,
                    "msg": (
                        "Could not parse a market slug from this Polymarket URL. "
                        "Please paste the URL directly from a market page "
                        "(looks like https://polymarket.com/event/<slug>)."
                    ),
                    "data": None,
                }
            ), 400
        else:
            logger.info("Searching for market by title: %s", input_text[:100])
            search_results = polymarket_source.search_markets(input_text, limit=5)
            input_lower = input_text.lower()
            confident_match = next(
                (
                    r
                    for r in search_results
                    if input_lower in (r.get("question") or "").lower()
                    or input_lower == (r.get("slug") or "").lower()
                ),
                None,
            )
            if confident_match:
                market = confident_match
                market_id = market.get("market_id")
            elif search_results:
                return jsonify(
                    {
                        "code": 0,
                        "msg": "Multiple possible markets matched. Please paste the exact Polymarket URL.",
                        "data": {
                            "candidates": [
                                {
                                    "market_id": r.get("market_id"),
                                    "question": r.get("question"),
                                    "polymarket_url": r.get("polymarket_url"),
                                }
                                for r in search_results[:5]
                            ]
                        },
                    }
                ), 409

        if not market:
            return jsonify(
                {"code": 0, "msg": "Market not found. Please check the URL or title.", "data": None}
            ), 404

        market_id = market_id or market.get("market_id")
        if not market_id:
            return jsonify({"code": 0, "msg": "Invalid market data", "data": None}), 400

        billing = get_billing_service()
        cost = 0

        if billing.is_billing_enabled():
            cost = billing.get_feature_cost("polymarket_deep_analysis")
            if cost > 0:
                user_credits = billing.get_user_credits(user_id)
                if user_credits < Decimal(str(cost)):
                    return jsonify(
                        {
                            "code": 0,
                            "msg": "Insufficient credits",
                            "data": {
                                "required": cost,
                                "current": float(user_credits),
                                "shortage": float(Decimal(str(cost)) - user_credits),
                            },
                        }
                    ), 400

                success, error_msg = billing.check_and_consume(
                    user_id=user_id,
                    feature="polymarket_deep_analysis",
                    reference_id=f"polymarket_{market_id}",
                )
                if not success:
                    if error_msg.startswith("insufficient_credits"):
                        parts = error_msg.split(":")
                        if len(parts) >= 3:
                            return jsonify(
                                {
                                    "code": 0,
                                    "msg": "Insufficient credits",
                                    "data": {
                                        "required": float(parts[2]),
                                        "current": float(parts[1]),
                                        "shortage": float(Decimal(parts[2]) - Decimal(parts[1])),
                                    },
                                }
                            ), 400
                    return jsonify(
                        {"code": 0, "msg": f"Failed to deduct credits: {error_msg}", "data": None}
                    ), 500

        analyzer = PolymarketAnalyzer()
        analysis_result = analyzer.analyze_market(
            market_id,
            user_id=user_id,
            use_cache=False,
            language=language,
            model=model,
            market_data=market,
        )

        if analysis_result.get("error"):
            return jsonify(
                {"code": 0, "msg": analysis_result.get("error", "Analysis failed"), "data": None}
            ), 500

        remaining_credits = float(billing.get_user_credits(user_id)) if billing.is_billing_enabled() else 0

        return jsonify(
            {
                "code": 1,
                "msg": "success",
                "data": {
                    "market": market,
                    "analysis": analysis_result,
                    "credits_charged": cost,
                    "remaining_credits": remaining_credits,
                },
            }
        )

    except Exception as e:
        logger.error("Polymarket analyze API failed: %s", e, exc_info=True)
        return jsonify({"code": 0, "msg": str(e), "data": None}), 500


@polymarket_blp.route("/history", methods=["GET"])
@login_required
def get_polymarket_history():
    """GET /api/polymarket/history?page=1&page_size=20"""
    try:
        user_id = g.user_id
        page = request.args.get("page", 1, type=int)
        page_size = min(request.args.get("page_size", 20, type=int), 100)
        offset = (page - 1) * page_size

        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT COUNT(*) AS total
                FROM qd_analysis_tasks
                WHERE user_id = %s AND market = 'Polymarket'
                """,
                (user_id,),
            )
            total_row = cur.fetchone()
            total = total_row["total"] if total_row else 0

            cur.execute(
                """
                SELECT
                    t.id,
                    t.symbol AS market_id,
                    t.model,
                    t.language,
                    t.status,
                    t.created_at,
                    t.completed_at,
                    t.result_json
                FROM qd_analysis_tasks t
                WHERE t.user_id = %s AND market = 'Polymarket'
                ORDER BY t.created_at DESC
                LIMIT %s OFFSET %s
                """,
                (user_id, page_size, offset),
            )
            rows = cur.fetchall() or []
            cur.close()

        items = []
        for row in rows:
            result_json = row.get("result_json", "{}")
            try:
                result_data = json.loads(result_json) if result_json else {}
            except json.JSONDecodeError:
                result_data = {}

            market_data = result_data.get("market", {})
            analysis_data = result_data.get("analysis", {})

            items.append(
                {
                    "id": row.get("id"),
                    "market_id": row.get("market_id"),
                    "market_title": (
                        market_data.get("question")
                        or market_data.get("title")
                        or f"Market {row.get('market_id')}"
                    ),
                    "market_url": market_data.get("polymarket_url"),
                    "ai_predicted_probability": analysis_data.get("ai_predicted_probability"),
                    "market_probability": analysis_data.get("market_probability"),
                    "recommendation": analysis_data.get("recommendation"),
                    "opportunity_score": analysis_data.get("opportunity_score"),
                    "confidence_score": analysis_data.get("confidence_score"),
                    "status": row.get("status"),
                    "created_at": row.get("created_at"),
                    "completed_at": row.get("completed_at"),
                }
            )

        return jsonify(
            {
                "code": 1,
                "msg": "success",
                "data": {
                    "items": items,
                    "total": total,
                    "page": page,
                    "page_size": page_size,
                    "total_pages": (total + page_size - 1) // page_size,
                },
            }
        )

    except Exception as e:
        logger.error("Get Polymarket history failed: %s", e, exc_info=True)
        return jsonify({"code": 0, "msg": str(e), "data": None}), 500
