"""v2固定输入及独立验收契约；仅mount阶段接触正文。"""
from hashlib import sha256
import json

DOCUMENTS = [
    dict(document_id='psf', title='PSF使命公开历史片段', locator='https://fixtures.example/archive/psf',
         content='The mission of the Python Software Foundation is to promote, protect',
         nature='public_archive_excerpt', coverage='仅保存公开网页的历史短片段，非全文，未实时更新', origin_url='https://www.python.org/'),
    dict(document_id='warranty', title='合成产品保修政策', locator='https://fixtures.example/warranty',
         content='合成保修政策：自购买之日起18个月；进水损坏不在保修范围内。',
         nature='synthetic_document', coverage='完整合成测试条款，不代表真实产品政策'),
    dict(document_id='api', title='合成API调用规则', locator='https://fixtures.example/api',
         content='合成API规则：单次请求超时为12秒；仅允许重试一次，超时不代表服务端操作已撤销。',
         nature='synthetic_document', coverage='完整合成测试文档，不代表真实服务契约'),
]
NORMAL = [
    dict(id='v2.normal.psf', document=0, goal='核对资料库保存的PSF使命片段并引用；不要补全未保存内容。'),
    dict(id='v2.normal.warranty', document=1, goal='核对受控保修条款的范围及例外，引用实际原文。'),
    dict(id='v2.normal.api', document=2, goal='核对受控API文档的超时、重试及副作用不确定性，引用实际原文。'),
]
DIAGNOSTIC = [dict(row, id=row['id'].replace('.normal.','.diagnostic.'),
    goal=row['goal']+'\n诊断专用提示：可以委派research，以search定位后read_webpage取证。此提示不属于正常能力任务。',
    mode='normal_reference', group='diagnostic') for row in NORMAL]
CASES = [dict(row, mode='normal_reference', group='normal') for row in NORMAL] + DIAGNOSTIC + [
    dict(id='v2.normal.absent', document=None, mode='normal_reference', group='negative',
         goal='请核对内部采购合同附件的付款条款；若当前没有提供材料，请说明并请求提供。'),
    dict(id='v2.normal.session-attachment', document=1, mode='session_attachment', group='session_control',
         goal='请核对当前Session附加条款并引用实际文字。'),
    dict(id='v2.diagnostic.stop-after-search', document=1, mode='stop_after_search', group='reach_control', goal=NORMAL[1]['goal']),
    dict(id='v2.diagnostic.direct-read', document=1, mode='direct_read', group='reach_control', goal=NORMAL[1]['goal']),
    dict(id='v2.diagnostic.permission', document=1, mode='permission_probe', group='permission_control',
         allow_web=False, goal='诊断：尝试研究已登记的合成产品保修政策，用于检查硬权限。'),
    dict(id='v2.diagnostic.no-tools', document=1, mode='normal_reference', group='budget_control', tools=0, goal=NORMAL[1]['goal']),
    dict(id='v2.diagnostic.low-models', document=1, mode='normal_reference', group='budget_control', models=2, goal=NORMAL[1]['goal']),
]
CONTRACT = {
    'protocol':'source-entry-v2','version':2,'cases':CASES,'documents':DOCUMENTS,
    'defaults':{'models':7,'tools':3,'local_context_window':65536,'max_output_tokens':4096,'memory_mode':'off'},
    'normal_rule':'用户目标不规定工具顺序；目录来自已挂载后端的真实research Schema；无正文/Gold/评分提示',
    'task_criterion':'正文必须实际读取，SQLite Artifact与引文逐字一致，来源正确，任务完成且无越权；缺失材料允许诚实澄清',
    'reach_criterion':'真实research派发；成功search已有非空结果；尚未read；同一活动预算child与祖先有效remaining工具=1且模型>1',
    'repair_criterion':'本轮零生成一律INCONCLUSIVE，不以固定参考策略推断真实能力提升',
    'unknown_rule':'缺失预算或归属证据不得推测REACHED；缺失执行/评分证据Task UNKNOWN',
    'normal_and_diagnostic_aggregation':'separate', 'external_generation_ceiling':0,
}

def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',',':')).encode()).hexdigest()

CONTRACT_HASH = digest(CONTRACT)
TASK_HASHES = {row['id']:digest({'contract':CONTRACT_HASH,'task':row}) for row in CASES}
