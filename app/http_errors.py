from flask import jsonify

LABELS = {
    'FILE_TOO_LARGE': '文件超过 256 KiB', 'FILE_EMPTY': '文件没有内容', 'FILE_BLANK': '文件只有空白',
    'HEADER_ONLY': '文件只有标题行', 'HEADER_MISMATCH': '列名与字段字典不一致', 'ENCODING_INVALID': '请使用 UTF-8 CSV',
    'CSV_MALFORMED': 'CSV 引号或行格式异常', 'ROW_LIMIT': '文件超过 2000 行', 'FILE_REQUIRED': '请选择 CSV 文件',
    'RUN_NOT_FOUND': '批次不存在', 'REVIEW_FIELDS': '请填写 3 至 200 字复核意见', 'STALE_REVIEW': '批次已更新，请刷新',
    'RUN_FINALIZED': '批次已签发', 'UNRESOLVED_ISSUES': '存在待处理差异或未达签发配额', 'SAMPLE_UNKNOWN': '样例不存在',
    'STAGE_UNKNOWN': '处理阶段不存在',
    'QUOTA_NOT_MET': '排上的分区数未达到签发配额',
    'STALE_SCHEDULE': '上游预留已更新，旧排程失效，请按新预留重算',
    'INVALID_RESERVATION': '预留区间无效',
}

CONFLICT_CODES = {
    'STALE_REVIEW', 'RUN_FINALIZED', 'UNRESOLVED_ISSUES', 'STALE_SCHEDULE',
    'QUOTA_NOT_MET', 'VERSION_CONFLICT', 'RESERVATION_CHANGED',
}
NOT_FOUND_CODES = {'RUN_NOT_FOUND', 'SAMPLE_UNKNOWN', 'STAGE_UNKNOWN'}


def response(error):
    code = error.code
    if code in NOT_FOUND_CODES:
        status = 404
    elif code in CONFLICT_CODES:
        status = 409
    elif code == 'FILE_TOO_LARGE':
        status = 413
    else:
        status = 422
    payload = {'code': code, 'message': LABELS.get(code, code)}
    aliases = getattr(error, 'aliases', (code,))
    if len(aliases) > 1:
        # 兼容不同命名约定：把候选原因码一并返回，不影响以 code 为准的客户端。
        payload['codes'] = list(aliases)
        payload['reason'] = aliases[1] if len(aliases) > 1 else code
    return jsonify(payload), status
