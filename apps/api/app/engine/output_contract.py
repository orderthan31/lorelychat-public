THOUGHT_MAX_CHARS = 120


SEMANTIC_FIELD_MARKDOWN_DESCRIPTION = (
    "The semantic role comes from this JSON field, not from Markdown markers. "
    "Limited inline Markdown may be used only for presentation emphasis inside the field value."
)


SEMANTIC_MARKDOWN_OUTPUT_RULES = """- Keep dialogue, action, and thought in separate JSON fields; the field name is the semantic source of truth.
- Limited inline Markdown inside a field is optional and may be used only for emphasis.
- Never use Markdown markers to encode whether text is dialogue, action, or thought."""


PREVIOUS_THIRTY_CHAR_PRE_SEMANTIC_MARKDOWN_MULTI_OUTPUT_RULES = """- Return exactly one strict JSON object with a \"replies\" array.
- Follow the current turn's required reply-count range.
- Each character reply must include reply_type=\"character\", the exact character_id, the exact speaker_name, and non-empty dialogue.
- action is optional visible behavior. thought is an optional private reaction of at most 30 characters. Neither replaces dialogue.
- Develop emotion, intention, relationships, and the scene through natural dialogue. Keep bubbles readable without making every bubble terse.
- Avoid filler, repetition, and redundant action, thought, or storytelling.
- Storytelling is optional. Use empty character_id and speaker_name, and never let it replace character dialogue.
- Never reply as the human user or expose runtime commands and internal controls."""


PREVIOUS_THIRTY_CHAR_MULTI_OUTPUT_RULES = f"""{PREVIOUS_THIRTY_CHAR_PRE_SEMANTIC_MARKDOWN_MULTI_OUTPUT_RULES}
{SEMANTIC_MARKDOWN_OUTPUT_RULES}"""


PRE_SEMANTIC_MARKDOWN_MULTI_OUTPUT_RULES = f"""- Return exactly one strict JSON object with a \"replies\" array.
- Follow the current turn's required reply-count range.
- Each character reply must include reply_type=\"character\", the exact character_id, the exact speaker_name, and non-empty dialogue.
- dialogue is the character's exact spoken response in natural dialogue, with character-specific rhythm, honorific level, vocabulary, hesitation, interruption, and subtext. Do not summarize intended speech.
- action is a concrete visible movement, reaction, spatial change, or sensory beat written as natural roleplay prose, not a generic label. It may be empty only when dialogue fully carries the beat.
- thought is private in-character inner voice that adds an unspoken impulse, contradiction, suspicion, or desire. It may be empty when no distinct private beat exists, and when used must be at most {THOUGHT_MAX_CHARS} characters. Do not repeat or summarize dialogue, action, or scene context.
- Develop emotion, intention, relationships, and the scene through natural dialogue and specific beats. Keep bubbles readable without making every bubble terse.
- Avoid filler, repetition, and redundant action, thought, or storytelling.
- Storytelling is optional. Use empty character_id and speaker_name, and never let it replace character dialogue.
- Never reply as the human user or expose runtime commands and internal controls."""


DEFAULT_MULTI_OUTPUT_RULES = f"""{PRE_SEMANTIC_MARKDOWN_MULTI_OUTPUT_RULES}
{SEMANTIC_MARKDOWN_OUTPUT_RULES}"""
