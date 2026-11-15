"""Prompt contract only; domain/SDK tests prove the enforcement separately."""
import ast
from pathlib import Path


def test_image_guidance_requires_inspection_and_explicit_owner_choice():
    path = Path(__file__).parents[1] / 'app/agent/runtime.py'
    module = ast.parse(path.read_text())
    prompt = next(ast.literal_eval(node.value) for node in module.body
                  if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Name) and target.id == 'SYSTEM_PROMPT'
                          for target in node.targets))
    required = (
        'Before proposing images, inspect the target with lore_image_context.',
        'Never infer this choice or select the latest image automatically.',
        'Stop the turn and ask for the owner choice when a proposal is undecided.',
        'Never silently fall back to new generation or another model.',
        'Reference use does not authorize replacing an avatar or world thumbnail.',
    )
    for instruction in required:
        assert instruction in prompt
