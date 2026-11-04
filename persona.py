"""Expose the role in AstrBot's native persona selector."""

from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context
from astrbot.core.persona_error_reply import resolve_event_conversation_persona_id
from astrbot.core.persona_mgr import DEFAULT_PERSONALITY, SYSTEM_DEFAULT_PERSONA_ID

NATIVE_PERSONA_ID = "角色灵魂拟人"
PERSONA_SKILL_ID = "role-soul-conversation"

NATIVE_SYSTEM_PROMPT = (
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
    manager = context.persona_manager
    conversation_persona_id = await resolve_event_conversation_persona_id(
        event, context.conversation_manager
    )
    persona_id, persona, _, _ = await manager.resolve_selected_persona(
        umo=event.unified_msg_origin,
        # Migrated conversations may use an empty ID to inherit the profile.
        conversation_persona_id=conversation_persona_id or None,
        platform_name=event.get_platform_name(),
    )
    if persona is None:
        return persona_id, "", False
    skills = persona.get("skills")
    explicitly_enabled = isinstance(skills, list) and PERSONA_SKILL_ID in skills
    prompt = persona.get("prompt") or ""
    if (persona_id, prompt) in (
        (SYSTEM_DEFAULT_PERSONA_ID, DEFAULT_PERSONALITY["prompt"]),
        (NATIVE_PERSONA_ID, NATIVE_SYSTEM_PROMPT),
    ):
        prompt = ""
    return persona_id, prompt, explicitly_enabled
