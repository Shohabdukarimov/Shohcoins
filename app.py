import os
import hmac
import hashlib
import json
from urllib.parse import parse_qsl

from flask import Flask, render_template, request, jsonify, session
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)

app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "shohcoin-secret")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///users.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

BOT_TOKEN = os.environ.get("BOT_TOKEN")


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    telegram_id = db.Column(db.String(50), unique=True, nullable=False)
    username = db.Column(db.String(100), default="")
    first_name = db.Column(db.String(100), default="")
    balance = db.Column(db.Integer, default=0)
    clicks = db.Column(db.Integer, default=0)


with app.app_context():
    db.create_all()


def check_telegram_data(init_data):
    if not BOT_TOKEN or not init_data:
        return None

    data = dict(parse_qsl(init_data, keep_blank_values=True))

    received_hash = data.pop("hash", None)

    if not received_hash:
        return None

    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(data.items())
    )

    secret_key = hmac.new(
        b"WebAppData",
        BOT_TOKEN.encode(),
        hashlib.sha256
    ).digest()

    calculated_hash = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        return None

    user_data = json.loads(data.get("user", "{}"))

    return user_data


@app.route("/auth", methods=["POST"])
def auth():
    data = request.get_json() or {}
    init_data = data.get("initData")

    telegram_user = check_telegram_data(init_data)

    if not telegram_user:
        return jsonify({"error": "Telegram foydalanuvchisi tasdiqlanmadi"}), 401

    telegram_id = str(telegram_user["id"])

    user = User.query.filter_by(telegram_id=telegram_id).first()

    if not user:
        user = User(
            telegram_id=telegram_id,
            username=telegram_user.get("username", ""),
            first_name=telegram_user.get("first_name", "")
        )
        db.session.add(user)
    else:
        user.username = telegram_user.get("username", "")
        user.first_name = telegram_user.get("first_name", "")

    db.session.commit()

    session["telegram_id"] = telegram_id

    return jsonify({
        "ok": True,
        "telegram_id": telegram_id,
        "username": user.username,
        "first_name": user.first_name,
        "balance": user.balance,
        "clicks": user.clicks
    })


@app.route("/user", methods=["GET"])
def get_user():
    telegram_id = session.get("telegram_id")

    if not telegram_id:
        return jsonify({"error": "Avval Telegram orqali kiring"}), 401

    user = User.query.filter_by(telegram_id=telegram_id).first()

    if not user:
        return jsonify({"error": "Foydalanuvchi topilmadi"}), 404

    return jsonify({
        "telegram_id": user.telegram_id,
        "username": user.username,
        "first_name": user.first_name,
        "balance": user.balance,
        "clicks": user.clicks
    })


@app.route("/click", methods=["POST"])
def click():
    telegram_id = session.get("telegram_id")

    if not telegram_id:
        return jsonify({"error": "Avval Telegram orqali kiring"}), 401

    user = User.query.filter_by(telegram_id=telegram_id).first()

    if not user:
        return jsonify({"error": "Foydalanuvchi topilmadi"}), 404

    user.clicks += 1
    user.balance += 1

    db.session.commit()

    return jsonify({
        "balance": user.balance,
        "clicks": user.clicks
    })


@app.route("/leaderboard")
def leaderboard():
    users = User.query.order_by(User.balance.desc()).limit(100).all()

    return jsonify([
        {
            "telegram_id": user.telegram_id,
            "username": user.username,
            "first_name": user.first_name,
            "balance": user.balance
        }
        for user in users
    ])


@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
