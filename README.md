# 安全资质覆盖审计工具

核对岗位能否由现有持证人合规填补，并支持从逐段覆盖扩展到带交接见证的连续排班。
纯 Python 标准库实现，Windows 原生 Python 3.12 + CPU 即可运行，无需第三方依赖。

## 1. 目录结构

```
solution/                 工具包（仅标准库）
  __main__.py             CLI 入口：python -m solution ...
  errors.py               错误码与校验异常
  model.py                领域数据模型
  validate.py             CASE.json 结构校验（字段路径 + 错误码）
  timeline.py             证据视图、更正解析、事件边界切段
  matching.py             Kuhn 二分匹配（可行性剪枝）
  solver_cover.py         逐段覆盖求解器（约束搜索 + 分支限界）
  solver_schedule.py      连续排班求解器（全局逐席回溯）
  naive.py                独立小规模朴素穷举对照（不参与正式求解）
  compare.py              新旧证据视图差异 / 已发布计划重放
  db.py                   SQLite 持久化（仅 --db 时使用）
fixtures/                 本地虚构夹具与确定性生成器
tests/                    unittest 测试（含随机小夹具朴素对照）
```

## 2. 快速开始

```bash
# 逐段覆盖
python -m solution cover fixtures/cover_demo.json

# 连续排班
python -m solution schedule fixtures/schedule_handover_ok.json

# 新旧证据视图差异（无 --db 时走 cover 口径）
python -m solution compare fixtures/cover_correction_middle.json

# 发布排班到 SQLite，再用 compare 重放旧计划
python -m solution schedule fixtures/schedule_replay_compare.json \
    --cutoff 50 --db state.sqlite
python -m solution compare fixtures/schedule_replay_compare.json \
    --db state.sqlite

# 运行全部断言测试
python -m unittest discover -s tests -v

# 重新生成全部虚构夹具（字节级可复现）
python fixtures/generate_cover.py
python fixtures/generate_schedule.py
python fixtures/generate_scale.py
```

任一命令标准输出都是 UTF-8 JSON，逻辑输入相同则输出字节级一致
（键排序、列表排序、整数时刻均为确定性处理）。
成功退出码 `0`，输入/运行期错误退出码 `2`。

## 3. 领域规则

### 3.1 类别层级与覆盖

`categories` 声明有根森林（`parent: null` 为顶级）。持有上位类别可覆盖其全部下位：
判断方式为要求类别 ∈ 持有类别闭包（自身 + 全部祖先）。层级成环会被校验拒绝。

### 3.2 资质事件与更正

每个资质版本由 `qual_id + version` 标识，半开有效区间 `[grant, terminate)`。

- 基础版本 `replaces: null`；更正版本 `replaces: <被替换版本号>`，
  并声明自己的更正生效半开区间 `[effective_from, effective_to)`；
- 更正只在生效区间内替换基础版本，区间外回落到基础版本；
- 同一被替换版本的多个更正区间互叠将被拒绝（`OVERLAPPING_CORRECTION`）；
- 证据录入切点 `cutoff`：仅 `entered_at <= cutoff` 的事件可见。
  补录事件因此只在更大的切点下出现。

### 3.3 同刻需求与席位

服务组合 `service_combos` 声明 `[from_time, to_time)` 内同时开席的岗位集合。
每个岗位固定两个席位，分属两个不同部门（双签）：

- 一个人同刻最多担任一个席位；
- 一个席位可由同一人同一部门的多份资质组合填补，类别并集必须覆盖岗位全部要求；
  求解器自动选择资质数最少、同数时 ID 字典序最小的集合；
- 双签两人必须不同；
- 员工排除（冷却）：`appointments`（独立已签任历史，排程不生成）配合
  `cooling_rules` 生成 `(人, 受限岗位)` 的禁用半开区间
  `[签任结束, 签任结束 + cooldown)`。半开区间使冷却结束时刻恰可上岗。

### 3.4 切段

取全部可见事件边界（授予、终止、更正生效区间、服务组合、冷却窗口）在查询窗口
`[query.from_time, query.to_time)` 内切段；段内资质状态、需求、排除关系恒定。
未显式给查询窗口时取全部事件边界的最小/最大值。

## 4. cover 求解语义

对每段独立求完整指派；不存在完整指派时求已填席位数最多的合法部分指派并标出空席。

目标序（每段）：

1. 完整指派优先，否则最大化已填席位数；
2. 最小化该段使用的资质数；
3. 按 `(岗位, 部门, 人, 资质 ID)` 字典序决胜。

求解以席位为变量做约束回溯：人员唯一、同部门多资质并集覆盖、双签同人排除、
冷却排除都在变量域内表达；Kuhn 最大匹配做前向可行性（Hall 条件）剪枝，
下界剪枝 + 字典序前缀剪枝做分支限界。支持 50 人 / 15 岗位（30 席）规模。

## 5. schedule 连续排班语义

在逐段可行之上增加跨段约束，规模上限 20 人 / 8 岗位 / 8 段：

- **最短在岗跨度 `min_span`**：席位上同一人连续在岗段数不得低于该值；
  岗位无需求造成的天然断开、时间线末尾收尾段豁免；
- **交接见证**：席位换人时，交界时刻必须有第三人作为见证，且
  - 见证资质在旧段与新段都有效（可分别是两份资质，均覆盖岗位绑定的见证类别）；
  - 见证部门独立于该岗位两个席位部门；
  - 见证人同刻不能兼任其他交接见证，也不能占任何在岗席位；
- **续期**：持续在岗者某份资质失效时，可用本人同部门另一份有效资质继续
  （自动换资质不换人）；若所需类别无法同刻补齐，则该排班失败；
- 一个人可跨不重叠的岗位区间工作（不同段不同岗），同段仍最多一个席位。

全局目标序：

1. 一次完整排班（所有段所有席位填满）；
2. 换人次数最少；
3. 各段使用资质数之和最少；
4. 按 `(段, 岗, 席位, 部门, 人, 资质 ID, 见证 ID)` 字典序决胜。

无完整连续排班时输出 `feasible: false` 与原因码，不回填部分计划。

## 6. compare 语义

- **cover 口径**（无已发布 schedule 时）：按新旧视图边界并集对齐窗口，
  逐段比较两视图最优指派指纹，输出 `first_affected_segment` 与全部差异段。
- **schedule 口径**（`--db` 中存在该 case 的已发布 schedule 时）：
  重放旧证据视图下发布的最优计划，**不重新优化、不回填历史**，
  在新视图上逐段核对席位资质/类别/部门/冷却与交接见证，
  给出 `first_failed_segment`（最早实质失效段）与逐行 `replay`，
  并附新视图自身的最优排班供参考（`old_plan_kept: true`）。

## 7. 输入 JSON 定义

根对象字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | 非空字符串 | 案例标识，持久化以此关联 |
| `categories` | 数组 | `{id, parent}`，顶级类别 `parent` 显式给 `null` |
| `qual_events` | 数组 | 资质版本事件，见下 |
| `appointments` | 数组 | 已签任历史 `{appointment_id, person, position, from_time, to_time}` |
| `cooling_rules` | 数组 | `{rule_id, controlled_position, barred_position, cooldown}` |
| `positions` | 数组 | 岗位定义，见下 |
| `service_combos` | 数组 | `{combo_id, positions[], from_time, to_time}` |
| `query` | 对象 | 查询参数，见下 |

`qual_events[]`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `qual_id` / `version` | 字符串 / 正整数 | 资质版本唯一键 |
| `person` / `department` | 非空字符串 | 持证人、授权部门 |
| `categories` | 非空字符串数组 | 该版本持有类别 |
| `grant` / `terminate` | 整数，`grant < terminate` | 有效半开区间 |
| `replaces` | 正整数或 `null` | 非 null 时为更正事件 |
| `effective_from` / `effective_to` | 整数 | 更正必填，`from < to` |
| `entered_at` | 非负整数 | 证据录入时刻（切点过滤用） |

`positions[]`：

| 字段 | 说明 |
|---|---|
| `position_id` | 岗位 ID |
| `required_categories` | 非空类别数组，并集必须被覆盖 |
| `seats` | 恰好两个 `{seat_id, department}`，两部门不同 |
| `min_span` | 可选，正整数，默认 1（仅 schedule 使用） |
| `handover` | `null` 或 `{department, categories[]}`，见证部门须独立于两席位部门 |

`query`：`cutoff`（cover/schedule）、`old_cutoff` + `new_cutoff`（compare）、
可选 `from_time` + `to_time`（查询窗口，要求 `from < to`）。
CLI 的 `--cutoff / --old-cutoff / --new-cutoff` 可覆盖文件内取值；
compare 可用 `--mode cover|schedule` 显式指定口径，缺省时按 `--db` 中
是否存在该 case 的已发布排班自动判定（显式 `schedule` 但无已发布计划时
返回 `NO_PUBLISHED_PLAN`）。

所有时刻均为整数；区间一律半开 `[from, to)`。

### 7.1 无需求段与 min_span 豁免

若某段内没有任何服务组合开席，该段仍出现在 schedule 输出中且 `assignments` 为空。
席位运行在无需求段处天然断开：重新开席时重新起算 `min_span`，不产生换人计数、
不要求交接见证；时间线末尾的收尾运行同样豁免 `min_span`。

## 8. 输出 JSON 定义

统一信封：`{"ok": true, "result": ...}` 或
`{"ok": false, "errors": [{"code", "field", "message"}]}`。

### 8.1 cover 结果

```json
{
  "cutoff": 100,
  "search_nodes": 119,
  "segments": [
    {
      "segment_index": 0,
      "start": 0, "end": 40,
      "active_positions": ["GUARD", "RADIO", "VAULT"],
      "complete": true,
      "filled_seats": 6, "total_seats": 6,
      "empty_seats": [],
      "assignments": [
        {"position_id": "GUARD", "seat_id": "G1", "department": "OPS",
         "person": "alice", "qual_ids": ["Q-ALPHA"]}
      ]
    }
  ]
}
```

部分指派时 `complete: false`，空席出现在 `empty_seats`，对应 assignment 的
`person` 为 `null`、`qual_ids` 为 `[]`。

### 8.2 schedule 结果

```json
{
  "feasible": true,
  "changes": 1,
  "qual_total": 4,
  "cutoff": 90,
  "search_nodes": 12,
  "segments": [{"segment_index": 0, "start": 0, "end": 30,
                "active_positions": ["GUARD"], "assignments": [ ... ]}],
  "handovers": [
    {"boundary_time": 30, "position_id": "GUARD", "seat_id": "S1",
     "old_person": "alice", "new_person": "bob",
     "witness": "wally", "witness_qual_ids": ["W1"]}
  ]
}
```

失败时 `feasible: false`，`changes/qual_total` 为 `null`，并给
`failure.reason = "no_legal_continuous_schedule"`。
带 `--db` 且成功时额外返回 `published_run_id`。

### 8.3 compare 结果

cover 口径含 `mode: "cover"`、`first_affected_segment`（无差异时为 `null`）、
`affected_segment_count`、`segments_changed[]` 与新视图完整 `new_cover`。

schedule 口径含 `mode: "schedule"`、`published_run_id`、
`first_failed_segment: {start,end,reason}`、逐段 `replay[]`
（含 `valid`、`reason`、`all_reasons`、`split_by_new_evidence`）、
`old_plan_kept: true` 与 `new_optimal_schedule`。

## 9. 错误码

| code | 触发场景 |
|---|---|
| `TYPE_ERROR` | 字段类型不符（含布尔冒充实整数） |
| `MISSING_FIELD` | 必填字段缺失 |
| `INVALID_VALUE` | 取值非法（区间反向、空数组、同部门双签等） |
| `DUPLICATE_ID` | 类别/资质版本/岗位/席位等 ID 重复 |
| `UNKNOWN_REFERENCE` | 引用不存在的类别、岗位或被替换版本 |
| `CYCLE_DETECTED` | 类别层级成环 |
| `OVERLAPPING_CORRECTION` | 同被替换版本的更正区间互叠 |
| `QUERY_MISMATCH` | 命令缺少必需切点参数 |
| `NO_PUBLISHED_PLAN` | compare 需要已发布计划但库中不存在 |
| `SEARCH_LIMIT` | 覆盖搜索节点超过安全上限 |
| `SCALE_LIMIT` | 超出 50/15（cover）或 20/8/8（schedule）规模上限 |

每条错误都带 JSONPath 风格字段位置，例如
`$.qual_events[0].grant`、`$.positions[2].seats[1].department`。

## 10. 夹具与测试对照

| 夹具 | 覆盖场景 |
|---|---|
| `cover_hierarchy` | 上位类别链覆盖方向（TOP→SEC→CLS→PUB） |
| `cover_contention` | 跨岗位同刻争用同一人，失效后出现空席 |
| `cover_dual_sign_fail` | 第二席无独立部门持证人，双签失败 |
| `cover_cooldown` | 冷却期结束恰可上岗（半开区间边界） |
| `cover_correction_middle` | 补录更正只改变中间一段 + compare |
| `cover_partial` | 无完整指派时的最大部分指派与空席标注 |
| `cover_demo` | 综合演示：并集拼接、多岗位、更正与冷却 |
| `schedule_no_handover` | 各段独立有解但无合法连续交接 |
| `schedule_witness_contention` | 交接见证人被两岗同刻争用 |
| `schedule_partial_recall` | 多类别拼接资质被部分回收后无法补齐 |
| `schedule_shared_employee` | 共享员工使全局最优（0 换人）不同于逐段最优 |
| `schedule_handover_ok` | 合法换人 + 第三人见证正向路径 |
| `schedule_min_span` | 最短在岗跨度违例 |
| `schedule_replay_compare` | 旧计划在新证据视图的最早实质失效段 |
| `scale_50_15` | 50 人 / 15 岗位 / 30 席规模压力 |

`tests/` 另含 20+30 个固定种子随机小夹具，用独立实现的朴素穷举
（`solution/naive.py`，笛卡尔积 + 全约束过滤 + 同目标序比较）
与正式约束搜索逐段/逐计划对照，保证两套独立实现结论一致。
