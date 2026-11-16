"""语义摘要通过证据索引回指原文；改写内容不冒充逐字引文。"""

import json
from hashlib import sha256


def save_evidence(units, artifacts, *, default_source=None):
    records = [{**unit, "source": unit.get("source") or default_source} for unit in units]
    content = json.dumps({"version": 1, "units": records}, ensure_ascii=False)
    identity = artifacts.save(content, kind="compression_evidence")
    reference = {"artifact_id": identity, "sha256": sha256(content.encode()).hexdigest()}
    # 创建时也检查真实出处，不能只给任意模型文字套一层引用。
    read_evidence(reference, [unit["unit_id"] for unit in records], artifacts)
    return reference


def read_evidence(reference, source_ids, artifacts):
    index = artifacts.get(reference["artifact_id"])
    if reference.get("sha256") != sha256(index["content"].encode()).hexdigest():
        raise ValueError("摘要证据索引版本不匹配")
    record = json.loads(index["content"])
    if record.get("version") != 1 or not isinstance(record.get("units"), list):
        raise ValueError("摘要证据索引格式无效")
    units = {unit["unit_id"]: unit for unit in record["units"]}
    if (len(units) != len(record["units"]) or not source_ids or len(set(source_ids)) != len(source_ids)
            or any(type(identity) is not int or identity not in units for identity in source_ids)):
        raise ValueError("摘要证据来源ID缺失、重复或不存在")
    selected = []
    for identity in source_ids:
        unit = units[identity]
        source = unit.get("source")
        if not isinstance(source, dict):
            raise ValueError("摘要证据缺少原始材料引用")
        original = artifacts.get(source["artifact_id"])
        start, end = unit.get("start"), unit.get("end")
        if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(original["content"])
                or source.get("sha256") != sha256(original["content"].encode()).hexdigest()
                or original["content"][start:end] != unit.get("quote")):
            raise ValueError("摘要证据原文、哈希或字符范围不匹配")
        selected.append(unit)
    return selected
