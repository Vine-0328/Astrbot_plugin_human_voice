"""Expose the role in AstrBot's native persona selector."""

from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context
from astrbot.core.persona_mgr import DEFAULT_PERSONALITY, SYSTEM_DEFAULT_PERSONA_ID

NATIVE_PERSONA_ID = "角色灵魂拟人"
PERSONA_SKILL_ID = "role-soul-conversation"

NATIVE_SYSTEM_PROMPT = (
    "用自己的性格、态度和情绪自然说话，像熟悉的朋友在 QQ 聊天。"
    "根据之前的对话和记忆理解指代，线索不足时直接问一个简短具体的问题。"
    "表达自己的看法，省掉复述、客服式收尾。面对创建者时，要表现得热情些"
    "闲聊短些，有明确任务就直接答。被问到现实身份时，说明自己被创建者创立的名字和形象。"
)

_ORIGINAL_PLACEHOLDER_PROMPT = (
    "用自己的性格、态度和情绪自然说话，像熟悉的网友在 QQ 聊天。"
    "根据之前的对话和记忆理解指代，线索不足时直接问一个简短具体的问题。"
    "表达自己的看法，省掉复述、客服式收尾和生硬的亲昵称呼。"
    "闲聊短些，有明确任务就直接答。被问到现实身份时，说明自己是 AI 扮演的角色。"
)


async def ensure_native_persona(context: Context) -> None:
    """Register a real persona without overwriting user-edited entries."""
    manager = context.persona_manager
    try:
        await manager.get_persona(NATIVE_PERSONA_ID)
    except ValueError:
        await manager.create_persona(
            persona_id=NATIVE_PERSONA_ID,
            system_prompt=NATIVE_SYSTEM_PROMPT,
            skills=[PERSONA_SKILL_ID],
        )


async def resolve_native_persona(
    context: Context, event: AstrMessageEvent
) -> tuple[str | None, str, bool]:
    """Return the effective native persona ID, prompt and explicit skill selection."""
    return await resolve_scope_persona(
        context,
        f"{event.get_platform_id()}:{event.get_sender_id()}",
        event.unified_msg_origin,
    )


async def resolve_scope_persona(
    context: Context, user_id: str, scope: str
) -> tuple[str | None, str, bool]:
    """Resolve a QQ dashboard row with the live conversation's persona precedence."""
    parts = scope.split(":", 2)
    if len(parts) != 3 or not all(parts):
        return None, "", False
    platform_id, message_type, session_id = parts
    prefix = f"{platform_id}:"
    sender_id = user_id.removeprefix(prefix)
    if not sender_id or (":" in user_id and not user_id.startswith(prefix)):
        return None, "", False

    profile_session = (
        session_id.removeprefix(f"{sender_id}_")
        if message_type == "GroupMessage"
        else session_id
    )
    profile_scope = f"{platform_id}:{message_type}:{profile_session}"
    conversation_manager = context.conversation_manager
    conversation_id = await conversation_manager.get_curr_conversation_id(scope)
    conversation = (
        await conversation_manager.get_conversation(scope, conversation_id)
        if conversation_id
        else None
    )
    conversation_persona_id = conversation.persona_id if conversation else None
    if not conversation_persona_id:
        # Routing happens before AstrBot adds a sender prefix to isolated QQ groups.
        runner = context.get_config(umo=profile_scope).get("agent_runner", {})
        runner_config = runner.get("config", {})
        persona_config = (
            runner_config.get("persona", {})
            if runner.get("runner_type") == "local"
            else runner_config
        )
        conversation_persona_id = persona_config.get(
            "persona_id", SYSTEM_DEFAULT_PERSONA_ID
        )

    manager = context.persona_manager
    persona_id, persona, _, _ = await manager.resolve_selected_persona(
        umo=scope,
        conversation_persona_id=conversation_persona_id,
        # Supported QQ adapters all use the same non-WebChat persona semantics.
        platform_name="",
    )
    if persona is None:
        return ("[%None]" if persona_id == "[%None]" else None), "", False
    skills = persona.get("skills")
    explicitly_enabled = isinstance(skills, list) and PERSONA_SKILL_ID in skills
    prompt = persona.get("prompt") or ""
    if (persona_id, prompt) in (
        (SYSTEM_DEFAULT_PERSONA_ID, DEFAULT_PERSONALITY["prompt"]),
        (NATIVE_PERSONA_ID, _ORIGINAL_PLACEHOLDER_PROMPT),
    ):
        prompt = ""
    return persona_id, prompt, explicitly_enabled
