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
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, StarTools
from astrbot.api.web import error_response, json_response, request
from astrbot.core.agent.message import TextPart
from astrbot.core.message.message_event_result import ResultContentType

from .bootstrap import suggest_initial_state
from .integration import (
    CONTEXT_MARKER,
    NATIVE_FALLBACK_ROLE,
    STATE_INSTRUCTIONS,
    StateUpdate,
    emotion_expression,
    merge_memory_summary,
    native_context,
    parse_state_update,
)
from .persona import (
    NATIVE_PERSONA_ID,
    ensure_native_persona,
    resolve_native_persona,
    resolve_scope_persona,
)
from .state import EMOTION_RULES, IDENTITY_LABELS, StateStore
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
NATIVE_TURN_KEY = f"{PLUGIN_NAME}:native_turn"
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
        self.native_tasks: set[asyncio.Task] = set()
        self.native_update_locks = [asyncio.Lock() for _ in range(64)]
        self.native_update_slots = asyncio.Semaphore(4)
        self.state_updates: OrderedDict[tuple[str, str], dict] = OrderedDict()
        # Older AstrBot versions keep bound handlers after reload and match the
        # methods list exactly when replacing a route (GET != GET + POST).
        owned_routes = {
            f"/{PLUGIN_NAME}/{endpoint}"
            for endpoint in ("status", "reset", "persona-core", "initialize")
        }
        registered = getattr(context, "registered_web_apis", None)
        if isinstance(registered, list):
            registered[:] = [api for api in registered if api[0] not in owned_routes]
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
        context.register_web_api(
            f"/{PLUGIN_NAME}/initialize",
            self.web_initialize,
            ["POST"],
            "从人格和历史初始化",
        )

    async def initialize(self):
        await ensure_native_persona(self.context)

    async def terminate(self):
        tasks = tuple(self.native_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

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

    @staticmethod
    def character_key(persona_id: str | None) -> str:
        return (
            f"astrbot:{persona_id}"
            if persona_id and persona_id != "[%None]"
            else "plugin-default"
        )

    def seed_legacy_creator(
        self, persona_key: str, user_id: str, *, legacy: bool = False
    ) -> None:
        platform_id = user_id.split(":", 1)[0]
        for entry in self.config.get("creator_ids", []):
            if not isinstance(entry, str) or not entry.strip():
                continue
            entry = entry.strip()
            if ":" not in entry:
                entry = f"{platform_id}:{entry}"
            if entry.startswith(f"{platform_id}:"):
                self.state.seed_creator(persona_key, entry)
                return
        if legacy:
            self.state.seed_creator(persona_key, user_id)

    async def character_context(self, user_id: str, scope: str) -> dict:
        persona_id, prompt, _ = await resolve_scope_persona(
            self.context, user_id, scope
        )
        source = "plugin"
        editor_prompt = self.config.get("persona_core", "")
        if persona_id and persona_id != "[%None]":
            try:
                native = await self.context.persona_manager.get_persona(persona_id)
            except ValueError:
                native = None
            if native is not None and prompt.strip():
                source, editor_prompt = "astrbot", native.system_prompt
        if source == "plugin" and not editor_prompt.strip():
            labels = {
                "name": "名字",
                "gender": "性别",
                "age": "年龄",
                "personality": "性格",
                "appearance": "外貌",
                "soul": "灵魂与价值观",
            }
            editor_prompt = "\n".join(
                f"{labels[field]}为{value}。"
                for field, value in self.persona_settings().items()
                if value != "" and value is not None
            )
        return {
            "persona_key": self.character_key(persona_id),
            "persona_name": persona_id or self.persona_name or "插件默认人物",
            "persona_id": persona_id,
            "native_style": prompt,
            "persona_core": editor_prompt,
            "persona_core_source": source,
        }

    def turn_lock(self, user_id: str) -> asyncio.Lock:
        return self.turn_locks[hash(user_id) % len(self.turn_locks)]

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
            return await self.status_response()

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
        generation = self.reset_generation
        character = await self.character_context(user_id, scope)
        provided_key = payload.pop("persona_key", character["persona_key"])
        if provided_key != character["persona_key"]:
            return error_response(
                "当前会话的人物已变化，请刷新后再保存", status_code=409
            )
        async with self.turn_lock(user_id):
            if self.reset_lock.locked() or generation != self.reset_generation:
                return error_response(
                    "角色状态正在重置，请刷新后再修改", status_code=409
                )
            key = character["persona_key"]
            self.seed_legacy_creator(
                key, user_id, legacy=self.state.legacy_creator_for(user_id)
            )
            creator = self.state.creator_for(key) == user_id
            if isinstance(selected := payload.get("identities"), list):
                creator = "creator" in selected
            try:
                row = self.state.edit_status(
                    user_id,
                    scope,
                    changes=payload,
                    creator=creator,
                    emotion_style=style,
                    persona_key=key,
                    overrides=self.overrides_for(user_id),
                )
            except KeyError:
                return error_response("这条会话记录已不存在", status_code=404)
            except ValueError as exc:
                return error_response(str(exc))
        payload = await self.status_payload()
        payload["user"] = await self.status_row(row, character=character)
        return json_response(payload, headers=headers)

    async def web_initialize(self):
        if not request.username:
            return error_response("需要登录 AstrBot 管理面板", status_code=401)
        payload = await request.json()
        if not isinstance(payload, dict):
            return error_response("初始化请求格式无效")
        user_id, scope = payload.get("user_id"), payload.get("scope")
        if (
            not isinstance(user_id, str)
            or not isinstance(scope, str)
            or not user_id
            or not scope
        ):
            return error_response("请选择要初始化的会话")
        generation = self.reset_generation
        character = await self.character_context(user_id, scope)
        if (
            payload.get("persona_key", character["persona_key"])
            != character["persona_key"]
        ):
            return error_response("当前会话的人物已变化，请刷新后重试", status_code=409)
        async with self.turn_lock(user_id):
            if self.reset_lock.locked() or generation != self.reset_generation:
                return error_response("角色状态正在重置，请稍后重试", status_code=409)
            snapshot = self.state.snapshot(
                user_id,
                scope,
                emotion_style=self.emotion_style(),
                overrides=self.overrides_for(user_id),
            )
            if not snapshot.initialized:
                self.seed_legacy_creator(
                    character["persona_key"],
                    user_id,
                    legacy=self.state.legacy_creator_for(user_id),
                )
                creator = self.state.creator_for(character["persona_key"]) == user_id
                changes = await suggest_initial_state(
                    self.context,
                    user_id,
                    scope,
                    character["native_style"] or character["persona_core"],
                    snapshot,
                    self.generate,
                    creator=creator,
                )
                if self.reset_lock.locked() or generation != self.reset_generation:
                    return error_response(
                        "角色状态已重置，请刷新后重试", status_code=409
                    )
                try:
                    self.state.initialize_missing(user_id, scope, changes)
                except KeyError:
                    return error_response("会话记录已不存在", status_code=404)
        return await self.status_response()

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
            await self.terminate()
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
            return await self.status_response()

    async def web_persona_core(self):
        if not request.username:
            return error_response("需要登录 AstrBot 管理面板", status_code=401)
        payload = await request.json()
        core = payload.get("persona_core") if isinstance(payload, dict) else None
        if not isinstance(core, str):
            return error_response("人格核心须为文本")
        if self.reset_lock.locked():
            return error_response("角色状态正在重置，请刷新后再修改", status_code=409)
        character = None
        user_id, scope = payload.get("user_id"), payload.get("scope")
        if user_id is not None or scope is not None:
            if not isinstance(user_id, str) or not isinstance(scope, str):
                return error_response("请选择要编辑的人物")
            character = await self.character_context(user_id, scope)
            if (
                payload.get("persona_key", character["persona_key"])
                != character["persona_key"]
            ):
                return error_response("当前人物已变化，请刷新后再保存", status_code=409)
        native = character and character["persona_core_source"] == "astrbot"
        limit = 32000 if native else 4000
        if len(core) > limit:
            return error_response(f"人格核心须为 {limit} 字以内的文本")
        if self.reset_lock.locked():
            return error_response("角色状态正在重置，请刷新后再修改", status_code=409)
        if native:
            await self.context.persona_manager.update_persona(
                character["persona_id"], system_prompt=core.strip()
            )
            return json_response(
                await self.status_payload(), headers={"Cache-Control": "no-store"}
            )
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
            await self.status_payload(), headers={"Cache-Control": "no-store"}
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
            self.state_updates.clear()
        except (OSError, sqlite3.Error):
            self.config.update(previous)
            try:
                self.config.save_config()
            except OSError as rollback_error:
                logger.error(
                    f"[{PLUGIN_NAME}] 无法恢复重置前的配置（{type(rollback_error).__name__}）"
                )
            raise

    async def status_response(self):
        return json_response(
            await self.status_payload(), headers={"Cache-Control": "no-store"}
        )

    async def status_payload(self):
        style = self.emotion_style()
        rows = self.state.list_status(emotion_style=style)
        return {
            "schema_version": 2,
            "users": [await self.status_row(row) for row in rows],
            "persona_core": self.config.get("persona_core", ""),
            "emotion_default": EMOTION_RULES[style][0],
        }

    async def status_row(self, row: dict, *, character: dict | None = None) -> dict:
        character = character or await self.character_context(
            row["user_id"], row["scope"]
        )
        self.seed_legacy_creator(
            character["persona_key"],
            row["user_id"],
            legacy=row.pop("legacy_creator", False),
        )
        identities = set(row["identities"])
        creator = self.state.creator_for(character["persona_key"]) == row["user_id"]
        if creator:
            identities.add("creator")
        else:
            identities.discard("creator")
        row["identities"] = [key for key in IDENTITY_LABELS if key in identities]
        row["creator"] = creator
        row["state_update"] = self.state_updates.get((row["user_id"], row["scope"]))
        row.update(
            {key: value for key, value in character.items() if key != "native_style"}
        )
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
        # Native mode adds state through hooks and leaves the agent/tool pipeline open.
        if self.config.get("reply_mode", "native") != "standalone":
            return
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
        if self.replies_to_other(event):
            address = "quoted_other"
        if not text and (
            address not in {"explicit", "quoted_other"}
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
        async with self.turn_lock(user_id):
            if self.reset_lock.locked() or generation != self.reset_generation:
                return
            try:
                await self.reply(
                    event,
                    user_id,
                    scope,
                    address,
                    text,
                    native_style=native_style,
                    persona_key=self.character_key(persona_id),
                )
            except Exception as exc:  # noqa: BLE001 - AstrBot's plugin boundary.
                # Provider exceptions can include request data. Keep it out of logs and chat.
                logger.warning(f"[{PLUGIN_NAME}] 对话处理失败（{type(exc).__name__}）")

    def native_enabled(self, event: AstrMessageEvent) -> bool:
        return (
            self.config.get("enabled", True)
            and self.config.get("reply_mode", "native") != "standalone"
            and event.get_platform_name() in QQ_PLATFORMS
            and bool(event.get_sender_id())
            and str(event.get_sender_id()) != str(event.get_self_id())
        )

    @staticmethod
    def replies_to_other(event: AstrMessageEvent) -> bool:
        return not event.is_private_chat() and any(
            isinstance(part, Reply)
            and bool(part.sender_id)
            and str(part.sender_id) != str(event.get_self_id())
            for part in event.get_messages()
        )

    @filter.on_llm_request(priority=-100)
    async def inject_native_state(self, event: AstrMessageEvent, req: ProviderRequest):
        if not self.native_enabled(event) or self.reset_lock.locked():
            return
        try:
            persona_id, native_style, selected_skill = await resolve_native_persona(
                self.context, event
            )
            if (
                self.config.get("activation_mode") == "selected_persona"
                and persona_id != NATIVE_PERSONA_ID
                and not selected_skill
            ):
                return
            generation = self.reset_generation
            user_id = f"{event.get_platform_id()}:{event.get_sender_id()}"
            scope = event.unified_msg_origin
            persona_key = self.character_key(persona_id)
            async with self.turn_lock(user_id):
                if self.reset_lock.locked() or generation != self.reset_generation:
                    return
                self.seed_legacy_creator(
                    persona_key, user_id, legacy=self.state.legacy_creator_for(user_id)
                )
                creator_binding = self.state.creator_for(persona_key)
                creator = creator_binding == user_id
                snapshot = self.state.snapshot(
                    user_id,
                    scope,
                    creator=creator,
                    emotion_style=self.emotion_style(),
                    overrides=self.overrides_for(user_id),
                )
                data = {
                    "creator": creator,
                    "identities": [IDENTITY_LABELS[key] for key in snapshot.identities],
                    "relationship": "创建者"
                    if creator and snapshot.relationship in {"", "初识"}
                    else snapshot.relationship,
                    "affection": snapshot.score,
                    "emotion": snapshot.emotion,
                    "memory": snapshot.memories,
                    "long_term_memory": snapshot.long_term_memory,
                    "attitude": snapshot.attitude,
                    "previous_psychology": snapshot.psychology,
                    "recent_dialogue": snapshot.dialogue,
                    "awaiting_clarification": snapshot.awaiting_clarification,
                    "replying_to_other": self.replies_to_other(event),
                }
                fallback_role = ""
                if not native_style.strip():
                    fallback_role = str(self.config.get("persona_core") or "").strip()
                    if not fallback_role:
                        fallback_role = NATIVE_FALLBACK_ROLE
                        data.update(
                            persona=self.persona_settings(),
                            persona_memory=self.config.get("memory", ""),
                            persona_psychology=self.config.get("psychology", ""),
                        )
                # Retries may reuse the same request; never duplicate this block.
                if not any(
                    getattr(part, "text", "").startswith(CONTEXT_MARKER)
                    for part in req.extra_user_content_parts
                ):
                    req.extra_user_content_parts.append(
                        TextPart(
                            text=native_context(data, fallback_role)
                        ).mark_as_temp()
                    )
                event.set_extra(
                    NATIVE_TURN_KEY,
                    {
                        "user_id": user_id,
                        "scope": scope,
                        "persona_key": persona_key,
                        "creator_binding": creator_binding,
                        "creator": creator,
                        "generation": generation,
                        "role_prompt": native_style or fallback_role,
                        "data": data,
                        "message": event.message_str.strip(),
                    },
                )
        except Exception as exc:  # noqa: BLE001 - Never break other plugins' requests.
            logger.warning(f"[{PLUGIN_NAME}] 角色状态注入失败（{type(exc).__name__}）")

    @filter.on_llm_response(priority=100)
    async def capture_native_response(
        self, event: AstrMessageEvent, response: LLMResponse
    ):
        turn = event.get_extra(NATIVE_TURN_KEY)
        if (
            not turn
            or not self.native_enabled(event)
            or response is None
            or response.role != "assistant"
            or response.is_chunk
            or response.tools_call_name
        ):
            return
        # Observe the final answer; retain its tool calls, rich content and wording unchanged.
        turn["reply"] = response.completion_text or ""

    @filter.after_message_sent(priority=100)
    async def record_native_state(self, event: AstrMessageEvent):
        turn = event.get_extra(NATIVE_TURN_KEY)
        if turn and not turn.get("reply"):
            result = event.get_result()
            if result and result.result_content_type == ResultContentType.LLM_RESULT:
                turn["reply"] = result.get_plain_text().strip()
        if (
            not turn
            or turn.get("recording")
            or not turn.get("reply")
            or not self.native_enabled(event)
            or not getattr(event, "_has_send_oper", False)
        ):
            return
        if len(self.native_tasks) >= 128:
            self.set_update_status(
                turn, "error", "档案更新队列忙，本轮未整理；原记录已保留。"
            )
            return
        turn["recording"] = True
        self.set_update_status(turn, "pending")
        task = asyncio.create_task(self.update_native_state(event, turn))
        self.native_tasks.add(task)
        task.add_done_callback(self.native_tasks.discard)

    def set_update_status(self, turn: dict, phase: str, message: str = "", **values):
        key = (turn["user_id"], turn["scope"])
        self.state_updates[key] = {
            "phase": phase,
            "message": message,
            "at": time.time(),
            **values,
        }
        self.state_updates.move_to_end(key)
        if len(self.state_updates) > 512:
            self.state_updates.popitem(last=False)

    async def extract_native_state(self, scope: str, data: dict) -> StateUpdate:
        instructions = STATE_INSTRUCTIONS
        structured = True
        timeout = max(15, min(180, int(self.config.get("state_update_timeout", 60))))
        for attempt in range(2):
            try:
                raw = await self.generate(
                    scope,
                    instructions,
                    data,
                    timeout=timeout,
                    **(
                        {"response_format": {"type": "json_object"}}
                        if structured
                        else {}
                    ),
                )
            except Exception as exc:
                if (
                    attempt == 0
                    and structured
                    and (
                        isinstance(exc, TypeError)
                        or "response_format" in str(exc).lower()
                        or "json_object" in str(exc).lower()
                    )
                ):
                    structured = False
                    continue
                raise
            try:
                return parse_state_update(raw)
            except (ValueError, TypeError):
                if attempt:
                    raise
                instructions += "\n上次输出未能解析。这次只输出 JSON 对象本身，确保包含 psychology、memory、long_term_memory、category、clarifying。"
        raise ValueError("No state update")

    async def update_native_state(self, event: AstrMessageEvent, turn: dict):
        user_id, scope = turn["user_id"], turn["scope"]
        try:
            async with self.native_update_locks[
                hash(user_id) % len(self.native_update_locks)
            ]:
                if self.native_turn_expired(turn):
                    if turn["generation"] == self.reset_generation:
                        self.set_update_status(
                            turn,
                            "skipped",
                            "人物身份已变化，本轮旧整理结果未应用；下轮按新设置整理。",
                        )
                    return
                self.set_update_status(turn, "pending")
                message_id = str(event.message_obj.message_id or "") or None
                if message_id and self.state.is_recorded(user_id, scope, message_id):
                    self.set_update_status(turn, "updated", "本轮已记录，不重复计分。")
                    return
                # Model work never holds the lock needed by the dashboard or the next reply.
                latest = self.state.snapshot(
                    user_id,
                    scope,
                    creator=turn["creator"],
                    emotion_style=self.emotion_style(),
                )
                current_state = {
                    **turn["data"],
                    "affection": latest.score,
                    "emotion": latest.emotion,
                    "relationship": latest.relationship,
                    "memory": latest.memories,
                    "long_term_memory": latest.long_term_memory,
                    "previous_psychology": latest.psychology,
                    "attitude": latest.attitude,
                    "recent_dialogue": latest.dialogue,
                }
                update = StateUpdate()
                failed = False
                try:
                    async with self.native_update_slots:
                        update = await self.extract_native_state(
                            scope,
                            {
                                "role": turn["role_prompt"],
                                "state": current_state,
                                "message": turn["message"],
                                "sent_reply": turn["reply"],
                            },
                        )
                except Exception as exc:  # noqa: BLE001 - Keep existing memory on provider errors.
                    failed = True
                    logger.warning(
                        f"[{PLUGIN_NAME}] 本轮档案整理失败，原记忆保留（{type(exc).__name__}）"
                    )
                async with self.turn_lock(user_id):
                    if self.native_turn_expired(turn):
                        if turn["generation"] == self.reset_generation:
                            self.set_update_status(
                                turn,
                                "skipped",
                                "人物身份已变化，本轮旧整理结果未应用；下轮按新设置整理。",
                            )
                        return
                    current = self.state.snapshot(
                        user_id,
                        scope,
                        creator=turn["creator"],
                        emotion_style=self.emotion_style(),
                    )
                    memory = (
                        update.memory if current.memories == latest.memories else ""
                    )
                    summary = merge_memory_summary(
                        latest.long_term_memory, memory, update.long_term_memory
                    )
                    result = self.state.record(
                        user_id,
                        scope,
                        message_id,
                        creator=turn["creator"],
                        category=update.category
                        if (current.score, current.emotion)
                        == (latest.score, latest.emotion)
                        else "neutral",
                        memory=memory,
                        psychology=(update.psychology or current.psychology)
                        if current.psychology == latest.psychology
                        else current.psychology,
                        user_text=turn["message"],
                        reply_text=turn["reply"],
                        clarifying=update.clarifying,
                        emotion_style=self.emotion_style(),
                        long_term_memory=summary
                        if current.long_term_memory == latest.long_term_memory
                        else None,
                    )
                    self.set_update_status(
                        turn,
                        "error" if failed else "updated",
                        "本轮整理失败，原记忆已保留；请检查档案整理模型或增加等待时间。"
                        if failed
                        else "",
                        affection_delta=round(result.score - current.score, 2),
                        emotion_delta=result.emotion - current.emotion,
                    )
                if not turn["creator"] and not update.clarifying:
                    await self.send_care(
                        event,
                        user_id,
                        scope,
                        result.milestones,
                        role_prompt=turn["role_prompt"],
                    )
        except Exception as exc:  # noqa: BLE001 - Do not affect later message hooks.
            if turn["generation"] == self.reset_generation:
                self.set_update_status(
                    turn, "error", "本轮档案未能保存，请查看 AstrBot 日志。"
                )
            logger.warning(f"[{PLUGIN_NAME}] 角色状态保存失败（{type(exc).__name__}）")

    def native_turn_expired(self, turn: dict) -> bool:
        return (
            self.reset_lock.locked()
            or turn["generation"] != self.reset_generation
            or self.state.creator_for(turn["persona_key"]) != turn["creator_binding"]
        )

    async def reply(
        self,
        event: AstrMessageEvent,
        user_id: str,
        scope: str,
        address: str,
        text: str,
        *,
        native_style: str = "",
        persona_key: str = "plugin-default",
    ) -> None:
        identities = self.state.identities_for(user_id)
        self.seed_legacy_creator(
            persona_key, user_id, legacy=self.state.legacy_creator_for(user_id)
        )
        creator_binding = self.state.creator_for(persona_key)
        creator = creator_binding == user_id
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
            "identities": [
                IDENTITY_LABELS[key]
                for key in IDENTITY_LABELS
                if (creator if key == "creator" else key in identities)
            ],
            "relationship": relationship or "普通网友",
            "affection": snapshot.score,
            "emotion": snapshot.emotion,
            "emotion_expression": emotion_expression(snapshot.emotion),
            "memory": snapshot.memories,
            "long_term_memory": snapshot.long_term_memory,
            "attitude": snapshot.attitude,
            "previous_psychology": snapshot.psychology,
            "recent_dialogue": [{"user": u, "reply": r} for u, r in recent_dialogue],
            "awaiting_clarification": snapshot.awaiting_clarification,
            "quoted_bot_message": bot_quote[:800],
            "message": text,
            "mention_only": not text,
            "replying_to_other": self.replies_to_other(event),
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
        if self.state.creator_for(persona_key) != creator_binding:
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
            psychology = "主动愿意按创建者的意思推进这件事。"
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
            if conversation.get("replying_to_other"):
                instructions += "\n当前用户引用或回复的是其他人。本轮先按角色语气只问一个简短问题，确认是否在向你说话或指什么；不要自行分析引用内容、代答他人的对话。此项仍服从当前人格核心。"
            raw = await self.generate(scope, instructions, conversation, timeout=45)
            return parse_turn(raw)
        except Exception as exc:  # noqa: BLE001 - Providers raise different error types.
            logger.warning(
                f"[{PLUGIN_NAME}] 模型未返回可用回复（{type(exc).__name__}）"
            )
            return None

    async def generate(
        self, scope: str, instructions: str, data: dict, *, timeout: int, **kwargs
    ) -> str:
        async with asyncio.timeout(timeout):
            provider_id = str(self.config.get("provider_id", "")).strip()
            if not provider_id:
                provider_id = await self.context.get_current_chat_provider_id(umo=scope)
            response = await self.context.llm_generate(
                chat_provider_id=provider_id,
                system_prompt=instructions,
                prompt=json.dumps(data, ensure_ascii=False),
                **kwargs,
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
