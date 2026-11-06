---
name: transcribe-church-audio
description: 教会の礼拝音声（org_audio/ の m4a など）をローカルの Whisper で文字起こしし、必要なら Claude で二次補正まで行う。ユーザーが明示的に呼び出したときだけ使う。
argument-hint: <音声ファイル名> [--correct] [Whisperのオプション 例: --model large]
disable-model-invocation: true
---

# 教会音声の文字起こし

`$ARGUMENTS` を解釈して、Whisper での文字起こしと、必要ならその後の二次補正を行う。
作業はプロジェクトルート（`config/` と `src/` がある場所）で行う。

## 最重要ルール：本文を読んでよいかは `--correct` の有無で決める

- 礼拝音声の文字起こしには非公開情報が含まれうる。
- **`$ARGUMENTS` に `--correct` がない場合は、Whisper の実行までで止める。**
  文字起こし結果の本文を読んだり、会話に表示したりしてはいけない。
- `--correct` がある場合に限り、手順3の補正のために本文を読んでよい。これは、その音声を AI（Claude Code）に渡してよいとユーザーが今回明示した、という意味。
- `--correct` がなく、ユーザーが補正を望んでいそうなときは、本文を読む前に AskUserQuestion で
  「この音声は AI に読ませてよいか」を確認する。確認が取れるまで読まない。
- 文字起こしの本文を、Whisper 以外の外部サービス（Web 検索・Web 取得・他の API）に送らない。

## 手順1：入力の確認

1. `$ARGUMENTS` から、音声ファイル名と `--correct` と、その他のオプション（`--model` など）を取り出す。
   `--correct` は Whisper には渡さない。
2. 音声ファイルが `org_audio/` にあるか確認する（`ls org_audio/`）。なければ、ファイル名の候補を示してユーザーに聞く。
3. `ffmpeg` と GPU が使えるか確認する。
   `uv run python -c "import torch; print(torch.cuda.is_available())"`
   GPU が使えないと CPU で非常に遅くなるので、その場合はユーザーに伝えて続けてよいか聞く。
4. 音声の長さを `ffprobe -v error -show_entries format=duration -of csv=p=0 org_audio/<ファイル名>` で確認し、おおよその所要時間を伝える。
   目安：medium・fp16 で、音声の長さの約1/6〜1/7。

## 手順2：Whisper で文字起こし

実行コマンド（既定は medium・fp16・GPU）：

```bash
uv run python src/whisper_transcribe/create_sound_to_text.py <音声ファイル名> [他のオプション] > output/_run_<音声名>.log 2>&1
```

- ターミナル出力にはすべての本文が含まれるため、**必ずログファイルに保存する**。画面に出さない。
- 実行は 1 回だけ行う。`&` を重ねて付けない。長いときは `run_in_background` で実行し、完了通知を待つ。
- Whisper の設定（言語、`temperature` のフォールバック、`condition_on_previous_text=False`、`initial_prompt`）は変更しない。
- 結果は `output/<日時>_<音声名>_<モデル>/` に作られる。最新のフォルダを使う。

終了後、**本文を読まずに**次を報告する：

- 所要時間、使ったデバイスとモデル、fp16 の設定（`_settings.txt`）
- 出力ファイルと行数（`wc -l`）
- temperature フォールバックの回数と、`compression_ratio` が 2.4 を超えた回数（反復幻覚の疑い）。
  `[decode]` 行は進行バーと同じ行にあるので、`grep -o` で数値の部分だけを取り出す：

  ```bash
  grep -o '\[decode\] count=[0-9]* temperature=[0-9.]* avg_logprob=[-0-9.]* compression_ratio=[0-9.]* no_speech_prob=[0-9.]*' output/_run_<音声名>.log
  ```

`--correct` がなければここで終了する。
最後に「補正まで行う場合は `--correct` を付けて呼び出してください」と伝える。

## 手順3：二次補正（`--correct` のときだけ）

0. 補足情報を確認する。`$ARGUMENTS` や会話に次の情報がなければ、補正を始める前にユーザーに尋ねる。
   - 教会名・集会名（`config/private/church_profile.json` があればそれを既定とし、音声ごとに違う点だけ尋ねる）
   - 話者の名前と立場（例：○○さん、教会員、小学校の教師）
   - 聖書箇所（章節。存在しない章節の可能性があれば、聞き取った本文と照らして確認する）
   - 話の概要（わかる範囲で）
   これらは人名・学校名・地名の聞き取り誤りを補正する根拠に使う。
   聞き取れた音に合う候補が補足情報や `config/` から見つかる場合は、その名前に直す。
   根拠が弱い場合は直さず【要確認】を付ける（推定した場合は「推定」と書く）。
1. 次を読む。
   - `config/transcript_cleanup_policy.md`（補正方針。**必ず守る**）
   - `config/correction_dict.json`（キリスト教会全般の辞書）
   - `config/bible_terms.md`（標準表記・読み・誤変換例。**機械的な置換表ではなく、音から正しい語を推定する手がかり**。後半の「読みと誤変換例」を参考にする）
   - `config/private/correction_dict.json` と `config/private/church_profile.json`（**教会個別**の人名・教会名・学校名など。Git 管理外。**存在すれば読んで、一般の辞書と合わせて使う**。同じ語があれば個別の方を優先する。なければ一般の辞書だけで進め、雛形は `config/private.example/`）
2. 出力フォルダの `<音声名>_NN.txt`（NN は分割番号）を、番号順に1つずつ読んで補正する。
3. 補正結果を、同じフォルダに `<音声名>_NN_corrected.txt` として保存する。元の Whisper 出力は上書きしない。

補正の規則：

- 行頭の `[mm:ss --> mm:ss]` のタイムスタンプは変えない。行の順序・数も変えない。
- `correction_dict.json` の `safe_replacements` は適用してよい。
- `contextual_replacements` は、`condition` に書かれた文脈に当てはまる場合だけ適用する。
  特定の説教（申命記8章3節など）に基づく例が含まれるため、他の説教には機械的に当てはめない。
  普通の日本語としても使う語（「生産」「広報」「洗練」など）も入っている。文脈に自信がなければ置換せず【要確認】を付ける。
- `protected_terms` にある語は、置換も言い換えもしない（誤って直さないための保護リスト）。
- 方針で「勝手に変えてはいけない」とされているもの（神学的主張、否定・肯定、数字・日付・章節、発話の順序など）は変えない。
- 教会個別の辞書・プロフィールは、会話・ファイル・報告に必要以上に引用しない。補正結果の `_corrected.txt` に反映するだけにする。
- 発話者が言っていないことを足さない。判断できない箇所は推測で確定せず、方針どおり【要確認】や【聞き取り不明】を付ける。
- 反復幻覚と思われる箇所は、方針の書式で印を付ける（本文は削除せず、印を残す）。

完了後の報告（本文は引用しない）：

- 作成した `_corrected.txt` のパス
- 辞書で置換した件数、【要確認】【聞き取り不明】などの印の件数
- 人間が音声で確認すべき箇所の行番号（本文は載せない）

補正後のファイルは、最終的な確認を人間が行う前提のたたき台であると伝える。

## 守ること

- `org_audio/` の音声を削除・変更しない。`output/` の過去の実行結果も、ユーザーの指示なしに削除しない。
- `torch` など動作済みの環境を再インストールしない。依存は `uv add` で管理する。
- Git にコミットする場合も、`org_audio/`、`output/`、`config/private/` は含めない（`.gitignore` で除外済み）。
- 一般の辞書（`config/correction_dict.json`）に、人名・教会名・学校名・特定の説教だけで通用する語を入れない。そうした語は `config/private/` に入れる。
