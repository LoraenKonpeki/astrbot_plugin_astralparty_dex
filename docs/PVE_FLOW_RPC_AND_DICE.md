# PVE 流程、RPC 与骰点可查询性研究

研究日期：2026-10-05（Asia/Shanghai）。用户要求为后续开发建立知识基线；本次只做研究与离线检查，没有增加骰点命令或改变线上插件。

## 证据层级与资料

分开使用三个层级：**网页玩法说明**、**客户端/描述符静态定义**、**真实回放中的实际数据**。有消息定义不代表该消息保存进回放；网页名称不代表已有 ID 映射；当前扫描没有发现结果不代表整个文件绝对不存在结果。

玩法来源（访问于研究日，社区资料可能落后于客户端）：

- [PVE 新手攻略 v2.4](https://www.taptap.cn/moment/806107072025329717)：行动顺序、移动前后、战斗卡与效果卡、成长途径。
- [PVE 基础篇](https://www.taptap.cn/moment/751920132208460186)：攻防/闪避、随机战斗卡、轮次资源。与其他攻略对遥控骰子受加减速影响的描述有冲突，不能据此写死算法。
- [筹码图鉴](https://wiki.biligame.com/starengine/筹码)：循环往复、彩羽手环及影响骰点的特殊筹码。
- [梦想号](https://wiki.biligame.com/starengine/星趴·梦想号)、[御魂庆典](https://wiki.biligame.com/starengine/御魂庆典)、[水乡古镇](https://wiki.biligame.com/starengine/水乡古镇)、[魔法学院](https://wiki.biligame.com/starengine/魔法学院)、[龙宫游乐园](https://wiki.biligame.com/starengine/龙宫游乐园)、[幽魂暗巷](https://wiki.biligame.com/starengine/幽魂暗巷)、[园林中庭](https://wiki.biligame.com/starengine/园林中庭)：地图事件、任务、敌方阶段与分支。
- [异变图书馆](https://wiki.biligame.com/starengine/异变图书馆)：线索/真凶等特殊地图机制，不能把所有 PVE 简化为击败一次固定 Boss。
- [合作挑战早期公告转载](https://wiki.biligame.com/starengine/用户:82428505/astralparty8月12)：资源成长、进度与胜负的早期设计，仅作背景，不用旧数值覆盖现版本。

技术来源：用户提供的 `astral-party-runtime-analysis/analysis/rpc-handoff` 中 dex/runtime 描述符、`rpc-index.md`，以及 `src/AstralParty.Runtime.cs` 的实际发送入口和接收回调；参考项目 [AstralParty_Dex](https://github.com/ZtyanCrany/AstralParty_Dex) 本地研究副本为 `35b8e69`。本插件实际使用 dex 描述符，不能混用旧 runtime 全套定义。

## 一局的流程与协议映射

下面的编号按描述符与交接路由核对，**不是用请求编号 +1 推算**。请求的参数表示操作意图，S2C 才是执行结果；推送可 UPSN=0。没有独立 RPC 的环节使用房间状态、动作通知和属性变化组合辨认。

| 环节 | 游戏侧内容 | 相关请求 / 结果或推送 | 复盘所需信息与边界 |
| --- | --- | --- | --- |
| 建房、组队、选人、准备 | 地图、难度、角色、皮肤、顺位和局外养成进入本局 | CreateRoom 5005/5006；JoinRoom 5009/5010；RoomReady 5131/5132；StartGame 5019/5020；RunningGameS2C 1003 | `Room.map_id/map_data_id/mapDifficultyId/difficulty/mapType`、players、monsters、lands、start_time。局外 PVE 等级与局内星级不同；地图/难度/分支都不能只按地图名称判断。 |
| 轮次与个人行动开始 | 通常四名玩家按顺位行动，再轮到怪物；回合开始效果、冷却、出牌次数、资源发放 | RoundStartS2C 1015；ActionStartNotifyS2C 1026；PredictActionS2C 1002；GameRoundChangeS2C 1117 | `Room.round/player_idx`、`Hero.round`、行动角色 ID、死亡/住院/停回合状态。预测动作不是执行结果。跳回合、死亡和额外行动会破坏“每人每轮一个骰”的假设。 |
| 移动前行动 | 使用效果牌、治疗、直伤、转移位置，使用角色技能 | UseEffectCard 5055/5056；UseQuickCard 5073/5074；UpdateHeroAttrS2C 1040 | 5055 同时具有 `use_skill/skill_id`，不能只当作普通出牌。用 target_ids/target_node_ids 和属性原因区分对象、伤害、恢复、移动。直伤不应伪造战斗骰。 |
| 掷移动骰、选择移动 | 普通移动、双骰、遥控或其他移动修正 | ThrowDice 5021/5022；ThrowDiceResult 5067/5068；MovePointBuffS2C 1019 | 5022 的 `vals[]` 是骰面列表，`move_point` 是移动点数，另有 `isControlMovePoint/force_dir`。5067 是选择点数的交互，不能当作随机骰结果。保留骰面、选择、最终点数三者。 |
| 路径与岔路 | 逐段移动、方向选择、途中停留、继续移动或再移动 | Move 5027/5028；ChoiceDirection 5061/5062；StopOrContinue 5077/5078；MoveAgain 5043/5044；Pursuit 5033/5034；MonsterPursuit 5213/5214 | MoveS2C.node_ids/end 是路径结果；MoveC2S.direction 是选择下一节点。请求次数不是步数。传送、停留、追击、额外行动与加减速都要分开。 |
| 遭遇与是否开战 | 经过可交战怪物时挑战或放过；技能也可能发起战斗 | AskBattle 5047/5048；BattleS2C 1007 | 5047 含 `is_battle/IsPursuit/fightBack/skill_player_id`。用 `Battle.battle_id` 关联整场战斗，不能按相邻两个骰包盲配。机械蛇龙等单位有经过不触发战斗的特例。 |
| 战斗出牌 | 双方准备攻击牌/防御牌、消耗本次战斗点数 | BattleUseCard 5035/5036；BattleS2C 1007 | 卡牌随机加成与六面战斗骰是不同随机量。`BattleRole.use_cards/cardCombatBonus/atk/def/cost/max_cost` 分开记录。 |
| 攻击骰 | 玩家或怪物掷攻击骰 | BattleThrowDice 5037/5038 | **5038.player_id/val** 才是直接骰点结果。客户端 FightLogic 回调以 model.Val 显示攻击骰。C2S.dev_point 来自 GMConfig，正常请求不提供最终骰点。 |
| 防御或闪避骰 | 受击方选择防御/闪避、结算骰点，可能发生反击 | BattleChoice 5039/5040；BattleS2C 1007 | **5040.player_id/val/dodge/existFightBack**。防御骰和闪避骰要区分；反击是新的攻击过程，不能把第一次攻防交换简单复制为反击。BattleRole.point/dodge 是快照中的状态，缺省 0 不等于掷出 0。 |
| 战斗结算与衍生效果 | 扣血、护盾、免伤/易伤、死亡、击杀奖励、反击、连锁伤害 | BattleS2C 1007；UpdateHeroAttrS2C 1040；ReplayDieS2C（当前 dex 有定义，未确认路由） | `Battle.is_end/fightBack/is_pursuit`、双方角色 ID、`inc_hp/chain_attacker/chain_attack_damage`；`HeroAttrEffect` 配合 `CauseOrigin`。不能从 HP 差值唯一倒推出双方骰点。 |
| 落地效果 | 商店、恢复、怪物格、突击门、事件、地图机制等 | LandChoiceTarget 5063/5064；PVEShopBuy 5215/5216；BuyRelic 5249/5250；TriggerEvent 5053/5054；EventThrowDice 5051/5052；SelectEvent 5317/5318；SelectMechanism 5259/5260 | 事件骰、炸弹骰、小游戏骰独立分类，不混入移动/战斗骰。落地并非每次都有全部环节；地图差异由 lands、事件 ID、目标和属性变化解释。 |
| 升星与筹码成长 | 升星、地图任务、商店、角色被动、延时或累计触发再次获取 | SelectRelic 5211/5212；SyncRelics；HeroUpLvS2C；MapMissionNotifyS2C 1072；UpdateHeroAttrS2C 1040 | 一次获取单独保存候选刷新链、终选、来源证据。同帧连续获取不能复制同一链；拥有筹码不等于证明触发。局外 PveHeroUpLv 5219 是养成接口，不是本局升星。 |
| 怪物行动 | 依次处理技能、移动、追击、攻击、同格多目标，Boss 可能休眠或进入新状态 | ThrowDiceS2C 5022、MoveS2C 5028、BattleThrowDiceS2C 5038、BattleChoiceS2C 5040；MonsterRefreshS2C 1018；BossSleepS2C 1043；RoomNotifyS2C 1024 | 玩家和怪物共用 player_id/Player 容器。用 Room.players / Room.monsters、Hero.monsterType/monster_index 分辨实体，不能按 ID 位数判断。怪物同种多只、重生、召唤、友方 NPC 必须独立实例记录。 |
| 回合收尾、地图进度与新阶段 | 状态持续时间衰减、延时效果、任务达成、波次、Boss 激活、转场与投票分支 | GameProgressChangeS2C 1071；MapEventS2C 1055；MapMissionNotifyS2C 1072；Vote 5309/5310；VoteSelect 5311/5312；RoomNotifyS2C 1024 | Room.gameProgress/gameMaxProgress、pveBossActive、mapMissions、mapIndex/mapStatus、waitExecEventIds、storyState。进度不等于轮数；任务奖励与选择操作可跨快照。 |
| 胜负与结算 | 达成本图胜利条件、失败、放弃、异常结束；保存回放与战绩 | GameFinishS2C 1016；GetShowPlayer 5153/5154；GetPlayerFightRecord 5155/5156 | replayId/version/finish_time/rank、newAchieve；“所有战斗骰为6的次数”是汇总计数，不是完整骰序列。保存游戏版本以避免不同版本字段/机制混淆。 |

静态客户端关键位置：`src/AstralParty.Runtime.cs:353025` 移动请求；`:353042` 移动结果显示（vals 与 move_point 同时传给 DiceManager）；`:357486` 攻击骰请求；`:357501` 攻击骰结果；`:357511` 防御选择；`:357527` 防御骰结果；`:368780` 指定移动点数提交；`:227153` 遥控选点界面。行号对应本地反编译版本，更新后应重新定位。

## 地图分支对开发的影响

不要固定写成“小怪 → 精锐 → 同一个 Boss → 结算”的单一状态机。

- 梦想号：按进度刷怪、Boss 登场/激活、不同难度的强化节点。保存实际 eventId 和 progress，而不是写死第几轮登场。[地图资料](https://wiki.biligame.com/starengine/星趴·梦想号)
- 水乡古镇：指挥平台与舞狮嘎呜、机械蛇龙的支援技能；存在经过不能触发战斗的怪物。追击路径、技能伤害和战斗伤害需要分别识别。[地图](https://wiki.biligame.com/starengine/水乡古镇)、[机械蛇龙](https://wiki.biligame.com/starengine/机械蛇龙)
- 魔法学院：存在不同怪物任务组合与甜点空间等分支，不能仅凭主地图名固定任务或 Boss 状态。[地图资料](https://wiki.biligame.com/starengine/魔法学院)
- 龙宫：先取得精英道具，再通过投票选择支持一方，敌对 Boss 随之确定。投票结果及 mapStatus/campId 应进入复盘上下文。[地图资料](https://wiki.biligame.com/starengine/龙宫游乐园)
- 幽魂暗巷：多组任务、人工生命体阶段与特殊糖果机制，任务可以发筹码并影响进度。[地图资料](https://wiki.biligame.com/starengine/幽魂暗巷)
- 园林中庭：孔雀/鸳鸯试炼与内心阶段，彩羽手环是主动技能与特定卡牌使用累计关联的筹码获取机制。[地图](https://wiki.biligame.com/starengine/园林中庭)、[筹码](https://wiki.biligame.com/starengine/筹码)
- 异变图书馆：线索与真凶揭露影响 Boss 可否继续复活。一个 monster_id 的多次出现不一定是不同种怪，也不能因首次击倒就宣布全局结束。[地图资料](https://wiki.biligame.com/starengine/异变图书馆)

这是解析器应支持的差异清单，不是以上各地图所有难度、词条、技能都已实战验证的声明。部分 Wiki 二次展开失败，未核对的细节不填入规则引擎。

## 筹码与骰点的关联

- 循环往复：图鉴描述为增加重选次数，并延时再次获得筹码；不是“升星后立刻再刷一次”。需要追踪个人回合、死亡/跳回合和实际触发证据。[图鉴](https://wiki.biligame.com/starengine/筹码)
- 彩羽手环：图鉴描述与主动技能、彩羽使用累计相关。社区攻略写“飞羽手环”，当前本地表为 50067 彩羽手环，**名称别名和版本对应仍未得到直接配置证据**，不能自动宣称两者相同。[图鉴](https://wiki.biligame.com/starengine/筹码)
- 迷幻海鲜汤、交错双鲤等可限制战斗骰面；双骰、移动速度、遥控等可改变移动表现。因此未来统计要保留当时状态，不能将所有结果视为同一种普通骰。[图鉴](https://wiki.biligame.com/starengine/筹码)
- 真实样本已证明：米米同一轮同一快照附近连续获取可以是两次独立选择。v0.3.3 按终选分链，额外来源只作为证据提示；该修复不能据此扩展成“所有来源都已准确识别”。

## 本次真实回放检查

使用用户给出的已有 PVE 回放离线检查；不提交原始回放、玩家资料、截图、房间密码或认证数据。

| 检查项 | 观察到的结果 |
| --- | --- |
| 当前 Room 扫描 | 54 个快照、4 名玩家；monsters 有实际实体 |
| 当前扫描的 5021 / 5037 / 5039 | 分别 83 / 47 / 42 条；这些数量有重复，不能直接称为实际掷骰次数 |
| 按 cmd、uid、sn、payload 去重 | 分别 43 / 42 / 42 条；该去重仅为诊断，也不能代替完整容器和执行语义验证 |
| 上述去重记录的 payload | 全部为空；外层有动作编号、角色 ID、序号，没有骰点结果 |
| 结果编号检查 | 当前包扫描没有 5022、5038、5040、1007；文件中也没找到 `0x0D + little-endian CMDID` 的这些标记。这只是对现有编码假设的检查，不是所有容器已完整解析的证明 |
| Room.battle | 仅末尾一个快照具有非零 battle_id；攻击方 point=6，防御方 point=0，无法补全其他战斗，也不能把 0 当作有效骰点 |
| 5067 | 去重后 4 条，payload 只有 max_point=6，没有 point；与动作提示/可选上限相容，不能当作选出了6 |
| ConditionalInfo | 最终有 movePoint 与 battleDiceSixCount；它们是累计统计，不是每次移动骰/战斗骰明细 |

**本次没有确认整个回放的顶层容器、是否有随机种子/随机序列、未解析块是否存储结果、或游戏官方回放重演接口。** 当前回放解析器仍依靠局部扫描，不能把“扫描不到”写成“服务器从不保存”。也不能通过 SN 推导骰点：SN 是动作关联信息，不是已证明的随机种子。

## 查询可行性结论

### 一局所有怪物和玩家的骰点

有技术机会：消息定义及客户端显示逻辑都存在，player_id 适用于玩家与怪物。准确采集 5022、5038、5040，并结合 BattleS2C 与房间实体状态，可以形成移动骰、攻击骰、防御/闪避骰事件表。

但**目前插件的回放数据路径不能保证返回所有结果**。请求包为空、稀疏快照和汇总统计不足以还原完整骰序列。不能通过伤害、走路累计值或调试字段造出缺失结果。

后续验证路线依次为：

1. 对回放顶层容器做完整解码，区分初始状态、快照、提示/预测动作、执行操作、重复记录、随机状态和结果；检查当前局部扫描是否漏掉结果块。
2. 如采用操作重演，确认当前游戏版本的重演入口与随机状态、全部随机消耗顺序。仅有种子也不足以复现：抽牌、卡牌随机值、事件、AI 与技能都可能消耗随机数。
3. 对比游戏内逐次显示和真实结果事件，验证玩家主动攻、防御、闪避、怪物攻防、反击、多目标、双骰、遥控、额外移动、重连与断线托管。每种都要报告覆盖率和缺口。
4. 若离线回放无法提供结果，研究在游戏客户端侧或受支持的观战会话采集 **S2C**。这不是普通插件登录自动获得全局广播；观战接口、权限、游戏账号共存和掉线补全均尚未联调验证。当前不主动加入/操作游戏房间。

### 一段时间内某玩家的所有骰点

可实现“对已收集且有完整骰点来源的多局，按 UID 和日期汇总”。但不能承诺“任意玩家、任意历史时间段、全部对局、全部骰点”。存在两道独立前提：

- **对局覆盖**：目前代码从 showPlayer.record 取近期战绩，参考实现通常最近10局。PlayerFightRecord 有 time/replayId/version；GetPlayerFightRecord 的 index 是已列出战绩条目的索引，未发现 start/end 日期或可靠历史分页。可用性/隐私/回放保存期还可能限制查询。
- **单局覆盖**：即使拿齐回放号，也必须先解决单局骰点结果缺失。只持续存回放号不会自动变出完整骰点。

未来可以持续归档可获得的战绩和解析结果、导入用户已有回放号，明确标注统计涉及几局、时间范围、已知缺局和骰点缺失。统计时间以战绩 time / 已确认的结束时间定义；动作 SN 和 use_time 不能未经验证视作墙钟时间。历史窗口无法被完整列举时，应称“已收集对局统计”。

## 后续事件模型与验收基线

建议先设计离线事件表，再设计 Bot 指令；不要先画一个声称全量的骰点图。

每个事件保存：replay_id、game_version、match_time、Room 轮次/个人回合、实体 instance_id/hero_id/monsterType、battle_id、角色（move/attack/defend/dodge/event）、骰面数组、最终移动点数、控制标志、真实路径、关联操作、来源（S2C/快照/推导）、完整性及缺失原因。没有数据用 null，不用 0。一次重复推送不重复计数，双骰保留两枚骰而不是只存和。

已确认的实际观察与推导必须分开：vals 与 move_point 分开；随机骰与遥控选择分开；骰面与随机卡牌加成分开；普通攻击与反击分开；移动点数与实际路径分开；同种怪物的不同实例分开。为快照去重应以 battle_id、实体与阶段判定，不能全局去重同一数值（连续两次掷6完全可能）。

正式功能的最低验收：至少一个完整 PVE 样本逐次对照游戏显示，包含玩家和怪物移动及攻防；特殊机制另有样本；对局覆盖和事件覆盖均可计数；未证实的区段明确缺失。完成这些之前，保持“研究可行，当前未实现全量查询”的产品表述。
