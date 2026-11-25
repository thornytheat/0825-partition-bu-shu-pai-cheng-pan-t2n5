from app.business import decision


def policy(result):
    return decision(result,'NO_FEASIBLE_SLOT' if result['flag'] else None)
