import json
from flask import abort, redirect, request, session, url_for
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.models import ModuleAccessSetting

ROLE_OPTIONS = [
    ("admin", "Admin"), ("supervisor", "Supervisor"),
    ("general_manager", "General Manager"), ("manager", "Manager / Shift Runner"),
    ("store", "Store"), ("tm", "Team Member"),
    ("maintenance", "Maintenance"), ("hr", "HR"),
    ("payroll", "Payroll"), ("platform_admin", "Platform Admin"),
]

MODULE_REGISTRY = [
    ("dashboard", "Dashboard", "Command", ["admin","supervisor","general_manager","manager","store","tm","maintenance","hr","payroll","platform_admin"], 10),
    ("store_dashboard", "Store Dashboard", "Command", ["admin","supervisor","general_manager","manager","platform_admin"], 20),
    ("doughy", "Doughy AI", "Command", ["admin","supervisor","general_manager","manager","store","maintenance","hr","payroll","platform_admin"], 30),
    ("checklist", "Checklist", "Daily Operations", ["admin","supervisor","general_manager","manager","store","platform_admin"], 100),
    ("forms", "Forms", "Daily Operations", ["admin","supervisor","general_manager","manager","store","hr","payroll","platform_admin"], 110),
    ("prep", "Prep", "Daily Operations", ["admin","supervisor","general_manager","manager","store","platform_admin"], 120),
    ("cash", "Cash Control", "Daily Operations", ["admin","general_manager","manager","store","platform_admin"], 130),
    ("nightly_numbers", "Nightly Numbers", "Daily Operations", ["admin","supervisor","general_manager","manager","store","platform_admin"], 140),
    ("shift_todos", "Shift To-Dos", "Daily Operations", ["admin","supervisor","general_manager","manager","tm","platform_admin"], 150),
    ("labels", "BPI Labels", "Daily Operations", ["admin","supervisor","general_manager","manager","store","platform_admin"], 160),
    ("reports", "Reports", "Review & Compliance", ["admin","supervisor","platform_admin"], 200),
    ("cash_review", "Cash Review", "Review & Compliance", ["admin","supervisor","general_manager","platform_admin"], 210),
    ("svr", "SVR", "Review & Compliance", ["admin","supervisor","platform_admin"], 220),
    ("verification", "Verification", "Review & Compliance", ["admin","supervisor","platform_admin"], 230),
    ("maintenance", "Maintenance", "Review & Compliance", ["admin","supervisor","general_manager","manager","store","maintenance","platform_admin"], 240),
    ("maintenance_time_cards", "Maintenance Time Cards", "Review & Compliance", ["admin","maintenance","hr","payroll","platform_admin"], 250),
    ("academy", "BPI Academy", "People & Development", ["admin","supervisor","general_manager","manager","tm","hr","platform_admin"], 300),
    ("dwp", "DWP", "People & Development", ["admin","supervisor","general_manager","manager","hr","payroll","tm","platform_admin"], 310),
    ("hr_documents", "HR Documents", "People & Development", ["admin","supervisor","hr","payroll","tm","platform_admin"], 320),
    ("my_documents", "My Documents", "People & Development", ["admin","supervisor","general_manager","manager","tm","maintenance","hr","payroll","platform_admin"], 325),
    ("users", "Users & Roles", "People & Development", ["admin","supervisor","general_manager","hr","platform_admin"], 330),
    ("registration_requests", "Registration Requests", "People & Development", ["admin","supervisor","general_manager","hr","platform_admin"], 340),
    ("registration_qr", "Registration QR Center", "People & Development", ["admin","supervisor","general_manager","manager","hr","platform_admin"], 350),
    ("connect_admin", "BPI Connect Admin", "Company Tools", ["admin","supervisor","platform_admin"], 400),
    ("perks", "BPI Perks", "Company Tools", ["admin","platform_admin"], 410),
    ("admin_center", "Admin Center", "Administration", ["admin","supervisor","manager","platform_admin"], 500),
    ("checklist_admin", "Checklist Admin", "Administration", ["admin","platform_admin"], 510),
    ("forms_admin", "Forms Admin", "Administration", ["admin","supervisor","hr","payroll","platform_admin"], 520),
    ("prep_admin", "Prep Admin", "Administration", ["admin","supervisor","platform_admin"], 530),
    ("svr_admin", "SVR Admin", "Administration", ["admin","platform_admin"], 540),
    ("verification_admin", "Verification Admin", "Administration", ["admin","platform_admin"], 550),
    ("nightly_numbers_admin", "Nightly Numbers Admin", "Administration", ["admin","platform_admin"], 560),
    ("store_admin", "Store Admin", "Administration", ["admin","platform_admin"], 570),
    ("module_access", "Module & Email Access", "Administration", ["admin","platform_admin"], 580),
]

EMAIL_REGISTRY = [
    ("email__checklist__11am", "Checklist · 11 AM incomplete alert", "Checklist", ["admin","supervisor","general_manager","store"], 1000),
    ("email__checklist__4pm", "Checklist · 4 PM incomplete alert", "Checklist", ["admin","supervisor","general_manager","store"], 1010),
    ("email__checklist__summary", "Checklist · Daily summary", "Checklist", ["admin","supervisor"], 1020),
    ("email__forms__submitted", "Forms · Submission received", "Forms", ["admin","supervisor","general_manager","store","hr","payroll"], 1100),
    ("email__nightly_numbers__submitted", "Nightly Numbers · Submitted", "Nightly Numbers", ["admin","supervisor","store"], 1200),
    ("email__verification__submitted", "Verification · Submitted", "Verification", ["admin","supervisor"], 1300),
    ("email__hr_documents__assigned", "HR Documents · Assigned", "HR Documents", ["tm","manager","general_manager","supervisor","maintenance","hr","payroll"], 1400),
    ("email__hr_documents__reminder", "HR Documents · Reminder / resend", "HR Documents", ["tm","manager","general_manager","supervisor","maintenance","hr","payroll"], 1410),
    ("email__dwp__submitted", "DWP · Submitted", "DWP", ["admin","hr","supervisor","general_manager","manager","payroll"], 1500),
    ("email__dwp__acknowledged", "DWP · Team member acknowledged", "DWP", ["admin","hr","supervisor","general_manager","manager","payroll"], 1510),
    ("email__maintenance__created", "Maintenance · New ticket", "Maintenance", ["admin","supervisor","general_manager","maintenance"], 1600),
    ("email__maintenance__completed", "Maintenance · Completed", "Maintenance", ["admin","supervisor","general_manager","maintenance"], 1610),
    ("email__doughy__daily_brief", "Doughy · Daily brief", "Doughy", ["admin","supervisor","general_manager"], 1700),
]

BLUEPRINT_MODULES = {
    "dashboard":"dashboard","checklist":"checklist","svr":"svr","maintenance":"maintenance",
    "store_admin":"store_admin","reports":"reports","nightly_numbers":"nightly_numbers",
    "cash":"cash","cash_review":"cash_review","verification":"verification",
    "store_dashboard":"store_dashboard","prep":"prep","shift_todos":"shift_todos",
    "forms":"forms","hr_documents":"hr_documents","connect_admin":"connect_admin",
    "dwp":"dwp","doughy":"doughy","labels":"labels","perks":"perks",
    "academy":"academy","mit_sts":"academy",
}

ENDPOINT_MODULES = {
    "dashboard.admin_center":"admin_center","dashboard.module_access_admin":"module_access",
    "auth.manage_users":"users","auth.registration_requests":"registration_requests",
    "auth.registration_qr_center":"registration_qr","checklist.admin":"checklist_admin",
    "forms.admin":"forms_admin","forms.edit_template":"forms_admin","prep.manage":"prep_admin",
    "svr.admin":"svr_admin","verification.admin":"verification_admin",
    "nightly_numbers.admin":"nightly_numbers_admin","nightly_numbers.edit_report":"nightly_numbers_admin",
    "maintenance.time_card":"maintenance_time_cards","maintenance.time_cards":"maintenance_time_cards",
    "maintenance.time_cards_pdf":"maintenance_time_cards",
    "hr_documents.my_documents":"my_documents",
    "hr_documents.acknowledge_document":"my_documents",
    "hr_documents.download_document":"my_documents",
}

PATH_MODULES = [
    ("/admin-center/module-access","module_access"),("/admin-center","admin_center"),
    ("/users/registration-requests","registration_requests"),("/users/registration-qr","registration_qr"),
    ("/users","users"),("/checklist/admin","checklist_admin"),("/checklist","checklist"),
    ("/forms/admin","forms_admin"),("/forms","forms"),("/prep/manage","prep_admin"),("/prep","prep"),
    ("/svr/admin","svr_admin"),("/svr","svr"),("/verification/admin","verification_admin"),
    ("/verification","verification"),("/nightly-numbers/admin","nightly_numbers_admin"),
    ("/nightly-numbers","nightly_numbers"),("/cash-review","cash_review"),("/cash","cash"),
    ("/maintenance/time-card","maintenance_time_cards"),("/maintenance","maintenance"),
    ("/reports","reports"),("/store-dashboard","store_dashboard"),("/shift-todos","shift_todos"),
    ("/hr-documents/my","my_documents"),("/hr-documents","hr_documents"),("/dwp","dwp"),("/labels","labels"),
    ("/connect-admin","connect_admin"),("/perks","perks"),("/academy","academy"),("/doughy","doughy"),
]

PROTECTED_MODULES = {"dashboard","module_access"}
LEGACY_MODULE_KEYS = {"manage_documents", "hr_manage_documents", "hr_documents_manage"}
LEGACY_MODULE_LABELS = {"manage documents"}

def _roles(raw):
    try:
        value = json.loads(raw or "[]")
        return value if isinstance(value, list) else []
    except Exception:
        return []

def current_account_role():
    return (session.get("account_role") or session.get("access_role") or session.get("user_role") or session.get("role") or "").strip().lower()

def registry_rows():
    return MODULE_REGISTRY + [(k,l,f"Email · {g}",r,o) for k,l,g,r,o in EMAIL_REGISTRY]

def seed_module_access_settings():
    changed = False
    for key,label,group,roles,order in registry_rows():
        setting = ModuleAccessSetting.query.filter_by(module_key=key).first()
        if setting is None:
            db.session.add(ModuleAccessSetting(module_key=key,module_label=label,module_group=group,allowed_roles_json=json.dumps(roles),is_enabled=True,sort_order=order))
            changed = True
        else:
            setting.module_label, setting.module_group, setting.sort_order = label, group, order

    # "Manage Documents" was an older name/module for the same administrative
    # HR document screen now represented by the canonical hr_documents module.
    # Old database rows were never pruned by the seeder, so the legacy entry
    # could continue to appear beside HR Documents even though both routes
    # opened the exact same screen. Remove only these known aliases; unknown
    # custom modules remain untouched.
    legacy_rows = ModuleAccessSetting.query.all()
    for setting in legacy_rows:
        key = (setting.module_key or "").strip().lower()
        label = (setting.module_label or "").strip().lower()
        if key in LEGACY_MODULE_KEYS or label in LEGACY_MODULE_LABELS:
            db.session.delete(setting)
            changed = True

    if changed:
        db.session.commit()

def module_access_allowed_roles(setting):
    return _roles(setting.allowed_roles_json)

def _default(module_key):
    for key,_label,_group,roles,_order in registry_rows():
        if key == module_key:
            return True, roles
    return False, []

def can_access_module(module_key, role=None):
    role = (role or current_account_role()).strip().lower()
    if not role:
        return False
    if module_key in PROTECTED_MODULES and role in {"admin","platform_admin"}:
        return True
    try:
        setting = ModuleAccessSetting.query.filter_by(module_key=module_key).first()
    except SQLAlchemyError:
        db.session.rollback()
        setting = None
    if setting is None:
        enabled, roles = _default(module_key)
        return enabled and role in roles
    return bool(setting.is_enabled) and role in module_access_allowed_roles(setting)

def email_event_setting(event_key):
    try:
        return ModuleAccessSetting.query.filter_by(module_key=event_key).first()
    except SQLAlchemyError:
        db.session.rollback()
        return None


def email_event_is_enabled(event_key):
    setting = email_event_setting(event_key)
    if setting is None:
        enabled, _roles_list = _default(event_key)
        return bool(enabled)
    return bool(setting.is_enabled)


def email_event_allowed_roles(event_key):
    setting = email_event_setting(event_key)
    if setting is None:
        _enabled, roles = _default(event_key)
        return list(roles)
    return module_access_allowed_roles(setting)


def email_event_enabled(event_key):
    # Template helper: enabled for the current role.
    return (
        email_event_is_enabled(event_key)
        and current_account_role() in email_event_allowed_roles(event_key)
    )


def resolve_email_event_users(event_key, store_number=None, area_name=None):
    from app.models import Store, User

    if not email_event_is_enabled(event_key):
        return []

    roles = email_event_allowed_roles(event_key)

    if store_number is not None:
        store_number = str(store_number)

    if store_number and not area_name:
        store = Store.query.filter_by(store_number=store_number).first()
        area_name = store.area_name if store else None

    store_scoped = {"store", "general_manager", "manager", "tm", "maintenance"}
    area_scoped = {"supervisor"}
    company_scoped = {"admin", "hr", "payroll", "platform_admin"}

    users = []
    seen_ids = set()

    for role in roles:
        query = User.query.filter_by(role=role, is_active=True)

        if role in store_scoped:
            if not store_number:
                continue
            query = query.filter_by(store_number=store_number)
        elif role in area_scoped:
            if not area_name:
                continue
            query = query.filter_by(area_name=area_name)
        elif role not in company_scoped:
            continue

        for user in query.all():
            if user.id in seen_ids:
                continue
            seen_ids.add(user.id)
            users.append(user)

    return users


def resolve_email_event_addresses(event_key, store_number=None, area_name=None):
    addresses = []
    seen = set()

    for user in resolve_email_event_users(
        event_key,
        store_number=store_number,
        area_name=area_name,
    ):
        email = user.get_notification_email()
        normalized = (email or "").strip().lower()

        if not normalized or normalized in seen:
            continue

        seen.add(normalized)
        addresses.append(email.strip())

    return addresses

def grouped_module_access_settings():
    seed_module_access_settings()
    settings = ModuleAccessSetting.query.order_by(ModuleAccessSetting.sort_order.asc(), ModuleAccessSetting.module_label.asc()).all()
    buckets = {}
    for setting in settings:
        buckets.setdefault(setting.module_group, []).append(setting)
    preferred = ["Command","Daily Operations","Review & Compliance","People & Development","Company Tools","Administration",
                 "Email · Checklist","Email · Forms","Email · Nightly Numbers","Email · Verification",
                 "Email · HR Documents","Email · DWP","Email · Maintenance","Email · Doughy"]
    result = []
    for group in preferred:
        if group in buckets:
            result.append((group,buckets.pop(group)))
    result.extend((group,buckets[group]) for group in sorted(buckets))
    return result

def module_for_request():
    if request.endpoint in ENDPOINT_MODULES:
        return ENDPOINT_MODULES[request.endpoint]
    if request.blueprint == "auth":
        return None
    return BLUEPRINT_MODULES.get(request.blueprint or "")

def _payload():
    role = current_account_role()
    return {key: can_access_module(key, role) for key,_,_,_,_ in MODULE_REGISTRY}

def _inject_ui(response):
    if response.status_code != 200 or response.mimetype != "text/html" or not session.get("user_id"):
        return response
    try:
        html = response.get_data(as_text=True)
    except Exception:
        return response
    if "</body>" not in html:
        return response
    payload = json.dumps(_payload())
    paths = json.dumps(PATH_MODULES)
    injection = f"""
<style>[data-bpi-permission-hidden="1"]{{display:none!important}}</style>
<script>
(function(){{
 const permissions={payload}, pathModules={paths};
 function moduleFor(href){{try{{const p=new URL(href,location.origin).pathname;for(const x of pathModules){{if(p===x[0]||p.startsWith(x[0]+"/"))return x[1];}}}}catch(e){{}}return null;}}
 function apply(){{document.querySelectorAll('a[href]').forEach(a=>{{const k=moduleFor(a.getAttribute('href'));if(k&&permissions[k]===false)a.dataset.bpiPermissionHidden="1";}});document.querySelectorAll('.nav-section').forEach(s=>{{if(!s.querySelector('a:not([data-bpi-permission-hidden="1"])'))s.dataset.bpiPermissionHidden="1";}});}}
 if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",apply);else apply();
}})();
</script>"""
    response.set_data(html.replace("</body>", injection + "\n</body>"))
    response.headers["Content-Length"] = str(len(response.get_data()))
    return response

def install_module_access(app):
    from app.dashboard import routes as dashboard_routes
    dashboard_routes.ACCOUNT_ROLE_OPTIONS = ROLE_OPTIONS
    dashboard_routes.DEFAULT_MODULE_ACCESS = [
        {"module_key":k,"module_label":l,"module_group":g,"allowed_roles":r,"sort_order":o}
        for k,l,g,r,o in registry_rows()
    ]
    dashboard_routes.seed_module_access_settings = seed_module_access_settings
    dashboard_routes.module_access_allowed_roles = module_access_allowed_roles
    dashboard_routes.grouped_module_access_settings = grouped_module_access_settings

    @app.context_processor
    def inject_permissions():
        return {"can_access_module":can_access_module,"email_event_enabled":email_event_enabled}

    @app.before_request
    def enforce_permissions():
        if not session.get("user_id"):
            return None
        key = module_for_request()
        if not key or can_access_module(key):
            return None
        if request.path.startswith("/api/") or request.is_json:
            abort(403)
        return redirect(url_for("dashboard.home", denied=key))

    app.after_request(_inject_ui)
    with app.app_context():
        try:
            seed_module_access_settings()
        except SQLAlchemyError:
            db.session.rollback()
