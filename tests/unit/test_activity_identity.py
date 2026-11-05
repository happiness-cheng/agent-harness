"""M1 固定合成向量；不读取任何平台数据。"""

import hashlib

import pytest

from min_agent.activity_identity import canonical, derive, object_identity


def test_jcs_subset_has_exact_bytes_and_utf16_sorting():
    assert canonical({"z": [None, True, 23], "a": "\n\"中"}) == (
        '{"a":"\\n\\\"中","z":[null,true,23]}'.encode()
    )
    assert canonical({"\ue000": 1, "😀": 2}) == '{"😀":2,"\ue000":1}'.encode()


def test_id_full_hash_and_domain_separation():
    expected = b'{"domain_tag":"adrian/uem/1/member","id_schema_version":"uem-id/1","x":"A"}'
    assert derive("member", {"x": "A"}) == "id1_" + hashlib.sha256(expected).hexdigest()
    assert derive("member", {"x": "A"}) != derive("stream", {"x": "A"})
    assert derive("member", {"b": 2, "a": 1}) == derive("member", {"a": 1, "b": 2})


def test_opaque_object_only_normalizes_nfc():
    args = ("synthetic", "thread", "{AbC-é}", "history")
    first = object_identity(*args)
    assert first == object_identity("synthetic", "thread", "{AbC-e\u0301}", "history")
    assert first != object_identity("synthetic", "thread", "{abc-é}", "history")
    assert first != object_identity("other", *args[1:])


@pytest.mark.parametrize("value", [1.0, float("nan"), 2**53, "\ud800", {1: "x"}])
def test_rejects_unsupported_canonical_values(value):
    with pytest.raises(ValueError):
        canonical(value)


def test_caller_cannot_override_id_domain():
    with pytest.raises(ValueError):
        derive("member", {"domain_tag": "other"})
