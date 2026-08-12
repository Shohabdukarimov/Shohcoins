from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///shohcoin.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Foydalanuvchi modeli
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    telegram_id = db.Column(db.String(50), unique=True, nullable=False)
    balance = db.Column(db.Integer, default=0)
    clicks = db.Column(db.Integer, default=0)

# Ma’lumotlar bazasini yaratish
with app.app_context():
    db.create_all()
@app.route('/user', methods=['GET'])
def get_user():
    telegram_id = request.args.get('telegram_id')

    if not telegram_id:
        return jsonify({"error": "telegram_id kerak"}), 400

    user = User.query.filter_by(telegram_id=telegram_id).first()

    if not user:
        user = User(telegram_id=telegram_id)
        db.session.add(user)
        db.session.commit()

    return jsonify({
        "balance": user.balance,
        "clicks": user.clicks
    })
# API: Foydalanuvchi klik bosganda ishlaydi
@app.route('/click', methods=['POST'])
def click():
    data = request.json
    telegram_id = data.get('telegram_id')

    user = User.query.filter_by(telegram_id=telegram_id).first()
    if not user:
        user = User(telegram_id=telegram_id, balance=0, clicks=0)
        db.session.add(user)
    
    user.clicks += 1
    user.balance += 1  # Har bir klik 1 Shohcoin
    db.session.commit()
    
    return jsonify({"balance": user.balance, "clicks": user.clicks})

# UI sahifasini ko‘rsatish
@app.route('/')
def index():
    return render_template('index.html')

import os

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
