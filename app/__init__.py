from datetime import timedelta

from app.config import Config
from app.extensions import db, migrate


def create_app():
    from flask import Flask

    app = Flask(__name__)
    app.config.from_object(Config)

    # -------------------------
    # SESSION SETTINGS
    # Keep users logged in longer for BPI Ops / notifications
    # -------------------------
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
    app.config["SESSION_PERMANENT"] = True

    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)

    # Import blueprints
    from app.auth.routes import auth_bp
    from app.dashboard.routes import dashboard_bp
    from app.checklist.routes import checklist_bp
    from app.svr.routes import svr_bp
    from app.maintenance.routes import maintenance_bp
    from app.store_admin.routes import store_admin_bp
    from app.reports.routes import reports_bp
    from app.nightly_numbers.routes import nightly_numbers_bp
    from app.cash.routes import cash_bp
    from app.cash_review.routes import cash_review_bp
    from app.verification.routes import verification_bp
    from app.store_dashboard import store_dashboard_bp
    from app.prep.routes import prep_bp

    # Register blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(checklist_bp)
    app.register_blueprint(svr_bp)
    app.register_blueprint(maintenance_bp)
    app.register_blueprint(store_admin_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(nightly_numbers_bp)
    app.register_blueprint(cash_bp)
    app.register_blueprint(cash_review_bp)
    app.register_blueprint(verification_bp)
    app.register_blueprint(store_dashboard_bp)
    app.register_blueprint(prep_bp)

    @app.route("/create-db")
    def create_db():
        from app import models
        db.create_all()
        return "Database tables created"

    # Seed data + ensure tables exist
    with app.app_context():
        from app import models
        db.create_all()
        seed_admin()
        seed_stores()
        seed_checklist_template()
        seed_svr_template()

    return app


def seed_admin():
    from app.models import User

    existing = User.query.filter_by(username="admin").first()
    if existing:
        return

    admin = User(
        name="Admin",
        username="admin",
        role="admin",
        is_active=True
    )
    admin.set_password("admin123")

    db.session.add(admin)
    db.session.commit()


def seed_stores():
    from app.models import Store

    if Store.query.count() > 0:
        return

    stores = [
        ("3001", "Store 3001", "Area 1"),
        ("3002", "Store 3002", "Area 1"),
        ("3003", "Store 3003", "Area 1"),
        ("3019", "Store 3019", "Area 1"),
        ("3201", "Store 3201", "Area 1"),
        ("3210", "Store 3210", "Area 1"),
        ("3216", "Store 3216", "Area 1"),
        ("3219", "Store 3219", "Area 1"),
        ("3225", "Store 3225", "Area 1"),
        ("3787", "Store 3787", "Area 1"),

        ("3207", "Store 3207", "Area 2"),
        ("3209", "Store 3209", "Area 2"),
        ("3221", "Store 3221", "Area 2"),
        ("3724", "Store 3724", "Area 2"),
        ("3732", "Store 3732", "Area 2"),
        ("3733", "Store 3733", "Area 2"),
        ("3734", "Store 3734", "Area 2"),
        ("3741", "Store 3741", "Area 2"),
        ("3742", "Store 3742", "Area 2"),
        ("3784", "Store 3784", "Area 2"),

        ("3215", "Store 3215", "Area 3"),
        ("3718", "Store 3718", "Area 3"),
        ("3769", "Store 3769", "Area 3"),
        ("3770", "Store 3770", "Area 3"),
        ("3782", "Store 3782", "Area 3"),
    ]

    for store_number, store_name, area_name in stores:
        db.session.add(
            Store(
                store_number=store_number,
                store_name=store_name,
                area_name=area_name,
                is_active=True,
            )
        )

    db.session.commit()


def seed_checklist_template():
    from app.models import ChecklistTemplateItem

    if ChecklistTemplateItem.query.count() > 0:
        return

    items = [
        ("Before Open / Before 10:30", "Put on uniform & clock in / start office PULSE", 2, 1, True),
        ("Before Open / Before 10:30", "Take orders now", 1, 2, True),
        ("Before Open / Before 10:30", "Turn on makeline", 2, 3, True),
        ("Before Open / Before 10:30", "Check schedule", 2, 4, True),
        ("Before Open / Before 10:30", "Run prep report - check enough food for day", 3, 5, True),
        ("Before Open / Before 10:30", "Put wash in dryer / hang laundry", 2, 6, True),
        ("Before Open / Before 10:30", "Bring out dough for 1-way proof", 3, 7, True),
        ("Before Open / Before 10:30", "Count till", 2, 8, True),
        ("Before Open / Before 10:30", "Turn on oven & open sign", 2, 9, True),
        ("Before Open / Before 10:30", "Set up makeline & stock cabinets", 4, 10, True),
        ("Before Open / Before 10:30", "Place scales & thermometers", 2, 11, True),
        ("Before Open / Before 10:30", "Set up dough & cut tables", 4, 12, True),
        ("Before Open / Before 10:30", "Check shelf-life dates - toss expired food", 3, 13, True),
        ("Before Open / Before 10:30", "Check all labels for rotation - including dough", 3, 14, True),
        ("Before Open / Before 10:30", "Clean customer area - including windows & sills", 4, 15, True),
        ("Before Open / Before 10:30", "Sweep / clean outside - 6' out from door", 3, 16, True),
        ("Before Open / Before 10:30", "Stock napkin & c-fold containers and soaps", 2, 17, True),
        ("Before Open / Before 10:30", "Check image - TM & cars", 2, 18, True),
        ("Before Open / Before 10:30", "Prep fresh sauce & warm cold sauce", 3, 19, True),
        ("Before Open / Before 10:30", "Complete self OER & correct as needed", 4, 20, True),
        ("Before Open / Before 10:30", "Stock cheese and prep for next day", 3, 21, True),
        ("Before Open / Before 10:30", "Clean all bathrooms & stock", 4, 22, True),

        ("During Dayshift", "Prep all products for day (done by 11am)", 5, 23, True),
        ("During Dayshift", "Pick up bank slips & staple to daily", 2, 24, True),
        ("During Dayshift", "Check & clean hot bags", 3, 25, True),
        ("During Dayshift", "Wash dishes", 5, 26, True),
        ("During Dayshift", "Sweep & mop office & trash area", 4, 27, True),
        ("During Dayshift", "Box-top & fold boxes", 3, 28, True),
        ("During Dayshift", "Cleaning task of the day", 5, 29, True),
        ("During Dayshift", "Sweep / shovel / sand / entry area", 4, 30, True),
        ("During Dayshift", "Hang all laundry", 2, 31, True),
        ("During Dayshift", "Fill out temp log", 2, 32, True),
        ("During Dayshift", "2 hr sanitation swap - time stamp buckets", 2, 33, True),

        ("3-O'Clock Restock", "Restock makeline rail and cabinets", 4, 34, True),
        ("3-O'Clock Restock", "Restock cut table and clean & sanitize", 4, 35, True),
        ("3-O'Clock Restock", "Pick makeline pits - wash pits", 4, 36, True),
        ("3-O'Clock Restock", "Clean and sanitize dough table", 4, 37, True),
        ("3-O'Clock Restock", "Restock Coke and box counter", 3, 38, True),
        ("3-O'Clock Restock", "Sweep floor", 3, 39, True),
        ("3-O'Clock Restock", "Wash all remaining dishes", 4, 40, True),
        ("3-O'Clock Restock", "Call / post dayshift numbers as required", 2, 41, True),
        ("3-O'Clock Restock", "Dayshift cash-out complete & logged", 2, 42, True),

        ("Manager's Walk", "Carryout area clean - swept, mopped, wiped etc", 3, 43, True),
        ("Manager's Walk", "Under counter shelves clean", 3, 44, True),
        ("Manager's Walk", "All counters clean & sanitized", 3, 45, True),
        ("Manager's Walk", "Under phone counter clean and organized", 2, 46, True),
        ("Manager's Walk", "Oven clean, wiped incl catch trays & fan covers", 4, 47, True),
        ("Manager's Walk", "Organize office desktop & sweep floor", 3, 48, True),
        ("Manager's Walk", "Makeline clean and wiped down - in and out", 4, 49, True),
        ("Manager's Walk", "Cut table and blue bins clean", 3, 50, True),
        ("Manager's Walk", "Laundry started (dirty towels)", 2, 51, True),
        ("Manager's Walk", "3 comp & handsinks clean", 3, 52, True),
        ("Manager's Walk", "Front stocked with boxes", 2, 53, True),
        ("Manager's Walk", "Bag rack clean & neat", 2, 54, True),
        ("Manager's Walk", "Heat racks clean and wiped incl very top", 3, 55, True),
        ("Manager's Walk", "Floor clean - under everything", 4, 56, True),
        ("Manager's Walk", "Trash empty &/or out back", 3, 57, True),
        ("Manager's Walk", "All dishes clean - check them! & drying", 3, 58, True),
        ("Manager's Walk", "All equipment turned off - ovens, hoods, etc", 2, 59, True),
        ("Manager's Walk", "Back room clean / swept & mopped", 4, 60, True),
        ("Manager's Walk", "Mops & bucket rinsed and drying", 2, 61, True),
        ("Manager's Walk", "Utensils back in place up front", 2, 62, True),
        ("Manager's Walk", "Walk-in clean and organized - food covered", 4, 63, True),
        ("Manager's Walk", "Safe secure with till inside", 2, 64, True),
        ("Manager's Walk", "Accurate inventory - incl line-by-line check", 4, 65, True),
        ("Manager's Walk", "Call / post numbers as required", 2, 66, True),
        ("Manager's Walk", "Paperwork properly filed / EOD run", 3, 67, True),
    ]

    for section_name, task_text, expected_minutes, sort_order, is_required in items:
        db.session.add(
            ChecklistTemplateItem(
                section_name=section_name,
                task_text=task_text,
                expected_minutes=expected_minutes,
                sort_order=sort_order,
                is_required=is_required,
                is_active=True,
            )
        )

    db.session.commit()


def seed_svr_template():
    from app.models import SVRTemplateField

    if SVRTemplateField.query.count() > 0:
        return

    fields = [
        ("date", "Date", "date", 1),
        ("store_number", "Store #", "readonly", 2),
        ("manager_on_duty", "Manager on duty", "text", 3),
        ("restroom_notes", "Restroom notes", "textarea", 4),
        ("checklist_book_notes", "Checklist book notes", "textarea", 5),
        ("one_way_proof", "1-way proof - dough projection/dough marked inside the walk-in", "textarea", 6),
        ("pizza_quality_notes", "Pizza Quality notes", "textarea", 7),
        ("load_go", "Load & Go - certified load captain on the schedule for every rush", "textarea", 8),
        ("last_weeks_svr_review", "Last week's SVR review", "textarea", 9),
        ("outside_store_condition_notes", "Outside store condition notes", "textarea", 10),
        ("carry_out_notes", "Carry out notes", "textarea", 11),
        ("store_condition_notes", "Store condition notes", "textarea", 12),
        ("refrigeration_units_notes", "Refrigeration units notes", "textarea", 13),
        ("bake_wares_notes", "Bake wares notes", "textarea", 14),
        ("oven_heatrack_notes", "Oven/heatrack notes", "textarea", 15),
        ("call_out_calendar_notes", "Call out calendar notes - who needs a meeting?", "textarea", 16),
        ("deposit_log", "Deposit Log - which days are missing?", "textarea", 17),
        ("pest_control", "Pest Control", "textarea", 18),
        ("cleaning_list_for_week", "Cleaning list for the week", "textarea", 19),
        ("goals_for_week", "Goals for the week", "textarea", 20),
        ("maintenance_needs", "Maintenance needs", "textarea", 21),
    ]

    for field_key, field_label, field_type, sort_order in fields:
        db.session.add(
            SVRTemplateField(
                field_key=field_key,
                field_label=field_label,
                field_type=field_type,
                sort_order=sort_order,
                is_active=True,
            )
        )

    db.session.commit()