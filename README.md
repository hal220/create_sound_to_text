# church-transcript-pipeline

ローカルWhisperで音声を荒起こしし、必要に応じてChatGPTで専門語彙を補正するための小さな文字起こしパイプラインです。

主な想定用途は、教会の礼拝・説教・教会学校など、一般的な文字起こしでは誤変換されやすい専門語彙を含む日本語音声ですが、py内部のinitial_promptを書き換えることによりその他のニッチ分野の文字起こしをすることも可能です。

## このリポジトリでできること

- 音声ファイルを指定分数ごとに分割する
- ローカルWhisperで文字起こしする
- `txt` または `srt` 形式で出力する
- `srt` 出力時は、分割ファイルのタイムスタンプを全体時間へ再同期した `merged.srt` を作成する
- Whisperの `decode` ログを出力し、`compression_ratio` などを確認する
- ChatGPT補正用の辞書・用語集・補正方針ファイルを管理する

## 全体の流れ

```text
音声ファイル
  ↓
必要に応じて人間が不要区間をカット
  ↓
create_sound_to_text.py
  ↓
Whisper荒起こし
  ↓
txt / srt 出力
  ↓
ChatGPT補正
  ↓
人間が最終確認
```

## ファイル構成例

```text
church-transcript-pipeline/
  create_sound_to_text.py
  README.md

  config/
    correction_dict.json
    bible_terms.md
    transcript_cleanup_policy.md

  input/
    sermon.wav

  split_audio/
    20260517_001333_sermon_medium/
      sermon_00.wav
      sermon_01.wav

  transcripts/
    20260517_001333_sermon_medium/
      _settings.txt
      sermon_00.txt
      sermon_01.txt
      merged.srt
```

`split_audio/` と `transcripts/` は実行時に自動生成されます。

## 必要なもの

- Python
- ffmpeg
- openai-whisper
- 必要に応じて yt-dlp

### Pythonパッケージ

```powershell
python -m pip install -U openai-whisper
```

### ffmpeg

Windowsの場合は `winget` で入れられます。

```powershell
winget install -e --id Gyan.FFmpeg
```

インストール後、PowerShellを開き直して確認します。

```powershell
ffmpeg -version
```

### yt-dlp

YouTube音声や字幕を取得する場合に使います。

```powershell
winget install -e --id yt-dlp.yt-dlp
```

確認します。

```powershell
yt-dlp --version
```

## YouTube音声を使う場合

YouTubeから音声だけ取得します。

```powershell
yt-dlp -f bestaudio -o "sermon.%(ext)s" "YouTubeのURL"
```

`.webm` で落ちてきた場合は、Whisperに渡しやすいように `.wav` に変換します。

```powershell
ffmpeg -i "sermon.webm" -ar 16000 -ac 1 "sermon.wav"
```

必要な範囲だけ切り出す場合は、次のようにします。

```powershell
ffmpeg -ss 00:18:35 -to 00:47:20 -i "sermon.wav" -ar 16000 -ac 1 "sermon_main.wav"
```

前奏、後奏、讃美、会場ざわめき、机移動などを先に除外しておくと、Whisperの反復幻覚を減らしやすくなります。

## 基本的な使い方

### txtで出力する

```powershell
python .\create_sound_to_text.py "sermon_main.wav" --model medium --output-format txt
```

### srtで出力する

```powershell
python .\create_sound_to_text.py "sermon_main.wav" --model medium --output-format srt
```

`srt` を指定した場合は、分割ファイルごとのSRTに加えて、全体時間に再同期した `merged.srt` も作成します。

### verboseログを出す

```powershell
python .\create_sound_to_text.py "sermon_main.wav" --model medium --verbose
```

`--verbose` は指定した場合だけ `True` になるフラグです。

`--verbose True` や `--verbose False` のようには指定しません。

## オプション

```text
input_file
  文字起こししたい音声ファイル

--model
  Whisperモデル
  例: tiny, base, small, medium, large
  デフォルト: small

--segment-minutes
  音声を何分ごとに分割するか
  デフォルト: 10

--split-dir
  分割音声の出力先
  デフォルト: split_audio

--transcript-dir
  文字起こし結果の出力先
  デフォルト: transcripts

--output-format
  txt または srt
  デフォルト: txt

--verbose
  Whisper本体の途中出力を表示する
```

## 出力例

```text
transcripts/
  20260517_001333_sermon_medium/
    _settings.txt
    sermon_main_00.txt
    sermon_main_01.txt
```

SRTの場合:

```text
transcripts/
  20260517_001333_sermon_medium/
    _settings.txt
    sermon_main_00.srt
    sermon_main_01.srt
    merged.srt
```

`_settings.txt` には、モデル名、分割秒数、出力形式、temperature設定などが保存されます。

## Whisper設定の考え方

このスクリプトでは、Whisper呼び出し時に次のような設定を使っています。

```python
temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
condition_on_previous_text=False
fp16=False
```

### temperature fallback

`temperature=0` 固定だと、同じ語句を繰り返す反復幻覚にロックすることがあります。

```text
読み、読み、読み、読み……
日本語で聞いています……
```

そのため、temperatureをtupleで渡し、Whisperが怪しい出力を検知した場合に再試行できるようにしています。

### compression_ratio

このスクリプトでは、Whisperの `model.decode` をラップして、次のようなログを出します。

```text
[decode] count=18 temperature=0.0 avg_logprob=-0.215 compression_ratio=15.06 no_speech_prob=0.81
[decode] count=19 temperature=0.2 avg_logprob=-1.500 compression_ratio=0.96 no_speech_prob=0.81
```

`compression_ratio` が極端に高い場合、同じ語句を繰り返している可能性があります。

反復幻覚や非発話区間を見つける手がかりとして使えます。

## initial_promptについて

`INITIAL_PROMPT` には、音声の大まかな文脈だけを入れています。

専門語彙や読み仮名を大量に入れても、必ずしも精度は上がりません。  
むしろ、プロンプト由来の語に引っ張られて壊れる場合があります。

そのため、Whisperには最低限の文脈だけを渡し、専門語彙の補正は後段のChatGPT補正に回します。

## ChatGPT補正用の3ファイル

Whisperの出力は荒起こしです。  
そのまま完成稿として使うのではなく、ChatGPTで補正することを想定しています。

補正用に、以下の3ファイルを使います。

```text
correction_dict.json
bible_terms.md
transcript_cleanup_policy.md
```

### correction_dict.json

Whisperの誤変換を補正する辞書です。

例:

```json
{
  "safe_replacements": {
    "心霊記": "申命記",
    "心霊器": "申命記",
    "神明記": "申命記",
    "新名記": "申命記",
    "新兵器": "申命記",
    "霊廃": "礼拝",
    "例杯": "礼拝",
    "マータイ": "マタイ",
    "精霊": "聖霊",
    "創世紀": "創世記",
    "出エジプト紀": "出エジプト記"
  },
  "contextual_replacements": {
    "罠": {
      "replace_with": "マナ",
      "condition": "申命記8章3節の文脈の場合のみ"
    }
  }
}
```

#### safe_replacements

文脈を見なくてもほぼ置換してよい誤変換です。

例:

```text
新兵器 → 申命記
霊廃 → 礼拝
```

#### contextual_replacements

文脈を見て判断する補正です。

例:

```text
罠 → マナ
```

これは申命記8章3節の文脈では有効ですが、常に置換してよいとは限りません。

### bible_terms.md

聖書書名、人名、教会用語などの標準表記リストです。

例:

```markdown
# 聖書・教会用語

## 聖書書名

- 創世記
- 出エジプト記
- レビ記
- 民数記
- 申命記
- マタイによる福音書
- ヨハネによる福音書
- ローマの信徒への手紙

## 人名

- モーセ
- マタイ
- ヨハネ
- イエス・キリスト
- ヘロデ王

## 教会用語

- 礼拝
- 祈り
- 御言葉
- 聖霊
- 聖餐
- 頌栄
- 祝祷
```

`correction_dict.json` が「誤変換 → 正しい表記」の対応表であるのに対し、`bible_terms.md` は標準表記の参照リストです。

### transcript_cleanup_policy.md

ChatGPTに補正させるときの作業方針です。

例:

```markdown
# 文字起こし補正方針

## 基本方針

Whisperによる文字起こしを、教会文脈として自然な日本語に補正する。

ただし、発話者が言っていない内容を勝手に追加しない。  
判断できない箇所は【要確認】または【聞き取り不明】として残す。

## 補正してよいもの

- 明らかな誤変換
- 聖書書名
- 人名
- 教会用語
- 章・節表記
- 明らかな反復幻覚
- 会場ざわめきや非発話区間の整理

## 勝手に変えてはいけないもの

- 神学的主張
- 否定・肯定
- 数字
- 日付
- 章・節
- 話者の意図

## 反復幻覚の扱い

同じ語句が不自然に連続する場合は、本文として無理に残さず、必要に応じて以下のように整理する。

[会場ざわめき／音声要確認]
[反復幻覚の可能性。音声要確認]
```

## ChatGPTへの補正依頼例

```text
File Library または添付ファイルの correction_dict.json、bible_terms.md、transcript_cleanup_policy.md を参照して、
以下のWhisper文字起こしを補正してください。

- safe_replacements は原則置換してください。
- contextual_replacements は文脈が合う場合のみ置換してください。
- 聖書書名、人名、章節、教会用語は bible_terms.md の標準表記を優先してください。
- 発話者が言っていない内容を勝手に追加しないでください。
- 判断できない箇所は【要確認】または【聞き取り不明】として残してください。
- 反復幻覚や会場ざわめきと思われる箇所は、無理に本文化せず整理してください。
- 最後に要確認箇所の一覧を出してください。

以下、文字起こし本文：
```

## 安全上の注意

このスクリプト自体は、音声をローカルで処理します。  
ただし、ChatGPTやOpenAI APIで補正する場合、文字起こしテキストを外部サービスへ送信することになります。

教会音声には、以下のような情報が混ざる可能性があります。

- 個人名
- 祈祷課題
- 病気や家庭事情
- 子どもの名前
- 内輪の運営話
- 公開前の情報

そのため、外部AIに送る前に、人間が一度確認する運用を推奨します。

```text
raw/
  Whisper荒起こし

reviewed/
  人間が確認・伏字化したもの

corrected/
  ChatGPT補正後
```

将来的にAPI化する場合も、`raw` を直接送らず、`reviewed` のみを送る設計にすると安全です。

## 推奨運用

```text
1. 音声を用意する
2. 不要区間を人間が切る
3. create_sound_to_text.py で荒起こしする
4. raw出力を人間が確認する
5. 必要なら個人情報や不要区間を伏字・削除する
6. reviewedとして保存する
7. ChatGPTに補正させる
8. 人間が最終確認する
```

## 注意: 完全自動化しない

このパイプラインは、AIにすべてを任せるためのものではありません。

WhisperもChatGPTも誤ります。  
特に専門語彙、固有名詞、聖書箇所、章・節、数字は必ず人間が確認してください。

このパイプラインの目的は、

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

公開する場合は、使用しているライブラリのライセンスも確認してください。

## 免責

このリポジトリのコードや設定ファイルは、個人利用・検証用途のものです。  
文字起こし結果の正確性は保証しません。

公開・配布・字幕化などに使う場合は、必ず元音声と照合し、人間が最終確認してください。
