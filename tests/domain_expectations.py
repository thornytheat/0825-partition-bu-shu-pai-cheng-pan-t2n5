EXPECTED = {'0001': (3, 3, 'READY'),
 '0002': (2, 2, 'READY'),
 '0003': (0, None, 'DEPENDENCY_CYCLE'),
 '0004': (2, 0, 'NO_FEASIBLE_SLOT')}
EVIDENCE = {'0001': {'schedule': {'1': [2, 4], '2': [4, 6], '3': [0, 1]}, 'rejected': {}},
 '0004': {'schedule': {},
          'rejected': {'1': 'NO_FEASIBLE_SLOT', '2': 'UPSTREAM_UNSCHEDULED'}}}
