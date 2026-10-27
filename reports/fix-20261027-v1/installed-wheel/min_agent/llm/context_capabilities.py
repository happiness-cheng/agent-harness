"""仅显式声明已验证的提供商能力；未知能力使用本地投影/压缩。"""


def context_capabilities(provider):
    return {"provider": provider, "remote_context_clear": False, "strategy": "local_projection_and_compaction",
            "reason": "当前适配器未验证可选远程清理接口，保留本地原始记录"}
