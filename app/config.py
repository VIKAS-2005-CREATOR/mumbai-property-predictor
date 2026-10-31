import os
import secrets
import warnings

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
PROJECT_DIR = os.path.dirname(BASE_DIR)
INSTANCE_DIR = os.environ.get('INSTANCE_DIR', os.path.join(PROJECT_DIR, 'instance'))


def _dev_secret_key(path=None):
    """A random key kept in instance/secret_key so it survives server restarts.

    A new random key on every start would invalidate every open page's security
    token (and every login cookie), so the first form submit after a restart
    would fail. Production must set SECRET_KEY explicitly instead.
    """
    path = path or os.path.join(INSTANCE_DIR, 'secret_key')
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            key = fh.read().strip()
        if key:
            return key
    except OSError:
        pass
    key = secrets.token_hex(32)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(key)
    except OSError:
        pass                      # read-only disk: fall back to a per-process key
    return key


def _env_flag(name, default=False):
    """Read a boolean environment variable (1/true/yes/on are truthy)."""
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ('1', 'true', 'yes', 'on')


def _env_float(name, default):
    raw = os.environ.get(name)
    if raw is None:
        return float(default)
    try:
        return float(raw)
    except ValueError:
        warnings.warn(f'{name}={raw!r} is not a number; using {default}.')
        return float(default)


def _database_uri():
    # Use SQLite by default (ignores any legacy PostgreSQL URLs).
    url = os.environ.get('DATABASE_URL')
    if url and not url.startswith(('postgres://', 'postgresql://')):
        return url
    return 'sqlite:///' + os.path.join(INSTANCE_DIR, 'app.db')


class Config:
    APP_ENV = os.environ.get('APP_ENV', 'development')
    IS_PRODUCTION = APP_ENV == 'production'

    # The secret key signs sessions and CSRF tokens. It must be set explicitly
    # in production; in development one is generated once and stored in instance/.
    SECRET_KEY = os.environ.get('SECRET_KEY')
    if not SECRET_KEY:
        if IS_PRODUCTION:
            raise RuntimeError('SECRET_KEY must be set when APP_ENV=production.')
        SECRET_KEY = _dev_secret_key()

    # Server settings used by run.py (production should use gunicorn instead).
    HOST = os.environ.get('HOST', '127.0.0.1')
    PORT = int(os.environ.get('PORT', '5000'))
    DEBUG_MODE = _env_flag('FLASK_DEBUG', default=not IS_PRODUCTION) and not IS_PRODUCTION

    # Writable folder for the SQLite file.
    INSTANCE_DIR = INSTANCE_DIR

    # Database
    SQLALCHEMY_DATABASE_URI = _database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    AUTO_CREATE_DB = os.environ.get('AUTO_CREATE_DB', '1') == '1'

    # Session and CSRF hardening
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = IS_PRODUCTION
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = IS_PRODUCTION
    WTF_CSRF_TIME_LIMIT = 8 * 60 * 60

    # Rate limiting (use a shared backend such as redis:// with several workers)
    RATELIMIT_STORAGE_URI = os.environ.get('RATELIMIT_STORAGE_URI', 'memory://')
    PREDICT_RATE_LIMIT = os.environ.get('PREDICT_RATE_LIMIT', '30 per minute')
    LOGIN_RATE_LIMIT = os.environ.get('LOGIN_RATE_LIMIT', '10 per minute')
    REGISTER_RATE_LIMIT = os.environ.get('REGISTER_RATE_LIMIT', '5 per minute')
    HEATMAP_RATE_LIMIT = os.environ.get('HEATMAP_RATE_LIMIT', '60 per minute')

    # Machine learning artifacts
    ARTIFACTS_DIR = os.environ.get(
        'ARTIFACTS_DIR', os.path.join(PROJECT_DIR, 'ml_pipeline', 'artifacts')
    )
    MODEL_PATH = os.path.join(ARTIFACTS_DIR, 'model.pkl')
    SCORES_PATH = os.path.join(ARTIFACTS_DIR, 'model_scores.json')
    CATEGORIES_PATH = os.path.join(ARTIFACTS_DIR, 'categories.json')
    DATA_PATH = os.environ.get(
        'DATA_PATH', os.path.join(PROJECT_DIR, 'data', 'raw', 'mumbai_properties.csv')
    )

    # Scenario assumptions shown in the UI. These are inputs, not model output.
    ASSUMED_ANNUAL_GROWTH = _env_float('ASSUMED_ANNUAL_GROWTH', 0.06)
    ASSUMED_RENTAL_YIELD = _env_float('ASSUMED_RENTAL_YIELD', 0.035)

    # Station / metro proximity adjustment applied on top of the model estimate.
    # These are assumptions, not values learned from the dataset.
    TRANSIT_PREMIUM_WALKABLE = _env_float('TRANSIT_PREMIUM_WALKABLE', 0.06)
    TRANSIT_DISCOUNT_EXTENDED = _env_float('TRANSIT_DISCOUNT_EXTENDED', 0.05)

    # Purchase-cost assumptions (Mumbai / BMC rates; other municipal areas can
    # differ, so always confirm with the sub-registrar before relying on them).
    STAMP_DUTY_RATE = _env_float('STAMP_DUTY_RATE', 0.06)
    STAMP_DUTY_RATE_WOMEN = _env_float('STAMP_DUTY_RATE_WOMEN', 0.05)
    REGISTRATION_FEE_RATE = _env_float('REGISTRATION_FEE_RATE', 0.01)
    REGISTRATION_FEE_CAP = _env_float('REGISTRATION_FEE_CAP', 30000)
    LEGAL_FEE_ESTIMATE = _env_float('LEGAL_FEE_ESTIMATE', 35000)

    # Optional links shown in the footer
    SOURCE_CODE_URL = os.environ.get('SOURCE_CODE_URL', '')
    DATASET_URL = os.environ.get('DATASET_URL', '')
    CONTACT_EMAIL = os.environ.get('CONTACT_EMAIL', '')