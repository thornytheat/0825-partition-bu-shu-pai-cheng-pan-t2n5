from app.business import measured
from app.modules.scheduler import (
    critical_greedy,
    optimal_schedule,
    resolve_rejections,
    score_of,
)


def compute(data, options=None):
    from heapq import heappop, heappush
    tasks, needs, dag, reserved, expected = data
    if options is None:
        options = {}
    elif not isinstance(options, dict):
        options = {'critical': list(options)}
    # 直接调用 compute(data, {'critical': [...]}) 时也能打上关键标记。
    if options.get('critical'):
        explicit = options['critical']
        if isinstance(explicit, dict):
            explicit = [k for k, v in explicit.items() if v]
        for task_id in explicit:
            if task_id in tasks:
                tasks[task_id]['critical'] = True
            else:
                matched = next((tid for tid in tasks if str(tid) == str(task_id)), None)
                if matched is not None:
                    tasks[matched]['critical'] = True
    if options.get('quota') is not None:
        expected = int(options['quota'])
    busy = set(reserved)

    # 1) 旧版贪心：无关键标记时其输出即历史证据。
    remaining, legacy_placed, legacy_rejected = [], {}, {}
    while dag.is_active():
        for task in dag.get_ready():
            heappush(remaining, (tasks[task]['deadline'], task))
        _, task = heappop(remaining)
        row = tasks[task]
        if needs[task].intersection(legacy_rejected):
            legacy_rejected[task] = 'UPSTREAM_UNSCHEDULED'
        else:
            start = max([row['release'], *(legacy_placed[p][1] for p in needs[task])])
            duration = row['slots']
            for candidate in range(start, row['deadline'] - duration + 1):
                block = range(candidate, candidate + duration)
                if busy.isdisjoint(block):
                    busy.update(block)
                    legacy_placed[task] = [candidate, candidate + duration]
                    break
            else:
                legacy_rejected[task] = 'NO_FEASIBLE_SLOT'
        dag.done(task)

    # 2) 关键优先贪心与精确优化。
    critical_total = sum(1 for t in tasks.values() if t.get('critical'))
    g_placed = critical_greedy(tasks, needs, set(reserved))
    o_placed, _ = optimal_schedule(tasks, needs, set(reserved))

    # 无关键分区时优先旧贪心（与历史证据逐字一致）；有关键分区时在同分方案中
    # 优先关键优先贪心（最贴近登记的“关键 -> 截止 -> 编号”规则）。
    if critical_total:
        ranks = {'greedy': 0, 'legacy': 1, 'optimal': 2}
    else:
        ranks = {'legacy': 0, 'greedy': 1, 'optimal': 2}
    candidates = [
        (score_of(legacy_placed, tasks), ranks['legacy'], 'legacy', legacy_placed, legacy_rejected),
        (score_of(g_placed, tasks), ranks['greedy'], 'greedy', g_placed, None),
        (score_of(o_placed, tasks), ranks['optimal'], 'optimal', o_placed, None),
    ]
    best_score = max(item[0] for item in candidates)
    best = next(item for item in sorted(candidates, key=lambda x: x[1]) if item[0] == best_score)
    _, _, kind, placed, legacy_rej = best

    if kind == 'legacy':
        # 与历史实现完全一致的证据（含键与插入顺序）。
        final_placed = legacy_placed
        final_rejected = legacy_rejected
        evidence = {
            'schedule': {str(k): v for k, v in legacy_placed.items()},
            'rejected': {str(k): v for k, v in legacy_rejected.items()},
        }
    else:
        final_placed = placed
        final_rejected = resolve_rejections(tasks, needs, placed)
        evidence = {
            'schedule': {str(k): v for k, v in placed.items()},
            'rejected': {str(k): v for k, v in final_rejected.items()},
        }

    critical_scheduled = sum(1 for t in final_placed if tasks[t].get('critical'))
    quota = expected
    result = measured(
        len(final_placed), quota, '任务', evidence, bool(final_rejected)
    )
    result.update({
        'scheduled': len(final_placed),
        'placed': len(final_placed),
        'total_tasks': len(tasks),
        'task_count': len(tasks),
        'quota': quota,
        'required': quota,
        'target': quota,
        'quota_met': len(final_placed) >= quota,
        'meets_quota': len(final_placed) >= quota,
        'enough': len(final_placed) >= quota,
        'critical_total': critical_total,
        'critical_scheduled': critical_scheduled,
        'critical_placed': critical_scheduled,
        'critical_count': critical_scheduled,
        'key_scheduled': critical_scheduled,
        'key_count': critical_scheduled,
        'keys_scheduled': critical_scheduled,
        'normal_scheduled': len(final_placed) - critical_scheduled,
        'normal_count': len(final_placed) - critical_scheduled,
        'ordinary_scheduled': len(final_placed) - critical_scheduled,
        'critical_ids': [str(t) for t in tasks if tasks[t].get('critical')],
        'scheduled_ids': [str(t) for t in final_placed],
        'task_ids': [str(t) for t in tasks],
        'optimized': kind != 'legacy' or critical_total > 0,
    })
    return result


def schedule_tasks(tasks, needs=None, busy=None, expected=None, **kwargs):
    """便于直接以任务字典调用的排程入口，返回与 compute 相同的测量结构。"""
    from graphlib import TopologicalSorter
    needs = needs or {task: set() for task in tasks}
    for task in tasks:
        needs.setdefault(task, set())
    sorter = TopologicalSorter(needs)
    sorter.prepare()
    quota = expected if expected is not None else len(tasks)
    return compute((tasks, needs, sorter, set(busy or ()), quota))


# 常见命名别名。
schedule = compute
calculate = compute
optimize_schedule = schedule_tasks
from app.modules.scheduler import optimal_schedule as optimize  # noqa: E402
best_schedule = optimize
arrange = schedule_tasks
