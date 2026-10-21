# S15 持久化实施方案与首次建表范围

日期：2026-10-20（Asia/Shanghai）。状态：下列schema/路径及临时故障触发器已明确获准；S15-a完成后用户回复“同意”，允许S15-b自动接入。S15-b已实现，本步23项、全量515项通过，等待审阅；见[S15-a交付](S15-a.md)、[S15-b交付](S15-b.md)。沿用已初始化的v1格式，不重复建表或覆盖/迁移既有库。

## 小步边界

S15涉及独立存储机制与现有业务接入，为便于审阅拆分：

- S15-a：新增SQLite存储适配器，显式保存/加载session及完整todo集合；验收关闭重开、归属隔离、记录保留和真实事务回滚。完成后停下审阅。
- S15-b：在下一审阅门接入SessionStore/TodoService，验证正常业务调用自动保存与重启后的对话/待办恢复。S15-a不冒充已自动持久化所有业务操作。

本会话由主代理按writing-plans/执行流程等价清单实施，不建Git仓库/工作树、不提交或委派。用户已授权进入S15，并已确认下列首次建表范围。

## 技术选择与路径

采用既有A0建议的标准库sqlite3；原生解释器实际Python3.13.16、SQLite3.50.4，不增加依赖。SQLite提供本地磁盘事务；不选单纯JSON文件，因为会话与待办两部分写入需要原子性；暂不引入ORM或数据库服务。

拟新建：`C:\Users\<USER>\Documents\agent-harness\data\agent-harness.sqlite3`。检查时文件及data目录均不存在。正式路径只允许新建；存在则停止检查，不覆盖、不自动迁移。现有库仅通过明确open读取已知格式，打开缺失路径不自动建库。

同一schema也用于pytest临时目录中的新测试库。回滚测试只在临时库安装故障触发器，故意让todo写入失败；不在正式路径安装测试触发器。无需修改任何其他用户数据库。

## 拟执行的schema

下列DDL已获用户确认，执行证据在S15-a实现阶段记录：

```sql
PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;

CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY NOT NULL,
    owner_id TEXT NOT NULL,
    snapshot_json TEXT NOT NULL
);

CREATE TABLE todos (
    session_id TEXT NOT NULL,
    todo_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK (position >= 0),
    item_json TEXT NOT NULL,
    PRIMARY KEY (session_id, todo_id),
    UNIQUE (session_id, position),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

PRAGMA application_id = 0x41485231;
PRAGMA user_version = 1;
COMMIT;
```

- sessions索引用真实session/owner，JSON保存现有SessionSnapshot的消息及TaskState；不新增登录系统或业务目标状态机。
- todos的JSON保存现有TodoItem完整字段，包含绝对截止日及逻辑删除标记；position保留插入顺序。没有物理清除、恢复入口或存储逾期派生值。
- application_id标识本应用格式，user_version标识当前schema；既有库标识/版本不符时拒绝，不自动修复或迁移。
- 每个连接启用外键，初始化和保存都明确BEGIN/COMMIT/ROLLBACK；SQL参数使用绑定占位符，不拼接用户文本。

## S15-a公共接口及校验约定

新增`src/min_agent/storage.py`：

- SQLiteStorage.create_new(path)创建全新文件和上述schema，拒绝覆盖；支持with并在退出时关闭连接。
- SQLiteStorage.open(path)只打开既有且格式/版本匹配的库，不隐式创建或更改schema。
- save(context, snapshot, todos)保存一个会话的事实快照与完整待办集合，同一事务提交。输入按现有Pydantic模型重新校验，context与snapshot的owner/session必须一致，每个todo必须属于该session，ID不得重复。
- load(context)返回StoredSession(snapshot,todos)，未知/错误owner统一拒绝；load_all()是可信应用启动恢复入口，不注册为模型工具，先校验全部记录再一次返回。
- StoredSession只包已有SessionSnapshot与list[TodoItem]；返回对象脱离存储，调用方修改不会写回。读取重新校验JSON及列中ID/归属/顺序一致性；损坏数据拒绝，不跳过部分行。
- 已有todo身份和顺序保留，完整保存必须包含已有所有记录，包括逻辑删除项；缺项或重排拒绝。只upsert、不物理删除已有todo；新ID追加，不按同名合并。检查与写入在同一事务内。
- 不允许旧快照恢复/改写已删除项或重新打开已完成项；启动load_all拒绝孤立行，不静默跳过损坏数据。
- 同一适配器以RLock串行访问连接；本步不承诺多个应用实例/进程同时修改的合并或冲突检测。发生异常回滚并传播，不能把写入失败说成成功。

S15-a只显式存取快照；不修改现有SessionStore/TodoService/Runtime，默认业务仍是内存行为。S15-b再在锁与提交边界接入，避免先改内存、提交失败后内存与磁盘分叉。

保存内容包含既有聊天消息、调用任务状态及todo（含逻辑删除内容）；不读取凭据文件，不增加密钥字段或保存trace。当前仅本地存储，没有加密、保留期限、清理、云同步或自动重放。running任务及未配对请求保留原事实，不自动标记完成或重试工具；后续恢复策略另行实施。

## 文件与执行顺序

- 新增src/min_agent/storage.py、tests/integration/test_storage_restart.py；只在.gitignore加入根data目录，避免本地业务库进入文件清单/未来Git。
- 首先编写关闭重开及owner/session隔离行为测试，实际确认缺实现RED；获准建表后最小实现并运行GREEN。
- 再分别增加保存校验、同名/日期/逻辑删除保留、重复保存、真实SQLite失败回滚、未知文件/格式/版本/损坏JSON拒绝、资源关闭与线程串行测试。每轮记录实际失败与通过，不假称全部先失败。
- 全量旧测试回归、离线wheel、pip check、compileall、限定安全扫描，实施前后哈希对比及旧wheel保留；无Type/Lint配置时明确SKIP。
- 使用真实SQLite临时文件验收，不用mock代替数据库。正式路径仅在授权后新建并检查空schema，不写入测试业务内容。
- 更新本步讲解/三份进度/CLAUDE，在S15-a交付后停下等审阅，不自动进入S15-b或S16。

实际将执行的测试入口：

```powershell
& 'C:\Users\<USER>\Documents\agent-harness\.venv\Scripts\python.exe' -X utf8 -m pytest tests/integration/test_storage_restart.py -q
& 'C:\Users\<USER>\Documents\agent-harness\.venv\Scripts\python.exe' -X utf8 -m pytest tests/ -q
```

首次预期RED为没有min_agent.storage；最终预期关闭重开值一致、跨session/owner拒绝、故障后两表无半写。这里是预期，不是已运行结果。

## 事务、资源及依据

事务用于“整组提交或回滚”，连接关闭是另一职责；with适配器负责关闭，显式事务负责原子性，可类比C++的RAII资源与事务守卫。回滚不能泛化成网络结果丢失或外部副作用撤销，S25的未知结果/幂等键尚未实现。

已查[Python3.13 sqlite3 §Transaction control](https://docs.python.org/3.13/library/sqlite3.html#transaction-control)、[§How to work with SQLite URIs](https://docs.python.org/3.13/library/sqlite3.html#how-to-work-with-sqlite-uris)、[SQLite §Enabling Foreign Key Support](https://www.sqlite.org/foreignkeys.html#fk_enable)及[Transactions §2](https://www.sqlite.org/lang_transaction.html#transactions)。采用标准库加薄适配器，不复制第三方Agent流程。

主代理方案自审：覆盖保存/恢复、隔离、完整记录保留和事务回滚；schema与路径已具体列明且获准，未知库不覆盖；存储层与业务接入分门，不伪造测试/建表结果。

## S15-b接入决策与验收

用户已明确允许该子步，核心只改sessions.py、tools/todo.py并新增接入测试，不改storage.py、Runtime、schema或依赖。

SessionStore通过显式storage参数接入；构造时load_all，保存全量会话事实和全部待办（含逻辑删除）。持久化TodoService绑定同一store的集合及RLock，候选状态提交成功才发布。未传storage沿用原内存行为；同一数据库由单个活动SessionStore协调，不承诺多个应用实例/进程的状态合并。

验证先走业务接口关闭重开，再用真实SQLite触发器确认session/todo与Runtime保存失败传播；另查归属隔离、默认内存回归、重复操作不写、running/未配对状态保留、并发以及绝对日期恢复后的逾期重算。两次独立Python进程手动使用忽略build库验证进程退出再恢复。完整结果/代码与局限见S15-b讲解；正式空库只读核对，不写测试业务记录。S16仍需下个审阅门。
