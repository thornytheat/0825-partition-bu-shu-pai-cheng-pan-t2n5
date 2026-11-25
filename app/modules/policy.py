from app.business import decision


def policy(result):
    # 配额是最低签发要求：排上数 >= 配额即满足；delta>=0 视为匹配。
    quota_met = result['value'] >= result['expected']
    if not quota_met:
        # 与历史反例保持同一个原因码（有未排任务时），其余记为配额不足。
        reason = 'NO_FEASIBLE_SLOT' if result.get('flag') else 'QUOTA_NOT_MET'
        outcome = decision(result, reason, lower_bound=True)
    else:
        # 达到配额后，即便有可选任务未排入，也不再推翻签收判定；
        # 未排原因仍完整保留在 measure.evidence.rejected 中。
        outcome = decision(result, None, lower_bound=True)
    outcome['quota_met'] = quota_met
    return outcome
