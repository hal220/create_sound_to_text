import argparse
import subprocess
import whisper
from datetime import datetime
from pathlib import Path

INITIAL_PROMPT = """これはキリスト教の教会の礼拝音声です。 頻出語: 教会学校、聖書、礼拝、祈り、福音、恵み、十字架、イエス様、神さま、イエス・キリスト、聖霊、御言葉、主の祈り、十戒、聖餐、頌栄、祝祷、モーセ、紀元前、ヘロデ王、パン。 聖書書名として、創世記、出エジプト記、レビ記、民数記、申命記、詩篇、箴言、マタイによる福音書、ヨハネによる福音書、ローマの信徒への手紙が出ます。 読み: れいはい=礼拝、しんめいき=申命記、みことば=御言葉、せいれい=聖霊。"""


def run(cmd: list[str]) -> None:
    print("\n$ " + " ".join(cmd))
    subprocess.run(cmd, check=True)


def split_audio(input_file: Path, split_dir: Path, segment_seconds: int) -> list[Path]:
    split_dir.mkdir(parents=True, exist_ok=True)

    pattern = split_dir / f"{input_file.stem}_%02d{input_file.suffix}"

    run([
        "ffmpeg",
        "-i", str(input_file),
        "-f", "segment",
        "-segment_time", str(segment_seconds),
        "-reset_timestamps", "1",
        "-c", "copy",
        str(pattern),
    ])

    return sorted(split_dir.glob(f"{input_file.stem}_*{input_file.suffix}"))


def format_time(seconds: float) -> str:
    total_seconds = int(seconds)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60

    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_srt_time(seconds: float) -> str:
    total_ms = int(round(seconds * 1000))

    hours = total_ms // 3_600_000
    total_ms %= 3_600_000

    minutes = total_ms // 60_000
    total_ms %= 60_000

    secs = total_ms // 1000
    ms = total_ms % 1000

    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def write_txt(output_path: Path, segments: list[dict]) -> None:
    lines = []

    for segment in segments:
        start = format_time(segment["start"])
        end = format_time(segment["end"])
        text = segment["text"].strip()

        lines.append(f"[{start} --> {end}] {text}")

    output_path.write_text("\n".join(lines), encoding="utf-8")


def write_srt(output_path: Path, segments: list[dict]) -> None:
    blocks = []

    for index, segment in enumerate(segments, start=1):
        start = format_srt_time(segment["start"])
        end = format_srt_time(segment["end"])
        text = segment["text"].strip()

        blocks.append(
            "\n".join([
                str(index),
                f"{start} --> {end}",
                text,
            ])
        )

    output_path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def attach_decode_logger(model_obj):
    original_decode = model_obj.decode
    decode_count = 0

    def logged_decode(mel_segment, options):
        nonlocal decode_count
        decode_count += 1

        result = original_decode(mel_segment, options)

        print(
            "[decode]"
            f" count={decode_count}"
            f" temperature={options.temperature}"
            f" avg_logprob={result.avg_logprob:.3f}"
            f" compression_ratio={result.compression_ratio:.2f}"
            f" no_speech_prob={result.no_speech_prob:.2f}",
            flush=True,
        )

        return result

    model_obj.decode = logged_decode


def transcribe(
    audio_files: list[Path],
    model: str,
    verbose: bool,
    transcript_dir: Path,
    segment_seconds: int,
    output_format: str,
) -> None:
    model_obj = whisper.load_model(model)
    print(INITIAL_PROMPT)

    # temperature fallback の挙動を見るためのログ
    attach_decode_logger(model_obj)

    transcript_dir.mkdir(parents=True, exist_ok=True)

    (transcript_dir / "_settings.txt").write_text(
        "\n".join([
            f"created_at={datetime.now().isoformat(timespec='seconds')}",
            f"model={model}",
            f"verbose={verbose}",
            f"segment_seconds={segment_seconds}",
            f"output_format={output_format}",
            "temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0)",
            "condition_on_previous_text=False",
            "fp16=False",
        ]),
        encoding="utf-8",
    )

    merged_segments = []

    for index, audio_file in enumerate(audio_files):
        offset_seconds = index * segment_seconds
        print(f"\n=== Transcribing: {audio_file.name} ===", flush=True)

        result = model_obj.transcribe(
            str(audio_file),
            language="Japanese",
            task="transcribe",
            temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
            initial_prompt=INITIAL_PROMPT,
            fp16=False,
            verbose=verbose,
            condition_on_previous_text=False,
        )

        adjusted_segments = []

        for segment in result["segments"]:
            adjusted_segment = {
                **segment,
                "start": segment["start"] + offset_seconds,
                "end": segment["end"] + offset_seconds,
            }

            adjusted_segments.append(adjusted_segment)
            merged_segments.append(adjusted_segment)

            start = format_time(adjusted_segment["start"])
            end = format_time(adjusted_segment["end"])
            text = adjusted_segment["text"].strip()

            print(f"[{start} --> {end}] {text}", flush=True)

        output_path = transcript_dir / f"{audio_file.stem}.{output_format}"

        if output_format == "srt":
            write_srt(output_path, adjusted_segments)
        else:
            write_txt(output_path, adjusted_segments)

        print(f"Saved: {output_path}", flush=True)

    if output_format == "srt":
        merged_path = transcript_dir / "merged.srt"
        write_srt(merged_path, merged_segments)
        print(f"Saved merged SRT: {merged_path}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="m4a音声を分割してWhisperで文字起こしする"
    )

    parser.add_argument(
        "input_file",
        type=Path,
        help="文字起こししたい音声ファイル。例: 20260510_adult_cs.m4a",
    )

    parser.add_argument(
        "--model",
        default="small",
        help="Whisperのモデル。未指定ならsmall。例: tiny, base, small, medium, large",
    )

    parser.add_argument(
        "--segment-minutes",
        type=int,
        default=10,
        help="分割単位の分数。未指定なら10分",
    )

    parser.add_argument(
        "--split-dir",
        type=Path,
        default=Path("split_audio"),
        help="分割後の音声ファイル出力先",
    )

    parser.add_argument(
        "--transcript-dir",
        type=Path,
        default=Path("transcripts"),
        help="Whisperの文字起こし出力先",
    )

    parser.add_argument(
        "--output-format",
        choices=["txt", "srt"],
        default="txt",
        help="出力形式。txt または srt。srt の場合は最後に merged.srt も作成します。",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Whisper本体のタイムスタンプ付き途中出力を出すフラグ。指定するとTrueになる。",
    )

    args = parser.parse_args()

    if args.segment_minutes <= 0:
        parser.error("--segment-minutes は1以上の整数を指定してください。")

    return args


def main() -> None:
    args = parse_args()

    input_file: Path = args.input_file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if not input_file.exists():
        raise FileNotFoundError(f"入力ファイルが見つかりません: {input_file}")

    run_name = f"{timestamp}_{input_file.stem}_{args.model}"
    transcript_dir = args.transcript_dir / run_name
    split_dir = args.split_dir / run_name
    segment_seconds = args.segment_minutes * 60

    audio_files = split_audio(
        input_file=input_file,
        split_dir=split_dir,
        segment_seconds=segment_seconds,
    )

    transcribe(
        audio_files=audio_files,
        model=args.model,
        verbose=args.verbose,
        transcript_dir=transcript_dir,
        segment_seconds=segment_seconds,
        output_format=args.output_format,
    )


if __name__ == "__main__":
    main()