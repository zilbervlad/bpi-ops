from datetime import datetime
import json

from flask import Blueprint, jsonify, request

from app import db
from app.models import PerkOffer, PerkEvent


perks_api_bp = Blueprint(
    "perks_api",
    __name__,
    url_prefix="/api/integrations/bpi-ops/perks",
)


def offer_is_current(offer):
    now = datetime.utcnow()

    if offer.status != "active":
        return False

    if offer.starts_at and offer.starts_at > now:
        return False

    if offer.ends_at and offer.ends_at < now:
        return False

    if offer.partner and offer.partner.status != "active":
        return False

    return True


def serialize_offer(offer):
    partner = offer.partner

    return {
        "id": offer.id,
        "partner_id": partner.id if partner else None,
        "partner_name": partner.name if partner else "",
        "partner_logo_url": partner.logo_url if partner else None,
        "partner_website_url": partner.website_url if partner else None,
        "title": offer.title,
        "short_description": offer.short_description,
        "description": offer.description,
        "category": offer.category or (partner.category if partner else None),
        "image_url": offer.image_url,
        "button_text": offer.button_text or "View Offer",
        "button_url": offer.button_url,
        "phone_number": offer.phone_number,
        "redemption_instructions": offer.redemption_instructions,
        "terms": offer.terms,
        "featured": bool(offer.featured),
        "sort_order": offer.sort_order,
    }


@perks_api_bp.get("")
def list_perks():
    offers = (
        PerkOffer.query
        .order_by(
            PerkOffer.featured.desc(),
            PerkOffer.sort_order.asc(),
            PerkOffer.title.asc(),
        )
        .all()
    )

    visible_offers = [offer for offer in offers if offer_is_current(offer)]

    return jsonify({
        "ok": True,
        "perks": [serialize_offer(offer) for offer in visible_offers],
    })


@perks_api_bp.get("/<int:offer_id>")
def get_perk(offer_id):
    offer = PerkOffer.query.get_or_404(offer_id)

    if not offer_is_current(offer):
        return jsonify({"ok": False, "error": "Offer not available."}), 404

    return jsonify({
        "ok": True,
        "perk": serialize_offer(offer),
    })


@perks_api_bp.post("/<int:offer_id>/event")
def record_perk_event(offer_id):
    offer = PerkOffer.query.get_or_404(offer_id)
    data = request.get_json(silent=True) or {}

    event_type = data.get("event_type") or data.get("type") or "view"

    if event_type not in {"view", "click", "call", "redeem"}:
        return jsonify({"ok": False, "error": "Invalid event type."}), 400

    event = PerkEvent(
        offer_id=offer.id,
        user_id=data.get("user_id"),
        store_number=str(data.get("store_number")) if data.get("store_number") else None,
        event_type=event_type,
        metadata_json=json.dumps(data.get("metadata") or {}),
    )

    db.session.add(event)
    db.session.commit()

    return jsonify({"ok": True})
