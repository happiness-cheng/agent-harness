# 架构题推演记录

来源：https://chatgpt.com/share/6ad80e3f-39ac-83e8-af3d-ca4a0829111c

原分享标题：讲解核心代码。按分享的当前分支父链恢复消息顺序，过滤不可见系统内容、工具内部信息及推理；不是仅截取浏览器当前渲染的少量消息。

以下为可见用户与助手消息的脱敏文本；不包含系统/开发者指令、工具执行结果、内部推理、附件和图表。原始引用标记或交互式内容需要打开原聊天查看。

## 001 · 用户 · 2026-10-20T02:17:13.137000+00:00

讲解里面的代码，我需要重点理解核心代码，我没有什么python基础所以你的讲解必须简单易懂保证一个没有相关的知识的人也能理解

## 002 · 助手 · 2026-10-20T02:17:26.684311+00:00

我会先看压缩包里的项目结构，找出 Agent 的核心执行链路，再按实际代码讲解：从用户输入，到模型决定调用工具、执行工具，再到循环结束。Python 语法会结合代码从零解释。

## 003 · 助手 · 2026-10-20T02:18:01.933096+00:00

我已经读取了你上传的 `agent-harness.zip` 项目源码。

这个项目的核心是一个**简化版 Agent 运行框架（Agent Harness）**。它不是单纯调用一次大模型，而是让模型能够判断是否需要使用工具、执行工具、获取结果，并继续思考。

我会按照**完全没有 Python 基础，但有编程思维**的角度讲解。重点不是让你背代码，而是让你理解：

1. 这段代码在做什么？
2. 为什么要这么设计？
3. Python 的语法是什么意思？
4. 如果让你自己实现一个 Agent，你应该怎么思考？

## 一、先理解整个项目的架构

根据实际源码，主要有以下模块：

| 文件 | 作用 | 重要程度 |
|---|---|---|
| `runtime.py` | Agent 核心循环，决定何时调用模型和工具 | ★★★★★ |
| `models.py` | 定义消息、工具请求、模型响应的数据格式 | ★★★★★ |
| `tools/registry.py` | 工具注册、参数校验、执行 | ★★★★★ |
| `tools/calculator.py` | 安全实现四则运算 | ★★★★ |
| `sessions.py` | 管理会话和聊天历史 | ★★★ |
| `trace.py` | 记录 Agent 执行过程 | ★★★ |
| `tools/todo.py` | 待办事项管理 | ★★ |
| `clock.py` | 日期和时间辅助 | ★ |

注意：当前 `todo.py` 是业务服务，**尚未直接注册成模型可调用的工具**。另外，在这些文件中没有看到一个实际连接大模型 API 的具体适配器，`runtime.py` 定义的是模型需要遵守的接口。

### 1. Agent 是如何运行的？

<box border radius="xl" padding={3} gap={2}>
  <svg viewBox="0 0 340 385" width="100%" xmlns="http://www.w3.org/2000/svg">
    <rect x="95" y="8" width="150" height="41" rx="9" fill="#64748b" fillOpacity=".16" stroke="#94a3b8"/>
    <text x="170" y="33" textAnchor="middle" fontSize="14" fill="currentColor">用户提出问题</text>
    <path d="M170 49 V75" stroke="#94a3b8" strokeWidth="1.8"/>
    <path d="M165 68 L170 75 L175 68" fill="none" stroke="#94a3b8" strokeWidth="1.8"/>
    <rect x="95" y="76" width="150" height="44" rx="9" fill="#0d9488" fillOpacity=".13" stroke="#0d9488"/>
    <text x="170" y="95" textAnchor="middle" fontSize="13" fill="currentColor" fontWeight="600">Runtime 调用 LLM</text>
    <text x="170" y="111" textAnchor="middle" fontSize="11" fill="currentColor">传入消息和工具说明</text>
    <path d="M170 120 V144" stroke="#94a3b8" strokeWidth="1.8"/>
    <path d="M165 137 L170 144 L175 137" fill="none" stroke="#94a3b8" strokeWidth="1.8"/>
    <path d="M170 146 L258 194 L170 242 L82 194 Z" fill="#d97706" fillOpacity=".12" stroke="#d97706" strokeWidth="1.5"/>
    <text x="170" y="191" textAnchor="middle" fontSize="13" fontWeight="600" fill="currentColor">模型要求</text>
    <text x="170" y="207" textAnchor="middle" fontSize="13" fill="currentColor">调用工具？</text>
    <path d="M82 194 H37 V313 H100" stroke="#94a3b8" strokeWidth="1.7" fill="none"/>
    <text x="44" y="184" textAnchor="middle" fontSize="12" fill="#0d9488">否</text>
    <rect x="100" y="292" width="142" height="42" rx="9" stroke="#0d9488" fill="#0d9488" fillOpacity=".13"/>
    <text x="171" y="317" textAnchor="middle" fontSize="14" fill="currentColor">返回最终回答</text>
    <path d="M258 194 H297 V226" stroke="#94a3b8" strokeWidth="1.7" fill="none"/>
    <text x="284" y="183" textAnchor="middle" fontSize="12" fill="#d97706">是</text>
    <rect x="245" y="226" width="88" height="44" rx="8" stroke="#d97706" fill="#d97706" fillOpacity=".13"/>
    <text x="289" y="243" textAnchor="middle" fontSize="12" fill="currentColor">Registry</text>
    <text x="289" y="258" textAnchor="middle" fontSize="11" fill="currentColor">执行工具</text>
    <path d="M289 270 V363 H170 V121" stroke="#0d9488" strokeWidth="1.8" fill="none" strokeDasharray="5 4"/>
    <path d="M165 129 L170 121 L175 129" stroke="#0d9488" fill="none" strokeWidth="1.8"/>
    <text x="225" y="355" fontSize="11" fill="#0d9488" textAnchor="middle">附加结果，进入下一轮</text>
  </svg>
  <caption>简化后的主流程。省略了错误处理、状态记录、会话和日志细节。</caption>
</box>

举个例子，用户说：

> 帮我计算 `(1+2)*3`。

整个过程如下：

**第一轮：模型决策**

模型收到问题以及工具清单，决定使用 `calculator`，返回类似：

```json
{
  "finish_reason": "tool_calls",
  "tool_calls": [
    {
      "id": "call_001",
      "name": "calculator",
      "arguments": {
        "expression": "(1+2)*3"
      }
    }
  ]
}
```

模型只是**提出了工具调用请求**，并没有亲自执行计算。

**第二步：Python 执行工具**

Runtime 将这个请求交给 ToolRegistry，验证参数、找到 `calculator`，然后执行计算，得到 `"9"`。

**第三步：将结果交回模型**

把工具的返回结果添加到聊天消息中，再调用一次模型。

**第四步：模型返回答案**

模型返回 `finish_reason="stop"`，正文例如：

> 计算结果是 9。

于是 Runtime 结束循环。

你需要先记住一个非常重要的原则：

**LLM 负责决策，Runtime 负责控制流程，ToolRegistry 负责管理和执行工具。**

这三个角色的分离，是整个项目最重要的架构思想。

---

## 二、核心代码：Runtime 如何实现 Agent 循环？

位置：`src/min_agent/runtime.py`

### 1. 最重要的 Python 语法：`while True`

你在 C++ 中应该见过：

```cpp
while (true) {
    // 执行逻辑
}
```

Python 写法：

```python
while True:
    # 执行逻辑
```

两个意思完全一样：**不断重复执行，直到遇到 return、break 或异常等终止情况。**

Python 不用大括号 `{}`，而是通过缩进来确定代码属于哪个作用域。

下面是从你的实际 `_run()` 中抽取出来的核心逻辑，删去了日志和复杂的异常处理。

```python
while True:
    response = self._model.generate(
        messages,
        tools=self._tools.schemas()
    )

    match response.finish_reason:
        case "stop":
            return RunResult(
                status="completed",
                response=response
            )

        case "tool_calls":
            for call in response.tool_calls:
                outcome = self._tools.execute(call)

                messages.append(
                    Message(
                        role="tool",
                        content=json.dumps(outcome),
                        tool_call_id=call.id
                    )
                )
```

这是一个用于理解结构的简化版本，不是完整的可运行替代代码。实际源码还包含工具请求消息、参数安全处理、错误修复和状态追踪。

我们逐句看。

### 2. `response = self._model.generate(...)`

```python
response = self._model.generate(
    messages,
    tools=self._tools.schemas()
)
```

先理解三个东西：

- `self`：当前的 `Runtime` 对象。类似 C++ 的 `this`。
- `self._model`：保存在 Runtime 对象中的模型。
- `.generate(...)`：调用模型对象上的 `generate` 方法。

`messages` 是历史消息，`tools` 是模型可以使用的工具定义。

所以这句等价于：

**拿着当前聊天历史和可用工具说明，问大模型下一步应该做什么。**

这里 `generate()` 的返回值赋给了 `response`。

### 3. `match response.finish_reason`

```python
match response.finish_reason:
    case "stop":
        ...
    case "tool_calls":
        ...
```

这是 Python 的模式匹配语法，你可以先把它理解为 C++ 的 `switch`。

`response.finish_reason` 表示模型为什么结束这次生成。

| 值 | 意思 | Runtime 行为 |
|---|---|---|
| `stop` | 模型给出最终回答 | 结束循环 |
| `tool_calls` | 模型希望调用工具 | 执行工具，再循环 |
| `length` | 输出达到长度限制 | 返回部分结果 |
| `refusal` | 模型拒绝处理 | 返回失败 |
| `error` | 模型出错 | 返回失败 |

重点理解：**一次模型生成结束，不等于整个 Agent 任务结束。**

例如模型生成一个工具调用时，这次生成已经结束，但 Agent 还要执行工具和继续生成。

### 4. `for call in response.tool_calls`

```python
for call in response.tool_calls:
    outcome = self._tools.execute(call)
```

`for` 就是遍历。

比如模型一轮请求两个工具：

```python
tool_calls = ["calculator", "search"]
```

那么：

```python
for call in tool_calls:
    print(call)
```

依次输出：

```text
calculator
search
```

实际代码中，`call` 不是单纯的字符串，而是一个 `ToolCall` 对象，里面保存了工具名称、参数和调用 ID。

`execute(call)` 执行一次工具调用，`outcome` 保存执行结果。

### 5. 为什么要 `messages.append()`？

```python
messages.append(
    Message(
        role="tool",
        content=json.dumps(outcome),
        tool_call_id=call.id
    )
)
```

`append()` 就是往列表末尾添加元素。

可以把 Python 的 `list` 暂时理解成 C++ 的 `vector`：

```python
messages = ["用户：计算1+2"]

messages.append("工具：计算结果是3")
```

结果：

```python
[
    "用户：计算1+2",
    "工具：计算结果是3"
]
```

为什么要这样做？

因为**模型本身不知道 Python 刚刚执行了什么**。

工具是在你自己的程序中执行的。程序必须主动将结果放回消息历史，下次调用模型时，模型才能看见结果。

也就是：

<box background="surface-secondary" radius="lg" padding={3} gap={1}>
  <text weight="medium" size="sm">模型第一次收到</text>
  <text color="secondary" size="sm">用户：1+2 等于多少？</text>
  <divider spacing={1}/>
  <text weight="medium" size="sm">模型第二次收到</text>
  <text color="secondary" size="sm">用户：1+2 等于多少？</text>
  <text color="secondary" size="sm">助手：请求调用 calculator(1+2)</text>
  <text color="success" size="sm">工具：计算结果是 3</text>
</box>

因此 Agent 不是模型“自己记住”了工具结果，而是 Runtime 维护了不断增长的消息历史。

这也解释了为什么 Agent 通常需要一个 Loop：

**模型决策 → 程序执行 → 更新上下文 → 模型重新决策。**

---

## 三、模型和 Runtime 之间如何传递信息？

位置：`src/min_agent/models.py`

这是第二个必须理解的核心文件。

你可以把 `models.py` 看成 C++ 中定义的几个数据结构，例如 `struct`。

### 1. ToolCall：模型提出的工具请求

真实代码：

```python
class ToolCall(BaseModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        allow_inf_nan=False
    )

    id: str = Field(min_length=1, pattern=r"\S")
    name: str = Field(min_length=1, pattern=r"\S")
    arguments: dict[str, JsonValue]
```

其中第一句：

```python
class ToolCall(BaseModel):
```

意思是定义一个叫 `ToolCall` 的类，并继承 `BaseModel`。

`BaseModel` 来自 Pydantic，这是一个负责**数据类型与规则校验**的 Python 库。

你可以理解为：定义了一个带自动参数检查能力的 `struct`。

先忽略 `model_config`，只看三个字段：

```python
id: str
name: str
arguments: dict[str, JsonValue]
```

它们表示：

| 字段 | 作用 | 举例 |
|---|---|---|
| `id` | 本次工具请求的唯一标识 | `call_001` |
| `name` | 需要使用哪个工具 | `calculator` |
| `arguments` | 调用工具时的参数 | `{"expression": "1+2"}` |

特别解释：

```python
arguments: dict[str, JsonValue]
```

Python 的 `dict` 是字典，类似 C++ 的 `unordered_map`。

例如：

```python
person = {
    "name": "张三",
    "age": 20
}

print(person["age"])  # 20
```

`dict[str, JsonValue]` 的含义是：键是字符串，值可以是合法的 JSON 数据。

另外，`id: str` 这种写法叫作**类型注解**，不是在赋值。

Pydantic 会利用这些注解验证输入。

### 2. Message：为什么要定义消息角色？

源码：

```python
class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str

    tool_call_id: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
```

先解释：

```python
role: Literal["system", "user", "assistant", "tool"]
```

表示 `role` 只能是这四个字符串之一。

你可以将它理解成一个只允许四种取值的枚举。

这里最重要的是**消息角色决定消息的含义**。

- `user`：用户输入。
- `assistant`：模型的回答或者工具调用请求。
- `tool`：工具执行结果。
- `system`：系统级指令。

为什么需要角色？

因为模型必须分清楚：

> “计算结果是 9”到底是用户说的、模型自己回答的，还是计算器真实执行得到的？

如果不区分角色，就很容易把不同来源的信息混为一谈。

### 3. ModelResponse：模型每轮返回什么？

```python
class ModelResponse(BaseModel):
    finish_reason: Literal[
        "stop", "tool_calls", "length", "refusal", "error"
    ]

    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    reasoning_summary: str | None = None
```

重点看：

```python
content: str | None = None
```

`|` 在这里表示“或者”，即 `content` 可以是字符串，也可以是 `None`。

`None` 就类似 C++ 中的空值概念。

再看：

```python
tool_calls: list[ToolCall] = Field(default_factory=list)
```

含义：

- `list[ToolCall]`：保存多个工具调用请求的列表。
- `default_factory=list`：如果没有传值，就创建一个新的空列表。

为什么不是每次只有一个 `ToolCall`？

因为模型可能一次提出多个工具调用请求。

例如同时查询两个数据源。不过，你现在的 Runtime 会按顺序执行这些工具，并不是并发执行。

### 4. 为什么模型输出还要校验？

真实源码：

```python
response = ModelResponse.model_validate(
    response.model_dump(warnings="error")
)
```

可以先把它理解为两个步骤：

1. `model_dump()`：把模型响应对象转成普通数据。
2. `model_validate()`：再根据 `ModelResponse` 的规则检查，并创建新对象。

这是为了防止不合法的数据进入 Agent 核心执行逻辑，同时切断部分可变数据的引用关系。

**重要的工程思想：大模型返回的数据不能直接被无条件信任。**

---

## 四、工具注册表：为什么模型知道工具，却不能直接执行工具？

位置：`src/min_agent/tools/registry.py`

这是我认为项目里仅次于 `runtime.py` 的核心代码。

### 1. ToolDefinition：一个工具究竟由什么组成？

```python
@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: type[BaseModel]
    handler: Callable[..., object]
```

先解释 `@dataclass`。

这是 Python 的一个装饰器，可以让类自动获得用于初始化等操作的方法。

例如：

```python
@dataclass
class Student:
    name: str
    age: int
```

你可以直接创建：

```python
student = Student(name="小明", age=20)
```

不需要自己写 `__init__()`。

`frozen=True` 表示对象创建后不能直接重新赋值字段，但不意味着内部所有对象都完全不可变。

再看 `ToolDefinition` 四个字段：

| 字段 | 含义 |
|---|---|
| `name` | 工具的名字 |
| `description` | 工具的用途说明 |
| `parameters` | 参数格式和校验规则 |
| `handler` | 真正执行工具功能的 Python 函数 |

其中最难理解的是：

```python
handler: Callable[..., object]
```

`Callable` 表示“可以被调用的东西”，这里通常就是函数。

例如：

```python
def add(a, b):
    return a + b

handler = add

print(handler(1, 2))  # 3
```

注意：

```python
handler = add
```

只是把函数交给变量保存。

而：

```python
handler = add(1, 2)
```

会立即执行函数，并将返回值赋给变量。

**这个区别非常关键：工具注册时只保存函数，并不执行函数。**

这类似于 C++ 中保存函数指针或者 `std::function`。

### 2. ToolRegistry 如何保存所有工具？

真实代码：

```python
class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
```

其中：

```python
def __init__(self):
```

是 Python 类的初始化方法，类似 C++ 构造函数。

```python
self._tools = {}
```

创建一个空字典。

后续你可以把它理解为：

```python
{
    "calculator": Calculator工具定义,
    "search": Search工具定义
}
```

为什么使用字典？

因为可以通过工具名称快速找到对应的工具对象。

### 3. register：注册工具

关键代码：

```python
def register(self, tool: ToolDefinition) -> None:
    if tool.name in self._tools:
        raise ValueError(f"工具名称重复：{tool.name}")

    self._tools[tool.name] = tool
```

这是原函数的部分代码，实际还校验名称、描述、参数模型和处理函数。

语法：

```python
if tool.name in self._tools:
```

意思是判断这个名称是否已经存在于字典里。

```python
raise ValueError(...)
```

就是抛出异常，类似 C++ 的 `throw`。

最后：

```python
self._tools[tool.name] = tool
```

把工具保存到字典中。

注册之后，程序才有办法通过名称找到这个工具。

### 4. schemas：模型怎样知道自己能调用什么？

真实代码：

```python
def schemas(self) -> list[dict[str, object]]:
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters.model_json_schema(),
        }
        for tool in self._tools.values()
    ]
```

这段 Python 对初学者可能比较陌生。

其中：

```python
for tool in self._tools.values()
```

表示遍历已经注册的所有工具。

整个 `return [...]` 是 Python 的**列表推导式**。

把它展开，就相当于：

```python
def schemas(self):
    result = []

    for tool in self._tools.values():
        data = {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters.model_json_schema()
        }

        result.append(data)

    return result
```

这两种写法的效果相同。

重点在于：返回给模型的只有工具的：

- 名称
- 描述
- 参数 Schema

**没有把 `handler` 函数交给模型。**

这是一条非常重要的权限边界。

模型可以说“我想调用 calculator”，但实际调用必须由程序执行。

### 5. execute：真正执行工具的地方

现在看最关键的一句：

```python
output = tool.handler(**arguments.model_dump())
```

这是实际 `execute()` 中的核心执行语句。

拆开解释。

首先：

```python
arguments.model_dump()
```

假设返回：

```python
{
    "expression": "1+2"
}
```

那么：

```python
tool.handler(**arguments.model_dump())
```

相当于：

```python
tool.handler(expression="1+2")
```

这里的 `**` 叫作**字典解包**，它把字典里的键值对转成函数的具名参数。

例如：

```python
def greet(name, age):
    return f"我是{name}，今年{age}岁"

data = {"name": "小明", "age": 20}

print(greet(**data))
```

就相当于：

```python
print(greet(name="小明", age=20))
```

理解这个语法之后，再看真实执行流程：

<box border radius="lg" padding={3} gap={2}>
  <box background="surface-secondary" radius="md" padding={3} align="center">
    <text weight="medium" size="sm">模型请求：calculator(expression="1+2")</text>
  </box>
  <row justify="center">
    <icon name="arrow-down" color="secondary" size="lg"/>
  </row>
  <box border radius="md" padding={3} align="center">
    **通过名称找到 ToolDefinition**
    <text size="xs" color="secondary">self.get(call.name)</text>
  </box>
  <row justify="center">
    <icon name="arrow-down" color="secondary" size="lg"/>
  </row>
  <box border radius="md" padding={3} align="center">
    **验证参数是否合法**
    <text size="xs" color="secondary">tool.parameters.model_validate(call.arguments)</text>
  </box>
  <row justify="center">
    <icon name="arrow-down" color="secondary" size="lg"/>
  </row>
  <box background="rgba(13,148,136,0.08)" border={{size:1,color:"rgba(13,148,136,0.5)"}} radius="md" padding={3} align="center">
    **执行 handler(expression="1+2")**
    <text size="xs" color="secondary">这一步才真正调用 Python 函数</text>
  </box>
  <row justify="center">
    <icon name="arrow-down" color="secondary" size="lg"/>
  </row>
  <box background="surface-secondary" radius="md" padding={3} align="center">
    <text weight="medium" size="sm">{"返回：{\"ok\": true, \"result\": \"3\"}"}</text>
  </box>
</box>

对 Agent 来说，ToolRegistry 的价值不只是统一管理工具，而是建立了一个**模型请求 → 参数验证 → 可信代码执行 → 结构化反馈**的边界。

这也是为什么不应该让大模型生成任意 Python 代码，然后直接使用 `eval()` 执行。

---

## 五、Calculator：项目中最值得深入理解的工具实现

位置：`src/min_agent/tools/calculator.py`

这个工具特别适合用来理解 Python，因为它不仅包含递归和树，还展示了实际工程中如何处理不可信输入。

先看它解决的问题：

用户输入：

```text
(1+2)*3
```

程序需要计算出：

```text
9
```

看起来很简单，但是有一个问题：

**计算机收到的是字符串，不是数学表达式。**

例如：

```python
expression = "(1+2)*3"
```

在 Python 看来，它只是几个字符：

```text
'(' '1' '+' '2' ')' '*' '3'
```

程序怎么知道先算加法，再算乘法？

### 1. AST：把字符串转换成一棵树

源码：

```python
tree = ast.parse(source, mode="eval")
```

`ast` 是 Python 标准库，AST 的全称是 Abstract Syntax Tree，即抽象语法树。

它的作用是把代码或表达式解析成具有结构的树。

例如 `(1+2)*3`：

<box border radius="lg" padding={3} gap={2}>
  <svg viewBox="0 0 340 205" width="100%" xmlns="http://www.w3.org/2000/svg">
    <g stroke="#94a3b8" strokeWidth="1.8" fill="none">
      <path d="M170 42 L95 108 M170 42 L255 108 M95 108 L50 176 M95 108 L135 176"/>
    </g>
    <g fill="#64748b" fillOpacity=".16" stroke="#64748b">
      <rect x="128" y="5" width="84" height="38" rx="8"/>
      <rect x="53" y="91" width="84" height="38" rx="8"/>
      <rect x="220" y="91" width="70" height="38" rx="8"/>
      <rect x="21" y="159" width="58" height="38" rx="8"/>
      <rect x="106" y="159" width="58" height="38" rx="8"/>
    </g>
    <g fill="currentColor" textAnchor="middle" fontSize="14">
      <text x="170" y="29">乘法 *</text>
      <text x="95" y="115">加法 +</text>
      <text x="255" y="115">数字 3</text>
      <text x="50" y="183">1</text>
      <text x="135" y="183">2</text>
    </g>
  </svg>
  <caption>概念示意：AST 会保留运算优先级，因此根节点是乘法，左子树是加法。</caption>
</box>

有了这棵树，就可以递归计算：

1. 计算乘法的左子树。
2. 左子树是 `1+2`，得到 3。
3. 计算乘法的右子树，得到 3。
4. 两个结果相乘，得到 9。

### 2. 核心递归 `_evaluate()`

这是源码中最关键的函数：

```python
def _evaluate(node: ast.AST, source: str, context: Context) -> Decimal:
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        literal = ast.get_source_segment(source, node)
        if literal is None:
            raise ValueError("无法读取数字字面量")
        return context.create_decimal(literal)

    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATIONS:
        left = _evaluate(node.left, source, context)
        right = _evaluate(node.right, source, context)
        if isinstance(node.op, ast.Div) and right.is_zero():
            raise ValueError("除数不能为零")
        return _BINARY_OPERATIONS[type(node.op)](context, left, right)

    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATIONS:
        value = _evaluate(node.operand, source, context)
        return _UNARY_OPERATIONS[type(node.op)](context, value)

    raise ValueError("仅支持四则运算、括号和一元正负号")
```

不用一次性看懂，我们分成三个分支。

**第一种：当前节点是数字**

```python
if isinstance(node, ast.Constant):
    ...
```

`isinstance()` 是 Python 判断对象类型的函数。

类似 C++ 中判断一个对象属于什么类型。

例如当前节点是数字 `3`，那么就把它转换为十进制数字并返回。

**第二种：当前节点是二元运算**

```python
left = _evaluate(node.left, source, context)
right = _evaluate(node.right, source, context)
```

二元运算就是需要两个操作数的运算，例如：

```text
1 + 2
3 * 4
8 / 2
```

这两句的关键是：**函数调用了自己**，也就是递归。

如果当前是乘法节点，就会先递归求出左右两个子树的结果。

然后：

```python
return _BINARY_OPERATIONS[type(node.op)](context, left, right)
```

它在字典里根据运算类型找到对应函数并执行。

你的源码中：

```python
_BINARY_OPERATIONS = {
    ast.Add: Context.add,
    ast.Sub: Context.subtract,
    ast.Mult: Context.multiply,
    ast.Div: Context.divide,
}
```

本质上是建立映射：

```text
加号 → 加法函数
减号 → 减法函数
乘号 → 乘法函数
除号 → 除法函数
```

类似 C++ 中通过运算符枚举选择对应的处理函数。

**第三种：一元运算**

例如：

```text
-3
+5
-(1+2)
```

一元运算只需要一个操作数。

因此只递归求出一个子树，再执行正号或负号处理。

### 3. 为什么不用 `eval()`？

Python 有一个很方便的函数：

```python
eval("(1+2)*3")
```

可以直接得到 9。

那为什么项目要写这么多代码？

因为 `eval()` 可以执行的不只是数学运算，它还能求值其他 Python 表达式。

如果把模型提供的不可信字符串直接送入 `eval()`，就可能产生严重的安全问题。

你的实现采用了另一种思路：

- 只允许数学表达式需要的字符。
- 使用 AST 解析表达式。
- 只接受明确允许的数字与运算节点。
- 不执行任意 Python 代码。
- 限制表达式长度和 AST 节点数量。

这叫作**白名单设计**。

与其列出所有禁止的危险操作，不如明确规定“哪些操作可以执行”。

### 4. 为什么使用 Decimal 而不是 float？

看一个例子。

```python
print(0.1 + 0.2)
```

常见输出是：

```text
0.30000000000000004
```

这是因为二进制浮点数无法精确表示某些十进制小数。

你的项目使用：

```python
context = Context(
    prec=28,
    rounding=ROUND_HALF_EVEN,
    Emin=-100,
    Emax=100,
    traps=[DivisionByZero, InvalidOperation, Overflow, Underflow, Subnormal],
)
```

其中：

- `prec=28`：运算使用 28 位十进制有效数字精度。
- `ROUND_HALF_EVEN`：遇到正好在两种舍入结果中间的情况，向末位为偶数的一侧舍入。
- `Emin` / `Emax`：控制十进制运算的指数范围。
- `traps`：遇到指定的异常数值情况时抛出异常。

注意，28 位是**有效数字**，不是固定保留 28 位小数。

同时，指数上下限也不完全等同于最终十进制字符串的位数限制。

这里使用 Decimal 的核心目的，是让计算行为更确定，并明确控制精度和异常边界。

---

## 六、一个交互例子：检验你是否理解递归计算

{@body const [expr,setExpr] = DIL.useState("a")}
{@body const steps = {
 a:[["乘法节点 *","先求左子树，再求右子树"],["加法节点 +","递归获取数字 1 和 2"],["数字节点 1、2","返回 1 和 2"],["加法结果","1 + 2 = 3"],["数字节点 3","右子树返回 3"],["乘法结果","3 × 3 = 9"]],
 b:[["加法节点 +","计算左子树 10 ÷ 2"],["除法节点 /","获取 10 和 2，得到 5"],["右子树数字 3","返回 3"],["加法结果","5 + 3 = 8"]],
 c:[["乘法节点 *","求左边一元负号表达式"],["一元负号节点 -","求括号内的 2 + 3"],["加法节点 +","得到 5"],["负号运算","得到 -5"],["右子树数字 4","返回 4"],["乘法结果","-5 × 4 = -20"]]
}}
{@body const [step,setStep] = DIL.useState(0)}
<box border radius="xl" padding={3} gap={3}>
  <text weight="medium">选择一个表达式，逐步观察递归过程</text>
  <radio-group direction="col" value={expr} onChange={v=>{setExpr(v);setStep(0)}}>
    <radio value="a">`(1+2)*3`</radio>
    <radio value="b">`10/2+3`</radio>
    <radio value="c">`-(2+3)*4`</radio>
  </radio-group>
  <box background="surface-secondary" radius="lg" padding={3} gap={2}>
    <row align="center" justify="between">
      <text weight="medium">{steps[expr][step][0]}</text>
      <text size="xs" color="secondary" tabularNums>第 {step+1}/{steps[expr].length} 步</text>
    </row>
    {steps[expr][step][1]}
    <row gap={1}>
      {#each steps[expr] as s,i}
        <box key={i} height="5px" flex="1" radius="full" background={i<=step?"#0d9488":"border"}/>
      {/each}
    </row>
  </box>
  <row justify="between">
    <button color="secondary" variant="outline" disabled={step===0} onClick={()=>setStep(s=>s-1)}>上一步</button>
    <button onClick={()=>setStep(s=>s===steps[expr].length-1?0:s+1)}>{step===steps[expr].length-1?"重新演示":"下一步"} <icon name="arrow-right" inline/></button>
  </row>
</box>

---

## 七、这个 Agent 项目还有哪些重要工程设计？

现在把其他文件串起来。

### `sessions.py`：会话管理

你可以理解成：Runtime 负责执行一次任务，而 `SessionStore` 负责保存多轮对话历史。

例如：

```text
用户：我叫小明
助手：你好，小明

用户：我叫什么？
助手：你叫小明
```

如果没有保存第一轮历史，第二轮模型可能不知道用户叫什么。

你的代码还做了额外检查，例如每个会话归属于哪个用户、同一会话是否已有正在执行的任务、工具调用结果能否与之前的请求配对。

这些设计的目的，是**防止不同会话混乱和消息状态不一致**。

### `trace.py`：执行追踪

假设一次 Agent 任务失败了。

只知道“失败”是不够的，你还需要知道：

- 是调用模型时失败？
- 还是模型请求的工具不存在？
- 是参数不合法？
- 还是工具本身执行异常？

你的 Runtime 使用：

```python
with self.trace.span("model", ...) as model_span:
```

来记录模型调用。

这里的 `with` 可以理解为自动管理一段操作的开始和结束，和 C++ 的 RAII 思想有相似之处。

不过具体实现依赖 `TraceRecorder` 提供的上下文管理机制。

### `todo.py`：业务逻辑

这个文件负责维护待办事项，提供新增、查询、更新、完成和删除等操作。

需要注意的是，**业务服务不自动等于 Agent 工具**。

你有一个 `TodoService`，不代表模型就能直接调用它。

还需要提供合适的 ToolDefinition、参数 Schema，以及与业务服务绑定的 handler，才能让 Runtime 通过工具注册表调用相关功能。

---

## 八、审阅源码后，我认为最值得关注的三个问题

这部分不仅仅是学 Python，更涉及 Agent 架构设计。

### 1. `while True` 缺少完整的执行预算

当前 Runtime 的主要循环是：

```python
while True:
```

它设置了工具请求错误的最大修正轮数：

```python
max_tool_error_rounds = 2
```

但这和最大 Agent 执行轮数是两回事。

如果模型每次都合法地请求工具，而且工具一直成功，模型始终不返回 `stop`，那么当前循环没有一个独立的最大轮次限制。

生产环境中通常还需要考虑最大轮次、时间预算、Token 预算和重复调用检测。

### 2. `completed` 不等于用户任务成功

源码中：

```python
case "stop":
    return RunResult(status="completed", response=response)
```

这表示模型正常产生了最终回答。

但它无法证明：

- 代码是否真的修改成功？
- 测试是否全部通过？
- 用户要求的文件是否正确生成？
- 外部任务是否完成？

因此你之前关注的一个问题——Agent 为什么经常声称任务完成，但实际没有完成——在这里仍然存在。

**Runtime 负责流程结束，业务目标是否达成还需要单独的验证机制。**

例如写代码的 Agent 应该有独立的测试和验收逻辑，不能只依赖模型说“修改完成”。

### 3. 工具失败的处理有明确边界

当前 Registry 会把工具不存在、参数不合法等情况转换成模型可以阅读的错误信息，让模型有机会修改请求。

但是如果工具函数本身抛出了未处理异常，Runtime 会返回 `tool_execution_failed`，而不是一概重新执行。

这是有意义的设计，因为某些工具可能已经产生外部副作用。

例如转账接口执行过程中出现超时，不能简单假设没有转账成功，再盲目重试。

重试必须考虑幂等性与执行状态。

---

## 九、应该怎样学习这个项目？

我的建议是暂时不要追求理解所有文件，而是按照真实的执行链路学习。

<box gap={2}>
  <row align="start" gap={3}>
    <box background="surface-secondary" radius="lg" padding={2}><icon name="check-circle-2" color="success" size="lg"/></box>
    <box flex="1" gap={1}>
      **第一阶段：理解 Agent 最小循环**

      `runtime.py` → `models.py` → `registry.py`

      重点掌握 `while`、模型响应、工具调用、消息追加、循环结束。
    </box>
  </row>
  <divider color="subtle"/>
  <row align="start" gap={3}>
    <box background="surface-secondary" radius="lg" padding={2}><icon name="calculator" size="lg"/></box>
    <box flex="1" gap={1}>
      **第二阶段：理解一个工具是怎么实现的**

      `calculator.py`

      重点掌握 Python 函数、字典、递归、AST、参数校验和工具注册。
    </box>
  </row>
  <divider color="subtle"/>
  <row align="start" gap={3}>
    <box background="surface-secondary" radius="lg" padding={2}><icon name="database" size="lg"/></box>
    <box flex="1" gap={1}>
      **第三阶段：理解工程化**

      `sessions.py` → `trace.py` → `todo.py`

      重点理解状态、会话隔离、可观测性、业务逻辑与工具接口的分离。
    </box>
  </row>
</box>

最后给你一道检验理解程度的题目：

假设模型第一次返回：

```python
ModelResponse(
    finish_reason="tool_calls",
    tool_calls=[
        ToolCall(
            id="001",
            name="calculator",
            arguments={"expression": "1+2"}
        )
    ]
)
```

**你尝试回答下面的问题：**

1. `Runtime` 为什么不能直接把这个响应返回给用户？
2. 谁负责找到真正的 `calculate()` 函数？
3. 工具算出 3 以后，为什么必须再调用一次模型？

你可以先用自己的话回答，不需要 Python 语法完全正确。只要这三个问题能解释清楚，就说明你真正理解了 Agent 最核心的执行闭环。

## 004 · 用户 · 2026-10-20T03:41:30.099000+00:00

2026年Agent harness技术笔试题
 
 
恭喜你通过我们的简历评估！
笔试题分成两部分，vibe coding题目和架构设计题目。

注意事项：
1 vibe coding题目请注意实现细节。完成后，发送github链接即可，不要给API
2 架构设计题一共5个模块，一个模块选一道题目回答
3 架构设计题，字数多不代表思考深，用大模型来帮助你思考，而不是用大模型来帮你完成任务。人和人的差距就在于如何使用AI来帮助自己完成工作。
4 Vibe coding 题目，手速快的候选人，两小时能做到70分（满分100）。觉得很难，谨慎往下写，超过70分才会进入下一轮面试。（agent源码看的足够多，会发现这道题很简单）
5 省心购和光辰是同一家公司，一个在广州，一个在厦门，沟通一个即可
6 一周内完成即可。

CTO知乎专栏，在大模型领域有一定的影响力
https://www.zhihu.com/column/c_1059389329360703488
想要深入学习大模型知识，欢迎关注和私信他

Vibe coding题目：从零实现一个最小可用 Agent
要求1：从零完成
  不能依赖现有agent框架（langgraph/openhands/openclaw）完成主流程，
  允许使用任何 AI 工具辅助开发，但核心 Agent Runtime 需要自行实现。

要求2：实现基本循环
Loop大致步骤
  Step one 接收用户输入
  Step two 判断是直接回复，还是调用工具
  Step three 调用工具
  Step four 根据工具结果判断是继续loop，还是返回结果给用户
工具相关
    至少实现三个工具（todo必须实现）
      calculator
      search（可 mock）
      read_docs / todo / weather（可自定义）
  
  需实现工具注册机制（每个工具包含名称、描述、参数 Schema），LLM 基于 Schema 自主决策调用。需实现 LLM 输出的解析逻辑，提取思考过程、工具调用或最终答案。
session管理
  用户 A 开了窗口 1：让 Agent 查天气记待办
  用户 A 开了窗口 2：让 Agent 写周报记待办
  这两个窗口应该是独立的session，用户A可以随时接着窗口1/2和继续聊，彼此不会影响。
context的有效管理
  最大轮次限制
  用户持续的对话，要能记住之前的状态。
  能支持追问
    纯对话追问
    带着工具的追问
  要如何实现？哪些信息要塞入context更合适？
    用户输入、工具执行结果、Agent 思考过程等，自行判断。
  context过长要有基础的压缩，复杂的压缩不用在这里实现。
额外要求
  基本异常处理
  工具调用trace或执行日志

要求3: 测试用例构建
  构建测试用例，来测试以上功能

提交内容：
  需要使用真实的LLM Api
  代码链接（github即可）
  README（运行方式、系统设计、memory 的召回时机与放置方式说明）
  AI Prompt 与问题解决记录
  

架构设计题：
模块一：Context / Performance
1.大模型面对第一轮长窗口或多模态输入时，first token 会显著变慢。有什么快速/低成本/用户体验也不差的方案？从5-10秒稳定压缩到2秒。
2.一个 session 连续聊了 200 轮，context 快爆了。你会怎么做压缩？如何确保压缩后的对话仍然流畅？
模块二：Memory
  1.和聊天 Agent 熟悉半个月后，用户问了一个以前问过的问题。Agent 如何做 memory 召回更合理？
  2.你理解的 Agent memory 经典框架是什么？它的发展趋势是什么，最头部的玩家在怎么做？
模块三：Task 
1.对于长程任务，大模型执行一段时间可能会忘掉目标，你知道哪些解决方案，有什么优缺？
2.用户给 Agent 下达任务：每天早上 9 点根据昨天聊天情况做复盘总结。你会怎么设计？
模块四：Tool / Session Runtime
1.Agent 工具有同步和异步两类。异步工具不能让用户一直等，但结果依然重要。你会如何设计异步工具执行和完成通知？
2.如果 session state 为 busy，此时用户又发来新消息，或者异步工具完成事件也到达，runtime 应该如何处理？
模块五：Agent Runtime 架构对比
1.Claude Code 的工具输出方式和国内 GLM / 豆包等 OpenAI-compatible function calling 有什么不同？他们各自这样设计的优缺点是什么？
2.Openhands的状态机设计有什么优缺？更优雅的实现方式是怎么样的？

别的技术方向
另附上我们四个技术方向的笔试题，有兴趣也可以找我来挑战，我们内部是不限制方向的。
后端：2026年后端(Agent方向)开发笔试题
嵌入式：2026嵌入式开发笔试题
agent：2026年Agent harness技术笔试题（最小Agent的实现要求会很高）
agent科研岗：2026年Agent科研岗技术笔试题 （最难）




这个项目有没有完成这个要求？现在项目还没有全部完成

## 005 · 助手 · 2026-10-20T03:41:43.266911+00:00

我会按笔试要求逐项核对压缩包中的实际代码和测试，区分“已实现”“部分实现”“尚未实现”，重点判断哪些缺口会影响 70 分门槛。

## 006 · 助手 · 2026-10-20T03:42:51.654283+00:00

核对后发现两个关键缺口：`todo.py` 目前是独立业务服务，模型还不能通过工具注册表调用；项目中的模型实现目前只有按预设响应工作的测试替身，没有真实 API 适配器。我也尝试运行测试，但当前环境缺少项目所需依赖，无法独立复现文档记录的 445 项通过。

## 007 · 助手 · 2026-10-20T03:42:10.418389+00:00

# Agent Harness 笔试项目完成度评估

**结论：目前还没有完成笔试要求，但核心 Runtime 已经有了不错的基础。**

我核对了你上传的 `agent-harness.zip`，包括 `runtime.py`、`models.py`、`sessions.py`、`registry.py`、`calculator.py`、`todo.py` 以及相关测试和开发进度文档。

目前最大的问题不是代码写得不够复杂，而是：

**你已经实现了 Agent 的一些核心组件，但还没有将它们组成一个可以通过真实 LLM API 完整运行、可持续对话的 Agent。**

从笔试的角度看，这是比增加高级功能更紧迫的问题。

## 一、对照笔试要求的完成情况

| 笔试要求 | 当前情况 | 判断 |
|---|---|---|
| 自行实现 Agent Runtime | 已实现同步模型与工具循环 | <icon name="check-circle" color="success" inline/> 已完成基础 |
| 用户输入→模型决策→工具→继续/结束 | Runtime 已具备 | <icon name="check-circle" color="success" inline/> 已完成基础 |
| 工具注册、描述和参数 Schema | ToolRegistry 已具备 | <icon name="check-circle" color="success" inline/> 已完成 |
| calculator 工具 | 已实现，含 AST 和 Decimal | <icon name="check-circle" color="success" inline/> 已完成 |
| search 工具（可 mock） | 未发现实现 | <icon name="x-circle" color="danger" inline/> 未完成 |
| todo 工具 | 业务服务已实现，尚未接入 Registry | <icon name="alert-circle" color="warning" inline/> 部分完成 |
| 真实 LLM API | 只有 Model 接口与 FakeModel 测试 | <icon name="x-circle" color="danger" inline/> 未完成 |
| LLM 输出解析 | 内部 ModelResponse 校验已有，API 格式转换未实现 | <icon name="alert-circle" color="warning" inline/> 部分完成 |
| Session 隔离与持续对话 | 内存会话管理已实现 | <icon name="check-circle" color="success" inline/> 基础完成 |
| 历史状态持续保存 | 进程内保存，重启后丢失 | <icon name="alert-circle" color="warning" inline/> 部分完成 |
| 最大 Agent 循环轮数 | 尚无独立最大轮次限制 | <icon name="x-circle" color="danger" inline/> 未完成 |
| Context 压缩 | 未发现 | <icon name="x-circle" color="danger" inline/> 未完成 |
| 基础异常处理 | 已有模型、工具、参数等异常处理 | <icon name="check-circle" color="success" inline/> 基础完成 |
| Tool Trace | 有 TraceRecorder | <icon name="check-circle" color="success" inline/> 基础完成 |
| 测试用例 | 已有较多单元测试 | <icon name="alert-circle" color="warning" inline/> 缺真实模型端到端测试 |
| README / Prompt / 问题解决记录 | 有过程文档，未发现根目录 README | <icon name="alert-circle" color="warning" inline/> 部分完成 |

这里的“已完成”主要表示源码具备相应功能，不代表所有线上运行场景都已经验证。

## 二、目前的完成度大概是多少？

<box gap={2}>
  <row align="end" justify="between">
    <box gap="1px">
      <text color="secondary" weight="medium" size="xs">我的粗略工程完成度估计</text>
      <title size="2xl" color="default" tabularNums>50%–60%</title>
    </box>
    <badge color="warning">尚不建议提交</badge>
  </row>
  <box background="surface-tertiary" height="12px" radius="full" clip>
    <box background="#d97706" width="55%" height="100%" radius="full"/>
  </box>
  <caption>这不是公司的评分，也不能推断目前能拿到多少分。缺少真实 LLM 和端到端闭环会明显影响实际笔试表现。</caption>
</box>

不过项目有一个优势：它并不是只有几段演示代码。

你已经有工具参数校验、重复调用 ID 检测、会话归属检查、Trace、工具执行错误处理，以及较完整的 Todo 业务层。这些可以成为面试时解释工程设计的材料。

接下来应该优先补齐最小可用闭环，而不是继续过度打磨单个工具。

## 三、最需要先解决的 5 个问题

按影响程度排序：

<box gap={3}>
  <row align="start" gap={2}>
    <badge color="danger">P0</badge>
    <box flex="1" gap={1}>
      **1. 接入真实 LLM API**

      当前 `runtime.py` 只规定了 `generate()` 接口，测试使用的 `ScriptedModel` 是按预设脚本返回结果，不会真正理解用户输入。

      需要编写模型适配器，将真实 API 响应转换为 `ModelResponse`，包括文本、工具调用、参数和结束原因。

      同时要处理不同 API 对工具调用消息格式的要求。`reasoning_summary` 可以保留为可选字段，但不应依赖获取模型内部思考过程。
    </box>
  </row>
  <divider color="subtle"/>
  <row align="start" gap={2}>
    <badge color="danger">P0</badge>
    <box flex="1" gap={1}>
      **2. 真正让模型调用至少三个工具**

      现在已经有 Calculator，以及独立的 TodoService。最省力的办法是：

      - 保留现有 Calculator。
      - 实现一个可预测结果的 Mock Search。
      - 为 TodoService 编写 Agent 可调用的工具包装函数。

      然后通过 `ToolRegistry` 注册这些工具，让模型根据 Schema 自主选择。

      Todo 建议至少暴露新增、列出、完成等操作，才能验证用户的待办管理需求。
    </box>
  </row>
  <divider color="subtle"/>
  <row align="start" gap={2}>
    <badge color="danger">P0</badge>
    <box flex="1" gap={1}>
      **3. 增加最大循环轮次**

      目前 `runtime.py` 的循环是：

      ```python
      while True:
      ```

      现有 `max_tool_error_rounds` 只限制工具请求错误的修正轮数，并不能限制正常工具调用导致的无限循环。

      建议新增 `max_iterations`，每轮模型调用计数，超过限制就停止并返回明确状态。
    </box>
  </row>
  <divider color="subtle"/>
  <row align="start" gap={2}>
    <badge color="warning">P1</badge>
    <box flex="1" gap={1}>
      **4. Context 长度管理与压缩**

      当前 SessionStore 维护历史消息，但尚未看到上下文长度监控和压缩机制。

      建议采用最小方案：

      **较早的历史摘要 + 最近几轮原始消息 + 当前未完成任务状态。**

      当上下文接近阈值时，把已完成的旧对话压缩成摘要。需要保留用户目标、关键决定、重要工具结果、未完成事项。

      注意不要在工具请求与工具结果尚未配对时截断消息。
    </box>
  </row>
  <divider color="subtle"/>
  <row align="start" gap={2}>
    <badge color="warning">P1</badge>
    <box flex="1" gap={1}>
      **5. 完成端到端测试和提交文档**

      至少要验证：

      - 真实模型可以直接回答问题。
      - 真实模型可以自主调用 Calculator。
      - 用户在同一个 Session 中可以追问 Todo。
      - 同一个用户的两个 Session 不相互影响。
      - 上下文过长时能压缩，并继续正确回答。
      - 异常和循环超限时能安全退出。

      此外需要 README、运行命令、Memory 召回时机与放置策略、AI Prompt 和问题解决记录。

      `progress.md` 记载 S14 时有 445 项测试通过，但这是项目自身的历史记录。当前环境尚未安装好对应包与依赖，所以我没有把这项记录当作独立验证结果。
    </box>
  </row>
</box>

## 四、当前项目存在两个容易忽视的设计问题

### 问题 1：Todo 和 Session 的关系

当前 `TodoService` 按 `session_id` 隔离待办事项。

这符合笔试中窗口 1 和窗口 2 独立的要求。

但是有一个值得你自己思考的设计选择：

如果用户在窗口 1 说：

> 提醒我明天面试。

随后在窗口 2 说：

> 查看我所有的待办。

到底应该查到什么？

按现在的设计，窗口 2 不会看到窗口 1 的 Todo。

**这不一定是错误。** 考题强调 Session 隔离，当前设计是合理的最小实现。不过 README 应当解释清楚：这是 Session 级别的 Todo，不是用户全局 Todo。

### 问题 2：Session 状态恢复

你已经实现：

```python
store.begin(context)
store.snapshot(context)
store.append(...)
store.finish(...)
```

可以防止同一 Session 同时执行多个任务。

但是当前 SessionStore 是内存字典，服务重启后历史会消失。

而且现在处于 Busy 状态时，新消息会被拒绝，并没有排队机制。

基础笔试不一定要求完整消息队列，但你需要知道这里的设计边界。

项目进度文档显示，你已经规划 S15 引入 SQLite 持久化，还没有实际实施。这个方向是合理的，但不应该让数据库工作推迟真实 LLM 闭环的完成。

---

## 五、接下来应该按照什么顺序开发？

我建议把接下来的工作分为四个阶段。

| 阶段 | 具体任务 | 完成标准 |
|---|---|---|
| 第一阶段 | 真实 LLM + Response 解析 + 最大轮次 | 用户输入后，真实模型能决定直接回答或调用工具 |
| 第二阶段 | Search + Todo 工具注册 | 三类工具可以通过真实模型自主调用 |
| 第三阶段 | Context 压缩 + Session 持久化 | 可以进行多轮追问、压缩长历史，并在重启后恢复 |
| 第四阶段 | 真实端到端测试 + README + Prompt 记录 | 可以按 README 从零运行、验证，并解释设计决策 |

### 一定要避免过度设计

这道题叫作**最小可用 Agent**。

目前你已经在 Calculator 的数值精度、Todo 日期处理、参数校验等方面做了不少工作。

这些实现有工程价值，但接下来不建议把精力继续集中在这些局部优化上。

例如，不需要为了这道题立刻引入：

- Redis 分布式锁
- Kafka 消息队列
- 多 Agent 协作
- 向量数据库 Memory
- 完整任务调度系统

考官更可能看重的是：核心循环能否真实运行、状态是否正确、错误是否可控，以及你能否解释设计的取舍。

## 六、架构设计题也是独立交付内容

不要忘记，这份笔试还有五个架构设计模块，每个模块选一道回答。

以你现有项目最容易结合实际代码展开的，我建议选：

| 模块 | 建议题目 | 原因 |
|---|---|---|
| Context | 第 2 题：200 轮对话如何压缩 | 可以结合即将实现的 Context 压缩 |
| Memory | 第 1 题：半个月前的问题如何召回 | 与 Session 和长期记忆相关 |
| Task | 第 1 题：长程任务如何保持目标 | 可以结合循环终止与任务状态 |
| Tool / Session | 第 2 题：Busy 时新事件怎么处理 | 你已有 SessionStore，可直接深入 |
| Runtime 架构 | 第 1 题：Claude Code 与 function calling 的区别 | 可以结合模型适配器设计来分析 |

这些只是推荐选题，**当前仓库中的代码不能代替五道架构题的书面答案**。

---

## 七、我认为你现在最应该研究的一段代码

在 `runtime.py` 中：

```python
if self._tools is None:
    response = self._model.generate(model_messages)
else:
    response = self._model.generate(
        model_messages,
        tools=self._tools.schemas()
    )
```

这里实际上隐藏着三个重要的问题：

1. `schemas()` 给模型的究竟是什么？为什么只给工具描述和参数，不给函数本身？
2. 模型返回的 function call 为什么不能直接执行？为什么还需要 Registry 校验？
3. 如果模型返回两个工具调用，一个成功、一个失败，Runtime 应该怎样决定是否继续执行？

把这三个问题弄明白，再亲自完成真实模型适配器，你就不仅是在调用 AI 写 Agent，而是在掌握 Harness 的核心设计。

**最终判断：项目目前处于“底层基础组件较完整，但可交付的最小 Agent 尚未闭环”的状态。接下来优先实现真实 LLM → ToolRegistry → 工具执行 → LLM → 最终回答这条链路，再补 Context 管理、测试和文档。**

## 008 · 用户 · 2026-10-20T03:46:02.990000+00:00

嗯知道了，后续会完善项目的，我们来讨论一下架构题吧

## 009 · 助手 · 2026-10-20T03:46:19.083159+00:00

我们按**真实 Agent 架构设计面试**的方式来讨论，而不是直接让 AI 生成五道题的标准答案。

我建议采用这样的流程：

1. **理解问题**：题目真正考察什么？现实中的 Agent 为什么会遇到这种问题？
2. **你先设计**：根据已经掌握的 Agent 知识，提出初步方案。
3. **我担任架构面试官**：围绕正确性、性能、成本、异常、Trade-off 追问。
4. **对比生产实践**：研究真实 Agent 系统为什么采用不同设计。
5. **形成最终答案**：整理成可以提交的架构设计文档，明确哪些是你的设计、哪些是参考实现。

每次只讨论一道题，不固定追问轮数。重点是你能解释**为什么这么设计，以及在什么条件下你的方案会失败**。

## 第一题：Context 压缩

我建议先做模块一的第 2 题：

> 一个 Session 连续聊了 200 轮，Context 快爆了。你会怎么做压缩？如何确保压缩后的对话仍然流畅？

之所以先选它，是因为它同时涉及 Context、Memory、Session 和 Runtime，也是你正在实现的 Agent 会遇到的真实工程问题。

### 先看一个具体场景

假设用户与 Agent 已经对话了 200 轮：

- 第 5 轮：用户要求使用 Python 和 FastAPI。
- 第 28 轮：用户决定数据库使用 PostgreSQL。
- 第 76 轮：Agent 发现登录模块存在 Bug。
- 第 130 轮：用户明确不要修改数据库表结构。
- 第 185 轮：Agent 已完成登录模块，但单元测试还没通过。
- 第 200 轮：用户说：“继续完成刚才的任务。”

假设模型最多支持 128K Token，当前 Context 已经占用了 120K。

如果不处理，很可能在后续的模型调用中超出上下文限制。

如果简单保留最近 20 轮，又可能忘记早期的关键约束，比如“不允许修改数据库表结构”。

核心难点在于：

**压缩的目标不是尽可能减少 Token，而是在有限 Token 下，尽可能保留影响后续决策的信息。**

### 第一轮架构面试：先听你的方案

假设你负责设计这个 Agent 的 Context 管理模块。

现在有三个候选方案：

- **A：滑动窗口**。只保留最近 20 轮，丢弃更早的消息。
- **B：摘要压缩**。让 LLM 总结前 180 轮，再拼接最近 20 轮。
- **C：分层管理**。把系统规则、用户重要约束、当前任务状态、历史摘要、最近消息分别管理，再按需要组成 Context。

你可以选择其中一种，也可以自己组合。

**我的第一个问题是：**

你会选择什么方案？具体会保留哪些信息、丢弃哪些信息？为什么？

先不考虑代码怎么写，也不用追求完整答案。你讲清楚初步思路后，我再根据你的设计提出工程上的追问。

作为参考，真实的 OpenAI Agents SDK 已经支持 Session 历史压缩、可配置触发条件以及压缩期间的并发状态保护。后面我们会用这些生产实现检验你的方案，而不是提前照搬答案。<Cite refs={["turn859605search1","turn859605search2"]}/>

## 010 · 用户 · 2026-10-20T04:17:33.008000+00:00

我说说为什么要压缩的几个理解吧我的理解第一点就是嗯第一个就是那个长度和成本的约束嘛；这个点就是总结后的形式比原始信息更容易让模型呃去了解理解提高思考的质量这一点的原因是因为大语言模型的本质其实是搜索而非推理这其实也很正常就像是人一样你大量的知你的关键信息埋没在大量的知识里面你是很难去找是有点困难的而且很有可能会找错这时候你总结出来的知识点我我什么没干你要干什么干了什么这些就会那个更容易理解思考第三点就是缓解上下文的模型的上下文焦虑这就像是人类在截止日期之前任务还没有完成的时候的那种紧迫感会导致可能会仓促的结束任务  然后就是什么东西要咬什么东西不要呀首先呢系统提示词和工具定义这些是不用压的因为这些是静态静态信息然后这个保留那个KV cache的缓存前缀吗然后压缩的主体是历史信息那么历史信息的话，历史信息中的工具结果是压缩的主体因为工具结果对并没有那么决定性因素吧,然后压缩的策略一共是一项集首先呢是把大量的工具输出结果存在磁盘自爆流简单的假药然后第二点就是噪音直接删除对于噪音的压缩是没有任何的意义第三点就是结构化压缩,每轮进行一个结构性的压缩就像是git log那个这样子 ；

## 011 · 助手 · 2026-10-20T04:17:50.508142+00:00

你的回答已经不局限于“Context 太长了，所以需要总结”，而是开始从**成本、模型表现、任务执行、信息重要性和工程实现**几个层面思考。

尤其是你提到的三个策略：

1. 大量工具输出放到磁盘，Context 只保留简短引用。
2. 直接删除无价值噪声，而不是让模型总结所有内容。
3. 像 Git Log 一样结构化记录任务进展。

这三个思路值得进一步展开，尤其第三点，实际上涉及 Agent 的**状态管理**，比简单的聊天摘要更加深入。

不过，你的解释中有几个需要纠正的地方。

## 一、先评价你的理解

| 你的观点 | 评价 | 需要完善的地方 |
|---|---|---|
| Context 压缩降低长度和成本 | 正确 | 还要考虑压缩本身的开销 |
| 总结后模型更容易抓住重点 | 基本正确 | 压缩也可能损失关键细节 |
| LLM 本质是搜索而非推理 | 不准确 | 检索与推理不是非此即彼 |
| 压缩可以缓解模型的上下文焦虑 | 有一定工程依据 | 模型并非像人一样产生焦虑 |
| 系统提示词和工具定义不应压缩 | 方向合理 | 应考虑相关工具筛选与前缀缓存 |
| 工具输出是主要压缩对象 | 很有价值 | 有些工具输出必须精确保留 |
| 噪音直接删除 | 正确 | 需要定义什么才算噪音 |
| 像 Git Log 一样结构化压缩 | 很有潜力 | 需要明确结构及更新机制 |

### 1. LLM 的本质是搜索而非推理？

这个说法不够准确。

LLM 既可以利用上下文中的信息，也能够进行多步计算、抽象和推理。不能简单把它定义为搜索系统。

但你的直觉抓住了一个真实问题：

**上下文中的信息越多，不代表模型越能有效利用它。**

比如一份 100 页的文档，其中第 48 页有一条关键限制。

模型可能因为无关信息干扰、信息位置或任务复杂程度而忽略这条限制。这与常说的 *Lost in the Middle* 现象有关。

因此，压缩的价值不仅是减少 Token，还有提高有效信息的密度。

但也要记住：

> 摘要不是原文的无损替代品。信息密度提高了，不代表答案一定更准确。

### 2. Context 焦虑怎么理解？

你说的“模型担心 Context 快满了，所以仓促结束”，可以作为一种现象类比，但不要在架构题里直接将它当作模型的心理机制。

更严谨的说法是：

如果模型能够看到剩余 Token 预算、或者受到任务轮次与输出长度限制，它可能倾向于缩短计划或提前结束。即便没有显式预算，过长的上下文也可能影响指令遵循和任务状态识别。

因此需要区分两个问题：

- **Context 压缩**：让模型获得更有效的任务信息。
- **任务完成验证**：防止模型过早宣布成功。

后者不能仅靠压缩解决。

### 3. 工具输出为什么值得重点处理？

你这个判断是很重要的。

举个真实工程例子：

Agent 使用 `read_file` 读取一个 5000 行的源码文件，模型真正需要的可能只是：

```text
文件：runtime.py
关键函数：AgentRuntime._run()
发现：循环缺少 max_iterations 限制
证据位置：runtime.py 第 120–170 行
原始内容：artifacts/tool-output-001.txt
```

把这份摘要放进 Context，保留原始输出供后续按需读取，就可能节省大量 Token。

但是，如果工具返回的是一条关键错误日志：

```text
IntegrityError: UNIQUE constraint failed: sessions.id
```

就不应该因为它是“工具输出”而删除。这个错误可能决定下一步怎么修复系统。

所以真正的筛选依据不是**它来自哪个角色**，而是：

**这段信息是否影响未来的决策，以及是否还能可靠地重新获取。**

## 二、我最感兴趣的是你提到的 Git Log 思路

我想追问这个设计，因为它可能成为你这道架构题的核心亮点。

假设你使用结构化摘要：

```yaml
goal: 完成用户的登录系统

constraints:
  - 使用 FastAPI
  - 使用 PostgreSQL
  - 不允许修改数据库表结构

completed:
  - 完成登录接口
  - 完成密码校验

current_state:
  - 登录接口测试失败

next_actions:
  - 分析测试失败原因
  - 修复代码
  - 重新运行测试
```

相比直接将前 180 轮聊天压缩成一段自然语言，这种结构的好处是：模型更容易找到当前目标、限制条件和下一步操作。

但是这里隐藏着一个更深层的问题。

### 第一次追问：如何避免压缩丢失关键信息？

假设第 50 轮用户说：

> 登录系统使用 PostgreSQL，禁止修改数据库表结构。

第 120 轮用户又说：

> 现在允许增加一个 `last_login_at` 字段，但其他表结构不能修改。

第 180 轮 Agent 进行 Context 压缩。

如果压缩器只保留第 50 轮的信息，Agent 就会错误地认为完全不能修改表结构。

如果只保留第 120 轮的信息，又可能错误地认为所有表结构都能随意修改。

这不是简单的“总结信息”，而是需要处理**状态更新与约束覆盖**。

现在假设你是架构负责人，我想问你：

**你会如何设计压缩机制，让 Agent 知道第 120 轮的要求修改了第 50 轮的约束，同时又不丢失原来的重要限制？**

可以从你的 Git Log 思路出发，不用给代码。

这道题我们先解决“压缩后信息是否正确”，下一轮再进一步讨论“如何触发压缩、压缩失败怎么办，以及如何验证压缩质量”。

## 012 · 用户 · 2026-10-20T05:04:56.229000+00:00

我刚刚说的模型本质是检索而非推理的前提是：下面用一个简单的例子直观感受这一点。假设上下文中包含一段宠物店的巡查记录：

> 笼子 1：黑猫。笼子 2：白猫。笼子 3：黑猫。笼子 4：黑猫。笼子 5：白猫。 ……（共 100 个笼子，其中 90 只黑猫、10 只白猫）

当你问“黑猫和白猫各有多少只”时，不启用思维链的模型很难直接答对：**查找**（“笼子 37 里是什么猫？”）是注意力的强项，**统计归纳**（“总共有多少只黑猫？”）却要遍历全部记录并维护计数状态，本质上是思考而非检索。启用思维链当然能数对，但每问一次就要从头数一遍；在 Agent 场景中这类统计常常要被反复使用，累积的思考成本很高。而如果提前做一次总结，在上下文中直接写入“当前统计：黑猫 90 只，白猫 10 只”，模型立刻就能检索到这个结论。**这就是压缩的第二个价值：把需要思考才能得到的结论，变成可以直接检索的知识。 我刚刚说的确实有点不严谨了，我现在有一个小问题就是什么是思维链呢？维护一个历史记录和状态表，状态表写明当前状态以及接下来的状态**

## 013 · 助手 · 2026-10-20T05:05:09.985764+00:00

你补充的这个例子让我更准确地理解了你的意思。

你真正想表达的是：

**Context 压缩不仅是减少 Token，还可以把原本需要模型反复计算、归纳的信息，提前转化为可以直接获取的结论。**

这个理解是正确的，而且涉及一个重要的 Agent 设计思想：**将部分计算结果持久化，避免重复推理。**

我们先解决你问的“思维链”，然后再讨论你的“历史记录 + 状态表”方案。

## 一、什么是思维链（Chain of Thought，CoT）？

思维链是一种让大语言模型在给出最终答案之前，通过中间步骤处理问题的方法。

最容易理解的例子是数学题。

假设题目是：

> 小明有 10 个苹果，送给小红 3 个，又买了 5 个，现在有多少个？

<box border radius="xl" padding={3} gap={3}>
  <box gap={1}>
    <text weight="medium">方式 A：直接回答</text>
    <box background="surface-secondary" radius="lg" padding={3}>
      用户：小明现在有多少个苹果？

      模型：12 个。
    </box>
    <caption>模型直接输出最终答案，没有显式展示中间解题步骤。</caption>
  </box>
  <divider/>
  <box gap={1}>
    <text weight="medium">方式 B：使用思维链</text>
    <box background="surface-secondary" radius="lg" padding={3} gap={2}>
      <row align="center" gap={2}>
        <icon name="list-ordered" color="secondary"/>
        <text>初始有 10 个苹果</text>
      </row>
      <row align="center" gap={2}>
        <icon name="minus" color="secondary"/>
        <text>送出 3 个，剩余 7 个</text>
      </row>
      <row align="center" gap={2}>
        <icon name="plus" color="secondary"/>
        <text>买入 5 个，最终得到 12 个</text>
      </row>
      <divider color="subtle"/>
      **最终答案：12 个**
    </box>
    <caption>这是人为编写的分步解题示例，并非某个模型真实的内部思考记录。</caption>
  </box>
</box>

### 思维链有什么价值？

它让模型能够通过中间步骤处理复杂任务，而不必直接预测最终答案。

例如你提到的统计猫的数量：

- 直接回答：模型需要从大量记录中直接得到统计结果，可能出错。
- 分步骤处理：模型可以分批统计、汇总数量，再得出结论。
- 调用工具：Agent 可以让 Python 程序统计，得到确定性的结果。

第三种尤其值得注意。

**CoT 不等于准确计算，更不意味着启用后一定数对。** 对于精确统计，使用程序往往比依赖模型逐项计数更可靠。

另外，还要区分两种概念：

- **提示式 CoT**：通过示例或指令引导模型分步骤解题。
- **推理模型的内部推理过程**：模型可能使用额外的推理计算，但这些过程不一定完整暴露给 Agent 开发者。

因此，不能把没有可见思维链理解成模型完全没有推理。

## 二、你的猫咪例子其实引出了更深的设计

假设原始数据：

```text
笼子1：黑猫
笼子2：白猫
...
笼子100：黑猫
```

Agent 第一次统计后得到：

```python
{
    "black": 90,
    "white": 10
}
```

随后用户问：

> 黑猫有多少只？

Agent 就不需要再遍历 100 条记录，而是直接查询已经计算好的状态。

这和数据库里的**物化视图（Materialized View）**很相似。

原始记录保存完整事实，物化结果保存预先计算好的汇总数据。

但这马上引出一个问题：

如果第 101 只白猫进入宠物店，之前的统计结果就过期了。

因此工程上不仅要考虑如何保存结论，还要考虑：

**结论如何更新、何时失效、怎样重新验证。**

这恰好与你提出的历史记录和状态表方案有关。

---

## 三、历史记录 + 状态表：非常值得继续发展的方案

你说：

> 维护一个历史记录和状态表，状态表写明当前状态以及接下来的状态。

我认为可以进一步明确两者的职责。

<box border radius="xl" padding={3} gap={2}>
  <box background="surface-secondary" radius="lg" padding={3} gap={2}>
    <row align="center" gap={2}>
      <icon name="history" color="secondary"/>
      **历史记录（Event Log）**
    </row>
    <text size="sm">第 50 轮：用户要求禁止修改数据库表结构。</text>
    <text size="sm">第 120 轮：用户允许新增 `last_login_at` 字段。</text>
    <text size="sm">第 180 轮：Agent 完成登录接口，但测试失败。</text>
    <text color="secondary" size="xs">负责回答：过去发生了什么？约束是如何变化的？</text>
  </box>
  <row justify="center">
    <icon name="arrow-down" color="secondary" size="lg"/>
  </row>
  <box background="rgba(13,148,136,0.07)" border={{size:1,color:"rgba(13,148,136,0.4)"}} radius="lg" padding={3} gap={2}>
    <row align="center" gap={2}>
      <icon name="clipboard-list" color="success"/>
      **当前状态表（State Snapshot）**
    </row>
    ```yaml
    goal: 完成登录系统

    constraints:
      - 不允许修改其他数据库表结构
      - 允许新增 last_login_at 字段

    completed:
      - 登录接口实现

    current_problem:
      - 单元测试失败

    next_action:
      - 检查测试失败原因
    ```
    <text color="secondary" size="xs">负责回答：现在是什么情况？下一步准备做什么？</text>
  </box>
</box>

这样的好处是：

**历史记录保证可追溯，状态表保证当前执行效率。**

这里还应该区分：

- `current_state`：已经确认的当前状态。
- `next_action`：计划执行的下一步，不代表已经完成。

这对避免 Agent 把计划当成结果十分重要。

## 四、架构面试第二轮：状态冲突

现在我继续追问你的设计。

假设：

第 50 轮，用户说：

> 禁止修改数据库表结构。

第 120 轮，用户说：

> 允许新增 `last_login_at` 字段。

第 150 轮，Agent 从数据库工具得到：

> `last_login_at` 字段已经存在。

但第 180 轮，另一个工具检查数据库 Schema，又返回：

> 没有发现 `last_login_at` 字段。

此时你的历史记录与状态表应该如何处理？

这里出现了一个关键区别：

- 用户修改约束，属于**规则更新**。
- 两个工具给出矛盾结果，属于**事实冲突**。

**我的问题：你会直接用第 180 轮的新结果覆盖状态表，还是同时保留两个结果？如果保留冲突，Agent 下一步应该做什么？**

这个问题可以检验你的状态表是否只是“聊天摘要”，还是能够真正支撑 Agent 的可靠决策。

## 014 · 用户 · 2026-10-20T05:11:43.362000+00:00

保留冲突，去查清楚为什么

## 015 · 助手 · 2026-10-20T05:11:58.186194+00:00

你的判断是正确的。**不能因为第 180 轮的信息更新，就默认它比第 150 轮的信息更可信。**

但作为架构设计答案，“保留冲突，查清原因”还不够。我们需要把它变成可执行的工程方案。

### 一、你的方案可以这样实现

状态表不直接覆盖旧事实，而是记录：

```yaml
fact: last_login_at 字段是否存在
status: DISPUTED  # 存在争议

evidence:
  - value: EXISTS
    source: tool_A
    round: 150

  - value: NOT_EXISTS
    source: tool_B
    round: 180

next_action: 验证实际数据库 Schema
```

这里有三个关键设计：

1. **保留证据**：避免新结果直接覆盖旧结果。
2. **标记争议**：告诉后续 Agent 这个事实还没有得到可靠确认。
3. **安排验证**：将事实冲突转化成明确的下一步行动。

还有一个重要细节：`DISPUTED` 并不意味着两份证据一定具有相同可信度。

### 二、继续追问：怎么验证？

假设你进一步调查后发现：

- 工具 A 查询的是生产数据库。
- 工具 B 查询的是测试数据库。
- 用户的实际任务是修改测试环境中的登录系统。

那么，两个工具的输出可能都正确，只是查询环境不同。

这说明问题并不一定是“某个工具出错”，还可能是**证据缺少必要的上下文**。

因此，事实记录最好还包含环境、查询时间、数据来源等信息。

---

### 第三轮架构面试

现在换一个更难的情况：

工具 A 和工具 B 查询的都是同一个测试数据库，并且都使用只读查询。

- A 返回：字段存在。
- B 返回：字段不存在。
- 两个结果的时间只相差 1 秒。
- Agent 没有权限修改数据库。

**你现在会采取什么具体行动来解决冲突？**

我希望你尽量回答实际操作，而不是笼统说“检查日志”或“再次验证”。

例如：你会查询什么信息？怎样判断两个结果谁更可靠？如果始终无法确定，还要不要继续执行用户的任务？

这个问题实际上在考察 Agent 的一个核心能力：**面对不确定事实时，如何利用工具主动减少不确定性，而不是仅仅依赖 LLM 推测。**

## 016 · 用户 · 2026-10-20T05:22:11.549000+00:00

具体查询的参数，检查查询的内容和参数是否符合用户意图；如果始终无法确定就停下询问用户

## 017 · 助手 · 2026-10-20T05:22:26.594578+00:00

你的回答方向正确，尤其是这两点：

1. **不急于判断哪个工具正确，而是先检查查询参数与用户意图是否一致。**
2. **如果无法可靠确认，就停止依赖这个事实的操作，必要时请求用户介入。**

不过第二点需要进一步优化：不是所有无法确定的问题都要立即询问用户。

## 一、面试评价：80/100

| 维度 | 评价 |
|---|---|
| 识别证据冲突 | 很好 |
| 检查工具参数 | 正确 |
| 验证用户意图 | 很好 |
| 主动获取新证据 | 还不够具体 |
| 失败后的处理 | 需要区分风险等级 |

你已经意识到，**工具执行成功不代表工具返回的结果符合用户真正想查的问题。**

这其实是 Agent 工程中非常重要的区别。

### 为什么要检查参数？

假设两个工具都返回：

```json
{
  "success": true
}
```

但它们执行的是：

```sql
-- 工具 A
SELECT column_name
FROM information_schema.columns
WHERE table_schema = 'public'
AND table_name = 'users';
```

```sql
-- 工具 B
SELECT column_name
FROM information_schema.columns
WHERE table_schema = 'auth'
AND table_name = 'users';
```

两条查询都可以成功执行，但它们查的是不同 Schema 下的表。

因此，需要检查的不是单纯的工具状态，而是：

**工具调用参数 → 实际执行行为 → 证据内容 → 用户目标**是否一致。

## 二、怎样把你的思路升级为工程方案？

我建议分成三个阶段。

<box border radius="lg" padding={3} gap={3}>
  <row align="start" gap={3}>
    <box background="surface-secondary" radius="lg" padding={2}>
      <icon name="search" size="lg"/>
    </box>
    <box flex="1" gap={1}>
      **第一阶段：检查已有证据**

      比较两个工具的数据库连接、Schema、表名、查询语句、执行时间以及返回内容。

      先排查是否存在查询目标不同、缓存结果过期或权限过滤等问题。
    </box>
  </row>
  <divider color="subtle"/>
  <row align="start" gap={3}>
    <box background="surface-secondary" radius="lg" padding={2}>
      <icon name="refresh-cw" size="lg"/>
    </box>
    <box flex="1" gap={1}>
      **第二阶段：独立验证**

      在确保权限允许的情况下，通过只读数据库查询直接检查指定表的列定义。

      比如查询数据库系统目录，而不是反复询问两个原有工具。

      尽可能引入新的、独立的证据，而不是重复获取相同来源的信息。
    </box>
  </row>
  <divider color="subtle"/>
  <row align="start" gap={3}>
    <box background="surface-secondary" radius="lg" padding={2}>
      <icon name="shield-alert" size="lg"/>
    </box>
    <box flex="1" gap={1}>
      **第三阶段：根据风险决定是否继续**

      如果冲突不影响后续操作，可以记录争议并继续执行不依赖该事实的任务。

      如果后续需要执行数据库迁移、删除或修改关键数据，而事实仍无法确认，则暂停相关操作，明确告知用户不确定性。
    </box>
  </row>
</box>

这里有一个特别重要的原则：

**Agent 不应该因为局部信息不确定，就把整个任务全部停止；应该尽可能只阻塞依赖该信息的操作。**

例如无法确认 `last_login_at` 字段是否存在，不妨碍继续编写与该字段无关的密码校验单元测试。

---

## 三、回到我们真正的笔试题：Context 压缩

目前你已经提出了一个初步架构：

<box border radius="lg" padding={3} gap={2}>
  <svg viewBox="0 0 330 225" width="100%">
    <rect x="15" y="14" width="140" height="65" rx="10" fill="#64748b" fillOpacity=".12" stroke="#94a3b8"/>
    <text x="85" y="39" fontSize="14" fontWeight="600" textAnchor="middle" fill="currentColor">历史记录</text>
    <text x="85" y="57" fontSize="11" textAnchor="middle" fill="currentColor">保留事件与证据</text>
    <rect x="175" y="14" width="140" height="65" rx="10" fill="#0d9488" fillOpacity=".1" stroke="#0d9488"/>
    <text x="245" y="39" fontSize="14" fontWeight="600" textAnchor="middle" fill="currentColor">当前状态表</text>
    <text x="245" y="57" fontSize="11" textAnchor="middle" fill="currentColor">目标、约束、下一步</text>
    <path d="M85 79 V107 H165 V128 M245 79 V107 H165 V128" fill="none" stroke="#94a3b8" strokeWidth="1.7"/>
    <rect x="85" y="128" width="160" height="42" rx="10" fill="#d97706" fillOpacity=".12" stroke="#d97706"/>
    <text x="165" y="154" fontSize="14" fontWeight="600" textAnchor="middle" fill="currentColor">Context 构建器</text>
    <path d="M165 170 V184" stroke="#94a3b8" strokeWidth="1.7"/>
    <path d="M160 178 L165 185 L170 178" stroke="#94a3b8" strokeWidth="1.7" fill="none"/>
    <rect x="85" y="185" width="160" height="34" rx="9" fill="#64748b" fillOpacity=".12" stroke="#94a3b8"/>
    <text x="165" y="207" fontSize="13" textAnchor="middle" fill="currentColor">传入 LLM 的上下文</text>
  </svg>
</box>

这意味着历史记录可以保留更多细节，而 Context 只需要包含当前决策真正有用的信息。

不过我们还有一个关键设计没有解决。

## 四、下一轮追问：什么时候触发压缩？

假设模型的 Context Window 是 128K Token。

你的 Agent 当前已经用了 100K Token，而且正在执行一个涉及多个工具的任务。

你准备采用什么策略？

{@body const [choice,setChoice] = DIL.useState("b")}
<box border radius="xl" padding={3} gap={3}>
  <radio-group direction="col" value={choice} onChange={setChoice}>
    <radio value="a">**A. 固定阈值**：使用超过 80% 就压缩。</radio>
    <radio value="b">**B. 动态判断**：结合 Token 剩余量、当前任务状态与预计后续消耗决定。</radio>
    <radio value="c">**C. 每轮增量压缩**：每完成一轮都更新摘要，Context 始终保持较小。</radio>
    <radio value="d">**D. 自定义方案**：组合以上策略，或者提出自己的方案。</radio>
  </radio-group>
  <button block onClick={()=>GenUI.issueNewTurn(choice==="a"?"我选 A，固定阈值触发压缩。":choice==="b"?"我选 B，动态判断压缩时机。":choice==="c"?"我选 C，每轮增量压缩。":"我选 D，我想提出自己的压缩方案。")}>选择并继续讨论 <icon name="arrow-right" inline/></button>
</box>

这次我重点想听你的 **Why**：为什么选择这个触发时机？它的优缺点是什么？如果一次工具调用突然产生 50K Token 输出，你的策略还能正常工作吗？

## 018 · 用户 · 2026-10-20T06:02:18.822000+00:00

我选 B，动态判断压缩时机。

## 019 · 助手 · 2026-10-20T06:02:34.224771+00:00

选择 B 是合理的。相比固定 80% 阈值，动态判断能结合 Agent 的执行状态决定压缩时机。

不过，**动态判断并不意味着完全放弃固定阈值**。生产系统可以把固定阈值作为安全边界，把动态策略用于决定是否提前压缩。

## 一、为什么动态压缩更合理？

假设模型的 Context Window 为 128K Token。

| 场景 | 已使用 | 任务状态 | 是否需要压缩 |
|---|---:|---|---|
| A | 100K | 即将返回最终答案 | 不一定 |
| B | 80K | 即将读取大型文档 | 可能需要提前压缩 |
| C | 110K | 还有多个工具调用 | 通常需要 |
| D | 60K | 下一次工具可能返回 50K | 优先限制工具输出 |

可以发现，仅看已经使用多少 Token，并不足以决定何时压缩。

更完整的判断需要考虑：

\[
B_{\text{remaining}}=W-T_{\text{current}}-T_{\text{reserved}}
\]

其中：

- \(W\)：模型支持的上下文窗口。
- \(T_{\text{current}}\)：当前 Context 已使用的 Token。
- \(T_{\text{reserved}}\)：预留给后续输出和安全缓冲的 Token。
- \(B_{\text{remaining}}\)：当前可安全使用的剩余预算。

如果预计后续工具输出和上下文增长超过剩余预算，就应该提前采取措施。

但注意：**工具输出过大，不代表一定应该压缩整个历史。** 有时候截断、分页、过滤当前工具输出更有效。

---

## 二、动态压缩有三个难点

**第一，怎样预测后续 Token 消耗？**

LLM 下一步可能直接回答，也可能调用工具读取一个 10 万行的文件。

因此预算估算只能是近似的，需要对工具返回内容设置独立限制。

**第二，什么时候允许压缩？**

假设模型刚刚请求调用工具，但工具还没有返回。

此时压缩历史，可能让请求和结果的对应关系丢失。

因此必须考虑消息结构和未完成的工具调用。

**第三，压缩本身也需要资源。**

如果每次都让一个大型 LLM 阅读大量历史并生成摘要，可能节省了后续 Token，却增加了当前延迟和成本。

所以动态压缩还需要考虑压缩的收益是否超过开销。

---

## 三、继续追问：突发大型工具输出

现在你是 Agent Runtime 的架构负责人。

假设：

- Context Window：128K Token
- 当前已使用：70K Token
- 计划预留：16K Token
- Agent 正在调用 `read_docs` 工具

突然，工具返回了 **80K Token** 的文档。

如果全部加入 Context，就需要：

\[
70K+80K+16K=166K>128K
\]

超出模型的上下文窗口。

而且这份文档可能包含用户问题的关键答案，你不能简单全部删除。

### 面试问题

**面对这份 80K Token 的工具输出，你会怎么处理？**

重点回答两个问题：

1. 如何在不丢失关键内容的情况下，让 Agent 继续执行？
2. 假如模型随后说“我需要查看刚刚被压缩掉的某个段落”，你的系统如何支持它重新获取原文？

你之前提出的“**工具输出保存到磁盘，Context 保留摘要和引用**”正好可以用在这里。

现在请你把这个想法展开成具体的执行流程。

## 020 · 用户 · 2026-10-20T06:06:29.233000+00:00

保留原文到磁盘，只返回摘要和引用

## 021 · 助手 · 2026-10-20T06:06:43.094365+00:00

这个方案正确，而且是 Agent Context 管理中非常实用的设计：**原始信息外部存储，Context 只保留摘要与引用，必要时按需读取。**

不过作为架构面试官，我还要追问你：怎么确保这个方案不会因为摘要不准确而丢失关键信息？

## 一、先把你的方案转化成工程流程

假设 `read_docs` 返回了 80K Token。

<box border radius="lg" padding={3} gap={2}>
  <box background="surface-secondary" radius="md" padding={3} align="center">
    **read_docs 返回 80K Token**
  </box>
  <row justify="center">
    <icon name="arrow-down" color="secondary"/>
  </row>
  <grid columns={2} gap={3}>
    <grid-item>
      <box border radius="md" padding={3} gap={2} align="center">
        <icon name="hard-drive" color="secondary" size="lg"/>
        **原文持久化**
        <text color="secondary" size="xs" textAlign="center">tool_outputs/doc_001.txt</text>
      </box>
    </grid-item>
    <grid-item>
      <box border radius="md" padding={3} gap={2} align="center">
        <icon name="file-text" color="secondary" size="lg"/>
        **生成摘要**
        <text color="secondary" size="xs" textAlign="center">保留关键发现、定位信息和引用</text>
      </box>
    </grid-item>
  </grid>
  <row justify="center">
    <icon name="arrow-down" color="secondary"/>
  </row>
  <box background="rgba(13,148,136,0.07)" border={{size:1,color:"rgba(13,148,136,0.45)"}} radius="lg" padding={3} gap={2}>
    **传入 LLM 的工具结果**

    ```json
    {
      "summary": "发现登录校验和会话管理逻辑",
      "artifact_id": "doc_001",
      "sections": [
        {"topic": "登录校验", "lines": "120-180"},
        {"topic": "Session管理", "lines": "310-400"}
      ],
      "truncated": true
    }
    ```
  </box>
</box>

注意：这里的 `sections` 必须依据原文实际定位生成，不能让模型凭空编行号。

当模型需要查看原文时，再调用：

```python
read_artifact(
    artifact_id="doc_001",
    start_line=120,
    end_line=180
)
```

这就实现了：

**大数据留在外部存储，小量决策信息留在 Context。**

不过它有一个隐含前提：Agent 必须拥有重新读取原文的工具，而且原文在任务执行期间不能丢失。

## 二、我给这个回答 85 分

你的基本方案已经很明确了，现在主要差三个工程细节：

- **摘要质量**：摘要遗漏关键信息时，如何发现和补救？
- **存储引用**：如果文件被删除、覆盖或更新，怎么保证引用仍然有效？
- **安全控制**：不同用户和 Session 不能通过猜测 `artifact_id` 读取别人的内容。

这些不是要你立即实现复杂系统，而是架构题里应该主动说明的边界。

---

## 三、下一轮追问：摘要遗漏怎么办？

假设用户的任务是：

> 阅读这份 80K Token 的技术文档，帮我判断系统是否存在安全隐患。

摘要模型处理原文后，只保留了：

```text
文档主要涉及登录模块、用户管理和数据库操作。
登录模块使用 JWT。
数据库使用 PostgreSQL。
```

但是原文第 3500 行还有一条：

```python
verify_signature = False
```

这意味着某段 JWT 验证逻辑关闭了签名校验，属于非常重要的安全线索。

然而，摘要完全遗漏了这条信息。

**我的追问是：**

你的 Agent 只看到了摘要，甚至不知道第 3500 行有值得检查的内容。

这种情况下，你会怎样设计系统，让 Agent 尽可能发现这些被摘要遗漏的关键信息？

提示：这里真正的矛盾是**压缩节省 Token，却可能牺牲信息完整性**。不能只靠“让摘要模型更认真”解决。

你可以考虑是否应该把“摘要”与“针对用户任务的检索”分开设计。

## 022 · 用户 · 2026-10-20T06:09:04.682000+00:00

摘要不是简单的总结概括内容，而是结合任务目标，用户要求做上下文感知压缩

## 023 · 助手 · 2026-10-20T06:09:18.738772+00:00

这个补充很关键。你已经抓住了 **Context 压缩和普通文本摘要的本质区别：压缩应该服务于后续决策，而不是仅仅复述过去发生了什么。**

我认可你的方向。不过，作为架构面试官，我要指出一个问题：**上下文感知压缩能够降低遗漏概率，但不能保证不会遗漏。**

## 一、为什么你的思路比普通摘要更合理？

还是刚才的 80K Token 文档。

用户的任务是：

> 分析登录系统是否存在安全隐患。

对比两种压缩方式：

<box border radius="lg" padding={3} gap={3}>
  <box gap={1}>
    <row align="center" gap={2}>
      <icon name="file-text" color="secondary"/>
      **普通摘要**
    </row>
    <box background="surface-secondary" radius="md" padding={3}>
      文档介绍了登录模块、JWT 身份认证、PostgreSQL 数据库以及用户管理。
    </box>
    <text size="sm" color="secondary">回答的是：这份文档讲了什么？</text>
  </box>
  <divider color="subtle"/>
  <box gap={1}>
    <row align="center" gap={2}>
      <icon name="target" color="success"/>
      **任务感知压缩**
    </row>
    <box background="surface-secondary" radius="md" padding={3} gap={2}>
      <text weight="medium" size="sm">当前目标：检查登录系统安全性</text>
      <text size="sm">关键发现：第 3500 行存在 `verify_signature=False`，疑似关闭 JWT 签名校验。</text>
      <text size="sm">待验证：该配置是否出现在实际执行路径中，以及是否受到其他验证机制保护。</text>
      <text size="sm">原文引用：`doc_001:3500`</text>
    </box>
    <text size="sm" color="secondary">回答的是：为了完成当前任务，我应该保留哪些信息？</text>
  </box>
</box>

这里的第二种压缩明显更有价值。

而且它实际上可以形成一个闭环：

**任务目标 → 信息筛选 → 结构化压缩 → 当前状态更新 → 下一步决策。**

不过，示例中的第二种摘要有个前提：压缩过程确实发现了第 3500 行的问题。

## 二、我想进一步挑战你的设计

假设原始文档有 80K Token，而负责摘要的模型只关注到了：

- JWT 使用什么算法
- Token 过期时间
- 用户认证流程
- 数据库权限

它遗漏了 `verify_signature=False`。

虽然提示词已经明确要求“根据登录安全审计目标进行压缩”，但这个问题仍然可能发生。

因为模型不一定能够准确识别所有安全风险。

这就暴露了一个重要区别：

**任务感知压缩解决的是“应该保留什么”，但没有完全解决“如何确认重要信息没有被遗漏”。**

工程上可以考虑先使用代码扫描、规则检查、分块检索等机制提取候选证据，再让模型进行任务感知压缩。即使如此，也不能保证发现全部风险。

---

## 三、下一轮架构追问

现在让我们把问题推进到真正的架构设计层面。

你有两个选择：

**方案 A：一次性任务感知压缩**

把 80K Token 文档交给一个长上下文模型，要求它根据当前安全审计目标，直接生成一份结构化摘要。

**方案 B：先提取候选证据，再压缩**

先将 80K Token 文档分块，对每块进行安全相关信息提取，汇总候选证据后，再进行全局任务感知压缩，并保留原文引用。

两者都不是绝对正确的。

**我想问你：你更倾向哪一种？为什么？**

尤其考虑三个因素：

1. 哪种方案更不容易遗漏隐藏在文档中间的关键风险？
2. 哪种方案的 Token 成本和延迟更低？
3. 如果文档有 1000K Token，你的设计是否仍然适用？

这次不需要写代码，重点说清楚你的取舍。

## 024 · 用户 · 2026-10-20T06:27:06.261000+00:00

说实话我哪一个方案都不倾向我觉得这个东西吧每个方案都有自己的优缺点根据情况来判断吧本案a的话他从如果从你给的三个因素来考虑的话它确实不容易遗漏中间关键的风险相比的方案比较不是真的然后呢但是呢它相比方案B的话它的token成本和延迟会更高嗯然后当文档的容量大起来以后这个就不一定适用了因为交给模型的模型字本身自己也是有那个上下文限制的那么方案B呢对我觉得方案B的核心的关键是程序能否对每一块的提取能够达到呃精确精密因为我觉得它这里最重最重要的点其实不是在于全局任务感知亚索而是在于它这个程序的这个分块提取因为你这个分块提取你一旦提取出来以后你之前的信息就是不进入那个感知压缩那么你的感知压缩你不管你的感知压缩的模型有多强他也不可能在你没有的星级上给你压缩出来信息嗯差不多就是这样吧

## 025 · 助手 · 2026-10-20T06:27:22.359200+00:00

这次回答我给你 **90 分**。不是因为你没有选择 A 或 B，而是因为你开始考虑一个更本质的问题：

**压缩质量的上限，受到前面信息筛选阶段的约束。**

你说的这句话尤其重要：

> 如果分块提取时丢失了信息，那么后面的全局压缩模型再强，也无法从不存在的信息中恢复它。

这实际上触及了信息处理系统中的一个关键问题：**不可逆的信息损失**。

不过，你对方案 A 和 B 的成本、准确性判断，还需要做两处修正。

## 一、先纠正两个判断

### 1. 方案 A 不一定比方案 B 更不容易遗漏信息

你认为 A 一次性读取完整上下文，因此更不容易遗漏中间的风险。

这个推断有一定道理，但不总是成立。

长上下文模型虽然可以接收完整文本，却不代表它能同等准确地处理文本中的每一处信息。

而 B 如果把文档分块，让模型逐块检查，每次处理的信息更少，有时反而更容易发现局部问题。

例如：

| 维度 | A：全文分析 | B：分块提取再汇总 |
|---|---|---|
| 全局上下文 | 较完整 | 容易丢失跨块关系 |
| 局部细节 | 可能受到长上下文干扰 | 可以更集中地分析 |
| 信息遗漏 | 可能忽略中间细节 | 可能在提取阶段遗漏 |
| 超长文档 | 受 Context Window 限制 | 更容易扩展 |

因此，不能单凭 A 看到了全文，就判定 A 一定更准确。

### 2. 方案 B 的 Token 成本不一定更低

假设文档有 80K Token。

A 可能只需要一次 80K 输入的模型调用。

B 需要分别分析所有分块，再对提取结果进行一次汇总。

如果 B 为每一块重复输入系统提示词和任务说明，实际消耗的总 Token 可能高于 A。

但 B 有机会并行处理各块，降低总体延迟。

所以需要区分：

- **总 Token 消耗**：影响费用。
- **端到端延迟**：影响用户等待时间。
- **峰值 Context 占用**：决定是否超过模型窗口。

这三个指标不能混为一谈。

---

## 二、你发现的真正难题：信息损失发生在哪里？

我们可以把方案 B 拆成三个阶段。

<box border radius="lg" padding={3} gap={2}>
  <box background="surface-secondary" radius="md" padding={3} align="center">
    **原始文档：1000K Token**
  </box>
  <row justify="center">
    <icon name="arrow-down" color="secondary"/>
  </row>
  <box border radius="md" padding={3} align="center">
    **分块提取候选信息**
    <text color="secondary" size="xs">第一道信息筛选关口</text>
  </box>
  <row justify="center">
    <icon name="arrow-down" color="secondary"/>
  </row>
  <box border radius="md" padding={3} align="center">
    **全局任务感知压缩**
    <text color="secondary" size="xs">第二道信息筛选关口</text>
  </box>
  <row justify="center">
    <icon name="arrow-down" color="secondary"/>
  </row>
  <box background="rgba(13,148,136,0.08)" border={{size:1,color:"rgba(13,148,136,0.4)"}} radius="md" padding={3} align="center">
    **最终 Context**
    <text color="secondary" size="xs">保留少量与当前任务有关的信息</text>
  </box>
</box>

你发现了一个很重要的事实：

假如第一阶段遗漏 `verify_signature=False`，第二阶段仅阅读候选信息，就没有办法凭空恢复这条证据。

但这里还有一个值得注意的区别：

**从当前 Context 中删除，不等于从整个系统中永久删除。**

如果原文仍然保留在磁盘上，Agent 就可以通过检索重新获取。

因此，我们可以进一步区分：

- **不可恢复的丢失**：原文被删除，或者无法重新定位。
- **可恢复的遗漏**：原文还在，只是当前摘要中没有包含。

这也是为什么你之前设计的“原文持久化 + 摘要 + 引用”很重要。

---

## 三、我认为你已经形成了自己的架构雏形

你目前的思路可以归纳成：

**历史记录负责完整性，状态表负责当前决策，任务感知压缩负责减少 Context 消耗，外部存储负责按需恢复细节。**

还差一个关键能力：**当摘要不足以支持决策时，系统应该知道需要重新获取原始证据，而不是强行回答。**

这也是下一轮我想重点讨论的问题。

## 四、第四轮追问：如何让 Agent 知道自己需要重新检索？

假设你采用了以下架构：

- 原始文档完整保存在磁盘中。
- Agent 当前 Context 只包含任务感知摘要。
- Agent 可以通过 `search_docs` 和 `read_artifact` 重新访问原文。

用户现在问：

> 这个登录系统有没有安全漏洞？

当前摘要只写了：

```yaml
authentication: JWT
database: PostgreSQL
security_findings: 未发现明显问题
```

但你知道原文中确实存在一个被遗漏的风险点。

**问题来了：Agent 应该在什么情况下决定“不相信当前摘要足以回答”，并主动重新检索原文？**

我给你三个方向供参考：

A. 只要是安全审计这类高风险任务，就必须验证原始证据。

B. 当摘要缺少足够证据、存在争议或置信度不足时，触发原文检索。

C. 根据任务风险、摘要覆盖范围和验证成本动态决定。

你也可以提出自己的方案。

这里特别想考察你的一个设计能力：**如何把“模型应该谨慎一点”这种模糊要求，转化成 Runtime 可以执行的明确规则。**

## 026 · 用户 · 2026-10-20T06:40:26.042000+00:00

这里吧我感觉我感觉这也是整个agen设计的核心吧怎么验证模型你有没有玩正确的完成任务呢就是怎么把模糊的内容转换成明确可验收的内容这里吧其实我只能告诉你大致思路吧首先就是嗯就是最好就是有明确的审查目标可以用代码来检验或者明确规则来验收的东西呃具体内容怎么实现呢我感觉我的能力没有达到所以说我不能很明确的给出我的答案我只有这样一个大致的思路你也可以给我提供一下目前呃最先进大家公认的一些方案

## 027 · 助手 · 2026-10-20T06:40:41.602260+00:00

你说的“把模糊目标变成可验收条件”，确实是 Agent 可靠性设计的核心问题之一。我先查一下目前主流框架和研究怎么做任务验证，再把它们整理成适合你这个最小 Agent 项目的分层方案。

## 028 · 助手 · 2026-10-20T06:40:56.410068+00:00

你现在提出的这个问题，其实比单纯讨论 Context 压缩更深一层：

**Agent 怎么知道自己真的完成了用户的任务，而不是仅仅认为自己完成了？**

你的初步思路是正确的：尽可能把模糊目标转化成明确、可验证的条件，然后使用程序或规则进行验收。

但我想补充一个关键观点：

**目前业界没有一种公认的方法，能够可靠地验证所有类型的 Agent 任务。主流方向是分层验证：能用确定性规则检验的就用规则，不能完全形式化的再使用模型评审或人工审查。**

我查了目前的几个主流方案，包括 OpenAI Agents SDK、LangGraph 的人工介入机制，以及软件工程 Agent 的测试评估方法。下面从工程原理讲起。

## 一、先区分三个完全不同的“成功”

假设用户要求：

> 帮我修复登录 Bug，确保系统可以正常登录。

Agent 执行以下操作：

1. 找到登录代码。
2. 修改三个文件。
3. 执行测试。
4. 回答用户：“已经修复完成。”

问题在于，最后一句话不能证明任务成功。

<box border radius="xl" padding={3} gap={3}>
  <box gap={1}>
    <row align="center" justify="between">
      **第一层：执行成功**
      <badge color="success">可程序验证</badge>
    </row>
    <text size="sm">文件修改工具返回成功，测试命令正常退出。</text>
    <text color="secondary" size="xs">这只能说明某个动作执行了，不能证明用户目标达成。</text>
  </box>
  <divider color="subtle"/>
  <box gap={1}>
    <row align="center" justify="between">
      **第二层：技术验收成功**
      <badge color="success">可部分自动验证</badge>
    </row>
    <text size="sm">登录测试通过，原有功能测试通过，相关接口行为符合预期。</text>
    <text color="secondary" size="xs">证据更强，但测试覆盖不到的缺陷仍然可能存在。</text>
  </box>
  <divider color="subtle"/>
  <box gap={1}>
    <row align="center" justify="between">
      **第三层：用户目标达成**
      <badge>可能需要人工确认</badge>
    </row>
    <text size="sm">用户实际遇到的登录问题已经解决，并且没有违反用户的其他约束。</text>
    <text color="secondary" size="xs">需要结合最初目标、真实环境以及用户意图判断。</text>
  </box>
</box>

你之前研究的 Agent Runtime 通常通过模型的 `finish_reason="stop"` 判断本轮生成结束。

但这里有一个重要区别：

\[
\text{模型停止生成}\ne\text{任务成功完成}
\]

所以，真正可靠的 Agent 不能只靠模型决定什么时候结束，还需要一个独立的验收机制。

## 二、目前主流的五类验证方案

它们并不是相互替代的关系，而是解决不同层次的问题。

### 方案 1：确定性验证（Deterministic Verification）

**能通过程序判断的，就不要让 LLM 猜。**

例如：

| 任务 | 验收方式 |
|---|---|
| 计算数学表达式 | 比对数值 |
| 创建文件 | 检查文件存在、内容和格式 |
| 修复代码 | 执行测试、静态检查、构建 |
| 查询数据库 | 校验查询结果与实际数据库状态 |
| 创建 Todo | 重新读取 Todo，检查是否存在 |

这类方案的优势是可重复、成本低、结果相对明确。

例如 SWE-bench 的经典软件工程验收思路：一个测试验证原来的 Bug 是否修复，另一个测试验证原有功能是否仍然正常。<Cite refs={["turn344319search6","turn344319search2"]}/>

但要注意，**测试通过不等于软件绝对正确**。测试本身也可能遗漏需求或者写错。

### 方案 2：Guardrails（执行边界检查）

这类验证主要回答：

> Agent 这一步操作是否允许执行？输入和输出是否合法？

例如，模型请求：

```json
{
  "tool": "execute_sql",
  "arguments": {
    "sql": "DROP TABLE users"
  }
}
```

系统可以根据权限、SQL 类型和用户授权决定是否阻止。

OpenAI Agents SDK 已提供工具调用前后的 Guardrails，可以校验或拦截工具输入、输出。<Cite ref="turn344319search0"/>

它解决的是**执行安全与规则合规**，并不直接证明任务最终成功。

### 方案 3：LLM-as-a-Judge（模型评审）

假设用户要求：

> 帮我写一份清晰、准确、适合零基础学习者的技术文档。

“清晰”和“容易理解”很难写成一个简单的 `if` 判断。

于是可以使用另一个模型，根据明确的评价标准检查结果，例如：

- 是否覆盖用户要求的知识点。
- 是否出现无法支持的技术结论。
- 是否提供了必要的例子。
- 是否符合目标读者的理解水平。

但模型评审也会犯错，特别是当评审模型与执行模型存在相似偏差时。

因此，不能让评审模型随便回复一句“通过”，就把它当成事实。

### 方案 4：独立验证者（Verifier）

这和上面的模型评审有关，但范围更广。

其核心思想是：

**执行任务的 Agent 和检查任务的组件应该在职责上分离。**

<box border radius="lg" padding={3} gap={2}>
  <box background="surface-secondary" radius="md" padding={3} align="center">
    **用户目标**
  </box>
  <row justify="center">
    <icon name="arrow-down" color="secondary"/>
  </row>
  <box border radius="md" padding={3} align="center">
    **Executor Agent**
    <text color="secondary" size="xs">规划、调用工具、完成任务</text>
  </box>
  <row justify="center">
    <icon name="arrow-down" color="secondary"/>
  </row>
  <box border radius="md" padding={3} align="center">
    **Verifier**
    <text color="secondary" size="xs">检查交付物、测试结果、约束和证据</text>
  </box>
  <row justify="center">
    <icon name="arrow-down" color="secondary"/>
  </row>
  <grid columns={2} gap={2}>
    <grid-item>
      <box background="rgba(13,148,136,0.08)" border={{size:1,color:"rgba(13,148,136,0.4)"}} radius="md" padding={3} align="center">
        <icon name="check-circle" color="success"/>
        **通过**
        <text size="xs" color="secondary">返回结果</text>
      </box>
    </grid-item>
    <grid-item>
      <box background="rgba(217,119,6,0.08)" border={{size:1,color:"rgba(217,119,6,0.4)"}} radius="md" padding={3} align="center">
        <icon name="refresh-cw"/>
        **未通过**
        <text size="xs" color="secondary">反馈修复或升级处理</text>
      </box>
    </grid-item>
  </grid>
</box>

这里的 Verifier 不一定是另一个 LLM。

对于写代码任务，它可以是测试运行器；对于文档任务，可以是规则检查加模型评审；对于高风险操作，还可以是人工审核。

而且 Verifier 应该返回具体失败证据，不能只给执行 Agent 一个模糊的低分。

### 方案 5：Human-in-the-Loop（人工介入）

有些问题不适合让 Agent 自己决定。

例如：

- 删除重要数据。
- 执行资金操作。
- 用户需求存在无法消除的歧义。
- 两份关键证据持续矛盾。

LangGraph 的 Interrupt 机制支持在特定步骤暂停执行，持久化状态，等待人工批准、编辑或拒绝，然后继续运行。<Cite refs={["turn344319search3","turn344319search4"]}/>

这类机制的价值是**把人的判断放在必要的位置**，而不是每个步骤都让用户确认。

---

## 三、如何把模糊目标转化为明确的验收条件？

这是你最关心的部分。

举个例子：

用户说：

> 帮我把这个 RAG 系统优化一下，让回答更准确。

这其实是一个不完整的需求。

什么叫更准确？提升多少？允许增加多少延迟？在什么数据集上测试？

一个可行的方法是建立 **Acceptance Criteria（验收标准）**。

例如：

```yaml
task: 优化 RAG 系统

requirements:
  - 提高检索质量
  - 不破坏现有 API

acceptance:
  - 使用固定评测集比较修改前后结果
  - Recall@5 不低于基线
  - MRR 高于基线
  - 原有 API 测试全部通过

constraints:
  - 不修改现有 API 协议
  - 不允许删除原始数据
```

这只是一个示例，实际指标和提升目标需要结合用户需求确定，不能由 Agent 擅自替用户决定重要的业务取舍。

关键是将需求拆成：

- **目标**：用户真正希望得到什么。
- **约束**：执行过程中不能违反什么。
- **验收条件**：拿什么证据证明结果符合要求。

但还有一个更难的问题：有些用户目标无法完全形式化。例如“让系统更好用”，往往需要澄清或引入用户体验评测。

因此，一个可靠的 Agent 应当允许验收结果出现三种状态：

<box gap={2}>
  <row align="center" gap={2}>
    <icon name="check-circle" color="success"/>
    **PASS**：有足够证据说明已满足验收条件。
  </row>
  <row align="center" gap={2}>
    <icon name="x-circle" color="danger"/>
    **FAIL**：已经确认某些验收条件不满足。
  </row>
  <row align="center" gap={2}>
    <icon name="circle-help" color="secondary"/>
    **UNKNOWN**：目前证据不足，不能判断是否完成。
  </row>
</box>

尤其是 `UNKNOWN`，非常重要。

**没有发现问题，不等于证明不存在问题。**

这就联系到我们刚才讨论的安全审计：如果 Agent 只阅读了摘要，没有检查所有必要的原始证据，就不应该把“摘要里没有风险”当成“系统不存在风险”。

## 四、放到你正在开发的 Agent Harness 中，怎么实现？

我不建议你现在就引入多 Agent 验证系统。

对于最小 Agent，可以先设计一个非常简单的验收模块：

```python
class VerificationResult:
    status: str
    evidence: list
    failures: list
```

这只是概念示意。三个字段分别代表：

- `status`：PASS、FAIL 或 UNKNOWN。
- `evidence`：实际支持判断的证据。
- `failures`：哪些条件未通过或无法确认。

然后让 Runtime 的逻辑从：

```text
模型说完成
    ↓
直接结束
```

升级为：

```text
模型提出完成
    ↓
检查验收条件
    ↓
PASS → 结束
FAIL → 根据失败证据继续修复
UNKNOWN → 继续验证，必要时询问用户
```

当然，修复和验证都应该有最大轮数和成本预算，不能无限循环。

**这不是当前笔试必须实现的全部功能，但非常适合放进你的架构设计思考中。**

## 五、回到我们的架构面试

现在我们已经了解了五类主流验证机制。我想看看你能不能把它们真正用起来。

假设用户让你的 Agent：

> 帮我修复一个 Python 项目的登录 Bug，完成后告诉我。

Agent 修改了代码，然后运行：

```text
pytest
================
100 passed
================
```

所有测试都通过了。

模型现在认为任务已完成。

**我的问题是：你会直接允许 Agent 结束吗？为什么？**

这里不要急着给出某一种固定答案。重点思考：

**现有的 100 个测试全部通过，究竟证明了什么？又有哪些事情是它们无法证明的？**

你可以结合自己写项目、测试代码的经历回答。下一轮我们就围绕你提出的方案继续追问，直到它能够成为一个真正可执行的 Agent 验收机制。

## 029 · 用户 · 2026-10-20T06:47:06.251000+00:00

如果按照三成你之前说的三成立生成的话第一层的程序验收城市成功的第二层的技术验收层呢不确定因为他只能确定当前这个文件的bug的修复了但他不能肯定的的确定的呃说明其他的bug其他的功能有没有被因此被修改或者被引出其他的bug第三点人呃用户意图首先如果用户意图只是单纯的修复bug的话可能是成功了也有可能是用户有其他的那个想法但是他没有表达出来这个也是不确定的嗯但是吧我应该会允许agen结束毕竟哎哟这个嗯我感觉可以结束吧毕竟明确的bug确实已经修复了嗯

## 030 · 助手 · 2026-10-20T06:47:22.369388+00:00

你的回答有一个值得肯定的地方：**你没有把“测试全部通过”直接等同于“整个软件完全正确”，但也没有因为无法保证绝对正确，就要求 Agent 无限验证下去。**

这是合理的工程思路。现实中的系统不可能证明自己不存在任何 Bug。

不过，我要纠正你一个关键判断。

## 一、你的三层验收分析

| 验收层次 | 你的判断 | 我的评价 |
|---|---|---|
| 执行成功 | 成功 | 基本正确，`pytest` 已执行并返回通过结果 |
| 技术验收 | 不确定 | 正确，但还需要区分具体测试覆盖范围 |
| 用户目标 | 可能完成 | 合理，但证据还不足以确认 |

这里最关键的是：

**100 个测试通过，甚至不能直接证明用户报告的 Bug 已经修复。**

为什么？

假设用户报告：

> 使用错误密码登录时，系统有时候会直接崩溃。

Agent 修改了登录代码，然后运行：

```text
100 passed
```

但这 100 个测试中，可能根本没有任何一个覆盖“错误密码登录”的场景。

那么测试全部通过，只能说明：

> 在当前环境中，已执行的这 100 个测试没有发现失败。

不能证明用户的 Bug 已被修复。

这也引出了软件工程中一个重要概念：**回归测试（Regression Testing）**。

除了检查目标 Bug 是否修复，还需要检查相关的原有功能是否因为修改而受到破坏。

## 二、什么时候可以允许 Agent 结束？

我赞成你说的“可以结束”，但需要区分：

- **结束执行**：Agent 不再继续修改或验证。
- **报告成功**：Agent 宣称用户的任务已经完成。

这两个动作不应该完全绑定。

例如：

| 情况 | 能否结束 | 应该怎样报告 |
|---|---|---|
| Bug 复现测试通过，相关回归测试通过 | 可以 | 已通过约定的验收条件 |
| 只运行了现有的 100 个测试 | 可以，但证据有限 | 现有测试通过，原始 Bug 尚待针对性验证 |
| Bug 复现测试仍然失败 | 不应报告成功 | 继续修复，或在预算耗尽时报告未完成 |
| 关键环境无法访问 | 可以暂停 | 报告无法验证的部分，等待补充条件 |

因此，你可以建立一个非常实用的原则：

**Agent 不必无限追求确定性，但必须准确报告自己已经验证了什么、没有验证什么。**

---

## 三、这里其实存在两个不同的终止条件

很多初学者容易把它们混为一谈。

<box border radius="lg" padding={3} gap={2}>
  <box background="surface-secondary" padding={3} radius="md" gap={1}>
    **执行终止条件（Termination Condition）**

    Agent 什么时候不再继续执行？

    例如任务完成、达到最大轮次、预算耗尽、遇到无法继续的阻塞。
  </box>
  <box background="surface-secondary" padding={3} radius="md" gap={1}>
    **成功验收条件（Acceptance Condition）**

    Agent 有什么证据说明用户目标已经达成？

    例如目标 Bug 测试通过，相关回归测试通过，没有违反用户约束。
  </box>
</box>

它们的关系不是一一对应的。

Agent 可以因为预算耗尽而终止，但不能因此宣称成功。

同样，如果验收条件已满足，Agent 就没有必要继续消耗 Token 去做无意义的检查。

---

## 四、下一轮架构面试：谁来制定验收标准？

这是我认为你目前最应该深入思考的地方。

假设用户只说：

> 帮我优化一下这个 Agent，让它更稳定。

这是一个非常模糊的任务。

现在有三种方案：

**A. Agent 自己制定标准。**

例如它认为：
- 现有测试全部通过。
- 工具调用成功率达到 99%。
- 连续运行 100 次没有崩溃。

**B. 必须让用户先明确标准。**

例如 Agent 询问：

> 你说的稳定，是指减少工具调用失败、降低模型幻觉，还是避免长任务中断？

用户回答后才开始执行。

**C. 混合方案。**

Agent 根据项目现状提出可测量的验收标准，对一般性、低风险的改进先执行；对于会明显影响范围、成本或用户预期的重要指标，先让用户确认。

**问题：你更倾向哪种方案？为什么？**

我再加一个限制：用户已经说了“你自己看着优化就行，我不懂技术”。

这种情况下，你的 Agent 应该如何避免频繁打扰用户，又不擅自改变用户真正想要的东西？

这道题直接关系到你此前关心的一个问题：**如何让 AI 自主完成工作，同时让你仍然能够掌控它的行为。**

## 031 · 用户 · 2026-10-20T06:50:05.921000+00:00

这里其实我感觉也是取舍的问题吧我感觉工程上的问题是没有完美答案只有取舍的上的比较完美的答案无意我认为吧我其实倾向于混合方案因为混合方案能那个比较综合两者的两个方案的优缺点优点如果用户说你自己看着优化就行我不懂技术这个时候我个人感觉让agen自己来执行吧毕竟我有时候也是这么用的因为确实是不懂嗯然后呢在一些关键决策关键节点汇报给用户吧就是不要自己盲目的埋头去干在一些比较关键的流程啊几点啊这种还是过问一下吧

## 032 · 助手 · 2026-10-20T06:50:20.467152+00:00

我认可你选择混合方案的方向，而且你说的这句话值得进一步展开：

> 工程问题没有绝对完美的答案，关键在于不同约束下做出合理的取舍。

不过，作为架构面试官，我想把你的回答再推进一层。

**你现在已经回答了“应该在关键节点让用户介入”，但还没有回答“什么样的节点才算关键”。**

这恰好是 Agent Runtime 需要解决的工程问题。

## 一、你的方案评价：85/100

你已经考虑到三个重要因素：

1. **自主性**：不能每一步都询问用户，否则 Agent 失去自主执行的价值。
2. **可控性**：不能让 Agent 在用户不了解的情况下擅自作出重大决策。
3. **用户体验**：用户不懂技术时，Agent 应主动承担技术判断，而不是把所有问题抛给用户。

欠缺的是：把“关键节点”转化成程序可以执行的判断条件。

比如，Agent 准备执行下面四种操作：

| Agent 准备执行的操作 | 建议处理方式 |
|---|---|
| 读取代码、分析日志 | 自动执行 |
| 修改一个局部函数并运行测试 | 通常自动执行 |
| 重构整个项目的核心架构 | 先确认重要设计取舍 |
| 删除生产数据库中的数据 | 必须有明确授权，并遵守执行权限控制 |

这里存在一个很重要的区别：

**汇报（Notification）和审批（Approval）不是一回事。**

- 汇报：告诉用户发生了什么，不一定暂停任务。
- 审批：Agent 必须等待用户确认才能继续。

例如，Agent 花了十分钟完成代码分析，可以发送进度报告，但没有必要让用户点击“同意继续”。

## 二、如何将你的混合方案真正工程化？

我建议建立一个基于风险的决策机制。

<box border radius="lg" padding={3} gap={2}>
  <box background="surface-secondary" radius="md" padding={3} align="center">
    **Agent 提出下一步行动**
  </box>
  <row justify="center">
    <icon name="arrow-down" color="secondary"/>
  </row>
  <box border radius="md" padding={3} align="center">
    **Runtime 检查风险、权限和任务约束**
  </box>
  <row justify="center">
    <icon name="arrow-down" color="secondary"/>
  </row>
  <grid columns={3} gap={2}>
    <grid-item>
      <box border radius="md" padding={2} gap={2} align="center">
        <badge>低风险</badge>
        <icon name="check-circle" color="success" size="lg"/>
        <text size="xs" textAlign="center">自动执行并记录</text>
      </box>
    </grid-item>
    <grid-item>
      <box border radius="md" padding={2} gap={2} align="center">
        <badge>中风险</badge>
        <icon name="clipboard-check" color="secondary" size="lg"/>
        <text size="xs" textAlign="center">自动校验，必要时询问</text>
      </box>
    </grid-item>
    <grid-item>
      <box border radius="md" padding={2} gap={2} align="center">
        <badge>高风险</badge>
        <icon name="shield-alert" color="danger" size="lg"/>
        <text size="xs" textAlign="center">暂停并要求授权</text>
      </box>
    </grid-item>
  </grid>
</box>

风险可以从四个维度判断：

- **影响范围**：只影响一个文件，还是整个系统？
- **可逆性**：执行失败后能否轻松回滚？
- **外部副作用**：是否涉及生产数据、资金、消息发送或外部服务？
- **用户授权**：用户是否明确允许该操作？

注意，不能只由 LLM 自己判断“这是低风险”，然后自己决定执行。涉及关键权限的检查，应该由 Runtime 的确定性规则约束。

---

## 三、继续架构面试：一个真正棘手的场景

假设用户要求：

> 帮我优化这个 Agent 项目，让运行速度更快、更稳定。具体怎么做你自己决定，我不懂技术。

Agent 分析代码后发现：

当前项目使用 SQLite 保存 Session，存在一些并发写入问题。

它提出两个方案：

**方案 A：保留 SQLite。**

通过事务管理、WAL、Busy Timeout 和单写入者等方式优化并发行为。

优点是改动较小、部署简单，但并发写入吞吐仍有上限。

**方案 B：迁移到 PostgreSQL。**

改造数据库访问层、数据迁移方式和部署配置。

可能获得更适合多实例服务的数据库能力，但复杂度、运维成本和迁移风险都会增加。

Agent 认为 B 更有长期发展潜力。

可是用户明确说了：“我不懂技术，你自己决定。”

### 我的追问

**你会允许 Agent 直接选择 B 并实施数据库迁移吗？还是要求用户确认？**

这里还有一个限制：如果用户确实不懂数据库，你不能简单把两个技术方案扔给用户，让他自己选。

我希望你回答两个方面：

1. Agent 应该根据什么依据决定采用 A 还是 B？
2. 如果必须让用户参与，应该怎样向不懂技术的用户解释，让他能够作出有意义的决策？

这道题的关键不只是权限控制，而是更深一层的 **Agent 自主决策边界**。

## 033 · 用户 · 2026-10-20T06:53:27.685000+00:00

嗯依据的话根据风险来吧中低风低风险的话就可以Asian自己去执行中风险的话系统检验吧高风险的话就人工授权如果用户真的不懂技术的话有想要用户参与进来的话可以用一种比较简单直接或者用生活例子来解释一下就是提及一下取舍收益吧

## 034 · 助手 · 2026-10-20T06:53:41.290790+00:00

你的回答方向正确，但我认为还差一个很关键的判断：

**风险等级决定 Agent 能不能自主执行，而不是决定哪个技术方案更值得选择。**

这是两个不同的问题。

## 一、先评价你的回答：82/100

你已经掌握了风险分级、人工授权和面向非技术用户解释 Trade-off 的思路。

但你没有真正回答：**SQLite 和 PostgreSQL，到底应该选哪个？**

假设两个方案都得到了用户授权，我们依然需要做技术选择。

这就要求 Agent 建立两套决策机制：

| 决策机制 | 解决什么问题 | 考虑因素 |
|---|---|---|
| 技术方案评估 | 应该采用哪个方案？ | 性能收益、成本、复杂度、可维护性 |
| 风险与权限控制 | 是否允许 Agent 自主执行？ | 影响范围、可逆性、外部副作用、用户授权 |

这两套机制应当分开，而不是混在一起。

## 二、实际工程中，我会如何设计？

首先，**不要因为 PostgreSQL 更适合大规模系统，就直接决定迁移。**

应该先拿到证据。

例如，Agent 发现 SQLite 并发写入出现问题，那么第一步应该检查：

- 是不是事务持有时间太长？
- 是否有多个写入操作相互阻塞？
- 是否配置了 WAL 和 `busy_timeout`？
- 当前用户量和并发规模究竟有多大？
- 系统是否确实需要多实例部署？

假设经过分析，发现：

当前只有十几个用户，主要问题是某个事务长期持有写锁。

那么保留 SQLite、修复事务管理，可能就是更合理的方案。

反过来，如果系统需要多个服务实例同时写入，有持续增长的高并发需求，迁移 PostgreSQL 就更值得考虑。

这体现了一个很重要的工程原则：

**不是选择技术上更强大的方案，而是选择能以合理成本满足实际需求的方案。**

## 三、怎样向不懂技术的用户解释？

你说的生活化解释很合适，但不能只解释技术名词，还应该给用户一个有依据的建议。

例如：

> 我发现系统变慢的主要原因是多个操作会同时争用数据写入权限。
>
> 现在有两种解决方法：
>
> 一种是优化目前的数据库，类似于改善现有道路的交通规则，成本低、改动小。
>
> 另一种是换一套更适合多车道交通的系统，未来扩展更方便，但需要迁移数据，部署和维护也会更加复杂。
>
> 根据目前的使用规模，我建议先优化现有数据库。如果之后用户量显著增长，再考虑迁移。
>
> 我可以先进行局部优化并运行测试。数据库迁移涉及较大改动，会单独征求你的确认。

这里 Agent 做了三件事情：

1. 根据证据提出建议。
2. 用用户能够理解的语言解释收益和代价。
3. 将普通技术执行与重大架构决策区别处理。

这样用户不需要成为数据库专家，也能掌握重要决策权。

---

## 四、最后一道追问：Agent 如何防止“验证通过却没有真正优化”？

这次回到我们最初讨论的核心：**任务验收机制**。

假设 Agent 选择了优化 SQLite。

经过修改后：

<box border radius="lg" padding={3} gap={2}>
  <text weight="medium">Agent 执行结果</text>
  <grid columns={2} gap={2}>
    <grid-item>
      <box background="surface-secondary" radius="md" padding={3} gap={1}>
        <text size="xs" color="secondary">修改前平均响应延迟</text>
        <title size="xl" color="default" tabularNums>120 ms</title>
      </box>
    </grid-item>
    <grid-item>
      <box background="surface-secondary" radius="md" padding={3} gap={1}>
        <text size="xs" color="secondary">修改后平均响应延迟</text>
        <title size="xl" color="success" tabularNums>80 ms</title>
      </box>
    </grid-item>
  </grid>
  <Chart content={{"chartType":"bar","meta":{"footer":"假设数据，用于面试讨论"},"xKey":"version","series":[{"dataKey":"latency","label":"平均响应延迟","valueSuffix":" ms"}],"yAxisMin":0,"data":[{"version":"修改前","latency":120},{"version":"修改后","latency":80}]}}/>
  <text size="sm">另外，原有的 100 个单元测试全部通过。</text>
</box>

模型看到这个结果后说：

> 优化成功！响应速度提升了约 33%，所有测试通过。

但是，你作为架构负责人发现：

修改前的测试只有 10 个并发用户，修改后的测试只有 1 个并发用户。

也就是说，**两次性能测试的环境和负载不一致**。

现在我的问题是：

**你会怎样设计 Agent 的验证机制，防止它拿着不具有可比性的数据，就宣称性能优化成功？**

这次请你尝试跳出“让模型再检查一次”的思路，思考能否让程序从测试设计、执行条件和数据记录三个层面保证验证的可靠性。

这道题如果你能解释清楚，我们就可以把前面讨论的内容总结为一套完整的 Agent 执行与验收架构，再回到笔试中的五个模块。

## 035 · 用户 · 2026-10-20T06:55:24.362000+00:00

首先呢验收的那个评测集那个测试是条件必须要是一样的必须要控制变量变的条件只能有一个然后差不多就这样吧嗯

## 036 · 助手 · 2026-10-20T06:55:39.089457+00:00

你的回答抓住了性能评测最核心的原则：**控制变量，保证修改前后的测试结果具有可比性。**

这个思路是正确的。不过，在真实工程中，“其他条件必须一样”还需要转化成程序可以检查的规则。

## 一、如何把你的想法工程化？

假设我们需要验证 SQLite 优化是否有效。

可以建立一份统一的测试配置：

```yaml
benchmark:
  concurrent_users: 10
  requests: 10000
  database_size: 100000
  test_data: fixed_dataset_v1
  hardware: same_machine
  warmup_seconds: 30
  repetitions: 5
```

Agent 不能随意改变这些条件，否则测试结果不能直接比较。

同时，实际工程中也很难保证只有代码这一个变量发生变化，例如操作系统调度、缓存和网络波动。因此需要多次重复实验，并记录测试环境。

### 但只控制变量还不够

假设在相同条件下，测试结果如下：

| 指标 | 修改前 | 修改后 | 变化 |
|---|---:|---:|---|
| 平均延迟 | 120 ms | 80 ms | 降低 33.3% |
| P99 延迟 | 250 ms | 600 ms | 增加 140% |
| 错误率 | 0.1% | 2.0% | 明显恶化 |

平均延迟确实改善了，但最慢的那部分请求变得更慢，错误率也大幅上升。

因此，Agent 不能仅凭平均延迟下降就宣布优化成功。

**验收必须检查用户真正关心的多个指标，而不是挑选对自己有利的数据。**

## 二、一个比较完整的自动验收方案

<box border radius="lg" padding={3} gap={2}>
  <box background="surface-secondary" radius="md" padding={3} align="center">
    **任务开始：固定验收标准和测试配置**
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **执行基线测试，保存环境和结果**
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **Agent 修改代码，运行相同测试**
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **程序检查配置一致性、性能指标和回归测试**
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <grid columns={2} gap={2}>
    <grid-item>
      <box border radius="md" padding={3} align="center" gap={1}>
        <icon name="check-circle" color="success"/>
        **PASS**
        <text size="xs" color="secondary">满足约定验收条件</text>
      </box>
    </grid-item>
    <grid-item>
      <box border radius="md" padding={3} align="center" gap={1}>
        <icon name="x-circle" color="danger"/>
        **FAIL / UNKNOWN**
        <text size="xs" color="secondary">修复、重测或报告限制</text>
      </box>
    </grid-item>
  </grid>
</box>

对于性能优化，还应该检查改进是否大于正常测试波动。否则 120 ms 和 118 ms 的差别，可能只是偶然现象。

---

## 三、我们这轮讨论可以总结成什么？

其实从 Context 压缩出发，我们逐步讨论出了一个更完整的 Agent 设计思路：

1. **Context 管理**：保留任务目标、重要约束、历史证据和当前状态，降低信息丢失风险。
2. **任务执行**：让模型决定怎么做，但由 Runtime 控制权限、工具和资源预算。
3. **结果验证**：尽可能通过代码、测试和外部证据判断结果，而不是相信模型的自我声明。
4. **失败处理**：区分已证实的失败与证据不足，决定继续修复、重新验证还是请求人工介入。
5. **终止机制**：任务验收通过可以结束；预算耗尽也可以结束，但不能谎称成功。

这套思路已经可以用于你的架构设计题了。

不过，**我们原本选择的模块一第 2 题——200 轮 Session 的 Context 压缩——还没有完全讨论完。** 我们已经解决了压缩内容、历史记录、状态表、任务感知压缩和动态触发的基本思路，但还有一个很关键的问题。

### 下一轮：如何验证压缩本身没有破坏 Agent 的状态？

假设压缩前，Agent 明确知道：

- 用户禁止修改数据库表结构，唯一例外是允许新增 `last_login_at`。
- 登录接口已经完成。
- 测试仍然失败。
- 下一步应该检查失败原因。

压缩后，摘要错误地写成：

> 登录接口已经完成，所有测试通过。

**你会怎样设计一个自动化验证机制，检查压缩前后的关键任务状态是否一致？**

这里可以借鉴你刚刚提出的“控制变量、明确验收条件”思路。重点不是让另一个 LLM 简单评价摘要好不好，而是思考：哪些状态可以用程序直接比较，哪些必须借助模型或原始证据验证？

## 037 · 用户 · 2026-10-20T06:59:35.538000+00:00

状态持久化把状态持久化到数据库里面每次压缩后的时候查询对应的状态吧

## 038 · 助手 · 2026-10-20T06:59:52.340539+00:00

你的方案是正确的，而且你已经从“如何压缩文本”转向了一个更重要的设计思想：

**不要把 Agent 的关键状态完全依赖于 LLM 的 Context，而应该由程序独立管理和持久化。**

我给这次回答 **85 分**。核心方向正确，但还需要解决“数据库中的状态是否可靠”和“压缩是否改变事实”两个问题。

## 一、你的方案为什么有效？

假设 Agent 正在修复登录系统。

数据库中保存：

```yaml
task_id: login_fix_001

goal: 修复登录系统

constraints:
  - 禁止修改数据库表结构
  - 允许新增 last_login_at 字段

completed:
  - 登录接口实现

test_status: FAILED

next_action: 检查失败原因
```

压缩前，Context 有 100K Token。

压缩后，摘要错误地写成：

```yaml
test_status: PASSED
```

如果数据库里的 `test_status` 仍然是 `FAILED`，程序就可以检测到冲突。

这意味着：

**压缩器可以改变信息的表达方式，但不能擅自改变已经确认的任务事实。**

## 二、我建议把你的方案设计成三层

<box border radius="lg" padding={3} gap={2}>
  <box background="surface-secondary" radius="md" padding={3} gap={1}>
    <row align="center" gap={2}>
      <icon name="database" color="secondary"/>
      **第一层：持久化状态（事实来源）**
    </row>
    <text size="sm">任务目标、用户约束、工具执行结果、验收状态、状态版本。</text>
  </box>
  <row justify="center">
    <icon name="arrow-down" color="secondary"/>
  </row>
  <box border radius="md" padding={3} gap={1}>
    <row align="center" gap={2}>
      <icon name="refresh-cw" color="secondary"/>
      **第二层：压缩与验证**
    </row>
    <text size="sm">生成摘要后，对照数据库中的关键状态检查是否出现遗漏或矛盾。</text>
  </box>
  <row justify="center">
    <icon name="arrow-down" color="secondary"/>
  </row>
  <box background="rgba(13,148,136,0.07)" border={{size:1,color:"rgba(13,148,136,0.4)"}} radius="md" padding={3} gap={1}>
    <row align="center" gap={2}>
      <icon name="file-text" color="success"/>
      **第三层：构建新 Context**
    </row>
    <text size="sm">把经过检查的摘要、权威状态和最近消息组合起来，再交给 LLM。</text>
  </box>
</box>

这里特别注意：数据库中的状态也需要有可信的更新流程。

例如，不能让 LLM 直接把 `test_status` 从 `FAILED` 改成 `PASSED`。应该要求真实的测试执行结果作为更新依据。

否则数据库只是把模型的错误长期保存起来，并不能提供真正的可靠性。

## 三、还有一个重要区别：压缩一致性与事实正确性

这是这道架构题很容易遗漏的地方。

| 问题 | 如何解决 |
|---|---|
| 摘要与数据库状态不一致 | 对比结构化字段 |
| 数据库状态本身过期 | 重新获取外部证据 |
| 数据库错误地记录任务成功 | 根据测试报告或工具结果验证 |
| 摘要遗漏了未结构化的重要约束 | 对照原始事件记录，必要时用模型辅助检查 |

因此，持久化状态解决了很大一部分问题，但不能替代全部验证。

---

## 四、接下来我想追问你一个更深的问题

假设你的系统有：

- **Event Log**：保存每次用户指令、工具结果、状态变化。
- **State Table**：保存最新的任务目标、约束和完成状态。
- **Context Summary**：专门给 LLM 阅读的压缩摘要。

现在出现下面的情况：

第 100 轮，数据库状态为：

```yaml
test_status: FAILED
version: 10
```

第 101 轮，Agent 重新运行测试，全部通过，状态更新为：

```yaml
test_status: PASSED
version: 11
```

但与此同时，一个 Context 压缩任务已经开始，它读取的是第 100 轮的旧状态。

最终，它生成：

```yaml
test_status: FAILED
version: 10
```

如果 Runtime 不加检查，旧摘要就可能覆盖新信息，让 Agent 误以为测试仍然失败。

**问题：你会怎样避免压缩任务使用旧状态覆盖数据库中的新状态？**

提示：这不是 LLM 理解能力的问题，而是经典的**并发与状态一致性问题**。

你可以从熟悉的 C++ 多线程、数据库事务、版本号或者乐观锁的角度思考。

## 039 · 用户 · 2026-10-20T07:03:20.006000+00:00

嗯这里就涉及到了并发的问题那就哎这个其实我感觉就是哎不对不对就是就是呃把查询和修改写成同一个原子操作就是要么一起完成要么同时步完成然后嗯差不多就这样然后数据库用唯一索引吧

## 040 · 助手 · 2026-10-20T07:03:35.502379+00:00

你的思路已经接近正确答案了。你意识到这是**并发状态一致性问题**，并且想通过数据库事务保证原子性。

但这里有一个关键点需要纠正：

**事务的原子性不等于能够防止旧数据覆盖新数据。唯一索引也不能单独解决这个问题。**

这个场景最适合使用你在 C++ 并发编程中接触过的思想：**乐观锁（Optimistic Locking）+ 版本号校验**。

## 一、为什么单纯使用事务还不够？

假设发生以下操作：

<box border radius="lg" padding={3} gap={2}>
  <text weight="medium">两个并发任务的执行过程</text>
  <table>
    <table-row><table-cell width="18%">时间</table-cell><table-cell>压缩任务 A</table-cell><table-cell>测试任务 B</table-cell></table-row>
    <table-row><table-cell> T1 </table-cell><table-cell>读取状态 V10（FAILED）</table-cell><table-cell>—</table-cell></table-row>
    <table-row><table-cell> T2 </table-cell><table-cell>LLM 开始生成摘要</table-cell><table-cell>运行测试</table-cell></table-row>
    <table-row><table-cell> T3 </table-cell><table-cell>仍在生成摘要</table-cell><table-cell>更新为 V11（PASSED）</table-cell></table-row>
    <table-row><table-cell> T4 </table-cell><table-cell>尝试写入旧摘要 V10</table-cell><table-cell>—</table-cell></table-row>
  </table>
</box>

即使 A 和 B 各自都使用事务，如果 A 最后直接覆盖当前摘要，依然可能将过时信息写入数据库。

这是因为事务保证的一部分性质是操作的原子提交，而这里需要额外判断：

> 我读取状态时的版本，是否仍然是数据库当前版本？

## 二、用版本号解决

假设状态表：

```text
task_id       test_status    version
login_001     FAILED         10
```

压缩任务 A 读取 `version=10`。

等它生成摘要以后，不直接执行普通 `UPDATE`，而是：

```sql
UPDATE task_context
SET summary = '新的摘要'
WHERE task_id = 'login_001'
  AND version = 10;
```

重点在：

```sql
AND version = 10
```

如果测试任务 B 已经将状态更新为 V11，A 的更新就匹配不到任何记录。

程序通过受影响行数为 0，知道这份摘要已经过期。

这就是一种 **Compare-And-Swap（CAS）式条件更新**。

在 SQLite 或 PostgreSQL 中，都可以利用带条件的 `UPDATE` 实现这种检查。

实际实现时，还应将摘要对应的状态版本单独记录；如果需要推进版本号，则在同一原子更新中完成。

### 更稳妥的策略

1. 压缩前读取状态和版本号。
2. 使用这个状态生成摘要。
3. 提交前比较数据库版本。
4. 版本一致则保存摘要。
5. 版本不一致则丢弃过期结果，或者基于最新状态重新压缩。

要注意：如果摘要依赖的是整个消息历史，版本号也必须能反映相关历史的变化，不能只检查 `test_status` 这一列。

## 三、唯一索引有什么作用？

你提到唯一索引，这个想法也有用，只是用途不一样。

例如：

```sql
CREATE UNIQUE INDEX idx_task_id
ON task_context(task_id);
```

它能保证同一个 `task_id` 不会出现两条记录。

但它不能自动判断哪条摘要更新、哪条摘要过期。

可以这样区分：

| 技术 | 解决的问题 |
|---|---|
| 事务 | 一组数据库操作要么提交，要么回滚 |
| 唯一索引 | 防止违反唯一性约束的重复记录 |
| 乐观锁 / 版本号 | 防止基于旧版本的更新覆盖新状态 |
| 悲观锁 | 在必要时阻止其他事务并发修改受保护的数据 |

对于 Context 压缩，由于 LLM 生成摘要可能耗费数秒甚至更长时间，**一般不建议整个生成过程都持有数据库写锁**。

乐观锁通常更加适合。

---

## 四、继续面试：如果版本一直变化怎么办？

这次问题更贴近真实的 Agent 系统。

你的 Agent 正在与用户持续对话，每隔几秒都会产生新的消息和状态更新。

与此同时，后台 Context 压缩任务需要 10 秒才能完成。

结果：

- 第一次压缩读取 V10，完成时状态已经 V12。
- 第二次读取 V12，完成时状态已经 V15。
- 第三次读取 V15，完成时状态已经 V18。

每次都因为版本不一致而丢弃摘要。

**我的追问是：你会怎样避免压缩任务一直失败，同时又保证旧摘要不会覆盖新状态？**

这里我希望你考虑一个新思路：

是否一定要让摘要覆盖整个最新 Context？能不能让摘要只负责某一段已经确定不再变化的历史，而把新消息单独保留？

这实际上就会涉及你最早提到的 **Git Log 式归档、增量压缩和滑动窗口结合**，我们可以把这些设计真正串起来。

## 041 · 用户 · 2026-10-20T07:06:49.847000+00:00

嗯清版本倾向首先呢我倾向就是这样子的你压缩任务的时候是不允许呃和对和那个用户继续对话的反正我用的cloud code和code都是这样子压缩的过程中是不能进行新用新的会话交流的我插我感觉这个方式会好一点吧说实话

## 042 · 助手 · 2026-10-20T07:07:05.046044+00:00

我认为你的方案是合理的，而且对于你正在实现的**最小可用 Agent Harness，我甚至更倾向于你这个方案**。

你的思路是：**压缩期间暂停当前 Session 的新一轮执行，等待压缩完成后再继续。**

这实际上是在用串行化降低并发复杂度。

不过，我想帮你区分两件事：

- **禁止同一个 Session 并发执行任务**：我支持。
- **压缩期间完全不允许用户发送消息**：不一定必要。

这两者看起来相似，工程上的区别却很大。

## 一、你的方案怎么实现？

假设 Session 有三个状态：

<box border radius="lg" padding={3} gap={2}>
  <svg viewBox="0 0 340 180" width="100%">
    {#each [{x:6,t:"IDLE",s:"空闲"},{x:122,t:"RUNNING",s:"执行任务"},{x:238,t:"COMPACTING",s:"压缩中"}] as n}
      <rect key={n.t} x={n.x} y="62" width="96" height="49" rx="9" fill={n.t==="COMPACTING"?"#d9770619":"#64748b18"} stroke={n.t==="COMPACTING"?"#d97706":"#94a3b8"}/>
      <text x={n.x+48} y="81" textAnchor="middle" fontSize="12" fontWeight="600" fill="currentColor">{n.t}</text>
      <text x={n.x+48} y="99" textAnchor="middle" fontSize="11" fill="currentColor">{n.s}</text>
    {/each}
    <g fill="none" stroke="#94a3b8" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      <path d="M102 76 H122 M116 71 L122 76 L116 81"/>
      <path d="M218 76 H238 M232 71 L238 76 L232 81"/>
      <path d="M286 111 V151 H54 V112 M49 118 L54 112 L59 118"/>
      <path d="M170 62 V25 H54 V62 M49 56 L54 62 L59 56" strokeDasharray="4 4"/>
    </g>
    <text x="170" y="17" fontSize="10" fill="currentColor" textAnchor="middle">任务执行完成</text>
    <text x="170" y="168" fontSize="10" fill="currentColor" textAnchor="middle">压缩完成，恢复运行</text>
    <text x="228" y="57" fontSize="10" fill="#d97706" textAnchor="middle">达到阈值</text>
  </svg>
  <caption>概念示意：同一 Session 在压缩期间不执行新任务。不同 Session 仍可独立运行。</caption>
</box>

这里有一个好处：

当压缩开始时，Runtime 可以冻结该 Session 的消息快照，压缩完成后再更新摘要。

这样就不会出现我们前面讨论的 V10、V12、V15 不断变化导致压缩失败的问题。

但要注意两个工程细节：

1. `RUNNING → COMPACTING` 的状态转换需要原子保护，不能让另一个任务趁机开始。
2. 如果压缩失败，Session 不能永远停留在 `COMPACTING`，必须有超时与恢复机制。

## 二、为什么我认为你的方案适合当前笔试？

因为你的笔试要求是实现一个最小可用 Agent，并不是开发一个支持百万用户并发的分布式平台。

你不需要一开始就实现复杂的异步压缩调度、版本合并、后台任务队列。

可以先采用：

**同一 Session 串行执行 + 同步压缩 + 压缩完成后恢复对话。**

优点是：

- 实现简单，容易测试。
- 状态一致性更容易保证。
- 压缩过程不需要频繁重试。
- 可以避免一部分消息顺序错乱的问题。

代价也很明显：压缩期间用户需要等待，压缩失败还会影响正常对话。

所以这是在用部分用户体验和吞吐量，换取实现复杂度和一致性的可控性。

你提到 Claude Code、Codex 的交互体验，也确实可以作为这种设计的参考。不过不能仅凭前端看起来暂停了，就认定它们内部完全使用同样的锁和状态机制。

---

## 三、我建议再做一个小优化

用户发送新消息时，不一定要直接拒绝。

例如：

```text
Session 状态：COMPACTING

用户：继续帮我检查登录模块

系统：收到消息，等待压缩完成
```

Runtime 可以把新消息放到等待队列。

压缩完成后：

```text
COMPACTING → IDLE → 处理等待消息
```

这样既保留了同一 Session 的串行语义，也减少了用户重新发送消息的麻烦。

不过，如果你希望第一版更简单，直接让前端暂时禁用输入也是可以接受的设计。

## 四、接下来一道追问：压缩失败怎么办？

假设你的 Session 已经使用了 120K Token，而模型最大支持 128K。

Runtime 开始压缩，锁定 Session。

但是负责生成摘要的 LLM API 突然超时。

此时：

- 旧 Context 仍然有 120K Token。
- 新摘要没有生成。
- Session 处于 `COMPACTING`。
- 用户还在等待继续聊天。

**你准备怎么处理？**

我希望你主要考虑两个问题：

1. 如何确保 Session 不会一直卡死在 `COMPACTING`？
2. 如果压缩反复失败，而旧 Context 又快超出窗口，怎样让 Agent 继续提供服务，同时尽可能保留关键状态？

这个问题解决以后，我们的 Context 压缩方案就基本形成完整闭环，可以整理成你笔试模块一的正式架构答案。

## 043 · 用户 · 2026-10-20T07:10:15.888000+00:00

配置熔断器吧嗯压缩反复失败上下文越快超了嗯这个时候啊我说实话有点棘手我想一想注意最坏的情况下就是停止服务嘛但是这个时候毕竟是最坏的情况嘛我想一想压缩不成功又没办法那就只能尽量多尝试那个工具呀工具结果压缩和那个噪音删除了然后的话没什么思路

## 044 · 助手 · 2026-10-20T07:10:30.287056+00:00

你的回答其实已经找到正确方向了。我尤其认可两个判断：

1. **不能无限重试压缩**，否则可能一直消耗 Token 和费用，却无法恢复服务。
2. **压缩失败不一定立即停止服务**，可以尝试不依赖 LLM 的降级方案，例如清理工具输出和删除确定无用的信息。

你现在缺少的是：如何把这些零散措施组织成一个**有优先级、有终止条件的故障恢复机制**。

我来补上这部分。

## 一、先纠正一个小问题：熔断器不是超时机制

你说配置熔断器，这个思路有价值，但它主要解决的是反复失败。

这三个概念需要区分：

| 机制 | 作用 | 例子 |
|---|---|---|
| Timeout（超时） | 防止单次请求永远不返回 | 压缩超过 15 秒就取消等待 |
| Retry（重试） | 处理短暂、可恢复的故障 | 网络超时后有限次数重试 |
| Circuit Breaker（熔断器） | 防止持续调用已经反复失败的服务 | 连续失败达到阈值后暂时停止请求 |

举例来说：

压缩模型 API 连续超时三次，熔断器进入 `OPEN` 状态，暂时不再请求它，而是进入降级流程。

不过在你的最小 Agent 项目里，**超时 + 有限重试 + 降级处理就足够了**。不必一开始就实现完整的熔断器状态机。

## 二、压缩失败后的分级降级

这是你可以直接用于架构题的思路。

<box border radius="lg" padding={3} gap={2}>
  <box background="surface-secondary" radius="md" padding={3} align="center">
    **正常 LLM 压缩失败**
    <text color="secondary" size="xs">超时、API 错误或摘要校验不通过</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} gap={1}>
    **Level 1：有限重试**
    <text color="secondary" size="sm">仅对可能恢复的错误重试，严格控制次数和总耗时</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} gap={1}>
    **Level 2：确定性压缩**
    <text color="secondary" size="sm">清理可重取的旧工具输出、无效消息和冗余数据，保留引用</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} gap={1}>
    **Level 3：最小 Context 重建**
    <text color="secondary" size="sm">从数据库重新加载任务状态、硬约束、最近消息及必要证据</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box background="rgba(217,119,6,0.07)" border={{size:1,color:"rgba(217,119,6,0.4)"}} radius="md" padding={3} gap={1}>
    **Level 4：安全暂停**
    <text color="secondary" size="sm">若仍无法构建可靠且符合预算的 Context，就暂停当前任务，保存进度并报告原因</text>
  </box>
</box>

最重要的是 Level 3。

你之前设计的“Event Log + State Table”在这里发挥了真正的作用。

即使 LLM 摘要失败，也不必完全依赖旧的 120K Token 历史。

Runtime 可以重新构造：

```yaml
goal: 修复登录系统

constraints:
  - 不允许修改其他数据库表结构
  - 允许新增 last_login_at

verified_state:
  login_implemented: true
  tests_passed: false

next_action:
  - 检查失败的单元测试

recent_messages:
  - 用户要求继续修复

references:
  - artifact://test-report-001
  - artifact://source-snapshot-001
```

这不是重新让模型总结历史，而是**从结构化数据中直接重建执行所需的 Context**。

当然，前提是这些数据已经经过可靠的状态更新和持久化，不能把未经验证的模型判断当作事实。

---

## 三、真正重要的设计：压缩必须是非破坏性操作

这里我想补充一个你之前没有明确提到，但对工程可靠性很重要的原则。

**新摘要验证成功之前，不能删除或覆盖唯一的原始历史。**

你可以把压缩设计成类似事务提交：

<box gap={2}>
  <row align="center" gap={2}>
    <icon name="database" color="secondary"/>
    **读取旧状态与历史快照**
  </row>
  <row align="center" gap={2}>
    <icon name="file-plus" color="secondary"/>
    **生成候选摘要**
  </row>
  <row align="center" gap={2}>
    <icon name="shield-check" color="secondary"/>
    **校验关键约束、状态版本、引用有效性**
  </row>
  <row align="center" gap={2}>
    <icon name="git-commit" color="success"/>
    **验证通过后，原子切换到新 Context**
  </row>
  <row align="center" gap={2}>
    <icon name="history" color="secondary"/>
    **保留原始历史作为恢复依据**
  </row>
</box>

这样即使压缩过程中 LLM 超时、程序崩溃，也不会直接丢失 Session 的原始信息。

这跟你熟悉的数据库事务思想是相通的。

---

## 四、到这里，我们的 Context 架构题已经可以收束了

回顾你自己提出的方案，我们实际上形成了以下设计：

| 架构问题 | 你的核心选择 |
|---|---|
| 为什么压缩 | 降低成本，提高有效信息密度 |
| 压缩什么 | 历史消息、冗余工具输出 |
| 如何压缩 | 任务感知压缩 + Git Log 式历史记录 |
| 如何保持任务状态 | Event Log + 持久化 State Table |
| 什么时机压缩 | 结合 Token 预算和任务状态动态触发 |
| 并发怎么处理 | 同一 Session 压缩期间串行化执行 |
| 如何保证一致性 | 状态版本检查、关键状态验证 |
| 压缩失败怎么办 | 有限重试、确定性降级、状态重建、安全暂停 |

这是一个比较完整的架构方案。它的优势不是使用了多复杂的技术，而是你能够解释每项设计针对什么问题，以及付出了什么代价。

## 五、最后一道综合问题

这次不再引入新机制，我想检查你是否真正把整个方案串起来。

假设你设计的 Agent：

- Context 上限是 128K。
- 当前已经使用 110K。
- 用户要求继续修复一个尚未完成的 Bug。
- 数据库中保存了任务目标、用户约束和当前测试状态。
- 原始工具输出已保存在磁盘。
- LLM 压缩服务连续超时。

**现在请你作为 Runtime 的设计者，描述从检测到 Context 不足，到最终恢复 Agent 执行的完整流程。**

不需要代码，用 5～8 个步骤说清楚即可。

这一轮回答完，我们就可以正式结束模块一第 2 题的训练，把你自己的架构思想整理成笔试提交版，再进入模块二 Memory。

## 045 · 用户 · 2026-10-20T07:15:48.281000+00:00

首先的话我会这么决定先简单分析一下这个任务的token的消耗量如果消耗量不大就是在剩余的那个那个那个那个的那个阈值上可以完成的话就暂时不不那个嗯那个压缩但是我个人的倾向是在上下文到达了80%的阈值的时候我会倾向于去压缩但是是比较轻量的压缩因为我们很难真的很难去去确确认它后续消耗会消耗多少所以说呢80%的时候做一些轻量确定性的压缩呃比如说呀把那些工具结果的输出呃大量的那个工具结果的输出呃存储在那个磁盘上只保留一些关键的摘要然后还有就是噪音删除然后呢如果清呃确定性的清单压缩还不够的话就那个结构性压缩就类似git log嗯嗯然后呢这里要有一个状态表记录当前状态和接下来呃的那个运行状态而且原文必须保存好方便回厂如果结构大家说还不够的话那就只能进行一次全量化压缩由模型推动的全量化压缩类似于git square但是呢为了防止模型不断的压缩失败以及不断的调用压缩这里必须要设置垄断七还有就是嗯差不多吧就差不多这样子了最终恢复的话就是要那个核查那个状态吧

## 046 · 助手 · 2026-10-20T07:16:06.133626+00:00

这次我给你 **92/100**。你已经能够独立提出一套比较完整的 Context 压缩方案，而且不是简单地堆技术名词，而是根据成本、性能、可靠性进行分层设计。

特别是你刚刚补充的观点：

**在 80% 左右先进行低成本的确定性压缩，而不是等到 Context 快满了才调用 LLM 进行昂贵的全量压缩。**

这是很合理的工程思路。

不过，我认为你还需要完善三个细节，才能让这套架构真正具备可落地性。

## 一、你这套方案已经形成了清晰的三级压缩策略

<box border radius="xl" padding={3} gap={3}>
  <box gap={1}>
    <text weight="medium">Context 使用量与压缩策略</text>
    <text color="secondary" size="xs">128K 窗口，以下阈值仅为设计示例</text>
  </box>
  <box gap={1}>
    <row justify="between" align="center">
      <text size="sm">正常区间</text>
      <text color="secondary" size="xs">0–80%</text>
    </row>
    <box background="surface-tertiary" height="12px" radius="full" clip>
      <box width="80%" height="100%" background="#0d9488" radius="full"/>
    </box>
    <text color="secondary" size="xs">正常执行，监控 Token 和预计增长量</text>
  </box>
  <box gap={1}>
    <row justify="between" align="center">
      <text size="sm">Level 1：轻量确定性压缩</text>
      <text color="secondary" size="xs">约 80% 时考虑</text>
    </row>
    <box background="surface-tertiary" height="12px" radius="full" clip>
      <box width="80%" height="100%" background="#d97706" radius="full"/>
    </box>
    <text color="secondary" size="xs">工具输出外置、清理可确认的冗余内容、保留原文引用</text>
  </box>
  <box gap={1}>
    <row justify="between" align="center">
      <text size="sm">Level 2：结构化历史压缩</text>
      <text color="secondary" size="xs">Level 1 不足时</text>
    </row>
    <box background="surface-tertiary" height="12px" radius="full" clip>
      <box width="90%" height="100%" background="#d97706" radius="full"/>
    </box>
    <text color="secondary" size="xs">归档事件，保留目标、约束、状态变化和下一步行动</text>
  </box>
  <box gap={1}>
    <row justify="between" align="center">
      <text size="sm">Level 3：LLM 全量压缩</text>
      <text color="secondary" size="xs">最后手段</text>
    </row>
    <box background="surface-tertiary" height="12px" radius="full" clip>
      <box width="96%" height="100%" background="#dc2626" radius="full"/>
    </box>
    <text color="secondary" size="xs">基于任务目标重建紧凑摘要，验证成功后才切换</text>
  </box>
</box>

这里需要特别说明：80% 是你设定的**软触发阈值**，不是模型的安全上限。如果预计一次工具输出就会超过预算，即使当前只使用 60%，也应该提前处理。

另外，你提到的 Git Log 式归档不是完全不使用 LLM，而是以事件为单位进行结构化记录，尽量避免反复将所有历史合并成一份摘要。它与 Git Squash 式的全量合并相比，更有利于保留状态变化轨迹。

## 二、三个需要修正的地方

### 1. 你没有明确处理“压缩服务持续失败”的情况

题目设定中，LLM 压缩服务已经连续超时。

因此，不能简单进入 Level 3 后继续调用同一个失败服务。

你的熔断器思路是正确的，但还需要指定降级路径：

**正常摘要不可用 → 优先确定性清理 → 从已持久化状态重建最小 Context → 如果仍不安全，则暂停任务。**

真正重要的是确保 Agent 即使不依赖压缩模型，也有机会从数据库恢复当前任务。

### 2. 原始历史与摘要需要分开管理

你说原文必须保存，方便回查，这是非常重要的一点。

但要区分：

- **Event Log**：记录实际发生的事件、工具调用和证据。
- **State Table**：存放经验证的当前目标、约束和执行状态。
- **Context Summary**：面向 LLM 的压缩视图。

其中 Context Summary 可以被重新生成，不能作为唯一事实来源。

### 3. 压缩完成后，不能只检查状态表

例如摘要写着“测试已通过”，但状态表记录的是“测试失败”，就必须拒绝这个摘要。

更进一步，压缩验证还应检查：

- 状态版本是否仍然有效。
- 用户的重要约束有没有丢失。
- 工具调用和结果是否保持正确配对。
- 原始证据引用是否仍可读取。
- 新 Context 加上预留 Token 后是否真的低于预算。

通过这些检查以后，才能让 Session 恢复执行。

---

## 三、整理成你自己的架构设计答案

下面这版保留了你提出的主要思路，并补足了必要的工程细节。它可以作为笔试答案的初稿，但建议最终由你再按自己的理解修改。

<WritingBlock id="58321" variant="document">### 模块一·第 2 题：200 轮 Session 的 Context 压缩设计

我会采用**动态预算管理 + 分级压缩 + 状态持久化 + 压缩后校验**的架构。

**1. 压缩目标**

Context 压缩不仅是为了解决 Token 上限和成本问题，也是为了提高有效信息密度，将历史中反复需要推理的结论转化为可以直接使用的任务状态。

但压缩是有损操作，不能为了缩短上下文而牺牲用户目标、关键约束和执行正确性。

**2. 触发机制**

结合当前 Token 用量、剩余预算、预计工具输出和任务阶段动态判断。

可以将 80% 作为软触发阈值，优先进行轻量压缩；同时为模型输出和工具结果预留安全空间，避免单次大型工具输出导致窗口溢出。

**3. 分级压缩**

- Level 1：确定性压缩。将大型工具输出持久化，Context 只保留关键内容和可回查的引用；清理已确认无用的冗余信息。
- Level 2：结构化归档。类似 Git Log，按事件记录目标变化、关键决策、工具结果和任务进展，保留历史的因果关系。
- Level 3：全量压缩。当上述策略无法满足预算时，由 LLM 根据当前任务目标生成更紧凑的摘要，类似 Git Squash。

**4. 状态与历史管理**

将 Event Log、State Table 和 Context Summary 分开维护。

Event Log 保留原始事件与证据；State Table 保存当前目标、用户约束、已完成工作和待执行动作；Context Summary 只是提供给 LLM 的上下文视图。

压缩不应改变已经验证的任务事实，历史记录也不应因为压缩而永久丢失。

**5. 并发一致性**

同一 Session 压缩期间暂停新的任务执行，必要时将新消息排队。不同 Session 可以继续运行。

压缩时记录状态版本，提交前检查版本及关键状态，避免过期摘要覆盖新信息。压缩成功并完成校验后再原子切换 Context。

**6. 失败恢复**

为压缩操作设置超时、有限重试及熔断机制。

当 LLM 压缩持续失败时，优先通过确定性清理和持久化状态重建最小 Context。

如果仍无法安全执行，则保存任务进度并暂停，明确告知用户原因，而不是无限重试或丢弃关键约束。

**7. 压缩质量验证**

压缩前后对比任务目标、重要约束、验收状态、未完成事项和证据引用。

验证通过后恢复执行；验证失败时保留旧数据并尝试修复或降级。

最终目标是：**Context 可以被压缩甚至重建，但 Agent 对当前任务的理解和已验证状态不能因此发生错误变化。**</WritingBlock>

## 四、最后给你一个重要反馈

我们这道题已经讨论得足够深入了。我认为你真正形成了三个值得保留的架构观点：

**第一，Context 不等于 Memory，也不等于真实任务状态。** Context 是模型当前能看到的信息，状态和历史应该有独立的存储与管理机制。

**第二，压缩不是单一算法，而是一套有成本梯度的策略。** 先做确定性的低成本处理，再使用昂贵的 LLM 压缩。

**第三，可靠性不是要求 Agent 永远不失败，而是发生错误时能够检测、恢复和安全停止。**

这三点可以作为你在架构面试中回答追问的核心思路。

至于评分，我认为你的架构方向已经达到较好的水平，但“压缩策略、状态持久化、版本一致性和失败恢复”能否真正落地，还需要代码与测试证明。

**下一步建议进入模块二 Memory 的第 1 题：**

> 和聊天 Agent 熟悉半个月后，用户问了一个以前问过的问题，Agent 如何做 Memory 召回更合理？

这道题和我们刚讨论的 Context 压缩有紧密联系，但会引入一个新的核心问题：**Agent 应该记住什么、何时召回、以及怎样判断过去的记忆现在是否仍然可信。**

## 047 · 用户 · 2026-10-20T07:24:38.962000+00:00

这一块我的理解其实没有那么深我对memory的主要的理解就是长期记忆可以适当的保存就是比较有呃有价值的信息比如说用户画像啊呃个人喜好啊这种是可以作为长期记忆来储存的这种然后呢怎么召回什么时候召回然后怎么召回吗我倾向于用IGI不对IAG之前先用那种结构化的信息呃类似于那个cloud的memory其实那个文档我用这里有几种组织记忆的方式（之前看到的感觉很有道理）：**Simple Notes** 体现极简主义设计，每条记忆是一个最小的、不可再分的事实（如 “用户邮箱：[[REDACTED_EMAIL]](mailto:[REDACTED_EMAIL])”）。其优势是开销极低，支持 O(1) 操作（即耗时固定、不随数据量增长的操作）。但信息关联性完全丢失——“在 TechCorp 担任高级工程师，负责推荐系统开发” 被分解为三个独立事实（“在 TechCorp 工作”、“职位是高级工程师”、“负责推荐系统”），同一份工作的内在联系被割裂。处理需要综合多条信息才能回答的查询时，系统需要重新拼凑碎片。
**Enhanced Notes** 采用整体视角，将每条记忆保存为包含完整上下文的段落。例如，同样的工作信息可以存储为：“用户在 TechCorp 担任高级软件工程师，专注于机器学习已有三年，目前领导一个推荐系统项目，团队 5 人。”这种表示保留了信息的叙事结构，确保语义完整而丰富。但代价是存储冗余（相同信息在多个段落中重复）、更新复杂（属性变化需重写多个段落）。
**JSON Cards** 采用三层嵌套结构（类别→子类别→键值对，如 personal.contact.email、work.position.title），模拟人类对信息的分类方式。它支持部分更新（修改 work.position.title 不影响 work.company.name），可预测且可扩展。但刚性结构假设信息可清晰分类——“周末用 Python 开发个人项目” 同时涉及时间偏好、技术偏好和活动类型，强制归入单一类别会丢失多维性。
**Advanced JSON Cards** 代表了记忆系统从信息存储到知识管理的范式转变。每张卡片不仅记录事实，还加入信息来源的叙事背景（backstory）、主体身份（person）、与用户的关系（relationship）和时间戳。这背后的核心思想是：同一条信息在不同场景下可能有完全不同的含义——“张医生”可能是用户自己的牙科医生，也可能是用户父亲的心脏科医生，脱离了具体情境就无法正确理解。嗯何时撤回这个也是个核心点怎我知道什么时候需要长期记忆呢这个把嗯很值得思考的一个问题怎么知道这里哎这里我就涉及到了一点测评与进化我个人认为这是呃这里的测评与进化就是呃每隔一定轮次用一个模型去扫描之前的会话提取核心内容与知识点然后呢评估模型有没有正确的执行任务有没有呃按照用户的意图来完成任务每一步有没有正确流程有没有正确这个时候我感觉就是可以注意一些记忆的那个存取然后何时召回这个点我还是有点小魔核就是很难说清楚到底何时召回根据需要我把但是什么时候才是需要的时候呢我倾向于啊我个人倾向于是把一些我也是参考了cloud code memory机制就是把一些简单摘要放进模型的静态上下文然后如果模型判断的时候就去索引呃完整的内容嗯然后就是记忆更新吧这也是一个很重点的内容记忆更新的话其实跟我哎呦哎这里其实思路其实我感觉也是一致的嗯可回查可回溯的原始内容佳佳当前的状态加当前的上下文来考虑嗯具体的话我感觉我得需要跟你讨论一下

## 048 · 助手 · 2026-10-20T07:24:53.216543+00:00

我觉得你已经抓住了 Agent Memory 的几个核心问题，只是还没有把它们串成一个完整的系统。

你提到的 Simple Notes、Enhanced Notes、JSON Cards、Advanced JSON Cards，实际上是在回答一个问题：

**记忆应该以什么形式存储？**

但一个真正可用的 Agent Memory 系统，还需要解决另外四个问题：

1. **Write**：什么信息值得写入长期记忆？
2. **Retrieve**：什么情况下应该召回记忆？
3. **Update**：新信息与旧记忆冲突时，怎么更新？
4. **Forget**：什么信息应该过期、删除或不再使用？

我想先帮你建立一个完整的直观模型，然后集中讨论你最困惑的“什么时候召回”。

## 一、先区分三种不同的信息

假设你有一个陪伴用户半个月的 Agent。

用户之前告诉它：

> 我喜欢通过实际项目学习技术，不喜欢单纯背概念。

后来又说：

> 我正在开发一个 Agent Harness 项目，想完成技术笔试。

再后来：

> 昨天运行的第 38 个测试失败了。

这三条信息的价值并不相同。

| 信息 | 更合适的存放位置 | 原因 |
|---|---|---|
| 喜欢项目驱动学习 | 长期 Memory | 未来很多学习任务都可能用到 |
| 正在开发 Agent Harness | 项目 / 任务记忆 | 与特定项目和持续任务有关 |
| 昨天第 38 个测试失败 | 任务状态 / 事件记录 | 需要根据最新测试结果更新 |

这里最重要的是：

**不是所有值得保存的信息都应该写进用户画像。**

否则长期使用后，用户画像会越来越庞大，充满过期项目、临时任务和历史错误。

我更倾向于把记忆分成三层。

<box border radius="xl" padding={3} gap={2}>
  <box background="surface-secondary" radius="lg" padding={3} gap={1}>
    <row align="center" gap={2}>
      <icon name="user-round" color="secondary"/>
      **长期稳定记忆**
    </row>
    <text size="sm">用户偏好、长期目标、相对稳定的个人信息</text>
    <text size="xs" color="secondary">例如：偏好通过代码和真实项目学习</text>
  </box>
  <box background="surface-secondary" radius="lg" padding={3} gap={1}>
    <row align="center" gap={2}>
      <icon name="folder-open" color="secondary"/>
      **项目 / 任务记忆**
    </row>
    <text size="sm">技术决策、架构约束、进展、下一步行动</text>
    <text size="xs" color="secondary">例如：Agent Harness 使用自实现 Runtime</text>
  </box>
  <box background="surface-secondary" radius="lg" padding={3} gap={1}>
    <row align="center" gap={2}>
      <icon name="history" color="secondary"/>
      **事件 / 历史记录**
    </row>
    <text size="sm">完整对话、工具结果、证据和时间信息</text>
    <text size="xs" color="secondary">例如：某次测试失败的完整错误日志</text>
  </box>
</box>

这三层并非完全互斥。长期记忆可以引用项目记忆，项目记忆又可以引用原始事件。

---

## 二、你提出的四种记忆组织方式，怎么选择？

我不认为应该选一种，然后让所有记忆都使用相同结构。

可以把它们当作不同的存储粒度。

| 方式 | 最适合的内容 | 主要问题 |
|---|---|---|
| Simple Notes | 独立事实、简单偏好 | 关联信息容易丢失 |
| Enhanced Notes | 经验、决策背景、复杂经历 | 更新和去重困难 |
| JSON Cards | 明确的状态和结构化属性 | Schema 不够灵活 |
| Advanced JSON Cards | 需要来源、时间、关系的长期记忆 | 管理成本更高 |

其中我想纠正一个小点：Simple Notes 并不天然保证 O(1) 的检索复杂度。按 ID 从哈希表获取单条记录可能接近 O(1)，但从大量笔记中找到与当前问题相关的记忆，仍需要索引或搜索。

**对你正在设计的 Memory 系统，我更建议混合结构：**

- 用 JSON / 数据库字段保存需要精确更新的状态。
- 用文本笔记保存难以结构化的经验和背景。
- 用引用和时间戳把它们关联到原始事件。

这与我们之前讨论的 Event Log + State Table 非常契合。

## 三、你最困惑的：什么时候应该召回 Memory？

这里我想提出一个区分：

**记忆检索的触发时机，与记忆是否值得放进 Context，是两个不同的决策。**

比如用户问：

> 我之前给 Agent Harness 选择了什么数据库？

这是一个明显依赖历史信息的问题。

而用户问：

> Python 的 `dict` 是什么？

这个问题通常不需要检索用户长期记忆。

再例如：

> 用我习惯的方式讲解 Python 的 `dict`。

这虽然不是询问历史事实，但用户的学习偏好会影响回答方式，因此可能需要相关记忆。

所以我倾向于将召回设计成三条路径。

<box border radius="xl" padding={3} gap={2}>
  <box background="surface-secondary" radius="lg" padding={3} gap={1}>
    **路径 A：始终可见的核心记忆**

    少量稳定、使用频繁的信息，在构建 Context 时自动加入。

    <text color="secondary" size="sm">例如用户偏好、长期项目背景的简短索引。</text>
  </box>
  <box border radius="lg" padding={3} gap={1}>
    **路径 B：Runtime 自动检索**

    如果用户问题明显依赖历史事实，Runtime 在调用主模型之前先检索相关记忆。

    <text color="secondary" size="sm">例如“我上周决定使用哪个数据库？”</text>
  </box>
  <box background="surface-secondary" radius="lg" padding={3} gap={1}>
    **路径 C：LLM 主动调用检索工具**

    当模型在执行过程中发现缺少背景，可以调用 `search_memory`，按需获取更完整的信息。

    <text color="secondary" size="sm">例如“上次为什么放弃了 Redis 方案？”</text>
  </box>
</box>

你提到的“核心摘要始终可见，完整信息按需索引”，与 <Entity category="organization" value="Letta" disambig="开源有状态 AI Agent 和记忆框架"/> 的 Core Memory 和 Archival Memory 思路相近。Letta 通过持久化 Memory Blocks 保存常驻上下文，并提供归档记忆搜索；其 MemFS 还使用 Git 支持记忆的版本管理。<Cite refs={["turn104260search3","turn104260search8","turn104260search1"]}/>

而 <Entity category="software" value="LangGraph" disambig="LangChain 的 Agent 编排框架"/> 将长期记忆与 Session 内的短期记忆分开，也讨论在交互路径内更新记忆和后台更新记忆的不同取舍。<Cite ref="turn104260search2"/>

### 那么如何判断“需要检索”？

最小可用版本不用一开始就让 LLM 做复杂分类。

可以先设计以下规则：

| 情况 | 是否检索 | 原因 |
|---|---|---|
| 用户明确说“以前、上次、记得吗” | 是 | 明确依赖历史 |
| 用户询问个人偏好 | 是 | 需要长期用户信息 |
| 当前任务引用过去项目决策 | 是 | 需要项目背景 |
| 普通知识性问题 | 通常否 | 不依赖历史 |
| 模型发现信息不足 | 按需检索 | 避免凭空猜测 |

这些规则只是第一版的触发器。真实对话中，是否依赖历史往往不能仅靠关键词判断，所以后续可以结合意图识别、检索置信度和成本预算改进。

更重要的是：**即使检索到了记忆，也不意味着一定应该使用它。**

例如，半个月前用户喜欢 Python，但昨天明确表示现在更希望使用 C++。

你需要考虑记忆的时间、来源、适用场景和是否已被更新。

---

## 四、关于你提出的“每隔几轮扫描并评估”

你的想法里其实包含两个不同系统：

**Memory Consolidation（记忆整理）**：定期扫描历史，提取值得长期保存的事实、偏好、决策和经验。

**Agent Evaluation（Agent 评测）**：判断 Agent 是否按照用户目标完成任务，分析错误，并改进未来的执行策略。

两者可以共享 Trace 和历史记录，但不要完全混在一起。

例如，Agent 某次调用工具失败了，并不意味着应该将“这个工具永远不能使用”写成长期记忆。

如果经过多次验证发现特定工具在某类参数下容易失败，可以把这个经验保存为带适用条件的程序性记忆。

而且，**固定每 10 轮提取一次记忆并不是唯一选择**。更合理的设计可能是事件触发与定期整理结合，例如用户明确表达长期偏好时立即产生候选记忆，其他信息在后台批量整理。

---

## 五、进入我们的 Memory 架构面试

我先不继续讲数据库、向量检索或 Embedding，因为你当前的主要问题并不在这些技术上，而在**如何决定什么值得记住、什么时候应该使用**。

我们用一个具体场景来推演。

假设用户与 Agent 对话了半个月：

- 第 1 天：“我喜欢使用 Python 开发项目。”
- 第 5 天：“我正在学习 C++，目前更希望使用 C++ 完成项目。”
- 第 10 天：“这个 Agent Harness 项目必须使用 Python。”
- 第 15 天：“帮我实现一个新的工具注册机制。”

现在你设计的 Memory 系统检索出这三条记录。

**问题：Agent 应该选择 Python 还是 C++ 实现新的工具注册机制？为什么？**

更重要的是：

你会怎样设计记忆的组织与召回逻辑，让模型知道第 5 天的信息更新了用户的一般偏好，但第 10 天的信息是针对特定项目的约束，两者并不一定冲突？

这次先不谈代码，你尝试从**记忆的作用范围、时间和优先级**三个角度分析。

## 049 · 用户 · 2026-10-20T07:33:17.012000+00:00

Adrian肯定得选择Python吧因为用户明确规定了那个agent必须使用Python这个规定已经规定下来了嗯组织结构的话我倾向于这样子吧全局记忆项目记忆和上下文记忆全局意义就是所有项目都能看懂的记忆而项目记忆只有在特定的项目里面的记忆嗯那么这里还有个虽然说这里分类可以解决一定的问题但是本质的问题还是那个记忆冲突和记忆找回吧记忆上一季一冲突的话感觉得根据大部分时间来可以根据时间线嗯这个哎这个其实冲突有点像像刚刚上面讨论过的压缩的那个状态更新啊很香啊感觉思路差不多我还是对记忆找回这一点存在疑问虽然说我之前有深入学过a机这个模型判断吧但是吧模型如果不知道有没有关于这个的记忆的记录的话那他怎么知道要不要去收呢如果固定拒收的话又太浪费了嗯

## 050 · 助手 · 2026-10-20T07:33:30.514304+00:00

你这次提出了一个非常关键的问题：

> **如果模型不知道记忆库里有没有相关信息，它怎么知道自己应该检索？如果每次都检索，又会浪费资源。**

这就是 Agent Memory 架构中很核心的 **Retrieval Trigger（检索触发）问题**。

而且你提出的“全局记忆 + 项目记忆 + 上下文记忆”已经是一个合理的分层方案。

先说结论：

**不要把“是否检索 Memory”的决定完全交给 LLM，也不要每一轮都进行完整的记忆搜索。更合理的是使用分层记忆、轻量检索路由和按需深入检索相结合的方案。**

## 一、先评价你的记忆分层

你提出三个层次：

| 记忆层 | 作用 | 举例 |
|---|---|---|
| 全局记忆 | 跨项目使用的长期偏好与背景 | 用户现在总体更喜欢 C++ |
| 项目记忆 | 只在特定项目内生效 | Agent Harness 必须使用 Python |
| 上下文记忆 | 当前 Session 的对话与任务状态 | 正在实现 ToolRegistry |

在刚才的例子中，应该使用 Python，因为项目级明确约束比一般的编程语言偏好更具体。

这里有一个需要修正的认识：

**记忆冲突并不总是靠时间线解决。**

例如：

- 昨天：“我喜欢 C++。”
- 半个月前：“Agent Harness 项目必须使用 Python。”

昨天的信息更新，但它没有明确取消项目约束。

所以不能简单采取“最新记录覆盖旧记录”。

更合理的判断顺序是：

1. 先判断两条记忆是否讨论同一件事。
2. 再检查各自的作用范围。
3. 判断新信息是否明确修改了旧约束。
4. 最后才考虑时间、来源与可信度。

项目约束和全局偏好可以同时成立，根本不需要判定为冲突。

---

## 二、模型不知道有哪些记忆，怎么决定检索？

这里有一个容易忽视的设计：

**模型不需要先知道具体记忆内容，才能判断当前问题可能需要历史信息。**

我们把两个问题分开：

- **是否需要过去的信息？** 可以通过当前用户输入、任务类型和少量元信息来判断。
- **过去的信息具体是什么？** 需要真正检索记忆库才知道。

举个例子：

用户说：

> 上次我们为什么决定使用 SQLite？

模型不需要知道数据库里是否有这条记忆，也能判断这个问题依赖历史记录。

但是：

> 帮我设计数据库。

这时模型可能不知道用户之前已经讨论过 PostgreSQL。

如果完全依赖模型主动检索，就可能漏掉重要约束。

这也是为什么纯粹的 LLM 自主 Tool Calling 不能充分解决 Memory Recall。

## 三、一个更实用的架构：三阶段召回

<box border radius="xl" padding={3} gap={2}>
  <box background="surface-secondary" radius="md" padding={3} align="center">
    **用户输入 + 当前 Session / 项目 ID**
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} gap={1} align="center">
    **第一阶段：轻量 Memory Router**
    <text color="secondary" size="xs">判断是否依赖历史、需要哪类记忆</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <grid columns={2} gap={2}>
    <grid-item>
      <box border radius="md" padding={3} gap={1} align="center">
        <icon name="book-open" color="secondary"/>
        **常驻核心记忆**
        <text size="xs" color="secondary" textAlign="center">少量用户偏好、当前项目约束</text>
      </box>
    </grid-item>
    <grid-item>
      <box border radius="md" padding={3} gap={1} align="center">
        <icon name="search" color="secondary"/>
        **按需检索**
        <text size="xs" color="secondary" textAlign="center">历史事实、决策、经验、原始证据</text>
      </box>
    </grid-item>
  </grid>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} gap={1} align="center">
    **第二阶段：检索结果筛选**
    <text color="secondary" size="xs">相关性、作用范围、有效时间、来源及冲突</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box background="rgba(13,148,136,0.08)" border={{size:1,color:"rgba(13,148,136,0.4)"}} radius="md" padding={3} align="center">
    **第三阶段：组装 Context → LLM**
  </box>
</box>

最关键的是第一阶段不一定需要调用一个昂贵的大模型。

例如可以先利用：

- 当前任务是否属于已有项目。
- 用户是否明确引用过去的事情。
- 是否存在与当前项目关联的关键约束。
- 当前 Context 是否已经包含所需信息。
- 是否有正在执行的长期任务。

如果这些条件已经能确定需要什么，就直接加载；如果不能确定，再用轻量分类器或模型辅助决策。

还有一种实际可行的折中方式：**每轮做低成本候选检索，但不是每轮都让大模型阅读完整记忆。**

例如用关键词索引、向量索引或项目 ID 过滤获取候选项，只有候选项达到相关性标准时，才读取完整内容。

这不等于每轮进行一次昂贵的 LLM 记忆检索。

## 四、这不是纯理论，生产框架也在采用类似思路

目前有几个值得你学习的实现：

- <Entity category="software" value="Mem0" disambig="面向 AI Agent 的长期记忆框架"/>：支持按用户、会话等条件过滤，通过语义搜索、相似度阈值和可选重排选择相关记忆。它的文档也建议在生成回答前检索相关记忆。<Cite refs={["turn936838search0","turn936838search7"]}/>
- <Entity category="software" value="Letta" disambig="有状态 AI Agent 与记忆管理框架"/>：区分 Core Memory Blocks 与可搜索的 Archival Memory，体现了“少量常驻 + 大量按需搜索”的思路。<Cite refs={["turn936838search5","turn936838search8"]}/>
- <Entity category="software" value="LangGraph" disambig="LangChain 开源 Agent 编排框架"/>：支持通过 Namespace 对不同用户或项目的数据进行隔离，并可配置语义搜索索引。<Cite refs={["turn936838search4","turn936838search6"]}/>

这里必须说明：**它们提供了不同的记忆存储与检索能力，不代表业界已经证明某一种触发策略绝对最优。**

我更推荐你从这些框架中学习设计思想，再根据自己 Agent 的任务特点做取舍。

## 五、接下来一道核心面试题：如何处理隐式记忆需求？

假设你采用了刚才的三阶段方案。

用户今天说：

> 帮我推荐一个适合我的开源 Agent 项目。

历史记忆里有两条：

```yaml
memory_001:
  content: 用户喜欢深入研究 Agent 底层实现
  scope: global

memory_002:
  content: 用户更喜欢 C++，但也能阅读 Python
  scope: global
```

用户这次**没有说“按照我以前的偏好”**，也没有要求回忆历史。

如果 Router 判断这是普通技术推荐，不检索 Memory，就可能推荐一个完全不符合用户学习需求的项目。

但如果每次推荐都搜索全部记忆，又会增加不必要的开销。

**我的追问是：**

你会如何让 Memory Router 识别这种“用户没有明确要求回忆，但历史偏好可能影响答案”的情况？

你可以考虑：是否应该按任务类型决定检索哪些记忆类别，而不是简单地判断“检索或不检索”。

这个问题回答清楚后，我们就可以把 Memory 的召回机制整理成一套可实现的设计，然后继续讨论记忆写入、更新和遗忘。

## 051 · 用户 · 2026-10-20T07:37:32.599000+00:00

我会嗯我认为按照任务类型来判断解锁哪一些记忆类别吧这里其实我感觉也有个问题啊你记忆的话你肯定感觉得提前存储吧你不可能觉得你需要你需要memory的时候去全量搜索所有的对话记录然后这里我对呃gpt的那个movie很感兴趣因为我用gpt的网页版聊天我发现了他一个与别的网页版AI的与众不同的功能就是他有记忆而且呢记得其实还挺挺准挺好的我很好奇它里面是怎么实现的

## 052 · 助手 · 2026-10-20T07:37:47.629130+00:00

你观察到的重点很有价值：记忆不能等到需要时才从全部历史里临时提取。我会先核对 OpenAI 目前公开披露的 Memory 工作机制，再区分哪些是已知产品行为、哪些只能从工程角度推测。

## 053 · 助手 · 2026-10-20T07:37:56.357044+00:00

你这个问题问得非常好，而且我找到了一份特别值得你阅读的官方技术文章。

**2026 年 6 月 4 日，OpenAI 正式介绍了 ChatGPT 新一代记忆系统：Dreaming（记忆整合）。**

它恰好回答了你关心的几个问题：

- 为什么 ChatGPT 可以跨越很多次聊天记住你的信息？
- 它是不是每次都重新搜索全部历史？
- 什么时候提取记忆？
- 如何避免记忆越来越多、越来越过时？

官方文章：[Dreaming: Better memory for a more helpful ChatGPT](https://openai.com/index/chatgpt-memory-dreaming/) <Cite ref="turn640165search5"/>

这次我们不只讨论概念，我想带你从 Agent 架构设计的角度理解它。

## 一、ChatGPT Memory 其实经历了三代演进

<box gap={3}>
  <row align="start" gap={3}>
    <box background="surface-secondary" radius="lg" padding={2}>
      <icon name="notebook-pen" size="lg"/>
    </box>
    <box flex="1" gap={1}>
      <text weight="medium">2024：Saved Memories（显式记忆）</text>
      用户明确说“记住这件事”，或者模型识别出明显值得保存的信息，形成独立记忆。

      **问题：** 像记事本一样，只保存少量事实，容易遗漏自然对话中的重要信息，也可能逐渐过时。
    </box>
  </row>
  <divider color="subtle"/>
  <row align="start" gap={3}>
    <box background="surface-secondary" radius="lg" padding={2}>
      <icon name="brain" size="lg"/>
    </box>
    <box flex="1" gap={1}>
      <text weight="medium">2025：Dreaming V0（后台整理）</text>
      引入后台流程，从多次历史对话中整理有价值的信息，不再依赖用户主动提出记忆请求。

      **意义：** 从“记录用户告诉我的事实”变成“持续归纳用户的偏好、项目与背景”。
    </box>
  </row>
  <divider color="subtle"/>
  <row align="start" gap={3}>
    <box background="surface-secondary" radius="lg" padding={2}>
      <icon name="layers" size="lg"/>
    </box>
    <box flex="1" gap={1}>
      <text weight="medium">2026：Dreaming V3（持续整合）</text>
      重点改进时效性、正确性、跨对话连续性和规模化成本。

      **意义：** Memory 不再只是越存越多，而是会随着时间和新信息持续更新。
    </box>
  </row>
</box>

以上演进是 OpenAI 已公开的信息。<Cite ref="turn744101view0"/>

最有意思的是：**你刚才独立提出的“提前存储有价值信息，而不是每次全量扫描历史”，与 Dreaming 的公开设计方向高度一致。**

## 二、Dreaming 的核心：把记忆整理移到后台

传统做法可能是：

<box border radius="lg" padding={3} gap={2}>
  <text weight="medium" color="secondary" size="xs">低效设计：每次查询时重新处理全部历史</text>
  <box background="surface-secondary" radius="md" padding={3} align="center">用户提出问题</box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">扫描过去 500 次对话</box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">重新提取相关信息</box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box background="surface-secondary" radius="md" padding={3} align="center">生成回答</box>
</box>

如果每次都这样做，延迟和成本显然难以接受。

而 Dreaming 的公开思路，是**在用户正常聊天之外，通过后台流程持续整理记忆**，让未来的对话可以利用已经形成的记忆状态。<Cite ref="turn640165search3"/>

从工程角度，我们可以把它抽象成两条路径：

<box border radius="xl" padding={3} gap={2}>
  <grid columns={2} gap={2}>
    <grid-item>
      <box gap={2}>
        <text weight="medium" size="sm" textAlign="center">后台：写入路径</text>
        <box background="surface-secondary" radius="md" padding={2} align="center">
          <text size="sm" weight="medium">历史对话</text>
        </box>
        <box align="center"><icon name="arrow-down" color="secondary"/></box>
        <box border radius="md" padding={2} align="center">
          <text size="sm">提取候选记忆</text>
        </box>
        <box align="center"><icon name="arrow-down" color="secondary"/></box>
        <box border radius="md" padding={2} align="center">
          <text size="sm">去重、冲突检查、更新</text>
        </box>
        <box align="center"><icon name="arrow-down" color="secondary"/></box>
        <box background="rgba(13,148,136,0.08)" border={{size:1,color:"rgba(13,148,136,0.4)"}} radius="md" padding={2} align="center">
          <text size="sm" weight="medium">Memory Store</text>
        </box>
      </box>
    </grid-item>
    <grid-item>
      <box gap={2}>
        <text weight="medium" size="sm" textAlign="center">前台：读取路径</text>
        <box background="surface-secondary" radius="md" padding={2} align="center">
          <text size="sm" weight="medium">用户提问</text>
        </box>
        <box align="center"><icon name="arrow-down" color="secondary"/></box>
        <box border radius="md" padding={2} align="center">
          <text size="sm">检索相关记忆</text>
        </box>
        <box align="center"><icon name="arrow-down" color="secondary"/></box>
        <box border radius="md" padding={2} align="center">
          <text size="sm">筛选有效记忆</text>
        </box>
        <box align="center"><icon name="arrow-down" color="secondary"/></box>
        <box background="rgba(13,148,136,0.08)" border={{size:1,color:"rgba(13,148,136,0.4)"}} radius="md" padding={2} align="center">
          <text size="sm" weight="medium">组装 Context</text>
        </box>
      </box>
    </grid-item>
  </grid>
  <caption>这是依据公开信息绘制的工程参考架构，并非 OpenAI 披露的内部系统图。实际内部检索和存储实现细节尚未公开。</caption>
</box>

这很好地解决了你提出的问题：

> 我不可能每次需要记忆时，再重新扫描所有对话。

对，完全没有必要每次这么做。

实际上，OpenAI 官方也明确表示：**ChatGPT 并不会针对每一条回答搜索全部历史，而是在相关上下文可能有帮助时引用它。** <Cite ref="turn640165search0"/>

## 三、它为什么能记得比较准确？我认为有三个核心设计

OpenAI 在 2026 年的官方文章中，将记忆质量评估重点放在三个维度：延续过去的信息、遵循用户偏好、保持信息时效性。<Cite ref="turn640165search5"/>

### 1. 不是简单保存原话，而是整合用户信息

举个例子，假设用户分别说过：

> 第一天：我喜欢 Python。

> 第五天：最近主要学习 C++。

> 第十天：我准备找 C++ 后端岗位。

一个简单的 Memory 系统可能保存三条独立笔记。

但一个更成熟的系统可以形成：

```yaml
programming:
  languages:
    - Python
    - C++

  current_focus: C++ 后端

  history:
    - 曾偏好 Python
    - 近期重心转向 C++

  source:
    - conversation_001
    - conversation_005
    - conversation_010
```

这跟你的 Advanced JSON Cards 思路非常接近。

注意，上面是我建议的实现方式，不是 OpenAI 官方公布的内部数据结构。

### 2. 记忆不是永久不变的事实

这是 Dreaming V3 很重要的改进方向。

例如用户三个月前说：

> 我下周准备去新加坡旅游。

到了今天，这条记忆不能继续被当作“即将去新加坡”。

官方特别介绍了时间推移后的记忆更新：过去的旅行计划应当变成过去发生过的事件，而不是继续影响当前地点相关的推荐。<Cite ref="turn744101view0"/>

这说明 Memory 系统需要考虑：

- 记忆的有效时间。
- 新信息是否覆盖旧信息。
- 用户的状态是否发生变化。
- 某条记忆是否已经不再适用。

### 3. 不需要把所有记忆都放进 Context

这一点正好解答你的召回疑问。

我建议区分两种信息：

**常驻核心记忆**：体积小、稳定、使用频繁，例如学习偏好。

**按需检索记忆**：更具体的项目决策、历史经历、过去问题的解决方案。

比如你问：

> Python 中 `dict` 是什么？

只需少量学习偏好，就能决定使用比较简单的讲解方式。

但你问：

> 我之前那个 Agent 项目的 Context 压缩方案是什么？

就需要进一步找回特定项目的架构讨论。

我们不需要把所有过去的 Agent 讨论都常驻在 Context 中。

## 四、OpenAI 还有哪些没有公开？

这一点必须特别说明。

虽然官方公开了 Dreaming 的整体思路，但没有完整披露以下工程细节：

| 问题 | 公开情况 |
|---|---|
| 后台会自动整合历史记忆吗？ | 已确认 |
| 记忆会随时间更新吗？ | 已确认 |
| 每次回复都会搜索全部历史吗？ | 官方明确表示不会 |
| 使用什么数据库存储？ | 未公开具体架构 |
| 是否使用向量数据库和 Embedding？ | 未公开具体实现 |
| Memory Router 用规则还是模型？ | 未公开 |
| Dreaming 具体多久运行一次？ | 未公开固定调度策略 |
| 怎样实现完整冲突检测算法？ | 未公开 |

因此我们不能直接断言 ChatGPT 内部使用了某种特定的向量数据库、固定时间间隔或重排模型。

---

## 五、如果你自己实现一个类似 ChatGPT 的 Memory，我建议这样设计

结合你的 Agent Harness，第一版可以做到：

<box border radius="lg" padding={3} gap={3}>
  <row align="start" gap={3}>
    <box background="surface-secondary" radius="lg" padding={2}>
      <icon name="database" size="lg"/>
    </box>
    <box flex="1" gap={1}>
      **Memory Store**

      使用 SQLite 存储记忆正文、类型、用户 ID、项目 ID、来源、更新时间和状态。

      第一版不需要引入向量数据库。
    </box>
  </row>
  <divider color="subtle"/>
  <row align="start" gap={3}>
    <box background="surface-secondary" radius="lg" padding={2}>
      <icon name="brain" size="lg"/>
    </box>
    <box flex="1" gap={1}>
      **Memory Consolidator**

      从新的对话中提取候选记忆，判断是否具有长期价值、是否重复、是否与已有记忆冲突。

      可以采用事件触发加定期后台整理。
    </box>
  </row>
  <divider color="subtle"/>
  <row align="start" gap={3}>
    <box background="surface-secondary" radius="lg" padding={2}>
      <icon name="search" size="lg"/>
    </box>
    <box flex="1" gap={1}>
      **Memory Retriever**

      先根据用户 ID、项目 ID、记忆类别进行过滤，再通过关键词或语义检索找出相关记忆。

      只将少量最相关的内容加入 Context。
    </box>
  </row>
  <divider color="subtle"/>
  <row align="start" gap={3}>
    <box background="surface-secondary" radius="lg" padding={2}>
      <icon name="shield-check" size="lg"/>
    </box>
    <box flex="1" gap={1}>
      **Memory Validator**

      检查来源、时间、作用范围、冲突及是否已经失效，并允许用户更正或要求不再使用相关记忆。
    </box>
  </row>
</box>

我会优先做一个可运行的版本，再通过评测来改进检索触发机制。

例如构造 100 个测试问题，其中有些必须依赖历史，有些完全不需要历史，有些要求解决过期记忆冲突。

分别测量：

- **Recall Rate**：应该使用的记忆是否成功找回。
- **Precision**：返回的记忆是否真正相关。
- **Stale Memory Error Rate**：是否错误地使用过期记忆。
- **额外延迟和 Token 成本**：记忆系统给主对话增加了多少负担。

这与你之前提出的“Memory 的测评与进化”思想是相通的。

---

## 六、我们继续一道架构面试题

现在我想考察你对 **Memory 写入与召回的取舍**。

假设用户过去 15 天和 Agent 讨论了很多技术，后台 Dreaming 已经提取出 200 条候选记忆。

但其中只有 20 条对用户接下来的任务具有长期价值。

你需要设计一个 Memory Consolidator，决定哪些记忆进入长期存储。

我先问你一个问题：

**你觉得判断一条记忆是否值得长期保存，最重要的指标是什么？**

例如，用户只说过一次：

> 我以后学习技术时，希望先理解原理，再看代码。

另一句话重复出现了 20 次：

> 今天有点困。

前一句只出现一次，后一句出现了 20 次。

你会保存哪一条？为什么？

我想看看你会如何权衡**重要程度、出现频率、未来价值和时效性**，而不是简单认为重复得越多就越值得记住。

## 054 · 用户 · 2026-10-20T07:44:19.567000+00:00

我的评价标准是价值长期价值当前价值如果一条记录对对现在和未来是有价值的而且还会重新复用的话那他就是应该存进去的还有就是时效性吧比如说用户明确规定了什么时候什么时候干什么的时候这个就毕竟那个也是比在就是怎么说呢比如说用户说11月20号去新加坡那么在11月20号之前这个条记忆都是有价值的可以考虑留下来

## 055 · 助手 · 2026-10-20T07:44:37.060513+00:00

你的判断是正确的，而且你已经抓住了长期记忆写入的两个关键因素：

**未来复用价值 + 信息的时效性。**

不过，我想在此基础上补充一个容易忽略的问题：**有价值的信息，不一定适合存入长期 Memory。**

## 一、先评价你的方案

你提出的标准可以整理成四个维度：

| 维度 | 核心问题 |
|---|---|
| 长期价值 | 未来很多次对话是否可能用到？ |
| 当前价值 | 是否对正在执行的任务有帮助？ |
| 复用概率 | 将来再次需要这条信息的可能性有多大？ |
| 时效性 | 这条信息什么时候开始有效、什么时候可能失效？ |

但我建议增加第五个维度：**可靠性与存储必要性**。

例如：

> 用户说：“我觉得 PostgreSQL 可能比 MySQL 快。”

这是一条用户观点，不应该直接存为：

```yaml
fact: PostgreSQL 比 MySQL 快
```

更合理的是：

```yaml
type: user_opinion
content: 用户认为 PostgreSQL 可能比 MySQL 快
confidence: tentative
```

同时，涉及敏感信息时，还需要考虑用户授权、隐私和保存的必要性。

## 二、你举的旅行例子非常好，但有一个陷阱

用户说：

> 我计划 11 月 20 日去新加坡。

你认为这条信息在 11 月 20 日之前有价值，这没有问题。

但是，**11 月 20 日过去以后，这条记忆不一定就没有价值了。**

我们可以给记忆设计一个生命周期：

<box border radius="xl" padding={3} gap={2}>
  <row align="center" justify="between">
    <box gap={1}>
      <text weight="medium">PLANNED</text>
      <text color="secondary" size="xs">旅行计划，尚未发生</text>
    </box>
    <icon name="arrow-right" color="secondary"/>
    <box gap={1}>
      <text weight="medium">PAST / UNKNOWN</text>
      <text color="secondary" size="xs">日期已过，是否成行未知</text>
    </box>
  </row>
  <divider/>
  <text size="sm">如果之后用户确认已经去过，才可以更新为 `COMPLETED`；如果用户取消行程，则更新为 `CANCELLED`。</text>
</box>

这里最重要的是：

**日期过去，并不能证明事件已经发生。**

因此，时效性不是简单设置一个过期时间并删除记录，而是根据时间变化重新判断记忆的状态和用途。

例如旅行结束后，“即将前往新加坡”失效了，但“用户曾经去过新加坡”可能仍有长期价值，前提是确实有证据证明行程已经发生。

---

## 三、我建议把 Memory 写入设计成两步

这是一个很实用的工程取舍。

**第一步：提取候选记忆。**

从用户对话中识别偏好、计划、项目约束、重要经历等信息。

**第二步：决定如何处理。**

不是所有候选记忆都直接保存到长期用户画像里。

{@body const cases=[{text:"以后讲解技术，请先讲原理，再讲代码。",action:"长期 Memory",why:"明确的稳定偏好，未来可能频繁复用。"},{text:"11 月 20 日计划去新加坡。",action:"有时效的事件记忆",why:"日期明确，需要记录计划状态，后续更新。"},{text:"今天有点困。",action:"通常不写入长期记忆",why:"属于短暂状态，除非用户有明确的持续记录需求。"},{text:"这个项目必须使用 Python。",action:"项目 Memory",why:"仅对特定项目生效，但属于重要硬约束。"}]}
{@body const [idx,setIdx]=DIL.useState(0)}
<box border radius="xl" padding={3} gap={3}>
  <text weight="medium">试着判断以下信息应该怎么存</text>
  <box background="surface-secondary" radius="lg" padding={3}>
    <text color="secondary" size="xs">用户说</text>
    <title size="lg">{cases[idx].text}</title>
  </box>
  <row wrap="wrap" gap={2}>
    {#each cases as c,i}
      <button key={i} size="sm" variant={idx===i?"solid":"outline"} color={idx===i?"primary":"secondary"} onClick={()=>setIdx(i)}>案例 {i+1}</button>
    {/each}
  </row>
  <divider color="subtle"/>
  <box gap={1}>
    <text color="secondary" size="xs">建议处理</text>
    **{cases[idx].action}**
    {cases[idx].why}
  </box>
</box>

这实际上让 Memory 从简单的“信息保存器”，升级为一个能够维护不同生命周期和作用范围的信息管理系统。

---

## 四、下一轮架构面试：Memory 更新比写入更难

现在假设你的系统已经保存了下面这条长期记忆：

```yaml
memory_id: 001
type: learning_preference
content: 用户喜欢先理解原理，再看代码
status: ACTIVE
```

半个月后，用户说：

> 最近准备技术面试，时间比较紧。以后讲解面试题时直接给我标准答案，不需要先讲原理。

现在出现了一个看似冲突的情况。

**我的问题：你会怎么更新这条 Memory？**

你可以考虑三个选择：

- A：直接用新偏好覆盖旧偏好，因为它时间更新。
- B：同时保留两条偏好，由召回阶段决定使用哪条。
- C：修改旧记忆的适用范围，并新增一条面试场景的偏好。

重点不是选字母，而是解释：**如何区分“用户改变了偏好”和“用户只是在某个场景下提出了例外”？**

这个问题回答清楚后，你的 Memory 系统就能从简单的存取，进入真正的**冲突处理与状态演化**。

## 056 · 用户 · 2026-10-20T07:48:26.102000+00:00

嗯偏好场景那就结合偏好和场景呗比如说呃用户说我喜欢吃巧克力然后后面又说呃现在在减肥不能吃巧克力哎哟就是我有点说不清道不明我就感觉就是我知道怎么处理但是不知道怎么清楚的表示出来就是结合情况吧就是嗯

## 057 · 助手 · 2026-10-20T07:48:42.212615+00:00

其实你已经理解了，只是还没有把直觉转化成明确的规则。

你说的“喜欢巧克力，但是减肥期间不能吃巧克力”，是一个很好的例子。因为它说明了：

**两条看似矛盾的记忆，可能描述的是不同维度的事实，根本不需要互相覆盖。**

## 一、先把你的直觉拆开

假设用户说：

> 第一天：我喜欢吃巧克力。

> 第十天：我现在正在减肥，暂时不能吃巧克力。

你直觉上知道，用户没有突然变得讨厌巧克力。

只是多了一个暂时的限制。

因此，Memory 应该这样理解：

| 维度 | 记忆内容 |
|---|---|
| 长期偏好 | 喜欢巧克力 |
| 当前约束 | 减肥期间避免吃巧克力 |
| 适用范围 | 饮食推荐 |
| 时间属性 | 当前有效，结束时间未知 |

如果直接用“不能吃巧克力”覆盖“喜欢巧克力”，就丢失了用户原本的偏好。

反过来，如果只保留“喜欢巧克力”，Agent 又可能在减肥期间反复推荐巧克力。

所以正确做法不是简单二选一，而是**同时维护偏好和约束，并在具体场景下决定优先采用哪个。**

## 二、如何将它变成工程上可执行的规则？

我建议给每条 Memory 增加一些元数据。

```yaml
memory_001:
  type: preference
  content: 喜欢巧克力
  scope: global
  status: active

memory_002:
  type: constraint
  content: 减肥期间避免巧克力
  scope: diet_recommendation
  valid_until: unknown
  status: active
```

那么，当用户问：

> 给我推荐一些零食。

Memory Retriever 可以同时召回这两条记录。

接下来，Context Builder 根据当前场景判断：

- 用户确实喜欢巧克力。
- 但当前存在饮食限制。
- 因此不应仅因为用户喜欢巧克力，就推荐它。

注意，“减肥期间避免巧克力”属于用户当前表达的限制，并不是系统应该自行制定的饮食规则。

## 三、这里可以总结出四种记忆关系

这是你架构设计中值得建立的一套分类。

| 关系 | 例子 | 处理方式 |
|---|---|---|
| 补充 | 喜欢 Python → 也喜欢 C++ | 合并或关联 |
| 场景例外 | 喜欢详细讲解 → 面试时希望简洁 | 同时保存，区分场景 |
| 明确覆盖 | 喜欢 Python → 以后默认使用 C++ | 更新默认偏好，保留变更记录 |
| 事实争议 | 工具 A 说成功，工具 B 说失败 | 保留证据，标记待验证 |

这样，Memory Consolidator 就不需要每次都凭模糊直觉决定要不要覆盖记忆。

它可以先判断两条信息之间的关系，再采取不同的更新策略。

但关系分类也可能出错，所以最好记录来源，并允许后续纠正。

---

## 四、我想让你理解一个更深的概念：偏好与约束的优先级

你前面提到“结合情况”，其实可以进一步表达成：

**先确定记忆的作用范围，再判断它是否与当前任务相关，最后处理偏好和约束的关系。**

例如：

<box border radius="lg" padding={3} gap={2}>
  <text weight="medium">当前任务：推荐零食</text>
  <box background="surface-secondary" radius="md" padding={3} gap={1}>
    <text color="secondary" size="xs">召回的长期偏好</text>
    用户喜欢巧克力
  </box>
  <box background="surface-secondary" radius="md" padding={3} gap={1}>
    <text color="secondary" size="xs">召回的当前约束</text>
    用户在减肥期间避免吃巧克力
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box background="rgba(13,148,136,0.07)" border={{size:1,color:"rgba(13,148,136,0.4)"}} radius="md" padding={3}>
    **决策：** 尊重当前限制，推荐其他符合用户要求的零食。长期偏好仍然保留。
  </box>
</box>

不过，也不要设计成“任何新约束永远压过所有偏好”。

例如用户随后明确说：

> 我已经结束减肥了，可以正常吃巧克力。

这时系统应该重新评估当前约束是否还有效。

---

## 五、下一轮面试：如何自动判断是覆盖还是例外？

现在假设 Memory 中已有：

```yaml
preference:
  content: 用户喜欢详细的技术讲解
  scope: global
```

用户今天说：

> 最近在准备面试，回答我的技术问题尽量简洁。

**你觉得 Memory Consolidator 应该怎么判断，这句话是修改全局偏好，还是新增一个面试场景的例外？**

我希望你重点思考：

**能否通过用户话语中的时间范围、场景限定词和明确程度，建立一些可执行的判断规则？**

你不需要一次给出完善算法。可以先像刚才一样用例子讲，我来帮你转化成工程方案。

## 058 · 用户 · 2026-10-20T07:53:30.543000+00:00

这句话最好就是加一个面试场景的例外，可以通过用户话语中的时间范围场景限定值和明确程度来建立一套可判断的规则吧可以

## 059 · 助手 · 2026-10-20T07:53:45.629092+00:00

对，你已经抓住了 Memory 更新的关键：**先判断新信息是全局偏好变更，还是特定场景下的例外，再决定如何更新记忆。**

但这里还有一个细节：

> “最近在准备面试，回答我的技术问题尽量简洁。”

“最近”表示时间可能有限，“准备面试”提供了场景，但并没有给出明确的结束日期。

因此，我们不能把这条记忆永久生效，也不能擅自设定一个确定的过期时间。

## 一、把你的思路转化为工程规则

可以先设计一个 Memory Update Pipeline：

<box border radius="lg" padding={3} gap={2}>
  <box background="surface-secondary" radius="md" padding={3} align="center">
    **新用户信息**
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **提取语义信息**
    <text size="xs" color="secondary">内容、时间、场景、明确程度、来源</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **与已有 Memory 比较**
    <text size="xs" color="secondary">补充？覆盖？场景例外？冲突？</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box background="surface-secondary" radius="md" padding={3} align="center">
    **更新 Memory Store + 保留来源**
  </box>
</box>

针对这个例子，可以保存为：

```yaml
memory_id: preference_002
type: communication_preference
content: 面试准备期间希望技术回答简洁
scope: interview_preparation
valid_until: unknown
status: active
source: user_explicit
```

原本的“喜欢详细理解技术原理”仍然保留。

这样用户在学习 Agent 源码时，可以得到详细解释；在模拟技术面试时，则优先使用简洁回答。

## 二、但不要只依赖关键词规则

例如：

> 我最近准备面试，所以以后所有技术问题都直接给结论。

这里虽然出现“准备面试”，但“以后所有技术问题”表达的是更广泛的偏好变更。

因此，“最近”“面试”“以后”等词只能作为判断线索，不能直接决定记忆的作用范围。

比较合理的方式是：**LLM 提取语义和候选更新，程序校验结构、来源与更新权限。**

对于含义不明确的重要修改，可以保留旧记忆并标记待确认，而不是贸然覆盖。

---

## 三、下一轮：Memory 真正困难的地方来了

我们已经讨论了记忆写入、作用范围、场景例外和更新。

现在把注意力重新放到你的笔试题：

> 和聊天 Agent 熟悉半个月后，用户问了一个以前问过的问题。Agent 如何做 Memory 召回更合理？

假设用户在半个月前问过：

> PostgreSQL 和 MySQL 的 MVCC 有什么区别？

当时 Agent 已经详细解释过，而且用户表示理解了。

15 天后，用户再次问：

> PostgreSQL 和 MySQL 的 MVCC 有什么区别？

这时 Memory 中存在两类信息：

- **知识记忆**：之前讨论了 MVCC、版本可见性、Undo Log、Tuple Version 等知识。
- **学习状态记忆**：用户上次已经理解 MVCC 的基础流程，但对 PostgreSQL 和 MySQL 的实现差异仍然不够熟悉。

**我的追问是：**

这一次，Agent 应该直接给出完整的标准讲解，还是利用过去的学习状态从用户薄弱点继续讲？

更进一步：**用户 15 天前说“我懂了”，是否足以证明他现在仍然掌握？**

这个问题会帮助我们区分：Memory 召回究竟应该服务于“复述历史”，还是服务于“更好地完成当前任务”。

## 060 · 用户 · 2026-10-20T07:56:20.612000+00:00

Memory的核心是为了更好的服务当前的任务嗯你的追问我的答案是根据用户薄弱点继续

## 061 · 助手 · 2026-10-20T07:56:36.695992+00:00

你的回答抓住了核心：

**Memory 的价值不是让 Agent 证明自己记得过去，而是利用过去的信息，更好地完成当前任务。**

我认可你选择“根据薄弱点继续讲解”，但这里还有一个容易忽视的问题。

## 一、记忆中的学习状态不一定等于当前学习状态

假设 Memory 记录：

```yaml
topic: MVCC
last_studied: 15天前
understood:
  - MVCC的基本原理
weaknesses:
  - PostgreSQL与MySQL的实现差异
```

15 天后用户重新问 MVCC，可能有三种情况：

1. 用户忘记了基本原理，需要重新学习。
2. 用户仍然掌握基本原理，只想深入实现差异。
3. 用户已经通过其他渠道掌握了全部知识，只想快速复习。

因此，**过去的学习状态是判断当前需求的参考证据，而不是绝对事实。**

你的方案应该增加一个环节：根据当前输入，判断旧记忆是否仍然适用。

## 二、我建议这样设计召回机制

<box border radius="lg" padding={3} gap={2}>
  <box background="surface-secondary" radius="md" padding={3} align="center">
    **当前问题：MVCC 有什么区别？**
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **召回相关 Memory**
    <text color="secondary" size="xs">历史学习内容、薄弱点、学习偏好</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **检查时效性与当前意图**
    <text color="secondary" size="xs">用户是在复习、深入学习，还是重新入门？</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box background="rgba(13,148,136,0.07)" border={{size:1,color:"rgba(13,148,136,0.4)"}} radius="md" padding={3} align="center">
    **生成适配当前需求的回答**
    <text color="secondary" size="xs">参考历史薄弱点，但不被旧记忆限制</text>
  </box>
</box>

例如，Agent 可以先用两三句话快速回顾 MVCC，再重点讲解 PostgreSQL 和 MySQL 的差异。

这样既利用了 Memory，也不会假设用户一定记得 15 天前学过的内容。

---

## 三、下一轮架构追问：如何衡量 Memory 是否真的有用？

这是一个非常关键的问题，也是你之前提到的“评测与进化”。

假设你实现了两套 Agent：

- **Agent A**：没有长期 Memory，每次只根据当前对话回答。
- **Agent B**：有长期 Memory，能够召回学习偏好、历史知识和薄弱点。

你希望证明 B 确实比 A 更好。

但是，如果 B 的回答更长、更详细，或者用户主观上觉得它更熟悉自己，是否就能证明 Memory 系统更优秀？

显然不能直接这样判断。

**我的问题：如果让你设计一套 Memory 评测系统，你会选择哪些指标和测试方法，来验证记忆召回确实提升了任务完成质量？**

你可以结合之前 RAG 评测中使用过的 Recall、MRR，以及你刚刚提出的“控制变量”思路。

我们这次重点讨论三个维度：**该记住的是否记住了、不该使用的是否避免使用、使用记忆以后任务是否真的完成得更好。**

## 062 · 用户 · 2026-10-20T07:58:49.549000+00:00

首先该召回的记忆是否都召回了第二召回的那个质量怎么样就是有没有找回无关的记忆第三用户的体验怎么样以及模型根据记忆生成的回答质量怎么样

## 063 · 助手 · 2026-10-20T07:59:04.260780+00:00

你的回答方向正确，而且已经覆盖了 Memory 评测的三个核心层次：

1. **召回完整性**：需要的记忆有没有找回来？
2. **召回准确性**：找回来的记忆是否相关，有没有混入无关信息？
3. **任务收益**：使用记忆以后，回答质量和用户体验是否真的提高？

我给你这轮 **90/100**。还差一个非常重要的指标：**错误记忆带来的负面影响**。

## 一、把你的思路转化成可量化指标

| 评测维度 | 对应指标 | 衡量什么 |
|---|---|---|
| 召回完整性 | Recall@K | 应该召回的记忆找回了多少 |
| 召回准确性 | Precision@K | 召回的记忆中有多少真正相关 |
| 排序质量 | MRR / NDCG | 重要记忆是否排在前面 |
| 记忆正确性 | Stale / Conflict Error Rate | 是否使用过期或冲突记忆 |
| 最终任务效果 | Task Success Rate | 使用记忆是否提高任务成功率 |
| 用户体验 | 用户评分、重复解释次数 | 是否减少用户重复提供背景 |
| 性能成本 | P95 延迟、Token 消耗 | 个性化收益是否值得额外成本 |

这里有一点需要强调：

**Recall 高不代表 Memory 系统一定好。**

例如用户问：

> 帮我继续完成 Agent Harness 项目。

Agent 召回了 20 条记忆，其中 18 条和项目有关，另外两条是用户几年前的偏好。

即使召回率很高，无关信息也可能干扰模型判断。

所以我们不能只评估 Retriever，还需要评估最终 Agent 的表现。

## 二、怎样做一套真正可信的 Memory A/B 测试？

可以设计三组 Agent。

<box border radius="lg" padding={3} gap={3}>
  <box gap={1}>
    **A：无 Memory**
    <text color="secondary" size="sm">只使用当前用户消息与必要的当前 Session 上下文。</text>
  </box>
  <divider color="subtle"/>
  <box gap={1}>
    **B：真实 Memory**
    <text color="secondary" size="sm">使用你设计的 Memory Router、Retriever 和记忆更新机制。</text>
  </box>
  <divider color="subtle"/>
  <box gap={1}>
    **C：Oracle Memory（理想记忆）**
    <text color="secondary" size="sm">人为提供正确且与任务相关的记忆，作为理想召回参考。</text>
  </box>
</box>

为什么需要 C？

假设实验结果是：

<Chart content={{"chartType":"bar","meta":{"footer":"假设数据，仅用于说明评测方法"},"xKey":"agent","series":[{"dataKey":"success","label":"任务成功率","valueSuffix":"%"}],"yAxisMin":0,"yAxisMax":100,"data":[{"agent":"A 无记忆","success":60},{"agent":"B 真实记忆","success":72},{"agent":"C 理想记忆","success":90}]}}/>

这说明两件事：

- B 比 A 高 12 个百分点，表明真实 Memory 系统可能带来了收益。
- B 比 C 低 18 个百分点，说明当前 Memory 流程还有改进空间。

但 B 和 C 的差距不一定全部来自检索，也可能来自过期记忆处理、Context 组装方式等因素。还需要进一步做消融实验。

实验应当使用相同模型、相同任务集、尽量一致的生成配置，并进行多次运行，减少随机性影响。

---

## 三、我认为还需要增加一个测试：不该使用 Memory 时，能不能不使用？

这是 Memory 系统很容易忽略的问题。

假设历史记忆：

> 用户以前喜欢使用 Python。

但用户这次明确要求：

> 帮我用 C++ 实现一个线程池。

正确行为是什么？

**应该使用 C++，不能因为记忆中的 Python 偏好而违背当前明确要求。**

这类测试可以叫作 *Negative Memory Test*。

它检验的不是记忆召回能力，而是 Agent 能否避免不恰当地使用记忆。

还可以测试：

- 过期的旅行计划是否仍影响当前推荐。
- 不同项目的约束是否相互污染。
- 用户明确更正的记忆是否停止使用。
- 当前消息是否被旧偏好错误覆盖。

对长期运行的 Agent 来说，这些问题往往比单纯提高 Recall 更重要。

---

## 四、我们已经可以归纳你的 Memory 方案了

到目前为止，你提出的主要设计思想是：

**存储层**：全局记忆、项目记忆、当前会话状态分层组织。

**写入层**：依据长期价值、复用概率、时效性提取候选记忆。

**召回层**：少量核心记忆常驻，结合任务类型选择需要检索的记忆类别，再按需获取完整内容。

**更新层**：比较新旧记忆的作用范围、时间和含义，区分补充、覆盖、例外和争议。

**评测层**：既评估 Recall、Precision 等检索指标，也评估最终任务质量、错误记忆率和用户体验。

这已经是一套相当完整的 Memory 架构思路。

---

## 五、最后一道综合追问

现在我想进一步考察你对 **Memory 评测结果如何指导系统优化** 的理解。

假设你设计的 Memory 系统经过测试，得到以下结果：

<box border radius="lg" padding={3} gap={2}>
  <grid columns={2} gap={2}>
    <grid-item>
      <box background="surface-secondary" padding={3} radius="md" gap={1}>
        <text color="secondary" size="xs">Recall@5</text>
        <title size="xl" color="success" tabularNums>95%</title>
      </box>
    </grid-item>
    <grid-item>
      <box background="surface-secondary" padding={3} radius="md" gap={1}>
        <text color="secondary" size="xs">Precision@5</text>
        <title size="xl" tabularNums>60%</title>
      </box>
    </grid-item>
    <grid-item>
      <box background="surface-secondary" padding={3} radius="md" gap={1}>
        <text color="secondary" size="xs">任务成功率</text>
        <title size="xl" tabularNums>72%</title>
      </box>
    </grid-item>
    <grid-item>
      <box background="surface-secondary" padding={3} radius="md" gap={1}>
        <text color="secondary" size="xs">无 Memory 时成功率</text>
        <title size="xl" tabularNums>70%</title>
      </box>
    </grid-item>
  </grid>
  <caption>假设这是同一批测试任务的结果。</caption>
</box>

现在发现：

- 应该召回的记忆基本都找到了。
- 但召回结果混入不少无关记忆。
- 使用 Memory 后，任务成功率仅提高 2 个百分点。

**作为 Memory 架构负责人，你会优先优化哪一部分？为什么？**

是继续提高 Recall、提升 Precision、修改 Context 组装，还是改进模型使用记忆的方式？

这次我希望你尝试从**指标 → 问题定位 → 实验设计 → 验证改进**的完整工程流程回答，而不是只选择一个指标。

## 064 · 用户 · 2026-10-20T08:01:05.288000+00:00

先排查问题吧先看看问题出现在哪如果是准确率的原因那这就得算金牌如果是上下文组装的原因那就得改变上下文组装如果前面都没问题而是呃模型拿到了正确的在下文记忆还是没有回答问题那就是模型的原因然后控制变量你一个个修复

## 065 · 助手 · 2026-10-20T08:01:19.135901+00:00

你的回答已经体现了比较成熟的工程排障思路：**不根据单个指标直接修改系统，而是先定位瓶颈，再通过控制变量验证改进是否有效。**

我给这轮 **90/100**。你的整体方法正确，不过还需要进一步明确：如何判断问题究竟发生在哪个环节？

## 一、我会怎样完善你的方案？

Memory 系统可以拆成四个环节：

<box border radius="lg" padding={3} gap={2}>
  <box background="surface-secondary" radius="md" padding={3} align="center">
    **Memory Store**
    <text color="secondary" size="xs">需要的记忆是否正确保存？</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **Retriever**
    <text color="secondary" size="xs">是否找回了正确、相关的记忆？</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **Context Builder**
    <text color="secondary" size="xs">正确记忆是否被恰当地放进模型上下文？</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box background="rgba(13,148,136,0.08)" border={{size:1,color:"rgba(13,148,136,0.4)"}} radius="md" padding={3} align="center">
    **LLM**
    <text color="secondary" size="xs">模型是否正确理解并使用了记忆？</text>
  </box>
</box>

如果最后任务失败，就需要判断是哪一层出了问题。

### 一个非常实用的方法：Oracle 替换实验

假设原来任务成功率是 72%。

我们分别替换某个组件，观察效果。

| 实验 | 改动 | 假设成功率 |
|---|---|---:|
| A | 原始系统 | 72% |
| B | 人工提供正确记忆，绕过 Retriever | 88% |
| C | 保留原检索结果，人工优化 Context 组装 | 76% |
| D | 正确记忆 + 人工优化 Context | 91% |

<caption>以上数据只是用来说明实验方法，不是真实测试结果。</caption>

如果 B 明显优于 A，就说明**检索链路可能是主要瓶颈**。

但是要小心：人工提供的正确记忆不仅改变了相关性，也可能改变记忆数量、长度和呈现方式。因此还需要进一步控制变量，才能准确归因。

这跟你做 RAG 检索、重排、上下文构造的消融实验，其实是同一个方法论。

## 二、你还漏掉了一个环节：Memory 写入

即使 Retriever 再强，如果正确的信息一开始就没有写入 Memory Store，也不可能成功召回。

所以我们需要区分：

- **记忆缺失**：相关信息根本没有保存。
- **检索失败**：保存了，但没有找到。
- **组装失败**：找到了，但没有正确提供给模型。
- **使用失败**：模型看到了，但没有正确应用。

特别是最后一项，还可能涉及旧记忆与当前用户指令冲突，并不能一概归因于模型能力不足。

---

## 三、模块二的核心思路可以定下来了

你现在对 Memory 已经形成了一条相当完整的设计链路：

**价值判断 → 候选提取 → 分类存储 → 召回触发 → 相关性排序 → Context 组装 → 任务执行 → 评测反馈 → 记忆更新。**

而我们前面讨论的 Context 压缩，主要解决的是单个任务或 Session 内的信息管理；Memory 则进一步解决跨时间、跨 Session 的信息延续。

两者可以共享底层存储和证据机制，但目的不同。

## 四、最后一道题：检验你对 Memory 与隐私边界的理解

假设用户过去和 Agent 聊过很多私人话题。Memory 系统认为其中一些信息可能有助于未来回答。

但用户今天问：

> 帮我推荐三本学习 Python 的书。

某条私人记忆与书籍推荐只有很弱的相关性。

**我的问题是：即使检索算法认为这条记忆相关，你会允许它进入当前 Context 吗？**

你可以从三个角度回答：

1. **相关性**：它对当前任务是否真的必要？
2. **敏感性**：不同类型的记忆是否应该有不同的使用规则？
3. **用户控制权**：用户应该如何决定哪些信息能被长期保存、召回和使用？

这道题回答以后，我们就结束模块二，把 Memory 方案整理为笔试提交版，然后进入模块三 Task。

## 066 · 用户 · 2026-10-20T08:03:05.630000+00:00

设置阈值一些相关性不高的没必要进入最近一个相关性阈值低于这个阈值的不要呃嗯一些啊不同类型的记忆确实应该有不同的用法然后用户控制权的话用户可以明确的控制可以明确的要求哪些信息应该长期存储然后什么时候该用也可以明确要求哪一些记忆不允许被记入用户有绝对的控制权

## 067 · 助手 · 2026-10-20T08:03:21.831469+00:00

你的回答方向正确。我尤其认可最后一点：**Memory 应该服务于用户，而不是让用户被 Memory 约束。**

不过，你提出的“相关性阈值”还需要补充一个条件：

**相关性高，不代表有权限使用。**

例如用户曾经透露过某项非常私人的信息，即使它与当前问题高度相关，如果用户明确要求不再使用，Agent 也不能因为相关性高就将它放入 Context。

## 一、把你的方案完善成可执行的机制

可以设计三层过滤：

<box border radius="lg" padding={3} gap={2}>
  <box background="surface-secondary" radius="md" padding={3} align="center">
    **Memory Retriever 返回候选记忆**
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} gap={1}>
    **第一关：权限和隐私**
    <text color="secondary" size="sm">是否允许保存、是否允许当前场景使用、是否涉及敏感信息？</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} gap={1}>
    **第二关：有效性**
    <text color="secondary" size="sm">是否过期？是否被更正？是否与当前用户指令冲突？</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box background="rgba(13,148,136,0.08)" border={{size:1,color:"rgba(13,148,136,0.4)"}} radius="md" padding={3} gap={1}>
    **第三关：相关性和预算**
    <text color="secondary" size="sm">是否有助于当前任务？是否值得占用 Context Token？</text>
  </box>
</box>

你可以把这个顺序理解为：

\[
\text{可使用记忆} = \text{权限允许} \land \text{当前有效} \land \text{任务相关}
\]

实际工程中，权限、有效性、相关性可以采用不同的判断方式。尤其是权限，不能只依赖 LLM 自己判断。

另外，用户对记忆的控制应该包括：查看、更正、限制使用、请求删除，以及控制不同项目之间是否共享记忆。这些权限要在存储与检索层落实，而不能只靠提示词承诺。

---

## 二、模块二：Memory 架构题总结

我们已经讨论了 Memory 的写入、召回、更新、遗忘、评测和用户控制。

下面把你自己的思路整理成一份笔试答案初稿，选择的是：

**模块二第 1 题：和聊天 Agent 熟悉半个月后，用户问了一个以前问过的问题。Agent 如何做 Memory 召回更合理？**

<WritingBlock id="68241" variant="document">### 模块二·第 1 题：长期 Memory 的合理召回机制

我认为 Memory 的核心价值不是单纯记录历史，而是利用过去的信息，更好地完成当前任务。因此召回的目标不是找到尽可能多的记忆，而是在合理成本和隐私约束下，为当前决策提供正确、相关、及时的信息。

**1. 记忆组织**

采用分层管理：

- 全局记忆：长期稳定的用户偏好、背景和目标。
- 项目记忆：特定项目中的技术决策、约束、经验和进展。
- Session / Context 记忆：当前对话和任务状态。

记忆可以采用结构化字段与文本笔记混合存储，保留来源、时间、作用范围和原始记录引用，方便更新与回溯。

**2. 记忆提前整合**

不应该等到用户需要时，再扫描全部历史对话。

可以通过事件触发与后台定期整理，从历史中提取候选记忆，根据未来价值、复用概率、时效性和可靠性决定是否保存。

保存前进行去重、冲突判断和分类，避免将短暂状态、未经确认的推测直接写成长期事实。

**3. 召回时机**

采用分层触发：

- 少量重要且稳定的核心记忆常驻 Context。
- Runtime 根据当前任务类型、项目 ID 和用户意图，自动决定是否检索相关记忆类别。
- 如果模型执行过程中发现缺少历史背景，可以通过 Memory Search 工具进一步检索。

不依赖每轮完整扫描，也不完全依赖 LLM 主动想到需要检索。

**4. 检索与排序**

先按用户、项目、权限和记忆类型过滤，再通过关键词、语义相关性等方式获取候选记忆。

排序时综合考虑相关性、作用范围、时效性、可信来源和 Token 成本。

只将真正有助于当前任务的记忆加入 Context，并允许按引用回查原始记录。

**5. 冲突与更新**

记忆不能简单采用“最新覆盖最旧”。

需要区分补充、明确覆盖、场景例外和事实争议。

例如用户总体偏好 C++，但某个项目明确要求 Python，这两条记忆可以同时成立，项目约束在该项目内优先适用。

对于过期信息，不应直接当作当前事实；必要时标记未知或重新验证。

**6. 隐私与用户控制**

权限与隐私检查应先于相关性排序。即使某条记忆高度相关，也不能违反用户对保存和使用范围的明确限制。

用户应能够查看、更正、限制或请求删除记忆，系统还应避免在不同用户或未授权项目之间泄露信息。

**7. 评测与改进**

建立有 Memory、无 Memory 和理想 Memory 的对照实验。

使用 Recall@K、Precision@K、MRR 等指标评估检索质量，同时评估任务成功率、过期记忆误用率、用户体验、延迟和成本。

通过分层消融实验区分问题来自记忆写入、检索、Context 组装还是模型使用阶段，再针对性优化。

最终目标是让 Memory 在需要时发挥作用，在不需要时减少干扰，并且始终服从用户当前明确表达的意图。</WritingBlock>

这份初稿已经覆盖题目的核心，但如果想在技术面试中进一步提高说服力，最好补充一个具体的召回案例，以及相应的失败处理流程。

---

## 三、进入模块三：Task

接下来我建议选择第 1 题：

> 对于长程任务，大模型执行一段时间可能会忘掉目标，你知道哪些解决方案？有什么优缺点？

这道题与你前面讨论的 Context、Memory 和状态持久化直接相关，但重点不同。

**Context 主要解决模型能看到什么；Memory 主要解决过去的信息如何被保留和召回；Task Management 则要解决 Agent 如何持续推进目标。**

假设用户要求：

> 帮我从零开发一个 Agent Harness，包括 Runtime、工具系统、Session、Context 压缩和测试，全部完成后提交 GitHub。

Agent 执行了 50 轮，完成了 Runtime 和 Calculator。

但是到了第 51 轮，模型开始不断优化 Calculator 的精度和错误处理，忘记了还有 Search、Todo 和真实 LLM API 没有实现。

虽然它一直在执行有效的工作，但实际上偏离了用户的整体目标。

**第一道问题：如果由你来设计，你会怎样防止 Agent 在长程任务中不断陷入局部优化，忘记最终交付目标？**

你可以从任务拆解、状态持久化、进度检查、完成条件等方面自由提出方案。我们继续保持一题一题讨论，重点研究每种机制为什么有效、又可能在什么情况下失败。

## 068 · 用户 · 2026-10-20T08:07:29.557000+00:00

嗯可以在执行任务的时候让agen把任务拆解拆解一个个流程然后一个个完成然后标记确认然后进入下一个状态

## 069 · 助手 · 2026-10-20T08:07:42.968344+00:00

你的思路是正确的。这实际上就是 Agent 长程任务管理中非常经典的方案：**任务拆解（Task Decomposition）+ 状态追踪（State Tracking）+ 阶段性验收（Checkpoint）**。

但我想指出一个关键区别：

**把任务拆成多个步骤，并不等于 Agent 就不会偏离目标。真正重要的是，谁来判断当前步骤已经完成，以及什么时候应该进入下一步。**

## 一、先把你的方案工程化

假设任务是完成 Agent Harness 笔试。

Agent 先把整体目标拆解：

<box border radius="lg" padding={3} gap={3}>
  <row align="center" gap={2}>
    <icon name="list-todo" color="secondary"/>
    **总体目标：交付可运行的 Agent Harness**
  </row>
  <divider color="subtle"/>
  <box gap={2}>
    <row align="center" gap={2}>
      <icon name="check-circle-2" color="success"/>
      <text color="secondary">S1：实现 Runtime 基本循环</text>
    </row>
    <row align="center" gap={2}>
      <icon name="check-circle-2" color="success"/>
      <text color="secondary">S2：实现 Calculator</text>
    </row>
    <row align="center" gap={2}>
      <icon name="circle-dot" color="info"/>
      **S3：接入真实 LLM API**
    </row>
    <row align="center" gap={2}>
      <icon name="circle" color="tertiary"/>
      <text color="secondary">S4：完成 Search 和 Todo 工具</text>
    </row>
    <row align="center" gap={2}>
      <icon name="circle" color="tertiary"/>
      <text color="secondary">S5：Session 与 Context 管理</text>
    </row>
    <row align="center" gap={2}>
      <icon name="circle" color="tertiary"/>
      <text color="secondary">S6：端到端测试与提交</text>
    </row>
  </box>
  <caption>这是任务拆解示例，并非重新检查后的项目实时进度。</caption>
</box>

你的想法是让 Agent：

1. 明确总体目标。
2. 拆解任务并保存计划。
3. 每次集中完成一个子任务。
4. 验证完成后更新状态。
5. 读取计划，进入下一步。

这相比单纯在 Prompt 中写“请记住你的目标”，可靠得多。

原因是：**计划和进度成为独立的结构化状态，而不是依赖 LLM 从越来越长的聊天历史中自行回忆。**

## 二、但这个方案存在三个问题

| 问题 | 例子 | 需要解决的设计 |
|---|---|---|
| 任务虚假完成 | Agent 写完 Calculator 就标记完成，但测试失败 | 独立验收 |
| 计划过时 | 接入真实 LLM 时发现必须调整工具消息格式 | 动态重规划 |
| 局部优化过度 | Agent 花 30 轮优化 Calculator，却不推进其他任务 | 预算与进度控制 |

尤其是第二点。

长程任务开始时，Agent 不可能提前知道所有工程细节。

因此，任务计划不能是永远不变的固定清单，但也不能允许模型随意删除原始目标。

我建议区分：

- **Goal（目标）**：用户最终要求的交付成果。
- **Plan（计划）**：达到目标的执行方案，可以调整。
- **State（状态）**：实际完成了什么、还剩什么，需要证据支持。

这三者应该分开存储。

---

## 三、继续追问：计划可以被 Agent 自己修改吗？

假设你的 Agent 最初制定：

```yaml
goal: 完成 Agent Harness

tasks:
  - S1: Runtime
  - S2: Calculator
  - S3: Search
  - S4: Todo
  - S5: 真实 LLM API
  - S6: 测试与交付
```

执行到 S2 的时候，模型发现 Calculator 存在浮点数精度问题。

于是它决定：

> 我要先实现任意精度计算、复杂表达式解析、三角函数、矩阵运算和单位转换，才能进入 S3。

这显然超出了笔试的原始要求。

但从模型自身的角度看，它可能认为这些都是“提高项目质量”。

**我的追问是：**

你会允许 Agent 自己修改任务计划吗？

如果允许，你会怎样区分：

- **合理重规划**：为了完成用户目标，确实必须调整计划。
- **目标漂移**：模型不断增加不必要的工作，让任务越来越偏离原始要求。

我希望你尝试从三个角度回答：**原始目标约束、任务变更审批、时间与 Token 预算**。

这一题非常接近生产级 Coding Agent 的真实难点，也直接对应你的笔试项目可能发生的过度设计问题。

## 070 · 用户 · 2026-10-20T08:08:56.825000+00:00

如果他修改了计划是真的有益于目标的话那可以修改但是如果偏移目标当然是不可以的嗯还要考虑就是时间吧还有资源的费用

## 071 · 助手 · 2026-10-20T08:09:13.177491+00:00

你的判断是对的：**Agent 应当允许自主重规划，但必须保证重规划仍然服务于原始目标，并且收益值得付出的成本。**

不过，我们需要把“对目标有益”进一步定义清楚。否则模型完全可以把任何额外功能解释成“提高质量”。

## 一、怎么区分合理重规划和目标漂移？

我建议用四个维度评价任务变更：

| 维度 | 要回答的问题 |
|---|---|
| 必要性 | 不修改计划，原始任务还能完成吗？ |
| 目标相关性 | 新工作是否直接支持用户的验收要求？ |
| 成本收益 | 额外时间、Token 和复杂度是否值得？ |
| 风险 | 是否扩大修改范围、引入新的故障或违反约束？ |

例如你的 Agent Harness 笔试：

**情况 A：发现 Calculator 有除零漏洞。**

修复它属于合理重规划，因为笔试要求基本异常处理，而这个问题影响工具的正确性。

**情况 B：决定增加三角函数和矩阵计算。**

这属于范围扩张。虽然功能更多，但并不是满足当前验收条件所必需的，而且会消耗开发和测试资源。

**情况 C：发现真实 LLM API 的工具调用格式与当前内部模型不兼容。**

修改模型适配层属于合理重规划，因为不解决这个问题就无法完成真实 LLM 闭环。

所以可以给 Agent 一个简单的原则：

> 优先完成原始验收条件。非必要的优化进入待办列表，不自动加入当前执行计划。

## 二、一个可执行的重规划机制

<box border radius="lg" padding={3} gap={2}>
  <box background="surface-secondary" radius="md" padding={3} align="center">
    **Agent 提出计划变更**
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **检查原始目标与验收条件**
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **评估必要性、风险、时间和成本**
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <grid columns={2} gap={2}>
    <grid-item>
      <box background="surface-secondary" radius="md" padding={3} gap={1} align="center">
        <icon name="check-circle" color="success"/>
        **符合目标与预算**
        <text size="xs" textAlign="center">更新计划并记录原因</text>
      </box>
    </grid-item>
    <grid-item>
      <box background="surface-secondary" radius="md" padding={3} gap={1} align="center">
        <icon name="shield-alert" color="secondary"/>
        **重大范围或风险变化**
        <text size="xs" textAlign="center">暂停相关操作，请求确认</text>
      </box>
    </grid-item>
  </grid>
</box>

这里有个重要的工程设计：

**原始 Goal 和验收条件不能被 Agent 悄悄修改；Plan 则允许在受控范围内变化。**

例如任务数据库可以分别维护：

```yaml
goal:
  description: 完成最小可用 Agent
  acceptance:
    - 真实 LLM API
    - 三个工具
    - Session 隔离
    - Context 管理
    - 端到端测试

plan:
  version: 3
  current_task: 接入 LLM API
  completed:
    - Runtime 基本循环
    - Calculator
  deferred:
    - 扩展三角函数支持
```

这就是把**目标与计划解耦**。

---

## 三、下一轮面试：怎样识别 Agent 正在陷入局部优化？

现在假设 Agent 没有擅自增加新任务。

它仍然在执行原计划中的 Calculator 开发，但连续进行了 15 轮：

- 第 1～5 轮：修复精度问题。
- 第 6～10 轮：优化异常信息。
- 第 11～15 轮：反复修改代码风格。

每一轮都能找到合理的修改理由，测试也一直通过。

可是任务总体进度几乎没有变化，还有真实 LLM、Todo、Search 等功能没有完成。

**我的追问是：**

你会如何让 Runtime 自动发现 Agent 虽然一直在工作，却没有真正推进总体目标？

注意，不能简单规定“超过 10 轮就强制切换任务”，因为有些复杂问题确实需要更多轮。

我想听你怎么定义和衡量 **Progress（任务进展）**，以及发现进展停滞后应该采取什么行动。

## 072 · 用户 · 2026-10-20T08:17:54.993000+00:00

在一定时间一定资源下能完成任务的期限吧差不多就是这个意思吧比如说分配三个人去干一个活要求是一天内完成如果一天内完成了就是正常的任务进展如果没有没有一天内没有完成的话那就是延后，根据情况而定吧，如果严重拖慢任务进展要不就是快速收尾要不就是暂时终止，先进行别的任务

## 073 · 助手 · 2026-10-20T08:18:10.666291+00:00

你的思路是合理的。你已经意识到：**长程任务不能只看 Agent 有没有继续工作，还要看它有没有在有限的时间和资源内推进整体目标。**

尤其是你提出的两种处理方式：

- 快速收尾当前子任务。
- 暂时停止当前子任务，优先推进其他任务。

这实际上涉及两个工程概念：**进度监控（Progress Monitoring）** 和 **任务调度（Task Scheduling）**。

不过，你的方案还缺少一个关键维度：**时间消耗不等于任务进展。**

## 一、为什么不能只根据截止时间判断进度？

假设 Agent 有 60 分钟完成三个任务：

| 子任务 | 预算时间 | 实际耗时 | 结果 |
|---|---:|---:|---|
| Calculator | 10 分钟 | 15 分钟 | 验收通过 |
| Search | 20 分钟 | 12 分钟 | 验收通过 |
| 真实 LLM API | 30 分钟 | 20 分钟 | 验收失败 |

虽然总体只用了 47 分钟，但关键功能还没完成。

反过来，一个困难任务可能超过预估时间，却已经解决了大部分阻塞问题。

因此，不能只看时间，还要看：

**花费了多少资源，取得了多少可验证的进展。**

## 二、我建议把 Progress 拆成三个维度

<box border radius="lg" padding={3} gap={3}>
  <box gap={1}>
    <row justify="between" align="center">
      **1. 完成度（Completion）**
      <text color="secondary" size="xs">最终验收</text>
    </row>
    <text size="sm">当前子任务的验收条件满足了多少？是否有测试或工具结果证明？</text>
    <box background="surface-tertiary" height="9px" radius="full" clip>
      <box background="#0d9488" width="60%" height="100%"/>
    </box>
    <caption>示例：5 项验收条件中通过 3 项</caption>
  </box>
  <divider color="subtle"/>
  <box gap={1}>
    **2. 状态推进（State Progress）**

    <text size="sm">即使任务还没有完成，是否消除了原有阻塞、定位了错误、完成了必要依赖？</text>

    <text color="secondary" size="xs">例如：真实 API 调用还未成功，但已经确定是工具调用消息格式不匹配。</text>
  </box>
  <divider color="subtle"/>
  <box gap={1}>
    **3. 资源效率（Resource Efficiency）**

    <text size="sm">每消耗一定时间、Token 或工具调用次数，是否取得了新的、对完成任务有意义的结果？</text>

    <text color="secondary" size="xs">例如：连续五轮只修改 Calculator 的提示信息，测试结果始终不变。</text>
  </box>
</box>

这里你最需要掌握的是第二点。

**没有完成任务，不代表没有进展；不断修改代码，也不代表真的有进展。**

例如模型连续尝试三种修复方法，虽然都失败，但如果排除了三个不同的根因假说，实际上仍然获得了新信息。

这也解释了为什么不能简单地通过“连续 N 轮没有测试通过”判定 Agent 陷入死循环。

---

## 三、如何让 Runtime 自动识别停滞？

可以给每个子任务维护一个结构化状态：

```yaml
task_id: calculator_001
status: IN_PROGRESS

acceptance:
  - 基础四则运算通过
  - 括号支持通过
  - 除零异常通过

progress:
  passed_checks: 3
  remaining_checks: 0
  unresolved_blockers: 0

budget:
  elapsed_minutes: 15
  estimated_minutes: 10

next_action: 进入下一个任务
```

在这个例子中，即使 Calculator 超过了预计时间，也应该收尾，因为验收条件已经全部满足。

继续优化代码风格，并不能显著推进原始目标。

如果连续多轮出现：

- 验收条件没有变化。
- 没有解决新的阻塞。
- 没有获取新的有效证据。
- 资源消耗持续增加。

那么 Runtime 就有理由触发一次进度审查。

但注意，**进度审查不一定意味着立即终止**。还可以要求模型重新规划、缩小任务范围，或者切换到其他未被阻塞的子任务。

## 四、你之前的想法可以升级成一个完整机制

<box border radius="lg" padding={3} gap={2}>
  <box background="surface-secondary" radius="md" padding={3} align="center">
    **执行一个子任务**
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **收集执行证据**
    <text color="secondary" size="xs">测试结果、状态变化、错误日志、资源消耗</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **检查任务进展**
    <text color="secondary" size="xs">是否更接近验收目标？是否获得新的有效信息？</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <grid columns={2} gap={2}>
    <grid-item>
      <box border radius="md" padding={3} gap={1} align="center">
        <icon name="check-circle" color="success"/>
        **进展正常**
        <text size="xs" color="secondary" textAlign="center">继续执行或完成子任务</text>
      </box>
    </grid-item>
    <grid-item>
      <box border radius="md" padding={3} gap={1} align="center">
        <icon name="refresh-cw" color="secondary"/>
        **进展停滞**
        <text size="xs" color="secondary" textAlign="center">重新规划、切换或升级处理</text>
      </box>
    </grid-item>
  </grid>
</box>

这样就将你原本的“到了期限还没有完成就调整任务”，扩展成了更可靠的**事件驱动 + 预算驱动的进度监控机制**。

---

## 五、下一轮追问：如何防止 Agent 伪造进展？

现在我给你一个更困难的例子。

Agent 正在修复登录 Bug，连续执行了十轮。

它每轮都向进度管理器报告：

> 本轮取得了重要进展。

例如：

- 第 1 轮：理解了代码结构。
- 第 2 轮：进一步理解了代码结构。
- 第 3 轮：优化了修复思路。
- 第 4 轮：重新分析了潜在问题。

但是实际上：

- 没有修改任何代码。
- 没有新增测试。
- 没有解决错误。
- 没有产生可验证的新证据。

**你会允许 LLM 自己决定 `progress = true` 吗？**

如果不允许，你准备让 Runtime 根据什么客观证据判断是否取得进展？

这里的核心是：**进度状态必须由证据支持，不能仅仅依赖模型的自我评价。**

## 074 · 用户 · 2026-10-20T08:21:52.208000+00:00

不允许，查验验收证据

## 075 · 助手 · 2026-10-20T08:22:07.105250+00:00

对，**不能让 LLM 自己宣布取得进展，应该由 Runtime 根据验收证据判断。**

不过，我想补充一个关键区别：

**任务完成证据（Completion Evidence）和任务推进证据（Progress Evidence）不完全一样。**

## 一、为什么要区分两种证据？

假设 Agent 正在修复一个 Bug。

| Agent 的行为 | 算不算进展？ | 原因 |
|---|---|---|
| 模型说“我理解问题了” | 不一定 | 缺乏新证据 |
| 修改了 100 行代码 | 不一定 | 修改量不代表质量 |
| 测试失败，但定位到具体异常 | 可以算 | 缩小了问题范围 |
| 修复一个失败测试 | 算 | 有明确验收进展 |
| 全部测试通过 | 算 | 满足了部分或全部验收条件 |
| 重复执行同一失败命令 | 通常不算 | 没有产生新信息 |

这就产生了一个比较实用的设计：

```python
class TaskProgress:
    completed_checks: list
    resolved_blockers: list
    new_evidence: list
    remaining_checks: list
```

Runtime 不直接接受模型报告的 `progress=True`，而是检查：

- 是否有新的验收条件通过？
- 是否有原有阻塞被解决？
- 是否获得新的有效证据？
- 是否减少了距离目标完成的实际工作？

其中，测试结果可以由程序检查，但“某条新证据是否真的有助于解决问题”有时仍需要模型辅助判断。

因此，**证据驱动不等于所有进展都能用简单的布尔表达式精确判断**。

---

## 二、把前面讨论的 Task 方案串起来

你已经提出了一套比较完整的长程任务管理思想：

<box border radius="lg" padding={3} gap={2}>
  <box background="surface-secondary" radius="md" padding={3} align="center">
    **Goal：用户最终目标与验收条件**
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **Planner：拆解子任务，制定计划**
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **Executor：执行当前子任务**
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **Verifier：检查实际证据与任务进度**
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box background="surface-secondary" radius="md" padding={3} align="center">
    **Task Manager：完成、继续、重规划或暂停**
  </box>
</box>

这里最重要的设计是：

- Goal 不允许 Agent 擅自改变。
- Plan 允许在约束内调整。
- State 需要持久化。
- Progress 必须有证据支持。
- Completion 必须满足验收条件。
- 长时间没有有效进展时，需要触发重规划或暂停。

这些机制可以让 Agent 即使经历 Context 压缩，也能从外部状态恢复目标与执行进度。

---

## 三、下一轮：如何防止验证器本身出错？

假设 Agent 修改代码后，运行了测试。

工具返回：

```json
{
  "exit_code": 0,
  "stdout": "100 passed"
}
```

Runtime 据此将当前任务标记为 `COMPLETED`。

但是现在发现：Agent 运行的是旧版本代码对应的测试，真正修改后的代码根本没有被测试到。

也就是说：

**验收工具的输出是真的，但它验证的对象错了。**

### 我的追问

如果由你设计 Task Manager，你会怎么确保：

**验收证据确实对应当前任务、当前代码版本和用户的真实验收要求？**

你可以从代码版本、测试执行环境、验收条件关联三个方向思考。

这道题解决后，我们就可以收束模块三的长程任务管理方案。

## 076 · 用户 · 2026-10-20T08:24:51.817000+00:00

核验测试内容以及测试版本以及用户真实目标需求

## 077 · 助手 · 2026-10-20T08:25:06.224839+00:00

对，你已经找到了三个关键检查对象：

1. **测试内容**：测试是否真正覆盖了当前修改的功能？
2. **测试版本**：测试是否针对最新代码执行？
3. **用户目标**：测试通过是否足以证明满足用户的实际要求？

这次回答方向正确。我们把它进一步转化成工程上可实现的机制，就可以结束模块三的讨论。

## 一、最重要的是建立“证据绑定”机制

假设 Agent 在 Git 项目中修复 Bug。

我们不能只记录：

```json
{
  "status": "PASSED",
  "tests": 100
}
```

而应该记录：

```json
{
  "task_id": "fix_login_001",
  "code_version": "commit_abc123",
  "test_suite": "login_regression_v2",
  "exit_code": 0,
  "passed": 100,
  "failed": 0,
  "acceptance_ids": [
    "AC_001",
    "AC_002"
  ]
}
```

这样，测试结果就与当前任务、代码版本和验收条件建立了关联。

但这里还有一个细节：Git Commit ID 只标识已经提交的版本。Agent 如果修改了尚未提交的文件，仅记录 Commit ID 还不够，需要额外记录工作区快照或文件哈希。

### 为什么要这样设计？

假设 Agent 在版本 V1 运行测试并通过，随后修改成 V2，却直接拿 V1 的测试结果宣布 V2 完成。

如果 Verifier 检查测试记录中的代码版本，就能发现：

```text
当前代码：V2
测试对象：V1
结果：STALE（证据已过期）
```

这时必须重新运行相关测试，而不是直接标记完成。

## 二、模块三的完整架构

<box border radius="lg" padding={3} gap={2}>
  <box background="surface-secondary" radius="md" padding={3} align="center">
    **Goal & Acceptance Criteria**
    <text color="secondary" size="xs">保存用户目标和验收标准</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **Planner & Task State**
    <text color="secondary" size="xs">任务拆解、依赖关系、进度持久化</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **Executor**
    <text color="secondary" size="xs">执行当前任务并产生实际结果</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **Evidence Verifier**
    <text color="secondary" size="xs">检查证据是否有效、匹配且满足验收条件</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box background="rgba(13,148,136,0.07)" border={{size:1,color:"rgba(13,148,136,0.4)"}} radius="md" padding={3} align="center">
    **Task Manager**
    <text color="secondary" size="xs">完成 / 继续 / 重规划 / 暂停</text>
  </box>
</box>

你现在已经能解释这套架构中每个组件存在的理由了。

---

## 三、模块三笔试答案初稿

<WritingBlock id="73618" variant="document">### 模块三·第 1 题：长程任务中如何防止 Agent 忘记目标？

我认为长程任务的核心问题不仅是 Context 过长导致信息丢失，还包括目标漂移、局部优化过度、错误地宣布完成，以及任务状态与真实执行结果不一致。

因此，我会采用 **Goal / Plan / State 分离 + 任务拆解 + 证据驱动验证 + 动态重规划** 的方案。

**1. 目标与计划分离**

用户的原始目标、约束和验收条件持久化保存，不能由 Agent 擅自修改。

Planner 将目标拆分成可执行的子任务，明确依赖关系和验收条件。计划允许随着执行结果动态调整，但修改必须服务于原始目标，并考虑成本、风险与收益。

**2. 任务状态管理**

使用结构化状态表记录当前任务、已完成任务、阻塞问题、下一步行动和资源预算。

即使 Context 被压缩，Agent 也能从持久化状态恢复执行，而不是依赖聊天历史重新推断任务进度。

**3. 进度监控**

不能简单以执行轮数、代码修改量或模型自我评价衡量进展。

需要结合验收条件变化、已解决的阻塞、新增有效证据，以及时间和 Token 消耗判断进展。

当持续消耗资源却没有有效进展时，触发重新规划、缩小范围、切换任务或暂停。

**4. 独立验收**

由 Verifier 检查实际执行证据，而不是直接相信模型声称任务完成。

对于代码任务，可以使用单元测试、集成测试、构建与静态检查等方式验收。

验收证据需要绑定任务 ID、代码版本、测试环境和具体验收条件，避免旧测试结果被用于证明新代码正确。

**5. 执行与终止**

Task Manager 根据验收结果决定继续、完成、重规划或暂停。

同时设置时间、Token 和工具调用预算，避免无限执行。

需要区分任务执行终止与任务成功完成：预算耗尽可以终止，但不能因此宣称成功。

**6. 方案取舍**

该方案能够提高长程任务的可靠性和可恢复性，但会引入额外的状态维护、验证成本和系统复杂度。

对于最小 Agent，可以先实现 Todo 计划、状态持久化和确定性验收；复杂任务再逐步引入自动重规划和独立 Verifier。</WritingBlock>

---

## 四、进入模块四：Tool / Session Runtime

我建议选择第 2 题：

> 如果 Session State 为 Busy，此时用户又发来新消息，或者异步工具完成事件也到达，Runtime 应该如何处理？

这道题非常适合结合你熟悉的 C++ 并发知识。

先给你一个具体场景。

用户在 Session A 中要求：

> 帮我搜索最近的 AI 新闻，然后生成一份报告。

Agent 调用了搜索工具，执行需要 20 秒。

此时：

- **T1**：Session A 处于 `BUSY`。
- **T2**：用户又发送：“报告不要太长，控制在 500 字以内。”
- **T3**：搜索工具完成，结果返回。
- **T4**：Runtime 准备继续调用 LLM 生成报告。

现在出现了两个事件：

**用户新消息**和**工具完成事件**。

它们可能几乎同时到达。

### 第一轮面试问题

如果你负责设计 Runtime：

**这两个事件应该如何处理，才能既不丢失用户的新要求，又不让工具结果与错误的任务状态混在一起？**

你可以从消息队列、Session 状态机、事件顺序和并发控制几个角度思考。

我们还是按之前的方式：你先提出自己的方案，我根据具体的并发情况继续追问。

## 078 · 用户 · 2026-10-20T08:29:22.635000+00:00

一个 Agent 实例可能同时面对多个事件：用户的新消息、工具返回的结果、定时器到期、另一个 Agent 的协作请求。如何高效而正确地处理这些事件，直接影响着性能和用户体验。

这套机制的骨架是并发编程里的**事件循环**（event loop）。可以把异步 Agent 看作一个长期运行的循环：每一轮从输入队列取出若干事件，追加到轨迹，调用一次 LLM，执行它决定的工具，再回到循环开头等待下一批事件——这与 Go 的 goroutine 从 channel 读取消息、在 `for { select { ... } }` 中逐轮处理是同一个结构。

在传统的同步接口实现中，**事件在每轮循环的边界被消费**。当 LLM 正在推理、工具正在执行时，新事件先在队列中等待，待本轮到达一个**安全点**（一段推理结束、一次工具返回）再处理。原生异步则允许在模型仍在思考或输出时接收新要求，由系统选择合适的时机继续处理。两者都有边界，只是边界由不同层管理。工具取消仍需执行器响应取消信号，类似 Go 中对 `ctx.Done()` 的检查；接收一条“停止”消息本身不会撤销已经发生的动作。

理解了这一点，下面先以兼容同步接口的事件循环说明三种处理策略：让事件等到下一个自然出现的安全点（队列式）、主动提前制造一个安全点（取消式），还是另起一个循环、不必等待主循环的安全点（并行式）。原生 steering 的接续方式将在后文展开。

**事件的结构化建模。**

处理的前提是理解。通用 Agent 面对的输入不只来自用户一个人——第三方发来的消息不是用户发给 Agent 的，但 Agent 需要理解它、评估其重要性、决定如何介入。这要求将每个输入都建模为包含丰富语义的**结构化事件**：

- **来源（谁）**：用户本人、联系人、陌生人、系统通知
- **渠道（方式）**：电话语音、短信、即时消息、邮件、社交媒体、定时器触发、异步工具调用结果、命令行监控状态更新
- **内容（什么）**：消息文本、情感色彩、紧急程度、是否需要回复
- **上下文（背景）**：是对之前某个对话的回复还是新发起的沟通，与当前任务的关联

以一封客户退款请求邮件为例，结构化事件的具体形式如下：

**自动换行复制**
```json
{
  "source": {"type": "email", "sender": "[REDACTED_EMAIL]"},
  "channel": "gmail_webhook",
  "content": {"subject": "退款请求", "body": "订单 #12345 希望退款..."},
  "context": {"priority": "high", "customer_tier": "vip", "related_orders": ["#12345"]}
}
```

只有当这些维度被清晰地建模为结构化事件，Agent 才能在多方通信中保持清晰的认知，避免将用户输入误当成工具结果，或将藏有指令的工具结果误认为用户指令而导致提示注入。多线程上下文管理的复杂性还要求 Agent 理解多个对话线程之间的关联——来自第三方的消息如何影响用户的情绪，用户在不同对话中扮演什么角色，何时需要将不同线程的信息综合起来提供建议。

从 n8n 等工作流平台的触发器生态可以看到，Webhook、定时器、邮件、数据库变更、文件监听——每一种触发器都是 Agent 感知世界的一个 “感官”。当这些异构的事件被统一建模为结构化格式之后，Agent 就能以一致的方式处理来自不同来源的刺激，下文的紧急度判定和处理策略也都建立在这一统一建模之上。

**基于紧急度的动态处理策略。**

人类在处理多个任务时，会根据紧急程度采取不同的策略。面对突发的紧急情况，会立即停下手头的工作；面对常规的待办事项，则加入任务列表稍后处理。Agent 的事件处理也应体现这种智能性。

[图6-2 异步事件处理的三种策略](https://bojieli.github.io/ai-agent-book/astro/figures/book/book/fig6-2-light.svg)

图6-2 异步事件处理的三种策略 · [来源图](https://bojieli.github.io/ai-agent-book/astro/book/images/fig6-2.svg "图6-2 异步事件处理的三种策略")

**取消式处理（Cancellation-Based）** 用于紧急事件，其本质是为紧急事件**提前制造一个安全点**：主动中断当前步骤，把这一刻变成可以消费新事件的边界。当紧急事件到达时（如用户点击 “停止” 或监督系统发来高优先级指令）：(1) 停止当前操作——如果 LLM 正在推理，立即取消流式响应；如果有同步工具在执行，发送取消信号；(2) 清空待处理队列，将所有事件取出；(3) 将队列中的事件和紧急事件一起追加到轨迹末尾；(4) 立即重新调用 LLM，以更新后的完整轨迹为输入来评估局势。例如，用户在 Agent 执行可能错误的操作时输入 “停！我说错了”，Agent 会立即看到这条新输入，重新理解真实意图，从而避免执行错误的操作。

**队列式处理（Queued）** 用于常规事件。当非紧急事件到达时（如异步工具返回结果或用户发来补充信息）：(1) 将事件放入队列末尾，不打断当前操作；(2) 等待当前操作完成——让 LLM 完成推理，让同步工具执行完毕；(3) 当任何工具调用完成并返回 `tool.result` 时，检查队列，如果队列非空则将所有事件一次性追加到轨迹；(4) LLM 综合处理更新后的轨迹。这实现了批量处理，提高了效率，例如 Agent 调用搜索工具后，在等待期间用户补充了 “只看最近一个月的结果”，这条补充信息进入队列，搜索结果返回时两个事件一起呈现给 LLM，避免了不必要的往返。

**并行处理（Parallel）** 用于独立的轻量级查询。比如 Agent 正在分析大量数据时，用户突然问 “今天天气怎么样？” 此类查询具有三个特征：与主任务无关、需要快速响应、执行成本低。既不应该用取消式处理（会打断重要的主任务），也不应该用队列式处理（让用户等太久）。系统首先判断查询的独立性和复杂度，然后在一个并行的推理会话中独立执行，调用必要的工具生成响应后立即返回。查询和响应会追加到主任务的轨迹中，并明确标记为 “与主任务并行执行”，以避免 LLM 混淆。

**紧急度的判定。**

紧急事件：用户中断（`user.interrupt`）、监督指令（`supervisor.instruction`）、Agent 间中断（`agent.interrupt`）、标记为紧急的外部触发器（如系统告警、支付失败）。

非紧急事件：常规用户输入（`user.input`）、Agent 输入（`agent.input`）、工具结果（`tool.result`）、定时器触发（`timer.trigger`）、常规外部触发器。

硬编码的规则有其局限性，事件的语义决定了处理方式——“马上停下来” 用取消式、“今天天气怎么样” 用并行式、“报告需要用中文发给我” 用队列式。**建议使用轻量级的分类 LLM 作为事件路由器**，在事件到达时快速判断应该采用哪种策略。

取消点必须是工具或推理能够安全收尾的位置；未完成的工具结果用显式占位符表示，不能伪造成功。

## 079 · 助手 · 2026-10-20T08:29:37.215451+00:00

你贴的这段内容，正好回答了我们模块四的核心问题：

> 当 Session 处于 Busy 状态时，新用户消息、异步工具结果和其他事件应该如何处理？

这段内容来自《AI Agent 书》的异步事件处理部分。我认为它提出了很好的设计框架：**队列式、取消式、并行式**。

不过，我们不能直接将书中的方案当成生产级实现。尤其是并发安全、事件顺序和工具取消，实际工程里还有一些容易忽略的问题。

## 一、先理解三种策略的本质

<box gap={3}>
  <row align="start" gap={3}>
    <box background="surface-secondary" radius="lg" padding={2}>
      <icon name="list-end" size="lg"/>
    </box>
    <box flex="1" gap={1}>
      **Queued：排队执行**

      当前操作继续运行，新消息进入队列，在下一个安全点统一处理。

      适合：“报告控制在 500 字以内。”

      **优点**：简单、稳定，不容易破坏当前执行状态。

      **缺点**：新消息可能需要等待。
    </box>
  </row>
  <divider color="subtle"/>
  <row align="start" gap={3}>
    <box background="surface-secondary" radius="lg" padding={2}>
      <icon name="octagon-x" size="lg"/>
    </box>
    <box flex="1" gap={1}>
      **Cancellation：取消执行**

      用户要求停止时，向当前模型请求或工具发送取消信号，等待安全停止。

      适合：“立即停止，不要继续修改代码。”

      **优点**：可以及时响应用户的重要指令。

      **缺点**：取消不等于回滚。工具可能已经产生副作用。
    </box>
  </row>
  <divider color="subtle"/>
  <row align="start" gap={3}>
    <box background="surface-secondary" radius="lg" padding={2}>
      <icon name="git-branch" size="lg"/>
    </box>
    <box flex="1" gap={1}>
      **Parallel：并行处理**

      对独立请求启动另一个执行分支，不等待当前任务结束。

      适合：正在生成报告时，用户询问一个无关的简单事实。

      **优点**：降低用户等待时间。

      **缺点**：共享状态、执行结果合并和资源竞争更复杂。
    </box>
  </row>
</box>

### 这里我想特别纠正书中的一个简化假设

书中提出，可以让轻量分类模型判断事件应该进入哪种策略。

这个思路可以作为辅助，但**不应该让模型直接决定是否中断关键操作、取消工具或绕过 Session 锁**。

原因是模型的分类结果不一定可靠。

更稳妥的分工是：

- Runtime：负责权限、状态机、事件顺序和取消规则。
- 轻量模型：辅助识别用户意图、事件语义。
- Tool Executor：执行工具，并报告实际执行状态。

例如，“停止”可以优先走确定性控制通道，而不是必须等待另一个 LLM 判断。

---

## 二、真正的难点不是分类，而是并发状态一致性

假设 Session 正在搜索 AI 新闻。

<box border radius="lg" padding={3} gap={2}>
  <text weight="medium">同一 Session 的事件时间线</text>
  <svg viewBox="0 0 330 215" width="100%">
    <path d="M42 20 V200" stroke="#94a3b8" strokeWidth="2"/>
    {#each [{y:28,t:"T1",s:"Agent 开始 Search",c:"#64748b"},{y:79,t:"T2",s:"用户：报告控制在 500 字",c:"#d97706"},{y:130,t:"T3",s:"Search 工具完成",c:"#0d9488"},{y:181,t:"T4",s:"Runtime 准备继续 LLM",c:"#64748b"}] as e}
      <circle key={e.t} cx="42" cy={e.y} r="6" fill={e.c}/>
      <text x="12" y={e.y+4} fontSize="11" fill="currentColor">{e.t}</text>
      <text x="60" y={e.y+4} fontSize="12" fill="currentColor">{e.s}</text>
    {/each}
  </svg>
</box>

你希望在 T4 时，模型同时知道：

- Search 返回了什么。
- 用户要求报告不超过 500 字。

这里的关键不是谁先进入队列，而是：

**Runtime 能否保证这两个事件都被正确记录、归属于对应任务，并在下一轮模型调用前形成一致的 Context。**

例如工具完成事件至少要包含：

```json
{
  "event_id": "evt_102",
  "type": "tool.completed",
  "session_id": "session_A",
  "run_id": "run_001",
  "tool_call_id": "call_003",
  "status": "success",
  "result_ref": "artifact_008"
}
```

为什么需要 `run_id` 和 `tool_call_id`？

因为一个 Session 可能执行过多个任务，一个任务也可能调用多个工具。

只有 `session_id`，还不足以确定这个工具结果属于哪一次执行。

---

## 三、我建议你的最小 Agent 先采用单 Session 串行模型

对于你目前的笔试项目，不必立即实现完整的并行 Agent。

可以采用以下结构：

<box border radius="lg" padding={3} gap={2}>
  <box background="surface-secondary" radius="md" padding={3} align="center">
    **Event Queue**
    <text color="secondary" size="xs">用户消息、工具结果、取消信号</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **Session Event Processor**
    <text color="secondary" size="xs">同一 Session 串行处理状态变化</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **Agent Runtime**
    <text color="secondary" size="xs">构建 Context、调用 LLM、调度工具</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box background="surface-secondary" radius="md" padding={3} align="center">
    **State Store + Trace**
    <text color="secondary" size="xs">持久化状态、工具关联关系与执行证据</text>
  </box>
</box>

这其实与并发编程中的 Actor Model 有相似之处：

**一个 Session 对应一个逻辑上的状态所有者，所有会修改它的事件都经过同一个串行处理入口。**

它并不要求所有 Session 都串行。Session A 和 Session B 完全可以并发运行。

---

## 四、现在进入真正的架构面试追问

先不讨论异步编程语法。

假设：

1. Session A 正在调用一个支付工具，状态为 `BUSY`。
2. 用户突然发送：“停止支付！”
3. Runtime 收到取消信号。
4. 支付 API 已经收到请求，但是还没有返回结果。
5. Runtime 取消了本地等待，支付 API 可能已经扣款成功。

这时如果按照书里简单的取消式逻辑：

> 停止当前操作，重新调用 LLM。

就会存在一个严重风险：**LLM 可能以为支付没有发生，从而再次调用支付工具，导致重复扣款。**

### 我的追问

**你会怎样设计 Runtime，让取消操作既能尽快响应用户，又不会把已经执行过的支付再次执行？**

你可以结合之前学过的：

- 幂等性（Idempotency Key）
- 工具执行状态
- 事务和状态持久化
- 不确定结果的查询与恢复

这次重点不是讲多少概念，而是用自己的话说明：**从用户点击停止，到 Runtime 最终确认支付状态，应该发生什么？**

## 080 · 用户 · 2026-10-20T08:36:51.929000+00:00

查验状态幂等查询，同一操作使用同一key，即使超时重试也不切换别的key，防止系统认为是不同操作，数据库使用唯一性索引

## 081 · 助手 · 2026-10-20T08:37:09.620151+00:00

你的回答抓住了两个关键点：**先查询实际执行状态，以及使用幂等键防止重复执行。**

我给这次回答 **88/100**。你已经理解了幂等性的核心，但还差一个重要问题：

**幂等性能够防止同一操作重复执行，却不能保证取消已经发生的操作。**

## 一、先看你的方案为什么有效

假设用户要求支付 100 元，Runtime 生成：

```json
{
  "operation_id": "pay_001",
  "idempotency_key": "key_abc123",
  "amount": 100,
  "status": "PENDING"
}
```

之后 Runtime 调用支付 API，但请求超时。

此时不能直接认为支付失败，因为可能出现：

- 支付请求没有到达服务器。
- 支付请求到达服务器，但没有执行。
- 支付已经成功，只是响应丢失。

所以你提出的“查询状态，再决定如何处理”是正确的。

如果确实需要重试同一笔支付，也应该复用原来的幂等键，并保持请求参数一致。

### 但唯一索引有一个前提

你说数据库使用唯一索引，这很有价值，例如：

```sql
CREATE UNIQUE INDEX idx_payment_key
ON payments(idempotency_key);
```

这样可以在本地数据库中防止重复创建相同的操作记录。

不过，**本地数据库的唯一索引不能自动约束外部支付平台**。

外部支付 API 也必须支持相应的幂等机制，或者提供可可靠查询的业务订单号，系统才能协调外部副作用。

---

## 二、我建议把异步工具状态设计得更明确

<box border radius="lg" padding={3} gap={2}>
  <svg viewBox="0 0 330 265" width="100%">
    <g fill="none" stroke="#94a3b8" strokeWidth="1.7">
      <path d="M165 45 V74 M160 67 L165 74 L170 67"/>
      <path d="M165 116 V144 M160 137 L165 144 L170 137"/>
      <path d="M119 166 H64 V209 M59 202 L64 209 L69 202"/>
      <path d="M211 166 H266 V209 M261 202 L266 209 L271 202"/>
      <path d="M165 187 V209 M160 202 L165 209 L170 202"/>
    </g>
    <rect x="106" y="7" width="118" height="38" rx="8" fill="#64748b" fillOpacity=".14" stroke="#94a3b8"/>
    <rect x="106" y="76" width="118" height="40" rx="8" fill="#64748b" fillOpacity=".14" stroke="#94a3b8"/>
    <rect x="106" y="145" width="118" height="42" rx="8" fill="#d97706" fillOpacity=".12" stroke="#d97706"/>
    <rect x="5" y="209" width="118" height="40" rx="8" fill="#0d9488" fillOpacity=".10" stroke="#0d9488"/>
    <rect x="126" y="209" width="78" height="40" rx="8" fill="#dc2626" fillOpacity=".09" stroke="#dc2626"/>
    <rect x="207" y="209" width="118" height="40" rx="8" fill="#d97706" fillOpacity=".12" stroke="#d97706"/>
    <g fontSize="12" textAnchor="middle" fill="currentColor">
      <text x="165" y="31">PENDING</text>
      <text x="165" y="100">RUNNING</text>
      <text x="165" y="170">确认执行结果</text>
      <text x="64" y="233">SUCCEEDED</text>
      <text x="165" y="233">FAILED</text>
      <text x="266" y="233">UNKNOWN</text>
    </g>
  </svg>
  <caption>简化的工具状态示意。取消请求是控制事件，不应直接等同于 FAILED 或 CANCELED。</caption>
</box>

注意 `UNKNOWN` 状态。

例如支付 API 超时，Runtime 不知道是否扣款成功时，应该先记录：

```json
{
  "operation_id": "pay_001",
  "status": "UNKNOWN",
  "cancel_requested": true
}
```

随后通过支付平台的订单查询接口确认状态。

如果查询仍然无法确定，就保持 `UNKNOWN`，进入有限次数的状态核对或人工处理流程，不能盲目重复支付。

**这里要区分“停止等待工具”和“撤销工具已经产生的副作用”。**

前者 Runtime 可以主动控制；后者通常需要外部服务提供取消、退款或补偿接口。

---

## 三、你还需要考虑一个细节：迟到的工具结果

现在我们把问题从支付扩展到一般的异步 Agent。

假设：

- T1：Agent 发起 `search`，工具请求 ID 是 `call_001`。
- T2：用户点击停止，Runtime 取消当前 Run。
- T3：用户重新提出一个新任务，Runtime 开始 `run_002`。
- T4：之前的 `call_001` 突然返回结果。

**这里就有一个典型的异步事件竞争问题。**

如果 Runtime 只根据 `session_id` 将工具结果加入 Context，那么 `call_001` 的结果就可能错误地进入新任务。

因此，你之前理解的 Session 隔离还需要更细一层：

\[
\text{Session ID} \rightarrow \text{Run ID} \rightarrow \text{Tool Call ID}
\]

工具完成事件不仅要属于正确的 Session，还必须属于正确的执行轮次和工具请求。

---

## 四、下一轮架构面试

现在假设 `run_001` 已经取消，`run_002` 正在执行，而旧工具 `call_001` 返回了成功结果。

**你会怎么处理这个迟到的结果？**

我希望你回答两点：

1. 是否应该直接丢弃这个结果？还是保存到执行日志？
2. 如何确保它不会污染 `run_002` 的 Context，但又不会丢失可能有价值的执行证据？

这题解决后，我们就可以把 Busy Session、事件队列、取消、幂等性和异步结果处理串成完整架构。

## 082 · 用户 · 2026-10-20T08:43:27.653000+00:00

判断价值吧， 2.不知道

## 083 · 助手 · 2026-10-20T08:43:41.655011+00:00

你说“判断价值”，这个思路不是错的，但在异步 Runtime 中，**处理迟到的工具结果，第一步不是判断它有没有价值，而是判断它属于谁、是否还被允许影响当前状态。**

这是一个很重要的工程原则：**执行结果的有效性和它的业务价值是两回事。**

## 一、先理解为什么不能直接丢弃

假设发生了以下事件：

| 时间 | 事件 |
|---|---|
| T1 | `run_001` 调用 `search` |
| T2 | 用户取消 `run_001` |
| T3 | 新任务 `run_002` 开始 |
| T4 | `run_001` 的搜索结果返回 |

如果是普通搜索，直接忽略迟到结果似乎没什么问题。

但如果工具是支付、发送邮件或者修改文件呢？

即使用户取消了任务，外部操作也可能已经成功。

因此我们应该区分两个问题：

- **结果是否需要记录？** 通常需要，至少记录其执行状态和证据。
- **结果是否允许加入新任务的 Context？** 默认不允许。

## 二、生产级思路：结果归属检查 + 状态隔离

可以给每次工具调用保存三个标识：

```python
class ToolResult:
    session_id: str
    run_id: str
    tool_call_id: str
    status: str
    result: dict
```

比如迟到的结果：

```json
{
  "session_id": "session_A",
  "run_id": "run_001",
  "tool_call_id": "call_001",
  "status": "success",
  "result": {
    "items": 10
  }
}
```

现在 `session_A` 正在执行 `run_002`。

Runtime 收到结果以后：

<box border radius="lg" padding={3} gap={2}>
  <box background="surface-secondary" padding={3} radius="md" align="center">
    **收到 ToolResult**
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **验证 Session / Run / ToolCall 身份**
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **检查原 Run 的状态**
    <text size="xs" color="secondary">RUNNING、CANCELLED、COMPLETED？</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <grid columns={2} gap={2}>
    <grid-item>
      <box border radius="md" padding={3} align="center" gap={1}>
        <icon name="check-circle" color="success"/>
        **仍属于有效 Run**
        <text size="xs" color="secondary" textAlign="center">提交结果，继续执行</text>
      </box>
    </grid-item>
    <grid-item>
      <box border radius="md" padding={3} align="center" gap={1}>
        <icon name="archive" color="secondary"/>
        **属于已取消 Run**
        <text size="xs" color="secondary" textAlign="center">记录结果，不直接注入新 Run</text>
      </box>
    </grid-item>
  </grid>
</box>

这里的检查必须在受保护的状态更新中完成，避免“刚检查 Run 还有效，提交时却已经取消”的竞态。

### 关键思想：状态隔离

`run_001` 的工具结果只能更新属于 `run_001` 的执行记录。

除非有明确的状态协调逻辑，否则不能直接更新 `run_002` 的消息历史。

可以理解为数据库里的事务隔离：

> 不能因为两个任务属于同一个 Session，就允许它们互相修改对方的执行状态。

---

## 三、那么旧结果有没有价值？

这才是你提出的“判断价值”真正发挥作用的地方。

例如 `run_001` 搜索了 AI 新闻，但用户取消后，`run_002` 又要求生成同一主题的报告。

之前的搜索结果可能仍然有价值。

但应该经过一个明确的复用流程：

1. 旧工具结果进入 Artifact Store，保留来源、时间和状态。
2. `run_002` 根据当前任务决定是否检索旧结果。
3. 检查它是否仍然相关、未过期且允许复用。
4. 通过显式引用将其加入 `run_002` 的 Context。

也就是说：

**旧结果可以被新任务复用，但不能未经检查就自动混入新任务。**

对于支付、邮件等具有副作用的工具，还需要独立处理执行状态，不能仅仅把迟到结果当作可丢弃的上下文数据。

---

## 四、下一轮：一个真正的并发问题

假设同一 Session 中：

- `run_001` 已取消。
- `run_002` 正在执行。
- `call_001` 的迟到结果到达。
- 用户又发送了一条新消息。

这三个事件几乎同时发生。

如果 Runtime 使用多个线程分别修改 Session State，就可能出现竞争条件。

**我的问题是：你会选择让多个线程直接修改 Session State，并通过锁保护，还是采用单 Session 事件队列，让所有状态修改串行执行？为什么？**

这次你可以结合熟悉的 C++ 多线程、锁竞争和事件循环来思考，重点讨论两种方案的正确性、复杂度和性能取舍。

## 084 · 用户 · 2026-10-20T08:52:55.341000+00:00

各有优缺点吧，根据情况来决定，串行化的问题就是失去了并行，而且容易被长任务拖住 ；锁的话就是切换开销，锁竞争

## 085 · 助手 · 2026-10-20T08:53:09.706606+00:00

你的分析方向是对的，但有一个关键点需要纠正：

**单 Session 状态串行化，不等于 Agent 的所有任务都必须串行执行。**

这是理解 Agent Runtime 并发架构非常重要的一步。

## 一、先评价你提到的优缺点

| 你的观点 | 我的评价 |
|---|---|
| 串行化失去了并行能力 | 部分正确，取决于串行化的范围 |
| 串行处理容易被长任务阻塞 | 正确，但可以通过异步执行解决 |
| 锁存在竞争和切换开销 | 正确 |
| 应该根据情况选择 | 正确，但需要进一步明确选择标准 |

你当前的理解有一个容易产生的误区：把**状态修改的串行化**和**任务执行的串行化**混在了一起。

其实可以分开设计。

## 二、生产级架构：状态串行，工具并行

举个例子，Session A 正在运行三个工具：

- Search：需要 3 秒。
- ReadDocs：需要 5 秒。
- Calculator：需要 1 秒。

我们完全可以让这三个工具并行运行，但由同一个事件处理器负责更新 Session 状态。

<box border radius="xl" padding={3} gap={2}>
  <svg viewBox="0 0 330 297" width="100%">
    <rect x="79" y="6" width="172" height="37" rx="8" fill="#64748b" fillOpacity=".14" stroke="#94a3b8"/>
    <text x="165" y="29" textAnchor="middle" fontSize="13" fill="currentColor">Session A Event Loop</text>
    <path d="M165 43 V68 M160 61 L165 68 L170 61" stroke="#94a3b8" strokeWidth="1.6" fill="none"/>
    <rect x="79" y="69" width="172" height="38" rx="8" fill="#64748b" fillOpacity=".12" stroke="#94a3b8"/>
    <text x="165" y="92" textAnchor="middle" fontSize="13" fill="currentColor">Dispatch 工具任务</text>
    <g stroke="#94a3b8" strokeWidth="1.6" fill="none">
      <path d="M165 107 V124 H56 V145 M165 124 V145 M165 124 H274 V145"/>
    </g>
    {#each [{x:5,t:"Search",d:"3 秒"},{x:115,t:"ReadDocs",d:"5 秒"},{x:225,t:"Calculator",d:"1 秒"}] as a}
      <rect key={a.t} x={a.x} y="145" width="100" height="53" rx="8" fill="#0d9488" fillOpacity=".1" stroke="#0d9488"/>
      <text x={a.x+50} y="167" fontSize="12" fontWeight="600" fill="currentColor" textAnchor="middle">{a.t}</text>
      <text x={a.x+50} y="185" fontSize="11" fill="currentColor" textAnchor="middle">{a.d}</text>
    {/each}
    <g stroke="#94a3b8" strokeWidth="1.6" fill="none">
      <path d="M55 198 V218 H165 M165 198 V218 M275 198 V218 H165 M165 218 V237"/>
      <path d="M160 231 L165 237 L170 231"/>
    </g>
    <rect x="52" y="238" width="226" height="44" rx="9" fill="#d97706" fillOpacity=".1" stroke="#d97706"/>
    <text x="165" y="256" textAnchor="middle" fontSize="13" fontWeight="600" fill="currentColor">Session 状态更新</text>
    <text x="165" y="272" textAnchor="middle" fontSize="11" fill="currentColor">按事件逐个提交，避免并发写入</text>
  </svg>
  <caption>工具并发运行，但所有状态变更经过统一入口。完成事件可以按实际完成顺序进入队列，必要时等待依赖满足后再继续模型执行。</caption>
</box>

这里并没有牺牲工具执行的并行性。

例如三个工具可以同时执行，总耗时接近最慢工具的 5 秒，而不是简单相加的 9 秒（忽略调度开销）。

Event Loop 只负责快速处理事件和修改状态，**不会同步等待工具执行完成**。

这实际上与 C++ 的 Reactor、事件驱动服务器的设计有相似之处。

## 三、两种方案真正的 Trade-off

| 比较 | 多线程 + 锁 | 单 Session 事件处理器 |
|---|---|---|
| 状态一致性 | 需要正确设计锁和事务 | 更容易维护顺序 |
| 工具并发 | 支持 | 同样支持 |
| 编程复杂度 | 容易出现锁顺序、竞态问题 | 需要管理队列和异步事件 |
| 长任务阻塞 | 取决于锁的持有范围 | 取决于是否阻塞事件处理器 |
| 多实例部署 | 需要额外协调机制 | 同样需要租约、分片或持久化队列等机制 |
| 适合你的笔试 | 可行 | 我更推荐 |

注意：事件队列并不会自动解决所有并发问题。例如多个进程同时消费同一个 Session 的事件时，仍然需要保证单一所有者，或者使用数据库版本控制。

---

## 四、我的建议：你的 Agent Harness 采用混合架构

**同一个 Session 的状态更新串行化，不同 Session 可以并发执行，耗时工具在独立执行器里运行。**

这个方案同时保留了：

- 简单的 Session 状态一致性。
- 多 Session 并发。
- 工具异步执行能力。
- 新用户消息及时进入队列。
- 迟到结果有明确的处理入口。

对于最小 Agent，可以从进程内队列开始，不必立即引入 Redis、Kafka 等复杂组件。

---

## 五、下一轮面试：事件顺序与用户意图

现在假设 Session A 正在执行：

> 搜索新闻并生成 500 字报告。

发生两个事件：

- **T1**：工具 Search 已经执行完毕，`tool.completed` 事件入队。
- **T2**：用户发送：“不用生成报告了，直接把搜索结果给我。”
- **T3**：Event Loop 开始处理队列。

如果严格按照 FIFO 顺序，Runtime 可能先处理工具完成事件，并立即调用 LLM 开始生成报告，之后才看到用户的新要求。

**我的问题是：你会怎样设计安全点和事件处理策略，避免 Agent 刚开始生成一份用户已经不需要的报告？**

提示：FIFO 保证的是事件顺序，不保证事件处理符合用户最新意图。

你可以从**批量消费、事件优先级、任务状态变更**三个方向思考。

## 086 · 用户 · 2026-10-20T11:07:23.559000+00:00

不知道

## 087 · 助手 · 2026-10-20T11:07:40.069765+00:00

这道题确实有点难，因为它涉及一个容易忽视的问题：

**事件到达的顺序，不等于 Agent 应该执行动作的顺序。**

你前面已经理解了 Event Loop、FIFO 队列和 Session 状态。现在只需要补充一个机制：**安全点（Safe Point）+ 批量消费（Batch Drain）+ 任务状态校验**。

## 一、为什么普通 FIFO 不够？

假设：

- T1：Search 完成，结果进入队列。
- T2：用户说：“不要报告了，直接给搜索结果。”
- T3：Runtime 开始处理事件。

如果采用最简单的写法：

```python
while True:
    event = queue.get()

    process(event)

    if event.type == "tool.completed":
        call_llm()
```

这里的 `queue.get()` 是从队列取出事件。

问题出在：

```python
if event.type == "tool.completed":
    call_llm()
```

Runtime 刚处理完搜索结果，就立刻调用模型。

但实际上队列里还有用户的新要求。

<box border radius="lg" padding={3} gap={2}>
  <text weight="medium">错误的处理顺序</text>
  <box background="surface-secondary" radius="md" padding={3} align="center">处理 Search 完成事件</box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">立刻调用 LLM 生成报告</box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box background="rgba(220,38,38,0.07)" border={{size:1,color:"rgba(220,38,38,0.4)"}} radius="md" padding={3} align="center">
    <icon name="alert-circle" color="danger" inline/> 后来才处理用户的新要求
  </box>
</box>

这不是队列的 FIFO 机制出了问题，而是**Runtime 太早开始下一轮模型调用**。

## 二、解决方案：到安全点时，先收集事件，再调用 LLM

可以这样设计：

<box border radius="lg" padding={3} gap={2}>
  <box background="surface-secondary" radius="md" padding={3} align="center">
    **工具完成，Runtime 到达安全点**
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **批量取出当前已到达的事件**
    <text color="secondary" size="xs">工具结果 + 用户补充消息</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **更新 Session 状态**
    <text color="secondary" size="xs">保存工具结果，修改报告生成要求</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **确认当前任务目标**
    <text color="secondary" size="xs">用户不再需要报告，只需要搜索结果</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box background="rgba(13,148,136,0.08)" border={{size:1,color:"rgba(13,148,136,0.4)"}} radius="md" padding={3} align="center">
    **调用 LLM，按照最新要求回答**
  </box>
</box>

这就叫作在安全点进行事件合并。

注意，批量消费不是无限等待新事件。通常只处理一个有界批次或当前队列快照，否则用户持续发消息可能导致模型永远无法继续执行。

### 一个简化的 Python 伪代码

```python
async def on_safe_point(session):
    events = drain_pending_events(session.queue)

    for event in events:
        apply_event(session, event)

    if session.should_continue:
        await call_llm(session.context)
```

你只需要理解三个函数：

- `drain_pending_events()`：取出目前等待处理的一批事件。
- `apply_event()`：根据事件更新任务状态。
- `call_llm()`：状态更新完成后才调用模型。

`async` 和 `await` 是 Python 的异步语法，可以先理解成让程序在等待耗时操作时，有机会处理其他任务。

实际实现还需要保证事件落库、状态更新与下一步调度之间的一致性。

---

## 三、这里还有更深的一层：事件优先级

并不是所有事件都应该按照相同方式处理。

例如：

| 事件 | 优先处理方式 |
|---|---|
| 用户要求停止 | 尽快触发取消控制 |
| 用户修改当前任务要求 | 在下一次执行决策前应用 |
| 工具执行完成 | 记录结果，等待必要依赖 |
| 普通通知 | 可以排队处理 |

**优先级决定处理时机，但不意味着可以随意改变历史顺序。**

例如，用户在支付完成之后才发送“停止”，系统不能假装停止指令比支付更早发生。

要保留真实事件顺序，再按照事件语义决定后续动作。

另外还有一个竞态边界：即使 Runtime 清空了当前队列，用户也可能恰好在下一次 LLM 调用前发送消息。

所以批量消费只能减少无效工作，不能保证完全避免它。更严格的实现可以使用任务版本号，在正式执行下一步有副作用的操作之前再次校验。

---

## 四、再来一道简单一点的检验题

假设 Agent 正在搜索新闻。

用户连续发送：

> 消息 A：帮我生成 1000 字报告。

> 消息 B：改成 500 字。

> 消息 C：算了，不要报告，直接返回搜索结果。

三个消息都已进入队列，Runtime 正准备调用 LLM。

**你觉得 Runtime 应该把三条消息全部原样交给 LLM，让模型自己理解，还是先由程序更新结构化任务状态，最终告诉模型“直接返回搜索结果”？**

为什么？

这个问题没有必要考虑复杂代码。只需要从**准确性、Token 成本、用户意图变化**三个角度想一想。

## 088 · 用户 · 2026-10-20T11:11:35.012000+00:00

没有必要把三条消息都交给llm；因为任务AB改变了，这两个已经不符合用户的需要了，如果再执行就是平白浪费token

## 089 · 助手 · 2026-10-20T11:11:50.213867+00:00

你的判断正确，而且你抓住了一个重要原则：

**Agent 应该执行用户当前有效的意图，而不是机械地执行历史上的每一条指令。**

不过有一个细节需要完善：**不需要把三条消息全部交给 LLM，不代表可以把它们从历史记录中删除。**

## 一、为什么你的方案更合理？

三条消息实际上是在不断修改同一个任务：

| 事件 | 任务状态变化 |
|---|---|
| A：生成 1000 字报告 | `output_mode = report`，`max_words = 1000` |
| B：改成 500 字 | `max_words = 500` |
| C：不要报告，返回搜索结果 | `output_mode = search_results` |

最终状态：

```python
task_state = {
    "output_mode": "search_results",
    "search_completed": True
}
```

这里甚至不需要保留 `max_words = 500` 这个参数，因为用户已经取消了报告生成需求。

但 A、B、C 三个事件仍然应当保存在 Event Log 中，用于审计、恢复以及理解用户为什么改变目标。

这与你之前提出的 **Event Log + State Table + Context** 设计完全一致。

## 二、但是存在一个重要风险

假设用户发送：

> A：帮我生成报告。

> B：改成 500 字。

> C：另外，再把搜索结果保存到文件。

这时 C 不是覆盖 A 和 B，而是**增加了一个新要求**。

因此不能简单采用“最新消息覆盖之前所有消息”。

Runtime 需要区分三种更新：

- **REPLACE**：替换旧要求，例如 1000 字改成 500 字。
- **CANCEL**：取消旧要求，例如不再生成报告。
- **ADD**：增加新要求，例如额外保存文件。

简单的字段更新可以由程序完成；如果自然语言含义不明确，可以由 LLM 生成候选状态变更，再由 Runtime 校验。

对于用户原始消息，最好保留来源与事件 ID，避免模型错误理解后无法追溯。

---

## 三、继续架构面试：更难的并发场景

现在假设 Agent 正在执行三个工具：

```text
Search：搜索新闻
ReadDocs：阅读文档
SaveFile：保存报告
```

当前状态：

- Search 已完成。
- ReadDocs 正在执行。
- SaveFile 尚未开始。

此时用户说：

> 不要生成报告了，直接把搜索结果发给我。

你的方案会更新任务状态，取消报告生成要求。

**我的追问是：**

你会怎么处理这三个工具？

特别考虑：

1. Search 已完成，它的结果怎么办？
2. ReadDocs 正在执行，要不要取消？
3. SaveFile 还没开始，是否应该执行？

这里想考察的是：**当用户改变任务目标时，Runtime 如何判断哪些已完成工作可以保留、哪些正在执行的工作应该取消、哪些尚未开始的工作应该跳过。**

你可以用自己的话回答，不需要写代码。

## 090 · 用户 · 2026-10-20T11:14:20.267000+00:00

1.返回结果 2，尝试停止 3.删除

## 091 · 助手 · 2026-10-20T11:14:35.265012+00:00

你的三个处理方向基本正确。尤其是第二点“**尝试停止**”，而不是“直接停止”，说明你已经意识到异步工具的取消不一定能成功。

不过，第三点我建议稍微调整：

**对于尚未执行的工具，应该取消调度，而不是直接删除任务记录。**

## 一、逐项分析你的答案

| 工具 | 你的方案 | 更完整的工程处理 |
|---|---|---|
| Search（已完成） | 返回结果 | 保留已完成结果，并根据新目标返回给用户 |
| ReadDocs（执行中） | 尝试停止 | 发送取消信号，等待执行器确认；处理可能迟到的结果 |
| SaveFile（未开始） | 删除 | 标记为 `SKIPPED` 或 `CANCELLED`，不再调度执行 |

为什么第三种不直接删除？

因为如果直接删除记录，我们可能无法回答：

> 用户原本要求保存报告，为什么最终没有生成文件？

保留状态可以让 Trace 解释：用户修改了需求，因此系统主动跳过这个工具。

## 二、用状态机表示会更清楚

<box border radius="lg" padding={3} gap={2}>
  <text weight="medium">用户修改目标后，各工具的状态变化</text>
  <box background="surface-secondary" radius="md" padding={3} gap={1}>
    <row align="center" justify="between">
      **Search**
      <badge color="success">SUCCEEDED</badge>
    </row>
    <text size="sm" color="secondary">保留搜索结果，直接用于最终回答</text>
  </box>
  <box background="surface-secondary" radius="md" padding={3} gap={1}>
    <row align="center" justify="between">
      **ReadDocs**
      <badge color="warning">CANCEL_REQUESTED</badge>
    </row>
    <text size="sm" color="secondary">请求取消，但最终状态可能是 CANCELLED、SUCCEEDED 或 UNKNOWN</text>
  </box>
  <box background="surface-secondary" radius="md" padding={3} gap={1}>
    <row align="center" justify="between">
      **SaveFile**
      <badge>SKIPPED</badge>
    </row>
    <text size="sm" color="secondary">尚未执行，跳过调度并记录用户变更原因</text>
  </box>
</box>

这套规则背后有一个核心判断：

**用户修改任务目标后，Runtime 应当重新评估每个工具与新目标的依赖关系，而不是简单取消所有工具。**

例如 Search 已完成且结果仍有用，就不必浪费时间重新搜索。

---

## 三、这里还需要补充一个容易遗漏的并发细节

假设 `ReadDocs` 收到取消信号，但由于工具不支持取消，它最后依然返回成功。

这时候不能让旧的 ReadDocs 结果重新触发报告生成。

因此可以为任务维护一个版本：

```python
task_version = 3
```

当用户改变目标时：

```python
task_version = 4
```

ReadDocs 原本属于版本 3，即使执行成功，也只能更新对应的工具执行记录。

它不能直接推动版本 4 的任务继续生成旧报告。

这里与你之前学习的数据库乐观锁思路是相通的。

---

## 四、最后一道综合追问

我们已经讨论了：

- Busy Session 的事件队列
- 工具并发与状态串行
- 取消式、队列式、并行式处理
- 幂等性
- 迟到工具结果
- 用户意图更新
- 工具状态与任务版本

现在假设发生以下情况：

1. 用户要求 Agent 搜索资料并保存报告。
2. Agent 正在执行 `ReadDocs`。
3. 用户取消报告生成，改为只返回搜索结果。
4. Runtime 发出取消信号，`ReadDocs` 迟迟没有响应。
5. 用户又发送一个完全独立的新请求：“帮我计算 `(12+8)*5`。”

**你会让计算请求等待 ReadDocs 完全结束，还是允许计算请求并行执行？**

如果允许并行，我希望你考虑一个问题：

**怎样确保新计算任务不会被旧 ReadDocs 的迟到结果污染，同时两个任务又可以共享同一个用户的长期 Memory？**

这道题是模块四的综合考察。你可以先给出架构选择，再解释状态隔离的思路。

## 092 · 用户 · 2026-10-20T11:16:40.493000+00:00

允许计算并行执行 ，没太懂为什么新计算任务为什么会被被旧 ReadDocs 的迟到结果污染，而且两个任务不就是共享同一个用户的长期 Memory吗

## 093 · 助手 · 2026-10-20T11:16:55.918449+00:00

你的疑问是合理的。**如果两个任务的执行状态完全隔离，那么旧 ReadDocs 的结果确实不会污染新计算任务。**

我上一轮实际上省略了一个前提：两个任务可能共享同一个 Session 的消息历史。如果 Runtime 的隔离设计不完善，才会出现污染。

而你说的第二点也对：**同一用户的多个任务可以共享长期 Memory，但不应该因此共享所有临时执行状态。**

我们用一个例子讲清楚。

## 一、什么叫迟到结果污染？

假设你设计的 Agent 只有一个 Session 消息列表：

```python
messages = []
```

当前发生：

<box border radius="lg" padding={3} gap={2}>
  <text weight="medium">两个任务并行执行</text>
  <svg viewBox="0 0 330 213" width="100%">
    <text x="85" y="20" fontSize="13" fontWeight="600" textAnchor="middle" fill="currentColor">旧任务 A</text>
    <text x="248" y="20" fontSize="13" fontWeight="600" textAnchor="middle" fill="currentColor">新任务 B</text>
    <path d="M85 30 V200 M248 30 V200" stroke="#94a3b8" strokeDasharray="4 4"/>
    <rect x="17" y="40" width="136" height="39" rx="7" fill="#64748b" fillOpacity=".13" stroke="#94a3b8"/>
    <text x="85" y="64" fontSize="12" textAnchor="middle" fill="currentColor">ReadDocs 执行中</text>
    <rect x="180" y="91" width="136" height="39" rx="7" fill="#0d9488" fillOpacity=".10" stroke="#0d9488"/>
    <text x="248" y="115" fontSize="12" textAnchor="middle" fill="currentColor">Calculator 计算中</text>
    <rect x="17" y="145" width="136" height="39" rx="7" fill="#d97706" fillOpacity=".12" stroke="#d97706"/>
    <text x="85" y="169" fontSize="12" textAnchor="middle" fill="currentColor">旧结果迟到</text>
    <path d="M154 163 H180" stroke="#dc2626" strokeWidth="2" strokeDasharray="4 3"/>
    <path d="M174 158 L181 163 L174 168" fill="none" stroke="#dc2626" strokeWidth="2"/>
    <text x="248" y="153" fontSize="10" textAnchor="middle" fill="#dc2626">错误写入 B 的 Context</text>
  </svg>
</box>

如果 ReadDocs 完成后的回调这样写：

```python
def on_tool_completed(result):
    session.messages.append(result)
```

问题就出现了。

它没有检查工具结果属于哪个 Run，而是直接写入共享的 Session 历史。

于是计算任务调用 LLM 时，可能看到：

```text
用户：计算 (12+8)*5

工具：ReadDocs 找到了登录系统的设计文档……
```

这就是**上下文污染（Context Contamination）**。

注意，这不是因为两个任务并行本身有问题，而是因为**旧工具结果被错误地写入新任务的上下文**。

---

## 二、解决方案：区分 User、Session、Run、ToolCall

你之前已经理解 Session 隔离，现在只需要再增加 Run 这一层。

<box border radius="lg" padding={3} gap={2}>
  <box background="surface-secondary" radius="md" padding={3} align="center">
    **User A**
    <text size="xs" color="secondary">拥有长期 Memory</text>
  </box>
  <row justify="center"><icon name="git-fork" color="secondary"/></row>
  <grid columns={2} gap={2}>
    <grid-item>
      <box border radius="md" padding={3} gap={2} align="center">
        **Run 001**
        <text size="xs" color="secondary">旧报告任务</text>
        <box background="surface-secondary" radius="md" padding={2} width="100%">
          <text size="xs" textAlign="center">ReadDocs：迟到结果</text>
        </box>
      </box>
    </grid-item>
    <grid-item>
      <box border radius="md" padding={3} gap={2} align="center">
        **Run 002**
        <text size="xs" color="secondary">新计算任务</text>
        <box background="surface-secondary" radius="md" padding={2} width="100%">
          <text size="xs" textAlign="center">Calculator：100</text>
        </box>
      </box>
    </grid-item>
  </grid>
  <row justify="center"><icon name="arrow-up" color="secondary"/></row>
  <box background="rgba(13,148,136,0.08)" border={{size:1,color:"rgba(13,148,136,0.4)"}} radius="md" padding={3} align="center">
    **Shared Long-term Memory**
    <text size="xs" color="secondary">仅按权限和相关性读取，不混合 Run 状态</text>
  </box>
</box>

实际系统中的关系可以是：

```text
User A
  ├── Long-term Memory
  └── Session 001
       ├── Run 001
       │    └── ToolCall ReadDocs
       └── Run 002
            └── ToolCall Calculator
```

这里有一个非常重要的区别：

| 数据 | 是否允许共享 |
|---|---|
| 用户长期学习偏好 | 可以，在权限允许且相关时使用 |
| 用户长期项目背景 | 可以，但需要检查项目范围 |
| Run 001 的工具执行状态 | 不能直接交给 Run 002 |
| Run 001 的迟到工具结果 | 保存原 Run；需要时显式复用 |
| Run 002 的临时消息和进度 | 属于 Run 002 |

即使两个 Run 在同一 Session 内并发，Runtime 也应该给它们各自建立执行上下文。之后如何合并回 Session 历史，还需要明确的顺序和提交规则。

## 三、共享 Memory 为什么通常没有问题？

因为这里的 Memory 应该是一个**受控的共享数据源**，而不是两个任务同时随意修改的全局变量。

例如：

```python
memory = memory_store.search(
    user_id="user_A",
    query="用户的计算偏好"
)
```

Run 001 和 Run 002 都可以查询同一个用户的长期记忆。

但如果两个 Run 同时准备修改同一条 Memory，就又会产生并发更新问题，需要版本控制、事务或者串行写入。

所以最简洁的原则是：

**长期 Memory 可以共享，执行状态必须隔离；共享记忆的写入也必须受一致性控制。**

---

## 四、回到你的回答

你选择允许计算并行执行，我同意。

对于这个场景，计算任务与旧报告任务没有数据依赖，也没有必须互斥的副作用，因此完全可以独立处理。

但这里还有一个前提：如果用户明确要求取消整个 Session，而不只是取消报告任务，Runtime 就要重新检查新任务是否允许启动。

### 最后一道小追问

假设：

- Run 001 正在读取技术文档。
- Run 002 正在计算数学表达式。
- 两者使用同一个用户的长期 Memory。
- Run 001 发现用户现在更喜欢 Python，于是更新长期偏好。
- Run 002 在它更新之前，已经读取了旧记忆：“用户更喜欢 C++”。

**Run 002 是否必须立即放弃当前执行，重新读取最新 Memory？**

还是可以继续使用已经读取的旧版本？

你可以从**数据一致性、任务相关性和执行成本**三个角度考虑。这个问题就是模块四中“共享长期 Memory”和“并发任务隔离”之间最后一个重要取舍。

## 094 · 用户 · 2026-10-20T11:20:22.122000+00:00

首先我觉得相比“可以继续使用已经读取的旧版本？”这个更前的是怎么知道002读取的是旧的版本呢？

## 095 · 助手 · 2026-10-20T11:20:36.927240+00:00

你这个反问非常关键。你发现了我上一个问题中隐含的前提：

**系统首先要能识别 Memory 发生了变化，才谈得上要不要重新读取。**

这本质上又回到了你熟悉的数据库并发控制问题：**版本管理与一致性**。

## 一、怎么知道 Run 002 读的是旧记忆？

最简单的方案就是给 Memory 增加版本号。

假设数据库中保存着：

```text
Memory ID: preference_001
Content: 用户更喜欢 C++
Version: 10
```

Run 002 读取时，不仅获取内容，还记录版本：

```python
memory = {
    "content": "用户更喜欢 C++",
    "version": 10
}
```

随后 Run 001 更新这条记忆：

```text
Memory ID: preference_001
Content: 用户现在更喜欢 Python
Version: 11
```

这样就出现了两个版本。

<box border radius="lg" padding={3} gap={2}>
  <svg viewBox="0 0 340 175" width="100%">
    <text x="83" y="20" textAnchor="middle" fontSize="13" fontWeight="600" fill="currentColor">Run 002</text>
    <text x="257" y="20" textAnchor="middle" fontSize="13" fontWeight="600" fill="currentColor">Memory Store</text>
    <rect x="12" y="38" width="142" height="49" rx="8" fill="#64748b" fillOpacity=".13" stroke="#94a3b8"/>
    <text x="83" y="59" textAnchor="middle" fontSize="12" fill="currentColor">已读取 V10</text>
    <text x="83" y="76" textAnchor="middle" fontSize="11" fill="currentColor">偏好：C++</text>
    <rect x="186" y="38" width="142" height="49" rx="8" fill="#0d9488" fillOpacity=".1" stroke="#0d9488"/>
    <text x="257" y="59" textAnchor="middle" fontSize="12" fill="currentColor">当前 V11</text>
    <text x="257" y="76" textAnchor="middle" fontSize="11" fill="currentColor">偏好：Python</text>
    <path d="M154 63 H186" stroke="#d97706" strokeWidth="1.7" strokeDasharray="4 3"/>
    <path d="M257 87 V113 H83 V87" fill="none" stroke="#94a3b8" strokeWidth="1.7"/>
    <rect x="57" y="113" width="226" height="46" rx="8" fill="#d97706" fillOpacity=".1" stroke="#d97706"/>
    <text x="170" y="132" textAnchor="middle" fontSize="12" fontWeight="600" fill="currentColor">版本比较：10 ≠ 11</text>
    <text x="170" y="148" textAnchor="middle" fontSize="11" fill="currentColor">Run 002 持有过期快照</text>
  </svg>
</box>

但注意：**记录版本号并不会自动通知 Run 002。**

只有发生以下操作之一，Run 002 才会知道：

- 主动向 Memory Store 查询当前版本。
- Memory Store 在更新时发送变更通知。
- Runtime 在关键执行点检查 Memory 版本。

## 二、生产环境中，我更倾向于版本号 + 安全点检查

没必要每次 Memory 更新，都立刻中断所有正在运行的 Agent。

可以让 Run 002 继续执行，并在重要的安全点检查。

例如：

```python
# Run 002 最初读取的记忆版本
loaded_version = 10

# 下一次关键决策前检查
current_version = memory_store.get_version("preference_001")

if loaded_version != current_version:
    print("Memory 已发生变化")
```

这段代码只是版本检测，还没有决定必须重新读取。

原因是：**记忆变化不一定影响当前任务。**

例如 Run 002 正在计算 `(12+8)*5`，用户的 Python/C++ 偏好发生变化，对计算结果没有任何影响。

这种情况下重新调用 LLM，反而浪费资源。

但如果 Run 002 正在根据用户偏好生成一个 C++ 项目，新的偏好可能影响后续决策，就值得重新检查。

## 三、可以借鉴数据库 MVCC 的思想

你之前学过 MVCC，这里正好能联系起来。

Run 002 可以持有一份 Memory 快照：

```text
Run 002:
  Memory Snapshot: V10
```

与此同时，Memory Store 更新到 V11。

两者可以同时存在。

核心思想是：

**允许正在执行的任务继续读取一致的旧快照，同时让新任务读取更新后的记忆。**

但 Memory 和数据库事务不同：Agent 的执行可能持续很久，因此不能简单照搬数据库的快照隔离，把旧偏好永久视为有效。

在关键决策之前，仍可能需要重新验证。

---

## 四、现在回到我原本的问题

假设：

- Run 002 正在根据用户偏好开发一个 C++ 项目。
- 它读取了 V10：“用户更喜欢 C++”。
- Run 001 更新了 V11：“用户现在更喜欢 Python”。
- Runtime 在安全点检测到版本发生变化。

**你会让 Run 002 立即重新规划，把项目改成 Python，还是继续使用 C++？**

这里我想提醒你一个细节：

**“用户更喜欢 Python”和“用户明确要求这个项目必须使用 C++”不是同一级别的约束。**

所以除了版本是否最新，你还需要考虑新记忆的作用范围以及当前任务是否已有明确约束。

这次可以重点从**版本变化 → 相关性检查 → 是否影响当前任务 → 是否需要重规划**这四步来回答。

## 096 · 用户 · 2026-10-20T11:24:21.723000+00:00

核心就是是否影响本任务吧，任务如果已经规定好了使用C++，肯定不会因为用户喜欢python而改变的

## 097 · 助手 · 2026-10-20T11:24:36.424956+00:00

对，这次你抓住了核心：**Memory 是否需要更新到当前 Run，不取决于版本是否变化，而取决于变化是否影响当前任务的决策。**

你的判断是正确的。

## 一、把你的思路整理成工程规则

假设：

- Run 002 正在开发 C++ 项目。
- 用户在当前任务中明确要求使用 C++。
- 长期 Memory 从“偏好 C++”更新为“偏好 Python”。

那么：

| 判断维度 | 结果 |
|---|---|
| Memory 版本发生变化？ | 是 |
| 新记忆与编程语言有关？ | 是 |
| 与当前任务存在直接冲突？ | 表面上存在 |
| 当前任务有明确约束？ | 有，必须使用 C++ |
| 是否重新规划？ | 不需要 |

这里最重要的区别是：

**用户的长期偏好是默认参考，当前任务的明确要求是具体约束。**

因此，Runtime 可以记录 Memory 版本变化，但没有必要中断当前任务，更不应该擅自将 C++ 改成 Python。

### 可以归纳成一个决策流程

<box border radius="lg" padding={3} gap={2}>
  <box background="surface-secondary" radius="md" padding={3} align="center">
    **检测到 Memory 版本变化**
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **判断新记忆是否影响当前任务**
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **检查当前任务的明确约束与作用范围**
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <grid columns={2} gap={2}>
    <grid-item>
      <box border radius="md" padding={3} gap={1} align="center">
        <icon name="check-circle" color="success"/>
        **不影响**
        <text color="secondary" size="xs" textAlign="center">继续当前执行</text>
      </box>
    </grid-item>
    <grid-item>
      <box border radius="md" padding={3} gap={1} align="center">
        <icon name="refresh-cw" color="secondary"/>
        **影响**
        <text color="secondary" size="xs" textAlign="center">刷新相关记忆，必要时重新规划</text>
      </box>
    </grid-item>
  </grid>
</box>

有一个边界需要注意：如果新消息不仅表达了偏好，而且是用户明确要求修改**当前任务**，那它应该作为任务变更事件处理，不能仅当作普通 Memory 更新。

---

## 二、模块四可以收束了

我们已经讨论了：

- Session Busy 时的事件队列。
- 队列式、取消式、并行式处理。
- 工具执行与 Session 状态更新分离。
- `Session ID → Run ID → ToolCall ID` 的归属管理。
- 幂等性与迟到工具结果。
- 用户修改任务后的事件合并和重新调度。
- 并发任务共享长期 Memory 时的版本一致性。

整个方案可以归纳为：

**单 Session 状态串行化 + 工具异步执行 + 结构化事件队列 + Run 隔离 + 版本控制。**

它能在保持状态一致性的同时，避免耗时工具阻塞整个 Agent。

还有一个重要的取舍：对于强依赖同一 Session 状态的任务，应优先串行处理；对于独立任务，可以使用隔离的 Run 并行执行，但要明确结果的提交与合并规则。

## 三、接下来进入最后一个模块

模块五：Agent Runtime 架构对比。

我建议选择第 1 题：

> Claude Code 的工具输出方式和国内 GLM / 豆包等 OpenAI-compatible function calling 有什么不同？他们各自这样设计的优缺点是什么？

这道题与之前的 Runtime、ToolRegistry、消息结构和模型适配器都有直接关系。

不过在开始前，我想先确认你的基础理解。

**你认为 Function Calling 的本质是什么？**

例如模型输出：

```json
{
  "name": "calculator",
  "arguments": {
    "expression": "1+2"
  }
}
```

究竟是模型自己执行了 `calculator`，还是 Runtime 根据模型生成的结构化请求执行了工具？

另外，你觉得不同模型厂商的 Function Calling 格式差异，应该由 Agent Runtime 直接处理，还是设计一层独立的 Model Adapter 统一转换？

先回答这两个问题，我们再深入比较 Claude Code、GLM 和豆包的具体工具调用协议与工程取舍。

## 098 · 用户 · 2026-10-20T11:26:22.110000+00:00

我觉得function calling的本质是函数调用，是Runtime 根据模型生成的结构化请求执行了工具；我觉得设计一层独立的 Model Adapter 统一转换

## 099 · 助手 · 2026-10-20T11:26:37.188516+00:00

你的两个判断都正确。尤其是第二点：**通过 Model Adapter 屏蔽不同模型厂商的协议差异，让 Agent Runtime 专注于执行逻辑。**

不过第一个概念可以再精确一点：

**Function Calling 的本质不是模型执行函数，而是模型生成结构化的工具调用意图，由外部 Runtime 负责验证和执行。**

例如：

```python
response = model.generate(messages, tools=tool_schemas)

for call in response.tool_calls:
    result = registry.execute(call)
```

这里有三个角色：

- **Model**：决定调用什么工具、传什么参数。
- **Model Adapter**：把不同厂商的响应转换成统一数据结构。
- **Runtime**：验证并调度工具，管理执行状态和后续循环。

但模块五真正考察的，不只是 Function Calling 的格式，而是**不同工具交互协议如何影响 Agent Runtime 的架构设计**。

## 一、先看两种协议究竟有什么不同

这里需要先区分：**Claude Code 是 Agent 产品，Claude Messages API 是模型接口。** 我们能够明确比较的是 Anthropic 官方 API 协议与 OpenAI-compatible Chat Completions 协议；Claude Code 在工具执行、输出截断、权限管理等方面还包含额外的 Harness 逻辑。

### 1. Anthropic：工具调用与结果使用 Content Block

Claude 返回工具请求：

```json
{
  "stop_reason": "tool_use",
  "content": [
    {
      "type": "tool_use",
      "id": "call_001",
      "name": "calculator",
      "input": {"expression": "1+2"}
    }
  ]
}
```

程序执行后，结果以 `tool_result` 内容块形式返回：

```json
{
  "role": "user",
  "content": [
    {
      "type": "tool_result",
      "tool_use_id": "call_001",
      "content": "3"
    }
  ]
}
```

这里有个很容易误解的点：

**工具结果虽然放在 `role=user` 消息中，但它仍然是工具结果，不是普通用户指令。**

因为内容块明确标记了 `type=tool_result`，并通过 `tool_use_id` 关联原始请求。<Cite ref="turn667340search0"/>

### 2. OpenAI-compatible：工具调用通常使用独立字段与消息角色

以下是 Chat Completions 风格的简化示例：

模型返回：

```json
{
  "finish_reason": "tool_calls",
  "message": {
    "role": "assistant",
    "tool_calls": [
      {
        "id": "call_001",
        "type": "function",
        "function": {
          "name": "calculator",
          "arguments": "{\"expression\":\"1+2\"}"
        }
      }
    ]
  }
}
```

执行后返回：

```json
{
  "role": "tool",
  "tool_call_id": "call_001",
  "content": "3"
}
```

这里将工具结果表示为独立的 `tool` 角色，而不是 Anthropic 的 `user` 消息内部内容块。

OpenAI 当前的 API 也不只有这一种形式，Responses API 还使用独立的工具调用和结果项目。<Cite ref="turn667340search5"/>

### 3. 核心差异对照

| 维度 | Anthropic Messages | OpenAI-compatible Chat Completions |
|---|---|---|
| 请求工具 | `tool_use` 内容块 | `tool_calls` 字段 |
| 参数 | `input` 对象 | `arguments` JSON 字符串 |
| 返回工具结果 | `tool_result` 内容块 | `role=tool` 消息 |
| 关联请求 | `tool_use_id` | `tool_call_id` |
| 模型调用工具的结束标记 | `stop_reason=tool_use` | `finish_reason=tool_calls` |
| 主要设计特点 | 统一的类型化内容块 | 明确区分消息角色和工具字段 |

以上是协议结构差异，不能仅凭这些区别断言哪家模型工具调用更准确、性能更好。

## 二、Model Adapter 为什么是正确的架构选择？

你正在开发的 Agent Harness 可以这样组织：

<box border radius="lg" padding={3} gap={2}>
  <box background="surface-secondary" radius="md" padding={3} align="center">
    **Agent Runtime**
    <text color="secondary" size="xs">只认识内部 ModelResponse / ToolCall</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **统一 Model Adapter 接口**
  </box>
  <row justify="center"><icon name="git-fork" color="secondary"/></row>
  <grid columns={2} gap={2}>
    <grid-item>
      <box background="surface-secondary" radius="md" padding={3} align="center">
        **Anthropic Adapter**
        <text size="xs" color="secondary">解析 tool_use / tool_result</text>
      </box>
    </grid-item>
    <grid-item>
      <box background="surface-secondary" radius="md" padding={3} align="center">
        **OpenAI-compatible Adapter**
        <text size="xs" color="secondary">解析 tool_calls / role=tool</text>
      </box>
    </grid-item>
  </grid>
</box>

Runtime 不必知道底层模型来自 Claude、GLM 还是豆包。

但 Adapter 必须知道不同厂商的参数结构、消息角色、结束原因和工具结果格式。

此外，统一接口并不代表抹平所有能力差异。比如 Anthropic API 的服务器端工具可能由平台执行，而不是由你本地执行。<Cite ref="turn667340search2"/>

---

## 三、下一轮架构追问：统一抽象会不会丢失信息？

假设你的内部模型只支持：

```python
class ModelResponse:
    content: str
    tool_calls: list[ToolCall]
    finish_reason: str
```

而 Claude 返回一个消息，里面交错包含：

```text
文本块：我先检查文件
工具调用块：read_file(...)
文本块：然后检查测试
工具调用块：run_tests(...)
```

如果你的 Adapter 把全部文本拼接成一个 `content`，再把工具调用单独存进 `tool_calls`，那么原始内容块之间的顺序关系就丢失了。

**我的追问是：**

你会继续使用这个简单的 `ModelResponse`，接受部分信息损失，还是把内部协议升级成类似 Content Block 的有序结构？

为什么？

这就是模块五最值得讨论的 Trade-off：**统一抽象带来开发便利，但抽象过度可能丢失底层模型的重要能力。**

## 100 · 用户 · 2026-10-20T11:29:07.091000+00:00

什么是content block

## 101 · 助手 · 2026-10-20T11:29:21.188039+00:00

**Content Block（内容块）就是把模型的一条消息拆成多个有类型的小部分。**

你可以把它理解为：**Message 是一个容器，Content Block 是容器里面的具体内容。**

## 一、先看普通 Message

你之前写 Agent 时，可能使用这样的消息结构：

```python
message = {
    "role": "assistant",
    "content": "我需要先读取文件，然后执行测试"
}
```

这里的 `content` 是一个字符串。

这意味着一条消息只能简单地保存一段文字。

但实际 Agent 不仅要输出文字，还可能需要：

- 输出普通文本
- 请求调用工具
- 接收图片、文档等不同类型的数据
- 表达多个工具请求

所以可以将 `content` 设计成一个**有序的内容块列表**。

## 二、Content Block 是怎么工作的？

假设你对 Claude 说：

> 检查 `runtime.py`，然后运行测试。

模型可能返回这样的简化消息：

```json
{
  "role": "assistant",
  "content": [
    {
      "type": "text",
      "text": "我先读取源代码。"
    },
    {
      "type": "tool_use",
      "id": "call_001",
      "name": "read_file",
      "input": {
        "path": "runtime.py"
      }
    }
  ]
}
```

注意 `content` 不再是字符串，而是数组。

<box border radius="xl" padding={3} gap={2}>
  <box background="surface-secondary" radius="lg" padding={3} align="center">
    **Message**
    <text size="xs" color="secondary">role = assistant</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="lg" padding={3} gap={2}>
    <text weight="medium" size="sm">content：有序列表</text>
    <box background="rgba(13,148,136,0.08)" radius="md" padding={3} gap={1}>
      <row align="center" gap={2}>
        <icon name="file-text" color="success"/>
        **Block 1：text**
      </row>
      <text size="sm">我先读取源代码。</text>
    </box>
    <box background="rgba(217,119,6,0.08)" radius="md" padding={3} gap={1}>
      <row align="center" gap={2}>
        <icon name="wrench" color="secondary"/>
        **Block 2：tool_use**
      </row>
      <text size="sm">read_file(path="runtime.py")</text>
    </box>
  </box>
</box>

模型其实是在说：

“这是我要说的话，这是我要调用的工具。”

这里有个重要细节：**内容块可以在同一条消息中按顺序出现，但不代表模型已经执行工具，也不代表第二个工具会自动等待第一个工具的执行结果。** 实际的工具执行和依赖调度仍由 Runtime 负责。

## 三、为什么不直接使用 `content + tool_calls`？

这是我们架构题的核心。

假设模型输出：

```text
Block 1：文本 A
Block 2：工具调用 X
Block 3：文本 B
Block 4：工具调用 Y
```

使用 Content Block 可以保留原来的顺序：

<box gap={2}>
  <box border radius="lg" padding={3} gap={2}>
    <text weight="medium" size="sm">Content Block 表示</text>
    <row gap={1} align="center">
      {#each [{t:"文本 A",c:"#0d9488"},{t:"工具 X",c:"#d97706"},{t:"文本 B",c:"#0d9488"},{t:"工具 Y",c:"#d97706"}] as b,i}
        <box key={i} flex="1" background={b.c+"18"} border={{size:1,color:b.c}} radius="md" padding={2} align="center">
          <text size="xs" weight="medium">{b.t}</text>
        </box>
      {/each}
    </row>
  </box>
  <box border radius="lg" padding={3} gap={2}>
    <text weight="medium" size="sm">简单的 content + tool_calls 表示</text>
    <box background="surface-secondary" radius="md" padding={2}>
      <text size="sm">content = "文本 A 文本 B"</text>
    </box>
    <box background="surface-secondary" radius="md" padding={2}>
      <text size="sm">tool_calls = [工具 X, 工具 Y]</text>
    </box>
    <text color="secondary" size="xs">原始的交错顺序被丢失。</text>
  </box>
</box>

这说明 Content Block 的一个优势：**能够统一表示多种内容类型，并保留消息内部的顺序信息。**

不过，对于只需要最终文本和工具调用列表的最小 Agent，这种额外结构不一定带来实际收益。

## 四、放在你的 Agent Harness 中理解

目前你项目的 `ModelResponse` 是：

```python
class ModelResponse(BaseModel):
    finish_reason: Literal[
        "stop", "tool_calls", "length", "refusal", "error"
    ]
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    reasoning_summary: str | None = None
```

这种设计有明显优势：

- 简单，容易实现。
- Runtime 不需要理解各种 Content Block。
- 非常适合完成最小 Agent 笔试要求。

但如果将来你需要支持复杂多模态消息、交错内容块或更完整的流式事件，就可能需要扩展内部消息协议。

---

## 五、回到我们的架构面试

现在你已经知道：

**Message 是消息，Content Block 是消息内部的一个有类型的内容单元。**

我想问你一个取舍问题：

你现在的 Agent Harness 只需要支持 Calculator、Search、Todo 三种工具，不需要图片、音频等复杂内容。

你会：

**A.** 继续使用当前的 `content + tool_calls`，保持 Runtime 简单。

**B.** 从一开始就设计统一的 Content Block 列表，为以后扩展复杂功能做准备。

你更倾向哪种方案？为什么？

重点考虑：**当前需求、未来扩展性、实现复杂度**。

## 102 · 用户 · 2026-10-20T11:32:38.722000+00:00

A；实现简单可以满足当前要求，缺点就是未来扩展性差

## 103 · 助手 · 2026-10-20T11:32:52.730375+00:00

我认可你选 A。**对于当前的 Agent Harness 笔试，优先满足需求、控制复杂度，比提前设计一套完整的 Content Block 系统更合理。**

这体现了一个工程原则：**不要为了尚未出现的需求提前支付过高的抽象成本。**

不过，“未来扩展性差”不一定是 A 的必然结果，关键在于你怎么设计 Adapter。

## 一、我会进一步优化你的方案

你当前使用：

```python
class ModelResponse(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCall]
    finish_reason: str
```

可以保持这个结构不变，但做好两件事：

1. **协议转换集中在 Model Adapter**：Runtime 不直接处理 Anthropic 或 OpenAI 的原始 JSON。
2. **明确抽象能力边界**：当前只支持纯文本和 Function Calling。未来需要多模态或交错内容块时，再升级内部协议。

这样既保证了当前实现简单，也降低了后续重构成本。

你的答案可以进一步表达为：

> 我会采用简单的 `content + tool_calls` 作为最小 Agent 的内部协议，因为它已经覆盖当前所有功能需求。通过独立的 Model Adapter 隔离厂商差异，避免 Runtime 与特定 API 耦合。
>
> 代价是无法完整表示复杂的交错 Content Block 和多模态信息。如果未来确实有这些需求，再通过扩展统一消息模型来支持，而不是一开始过度设计。

## 二、但是还有一个你需要理解的关键问题

**Model Adapter 不仅需要解析模型输出，还必须正确还原下一轮请求。**

例如 Claude 返回：

```json
{
  "type": "tool_use",
  "id": "call_001",
  "name": "calculator",
  "input": {"expression": "1+2"}
}
```

你的 Adapter 转换成：

```python
ToolCall(
    id="call_001",
    name="calculator",
    arguments={"expression": "1+2"}
)
```

Runtime 调用 Calculator，得到结果 `3`。

现在问题来了：

下一轮请求 Claude 时，必须按照 Anthropic 协议，把原始工具请求和对应的 `tool_result` 正确编码回消息历史。

不能直接把内部的：

```python
Message(
    role="tool",
    content="3",
    tool_call_id="call_001"
)
```

原样发送给 Claude，因为 Anthropic Messages API 使用的工具结果格式不是独立的 `tool` 角色。

这意味着 Adapter 实际上需要双向转换：

<box border radius="lg" padding={3} gap={2}>
  <box background="surface-secondary" radius="md" padding={3} align="center">厂商原生响应</box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **Adapter：解析**
    <text color="secondary" size="xs">转换成统一 ModelResponse</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">Runtime：执行工具，记录消息</box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **Adapter：编码**
    <text color="secondary" size="xs">转换成厂商要求的消息格式</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box background="surface-secondary" radius="md" padding={3} align="center">下一次模型请求</box>
</box>

## 三、下一轮面试：Adapter 发生信息损失怎么办？

假设你用当前简单结构接入 Claude。

Claude 返回包含文本、工具调用和其他类型的内容块。

你的 Adapter 只提取 `content` 和 `tool_calls`，忽略它当前不认识的其他内容。

随后 Runtime 执行工具，再通过 Adapter 将消息重新编码回 Claude API。

**我的问题是：你会允许 Adapter 直接丢弃不认识的内容块吗？**

如果丢弃，可能损坏后续对话所需的信息；如果一律报错，又会影响兼容性。

你可以从两个角度思考：

1. 哪些信息可以安全丢弃，哪些必须保留？
2. 当前 Runtime 不支持的能力，Adapter 应该如何处理才能既简单又可靠？

这道题解决后，我们就可以完成模块五，并总结整套架构设计笔试的五个模块。

## 104 · 用户 · 2026-10-20T11:34:58.328000+00:00

不知道

## 105 · 助手 · 2026-10-20T11:35:13.188147+00:00

这个问题确实有一定难度，因为它涉及 **Agent Runtime 的兼容性设计和协议安全性**。

我先给你一个核心原则：

**Adapter 可以简化信息的表示方式，但不能悄悄丢弃影响协议正确性的信息。**

这叫作 *Fail Fast（快速失败）* 和 *Graceful Degradation（优雅降级）* 之间的取舍。

## 一、为什么不能直接丢弃未知 Content Block？

假设 Claude 返回：

```json
{
  "content": [
    {
      "type": "text",
      "text": "正在分析问题"
    },
    {
      "type": "tool_use",
      "id": "call_001",
      "name": "calculator",
      "input": {"expression": "1+2"}
    },
    {
      "type": "unknown_block",
      "data": "某些协议要求保留的信息"
    }
  ]
}
```

你的 Adapter 只认识 `text` 和 `tool_use`。

如果直接忽略第三个 Block，可能出现两种情况：

- 第三个 Block 只是可选的辅助信息，忽略后不影响执行。
- 第三个 Block 包含后续请求必须保留的信息，忽略后可能导致 API 拒绝请求，或者改变模型行为。

关键是：**既然 Adapter 不认识它，就不能确定丢弃它是否安全。**

## 二、我建议采用分级处理

| 内容类型 | 处理方式 | 原因 |
|---|---|---|
| 已支持的文本 Block | 正常解析 | Runtime 需要 |
| 已支持的工具调用 Block | 保留 ID、名称、参数 | 保证工具调用正确 |
| 可选的用量统计 | 可单独记录，不进入 Context | 不影响工具执行语义 |
| 协议要求回传的 Block | 保留原始结构 | 防止后续请求格式错误 |
| 未知且可能影响执行的 Block | 拒绝执行或返回明确错误 | 避免静默丢失关键内容 |

这里你需要理解一个重要区别：

**不能识别 ≠ 必须直接丢弃，也不等于必须立即让整个 Agent 崩溃。**

例如可以把错误转化为：

```python
class UnsupportedModelContent(Exception):
    pass
```

Adapter 发现无法安全处理的内容时抛出异常，Runtime 捕获后返回明确的模型兼容性错误，并记录 Trace。

如果某些内容确实需要原样回传，也可以由 Adapter 保存原始响应，并在下一轮请求中正确恢复。

但这种原始内容应该由对应厂商的 Adapter 管理，不应该让 Runtime 随意修改。

---

## 三、一个更适合你当前项目的设计

你不需要为最小 Agent 实现所有 Content Block。

可以明确规定：

```python
SUPPORTED_CONTENT = {
    "text",
    "tool_use"
}
```

这只是能力白名单的示意。实际实现要区分厂商的请求和响应协议。

然后遵循三个规则：

1. **支持的内容**：转换成统一的 `ModelResponse`。
2. **明确可忽略的元数据**：记录到日志或 Trace。
3. **不能安全处理的内容**：停止当前模型响应的处理，返回兼容性错误，而不是假装成功。

这实际上是你之前学习过的工具参数 Schema 白名单思想在 Model Adapter 中的应用。

还有一个工程注意点：如果采用流式输出，应当避免在完整工具调用参数到达并通过校验之前执行工具。

---

## 四、回到架构题：Claude 与 OpenAI-compatible 的 Trade-off

我们已经讨论出了两种协议的主要区别。

**Anthropic Content Block 设计**

优点是表达能力丰富，支持类型化内容与顺序关系，适合复杂工具、多模态和扩展场景。

代价是解析和消息回放相对复杂，Adapter 需要处理不同内容块及其协议约束。

**OpenAI-compatible Chat Completions 设计**

优点是工具调用字段明确、消息角色清晰，当前最小 Agent 更容易实现。

代价是如果内部只抽象成 `content + tool_calls`，就无法完整表示所有模型协议中的复杂交错内容；不同厂商对兼容协议的支持也可能存在差异。

需要注意，OpenAI 的 Responses API 同样具有类型化输出项目，所以这里比较的是具体 API 形式，而不是两家公司所有技术能力的高低。

---

## 五、最后给你一道检验题

假设 Adapter 收到 Claude 的响应：

```json
{
  "content": [
    {
      "type": "text",
      "text": "我要计算 1+2"
    },
    {
      "type": "tool_use",
      "id": "call_001",
      "name": "calculator",
      "input": {"expression": "1+2"}
    }
  ]
}
```

你当前的 Runtime 只有：

```python
ModelResponse(
    content="我要计算 1+2",
    tool_calls=[
        ToolCall(
            id="call_001",
            name="calculator",
            arguments={"expression": "1+2"}
        )
    ],
    finish_reason="tool_calls"
)
```

**问题：这个转换是否安全？**

我认为在你当前的最小 Agent 需求下可以接受，因为文本和工具调用的关键语义都保留了，而且目前没有复杂的未知 Block。

但接下来，你需要考虑：

**下一次请求 Claude 时，Adapter 是否能将这个内部结构重新编码为合法、语义一致的消息？**

如果能，你的设计就满足当前需求；如果不能，就说明内部抽象需要补充必要信息。

到这里，模块五的主要工程取舍已经讲完了。下一步可以把这五道架构题各自整理成最终提交稿，并检查它们之间的设计是否一致。

## 106 · 用户 · 2026-10-20T11:38:25.267000+00:00

对于这个问题我想知道对于这些输出方式，生产上是怎么处理的？我觉得这个思路应该有点像MCP

## 107 · 助手 · 2026-10-20T11:38:40.658127+00:00

我会把生产实现拆成三层来看：模型厂商的输出协议、Harness 内部统一事件格式、工具执行与结果回传。再对照 MCP，解释它们到底像在哪里、又有什么本质区别。

## 108 · 助手 · 2026-10-20T11:38:47.921948+00:00

你的联想是对的。**生产级 Agent 对工具调用和结果的处理，确实与 MCP 的设计思想有相似之处：都在通过标准化协议，把“模型决策”和“工具执行”解耦。**

但有一个重要区别：

**Function Calling 解决模型如何表达工具调用意图；MCP 解决 Agent 如何发现和调用外部工具；Harness 则负责把整个过程组织起来。**

它们处于不同层次，不是三种互相竞争的技术。

## 一、生产级 Agent 通常怎样组织这套系统？

我们以 Claude、OpenAI-compatible 模型和 MCP 工具为例。

<box border radius="xl" padding={3} gap={2}>
  <svg viewBox="0 0 340 402" width="100%">
    <rect x="78" y="6" width="184" height="42" rx="9" fill="#64748b" fillOpacity=".13" stroke="#94a3b8"/>
    <text x="170" y="23" fill="currentColor" textAnchor="middle" fontSize="13" fontWeight="600">Agent Runtime</text>
    <text x="170" y="39" fill="currentColor" textAnchor="middle" fontSize="10">Loop / Session / State / Trace</text>
    <path d="M170 48 V69" stroke="#94a3b8" strokeWidth="1.8"/>
    <path d="M165 62 L170 69 L175 62" fill="none" stroke="#94a3b8" strokeWidth="1.8"/>
    <rect x="75" y="70" width="190" height="41" rx="8" fill="#0d9488" fillOpacity=".12" stroke="#0d9488"/>
    <text x="170" y="87" fill="currentColor" textAnchor="middle" fontSize="13" fontWeight="600">统一内部消息模型</text>
    <text x="170" y="103" fill="currentColor" textAnchor="middle" fontSize="10">Text / ToolCall / ToolResult</text>
    <path d="M170 111 V129 H87 V145 M170 129 H253 V145" fill="none" stroke="#94a3b8" strokeWidth="1.8"/>
    <rect x="7" y="145" width="156" height="54" rx="8" fill="#64748b" fillOpacity=".12" stroke="#94a3b8"/>
    <text x="85" y="166" fill="currentColor" textAnchor="middle" fontSize="12" fontWeight="600">Anthropic Adapter</text>
    <text x="85" y="184" fill="currentColor" textAnchor="middle" fontSize="10">tool_use / tool_result</text>
    <rect x="177" y="145" width="156" height="54" rx="8" fill="#64748b" fillOpacity=".12" stroke="#94a3b8"/>
    <text x="255" y="166" fill="currentColor" textAnchor="middle" fontSize="12" fontWeight="600">OpenAI Adapter</text>
    <text x="255" y="184" fill="currentColor" textAnchor="middle" fontSize="10">tool_calls / tool</text>
    <path d="M170 199 V226" stroke="#94a3b8" strokeWidth="1.8" strokeDasharray="4 3"/>
    <rect x="76" y="227" width="188" height="41" rx="8" fill="#d97706" fillOpacity=".11" stroke="#d97706"/>
    <text x="170" y="244" fill="currentColor" textAnchor="middle" fontSize="13" fontWeight="600">Tool Registry / Executor</text>
    <text x="170" y="260" fill="currentColor" textAnchor="middle" fontSize="10">校验、授权、执行、记录</text>
    <path d="M170 268 V289 H87 V303 M170 289 H253 V303" fill="none" stroke="#94a3b8" strokeWidth="1.8"/>
    <rect x="7" y="303" width="156" height="54" rx="8" fill="#0d9488" fillOpacity=".09" stroke="#0d9488"/>
    <text x="85" y="323" fill="currentColor" textAnchor="middle" fontSize="12" fontWeight="600">本地工具</text>
    <text x="85" y="341" fill="currentColor" textAnchor="middle" fontSize="11">Calculator / Todo</text>
    <rect x="177" y="303" width="156" height="54" rx="8" fill="#0d9488" fillOpacity=".09" stroke="#0d9488"/>
    <text x="255" y="323" fill="currentColor" textAnchor="middle" fontSize="12" fontWeight="600">MCP Client</text>
    <text x="255" y="341" fill="currentColor" textAnchor="middle" fontSize="11">tools/list / tools/call</text>
    <path d="M255 357 V371" stroke="#94a3b8" strokeWidth="1.8"/>
    <rect x="184" y="372" width="142" height="27" rx="7" fill="#64748b" fillOpacity=".12" stroke="#94a3b8"/>
    <text x="255" y="390" fill="currentColor" textAnchor="middle" fontSize="11">MCP Server</text>
  </svg>
  <caption>工程参考架构，不代表某一家公司的内部实现。上半部分处理模型协议，下半部分处理工具执行协议；箭头省略了结果回传路径。</caption>
</box>

这里最值得注意的是：**Model Adapter 和 MCP Client 不是同一个东西。**

前者面向模型厂商，后者面向工具提供方。

## 二、从真实执行过程看两者怎样配合

假设用户说：

> 帮我查询 PostgreSQL 数据库里有多少个用户。

整个流程可以拆成六步。

1. Harness 通过 MCP 的 `tools/list` 发现数据库工具及其参数 Schema。
2. Model Adapter 将工具定义转换成当前模型支持的格式。
3. 模型返回 `tool_use` 或 `tool_calls`，表达查询意图。
4. Runtime 检查权限、参数和风险，再由 MCP Client 发送 `tools/call`。
5. MCP Server 执行查询，返回文本、结构化 JSON 或其他内容。
6. Runtime 处理结果，再由 Model Adapter 编码为模型要求的工具结果消息。

MCP 官方协议明确支持工具发现、调用、结构化结果及多种内容类型。<Cite refs={["turn794688search5","turn794688search2"]}/>

所以它们之间的关系是：

| 层次 | 负责什么 | 示例 |
|---|---|---|
| Function Calling | 模型表达调用意图 | `get_users_count()` |
| Model Adapter | 厂商协议转换 | `tool_use` ↔ 内部 `ToolCall` |
| Tool Registry | 查找、校验、控制工具 | 参数 Schema、授权 |
| MCP Client | 调用外部工具服务 | `tools/call` |
| Runtime | 决定循环与任务状态 | 执行、等待、继续、终止 |

---

## 三、生产环境中如何处理 Content Block？

你刚才问的是生产系统最值得关注的实现细节。

我建议把它分为三种方案。

### 方案 A：完全转换为统一模型

例如：

```python
class ToolCall:
    id: str
    name: str
    arguments: dict

class ModelResponse:
    content: str
    tool_calls: list[ToolCall]
```

适用于你的最小 Agent。

**优点**：简单、Runtime 与模型厂商解耦。

**缺点**：如果原始消息包含交错内容块、推理相关元数据、图片等信息，可能无法无损表达。

### 方案 B：统一结构 + 保留原始内容

这是一种实用的折中设计。

```python
class ModelResponse:
    content: str
    tool_calls: list[ToolCall]
    finish_reason: str

    provider_data: dict | None
```

`provider_data` 只是教学示意。实际更适合由 Adapter 私有存储原始响应，并通过不可随意篡改的引用管理，避免在日志中泄漏敏感信息。

Runtime 只使用标准化字段完成执行，Adapter 负责维护厂商要求回传的原始内容。

**优点**：执行逻辑简单，同时有机会保留必要的协议细节。

**缺点**：消息持久化、重放和跨模型切换时，原始厂商数据可能不兼容。

### 方案 C：内部也采用类型化 Content Block

例如：

```python
class Message:
    role: str
    blocks: list[ContentBlock]
```

每个 Block 可以是文本、工具调用、图片、工具结果或其他受支持类型。

**优点**：能保留更多内容结构和顺序，适合复杂多模态 Agent。

**缺点**：对消息模型、序列化、验证和协议适配的要求更高。

但这里需要澄清：

**生产级并不意味着必须采用方案 C。** 如果系统只支持文本与常规 Function Calling，方案 A 或 B 也完全可能是合理选择。

## 四、这里有一个真实的生产协议限制：Claude 的 Thinking Block

这是为什么我更建议你关注方案 B。

Anthropic 官方文档明确指出，在使用带推理内容的工具调用时，部分 `thinking` 和 `redacted_thinking` 内容块需要按照 API 要求在后续请求中原样保留。

这些块可能包含用于验证的签名，不能随意拼接、重写或删除。否则后续请求可能报错。<Cite refs={["turn794688search3","turn794688search9"]}/>

举个简化例子：

```json
{
  "content": [
    {
      "type": "thinking",
      "thinking": "...",
      "signature": "..."
    },
    {
      "type": "tool_use",
      "id": "call_001",
      "name": "calculator",
      "input": {"expression": "1+2"}
    }
  ]
}
```

假设 Adapter 只保留了：

```python
ToolCall(
    id="call_001",
    name="calculator",
    arguments={"expression": "1+2"}
)
```

那么 Runtime 的计算逻辑可能完全正确，但 Adapter 在下一轮请求时无法恢复协议要求保留的 Thinking Block。

因此你必须区分：

**执行所需的语义信息**和**协议回放所需的原始信息**。

它们不是同一回事。

而且还要注意，保存厂商原始数据不意味着可以随意把它发给另一个模型。原始内容可能只对特定厂商或特定 API 有意义。

## 五、MCP 的 Content 和 Claude 的 Content Block 是不是一样的？

**结构思想相似，但不是同一种协议对象。**

例如 MCP 工具结果：

```json
{
  "content": [
    {
      "type": "text",
      "text": "找到 3 个用户"
    }
  ],
  "structuredContent": {
    "count": 3
  }
}
```

MCP 允许工具同时返回内容块和结构化结果，也支持输出 Schema。<Cite ref="turn794688search4"/>

而 Claude 的 `tool_result` 是送回模型 API 的消息内容块。

所以生产系统必须完成一个转换：

```text
MCP CallToolResult
       ↓
Tool Executor 处理与校验
       ↓
内部 ToolResult
       ↓
Anthropic Adapter
       ↓
Claude tool_result Content Block
```

这也是为什么**MCP 不会自动解决不同模型厂商的协议兼容问题**。

你不能因为两边都有 `content` 字段，就认为可以直接把 MCP 返回值塞进 Claude API。

---

## 六、结合你的 Agent Harness，我会怎样选择？

我建议你当前采用 **方案 B 的简化版本**：

- Runtime 继续使用 `ModelResponse`、`ToolCall` 和 `Message`。
- Adapter 统一进行请求编码与响应解析。
- 对必须回放的厂商内容，由 Adapter 维护原始数据或安全引用。
- 遇到无法安全保留或转换的内容，明确报错，不静默丢弃。
- 以后接入 MCP 时，在 Tool Registry 后面增加 `MCPToolAdapter`，不要让 MCP 协议侵入主循环。

这对你目前的笔试来说，已经是有明确工程取舍的方案。不必立即实现完整的多模态 Content Block 系统。

### 最后我想追问你一个关键架构问题

假设你的 Agent 同时支持 Claude 和 GLM。

前 20 轮一直使用 Claude，消息历史中保存了 Claude 特有的 Thinking Block 和工具调用记录。

第 21 轮，因为 Claude API 不可用，Runtime 准备自动切换到 GLM。

**你认为系统能否直接把 Claude 的完整消息历史发送给 GLM？**

如果不能，你会如何处理历史信息，既保留用户任务的连续性，又避免厂商专有协议导致新模型无法理解？

这道题实际上是在考察 **跨模型切换、协议归一化与 Context 可移植性**，也是 Model Adapter 架构中非常有价值的工程问题。

## 109 · 用户 · 2026-10-20T11:43:36.732000+00:00

转化为GLM能处理的形式

## 110 · 助手 · 2026-10-20T11:43:52.133429+00:00

对，**将 Claude 的历史消息转换成 GLM 能理解的格式**，这是正确方向。

但这里需要深入一步：**格式能够转换，不代表语义能够完整保留。**

这正是 Model Adapter 在生产环境中最难处理的问题之一。

## 一、哪些信息可以转换，哪些不能？

假设 Claude 的历史消息包含：

| Claude 历史内容 | 切换 GLM 时的处理 |
|---|---|
| 用户普通文本 | 直接转换为 `user` 消息 |
| Assistant 普通文本 | 转换为 `assistant` 消息 |
| `tool_use` | 转换为 `tool_calls` |
| `tool_result` | 转换为 `tool` 消息 |
| Thinking Block | 不直接发送厂商专有数据 |
| 图片、音频等 | 检查 GLM 目标模型是否支持 |
| 未完成的工具调用 | 需要先恢复或处理执行状态 |

最后一项特别重要。

如果 Claude 已经请求调用工具，但是工具还没有执行完，这时直接切换到 GLM，可能导致工具请求与结果无法正确配对。

因此，**跨模型切换不仅是协议转换，还涉及 Runtime 状态恢复。**

## 二、我更倾向于建立两个不同的表示层

<box border radius="lg" padding={3} gap={2}>
  <box background="surface-secondary" radius="md" padding={3} align="center">
    **厂商原始历史**
    <text color="secondary" size="xs">Claude 的 Content Blocks、工具调用和协议元数据</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **统一语义历史（Canonical History）**
    <text color="secondary" size="xs">用户消息、工具调用、执行结果、任务状态、证据引用</text>
  </box>
  <row justify="center"><icon name="git-fork" color="secondary"/></row>
  <grid columns={2} gap={2}>
    <grid-item>
      <box border radius="md" padding={3} align="center">
        **Claude Adapter**
        <text color="secondary" size="xs">编码为 Anthropic 协议</text>
      </box>
    </grid-item>
    <grid-item>
      <box background="rgba(13,148,136,0.08)" border={{size:1,color:"rgba(13,148,136,0.4)"}} radius="md" padding={3} align="center">
        **GLM Adapter**
        <text color="secondary" size="xs">编码为兼容的工具调用协议</text>
      </box>
    </grid-item>
  </grid>
</box>

这里的设计思想是：

**厂商原始历史负责协议级恢复，统一语义历史负责跨模型迁移。**

两种历史可以共享底层事件记录，但不能简单视为完全等价。

例如 Claude 特有的 Thinking Block 不一定能直接移植给 GLM，但任务目标、用户约束、已经完成的工具调用及其结果，可以尽可能转换成通用形式。

## 三、真正需要保证的是任务连续性

切换模型之后，GLM 至少需要知道：

```yaml
goal: 完成 Agent Harness

constraints:
  - 不依赖现有 Agent 框架
  - 需要真实 LLM API
  - 必须实现 Todo

completed:
  - Runtime 基本循环
  - Calculator

current_task:
  - 接入模型 API

tool_history:
  - calculator 已执行成功

pending_calls: []

next_action:
  - 继续完成模型适配器
```

这些信息比保留 Claude 原始 Thinking Block 更有助于让新模型继续任务。

当然，转换后的 Context 也需要验证，确保重要约束与工具执行状态没有丢失。

---

## 四、最后一个工程边界

**并不是所有情况下都应该自动切换模型。**

例如：

- Claude 的工具请求尚未完成。
- 新模型不支持当前任务必须使用的工具或输入模态。
- 历史包含无法安全转换的关键内容。
- 当前操作可能产生不可重复的外部副作用。

这时应该暂停、完成状态协调或明确报告不兼容，而不是强行迁移。

---

### 最后一轮追问

假设 Claude 已经完成了 20 轮任务，Runtime 准备切换 GLM。

但你发现：

- 原始历史有 100K Token。
- GLM 当前可用的 Context 预算只有 32K Token。
- 任务目标、状态表和工具执行结果都已持久化。

**你会怎样在切换模型的同时解决 Context 容量不一致的问题？**

这道题可以直接复用我们模块一讨论的压缩方案。重点是：你会优先保留哪些信息，又如何保证切换后的模型不会重新执行已经完成的工具操作？

## 111 · 用户 · 2026-10-20T11:45:18.390000+00:00

压缩；重点保存用户目标以及流程

## 112 · 助手 · 2026-10-20T11:45:33.329868+00:00

对，**压缩是正确的方向，优先保留用户目标和任务流程也正确。**

不过，这里我想补充一个比“流程”更重要的概念：**执行状态（Execution State）**。

因为切换模型时，最危险的并不是 GLM 忘记 Claude 做过什么，而是它**把已经完成的操作重新执行一遍**。

## 一、我会优先保留这五类信息

| 优先级 | 信息 | 为什么 |
|---|---|---|
| P0 | 用户目标和硬约束 | 防止偏离原始任务 |
| P0 | 当前任务执行状态 | 知道已完成、未完成和失败的操作 |
| P0 | 未完成工具调用及副作用状态 | 防止重复执行 |
| P1 | 关键决策和证据 | 理解为什么这样做 |
| P2 | 最近几轮消息与历史摘要 | 保证对话自然衔接 |

特别注意：**已完成工具的记录不一定要完整放入 Context，但它们的执行状态必须可靠地保存在 Runtime 中。**

例如：

```yaml
goal: 完成 Agent Harness

completed:
  - task: 创建 calculator.py
    status: VERIFIED
    evidence: test_calculator_passed

pending:
  - task: 接入真实 LLM API
    status: NOT_STARTED

tool_operations:
  - id: call_001
    operation: write_file
    status: SUCCEEDED

next_action: 接入真实 LLM API
```

GLM 看到这份状态后，就知道应该从哪里继续。

但是，防止重复写文件不能只依赖 GLM 阅读这份摘要。**Runtime 还应该检查工具操作记录、幂等键和当前任务状态。**

## 二、切换模型的完整流程

<box border radius="lg" padding={3} gap={2}>
  <box background="surface-secondary" radius="md" padding={3} align="center">
    **Claude API 不可用**
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **冻结执行边界，核查未完成操作**
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **加载 Goal / State / Event Log**
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **压缩并重建 32K 以内的 Context**
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **GLM Adapter 编码消息与工具 Schema**
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box background="rgba(13,148,136,0.07)" border={{size:1,color:"rgba(13,148,136,0.4)"}} radius="md" padding={3} align="center">
    **验证状态一致性，恢复执行**
  </box>
</box>

这一套方案实际上把我们前面四个模块的知识串联起来了：

- Context：如何控制 Token。
- Memory：如何找回历史关键信息。
- Task：如何恢复目标和进度。
- Runtime：如何保证并发与工具执行正确性。
- Model Adapter：如何实现跨厂商协议转换。

## 三、模块五的核心结论

到这里，你的架构选择已经比较明确了：

**最小 Agent 采用简单统一消息模型，Model Adapter 双向转换厂商协议，保留必要的原始协议数据；跨模型迁移则通过统一语义历史和持久化任务状态恢复执行。**

主要 Trade-off 是牺牲部分复杂协议的表达能力，换取实现简单、运行逻辑清晰和较低的开发成本。

---

## 四、最后，我想检验你是否真正理解了这五个模块的关系

假设你的 Agent 正在执行一个长程任务：

用户说：

> 帮我从零开发一个 Agent Harness，全部完成后提交 GitHub。

执行过程中发生：

1. Session 已经聊了 200 轮，Context 接近上限。
2. Claude API 突然不可用，需要切换 GLM。
3. 之前一个异步工具仍然在执行。
4. 用户又发来新消息：“不要继续优化 Calculator，优先完成真实 LLM API。”
5. 数据库里保存着完整任务状态和历史事件。

**如果你是这个 Agent Harness 的架构负责人，你会让 Runtime 首先处理哪件事？为什么？**

这里没有要求你一次解决所有问题。重点考察你能否根据**正确性、用户意图、状态一致性和执行成本**确定处理顺序。

这已经是一个非常接近真实生产环境的综合 Agent Runtime 场景。

## 113 · 用户 · 2026-10-20T11:49:01.798000+00:00

根据用户意图以及事情紧急程度排优先级，按照优先级来完成

## 114 · 助手 · 2026-10-20T11:49:17.631060+00:00

你的原则是对的，但还需要补充一个关键点：

**在 Agent Runtime 中，不是所有事情都能简单地按照紧急程度排序。有些操作存在必须遵守的先后依赖关系。**

例如，用户的新指令优先级很高，但如果一个旧工具正在修改文件，我们不能在不知道它是否完成的情况下直接启动新的修改。

这里需要区分两个概念：

- **优先级（Priority）**：哪件事更重要、更应该尽快处理？
- **执行依赖（Dependency）**：哪些事情必须完成或确认后，其他事情才能安全执行？

## 一、我会如何处理这个综合场景？

| 顺序 | Runtime 行为 | 原因 |
|---|---|---|
| 1 | 接收用户新指令，持久化事件 | 防止新要求丢失 |
| 2 | 标记旧计划需要调整，阻止继续调度 Calculator 优化 | 避免继续执行过时目标 |
| 3 | 核查正在执行的异步工具状态 | 防止取消、重试导致重复副作用 |
| 4 | 更新任务计划与优先级 | 将真实 LLM API 调整为当前重点 |
| 5 | 从数据库加载状态，进行 Context 压缩 | 解决 200 轮上下文过长 |
| 6 | 通过 GLM Adapter 恢复模型调用 | 保证协议兼容并继续执行 |

这里的顺序是逻辑依赖，不意味着必须等所有异步工具结束才能做其他工作。安全的独立操作可以并行。

## 二、为什么不应该第一时间压缩 Context？

因为用户刚刚修改了任务要求：

> 不要继续优化 Calculator，优先完成真实 LLM API。

如果 Runtime 先用旧计划进行压缩，那么摘要可能依然强调：

```yaml
current_task: 优化 Calculator
```

随后又需要修改摘要，造成无意义的重复处理。

更合理的是先将用户指令写入 Event Log，并更新权威任务状态，再根据最新状态构建新 Context。

**这体现了一个非常重要的架构原则：先保证状态正确，再优化执行效率。**

---

## 三、五个模块其实共同解决一个问题

你之前分别讨论了 Context、Memory、Task、Tool/Session Runtime、Model Adapter。

现在可以把它们联系起来：

<box border radius="lg" padding={3} gap={2}>
  <box background="surface-secondary" radius="md" padding={3} align="center">
    **用户目标与最新指令**
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **Task Manager**
    <text color="secondary" size="xs">目标、计划、进度和验收标准</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **Session Runtime + Event Log**
    <text color="secondary" size="xs">事件顺序、并发控制和状态持久化</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **Context Builder + Memory Retriever**
    <text color="secondary" size="xs">选择当前决策所需的信息</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **Model Adapter + LLM**
    <text color="secondary" size="xs">生成下一步行动意图</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box background="rgba(13,148,136,0.08)" border={{size:1,color:"rgba(13,148,136,0.4)"}} radius="md" padding={3} align="center">
    **Tool Executor + Verifier**
    <text color="secondary" size="xs">执行、验证并反馈进度</text>
  </box>
</box>

这张图是逻辑职责图，不是严格的单向调用顺序。实际运行中，工具结果、用户事件和任务状态会不断反馈到 Runtime。

---

## 四、对你目前架构能力的阶段性评价

经过这五个模块的讨论，我认为你已经掌握了三个非常重要的思想：

**1. 模型负责智能决策，Runtime 负责可靠执行。**

不能把工具权限、任务状态和执行正确性完全交给模型。

**2. Context 是工作视图，不应该是唯一事实来源。**

历史事件、关键状态、工具执行证据需要独立持久化，才能支持压缩、恢复和跨模型迁移。

**3. Agent 的可靠性来自可验证的状态变化，而不是模型的自我评价。**

任务完成、工具执行和状态更新都需要相应证据。

你目前更需要补强的是：**如何将这些原则落实为具体的数据结构、状态转换和异常恢复代码。**

### 最后一条工程面试追问

假设 Runtime 已经处理了新指令、更新了任务计划，但在准备调用 GLM 前，程序突然崩溃并重启。

数据库里只有部分状态已经提交。

**你会怎样设计恢复机制，确保重启后不会遗漏用户的新要求，也不会重复执行旧工具？**

这其实是将我们讨论的 Event Log、事务、幂等性和任务状态持久化真正串起来的最后一步。

## 115 · 用户 · 2026-10-20T11:54:01.874000+00:00

先对照event log和数据库，标记出入的点，然后根据幂等性去检查，把五道架构的答案总结出来，然后串起来这五题

## 116 · 助手 · 2026-10-20T11:54:16.349432+00:00

你的恢复思路是对的：**通过 Event Log 与持久化状态进行对账，找出不一致，再结合幂等性决定哪些操作需要恢复、重试或人工确认。**

但要补充一个生产级系统的重要边界：

**不能只依赖数据库里的 `SUCCEEDED` 标记判断外部工具是否执行成功。** 例如 GitHub 提交已经成功，但程序在写入成功记录之前崩溃，此时数据库可能仍显示 `RUNNING`。应该先查询外部实际状态，再决定是否重试。

下面我把我们讨论过的五道架构题整理为一份完整的技术方案。每道题都按**核心问题 → 设计方案 → 底层原理 → Trade-off → 工程边界**展开，最后将它们串成一套 Agent Harness 架构。

# Agent Harness：五道架构设计题总结

## 第一题：200 轮对话，Context 即将耗尽，如何压缩？

### 1. 核心问题

LLM 的 Context Window 有上限，但 Agent 可能持续执行很长时间。

我们真正要解决的不是简单删减 Token，而是：

**在有限 Context 中，保留完成当前任务所必需的信息，同时保证历史状态与决策连续性。**

### 2. 设计方案：分层压缩

<box border radius="lg" padding={3} gap={2}>
  <box background="surface-secondary" radius="md" padding={3} align="center">
    原始对话 + 工具结果
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **第一级：确定性压缩**
    <text color="secondary" size="xs">移除冗余日志、裁剪超长工具输出、保留原始引用</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **第二级：结构化事件摘要**
    <text color="secondary" size="xs">保留重要决策、状态变化、证据及事件关系</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **第三级：LLM 全量压缩**
    <text color="secondary" size="xs">作为最后手段，重建任务相关的紧凑上下文</text>
  </box>
</box>

关键是把以下三类数据分开管理：

- **Event Log**：记录发生过什么，尽量保留完整历史。
- **Task State**：记录当前目标、进度、约束、待办和已验证结果。
- **Context**：从前两者选择并构造给模型看的有限工作视图。

### 3. 底层原理

这与数据库的**事实数据和物化视图**有相似之处：

Event Log 是可追溯的历史记录，Context 是面向当前推理任务生成的视图。

压缩改变的是模型看到的视图，不应该直接篡改权威任务状态。

### 4. Trade-off

| 方案 | 优点 | 缺点 |
|---|---|---|
| 删除冗余内容 | 快、成本低 | 信息压缩能力有限 |
| 结构化摘要 | 可追溯，较易恢复决策链 | 摘要维护更复杂 |
| LLM 全量压缩 | 压缩率较高，能够语义概括 | 可能遗漏、歪曲事实，增加 Token 成本 |

### 5. 工程边界

建议在 Context 使用率达到一定阈值时触发预警，例如 80%，但实际阈值需要考虑工具 Schema、预留输出空间和模型差异。

压缩应在安全执行边界上进行，并使用版本校验，避免把压缩期间的新消息遗漏掉。

如果压缩失败，保留原始记录，允许回退或安全暂停，不应直接覆盖原数据。

**面试核心表达：Context 是可重建的工作视图，不是唯一事实来源。**

---

## 第二题：用户半个月后重新提问，如何利用 Memory 恢复上下文？

### 1. 核心问题

用户的新问题可能依赖很久以前的对话，但不能每次都把完整历史塞给 LLM。

核心是：

**从大量历史中找出与当前任务相关、可信、仍然有效的记忆。**

### 2. 设计方案：分层 Memory + 按需检索

区分三类记忆：

| Memory 类型 | 保存内容 | 例子 |
|---|---|---|
| Global Memory | 长期偏好、稳定约束 | 用户偏好通过实际工程理解技术 |
| Project Memory | 特定项目的架构和决策 | Agent Harness 采用独立 Model Adapter |
| Session Memory | 本次任务的近期信息 | 当前正在调试 Calculator |

执行流程：

```text
用户新问题
   ↓
识别用户意图、项目和所需历史
   ↓
选择检索范围
   ↓
关键词 / 语义 / 结构化检索
   ↓
相关性、时间、来源、权限检查
   ↓
选择性注入 Context
   ↓
生成回答或执行下一步
```

### 3. 底层原理

Memory 可以看作一个**持续更新的信息检索系统**。

关键不只是存储，而是四件事：

- 写入：哪些事实值得长期保存？
- 检索：哪些记忆与当前问题有关？
- 更新：新旧事实发生冲突怎么办？
- 使用：哪些记忆应该影响本次决策？

例如：

```text
V10：用户偏好 C++
V11：用户偏好 Python
```

Memory 更新不意味着所有运行中的任务都必须重新规划。

如果当前任务明确要求使用 C++，长期偏好变化不应覆盖当前硬约束。

### 4. Trade-off

主动保存大量记忆能提高召回可能性，但会产生噪声、过期信息和隐私成本。

严格筛选记忆有利于准确性，但也可能漏掉未来重要的信息。

因此需要结合来源、作用范围、置信度、更新时间以及任务相关性进行判断。

### 5. 工程边界

- 不把模型推断出的偏好直接当成用户确认的事实。
- 遇到冲突时保留来源和版本，必要时标记 `DISPUTED`。
- 对重要事实支持重新验证，而不是简单相信最新一条。
- 通过 Recall@k、MRR、过期记忆误用率和最终任务成功率评估效果。

**面试核心表达：Memory 的价值不在于记住所有历史，而在于在正确的时候提供正确的信息。**

---

## 第三题：长程任务执行中，Agent 忘记最初目标，怎么办？

### 1. 核心问题

Agent 可能执行了大量工具、经历多轮压缩和重新规划，却逐渐偏离用户真正想完成的事情。

尤其不能把模型声称的“任务完成”当作真实成功。

### 2. 设计方案：Goal、Plan、State 分离

这是这一题最核心的架构设计。

| 对象 | 职责 | 能否修改 |
|---|---|---|
| Goal | 用户最终想达到什么结果 | 通常只有用户授权才能修改 |
| Plan | 通过哪些步骤实现 Goal | 可以随着执行结果动态调整 |
| State | 当前真实执行进度 | 根据事件、证据和验证结果更新 |

例如用户要求：

> 实现一个可运行的 Agent Harness，包含工具调用、Session 和测试。

```yaml
goal:
  deliverable: 可运行的 Agent Harness

plan:
  - 实现 Agent Loop
  - 实现 Tool Registry
  - 添加 Calculator / Search / Todo
  - 接入真实 LLM
  - 编写测试

state:
  loop: VERIFIED
  tool_registry: VERIFIED
  calculator: VERIFIED
  llm_api: NOT_STARTED
  tests: PARTIAL
```

即使模型在中途压缩 Context，Runtime 也能根据持久化状态恢复目标和进度。

### 3. 底层原理：闭环控制

Agent 不应该只是：

```text
计划 → 执行 → 声称成功
```

而应该是：

```text
Goal
 ↓
Plan
 ↓
Execute
 ↓
Verify
 ↓
Update State
 ↓
继续 / 重规划 / 结束
```

Verify 是关键。

例如测试通过，只能证明相关测试通过，不能直接证明整个项目满足所有需求。

### 4. Trade-off

过于严格的验证会增加工具调用和延迟。

过于宽松又容易产生虚假的完成状态。

因此应当根据操作风险和验收要求选择不同强度的验证。

### 5. 工程边界

- 每项任务设置明确验收条件。
- `COMPLETED` 与 `VERIFIED` 区分。
- 保存工具执行证据及其对应版本。
- 设置最大循环次数、Token 和时间预算。
- 多轮无有效进展时触发重规划。
- 无法验证的任务标记为未确认，不伪装成功。

**面试核心表达：Goal 保证方向，Plan 负责策略，State 和 Verifier 保证执行结果可信。**

---

## 第四题：Session 正在 Busy，新的用户消息或异步工具结果到达，如何处理？

### 1. 核心问题

Agent 正在执行一个耗时任务时，可能同时出现：

- 用户修改要求。
- 旧工具尚未执行完。
- 另一个工具返回结果。
- 新任务需要立即开始。
- 用户要求取消原任务。

如果多个执行流程直接修改同一份 Session 状态，就可能出现竞态条件、消息串扰和重复工具执行。

### 2. 设计方案：事件队列 + Run 隔离 + 异步工具

我会采用以下架构：

<box border radius="lg" padding={3} gap={2}>
  <grid columns={3} gap={2}>
    <grid-item>
      <box background="surface-secondary" radius="md" padding={2} align="center">
        <icon name="user-round"/>
        <text size="xs" weight="medium">用户新消息</text>
      </box>
    </grid-item>
    <grid-item>
      <box background="surface-secondary" radius="md" padding={2} align="center">
        <icon name="wrench"/>
        <text size="xs" weight="medium">工具完成事件</text>
      </box>
    </grid-item>
    <grid-item>
      <box background="surface-secondary" radius="md" padding={2} align="center">
        <icon name="circle-x"/>
        <text size="xs" weight="medium">取消 / 超时</text>
      </box>
    </grid-item>
  </grid>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center">
    **Session Event Queue**
    <text size="xs" color="secondary">持久化事件，保证归属与顺序</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box background="rgba(13,148,136,0.08)" border={{size:1,color:"rgba(13,148,136,0.4)"}} radius="md" padding={3} align="center">
    **Session Actor / State Reducer**
    <text color="secondary" size="xs">串行处理该 Session 的共享状态变化</text>
  </box>
  <row justify="center"><icon name="git-fork" color="secondary"/></row>
  <grid columns={2} gap={2}>
    <grid-item>
      <box border radius="md" padding={3} align="center">
        **Run 001**
        <text size="xs" color="secondary">ReadDocs 异步执行</text>
      </box>
    </grid-item>
    <grid-item>
      <box border radius="md" padding={3} align="center">
        **Run 002**
        <text size="xs" color="secondary">Calculator 独立执行</text>
      </box>
    </grid-item>
  </grid>
</box>

这里的设计是：

**同一 Session 的共享状态修改串行化，但不同 Run 的独立计算与工具执行可以并发。**

工具执行结果不能随意追加到全局消息列表，而要通过 `run_id`、`tool_call_id` 回到正确的执行上下文。

### 3. 新消息的处理策略

生产上可以根据任务关系选择不同策略：

| 策略 | 适用场景 | 代价 |
|---|---|---|
| Queue 排队 | 新请求依赖旧任务结果 | 用户需要等待 |
| Cancel 取消 | 用户明确要求停止或修改旧任务 | 需要处理迟到结果和外部副作用 |
| Parallel 并行 | 新旧任务彼此独立 | 需要隔离状态并处理资源竞争 |

例如用户原先要求写 1000 字报告，后来改成 500 字。

Runtime 不应该盲目执行两份报告，而应该把新指令合并进当前目标状态，在安全点重新规划。

### 4. 底层原理

这与并发编程的 Actor Model、事件驱动架构有相似之处：

- Event Queue 接收外部事件。
- Session Actor 负责共享状态的串行更新。
- 异步工具负责耗时的外部操作。
- Event Log 记录已发生的状态转换。
- Version / CAS 防止过期操作覆盖新状态。

Actor 串行处理可以减少共享状态竞态，但不能自动保证外部工具的幂等性，也不能解决所有分布式一致性问题。

### 5. 工程边界：取消不等于撤销

假设 Agent 调用支付工具，用户随后要求取消。

即使 Runtime 取消了本地等待，也不代表支付平台没有完成扣款。

正确做法是：

1. 记录该工具请求的幂等键。
2. 将结果不确定的操作标记为 `UNKNOWN`。
3. 查询外部真实执行状态。
4. 依据结果决定确认、补偿或重试。
5. 不允许旧 Run 的迟到结果污染新 Run。

**面试核心表达：Session 共享状态串行化，Run 执行上下文隔离，工具可以异步并行，但结果必须有归属，副作用必须可追踪。**

---

## 第五题：Claude 与 OpenAI-compatible 的工具调用协议不同，如何设计 Runtime？

### 1. 核心问题

不同模型厂商的工具调用结构、消息编码、内容块和结束标记可能不同。

如果让 Runtime 直接依赖某一家厂商的 JSON 结构，未来更换模型就需要改动主循环。

### 2. 设计方案：Model Adapter + 统一内部协议

区分三层：

- **模型协议层**：Anthropic Messages、OpenAI-compatible Chat Completions 等。
- **内部语义层**：统一的 Message、ToolCall、ToolResult、ModelResponse。
- **工具协议层**：本地函数或 MCP Client / Server。

这三个层次应该解耦。

例如内部保留简化结构：

```python
class ToolCall:
    id: str
    name: str
    arguments: dict

class ModelResponse:
    content: str | None
    tool_calls: list[ToolCall]
    finish_reason: str
```

对于当前只有 Calculator、Search、Todo 的最小 Agent，这种方案已经能满足主要需求。

但 Adapter 必须是**双向转换**：

```text
厂商响应 → 内部 ModelResponse
内部消息历史 → 厂商请求格式
```

不仅要解析模型输出，也要负责下一轮工具结果的正确编码。

### 3. Content Block 与 MCP 的关系

这是我们后来深入讨论的重点。

**Content Block** 是消息内部的类型化内容单元，例如文本、工具请求、工具结果等。

**MCP** 是连接外部工具与上下文资源的协议。

两者都采用了结构化内容的思想，但解决的问题不同：

```text
LLM
 ↓  Function Calling
Model Adapter
 ↓
Agent Runtime
 ↓
Tool Registry
 ├─ Calculator（本地工具）
 └─ MCP Client
       ↓ tools/call
     MCP Server
       ↓
     外部数据库 / 文件服务 / API
```

因此，MCP 不是 Function Calling 的替代品。

它主要解决工具发现、调用和结果交换的标准化问题。

### 4. Trade-off：统一抽象的程度

| 方案 | 优点 | 缺点 |
|---|---|---|
| A：`content + tool_calls` | 简单、开发成本低 | 无法完整表达复杂内容块 |
| B：统一结构 + 必要原始数据 | 简单与协议兼容性的折中 | 需要管理厂商专有数据 |
| C：统一 Content Block 模型 | 表达能力强，适合多模态 | 实现、测试和维护复杂 |

对于你当前的 Agent Harness，我推荐 **B 的轻量版本**。

Runtime 继续使用简单的统一结构；Adapter 负责保留协议要求回传的关键原始信息，遇到无法安全转换的内容则明确报错，不能静默丢弃。

### 5. 工程边界：跨模型切换

假设 Claude 执行 20 轮后不可用，需要切换 GLM。

不能直接把完整 Anthropic 原生消息发给 GLM。

应当：

1. 确认未完成工具操作的执行状态。
2. 从 Event Log 和 Task State 恢复统一语义历史。
3. 根据 GLM 的 Context 预算压缩历史。
4. 使用 GLM Adapter 重新编码消息和工具定义。
5. 检查目标模型是否具备继续任务所需的能力。
6. 在状态一致的安全点恢复执行。

特别是厂商专有的 Thinking Block，不应直接发送给其他模型。

**面试核心表达：Model Adapter 隔离模型厂商差异，Tool Adapter 隔离工具协议差异，Runtime 只依赖稳定的内部语义模型。**

---

# 六、把五道题串起来：一套完整的生产级 Agent Harness

现在最重要的不是再记五套答案，而是建立一张知识网络。

这五道题实际上都在解决同一个问题：

**如何把不确定的 LLM 决策，组织成可控、可恢复、可验证的长期任务执行系统？**

## 1. 完整架构图

<box border radius="xl" padding={3} gap={2}>
  <box background="surface-secondary" radius="md" padding={3} align="center">
    **用户输入 / 新指令**
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center" gap={1}>
    **Session Runtime + Event Queue**
    <text size="xs" color="secondary">接收事件、管理 Run、并发控制、异常恢复</text>
    <badge color="secondary">第四题</badge>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} gap={1} align="center">
    **Task Manager**
    <text size="xs" color="secondary">Goal / Plan / State / 验收条件</text>
    <badge color="secondary">第三题</badge>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <grid columns={2} gap={2}>
    <grid-item>
      <box border radius="md" padding={3} gap={1} align="center">
        **Context Builder**
        <text size="xs" color="secondary">Token 预算、压缩、摘要</text>
        <badge color="secondary">第一题</badge>
      </box>
    </grid-item>
    <grid-item>
      <box border radius="md" padding={3} gap={1} align="center">
        **Memory Retriever**
        <text size="xs" color="secondary">长期记忆、相关性和版本</text>
        <badge color="secondary">第二题</badge>
      </box>
    </grid-item>
  </grid>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} gap={1} align="center">
    **Model Adapter → LLM**
    <text size="xs" color="secondary">协议转换、工具定义、响应解析</text>
    <badge color="secondary">第五题</badge>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box border radius="md" padding={3} align="center" gap={1}>
    **Tool Executor + Verifier**
    <text size="xs" color="secondary">权限、Schema、执行、幂等性、验证</text>
  </box>
  <row justify="center"><icon name="arrow-down" color="secondary"/></row>
  <box background="rgba(13,148,136,0.08)" border={{size:1,color:"rgba(13,148,136,0.4)"}} radius="md" padding={3} align="center" gap={1}>
    **持久化层**
    <text size="xs" color="secondary">Event Log / Task State / Tool Operations / Memory</text>
  </box>
  <caption>这张图展示职责关系。真实系统是闭环：工具结果进入 Runtime，验证后更新 Task State，再决定是否继续调用模型。</caption>
</box>

## 2. 五道题之间的逻辑联系

| 模块 | 解决的问题 | 如果缺少它 |
|---|---|---|
| Context | 模型当前能看到什么 | 历史过长、丢失关键信息 |
| Memory | 模型能从过去找回什么 | 无法跨会话利用知识与偏好 |
| Task | 模型应该完成什么、完成到哪里 | 长程任务偏航、虚假完成 |
| Runtime | 谁在执行、发生什么、如何恢复 | 并发混乱、重复副作用 |
| Adapter | 如何与不同模型和工具交互 | 厂商锁定、协议不兼容 |

还有一个特别值得理解的关系：

**Context、Memory、Task State、Event Log 不能混为一谈。**

你可以这样类比：

- **Event Log 像流水账**：记录每件事实际发生的过程。
- **Task State 像项目进度看板**：显示现在做到哪里。
- **Memory 像长期知识库**：保存可跨任务复用的信息。
- **Context 像当前工作台**：只摆放这一步需要的材料。

LLM 每次推理主要接触工作台，但真实世界的执行记录和权威状态不能只存放在工作台上。

## 3. 用一个故障场景贯穿五个模块

用户要求：

> 开发 Agent Harness，完成测试后提交 GitHub。

现在 Agent 已经执行了 200 轮，Claude API 不可用，工具正在执行，用户又要求修改开发优先级，随后 Runtime 意外崩溃。

恢复流程应该是：

<list marker="number" gap={2}>
  <list-item>**恢复事件与执行状态（第四题）**

    从 Event Log 与数据库读取持久化记录，恢复 Run 状态。先识别哪些工具操作已完成、哪些处于不确定状态，不盲目重试。

  </list-item>
  <list-item>**恢复最新任务目标（第三题）**

    重放已持久化的用户指令，重建最新 Task State。停止已被用户取消的工作，将真实 LLM API 接入设为当前优先事项。

  </list-item>
  <list-item>**检索必要历史（第二题）**

    找回项目约束、过去的架构决策，以及仍然有效的开发偏好。检查来源、版本和冲突。

  </list-item>
  <list-item>**构建新的 Context（第一题）**

    在目标模型可用的 Token 预算内，优先保留目标、约束、已验证进度、未决操作和关键证据；较早的历史使用摘要和引用。

  </list-item>
  <list-item>**切换模型并恢复循环（第五题）**

    通过 GLM Adapter 编码消息和工具定义，验证能力兼容后继续执行。Runtime 负责工具安全和状态验证，而不是让新模型自行猜测哪些操作已经完成。

  </list-item>
</list>

## 4. 再补齐你最后回答中的一个工程细节

你说：

> 先对照 Event Log 和数据库，标记出入的点，然后根据幂等性检查。

这个思路可以升级成真正的恢复算法。

前提是让 Event Log 成为可恢复的权威事件记录，并把关联的任务状态更新放在同一事务中，或采用可重复、可校验的状态投影机制。

例如：

```text
Event Log:
  #101 USER_MESSAGE_RECEIVED
  #102 PLAN_UPDATED
  #103 TOOL_EXECUTION_STARTED
  #104 TOOL_EXECUTION_SUCCEEDED

Task State:
  last_applied_event = 102
```

此时状态投影落后两个事件。

Runtime 可以从事件 `#103` 开始重放，恢复进度。

但如果事件 `#103` 是向外部服务发出请求，重放时**不能再次无条件调用该服务**。需要先检查工具操作表及外部执行状态，并使用稳定的幂等键，避免重复副作用。

这里还有两个关键保障：

- **原子持久化**：用户新指令与其接收事件必须可靠记录；事务提交之前不能假装已经保存成功。
- **幂等恢复**：状态投影可以重复计算，但外部副作用只能按照可验证的执行状态进行恢复。

因此，Event Log 不只是日志系统，更可以成为 Agent 的恢复依据。

---

## 七、最终面试回答：一分钟串联五道题

<box gap={2}>
  <text color="secondary" weight="medium" size="xs">可直接练习口述</text>
  <WritingBlock id="84716" variant="standard">我会把 Agent Harness 设计成一个**由 Runtime 负责可靠执行、由 LLM 负责智能决策**的系统。

首先，在 **Context 管理**上，我会采用分层压缩，将完整事件历史和权威任务状态独立持久化，Context 只作为可重建的推理工作视图。

其次，在 **Memory 管理**上，我会区分用户级、项目级和会话级记忆，按当前任务进行检索，并通过来源、版本、有效期和冲突检查控制记忆质量。

第三，在 **长程任务管理**上，我会分离 Goal、Plan 和 State。目标由用户确定，计划允许动态调整，进度必须由可验证的执行结果驱动，而不是依赖模型声称成功。

第四，在 **Runtime 并发控制**上，我会采用事件驱动设计，对单 Session 的共享状态串行化更新，对独立 Run 和耗时工具进行隔离及异步执行，并通过 Event Log、幂等键和状态核查支持异常恢复。

最后，在 **模型和工具适配**上，我会通过 Model Adapter 统一不同模型厂商的调用协议，通过 Tool Registry 和 MCP Client 接入工具，避免业务主循环与外部协议耦合。

这五个模块共同组成一个可观测、可恢复、可验证的 Agent 执行闭环。其核心原则是：**模型可以提出下一步行动，但系统必须控制执行边界，并验证实际结果。**</WritingBlock>
</box>

## 八、真正掌握这五题，还需要理解什么？

你现在已经形成了比较完整的架构思路。接下来与其继续增加抽象概念，我更建议你围绕自己的 Agent Harness 做三个小实验：

1. **Context 恢复实验**：模拟 200 轮历史并触发压缩，检查用户约束和执行状态是否丢失。
2. **Session 并发实验**：同时运行耗时工具和新用户指令，检查迟到工具结果是否污染新 Run。
3. **崩溃恢复实验**：故意在工具执行前后和数据库提交前后中断进程，检查重启后的状态与副作用。

这三个实验可以将你刚才学到的五道题从理论设计变成真实工程能力。

**最终可以用一句话建立知识网络：**

> **Event Log 保存过去，Memory 提供长期知识，Task State 定义现在，Context 服务当前推理，Model Adapter 连接智能能力，而 Runtime 负责让这一切安全、持续地运行。**
