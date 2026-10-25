"""来源级代理指标与有界归因；未审真实回答不生成语义成绩。"""
from hashlib import sha256
import re

from min_agent.long_memory.evaluation import normalized

BOOK_DIMENSIONS=('precision','recall','reasoning','proactivity')
EXTRA_DIMENSIONS=('fact_accuracy','entity_scenario','temporal_state','current_conflict','unsupported_memory','evidence_use')


def stage(*,history_exists,saved,candidate,topk,visible,reviewed_failure=False,conflict=False,evaluation_issue=False):
    if evaluation_issue or not history_exists:return 'EVALUATION'
    if not saved:return 'STORAGE'
    if visible:
        if reviewed_failure:return 'CONFLICT' if conflict else 'REASONING'
        return 'UNDETERMINED_WITHOUT_REVIEW'
    if not candidate:return 'RETRIEVAL'
    if not topk:return 'RANKING'
    if not visible:return 'PACKING'
    return 'UNDETERMINED_WITHOUT_REVIEW'


def _contains(text,fact):
    variants=fact.get('variants',[]) if isinstance(fact,dict) else fact if isinstance(fact,list) else [fact]
    return any(normalized(v) in normalized(text) for v in variants)


def answer_metrics(case,group,response=None):
    request=case['groups'][group]
    if response is None or response.get('status')!='ANSWERED':
        return dict(status='NOT_RUN' if response is None else response['status'],
            book_scores={k:'NOT_RUN' for k in BOOK_DIMENSIONS},
            dimensions={k:'NOT_RUN' for k in EXTRA_DIMENSIONS},fact_surface_recall='NOT_RUN',
            answer=None if response is None else response.get('raw_answer'),
            verified_failure_categories=[],bottleneck='NOT_DETERMINED')
    parsed=response['parsed'];text=parsed['answer'];ids=parsed['used_evidence_ids']
    facts=case['gold']['required_facts'];visible=set(request['visible_ids'])
    unsupported=sorted(set(ids)-visible)
    matches=[_contains(text,f) for f in facts]
    return dict(status='ANSWERED_UNREVIEWED',answer=text,book_scores={k:'NOT_RUN' for k in BOOK_DIMENSIONS},
        dimensions={k:'NOT_RUN' for k in EXTRA_DIMENSIONS},fact_surface_matches=matches,
        fact_surface_recall=sum(matches)/len(matches) if matches else None,
        cited_ids=ids,visible_cited_ids=sorted(set(ids)&visible),unsupported_or_invisible_citations=unsupported,
        evidence_use_note='self reported citations, not proof of internal causal use',
        verified_failure_categories=[],bottleneck='NOT_DETERMINED_WITHOUT_SEMANTIC_REVIEW')


def validate_review(case,group,response,review):
    if response.get('status')!='ANSWERED':raise ValueError('不能给未取得或不完整回答填语义成绩')
    if review.get('answer_sha256')!=response['answer_sha256']:raise ValueError('审查不对应当前真实回答')
    if not review.get('reviewer') or review.get('verified') is not True:raise ValueError('需显式审查人/代理和确认')
    for key in BOOK_DIMENSIONS:
        value=review.get('book_scores',{}).get(key)
        if type(value) is not int or value not in {1,2,3,4}:raise ValueError('原书四项评分必须是1～4')
    for key in EXTRA_DIMENSIONS:
        if key not in review.get('dimensions',{}):raise ValueError('缺失额外维度')
    if not review.get('answer_excerpt') or review['answer_excerpt'] not in response['parsed']['answer']:
        raise ValueError('需可定位的真实回答片段')
    legal={m['id']:m for m in case['source_episodes']}
    for source in review.get('source_evidence',[]):
        if source['memory_id'] not in legal or source['quote'] not in legal[source['memory_id']]['content']:
            raise ValueError('审查不能使用不存在或未来来源')
    allowed={'STORAGE','RETRIEVAL','RANKING','PACKING','REASONING','CONFLICT','EVALUATION'}
    if not set(review.get('failure_categories',[]))<=allowed:raise ValueError('未知失败类别')
    if {'REASONING','CONFLICT'}&set(review.get('failure_categories',[])) and review.get('adequate_visible_evidence') is not True:
        raise ValueError('不能在证据不足时断言模型使用失败')
    if not review.get('rationale'):raise ValueError('评分需要理由')
    return review
