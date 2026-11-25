from app.business import DomainIssue, by_kind, prepared, single

# 关键分区的标记种类。既允许直接用该 kind 定义一个关键任务，也允许用它
# 作为指向既有 task 编号的标记行（两种写法都被接受）。
CRITICAL_KINDS = ('critical', 'key', 'major', 'important', 'core', 'vital',
                  'priority', 'pinned', '关键', '关键分区', '重点')
# 仅作为“标记既有任务”的行种类。
MARKER_KINDS = ('mark', 'star')
TASK_LIKE_KINDS = ('task',) + CRITICAL_KINDS

# 当来源只能复用 note 列时，这些标记也会把任务识别为关键分区。
NOTE_TAGS = ('关键', 'critical', '[key]', '【关键】', '重要')


def _note_marks_critical(row):
    note = str(row.get('note', '') or '').lower()
    return any(tag.lower() in note for tag in NOTE_TAGS)


def _mark_critical(tasks, task_id):
    if task_id in tasks:
        tasks[task_id]['critical'] = True
        return True
    return False


def prepare(rows, options=None):
    from graphlib import TopologicalSorter, CycleError
    options = options or {}
    plan = single(rows, 'plan')
    horizon = plan['deadline']
    canonical = by_kind(rows, 'task')
    tasks = {r['task']: dict(r, critical=_note_marks_critical(r)) for r in canonical}

    # 关键分区：critical/key 行若编号已存在则视为标记行，否则本身就是关键任务。
    extra_sources = []
    for kind in CRITICAL_KINDS:
        for row in by_kind(rows, kind):
            task_id = row['task']
            if task_id in tasks:
                tasks[task_id]['critical'] = True
                continue
            if any(r['task'] == task_id for r in extra_sources):
                raise DomainIssue('INVALID_PLAN')
            extra_sources.append(row)
            tasks[task_id] = dict(row, critical=True)

    # 纯标记行（mark/star）只能指向既有任务。
    for kind in MARKER_KINDS:
        for row in by_kind(rows, kind):
            if not _mark_critical(tasks, row['task']):
                raise DomainIssue('UNKNOWN_TASK')

    # API/服务层显式指定的关键分区编号（列表，或 {编号: 是否关键} 映射）。
    explicit = options.get('critical', ()) or ()
    if isinstance(explicit, dict):
        explicit = [task_id for task_id, flag in explicit.items() if flag]
    for task_id in explicit:
        # 允许字符串编号与 int 编号互相匹配。
        if task_id in tasks:
            tasks[task_id]['critical'] = True
        else:
            matched = next((tid for tid in tasks if str(tid) == str(task_id)), None)
            if matched is not None:
                tasks[matched]['critical'] = True

    source = canonical + extra_sources
    if not 0 < horizon <= 720 or len(tasks) != len(source) or not tasks:
        raise DomainIssue('INVALID_PLAN')
    if any(not 0 < r['slots'] <= horizon or not r['release'] < r['deadline'] <= horizon for r in source):
        raise DomainIssue('INVALID_TASK_WINDOW')

    needs = {task: set() for task in tasks}
    for row in by_kind(rows, 'edge'):
        if row['task'] not in tasks or row['prerequisite'] not in tasks:
            raise DomainIssue('UNKNOWN_TASK')
        needs[row['task']].add(row['prerequisite'])
    schedule = TopologicalSorter(needs)
    try:
        schedule.prepare()
    except CycleError as error:
        raise DomainIssue('DEPENDENCY_CYCLE') from error

    reserved = by_kind(rows, 'reservation')
    if any(not r['release'] < r['deadline'] <= horizon for r in reserved):
        raise DomainIssue('INVALID_RESERVATION')
    busy = {t for r in reserved for t in range(r['release'], r['deadline'])}

    # 服务层可下发新的预留区间（上游更新），默认替换 CSV 内预留；也可合并。
    override = options.get('reservations')
    if override is not None:
        if options.get('reservation_mode', 'replace') == 'replace':
            busy = set()
        for item in override:
            if isinstance(item, (tuple, list)) and len(item) == 2:
                left, right = item
            elif isinstance(item, dict):
                left = item.get('release', item.get('start', item.get('left')))
                right = item.get('deadline', item.get('end', item.get('right')))
            else:
                raise DomainIssue('INVALID_RESERVATION')
            try:
                left, right = int(left), int(right)
            except (TypeError, ValueError):
                raise DomainIssue('INVALID_RESERVATION')
            if not 0 <= left < right:
                raise DomainIssue('INVALID_RESERVATION')
            # 超出计划视界的部分与时隙编排无关，截断以避免构造巨型集合。
            if left < horizon:
                busy.update(range(left, min(right, horizon)))

    quota = options.get('quota')
    expected = plan['slots'] if quota is None else int(quota)
    return prepared(len(tasks), {
        'reserved_slots': len(busy), 'horizon': horizon,
        'critical': sum(1 for t in tasks.values() if t.get('critical')),
        'quota': expected,
    }, (tasks, needs, schedule, busy, expected))
