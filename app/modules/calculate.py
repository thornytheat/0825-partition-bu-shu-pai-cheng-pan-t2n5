from app.business import measured


def compute(data):
    from heapq import heappop, heappush
    tasks, needs, dag, reserved, expected = data
    busy = set(reserved)
    remaining, placed, rejected = [], {}, {}
    while dag.is_active():
        for task in dag.get_ready():
            heappush(remaining,(tasks[task]['deadline'],task))
        _, task = heappop(remaining)
        row = tasks[task]
        if needs[task].intersection(rejected):
            rejected[task] = 'UPSTREAM_UNSCHEDULED'
        else:
            start = max([row['release'],*(placed[p][1] for p in needs[task])])
            duration = row['slots']
            for candidate in range(start,row['deadline']-duration+1):
                block = range(candidate,candidate+duration)
                if busy.isdisjoint(block):
                    busy.update(block)
                    placed[task] = [candidate,candidate+duration]
                    break
            else:
                rejected[task] = 'NO_FEASIBLE_SLOT'
        dag.done(task)
    return measured(len(placed),expected,'任务',{'schedule':{str(k):v for k,v in placed.items()},'rejected':{str(k):v for k,v in rejected.items()}},bool(rejected))
