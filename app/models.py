from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db


def _utcnow():
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    """Registered application user."""
    __tablename__ = 'user'

    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(64),  index=True, unique=True, nullable=False)
    email         = db.Column(db.String(120), index=True, unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    is_admin      = db.Column(db.Boolean, default=False, nullable=False)
    created_at    = db.Column(db.DateTime, default=_utcnow)

    valuations = db.relationship(
        'ValuationHistory', backref='author', lazy='dynamic',
        cascade='all, delete-orphan',
    )

    def __init__(
        self,
        username: str = '',
        email: str = '',
        password_hash: str | None = None,
        is_admin: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.username = username
        self.email = email
        self.password_hash = password_hash
        self.is_admin = is_admin

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password, method='pbkdf2:sha256')

    def check_password(self, password: str) -> bool:
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f'<User {self.username!r}>'


class ValuationHistory(db.Model):
    """One ML valuation run saved to a user's history."""
    __tablename__ = 'valuation_history'

    id              = db.Column(db.Integer, primary_key=True)
    bhk             = db.Column(db.Integer,  nullable=False)
    carpet_area     = db.Column(db.Float,    nullable=False)
    prop_type       = db.Column(db.String(100))
    region          = db.Column(db.String(100))
    status          = db.Column(db.String(50))
    age             = db.Column(db.String(50))
    predicted_price = db.Column(db.Float,    nullable=False)
    timestamp       = db.Column(db.DateTime, index=True, default=_utcnow)
    user_id         = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)

    def __init__(
        self,
        bhk: int,
        carpet_area: float,
        predicted_price: float,
        user_id: int,
        prop_type: str | None = None,
        region: str | None = None,
        status: str | None = None,
        age: str | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.bhk = bhk
        self.carpet_area = carpet_area
        self.predicted_price = predicted_price
        self.user_id = user_id
        self.prop_type = prop_type
        self.region = region
        self.status = status
        self.age = age

    def __repr__(self):
        return f'<ValuationHistory id={self.id} price={self.predicted_price}>'