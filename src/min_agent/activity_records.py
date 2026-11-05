"""冻结 Locator Profile 的合成输入适配；不读取平台文件。"""
from min_agent.activity_identity import canonical, derive, digest


PROFILES = {
    "codex/rollout": ("rollout", {"codex.ordinal": "ordinal"}, True),
    "dsh/v4": ("v4", {"dsh.seq": "seq"}, True),
    "dsh/old": ("old", {"dsh.seq": "seq", "dsh.seq0": "seq0"}, False),
}


def profile_spec(profile, namespace):
    if profile not in PROFILES or namespace not in PROFILES[profile][1]:
        raise ValueError("unsupported_locator_profile_or_namespace")
    return PROFILES[profile]


def fingerprint(structure, payload):
    parts = dict(struct_hash=digest(structure), payload_hash=digest(payload))
    return {**parts, "record_hash": digest(parts)}


def make_record(envelope, *, source_member_id, source_generation_id):
    """位点为解压内容坐标；未知必需字段降级，格式错误则拒绝。"""
    canonical(envelope)
    version, aliases, offset_required = profile_spec(envelope["profile"], envelope["seq_namespace"])
    namespace, locator = envelope["seq_namespace"], envelope["locator"]
    if type(locator) is not dict or not all(type(x) is str and x for x in (source_member_id, source_generation_id)):
        raise ValueError("invalid_locator_context")
    alias = aliases[namespace]
    seq = locator.get(alias, locator.get("seq_value"))
    if alias in locator and "seq_value" in locator and locator[alias] != locator["seq_value"]:
        raise ValueError("contradictory_sequence")
    offset = locator.get("byte_offset_in_decompressed_stream", locator.get("byte_offset") if not offset_required else None)
    if "byte_offset" in locator and "byte_offset_in_decompressed_stream" in locator and locator["byte_offset"] != offset:
        raise ValueError("contradictory_offset")
    line = locator.get("line_index")
    for value in (seq, line, offset):
        if value is not None and (type(value) is not int or not 0 <= value <= 2**53 - 1):
            raise ValueError("invalid_locator_number")
    stream = derive("stream", dict(source_member_id=source_member_id, seq_namespace=namespace))
    context = dict(source_member_id=source_member_id, source_stream_id=stream, source_generation_id=source_generation_id)
    fields = dict(**context, format_version=version, seq_namespace=namespace, seq_value=seq,
                  line_index=line, byte_offset_in_decompressed_stream=offset)
    provisional = seq is None or line is None or (offset_required and offset is None)
    native, provisional_id = None, None
    if provisional:
        run, ordinal = envelope.get("observation_run_id"), envelope.get("observation_ordinal")
        if type(run) is not str or not run or type(ordinal) is not int or not 0 <= ordinal <= 2**53 - 1:
            raise ValueError("missing_provisional_observation_key")
        provisional_id = derive("provisional_record", dict(**context, observation_run_id=run, observation_ordinal=ordinal))
    else:
        native = derive("record_locator", fields)
    return dict(id=provisional_id or native, **context, locator_fields=fields,
                record_locator_key=native, native_record_id=native, provisional_record_id=provisional_id,
                canonical_event_id=derive("canonical_event", {"native_record_id": native}) if native else None,
                identity_provisional=provisional, locator_confidence="UNRELIABLE" if provisional else "HIGH",
                dedup_confidence="LOW" if provisional else "HIGH",
                record_fingerprint=fingerprint(envelope["structure"], envelope["payload"]), envelope=envelope)
