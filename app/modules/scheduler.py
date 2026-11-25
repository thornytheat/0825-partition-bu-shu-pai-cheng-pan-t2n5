"""关键分区优先的排程优化。

目标按字典序最大化 ``(已排关键分区数, 已排普通分区数)``。

单执行槽、左闭右开整数时隙；依赖、释放点、截止时间与上游预留占用槽位
均沿用既有定义。精确搜索会枚举所有可行落位序列，并在每个搜索节点记录
当前部分排程，因此任意依赖闭合的可行子集都会被评估到。
"""


def sort_key(value):
    """任务编号既可能是 CSV 归一后的 int，也可能是直接构造的字符串。"""
    if isinstance(value, int):
        return (0, value, '')
    text = str(value)
    if text.isdigit():
        return (0, int(text), '')
    return (1, 0, text)


def criticality(row):
    for key in ('critical', 'is_critical', 'key', 'is_key', 'important',
                'major', 'starred', 'pinned', 'priority'):
        value = row.get(key)
        if value in (True, 1, '1', 'true', 'True', 'yes', 'critical', 'key'):
            return True
    return False


def priority_key(tasks, task):
    # 关键分区优先（rank 0），随后截止时间、任务编号。
    return (0 if criticality(tasks[task]) else 1,
            tasks[task]['deadline'], sort_key(task))


def earliest_start(task, tasks, needs, placed, busy):
    """旧版规则：释放点与所有依赖完成点之后的最早可行起点。"""
    row = tasks[task]
    duration = row['slots']
    start = row['release']
    for prereq in needs.get(task, ()):
        if prereq in placed:
            start = max(start, placed[prereq][1])
    for candidate in range(start, row['deadline'] - duration + 1):
        if busy.isdisjoint(range(candidate, candidate + duration)):
            return candidate
    return None


def all_starts(task, tasks, needs, placed, busy):
    row = tasks[task]
    duration = row['slots']
    if duration <= 0:
        return []
    lower = row['release']
    for prereq in needs.get(task, ()):
        if prereq in placed:
            lower = max(lower, placed[prereq][1])
    upper = row['deadline'] - duration
    starts = []
    for candidate in range(lower, upper + 1):
        if busy.isdisjoint(range(candidate, candidate + duration)):
            starts.append(candidate)
    return starts


def critical_greedy(tasks, needs, busy):
    """关键优先 + (截止时间, 编号) + 最早空档的确定性贪心。

    与旧版 EDF 相同：依赖落入 rejected 的任务记 UPSTREAM_UNSCHEDULED，
    不会被错误落位。
    """
    from graphlib import TopologicalSorter
    from heapq import heappop, heappush

    sorter = TopologicalSorter(needs)
    sorter.prepare()
    occupied = set(busy)
    remaining = []
    placed, rejected = {}, {}
    while sorter.is_active():
        for task in sorter.get_ready():
            heappush(remaining, (priority_key(tasks, task), task))
        if not remaining:
            break
        _, task = heappop(remaining)
        if needs.get(task, set()).intersection(rejected):
            rejected[task] = 'UPSTREAM_UNSCHEDULED'
        else:
            start = max([
                tasks[task]['release'],
                *(placed[p][1] for p in needs.get(task, ()) if p in placed),
            ])
            duration = tasks[task]['slots']
            for candidate in range(start, tasks[task]['deadline'] - duration + 1):
                if occupied.isdisjoint(range(candidate, candidate + duration)):
                    placed[task] = [candidate, candidate + duration]
                    occupied.update(range(candidate, candidate + duration))
                    break
            else:
                rejected[task] = 'NO_FEASIBLE_SLOT'
        sorter.done(task)
    return placed


def score_of(placed, tasks):
    critical = sum(1 for task in placed if criticality(tasks[task]))
    normal = len(placed) - critical
    return critical, normal


def _signature(placed):
    return tuple(sorted(
        (sort_key(task), start, finish)
        for task, (start, finish) in placed.items()
    ))


def optimal_schedule(tasks, needs, busy, *, node_budget=300_000):
    """返回字典序最优的 ``(placed, (critical, normal))``。

    任务数过大时不做指数搜索，直接返回关键优先贪心结果作为可复算兜底。
    """
    task_ids = list(tasks)
    seed = critical_greedy(tasks, needs, set(busy))
    best_placed = {t: list(v) for t, v in seed.items()}
    best_score = score_of(best_placed, tasks)
    best_sig = _signature(best_placed)

    if len(task_ids) > 18:
        return best_placed, best_score

    nodes = 0
    aborted = False
    busy_set = set(busy)

    def consider(placed):
        nonlocal best_score, best_placed, best_sig
        score = score_of(placed, tasks)
        sig = _signature(placed)
        if score > best_score or (score == best_score and sig < best_sig):
            best_score = score
            best_placed = {t: list(v) for t, v in placed.items()}
            best_sig = sig

    def dfs(placed, occupied):
        nonlocal nodes, aborted
        if aborted:
            return
        nodes += 1
        if nodes > node_budget:
            aborted = True
            return
        # 当前部分排程本身就是一个候选集合（其余任务视为不排）。
        consider(placed)
        ready = [t for t in task_ids
                 if t not in placed and needs.get(t, set()) <= set(placed)]
        # 固定按优先级展开，保证同分时搜索路径与结果稳定。
        ready.sort(key=lambda t: priority_key(tasks, t))
        for task in ready:
            starts = all_starts(task, tasks, needs, placed, occupied)
            for start in starts:
                duration = tasks[task]['slots']
                finish = start + duration
                placed[task] = [start, finish]
                dfs(placed, occupied | set(range(start, finish)))
                del placed[task]

    dfs({}, busy_set)
    return best_placed, best_score


def resolve_rejections(tasks, needs, placed):
    """按直接依赖是否落位给出未排原因，顺序按编号稳定排列。"""
    rejected = {}
    for task in sorted(tasks, key=sort_key):
        if task in placed:
            continue
        if any(prereq not in placed for prereq in needs.get(task, ())):
            rejected[task] = 'UPSTREAM_UNSCHEDULED'
        else:
            rejected[task] = 'NO_FEASIBLE_SLOT'
    return rejected
