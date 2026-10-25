# v0.4 主代理交付自审

无子代理；按用户指引执行同等计划/代码/交付自审。全局code-quality/review-checklist文件不可用，依AGENTS及执行/TDD/验证清单执行。

| 检查项 | 结果 | 证据/限制 |
|---|---|---|
| 独立版本 | PASS | v03精确blob基线5a962bd；实现冻结6e9f1a4；不是只给b90 |
| 旧材料保留 | PASS | 75文件hash与原工作区/基线一致，源工作区未修改 |
| 隔离变量 | PASS | R0/R1同候选；三装配同排序/Top8/字节；旧20题R0_P0重现v03 B |
| Gold/Oracle边界 | PASS | 六实际输出先于Gold评分；Gold替换测试不改输出；Oracle后验DP |
| 精确引用与预算 | PASS mechanical | 原文字符范围逐字匹配，限定/时间/身份句不截头，预算不足整包跳过；不等于语义完整证明 |
| owner/时间/删除 | PASS regression | 1211完整回归；新增过滤和未来Topic证明，正式存储/运行时未改 |
| 事实绑定 | LIMITED | 当前子句显式标签，不继承邻句；仅编号规则和小批审核，不证明普遍准确 |
| 等价来源 | LIMITED | 13项源审与明确拒绝表；span部分偏粗；同代理非盲评，原分保留 |
| 实验收益 | NOT PROVEN | 新测试P1不增、P2退化，R1只在已见题有效，不默认启用 |
| 成本/检查 | PASS bounded | 字节/延迟实际输出；单次本地测量含诊断开销，无SLA/精确Token声明 |
| 真实模型/Judge/私人数据 | NOT_RUN | 未授权故未调用，私人导入0、远端推送0 |
| Build/compile/deps | PASS | 离线wheel、源码hash、compileall、pip check、补丁/差异 |
| 静态类型/lint | NOT_RUN | 环境工具/配置缺失，不冒充通过 |

不因结果退化修改Gold、权重或删除案例；后验发现的span粒度和测时边界写进报告，不回填更好看的分数。本轮收尾，等待设计评审。
