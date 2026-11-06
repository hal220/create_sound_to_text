# church-transcript-pipeline

ローカルの Whisper（openai-whisper）と GPU（CUDA）で音声を荒起こしし、必要に応じて Claude Code で専門語彙を二次補正するための、小さな文字起こしパイプラインです。

主な想定用途は、教会の礼拝・説教・教会学校など、一般的な文字起こしでは誤変換されやすい専門語彙を含む日本語音声です。
`INITIAL_PROMPT`（`src/whisper_transcribe/create_sound_to_text.py`）と `config/` 内の辞書・定義を書き換えれば、他のニッチな分野にも使えます。

動作環境は Windows 11 + WSL2（Ubuntu）+ uv + CUDA です（Python パッケージ名は `whisper-transcribe`）。

## このリポジトリでできること

- 音声ファイルを指定分数ごとに分割する
- ローカルの Whisper で文字起こしする（GPU 対応）
- `txt` または `srt` 形式で出力する
- `srt` 出力時は、分割ファイルのタイムスタンプを全体時間に合わせた `merged.srt` を作成する
- Whisper の `decode` ログを出力し、`compression_ratio` などを確認できる
- 補正用の辞書・用語集・補正方針を管理し、Claude Code のスキルで二次補正まで一気通貫で行う

## 全体の流れ

```text
音声ファイル
  ↓
必要に応じて人間が不要区間をカット
  ↓
Whisper 荒起こし（ローカル・GPU）
  ↓
txt / srt 出力
  ↓
二次補正（Claude Code スキル。--correct を付けたときだけ）
  ↓
人間が最終確認
```

## 前提環境

- Windows 11 + WSL2（Ubuntu）
- Python 3.12 / uv
- NVIDIA GPU + CUDA 12.8 対応の PyTorch（`pyproject.toml` で cu128 インデックスを指定済み）
- ffmpeg（`sudo apt install ffmpeg`）

## ディレクトリ構成

```text
church-transcript-pipeline/
├── src/whisper_transcribe/create_sound_to_text.py  # 文字起こしスクリプト
├── config/
│   ├── correction_dict.json          # 誤変換辞書（教会全般）
│   ├── bible_terms.md                # 標準表記・読み・誤変換例
│   ├── transcript_cleanup_policy.md  # 補正方針
│   ├── private.example/              # 教会個別設定の雛形
│   └── private/                      # 教会個別設定（Git管理外）
├── .claude/skills/transcribe-church-audio/  # 二次補正までのスキル
├── org_audio/   # 元音声を置く（Git管理外）
└── output/      # 文字起こし結果（Git管理外）
```

## セットアップ

```bash
cd ~/projects/whisper-transcribe
uv sync --locked
```

`--locked` を付けると `uv.lock` どおりにインストールされ、動作中の CUDA 版 PyTorch が勝手に更新されることはありません。

GPU 確認:

```bash
uv run python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

## 音声の用意

`org_audio/` に音声ファイルを置きます。

### YouTube の音声を使う場合

[yt-dlp](https://github.com/yt-dlp/yt-dlp) で音声だけ取得できます（`uv tool install yt-dlp` などで導入）。

```bash
yt-dlp -f bestaudio -o "org_audio/sermon.%(ext)s" "YouTubeのURL"
```

`.webm` で落ちてきた場合は、Whisper に渡しやすいように `.wav` に変換します。

```bash
ffmpeg -i org_audio/sermon.webm -ar 16000 -ac 1 org_audio/sermon.wav
```

必要な範囲だけ切り出す場合：

```bash
ffmpeg -ss 00:18:35 -to 00:47:20 -i org_audio/sermon.wav -ar 16000 -ac 1 org_audio/sermon_main.wav
```

前奏・後奏・讃美・会場のざわめき・机の移動などを先に除外しておくと、Whisper の反復幻覚を減らしやすくなります。

## 使い方

プロジェクトルートで実行します（ファイル名だけ指定すれば `org_audio/` から探します）。

```bash
uv run python src/whisper_transcribe/create_sound_to_text.py 20260510_adult_cs.m4a
```

次の短縮形も同じ動作です（どのディレクトリからでも実行可）。

```bash
uv run whisper-transcribe 20260510_adult_cs.m4a
```

### 主なオプション

| オプション | 既定値 | 説明 |
|---|---|---|
| `--model` | `medium` | tiny / base / small / medium / large など |
| `--segment-minutes` | `10` | ffmpeg で分割する長さ（分） |
| `--output-format` | `txt` | `txt` または `srt`（`srt` の場合は `merged.srt` も作成） |
| `--transcript-dir` | `output/` | 出力先 |
| `--split-dir` | （`output/<実行名>/split_audio/`） | 分割音声の出力先 |
| `--device` | `auto` | `auto` / `cuda` / `cpu` |
| `--fp16` / `--no-fp16` | fp16 | GPU では半精度(fp16)推論が既定（高速・省VRAM）。`--no-fp16` で fp32。CPU では常に fp32 |
| `--verbose` | オフ | Whisper 本体の途中出力を表示（`--verbose True` のようには指定しない） |

例: SRT で出力

```bash
uv run python src/whisper_transcribe/create_sound_to_text.py 20260510_adult_cs.m4a --output-format srt
```

VRAM 6GB の場合、`medium` / `large` は fp16（既定）で使ってください。

## 出力

実行ごとに `output/<日時>_<ファイル名>_<モデル>/` が作られます。

```text
output/20261106_120000_20260510_adult_cs_medium/
├── _settings.txt              # 実行時の設定
├── 20260510_adult_cs_00.txt   # 分割ごとの結果（時刻は元音声基準）
├── 20260510_adult_cs_01.txt
├── merged.srt                 # --output-format srt のときのみ
└── split_audio/               # 分割した音声（不要なら削除可）
```

`_settings.txt` には、モデル名、デバイス、分割秒数、出力形式、temperature 設定、fp16 などが保存されます。

## Whisper 設定の考え方

Whisper の呼び出しには次の設定を使っています。

```python
temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
condition_on_previous_text=False
fp16=True   # GPU のとき。--no-fp16 で False
```

Windows 版（`fp16=False`）から変えたのは、GPU 実行時の `fp16` の既定だけです。

### temperature fallback

`temperature=0` 固定だと、同じ語句を繰り返す反復幻覚にロックすることがあります。

```text
読み、読み、読み、読み……
日本語で聞いています……
```

そのため temperature をタプルで渡し、Whisper が怪しい出力を検知したときに温度を上げて再試行させます。
再試行には乱数が入るため、**同じ音声でも実行のたびに結果が少し変わることがあります**。

### compression_ratio

`model.decode` をラップして、次のようなログを出します。

```text
[decode] count=18 temperature=0.0 avg_logprob=-0.215 compression_ratio=15.06 no_speech_prob=0.81
[decode] count=19 temperature=0.2 avg_logprob=-1.500 compression_ratio=0.96 no_speech_prob=0.81
```

`compression_ratio` が極端に高い（目安：2.4 超）場合、同じ語句を繰り返している可能性があります。反復幻覚や非発話区間を見つける手がかりになります。

### initial_prompt

`INITIAL_PROMPT` には、音声の大まかな文脈（教会の礼拝音声であること）だけを入れています。
専門語彙や読み仮名を大量に入れても、必ずしも精度は上がらず、プロンプト由来の語に引っ張られて壊れる場合があります。
そのため Whisper には最低限の文脈だけを渡し、専門語彙の補正は後段の二次補正に回します。

## 二次補正（Claude Code スキル）

[.claude/skills/transcribe-church-audio/](.claude/skills/transcribe-church-audio/SKILL.md) に、Whisper の実行から二次補正までのスキルがあります。
Claude Code をこのプロジェクトで開き、次のように呼び出します。

```text
/transcribe-church-audio 20260510_adult_cs.m4a              # Whisper まで（本文は AI に読ませない）
/transcribe-church-audio 20260510_adult_cs.m4a --correct    # Whisper → Claude による二次補正まで

話者：○○さん（教会員、小学校の教師）
聖書箇所：ヨハネの黙示録21:3〜4
概要：…
```

- `--correct` を付けたときだけ、文字起こしの本文を Claude Code（Anthropic）に読ませて補正します。
  **AI に読ませてよい音声にだけ付けてください。**
- 話者・聖書箇所・概要を添えると、人名や聖書箇所の聞き取り誤りを直しやすくなります（書かなければ聞き返されます）。
- 補正結果は `output/<実行名>/<音声名>_NN_corrected.txt` に保存します。元の Whisper 出力は上書きしません。

### 補正用の設定（config/）

| 場所 | 内容 | Git |
|---|---|---|
| `config/correction_dict.json` | キリスト教会全般の誤変換辞書 | 管理する |
| `config/bible_terms.md` | 聖書書名・人名・教会用語の標準表記、読み、誤変換例 | 管理する |
| `config/transcript_cleanup_policy.md` | 補正方針 | 管理する |
| `config/private/` | **教会個別**の人名・教会名・学校名などの辞書（`correction_dict.json`）とプロフィール（`church_profile.json`） | **管理しない** |
| `config/private.example/` | 上記の雛形（実名なし） | 管理する |

新しい環境では `cp -r config/private.example config/private` で雛形をコピーして書き換えます。

**`correction_dict.json`** は誤変換の辞書です。

- `safe_replacements`：文脈を見なくてもほぼ置換してよい誤変換（例：`新兵器 → 申命記`、`例杯 → 礼拝`）。
- `contextual_replacements`：文脈を見て判断する補正。`condition` に当てはまる場合だけ置換します（例：`罠 → マナ` は特定の聖書箇所の文脈のみ）。
- `protected_terms`：誤って直さないための保護リスト。

**`bible_terms.md`** は標準表記の参照リストです。Whisper の誤変換は実行のたびに変わるため、後半に「読みと誤変換例」を載せ、機械的な置換ではなく、音から正しい語を推定する手がかりとして使います。

**`transcript_cleanup_policy.md`** は補正方針です。補正してよいもの（明らかな誤変換、聖書書名、人名、教会用語、章節表記、反復幻覚の整理）と、勝手に変えてはいけないもの（神学的主張、否定・肯定、数字、日付、章節、話者の意図）を定め、判断できない箇所は【要確認】【聞き取り不明】で残します。

## 安全上の注意

Whisper による文字起こし自体は、音声をローカルで処理します。
一方、`--correct`（Claude Code による二次補正）や、ChatGPT などの外部 AI を使う場合は、**文字起こしテキストを外部サービスへ送信すること**になります。

教会音声には、次のような情報が混ざる可能性があります。

- 個人名
- 祈祷課題
- 病気や家庭事情
- 子どもの名前
- 内輪の運営話
- 公開前の情報

外部に送ってよい音声かどうかを、毎回人間が判断してください（スキルは `--correct` が指定されたときだけ本文を読みます）。
外部 AI に送る前に、人間が一度確認し、必要なら伏字化する運用もおすすめです。

```text
raw/        Whisper 荒起こし
reviewed/   人間が確認・伏字化したもの
corrected/  AI 補正後
```

`org_audio/`・`output/`・`config/private/` は非公開情報を含みうるため `.gitignore` で除外しています。**これらを Git に含めないでください。**

## 推奨運用

```text
1. 音声を用意する
2. 不要区間を人間が切る
3. 荒起こしする（Whisper）
4. raw 出力を人間が確認する
5. 必要なら個人情報や不要区間を伏字・削除する
6. AI に読ませてよい音声なら --correct で二次補正する
7. 人間が最終確認する（【要確認】の箇所は音声で確かめる）
```

## 完全自動化しない

このパイプラインは、AI にすべてを任せるためのものではありません。
Whisper も AI 補正も誤ります。特に専門語彙、固有名詞、聖書箇所、章・節、数字は必ず人間が確認してください。

目的は、

```text
人間が最初から全部聞き起こす負担を減らす
```

ことであり、

```text
人間確認をゼロにする
```

ことではありません。

## ライセンス

未定。

公開する場合は、使用しているライブラリ（openai-whisper、PyTorch など）のライセンスも確認してください。

## 免責

このリポジトリのコードや設定ファイルは、個人利用・検証用途のものです。
文字起こし結果の正確性は保証しません。

公開・配布・字幕化などに使う場合は、必ず元音声と照合し、人間が最終確認してください。
