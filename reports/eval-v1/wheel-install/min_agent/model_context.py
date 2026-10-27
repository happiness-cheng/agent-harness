"""模型声明的容量与应用输出配置分离；真实Provider不能猜测默认窗口。"""
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ModelWindowExceeded(ValueError):
    pass


class ModelContextLimits(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra='forbid')
    model: str = Field(min_length=1)
    context_window: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    source: str = Field(min_length=1)

    @model_validator(mode='after')
    def output_within_context(self):
        if self.max_output_tokens > self.context_window:
            raise ValueError('模型输出上限超过上下文容量')
        return self


def output_reserve(model):
    # 4096是无Provider测试/演示的输出配置，不是实际模型上下文容量。
    settings = getattr(model, '_settings', None)
    value = getattr(settings, 'max_output_tokens', 4096)
    if value is None:
        return 0  # 自适应输出已由Provider按实际请求余量预留，不能再固定扣模型最大输出。
    if type(value) is not int or value <= 0:
        raise ValueError('模型输出预留无效')
    return value


def context_window(model, override=None):
    if override is not None and (type(override) is not int or override <= 0):
        raise ValueError('显式上下文窗口无效')
    discover = getattr(model, 'context_limits', None)
    if callable(discover):
        limits = ModelContextLimits.model_validate(discover())
        if output_reserve(model) > limits.max_output_tokens:
            raise ValueError('配置的生成输出上限超过当前模型能力')
        if override is not None and override > limits.context_window:
            raise ValueError('显式上下文限制不能扩大模型容量')
        return override if override is not None else limits.context_window
    if getattr(model, '_provider', None) is not None and override is None:
        raise ValueError('当前Provider未提供上下文能力，请配置已核实的窗口，不能猜测32K')
    # 无远端Provider的Mock/Demo没有模型物理容量；沿用明确的本地试验窗口。
    return override if override is not None else 32768
