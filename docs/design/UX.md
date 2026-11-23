# UX — luồng dùng và nguyên tắc giao diện

> Trạng thái: **Draft v0.20** · 2026-11-23 · Nền cho dựng giao diện. Nguồn: lời mô tả của admin ngày 2026-11-16 (mục 1–2), các bất biến trong `AGENTS.md`, và các quyết định ở SPEC #26. Quyết định: SPEC #25, #26, FR-10.4, FR-10.5; ADR-17. Wireframe: [wireframe-v2.md](wireframe-v2.md) (v2.1); prompt vẽ: [wireframe-prompt.md](wireframe-prompt.md). **Khi wireframe và tài liệu này khác nhau, tài liệu này thắng.**
> v0.14: sau review wireframe v1: giao diện React, bỏ Stale khỏi Home, bỏ nút Check, Estimate/Confirm, banner toàn cục, Inherit và Style cấp tập, Settings 7 nhóm. v0.15: các mặc định đã vẽ thành quy tắc (SPEC #26 điểm 12). v0.16: iPad cầm dọc nên khổ chính là dọc (SPEC #26 điểm 13); Import YAML chốt; wireframe v2.1 đã sửa các sai lệch của v2 nên bỏ Phụ lục C. v0.17: đã kiểm code cho sáu điểm còn mở (kết quả ở PLAN P12b); mục 8 chỉ còn ý tưởng chưa quyết; Memory và Specials after không có dòng impact ở v1 (SPEC #26 điểm 14). v0.18: ghi lại nút **Download golden set** của trang Insights (đã có trong code): mục 6.6 và Phụ lục A.6b; không đổi hành vi. v0.19: nút **Golden set** trên mỗi dòng ngôn ngữ mở một bottom sheet có **Download** và **Copy** (giống Export YAML), để dòng ngôn ngữ không bị chật; thêm wireframe Insights (mục 5b của wireframe-v2.md). v0.20: Insights có một cửa sổ thời gian, mũi tên so kỳ trước và số cue đã sửa theo ngôn ngữ; mục 6.7 Research terms (P16): nút trên Glossary, hộp Estimate riêng, thẻ Review có nguồn, Auto kèm Undo, ngân sách theo series; thêm wireframe Review (mục 5c).
> Giao diện viết bằng **tiếng Anh** (khớp arr stack); tài liệu này bằng tiếng Việt, các nhãn UI giữ nguyên tiếng Anh.

## 1. Người dùng và bối cảnh

Một admin duy nhất (chủ homelab). Mở console **trong Organizr, trong một iframe, trên iPad** (cảm ứng, không hover, không dùng thanh địa chỉ), cùng các app arr khác. Không muốn một trang riêng cho nó. Bazarr chỉ cho biết một mục đang *missing*, không cho biết vì sao (không tìm thấy, đang tải, hết hạn mức), nên admin chuyển việc kiểm tra sang console này.

## 2. Việc cần làm, theo tần suất

| # | Việc | Tần suất | Ghi chú |
|---|---|---|---|
| 1 | "Series hay phim này đã có phụ đề tiếng Việt chưa?" | Thường nhất, trước mỗi lần xem | Trả lời bằng **một màn hình**, theo tên. Hiện chỉ đến được bằng Library → series → tập. |
| 2 | Chưa có thì bấm **Translate** (cả tập, mùa hay series) | Khi cần | Không có hàng đợi request cần duyệt. Việc bấm *là* sự chấp thuận. |
| 3 | Xem phim, thấy phụ đề dở, quay lại **cải thiện** | Sau khi xem | Lỗi thường gặp: dịch sai, dịch dở, sai idiom, máy móc. Đây là lỗi **cấp style**, không phải vài câu lẻ. |
| 4 | Thiết lập series mới: glossary, style, thẻ nhân vật | Khi có series mới | Làm một lần cho mỗi series. |
| 5 | Theo dõi việc đang chạy, xử lý job lỗi hoặc chờ quota | Thỉnh thoảng | Là việc theo dõi, không phải điểm bắt đầu. |
| 6 | Duyệt đề xuất glossary và thẻ nhân vật (P13) | Khi có đề xuất | Chỉ duyệt đề xuất, không duyệt job. |

## 3. Nguyên tắc

1. **Hỏi theo tên, không theo hàng đợi.** Trang vào là danh mục series và phim lấy từ Bazarr, có ô tìm. Dashboard và Queue lùi xuống vai trò theo dõi.
2. **Mỗi trạng thái đi kèm đúng một việc kế tiếp.** Một dòng không bao giờ chỉ là nhãn: nó có nút (Translate, Retry, Improve…) hoặc nói rõ phải chờ gì.
3. **Không có gì tự chạy khi admin chưa bấm.** Không tự ghi đè, không tự dịch lại. Stale chỉ là cờ. `auto_request` là tính năng nâng cao, tắt mặc định, và không nằm trên đường đi chính (không hiện trên Home).
4. **Xem giá trước khi trả.** Chi phí **đã biết thì hiện luôn**, không bắt bấm nút kiểm: số bản dịch sẽ stale hiện thành một dòng cập nhật khi chỉnh form (mục 6). Chi phí **chỉ biết khi hỏi** (số lời gọi) hiện trong hộp Estimate (mục 6.4).
5. **Sửa từ rộng đến hẹp.** Lỗi chất lượng đi từ style của series → thử → so sánh cả tập → tên và xưng hô → từng câu. Công cụ rộng nằm trước, công cụ hẹp là phụ (và vẫn nuôi few-shot).
6. **Cảm ứng là chính.** Không chức năng nào chỉ hiện khi rê chuột; vùng bấm ≥ 44 px; **khổ chính là DỌC** (~820 px, iPad cầm dọc), khổ ngang ≥ 1024 px là khổ phụ cùng bố cục. Dưới 1024 px mọi bảng rộng (cue, address rules, Try 20 lines, Compare, providers) thu về **thẻ xếp dọc**, thân trang không cuộn ngang (thanh tab được cuộn trong chính nó); xác nhận dùng dialog hoặc bottom sheet (không popover, vì chạm ra ngoài là đóng); điều hướng bằng `#hash` chạy trong iframe.
7. **Ít điều khiển, mở dần.** Style hiện bốn lựa chọn chính (Formality, Tone, Swearing, Idioms and wordplay), phần còn lại (giới hạn phụ đề, honorifics, ghi chú) nằm sau "More". Settings là các nhóm gập, hai nhóm đầu mở sẵn.
8. **Từ vựng arr stack, tiếng Anh, ngắn.** Một trạng thái một nhãn trong toàn bộ giao diện (mục 5). Tên trạng thái nội bộ (`pending`, `deferred`, `processing`) không lên giao diện.
9. **Mọi nút tốn quota đi qua Estimate/Confirm** (mục 6.4). Ngoại lệ duy nhất: **Try 20 lines** (≈ 1 lời gọi, ghi ngay trên nút, chạy luôn) vì nó là bước thử rẻ đứng trước mọi thứ đắt.
10. **Điều gì làm mọi job dừng thì phải thấy ngay**, không nằm sâu trong Settings hay Activity: banner toàn cục (mục 4.2).

## 4. Cấu trúc trang (đích)

```
Home (search + list of series and movies)          nav: Library · Activity · Insights · Settings   (+ Review khi P13)
 ├─ filter chips: All · Missing Vietnamese · Running · Failed · Ignored
 ├─ [TV] row → Series page
 │    ├─ Episodes     (per-episode status + next action; Translate season / series)
 │    ├─ Style        (axes · Try 20 lines · Compare a whole episode)      ← nút "Improve" ở dòng tập dẫn vào đây
 │    ├─ Glossary     (Terms · Forms of address)
 │    ├─ Characters   (Name · Voice)
 │    ├─ Context      (story memory: which episodes have facts)
 │    └─ Episode page (Subtitles · Corrections · Retranslate · History · Context · Style override)
 └─ [MV] row → Unit page (cùng layout Episode page; không có bộ chọn tập, không có tab Context)
Activity  = Queue + provider health + quota   (dải trạng thái chạy cũng hiện trên Home)
Review    = suggestions inbox (P13, xong 2026-11-17), badge on nav; mục nav ẩn cho tới khi máy đã đề xuất gì đó
Insights  = số đo của worker (P15): chất lượng (tỉ lệ cue bạn phải sửa, repair, dịch lại, CPS, dòng dài), theo model và prompt version,
            điểm A/B theo style, hàng đợi, quota, chi phí mỗi tập, độ tin cậy, từng ngày; cuối trang là "Your corrections":
            Look at my edits và tải bộ golden của từng series. Chỉ đọc, không tốn lời gọi model; khổ dọc, thẻ xếp chồng, không CDN
Settings  = 7 nhóm gập (mục 4.1); Backups và Logs là hai trang riêng, mở từ nhóm Maintenance
Login     = trang tĩnh riêng, ngoài giao diện chính (mục 7)
```

- **Gỡ:** Bazarr wanted và Media scan (đã định gỡ khi Library đủ dùng, PLAN P10 việc treo 2).
- **Hạ cấp:** Dashboard thành dải trạng thái trên Home và trang Activity; `auto_request` vào header của trang series và nhóm Library của Settings.
- **Gộp:** Glossary, Style, Characters và Memory thành các tab của trang series thay vì trang rời; ngôn ngữ đích là một bộ chọn trên trang series, ẩn khi chỉ có một.
- **Phim:** bấm [MV] vào thẳng trang Unit (cùng layout trang tập: Subtitles · Corrections · Retranslate · History · Style override). Glossary và Style cấp phim mở từ trang Unit. Tab Context không hiện vì memory chỉ áp dụng cho tập của series.
- **Tên tab Style / nút Improve:** tab là danh từ (**Style**, giống Glossary, Characters); **Improve** là nút hành động ở dòng tập và trên Home, bấm vào mở tab Style.

### 4.1 Settings: 7 nhóm, Save theo nhóm

Mỗi nhóm là một thẻ gập có nút **Save** riêng, hiện trạng thái *unsaved changes* và chặn rời trang khi còn thay đổi chưa lưu. Cài đặt có hiệu lực ngay khi lưu (không cần khởi động lại). **Connections** và **Translation** mở sẵn; năm nhóm còn lại gập. Kiểu, mặc định và giới hạn từng trường ở Phụ lục B.

Từng trường của mỗi nhóm (nhãn, kiểu, mặc định) ở Phụ lục B. Ở đây chỉ ghi hành vi của nhóm:

| # | Nhóm | Hành vi |
|---|---|---|
| 1 | Connections | `llm_base_url` chỉ hiện khi provider là OpenAI-compatible. Mỗi khoá hiện 4 ký tự cuối và trạng thái `ok`/`invalid`/`exhausted`, có nút **Test**; Test thành công đưa khoá về `ok` (không cần nút "reset" riêng) |
| 2 | Translation | Model chọn từ danh sách `GET /api/gemini-models` |
| 3 | Library | `auto_request` là mục nâng cao, nhãn nói rõ "off by default; translates missing items of chosen series without asking". `media_roots` ở lại (dọn file bỏ qua mọi thư mục media) |
| 4 | Queue and quota | `quota_reset_tz` là múi giờ **nửa đêm của provider** (Gemini: `America/Los_Angeles`), không phải múi giờ của admin; có chú thích |
| 5 | Story and style | |
| 6 | Maintenance | Nút **Clean up now** (xem trước rồi xác nhận); liên kết tới **Backups** và **Logs**, hai trang riêng luôn mở được, không giấu |
| 7 | Security | Là form riêng, không phải cài đặt |

`scan_limit` được gỡ cùng hai trang Media scan và Bazarr wanted (cả hai route `/api/scan` và `/api/wanted` đều đọc nó): PLAN P12b.

### 4.2 Banner toàn cục và dải trạng thái

Dưới thanh nav, trên mọi trang:

| Điều kiện | Banner | Nút |
|---|---|---|
| Một khoá bị từ chối (`invalid`) hoặc model lạ (`ModelNotFound`); job `waiting · provider` | *"Gemini rejected the API key. Jobs are waiting."* (đỏ) | **Open Connections** |
| Danh mục ở trạng thái `kept` (Bazarr không đọc được, trả rỗng hoặc ít hơn bất thường) | *"Bazarr could not be read. Showing the library as of {synced_at}. {warning}"* (vàng) | **Sync now** |
| Phiên hết hạn (một lời gọi `/api/*` trả `401`) | Lớp phủ chặn, không phải banner: *"Your session expired."* Giữ nguyên bản nháp đang sửa | **Sign in** (mục 7) |

Dải trạng thái (thay Dashboard, nhỏ, không phải cảnh báo): số job Running/Requested/Waiting (cùng tên và cùng cách đếm với các tab của Activity: Requested là job đang xếp hàng, Waiting là job chờ một điều kiện), tuổi catalog (`Synced 2m ago`), và provider đang `exhausted` hoặc breaker mở (kèm `until`). Hết quota ngày là trạng thái bình thường (job `Waiting · quota`), không lên banner.

## 5. Trạng thái và việc kế tiếp (một nhãn một nghĩa)

Nhãn lấy **nguyên văn từ SPEC FR-14.5** và ghép từ ba trục độc lập, như "Missing · Requested" hay "Translated · Waiting". UX chỉ thêm cột "việc kế tiếp". Khi FR-14.5 đổi thì bảng này đổi theo.

**Trục nội dung (phiên bản)**

| Nhãn | Nghĩa | Việc kế tiếp |
|---|---|---|
| Missing | Bazarr liệt kê thiếu và không có phiên bản `current` | **Translate** nếu có phụ đề nguồn; nếu không, ghi "no source subtitle" và chờ Bazarr |
| Translated | `current` do máy dịch | **Improve** |
| Edited | `current` có chỉnh tay | History |
| External | File đích không do hệ thống tạo (Bazarr tải, admin bỏ vào) | **View**; dịch lại sẽ giữ file đó làm một phiên bản |
| Removed | Admin đã xoá phụ đề đích; không tự yêu cầu lại | Hai việc khác nhau, hai nút: **Restore** (revert về phiên bản đã xoá, tức thì, không tốn lời gọi) và **Translate again** (job mới, tốn lời gọi, qua Estimate) |
| Stale | Luật, glossary, style hoặc facts đổi sau lúc dịch | **Retranslate** (qua Estimate). Chỉ hiện ở trang Series và trang tập, **không** hiện số hay chip ở Home |
| Outside profile | Ngôn ngữ đích ngoài profile của Bazarr (FR-14.6, SPEC viết "Ngoài profile") | **Translate** |

**Trục công việc (job)**

| Nhãn | Nghĩa | Việc kế tiếp |
|---|---|---|
| Requested | Có job `queued`, chưa chạy (không có thực thể "request" riêng: Requested chỉ là nhãn của job `queued`, SPEC #13) | Cancel |
| Running | Đang dịch, có tiến độ | Stop |
| Waiting · `quota` | Hết quota ngày; chờ đến mốc reset | Không cần làm gì; hiện giờ chạy lại |
| Waiting · `provider` | Khoá bị từ chối, model lạ hoặc breaker đang mở | Khoá hoặc model bị từ chối: sửa trong Settings, job tự chạy lại. Breaker đang mở: chỉ cần chờ |
| Waiting · `context` | Chờ facts của tập trước | Nút "Request that episode" (qua Estimate nếu có thể tốn lời gọi) |
| Waiting · `settle` | Phụ đề nguồn vừa đổi, chờ ổn định | Không cần làm gì |
| Waiting · `rate` | Provider bận, thử lại sau ít giây | Không cần làm gì |
| Failed | Lần thử gần nhất lỗi do chính job, có lý do | **Retry** (qua Estimate) |

**Trục quyết định**

| Nhãn | Nghĩa | Việc kế tiếp |
|---|---|---|
| Ignored | Admin bảo đừng dịch | Stop ignoring |

**Huy hiệu của phiên bản (không phải nhãn của một mục):** *Candidate* là bản chưa ra đĩa (variant, hoặc kết quả bị chặn ghi đè). Nó hiện trong History và trên trang Compare, với nút **Compare** và **Use this version**. FR-14.5 chưa liệt kê nó vì nó là trạng thái của một phiên bản, không phải của (unit, ngôn ngữ).

**Tên trạng thái của hàng đợi trên Activity** dùng cùng nhãn: *Requested · Running · Waiting · Failed · Done · Cancelled* (không dùng `pending`, `deferred`, `processing`). *Cancelled* gom job admin hủy khi chưa chạy và job Stop khi đang chạy: worker giữ một trạng thái `cancelled` cho cả hai.

**Mục Bazarr không còn liệt kê:** series hoặc phim mà Bazarr ngừng liệt kê vẫn hiện trong Library khi worker còn giữ unit của nó (bản dịch, lịch sử), với nhãn *No longer in Bazarr*. Nó không bao giờ được tính là Missing và không có nút Translate. Mục mà worker chưa giữ gì thì không hiện. Trên trang series, một tập Bazarr không còn liệt kê mà chưa có file ghi *Not translated* (không bao giờ *Missing*) và chú thích *no longer in Bazarr*; trang tập không nhắc điều này (nó chỉ nằm ở danh sách).

Chưa nói được "Missing vì sao" ở phía Bazarr (không tìm thấy, đang tải, vượt hạn mức): worker chưa đọc lịch sử hay trạng thái provider của Bazarr (SPEC #25.6).

**Một dòng Home** gồm: huy hiệu loại ([TV] hoặc [MV]), tên, số tập (series) hoặc nhãn trạng thái (phim), số **Missing** và số **Running/Waiting** nếu có (số **Failed** chỉ khi đếm được rẻ, mục 8), và **một** nút việc kế tiếp. Chọn nút theo thứ tự: có Failed → **View failed**; còn mục Missing **chưa có job mở** → **Translate missing** (qua Estimate); còn lại → **Improve**. Mục đã có job Requested, Running hoặc Waiting thì không có nút Translate (nút đó sẽ tạo job trùng). Không có số Stale, không có `auto_request`.

## 6. Các luồng chính

### 6.1 Luồng "cải thiện" (việc 3)

Tên trục trên giao diện là tên đang dùng trong form Style (`static/js/views/style-form.js` hiện tại); trường API và tài liệu dùng tên ở cột giữa. Không dùng tên thứ ba nào trong UX.

| Nhãn trên giao diện | Trường (API, SPEC FR-18.2) | Giá trị |
|---|---|---|
| Formality | `formality` | casual · neutral · formal |
| Tone | `tone` | chữ tự do |
| Swearing | `profanity` | keep · soften · remove |
| Idioms and wordplay | `localization` | literal · balanced · adapt |
| Dialect | `dialect` | north · south · neutral |
| Honorifics (anime) | `honorifics` | keep · translate |
| Characters per second · Longest line · Lines per cue · Fast dialogue | `max_cps` · `max_line_length` · `max_lines` · `allow_condense` | số · số · số · có/không |
| Notes | `notes` | chữ tự do |

Bốn lựa chọn chính là **Formality, Tone, Swearing, Idioms and wordplay**; phần còn lại nằm sau "More".

**Inherit.** Mọi trường của style đều tuỳ chọn và xếp tầng (language < preset < series < episode < character card). Mỗi Segmented Control có ô **Inherit** ở đầu và là mặc định khi trường chưa đặt; một trục đã đặt thì lưu ở đúng tầng đang sửa. Khi một trục đang Inherit, ô hiện giá trị đang hiệu lực ("Inherit · now Neutral"), lấy từ `effective`. Giao diện **không** biết một giá trị đã đặt đến từ tầng nào (API chỉ trả giá trị gộp), nên không có nhãn "from: Series".

1. Mở series → tab **Style** (hoặc bấm **Improve** ở dòng tập). Thấy preset, các lựa chọn chính, và **dòng impact** (mục 6.2).
2. **Try 20 lines** (nút ghi "≈ 1 call") với lựa chọn đang chỉnh: cạnh nhau **Now** và **With this style** (thẻ xếp dọc ở khổ dọc, bảng ở khổ ngang). Chưa tốn gì ngoài một lời gọi và không ghi gì. Tập mẫu là tập đầu tiên có bản dịch; admin đổi được tập và các cue mẫu.
3. **Series chưa có tập nào được dịch** (series mới, việc 4): Try 20 lines lấy 20 cue đầu của tập đầu tiên có phụ đề nguồn; bảng chỉ có hai cột **Source** và **Preview**, ẩn cột Now.
4. Ưng ý thì **Save** (xác nhận theo mục 6.2).
5. Cần so với cả tập thì **Compare on a whole episode** (qua Estimate): chọn tập, đặt nhãn; job chạy nền và kết quả là bản **Candidate** xuất hiện trong History của tập, mở được trên trang Compare.
6. Trên trang Compare, **Use this version** (cả bản variant): hộp xác nhận có checkbox mặc định bật **[x] Also apply this variant's style to the series** (lấy preset và style từ `params.variant`), kèm dòng impact của chính style đó. Tắt checkbox thì chỉ đổi bản dịch của tập. Chọn từng cue (**Apply picked cues**) không có checkbox này vì bản ghép không thuộc một style nào.
7. Retranslate các tập stale, từng tập một, khi admin muốn (qua Estimate).
8. Còn vài câu sai: vào trang tập, sửa câu (khoá và làm ví dụ cho các tập sau).

**Style cấp tập.** Trang tập có tab **Style** (ghi đè style của series cho riêng tập đó, `GET/PUT /api/units/{id}/style`), cùng form, mọi trục mặc định Inherit, ghi rõ "Overrides the series style for this episode only". Style rỗng thì bỏ ghi đè.

### 6.2 Tác động stale hiện trực tiếp (thay nút Check)

Khi admin chỉnh form Style hoặc Glossary (và khi đổi thẻ nhân vật), giao diện gọi `dry_run` (chỉ đếm, **không tốn lời gọi model**, không ghi) và hiện một dòng cập nhật: *"Saving flags 12 episodes stale."* hoặc *"No translation is affected."* Có debounce; trong lúc tính hiện *"Checking…"*. **Save** chỉ hiện hộp xác nhận (bottom sheet) khi N > 0: *"Saving flags N episodes stale. Nothing is retranslated."*; N = 0 thì lưu luôn. Không còn nút Check riêng.

**Bản nháp chưa lưu** của mọi form (Style, Glossary, Characters, và từng thẻ của Settings) hiện chỉ báo *Unsaved changes* và chặn rời trang bằng bottom sheet *Stay on page / Discard & leave*.

**Chính sách của series (header trang series):** `auto_request` lưu ngay khi chạm (không làm bản dịch nào stale). **Memory** và **Specials after** lưu bằng nút **Apply**, vì đổi chúng có thể làm bản dịch stale (API nói rõ); hai trường này **không có dòng impact ở v1** (API không có `dry_run` và chưa có hàm tính stale theo chính sách ứng viên; để v1.1, SPEC #26 điểm 14). Cạnh nút Apply chỉ ghi "Existing translations are flagged Stale, never redone".

### 6.3 Glossary và Characters: phân biệt khái niệm

Dữ liệu có hai nơi riêng (bảng glossary và bảng thẻ nhân vật); giao diện tách theo **việc admin muốn làm**, không theo bảng:

- **Glossary** = quy tắc cứng, luôn thắng mọi style: **Terms** (source → target, `locked`, ghi chú, khoảng hiệu lực từ–đến theo tập) và **Forms of address** (`address_rules`: ai gọi ai thế nào, từ tập hoặc timecode nào).
- **Characters** = mỗi nhân vật một thẻ gồm hai phần: **Name** (tên dịch, giới tính, mô tả ngắn; đây là `characters` của glossary) và **Voice** (cách nói, bí danh, câu cửa miệng, câu mẫu, ghi đè style, "luôn đưa vào prompt"; đây là thẻ nhân vật).

Tab Glossary **không** có sub-tab Characters. **Import YAML** là bottom sheet: dán văn bản hoặc chọn tệp (đổ vào ô chữ), dòng impact trực tiếp, lỗi nêu đường dẫn sai (`terms[0]: missing required field 'target'`); nội dung cũ vào History. Lưu một thẻ ghi vào cả hai nơi (hai yêu cầu) và dòng impact cộng cả hai. Cách xưng hô chỉ sửa ở **Forms of address** (glossary thắng mọi tầng): thẻ nhân vật **không có ô nhập `address`**, chỉ hiện bản tóm tắt chỉ đọc có liên kết sang đó. Thẻ đã lưu sẵn `address` thì hiện chỉ đọc dưới phần Voice.

### 6.4 Estimate/Confirm: những nút phải đi qua

Hộp Estimate là dialog hoặc bottom sheet có **Cancel** và **Confirm** (không popover). Nó hiện: số job, số cue, số lời gọi ước tính, `calls_left_today` (nếu có trần), `paused_until` (nếu đang hết quota), thời gian và giờ xong dự kiến.

| Nút | Ước lượng lấy từ | Có sẵn? |
|---|---|---|
| Translate (tập, mùa, series, tất cả), Translate again | `POST /api/jobs` với `estimate_only` | Có |
| Retranslate cả tập | `POST /api/jobs` với `mode: replace`, `estimate_only` | Có |
| Retranslate theo dải cue hoặc thời gian | `POST /api/units/{id}/retranslate` với `estimate_only` (số cue của dải và số lời gọi); số cue khoá giữ lại vẫn tính ở giao diện | Có |
| Retry một job, **Retry All Failed** | `POST /api/queue/retry` với `estimate_only` và `job_id`, `job_ids` hoặc `all` | Có |
| **Extract facts** (tab Context) | số tập có phụ đề nguồn mà chưa có facts, mỗi tập một lời gọi (`memory_model`); từ `GET /api/series/{id}/memory` | Tính được ở giao diện |
| **Compare on a whole episode** (variant) | `POST /api/units/{id}/variant` với `estimate_only` (cue và lời gọi của một tập) | Có |
| Request that episode (Waiting · context) | như Translate | Có |
| Try 20 lines | ≈ 1 lời gọi, ghi trên nút | Ngoại lệ, không có hộp |

### 6.5 Trang tập (Episode / Unit page)

- **Header của bản Current:** trạng thái, version, origin, model, thời gian; nút **Remove output** (xoá file phụ đề đích, có xác nhận, nói rõ "the version stays in History and can be restored") nằm ở đây, **không** nằm trên dòng của một phiên bản trong History.
- **Subtitles:** bảng gốc–dịch (`#`, timecode, Source, Target) và khoá cue; ở khổ dọc là thẻ (tiêu đề `#` và timecode, rồi Source, rồi Target). Tìm theo text và nhảy theo timecode. **Sửa một cue** mở bottom sheet hoặc modal có Source phía trên, ô Target, **Prev/Next**, Save; không sửa inline trong bảng (bàn phím iOS che nửa màn hình). Cue đã sửa bị khoá; bỏ khoá = gỡ correction đó. Không hiện chi tiết gộp lần sửa.
- **Corrections:** danh sách (term, address, note, cue_edit), thêm mới, phạm vi episode/forward/series.
- **Retranslate:** phạm vi (cả tập, dải cue, dải thời gian), chọn correction đính kèm, thêm correction mới; qua Estimate, nói rõ kết quả **ghi đè ngay** file đích và bản cũ ở lại trong History. Dải cần bản dịch hiện có cùng số cue (nếu không: báo lỗi rõ).
- **History:** mỗi phiên bản có version, trigger, origin, status, model, tokens, thời gian, `params`. Hành động theo dòng: **Restore**, **Compare** và **Use this version** (cho Candidate), diff. Không có "Remove output" ở đây.
- **Context:** hai cột `used` (bối cảnh đã dùng để dịch) và `now` (bối cảnh nếu dịch lúc này), gồm **các section chữ của story memory** (mỗi section có tiêu đề và nội dung), cờ `changed` và các tập thiếu. Không có version glossary hay style ở đây. Không có lý do cụ thể "stale vì glossary hay style" (chưa có dữ liệu).
- **Style:** ghi đè cấp tập (mục 6.1).

### 6.6 Export và tải file trong iframe

Tải bằng `blob:` có thể bị iOS chặn trong iframe (mục 7). Mọi thao tác xuất (**Export YAML** của glossary, **Export job spec**, tải backup) dùng **liên kết do server phục vụ** (có `Content-Disposition`), không tạo `blob:`. Bản xuất dạng chữ (YAML, job spec) có thêm nút **Copy** (nếu iframe không cho ghi clipboard thì hiện ô văn bản chọn-tất-cả để chép tay); backup `.zip` chỉ có liên kết tải, không có Copy.

**Bộ golden** của một series (trong "Your corrections" ở cuối trang Insights, mỗi ngôn ngữ của series một nút **Golden set**) mở một **bottom sheet** giống Export YAML, để dòng ngôn ngữ không chật:

- **Download** là một liên kết `<a>` tới `GET /api/series/{id}/golden?lang=<lang>&download=1`, server trả file đính kèm `<series-key>.<lang>.golden.json`; không tạo `blob:`.
- **Copy** lấy văn bản JSON của bộ golden bằng `GET /api/series/{id}/golden?lang=<lang>` (cùng nội dung, không có `download=1`) rồi ghi vào clipboard. Trong lúc lấy hiện *Copying…*; xong thì `role="status"` báo *Copied {cues} cues*. Nếu iframe không cho ghi clipboard, hoặc việc lấy lỗi, thì hiện ô văn bản chọn-tất-cả để chép tay (lỗi lấy hiện `Alert` đỏ).
- Mọi nút trong sheet cao ít nhất 44 px. Chỉ đọc, không tốn lời gọi model nên không có Estimate/Confirm.

Chính nút **Golden set** trên dòng chỉ mở sheet; nó không tải gì.

### 6.7 Research terms (P16)

Tra tên riêng và thuật ngữ trên web (Google Search grounding) rồi đổ kết quả vào Review. Không thêm màn hình hay mục nav.

- **Điểm bấm:** nút **Research terms (N)** trên thanh nút của tab Glossary, `N` là số term xuất hiện ở ≥ 2 tập mà chưa có trong glossary (đếm cục bộ, nguyên tắc 4). Dải trạng thái dưới thanh nút theo mẫu Extract facts: đang chạy (Stop), chờ quota, xong (Open Review →), lỗi (Retry). Mọi trạng thái đều có việc kế tiếp (nguyên tắc 2).
- **Tắt có lý do:** `N = 0` ("No unmapped terms found"); provider `openai_compat` ("Web research requires Gemini", cố định); hết ngân sách ("Research budget reached for this series", gỡ bằng Settings → Translation). Hai lý do sau có liên kết Settings.
- **Estimate (nguyên tắc 9):** số term, số lời gọi (1 tìm kiếm + 1 trích xuất; một lời gọi tìm kiếm có thể chạy nhiều truy vấn nên số lời gọi là ước), model của từng lời gọi, ngân sách đã dùng và sau lần chạy, `calls_left_today`, và ô *Auto-accept terms with 2+ independent sources* (mặc định theo cài đặt; bỏ tích chỉ áp cho lần này).
- **Ngân sách `research_max_calls_per_series`:** tính theo series, cộng dồn suốt đời series (chưa có cửa sổ thời gian); mặc định 10; muốn không trần phải bật *No cap*.
- **Tự chạy (nguyên tắc 3):** research chỉ tự chạy cho series có `auto_request` bật, cùng luồng bootstrap P13, hiện ở Activity là `policy · research` và tính vào ngân sách. Series khác chỉ chạy khi admin bấm nút.
- **Kết quả:** vào Review với nhãn **Research** (khác Facts và Your edits), kèm danh sách nguồn lấy từ grounding metadata (tên miền, mở tab mới), không có `confidence` mà có số nguồn. Term đủ ≥ 2 nguồn độc lập và đang bật auto_accept thì tự áp dụng, có nhãn **Auto** ở History và nút **Undo** nói rõ số tập sẽ stale. Tự áp dụng chỉ đánh cờ stale, không dịch lại (nguyên tắc 3).

## 7. Ràng buộc kỹ thuật cho iframe, đăng nhập và hết phiên

- Console không gửi `X-Frame-Options` hay `frame-ancestors`, nên nhúng được.
- Cookie phiên đang `SameSite=Strict`: chỉ chạy khi Organizr và console **cùng site** (cùng host, khác cổng vẫn là cùng site). Khác site thì Safari không gửi cookie. Đã làm (2026-11-17): biến môi trường `CONSOLE_COOKIE_SAMESITE=none` đặt cookie `SameSite=None; Secure` khi đứng sau HTTPS (proxy gửi `X-Forwarded-Proto: https`), còn qua HTTP thường thì rơi về `Lax` vì trình duyệt không giữ `None` thiếu `Secure`; mặc định vẫn `Strict`. Khi bật `none`, kiểm `Origin` của thao tác ghi là lớp chặn CSRF duy nhất.
- qBittorrent không chạy được trong Organizr vì tự chặn nhúng khung (clickjacking, CSRF, kiểm Host header). Các lựa chọn này nằm trong *Options → Web UI* của qBittorrent và có thể tắt; ghi ở đây vì admin đã nêu, chưa kiểm trên máy này.
- Tải file bằng `blob:` có thể bị iOS chặn trong iframe: xem mục 6.6.
- **Trang đăng nhập là trang tĩnh riêng**, không nằm trong gói React (ADR-17), để ngoại lệ duy nhất của middleware xác thực (`GET /login`, tài nguyên của nó, `POST /api/auth/login`) không đổi. Wireframe: tên đăng nhập, mật khẩu, **Sign in**; lỗi *Wrong user or password*, *Too many attempts, try again in 5 minutes*, và trường hợp console chưa có tài khoản (503 kèm hướng dẫn đặt `CONSOLE_USER`/`CONSOLE_PASSWORD`). Không có "quên mật khẩu" trên màn hình (đặt lại bằng `CONSOLE_PASSWORD_RESET=1`, ADR-7).
- **Hết phiên (401 trong iframe):** lớp phủ *"Your session expired"* với nút **Sign in** đưa khung hiện tại sang trang đăng nhập; sau đăng nhập quay về đúng `#hash` đang mở: giao diện lưu `location.hash` vào `sessionStorage` trước khi sang `/login`, trang đăng nhập đọc ra và đặt lại sau khi đăng nhập (phía client, server không đổi; chỉ nhận đường tương đối cùng origin). Bản nháp form đang sửa được giữ trong bộ nhớ trình duyệt để không mất.

## 8. Còn mở

Chỉ những điểm chưa có kết luận. Mọi điều đã chốt nằm ở nội dung các mục trên và ở SPEC #26; thiếu endpoint hay dữ liệu thì ở Phụ lục A.7, không nhắc lại ở đây. Sáu điểm "cần kiểm code" của v0.16 đã kiểm xong (2026-11-16, kết quả ở PLAN P12b) và đã vào nội dung các mục trên và Phụ lục A.7.

**Ý tưởng chưa quyết, không làm trong đợt này**

- Nút "Translate without prior context" cho job Waiting · context (cần kiểm hành vi chờ của Translate thủ công).
- "Open in Jellyfin": chưa có tích hợp nào.

---

## Phụ lục A. Nội dung từng màn hình và nguồn dữ liệu

Ký hiệu: **Có** = đã có endpoint; **Mới** = cần backend nhỏ (P12b); **Tính** = giao diện tự tính từ dữ liệu có sẵn.

### A.1 Shell (mọi trang)

| Hiển thị | Nguồn |
|---|---|
| Nav: Library · Activity · Settings (+ Review khi P13, badge số `pending`) | tĩnh |
| Banner toàn cục (mục 4.2) | `providers` (`state`, `reason`) và `key_warnings` ở `GET /api/status`/`/api/queue`; `status` và `warning` ở `GET /api/catalog/status` |
| Dải trạng thái: Running/Waiting, tuổi catalog, provider exhausted/breaker kèm `until` | `GET /api/queue` (`counts`, `providers`), `GET /api/catalog/status` (`synced_at`) |
| Lớp phủ hết phiên | mọi `/api/*` trả `401` |

### A.2 Home

| Hiển thị | Nguồn |
|---|---|
| Ô tìm theo tên; chip All · Missing Vietnamese · Running · Failed · Ignored | `GET /api/catalog` (số `failed` và `ignored` theo series: **Mới**, A.7) |
| Mỗi dòng series: huy hiệu, tên, số tập, `missing`, `open_jobs`, một nút việc kế tiếp | `GET /api/catalog` → `series[]` (`title`, `episodes`, `missing`, `open_jobs`) |
| Mỗi dòng phim: huy hiệu, tên, nhãn trạng thái, một nút | `GET /api/catalog/movies` → `items[].targets[].text` |
| Nút Sync now | `POST /api/catalog/sync` |
| **Không** hiện: Stale, `auto_request`, poster, năm | |

### A.3 Series page

| Phần | Hiển thị | Nguồn |
|---|---|---|
| Header | tên, bộ chọn ngôn ngữ đích, `auto_request` (Default / On / Off), Memory (Default / On / Off), `specials_after` (một tập, vd `S02E05`); `auto_request` lưu ngay, Memory và `specials_after` lưu bằng Apply (mục 6.2) | `GET /api/catalog/series/{id}`, `GET /api/series/{id}/memory`, `PUT …/policy`, `PUT …/memory` |
| Episodes | season, episode, title, nhãn trạng thái ghép, **một** nút; với mỗi `target`: content, job, wait, stale, version, model, `missing_units`, `updated_at`, `modified_outside`; chọn nhiều; Translate season/series (Estimate) | `GET /api/catalog/series/{id}`, `GET /api/library/series/{id}/units`, `POST /api/jobs` |
| Style | các trục (mục 6.1), preset, giá trị hiệu lực, dòng impact, Try 20 lines, Compare on a whole episode | `GET/PUT /api/series/{id}/style` (`dry_run`), `POST …/style/preview`, `GET /api/style/presets`, `GET …/style/suggestions` (gợi ý preset theo TMDB), `POST /api/units/{id}/variant` |
| Glossary | Terms (`source`, `target`, `locked`, `note`, `from`, `until`), Forms of address (`a`, `b`, `a_to_b`, `b_to_a`, `from`), `version`, `stale_units`, History (version, at, note, số mục), Restore, Copy from series, YAML import/export | `GET/PUT /api/series/{id}/glossary`, `…/history`, `…/restore`, `…/copy`; tải YAML: `GET /api/series/{id}/glossary.yaml?lang=` |
| Characters | Name (`name`, tên dịch, `gender`, mô tả) và Voice (`speech`, `aliases`, `catchphrases`, `samples`, `style`, `always`), thêm/xoá | glossary `characters`; `PUT …/cards`, `POST …/cards/delete` |
| Context | tập theo thứ tự câu chuyện (`has_source`, `has_facts`), policy, **Extract facts** (Estimate, một **Confirm**: tạo một job `extract` mỗi tập, theo dõi ở Activity; thẻ hiện "Reading S01E02 · 2 more in line" và **Stop reading**) | `GET /api/series/{id}/memory`, `POST /api/units/{id}/context/extract`, `GET /api/queue`, `POST /api/queue/stop` |

### A.4 Episode / Unit page

| Phần | Hiển thị | Nguồn |
|---|---|---|
| Header | tên, nhãn trạng thái, Current (version, origin, model, `created_at`), `missing_units`, **Remove output** | `GET /api/units/{id}`, `GET …/versions`, `POST …/output/remove` |
| Subtitles | `n`, `start`, `end`, `source`, `target`, `locked`; tìm `q`, nhảy `at`; sheet sửa cue | `GET …/cues`, `POST …/corrections` (`cue_edit`) |
| Corrections | `id`, `kind`, `payload`, `scope`, `created_at` | `GET/POST …/corrections`, `…/remove` |
| Retranslate | phạm vi, `correction_ids`, `new_corrections`, Estimate | `POST …/retranslate` |
| History | version, trigger, origin, status, parent, model, tokens, `created_at`, `params`; diff; Restore; Compare; Use this version | `GET …/versions`, `…/diff`, `POST …/revert`, `…/adopt` |
| Context | `used.sections` và `now.sections` (mỗi section: `title`, `body`; story memory), `changed`, `missing`; không có version glossary hay style | `GET …/context` |
| Style | ghi đè cấp tập | `GET/PUT /api/units/{id}/style` |
| Compare | `n`, `source`, `current`, `variant`, `differs`; chọn từng cue hoặc cả bản; checkbox lưu style; **Judge** (P15): thẻ "The judge looked at N cues: …" và một dòng lời khuyên dưới từng cue (`Judge prefers the variant: <lý do>`), nút **Ask the judge (N cues)** qua Estimate (chỉ hiện khi `judge_enabled`; lời khuyên không tick hộ ô nào) | `GET …/versions/{v}/compare` (`judge` mỗi hàng và cả trang), `POST …/choose`, `POST …/adopt` (+ lưu style: **Mới**), `POST …/judge` (`estimate_only`) |

### A.5 Activity

| Phần | Hiển thị | Nguồn |
|---|---|---|
| Providers | `model`, `state`, `reason`, `until`, `failures`, `usable` | `GET /api/queue` → `providers` |
| Model layers | `by_model` (done/failed), `escalated`, cửa sổ ngày (chỉ hiển thị, không chọn được) | `GET /api/status` → `model_layers` |
| Catalog | `synced_at`, `attempted_at`, `status`, `warning`, `items`, `listed`; Sync now | `GET /api/catalog/status`, `POST /api/catalog/sync` |
| Jobs (Requested · Running · Waiting · Failed · Done) | unit, ngôn ngữ, `origin`, `mode`, `stage`, tiến độ, `wait_reason`, `retry_at`, `attempts`, `error`, `missing_units`; Stop, Retry, Request that episode, Export job spec | `GET /api/queue`, `GET /api/jobs/{id}/spec` (mỗi nhóm tối đa 100, `counts` là số thật) |

### A.6 Review (P13)

Nhóm theo series; mỗi mục: `source`, nội dung đề xuất, bằng chứng (cue + timecode hoặc URL), số bản dịch sẽ stale; Accept · Edit · Reject; Accept all in series (kèm số lượng). Đã làm ở P13: hai tab **Pending** và **History** (đã chấp nhận, kèm nhãn *Auto* và *Edited*, và đã từ chối); số stale đọc từ `POST /api/suggestions/{id}/accept` với `dry_run` (không có nút Check); Edit là bottom sheet có số stale theo bản sửa; Accept all hỏi trong bottom sheet kèm số và số stale của cả nhóm (`POST /api/suggestions/bulk`, `dry_run`); sau mỗi quyết định có dòng thông báo kèm **Undo**. Accept một mục không hỏi lại vì số stale đã hiện trên thẻ. Series bị `auto_request` giữ lại (FR-15.12) có khung **Continue** ở đầu trang series và nhãn *Waiting for glossary review*.

### A.6b Insights: "Your corrections" (P15)

Phần cuối trang Insights, chỉ đọc. Mỗi series có ít nhất một ngôn ngữ thì có một thẻ; series chưa có ngôn ngữ nào thì không hiện. Mỗi ngôn ngữ một dòng gồm huy hiệu ngôn ngữ, nút **Look at my edits** và nút **Golden set** (cao ít nhất 44 px; xếp xuống dòng khi hẹp). Khổ dọc: thẻ xếp chồng. Khổ ngang: xem wireframe-v2.md mục 5b.

| Hiển thị | Nguồn |
|---|---|
| Danh sách series; tên series là liên kết tới tab Glossary của series (chỉ khi series có trong catalog; nếu không thì chữ thường) | `GET /api/library/series`, `GET /api/catalog` |
| Các ngôn ngữ của series (một dòng mỗi ngôn ngữ) | `GET /api/library/series/{id}/units` → `languages` |
| Nút **Look at my edits** (tên truy cập: "Look at my <lang> edits of <series>"): tìm mẫu sửa lặp lại trong cue đã sửa và đề xuất vào Review (`pending`). Không tốn lời gọi model, không qua Estimate. Khi chạy nút hiện trạng thái loading; xong thì một dòng kết quả `<lang>: <câu tóm tắt>` (`role="status"`) dưới thẻ; lỗi hiện `Alert` đỏ | `POST /api/suggestions/from-edits` (`series_id`, `lang`) |
| Nút **Golden set** (tên truy cập: "Open the <lang> golden set of <series>"): mở bottom sheet có **Download** (liên kết tải) và **Copy** (xem 6.6). Download không có trạng thái loading; Copy có *Copying…* | Download: `GET /api/series/{id}/golden?lang=&download=1` → file `<series-key>.<lang>.golden.json`. Copy: `GET /api/series/{id}/golden?lang=`. Cả hai có `golden_version`, `series`, `lang`, `cues[]`: `season`, `episode`, `cue`, `source`, `text` |
| Đang tải danh sách series: vòng quay "Loading the series"; không có series: "The worker keeps no series yet." | `GET /api/library/series` |

Dòng ngôn ngữ hiện thêm `{n} edits` (số cue đã sửa; cần API trả số này); series sắp theo số sửa giảm dần; bằng 0 thì cả hai nút tắt. Đầu trang có liên kết *Your corrections ↓* và một bộ chọn cửa sổ thời gian (7d · 30d · 90d) cho mọi khối số đo. Phần còn lại của trang Insights (số đo, theo model, từng ngày) chưa có bảng trường API ở đây; bố cục ở wireframe-v2.md mục 5b.

### A.7 Còn thiếu (Mới, làm ở P12b hoặc để sau)

- Lưu style của variant khi **Use this version**: `POST /api/units/{id}/versions/{v}/adopt` nhận `apply_style` (một yêu cầu) và `dry_run` (đếm số tập khác sẽ stale trước; không tính tập đang dùng bản variant). Đã làm 2026-11-17.
- `estimate_only` cho Retry, variant và Retranslate theo dải: đã làm 2026-11-17 (xem bảng 6.4).
- Số đếm `failed`, `ignored` theo series (và cho `movies`) ở `GET /api/catalog`: đếm rẻ được bằng SQL trên `jobs` và `unit_decisions`, theo đúng định nghĩa nhãn Failed (không có job mở và job kết thúc mới nhất của key là `failed`); chỉ là hai số trong phần tổng quan có sẵn, không phải read model mới.
- Glossary, style và thẻ nhân vật **cấp phim** (`owner_kind = movies`): route đã có từ 2026-11-17 theo unit phim (`/api/movies/{unit_id}/glossary|glossary/history|glossary/restore|style|style/preview|cards`, xem `docs/API.md`) cho liên kết **Movie Glossary** và tab Style của trang Unit; impact tính cho một unit.
- "Try 20 lines" khi series chưa có bản dịch: **không cần backend**. `rows[].current` là `null`; giao diện để trống cột hiện tại.
- Không làm ở v1: `dry_run` và dòng impact cho `PUT /api/series/{id}/memory` (xem 6.2 và SPEC #26 điểm 14).
- Quota ngày đã dùng; danh sách few-shot theo series; lý do một bản dịch bị Stale; tầng nguồn của từng giá trị style.

### A.8 Giá trị hợp lệ của các trường hay bị vẽ sai

| Trường | Giá trị |
|---|---|
| Phiên bản: `origin` | `machine` · `edit` · `revert` · `external` |
| Phiên bản: `status` | `current` · `superseded` · `candidate` · `removed` · `pruned` |
| Phiên bản: `trigger` | `auto` · `retranslate` · `variant` · `cue_edit` · `revert` · `external` · `remove` |
| Job: `origin` | `manual` · `policy` · `retranslate` · `cue_edit` · `revert` · `variant` |
| Job: `mode` | `fill` · `replace` · `variant` |
| Job: `wait_reason` | `quota` · `provider` · `context` · `settle` · `rate` |
| Provider: `state` | `closed` · `open` · `exhausted` · `invalid` · `half_open` |
| Khoá: `status` | `ok` · `invalid` · `exhausted` |
| Catalog: `status` | `ok` · `kept` · rỗng |

## Phụ lục B. Settings: kiểu, mặc định và giới hạn

Nguồn: `docs/CONFIGURATION.md`. Khi một tên giá trị trong tài liệu đổi, bảng này đổi theo. Tên trường ở cột đầu là tên API.

| Nhóm | Trường (nhãn gợi ý) | Kiểu | Mặc định | Ghi chú |
|---|---|---|---|---|
| Connections | `bazarr_url` (Bazarr URL) | URL | rỗng | |
| | Khoá Bazarr, TMDB, Gemini 1, Gemini 2, OpenAI-compatible | bí mật | | hiện 4 ký tự cuối, trạng thái `ok`/`invalid`/`exhausted`, Test; dán khoá mới hoặc Test thành công thì trạng thái về `ok` |
| | `llm_provider` (LLM provider) | `gemini` · `openai_compat` | `gemini` | |
| | `llm_base_url` (Base URL) | URL tới `/v1` | rỗng | chỉ khi `openai_compat` |
| Translation | `gst_model` (Default model) | chọn từ danh sách | `gemini-3.1-flash-lite` | |
| | `gst_fallback_model` (Strong model) | chọn | `gemini-3.5-flash` | xoá để bỏ tầng thứ hai |
| | `research_model` (Research model) | chọn (chỉ model có grounding) | rỗng = dùng `gst_model` | chưa ai gọi (P16); Gemini only |
| | `research_max_calls_per_series` (Max research calls per series) | số nguyên ≥ 1, và công tắc *No cap* | 10 | P16; không có "0 = không giới hạn": muốn bỏ trần phải bật *No cap* |
| | `judge_enabled` (LLM judge) | bật/tắt | tắt | mỗi lần dùng tốn lời gọi; chỉ là lời khuyên (P15) |
| | `judge_model` (Judge model) | chọn | rỗng = dùng `gst_model` | model mạnh hơn model dịch thì chấm tốt hơn |
| | `gst_temperature` | số | `0.7` | |
| | `gst_thinking_budget`, `gst_no_thinking` | số, bật/tắt | `2048`, tắt | |
| | `gst_paid_quota` (Paid quota tier) | bật/tắt | tắt | |
| | `llm_max_input_tokens`, `llm_max_output_tokens` | số | `128000`, `8192` | |
| | `source_languages`, `target_languages` | danh sách `{code, language, enabled}` | en; zh (ví dụ trong tài liệu) | |
| Library | `media_roots` | danh sách đường dẫn | `["/media"]` | |
| | `catalog_sync_interval` (Sync every, giây) | số | `300` | `0` tắt đồng bộ định kỳ |
| | `catalog_shrink_guard` (Ignore a read that shrinks by, %) | **phần trăm** | `20` | không phải bật/tắt |
| | `auto_request` (Auto-request) | bật/tắt | tắt | nâng cao |
| | `auto_request_daily_cap` | số | `0` = không trần | |
| Queue and quota | `job_settle_seconds` (Settle window, giây) | số | `600` | |
| | `breaker_threshold` | số ≥ 1 | `3` | |
| | `max_crashes` | số | `3` | |
| | `max_calls_per_job` | số | `20` | `0` = không trần |
| | `max_calls_per_day` | số | `0` = không trần | |
| | `transient_retry_seconds` | số | `30` | |
| | `quota_reset_tz` (Provider quota resets at midnight in) | múi giờ IANA | `America/Los_Angeles` | múi giờ của provider, không phải của admin |
| Story and style | `memory_enabled` | bật/tắt | tắt | |
| | `memory_model`, `summarize_model` | chọn | rỗng = `gst_model` (summarize: rồi `memory_model`) | |
| | `memory_gap_wait_seconds` | số | `1800` | `0` không chờ |
| | `memory_budget_tokens`, `memory_recent_episodes` | số | `8000`, `3` | |
| | `few_shot_budget_tokens` | số | `600` | `0` tắt |
| | `glossary_filter_threshold` | **số term** | `50` | `0` không lọc; không phải số thập phân |
| | `edit_coalesce_minutes` | số ≥ 0 | `10` | gộp các lần sửa cue |
| Maintenance | `log_level` | `DEBUG` · `INFO` · `WARNING` · `ERROR` | `INFO` | |
| | `log_prompt_bodies` | `all` · `sample` · `errors` · `off` | `all` | **enum**, không phải hộp kiểm |
| | `log_body_max_bytes` | số ≥ 1024 | `262144` | |
| | `log_max_mb`, `log_retention_days` | số | `500`, `30` | `0` tắt giới hạn |
| | `events_retention_days` | số | `90` | `0` giữ mãi |
| | `llm_cost_in_per_mtok`, `llm_cost_out_per_mtok` | số | `0` | chỉ để ước tính chi phí |
| | `retention_done_days`, `retention_failed_days` | số | `30`, `90` | `0` tắt nhóm |
| | `retention_versions_keep`, `retention_glossary_keep` | số | `5`, `20` | `0` tắt nhóm |
| | nút **Clean up now** (xem trước rồi xác nhận), liên kết Backups, Logs | | | |
| Security | đổi mật khẩu | form | | |

Các giá trị mẫu trong wireframe (model, số) chỉ là minh hoạ; dùng mặc định ở bảng này khi vẽ.
