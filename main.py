"""AstrBot entry point for QQ persona conversations."""

import asyncio
import json
import re
import sqlite3
import time
from collections import OrderedDict
from contextlib import AsyncExitStack
from copy import deepcopy

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import At, AtAll, Plain, Reply
from astrbot.api.star import Context, Star, StarTools
from astrbot.api.web import error_response, json_response, request

from .persona import NATIVE_PERSONA_ID, ensure_native_persona, resolve_native_persona
from .state import EMOTION_RULES, StateStore
from .voice import (
    AUTHORITATIVE_CREATOR_CONTEXT,
    AUTHORITATIVE_GUIDANCE,
    CREATOR_INSTRUCTIONS,
    LEGACY_SAMPLE_NAME,
    SYSTEM_PROMPT,
    TURN_PROTOCOL,
    Turn,
    clarification_question,
    omit_legacy_self_introduction,
    parse_turn,
    remove_chat_filler,
    short_messages,
)

PLUGIN_NAME = "astrbot_plugin_human_voice"
QQ_PLATFORMS = {"aiocqhttp", "qq_official", "qq_official_webhook"}
FOLLOWUP_SECONDS = 90
RESET_CONFIG_FIELDS = ("soul", "persona_core", "memory", "psychology", "relationships")
CARE_MESSAGES = {
    50: "对了，你今天有好好吃饭吗？",
    60: "这几天睡得怎么样？",
    70: "最近上学或上班还顺利吗？",
    80: "最近有遇到聊得来的人吗？",
    90: "这几天有留点时间给自己吗？",
    100: "最近有什么让你开心的小事吗？",
}
CARE_TOPICS = {
    50: "吃饭",
    60: "睡眠",
    70: "上学或工作近况",
    80: "和朋友相处",
    90: "休息和独处时间",
    100: "近来开心的小事",
}


class HumanVoicePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.state = StateStore(StarTools.get_data_dir(PLUGIN_NAME) / "state.sqlite3")
        self.turn_locks = [asyncio.Lock() for _ in range(64)]
        self.reset_lock = asyncio.Lock()
        self.reset_generation = 0
        self.recent_group_chats: OrderedDict[tuple[str, str], float] = OrderedDict()
        context.register_web_api(
            f"/{PLUGIN_NAME}/status", self.web_status, ["GET", "POST"], "读写角色状态"
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/reset", self.web_reset, ["POST"], "重置全部角色状态"
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/persona-core",
            self.web_persona_core,
            ["POST"],
            "保存人格核心",
        )

    async def initialize(self):
        await ensure_native_persona(self.context)

    @property
    def persona_name(self) -> str:
        name = str(self.config.get("persona_name") or "").strip()
        return "" if name == LEGACY_SAMPLE_NAME else name

    def persona_settings(self) -> dict:
        settings = {
            key: self.config.get(key, "")
            for key in ("gender", "personality", "age", "appearance", "soul")
        }
        if self.persona_name:
            settings["name"] = self.persona_name
        return settings

    def emotion_style(self) -> str:
        selected = self.config.get("emotion_style", "auto")
        if selected in {"lively", "cool", "neutral"}:
            return selected
        personality = str(self.config.get("personality", ""))
        cold = re.search(r"冷淡|高冷|寡言|冷漠|疏离", personality)
        lively = re.search(r"活泼|开朗|外向|热情|开放", personality)
        if bool(cold) == bool(lively):
            return "neutral"
        return "cool" if cold else "lively"

    def is_creator(self, user_id: str) -> bool:
        creator_ids = self.config.get("creator_ids", [])
        return user_id in creator_ids or user_id.split(":", 1)[-1] in creator_ids

    def overrides_for(self, user_id: str) -> dict:
        profiles = self.config.get("relationships", [])
        bare_id = user_id.split(":", 1)[-1]
        for candidate in (user_id, bare_id):
            for profile in reversed(profiles):
                if str(profile.get("user_id", "")).strip() == candidate:
                    overrides = {"relationship": profile.get("relationship", "")}
                    memory = str(profile.get("memory", "")).strip()
                    psychology = str(profile.get("psychology", "")).strip()
                    if memory:
                        overrides["memories"] = (
                            [] if memory == "#清空" else memory.splitlines()
                        )
                    if psychology:
                        overrides["psychology"] = (
                            "" if psychology == "#清空" else psychology
                        )
                    score = profile.get("affection", -1)
                    if score != -1:
                        overrides["score"] = score
                    return overrides
        return {}

    async def web_status(self):
        if not request.username:
            return error_response("需要登录 AstrBot 管理面板", status_code=401)
        headers = {"Cache-Control": "no-store"}
        style = self.emotion_style()
        if request.method == "GET":
            return self.status_response()

        payload = await request.json()
        if not isinstance(payload, dict):
            return error_response("保存请求格式无效")
        user_id, scope = payload.pop("user_id", None), payload.pop("scope", None)
        if (
            not isinstance(user_id, str)
            or not isinstance(scope, str)
            or not (user_id and scope)
        ):
            return error_response("请选择要编辑的用户和会话")
        creator = self.is_creator(user_id)
        generation = self.reset_generation
        async with self.turn_locks[hash((user_id, scope)) % len(self.turn_locks)]:
            if self.reset_lock.locked() or generation != self.reset_generation:
                return error_response(
                    "角色状态正在重置，请刷新后再修改", status_code=409
                )
            try:
                row = self.state.edit_status(
                    user_id,
                    scope,
                    changes=payload,
                    creator=creator,
                    emotion_style=style,
                )
            except KeyError:
                return error_response("这条会话记录已不存在", status_code=404)
            except ValueError as exc:
                return error_response(str(exc))
        return json_response({"user": self.status_row(row)}, headers=headers)

    async def web_reset(self):
        if not request.username:
            return error_response("需要登录 AstrBot 管理面板", status_code=401)
        payload = await request.json()
        if not isinstance(payload, dict) or payload.get("confirm") is not True:
            return error_response("请先确认重置全部角色状态")
        if self.reset_lock.locked():
            return error_response("重置正在进行中", status_code=409)

        async with self.reset_lock, AsyncExitStack() as locks:
            self.reset_generation += 1
            for lock in self.turn_locks:
                await locks.enter_async_context(lock)
            try:
                self.reset_state_and_config()
            except (OSError, sqlite3.Error) as exc:
                logger.warning(f"[{PLUGIN_NAME}] 重置失败（{type(exc).__name__}）")
                return error_response(
                    "重置失败，请检查 AstrBot 日志后重试", status_code=500
                )
            self.recent_group_chats.clear()
            return self.status_response()

    async def web_persona_core(self):
        if not request.username:
            return error_response("需要登录 AstrBot 管理面板", status_code=401)
        payload = await request.json()
        core = payload.get("persona_core") if isinstance(payload, dict) else None
        if not isinstance(core, str) or len(core) > 4000:
            return error_response("人格核心须为 4000 字以内的文本")
        if self.reset_lock.locked():
            return error_response("角色状态正在重置，请刷新后再修改", status_code=409)
        previous = self.config.get("persona_core", "")
        try:
            self.config.save_config({"persona_core": core.strip()})
        except OSError as exc:
            self.config["persona_core"] = previous
            logger.warning(f"[{PLUGIN_NAME}] 保存人格核心失败（{type(exc).__name__}）")
            return error_response(
                "保存失败，请检查 AstrBot 日志后重试", status_code=500
            )
        return json_response(
            {"persona_core": self.config["persona_core"]},
            headers={"Cache-Control": "no-store"},
        )

    def reset_state_and_config(self) -> None:
        defaults = {
            key: deepcopy(self.config.schema[key]["default"])
            for key in RESET_CONFIG_FIELDS
        }
        previous = {key: deepcopy(self.config[key]) for key in RESET_CONFIG_FIELDS}
        try:
            self.config.save_config(defaults)
            self.state.reset()
        except (OSError, sqlite3.Error):
            self.config.update(previous)
            try:
                self.config.save_config()
            except OSError as rollback_error:
                logger.error(
                    f"[{PLUGIN_NAME}] 无法恢复重置前的配置（{type(rollback_error).__name__}）"
                )
            raise

    def status_response(self):
        style = self.emotion_style()
        rows = self.state.list_status(emotion_style=style)
        return json_response(
            {
                "users": [self.status_row(row) for row in rows],
                "persona_core": self.config.get("persona_core", ""),
                "emotion_default": EMOTION_RULES[style][0],
            },
            headers={"Cache-Control": "no-store"},
        )

    def status_row(self, row: dict) -> dict:
        row["creator"] = self.is_creator(row["user_id"])
        if row["creator"]:
            row["affection"] = 100.0
            if row["relationship"] in {"", "初识"}:
                row["relationship"] = "创建者"
        return row

    @filter.command("拟人身份")
    async def show_identity(self, event: AstrMessageEvent):
        if event.get_platform_name() in QQ_PLATFORMS and event.is_private_chat():
            yield event.plain_result(
                f"你的用户 ID 为 {event.get_sender_id()}\n"
                f"带平台实例 ID 的写法为 {event.get_platform_id()}:{event.get_sender_id()}"
            ).use_markdown(False)

    def group_address(self, event: AstrMessageEvent, text: str) -> str:
        self_id = str(event.get_self_id())
        broadcast = False
        other_target = False
        for component in event.get_messages():
            if isinstance(component, (At, Reply)):
                target = str(
                    component.qq if isinstance(component, At) else component.sender_id
                )
                if target == self_id:
                    return "explicit"
                if target == "all":
                    broadcast = True
                else:
                    other_target = True
            elif isinstance(component, AtAll):
                broadcast = True
        if other_target or broadcast:
            return "other"
        if event.is_at_or_wake_command:
            return "explicit"
        if not self.persona_name:
            return "unaddressed"
        vocative = rf"^{re.escape(self.persona_name)}(?=$|[\s，,。！？!?:：]|你)"
        return "named" if re.match(vocative, text) else "unaddressed"

    @staticmethod
    def bot_account_display_name(event: AstrMessageEvent) -> str:
        self_id = str(event.get_self_id() or "")
        if not self_id:
            return ""
        for part in event.get_messages():
            if isinstance(part, At) and str(part.qq) == self_id:
                name = part.name
            elif isinstance(part, Reply) and str(part.sender_id) == self_id:
                name = part.sender_nickname
            else:
                continue
            if name and name.strip():
                return name.strip()[:120]
        return ""

    @filter.event_message_type(filter.EventMessageType.ALL, priority=-100)
    async def chat(self, event: AstrMessageEvent):
        if (
            not self.config.get("enabled", True)
            or event.get_platform_name() not in QQ_PLATFORMS
        ):
            return
        # Yield to a higher-priority plugin or an explicit command that has already responded.
        if getattr(event, "_has_send_oper", False) or event.call_llm:
            return
        profile = self.context.get_config(umo=event.unified_msg_origin)
        if not profile.get("provider_settings", {}).get("enable", True):
            return
        sender_id = str(event.get_sender_id() or "")
        text = event.message_str.strip()
        if not sender_id or sender_id == str(event.get_self_id()):
            return
        if text.startswith(("/", "／")) or (
            text and text.split(maxsplit=1)[0] == "拟人身份"
        ):
            return

        private = event.is_private_chat()
        if private and not event.is_at_or_wake_command:
            return  # Respect the platform profile's private wake-prefix setting.
        user_id = f"{event.get_platform_id()}:{sender_id}"
        scope = event.unified_msg_origin
        key = (user_id, scope)
        address = "private" if private else self.group_address(event, text)
        if not text and (
            address != "explicit"
            or any(
                not isinstance(part, (At, Reply, Plain))
                for part in event.get_messages()
            )
        ):
            return
        if address == "unaddressed":
            last_chat = self.recent_group_chats.get(key)
            if last_chat is None or time.monotonic() - last_chat > FOLLOWUP_SECONDS:
                return
            address = "continuation"

        generation = self.reset_generation
        persona_id, native_style, selected_skill = await resolve_native_persona(
            self.context, event
        )
        if (
            self.config.get("activation_mode") == "selected_persona"
            and persona_id != NATIVE_PERSONA_ID
            and not selected_skill
        ):
            return
        event.should_call_llm(True)
        if address == "other":
            return
        if self.reset_lock.locked():
            return
        lock = self.turn_locks[hash(key) % len(self.turn_locks)]
        async with lock:
            if self.reset_lock.locked() or generation != self.reset_generation:
                return
            try:
                await self.reply(
                    event, user_id, scope, address, text, native_style=native_style
                )
            except Exception as exc:  # noqa: BLE001 - AstrBot's plugin boundary.
                # Provider exceptions can include request data. Keep it out of logs and chat.
                logger.warning(f"[{PLUGIN_NAME}] 对话处理失败（{type(exc).__name__}）")

    async def reply(
        self,
        event: AstrMessageEvent,
        user_id: str,
        scope: str,
        address: str,
        text: str,
        *,
        native_style: str = "",
    ) -> None:
        creator = self.is_creator(user_id)
        role_prompt = (
            native_style
            if native_style.strip()
            else (self.config.get("persona_core") or "").strip()
        )
        emotion_style = self.emotion_style()
        snapshot = self.state.snapshot(
            user_id,
            scope,
            creator=creator,
            overrides=self.overrides_for(user_id),
            emotion_style=emotion_style,
        )
        message_id = str(event.message_obj.message_id or "") or None
        if message_id and self.state.is_recorded(user_id, scope, message_id):
            if not creator and not snapshot.awaiting_clarification:
                await self.send_care(
                    event,
                    user_id,
                    scope,
                    snapshot.milestones,
                    role_prompt=role_prompt,
                )
            return

        intimate_allowed = creator or snapshot.score > 60
        relationship = snapshot.relationship
        if creator and relationship in {"", "初识"}:
            relationship = "创建者"
        bot_quote = next(
            (
                part.message_str
                or "".join(
                    item.text for item in (part.chain or []) if isinstance(item, Plain)
                )
                for part in event.get_messages()
                if isinstance(part, Reply)
                and str(part.sender_id) == str(event.get_self_id())
            ),
            "",
        )
        recent_dialogue = snapshot.dialogue
        if LEGACY_SAMPLE_NAME not in role_prompt:
            bot_quote = omit_legacy_self_introduction(bot_quote)
            recent_dialogue = [
                (user_text, omit_legacy_self_introduction(bot_text))
                for user_text, bot_text in recent_dialogue
            ]
        conversation = {
            "address": address,
            "creator": creator,
            "relationship": relationship or "普通网友",
            "affection": snapshot.score,
            "emotion": snapshot.emotion,
            "memory": snapshot.memories,
            "long_term_memory": snapshot.long_term_memory,
            "attitude": snapshot.attitude,
            "previous_psychology": snapshot.psychology,
            "recent_dialogue": [{"user": u, "reply": r} for u, r in recent_dialogue],
            "awaiting_clarification": snapshot.awaiting_clarification,
            "quoted_bot_message": bot_quote[:800],
            "message": text,
            "mention_only": not text,
        }
        if display_name := self.bot_account_display_name(event):
            conversation["bot_account_display_name"] = display_name
        if not role_prompt:
            conversation.update(
                persona=self.persona_settings(),
                emotion_style=emotion_style,
                intimate_allowed=intimate_allowed,
                persona_memory=self.config.get("memory", ""),
                persona_psychology=self.config.get("psychology", ""),
            )
        turn = await self.generate_turn(
            scope, conversation, creator=creator, role_prompt=role_prompt
        )
        if self.reset_lock.locked():
            return
        if turn is None:
            if role_prompt or address == "continuation":
                return
            turn = Turn("clarify", "casual", "neutral", False, "", "", "")

        if turn.action == "ignore" and (role_prompt or address == "continuation"):
            return
        action = (
            "clarify"
            if not role_prompt and (not text or turn.action == "ignore")
            else turn.action
        )
        reply = turn.reply
        task_reply = turn.mode == "task" or (
            creator and turn.category == "help_request"
        )
        if not role_prompt and action == "answer" and not task_reply:
            reply = remove_chat_filler(reply)
            if not reply:
                action = "clarify"
        if not role_prompt and action == "clarify":
            reply = clarification_question(
                reply, mention_only=not text, creator=creator
            )
        blocked_intimacy = (
            not role_prompt
            and action == "answer"
            and turn.intimate
            and not intimate_allowed
        )
        if blocked_intimacy:
            reply = "这个话题我想先缓缓。"
        bubbles = (
            [reply]
            if role_prompt or action == "clarify" or task_reply
            else short_messages(reply)[:3]
        )
        sent = []
        for bubble in bubbles:
            if self.reset_lock.locked():
                return
            try:
                await self.send_text(event, bubble)
            except Exception:
                if not sent:
                    raise
                logger.warning(f"[{PLUGIN_NAME}] QQ 仅发送了部分回复")
                break
            sent.append(bubble)

        if self.reset_lock.locked():
            return
        psychology = turn.psychology
        if (
            not role_prompt
            and creator
            and task_reply
            and action == "answer"
            and re.search(
                r"不服(?!输)|被迫|被逼|索性认了|不想再争|心不甘情不愿", psychology
            )
        ):
            psychology = "愿意按创建者的意思推进这件事。"
        result = self.state.record(
            user_id,
            scope,
            message_id,
            creator=creator,
            category=turn.category if text and turn.action != "ignore" else "neutral",
            memory=turn.memory if action == "answer" and not blocked_intimacy else "",
            psychology=("想先保持一点距离。" if blocked_intimacy else psychology)
            or ("" if role_prompt else "还在琢磨对方的意思。"),
            user_text=text,
            reply_text="\n".join(sent),
            clarifying=action == "clarify",
            emotion_style=emotion_style,
            long_term_memory=turn.long_term_memory
            if action == "answer" and not blocked_intimacy
            else None,
        )
        if not event.is_private_chat():
            key = (user_id, scope)
            self.recent_group_chats[key] = time.monotonic()
            self.recent_group_chats.move_to_end(key)
            if len(self.recent_group_chats) > 512:
                self.recent_group_chats.popitem(last=False)
        if not creator and action == "answer":
            await self.send_care(
                event, user_id, scope, result.milestones, role_prompt=role_prompt
            )

    async def generate_turn(
        self, scope: str, conversation: dict, *, creator: bool, role_prompt: str
    ) -> Turn | None:
        try:
            instructions = (
                f"【当前会话的角色设定】\n{role_prompt}\n\n{AUTHORITATIVE_GUIDANCE}\n\n{TURN_PROTOCOL}"
                if role_prompt
                else SYSTEM_PROMPT
            )
            if creator:
                instructions += f"\n\n{AUTHORITATIVE_CREATOR_CONTEXT if role_prompt else CREATOR_INSTRUCTIONS}"
            raw = await self.generate(scope, instructions, conversation, timeout=45)
            return parse_turn(raw)
        except Exception as exc:  # noqa: BLE001 - Providers raise different error types.
            logger.warning(
                f"[{PLUGIN_NAME}] 模型未返回可用回复（{type(exc).__name__}）"
            )
            return None

    async def generate(
        self, scope: str, instructions: str, data: dict, *, timeout: int
    ) -> str:
        async with asyncio.timeout(timeout):
            provider_id = str(self.config.get("provider_id", "")).strip()
            if not provider_id:
                provider_id = await self.context.get_current_chat_provider_id(umo=scope)
            response = await self.context.llm_generate(
                chat_provider_id=provider_id,
                system_prompt=instructions,
                prompt=json.dumps(data, ensure_ascii=False),
            )
            return response.completion_text or ""

    async def care_text(
        self, scope: str, milestone: int, *, role_prompt: str
    ) -> str | None:
        example = CARE_MESSAGES[milestone]
        try:
            if role_prompt:
                raw = await self.generate(
                    scope,
                    f"【当前会话的角色设定】\n{role_prompt}\n\n"
                    "按上面设定判断角色会不会主动关心对方。会的话，按角色的习惯只输出一条消息正文。"
                    "若角色会保持沉默，只输出 SKIP。",
                    {"topic": CARE_TOPICS[milestone]},
                    timeout=8,
                )
                text = raw.strip()
                return "" if text == "SKIP" else text or None
            raw = await self.generate(
                scope,
                "你在 QQ 扮演一个有自己的想法和情绪的角色。先用人设性别和性格校准语气，"
                "把示例改写成自然的关心。只输出一个日常问题，不超过 20 字。"
                "不附加引号、表情代码、旁白或分数。",
                {"persona": self.persona_settings(), "example": example},
                timeout=8,
            )
            text = clarification_question(raw)
            if text != "你指哪件事？":
                return text
        except Exception as exc:  # noqa: BLE001 - Providers raise different error types.
            logger.debug(f"[{PLUGIN_NAME}] 模型未返回关心消息（{type(exc).__name__}）")
        return None if role_prompt else example

    @staticmethod
    async def send_text(event: AstrMessageEvent, text: str) -> None:
        await event.send(MessageChain().message(text).use_markdown(False))

    async def send_care(
        self,
        event: AstrMessageEvent,
        user_id: str,
        scope: str,
        milestones: tuple[int, ...],
        *,
        role_prompt: str,
    ) -> None:
        for milestone in milestones:
            token = self.state.claim_care(user_id, scope, milestone)
            if not token:
                continue
            sent = False
            text = ""
            try:
                care = await self.care_text(scope, milestone, role_prompt=role_prompt)
                if self.reset_lock.locked():
                    return
                if care is None:
                    return
                if not care:
                    sent = True  # The selected role opted to stay silent at this milestone.
                    return
                text = care
                message = MessageChain().use_markdown(False)
                if not event.is_private_chat():
                    message.at(event.get_sender_name(), event.get_sender_id())
                await event.send(message.message(text))
                sent = True
            except Exception as exc:  # noqa: BLE001 - Adapter errors are not uniform.
                logger.warning(
                    f"[{PLUGIN_NAME}] 关心消息待重试（{type(exc).__name__}）"
                )
            finally:
                self.state.finish_care(user_id, milestone, token, sent=sent, text=text)
            break  # At most one extra check-in on each inbound event.
