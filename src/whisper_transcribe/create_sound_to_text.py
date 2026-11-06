import argparse
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import torch
import whisper

# このファイルは <project>/src/whisper_transcribe/ にある。
# 実行時のカレントディレクトリに依存しないよう、パスはプロジェクトルート基準で解決する。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
AUDIO_DIR = PROJECT_ROOT / "org_audio"
OUTPUT_DIR = PROJECT_ROOT / "output"

INITIAL_PROMPT = """これはキリスト教の教会の礼拝音声です。"""


def run(cmd: list[str]) -> None:
    print("\n$ " + " ".join(cmd))
    subprocess.run(cmd, check=True)


def split_audio(input_file: Path, split_dir: Path, segment_seconds: int) -> list[Path]:
    split_dir.mkdir(parents=True, exist_ok=True)

    # 入力名(ファイル名・実行フォルダ名の両方)に "%" があると ffmpeg の番号パターンとして
    # 解釈されるため、"%02d" 以外の "%" は "%%" にする(実際に作られる名前は "%" のまま)
    safe_dir = str(split_dir).replace("%", "%%")
    safe_stem = input_file.stem.replace("%", "%%")
    pattern = f"{safe_dir}/{safe_stem}_%02d{input_file.suffix}"

    run([
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-i", str(input_file),
        "-f", "segment",
        "-segment_time", str(segment_seconds),
        "-reset_timestamps", "1",
        "-c", "copy",
        pattern,
    ])

    # split_dir は実行ごとに新規作成されるので、中身はすべて今回の分割結果。
    # (glob にファイル名を埋め込むと "[" などを含む名前で壊れるため suffix のみで絞る)
    # 末尾の番号は %02d なので 100 以降は 3 桁になる。文字列順だと _10, _100, _11 となり
    # 時刻のオフセットがずれるため、番号を整数として並べ替える。
    def segment_number(path: Path) -> int:
        return int(path.stem.rsplit("_", 1)[1])

    return sorted(
        (
            p for p in split_dir.iterdir()
            if p.suffix == input_file.suffix and p.stem.rsplit("_", 1)[-1].isdigit()
        ),
        key=segment_number,
    )


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
    device: str,
    fp16: bool,
    input_file: Path,
) -> None:
    print(f"device={device}" + (f" ({torch.cuda.get_device_name(0)})" if device == "cuda" else ""), flush=True)
    model_obj = whisper.load_model(model, device=device)
    print(INITIAL_PROMPT)

    # temperature fallback の挙動を見るためのログ
    attach_decode_logger(model_obj)

    transcript_dir.mkdir(parents=True, exist_ok=True)

    (transcript_dir / "_settings.txt").write_text(
        "\n".join([
            f"created_at={datetime.now().isoformat(timespec='seconds')}",
            f"input_file={input_file}",
            f"model={model}",
            f"device={device}",
            f"verbose={verbose}",
            f"segment_seconds={segment_seconds}",
            f"output_format={output_format}",
            "temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0)",
            "condition_on_previous_text=False",
            f"fp16={fp16}",
        ]),
        encoding="utf-8",
    )

    merged_segments = []
    offset_seconds = 0.0

    for audio_file in audio_files:
        print(f"\n=== Transcribing: {audio_file.name} ===", flush=True)

        # 分割は -c copy のため境界がちょうど segment_seconds にならないことがある。
        # 実際の長さを積算してオフセットにする（ずれの累積を防ぐ）。
        audio = whisper.load_audio(str(audio_file))
        chunk_seconds = len(audio) / whisper.audio.SAMPLE_RATE

        result = model_obj.transcribe(
            audio,
            language="Japanese",
            task="transcribe",
            temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
            initial_prompt=INITIAL_PROMPT,
            fp16=fp16,
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
        offset_seconds += chunk_seconds

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
        help=(
            "文字起こししたい音声ファイル。例: 20260510_adult_cs.m4a 。"
            "ファイル名だけを指定した場合は org_audio/ を先に探し、なければカレントディレクトリを探します。"
        ),
    )

    parser.add_argument(
        "--model",
        default="medium",
        help="Whisperのモデル。未指定ならmedium。例: tiny, base, small, medium, large",
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
        default=None,
        help="分割後の音声ファイル出力先。未指定なら output/<実行名>/split_audio/",
    )

    parser.add_argument(
        "--transcript-dir",
        type=Path,
        default=OUTPUT_DIR,
        help="Whisperの文字起こし出力先。未指定なら output/ (その下に実行ごとのフォルダを作成)",
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

    parser.add_argument(
        "--device",
        choices=["auto", "cuda", "cpu"],
        default="auto",
        help="実行デバイス。auto ならCUDAが使えればGPU、なければCPU。",
    )

    parser.add_argument(
        "--fp16",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="GPUで半精度(fp16)推論する(既定: 有効)。--no-fp16 で従来のfp32に戻す。CPUでは常にfp32。",
    )

    args = parser.parse_args()

    if args.segment_minutes <= 0:
        parser.error("--segment-minutes は1以上の整数を指定してください。")

    return args


def resolve_input_file(input_file: Path) -> Path | None:
    """ファイル名だけなら org_audio/ → カレントディレクトリ、
    ディレクトリを含む指定ならそのままのパス → org_audio/ 配下 の順で探す。"""
    if input_file.is_absolute():
        candidates = [input_file]
    elif len(input_file.parts) == 1:
        candidates = [AUDIO_DIR / input_file, input_file]
    else:
        candidates = [input_file, AUDIO_DIR / input_file]

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def resolve_device(requested: str) -> str:
    if requested == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    if requested == "cuda" and not torch.cuda.is_available():
        sys.exit("エラー: --device cuda が指定されましたが、CUDAが利用できません。")
    return requested


def main() -> None:
    args = parse_args()

    input_file = resolve_input_file(args.input_file)
    if input_file is None:
        sys.exit(
            f"エラー: 入力ファイルが見つかりません: {args.input_file}\n"
            f"  (カレントディレクトリと {AUDIO_DIR} を探しました)"
        )

    if shutil.which("ffmpeg") is None:
        sys.exit("エラー: ffmpeg が見つかりません。 sudo apt install ffmpeg でインストールしてください。")

    device = resolve_device(args.device)
    if args.device == "auto" and device == "cpu":
        print("警告: CUDAが利用できないためCPUで実行します（低速です）。", flush=True)
    fp16 = args.fp16 and device == "cuda"

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_name = f"{timestamp}_{input_file.stem}_{args.model}"
    transcript_dir = args.transcript_dir.resolve() / run_name
    if args.split_dir is None:
        split_dir = transcript_dir / "split_audio"
    else:
        split_dir = args.split_dir.resolve() / run_name
    segment_seconds = args.segment_minutes * 60

    try:
        audio_files = split_audio(
            input_file=input_file,
            split_dir=split_dir,
            segment_seconds=segment_seconds,
        )
    except subprocess.CalledProcessError as e:
        sys.exit(f"エラー: ffmpeg による分割に失敗しました (終了コード {e.returncode})。")

    if not audio_files:
        sys.exit(f"エラー: 分割後の音声ファイルが作成されませんでした: {split_dir}")

    transcribe(
        audio_files=audio_files,
        model=args.model,
        verbose=args.verbose,
        transcript_dir=transcript_dir,
        segment_seconds=segment_seconds,
        output_format=args.output_format,
        device=device,
        fp16=fp16,
        input_file=input_file,
    )


if __name__ == "__main__":
    main()