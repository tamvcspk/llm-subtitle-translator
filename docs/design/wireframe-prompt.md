# Prompt cho wireframe v2

> Dùng cùng UX.md v0.16. Bản [wireframe-v2.md](wireframe-v2.md) (v2.1) đã vẽ từ prompt này; prompt giữ lại để vẽ lại hoặc mở rộng. Prompt dưới đây tự chứa (công cụ vẽ không đọc được repo), nên khi UX.md đổi thì sửa prompt theo. Dán nguyên phần trong khung, từ "VAI TRÒ" đến hết "KIỂM TRA CUỐI".

```text
VAI TRÒ
Bạn là UX/UI designer cho một console quản trị nhỏ. Hãy vẽ WIREFRAME v2 (ASCII, không màu, không phong cách thị giác) cho mọi màn hình dưới đây. Chỉ vẽ cấu trúc, nội dung, trạng thái và hành động. Không đề xuất thư viện hay công nghệ. Không thêm trường, nút hay màn hình ngoài những gì được liệt kê; nếu thấy thiếu, ghi riêng ở cuối dưới tên "ĐỀ XUẤT THÊM" thay vì vẽ chen vào.

SẢN PHẨM
Console dịch phụ đề bằng LLM, đi kèm Bazarr. Một admin duy nhất. Console chạy TRONG IFRAME của Organizr, trên iPad (cảm ứng, không hover, không có thanh địa chỉ). Giao diện tiếng Anh, từ vựng kiểu arr stack (Sonarr/Radarr/Bazarr), nhãn ngắn. Việc thường làm nhất: "Series/phim này đã có phụ đề tiếng Việt chưa?" (tìm theo tên, trả lời trong một màn hình). Việc thứ hai: bấm Translate. Việc thứ ba: xem phim thấy phụ đề dở, quay lại cải thiện STYLE của cả series (không phải sửa từng câu).

RÀNG BUỘC CHUNG (bắt buộc áp dụng)
1. Cảm ứng: vùng bấm >= 44px; không chức năng nào chỉ hiện khi hover. Điều hướng bằng #hash (mỗi màn hình và mỗi tab có đường dẫn riêng, ví dụ #/series/12/style).
2. KHỔ CHÍNH LÀ DỌC (~820px, iPad cầm dọc). Khổ ngang (>= 1024px) là khổ phụ, cùng bố cục. Vẽ MỌI màn hình ở khổ dọc; dưới 1024px mọi bảng rộng (danh sách cue, Forms of address, Try 20 lines, Compare, providers) là THẺ xếp dọc, thân trang không cuộn ngang (thanh tab được cuộn trong chính nó). Khổ ngang chỉ cần một bảng "khác gì" và vài ví dụ bảng.
3. Mỗi trạng thái có đúng MỘT việc kế tiếp (một nút chính). Một dòng không bao giờ chỉ là nhãn.
4. Không có gì tự chạy. Không popover cho xác nhận (chạm ra ngoài là đóng): dùng dialog hoặc bottom sheet có Cancel và Confirm.
5. MỌI nút tốn lời gọi model đều mở hộp ESTIMATE trước khi chạy. Hộp gồm: jobs, cues, ước tính lời gọi, "calls left today" (nếu có trần), "paused until" (nếu đang hết quota), thời gian và giờ xong dự kiến, nút Cancel/Confirm. Các nút này: Translate (tập/mùa/series/tất cả), Translate again, Retranslate, Retry (kể cả Retry All Failed), Extract facts, Compare on a whole episode, Request that episode. NGOẠI LỆ duy nhất: "Try 20 lines" (ghi "≈ 1 call" ngay trên nút, chạy luôn).
6. KHÔNG có nút "Check". Khi chỉnh form Style hoặc Glossary, hiện một dòng cập nhật trực tiếp: "Saving flags N episodes stale." hoặc "No translation is affected." (có trạng thái "Checking…"). Bấm Save chỉ hiện bottom sheet xác nhận khi N > 0.
7. Banner toàn cục dưới thanh nav (xem mục SHELL). Phiên hết hạn là lớp phủ riêng.
8. Một nhãn trạng thái một nghĩa (xem NHÃN). Không dùng từ nội bộ pending/deferred/processing.
9. Tải file (Export YAML, Export job spec, tải backup) = liên kết do server phục vụ + nút Copy (nếu không ghi được clipboard thì hiện ô văn bản chọn-tất-cả). Không vẽ nút "tải bằng blob".

NHÃN TRẠNG THÁI (ghép từ ba trục, ví dụ "Missing · Requested", "Translated · Waiting · quota")
- Nội dung: Missing · Translated · Edited · External · Removed · Stale · Outside profile
- Job: Requested · Running (có tiến độ) · Waiting · quota | provider | context | settle | rate · Failed
- Quyết định: Ignored
- Huy hiệu của PHIÊN BẢN (không phải của mục): Candidate
Việc kế tiếp theo nhãn: Missing → Translate (nếu không có phụ đề nguồn: ghi "no source subtitle", không có nút, chờ Bazarr) · Translated → Improve · Edited → History · External → View · Removed → hai nút riêng: Restore (tức thì, miễn phí) và Translate again (tốn lời gọi, qua Estimate) · Stale → Retranslate (qua Estimate) · Outside profile → Translate · Requested → Cancel · Running → Stop · Waiting·context → "Request that episode" · Waiting·quota → không có nút, hiện giờ chạy lại · Waiting·provider → "Open Connections" nếu khoá/model bị từ chối, không nút nếu breaker mở · Waiting·settle/rate → không nút · Failed → Retry · Ignored → Stop ignoring.

SHELL (mọi trang)
- Thanh nav: Library · Activity · Settings. KHÔNG hiện "Review" (chưa có trước P13).
- Dải trạng thái nhỏ: số job Running/Waiting, "Synced 2m ago", provider đang exhausted hoặc breaker mở kèm "until". Hết quota ngày là bình thường, KHÔNG lên banner.
- Banner (dưới nav): (a) đỏ khi khoá API bị từ chối hoặc model lạ: "Gemini rejected the API key. Jobs are waiting." + nút "Open Connections". (b) vàng khi danh mục Bazarr ở trạng thái "kept": "Bazarr could not be read. Showing the library as of {time}. {reason}" + nút "Sync now".
- Lớp phủ hết phiên (khi API trả 401): "Your session expired." + nút "Sign in"; ghi chú: bản nháp đang sửa được giữ.
- Màn hình đăng nhập (trang tĩnh riêng, ngoài ứng dụng): Username, Password, Sign in; lỗi "Wrong user or password" / "Too many attempts, try again in 5 minutes" / trạng thái "No console account is set up yet" kèm hướng dẫn (đặt biến môi trường). KHÔNG có "Forgot password".

CÁC MÀN HÌNH CẦN VẼ

1) HOME (#/library)
- Ô tìm theo tên + chip lọc: All · Missing Vietnamese · Running · Failed · Ignored. KHÔNG có chip hay số Stale.
- Danh sách trộn series và phim, huy hiệu [TV] / [MV]. Mỗi dòng: huy hiệu, tên, (series) số tập, "Missing: N", "Running/Waiting: N" nếu có; (phim) nhãn trạng thái ghép; và MỘT nút theo thứ tự ưu tiên: có Failed → "View failed"; còn Missing → "Translate missing" (qua Estimate); còn lại → "Improve". Mục đã có job Requested/Running/Waiting thì KHÔNG có nút Translate. KHÔNG hiện Auto-request, KHÔNG hiện Stale, KHÔNG poster.
- Bấm [TV] → trang Series. Bấm [MV] → thẳng trang Unit (màn hình 3).
- Trạng thái: danh sách rỗng khi chưa có Bazarr; không có kết quả tìm; banner catalog "kept".

2) SERIES (#/series/:id/...), header chung + 5 tab
Header: "< Library", tên, bộ chọn ngôn ngữ đích (ẩn nếu chỉ có một), Auto-request [Default | On | Off], Memory [Default | On | Off], "Specials after" (nhập một tập, ví dụ S02E05). Auto-request lưu ngay khi chạm; Memory và Specials after lưu bằng nút "Apply" (đổi chúng có thể làm bản dịch stale).
2a) Tab Episodes: nhóm theo mùa. Nút lớn "Translate season N (x missing)" và "Translate whole series" → Estimate. Mỗi dòng tập: SxxEyy, tên, nhãn ghép, một nút việc kế tiếp, và (nếu có) "version, model, updated" + cảnh báo "File changed outside the worker". Chọn nhiều hàng (checkbox) để dịch/bỏ qua hàng loạt. PHẢI vẽ đủ mẫu hàng: Missing (có Translate), Missing · no source subtitle, Missing · Requested, Missing · Running (tiến độ 120/340), Missing · Waiting · context (nút Request that episode), Waiting · quota (giờ chạy lại), Failed (lý do + Retry), Translated, Translated · Stale (Retranslate), Edited (locked cues: 4), External, Removed (Restore + Translate again), Ignored, Outside profile, Candidate (huy hiệu trên hàng có bản nháp).
2b) Tab Style (nút "Improve" ở dòng tập dẫn vào đây): 
 - Preset (chọn; gợi ý theo thể loại TMDB nếu có). 
 - 4 lựa chọn chính, MỖI lựa chọn là Segmented Control có ô "Inherit" ĐẦU TIÊN và là mặc định khi chưa đặt: Formality (Inherit | Casual | Neutral | Formal) · Tone (ô chữ tự do, có Inherit/Clear) · Swearing (Inherit | Keep | Soften | Remove) · Idioms and wordplay (Inherit | Literal | Balanced | Adapt). Khi đang Inherit, ô hiện giá trị đang hiệu lực: "Inherit · now Neutral". KHÔNG vẽ nhãn "from: Series/Global" (hệ thống không biết tầng nguồn).
 - "More" (gập): Dialect (Inherit | North | South | Neutral) · Honorifics (anime) (Inherit | Keep | Translate) · Characters per second · Longest line · Lines per cue · Fast dialogue (Inherit | Allow | Don't) · Notes.
 - Dòng tác động stale trực tiếp (ràng buộc 6). Nút Save. KHÔNG có nút Check.
 - "Try 20 lines (≈ 1 call)": bảng/thẻ "Now" | "With this style" (cột "Now" bị ẩn nếu series chưa có tập nào được dịch: khi đó dùng 20 cue đầu của tập đầu có phụ đề nguồn, chỉ hai cột Source | Preview). Cho chọn tập mẫu. Trạng thái: đang gọi, lỗi provider (nêu loại lỗi), kết quả. Khổ dọc: mỗi cue một thẻ (Source trên, Now, Preview).
 - "Compare on a whole episode": chọn tập + đặt nhãn → Estimate → job chạy nền; sau đó hiện "A candidate is ready in History of S01E03" kèm liên kết sang màn hình Compare.
2c) Tab Glossary = hai phần: Terms (source, target, Locked, Note, From episode, Until episode) và Forms of address (cặp nhân vật A↔B; hai hướng "A calls B" và "B calls A" mỗi hướng một chuỗi dạng "self–other", ví dụ "em–anh"; From: tập hoặc timecode). Forms of address ở khổ dọc là thẻ. Thanh trên: Version, "Saving flags N episodes stale" (trực tiếp), Save, History (danh sách phiên bản, Restore), Copy from another series (có Merge), Import YAML (bottom sheet: chọn tệp hoặc dán vào ô chữ, dòng impact trực tiếp, lỗi nêu đường dẫn sai), Export YAML. KHÔNG có sub-tab Characters ở đây.
2d) Tab Characters = lưới thẻ; mỗi nhân vật một thẻ có hai phần: Name (tên dịch, giới tính, mô tả ngắn) và Voice (cách nói, bí danh, câu cửa miệng, câu mẫu, "Always include in prompt", ghi đè style cho riêng nhân vật). Cách xưng hô chỉ hiện dạng tóm tắt chỉ đọc kèm liên kết sang Forms of address; KHÔNG có ô nhập "address" trên thẻ. Nút Add character, Edit, Delete. Trạng thái rỗng.
2e) Tab Context (story memory): danh sách tập theo thứ tự câu chuyện, mỗi tập hai dấu: Source ✓/✗ và Facts ✓/✗; số tập thiếu; nút "Extract facts" (qua Estimate: số tập có source mà chưa có facts, mỗi tập một lời gọi). KHÔNG vẽ "token đã dùng" hay trạng thái "generating".

3) EPISODE / UNIT PAGE (#/series/:id/episodes/:ep và #/unit/:id cho phim)
Dùng chung layout. Phim: không có bộ chọn tập, không có tab Context; Glossary/Style cấp phim mở từ đây.
- Header: tên, nhãn ghép, bản Current (version, origin, model, thời gian), "Missing units" nếu có, và nút "Remove output" (xác nhận: "The version stays in History and can be restored"). "Remove output" CHỈ nằm ở đây.
- Tabs: Subtitles · Corrections · Retranslate · History · Context (chỉ tập) · Style.
- Subtitles: ô tìm, ô "Jump to timecode". Bảng: #, timecode, Source, Target, biểu tượng khoá. Khổ dọc = thẻ. Bấm Target mở BOTTOM SHEET sửa cue: Source phía trên, ô Target, nút Prev/Next, Save, Cancel; không sửa inline trong bảng. Cue đã sửa là cue khoá; "Unlock" gỡ khoá. Không hiện chi tiết gộp các lần sửa.
- Corrections: danh sách (Term | Address | Note | Cue edit), thêm mới, phạm vi Episode | From here on | Whole series.
- Retranslate: phạm vi (Whole episode | Cue range | Time range), danh sách correction đính kèm + thêm mới, dòng "Will overwrite N cues, keep M locked", nút → Estimate. Ghi rõ: "The result replaces the current subtitle now; the old one stays in History."
- History: mỗi phiên bản = version, trigger, origin, status, model, tokens, thời gian; hành động theo dòng: Restore, Diff, và với Candidate: Compare + Use this version. Không có "Remove output" ở đây.
- Context: hai cột "Used for the current translation" và "If translated now", mỗi cột là các section chữ của story memory (tiêu đề + nội dung); cờ "Changed"; các tập thiếu. KHÔNG hiện version glossary/style, không có "lý do stale cụ thể".
- Style (ghi đè cấp tập): cùng form như 2b, mọi trục mặc định Inherit, dòng chú "Overrides the series style for this episode only", nút xoá ghi đè.

4) COMPARE (#/series/:id/episodes/:ep/compare)
Current vs một bản Candidate (variant). Lọc "Show only differing cues (48 of 342)". Mỗi cue: Source, Current, Variant, ô chọn (pick). Khổ dọc = thẻ. Chân trang: "Selected 12 from Variant, 330 from Current" + hai nút: "Apply picked cues" (không có checkbox lưu style) và "Use this version" (cả bản Variant). "Use this version" mở bottom sheet xác nhận có checkbox MẶC ĐỊNH BẬT "Also apply this variant's style to the series", kèm dòng "Saving flags N episodes stale." và hiện preset + style của variant sẽ được lưu.

5) ACTIVITY (#/activity)
- Providers: mỗi model một dòng: state (closed/open/exhausted/invalid/half open), lý do, "until", số lần lỗi liên tiếp, usable.
- Model layers: số job Done/Failed theo model, Escalated (total/done/failed/waiting) và dòng chú "counts cover the last 30 days done / 90 days failed" (chỉ để đọc, KHÔNG có công tắc chọn 30d/90d).
- Catalog: "Synced {time}", status ok/kept + lý do, số mục, nút "Sync now".
- Jobs: tab Requested · Running · Waiting · Failed · Done (mỗi nhóm tối đa 100 dòng, số đếm trên tab là số thật). Dòng: series, SxxEyy, ngôn ngữ, origin (manual/policy/retranslate…), mode (fill/replace/variant), giai đoạn + tiến độ, lý do chờ + giờ thử lại, số lần restart, lỗi. Hành động: Stop, Retry, Request that episode, "Retry all failed" (qua Estimate: số job; số lời gọi ghi "unknown").
- KHÔNG vẽ "quota used today" (chưa có dữ liệu): ghi chú "Calls left today: shown in Estimate only".

6) SETTINGS (#/settings): 7 thẻ GẬP, mỗi thẻ có nút Save RIÊNG và chỉ báo "Unsaved changes"; hai thẻ đầu mở sẵn. Có chặn khi rời trang với thay đổi chưa lưu. Dùng ĐÚNG kiểu và mặc định dưới đây (đừng bịa giá trị):
 1 Connections: Bazarr URL; khoá Bazarr, TMDB, Gemini 1, Gemini 2, OpenAI-compatible (mỗi khoá: nhãn, 4 ký tự cuối, trạng thái ok/invalid/exhausted, nút Test, thay khoá); LLM provider (Gemini | OpenAI-compatible); Base URL (chỉ hiện khi OpenAI-compatible).
 2 Translation: Default model (mặc định gemini-3.1-flash-lite), Strong model (gemini-3.5-flash, xoá được), Research model (rỗng = dùng Default), Temperature (0.7), Thinking budget (2048), No thinking (tắt), Paid quota tier (tắt), Max input tokens (128000), Max output tokens (8192), Source languages và Target languages (danh sách có công tắc).
 3 Library: Media roots (danh sách đường dẫn, mặc định /media), Sync every (giây, 300; 0 = tắt), "Ignore a read that shrinks by" (PHẦN TRĂM, 20), Auto-request (Off | On, mặc định Off, ghi chú "translates missing items of chosen series without asking"), Auto-request daily cap (0 = no cap).
 4 Queue and quota: Settle window (giây, 600), Breaker threshold (3), Max crashes (3), Max calls per job (20; 0 = no cap), Max calls per day (0 = no cap), Transient retry (giây, 30), "Provider quota resets at midnight in" (múi giờ IANA, mặc định America/Los_Angeles, ghi chú: đây là múi giờ của PROVIDER, không phải múi giờ của bạn).
 5 Story and style: Story memory (Off), Memory model (rỗng = Default), Summarize model (rỗng), Gap wait (giây, 1800; 0 = never wait), Memory budget (tokens, 8000), Recent episodes (3), Few-shot budget (tokens, 600; 0 = off), "Filter terms above" (SỐ term, 50; 0 = never), Merge cue edits within (phút, 10).
 6 Maintenance: Log level (DEBUG | INFO | WARNING | ERROR, INFO), Keep prompt bodies (All | Sample | Errors only | Off, mặc định All), Max body size (bytes, 262144), Max log size (MB, 500; 0 = no limit), Keep logs (days, 30), Keep events (days, 90; 0 = forever), Cost per million tokens in/out (0/0), Keep finished jobs (days, 30), Keep failed jobs (days, 90), Keep versions per subtitle (5), Keep glossary history (20); nút "Clean up now" (xem trước số file và dung lượng rồi xác nhận); liên kết sang trang Backups và trang Logs (hai trang riêng, luôn mở được).
 7 Security: đổi mật khẩu (Current, New >= 8 ký tự, Update password).
Vẽ cả trạng thái: đang lưu, đã lưu, lỗi lưu, "Test" thành công/thất bại.

7) BACKUPS và LOGS: mỗi trang một khung đơn giản (Backups: danh sách tệp `.zip`, tạo, tải (chỉ liên kết server, không nút Copy), nhập; ghi chú "settings only, keys excluded unless you tick Include keys". Logs: bộ lọc mức, tìm, Clear). Nhẹ, không cần chi tiết.

CÁCH TRÌNH BÀY
- Mỗi màn hình: tiêu đề + đường dẫn #hash, wireframe ASCII khổ dọc (khung rộng ~80 ký tự), rồi danh sách TRẠNG THÁI (rỗng, đang tải, lỗi, thành công) và các hộp thoại/bottom sheet đi kèm. Cuối cùng một bảng "khổ ngang khác gì".
- Ghi tên trường dữ liệu trong ngoặc nhọn bên cạnh nội dung (ví dụ {missing}, {wait_reason}) để dev đối chiếu. Trường nào không có trong văn bản trên thì đánh dấu [NEW] chứ đừng im lặng thêm.
- Dữ liệu mẫu: dùng mặc định ở mục SETTINGS; không dùng tên model khác (không có "gemini-2.5-*"). Giá trị hợp lệ: phiên bản origin = machine | edit | revert | external; status = current | superseded | candidate | removed | pruned; trigger = auto | retranslate | variant | cue_edit | revert | external | remove; job origin = manual | policy | retranslate | cue_edit | revert | variant; job mode = fill | replace | variant; khoá = ok | invalid | exhausted.
- Cuối cùng liệt kê: (a) các "ĐỀ XUẤT THÊM" nếu có, (b) mọi chỗ bạn buộc phải đoán.

KIỂM TRA CUỐI (tự rà trước khi trả lời, ghi kết quả từng dòng)
[ ] Home: không có Stale, không có Auto-request, có chip Failed và Ignored, [MV] đi thẳng vào Unit.
[ ] Không nút Check ở đâu cả; mọi form Style/Glossary có dòng tác động stale trực tiếp.
[ ] Mọi Segmented Control của Style có Inherit đầu tiên; có Style cấp tập ở trang tập.
[ ] Mọi nút tốn quota đi qua Estimate (trừ Try 20 lines); "Retry all failed" cũng qua Estimate.
[ ] Sửa cue bằng bottom sheet có Prev/Next; "Remove output" ở header của Current, không ở dòng History.
[ ] Compare: "Use this version" có checkbox lưu style mặc định bật; "Apply picked cues" không có.
[ ] Try 20 lines có biến thể series mới (2 cột, không có Now).
[ ] Banner đỏ/vàng, lớp phủ hết phiên, màn hình đăng nhập.
[ ] Settings đủ 7 thẻ, Save theo thẻ, kiểu và mặc định đúng (shrink guard là %, filter terms là số, prompt bodies là enum, quota timezone ghi chú đúng).
[ ] Mọi màn hình vẽ ở khổ dọc ~820px; danh sách cue, Forms of address, Try 20 lines, Compare, providers đều là thẻ.
[ ] Không có công tắc 30d/90d, không có "from: Global", không có token memory đã dùng.
```

## Cách dùng

1. Dán khối trên vào công cụ vẽ (Gemini hoặc tương tự). Nếu đầu ra quá dài, chia theo màn hình: gửi phần VAI TRÒ → NHÃN → SHELL một lần, rồi gửi lần lượt từng mục "CÁC MÀN HÌNH CẦN VẼ".
2. Đối chiếu kết quả với "KIỂM TRA CUỐI" và UX.md §8 trước khi dựng.
3. Các quy tắc đã chốt (SPEC #26 điểm 12) đã nằm trong prompt. Nếu bạn đổi một quy tắc, sửa UX.md, SPEC #26 và prompt này cùng lúc.
