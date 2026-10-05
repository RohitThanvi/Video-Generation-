import argparse
from pathlib import Path


def synthesize(text: str, output: Path, rate: int = 170, volume: float = 1.0):
    import pyttsx3  # imported lazily so argument handling can be tested without a speech engine

    text = (text or "").strip()
    if not text:
        raise RuntimeError("Narration text is empty.")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)  # a stale file must never pass the check below

    engine = pyttsx3.init()
    engine.setProperty("rate", rate)
    engine.setProperty("volume", max(0.0, min(volume, 1.0)))
    engine.save_to_file(text, str(output))
    engine.runAndWait()
    if not output.exists() or output.stat().st_size == 0:
        raise RuntimeError("TTS did not produce an audio file.")


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--text")
    # A file avoids command-line length limits and values that start with "-".
    source.add_argument("--text-file")
    parser.add_argument("--output", required=True)
    parser.add_argument("--rate", type=int, default=170)
    parser.add_argument("--volume", type=float, default=1.0)
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    text = Path(args.text_file).read_text(encoding="utf-8") if args.text_file else args.text
    synthesize(text, Path(args.output), args.rate, args.volume)
