import argparse
from pathlib import Path
import pyttsx3

def synthesize(text: str, output: Path, rate: int = 170, volume: float = 1.0):
    engine = pyttsx3.init()
    engine.setProperty("rate", rate)
    engine.setProperty("volume", volume)
    output.parent.mkdir(parents=True, exist_ok=True)
    engine.save_to_file(text, str(output))
    engine.runAndWait()
    if not output.exists() or output.stat().st_size == 0:
        raise RuntimeError("TTS did not produce an audio file.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--rate", type=int, default=170)
    parser.add_argument("--volume", type=float, default=1.0)
    args = parser.parse_args()
    synthesize(args.text, Path(args.output), args.rate, args.volume)
