# 原 Gold 的两处证据边界（不改 Gold / 不补进检索输入）

本注记是检索运行完成后的离线原文审查，不能传回卡片构建、查询或排序。

## Amex Gold：历史可组合支持，原字面判据不能识别

`layer2_03_multiple_credit_cards` 的 Gold 要求 `Amex Gold` / `Amex Gold Card`。历史没有连续的这些字样：

- `amex_application_002` 消息 1：`Good morning! I'm Diana from American Express...`
- 同会话消息 3：`... Gold Card and the Platinum Card ...`
- 消息 7：`The Gold Card might be perfect for you. It earns 4x points on restaurants and groceries, 3x on flights.`
- 消息 16：`I don't fly enough for that. Let's go with the Gold Card.`
- 消息 35：`... You're approved for the Gold Card with a $15,000 credit limit.`
- `fraud_alert_003` 消息 65–66：`You mentioned you have an Amex you could use?` / `Right, my Gold Card. That's fine.`

因此，“用户的 Amex 是 Gold Card”有历史依据，但需要实体关联和跨句组合。原评分器的 `source_exists_in_legal_history=false` 实际仅表示未找到 Gold 连续表面字样，不是证明历史没有事实。A/B/C 仍沿用原 Gold，不给人工补的实体名称计分；上下文化最多前一句也没有把这些名称拼成 Gold 连续字符串。

## 600–800 美元笔记本维修费：历史无此事实

`layer3_04_warranty_coordination` 的原 `evaluation_criteria` 包含 16 英寸屏幕维修约 `600-800` 美元，原 Gold 要求 `600-800` / `600`。

完整历史里没有 `600` 字样。`laptop_purchase_001` 消息 9 只有 AppleCare+ 399 美元及屏幕 99 美元免赔额；`phone_damage_001` 消息 9 是 iPhone 14 Pro 维修费 379 美元，消息 13 是手机保障 800 美元上限。它们都不能作为笔记本 600–800 美元维修费的证据。

结论：该 Gold 项不能由给定历史召回，属于评分要求混入外部估计的证据缺口。不能把它归罪于排序器，也不能从 evaluation_criteria 复制进卡片。本轮保留原 Gold 与原分母，不补造历史、不联网查价，不声称现实维修价格或保险条款成立。
