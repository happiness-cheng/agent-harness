# Adrian长期Memory与桌面启动集成

任务已授权直接实施，保留主目录所有既有修改。使用execution-workflow、systematic-debugging、TDD；基线代码逐字归档于reports/adrian-integration/prechange-code.zip。

- [x] 审计当前真实Runtime、ContextManager与v08源码/依赖哈希；确认现有数据库无需迁移。
- [x] 固定A普通任务/B跨会话读取/C权限撤回及启动去重的失败测试。
- [x] 将v08装配移植到生产包，使用真实Registry与ManagedModel后/Provider前收据边界；不引入实验运行器。
- [x] 新增显式Memory模式和工作台开关，原默认模式不变；不自动提炼或长期写入。
- [x] 同DB排他进程锁、身份/nonce/端口核对、就绪后浏览器、明确配置失败；不回退Demo。
- [x] CMD/桌面快捷方式、离线Web/API/进程重启验证，最后只启动真实服务做无模型请求健康检查。
- [x] 回归、自审、源diff/哈希和最小用户说明；不提交/推送，完成停止。全量回归保留改动前已有的1项失败；不是全量绿色。

取舍：沿用现有Python网页，不建新桌面后端；不整体覆盖主目录，不合并v02-v08的离线排序实验。单实例锁覆盖所有本项目网页启动入口，不能阻止任意第三方绕过应用直接写SQLite。用户以Ctrl+C关闭可见服务窗口；浏览器关闭不代表服务已停。
