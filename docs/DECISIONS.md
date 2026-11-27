# Quyết định kỹ thuật

Chỉ thêm, không sửa entry cũ. Đổi quyết định thì thêm entry mới và ghi "Thay thế D-xx".

Các ADR chi tiết nằm ở [docs/design/ARCHITECTURE.md](design/ARCHITECTURE.md) (ADR-1 đến ADR-18); file này chỉ là mục lục và các quyết định áp dụng khi adopt.

## D-01 · 2026-11-25 · Áp project-kit ở Mode 1

- Bối cảnh: repo đã có AGENTS.md, docs/design/ và CI; cần một lệnh kiểm tra duy nhất và file handoff cho việc đổi tool.
- Chọn: Mode 1. Giữ nguyên AGENTS.md, SPEC và ADR hiện có; thêm CLAUDE.md, .agent/rules/project.md, scripts/check, docs/HANDOFF.md.
- Vì: repo chưa dùng worktree song song; Mode 2 chưa cần.
- Đã loại: Mode 2 (không có nhiều phiên song song trên nhánh riêng); tạo docs/SPEC.md và docs/BRIEF.md mới (trùng với docs/design/SPEC.md).
- KB: không có ghi chú liên quan.

## D-02 · 2026-11-26 · Phạm vi P19 (Dịch & Glossary) sau review chéo

- Bối cảnh: trace homeserver (*Malcolm in the Middle*) cho thấy nhân vật bị tách đôi (`Mom`/`Lois`, bug `_Names.add` trong `fold.py`), Relationship state mâu thuẫn Glossary, prompt phình 72 dòng Forms of address. Bản nháp P19a–d đề xuất gộp alias `Mom→Lois` và lọc theo facts tự trích.
- Chọn: P19a–d bản sửa. (1) Danh xưng chung (Mom, Dad, Sir, Grandma…) bị lọc khỏi `name` và `aliases` trước `fold()`; chỉ gộp alias là tên riêng. (2) Hướng quan hệ đổi từ enum `a_above`/`b_above` sang `senior: <tên> | equal`. (3) TMDB Credits theo tập (cast + guest_stars, 0 model call) là nền của P19b/c: chính tả chuẩn và phạm vi lọc Forms of address. (4) Harness hồi quy: diff `subcore render-prompt` trước/sau (0 call) + A/B nhỏ ~30 câu xưng hô trên S02E04. (5) LLM-as-a-Judge đóng băng nguyên trạng (`judge_enabled = false`, không sửa code).
- Vì: "Mom" là danh xưng tương đối (Lois gọi Ida là "Mom"); gộp làm alias toàn cục quy mọi "X & Mom" về Lois (đúng lỗi `Hal ↔ Mom` S05E15). Các dòng "Malcolm is the senior" sai cùng chiều, khả năng model đảo enum hướng. Lọc theo facts tự trích mất rule khi extract bỏ sót nhân vật. Không đo trước/sau thì không biết P19 có sửa được xưng hô.
- Đã loại: gộp alias `Mom→Lois` (bản nháp); TMDB tuỳ chọn cuối P19d; ẩn hoặc gỡ Judge khỏi UI; thêm `speaker`/`addressee` vào output dịch ngay trong P19 (ghi vào backlog PLAN.md, làm nếu harness cho thấy còn sai).
- Giới hạn còn lại: SRT không có người nói; P19 giảm nhiễu prompt nhưng không bảo đảm model đoán đúng ai nói với ai.

## D-03 · 2026-11-27 · Đích của P19 sau khi đo mốc: prompt gọn, không phải điểm xưng hô

- Bối cảnh: P19-0 chấm tay 66 câu xưng hô trên ba tập thật (S02E04, S01E03, S01E05). Bản dịch đang chạy (Prod) sai 6/66; chạy lại cùng bundle sai 9/66, và chỉ 10/34 câu giống hệt nhau giữa hai lần chạy. 5/6 lỗi của Prod có rule đúng trong Glossary. Prompt mốc: 72 đến 152 dòng Forms of address, 7/7 đến 18/23 cặp Relationship state trùng rule Glossary.
- Chọn: tiêu chí P19b/P19c là prompt (số dòng Forms of address, 0 cặp trùng, không còn `Mom`/`Dad` làm nhân vật, token) cộng điều kiện không tệ hơn mốc (≤ 9/66 sai khi chấm tay, ngoài CI). Không hứa điểm xưng hô cao hơn rõ rệt. Spec ở SPEC v0.22 (FR-5.9 đến FR-5.12, tiêu chí 25, quyết định #30).
- Vì: độ ồn của model gần bằng tỉ lệ lỗi, nên không phân biệt được cải thiện nhỏ bằng 66 câu; nhiễu và mâu thuẫn trong prompt là thứ đo được và sửa được bằng 0 lời gọi.
- Đã loại: đo bằng so chuỗi; hứa giảm số câu sai; mở `speaker`/`addressee` ngay (backlog, SPEC Non-goals) vì mốc chưa cho thấy xưng hô sai nhiều.
- Giới hạn: bộ golden không khó (mốc 91% đúng); muốn chứng minh P19 sửa xưng hô cần bộ câu mà Relationship state đè sai lên Glossary. Chưa có.

## D-04 · 2026-11-27 · Thiết kế UX P19a (Model theo tác vụ) và P19b (Aliases & Dọn trùng)

- Bối cảnh: (1) Trace homeserver cho thấy đổi `gst_model` ở nhóm `2. Translation` âm thầm xoá `gst_fallback_model` khi trùng tên và kéo `extract`/`summarize`/`suggest` chạy model đắt vì `memory_model`/`summarize_model` nằm khuất ở nhóm `5. Story and style` (gập mặc định); đồng thời dòng lời gọi chính của `EstimateSheet` và `ExtractSheet` chưa hiện tên model. (2) Trang `Characters` đang để `Aliases` dưới `Voice` (`CharacterCard.aliases`), làm rơi `GlossaryCharacter.aliases` khi lưu và lệch với `subcore.translate.scope` (chỉ đọc `glossary.characters[].aliases`); ngoài ra DB thật trên homeserver đã có sẵn 76 rule `Forms of address` chứa `Mom`/`Dad`.
- Chọn: (1) Chuyển `memory_model` và `summarize_model` lên nhóm `2. Translation`, hiện placeholder động kèm tên model kế thừa, bảng `Active models by task` (7 vai trò) và cảnh báo khi đổi `gst_model` mà còn ô phụ trống (hoặc trùng `gst_fallback_model`). (2) `EstimateSheet` đổi nhãn thành `Translate (est.)` / `Judge (est.)` kèm `<model>`, thêm `Total calls (est.)` khi có ≥ 2 nhóm lời gọi; `ExtractSheet` hiện `<extract_model>`. (3) Chuyển `Aliases` lên phần `Name` của trang `Characters` (một ô duy nhất, ghi `glossary.characters[].aliases` và đồng bộ sang `card.aliases` khi nhân vật có Voice hoặc khi accept đề xuất). (4) Chặn cứng danh xưng chung (`Mom`, `Dad`, `Sir`…) khi tạo/sửa tên và alias nhân vật, nhưng không chặn trong `from_doc` khi đọc DB và hiện cảnh báo cam trên các rule `Forms of address` cũ để dọn dần; `Forms of address` gom cặp và `Clean up duplicates` gộp rule liền kề cùng chữ qua alias.
- Vì: gom các ô chọn model về một nhóm cho phép chỉnh và lưu phân bổ quota trong một thao tác; giữ `from_doc` tương thích ngược bảo toàn bất biến "glossary đã lưu đọc lỗi sẽ dừng job" trên dữ liệu cũ của homeserver.
- Đã loại: giữ `memory_model`/`summarize_model` ở nhóm 5 kèm nút nhảy trang; để hai ô `Aliases` riêng cho `Name` và `Voice`; ném `GlossaryError` cho `Mom` ngay trong `from_doc`; mở nút extract lại hàng loạt facts `x3` trên tab `Context` (tốn hàng trăm call trong khi `clean_facts` đã lọc lúc đọc tốn 0 call).
- Giới hạn: các rule `Forms of address` cũ dính `Mom`/`Dad` hoặc xung đột chữ xưng hô qua alias vẫn cần admin xác nhận xoá/sửa tay (đúng nguyên tắc D-02 không tự quy `Mom → Lois`).

## D-06 · 2026-11-27 · Lộ trình P20 (v1.1): Gộp 3 tab thành Unified Glossary Workspace 4 góc nhìn

- Bối cảnh: Hiện trang Series tách quy tắc dịch thành 3 tab rời (`Glossary`, `Characters`, `Suggestions`) cộng dữ liệu sự kiện ở `Context`, khiến việc kiểm tra/sửa xưng hô và duyệt ~64 đề xuất lúc Bootstrap phải nhảy qua nhiều tab và cuộn dọc dài trên iPad.
- Chọn: Giữ ở **v1.1 / P20** (làm sau khi đóng P19) và **gộp hẳn 3 tab `Glossary`, `Characters`, `Suggestions` vào 1 tab `Glossary` duy nhất** (thanh tab Series còn 4 mục: `Episodes · Style · Glossary · Context`). Bên trong gồm `Shared Context Bar` (`Scope: Whole series | At SxxEyy` soi X-ray prompt P19c) và 4 góc nhìn: `1. List` (Terms + Characters), `2. Relations` (Focus Matrix theo nhân vật tâm + `Auto-mirror` đảo vế `self–other`, bỏ force-graph), `3. Timeline` (chỉ cặp đổi giai đoạn + cờ sự kiện `📍` từ `facts`), `4. Triage` (Flashcard *Characters → Address → Terms* có thanh nút ngón cái ≥ 48 px + phím tắt). Thiết kế chi tiết ở [docs/design/P20-GLOSSARY-WORKSPACE.md](design/P20-GLOSSARY-WORKSPACE.md).
- Vì: tách sang P20 giữ cho P19 đóng gọn và kiểm chứng được ngay; gộp 3 tab xoá hẳn ma sát chuyển ngữ cảnh trên iPad, còn triển khai cuốn chiếu 3 bước (`P20a` nhúng `ReviewInbox` sẵn có vào `Triage` → `P20b` Flashcard → `P20c` Timeline) giúp mỗi bước nhỏ và không mất tính năng giữa chừng.
- Đã loại: nhồi vào P19; giữ tab `Suggestions` hoặc `Characters` tách rời song song với Workspace; vẽ đồ thị mạng nhện force-graph trên màn 820 px; đặt cột "tự xưng mặc định" toàn cục cho nhân vật (trái bài học P19b).
- Giới hạn: tài liệu P20 ở trạng thái Draft v0.1, cần bước review & phản biện chéo đầu phiên kế tiếp trước khi chuyển thành FR chính thức trong `SPEC.md`.

## D-05 · 2026-11-27 · Thiết kế UX và chuẩn hoá số đếm cho Bootstrap (P19d)

- Bối cảnh: Thử nghiệm *Malcolm in the Middle* (`UX-FEEDBACK-03`) lộ 3 lỗi: (1) hộp vàng báo `121 more wait` trong khi nút xanh báo `99 missing` vì `catalog_view.py` đếm cả 22 tập chưa có phụ đề nguồn (và `bootstrap.py` chọn 2 tập đầu chưa lọc `has_source`, có nguy cơ kẹt cứng 0 job nếu tập 1–2 thiếu nguồn); (2) khi `pending = 0` hộp vẫn hứa "until the suggestions are reviewed", còn nút `Continue` không qua Estimate/Confirm nên mở toang cả series với glossary rỗng; (3) bấm xong hộp biến mất, không có nút khoá lại hay huỷ nhanh hàng loạt job `policy` vừa xếp.
- Chọn: (1) Chuẩn hoá định nghĩa: chỉ tập có phụ đề nguồn (`has_source = true`), chưa `ignored`/`removed` mới vào `allowed` và `held_by_lang` (ghi chú phụ số tập thiếu nguồn nếu > 0). (2) Hộp vàng hiện đúng 1 trong 4 trạng thái thật (`pending > 0`, đang chạy job đầu, Story memory tắt, hoặc 0 đề xuất); đổi nhãn `Continue` thành `Auto-request remaining N…` bắt buộc qua hộp Estimate/Confirm (có cảnh báo cam khi glossary rỗng) và gọi `AutoRequest.run()` ngay khi xác nhận. (3) Khi `released = true` và chưa có glossary/card được duyệt, hiện dải `Hold again` kèm hộp tích mặc định bật huỷ các job `origin = policy` đang `queued`/`waiting` của series.
- Vì: đồng nhất tập mẫu giữa Bootstrap và `JobRequests.plan` xoá sạch độ lệch số và lỗi kẹt cứng; kích hoạt `AutoRequest.run()` ngay sau Confirm giúp người dùng thấy tức thì kết quả của hành động vừa trả giá thay vì nhảy số lặng lẽ sau 5 phút.
- Đã loại: giữ hành vi chờ chu kỳ sync kế tiếp sau khi mở khoá; chỉ đặt `released = false` mà bắt người dùng vào Activity huỷ tay từng job trong hàng trăm job `policy`.
- Giới hạn: các job `origin = policy` đã vào trạng thái `running` vẫn hoàn tất tập/batch đang dịch dở (chỉ huỷ `queued`/`waiting`, hoặc người dùng Stop riêng ở Activity).

## D-07 · 2026-11-27 · Sửa thiết kế P19d sau phản biện đối chiếu code (thay một phần D-05)

- Bối cảnh: phản biện chéo ([docs/design/REVIEW-P19-P20.md](design/REVIEW-P19-P20.md)) đối chiếu D-05 với code: (1) `jobs/enqueue.py` (luật D4) không cho policy xin lại tập có job kết thúc `failed` **hoặc `cancelled`**, nên `Hold again` huỷ job policy làm các tập đó không bao giờ được tự xin lại, và job của 2 tập bootstrap cũng bị huỷ thì series kẹt 0 job; (2) `policy_jobs_since` đếm cả job đã huỷ vào trần ngày; (3) `AutoRequest.run()` dùng khoá `acquire(blocking=False)` và quét mọi series, nên gọi trong request HTTP có thể trả 0 job im lặng hoặc chạy lâu; (4) `is_locked` trả `False` khi đã có đề xuất được quyết định và 0 `pending`, nên `Hold again` ở trạng thái đó không có tác dụng.
- Chọn: job rút bởi `Hold again` kết thúc `cancelled` với lý do `bootstrap_hold`; D4 và trần ngày bỏ qua lý do này; không rút job của tập bootstrap `allowed`; `Hold again` chỉ hiện khi `relockable`; lượt policy ngay sau Confirm chỉ cho series đó, chạy nền, route trả `started`; pool bootstrap bỏ cả tập `failed`/`cancelled`.
- Vì: giữ lịch sử job ở Activity (không xoá dòng), không đổi luật D4 cho việc admin tự huỷ, và mở khoá lại cho đúng các tập đã rút.
- Đã loại: xoá hẳn dòng job (mất lịch sử, job `waiting` có thể đã có attempt); chỉ đặt `released = false` không rút job; chạy `run()` đồng bộ trong request.
- Giới hạn: cần thêm chỗ lưu lý do kết thúc (cột hoặc trường có sẵn của `jobs`), kiểm khi code.
