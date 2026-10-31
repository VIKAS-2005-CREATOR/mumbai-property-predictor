"""
predictor.py
============
Prediction service for PropVal Insights.
Loads the trained ML model and dataset, and provides functions for property valuation, market trends, and heatmap data.
"""

import json
import logging
import math
import os
from functools import lru_cache
from typing import Any

import joblib  # pyright: ignore[reportMissingImports]
import pandas as pd
from flask import current_app

logger = logging.getLogger(__name__)

LAKH_PER_CRORE = 100.0
RUPEES_PER_CRORE = 10_000_000
RUPEES_PER_LAKH = 100_000

# Minimum listings required to avoid "limited data" warning
DEFAULT_MIN_REGION_LISTINGS = 30
# Minimum comparable listings required to calculate regional averages
MIN_COMPARABLES = 5

MIN_AREA_SQFT = 100.0
MAX_AREA_SQFT = 10_000.0
MAX_BHK = 10
MIN_ASKING_CR = 0.05
MAX_ASKING_CR = 1_000.0

TRANSIT_OPTIONS = ('walkable', 'standard', 'extended')

# ML model feature columns (must match training pipeline)
FEATURES = ['bhk', 'area', 'type', 'region', 'status', 'age']
TARGET = 'final_price'
REQUIRED_COLUMNS = set(FEATURES + [TARGET])

# Property price tiers for the heatmap
HEATMAP_TIERS = (
    {'key': 'ultra_luxury', 'min': 42_000, 'color': '#dc2626', 'label': 'Ultra-Luxury Core'},
    {'key': 'prime',        'min': 25_000, 'color': '#ea580c', 'label': 'Prime Suburbs'},
    {'key': 'established',  'min': 16_000, 'color': '#eab308', 'label': 'Established Mid-Market'},
    {'key': 'growth',       'min': 0,      'color': '#10b981', 'label': 'Value & Growth Hub'},
)


class PredictionError(Exception):
    """Raised when the input is invalid or a model artifact is missing."""


# ---------------------------------------------------------------------------
# Artifact loading
# ---------------------------------------------------------------------------

def _cfg(key):
    return current_app.config[key]


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


@lru_cache(maxsize=4)
def _load_model(path, mtime):
    # `mtime` is part of the cache key so a retrained model is picked up.
    return joblib.load(path)


@lru_cache(maxsize=2)
def _load_dataset(path, mtime):
    df = pd.read_csv(path)
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        logger.warning('Dataset %s is missing columns: %s', path, sorted(missing))
        return pd.DataFrame()
    df = df.dropna(subset=FEATURES + [TARGET])
    df = df[(df[TARGET] > 0) & (df['area'] > 0) & (df['bhk'] > 0)]
    df_any: Any = df
    return df_any.drop_duplicates(subset=FEATURES + [TARGET]).reset_index(drop=True)


def _model_for_request():
    path = _cfg('MODEL_PATH')
    mtime = _mtime(path)
    if mtime is None:
        raise PredictionError(
            'The trained model was not found. Run `python ml_pipeline/train.py` first.'
        )
    return _load_model(path, mtime)


def get_dataset():
    """Returns the loaded reference dataset."""
    path = _cfg('DATA_PATH')
    mtime = _mtime(path)
    if mtime is None:
        return pd.DataFrame()
    return _load_dataset(path, mtime)


_dataset = get_dataset          # kept as an alias for older call sites and tests
_json_cache = {}


def _read_json(path, default):
    mtime = _mtime(path)
    if mtime is None:
        return default
    cached = _json_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        logger.warning('Could not read %s; using defaults.', path, exc_info=True)
        return default
    _json_cache[path] = (mtime, data)
    return data


def get_categories():
    empty = {'types': [], 'regions': [], 'statuses': [], 'ages': [], 'region_counts': {}}
    return _read_json(_cfg('CATEGORIES_PATH'), empty)


def get_scores():
    return _read_json(_cfg('SCORES_PATH'), {})


def get_coordinates():
    folder = current_app.static_folder or ''
    path = os.path.join(folder, 'data', 'mumbai_coordinates.json')
    return _read_json(path, {})


def clear_caches():
    """Clears all cached model and dataset artifacts."""
    _load_model.cache_clear()
    _load_dataset.cache_clear()
    _compute_heatmap.cache_clear()
    _json_cache.clear()


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def _parse_number(value, label, low, high):
    if value is None or isinstance(value, bool) or str(value).strip() == '':
        raise PredictionError(f'{label} is required.')
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise PredictionError(f'{label} must be a number.') from None
    if not math.isfinite(number) or not low <= number <= high:
        raise PredictionError(f'{label} must be between {low:g} and {high:g}.')
    return number


def validate_inputs(bhk, area, prop_type, region, status, age,
                    asking_price=None, transit_proximity=None):
    """Validates and cleans user inputs for the prediction model."""
    categories = get_categories()

    bhk_value = _parse_number(bhk, 'BHK', 1, MAX_BHK)
    if bhk_value != int(bhk_value):
        raise PredictionError('BHK must be a whole number.')
    area_value = _parse_number(area, 'Carpet area', MIN_AREA_SQFT, MAX_AREA_SQFT)

    cleaned: dict[str, Any] = {'bhk': int(bhk_value), 'area': area_value}
    text_fields = (
        ('prop_type', 'property type', 'types',    prop_type),
        ('region',    'location',      'regions',  region),
        ('status',    'status',        'statuses', status),
        ('age',       'property age',  'ages',     age),
    )
    for field, label, key, raw in text_fields:
        text = '' if raw is None else str(raw).strip()
        if not text:
            raise PredictionError(f'The {label} is required.')
        options = categories.get(key) or []
        if options and text not in options:
            raise PredictionError(f'The selected {label} is not supported.')
        cleaned[field] = text

    if asking_price is None or str(asking_price).strip() == '':
        cleaned['asking_price'] = None
    else:
        cleaned['asking_price'] = _parse_number(
            asking_price, 'Asking price', MIN_ASKING_CR, MAX_ASKING_CR
        )

    transit = 'standard' if transit_proximity in (None, '') else str(transit_proximity).strip().lower()
    if transit not in TRANSIT_OPTIONS:
        raise PredictionError('The selected station proximity is not supported.')
    cleaned['transit_proximity'] = transit
    return cleaned


def transit_multiplier(transit):
    """Applies a price multiplier based on transit proximity."""
    if transit == 'walkable':
        return 1 + _cfg('TRANSIT_PREMIUM_WALKABLE')
    if transit == 'extended':
        return 1 - _cfg('TRANSIT_DISCOUNT_EXTENDED')
    return 1.0


# ---------------------------------------------------------------------------
# Prediction
# ---------------------------------------------------------------------------

def _estimate_cr(model, inputs, bhk, area):
    frame = pd.DataFrame([{
        'bhk': bhk, 'area': area, 'type': inputs['prop_type'],
        'region': inputs['region'], 'status': inputs['status'], 'age': inputs['age'],
    }])
    return float(model.predict(frame)[0]) / LAKH_PER_CRORE


def _market_insights(df, model, inputs, predicted, multiplier):
    """Generates market insights and comparable listings."""
    bhk, area, region = inputs['bhk'], inputs['area'], inputs['region']

    # What-if scenarios are evaluated by the model itself (same proximity factor).
    insights = {
        'whatif_bhk_cr':  round(_estimate_cr(model, inputs, bhk + 1, area + 200) * multiplier, 2),
        'whatif_area_cr': round(_estimate_cr(model, inputs, bhk, area + 500) * multiplier, 2),
    }
    recommendations = []
    if df.empty:
        return insights, recommendations

    same_bhk = df[df['bhk'] == bhk]
    similar = same_bhk[same_bhk['region'] == region]

    # 1. Regional average for the same BHK (only with enough listings)
    if len(similar) >= MIN_COMPARABLES:
        avg_cr = float(similar[TARGET].mean()) / LAKH_PER_CRORE
        insights['avg_count'] = len(similar)
        insights['avg_price_cr'] = round(avg_cr, 2)
        insights['avg_diff_pct'] = round((predicted - avg_cr) / avg_cr * 100, 1)

    # 2. Alternative regions within +/-20% of the estimate (enough listings each)
    if not same_bhk.empty:
        grouped = same_bhk.groupby('region')[TARGET].agg(['mean', 'count'])
        grouped = grouped[grouped['count'] >= MIN_COMPARABLES]
        region_avgs = grouped['mean'] / LAKH_PER_CRORE
        band = region_avgs[
            (region_avgs >= predicted * 0.80) & (region_avgs <= predicted * 1.20)
        ].drop(labels=[region], errors='ignore')
        if not band.empty:
            closest = (band - predicted).abs().sort_values().head(3).index
            insights['alternative_regions'] = [
                {'name': str(name), 'avg_price': round(float(region_avgs[name]), 2)}
                for name in closest
            ]

    # 3. Comparable listings: closest by relative price and area distance
    if not similar.empty:
        comps = similar.copy()
        comps['price_cr'] = comps[TARGET] / LAKH_PER_CRORE
        comps['distance'] = (
            (comps['price_cr'] - predicted).abs() / predicted
            + (comps['area'] - area).abs() / area
        )
        for _, row in comps.nsmallest(3, 'distance').iterrows():
            locality = row.get('locality')
            if locality is None or pd.isna(locality):
                locality = 'Unknown'
            recommendations.append({
                'bhk':      int(row['bhk']),
                'area':     int(row['area']),
                'type':     str(row['type']),
                'locality': str(locality),
                'status':   str(row['status']),
                'price_cr': round(float(row['price_cr']), 2),
            })
    return insights, recommendations


def predict_price(bhk, area, prop_type, region, status, age,
                  asking_price=None, transit_proximity=None) -> dict[str, Any]:
    """
    Predicts the property price based on inputs and returns a detailed valuation dictionary.
    Returns an error message dictionary on failure.
    """
    try:
        # --- STEP 1: VALIDATE & CLEAN USER INPUTS ---
        inputs = validate_inputs(bhk, area, prop_type, region, status, age,
                                 asking_price, transit_proximity)
        model = _model_for_request()

        # --- STEP 2: GRADIENT BOOSTING PREDICTION ---
        base_cr = _estimate_cr(model, inputs, inputs['bhk'], inputs['area'])
        if not math.isfinite(base_cr) or base_cr <= 0:
            raise PredictionError('The model returned an invalid estimate.')
        multiplier = transit_multiplier(inputs['transit_proximity'])
        predicted = base_cr * multiplier

        # --- STEP 3: PREDICTION INTERVAL (90% EMPIRICAL CONFIDENCE BAND) ---
        metrics = get_scores().get('best_metrics') or {}
        min_listings = metrics.get('min_region_listings') or DEFAULT_MIN_REGION_LISTINGS
        region_counts = get_categories().get('region_counts') or {}
        region_listings = int(region_counts.get(inputs['region'], 0)) if region_counts else None
        low_data = region_listings is not None and region_listings < min_listings

        # Thinly covered regions get the wider band measured on such regions.
        interval = (metrics.get('interval_sparse') if low_data else None) \
            or metrics.get('interval') or {}
        price_low = price_high = None
        if interval.get('low') and interval.get('high'):
            price_low = round(predicted * interval['low'], 2)
            price_high = round(predicted * interval['high'], 2)

        insights, recommendations = _market_insights(
            get_dataset(), model, inputs, predicted, multiplier
        )

        result = {
            'inputs':               inputs,
            'predicted_price':      round(predicted, 2),
            'base_predicted_price': round(base_cr, 2),
            'price_low':            price_low,
            'price_high':           price_high,
            'typical_error_pct':    metrics.get('mape_pct'),
            'price_per_sqft':       round(predicted * RUPEES_PER_CRORE / inputs['area']),
            'region_listings':      region_listings,
            'low_data':             low_data,
            'transit_proximity':    inputs['transit_proximity'],
            'transit_adjustment_pct': round((multiplier - 1) * 100, 1),
            'transit_impact_lakh':  round((predicted - base_cr) * LAKH_PER_CRORE, 1),
            'insights':             insights,
            'recommendations':      recommendations,
        }

        asking = inputs['asking_price']
        if asking is not None:
            diff_pct = (asking - predicted) / predicted * 100
            if price_low is not None and price_high is not None:
                verdict = 'below' if asking < price_low else 'above' if asking > price_high else 'within'
            else:
                verdict = 'below' if diff_pct < -10 else 'above' if diff_pct > 10 else 'within'
            result.update(
                asking_price=asking,
                asking_diff_pct=round(diff_pct, 1),
                asking_verdict=verdict,
            )
        return result

    except PredictionError as exc:
        return {'error': str(exc)}
    except Exception:
        current_app.logger.exception('Prediction failed')
        return {'error': 'Prediction failed. Please try again later.'}


# ---------------------------------------------------------------------------
# Market trends and heatmap
# ---------------------------------------------------------------------------
# Leaflet Heatmap / Price Tiers
# ---------------------------------------------------------------------------


def _tier_for(rate):
    for tier in HEATMAP_TIERS:
        if rate >= tier['min']:
            return tier
    return HEATMAP_TIERS[-1]


@lru_cache(maxsize=2)
def _compute_heatmap(path, mtime, coords_json, min_listings):
    df = _load_dataset(path, mtime)
    if df.empty:
        return []
    coords_map = json.loads(coords_json)

    valid = df[(df['area'] > MIN_AREA_SQFT) & (df[TARGET] > 5)].copy()
    valid['rate_sqft'] = valid[TARGET] * RUPEES_PER_LAKH / valid['area']
    agg = valid.groupby('region').agg(
        rate=('rate_sqft', 'median'),
        price_lakh=(TARGET, 'median'),
        count=(TARGET, 'count'),
    ).reset_index()

    regions = []
    for row in agg.to_dict('records'):
        coords = coords_map.get(str(row['region']))
        if not coords:
            continue
        rate = round(float(row['rate']))
        tier = _tier_for(rate)
        count = int(float(row['count']))
        regions.append({
            'region':       str(row['region']),
            'lat':          coords[0],
            'lng':          coords[1],
            'avg_rate':     rate,
            'avg_price_cr': round(float(row['price_lakh']) / LAKH_PER_CRORE, 2),
            'count':        count,
            'low_data':     count < min_listings,
            'tier':         tier['key'],
            'color':        tier['color'],
            'label':        tier['label'],
        })
    return sorted(regions, key=lambda item: item['avg_rate'], reverse=True)


def get_heatmap_data():
    """Returns heatmap tier definitions and region data."""
    tiers = []
    for index, tier in enumerate(HEATMAP_TIERS):
        tier_min = int(tier['min'])
        upper = int(HEATMAP_TIERS[index - 1]['min']) if index else None
        if upper is None:
            text = f"> ₹{tier_min // 1000}k"
        elif tier_min == 0:
            text = f"< ₹{upper // 1000}k"
        else:
            text = f"₹{tier_min // 1000}k - ₹{upper // 1000}k"
        tiers.append({**tier, 'range': text})

    path = _cfg('DATA_PATH')
    mtime = _mtime(path)
    if mtime is None:
        return {'tiers': tiers, 'regions': []}
    min_listings = (get_scores().get('best_metrics') or {}).get(
        'min_region_listings') or DEFAULT_MIN_REGION_LISTINGS
    coords_json = json.dumps(get_coordinates(), sort_keys=True)
    return {'tiers': tiers, 'regions': _compute_heatmap(path, mtime, coords_json, min_listings)}