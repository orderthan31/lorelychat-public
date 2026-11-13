# Chat thinking levels

Chat reasoning is a common runtime preference, not a separate provider integration setting. Configure it in the shared model/runtime settings, or override it in one room's runtime settings.

## Selection and inheritance

- The common default is **LOW**.
- A room can select LOW, MEDIUM, HIGH, XHIGH or MAX, or inherit the common setting.
- Inheritance is dynamic: changing the common level affects rooms that inherit it. Saving an unrelated room field must not turn inheritance into a fixed override.
- The API field is `thinking_level`; `null` on a room means inherit. `effective_thinking_level` resolves room/common inheritance, **not** provider-confirmed reasoning depth.
- This setting applies to chat generation and its selected chat fallback. It does not change compression quality/strategy or configure TTS, image generation or Decision models.

## Provider adaptation

The application supplies the shared LiteLLM `reasoning_effort` control. The pinned SDK maps it into the selected provider's native request, including Gemini/Vertex thinking levels and Anthropic thinking/effort options. Account authentication, location, selected model, retry budgets and atomic response validation remain application responsibilities.

Reasoning levels are not universally supported or quantitatively equivalent. SDK capability/model metadata is used to resolve the requested level to the supported range; an unsupported upper tier can resolve to the strongest supported tier. This does not mean every future/custom model is known to the catalog. Unknown capability, omitted controls and adjusted levels must remain distinguishable in diagnostics. Known non-reasoning models do not receive a reasoning control.

The implementation does not silently drop unsupported controls, switch models, or make additional inference calls merely to negotiate a reasoning level. The saved preference and resolved SDK control are separate evidence; a serialized control is not provider-confirmed behavior.

## Output headroom

Gemini's total generation ceiling includes both reasoning and the final response. Hiding thought summaries does not remove reasoning from that ceiling. Selecting a lower reasoning level does not turn reasoning off or create an independent final-response budget.

The application keeps response-length guidance separate from the API's total ceiling. It starts with the existing response-length ceiling and adds estimated reasoning headroom:

- LOW: 4,096 tokens
- MEDIUM: 8,192 tokens
- HIGH: 16,384 tokens
- XHIGH: 32,768 tokens
- MAX: 65,536 tokens

These values are **application allowances**, not provider `thinkingBudget` values. The total is bounded by known selected-model/SDK output and context limits. The effective supported reasoning level determines the allowance; selecting an unavailable upper tier does not reserve an imaginary unsupported tier.

Preview and context admission account for the same total ceiling as generation, including the reachable chat fallback. A larger allowance can reduce the space available for input/history and can increase chargeable output if the model uses it. It does not guarantee a particular final-response length, a separate reasoning allocation, freedom from truncation, or unchanged model quality.

Compression retains its existing generation controls and output ceiling. No paid evaluation is implied by local SDK serialization tests or a successful settings save.
