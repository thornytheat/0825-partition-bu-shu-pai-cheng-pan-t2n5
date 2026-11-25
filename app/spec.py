COLUMNS = ('task', 'prerequisite', 'slots', 'release', 'deadline')
KINDS = ('task', 'edge', 'reservation', 'plan')
PROJECT = {'name': '分区补数排程盘', 'actor': '数仓调度值班员', 'stages': [('intake', '样例采信'), ('prepare', '依赖校核'), ('calculate', '时隙编排'), ('policy', '期限核验'), ('balance', '运行对账')], 'chart': '执行时隙', 'mark': '分'}
