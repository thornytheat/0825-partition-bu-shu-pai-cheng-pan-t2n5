from collections import Counter


def balance(intake, cases):
    accounted = intake['accepted'] + intake['rejected'] + intake['blank'] + intake['repeated']
    status = Counter(case['status'] for case in cases)
    ready = status.pop('READY', 0)
    observed = sum(len(case['records']) for case in cases)

    scheduled = sum((case.get('measure') or {}).get('value', 0) for case in cases)
    quota = sum((case.get('measure') or {}).get('expected', 0) for case in cases)

    def satisfied(case):
        # 新规则：显式 quota_met 或可由 measure 推算时，按配额判定。
        if case.get('quota_met') is not None:
            return bool(case['quota_met'])
        measure = case.get('measure')
        if measure and 'value' in measure and 'expected' in measure:
            return measure['value'] >= measure['expected']
        # 向后兼容：没有排程信息的合成用例沿用 READY 状态判定。
        return case.get('status') == 'READY'

    quota_met = bool(cases) and all(satisfied(case) for case in cases)

    balanced = (accounted == intake['input']
                and observed == intake['accepted'])
    clean = not intake['rejected']
    return dict(input=intake['input'], accepted=intake['accepted'], isolated=intake['rejected'],
                ignored=intake['blank'] + intake['repeated'], accounted=accounted,
                balanced=balanced,
                ready=ready, pending=len(cases) - ready, issues=dict(sorted(status.items())),
                scheduled=scheduled, quota=quota, quota_met=quota_met,
                # 签发条件：有业务组、各组达到配额（或 READY）、来源无隔离行且行数守恒。
                releasable=quota_met and clean and balanced)
