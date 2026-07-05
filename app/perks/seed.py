from app import db
from app.models import PerkPartner, PerkOffer


DEFAULT_PERK_TEMPLATES = [
    {
        "key": "fitness",
        "partner_name": "Fitness Partner",
        "category": "Health",
        "title": "Fitness Partner",
        "short_description": "Special gym pricing for BPI team members.",
        "description": "Use this template for gyms, personal training, fitness memberships, or team wellness discounts.",
        "sort_order": 10,
    },
    {
        "key": "car-care",
        "partner_name": "Car Care Partner",
        "category": "Auto",
        "title": "Car Care Partner",
        "short_description": "Discounts on detailing, oil changes, and basic service.",
        "description": "Use this template for mechanics, detailing shops, tire shops, oil changes, and car services.",
        "sort_order": 20,
    },
    {
        "key": "phone-repair",
        "partner_name": "Phone Repair Partner",
        "category": "Tech",
        "title": "Phone Repair Partner",
        "short_description": "Team pricing on screen repairs and accessories.",
        "description": "Use this template for phone repair, device accessories, wireless stores, or tech support offers.",
        "sort_order": 30,
    },
    {
        "key": "family-fun",
        "partner_name": "Family Fun Partner",
        "category": "Local",
        "title": "Family Fun Partner",
        "short_description": "Deals for family activities and weekend plans.",
        "description": "Use this template for trampoline parks, bowling, movies, kids activities, and local attractions.",
        "sort_order": 40,
    },
    {
        "key": "travel",
        "partner_name": "Travel Partner",
        "category": "Travel",
        "title": "Travel Partner",
        "short_description": "Hotel, rental, or travel deals for BPI employees.",
        "description": "Use this template for hotels, rentals, travel agencies, airport parking, or employee travel discounts.",
        "sort_order": 50,
    },
    {
        "key": "insurance",
        "partner_name": "Insurance Partner",
        "category": "Money",
        "title": "Insurance Partner",
        "short_description": "Preferred quotes or savings for team members.",
        "description": "Use this template for auto, home, renter, life, or business insurance partner offers.",
        "sort_order": 60,
    },
    {
        "key": "tax-help",
        "partner_name": "Tax Help Partner",
        "category": "Money",
        "title": "Tax Help Partner",
        "short_description": "Discounted personal tax prep or financial help.",
        "description": "Use this template for tax preparation, financial planning, budgeting help, or accounting offers.",
        "sort_order": 70,
    },
    {
        "key": "wellness",
        "partner_name": "Wellness Partner",
        "category": "Health",
        "title": "Wellness Partner",
        "short_description": "Wellness, massage, or recovery offers.",
        "description": "Use this template for massage, chiropractic, physical therapy, recovery, or wellness partners.",
        "sort_order": 80,
    },
    {
        "key": "uniforms",
        "partner_name": "Uniforms & Gear",
        "category": "Work",
        "title": "Uniforms & Gear",
        "short_description": "Useful team gear and work essentials.",
        "description": "Use this template for shoes, uniforms, jackets, bags, work gear, or team essentials.",
        "sort_order": 90,
    },
    {
        "key": "local-eats",
        "partner_name": "Local Eats Partner",
        "category": "Food",
        "title": "Local Eats Partner",
        "short_description": "Local restaurant or coffee deals for the team.",
        "description": "Use this template for coffee shops, restaurants, bakeries, and local food partners.",
        "sort_order": 100,
    },
]


def seed_default_perk_templates():
    created = 0

    for template in DEFAULT_PERK_TEMPLATES:
        partner = PerkPartner.query.filter_by(name=template["partner_name"]).first()

        if not partner:
            partner = PerkPartner(
                name=template["partner_name"],
                category=template["category"],
                status="active",
            )
            db.session.add(partner)
            db.session.flush()

        existing_offer = (
            PerkOffer.query
            .filter_by(partner_id=partner.id, title=template["title"])
            .first()
        )

        if existing_offer:
            if not existing_offer.is_template:
                existing_offer.is_template = True
            continue

        offer = PerkOffer(
            partner_id=partner.id,
            title=template["title"],
            short_description=template["short_description"],
            description=template["description"],
            category=template["category"],
            button_text="Coming Soon",
            redemption_instructions="Edit this template when the partner offer is ready.",
            terms="Template only. Replace with real offer terms before activating.",
            featured=False,
            is_template=True,
            sort_order=template["sort_order"],
            status="draft",
        )
        db.session.add(offer)
        created += 1

    db.session.commit()
    return created
