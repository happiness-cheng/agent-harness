"""M1 的 JCS 子集与确定性身份；不依赖平台历史或 Runtime。"""

import hashlib
import json
import unicodedata


def canonical(value) -> bytes:
    """仅接受无浮点、无孤立 surrogate 的 I-JSON 子集。"""
    def encode(item):
        if item is None or type(item) is bool:
            return json.dumps(item)
        if type(item) is int and abs(item) <= 2**53 - 1:
            return str(item)
        if type(item) is str:
            item.encode("utf-8", errors="strict")
            return json.dumps(item, ensure_ascii=False)
        if type(item) is list:
            return "[" + ",".join(encode(x) for x in item) + "]"
        if type(item) is dict and all(type(k) is str for k in item):
            keys = sorted(item, key=lambda k: k.encode("utf-16-be", errors="strict"))
            return "{" + ",".join(encode(k) + ":" + encode(item[k]) for k in keys) + "}"
        raise ValueError("unsupported_canonical_value")

    try:
        return encode(value).encode("utf-8")
    except UnicodeError as exc:
        raise ValueError("invalid_unicode") from exc


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def derive(domain: str, fields: dict) -> str:
    if not domain or not isinstance(fields, dict) or {"domain_tag", "id_schema_version"} & fields.keys():
        raise ValueError("invalid_identity_fields")
    return "id1_" + digest({
        "id_schema_version": "uem-id/1",
        "domain_tag": "adrian/uem/1/" + domain,
        **fields,
    })


def object_identity(platform: str, object_namespace: str, logical_id: str, object_role: str) -> dict:
    fields = dict(platform=platform, object_namespace=object_namespace,
                  logical_id=unicodedata.normalize("NFC", logical_id), object_role=object_role)
    if not all(type(v) is str and v for v in fields.values()):
        raise ValueError("empty_object_identity")
    return {"id": derive("object", fields), "fields": fields}
