from app.business import DomainIssue, by_kind, prepared, single


def prepare(rows):
    from graphlib import TopologicalSorter, CycleError
    plan = single(rows,'plan')
    horizon = plan['deadline']
    source = by_kind(rows,'task')
    tasks = {r['task']:r for r in source}
    if not 0 < horizon <= 720 or len(tasks) != len(source) or not tasks:
        raise DomainIssue('INVALID_PLAN')
    if any(not 0 < r['slots'] <= horizon or not r['release'] < r['deadline'] <= horizon for r in source):
        raise DomainIssue('INVALID_TASK_WINDOW')
    needs = {task:set() for task in tasks}
    for row in by_kind(rows,'edge'):
        if row['task'] not in tasks or row['prerequisite'] not in tasks:
            raise DomainIssue('UNKNOWN_TASK')
        needs[row['task']].add(row['prerequisite'])
    schedule = TopologicalSorter(needs)
    try:
        schedule.prepare()
    except CycleError as error:
        raise DomainIssue('DEPENDENCY_CYCLE') from error
    reserved = by_kind(rows,'reservation')
    if any(not r['release'] < r['deadline'] <= horizon for r in reserved):
        raise DomainIssue('INVALID_RESERVATION')
    busy = {t for r in reserved for t in range(r['release'],r['deadline'])}
    return prepared(len(tasks),{'reserved_slots':len(busy),'horizon':horizon},(tasks,needs,schedule,busy,plan['slots']))
