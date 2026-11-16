# Wireframe v2.1 — khổ dọc là chính

> Trạng thái: **v2.1** · 2026-11-16 · Vẽ theo [UX.md](UX.md) v0.16 và [wireframe-prompt.md](wireframe-prompt.md). **Khi khác UX.md thì UX.md thắng.** Điểm còn mở: UX §8.
> Bản v2.1 vẽ lại v2: khổ **DỌC (~820 px, iPad cầm dọc)** là khổ chính; khổ ngang chỉ ghi phần khác (mục 8). Đã sửa 9 sai lệch của v2, bỏ phần "đề xuất thêm" và "tự rà soát" (kết luận nằm ở UX.md và SPEC #26).

## Cách đọc

- Mỗi khung ASCII rộng khoảng 80 ký tự, đại diện cho màn hình ~820 px. Chiều rộng ký tự không phải pixel.
- `{field}` trường dữ liệu có sẵn (tên theo `docs/API.md`). `{x [UI]}` trạng thái chỉ của giao diện. `[NEW]` cần backend mới (UX Phụ lục A.7).
- Mọi vùng bấm cao ≥ 44 px. Bảng rộng ở khổ dọc là **thẻ xếp dọc**; thanh tab được cuộn ngang trong chính nó nếu không đủ chỗ, thân trang không cuộn ngang.
- Giá trị mẫu (model, số) là minh hoạ; mặc định thật ở UX Phụ lục B. Giá trị enum hợp lệ ở UX A.8.
- Nút nào tốn lời gọi model đều mở hộp **Estimate** (mục 0d), trừ **Try 20 lines**.

---

## 0. Shell, đăng nhập, lớp phủ dùng chung

### 0a) Shell (mọi trang `#/*`)

```text
+------------------------------------------------------------------------------+
| [ Library ]    [ Activity ]    [ Settings ]                                  |
+------------------------------------------------------------------------------+
| Running {counts.running: 1} · Waiting {counts.waiting: 3}                    |
| Synced {synced_at: 2m ago} · {providers[0].model: gemini-3.5-flash}          |
| {providers[0].state: exhausted} until {providers[0].until: 00:00}             |
+------------------------------------------------------------------------------+
| [!] RED BANNER (khoá bị từ chối hoặc model lạ, job Waiting · provider)       |
| Gemini rejected the API key. Jobs are waiting.                               |
|                                                    [ Open Connections ]      |
+------------------------------------------------------------------------------+
| [!] YELLOW BANNER (catalog.status = kept)                                    |
| Bazarr could not be read. Showing the library as of {synced_at: 10:15}.      |
| {warning: timeout}                                         [ Sync now ]      |
+------------------------------------------------------------------------------+
|                              < NỘI DUNG TRANG >                              |
+------------------------------------------------------------------------------+
```

- Nav chỉ có Library · Activity · Settings. **Không** có Review trước P13.
- Dải trạng thái là thông tin, không phải cảnh báo. Hết quota ngày là bình thường: không lên banner.
- Hai banner chỉ hiện khi đúng điều kiện (UX §4.2).

### 0b) Lớp phủ hết phiên (bất kỳ `/api/*` trả `401`)

```text
+------------------------------------------------------------------------------+
| ////////////////////////////////////////////////////////////////////////////// |
|        +------------------------------------------------------+              |
|        | Your session expired.                                |              |
|        | Your unsaved changes are kept in this browser.       |              |
|        |                                          [ Sign in ] |              |
|        +------------------------------------------------------+              |
| ////////////////////////////////////////////////////////////////////////////// |
+------------------------------------------------------------------------------+
```

### 0c) Đăng nhập (`GET /login`, trang tĩnh riêng ngoài ứng dụng)

```text
+------------------------------------------------------------------------------+
|                    +----------------------------------+                      |
|                    | SUBTITLE CONSOLE                 |                      |
|                    | Username   [                   ] |                      |
|                    | Password   [                   ] |                      |
|                    | {error [UI]}                     |                      |
|                    |                      [ Sign in ] |                      |
|                    +----------------------------------+                      |
+------------------------------------------------------------------------------+
```

Trạng thái `{error}`: `Wrong user or password` (401) · `Too many attempts, try again in 5 minutes` (429, nút Sign in tắt) · chưa có tài khoản (503): ẩn form, hiện:

```text
+------------------------------------------------------------------------------+
| No console account is set up yet.                                            |
| Set CONSOLE_USER and CONSOLE_PASSWORD in the container environment and       |
| restart it.                                                                  |
+------------------------------------------------------------------------------+
```

Không có "Forgot password".

### 0d) Hộp Estimate (bottom sheet dùng chung)

Dùng cho: Translate (tập, mùa, series, tất cả), Translate again, Retranslate, Retry, Retry all failed, Extract facts, Compare on a whole episode, Request that episode.

```text
+------------------------------------------------------------------------------+
| {action [UI]: Translate season 1}                           (Bottom Sheet)   |
+------------------------------------------------------------------------------+
| Jobs                {estimate.jobs: 8}                                        |
| Cues                {estimate.cues: 2,740}                                    |
| Model calls (est.)  {estimate.calls: 16}   ("unknown" for Retry and range)    |
| Calls left today    {estimate.calls_left_today: 42}   (hidden when no cap)    |
| Paused until        {estimate.paused_until: 00:00}    (only when quota spent) |
| Time                {estimate.minutes: ~4} min · done ~{estimate.finish_at}   |
|                                                                              |
| [ Cancel ]                                                      [ Confirm ]  |
+------------------------------------------------------------------------------+
```

### 0e) Rời trang khi còn thay đổi chưa lưu (mọi form)

```text
+------------------------------------------------------------------------------+
| Unsaved changes                                             (Bottom Sheet)   |
| You have unsaved changes in {form [UI]: Connections}.                        |
| [ Stay on page ]                                       [ Discard & leave ]   |
+------------------------------------------------------------------------------+
```

---

## 1. Home (`#/library`)

```text
+------------------------------------------------------------------------------+
| [ Library ]    [ Activity ]    [ Settings ]                  [ Sync now ]    |
| Running {counts.running: 1} · Waiting {counts.waiting: 2} · Synced 2m ago    |
+------------------------------------------------------------------------------+
| [ Search by title…                                                   {q} ]   |
| ( All )  ( Missing Vietnamese )  ( Running )  ( Failed )  ( Ignored )        |
+------------------------------------------------------------------------------+
| [TV] {title: Arcane}                                                         |
|      {episodes: 18} eps · Missing {missing: 3} · Running {open_jobs: 1}      |
|      · Failed {failed [NEW]: 1}                          [ View failed ]     |
+------------------------------------------------------------------------------+
| [TV] {title: Severance}                                                      |
|      {episodes: 19} eps · Missing {missing: 5} · Waiting {open_jobs: 2}      |
|                                                       [ Translate missing ]  |
+------------------------------------------------------------------------------+
| [TV] {title: Shogun}                                                         |
|      {episodes: 10} eps · Missing 0                          [ Improve ]     |
+------------------------------------------------------------------------------+
| [MV] {title: Oppenheimer}                                                    |
|      {targets[0].text: Missing}                      [ Translate missing ]  |
+------------------------------------------------------------------------------+
| [MV] {title: Dune: Part Two}                                                 |
|      {targets[0].text: Missing · Waiting · quota}                            |
|      Resumes {retry_at: 00:00}                       (no button: job is open) |
+------------------------------------------------------------------------------+
| [MV] {title: Past Lives}                                                     |
|      {targets[0].text: Translated}                           [ Improve ]     |
+------------------------------------------------------------------------------+
```

- Một nút mỗi dòng, theo thứ tự: có Failed → **View failed**; còn mục Missing **chưa có job mở** → **Translate missing** (qua Estimate); còn lại → **Improve**. Mục đã có job Requested, Running hoặc Waiting **không** có nút Translate.
- Không có số Stale, không có Auto-request, không có poster.
- Số **Failed** và hai chip Failed/Ignored chỉ có khi đếm được rẻ (UX §8); nếu không, dòng bỏ số Failed và chip lọc sau khi tải.
- Bấm tên `[TV]` → `#/series/{id}/episodes`; `[ Improve ]` của `[TV]` → `#/series/{id}/style`. Bấm tên `[MV]` → `#/unit/{id}`; `[ Improve ]` của `[MV]` → `#/unit/{id}/style`.

Trạng thái:

1. Đang tải: năm hàng khung xương.
2. Chưa có danh mục (chưa cấu hình hoặc chưa đọc được Bazarr):

```text
+------------------------------------------------------------------------------+
| No series or movies in the catalog yet.                                      |
| Check the Bazarr connection, or sync now.                                    |
| [ Open Connections ]                                         [ Sync now ]    |
+------------------------------------------------------------------------------+
```

3. Không khớp tìm kiếm hoặc chip: `No items match "{q}" in {filter [UI]}.   [ Clear filter ]`
4. Danh mục `kept`: banner vàng (mục 0a).

---

## 2. Series (`#/series/:id/...`)

### Header chung (cả 5 tab)

```text
+------------------------------------------------------------------------------+
| [ < Library ]   {title: Arcane}                                              |
| Target language   [ {target_lang: vi} v ]       (ẩn nếu chỉ có một đích)     |
| Auto-request      [ Default | On | Off ]         (lưu ngay khi chạm)          |
| Story memory      [ Default | On | Off ]                                     |
| Specials after    [ {specials_after: S01E09} ]                               |
|   Changing Memory or Specials after can flag translations stale.             |
|                                   [ Apply ]   (hiện khi một trong hai đổi)   |
+------------------------------------------------------------------------------+
| [Episodes] [Style] [Glossary] [Characters] [Context]                         |
+------------------------------------------------------------------------------+
```

### 2a) Episodes (`#/series/:id/episodes`)

```text
+------------------------------------------------------------------------------+
| Selected {selected_count [UI]: 2}   [ Translate selected ]  [ Ignore selected ] |
| [ Translate whole series (12 missing) ]                                      |
+------------------------------------------------------------------------------+
| SEASON {season: 1}                          [ Translate season 1 (6 missing) ] |
+------------------------------------------------------------------------------+
| [x] S01E01 · {title: Welcome to the Playground}                              |
|     Missing                                               [ Translate ]      |
+------------------------------------------------------------------------------+
| [ ] S01E02 · {title}                                                         |
|     Missing · no source subtitle        (waiting for Bazarr, no button)      |
+------------------------------------------------------------------------------+
| [ ] S01E03 · {title}                                                         |
|     Missing · Requested                                   [ Cancel ]         |
+------------------------------------------------------------------------------+
| [ ] S01E04 · {title}                                                         |
|     Missing · Running  {progress_done: 120}/{progress_total: 340}  [ Stop ]  |
+------------------------------------------------------------------------------+
| [ ] S01E05 · {title}                                                         |
|     Missing · Waiting · context (needs S01E04)    [ Request that episode ]   |
+------------------------------------------------------------------------------+
| [ ] S01E06 · {title}                                                         |
|     Missing · Waiting · quota          resumes {retry_at: 00:00} (no button) |
+------------------------------------------------------------------------------+
| [ ] S01E07 · {title}                                                         |
|     Missing · Waiting · provider                  [ Open Connections ]       |
|     (nếu là breaker mở: "Retrying after {until}", không nút)                 |
+------------------------------------------------------------------------------+
| [ ] S01E08 · {title}                                                         |
|     Missing · Waiting · settle     source changed a moment ago (no button)   |
+------------------------------------------------------------------------------+
| [ ] S01E09 · {title}                                                         |
|     Missing · Waiting · rate         provider busy, retrying (no button)     |
+------------------------------------------------------------------------------+
| [ ] S01E10 · {title}                                                         |
|     Missing · Failed · {error: Output token limit exceeded}   [ Retry ]      |
+------------------------------------------------------------------------------+
| [ ] S02E01 · {title}                                                         |
|     Translated                                            [ Improve ]        |
|     v{version: 2} · {model: gemini-3.1-flash-lite} · {updated_at: 2d ago}    |
+------------------------------------------------------------------------------+
| [ ] S02E02 · {title}                                                         |
|     Translated · Stale                                    [ Retranslate ]    |
|     v1 · gemini-3.1-flash-lite · 5d ago                                      |
+------------------------------------------------------------------------------+
| [ ] S02E03 · {title}                                                         |
|     Edited                                                [ History ]        |
|     v3 · 1d ago                                                              |
|     [!] File changed outside the worker  ({modified_outside: true})          |
+------------------------------------------------------------------------------+
| [ ] S02E04 · {title}                                                         |
|     External                                              [ View ]           |
+------------------------------------------------------------------------------+
| [ ] S02E05 · {title}                                                         |
|     Removed                       [ Restore ]  [ Translate again ]           |
+------------------------------------------------------------------------------+
| [ ] S02E06 · {title}                                                         |
|     Ignored                                         [ Stop ignoring ]        |
+------------------------------------------------------------------------------+
| [ ] S02E07 · {title}                                                         |
|     Outside profile                                       [ Translate ]      |
+------------------------------------------------------------------------------+
| [ ] S02E08 · {title}                                                         |
|     Translated   [Candidate]                              [ Improve ]        |
|     (huy hiệu Candidate: có bản nháp chưa ra đĩa, mở từ History)             |
+------------------------------------------------------------------------------+
```

- Hàng Translated · Waiting hoặc Failed ghép nhãn như `Translated · Waiting · quota`, nút theo nhãn job.
- `[ Translate ]`, `[ Translate selected ]`, `[ Translate season N ]`, `[ Translate whole series ]`, `[ Request that episode ]`, `[ Retry ]`, `[ Retranslate ]`, `[ Translate again ]` → Estimate (0d). `[ Improve ]` → `#/series/:id/style`. Bấm `SxxEyy · {title}` → `#/series/:id/episodes/:ep`.

### 2b) Style (`#/series/:id/style`)

Mọi Segmented Control có **Inherit** đầu tiên, là mặc định khi trục chưa đặt. Khi đang Inherit, ô hiện giá trị đang hiệu lực. Không có nhãn "from: …" (hệ thống không biết tầng nguồn).

```text
+------------------------------------------------------------------------------+
| Preset  [ {preset: —} v ]    Suggested for {genres [UI]: Animation, Drama}:  |
|                                                    [ Apply "Drama" ]         |
+------------------------------------------------------------------------------+
| Formality ({formality})                                                      |
| [ Inherit · now Neutral ][ Casual ][ Neutral ][ Formal ]                     |
|                                                                              |
| Tone ({tone})                                                                |
| [ Dark, cynical, street-smart                          ]   [ Inherit ]       |
|   (đang Inherit: ô trống, ghi "Inherit · now {effective.tone}")              |
|                                                                              |
| Swearing ({profanity})                                                       |
| [ Inherit · now Soften ][ Keep ][ Soften ][ Remove ]                         |
|                                                                              |
| Idioms and wordplay ({localization})                                         |
| [ Inherit · now Balanced ][ Literal ][ Balanced ][ Adapt ]                   |
|                                                                              |
| [ > More ]   (gập mặc định; mở ra:)                                          |
| +--------------------------------------------------------------------------+ |
| | Dialect ({dialect})                                                      | |
| | [ Inherit · now Neutral ][ North ][ South ][ Neutral ]                   | |
| | Honorifics (anime) ({honorifics})                                        | |
| | [ Inherit · now Translate ][ Keep ][ Translate ]                         | |
| | Characters per second ({max_cps})    [ 18 ]   (để trống = Inherit)       | |
| | Longest line ({max_line_length})     [ 42 ]   (để trống = Inherit)       | |
| | Lines per cue ({max_lines})          [ 2  ]   (để trống = Inherit)       | |
| | Fast dialogue ({allow_condense})                                         | |
| | [ Inherit · now Allow ][ Allow ][ Don't ]                                | |
| | Notes ({notes})                                                          | |
| | [ Keep Piltover speech formal; Zaun speech rough and clipped.        ]   | |
| +--------------------------------------------------------------------------+ |
|                                                                              |
| Saving flags {stale_after: 12} episodes stale.                  [ Save ]     |
|   ("Checking…" khi đang tính · "No translation is affected." khi N = 0)      |
+------------------------------------------------------------------------------+
```

Try 20 lines (không có hộp Estimate; chi phí ghi trên nút):

```text
+------------------------------------------------------------------------------+
| TRY 20 LINES                          (tính trước, không ghi file nào)       |
| Sample episode [ {unit [UI]: S01E08 · Oil and Water} v ]                     |
| Start at cue   [ {start [UI]: 1} ]                                           |
|                                              [ Try 20 lines (≈ 1 call) ]     |
+------------------------------------------------------------------------------+
| A) Series đã có bản dịch (thẻ):                                              |
| +--------------------------------------------------------------------------+ |
| | Cue #{n: 1}                                                              | |
| | Source          You think you can just walk in?                          | |
| | Now             Cậu nghĩ cậu cứ thế bước vào à?                          | |
| | With this style Mày tưởng cứ thế mà bước vào à?                          | |
| +--------------------------------------------------------------------------+ |
| B) Series mới, chưa tập nào được dịch (thẻ, không có dòng Now):              |
| +--------------------------------------------------------------------------+ |
| | Cue #{n: 1}                                                              | |
| | Source   Welcome to the lanes, kid.                                      | |
| | Preview  Chào mừng tới khu ổ chuột, nhóc.                                | |
| +--------------------------------------------------------------------------+ |
+------------------------------------------------------------------------------+
```

- Trạng thái Try 20 lines: nút `[ Trying… ]` tắt khi đang gọi; lỗi provider hiện khung đỏ `Preview failed ({error type}): {message}`.
- Series mới: tập mẫu mặc định là tập đầu tiên có phụ đề nguồn, 20 cue đầu.

Compare on a whole episode:

```text
+------------------------------------------------------------------------------+
| COMPARE ON A WHOLE EPISODE                                                   |
| Episode [ {unit [UI]: S01E03} v ]    Label [ {label [UI]: gritty} ]          |
|                                          [ Compare on a whole episode ]      |
|   → Estimate (0d). Job chạy nền; kết quả là bản Candidate.                   |
|                                                                              |
| (khi xong)  [i] A candidate is ready in History of S01E03.  [ Open Compare ] |
+------------------------------------------------------------------------------+
```

Xác nhận **Save** (chỉ khi N > 0):

```text
+------------------------------------------------------------------------------+
| Save style?                                                 (Bottom Sheet)   |
| Saving flags {stale_after: 12} episodes stale. Nothing is retranslated.      |
| [ Cancel ]                                                      [ Confirm ]  |
+------------------------------------------------------------------------------+
```

### 2c) Glossary (`#/series/:id/glossary`)

```text
+------------------------------------------------------------------------------+
| Version v{version: 4}                                                        |
| Saving flags {stale_after: 5} episodes stale.                    [ Save ]    |
| [ History ] [ Copy from another series ] [ Import YAML ] [ Export YAML ]     |
+------------------------------------------------------------------------------+
| TERMS                                                       [ + Add term ]   |
| +--------------------------------------------------------------------------+ |
| | Source ({source})  [ Enforcer             ]                              | |
| | Target ({target})  [ Cảnh vệ              ]                              | |
| | [x] Locked ({locked})                                                    | |
| | Note ({note})      [ Piltover police      ]                              | |
| | From episode ({from}) [ S01E01 ]   Until ({until}) [        ]            | |
| |                                                     [ Delete ]          | |
| +--------------------------------------------------------------------------+ |
|                                                                              |
| FORMS OF ADDRESS                                     [ + Add address rule ]  |
| +--------------------------------------------------------------------------+ |
| | Pair  [ {a: Vi} ]  <->  [ {b: Powder} ]                                  | |
| | A calls B ({a_to_b})  [ chị–em ]   (self–other)                          | |
| | B calls A ({b_to_a})  [ em–chị ]   (self–other)                          | |
| | From ({from})  [ S01E01 ]          (episode or timecode)                 | |
| |                                                     [ Delete ]          | |
| +--------------------------------------------------------------------------+ |
+------------------------------------------------------------------------------+
```

Không có sub-tab Characters ở đây. Hộp thoại:

**History**

```text
+------------------------------------------------------------------------------+
| Glossary history                                            (Bottom Sheet)   |
| v4 · {at: 2026-11-15 19:00} · {note: console}                  (current)     |
| v3 · {at: 2026-11-10 12:30} · {note: restore}                  [ Restore ]   |
| ({note}: console · import · correction · restore · copy)    [ Close ]        |
+------------------------------------------------------------------------------+
```

**Copy from another series**

```text
+------------------------------------------------------------------------------+
| Copy glossary from another series                           (Bottom Sheet)   |
| From   [ {from_series [UI]: Cyberpunk: Edgerunners} v ]                      |
| (•) Merge (keep this series' entries on conflict)   ( ) Replace              |
| [ Cancel ]                                                        [ Copy ]   |
+------------------------------------------------------------------------------+
```

**Import YAML** (dán hoặc chọn file; có impact trực tiếp)

```text
+------------------------------------------------------------------------------+
| Import glossary YAML                                        (Bottom Sheet)   |
| [ Choose file… ]  or paste below                                             |
| +--------------------------------------------------------------------------+ |
| | terms:                                                                   | |
| |   - source: Enforcer                                                     | |
| |     target: Cảnh vệ                                                      | |
| +--------------------------------------------------------------------------+ |
| {validation [UI]: terms[0]: missing required field 'target'}   (nếu lỗi)     |
| Importing flags {stale_after: 5} episodes stale. The current glossary is     |
| kept in History.                                                             |
| [ Cancel ]                                                        [ Import ] |
+------------------------------------------------------------------------------+
```

**Export YAML** (liên kết server, không `blob:`; có Copy vì là chữ)

```text
+------------------------------------------------------------------------------+
| Export glossary YAML                                        (Bottom Sheet)   |
| [ Download glossary-arcane-vi.yaml ]        (liên kết server)                |
| [ Copy YAML ]    (nếu clipboard bị chặn: ô chữ chọn-tất-cả để chép tay)      |
| +--------------------------------------------------------------------------+ |
| | terms:                                                                   | |
| |   - source: Enforcer ...                                                 | |
| +--------------------------------------------------------------------------+ |
|                                                                 [ Close ]    |
+------------------------------------------------------------------------------+
```

### 2d) Characters (`#/series/:id/characters`)

Mỗi nhân vật một thẻ gồm **Name** (glossary `characters`) và **Voice** (thẻ nhân vật). Xưng hô chỉ đọc, sửa ở Forms of address. **Không có ô nhập `address`.**

```text
+------------------------------------------------------------------------------+
| Saving flags {stale_after: 0} … (dòng impact trực tiếp)   [ + Add character ]|
+------------------------------------------------------------------------------+
| +--------------------------------------------------------------------------+ |
| | NAME                                                                     | |
| | Source name   {name: Vi}        Translated {target: Vi}                  | |
| | Gender        {gender: f}                                                | |
| | Description   {description: Brawler from Zaun, Powder's sister}         | |
| |                                                                          | |
| | VOICE                                                                    | |
| | Speech        {speech: Blunt, street slang, short sentences}             | |
| | Aliases       {aliases: Violet}                                          | |
| | Catchphrases  {catchphrases: "Cupcake"}                                  | |
| | Samples       {samples: 2 lines}                                         | |
| | Style         {style: Swearing = Keep}                                   | |
| | [x] Always include in the prompt ({always})                              | |
| |                                                                          | |
| | Forms of address (read only)                                             | |
| |   with Powder: chị–em / em–chị                                           | |
| |   with Caitlyn: tôi–cô / tôi–cô                                          | |
| |   [ Manage in Forms of address → ]                                       | |
| |                                                                          | |
| | [ Edit ]                                                    [ Delete ]   | |
| +--------------------------------------------------------------------------+ |
| (thẻ thứ hai xếp ngay dưới)                                                  |
+------------------------------------------------------------------------------+
```

- Trạng thái rỗng: `No character cards yet. Add characters to define their names and voices.   [ + Add character ]`
- **Add/Edit** (bottom sheet): hai phần Name và Voice với đủ trường trên, `Style` là các Segmented Control có Inherit đầu tiên, dòng impact trực tiếp, `[ Cancel ]` `[ Save ]` (lưu ghi vào hai nơi, impact cộng cả hai). Thẻ đã lưu sẵn `address` thì hiện chỉ đọc dưới Voice.
- **Delete**: bottom sheet `[ Cancel ]` `[ Confirm delete ]` kèm dòng impact.

### 2e) Context (`#/series/:id/context`)

```text
+------------------------------------------------------------------------------+
| STORY MEMORY                                                                 |
| {missing_facts [UI]: 2} episodes have a source subtitle but no facts.        |
|                                         [ Extract facts (2 episodes) ]       |
+------------------------------------------------------------------------------+
| 1 · S01E01 · {title}                       Source ✓ · Facts ✓                |
| 2 · S01E02 · {title}                       Source ✓ · Facts ✓                |
| 3 · S01E03 · {title}                       Source ✓ · Facts ✗                |
| 4 · S01E04 · {title}                       Source ✓ · Facts ✗                |
| 5 · S01E05 · {title}                       Source ✗ · Facts ✗                |
+------------------------------------------------------------------------------+
```

`{has_source}` và `{has_facts}` theo thứ tự câu chuyện. Không có token đã dùng, không có trạng thái "generating". **Extract facts** → Estimate: số tập có source mà chưa có facts, một lời gọi mỗi tập (`memory_model`).

---

## 3. Trang tập và trang Unit

`#/series/:id/episodes/:ep` (tập) và `#/unit/:id` (phim) dùng chung layout. Phim: không có tab **Context**, có liên kết **Movie Glossary** ở header.

### Header

```text
+------------------------------------------------------------------------------+
| [ < Arcane ]   S01E08 · {title: Oil and Water}      (phim: [ < Library ])    |
| Edited · Missing units: {missing_units: none}                                |
| Current v{version: 3} · {origin: edit} · {model: gemini-3.1-flash-lite}      |
| {created_at: 2026-11-15 14:20}                         [ Remove output ]     |
| (phim:)                                          [ Movie Glossary → ]        |
+------------------------------------------------------------------------------+
| [Subtitles] [Corrections] [Retranslate] [History] [Context] [Style]  → cuộn  |
+------------------------------------------------------------------------------+
```

`[ Remove output ]` **chỉ ở đây**:

```text
+------------------------------------------------------------------------------+
| Remove subtitle output?                                     (Bottom Sheet)   |
| This deletes the target subtitle file. The version stays in History and can  |
| be restored.                                                                 |
| [ Cancel ]                                                [ Remove output ]  |
+------------------------------------------------------------------------------+
```

### 3a) Subtitles

```text
+------------------------------------------------------------------------------+
| [ Search text…                      {q} ]    Jump to  [ 00:14:20 ] [ Jump ]  |
+------------------------------------------------------------------------------+
| +--------------------------------------------------------------------------+ |
| | Cue #{n: 142} · {start: 00:14:20,100} → {end: 00:14:23,400}              | |
| | Source  You don't understand what's at stake here, Caitlyn.              | |
| | Target  Cô không hiểu chuyện gì đang bị đe doạ đâu.      [ Edit cue ]    | |
| +--------------------------------------------------------------------------+ |
| | Cue #{n: 143} · 00:14:24,000 → 00:14:25,800         [🔒 Locked]          | |
| | Source  Then explain it to me!                                           | |
| | Target  Vậy thì nói cho tôi rõ đi!                      [ Edit cue ]     | |
| +--------------------------------------------------------------------------+ |
+------------------------------------------------------------------------------+
```

Sửa cue bằng bottom sheet (không sửa inline):

```text
+------------------------------------------------------------------------------+
| Edit cue #{n: 143} ({start} → {end})                        (Bottom Sheet)   |
| [🔒 Locked]                                                  [ Unlock ]      |
| Source ({source})                                                            |
| Then explain it to me!                                                       |
| Target ({target})                                                            |
| +--------------------------------------------------------------------------+ |
| | Vậy thì nói cho tôi rõ đi!                                               | |
| +--------------------------------------------------------------------------+ |
| Saving locks this cue.                                                       |
| [ < Prev ]  [ Next > ]                          [ Cancel ]   [ Save ]        |
+------------------------------------------------------------------------------+
```

### 3b) Corrections

```text
+------------------------------------------------------------------------------+
| Add a correction                                                             |
| Kind   [ Term | Address | Note | Cue edit ]                                 |
| {payload}  [ "Council" → "Hội Đồng"                                      ]   |
| Scope  [ Episode | From here on | Whole series ]              [ + Add ]      |
+------------------------------------------------------------------------------+
| [Term]     "Enforcer" → "Cảnh vệ" · Whole series                [ Remove ]   |
| [Note]     Scene at the bridge is whispered · Episode           [ Remove ]   |
| [Cue edit] Cue #143 locked · 10m ago                            [ Remove ]   |
+------------------------------------------------------------------------------+
```

### 3c) Retranslate

```text
+------------------------------------------------------------------------------+
| Scope  [ Whole episode | Cue range | Time range ]                            |
|   (Cue range)  from #[ 100 ] to #[ 150 ]                                     |
|   (Time range) from [ 00:10:00 ] to [ 00:15:00 ]                             |
| Attach corrections ({correction_ids}):                                       |
|   [x] [Term] "Enforcer" → "Cảnh vệ" (Whole series)                           |
|   [x] [Note] Scene at the bridge is whispered (Episode)                      |
|   [ + Add a new correction ]                           ({new_corrections})   |
|                                                                              |
| Will overwrite {overwrite [UI]: 336} cues and keep {locked [UI]: 4} locked.  |
| The result replaces the current subtitle now; the old one stays in History.  |
|                                              [ Retranslate ]  → Estimate      |
+------------------------------------------------------------------------------+
```

Lỗi khi dải không hợp lệ: `Range retranslate needs an existing translation with the same number of cues.`

### 3d) History

Không có nút Remove output ở đây.

```text
+------------------------------------------------------------------------------+
| v4  [Candidate]   {trigger: variant} · {origin: machine} · {status: candidate} |
|     {model: gemini-3.1-flash-lite} · {tokens_out: 14,200} · 10m ago          |
|     label "gritty" ({params.variant.label})                                  |
|              [ Diff ]   [ Compare ]   [ Use this version ]                   |
+------------------------------------------------------------------------------+
| v3  (current)   {trigger: cue_edit} · {origin: edit} · {status: current}     |
|     1d ago                                                      [ Diff ]     |
+------------------------------------------------------------------------------+
| v2  {trigger: retranslate} · {origin: machine} · {status: superseded}        |
|     gemini-3.1-flash-lite · 3d ago                [ Diff ]  [ Restore ]      |
+------------------------------------------------------------------------------+
```

### 3e) Context (chỉ tập)

Chỉ các section chữ của story memory. Không có version glossary hay style, không có lý do stale.

```text
+------------------------------------------------------------------------------+
| EPISODE CONTEXT                              [!] Changed ({changed: true})   |
| Missing earlier episodes: {missing: S01E06, S01E07}                          |
+------------------------------------------------------------------------------+
| Used for the current translation ({used.sections})                           |
|   Story so far                                                               |
|   {body: Vi and Powder grew up together in Zaun …}                           |
+------------------------------------------------------------------------------+
| If translated now ({now.sections})                                           |
|   Relationship state                                                         |
|   {body: Vi and Caitlyn are allies …}                                        |
+------------------------------------------------------------------------------+
```

### 3f) Style (ghi đè cấp tập)

```text
+------------------------------------------------------------------------------+
| [i] Overrides the series style for this episode only.                        |
|                                              [ Clear episode override ]      |
+------------------------------------------------------------------------------+
| Preset [ Inherit v ]                                                         |
| Formality            [ Inherit · now Neutral ][ Casual ][ Neutral ][ Formal ] |
| Tone                 [                                        ] [ Inherit ]   |
| Swearing             [ Inherit · now Soften ][ Keep ][ Soften ][ Remove ]     |
| Idioms and wordplay  [ Inherit · now Balanced ][ Literal ][ Balanced ][ Adapt ]|
| [ > More ]  (Dialect, Honorifics, Characters per second, Longest line,       |
|              Lines per cue, Fast dialogue, Notes)                            |
| Saving flags {stale_after: 1} episode stale.                     [ Save ]    |
+------------------------------------------------------------------------------+
```

---

## 4. Compare (`#/series/:id/episodes/:ep/compare`)

```text
+------------------------------------------------------------------------------+
| [ < S01E03 ]   Current v3  vs  Candidate v4 "gritty"                         |
| [x] Show only differing cues ({differs}: 48 of 342)                          |
+------------------------------------------------------------------------------+
| +--------------------------------------------------------------------------+ |
| | Cue #{n: 12}                                    [x] Pick variant         | |
| | Source   Get out of my way, enforcer!                                    | |
| | Current  Tránh đường cho tôi, cảnh vệ!                                   | |
| | Variant  Cút khỏi đường tao, tên cớm kia!                                | |
| +--------------------------------------------------------------------------+ |
| | Cue #{n: 19}                                    [ ] Pick variant         | |
| | Source   We don't have time for this.                                    | |
| | Current  Chúng ta không có thời gian cho việc này đâu.                   | |
| | Variant  Bọn mình không còn giờ cho chuyện này đâu.                      | |
| +--------------------------------------------------------------------------+ |
+------------------------------------------------------------------------------+
| STICKY: Selected 12 from variant, 330 kept from current                      |
| [ Apply picked cues ]                              [ Use this version ]      |
+------------------------------------------------------------------------------+
```

`Use this version` (cả bản variant) mở xác nhận có checkbox lưu style. `Apply picked cues` **không** có checkbox này (bản ghép không thuộc một style nào).

```text
+------------------------------------------------------------------------------+
| Use candidate v4 as the current translation?                (Bottom Sheet)   |
| This replaces the current subtitle file; v3 stays in History.                |
|                                                                              |
| [x] Also apply this variant's style to the series                            |
|     Preset     {params.variant.preset: —}                                    |
|     Formality  {params.variant.style.formality: Casual}                      |
|     Swearing   {params.variant.style.profanity: Keep}                        |
|     Saving flags {stale_after: 11} episodes stale.                           |
| [ Cancel ]                                                      [ Confirm ]  |
+------------------------------------------------------------------------------+
```

---

## 5. Activity (`#/activity`)

```text
+------------------------------------------------------------------------------+
| PROVIDERS            (calls left today: shown in the Estimate box only)      |
| +--------------------------------------------------------------------------+ |
| | {model: gemini-3.1-flash-lite}      closed · usable · failures 0         | |
| +--------------------------------------------------------------------------+ |
| | {model: gemini-3.5-flash}           exhausted · not usable               | |
| |   {reason: daily quota} · until {until: 2026-11-17 00:00} · failures 3   | |
| +--------------------------------------------------------------------------+ |
+------------------------------------------------------------------------------+
| MODEL LAYERS    (done {window.done_days: 30} days / failed {window.failed_days: 90} days, read only)
| {by_model: gemini-3.1-flash-lite}   done 412 · failed 8                      |
| {by_model: gemini-3.5-flash}        done 29 · failed 2                       |
| Escalated {escalated.total: 35}: done 29 · failed 2 · waiting 4              |
+------------------------------------------------------------------------------+
| CATALOG   Synced {synced_at: 2m ago} · {status: ok} · {items: 1,240} items   |
|           {warning: —}                                      [ Sync now ]     |
+------------------------------------------------------------------------------+
| JOBS  (up to 100 per tab; tab counts are real)         [ Retry all failed ]  |
| [Requested 2] [Running 1] [Waiting 4] [Failed 3] [Done 441]       → cuộn     |
+------------------------------------------------------------------------------+
| Arcane · S01E04 · vi                                                         |
| manual · fill · translating {progress_done: 120}/{progress_total: 340}       |
| restarts {attempts: 0}                                          [ Stop ]     |
+------------------------------------------------------------------------------+
| Arcane · S01E05 · vi                                                         |
| policy · fill · Waiting · context (needs {missing_units: S01E04})            |
|                                                  [ Request that episode ]    |
+------------------------------------------------------------------------------+
| Severance · S01E02 · vi                                                      |
| retranslate · replace · Waiting · quota · retry at {retry_at: 00:00}         |
+------------------------------------------------------------------------------+
| Arcane · S01E07 · vi                                                         |
| manual · variant · Failed · {error: Output token limit exceeded}             |
| restarts {attempts: 3}               [ Retry ]    [ Export job spec ]        |
+------------------------------------------------------------------------------+
```

- **Retry all failed** → Estimate: `Jobs {counts.failed: 3}`, `Model calls: unknown`.
- **Export job spec** → liên kết server + Copy (bản xuất dạng chữ, UX 6.6).
- Tên tab là Requested · Running · Waiting · Failed · Done (không dùng tên trạng thái nội bộ).

---

## 6. Settings (`#/settings`)

Bảy thẻ gập, mỗi thẻ có **Save** riêng và chỉ báo `● Unsaved changes`; thẻ 1 và 2 mở sẵn. Nhãn trên, ô nhập dưới. Dưới đây vẽ cả bảy thẻ mở để đủ trường.

```text
+------------------------------------------------------------------------------+
| [v] 1. CONNECTIONS                    [● Unsaved changes]       [ Save ]     |
+------------------------------------------------------------------------------+
| Bazarr URL ({bazarr_url})                                                    |
| [ http://bazarr:6767                                                     ]   |
|                                                                              |
| API keys                                                                     |
| Bazarr          ••••9a1f · ok                                                |
|   New key [                              ]                     [ Test ]      |
| TMDB            ••••44b2 · ok                                                |
|   New key [                              ]                     [ Test ]      |
| Gemini 1        ••••88c0 · exhausted                                         |
|   New key [                              ]                     [ Test ]      |
| Gemini 2        ••••12d4 · invalid                                           |
|   New key [                              ]                     [ Test ]      |
| OpenAI-compatible   (not set)                                                |
|   New key [                              ]                     [ Test ]      |
|   Pasting a new key, or a Test that succeeds, sets the key back to ok.       |
|                                                                              |
| LLM provider ({llm_provider})   [ Gemini | OpenAI-compatible ]               |
| Base URL ({llm_base_url})       [                          ]                 |
|   (Base URL only when OpenAI-compatible)                                     |
+------------------------------------------------------------------------------+
| [v] 2. TRANSLATION                                              [ Save ]     |
+------------------------------------------------------------------------------+
| Default model ({gst_model})          [ gemini-3.1-flash-lite   v ]           |
| Strong model ({gst_fallback_model})  [ gemini-3.5-flash  v ]  [ Clear ]      |
| Research model ({research_model})    [ (same as Default)       v ]           |
| Temperature ({gst_temperature})      [ 0.7 ]                                 |
| Thinking budget ({gst_thinking_budget}) [ 2048 ]                             |
| [ ] No thinking ({gst_no_thinking})   [ ] Paid quota tier ({gst_paid_quota}) |
| Max input tokens ({llm_max_input_tokens})    [ 128000 ]                      |
| Max output tokens ({llm_max_output_tokens})  [ 8192 ]                        |
| Source languages ({source_languages})                                        |
|   [x] English (en)  [x] Chinese (zh)  [ ] Japanese (ja)   [ + Add language ] |
| Target languages ({target_languages})                                        |
|   [x] Vietnamese (vi)                                     [ + Add language ] |
+------------------------------------------------------------------------------+
| [>] 3. LIBRARY                                                  [ Save ]     |
+------------------------------------------------------------------------------+
| Media roots ({media_roots})                                                  |
|   [ /media                               ] [ Remove ]       [ + Add path ]   |
| Sync every, seconds ({catalog_sync_interval})   [ 300 ]  (0 = off)           |
| Ignore a read that shrinks by, % ({catalog_shrink_guard})  [ 20 ]            |
| Auto-request ({auto_request})        [ Off | On ]                           |
|   Off by default. Translates missing items of chosen series without asking.  |
| Auto-request daily cap ({auto_request_daily_cap})   [ 0 ]  (0 = no cap)      |
+------------------------------------------------------------------------------+
| [>] 4. QUEUE AND QUOTA                                          [ Save ]     |
+------------------------------------------------------------------------------+
| Settle window, seconds ({job_settle_seconds})          [ 600 ]               |
| Breaker threshold ({breaker_threshold})                [ 3 ]                 |
| Max crashes ({max_crashes})                            [ 3 ]                 |
| Max calls per job ({max_calls_per_job})                [ 20 ]  (0 = no cap)  |
| Max calls per day ({max_calls_per_day})                [ 0 ]   (0 = no cap)  |
| Transient retry, seconds ({transient_retry_seconds})   [ 30 ]                |
| Provider quota resets at midnight in ({quota_reset_tz})                      |
| [ America/Los_Angeles  v ]                                                   |
|   This is the provider's midnight (Gemini), not your local time zone.        |
+------------------------------------------------------------------------------+
| [>] 5. STORY AND STYLE                                          [ Save ]     |
+------------------------------------------------------------------------------+
| Story memory ({memory_enabled})        [ Off | On ]                         |
| Memory model ({memory_model})          [ (same as Default)  v ]              |
| Summarize model ({summarize_model})    [ (same as Memory)   v ]              |
| Gap wait, seconds ({memory_gap_wait_seconds})   [ 1800 ]  (0 = never wait)   |
| Memory budget, tokens ({memory_budget_tokens})  [ 8000 ]                     |
| Recent episodes ({memory_recent_episodes})      [ 3 ]                        |
| Few-shot budget, tokens ({few_shot_budget_tokens})   [ 600 ]  (0 = off)      |
| Filter terms above ({glossary_filter_threshold})  [ 50 ] terms  (0 = never)  |
| Merge cue edits within, minutes ({edit_coalesce_minutes})  [ 10 ]            |
+------------------------------------------------------------------------------+
| [>] 6. MAINTENANCE                                              [ Save ]     |
+------------------------------------------------------------------------------+
| Log level ({log_level})     [ DEBUG | INFO | WARNING | ERROR ]               |
| Keep prompt bodies ({log_prompt_bodies})                                     |
|   [ All | Sample | Errors only | Off ]                                        |
| Max body size, bytes ({log_body_max_bytes})   [ 262144 ]                     |
| Max log size, MB ({log_max_mb})               [ 500 ]  (0 = no limit)        |
| Keep logs, days ({log_retention_days})        [ 30 ]   (0 = no limit)        |
| Keep events, days ({events_retention_days})   [ 90 ]   (0 = forever)         |
| Cost per million tokens, in / out ({llm_cost_in_per_mtok} / {llm_cost_out_per_mtok})
|   [ 0 ] / [ 0 ]                                                              |
| Keep finished jobs, days ({retention_done_days})       [ 30 ]                |
| Keep failed jobs, days ({retention_failed_days})       [ 90 ]                |
| Keep versions per subtitle ({retention_versions_keep}) [ 5 ]                 |
| Keep glossary history ({retention_glossary_keep})      [ 20 ]                |
| [ Clean up now ]            [ Backups → ]            [ Logs → ]              |
+------------------------------------------------------------------------------+
| [>] 7. SECURITY                                                              |
+------------------------------------------------------------------------------+
| Current password   [                          ]                              |
| New password (8+)  [                          ]                              |
|                                                  [ Update password ]         |
+------------------------------------------------------------------------------+
```

Trạng thái: lưu `[ Saving… ]` → `✓ Saved` · lỗi `Save failed: {error}` · Test `[ Testing… ]` → `✓ Key valid` (khoá về `ok`) hoặc `✗ Rejected` (khoá về `invalid`) · rời trang khi còn thay đổi: hộp 0e.

Xác nhận **Clean up now** (xem trước rồi xác nhận):

```text
+------------------------------------------------------------------------------+
| Clean up now                                                (Bottom Sheet)   |
| Preview: removes {files: 142} items and frees {bytes: 38.4 MB}.              |
| Media folders are never touched.                                             |
| [ Cancel ]                                                  [ Confirm ]      |
+------------------------------------------------------------------------------+
```

---

## 7. Backups và Logs

### 7a) Backups (`#/backups`)

```text
+------------------------------------------------------------------------------+
| [ < Settings ]   BACKUPS                                                     |
| [i] A backup holds settings only. Keys are left out unless you tick below.   |
| [ ] Include keys                                      [ Create backup ]      |
| [ Import backup… ]                                                           |
+------------------------------------------------------------------------------+
| backup-2026-11-16-1000.zip     2026-11-16 10:00 · 14 KB        [ Download ]  |
| backup-2026-11-10-0830.zip     2026-11-10 08:30 · 16 KB        [ Download ]  |
+------------------------------------------------------------------------------+
```

`[ Download ]` là liên kết server; không có Copy (tệp nén).

### 7b) Logs (`#/logs`)

```text
+------------------------------------------------------------------------------+
| [ < Settings ]   LOGS                                                        |
| Level [ All | DEBUG | INFO | WARNING | ERROR ]                               |
| [ Search…                                  ]                [ Clear logs ]   |
+------------------------------------------------------------------------------+
| 21:58:01 INFO   worker.job   S01E04 batch 3/9 translated (40 cues, 1.8 s)    |
| 21:55:12 WARN   provider     Key 1 exhausted the daily quota, using key 2    |
| 21:40:00 ERROR  worker.job   S01E07 failed after 3 attempts: output limit    |
+------------------------------------------------------------------------------+
```

`[ Clear logs ]` → bottom sheet `[ Cancel ]` `[ Confirm clear ]`.

---

## 8. Khổ ngang (≥ 1024 px): khác gì khổ dọc

Cùng bố cục. Chỉ các bảng và lưới mở rộng ra cột; mọi thứ khác (nút, hộp thoại, trạng thái) giữ nguyên.

| Màn hình | Khổ dọc (< 1024 px) | Khổ ngang (≥ 1024 px) |
|---|---|---|
| Subtitles | thẻ mỗi cue | bảng: `#` · timecode · Source · Target · khoá |
| Compare | thẻ mỗi cue | bảng: `#` · Source · Current · Variant · chọn |
| Try 20 lines | thẻ mỗi cue | bảng 3 cột (Source · Now · With this style), hoặc 2 cột khi series mới |
| Glossary: Terms | thẻ | bảng: Source · Target · Locked · Note · From · Until |
| Glossary: Forms of address | thẻ | bảng: A · B · A calls B · B calls A · From |
| Characters | một cột thẻ | lưới hai cột thẻ |
| Activity: Providers | thẻ | bảng: Model · State · Reason · Until · Failures · Usable |
| Context (tập) | hai khối xếp chồng | hai cột cạnh nhau: Used · If translated now |

Ví dụ bảng Subtitles ở khổ ngang:

```text
+------+-------------------------+--------------------------------+------------------------+------+
| #    | Timecode                | Source                         | Target (chạm để sửa)   | Lock |
+------+-------------------------+--------------------------------+------------------------+------+
| 142  | 00:14:20,100 → 00:14:23 | You don't understand what's at | Cô không hiểu chuyện gì|      |
|      |                         | stake here, Caitlyn.           | đang bị đe doạ đâu.    |      |
| 143  | 00:14:24,000 → 00:14:25 | Then explain it to me!         | Vậy thì nói cho tôi rõ | 🔒   |
+------+-------------------------+--------------------------------+------------------------+------+
```

---

## 9. Còn mở

Điểm còn mở của giao diện chỉ ghi ở **UX §8** (không nhắc lại ở đây). Ảnh hưởng tới wireframe này:

- Số Failed và chip Failed/Ignored ở Home (mục 1).
- Glossary và style cấp phim: đường dẫn API cho **Movie Glossary** và tab Style của phim (mục 3).
- Try 20 lines với series mới: API có trả `current` rỗng hay không (mục 2b).
- Trang đăng nhập có nhận tham số quay về `#hash` không (mục 0b).
- Dòng impact của Memory và Specials after: phụ thuộc `PUT /api/series/{id}/memory` có `dry_run` (mục 2, header).
