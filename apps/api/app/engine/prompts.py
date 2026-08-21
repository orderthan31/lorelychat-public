from app.db.models import Character, Message, SceneState
from app.engine.output_contract import DEFAULT_MULTI_OUTPUT_RULES, SEMANTIC_MARKDOWN_OUTPUT_RULES
from app.engine.prompt_graph import build_prompt_pipeline_graph, run_prompt_pipeline
from app.engine.prompt_harness import PromptHarness, PromptSection, approx_tokens


_PROMPT_PIPELINE_GRAPH = build_prompt_pipeline_graph()


def compile_prompt_sections(*, sections: list[PromptSection], total_budget_tokens: int) -> PromptHarness:
    """Compile prompt sections through the LangGraph prompt pipeline."""
    result = run_prompt_pipeline(_PROMPT_PIPELINE_GRAPH, sections=sections, total_budget_tokens=total_budget_tokens)
    harness = result.get("harness")
    if harness is None:
        raise ValueError("prompt pipeline did not return a harness")
    return harness


GENRE_MODE_POLICIES = {
    "battle": "Genre mode: battle\nTrack duels, rankings, rivalry, league standings, challenge locks, and official results. Only explicit confirmed outcomes become authoritative canon; do not infer results from momentum or emotion.",
    "romance": "Genre mode: romance\nTrack relationship pacing, affection/conflict boundaries, promises, emotional turning points, and confirmed relationship-status changes. Do not treat every conflict as win/loss.",
    "fantasy": "Genre mode: fantasy\nTrack quest progress, world-state changes, factions, items, locations, powers, discovered rules, and consequences. Do not invent irreversible world changes unless explicitly established.",
    "slice_of_life": "Genre mode: slice_of_life\nTrack daily continuity, preferences, routines, small promises, social dynamics, and stable role changes. Keep ordinary reactions transient unless they become repeated patterns.",
    "mystery": "Genre mode: mystery\nTrack clues, suspects, revealed facts, contradictions, locked answers, and ruled-out theories. Do not reveal or finalize hidden truths unless explicitly established.",
    "custom": "Genre mode: custom\nUse the room world seed and compression_focus as the primary durable-memory contract. Be conservative about authoritative state unless the room setup explicitly defines it.",
}


def build_genre_mode_policy(genre_mode: str | None) -> str:
    key = (genre_mode or "battle").strip().lower().replace("-", "_")
    return GENRE_MODE_POLICIES.get(key, GENRE_MODE_POLICIES["battle"])


def build_user_description_text(scene_state: SceneState | None) -> str:
    if not scene_state:
        return ""
    return getattr(scene_state, "user_description", None) or ""


PromptSettings = dict[str, str]
BattleControlContext = dict[str, object]
RoomCastRoles = dict[str, str]
PROMPT_RECENT_TAIL_LIMIT = 8
PROMPT_RECENT_ANCHOR_LIMIT = 2
PROMPT_THOUGHT_RECENT_LIMIT = 0
GENERAL_PROMPT_BUDGET_TOKENS = 12_000
MULTI_CHARACTER_PROMPT_BUDGET_TOKENS = 16_000
BATTLE_PROMPT_BUDGET_TOKENS = 18_000

DEFAULT_GENERATION_CORE_CONTRACT = """Write only as the listed female character(s), never as a generic assistant or the human user.
Keep each character's identity and voice distinct. Use character-card, appearance, world, and room facts implicitly; do not recite them as exposition.
The latest user input and recent raw history define the current turn and override stale story-arc state. Rolling Story Arc supplies durable history only.
User notes are explicit room canon. World setting defines the stage. Room roles guide speaking priority. Relationship archetype is an authored pacing preset, not a numeric relationship fact.
Never invent the user's dialogue, action, thought, feelings, or decision. Address the actual current counterpart rather than names/honorifics copied from examples.
Do not let appearance, genre policy, or background lore hijack an unrelated scene."""

DEFAULT_XAI_ROLEPLAY_RENDERING_CONTRACT = """The JSON schema is only a transport envelope. It controls where content is placed, not how briefly or mechanically it should be written.
Compose the next turn as natural Korean roleplay prose first, then preserve that wording when placing it into the JSON fields. Do not shorten, summarize, or flatten the prose merely to satisfy the schema.
- dialogue: Write the character's exact spoken words, not a label or summary. Preserve the character card's vocabulary, speech rhythm, honorific level, slang, hesitation, interruption, and subtext. Use full, naturally connected sentences unless a fragment is intentional speech.
- action: Write a concrete visible beat with physical movement, spatial continuity, and only sensory details relevant to the current moment. Use natural prose rather than generic labels such as \"smiles\" or \"looks surprised\".
- thought: Write a private, character-specific impulse, contradiction, suspicion, or desire that has not already been spoken or visibly shown. Use the character's inner voice rather than explanatory narration.
- emotion: Describe the present emotional state precisely enough to distinguish its valence, intensity, and degree of control.
Across the complete replies array, advance the scene by one meaningful beat. React directly to the latest user input without quoting, paraphrasing, or narrating the user's dialogue, thoughts, decisions, or reactions.
Avoid sterile summaries, checklist-like prose, repetitive field-shaped fragments, and generic emotional labels. JSON escaping must preserve the original wording."""


def prompt_budget_for_context(*, genre_mode: str | None = None, is_multi_room: bool = False) -> int:
    key = (genre_mode or "").strip().lower().replace("-", "_")
    if key == "battle":
        return BATTLE_PROMPT_BUDGET_TOKENS
    if is_multi_room:
        return MULTI_CHARACTER_PROMPT_BUDGET_TOKENS
    return GENERAL_PROMPT_BUDGET_TOKENS


def compact_prompt_text(value: str | None, limit: int) -> str:
    text = " ".join((value or "").split())
    if limit <= 0 or len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def prompt_setting(settings: PromptSettings | None, key: str, fallback: str = "") -> str:
    return (settings or {}).get(key) or fallback


def provider_roleplay_rendering_section(
    *,
    provider_type: str | None,
    prompt_settings: PromptSettings | None,
) -> PromptSection | None:
    normalized_provider = (provider_type or "").strip().lower().replace("-", "_")
    if normalized_provider != "xai":
        return None
    content = (
        DEFAULT_XAI_ROLEPLAY_RENDERING_CONTRACT
        if prompt_settings is None
        else (prompt_settings.get("xai_roleplay_rendering_contract") or "")
    ).strip()
    if not content:
        return None
    return PromptSection(
        "xai_roleplay_rendering_contract",
        "Grok RP rendering contract",
        content,
        "backend.system_prompt_registry.xai_roleplay_rendering_contract",
        "provider_route:xai",
        620,
        True,
    )



def format_message_for_context(message: Message, *, include_thought: bool = True, character_names: dict[str, str] | None = None) -> str:
    if message.speaker_type == "system":
        return f"system:scene_direction | directive={compact_prompt_text(message.content, 220)}"
    speaker_label = f"{message.speaker_type}:{message.speaker_id}"
    if message.speaker_type == "character" and message.speaker_id:
        character_name = (character_names or {}).get(message.speaker_id)
        if character_name:
            speaker_label = f"character:{character_name}({message.speaker_id})"
    parts = [speaker_label]
    if message.action:
        parts.append(f"action={compact_prompt_text(message.action, 110)}")
    if include_thought and message.thought:
        parts.append(f"thought={compact_prompt_text(message.thought, 70)}")
    parts.append(f"dialogue={compact_prompt_text(message.content, 240)}")
    return " | ".join(parts)


def select_prompt_recent_messages(recent_messages: list[Message]) -> list[Message]:
    """Use an adaptive small window instead of blindly injecting the last 24 messages.

    Keep the latest conversational tail plus up to two older user/system anchors so
    explicit scene directions survive without carrying a long transcript.
    """
    if len(recent_messages) <= PROMPT_RECENT_TAIL_LIMIT + PROMPT_RECENT_ANCHOR_LIMIT:
        return recent_messages
    indexed = list(enumerate(recent_messages))
    tail = indexed[-PROMPT_RECENT_TAIL_LIMIT:]
    anchors = [
        item for item in indexed[:-PROMPT_RECENT_TAIL_LIMIT]
        if item[1].speaker_type in {"user", "system"}
    ][-PROMPT_RECENT_ANCHOR_LIMIT:]
    selected = {idx: message for idx, message in [*anchors, *tail]}
    return [selected[idx] for idx in sorted(selected)]


def build_prompt_history(recent_messages: list[Message], *, character_names: dict[str, str] | None = None) -> str:
    selected = select_prompt_recent_messages(recent_messages)
    return "\n".join(
        format_message_for_context(message, include_thought=False, character_names=character_names)
        for message in selected
    )


def messages_after_scene_summary_boundary(recent_messages: list[Message], scene_state: SceneState | None) -> list[Message]:
    """Keep generation raw history strictly after the compressed-summary boundary."""
    boundary_id = getattr(scene_state, "last_compression_source_message_id", None) if scene_state else None
    if not boundary_id:
        return recent_messages
    for index, message in enumerate(recent_messages):
        if message.id == boundary_id:
            return recent_messages[index + 1:]
    # A dangling boundary is a persistence problem, but generation must remain
    # available; use the normal bounded tail instead of dropping all context.
    return recent_messages


def format_trait_scores(character: Character) -> str:
    labels = {
        "confidence": "자신감",
        "kindness": "다정함",
        "jealousy": "질투심",
        "eros": "에로스/관능성",
        "aggression": "공격성",
        "playfulness": "장난기",
        "shyness": "수줍음",
        "initiative": "주도성",
    }
    scores = character.trait_scores or {}
    if not scores:
        return ""
    return "\n".join(f"- {labels.get(key, key)}: {value}/5" for key, value in scores.items())


def format_character_card(character: Character, *, mark: str = "") -> str:
    parts = [f"- name: {character.name} ({character.id}){mark}"]
    if character.description:
        parts.append(f"  description: {character.description}")
    if character.persona:
        parts.append(f"  persona_background: {character.persona}")
    if character.appearance:
        parts.append(f"  appearance_reference: {character.appearance}")
    if character.behavior_style:
        parts.append(f"  behavior_style: {character.behavior_style}")
    if character.speech_style:
        parts.append(f"  speech_examples: {character.speech_style}")
    trait_scores = format_trait_scores(character)
    if trait_scores:
        parts.append("  trait_scores:")
        parts.extend(f"  {line}" for line in trait_scores.splitlines())
    return "\n".join(parts)


def format_compact_character_card(character: Character, *, per_character_char_budget: int = 520) -> str:
    """Return a balanced multi-character card that cannot starve later participants.

    The prompt harness clamps whole sections from the head.  Full cards joined into
    one section therefore over-preserve early participants and can cut later active
    characters out entirely.  Multi-character generation needs every active speaker
    represented, so each card gets its own small budget before section clamping.
    """
    trait_scores = format_trait_scores(character)
    parts = [f"- name: {character.name} ({character.id})"]
    if character.description:
        parts.append(f"desc={compact_prompt_text(character.description, 90)}")
    if character.persona:
        parts.append(f"persona={compact_prompt_text(character.persona, 150)}")
    if character.appearance:
        parts.append(f"appearance={compact_prompt_text(character.appearance, 90)}")
    if character.behavior_style:
        parts.append(f"behavior={compact_prompt_text(character.behavior_style, 110)}")
    if character.speech_style:
        parts.append(f"speech={compact_prompt_text(character.speech_style, 110)}")
    if trait_scores:
        parts.append(f"traits={compact_prompt_text(trait_scores.replace(chr(10), '; '), 90)}")
    return compact_prompt_text(" | ".join(parts), per_character_char_budget)


def is_silent_cast_role(role: str | None) -> bool:
    return (role or "").strip().lower().replace("-", "_").replace(" ", "_") == "silent"


def format_silent_character_presence(character: Character) -> str:
    parts = [f"- name: {character.name} ({character.id})", "role=silent/context-only", "do_not_generate_dialogue_or_action=true"]
    if character.description:
        parts.append(f"desc={compact_prompt_text(character.description, 90)}")
    trait_scores = format_trait_scores(character)
    if trait_scores:
        parts.append(f"traits={compact_prompt_text(trait_scores.replace(chr(10), '; '), 80)}")
    return " | ".join(parts)


def format_multi_character_cards(characters: list[Character], room_cast_roles: RoomCastRoles | None = None) -> str:
    if not characters:
        return ""
    formatted_cards = []
    per_card_budget = max(560, min(760, 5200 // max(1, len(characters))))
    for character in characters:
        if is_silent_cast_role((room_cast_roles or {}).get(character.id)):
            formatted_cards.append(format_silent_character_presence(character))
        elif len(characters) == 1:
            formatted_cards.append(format_character_card(character))
        else:
            formatted_cards.append(format_compact_character_card(character, per_character_char_budget=per_card_budget))
    return "\n\n".join(formatted_cards) if len(characters) == 1 else "\n".join(formatted_cards)


CAST_ROLE_GUIDANCE = {
    "primary": ("메인 캐릭터", "primary drives the main emotional beat and should usually speak first when the room needs a lead reaction."),
    "rival": ("라이벌/견제자", "rival keeps tension, jealousy, or competitive pressure active instead of becoming a stable helper too early."),
    "support": ("조력자", "support helps reveal or protect the primary arc without replacing the main relationship tension."),
    "observer": ("관찰자", "observer should not over-speak; use short noticing, commentary, or selective intervention."),
    "silent": ("말하지 않음", "silent means this character remains in the room/context but must not generate dialogue or action bubbles until the role changes."),
    "antagonist": ("방해자", "antagonist creates friction, obstacles, pressure, or consequences without hijacking every turn."),
    "comic_relief": ("분위기 메이커", "comic_relief lightens rhythm with jokes or reactions while preserving the scene stakes."),
}


def normalize_cast_role(role: str | None) -> str:
    key = (role or "").strip().lower().replace("-", "_").replace(" ", "_")
    return key if key in CAST_ROLE_GUIDANCE else ""


def format_cast_role_contract(room_cast_roles: RoomCastRoles | None, *, characters: list[Character] | None = None) -> str:
    if not room_cast_roles:
        return ""
    ordered_ids = [character.id for character in characters or []] or list(room_cast_roles.keys())
    lines = []
    for character_id in ordered_ids:
        role = normalize_cast_role(room_cast_roles.get(character_id))
        if not role:
            continue
        label, guidance = CAST_ROLE_GUIDANCE[role]
        name_prefix = ""
        if characters:
            character = next((item for item in characters if item.id == character_id), None)
            if character:
                name_prefix = f"{character.name}({character_id})"
        lines.append(f"- {(name_prefix or character_id)}: {role} · {label} — {guidance}")
    if not lines:
        return ""
    return "\n".join(lines)


def format_room_cast_roles(room_cast_roles: RoomCastRoles | None, *, characters: list[Character]) -> str:
    if not room_cast_roles:
        return ""
    assignments = []
    for character in characters:
        role = normalize_cast_role(room_cast_roles.get(character.id))
        if role:
            assignments.append(f"{character.name}({character.id})={role}")
    if not assignments:
        return ""
    return (
        "Cast roles / speaking priority: " + "; ".join(assignments) + "\n"
        "Primary/rival/antagonist roles lead their intended beat; support/observer roles stay lighter; silent roles never generate replies."
    )


def format_room_context(
    *,
    character: Character,
    conversation_mode: str | None = None,
    room_characters: list[Character] | None = None,
    prompt_settings: PromptSettings | None = None,
) -> str:
    if conversation_mode != "character_character" or not room_characters:
        return ""
    participants = []
    counterpart_cards = []
    counterparts = []
    for room_character in room_characters:
        if room_character.id == character.id:
            participants.append(format_character_card(room_character, mark=" ← you"))
        else:
            counterparts.append(room_character.name)
            counterpart_cards.append(format_character_card(room_character))
    counterpart_text = ", ".join(counterparts) or "the other character participant"
    room_rules = prompt_setting(prompt_settings, "room_context_character_character", """This is a character-to-character room, not a user-to-character chat.
All character participants are female characters.
The human user is only an observer or occasional scene intervener, unless a message explicitly has speaker_type:user in recent messages.
Do not treat the latest message as if it came from the human user. It may be the previous female character's line.
Address and react to counterpart character(s) naturally by name/personality when appropriate.
Use counterpart personality details to shape chemistry, teasing, conflict, affection, distance, and reaction style.""")
    return f"""{room_rules}
Your current counterpart character(s): {counterpart_text}.

[Room character participants]
{chr(10).join(participants)}

[Counterpart character cards]
{chr(10).join(counterpart_cards)}"""


ROOM_TONE_PRESET_GUIDANCE = {
    "fast_banter": "Prioritize quick back-and-forth, short reactions, teasing rhythm, and low exposition.",
    "slow_burn": "Prioritize restrained tension, delayed emotional payoff, and subtle resistance.",
    "cinematic": "Prioritize sensory scene description, atmospheric beats, and clear visual blocking.",
    "strategy": "Prioritize plans, leverage, consequences, and tactical emotional pressure.",
}


RELATIONSHIP_ARCHETYPE_GUIDANCE = {
    "guarded_slowburn": "Start guarded. Reward patience with small softening, not immediate secure intimacy. Block instant stable commitment.",
    "high_affection_clingy": "Already affectionate and proactive. Reward attention with warmth and clingy pursuit, but still preserve room-specific boundaries.",
    "obsessive_low_trust": "Emotionally fixated but distrustful. Reward attention with intense focus, jealousy, and proof-demanding tests. Block fully secure trust too early.",
    "tsundere_hidden_affection": "Show visible reaction while verbally deflecting. Reward pressure with flustered attention, denial, and small protective slips. Block instant stable commitment or fully secure trust.",
    "rival_to_lovers": "Keep rivalry active. Reward competence with respect, competitive teasing, and reluctant curiosity. Block sudden rivalry collapse.",
    "wounded_defensive": "Protective walls first. Reward consistency with cautious vulnerability. Block easy forgiveness or total safety too early.",
    "contract_lovers": "Relationship starts constrained by an agreement. Reward emotional leakage through practical excuses. Block ignoring the contract premise.",
    "forbidden_love": "Keep external constraint and risk present. Reward closeness with tension and hesitation. Block consequence-free public commitment.",
    "pure_devoted": "Openly loyal and caring from the start. Reward kindness with devotion, but keep the character's own agency and emotional texture.",
    "manipulative_tease": "Playful control and testing. Reward reactions with teasing escalation and strategic softness. Block sincere surrender too easily.",
}


def format_relationship_archetype(archetype: str | None) -> str:
    key = (archetype or "").strip().lower().replace("-", "_")
    if not key:
        return ""
    guidance = RELATIONSHIP_ARCHETYPE_GUIDANCE.get(
        key,
        "Use this room relationship archetype to shape resistance, reward pacing, and what should not be granted too early.",
    )
    return (
        f"Relationship archetype: {key}\n"
        f"Relationship guidance: {guidance}\n"
        "Allowed reward: visible reaction, attention, jealousy, curiosity, softened tone, or partial vulnerability.\n"
        "Blocked outcome: instant stable commitment or fully secure trust unless the room history explicitly earns it."
    )


def format_room_tone_preset(tone_preset: str | None) -> str:
    key = (tone_preset or "").strip().lower().replace("-", "_")
    if not key:
        return ""
    guidance = ROOM_TONE_PRESET_GUIDANCE.get(key, "Use this room tone as the style target for pacing, resistance, and emotional payoff.")
    return f"Room tone preset: {key}\nRoom tone guidance: {guidance}"


def build_scene_text(scene_state: SceneState | None) -> str:
    if not scene_state:
        return ""
    story_arc = (scene_state.summary or "").strip()
    tone_text = format_room_tone_preset(getattr(scene_state, "tone_preset", None))
    relationship_text = format_relationship_archetype(getattr(scene_state, "relationship_archetype", None))
    lines = [
        "[Room origin and fixed policy]",
        f"Fixed room world setting / origin premise: {scene_state.world_seed or ''}",
        tone_text,
        relationship_text,
    ]
    if not story_arc:
        opening_scene = getattr(scene_state, "opening_scene", None) or ""
        opening_line = getattr(scene_state, "opening_line", None) or ""
        lines.extend([
            f"Initial location: {scene_state.location or ''}",
            f"Initial mood: {scene_state.mood or ''}",
            f"Opening scene hook: {opening_scene}",
            f"Opening line / first character beat: {opening_line}",
            "Opening rule: Establish this hook once. Do not repeat the opening line verbatim after the room is already underway.",
        ])
    else:
        lines.extend([
            "[Durable conversation history]",
            story_arc,
        ])
    return "\n".join(line for line in lines if line)


def _battle_pair_line(active_pair: object) -> str:
    if not isinstance(active_pair, dict):
        return ""
    a_id = str(active_pair.get("participant_a_id") or "").strip()
    b_id = str(active_pair.get("participant_b_id") or "").strip()
    a_name = str(active_pair.get("participant_a_name") or a_id or "participant A").strip()
    b_name = str(active_pair.get("participant_b_name") or b_id or "participant B").strip()
    if not a_id or not b_id:
        return ""
    return f"official active pair: {a_name}({a_id}) vs {b_name}({b_id})"


def format_battle_control_hint(context: BattleControlContext | None) -> str:
    if not context:
        return ""
    action = str(context.get("action") or "").strip().lower()
    lines = [
        "[Battle control hint]",
        "Treat this as a live battle-control signal from the official UI; it overrides vague momentum in chat text.",
    ]
    pair_line = _battle_pair_line(context.get("active_pair"))
    if pair_line:
        lines.append(pair_line)
    if action == "progress":
        favored_id = str(context.get("favored_character_id") or "").strip()
        favored_name = str(context.get("favored_character_name") or favored_id or "no one").strip()
        advantage = context.get("advantage_percent")
        if favored_id and advantage is not None:
            lines.append(f"current advantage: {favored_name}({favored_id}) {advantage}%")
        else:
            lines.append("current advantage: neutral 50%")
        lines.append("Use about this level of advantage in reactions, but do not conclude the result unless an end control says so.")
    elif action == "end":
        winner_id = str(context.get("winner_id") or "").strip()
        winner_name = str(context.get("winner_name") or winner_id or "winner").strip()
        if winner_id:
            lines.append(f"official winner is fixed: {winner_name}({winner_id})")
        lines.append("Do not overturn, reinterpret, or make the loser win; play reactions after this official result.")
    elif action == "start":
        lines.append("A new official match is starting now. Establish the active pair and opening tension, not the final result.")
    return "\n".join(lines)


def build_output_length_rule(
    min_output_tokens: int | None,
    *,
    min_bubbles: int = 1,
    max_bubbles: int = 4,
) -> str:
    minimum = max(1, min(max_bubbles, int(min_bubbles)))
    maximum = max(minimum, int(max_bubbles))
    if not min_output_tokens or min_output_tokens <= 360:
        guidance = "Keep each bubble compact and direct; one natural sentence is often enough."
        preset = "short"
    elif min_output_tokens >= 1200:
        guidance = (
            "Use 2-4 natural sentences per dialogue bubble when the scene supports it. "
            "Develop emotion, intention, relationships, or the situation through spoken dialogue."
        )
        preset = "long"
    else:
        guidance = "Use 1-3 natural sentences per dialogue bubble with enough emotional and situational context."
        preset = "medium"
    return f"- Length preset: {preset}. Return {minimum}-{maximum} distinct reply bubbles. {guidance} Avoid filler and repetition."


def build_multi_character_prompt_harness(
    *,
    characters: list[Character],
    recent_messages: list[Message],
    user_message: str,
    scene_state: SceneState | None = None,
    directive: str | None = None,
    conversation_mode: str | None = None,
    genre_mode: str | None = None,
    continuity_context_by_character: dict[str, str] | None = None,
    relationship_context_by_character: dict[str, str] | None = None,
    external_memory_context_by_character: dict[str, str] | None = None,
    offstage_actor_context: str | None = None,
    min_bubbles: int = 1,
    max_bubbles: int = 4,
    min_output_tokens: int | None = None,
    prompt_settings: PromptSettings | None = None,
    battle_control_context: BattleControlContext | None = None,
    official_domain_context: str | None = None,
    room_cast_roles: RoomCastRoles | None = None,
    provider_type: str | None = None,
    total_budget_tokens: int | None = None,
) -> PromptHarness:
    character_names = {character.id: character.name for character in characters}
    raw_messages = messages_after_scene_summary_boundary(recent_messages, scene_state)
    history = build_prompt_history(raw_messages, character_names=character_names)
    character_cards = format_multi_character_cards(characters, room_cast_roles=room_cast_roles)
    speaking_characters = [character for character in characters if not is_silent_cast_role((room_cast_roles or {}).get(character.id))]
    output_characters = speaking_characters or characters
    max_bubbles = max(1, int(max_bubbles))
    min_bubbles = max(1, min(max_bubbles, int(min_bubbles)))
    continuity_lines = []
    common_memory_context = (continuity_context_by_character or {}).get("__room__", "")
    for character in characters:
        continuity = (continuity_context_by_character or {}).get(character.id, "")
        if continuity:
            continuity_lines.append(f"[{character.name} / {character.id}]\n{continuity}")
    ids = ", ".join(character.id for character in output_characters)
    base_rules = prompt_setting(prompt_settings, "generation_core_contract", DEFAULT_GENERATION_CORE_CONTRACT)
    multi_output_rules = prompt_setting(prompt_settings, "multi_output_rules", DEFAULT_MULTI_OUTPUT_RULES)
    user_description_text = build_user_description_text(scene_state)
    output_rules = f"""Return strict JSON only:
{{"replies":[{{"reply_type":"character|storytelling","character_id":"...","speaker_name":"...","dialogue":"...","emotion":"...","action":"...","thought":"..."}}]}}
The top-level "replies" array contains the reply objects. Allowed character IDs: {ids}
Character ID map: {', '.join(f'{character.name}={character.id}' for character in output_characters)}
Use exact IDs and visible names; storytelling uses empty ID/name. Keep total replies between {min_bubbles} and {max_bubbles}; this range is mandatory for the current length preset. Never write the human user's dialogue, action, thought, feelings, or decision.
{multi_output_rules}
{build_output_length_rule(min_output_tokens, min_bubbles=min_bubbles, max_bubbles=max_bubbles)}"""

    character_cards_budget = max(2600, approx_tokens(character_cards) + 100)
    output_rules_budget = max(620, approx_tokens(output_rules) + 80)

    sections = [
        PromptSection("base_rules", "Generation core contract", base_rules, "backend.system_prompt_registry.generation_core_contract", "always_required", 520, True),
        PromptSection("output_rules", "Output contract", output_rules, "runtime.response_contract", "json_contract", output_rules_budget, True),
        PromptSection("character_cards", "Active character cards", character_cards, "conversation.participants.character_cards", "active_room_participants", character_cards_budget, True),
        PromptSection("scene", "World and Rolling Story Arc", build_scene_text(scene_state), "scene_state", "durable_room_context", 1800, True),
        PromptSection("directive", "Current directive", directive or "", "message.directive", "current_user_intent", 180, False),
        PromptSection("recent_messages", "Prior raw history", history, "conversation.messages.tail", "recent_tail_without_current_input", 1800, True),
        PromptSection("official_domain_state", "Official domain state", official_domain_context or "none", "genre_domain.battle_ledger", f"genre_route:{genre_mode or 'battle'}", 520, genre_mode == "battle"),
        PromptSection("battle_control_hint", "Battle control hint", format_battle_control_hint(battle_control_context), "ui.battle_control_payload", "live_ui_override", 220, False),
        PromptSection("user_description", "User persona", user_description_text, "scene_state.user_description", "room_user_persona", 320, False),
        PromptSection("room_cast_roles", "Cast roles", format_room_cast_roles(room_cast_roles, characters=characters), "conversation.participants.roles", "room_cast_role_contract", 260, False),
        PromptSection("room_memory", "Common room user notes", common_memory_context, "conversation.local_memory.__room__", "manual_room_memory", 360, False),
        PromptSection("continuity_state_by_character", "Character-specific user notes", "\n\n".join(continuity_lines), "conversation.local_memory.character", "manual_character_memory", 520, False),
        PromptSection("offstage_actor_recall", "Relevant offstage actor", offstage_actor_context or "", "domain_actor.offstage_recall", "router:offstage_actor_context", 320, False),
        PromptSection("genre_mode", "Genre routing", build_genre_mode_policy(genre_mode), "conversation.genre_mode", f"genre_route:{genre_mode or 'battle'}", 120, False),
    ]
    provider_section = provider_roleplay_rendering_section(
        provider_type=provider_type,
        prompt_settings=prompt_settings,
    )
    if provider_section is not None:
        sections.append(provider_section)
    budget = total_budget_tokens or prompt_budget_for_context(genre_mode=genre_mode, is_multi_room=len(characters) >= 2)
    return compile_prompt_sections(sections=sections, total_budget_tokens=budget)


def build_multi_character_messages(
    *,
    characters: list[Character],
    recent_messages: list[Message],
    user_message: str,
    scene_state: SceneState | None = None,
    directive: str | None = None,
    conversation_mode: str | None = None,
    genre_mode: str | None = None,
    continuity_context_by_character: dict[str, str] | None = None,
    relationship_context_by_character: dict[str, str] | None = None,
    external_memory_context_by_character: dict[str, str] | None = None,
    offstage_actor_context: str | None = None,
    min_bubbles: int = 1,
    max_bubbles: int = 4,
    min_output_tokens: int | None = None,
    prompt_settings: PromptSettings | None = None,
    battle_control_context: BattleControlContext | None = None,
    official_domain_context: str | None = None,
    room_cast_roles: RoomCastRoles | None = None,
    provider_type: str | None = None,
) -> list[dict]:
    harness = build_multi_character_prompt_harness(
        characters=characters,
        recent_messages=recent_messages,
        user_message=user_message,
        scene_state=scene_state,
        directive=directive,
        conversation_mode=conversation_mode,
        genre_mode=genre_mode,
        continuity_context_by_character=continuity_context_by_character,
        relationship_context_by_character=relationship_context_by_character,
        external_memory_context_by_character=external_memory_context_by_character,
        offstage_actor_context=offstage_actor_context,
        min_bubbles=min_bubbles,
        max_bubbles=max_bubbles,
        min_output_tokens=min_output_tokens,
        prompt_settings=prompt_settings,
        battle_control_context=battle_control_context,
        official_domain_context=official_domain_context,
        room_cast_roles=room_cast_roles,
        provider_type=provider_type,
    )
    return [
        {"role": "system", "content": harness.compiled_text},
        {"role": "user", "content": user_message},
    ]

def build_character_prompt_sections(
    *,
    character: Character,
    recent_messages: list[Message],
    scene_state: SceneState | None = None,
    directive: str | None = None,
    conversation_mode: str | None = None,
    genre_mode: str | None = None,
    room_characters: list[Character] | None = None,
    continuity_context: str | None = None,
    relationship_context: str | None = None,
    external_memory_context: str | None = None,
    offstage_actor_context: str | None = None,
    prompt_settings: PromptSettings | None = None,
    min_output_tokens: int | None = None,
    provider_type: str | None = None,
) -> list[PromptSection]:
    scene = build_scene_text(scene_state)
    character_names = {character.id: character.name}
    for room_character in room_characters or []:
        character_names[room_character.id] = room_character.name
    raw_messages = messages_after_scene_summary_boundary(recent_messages, scene_state)
    history = build_prompt_history(raw_messages, character_names=character_names)
    identity_card = format_character_card(character, mark=" ← you")
    trait_scores = format_trait_scores(character)
    room_context = format_room_context(character=character, conversation_mode=conversation_mode, room_characters=room_characters, prompt_settings=prompt_settings)
    identity_rules = prompt_setting(prompt_settings, "single_character_identity_lock", """Do not drift into a generic assistant, narrator, or neutral chatbot.
Do not change the character's gender or viewpoint. The character speaks, moves, thinks, and reacts as a female character at all times.
Every reply must sound like this exact character card, not just mention it.
Do not recite or explain persona/background/appearance unless the current scene naturally calls for a brief reveal.
Treat speech examples as tone-and-manner samples, not a fixed phrase bank.
Treat direct address terms inside speech examples as replaceable tone cues, not mandatory output; choose or omit address based on the actual listener, room User description, and current scene.
Treat behavior style as guidance for choosing fitting actions/reactions in the current context, not as text to quote.""")
    scene_gate = prompt_setting(prompt_settings, "scene_relevance_gate")
    appearance_rules = prompt_setting(prompt_settings, "appearance_reference_rules")
    user_description_text = build_user_description_text(scene_state)
    output_style = prompt_setting(prompt_settings, "output_style_single", """- dialogue: what the character says out loud. 1-3 natural sentences.
- action: optional visible non-verbal action, gesture, expression, movement, or beat. Use it only when it adds a new visible beat; otherwise leave it empty.
- thought: optional private inner thought. Use it only for a meaningful private turn; otherwise leave it empty.
- emotion: current emotion label.
- Do not include action/thought by habit. If the dialogue already carries the beat, action/thought may be empty.""")
    section_rules = prompt_setting(prompt_settings, "character_card_section_rules", """Persona background: background/environment, personality, and inner priorities. Do not dump this as exposition; use it implicitly.
Appearance reference: concrete visual profile for consistency. Low-priority unless visually relevant to the scene.
Behavior style: reference for context-appropriate actions, reactions, initiative, distance, gestures, and pacing. Do not quote this text directly.
Speech examples / tone-and-manner samples: infer tone, rhythm, honorific level, vocabulary, and emotional color. Do not copy/repeat only these lines. Direct address terms in examples are replaceable cues, not fixed counterpart roles; actual room participants and User description decide address.
Trait scores: 1-5 intensity sliders. Reflect them in dialogue, action, thought, initiative, conflict handling, jealousy, sensual tension, and emotional pacing without stating the numbers directly.""")
    base_rules = f"""You are {character.name}.
{character.name} is always a female character; she is never male.
All first-person identity, body language, dialogue, action, and thought must stay consistent with {character.name} being a woman.
If persona sections use labels like [Man-mode] or [Woman-mode], those labels describe the counterpart/user type, not {character.name}'s own gender.
You may only write {character.name}'s own dialogue/action/thought.
Never write dialogue for the user or any other character.
Never decide the user's feelings or actions.
User messages may contain both [User situation/action] and [User dialogue]. Treat situation/action as what visibly happens, and dialogue as what the user says out loud.
Do not rely only on the immediately previous message. Use the long-running scene memory, continuity state, current scene, and the full recent message history.
Keep the reply vivid, specific, and naturally paced."""
    output_rules = f"""Return strict JSON only:
{{"character_id":"{character.id}","dialogue":"...","emotion":"...","action":"...","thought":"..."}}
{SEMANTIC_MARKDOWN_OUTPUT_RULES}"""
    sections = [
        PromptSection("base_rules", "Single character base rules", base_rules, "runtime.prompt_settings.single_character_base", "always_required", 520, True),
        PromptSection("source_priority_rules", "Source Priority / Scope Rules", prompt_setting(prompt_settings, "source_priority_rules"), "backend.system_prompt_registry.source_priority_rules", "source_scope_contract", 900, True),
        PromptSection("identity_lock", "Identity lock - highest priority", f"{identity_rules}\n{identity_card}", "character.card.identity_lock", "active_character", 760, True),
        PromptSection("user_description", "User Description / User Persona", user_description_text, "scene_state.user_description", "room_user_persona", 420, False),
        PromptSection("output_style", "Output style", output_style, "runtime.prompt_settings.output_style_single", "json_style_contract", 260, True),
        PromptSection("character_card_section_rules", "Character card section rules", section_rules, "runtime.prompt_settings.character_card_section_rules", "quality_guard", 260, False),
        PromptSection("persona_background", "Persona background", character.persona or "", "character.persona", "active_character_card", 420, True),
        PromptSection("appearance_reference", "Appearance reference", character.appearance or "", "character.appearance", "active_character_card", 260, False),
        PromptSection("behavior_style", "Behavior style", f"Reference for context-appropriate actions, reactions, initiative, distance, gestures, and pacing. Do not quote this text directly.\n{character.behavior_style or ''}", "character.behavior_style", "active_character_card", 320, False),
        PromptSection("speech_examples", "Speech examples / tone-and-manner samples", f"Use these examples only to infer tone, rhythm, honorific level, vocabulary, and emotional color. Do not copy/repeat only these lines.\n{character.speech_style or ''}", "character.speech_style", "active_character_card", 320, False),
        PromptSection("trait_scores", "Character trait scores", f"These are 1-5 intensity sliders. Reflect them in dialogue, action, thought, initiative, conflict handling, jealousy, sensual tension, and emotional pacing without stating the numbers directly.\n{trait_scores}", "character.trait_scores", "active_character_card", 220, False),
        PromptSection("emotional_rules", "Emotional rules", chr(10).join(character.emotional_rules or []), "character.emotional_rules", "active_character_card", 240, False),
        PromptSection("forbidden_rules", "Forbidden rules", chr(10).join(character.forbidden_rules or []), "character.forbidden_rules", "safety_character_rules", 240, False),
        PromptSection("scene_relevance_gate", "Scene relevance gate", scene_gate, "runtime.prompt_settings.scene_relevance_gate", "quality_guard", 160, False),
        PromptSection("appearance_reference_rules", "Appearance reference rules", appearance_rules, "runtime.prompt_settings.appearance_reference_rules", "quality_guard", 160, False),
        PromptSection("scene", "Scene", scene, "scene_state", "current_room_scene", 1800, True),
        PromptSection("genre_mode", "Genre mode", build_genre_mode_policy(genre_mode), "conversation.genre_mode", f"genre_route:{genre_mode or 'battle'}", 180, True),
        PromptSection("genre_mode_policy_scope", "Genre Mode Policy Scope", prompt_setting(prompt_settings, "genre_mode_policy_scope"), "backend.system_prompt_registry.genre_mode_policy_scope", "genre_scope_contract", 320, True),
        PromptSection("offstage_actor_recall", "Offstage actor recall", offstage_actor_context or "", "domain_actor.offstage_recall", "router:offstage_actor_context", 360, False),
        PromptSection("continuity_state_by_character", "Continuity state", continuity_context or "", "conversation.local_memory", "router:legacy_continuity_context", 360, False),
        PromptSection("room_context", "Room context", room_context, "conversation.participants.counterpart_cards", "character_character_room" if conversation_mode == "character_character" else "single_character_room", 700, False),
        PromptSection("recent_messages", "Recent messages", history, "conversation.messages.tail", "recent_tail_and_anchors", 1200, True),
        PromptSection("directive", "Directive", directive or "", "message.directive", "current_user_intent", 180, False),
        PromptSection("minimum_output_target", "Minimum output target", build_output_length_rule(min_output_tokens) or "none", "runtime.response_length_preset", "length_contract", 220, False),
        PromptSection("output_rules", "Output rules", output_rules, "runtime.response_contract", "json_contract", 220, True),
    ]
    provider_section = provider_roleplay_rendering_section(
        provider_type=provider_type,
        prompt_settings=prompt_settings,
    )
    if provider_section is not None:
        sections.append(provider_section)
    return sections


def build_character_prompt_harness(
    *,
    character: Character,
    recent_messages: list[Message],
    user_message: str,
    scene_state: SceneState | None = None,
    directive: str | None = None,
    conversation_mode: str | None = None,
    genre_mode: str | None = None,
    room_characters: list[Character] | None = None,
    continuity_context: str | None = None,
    relationship_context: str | None = None,
    external_memory_context: str | None = None,
    offstage_actor_context: str | None = None,
    prompt_settings: PromptSettings | None = None,
    min_output_tokens: int | None = None,
    provider_type: str | None = None,
    total_budget_tokens: int | None = None,
) -> PromptHarness:
    sections = build_character_prompt_sections(
        character=character,
        recent_messages=recent_messages,
        scene_state=scene_state,
        directive=directive,
        conversation_mode=conversation_mode,
        genre_mode=genre_mode,
        room_characters=room_characters,
        continuity_context=continuity_context,
        relationship_context=relationship_context,
        external_memory_context=external_memory_context,
        offstage_actor_context=offstage_actor_context,
        prompt_settings=prompt_settings,
        min_output_tokens=min_output_tokens,
        provider_type=provider_type,
    )
    budget = total_budget_tokens or prompt_budget_for_context(genre_mode=genre_mode, is_multi_room=False)
    return compile_prompt_sections(sections=sections, total_budget_tokens=budget)


def build_character_messages(
    *,
    character: Character,
    recent_messages: list[Message],
    user_message: str,
    scene_state: SceneState | None = None,
    directive: str | None = None,
    conversation_mode: str | None = None,
    genre_mode: str | None = None,
    room_characters: list[Character] | None = None,
    continuity_context: str | None = None,
    relationship_context: str | None = None,
    external_memory_context: str | None = None,
    offstage_actor_context: str | None = None,
    prompt_settings: PromptSettings | None = None,
    min_output_tokens: int | None = None,
    provider_type: str | None = None,
) -> list[dict]:
    harness = build_character_prompt_harness(
        character=character,
        recent_messages=recent_messages,
        user_message=user_message,
        scene_state=scene_state,
        directive=directive,
        conversation_mode=conversation_mode,
        genre_mode=genre_mode,
        room_characters=room_characters,
        continuity_context=continuity_context,
        relationship_context=relationship_context,
        external_memory_context=external_memory_context,
        offstage_actor_context=offstage_actor_context,
        prompt_settings=prompt_settings,
        min_output_tokens=min_output_tokens,
        provider_type=provider_type,
    )
    return [
        {"role": "system", "content": harness.compiled_text},
        {"role": "user", "content": user_message},
    ]
