from app.business import DomainIssue
from app.intake.service import inspect
from app.modules.calculate import compute
from app.modules.grouping import case_groups
from app.modules.policy import policy
from app.modules.prepare import prepare
from app.modules.reconcile import balance


def _as_options(options):
    if options is None:
        return {}
    if isinstance(options, dict):
        return dict(options)
    # 允许把关键编号列表直接作为第三个参数传入。
    return {'critical': list(options)}


def evaluate(rows, stage, options=None):
    options = _as_options(options)
    case_id = rows[0]['case']
    # 支持 {case: [ids]} 分组级列表，也兼容 {task_id: bool} 标记映射。
    critical = options.get('critical')
    if isinstance(critical, dict) and critical and all(isinstance(v, (list, tuple, set)) for v in critical.values()):
        options['critical'] = list(critical.get(case_id, []))
    by_case = options.pop('critical_by_case', None)
    if isinstance(by_case, dict):
        merged = list(options.get('critical') or []) + list(by_case.get(case_id, []) or [])
        options['critical'] = merged
    case = {'case': case_id, 'records': [r['record'] for r in rows], 'status': 'READY'}
    try:
        case['prep'], payload = prepare(rows, options)
        if stage >= 3:
            case['measure'] = compute(payload, options)
            for key in ('critical_scheduled', 'critical_total', 'normal_scheduled',
                        'quota', 'quota_met', 'scheduled'):
                if key in case['measure']:
                    case[key] = case['measure'][key]
        if stage >= 4:
            case.update(policy(case['measure']))
            case['quota'] = case['measure'].get('quota')
            case['quota_met'] = case['measure'].get('quota_met')
    except DomainIssue as error:
        case['status'] = error.code
        case['code'] = error.code
    return case


def run(data, stage=5, options=None, **extra):
    options = _as_options(options)
    # 允许 run(data, 5, critical=[...]) / run(data, reservations=[...]) 等直接写法。
    for key in ('critical', 'critical_by_case', 'reservations', 'quota', 'reservation_mode'):
        if key in extra and extra[key] is not None:
            options[key] = extra[key]
    intake = inspect(data)
    output = {'intake': {k: v for k, v in intake.items() if k != 'rows'}}
    if stage == 1:
        return output
    cases = [evaluate(rows, stage, options) for _, rows in case_groups(intake['rows'])]
    output['cases'] = cases
    if stage >= 5:
        output['balance'] = balance(intake, cases)
    return output
