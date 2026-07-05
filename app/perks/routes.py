from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, session

from app import db
from app.models import PerkPartner, PerkOffer, PerkEvent


perks_bp = Blueprint("perks", __name__, url_prefix="/admin/perks")


def perks_admin_required():
    role = session.get("account_role") or session.get("role")
    return role in {"admin", "hr"}


def safe_redirect_home():
    for endpoint in ("dashboard.index", "dashboard", "admin.index", "auth.login", "login"):
        try:
            return redirect(url_for(endpoint))
        except Exception:
            continue
    return redirect("/")


def parse_optional_datetime(value):
    value = (value or "").strip()
    if not value:
        return None

    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@perks_bp.before_request
def require_perks_admin():
    if not perks_admin_required():
        flash("You do not have access to BPI Perks.", "error")
        return safe_redirect_home()


@perks_bp.route("/")
def index():
    partners_count = PerkPartner.query.count()
    active_offers_count = PerkOffer.query.filter_by(status="active").count()
    featured_count = PerkOffer.query.filter_by(featured=True, status="active").count()

    recent_offers = (
        PerkOffer.query
        .join(PerkPartner)
        .order_by(PerkOffer.updated_at.desc())
        .limit(10)
        .all()
    )

    return render_template(
        "perks/index.html",
        partners_count=partners_count,
        active_offers_count=active_offers_count,
        featured_count=featured_count,
        recent_offers=recent_offers,
    )


@perks_bp.route("/partners")
def partners():
    partners = PerkPartner.query.order_by(PerkPartner.name.asc()).all()
    return render_template("perks/partners.html", partners=partners)


@perks_bp.route("/partners/new", methods=["GET", "POST"])
def new_partner():
    partner = None

    if request.method == "POST":
        partner = PerkPartner(
            name=request.form.get("name", "").strip(),
            category=request.form.get("category", "").strip() or None,
            logo_url=request.form.get("logo_url", "").strip() or None,
            website_url=request.form.get("website_url", "").strip() or None,
            contact_name=request.form.get("contact_name", "").strip() or None,
            contact_email=request.form.get("contact_email", "").strip() or None,
            contact_phone=request.form.get("contact_phone", "").strip() or None,
            status=request.form.get("status", "active"),
        )

        if not partner.name:
            flash("Partner name is required.", "error")
            return render_template("perks/partner_form.html", partner=partner)

        db.session.add(partner)
        db.session.commit()

        flash("Perk partner created.", "success")
        return redirect(url_for("perks.partners"))

    return render_template("perks/partner_form.html", partner=partner)


@perks_bp.route("/partners/<int:partner_id>/edit", methods=["GET", "POST"])
def edit_partner(partner_id):
    partner = PerkPartner.query.get_or_404(partner_id)

    if request.method == "POST":
        partner.name = request.form.get("name", "").strip()
        partner.category = request.form.get("category", "").strip() or None
        partner.logo_url = request.form.get("logo_url", "").strip() or None
        partner.website_url = request.form.get("website_url", "").strip() or None
        partner.contact_name = request.form.get("contact_name", "").strip() or None
        partner.contact_email = request.form.get("contact_email", "").strip() or None
        partner.contact_phone = request.form.get("contact_phone", "").strip() or None
        partner.status = request.form.get("status", "active")
        partner.updated_at = datetime.utcnow()

        if not partner.name:
            flash("Partner name is required.", "error")
            return render_template("perks/partner_form.html", partner=partner)

        db.session.commit()

        flash("Perk partner updated.", "success")
        return redirect(url_for("perks.partners"))

    return render_template("perks/partner_form.html", partner=partner)


@perks_bp.route("/offers")
def offers():
    offers = (
        PerkOffer.query
        .join(PerkPartner)
        .order_by(
            PerkOffer.featured.desc(),
            PerkOffer.sort_order.asc(),
            PerkOffer.title.asc(),
        )
        .all()
    )
    return render_template("perks/offers.html", offers=offers)


@perks_bp.route("/offers/new", methods=["GET", "POST"])
def new_offer():
    partners = PerkPartner.query.order_by(PerkPartner.name.asc()).all()

    if request.method == "POST":
        partner_id = request.form.get("partner_id")
        if not partner_id:
            flash("Partner is required.", "error")
            return render_template("perks/offer_form.html", offer=None, partners=partners)

        offer = PerkOffer(
            partner_id=int(partner_id),
            title=request.form.get("title", "").strip(),
            short_description=request.form.get("short_description", "").strip() or None,
            description=request.form.get("description", "").strip() or None,
            category=request.form.get("category", "").strip() or None,
            image_url=request.form.get("image_url", "").strip() or None,
            button_text=request.form.get("button_text", "").strip() or "View Offer",
            button_url=request.form.get("button_url", "").strip() or None,
            phone_number=request.form.get("phone_number", "").strip() or None,
            redemption_instructions=request.form.get("redemption_instructions", "").strip() or None,
            terms=request.form.get("terms", "").strip() or None,
            featured=bool(request.form.get("featured")),
            is_template=bool(request.form.get("is_template")),
            sort_order=int(request.form.get("sort_order") or 100),
            starts_at=parse_optional_datetime(request.form.get("starts_at")),
            ends_at=parse_optional_datetime(request.form.get("ends_at")),
            status=request.form.get("status", "draft"),
        )

        if not offer.title:
            flash("Offer title is required.", "error")
            return render_template("perks/offer_form.html", offer=offer, partners=partners)

        db.session.add(offer)
        db.session.commit()

        flash("Perk offer created.", "success")
        return redirect(url_for("perks.offers"))

    return render_template("perks/offer_form.html", offer=None, partners=partners)



@perks_bp.post("/offers/<int:offer_id>/status")
def update_offer_status(offer_id):
    offer = PerkOffer.query.get_or_404(offer_id)

    new_status = request.form.get("status", "").strip().lower()

    if new_status not in {"draft", "active", "paused", "expired"}:
        flash("Invalid offer status.", "error")
        return redirect(url_for("perks.offers"))

    offer.status = new_status
    offer.updated_at = datetime.utcnow()

    if new_status == "active":
        offer.starts_at = None
        offer.ends_at = None

        for field in ["image_url", "button_url", "phone_number"]:
            value = getattr(offer, field)
            if value and str(value).strip().lower() in {"none", "null"}:
                setattr(offer, field, None)

    db.session.commit()

    flash(f"Offer marked {new_status}.", "success")
    return redirect(url_for("perks.offers"))


@perks_bp.route("/offers/<int:offer_id>/edit", methods=["GET", "POST"])
def edit_offer(offer_id):
    offer = PerkOffer.query.get_or_404(offer_id)
    partners = PerkPartner.query.order_by(PerkPartner.name.asc()).all()

    if request.method == "POST":
        offer.partner_id = int(request.form.get("partner_id"))
        offer.title = request.form.get("title", "").strip()
        offer.short_description = request.form.get("short_description", "").strip() or None
        offer.description = request.form.get("description", "").strip() or None
        offer.category = request.form.get("category", "").strip() or None
        offer.image_url = request.form.get("image_url", "").strip() or None
        offer.button_text = request.form.get("button_text", "").strip() or "View Offer"
        offer.button_url = request.form.get("button_url", "").strip() or None
        offer.phone_number = request.form.get("phone_number", "").strip() or None
        offer.redemption_instructions = request.form.get("redemption_instructions", "").strip() or None
        offer.terms = request.form.get("terms", "").strip() or None
        offer.featured = bool(request.form.get("featured"))
        offer.is_template = bool(request.form.get("is_template"))
        offer.sort_order = int(request.form.get("sort_order") or 100)
        offer.starts_at = parse_optional_datetime(request.form.get("starts_at"))
        offer.ends_at = parse_optional_datetime(request.form.get("ends_at"))
        offer.status = request.form.get("status", "draft")
        offer.updated_at = datetime.utcnow()

        if not offer.title:
            flash("Offer title is required.", "error")
            return render_template("perks/offer_form.html", offer=offer, partners=partners)

        db.session.commit()

        flash("Perk offer updated.", "success")
        return redirect(url_for("perks.offers"))

    return render_template("perks/offer_form.html", offer=offer, partners=partners)


@perks_bp.route("/analytics")
def analytics():
    rows = (
        db.session.query(
            PerkOffer,
            db.func.count(PerkEvent.id).label("total_events"),
        )
        .outerjoin(PerkEvent, PerkEvent.offer_id == PerkOffer.id)
        .group_by(PerkOffer.id)
        .order_by(db.func.count(PerkEvent.id).desc())
        .all()
    )

    return render_template("perks/analytics.html", rows=rows)
