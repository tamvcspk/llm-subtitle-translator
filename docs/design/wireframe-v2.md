# Wireframe v2.1 — khổ dọc là chính

> Trạng thái: **v2.4** · 2026-11-27 · Vẽ theo [UX.md](UX.md) v0.24 và [wireframe-prompt.md](wireframe-prompt.md). v2.4 cập nhật P19d (mục 2 Header): hộp vàng Bootstrap đồng nhất số đếm theo ngôn ngữ đích có phụ đề nguồn, phân biệt 4 trạng thái thông điệp, nút `Auto-request remaining N…` qua hộp Estimate/Confirm có cảnh báo cam khi glossary rỗng, và dải `Hold again` kèm tuỳ chọn huỷ job `policy` đang chờ. v2.3 cập nhật P19a/P19b: hộp Estimate (0d, 2e) hiện model theo từng nhóm lời gọi + `Total calls (est.)`; Settings `2. Translation` (6) gom đủ các ô chọn model, placeholder động, cảnh báo dùng chung model và bảng `Active models by task`; Glossary `2c` (SegmentedControl, gom cặp & dọn trùng qua `aliases`, cảnh báo danh xưng chung trên rule cũ) và Characters `2d` (`Aliases` ở phần `Name`, chặn danh xưng chung khi sửa). v2.2 thêm mục 5b (Insights, nút Golden set mở bottom sheet có Download và Copy), 5c (Review) và biến thể Research (0d, 2c, Activity, Settings). **Khi khác UX.md thì UX.md thắng.** Điểm còn mở: UX §8.
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

Dùng cho: Translate (tập, mùa, series, tất cả), Translate again, Retranslate, Retry, Retry all failed, Compare on a whole episode, Ask the judge, Request that episode; biến thể Extract facts (2e) và Research terms (bên dưới).

```text
+------------------------------------------------------------------------------+
| {action [UI]: Translate season 1}                           (Bottom Sheet)   |
+------------------------------------------------------------------------------+
| Jobs                {estimate.jobs: 8}                                       |
| Cues                {estimate.cues: 2,740}                                   |
| Translate (est.)    {estimate.calls: 16} calls · {estimate.model [NEW]}      |
|   ("Judge (est.)" cho Ask the judge; "unknown · {model}" khi Retry không rõ) |
| Extract facts       {estimate.extract_calls: 3} calls · {extract_model}      |
| Condense story      {estimate.summarize_calls: 1} call · {summarize_model}   |
| Total calls (est.)  {total [UI]: 20}   (chỉ hiện khi có ≥ 2 nhóm lời gọi)    |
| Calls left today    {estimate.calls_left_today: 42}   (hidden when no cap)   |
| Paused until        {estimate.paused_until: 00:00}    (only when quota spent)|
| Time                {estimate.minutes: ~5} min · done ~{estimate.finish_at}  |
|                                                                              |
| [ Cancel ]                                                      [ Confirm ]  |
+------------------------------------------------------------------------------+
```

**Biến thể Research terms** (P16; UX 6.7). Thêm ba dòng riêng của tác vụ `research` và một ô chọn:

```text
+------------------------------------------------------------------------------+
| Research terms for Arcane (vi)                              (Bottom Sheet)   |
+------------------------------------------------------------------------------+
| Scope                   {terms_count: 14} unmapped terms (in ≥ 2 episodes)   |
| Model calls (est.)      {estimate.calls: 2}  (1 web search + 1 extract)      |
|                         (one search call may run several queries)            |
| Models                  {research_model} (search) · {extract_model} (extract)|
| Series research budget  {used: 2} / {research_max_calls_per_series: 10}      |
|                         (after this run: 4 / 10)                             |
| Calls left today        {estimate.calls_left_today: 42}   (hidden when no cap)|
|                                                                              |
| [x] Auto-accept terms with 2+ independent sources                            |
|     (shows the saved setting; unchecking applies to this run only)           |
| Auto-accepted terms flag the episodes that use them as stale. Nothing is     |
| retranslated. Every other result goes to Review.                             |
| [ Cancel ]                                                      [ Confirm ]  |
+------------------------------------------------------------------------------+
```

- `{extract_model}` là model của lời gọi 2. `ARCHITECTURE.md` (§3.5, `model_for_task`) cho tác vụ `extract` là `memory_model`; kiểm lại khi dựng xem lời gọi 2 chạy tác vụ nào.
- Số lời gọi và ngân sách còn lại đọc từ Estimate; ngân sách hết thì không mở được hộp này (nút bị tắt, xem 2c).

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

### Header chung (cả 6 tab)

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
| (khi bootstrap.locked = true — P19d: chỉ đếm tập có phụ đề nguồn theo lang)  |
| [!] Auto-request is holding {held_by_lang[lang]: 99} episodes for glossary   |
| Only the first {limit: 2} episodes with source subtitles were requested      |
| ({no_source: 22} other episodes have no source subtitle yet).                |
| {message [UI]: theo 1 trong 4 trạng thái: pending > 0 / job đang chạy /      |
|  Story memory đang tắt / 0 đề xuất và glossary đang rỗng}                    |
| [ Review suggestions (4) | View in Activity | Open Glossary ]                |
|                                   [ Auto-request 99 without glossary… ]      |
+------------------------------------------------------------------------------+
| (khi bootstrap.released = true & chưa có glossary/card được duyệt — P19d)    |
| Glossary hold is off ({queued_policy_jobs: 14} auto-requests in line).       |
|                                                             [ Hold again ]   |
+------------------------------------------------------------------------------+
| [Episodes] [Style] [Glossary] [Characters] [Context] [Suggestions (4)]       |
+------------------------------------------------------------------------------+
```

**Hộp Estimate khi bấm `[ Auto-request {N} without glossary… ]`** (P19d, `PUT /api/catalog/series/{id}/bootstrap` với `estimate_only: true`; xác nhận gọi `AutoRequest.run()` ngay):

```text
+------------------------------------------------------------------------------+
| Auto-request remaining episodes · {title}                   (Bottom Sheet)   |
+------------------------------------------------------------------------------+
| Episodes to release   {estimate.jobs: 99}                                    |
|   (từ 2 ngôn ngữ đích trở lên: nhãn "Translations to release" và thêm dòng   |
|    "By language  99 vi + 21 fr" ngay dưới; R19-8)                            |
|   (khi daily_cap > 0: "up to {cap_left} today · daily cap {daily_cap}")      |
| Cues                  {estimate.cues: 48,210}                                |
| Translate (est.)      {estimate.calls: 99} calls · {estimate.model}          |
| Extract facts         {estimate.extract_calls: 99} calls · {extract_model}   |
| Condense story        {estimate.summarize_calls: 96} calls · {summarize_mdl} |
| Total calls (est.)    {total [UI]: 294}                                      |
| Calls left today      {estimate.calls_left_today: 1,000}                     |
| Time                  {estimate.minutes: ~28} min · done ~{finish_at}        |
|                                                                              |
| [!] ORANGE WARNING (khi bootstrap.glossary_empty = true)                     |
| Glossary is empty                                                            |
| No terms, characters, or forms of address are approved yet. Character names  |
| and pronouns may vary across seasons.                                        |
|                                                                              |
| [ Cancel ]                         [ Release & auto-request ({jobs: 99}) ]   |
+------------------------------------------------------------------------------+
```

**Hộp xác nhận `[ Hold again ]`** (khi `queued_policy_jobs > 0`; bằng 0 thì khoá lại ngay không mở hộp):

```text
+------------------------------------------------------------------------------+
| Hold auto-request for {title}?                              (Bottom Sheet)   |
| Auto-request will stop queueing further episodes until the glossary is set.  |
| [x] Also cancel {queued_policy_jobs: 14} queued auto-request jobs            |
|     (running jobs finish their current batch; manual jobs are untouched)     |
| [ Cancel ]                                                   [ Hold again ]  |
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
| RESEARCH TERMS                                                               |
| [ Research terms (14) ]                                                      |
| (dải trạng thái job research hiện ngay bên dưới khi có job)                  |
+------------------------------------------------------------------------------+
| Version v{version: 4}   [ History ] [ Copy from another series ]             |
|                         [ Import YAML ] [ Export YAML ]                      |
+------------------------------------------------------------------------------+
| [ Terms (18)                      | Forms of address (76)                  ] |
| [ Filter forms of address…                                        {filter} ] |
+------------------------------------------------------------------------------+
| (khi tab = Terms)                                            [ + Add term ]  |
| +--------------------------------------------------------------------------+ |
| | Source ({source})  [ Enforcer             ]  Target [ Cảnh vệ          ] | |
| | [x] Locked ({locked})                                                    | |
| | Note ({note})      [ Piltover police      ]                              | |
| | From episode ({from}) [ S01E01 ]   Until ({until}) [        ] [ Delete ] | |
| +--------------------------------------------------------------------------+ |
|                                                                              |
| (khi tab = Forms of address)                                                 |
| [ Clean up duplicates (4) ]                           [ + Add address rule ] |
|                                                                              |
| Malcolm ↔ Caroline Miller                                     [ 2 stages ]   |
| +--------------------------------------------------------------------------+ |
| | Person A [ {a: Malcolm}         ]  Person B [ {b: Caroline Miller}     ] | |
| | A calls B ({a_to_b}) [ em–cô    ]  B calls A ({b_to_a}) [ cô–em        ] | |
| | From ({from}) [          ]         Until ({until}) [          ][ Delete ]| |
| +--------------------------------------------------------------------------+ |
| | Written via alias: Carolyn Miller (merges on Clean up duplicates)        | |
| | Person A [ {a: Malcolm}         ]  Person B [ {b: Carolyn Miller}      ] | |
| | A calls B ({a_to_b}) [ em–cô    ]  B calls A ({b_to_a}) [ cô–em        ] | |
| | From ({from}) [          ]         Until ({until}) [          ][ Delete ]| |
| +--------------------------------------------------------------------------+ |
|                                                                              |
| Malcolm ↔ Mom                                                                |
| +--------------------------------------------------------------------------+ |
| | [!] "Mom" is a generic form of address, not a character. Replace it with | |
| |     a proper name (e.g. Lois) or delete this rule.                       | |
| | Person A [ {a: Malcolm}         ]  Person B [ {b: Mom}                 ] | |
| | A calls B ({a_to_b}) [ con–mẹ   ]  B calls A ({b_to_a}) [ mẹ–con       ] | |
| |                                                                [ Delete ]| |
| +--------------------------------------------------------------------------+ |
+------------------------------------------------------------------------------+
| [● Unsaved changes]  Saving flags {stale_after: 5} episodes stale.           |
| (sticky bottom bar, safe-area)                    [ Discard ]     [ Save ]   |
+------------------------------------------------------------------------------+
```

**Research terms (N)** (P16, UX 6.7): `N` là số term xuất hiện ở ≥ 2 tập mà chưa có trong glossary, đếm cục bộ (không tốn model). Bấm mở hộp Estimate biến thể Research (0d). Dải trạng thái dưới thanh nút, mẫu giống **Extract facts** ở tab Context; mỗi trạng thái có đúng một việc kế tiếp:

```text
+------------------------------------------------------------------------------+
| Đang chạy:    Researching terms… (searching web · 1/2)             [ Stop ]  |
|               (Stop giữ phần đã có: kết quả đã ghi vẫn vào Review)           |
+------------------------------------------------------------------------------+
| Chờ quota:    Waiting · quota · retry at {retry_at: 00:00}                   |
+------------------------------------------------------------------------------+
| Xong:         [i] Research finished: 2 auto-accepted (flag 3 episodes        |
|               stale), 4 pending.                         [ Open Review → ]   |
+------------------------------------------------------------------------------+
| Lỗi:          [!] Research failed: {error}                      [ Retry ]    |
|               (Retry qua Estimate)                                           |
+------------------------------------------------------------------------------+
| N = 0:        [ Research terms (0) ] tắt · No unmapped terms found.          |
+------------------------------------------------------------------------------+
| Provider:     [ Research terms ] tắt · Web research requires Gemini         |
|               (unavailable on OpenAI-compatible).          [ Settings → ]    |
+------------------------------------------------------------------------------+
| Hết ngân sách: [ Research terms ] tắt · Research budget reached for this     |
|               series (10/10 calls used).                   [ Settings → ]    |
+------------------------------------------------------------------------------+
```

- Hai trạng thái "tắt vì provider" và "tắt vì ngân sách" khác nhau: cái đầu cố định, cái sau gỡ được bằng cách nâng ngân sách ở Settings → Translation.
- Chỉ series có `auto_request` bật mới được research tự chạy (cùng luồng bootstrap P13); mọi series khác chỉ chạy khi admin bấm nút này (UX nguyên tắc 3).

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

Mỗi nhân vật một thẻ gồm **Name** (glossary `characters`, gồm cả `aliases` — P19b) và **Voice** (thẻ nhân vật). Xưng hô chỉ đọc (gom theo cặp kèm cửa sổ `from–until` và nhận biết cả rule viết bằng `aliases`), sửa ở Forms of address. **Không có ô nhập `address`, không có ô `Aliases` thứ hai ở Voice.**

```text
+------------------------------------------------------------------------------+
| A character is a name and a voice…                        [ + Add character ]|
+------------------------------------------------------------------------------+
| +--------------------------------------------------------------------------+ |
| | NAME                                                                     | |
| | {name: Caroline Miller}                                                  | |
| | Translated    {target: Caroline Miller}                                  | |
| | Aliases       {aliases: Carolyn Miller}                                  | |
| | Gender        {gender: f}                                                | |
| | Description   {description: Malcolm's Krelboyne teacher}                 | |
| |                                                                          | |
| | VOICE                                                                    | |
| | Speech        {speech: Warm, enthusiastic, slightly overwhelmed}         | |
| | Catchphrases  {catchphrases: "Class!"}                                   | |
| | Samples       {samples: 2 lines}                                         | |
| | Style         {style: Formality = Neutral}                               | |
| | [x] Always in the prompt ({always})                                      | |
| |                                                                          | |
| | FORMS OF ADDRESS (READ ONLY)                                             | |
| |   with Malcolm: cô–em / em–cô                                            | |
| |   [ Manage in Forms of address → ]        (trong sheet Add/Edit)         | |
| |                                                                          | |
| | [ Edit ]                                                    [ Delete ]   | |
| +--------------------------------------------------------------------------+ |
| (thẻ thứ hai xếp ngay dưới)                                                  |
+------------------------------------------------------------------------------+
```

- Trạng thái rỗng: `No character cards yet. Add characters to define their names and voices.   [ + Add character ]`
- **Add/Edit** (bottom sheet): hai phần **Name** (`Source name *`, `Translated name`, `Aliases` phân tách dấu phẩy, `Gender`, `Description`) và **Voice** (`Speech`, `Catchphrases`, `Samples`, `Style` có Inherit đầu tiên, `Always include in the prompt`), dòng impact trực tiếp, `[ Cancel ]` `[ Save ]` (lưu ghi `aliases` vào `glossary.characters` và đồng bộ sang `card.aliases` nếu có Voice; nhân vật chưa có Voice không tạo `CharacterCard` rỗng). Nhập danh xưng chung (`Mom`, `Dad`, `Sir`…) vào `Source name` hoặc `Aliases`, hoặc nhập alias trùng nhân vật khác, hiện lỗi đỏ ngay dưới ô (`"Mom" is a relative form of address, not a character's name.`) và tắt nút `[ Save ]`.
- **Delete**: bottom sheet `[ Cancel ]` `[ Confirm delete ]` kèm dòng impact.

### 2e) Context (`#/series/:id/context`)

```text
+------------------------------------------------------------------------------+
| STORY MEMORY                                                                 |
| {missing_facts [UI]: 2} episodes have a source subtitle but no facts.        |
|                                         [ Extract facts (2 episodes) ]       |
+------------------------------------------------------------------------------+
| 1 · S01E01 · {title}                       Source ✓ · Facts ✓     [ Show ]   |
| 2 · S01E02 · {title}                       Source ✓ · Facts ✓     [ Show ]   |
| 3 · S01E03 · {title}                       Source ✓ · Facts ✗                |
| 4 · S01E04 · {title}                       Source ✓ · Facts ✗                |
| 5 · S01E05 · {title}                       Source ✗ · Facts ✗                |
+------------------------------------------------------------------------------+
```

`{has_source}` và `{has_facts}` theo thứ tự câu chuyện (facts `x3` cũ vẫn là `Facts ✓`, không có nút extract lại hàng loạt). **Extract facts** mở bottom sheet Estimate riêng hiện rõ tên model (`extract_model`):

```text
+------------------------------------------------------------------------------+
| Extract facts                                               (Bottom Sheet)   |
+------------------------------------------------------------------------------+
| Episodes            {count [UI]: 2}                                          |
| Extract facts       {count [UI]: 2} calls · {extract_model [NEW]}            |
| One call per episode. Nothing is translated. The jobs wait in line with the  |
| translations and stop for the day's quota like them.                         |
| [ Cancel ]                                                      [ Confirm ]  |
+------------------------------------------------------------------------------+
```

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
| Arcane · vi                                                                  |
| manual · research · searching web (1/2)                          [ Stop ]    |
+------------------------------------------------------------------------------+
| Severance · vi                                                               |
| policy · research · Waiting · quota · retry at {retry_at: 00:00}             |
+------------------------------------------------------------------------------+
```

Job `research` thuộc series (không có tập); dùng đúng năm nhãn trạng thái chuẩn. `policy · research` chỉ có khi bật `research_enabled` (UX.md 6.7 v0.21, SPEC #28), không phụ thuộc `auto_request`.

- **Retry all failed** → Estimate: `Jobs {counts.failed: 3}`, `Model calls: unknown`.
- **Export job spec** → liên kết server + Copy (bản xuất dạng chữ, UX 6.6).
- Tên tab là Requested · Running · Waiting · Failed · Done (không dùng tên trạng thái nội bộ).

---

## 5b. Insights (`#/insights`)

Chỉ đọc, **không tốn lời gọi model** (không có Estimate/Confirm), không dùng CDN biểu đồ: chỉ thẻ số liệu và thanh ngang CSS/SVG thuần. Mọi nút cao ≥ 44 px; bảng thu về thẻ xếp dọc. Số liệu trong `{...}` là minh hoạ theo nhóm ở UX §4; bảng trường API của các khối số đo ở UX Phụ lục A.6b. Đã làm khác hình: dòng Research hiện số lời gọi và chi phí, chưa có `search queries` (API chưa ghi); thanh Daily đo số tập.

```text
+------------------------------------------------------------------------------+
| [ Library ]  [ Activity ]  [ Insights ]  [ Settings ]  [ Review (2) ]        |
| Running 1 · Waiting 2 · Synced 2m ago                                        |
+------------------------------------------------------------------------------+
| INSIGHTS                                           (read-only · no LLM calls)|
| Window  [ 7d ]  [ 30d ]  [ 90d ]                     [ Your corrections ↓ ]  |
+------------------------------------------------------------------------------+
| QUALITY                         (▼ better · ▲ worse · vs the previous window)|
| +--------------------------------------------------------------------------+ |
| | Cue edit rate          {edit_rate: 4.2%}  ▼ 0.6  (cues you had to edit)  | |
| | Repair rate            {repair_rate: 1.8%}  ▲ 0.2  (auto-repaired batches)| |
| | Retranslate rate       {retranslate_rate: 6.5%}  ▼ 1.1                   | |
| | Over max CPS           {over_cps: 3.1%}  ▼ 0.4                           | |
| | Over max line length   {over_line: 2.4%}  — (no previous data)           | |
| +--------------------------------------------------------------------------+ |
+------------------------------------------------------------------------------+
| BY MODEL AND PROMPT VERSION                                                  |
| +--------------------------------------------------------------------------+ |
| | {model: gemini-3.1-flash-lite} · prompt {prompt_v: v4}                   | |
| | Episodes {episodes: 38} · Edit {edit_rate: 4.8%} · Repair {repair: 2.1%} | |
| | Over CPS {over_cps: 3.4%} · Long lines {over_line: 2.6%}                 | |
| +--------------------------------------------------------------------------+ |
| | {model: gemini-3.5-flash} · prompt {prompt_v: v4}                        | |
| | Episodes {episodes: 12} · Edit {edit_rate: 2.1%} · Repair {repair: 0.4%} | |
| | Over CPS {over_cps: 1.9%} · Long lines {over_line: 1.5%}                 | |
| +--------------------------------------------------------------------------+ |
+------------------------------------------------------------------------------+
| STYLE A/B SCORES                                                             |
| +--------------------------------------------------------------------------+ |
| | {style_label: gritty (Casual · Keep · Adapt)}                            | |
| | Compared {comparisons: 6} · Chosen {wins: 5} ({win_rate: 83%})           | |
| | Too few comparisons to conclude (needs 10)                               | |
| +--------------------------------------------------------------------------+ |
| | {style_label: default (Neutral · Soften · Balanced)}                     | |
| | Compared {comparisons: 6} · Chosen {wins: 1} ({win_rate: 17%})           | |
| | Too few comparisons to conclude (needs 10)                               | |
| +--------------------------------------------------------------------------+ |
+------------------------------------------------------------------------------+
| QUEUE, QUOTA AND COST                                                        |
| +--------------------------------------------------------------------------+ |
| | Queue wait (median)    {queue_wait: 1m 20s}                              | |
| | Quota pauses           {quota_pauses: 2}                                 | |
| | Avg cost / episode     {cost_per_ep: $0.014}  (from Maintenance rates)   | |
| | Avg tokens / episode   {tokens_in: 42k} in · {tokens_out: 14k} out       | |
| | Calls by task          Translate 412                                     | |
| |                        Extract 38                                        | |
| |                        Judge 6                                           | |
| |                        Research 8 (search queries {queries: 19})         | |
| | Research cost          {research_cost: $0.04}                            | |
| +--------------------------------------------------------------------------+ |
+------------------------------------------------------------------------------+
| RELIABILITY                                                                  |
| +--------------------------------------------------------------------------+ |
| | Job success rate       {success_rate: 94.6%}                             | |
| | Retries / episode      {avg_retries: 0.18}                               | |
| | Breaker opens          {breaker_opens: 1}                                | |
| +--------------------------------------------------------------------------+ |
+------------------------------------------------------------------------------+
| DAILY            (bar = episodes that day; full bar = {max: 14}; no CDN)     |
| +--------------------------------------------------------------------------+ |
| | 2026-11-23   ████████████░░░░  12 eps · 0 failed · edit 3.8% · $0.16     | |
| | 2026-11-22   ████████░░░░░░░░   8 eps · 1 failed · edit 4.5% · $0.11     | |
| | 2026-11-21   ██████████████░░  14 eps · 0 failed · edit 4.1% · $0.19     | |
| +--------------------------------------------------------------------------+ |
+------------------------------------------------------------------------------+
| YOUR CORRECTIONS                                                             |
| Find repeated edit patterns to propose to Review, or download a golden set.  |
+------------------------------------------------------------------------------+
| +--------------------------------------------------------------------------+ |
| | [ Arcane → ]                              (link tới #/series/:id/glossary)| |
| |                                                                          | |
| | [ vi · 14 edits ]  [ Look at my edits ]  [ Golden set ]                  | |
| | [ en · 0 edits ]   [ Look at my edits ]  [ Golden set ]   (cả hai tắt)   | |
| |                                                                          | |
| | (role="status") vi: Found 3 repeated edit patterns and added to Review.  | |
| +--------------------------------------------------------------------------+ |
| +--------------------------------------------------------------------------+ |
| | [ Severance → ]                                                          | |
| |                                                                          | |
| | [ vi · 6 edits ]   [ Looking… ]                    [ Golden set ]        | |
| +--------------------------------------------------------------------------+ |
| +--------------------------------------------------------------------------+ |
| | Old Mini-Series (no longer in catalog)    (chữ thường, không gắn link)   | |
| |                                                                          | |
| | [ vi · 2 edits ]   [ Look at my edits ]  [ Golden set ]                  | |
| |                                                                          | |
| | [!] RED ALERT: Could not scan edits ({error: internal error})            | |
| +--------------------------------------------------------------------------+ |
+------------------------------------------------------------------------------+
```

**Trạng thái tải và rỗng**

```text
+------------------------------------------------------------------------------+
| Đang tải:   (o) Loading the series                                           |
+------------------------------------------------------------------------------+
| Rỗng:       The worker keeps no series yet.                                  |
+------------------------------------------------------------------------------+
```

**Golden set** (bottom sheet, giống Export YAML; mở từ nút Golden set của một dòng ngôn ngữ)

```text
+------------------------------------------------------------------------------+
| Golden set vi · Arcane                                      (Bottom Sheet)   |
| [ Download arcane.vi.golden.json ]     (liên kết server, không blob:)        |
| [ Copy ]                                                                     |
|   (GET .../golden?lang=vi rồi ghi clipboard)                                 |
| Đang lấy:   Copying…                          (role="status")                |
| Xong:       Copied {cues: 342} cues.          (role="status")                |
| Nếu clipboard bị chặn: ô chữ chọn-tất-cả, chỉ xem trước vài cue đầu:         |
| +--------------------------------------------------------------------------+ |
| | { "golden_version": 1, "series": "arcane", "lang": "vi", "cues": [ ...  | |
| +--------------------------------------------------------------------------+ |
|   (chọn-tất-cả vẫn chép đủ {cues: 342} cues; chỉ phần hiện ra bị cắt)       |
| Lỗi lấy:    [!] Could not copy: {error}   (Alert đỏ)                         |
|                                                                 [ Close ]    |
+------------------------------------------------------------------------------+
```

Quy tắc của trang:

- **Một cửa sổ thời gian** (7d · 30d · 90d, mặc định 30d) cho mọi khối; không khối nào tự chọn cửa sổ riêng. Mũi tên so với cửa sổ liền trước cùng độ dài; không có dữ liệu trước thì hiện "—". Máy mới chưa có số liệu: mọi số hiện "—", không hiện 0%.
- **Style A/B**: dưới 10 lần so sánh thì thêm dòng *Too few comparisons to conclude*, không tô nổi tỉ lệ thắng.
- **Your corrections**: sắp theo số cue đã sửa giảm dần; `{n} edits` của mỗi ngôn ngữ là số cue đã sửa (`corrections` của `GET /api/metrics`, tính cả thời gian). Bằng 0 thì cả hai nút tắt. Liên kết *Your corrections ↓* ở đầu trang cuộn tới khối này.
- Sheet Golden set: nút **Copy** không vẽ thêm khi ngôn ngữ chưa có cue sửa (nút Golden set đã tắt).
- Calls by task và Research cost: tách tác vụ nhờ `llm_calls` ghi tác vụ `research` (ARCHITECTURE §3.5); chưa có research thì dòng Research hiện "—".

**Khổ ngang (≥ 1024 px)**: cùng nguyên tắc mục 8.

| Khu vực | Khổ dọc | Khổ ngang |
| --- | --- | --- |
| Quality / Queue & Quota / Reliability | thẻ xếp dọc 1 cột | lưới 2–3 thẻ cạnh nhau |
| By model and prompt version | thẻ xếp dọc | bảng: Model · Prompt · Episodes · Edit % · Repair % · Over CPS · Long lines |
| Style A/B scores | thẻ xếp dọc | bảng: Style · Compared · Chosen · Win rate |
| Daily | danh sách dòng xếp dọc | bảng kèm thanh ngang: Date · Bar · Episodes · Failed · Edit % · Cost |
| Your corrections | thẻ xếp dọc 1 cột | lưới 2 cột thẻ (hoặc 1 cột rộng, hai nút nằm trên cùng một hàng) |

---

## 5c. Review (`#/review`)

Hộp thư đề xuất (P13) cho glossary, thẻ nhân vật và quy tắc xưng hô. Nguồn của thẻ: **Facts** (từ facts của tập), **Your edits** (từ cue bạn sửa), **Research** (P16, từ tìm kiếm có grounding). Mục nav *Review (N)* chỉ hiện khi có đề xuất. Accept · Edit · Reject và Accept all giữ như UX Phụ lục A.6; hình dưới chỉ vẽ phần khác khi có Research.

```text
+------------------------------------------------------------------------------+
| [ Pending (4) ]   [ History (12) ]                                           |
+------------------------------------------------------------------------------+
| ARCANE (4 pending)                                [ Accept all in Arcane ]   |
+------------------------------------------------------------------------------+
| +--------------------------------------------------------------------------+ |
| | [Term]  [Research]                                  1 of 2 sources       | |
| | Source  Hextech                                                          | |
| | Target  Công nghệ Hextech                                                | |
| |                                                                          | |
| | Sources                                                                  | |
| |   ↗ leagueoflegends.fandom.com                                           | |
| | Pending because only 1 source was found (2 are needed to auto-accept).   | |
| |                                                                          | |
| | Accepting flags 3 episodes stale.                                        | |
| | [ Accept ]                  [ Edit ]                        [ Reject ]   | |
| +--------------------------------------------------------------------------+ |
+------------------------------------------------------------------------------+
```

- Thẻ Research **không có `confidence`**: số nguồn thay vai trò đó (thẻ Facts giữ `confidence` nếu API có). Mỗi nguồn là một liên kết mở tab mới, vùng chạm ≥ 44 px, hiện tên miền (và tiêu đề nếu grounding trả); URL lấy từ grounding metadata, không bao giờ từ chữ model viết. Link grounding có thể là link chuyển hướng: hiện tên miền, không hiện URL thô.
- Số nguồn độc lập là điều admin tự đánh giá được qua tên miền; không thêm điểm tin cậy.

**History**: term tự chấp nhận (`auto_accept`, ≥ 2 nguồn) có huy hiệu **Auto**, nguồn, và **Undo**. Undo gỡ term khỏi glossary và đánh stale lại, nên nút nói rõ số tập.

```text
+------------------------------------------------------------------------------+
| [Term]  [Research]  [Auto · 2 sources]   "Piltover" → "Piltover"             |
|   ↗ vi.wikipedia.org  ·  ↗ leagueoflegends.fandom.com                        |
|   Auto-accepted 10m ago · flagged 3 episodes stale                           |
|                                                  [ Undo (flags 3 stale) ]    |
+------------------------------------------------------------------------------+
```

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
| Default model ({gst_model})          [ gemini-3.8-flash        v ]           |
| Strong model ({gst_fallback_model})  [ (none)              v ]  [ Clear ]    |
| Memory model ({memory_model})        [ Same as Default (gemini-3.8-flash) v ]|
|   Runs Extract facts, Suggest from facts, and step 2 of Web research.        |
| Summarize model ({summarize_model})  [ Same as Default (gemini-3.8-flash) v ]|
|   Condenses old story summaries (falls back to Memory, then Default).        |
| Research model ({research_model})    [ Same as Default (gemini-3.8-flash) v ]|
|   Uses Google Search grounding (Gemini only; skipped on OpenAI-compatible).  |
| Judge model ({judge_model})          [ Same as Default (gemini-3.8-flash) v ]|
|   A stronger model than the one that translated judges better.               |
|                                                                              |
| [!] YELLOW ALERT (khi đổi gst_model mà còn ô phụ trống, hoặc trùng Strong):  |
| Extract facts, Condense story, Suggest from facts, and Web research have no  |
| model of their own and will also run on gemini-3.8-flash.                    |
|                                                                              |
| ACTIVE MODELS BY TASK ({task_models [NEW]}, cập nhật trực tiếp theo draft)   |
| +--------------------------------------------------------------------------+ |
| | Translate            gemini-3.8-flash                                    | |
| | Strong fallback      Off (single model)                                  | |
| | Extract facts        gemini-3.8-flash  (uses Default model)              | |
| | Condense story       gemini-3.8-flash  (uses Default model)              | |
| | Suggest from facts   gemini-3.8-flash  (uses Extract facts model)        | |
| | Web research         gemini-3.8-flash  (uses Default model)              | |
| | LLM judge            Off  (gemini-3.8-flash when enabled)                | |
| +--------------------------------------------------------------------------+ |
|                                                                              |
| [ ] Research terms by itself ({research_enabled})                            |
| Research budget, calls per series ({research_max_calls_per_series})  [ 10 ]  |
|   (0 = no cap)                                                               |
| [ ] LLM judge ({judge_enabled})                                              |
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
