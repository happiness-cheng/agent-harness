# Adrian Memory v0.5：逐题真实回答评测

范围：生产自动首轮Memory投影后的受控单次回答；不是自主工具循环或业务执行成绩。A无长期材料，B生产默认4096字节自动视图，C只给截至任务时点的原始必要证据。三组共享system/任务/模型/推理配置与总预算。

实际调用：0；执行状态：NOT_RUN；已知峰值计价usage费用：NOT_RUN USD；账单实付：NOT_RUN。

未执行/未审语义维度全部NOT_RUN。事实表面覆盖只是代理，不是事实准确率；自报引用不是模型内部因果证明。没有外部Judge。

## layer1_01_bank_account

问题：What was my checking account number again? I need it to set up my direct deposit at work.

任务时点：2024-11-15T10:30:00.000001+00:00

### A

参考材料：0 UTF-8字节；请求SHA256 `cc1f32f57cc849f3e9fe6486a486ced00115738d993d7404366398154516d49b`。

模型原始回答：
```text
NOT_RUN：未获本轮模型调用授权或该请求未执行。
```

评分：`{"book": {"precision": "NOT_RUN", "recall": "NOT_RUN", "reasoning": "NOT_RUN", "proactivity": "NOT_RUN"}, "dimensions": {"fact_accuracy": "NOT_RUN", "entity_scenario": "NOT_RUN", "temporal_state": "NOT_RUN", "current_conflict": "NOT_RUN", "unsupported_memory": "NOT_RUN", "evidence_use": "NOT_RUN"}, "surface_recall": "NOT_RUN", "failure_categories": []}`

实际提供的参考材料：
```json
{"application_state":{"mode":"plan","current_date":"2024-11-15","as_of":"2024-11-15T10:30:00.000001+00:00"}}
```

### B

参考材料：3885 UTF-8字节；请求SHA256 `fbf453761fdfd1815dcf5ce2a079600f115b64ae92ea14d4007ee00de3a2beaf`。

模型原始回答：
```text
NOT_RUN：未获本轮模型调用授权或该请求未执行。
```

评分：`{"book": {"precision": "NOT_RUN", "recall": "NOT_RUN", "reasoning": "NOT_RUN", "proactivity": "NOT_RUN"}, "dimensions": {"fact_accuracy": "NOT_RUN", "entity_scenario": "NOT_RUN", "temporal_state": "NOT_RUN", "current_conflict": "NOT_RUN", "unsupported_memory": "NOT_RUN", "evidence_use": "NOT_RUN"}, "surface_recall": "NOT_RUN", "failure_categories": []}`

实际提供的参考材料：
```json
{"application_state":{"mode":"plan","current_date":"2024-11-15","as_of":"2024-11-15T10:30:00.000001+00:00"},"untrusted_user_memory":{"reference_only":true,"current_request_takes_precedence":true,"precedence":["current_directive","confirmed_scenario_exception","historical_preference"],"stable_core":[],"active_core":[{"id":"fffa9582-26db-536f-99e6-2fb39372c3de","version":1,"content":"[user;session=bank_setup_001;range=0:108] Got it. Oh, I should mention, I travel internationally for work sometimes. Will my debit card work overseas?","evidence_type":"DIRECT","evidence_refs":["f5da1168-93e0-5a87-8fdb-b646143bfeff"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-11-15T10:30:00+00:00","effective_from":"2024-11-15T10:30:00+00:00","valid_until":null},{"id":"61f31410-7cb9-5267-901f-8fea99905d52","version":1,"content":"[user;session=bank_setup_001;range=0:35] I'll set it up now. Can I use 4827?","evidence_type":"DIRECT","evidence_refs":["01221257-f9e8-5e0f-a936-3ca96701a5ad"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-11-15T10:30:00+00:00","effective_from":"2024-11-15T10:30:00+00:00","valid_until":null},{"id":"24daf55f-0c0a-5bcc-b217-20448e98680a","version":1,"content":"[user;session=bank_setup_001;range=0:51] Hi, I'd like to open a new checking account please.","evidence_type":"DIRECT","evidence_refs":["b72dba85-4e9d-556c-bedc-f55ed742c6c7"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-11-15T10:30:00+00:00","effective_from":"2024-11-15T10:30:00+00:00","valid_until":null},{"id":"3b2fedaa-1a89-5470-ad4b-852a099de342","version":1,"content":"[user;session=bank_setup_001;range=0:20] What are my options?","evidence_type":"DIRECT","evidence_refs":["b9567c1c-2f9b-5a79-999e-879589df6136"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-11-15T10:30:00+00:00","effective_from":"2024-11-15T10:30:00+00:00","valid_until":null},{"id":"bade45c4-d3ca-538a-8bc2-e8635b3aaaf7","version":1,"content":"[user;session=bank_setup_001;range=0:22] Yes, I'll need checks.","evidence_type":"DIRECT","evidence_refs":["b6d8d294-6b6a-5d8d-87ab-b19f8567d631"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-11-15T10:30:00+00:00","effective_from":"2024-11-15T10:30:00+00:00","valid_until":null}],"retrieved":[{"id":"853a8c77-d936-501e-8ddf-13fb118f8467","version":1,"content":"[assistant;session=bank_setup_001;range=0:189] Of course. You can always add them through online banking or by calling us. Now, for security questions for your account, I need to set up three. First, what was the name of your first pet?","evidence_type":"INFERRED","evidence_refs":["cdfcb427-473c-5ddf-9bca-c0ef0cfb098b"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-11-15T10:30:00+00:00","effective_from":"2024-11-15T10:30:00+00:00","valid_until":null},{"id":"662f7be4-5123-5d34-b41a-339fb8926af5","version":1,"content":"[assistant;session=bank_setup_001;range=0:124] Great. I'll set that up for you. Now, for your initial deposit to open both accounts, what amount would you like to deposit?","evidence_type":"INFERRED","evidence_refs":["1f5b1f41-8423-54d0-bd0c-8873d17ead91"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-11-15T10:30:00+00:00","effective_from":"2024-11-15T10:30:00+00:00","valid_until":null},{"id":"2153b369-d683-5274-a234-255423546196","version":1,"content":"[assistant;session=bank_setup_001;range=0:174] Thank you. I've initiated that transfer. It should complete within 2-3 business days. Now, would you like to set up any automatic transfers between your checking and savings?","evidence_type":"INFERRED","evidence_refs":["13681a7c-6d48-5c01-8f3e-daf665a3d91c"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-11-15T10:30:00+00:00","effective_from":"2024-11-15T10:30:00+00:00","valid_until":null}],"root_index":[{"id":"b535d234-ba41-5cf4-a3f5-600ac60fd990","name":"First National Bank","scenarios":[]}]}}
```

### C

参考材料：389 UTF-8字节；请求SHA256 `7003012e7e75a22d7a7497768bb4f9ee6d3a7d01faaebe4e433cc0f3ee844d52`。

模型原始回答：
```text
NOT_RUN：未获本轮模型调用授权或该请求未执行。
```

评分：`{"book": {"precision": "NOT_RUN", "recall": "NOT_RUN", "reasoning": "NOT_RUN", "proactivity": "NOT_RUN"}, "dimensions": {"fact_accuracy": "NOT_RUN", "entity_scenario": "NOT_RUN", "temporal_state": "NOT_RUN", "current_conflict": "NOT_RUN", "unsupported_memory": "NOT_RUN", "evidence_use": "NOT_RUN"}, "surface_recall": "NOT_RUN", "failure_categories": []}`

实际提供的参考材料：
```json
{"application_state":{"mode":"plan","current_date":"2024-11-15","as_of":"2024-11-15T10:30:00.000001+00:00"},"untrusted_user_memory":{"reference_only":true,"current_request_takes_precedence":true,"evidence":[{"id":"99960778-2ed0-54d9-ba39-abf8cf18019e","role":"assistant","scene":"bank_setup_001","at":"2024-11-15T10:30:00+00:00","chars":[0,164],"text":"I can provide you with our routing number and your new account number for that. Our routing number is 123006800, and your new checking account number is 4429853327."}]}}
```

## layer1_08_hotel_reservation

问题：Can you remind me of my Hilton confirmation number and what room number we reserved? My wife is asking about our anniversary trip details.

任务时点：2024-11-09T19:30:00.000001+00:00

### A

参考材料：0 UTF-8字节；请求SHA256 `17980332fe4b43161bc63984b5cb701284a83b670da4b57eacc53bb53bfcd588`。

模型原始回答：
```text
NOT_RUN：未获本轮模型调用授权或该请求未执行。
```

评分：`{"book": {"precision": "NOT_RUN", "recall": "NOT_RUN", "reasoning": "NOT_RUN", "proactivity": "NOT_RUN"}, "dimensions": {"fact_accuracy": "NOT_RUN", "entity_scenario": "NOT_RUN", "temporal_state": "NOT_RUN", "current_conflict": "NOT_RUN", "unsupported_memory": "NOT_RUN", "evidence_use": "NOT_RUN"}, "surface_recall": "NOT_RUN", "failure_categories": []}`

实际提供的参考材料：
```json
{"application_state":{"mode":"plan","current_date":"2024-11-09","as_of":"2024-11-09T19:30:00.000001+00:00"}}
```

### B

参考材料：3936 UTF-8字节；请求SHA256 `95cf09c168dbf5ea18ecbf8d3f24ac5d9d2dcbbb63ab04c1406cead9272e7832`。

模型原始回答：
```text
NOT_RUN：未获本轮模型调用授权或该请求未执行。
```

评分：`{"book": {"precision": "NOT_RUN", "recall": "NOT_RUN", "reasoning": "NOT_RUN", "proactivity": "NOT_RUN"}, "dimensions": {"fact_accuracy": "NOT_RUN", "entity_scenario": "NOT_RUN", "temporal_state": "NOT_RUN", "current_conflict": "NOT_RUN", "unsupported_memory": "NOT_RUN", "evidence_use": "NOT_RUN"}, "surface_recall": "NOT_RUN", "failure_categories": []}`

实际提供的参考材料：
```json
{"application_state":{"mode":"plan","current_date":"2024-11-09","as_of":"2024-11-09T19:30:00.000001+00:00"},"untrusted_user_memory":{"reference_only":true,"current_request_takes_precedence":true,"precedence":["current_directive","confirmed_scenario_exception","historical_preference"],"stable_core":[],"active_core":[{"id":"6bf446b1-fdd0-5859-9e9f-0dbd75d1412d","version":1,"content":"[user;session=hotel_booking_001;range=0:32] What's your cancellation policy?","evidence_type":"DIRECT","evidence_refs":["5b753e1c-9c97-5459-b894-8265afa76c13"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-11-09T19:30:00+00:00","effective_from":"2024-11-09T19:30:00+00:00","valid_until":null},{"id":"4c6f4a45-ef6f-581d-a10a-96281737697a","version":1,"content":"[user;session=hotel_booking_001;range=0:54] Good to know, though we definitely won't be canceling!","evidence_type":"DIRECT","evidence_refs":["bad0e860-283a-5132-aa5b-2d983f23d898"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-11-09T19:30:00+00:00","effective_from":"2024-11-09T19:30:00+00:00","valid_until":null},{"id":"add87403-7348-5d9b-80a8-27d59c614853","version":1,"content":"[assistant;session=hotel_booking_001;range=0:106] You can cancel up to 48 hours before arrival without penalty. Within 48 hours, there's a one-night charge.","evidence_type":"INFERRED","evidence_refs":["d6261eac-deeb-5576-a9d3-cfd21c6c4a1f"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-11-09T19:30:00+00:00","effective_from":"2024-11-09T19:30:00+00:00","valid_until":null},{"id":"916d0461-a784-57a8-8d22-7a28e7a2483a","version":1,"content":"[user;session=hotel_booking_001;range=0:59] My wife is. Her number is... let me find it... HH294857301.","evidence_type":"DIRECT","evidence_refs":["6bf4f4b7-987f-54eb-9c6a-c714711137fa"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-11-09T19:30:00+00:00","effective_from":"2024-11-09T19:30:00+00:00","valid_until":null}],"retrieved":[{"id":"875118d6-d677-54c1-82e5-f6ff8c605845","version":1,"content":"[assistant;session=hotel_booking_001;range=0:81] Thank you. Your reservation is confirmed! Your confirmation number is HSD-445782.","evidence_type":"INFERRED","evidence_refs":["76a5b2ec-989e-5ff7-82fb-685b04e096b3"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-11-09T19:30:00+00:00","effective_from":"2024-11-09T19:30:00+00:00","valid_until":null},{"id":"ca615f47-4e9d-5619-a9ae-16221edca9da","version":1,"content":"[assistant;session=hotel_booking_001;range=0:82] Room 1912 is now reserved for you. Would you like to make any dinner reservations?","evidence_type":"INFERRED","evidence_refs":["55515c6b-8774-5e8f-9093-17412d89d70d"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-11-09T19:30:00+00:00","effective_from":"2024-11-09T19:30:00+00:00","valid_until":null},{"id":"a5350657-be2a-5bda-9756-fb93742c3ed2","version":1,"content":"[assistant;session=hotel_booking_001;range=0:223] It's my pleasure, Mr. Thompson! To summarize: December 8-10, room 1912 Junior Suite with bay view on the 19th floor, Romance Package included, dinner at Vela on Saturday at 7:30 PM, confirmation HSD-445782. Total is $1,333.","evidence_type":"INFERRED","evidence_refs":["0d9c1ae7-758d-575a-bf26-93b97528b209"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-11-09T19:30:00+00:00","effective_from":"2024-11-09T19:30:00+00:00","valid_until":null},{"id":"f29904cc-55c6-532d-8536-b0df803ca55a","version":1,"content":"[assistant;session=hotel_booking_001;range=0:106] Wonderful! Happy early anniversary to you and Susan. We look forward to making your celebration memorable!","evidence_type":"INFERRED","evidence_refs":["716ca2e5-09fa-5e0c-a5ae-96a01e28916e"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-11-09T19:30:00+00:00","effective_from":"2024-11-09T19:30:00+00:00","valid_until":null}],"root_index":[{"id":"ddb36b73-23fb-5d71-af54-3596de47d248","name":"Hilton Hotels","scenarios":[]}]}}
```

### C

参考材料：916 UTF-8字节；请求SHA256 `56da2f4be575cae5cad98a46ee4bea46161eb267c6c6f9eeb60d6609c5e45293`。

模型原始回答：
```text
NOT_RUN：未获本轮模型调用授权或该请求未执行。
```

评分：`{"book": {"precision": "NOT_RUN", "recall": "NOT_RUN", "reasoning": "NOT_RUN", "proactivity": "NOT_RUN"}, "dimensions": {"fact_accuracy": "NOT_RUN", "entity_scenario": "NOT_RUN", "temporal_state": "NOT_RUN", "current_conflict": "NOT_RUN", "unsupported_memory": "NOT_RUN", "evidence_use": "NOT_RUN"}, "surface_recall": "NOT_RUN", "failure_categories": []}`

实际提供的参考材料：
```json
{"application_state":{"mode":"plan","current_date":"2024-11-09","as_of":"2024-11-09T19:30:00.000001+00:00"},"untrusted_user_memory":{"reference_only":true,"current_request_takes_precedence":true,"evidence":[{"id":"875118d6-d677-54c1-82e5-f6ff8c605845","role":"assistant","scene":"hotel_booking_001","at":"2024-11-09T19:30:00+00:00","chars":[0,81],"text":"Thank you. Your reservation is confirmed! Your confirmation number is HSD-445782."},{"id":"ca615f47-4e9d-5619-a9ae-16221edca9da","role":"assistant","scene":"hotel_booking_001","at":"2024-11-09T19:30:00+00:00","chars":[0,82],"text":"Room 1912 is now reserved for you. Would you like to make any dinner reservations?"},{"id":"a5350657-be2a-5bda-9756-fb93742c3ed2","role":"assistant","scene":"hotel_booking_001","at":"2024-11-09T19:30:00+00:00","chars":[0,223],"text":"It's my pleasure, Mr. Thompson! To summarize: December 8-10, room 1912 Junior Suite with bay view on the 19th floor, Romance Package included, dinner at Vela on Saturday at 7:30 PM, confirmation HSD-445782. Total is $1,333."}]}}
```

## layer2_01_multiple_vehicles

问题：I need to schedule service for my car. Can you tell me what services I have scheduled?

任务时点：2024-11-20T09:30:00.000001+00:00

评分审计：Record says Friday November 24 while case year is 2024; calendar inconsistency must be reviewed, not silently corrected.

### A

参考材料：0 UTF-8字节；请求SHA256 `8f2ea004a7f0d3840752b9ebcb54f983172a08246908a5fac3b6c497482bfbb9`。

模型原始回答：
```text
NOT_RUN：未获本轮模型调用授权或该请求未执行。
```

评分：`{"book": {"precision": "NOT_RUN", "recall": "NOT_RUN", "reasoning": "NOT_RUN", "proactivity": "NOT_RUN"}, "dimensions": {"fact_accuracy": "NOT_RUN", "entity_scenario": "NOT_RUN", "temporal_state": "NOT_RUN", "current_conflict": "NOT_RUN", "unsupported_memory": "NOT_RUN", "evidence_use": "NOT_RUN"}, "surface_recall": "NOT_RUN", "failure_categories": []}`

实际提供的参考材料：
```json
{"application_state":{"mode":"plan","current_date":"2024-11-20","as_of":"2024-11-20T09:30:00.000001+00:00"}}
```

### B

参考材料：4069 UTF-8字节；请求SHA256 `3f47a43117609134c517114f497051efaa43ff3c8892f95cfbf68da61f748661`。

模型原始回答：
```text
NOT_RUN：未获本轮模型调用授权或该请求未执行。
```

评分：`{"book": {"precision": "NOT_RUN", "recall": "NOT_RUN", "reasoning": "NOT_RUN", "proactivity": "NOT_RUN"}, "dimensions": {"fact_accuracy": "NOT_RUN", "entity_scenario": "NOT_RUN", "temporal_state": "NOT_RUN", "current_conflict": "NOT_RUN", "unsupported_memory": "NOT_RUN", "evidence_use": "NOT_RUN"}, "surface_recall": "NOT_RUN", "failure_categories": []}`

实际提供的参考材料：
```json
{"application_state":{"mode":"plan","current_date":"2024-11-20","as_of":"2024-11-20T09:30:00.000001+00:00"},"untrusted_user_memory":{"reference_only":true,"current_request_takes_precedence":true,"precedence":["current_directive","confirmed_scenario_exception","historical_preference"],"stable_core":[],"active_core":[{"id":"d97b1af1-f074-5197-afc6-e69ac8322046","version":1,"content":"[user;session=auto_service_001;range=0:40] Good. My allergies have been bad lately.","evidence_type":"DIRECT","evidence_refs":["3fa5d23c-1906-5fba-9f6f-ee58c2c9ec64"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-11-20T09:30:00+00:00","effective_from":"2024-11-20T09:30:00+00:00","valid_until":null},{"id":"e06b2fe8-5dda-5c74-9657-64ddb987ab23","version":1,"content":"[assistant;session=auto_service_001;range=0:61] Never here. We believe in transparency and customer approval.","evidence_type":"INFERRED","evidence_refs":["b4fe4281-9097-59ed-9391-6ee89c08b3ff"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-11-20T09:30:00+00:00","effective_from":"2024-11-20T09:30:00+00:00","valid_until":null},{"id":"15d2f5e2-5d64-55e7-9e5d-465ba9005d96","version":1,"content":"[user;session=auto_service_001;range=0:42] Hi, I need to schedule service for my car.","evidence_type":"DIRECT","evidence_refs":["275d38f8-335c-5232-850a-e15f8ee86832"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-11-20T09:30:00+00:00","effective_from":"2024-11-20T09:30:00+00:00","valid_until":null},{"id":"778dba66-2486-5462-9086-fde24d518806","version":1,"content":"[user;session=auto_insurance_001;range=0:52] Hi, I need to add my new car to my insurance policy.","evidence_type":"DIRECT","evidence_refs":["942d46fc-264b-5c1d-ac9c-d5bedd3699dc"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-10-15T11:00:00+00:00","effective_from":"2024-10-15T11:00:00+00:00","valid_until":null},{"id":"2db2e0f7-2a7c-5bb2-8109-4223ff3da655","version":1,"content":"[user;session=auto_service_001;range=0:24] Can I drop it off early?","evidence_type":"DIRECT","evidence_refs":["e03d8818-1d41-5b41-a5af-3016439e7e00"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-11-20T09:30:00+00:00","effective_from":"2024-11-20T09:30:00+00:00","valid_until":null}],"retrieved":[{"id":"80bcc172-5027-52bd-b0f5-dabc8f713952","version":1,"content":"[assistant;session=auto_service_001;range=0:116] Good morning! This is Mike at Firestone Auto Care. I'll help you schedule service. What kind of service do you need?","evidence_type":"INFERRED","evidence_refs":["95f9514e-e0b8-5a37-8207-c76c7da22ef2"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-11-20T09:30:00+00:00","effective_from":"2024-11-20T09:30:00+00:00","valid_until":null},{"id":"e90817a9-7ce8-5e6e-a97f-f6084e5d1f33","version":1,"content":"[assistant;session=auto_insurance_001;range=0:101] Perfect. I see you currently have a 2019 Honda Accord on your policy. Tell me about your new vehicle.","evidence_type":"INFERRED","evidence_refs":["f5e0bb22-3cc7-5e09-9ca1-3b99d2132c08"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-10-15T11:00:00+00:00","effective_from":"2024-10-15T11:00:00+00:00","valid_until":null},{"id":"e3c48a5b-3302-507f-98a3-16c667b45a55","version":1,"content":"[user;session=auto_insurance_001;range=0:31] What if I need to file a claim?","evidence_type":"DIRECT","evidence_refs":["df359d7a-a935-5207-92cc-91b6c612fc94"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-10-15T11:00:00+00:00","effective_from":"2024-10-15T11:00:00+00:00","valid_until":null},{"id":"ff313927-58ea-50bb-93d6-4347adfb3d25","version":1,"content":"[user;session=auto_insurance_001;range=0:61] It's an addition. I'm keeping the Honda for my wife to drive.","evidence_type":"DIRECT","evidence_refs":["3314909f-03b7-58da-8bd5-95ce174cc8ef"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-10-15T11:00:00+00:00","effective_from":"2024-10-15T11:00:00+00:00","valid_until":null}],"root_index":[{"id":"fdeeeb50-da99-50d6-bbae-e46e4089599e","name":"State Farm Insurance","scenarios":[]}]}}
```

### C

参考材料：1363 UTF-8字节；请求SHA256 `9f6f9158aa4eba988e0f0aefc2d5814d941f5ae1e6b2640e6ee0ff1269f9bf8b`。

模型原始回答：
```text
NOT_RUN：未获本轮模型调用授权或该请求未执行。
```

评分：`{"book": {"precision": "NOT_RUN", "recall": "NOT_RUN", "reasoning": "NOT_RUN", "proactivity": "NOT_RUN"}, "dimensions": {"fact_accuracy": "NOT_RUN", "entity_scenario": "NOT_RUN", "temporal_state": "NOT_RUN", "current_conflict": "NOT_RUN", "unsupported_memory": "NOT_RUN", "evidence_use": "NOT_RUN"}, "surface_recall": "NOT_RUN", "failure_categories": []}`

实际提供的参考材料：
```json
{"application_state":{"mode":"plan","current_date":"2024-11-20","as_of":"2024-11-20T09:30:00.000001+00:00"},"untrusted_user_memory":{"reference_only":true,"current_request_takes_precedence":true,"evidence":[{"id":"e90817a9-7ce8-5e6e-a97f-f6084e5d1f33","role":"assistant","scene":"auto_insurance_001","at":"2024-10-15T11:00:00+00:00","chars":[0,101],"text":"Perfect. I see you currently have a 2019 Honda Accord on your policy. Tell me about your new vehicle."},{"id":"ca11c2a1-4c7e-5c88-a616-f67fc54666e9","role":"user","scene":"auto_insurance_001","at":"2024-10-15T11:00:00+00:00","chars":[0,35],"text":"I just bought a 2023 Tesla Model 3."},{"id":"0d19063b-51cc-5c16-9b13-fb69073e6548","role":"user","scene":"auto_service_001","at":"2024-11-20T09:30:00+00:00","chars":[0,25],"text":"It's a 2019 Honda Accord."},{"id":"36f75375-a95c-55c0-ad3e-37f456ebdf88","role":"assistant","scene":"auto_service_001","at":"2024-11-20T09:30:00+00:00","chars":[0,55],"text":"No problem. So just the Honda Accord on Friday at 8 AM."},{"id":"85759dd7-5f31-5d0d-95a2-003397561739","role":"assistant","scene":"auto_service_001","at":"2024-11-20T09:30:00+00:00","chars":[0,96],"text":"Exactly. Plenty of parking too. Your appointment is confirmed for Friday, November 24th at 8 AM."},{"id":"e810426e-8eeb-507b-b0a3-6c797547b845","role":"assistant","scene":"auto_service_001","at":"2024-11-20T09:30:00+00:00","chars":[0,80],"text":"Target next door will definitely be busy! Your confirmation number is FS-447291."}]}}
```

## layer2_03_multiple_credit_cards

问题：I want to book a flight and wondering which of my cards I should use to get the best rewards?

任务时点：2024-11-18T09:15:00.000001+00:00

评分审计：Amex Gold is cross-sentence composition; literal-only matching is insufficient.；Original rubric asserts zero Amex foreign fees but the selected historical call has no foreign-fee statement; do not treat unsupported external rules as user history.

### A

参考材料：0 UTF-8字节；请求SHA256 `c2cd73bed41fb601a2a82ec96b67c2c8cf98936a16f66d906eca5f243e01ea8b`。

模型原始回答：
```text
NOT_RUN：未获本轮模型调用授权或该请求未执行。
```

评分：`{"book": {"precision": "NOT_RUN", "recall": "NOT_RUN", "reasoning": "NOT_RUN", "proactivity": "NOT_RUN"}, "dimensions": {"fact_accuracy": "NOT_RUN", "entity_scenario": "NOT_RUN", "temporal_state": "NOT_RUN", "current_conflict": "NOT_RUN", "unsupported_memory": "NOT_RUN", "evidence_use": "NOT_RUN"}, "surface_recall": "NOT_RUN", "failure_categories": []}`

实际提供的参考材料：
```json
{"application_state":{"mode":"plan","current_date":"2024-11-18","as_of":"2024-11-18T09:15:00.000001+00:00"}}
```

### B

参考材料：4020 UTF-8字节；请求SHA256 `c92db97d77837fc5f64ca11bed17cdbf78a434364c046ac96362014d06a004e3`。

模型原始回答：
```text
NOT_RUN：未获本轮模型调用授权或该请求未执行。
```

评分：`{"book": {"precision": "NOT_RUN", "recall": "NOT_RUN", "reasoning": "NOT_RUN", "proactivity": "NOT_RUN"}, "dimensions": {"fact_accuracy": "NOT_RUN", "entity_scenario": "NOT_RUN", "temporal_state": "NOT_RUN", "current_conflict": "NOT_RUN", "unsupported_memory": "NOT_RUN", "evidence_use": "NOT_RUN"}, "surface_recall": "NOT_RUN", "failure_categories": []}`

实际提供的参考材料：
```json
{"application_state":{"mode":"plan","current_date":"2024-11-18","as_of":"2024-11-18T09:15:00.000001+00:00"},"untrusted_user_memory":{"reference_only":true,"current_request_takes_precedence":true,"precedence":["current_directive","confirmed_scenario_exception","historical_preference"],"stable_core":[],"active_core":[{"id":"e083158b-e94e-5a92-94e7-e1723f1d7b59","version":1,"content":"[user;session=amex_application_002;range=0:21] No trip cancellation?","evidence_type":"DIRECT","evidence_refs":["292d5c9e-f0ae-5b6b-aac4-ad179737e019"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-11-10T10:30:00+00:00","effective_from":"2024-11-10T10:30:00+00:00","valid_until":null},{"id":"75e5d642-d917-55f6-be3e-b585e9723c68","version":1,"content":"[assistant;session=fraud_alert_003;range=0:88] Likely the number was compromised online. We'll cancel this card and send a replacement.","evidence_type":"INFERRED","evidence_refs":["646f015f-39c9-5f33-8184-4eb0f6ea86a0"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-11-18T09:15:00+00:00","effective_from":"2024-11-18T09:15:00+00:00","valid_until":null},{"id":"752acdb8-ee65-5f39-82ef-797ab308b264","version":1,"content":"[assistant;session=amex_application_002;range=0:95] Most purchases must be paid in full monthly. But we have Pay Over Time for purchases over $100.","evidence_type":"INFERRED","evidence_refs":["8761178d-2a63-5148-bf54-44552b7a64f4"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-11-10T10:30:00+00:00","effective_from":"2024-11-10T10:30:00+00:00","valid_until":null},{"id":"07e77c7f-f7ef-5eff-88b1-64f5a2f55c3e","version":1,"content":"[user;session=chase_card_activation_001;range=0:41] Not yet, I need to get the exact balance.","evidence_type":"DIRECT","evidence_refs":["5ab32ae2-4778-5554-bebc-d86dbc09e660"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-10-05T14:00:00+00:00","effective_from":"2024-10-05T14:00:00+00:00","valid_until":null}],"retrieved":[{"id":"f2622a47-a662-5eef-9c42-41b273cb801e","version":1,"content":"[user;session=fraud_alert_003;range=0:41] Should I be worried about my other cards?","evidence_type":"DIRECT","evidence_refs":["3be29cd3-b5d1-589c-bdc7-6540332b74f7"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-11-18T09:15:00+00:00","effective_from":"2024-11-18T09:15:00+00:00","valid_until":null},{"id":"6659bfc1-598d-50c0-9ae6-2634c392834d","version":1,"content":"[user;session=fraud_alert_003;range=0:63] Um, I have multiple Chase cards. Which one has the fraud alert?","evidence_type":"DIRECT","evidence_refs":["e4c077b1-4b4c-5302-8191-aa47a880a4fe"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-11-18T09:15:00+00:00","effective_from":"2024-11-18T09:15:00+00:00","valid_until":null},{"id":"66cd0ab5-7c72-58ae-9404-43ad38a27a68","version":1,"content":"[assistant;session=chase_card_activation_001;range=0:93] For travel, you might want to consider our Sapphire Preferred or Reserve cards in the future.","evidence_type":"INFERRED","evidence_refs":["2c5c3403-e5cb-53d8-887b-1cf3121cdec6"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-10-05T14:00:00+00:00","effective_from":"2024-10-05T14:00:00+00:00","valid_until":null},{"id":"41328c75-e564-52ec-94f9-07309cf872be","version":1,"content":"[user;session=fraud_alert_003;range=0:37] And I can't use the old card anymore?","evidence_type":"DIRECT","evidence_refs":["01545ed8-fdce-51bb-b04b-bfe21cd64d3f"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-11-18T09:15:00+00:00","effective_from":"2024-11-18T09:15:00+00:00","valid_until":null},{"id":"b720041d-7cbf-5195-a080-20b5e410d180","version":1,"content":"[user;session=chase_card_activation_001;range=0:69] Hi, I just received my new Chase credit card and need to activate it.","evidence_type":"DIRECT","evidence_refs":["36a9853a-7979-5d50-8f08-a24d2d7c857d"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-10-05T14:00:00+00:00","effective_from":"2024-10-05T14:00:00+00:00","valid_until":null}],"root_index":[]}}
```

### C

参考材料：2450 UTF-8字节；请求SHA256 `1ac30ebca573168009c0e9009802e9b75cc2970b26627e3dc1d0ab72d68830b3`。

模型原始回答：
```text
NOT_RUN：未获本轮模型调用授权或该请求未执行。
```

评分：`{"book": {"precision": "NOT_RUN", "recall": "NOT_RUN", "reasoning": "NOT_RUN", "proactivity": "NOT_RUN"}, "dimensions": {"fact_accuracy": "NOT_RUN", "entity_scenario": "NOT_RUN", "temporal_state": "NOT_RUN", "current_conflict": "NOT_RUN", "unsupported_memory": "NOT_RUN", "evidence_use": "NOT_RUN"}, "surface_recall": "NOT_RUN", "failure_categories": []}`

实际提供的参考材料：
```json
{"application_state":{"mode":"plan","current_date":"2024-11-18","as_of":"2024-11-18T09:15:00.000001+00:00"},"untrusted_user_memory":{"reference_only":true,"current_request_takes_precedence":true,"evidence":[{"id":"f5cdb8d6-6ab4-512c-9ec6-865274570f21","role":"assistant","scene":"chase_card_activation_001","at":"2024-10-05T14:00:00+00:00","chars":[0,81],"text":"Perfect. I see your Freedom Unlimited ending in 7823. Your card is now activated!"},{"id":"50fb183c-e1f2-51cf-9159-29af484c25a3","role":"assistant","scene":"chase_card_activation_001","at":"2024-10-05T14:00:00+00:00","chars":[0,76],"text":"Of course! You earn 1.5% cash back on all purchases, no categories to track."},{"id":"7077d438-ba1e-57b5-830a-9f7b3b79ca71","role":"assistant","scene":"chase_card_activation_001","at":"2024-10-05T14:00:00+00:00","chars":[0,64],"text":"There's a 3% foreign transaction fee on international purchases."},{"id":"c10f22bc-3eea-593d-943e-6596a2b5caf8","role":"assistant","scene":"amex_application_002","at":"2024-11-10T10:30:00+00:00","chars":[0,141],"text":"Good morning! I'm Diana from American Express. I'd be happy to help you find the perfect travel card. Are you familiar with our travel cards?"},{"id":"d72ea585-b5bd-5d5e-8f22-90a0edd5766a","role":"assistant","scene":"amex_application_002","at":"2024-11-10T10:30:00+00:00","chars":[0,103],"text":"The Gold Card might be perfect for you. It earns 4x points on restaurants and groceries, 3x on flights."},{"id":"daf2090e-de97-5ee7-8021-1aba8149ebe0","role":"assistant","scene":"amex_application_002","at":"2024-11-10T10:30:00+00:00","chars":[0,96],"text":"The Gold Card is $250 annually, but it includes $120 in dining credits and $120 in Uber credits."},{"id":"4bd54a15-dc51-5346-9fd5-d2441425a013","role":"user","scene":"amex_application_002","at":"2024-11-10T10:30:00+00:00","chars":[0,57],"text":"I don't fly enough for that. Let's go with the Gold Card."},{"id":"3d478724-7ffe-583a-b346-393ef70912da","role":"assistant","scene":"amex_application_002","at":"2024-11-10T10:30:00+00:00","chars":[0,102],"text":"Let me process this... Congratulations! You're approved for the Gold Card with a $15,000 credit limit."},{"id":"2a1a6a03-cffc-5873-909f-48ab6894756b","role":"user","scene":"fraud_alert_003","at":"2024-11-18T09:15:00+00:00","chars":[0,28],"text":"That's my Freedom Unlimited."},{"id":"75e5d642-d917-55f6-be3e-b585e9723c68","role":"assistant","scene":"fraud_alert_003","at":"2024-11-18T09:15:00+00:00","chars":[0,88],"text":"Likely the number was compromised online. We'll cancel this card and send a replacement."}]}}
```

## layer3_01_travel_coordination

问题：I'm finalizing my trip to Tokyo in January. Is there anything I need to take care of before I go?

任务时点：2024-11-22T10:00:00.000001+00:00

评分审计：Original rubric passport-validity/entry rules are external knowledge, not established by these source facts; do not infer country law from generic advice.

### A

参考材料：0 UTF-8字节；请求SHA256 `892fb59959fdfc818779b5a260a913a1c89a3524d15cbe6d445cedd0dc5eef33`。

模型原始回答：
```text
NOT_RUN：未获本轮模型调用授权或该请求未执行。
```

评分：`{"book": {"precision": "NOT_RUN", "recall": "NOT_RUN", "reasoning": "NOT_RUN", "proactivity": "NOT_RUN"}, "dimensions": {"fact_accuracy": "NOT_RUN", "entity_scenario": "NOT_RUN", "temporal_state": "NOT_RUN", "current_conflict": "NOT_RUN", "unsupported_memory": "NOT_RUN", "evidence_use": "NOT_RUN"}, "surface_recall": "NOT_RUN", "failure_categories": []}`

实际提供的参考材料：
```json
{"application_state":{"mode":"plan","current_date":"2024-11-22","as_of":"2024-11-22T10:00:00.000001+00:00"}}
```

### B

参考材料：4019 UTF-8字节；请求SHA256 `030999d62025960e21d4bcf473e2dddf4a0df4f400a66d3b89004d26de744539`。

模型原始回答：
```text
NOT_RUN：未获本轮模型调用授权或该请求未执行。
```

评分：`{"book": {"precision": "NOT_RUN", "recall": "NOT_RUN", "reasoning": "NOT_RUN", "proactivity": "NOT_RUN"}, "dimensions": {"fact_accuracy": "NOT_RUN", "entity_scenario": "NOT_RUN", "temporal_state": "NOT_RUN", "current_conflict": "NOT_RUN", "unsupported_memory": "NOT_RUN", "evidence_use": "NOT_RUN"}, "surface_recall": "NOT_RUN", "failure_categories": []}`

实际提供的参考材料：
```json
{"application_state":{"mode":"plan","current_date":"2024-11-22","as_of":"2024-11-22T10:00:00.000001+00:00"},"untrusted_user_memory":{"reference_only":true,"current_request_takes_precedence":true,"precedence":["current_directive","confirmed_scenario_exception","historical_preference"],"stable_core":[],"active_core":[{"id":"80a3a2b8-184a-556c-9f4b-788ac30e9abc","version":1,"content":"[user;session=passport_renewal_001;range=0:56] It expires in February 2025, so about 8 months from now.","evidence_type":"DIRECT","evidence_refs":["f3a853a7-711e-5d2b-9298-444dcaa72fce"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-06-15T14:00:00+00:00","effective_from":"2024-06-15T14:00:00+00:00","valid_until":null},{"id":"dd76913a-0457-5dc6-bb68-b6e52099576c","version":1,"content":"[user;session=flight_booking_002;range=0:46] That's right. What's your cancellation policy?","evidence_type":"DIRECT","evidence_refs":["a14f1f28-b2d6-59e2-bc0e-a2282cd74b29"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-11-22T10:00:00+00:00","effective_from":"2024-11-22T10:00:00+00:00","valid_until":null},{"id":"355f9592-2029-5e9e-81f4-e0ffd51a1b3c","version":1,"content":"[user;session=passport_renewal_001;range=0:53] October deadline, got it. What about visas for Japan?","evidence_type":"DIRECT","evidence_refs":["aafce059-fa7a-531b-ae6a-3c71a732ed25"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-06-15T14:00:00+00:00","effective_from":"2024-06-15T14:00:00+00:00","valid_until":null},{"id":"a9597ecb-a141-55a7-a180-2a752ea2d989","version":1,"content":"[assistant;session=flight_booking_002;range=0:142] I need to mention - Japan requires your passport to be valid for the duration of your stay. Yours expires less than a month after your return.","evidence_type":"INFERRED","evidence_refs":["057d8d69-297c-58bc-a8ad-f7a685c88430"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-11-22T10:00:00+00:00","effective_from":"2024-11-22T10:00:00+00:00","valid_until":null}],"retrieved":[{"id":"ff7a733f-01d7-5b24-a5d7-45043e143af4","version":1,"content":"[user;session=passport_renewal_001;range=0:81] Good to know. I think I'll wait until my Japan trip is confirmed before renewing.","evidence_type":"DIRECT","evidence_refs":["eab0ad22-b058-5805-bd57-48bbc2b00ebf"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-06-15T14:00:00+00:00","effective_from":"2024-06-15T14:00:00+00:00","valid_until":null},{"id":"42abf14b-9c4b-50e3-9f5a-c87148d81222","version":1,"content":"[assistant;session=passport_renewal_001;range=0:158] For Japan in January, your passport would need to be valid for at least the duration of your stay. With a February expiration, you'd be cutting it very close.","evidence_type":"INFERRED","evidence_refs":["cf2d74f2-64db-567a-991c-880cbb142283"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-06-15T14:00:00+00:00","effective_from":"2024-06-15T14:00:00+00:00","valid_until":null},{"id":"63ca7202-14e4-5efd-a2ac-874952b67435","version":1,"content":"[assistant;session=flight_booking_002;range=0:61] Absolutely. Is there anything else I can help you with today?","evidence_type":"INFERRED","evidence_refs":["02228b3d-c1be-5797-b422-1f0535b87a69"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-11-22T10:00:00+00:00","effective_from":"2024-11-22T10:00:00+00:00","valid_until":null},{"id":"8fec16f0-3f44-5f9a-844d-2059fd924ba5","version":1,"content":"[user;session=flight_booking_002;range=0:53] I need to leave January 15th and return January 22nd.","evidence_type":"DIRECT","evidence_refs":["2daf32cc-4d3f-5d1d-86bd-3698953faeec"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-11-22T10:00:00+00:00","effective_from":"2024-11-22T10:00:00+00:00","valid_until":null}],"root_index":[{"id":"7b2e6410-0c2f-55f5-af52-4b11f2a7d34e","name":"USPS Passport Services","scenarios":[]},{"id":"836b7481-8a6f-52b5-aa8f-a51f51db82f7","name":"Chase Bank","scenarios":[]},{"id":"6e577b78-af29-55c4-9956-18ca453b38bd","name":"Delta Airlines","scenarios":[]}]}}
```

### C

参考材料：1642 UTF-8字节；请求SHA256 `98c8180adc0c9717f8f7cf84b1a3a823a6ea7fd05ca566ced75be4f671a3d137`。

模型原始回答：
```text
NOT_RUN：未获本轮模型调用授权或该请求未执行。
```

评分：`{"book": {"precision": "NOT_RUN", "recall": "NOT_RUN", "reasoning": "NOT_RUN", "proactivity": "NOT_RUN"}, "dimensions": {"fact_accuracy": "NOT_RUN", "entity_scenario": "NOT_RUN", "temporal_state": "NOT_RUN", "current_conflict": "NOT_RUN", "unsupported_memory": "NOT_RUN", "evidence_use": "NOT_RUN"}, "surface_recall": "NOT_RUN", "failure_categories": []}`

实际提供的参考材料：
```json
{"application_state":{"mode":"plan","current_date":"2024-11-22","as_of":"2024-11-22T10:00:00.000001+00:00"},"untrusted_user_memory":{"reference_only":true,"current_request_takes_precedence":true,"evidence":[{"id":"aa2c9e03-35a1-597c-b714-bd4824f3b67b","role":"assistant","scene":"passport_renewal_001","at":"2024-06-15T14:00:00+00:00","chars":[0,53],"text":"Thank you, Ms. Martinez. When exactly does it expire?"},{"id":"f7ca19e6-6973-5027-8a1a-562bb84b3c1d","role":"user","scene":"passport_renewal_001","at":"2024-06-15T14:00:00+00:00","chars":[0,20],"text":"February 18th, 2025."},{"id":"a4d8038a-a563-52dc-ba0d-f69c41b78a96","role":"assistant","scene":"flight_booking_002","at":"2024-11-22T10:00:00+00:00","chars":[0,117],"text":"I have a non-stop flight, DL 7 departing LAX at 11:30 AM on January 15th, arriving Haneda at 4:45 PM on January 16th."},{"id":"89f90bb9-5e6a-5fc8-a919-b541c0c07492","role":"assistant","scene":"flight_booking_002","at":"2024-11-22T10:00:00+00:00","chars":[0,86],"text":"DL 8 departs Haneda at 6:00 PM on January 22nd, arriving LAX at 10:15 AM the same day."},{"id":"9259442c-45ff-538b-8ad1-408ecc003dab","role":"assistant","scene":"flight_booking_002","at":"2024-11-22T10:00:00+00:00","chars":[0,65],"text":"Processing... Approved! Your confirmation number is DELTA-JMK892."},{"id":"3423c527-7399-5720-8f25-c2b8aba5a164","role":"assistant","scene":"credit_card_003","at":"2024-10-05T15:30:00+00:00","chars":[0,73],"text":"You have Priority Pass membership included. Over 1,300 lounges worldwide."},{"id":"8c27432c-e8a0-58be-9c4f-e1123412bd92","role":"assistant","scene":"credit_card_003","at":"2024-10-05T15:30:00+00:00","chars":[0,94],"text":"You can use the digital card in the Priority Pass app. Just download and link to your account."}]}}
```

## layer3_03_home_purchase_coordination

问题：We're getting close to our house closing. Can you check if everything is on track for the move?

任务时点：2024-11-18T10:00:00.000001+00:00

评分审计：As-of is 2024-11-18; November 20 preapproval has NOT yet expired. Original rubric saying already expired conflicts with legal task time.

### A

参考材料：0 UTF-8字节；请求SHA256 `fc26750291055b7f26eb8de2f89d36ccfda8d6e88e522f4db6be7bcea9efe1ab`。

模型原始回答：
```text
NOT_RUN：未获本轮模型调用授权或该请求未执行。
```

评分：`{"book": {"precision": "NOT_RUN", "recall": "NOT_RUN", "reasoning": "NOT_RUN", "proactivity": "NOT_RUN"}, "dimensions": {"fact_accuracy": "NOT_RUN", "entity_scenario": "NOT_RUN", "temporal_state": "NOT_RUN", "current_conflict": "NOT_RUN", "unsupported_memory": "NOT_RUN", "evidence_use": "NOT_RUN"}, "surface_recall": "NOT_RUN", "failure_categories": []}`

实际提供的参考材料：
```json
{"application_state":{"mode":"plan","current_date":"2024-11-18","as_of":"2024-11-18T10:00:00.000001+00:00"}}
```

### B

参考材料：4055 UTF-8字节；请求SHA256 `0874d7dd283c77fa33b346f75976c3b271b8c2780d8f4b9a628613c8fc5b6431`。

模型原始回答：
```text
NOT_RUN：未获本轮模型调用授权或该请求未执行。
```

评分：`{"book": {"precision": "NOT_RUN", "recall": "NOT_RUN", "reasoning": "NOT_RUN", "proactivity": "NOT_RUN"}, "dimensions": {"fact_accuracy": "NOT_RUN", "entity_scenario": "NOT_RUN", "temporal_state": "NOT_RUN", "current_conflict": "NOT_RUN", "unsupported_memory": "NOT_RUN", "evidence_use": "NOT_RUN"}, "surface_recall": "NOT_RUN", "failure_categories": []}`

实际提供的参考材料：
```json
{"application_state":{"mode":"plan","current_date":"2024-11-18","as_of":"2024-11-18T10:00:00.000001+00:00"},"untrusted_user_memory":{"reference_only":true,"current_request_takes_precedence":true,"precedence":["current_directive","confirmed_scenario_exception","historical_preference"],"stable_core":[],"active_core":[{"id":"8d47bf3b-aad4-5bef-917b-ff0346418d59","version":1,"content":"[assistant;session=mortgage_preapproval_001;range=0:123] You're welcome! Remember, this expires November 20th. Call me when you find a house or if you need an extension. Good luck!","evidence_type":"INFERRED","evidence_refs":["b267506b-c54d-5e64-9936-3db4e2c1a6e4"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-08-20T11:00:00+00:00","effective_from":"2024-08-20T11:00:00+00:00","valid_until":null},{"id":"47e9d679-16fe-5d5e-921a-e0bd9c48ad3b","version":1,"content":"[assistant;session=mortgage_preapproval_001;range=0:78] Wire transfer is safest. Never wire without calling us to verify instructions.","evidence_type":"INFERRED","evidence_refs":["0969c3e2-0327-578c-8880-aa7182afc964"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-08-20T11:00:00+00:00","effective_from":"2024-08-20T11:00:00+00:00","valid_until":null},{"id":"83725be9-94b0-5c23-af1e-aa7a03d405a0","version":1,"content":"[assistant;session=home_purchase_offer_002;range=0:56] Your Wells Fargo approval is solid. When does it expire?","evidence_type":"INFERRED","evidence_refs":["d68a333c-c46a-5dfe-bf3f-6ac9a0e687f2"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-11-15T14:00:00+00:00","effective_from":"2024-11-15T14:00:00+00:00","valid_until":null},{"id":"7e82c22e-decb-51f5-9422-822a1afd1eb0","version":1,"content":"[user;session=moving_company_quote_003;range=0:70] Hi, I need to schedule a move. Our offer on a house was just accepted!","evidence_type":"DIRECT","evidence_refs":["c87d269d-d0d3-5ab5-8cdf-60301c158d9f"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-11-18T10:00:00+00:00","effective_from":"2024-11-18T10:00:00+00:00","valid_until":null}],"retrieved":[{"id":"6f6b250b-e627-59c9-9ec9-950c46d14127","version":1,"content":"[assistant;session=moving_company_quote_003;range=0:58] We'll call December 19th to confirm everything's on track.","evidence_type":"INFERRED","evidence_refs":["805dcb9b-5eb1-50eb-b539-b9ce45f2ecaf"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-11-18T10:00:00+00:00","effective_from":"2024-11-18T10:00:00+00:00","valid_until":null},{"id":"c4e4b52f-57d6-597d-9ff1-559e3420abb1","version":1,"content":"[assistant;session=home_purchase_offer_002;range=0:81] That's only 5 days away! You'll need to extend it if we're closing December 20th.","evidence_type":"INFERRED","evidence_refs":["f029643c-2f25-508c-8144-773b2dd75b6b"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-11-15T14:00:00+00:00","effective_from":"2024-11-15T14:00:00+00:00","valid_until":null},{"id":"c4df51f8-380d-5e05-81df-eb9d84a351f2","version":1,"content":"[assistant;session=home_purchase_offer_002;range=0:146] Hi Michael! This is Tom from Keller Williams. Yes, your agent Emma asked me to coordinate the paperwork. You're offering on 5522 Oak Valley Drive?","evidence_type":"INFERRED","evidence_refs":["f3c8c882-6e89-5c15-8241-6f6faec7e6b3"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-11-15T14:00:00+00:00","effective_from":"2024-11-15T14:00:00+00:00","valid_until":null},{"id":"9b5f4feb-b517-5322-8226-7f38baf13cac","version":1,"content":"[assistant;session=home_purchase_offer_002;range=0:80] Wait until the offer's accepted, but then move quickly. You need it for closing.","evidence_type":"INFERRED","evidence_refs":["130e76ca-d122-58a5-96f9-cb313b4062a4"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-11-15T14:00:00+00:00","effective_from":"2024-11-15T14:00:00+00:00","valid_until":null}],"root_index":[{"id":"712d449a-93ee-5916-8832-c76396947383","name":"Wells Fargo Home Mortgage","scenarios":[]},{"id":"080d3294-55f4-5b0f-af99-f931520c1580","name":"Keller Williams Realty","scenarios":[]}]}}
```

### C

参考材料：2274 UTF-8字节；请求SHA256 `2f2622528721bf280f11fa7efdb808f8e6b5c9d9c3e5827135f69fab0a3d2f67`。

模型原始回答：
```text
NOT_RUN：未获本轮模型调用授权或该请求未执行。
```

评分：`{"book": {"precision": "NOT_RUN", "recall": "NOT_RUN", "reasoning": "NOT_RUN", "proactivity": "NOT_RUN"}, "dimensions": {"fact_accuracy": "NOT_RUN", "entity_scenario": "NOT_RUN", "temporal_state": "NOT_RUN", "current_conflict": "NOT_RUN", "unsupported_memory": "NOT_RUN", "evidence_use": "NOT_RUN"}, "surface_recall": "NOT_RUN", "failure_categories": []}`

实际提供的参考材料：
```json
{"application_state":{"mode":"plan","current_date":"2024-11-18","as_of":"2024-11-18T10:00:00.000001+00:00"},"untrusted_user_memory":{"reference_only":true,"current_request_takes_precedence":true,"evidence":[{"id":"49ca4d77-c40a-5998-bd19-3075d582f970","role":"assistant","scene":"mortgage_preapproval_001","at":"2024-08-20T11:00:00+00:00","chars":[0,75],"text":"90 days, until November 20th, 2024. If you need an extension, just call us."},{"id":"c51f3106-41e9-51ea-b7b0-dea4b9e499ac","role":"assistant","scene":"home_purchase_offer_002","at":"2024-11-15T14:00:00+00:00","chars":[0,56],"text":"Let's put December 20th as closing date. That's 35 days."},{"id":"22a7c64b-3bfd-52dd-866d-95557b4fc67a","role":"user","scene":"home_purchase_offer_002","at":"2024-11-15T14:00:00+00:00","chars":[0,47],"text":"Yes. My parents are gifting us $30,000 to help."},{"id":"b931df9e-0a8d-5ac2-8640-490063bca36c","role":"user","scene":"home_purchase_offer_002","at":"2024-11-15T14:00:00+00:00","chars":[0,44],"text":"Wells Fargo mentioned we need a gift letter."},{"id":"b6d5e559-ee05-57f7-9028-faff50c958ab","role":"assistant","scene":"home_purchase_offer_002","at":"2024-11-15T14:00:00+00:00","chars":[0,56],"text":"Yes, 10 days is typical. And your financing contingency?"},{"id":"e86b36a7-7f3d-5e85-a486-856d39627ddc","role":"assistant","scene":"home_purchase_offer_002","at":"2024-11-15T14:00:00+00:00","chars":[0,81],"text":"Good. We'll include 21 days for financing contingency. When do you want to close?"},{"id":"173ad80a-e709-515b-b545-f760eed3750f","role":"user","scene":"moving_company_quote_003","at":"2024-11-18T10:00:00+00:00","chars":[0,90],"text":"Oh, I should check that. Speaking of insurance, I need to get homeowner's insurance still."},{"id":"85d3546f-a5ca-5d40-8329-7fd0847b448a","role":"assistant","scene":"moving_company_quote_003","at":"2024-11-18T10:00:00+00:00","chars":[0,60],"text":"Definitely do that before closing. The bank will require it."},{"id":"07c4e201-0bb9-5bb5-a8de-f64a164d56e2","role":"assistant","scene":"moving_company_quote_003","at":"2024-11-18T10:00:00+00:00","chars":[0,51],"text":"Usually a week before closing, so by December 13th."},{"id":"e84b87e4-afa6-5a36-b766-d5507b327fbf","role":"assistant","scene":"moving_company_quote_003","at":"2024-11-18T10:00:00+00:00","chars":[0,78],"text":"I'll note your move is contingent on closing. Your confirmation is ALV-122124."}]}}
```

## v05_current_instruction

问题：只用一句中文告诉我，记录中已经确认的项目代号是什么？不要按以前的偏好写长篇英文。

任务时点：2024-10-01T00:00:00.000001+00:00

### A

参考材料：0 UTF-8字节；请求SHA256 `f927545aefddf0ffb1d65ef3ceb49bf58dfdaffaea9dce7a7ad42f2151027cd2`。

模型原始回答：
```text
NOT_RUN：未获本轮模型调用授权或该请求未执行。
```

评分：`{"book": {"precision": "NOT_RUN", "recall": "NOT_RUN", "reasoning": "NOT_RUN", "proactivity": "NOT_RUN"}, "dimensions": {"fact_accuracy": "NOT_RUN", "entity_scenario": "NOT_RUN", "temporal_state": "NOT_RUN", "current_conflict": "NOT_RUN", "unsupported_memory": "NOT_RUN", "evidence_use": "NOT_RUN"}, "surface_recall": "NOT_RUN", "failure_categories": []}`

实际提供的参考材料：
```json
{"application_state":{"mode":"plan","current_date":"2024-10-01","as_of":"2024-10-01T00:00:00.000001+00:00"}}
```

### B

参考材料：1075 UTF-8字节；请求SHA256 `2f7e227ef809beba40cacc24479ffc47bd8556d88461bdd6893b1644805e3c61`。

模型原始回答：
```text
NOT_RUN：未获本轮模型调用授权或该请求未执行。
```

评分：`{"book": {"precision": "NOT_RUN", "recall": "NOT_RUN", "reasoning": "NOT_RUN", "proactivity": "NOT_RUN"}, "dimensions": {"fact_accuracy": "NOT_RUN", "entity_scenario": "NOT_RUN", "temporal_state": "NOT_RUN", "current_conflict": "NOT_RUN", "unsupported_memory": "NOT_RUN", "evidence_use": "NOT_RUN"}, "surface_recall": "NOT_RUN", "failure_categories": []}`

实际提供的参考材料：
```json
{"application_state":{"mode":"plan","current_date":"2024-10-01","as_of":"2024-10-01T00:00:00.000001+00:00"},"untrusted_user_memory":{"reference_only":true,"current_request_takes_precedence":true,"precedence":["current_directive","confirmed_scenario_exception","historical_preference"],"stable_core":[],"active_core":[{"id":"c23bc9fd-ee7b-5f72-bbb1-9710b674c246","version":1,"content":"[user;session=preferences;range=0:20] 项目代号已经确认是 ORCHID-17。","evidence_type":"DIRECT","evidence_refs":["71616b9c-bb5f-53c7-8791-6c40f05e3561"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-10-01T00:00:00+00:00","effective_from":"2024-10-01T00:00:00+00:00","valid_until":null},{"id":"65376040-be0d-54e4-8eda-0e7e28650925","version":1,"content":"[user;session=preferences;range=0:37] I prefer detailed answers in English.","evidence_type":"DIRECT","evidence_refs":["22cdff85-8c4a-5cb9-a146-04ec361af8ae"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-10-01T00:00:00+00:00","effective_from":"2024-10-01T00:00:00+00:00","valid_until":null}],"retrieved":[],"root_index":[{"id":"e8311269-98ca-56e0-8991-abf8d776c9ae","name":"synthetic","scenarios":[]}]}}
```

### C

参考材料：433 UTF-8字节；请求SHA256 `281e90577e2bb4ded58499c8a5d5f28e1d4f9809c9ee284067974d56d2777202`。

模型原始回答：
```text
NOT_RUN：未获本轮模型调用授权或该请求未执行。
```

评分：`{"book": {"precision": "NOT_RUN", "recall": "NOT_RUN", "reasoning": "NOT_RUN", "proactivity": "NOT_RUN"}, "dimensions": {"fact_accuracy": "NOT_RUN", "entity_scenario": "NOT_RUN", "temporal_state": "NOT_RUN", "current_conflict": "NOT_RUN", "unsupported_memory": "NOT_RUN", "evidence_use": "NOT_RUN"}, "surface_recall": "NOT_RUN", "failure_categories": []}`

实际提供的参考材料：
```json
{"application_state":{"mode":"plan","current_date":"2024-10-01","as_of":"2024-10-01T00:00:00.000001+00:00"},"untrusted_user_memory":{"reference_only":true,"current_request_takes_precedence":true,"evidence":[{"id":"65376040-be0d-54e4-8eda-0e7e28650925","role":"user","scene":"preferences","at":"2024-10-01T00:00:00+00:00","chars":[0,37],"text":"I prefer detailed answers in English."},{"id":"c23bc9fd-ee7b-5f72-bbb1-9710b674c246","role":"user","scene":"preferences","at":"2024-10-01T00:00:00+00:00","chars":[0,20],"text":"项目代号已经确认是 ORCHID-17。"}]}}
```

## v05_temporal_state

问题：截至2024年10月5日，设备D7当前是什么状态？

任务时点：2024-10-05T00:00:00+00:00

### A

参考材料：0 UTF-8字节；请求SHA256 `789f5d70e6875252651bd45feaf4911d9a0fc9300829c04c5d7146a0829474bc`。

模型原始回答：
```text
NOT_RUN：未获本轮模型调用授权或该请求未执行。
```

评分：`{"book": {"precision": "NOT_RUN", "recall": "NOT_RUN", "reasoning": "NOT_RUN", "proactivity": "NOT_RUN"}, "dimensions": {"fact_accuracy": "NOT_RUN", "entity_scenario": "NOT_RUN", "temporal_state": "NOT_RUN", "current_conflict": "NOT_RUN", "unsupported_memory": "NOT_RUN", "evidence_use": "NOT_RUN"}, "surface_recall": "NOT_RUN", "failure_categories": []}`

实际提供的参考材料：
```json
{"application_state":{"mode":"plan","current_date":"2024-10-05","as_of":"2024-10-05T00:00:00+00:00"}}
```

### B

参考材料：1075 UTF-8字节；请求SHA256 `f78e9c3bfe1801847d2f966cc0cc5d3a5529bc109782c6beaf0e221ecca1e6c7`。

模型原始回答：
```text
NOT_RUN：未获本轮模型调用授权或该请求未执行。
```

评分：`{"book": {"precision": "NOT_RUN", "recall": "NOT_RUN", "reasoning": "NOT_RUN", "proactivity": "NOT_RUN"}, "dimensions": {"fact_accuracy": "NOT_RUN", "entity_scenario": "NOT_RUN", "temporal_state": "NOT_RUN", "current_conflict": "NOT_RUN", "unsupported_memory": "NOT_RUN", "evidence_use": "NOT_RUN"}, "surface_recall": "NOT_RUN", "failure_categories": []}`

实际提供的参考材料：
```json
{"application_state":{"mode":"plan","current_date":"2024-10-05","as_of":"2024-10-05T00:00:00+00:00"},"untrusted_user_memory":{"reference_only":true,"current_request_takes_precedence":true,"precedence":["current_directive","confirmed_scenario_exception","historical_preference"],"stable_core":[],"active_core":[{"id":"e0b1d053-6b82-576c-8fba-bae920eb3034","version":1,"content":"[user;session=cancel;range=0:20] 确认：设备D7已取消，不再是待审核状态。","evidence_type":"DIRECT","evidence_refs":["a1c50f1e-e2ff-5d01-b58e-19298f9d2678"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-10-03T00:00:00+00:00","effective_from":"2024-10-03T00:00:00+00:00","valid_until":null},{"id":"5f621b2e-2eed-5dad-b367-0f1cea76adb9","version":1,"content":"[user;session=old;range=0:14] 设备 D7 的状态是待审核。","evidence_type":"DIRECT","evidence_refs":["8ed922d1-40e9-5cfc-ab34-837975be08ed"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-10-01T00:00:00+00:00","effective_from":"2024-10-01T00:00:00+00:00","valid_until":null}],"retrieved":[],"root_index":[{"id":"46ad1cd8-b913-55cf-b1ad-9a38ab066056","name":"synthetic","scenarios":[]}]}}
```

### C

参考材料：433 UTF-8字节；请求SHA256 `2672930d9adc484066e35dca394ed47ed44b5d10658b1a878429a021a0b97176`。

模型原始回答：
```text
NOT_RUN：未获本轮模型调用授权或该请求未执行。
```

评分：`{"book": {"precision": "NOT_RUN", "recall": "NOT_RUN", "reasoning": "NOT_RUN", "proactivity": "NOT_RUN"}, "dimensions": {"fact_accuracy": "NOT_RUN", "entity_scenario": "NOT_RUN", "temporal_state": "NOT_RUN", "current_conflict": "NOT_RUN", "unsupported_memory": "NOT_RUN", "evidence_use": "NOT_RUN"}, "surface_recall": "NOT_RUN", "failure_categories": []}`

实际提供的参考材料：
```json
{"application_state":{"mode":"plan","current_date":"2024-10-05","as_of":"2024-10-05T00:00:00+00:00"},"untrusted_user_memory":{"reference_only":true,"current_request_takes_precedence":true,"evidence":[{"id":"5f621b2e-2eed-5dad-b367-0f1cea76adb9","role":"user","scene":"old","at":"2024-10-01T00:00:00+00:00","chars":[0,14],"text":"设备 D7 的状态是待审核。"},{"id":"e0b1d053-6b82-576c-8fba-bae920eb3034","role":"user","scene":"cancel","at":"2024-10-03T00:00:00+00:00","chars":[0,20],"text":"确认：设备D7已取消，不再是待审核状态。"}]}}
```

## v05_missing_memory

问题：我之前记录过的门禁密码是什么？如果没有记录请直接说明，不要猜。

任务时点：2024-10-01T00:00:00.000001+00:00

### A

参考材料：0 UTF-8字节；请求SHA256 `30fbba10d90e8773539d4db3d4b26cc744688817e48e3e4f2a3af60990efd6d1`。

模型原始回答：
```text
NOT_RUN：未获本轮模型调用授权或该请求未执行。
```

评分：`{"book": {"precision": "NOT_RUN", "recall": "NOT_RUN", "reasoning": "NOT_RUN", "proactivity": "NOT_RUN"}, "dimensions": {"fact_accuracy": "NOT_RUN", "entity_scenario": "NOT_RUN", "temporal_state": "NOT_RUN", "current_conflict": "NOT_RUN", "unsupported_memory": "NOT_RUN", "evidence_use": "NOT_RUN"}, "surface_recall": "NOT_RUN", "failure_categories": []}`

实际提供的参考材料：
```json
{"application_state":{"mode":"plan","current_date":"2024-10-01","as_of":"2024-10-01T00:00:00.000001+00:00"}}
```

### B

参考材料：671 UTF-8字节；请求SHA256 `ecae3466aeed3e088596e3703257dcf57078c76ff04e0865b5d6db1a9c1ba050`。

模型原始回答：
```text
NOT_RUN：未获本轮模型调用授权或该请求未执行。
```

评分：`{"book": {"precision": "NOT_RUN", "recall": "NOT_RUN", "reasoning": "NOT_RUN", "proactivity": "NOT_RUN"}, "dimensions": {"fact_accuracy": "NOT_RUN", "entity_scenario": "NOT_RUN", "temporal_state": "NOT_RUN", "current_conflict": "NOT_RUN", "unsupported_memory": "NOT_RUN", "evidence_use": "NOT_RUN"}, "surface_recall": "NOT_RUN", "failure_categories": []}`

实际提供的参考材料：
```json
{"application_state":{"mode":"plan","current_date":"2024-10-01","as_of":"2024-10-01T00:00:00.000001+00:00"},"untrusted_user_memory":{"reference_only":true,"current_request_takes_precedence":true,"precedence":["current_directive","confirmed_scenario_exception","historical_preference"],"stable_core":[],"active_core":[{"id":"225a95f7-1128-516c-9e44-2a4fe90a1190","version":1,"content":"[user;session=project;range=0:16] 项目代号是 ORCHID-17。","evidence_type":"DIRECT","evidence_refs":["b8173d13-6360-5cc0-a393-bf0fb6ddc2f2"],"scope":"user","scenario_ids":[],"lifecycle":"active","observed_at":"2024-10-01T00:00:00+00:00","effective_from":"2024-10-01T00:00:00+00:00","valid_until":null}],"retrieved":[],"root_index":[{"id":"bd479082-923c-5902-821e-84c6ecafdc74","name":"synthetic","scenarios":[]}]}}
```

### C

参考材料：0 UTF-8字节；请求SHA256 `30fbba10d90e8773539d4db3d4b26cc744688817e48e3e4f2a3af60990efd6d1`。

模型原始回答：
```text
NOT_RUN：未获本轮模型调用授权或该请求未执行。
```

评分：`{"book": {"precision": "NOT_RUN", "recall": "NOT_RUN", "reasoning": "NOT_RUN", "proactivity": "NOT_RUN"}, "dimensions": {"fact_accuracy": "NOT_RUN", "entity_scenario": "NOT_RUN", "temporal_state": "NOT_RUN", "current_conflict": "NOT_RUN", "unsupported_memory": "NOT_RUN", "evidence_use": "NOT_RUN"}, "surface_recall": "NOT_RUN", "failure_categories": []}`

实际提供的参考材料：
```json
{"application_state":{"mode":"plan","current_date":"2024-10-01","as_of":"2024-10-01T00:00:00.000001+00:00"}}
```

## 判断边界

没有真实回答或完整成对源审时，不能判断主瓶颈在模型。STORAGE/RETRIEVAL/RANKING/PACKING必须结合合法存储、FTS及Core两条路径；Core可绕过FTS直接注入，不可仅因FTS漏召就说模型没有证据。REASONING/CONFLICT需要已审正确证据确实可见而模型仍失败。争议Gold、等价证据不足、当前时点矛盾归EVALUATION。

原书四项precision/recall/reasoning/proactivity需依据原始对话审查，额外实体/时间/冲突/幻觉/引用独立记录。未审不算对、不算错。当前fixture模拟批准完整原文，不证明自动提炼/真实确认质量。

本轮不修改任何生产默认，不迁移数据库，不导入私人记录、不推远端。
