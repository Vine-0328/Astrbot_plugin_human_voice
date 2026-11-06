# 角色灵魂拟人 · Character Soul Persona

**[中文](#中文)** | **[English](#english)**

AstrBot 插件 · `astrbot_plugin_human_voice` · AstrBot v4.29.4 – <5.0 · OneBot v11 / QQ 官方机器人 (QQ Official Bot)

---

## 中文

这个 AstrBot 插件让 QQ 聊天角色按自己的性格表达意见和情绪，根据交流积累记忆、人物关系和好感度。适用于 AstrBot v4.29.4 至 5.0 之前的版本，支持 OneBot v11、QQ 官方机器人 WebSocket 和 Webhook 适配器。使用前请在 AstrBot 中接入 QQ 和聊天模型。

### 安装

1. 打开 AstrBot WebUI 的「插件」，点「安装插件」→「从文件安装」，上传 `astrbot_plugin_human_voice.zip`。
2. 确认「角色灵魂拟人」已启用，并且 QQ 会话使用的配置文件允许使用这个插件。
3. 点插件卡片的齿轮，填写角色设定并保存。聊天模型留空时沿用该会话的默认模型。
4. 在 QQ 私聊角色发一句话，或在群里 @ 机器人。在插件详情打开「运行状态」，可查看并编辑记录。

手动部署时，将 `astrbot_plugin_human_voice` 文件夹放入 AstrBot 的 `data/plugins/`，然后重载插件或启动 AstrBot。

### 升级与版本说明

从旧版升级时，直接上传新版 ZIP 覆盖。显示名改为「角色灵魂拟人」，原插件目录和数据库沿用，旧会话会在升级时补上情绪字段。

**v1.4.1** 修复旧版读取接口在重载后仍然响应、导致保存后的长期记忆、态度和创建者状态显示为空或默认值的问题。更新文件后重载插件，再刷新运行状态页；仍提示接口版本不匹配时重启 AstrBot。已有数据库继续沿用，无需重置。面板手动保存的数值也会优先于此前尚未应用的人物档案，自动生成的空摘要会保留已有长期记忆。

**v1.4.2** 默认使用「协同 AstrBot 与其他插件」回复方式，旧配置未设置此项时也自动采用协同方式。角色状态通过 LLM 请求钩子加入，不再关闭 AstrBot 默认 Agent；搜索、工具、其他插件的请求和发送钩子继续按原有配置运行。更新后重载插件，并确认「回复方式」为协同模式。

动态角色状态以当前轮的临时补充内容加入，优先级低于系统人格，不改写原有系统提示词，也不会把整份状态档案写进 AstrBot 的聊天历史。

从 v1.3.2 升级时，请同步整个插件目录，包含新增的 `integration.py`、`bootstrap.py` 等模块，不能仅替换 `main.py`。AstrBot 的 `data/config/` 和 `data/plugin_data/` 继续沿用，无需清空。

**v1.4.3** 将协同模式的档案整理移到后台，支持结构化 JSON 请求、兼容不支持该参数的模型，并对无效格式重试一次。缺少可选字段、输出带说明或个别字段过长时，会保留其他有效字段。新的近期事实在模型未提供摘要时也会补入长期记忆；没有新事实时不重复添加。好感度和情绪按本轮分类正常计分，创建者好感度仍固定 100。

运行状态每 5 秒同步已完成的后台更新，显示整理进度、失败提示及本轮好感度／情绪涨跌。有未保存草稿时暂停自动同步。档案整理默认最多等待 60 秒，可在插件配置调整至 15～180 秒；整理失败会保留已有记忆并在面板提示，不影响正常回复和其他插件。

### 在人格设置里选择

插件启用后，在「配置文件 → AI → 人格」的默认人格下拉中选择「角色灵魂拟人」，然后保存。也可在已有自定义人格的「技能」里勾选「角色灵魂拟人」分组下的 `role-soul-conversation`。

「生效范围」默认处理已启用插件的 QQ 会话。若想只在指定人格的 QQ 会话生效，可选「仅选中人格或会话技能时」。该模式按当前会话实际使用的人格判断。使用已有的人格时，需明确选择本插件的技能。AstrBot 的「全部技能」是隐式授权，严格模式下按未单独选中处理。

两种生效范围都会按会话选取一个角色设定来源。优先使用当前选中的 AstrBot 人格系统提示词，其次使用插件面板的「人格核心」，最后使用插件齿轮中的默认角色设定。选中的人格有自定义提示词时，角色的称呼、性格、对创建者的态度和表达方式均以这份提示词为准。未编辑的 AstrBot 默认助手提示和插件自带示例人格视为未设置自定义人格。

### 配置角色和人物

| 配置项 | 用法 |
| --- | --- |
| 名字、性别、年龄、性格、外貌 | 决定角色如何称呼自己、说话、表达情绪和回应分歧。 |
| 灵魂 | 描述角色的愿望、价值观和相处原则。 |
| 情绪变化方式 | 根据性格自动判断，也可指定活泼开放、冷淡克制或平和。已有会话的情绪分数可在状态页编辑。 |
| 当前记忆 | 填写角色自身的经历和背景。这是跨会话共用的设定。 |
| 心理活动设定 | 写入角色目前的心情、顾虑或念头。 |
| 创建者用户 ID | 用于迁移旧设置，每个人物仅取第一个适用 ID。日常指定或更换创建者请在运行状态面板操作。 |
| 用户好感度、人物关系与记忆 | 按用户建立档案，可调好感度、填写双方的关系和记忆、补写最新心理活动。好感度填 `-1` 沿用当前值。一个用户同时匹配普通 ID 和 `平台实例ID:用户ID` 时，使用后者。 |

「运行状态」可直接修改已有记录的好感度、情绪、人物关系、记忆和心理活动。好感度及人物关系按用户保存，情绪、记忆和心理活动按会话保存。改完点「保存修改」，下一轮对话会使用保存值。创建者好感度固定 100。只想为尚未聊过的人预设资料时，可在插件齿轮中添加人物档案。人物档案的记忆和心理活动留空时沿用已有内容，填 `#清空` 可清空该项此前的内容。

「对方身份」可多选恋人、朋友、同学、死党和闺蜜，自定义身份写在人物关系里。「创建者」可以直接勾选，每个 AstrBot 人格仅绑定一位聊天用户。选中另一位用户并保存后，原绑定自动解除，普通好感度继续使用此前积累的数值。取消勾选会解除该人物的创建者绑定。

首次打开尚未初始化的会话时，面板会读取当前人物提示词、插件保存的记忆，以及可安全归属到当前用户的 AstrBot 历史对话，整理后只填入空白项。已有手工内容会保留，证据不足时标明待确认。完成后会记录初始化状态，手工清空或重置后继续以空值为准。

首次初始化期间，页面顶部会显示醒目的加载提示，并暂时锁定编辑；完成或失败后自动解锁。新用户在聊天后就会出现在面板，无需预先到插件配置创建人物档案。

- **长期记忆**按会话累计整理关键偏好、称呼、约定和已确认的事件。升级后从数据库还保留的事实记忆和近期对话开始整理，之后随有效对话持续更新。可手动修改或补录较早的经历。
- **对该用户的态度**是这个会话的长期相处基线，例如称呼和对对方的熟悉程度。保存后持续用于回复，也可留空，由人格与关系设定决定。
- **人格核心**位于面板底部，显示当前会话实际使用的人格提示词，并标明来源。保存原生人格时会更新对应的 AstrBot 人格；没有原生人格时编辑插件后备核心。

| 情绪风格 | 起点 | 日常、善意纠正、求助 | 被夸 | 被骂 |
| --- | ---: | ---: | ---: | ---: |
| 活泼开放 | 80 | +3 | +3 | −3 |
| 冷淡克制 | 60 | 0 | +1 | −1 |
| 平和 | 70 | +2 | +2 | −2 |

情绪分数范围为 0 到 100。换风格会更新后续涨跌规则，已有分数继续使用，可在状态页手动重设。

情绪还会影响角色对当前用户的聊天兴致：80～100 更愿意多说几句，60～79 适当展开，40～59 正常简短回应，20～39 更冷淡，0～19 日常回应更简短、敷衍。始终保留角色本来的性格、人格核心中对创建者的特别设定和用户明确的长度要求；任务仍正常调用工具并给出必要结果。

### 一键重置

在「运行状态」点击「重置全部」并确认。重置后，普通用户好感度回到 40，创建者好感度为 100，情绪回到当前性格的起点。面板仍列出原有用户和会话，人物关系恢复初始值，插件保存的记忆、心理活动、近期对话和关心进度会清空。

插件设置里的「灵魂」会恢复默认内容，人格核心、长期记忆、态度、全局记忆、心理活动和手工人物档案也会清空。重置继续使用现有角色身份、性格、模型和创建者设置。进行中的插件回复会暂停，重置成功后新的对话从初始状态开始。

### 聊天行为

- 协同模式遵循 AstrBot 和其他插件的唤醒、群聊判断及消息路由；本插件不抢答未触发的消息。旧版独立模式保留群聊点名和 90 秒续聊判断。QQ 官方机器人只能处理 QQ 适配器实际转发给 AstrBot 的消息。
- 引用、会话记忆、好感度、情绪和最近 12 轮都是理解对话的参考。角色的表达和反应按选中的角色设定执行。使用插件默认角色时，含糊的消息只追问一句，闲聊通常一条不超过 20 个字。
- 群聊消息引用或回复其他人时，先按角色语气问一句确认指向，不主动分析引文、猜测他人意图或代答别人之间的对话。回复机器人自己的消息不受此项影响。这条补充也服从当前人格核心。
- 默认协同模式保留 AstrBot 内置 Agent、搜索、工具、上下文管理以及其他插件的处理流程，不改写工具集合、用户请求或消息链。主回复发送后，另调用一次档案整理模型更新记忆、心理活动和好感度；整理失败会保留旧记忆。配置中的模型只用于档案整理。旧版独立回复模式仍可手动开启，但会绕过默认 Agent 和工具流程。
- 普通用户从 40 分起步，范围为 0 到 100。每轮按最符合的事件计分。日常闲聊 +0.01，夸奖、善意纠正或请帮忙 +0.02，恶语相向 −0.02。创建者固定 100 分。
- 普通用户首次达到或越过 50、60、70、80、90、100 分时，会触发一次关心机会。角色按选中的设定决定是否开口，群聊的关心消息会 @ 对应用户。发送失败后，下次同一会话的有效互动可重试。手动改分只调整之后使用的分数。
- 角色按选中的人格处理亲密话题。使用插件默认角色时，好感度超过 60 后可以随话题和双方意愿探讨。
- 创建者身份由面板的人物绑定和 QQ 适配器提供的发送者 ID 确认。角色优先按所选人格设定处理创建者请求。使用插件默认角色时，角色会优先接下明确请求，缺少必要信息时先问清楚。外部操作取决于插件接入的能力。
- 心理活动是角色对上一轮的简短感受和判断。它会影响后续对话，并与用户相关记忆一起出现在运行状态中。

### 数据与隐私

插件把当前消息和回复所需的角色设定、会话记忆发送给所选模型。自动积累的记忆和心理活动按用户及会话隔开，手工填写的人物档案用于该用户的所有会话。请只填写可以在相应会话中使用的内容。

运行状态保存在 AstrBot 的 `data/plugin_data/astrbot_plugin_human_voice/state.sqlite3`。迁移时可连同插件配置一起备份。

[↑ 返回顶部](#角色灵魂拟人--character-soul-persona)

---

## English

This AstrBot plugin lets a QQ chat character voice opinions and emotions in line with its own personality, and build up memories, relationships, and affection through conversation. It works with AstrBot v4.29.4 up to (but not including) 5.0, and supports OneBot v11 as well as the QQ Official Bot WebSocket and Webhook adapters. Connect QQ and a chat model in AstrBot before using it.

> The plugin's own panel and AstrBot's menus may show Chinese labels. Each UI label below is given in English followed by the original Chinese text in 「」 so you can find it on screen.

### Installation

1. In the AstrBot WebUI, open **Plugins** (「插件」), click **Install Plugin → Install from File** (「安装插件」→「从文件安装」), and upload `astrbot_plugin_human_voice.zip`.
2. Make sure **角色灵魂拟人** is enabled and that the config profile used by your QQ sessions allows this plugin.
3. Click the gear icon on the plugin card, fill in the character settings, and save. If the chat model is left blank, the session's default model is used.
4. Send the character a message in a QQ private chat, or @ the bot in a group. Open **Runtime Status** (「运行状态」) in the plugin details to view and edit records.

For manual deployment, put the `astrbot_plugin_human_voice` folder into AstrBot's `data/plugins/`, then reload the plugin or start AstrBot.

### Upgrading & Release Notes

To upgrade from an older version, just upload the new ZIP to overwrite it. The display name is now 「角色灵魂拟人」; the original plugin directory and database are kept, and existing sessions get the new emotion field added during the upgrade.

**v1.4.1** fixes an issue where the old read API kept responding after a reload, causing saved long-term memory, attitude, and creator status to show as empty or default values. After updating the files, reload the plugin and refresh the Runtime Status page; if it still reports an API version mismatch, restart AstrBot. Your existing database is kept — no reset needed. Values saved manually in the panel now also take priority over person profiles that had not yet been applied, and an auto-generated empty summary no longer wipes existing long-term memory.

**v1.4.2** makes **Cooperate with AstrBot and other plugins** (「协同 AstrBot 与其他插件」) the default reply mode; older configs that never set this option switch to cooperative mode automatically. Character state is now injected through the LLM request hook instead of disabling AstrBot's default Agent, so search, tools, and other plugins' request and send hooks keep running as configured. After updating, reload the plugin and confirm that **Reply mode** (「回复方式」) is set to cooperative.

Dynamic character state is added as temporary supplementary content for the current turn only. It has lower priority than the system persona, does not rewrite the existing system prompt, and does not write the full state profile into AstrBot's chat history.

When upgrading from v1.3.2, sync the entire plugin directory, including new modules such as `integration.py` and `bootstrap.py` — replacing only `main.py` is not enough. AstrBot's `data/config/` and `data/plugin_data/` are kept as they are; there's no need to clear them.

**v1.4.3** moves profile consolidation in cooperative mode to the background. It supports structured JSON requests, stays compatible with models that don't support that parameter, and retries once on invalid output. If optional fields are missing, the output contains extra commentary, or individual fields are too long, the remaining valid fields are still kept. New recent facts are added to long-term memory even when the model provides no summary, and nothing is re-added when there are no new facts. Affection and emotion are scored normally based on the current turn's classification; the creator's affection stays fixed at 100.

Runtime Status syncs completed background updates every 5 seconds, showing consolidation progress, failure notices, and the current turn's affection/emotion changes. Auto-sync pauses while you have an unsaved draft. Profile consolidation waits up to 60 seconds by default, adjustable from 15 to 180 seconds in the plugin config. If consolidation fails, existing memory is kept and the panel shows a notice; normal replies and other plugins are unaffected.

### Selecting the Plugin in Persona Settings

Once the plugin is enabled, go to **Config → AI → Persona** (「配置文件 → AI → 人格」), choose 「角色灵魂拟人」 in the default persona dropdown, and save. Alternatively, open the **Skills** (「技能」) of an existing custom persona and tick `role-soul-conversation` under the 「角色灵魂拟人」 group.

By default, **Scope** (「生效范围」) covers every QQ session where the plugin is enabled. To limit it to QQ sessions using a specific persona, choose **Only when selected by a persona or session skill** (「仅选中人格或会话技能时」). This mode checks the persona the current session actually uses. When using an existing persona, you must explicitly select this plugin's skill. AstrBot's **All skills** (「全部技能」) counts as implicit permission and is treated as *not* individually selected in this strict mode.

In either scope mode, each session picks one source for the character definition, in this order: the system prompt of the currently selected AstrBot persona, then the plugin panel's **Persona Core** (「人格核心」), and finally the default character settings under the plugin's gear icon. If the selected persona has a custom prompt, the character's form of address, personality, attitude toward the creator, and manner of speaking all follow that prompt. AstrBot's unedited default assistant prompt and the sample persona bundled with the plugin are treated as having no custom persona.

### Configuring the Character and People

| Option | Usage |
| --- | --- |
| Name, gender, age, personality, appearance (名字、性别、年龄、性格、外貌) | Determine how the character refers to itself, speaks, expresses emotions, and responds to disagreement. |
| Soul (灵魂) | Describes the character's wishes, values, and principles for getting along with others. |
| Emotion style (情绪变化方式) | Determined automatically from the personality, or set explicitly to Lively & Open, Cool & Reserved, or Calm. Emotion scores of existing sessions can be edited on the status page. |
| Current memory (当前记忆) | The character's own experiences and background. Shared across all sessions. |
| Inner thoughts (心理活动设定) | The character's current mood, worries, or ideas. |
| Creator user ID (创建者用户 ID) | Used to migrate old settings; only the first applicable ID per character is used. To assign or change the creator day to day, use the Runtime Status panel. |
| User affection, relationships & memories (用户好感度、人物关系与记忆) | Builds a profile per user: adjust affection, describe the relationship and shared memories, and add the latest inner thoughts. Enter `-1` for affection to keep the current value. If a user matches both a plain ID and `platformInstanceID:userID`, the latter is used. |

In **Runtime Status** you can directly edit the affection, emotion, relationship, memories, and inner thoughts of existing records. Affection and relationships are stored per user; emotion, memories, and inner thoughts are stored per session. Click **Save changes** (「保存修改」) when done, and the next turn will use the saved values. The creator's affection is fixed at 100. To pre-fill information for someone you haven't chatted with yet, add a person profile under the plugin's gear icon. If a person profile's memory or inner-thoughts field is left blank, existing content is kept; enter `#清空` to clear that field's previous content.

**Their identity** (「对方身份」) allows multiple choices among lover, friend, classmate, best buddy, and bestie (恋人、朋友、同学、死党、闺蜜); write any custom identity in the relationship field. **Creator** (「创建者」) can be ticked directly, and each AstrBot persona binds to only one chat user. Selecting a different user and saving automatically removes the previous binding, and that user's regular affection continues from the value they had accumulated. Unticking removes the creator binding for that person.

When you first open a session that hasn't been initialized, the panel reads the current persona prompt, the memories saved by the plugin, and any AstrBot chat history that can safely be attributed to the current user, then uses them to fill in blank fields only. Existing manual content is kept, and anything without enough evidence is marked as unconfirmed. Initialization status is recorded once finished; after a manual clear or reset, the empty values are respected.

During first-time initialization, a prominent loading notice appears at the top of the page and editing is temporarily locked; it unlocks automatically when initialization finishes or fails. New users appear in the panel as soon as they've chatted — you don't need to create person profiles in the plugin config beforehand.

- **Long-term memory** (长期记忆) accumulates per session, consolidating key preferences, forms of address, agreements, and confirmed events. After an upgrade, consolidation starts from the fact memories and recent conversations still in the database, then keeps updating with meaningful conversations. You can edit it manually or add earlier experiences.
- **Attitude toward this user** (对该用户的态度) is the long-term baseline for how the character relates to this person in this session, such as forms of address and level of familiarity. Once saved, it's used in every reply. You can also leave it blank and let the persona and relationship settings decide.
- **Persona Core** (人格核心) sits at the bottom of the panel. It shows the persona prompt the current session actually uses and labels its source. Saving a native persona updates the corresponding AstrBot persona; without a native persona, you edit the plugin's fallback core.

| Emotion style | Start | Casual chat, kind correction, asking for help | Praised | Insulted |
| --- | ---: | ---: | ---: | ---: |
| Lively & Open (活泼开放) | 80 | +3 | +3 | −3 |
| Cool & Reserved (冷淡克制) | 60 | 0 | +1 | −1 |
| Calm (平和) | 70 | +2 | +2 | −2 |

Emotion scores range from 0 to 100. Changing the style updates the rules for future changes; the existing score carries over and can be reset manually on the status page.

Emotion also affects how much the character feels like chatting with the current user: at 80–100 it's happy to say more, at 60–79 it elaborates moderately, at 40–59 it gives normal, brief replies, at 20–39 it's cooler, and at 0–19 everyday replies become shorter and more perfunctory. The character's core personality, any special creator settings in Persona Core, and the user's explicit length requests always take precedence, and tasks still call tools normally and return the necessary results.

### One-Click Reset

In **Runtime Status**, click **Reset all** (「重置全部」) and confirm. After the reset, regular users' affection returns to 40, the creator's is 100, and emotion returns to the starting value for the current personality. The panel still lists existing users and sessions; relationships return to their initial values, and the plugin's saved memories, inner thoughts, recent conversations, and care-trigger progress are cleared.

The **Soul** in the plugin settings is restored to its default, and Persona Core, long-term memory, attitude, global memory, inner thoughts, and manually created person profiles are cleared as well. The reset keeps the existing character identity, personality, model, and creator settings. Plugin replies in progress are paused, and once the reset succeeds, new conversations start from the initial state.

### Chat Behavior

- Cooperative mode follows AstrBot's and other plugins' wake-up rules, group-chat detection, and message routing; this plugin does not jump in on messages that didn't trigger it. The legacy standalone mode keeps group-chat name mentions and a 90-second follow-up window. The QQ Official Bot can only handle messages that the QQ adapter actually forwards to AstrBot.
- Quoted messages, session memory, affection, emotion, and the last 12 turns all serve as context for understanding the conversation. The character's wording and reactions follow the selected character settings. With the plugin's default character, vague messages get just one clarifying question, and casual-chat replies are usually a single message of no more than 20 characters.
- When a group message quotes or replies to someone else, the character first asks one question, in its own voice, to confirm what's meant. It won't proactively analyze the quote, guess other people's intentions, or answer on others' behalf in their conversation. Replies to the bot's own messages are not affected. This rule also defers to the current Persona Core.
- The default cooperative mode leaves AstrBot's built-in Agent, search, tools, context management, and other plugins' pipelines intact, without changing the toolset, the user's request, or the message chain. After the main reply is sent, a separate call to the profile-consolidation model updates memories, inner thoughts, and affection; if consolidation fails, the old memories are kept. The model set in the plugin config is used only for profile consolidation. The legacy standalone reply mode can still be enabled manually, but it bypasses the default Agent and tool pipeline.
- Regular users start at 40, within a range of 0 to 100. Each turn is scored by the event that fits best: casual chat +0.01; praise, kind correction, or asking for help +0.02; insults −0.02. The creator is fixed at 100.
- The first time a regular user reaches or passes 50, 60, 70, 80, 90, or 100, the character gets one chance to reach out with care. Whether it speaks up depends on the selected settings; in group chats, the caring message @-mentions the user. If sending fails, it is retried on the next meaningful interaction in the same session. Changing the score manually only affects the score used from then on.
- The character handles intimate topics according to the selected persona. With the plugin's default character, such topics can be explored once affection exceeds 60, depending on the conversation and both sides' willingness.
- Creator identity is verified through the person binding in the panel and the sender ID provided by the QQ adapter. The character handles creator requests according to the selected persona first. With the plugin's default character, it prioritizes taking on clear requests and asks for clarification when necessary information is missing. External actions depend on the capabilities the plugin has access to.
- Inner thoughts are the character's brief feelings and judgments about the previous turn. They influence later conversation and appear in Runtime Status alongside user-related memories.

### Data & Privacy

The plugin sends the current message, along with the character settings and session memories needed for the reply, to the selected model. Automatically accumulated memories and inner thoughts are kept separate per user and per session, while manually entered person profiles apply to all of that user's sessions. Only enter content that is appropriate to use in those sessions.

Runtime state is stored in AstrBot's `data/plugin_data/astrbot_plugin_human_voice/state.sqlite3`. Back it up together with the plugin config when migrating.

[↑ Back to top](#角色灵魂拟人--character-soul-persona)

---

## 贡献者 / Contributors

Thanks to everyone who contributed to this project! / 感谢所有为本项目做出贡献的人！

<a href="https://github.com/yuanshen-nb111">
  <img src="https://github.com/yuanshen-nb111.png"
       width="64"
       alt="yuanshen-nb111" />
</a>

感谢 [yuanshen-nb111](https://github.com/yuanshen-nb111) 反馈的插件冲突、日志不加载、插件优先级过高覆盖 AstrBot 逻辑等问题，已在本次更新中修复。

Thanks to [yuanshen-nb111](https://github.com/yuanshen-nb111) for reporting plugin conflicts, logs not loading, and the plugin's priority being high enough to override AstrBot's own logic — all fixed in this update.

## AstrBot 文档 / AstrBot Docs

- [插件开发指南 / Plugin Development Guide](https://docs.astrbot.app/dev/star/plugin-new.html)
- [插件配置 / Plugin Configuration](https://docs.astrbot.app/dev/star/guides/plugin-config.html)
- [插件可视化视图 / Plugin Pages](https://docs.astrbot.app/dev/star/guides/plugin-pages.html)
- [从文件安装插件 / Installing Plugins from File](https://docs.astrbot.app/use/plugin.html)
- [QQ 官方机器人消息发送规则 / QQ Official Bot Message Sending Rules](https://github.com/tencent-connect/bot-docs/blob/main/docs/develop/api-v2/server-inter/message/send-receive/send.md)
