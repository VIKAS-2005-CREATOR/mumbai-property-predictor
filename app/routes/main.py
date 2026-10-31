from collections import Counter
from typing import Any, cast
from flask import (
    Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for,
)
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError
from app.extensions import csrf, db, limiter
from app.models import ValuationHistory
from app.services import finance, predictor

main_bp = Blueprint('main', __name__)

FORM_FIELDS = ('bhk', 'area', 'prop_type', 'region', 'status', 'age',
               'asking_price', 'transit_proximity')

DEFAULTS = {
    'bhk': '2', 'area': '850', 'prop_type': 'Apartment', 'region': 'Andheri West',
    'status': 'Ready to move', 'age': 'New', 'asking_price': '', 'transit_proximity': 'standard',
}

SELECT_FIELDS = {
    'prop_type': 'types', 'region': 'regions', 'status': 'statuses', 'age': 'ages',
}

HISTORY_LIMIT = 200


def _form_values(source, categories, submitted):
    """Fill form with user input or default values."""
    values = {}
    for field in FORM_FIELDS:
        raw = (source.get(field) or '').strip()
        values[field] = raw if (submitted or raw) else DEFAULTS[field]
    if not submitted:
        for field, key in SELECT_FIELDS.items():
            options = categories.get(key) or []
            if options and values[field] not in options:
                values[field] = options[0]
    return values


def _predict_from(values):
    return predictor.predict_price(
        values['bhk'], values['area'], values['prop_type'], values['region'],
        values['status'], values['age'], values.get('asking_price'),
        values.get('transit_proximity'),
    )


@main_bp.route('/')
def index():
    categories = predictor.get_categories()
    model_scores = predictor.get_scores()
    submitted = 'area' in request.args
    form = _form_values(request.args, categories, submitted)

    context = {
        'categories':   categories,
        'model_scores': model_scores,
        'metrics':      model_scores.get('best_metrics') or {},
        'form':         form,
    }

    if submitted:
        result = _predict_from(form)
        if 'error' in result:
            context['form_error'] = result['error']
        else:
            context.update(result)
            context['cost'] = finance.get_quick_cost_summary(result['predicted_price'])

    return render_template('dashboard/index.html', **context)


@main_bp.route('/predict', methods=['POST'])
@limiter.limit(lambda: current_app.config['PREDICT_RATE_LIMIT'])
def predict():
    """Validate inputs, save to user history if logged in, and redirect."""
    values = {field: (request.form.get(field) or '').strip() for field in FORM_FIELDS}
    result = _predict_from(values)

    if 'error' not in result and current_user.is_authenticated:
        res_data = cast(dict[str, Any], result)
        inputs = cast(dict[str, Any], res_data['inputs'])
        try:
            db.session.add(ValuationHistory(
                bhk=inputs['bhk'],
                carpet_area=inputs['area'],
                prop_type=inputs['prop_type'],
                region=inputs['region'],
                status=inputs['status'],
                age=inputs['age'],
                predicted_price=float(res_data['predicted_price']),
                user_id=current_user.id,
            ))
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception('Could not save valuation history')

    params = {key: value for key, value in values.items() if value}
    return redirect(url_for('main.index', _anchor='predictor', **cast(Any, params)))


@main_bp.route('/how-it-works')
def how_it_works():
    return render_template('dashboard/how_it_works.html')


@main_bp.route('/history')
@login_required
def history():
    valuations = (
        current_user.valuations
        .order_by(ValuationHistory.timestamp.desc())
        .limit(HISTORY_LIMIT)
        .all()
    )
    return render_template('history/history.html', valuations=valuations)


@main_bp.route('/profile')
def profile():
    """Personal user profile dashboard."""
    if not current_user.is_authenticated:
        flash('Please log in with your credentials to access your personal profile dashboard.', 'info')
        return redirect(url_for('auth.login', next=request.path))

    all_valuations = current_user.valuations.order_by(ValuationHistory.timestamp.desc()).all()
    total_count = len(all_valuations)
    recent_valuations = all_valuations[:5]
    avg_price = (sum(v.predicted_price for v in all_valuations) / total_count) if total_count > 0 else None

    top_region = None
    if total_count > 0:
        regions = [v.region for v in all_valuations if v.region]
        if regions:
            top_region = Counter(regions).most_common(1)[0][0]

    return render_template(
        'profile/profile.html',
        user=current_user,
        total_count=total_count,
        recent_valuations=recent_valuations,
        avg_price=avg_price,
        top_region=top_region,
    )


@main_bp.route('/api/predict', methods=['POST'])  # type: ignore
@csrf.exempt
@limiter.limit(lambda: current_app.config['PREDICT_RATE_LIMIT'])
def api_predict():
    """JSON API for predictions."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error='Send a JSON object as the request body.'), 400
    result = predictor.predict_price(
        data.get('bhk'), data.get('area'), data.get('prop_type'),
        data.get('region'), data.get('status'), data.get('age'),
        data.get('asking_price'), data.get('transit_proximity'),
    )
    if 'error' not in result:
        result['cost'] = finance.get_quick_cost_summary(result['predicted_price'])
    return jsonify(result), (400 if 'error' in result else 200)


@main_bp.route('/api/heatmap')
@main_bp.route('/api/heatmap-data')
@limiter.limit(lambda: current_app.config['HEATMAP_RATE_LIMIT'])
def api_heatmap_data():
    """Price tiers and per-region rates for the Leaflet heatmap overlay."""
    response = jsonify(predictor.get_heatmap_data())
    response.headers['Cache-Control'] = 'public, max-age=300'
    return response


@main_bp.route('/healthz')
def healthz():
    return jsonify(status='ok')


@main_bp.route('/about')
def about():
    categories = predictor.get_categories()
    num_regions = len(categories.get('regions', []))
    num_listings = sum(categories.get('region_counts', {}).values())
    context = {
        'num_regions': num_regions,
        'num_listings': num_listings,
    }
    return render_template('public/about.html', **context)


@main_bp.route('/terms')
def terms():
    return render_template('public/terms.html')


@main_bp.route('/privacy')
def privacy():
    return render_template('public/privacy.html')