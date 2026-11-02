"""人工语义审查单独留痕；不覆盖原成绩，不允许抹掉硬安全FAIL。"""
from pathlib import Path
from min_agent.evaluation.contracts import EvaluationResult,digest
from min_agent.evaluation.runner import save_json
from min_agent.evaluation.verifier import verdict


def record_review(result_path,out,*,check_id,decision,reviewer,reason):
    if decision not in {'PASS','FAIL','UNKNOWN'} or not reviewer.strip() or not reason.strip():raise ValueError('审查信息不完整')
    original=EvaluationResult.model_validate_json(Path(result_path).read_text(encoding='utf-8'))
    target=next((c for c in original.checks if c.id==check_id),None)
    if target is None or target.layer!='semantic' or target.verdict!='UNKNOWN':raise PermissionError('只能明确复核待判断的语义项')
    out=Path(out)
    if out.exists():raise FileExistsError('不能覆盖历史人工审查')
    annotation=dict(original_result_sha256=digest(original),check_id=check_id,decision=decision,
        reviewer=reviewer,reason=reason,judgment_type='HUMAN_NONDETERMINISTIC',
        original_verdict=original.verdict,reviewed_verdict=verdict(decision if c.id==check_id else c.verdict for c in original.checks),
        hard_safety_preserved=all(c.verdict!='FAIL' or c.id!=check_id for c in original.checks if c.hard_safety),auto_merge=False)
    save_json(out,annotation);return annotation
