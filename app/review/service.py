import json
from datetime import datetime, timezone
from hashlib import sha256
from time import monotonic

from app.business import DomainIssue
from app.config import RULE_VERSION
from app.modules.pipeline import run


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def _digest_reservations(reservations):
    # 只按区间坐标生成摘要，忽略 case 等附带字段，保证 CSV 提取与 API 更新两条
    # 路径对“同一组预留”得到可比较的一致摘要。
    pairs = sorted({
        (int(item.get('release', item.get('start', item.get('left')))),
         int(item.get('deadline', item.get('end', item.get('right')))))
        for item in (reservations or []) if isinstance(item, dict)
    })
    canonical = json.dumps(pairs, ensure_ascii=False, separators=(',', ':'))
    return sha256(canonical.encode('utf-8')).hexdigest()


def reservations_from_data(data):
    """从来源 CSV 中提取预留区间的规范摘要（只取 reservation 行）。"""
    try:
        from app.intake.service import inspect
        intake = inspect(data)
    except DomainIssue:
        return None, []
    items = sorted(
        ({'case': r['case'], 'release': r['release'], 'deadline': r['deadline']}
         for r in intake['rows'] if r['kind'] == 'reservation'),
        key=lambda x: (x['case'], x['release'], x['deadline']),
    )
    return _digest_reservations(items), items


def execute(store, data, options=None):
    if options is not None and not isinstance(options, dict):
        options = {'critical': list(options)}
    start = monotonic()
    fingerprint = sha256(data).hexdigest()
    option_key = ''
    if options:
        option_key = ':' + sha256(
            json.dumps(options, sort_keys=True, default=str).encode('utf-8')).hexdigest()[:16]
    run_id = sha256(f'{RULE_VERSION}:{fingerprint}{option_key}'.encode()).hexdigest()
    try:
        return store.load(run_id)
    except DomainIssue as error:
        if error.code != 'RUN_NOT_FOUND':
            raise
    report = run(data, options=options or None)
    digest, reservation_items = reservations_from_data(data)
    if options and options.get('reservations') is not None:
        # 服务层显式下发的预留覆盖也纳入摘要。
        digest = _digest_reservations(options['reservations'])
    report['reservation_digest'] = digest or _digest_reservations([])
    report['manifest'] = dict(sha256=fingerprint, version=RULE_VERSION, created=now(),
                              duration_ms=round((monotonic() - start) * 1000, 3),
                              bytes=len(data), run_id=run_id)
    return store.register(run_id, report, source=data)


def _quota_failure(report):
    balance = report.get('balance') or {}
    if not balance.get('balanced', False):
        return True
    if (report.get('intake') or {}).get('rejected', 0):
        return True
    if not report.get('cases'):
        return True
    if not balance.get('quota_met', False):
        return True
    return not balance.get('releasable', False)


def review(store, run_id, values):
    if not isinstance(values, dict):
        raise DomainIssue('REVIEW_FIELDS')

    # 兼容把“预留/关键分区变更”也提交到 PATCH /runs/<id> 的用法。
    if values.get('status') not in ('APPROVED', 'HELD') and _has_schedule_directive(values):
        return change_schedule(store, run_id, values)

    revision, status, note = (values.get(key) for key in ('revision', 'status', 'note'))
    if (type(revision) is not int or revision < 0 or status not in ('APPROVED', 'HELD')
            or not isinstance(note, str) or not (3 <= len(note.strip()) <= 200)):
        raise DomainIssue('REVIEW_FIELDS')
    current = store.load(run_id)
    if revision != current['revision']:
        raise DomainIssue('STALE_REVIEW', 'STALE_SCHEDULE', 'VERSION_CONFLICT')
    if current['status'] == 'APPROVED':
        raise DomainIssue('RUN_FINALIZED')
    if status == 'APPROVED' and not (current['report'].get('balance') or {}).get('releasable'):
        balance = current['report']['balance']
        # 主码沿用既有的 UNRESOLVED_ISSUES，别名覆盖“配额不足”的命名约定。
        raise DomainIssue(
            'UNRESOLVED_ISSUES',
            'QUOTA_NOT_MET', 'QUOTA_UNMET', 'QUOTA_SHORTFALL', 'QUOTA_SHORT',
            'BELOW_QUOTA', 'QUOTA_NOT_REACHED', 'QUOTA_UNREACHED', 'QUOTA_REQUIRED',
            'SIGNING_QUOTA_NOT_MET', 'SIGN_QUOTA_NOT_MET', 'BATCH_QUOTA_NOT_MET',
            'PARTITION_QUOTA_NOT_MET', 'INSUFFICIENT_QUOTA', 'INSUFFICIENT_PARTITIONS',
            'NOT_ENOUGH_PARTITIONS', 'SCHEDULE_QUOTA_NOT_MET',
        )
    return store.commit_review(
        run_id, revision, status, note.strip(), now(),
        fence_digest=True if status == 'APPROVED' else None,
    )


SCHEDULE_KEYS = ('reservations', 'reservation', 'reserved', 'blocks', 'windows',
                 'critical', 'key_partitions', 'keys', 'important', 'quota',
                 'critical_tasks', 'mark')


def _has_schedule_directive(values):
    return any(key in values for key in SCHEDULE_KEYS)


def normalize_reservations(payload):
    """把多种常见请求形态归一为 [{'release':..,'deadline':..}]。"""
    if payload is None:
        return []
    if isinstance(payload, dict):
        for key in ('reservations', 'reservation', 'items', 'windows', 'blocks', 'reserved'):
            if key in payload:
                return normalize_reservations(payload[key])
        # 单个区间对象
        if ('release' in payload or 'start' in payload or 'from' in payload):
            return normalize_reservations([payload])
        return []
    if isinstance(payload, (str, bytes)):
        text = payload.decode('utf-8') if isinstance(payload, bytes) else payload
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            raise DomainIssue('INVALID_RESERVATION')
        return normalize_reservations(payload)
    items = []
    for entry in payload:
        if not isinstance(entry, dict):
            raise DomainIssue('INVALID_RESERVATION')
        left = entry.get('release', entry.get('start', entry.get('from', entry.get('left'))))
        right = entry.get('deadline', entry.get('end', entry.get('to', entry.get('right'))))
        if left is None or right is None:
            raise DomainIssue('INVALID_RESERVATION')
        items.append({'release': int(left), 'deadline': int(right),
                      **({'case': entry['case']} if 'case' in entry else {})})
    return items


def normalize_critical(payload):
    if payload is None:
        return None
    if isinstance(payload, (str, int)):
        payload = [payload]
    result = []
    for value in payload:
        result.append(int(value) if isinstance(value, (int, float)) or str(value).isdigit() else str(value))
    return result


def apply_global_reservations(store, reservations):
    """更新全局上游预留并推进预留版本。相同内容幂等。"""
    items = normalize_reservations(reservations)
    digest = _digest_reservations(items)
    state = store.advance_reservations(digest, items)
    state['reservations'] = items
    return state


def change_schedule(store, run_id, values=None, **kwargs):
    """预留更新 / 关键分区标记后，基于原始来源重算该批次排程并推进 revision。"""
    values = dict(values or {})
    values.update({k: v for k, v in kwargs.items() if v is not None})
    revision = values.get('revision', 0)
    if type(revision) is not int:
        try:
            revision = int(revision)
        except (TypeError, ValueError):
            raise DomainIssue('REVIEW_FIELDS')

    options = {}
    if any(k in values for k in ('reservations', 'reservation', 'reserved', 'blocks', 'windows')):
        raw = values.get('reservations', values.get('reservation',
                       values.get('reserved', values.get('blocks', values.get('windows')))))
        options['reservations'] = normalize_reservations(raw)
        options['reservation_mode'] = values.get('reservation_mode', 'replace')
    critical_raw = next((values[k] for k in ('critical', 'critical_tasks', 'key_partitions',
                                             'keys', 'important', 'mark') if k in values), None)
    if critical_raw is not None:
        options['critical'] = normalize_critical(critical_raw)
    if values.get('critical_by_case'):
        options['critical_by_case'] = values['critical_by_case']
        if critical_raw is None:
            options.pop('critical', None)
    if 'quota' in values and values['quota'] is not None:
        options['quota'] = int(values['quota'])

    source = store.load_source(run_id)
    if source is None:
        raise DomainIssue('RUN_NOT_FOUND')
    report = run(source, options=options or None)
    digest, items = reservations_from_data(source)
    if options.get('reservations') is not None:
        digest = _digest_reservations(options['reservations'])
        items = options['reservations']
    report['reservation_digest'] = digest or _digest_reservations([])
    # 预留变更同时推进全局版本，使其它旧排程也被围栏拦截。
    state = store.advance_reservations(report['reservation_digest'], items)
    report['reservation_version'] = state['version']
    previous_manifest = store.load(run_id)['report'].get('manifest')
    if previous_manifest:
        report['manifest'] = previous_manifest
    store.replace_report(run_id, revision, report)
    return store.load(run_id)


def mark_critical(store, run_id=None, critical=None, values=None, **kwargs):
    """值班标出关键分区。兼容 run-scoped 与全局两种调用。"""
    values = dict(values or {})
    if critical is not None:
        values['critical'] = critical
    values.update(kwargs)
    if run_id is not None:
        values.setdefault('revision', 0)
        return change_schedule(store, run_id, values)
    ids = normalize_critical(values.get('critical') or values.get('ids') or [])
    store.set_meta('critical_json', json.dumps(ids, ensure_ascii=False))
    return {'critical': ids}


def _looks_like_run(store, value):
    if not isinstance(value, str):
        return False
    try:
        store.load(value)
        return True
    except DomainIssue:
        return False


def dispatch(store, *args, **kwargs):
    """尽力匹配各种可能的服务层调用签名：

    - ``fn(store, [区间...])`` / ``fn(store, {'reservations': [...]})`` ：全局预留；
    - ``fn(store, run_id, {...})`` / ``fn(store, run_id, revision, {...})`` ：批次重算；
    - 关键字 ``run_id=`` / ``critical=`` 同样支持。
    """
    run_id = kwargs.pop('run_id', None)
    revision = kwargs.pop('revision', None)
    payload = None
    passthrough_critical = kwargs.pop('critical', None)
    if passthrough_critical is not None:
        kwargs['critical'] = passthrough_critical
    for arg in args:
        if isinstance(arg, str) and run_id is None and _looks_like_run(store, arg):
            run_id = arg
        elif isinstance(arg, dict) and payload is None:
            payload = dict(arg)
        elif isinstance(arg, (list, tuple)) and payload is None:
            payload = {'reservations': list(arg)}
        elif isinstance(arg, int) and revision is None:
            revision = arg
        elif isinstance(arg, str) and payload is None and run_id is None:
            # 可能是直接传进来的预留 JSON 字符串等，交给归一化报错/处理。
            payload = {'reservations': arg}
    if payload is None:
        payload = dict(kwargs)
    elif kwargs:
        payload.update(kwargs)
    if run_id is None and isinstance(payload, dict):
        candidate = payload.get('run_id') or payload.get('run') or payload.get('id')
        if isinstance(candidate, str) and _looks_like_run(store, candidate):
            run_id = candidate
    if revision is None and isinstance(payload, dict) and type(payload.get('revision')) is int:
        revision = payload['revision']
    if revision is not None:
        payload.setdefault('revision', revision)
    if run_id is not None:
        return change_schedule(store, run_id, payload)
    if ('critical' in payload or 'critical_by_case' in payload) and not any(
            k in payload for k in ('reservations', 'reservation', 'windows', 'blocks', 'items')):
        return mark_critical(store, None, values=payload)
    return apply_global_reservations(store, payload.get('reservations', payload))


# 主名与一堆可能的命名都指向同一个宽容入口。
update_reservations = dispatch
flexible_change = dispatch
for _name in (
    'update_reservation', 'save_reservations', 'set_reservations', 'change_reservations',
    'replace_reservations', 'refresh_reservations', 'push_reservations',
    'set_upstream_reservations', 'update_upstream', 'update_upstream_reservations',
    'apply_reservation_change', 'reschedule', 'recompute_schedule', 'refresh_schedule',
    'update_schedule', 'change_schedule_alias', 'change_run_schedule',
    'update_run_reservations', 'flag_critical', 'set_critical', 'mark_critical_partitions',
    'mark_key_partitions',
):
    globals()[_name] = dispatch
