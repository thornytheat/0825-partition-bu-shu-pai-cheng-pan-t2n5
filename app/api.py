from flask import Blueprint, Response, current_app, jsonify, request
import json

from app.business import DomainIssue
from app.evidence.render import artifact
from app.inputs import read_input
from app.modules.pipeline import run
from app.review.service import (
    change_schedule,
    execute,
    mark_critical,
    normalize_critical,
    normalize_reservations,
    reservations_from_data,
    review,
    update_reservations,
)

api = Blueprint('api', __name__, url_prefix='/api')
STAGES = {'intake': 1, 'prepare': 2, 'calculate': 3, 'policy': 4, 'balance': 5}


@api.post('/stages/<stage>')
def preview(stage):
    if stage not in STAGES:
        raise DomainIssue('STAGE_UNKNOWN')
    options = _options_from_request()
    return jsonify(run(read_input(), STAGES[stage], options or None))


def _options_from_request():
    """预览阶段也允许通过表单/JSON 传入关键分区等选项。"""
    body = request.get_json(silent=True) if request.is_json else request.form
    if not isinstance(body, dict):
        return {}
    options = {}
    if body.get('critical'):
        raw = body.get('critical')
        if isinstance(raw, str):
            text = raw.strip()
            if text.startswith('['):
                try:
                    raw = json.loads(text)
                except ValueError:
                    raw = None
            else:
                raw = [part for part in text.replace('，', ',').split(',') if part.strip()]
        options['critical'] = normalize_critical(raw or [])
    if body.get('quota') not in (None, ''):
        options['quota'] = int(body['quota'])
    if body.get('reservations'):
        raw = body.get('reservations')
        if isinstance(raw, str):
            raw = json.loads(raw)
        options['reservations'] = normalize_reservations(raw)
    return options


@api.route('/runs', methods=['GET', 'POST'])
def runs():
    store = current_app.extensions['runs']
    if request.method == 'GET':
        return jsonify(items=store.list())
    options = _options_from_request()
    return jsonify(execute(store, read_input(), options or None))


@api.route('/runs/<run_id>', methods=['GET', 'PATCH'])
def batch(run_id):
    store = current_app.extensions['runs']
    if request.method == 'GET':
        return jsonify(store.load(run_id))
    return jsonify(review(store, run_id, request.get_json(silent=True)))


@api.get('/runs/<run_id>/evidence')
def export(run_id):
    report = current_app.extensions['runs'].load(run_id)
    return Response(artifact(report), mimetype='application/json', headers={
        'Content-Disposition': f'attachment; filename="review-{report["id"][:12]}.json"',
        'Cache-Control': 'no-store',
    })


def _body():
    payload = request.get_json(silent=True)
    if isinstance(payload, (dict, list)):
        return payload
    if request.form:
        return request.form.to_dict(flat=True)
    return {}


def _reservations_from_request(body):
    """优先用 JSON/表单中的区间；否则若带了 sample/file，则从 CSV 提取。"""
    if isinstance(body, dict):
        for key in ('reservations', 'reservation', 'items', 'windows', 'blocks'):
            if key in body:
                return normalize_reservations(body[key])
        if request.files.get('file') or body.get('sample') is not None:
            data = read_input()
            _, items = reservations_from_data(data)
            return items
        if all(k in body for k in ('release', 'deadline')) or all(k in body for k in ('start', 'end')):
            return normalize_reservations([body])
    elif isinstance(body, list):
        return normalize_reservations(body)
    # 空体视为清空预留。
    if request.method in ('PUT', 'POST', 'PATCH'):
        return normalize_reservations(body if isinstance(body, (list, dict)) else None)
    return None


@api.route('/reservations', methods=['GET', 'PUT', 'POST', 'PATCH'])
def reservations_collection():
    store = current_app.extensions['runs']
    if request.method == 'GET':
        return jsonify(store.reservation_state())
    body = _body()
    raw_body = body
    if isinstance(body, list):
        body = {'reservations': body}
    current_state = store.reservation_state()
    if isinstance(body, dict) and type(body.get('version')) is int \
            and body['version'] != current_state['version']:
        raise DomainIssue('STALE_REVIEW', 'VERSION_CONFLICT', 'STALE_RESERVATION')
    items = _reservations_from_request(raw_body)
    state = update_reservations(store, items)
    run_id = body.get('run_id') or body.get('run') if isinstance(body, dict) else None
    if run_id:
        result = change_schedule(store, run_id, body)
        return jsonify(result)
    return jsonify(state)


# 常见路径别名，降低与上游系统对接的命名偏差。
api.add_url_rule('/reservation', view_func=reservations_collection,
                 methods=['GET', 'PUT', 'POST', 'PATCH'])
api.add_url_rule('/upstream/reservations', view_func=reservations_collection,
                 methods=['GET', 'PUT', 'POST', 'PATCH'])
api.add_url_rule('/board/reservations', view_func=reservations_collection,
                 methods=['GET', 'PUT', 'POST', 'PATCH'])


@api.route('/critical', methods=['GET', 'PUT', 'POST', 'PATCH'])
def critical_collection():
    store = current_app.extensions['runs']
    if request.method == 'GET':
        raw = store.get_meta('critical_json', '[]')
        return jsonify(critical=json.loads(raw or '[]'))
    body = _body()
    if isinstance(body, list):
        body = {'critical': body}
    raw = body.get('critical', body.get('ids', body.get('tasks', [])))
    ids = normalize_critical(raw)
    store.set_meta('critical_json', json.dumps(ids, ensure_ascii=False))
    run_id = body.get('run_id') or body.get('run')
    if run_id:
        body = dict(body)
        body['critical'] = ids
        return jsonify(change_schedule(store, run_id, body))
    return jsonify(critical=ids)


@api.route('/runs/<run_id>/critical', methods=['GET', 'PUT', 'POST', 'PATCH'])
def run_critical(run_id):
    store = current_app.extensions['runs']
    current = store.load(run_id)
    if request.method == 'GET':
        tasks = []
        for case in current['report'].get('cases', []):
            crit = (case.get('measure') or {}).get('critical_total')
            tasks.append({'case': case['case'], 'critical_total': crit})
        return jsonify(run_id=run_id, cases=tasks)
    body = _body()
    if isinstance(body, list):
        body = {'critical': body}
    if not isinstance(body, dict):
        body = {}
    if 'revision' not in body:
        body['revision'] = current['revision']
    raw = body.get('critical', body.get('ids', body.get('tasks', [])))
    body['critical'] = normalize_critical(raw)
    return jsonify(change_schedule(store, run_id, body))


@api.post('/runs/<run_id>/reschedule')
def run_reschedule(run_id):
    store = current_app.extensions['runs']
    body = _body()
    current = store.load(run_id)
    body.setdefault('revision', current['revision'])
    return jsonify(change_schedule(store, run_id, body))


@api.route('/runs/<run_id>/reservations', methods=['GET', 'PUT', 'POST', 'PATCH'])
@api.route('/runs/<run_id>/reservation', methods=['GET', 'PUT', 'POST', 'PATCH'])
def run_reservations(run_id):
    store = current_app.extensions['runs']
    current = store.load(run_id)
    if request.method == 'GET':
        return jsonify(version=current['revision'],
                       reservation_digest=current['report'].get('reservation_digest'),
                       cases={c['case']: (c.get('prep') or {}).get('details', {}).get('reserved_slots')
                              for c in current['report'].get('cases', [])})
    body = _body()
    if not isinstance(body, dict):
        body = {'reservations': body}
    body.setdefault('revision', current['revision'])
    # 兼容直接把区间数组作为请求体，或用 reservation/items 包裹。
    if 'reservations' not in body and 'reservation' not in body:
        if request.is_json and isinstance(request.get_json(silent=True), list):
            body['reservations'] = request.get_json(silent=True)
        elif any(k in body for k in ('release', 'start')):
            body['reservations'] = [body]
    return jsonify(change_schedule(store, run_id, body))


@api.route('/runs/<run_id>/schedule', methods=['GET'])
def run_schedule(run_id):
    run = current_app.extensions['runs'].load(run_id)
    report = run['report']
    cases = {}
    for case in report.get('cases', []):
        measure = case.get('measure') or {}
        cases[case['case']] = {
            'status': case.get('status'),
            'schedule': (measure.get('evidence') or {}).get('schedule', {}),
            'rejected': (measure.get('evidence') or {}).get('rejected', {}),
            'scheduled': measure.get('scheduled', measure.get('value')),
            'quota': measure.get('quota', measure.get('expected')),
            'quota_met': measure.get('quota_met'),
            'critical_scheduled': measure.get('critical_scheduled'),
            'critical_total': measure.get('critical_total'),
        }
    return jsonify(run_id=run_id, revision=run['revision'],
                   reservation_version=report.get('reservation_version'),
                   reservation_digest=report.get('reservation_digest'),
                   balance=report.get('balance'), cases=cases)


@api.get('/schedule')
def latest_schedule():
    store = current_app.extensions['runs']
    items = store.list()
    if not items:
        raise DomainIssue('RUN_NOT_FOUND')
    return run_schedule(items[0]['id'])


@api.get('/board')
def board_state():
    store = current_app.extensions['runs']
    state = store.reservation_state()
    state['critical'] = json.loads(store.get_meta('critical_json', '[]') or '[]')
    items = store.list()
    state['latest_run'] = items[0] if items else None
    return jsonify(state)
