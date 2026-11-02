"""Isolated optional local TTS engine entry point (never an app lifespan)."""
from pathlib import Path
import json
import sys


def main() -> None:
    from app.services.tts_service import _synthesize_supertonic_in_process
    payload = json.loads(sys.stdin.buffer.read())
    _synthesize_supertonic_in_process(payload["text"], voice_style=payload["voice_style"],
                                     output_path=Path(payload["output_path"]))


if __name__ == "__main__":
    main()
