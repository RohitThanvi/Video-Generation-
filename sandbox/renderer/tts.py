import argparse
import subprocess
from pathlib import Path


def synthesize(text: str, output: Path, rate: int = 170, volume: float = 1.0):
    text = (text or "").strip()
    if not text:
        raise RuntimeError("Narration text is empty.")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)  # a stale file must never pass the check below

    # pyttsx3's Linux driver binds to the classic libespeak.so.1 ABI; the sandbox
    # ships espeak-ng (libespeak-ng.so.1), which silently produces no file through
    # pyttsx3. The espeak-ng CLI is installed in the image and is deterministic.
    volume = max(0.0, min(volume, 1.0))
    cmd = [
        "espeak-ng",
        "-v", "en",
        "-s", str(int(rate)),                 # words per minute
        "-a", str(int(round(volume * 200))),  # amplitude 0-200
        "-w", str(output),                    # write WAV to file
        "--stdin",                            # read text from stdin (no argv length limits);
                                              # note: "-f -" does NOT work, espeak-ng stat()s a file named "-"
    ]
    try:
        proc = subprocess.run(cmd, input=text, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        proc = None  # espeak-ng missing: fall back to pyttsx3 below
    if proc is not None and proc.returncode != 0:
        raise RuntimeError(f"espeak-ng failed: {proc.stderr.strip() or proc.stdout.strip()}")

    if (not output.exists() or output.stat().st_size == 0) and proc is None:
        try:
            import pyttsx3  # fallback for environments without the espeak-ng CLI
        except ImportError:
            pyttsx3 = None
        if pyttsx3 is not None:
            engine = pyttsx3.init()
            engine.setProperty("rate", rate)
            engine.setProperty("volume", volume)
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
