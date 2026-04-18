from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from flask_wtf import FlaskForm
from services import bkc_db
from services.auth_user import BKCUser
from services.rate_limit import limiter
from wtforms import PasswordField, StringField
from wtforms.validators import DataRequired


class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired()])
    password = PasswordField("Password", validators=[DataRequired()])


auth_blueprint = Blueprint("auth", __name__)


@auth_blueprint.route("/login", methods=["GET", "POST"])
@limiter.limit("30 per minute", methods=["POST"])
def login():
    if request.args.get("next", "").startswith("http"):
        next_url = url_for("index.index")
    else:
        next_url = request.args.get("next") or url_for("index.index")

    form = LoginForm()
    if form.validate_on_submit():
        user = bkc_db.verify_user(form.username.data.strip(), form.password.data)
        if not user:
            flash("Invalid username or password.", "error")
        else:
            login_user(BKCUser(user["id"]), remember=False)
            session_slug = (request.args.get("tenant") or "default").strip().lower()
            tenant = bkc_db.fetch_tenant_by_slug(session_slug)
            if tenant and (
                bkc_db.membership_role(int(user["id"]), int(tenant["id"])) or user.get("is_superuser")
            ):
                session["tenant_slug"] = tenant["slug"]
            else:
                memberships = bkc_db.list_memberships(int(user["id"]))
                session["tenant_slug"] = memberships[0]["slug"] if memberships else "default"
            bkc_db.append_audit(
                int(user["id"]),
                None,
                "auth.login",
                "session",
                {"username": user["username"]},
                request.remote_addr,
            )
            return redirect(next_url)

    return render_template("login.html.j2", form=form, no_users=bkc_db.count_users() == 0)


@auth_blueprint.route("/logout", methods=["POST"])
def logout():
    logout_user()
    session.pop("tenant_slug", None)
    flash("Signed out.")
    return redirect(url_for("auth.login"))


@auth_blueprint.route("/profile/tenant", methods=["POST"])
@login_required
def switch_tenant():
    slug = (request.form.get("slug") or "default").strip().lower()
    tenant = bkc_db.fetch_tenant_by_slug(slug)
    if not tenant:
        flash("Unknown tenant.", "error")
        return redirect(request.referrer or url_for("index.index"))
    user_id = int(current_user.id)
    user = bkc_db.fetch_user_by_id(user_id)
    tid = int(tenant["id"])
    if user and user.get("is_superuser"):
        session["tenant_slug"] = tenant["slug"]
    elif bkc_db.membership_role(user_id, tid):
        session["tenant_slug"] = tenant["slug"]
    else:
        flash("You are not a member of that tenant.", "error")
        return redirect(request.referrer or url_for("index.index"))
    bkc_db.append_audit(
        user_id,
        tid,
        "auth.tenant_switch",
        f"tenant:{tid}",
        {"slug": tenant["slug"]},
        request.remote_addr,
    )
    return redirect(request.referrer or url_for("index.index"))
