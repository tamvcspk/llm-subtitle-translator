# Phản biện chéo thiết kế P19a/P19b/P19d và P20 (2026-11-27)

> Đối tượng: SPEC v0.24, ARCHITECTURE v0.24, UX.md v0.24, wireframe-v2.md v2.4, `docs/DECISIONS.md` D-04..D-06, [P20-GLOSSARY-WORKSPACE.md](P20-GLOSSARY-WORKSPACE.md) v0.1. Cách làm: đối chiếu từng hứa hẹn của thiết kế với code đang có (không chạy app, không gọi model).
> Kết quả: SPEC, ARCHITECTURE và UX.md lên v0.25 cho các lỗi P19 đã sửa được (cột "Xử lý"); quyết định có đánh đổi ghi ở D-07. Phần P20 ghi ở đây và ở mục 6 của tài liệu P20; chưa thành FR.

Mức: **Chặn** = cài đúng như thiết kế sẽ ra lỗi; **Nên** = sai lệch hoặc thiếu nhưng không hỏng dữ liệu; **Ghi chú** = đã kiểm, không cần đổi.

## P19d · Bootstrap

| # | Mức | Phát hiện (bằng chứng trong code) | Xử lý |
|---|---|---|---|
| D1 | Chặn | `Hold again` **huỷ** job `origin = policy`. Luật D4 ở `jobs/enqueue.py:80-85`: policy không bao giờ xin lại tập có job kết thúc `failed` **hoặc `cancelled`**. Huỷ = các tập đó không bao giờ được tự xin lại sau lần mở khoá tiếp theo. | Job rút kết thúc `cancelled` với lý do `bootstrap_hold`, D4 bỏ qua lý do này (D-07, SPEC FR-15.12 v0.25). |
| D2 | Chặn | `Hold again` huỷ cả job của 2 tập bootstrap (`allowed`, cũng `origin = policy`), cộng với D1 thì series kẹt 0 job. | Không rút job của tập `allowed`. |
| D3 | Chặn | Pool của `Bootstrap.state` (`services/bootstrap.py:83-88`) chỉ được thiết kế loại `failed`. Tập có job `cancelled` cũng bị D4 bỏ qua, nên nếu nó nằm trong 2 tập đầu thì vẫn kẹt (cùng loại lỗi mà D-05 muốn sửa). | Loại cả `failed` và `cancelled` (theo `last_ended_state`). |
| D4 | Nên | `AutoRequest.run()` dùng `self._running.acquire(blocking=False)` (`services/auto_request.py:101`): khi lượt sync đang chạy, lời gọi từ HTTP trả `AutoRequested()` rỗng và không báo gì. Thiết kế ghi "dưới khoá sẵn có" như thể nó chờ được. | Route trả `started: bool`; UI nói "Starts with the next library sync". |
| D5 | Nên | `run()` quét mọi series trong phạm vi policy và `plan(Selection(everything=True))` (`auto_request.py:79-122`), kể cả `iterdir` thư mục đích (P18a). Chạy trong threadpool của request làm câu trả lời chậm theo cả thư viện, không theo series. | `run(series_key=...)` cho lượt ngay sau Confirm, chạy nền sau khi trả lời. |
| D6 | Nên | `is_locked` (`bootstrap.py:37-45`) trả `False` khi đã có đề xuất được quyết định và 0 `pending`. Điều kiện hiện `Hold again` của thiết kế ("chưa có glossary/card được duyệt") đúng cả trong trạng thái này, nên bấm `Hold again` đặt `released = false` mà không giữ được tập nào. | Server trả `relockable = is_locked(released=False)`; chỉ hiện `Hold again` khi đúng. |
| D7 | Nên | `policy_jobs_since` (`adapters/store/records.py:76-80`) đếm mọi job policy tạo trong ngày, kể cả job đã huỷ: rút rồi mở lại trong cùng ngày tiêu trần `auto_request_daily_cap` hai lần. | Không đếm job có lý do `bootstrap_hold`. |
| D8 | Nên | Tiêu chí 27a "`held_by_lang[lang]` bằng đúng `askable`": `askable` (`apps/web/src/lib/episodes.ts:218`) loại tập có job mở nhưng gồm tập `failed`; `held` thì ngược lại. Hai số chỉ bằng nhau sau lượt policy đầu và khi chưa có job `failed`/`cancelled`. | Tiêu chí 27a ghi rõ điều kiện đó. |
| D9 | Ghi chú | Mở khoá tự động "khi pending trống **và** đã có quyết định/glossary" khớp `is_locked` hiện có, không phải hành vi mới. Series tắt Story memory thì không bao giờ tự mở khoá; trạng thái "Story memory tắt" của hộp vàng là đúng chỗ để nói điều này. | Không đổi. |

## P19a · Model theo tác vụ

| # | Mức | Phát hiện | Xử lý |
|---|---|---|---|
| A1 | Ghi chú | `model_for_task` (`domain/config.py:208-225`) là nguồn duy nhất; `suggest` chạy tác vụ `extract` (`jobs/read_jobs.py:107`); `research` rơi về `gst_model`, không về `memory_model`; `gst_fallback_model == gst_model` bị xoá khi chuẩn hoá (`config.py:287-288`). Bảng `Active models by task` của UX khớp. | Không đổi. |
| A2 | Ghi chú | (Câu hỏi 1 của checklist P20 §6) Chuyển `memory_model`/`summarize_model` sang nhóm 2: `Settings.test.tsx` chỉ dùng `memory_enabled` ở nhóm 5 nên không gãy. Có một chỗ cần làm khi code: nháp theo nhóm nằm ở `sessionStorage` (`gst-draft:settings:story`); một nháp cũ có `memory_model` sẽ được khôi phục vào nhóm 5. Bỏ qua khoá không còn thuộc nhóm khi đọc nháp, và thêm test cho việc này. | Làm khi code P19a. |

## P19b · Aliases

| # | Mức | Phát hiện | Xử lý |
|---|---|---|---|
| B1 | Nên | UX 6.3 ghi "Lưu một thẻ ghi vào cả hai nơi (hai yêu cầu)", trong khi code đã dùng một yêu cầu `PUT …/characters` trong một transaction (`apps/web/src/api/queries/glossary.ts:127-138`). Dòng impact là **tổng** hai `dry_run` (`apps/web/src/components/Characters.tsx:204`), nên một tập bị cả glossary lẫn card làm stale được đếm hai lần. P19b đồng bộ `aliases` sang cả hai nơi nên mỗi lần sửa alias đều bị đếm đôi. | UX v0.25: impact lấy từ `dry_run` của `PUT …/characters` (hợp tập stale). Route cần thêm `dry_run` (backend nhỏ). |
| B2 | Ghi chú | (Câu hỏi 2 của checklist) Đổi tên qua `replaces`: `aliases` có trong `to_doc` của cả glossary (`subcore/glossary.py:392`) lẫn card (`subcore/style.py:294`), nên cả hai hash luật cùng đổi trong một transaction và không lệch nhau. Không cần cơ chế riêng. | Không đổi; test đồng bộ đã có trong tiêu chí 26d. |

## P20 · Unified Glossary Workspace

| # | Mức | Phát hiện | Đề xuất |
|---|---|---|---|
| W1 | Chặn | §3 nói P20a "không đổi API". Một bản nháp chung cho View 1–3 với một nút Save cần ghi glossary và **nhiều** card cùng lúc; `PUT …/characters` chỉ nhận một card. | Thêm route lưu hàng loạt (glossary + `cards[]` + `delete_cards[]`, một transaction, `dry_run` hợp tập stale), hoặc giữ Save theo từng nhân vật như hiện tại. Chọn trước khi viết FR. |
| W2 | Chặn | Không có kiểm tra phiên bản khi lưu glossary (`docs/API.md` không có `base_version`/409). Bản nháp sống lâu cạnh Triage, auto-accept của research (`auto_accept_terms` mặc định bật) và tab khác có thể ghi glossary trong lúc nháp mở; Save sau đó ghi đè. Hộp `Save / Discard` khi sang Triage không chặn được các nguồn ghi khác. | Thêm `base_version` vào PUT glossary và characters, trả 409 khi lệch, UI cho "Reload and keep my edits". |
| W3 | Chặn | Timeline hiện `📍E06 00:32`. Facts chỉ lưu số cue (`subcore/memory/facts.py:178`); đổi ra timecode phải đọc SRT (`subcore/suggest.py:207-226`), tức đọc đĩa mọi tập khi mở Timeline, trái P18a. | `GET …/memory/events` trả số cue, UI hiện `E06 · cue 412`; timecode chỉ tính khi mở một pin (một tập). |
| W4 | Nên | `Scope: At SxxEyy` cần `guest_stars` của tập. Cache TMDB chỉ có cho tập đã gom bundle; tập chưa dịch thì route đọc nhẹ phải gọi TMDB (mạng) hoặc không biết cast. Tập chưa có facts thì `scoped_rules` giữ hết. | Route không gọi mạng: chỉ dùng cache và facts có sẵn, trả `scope_known: false` để UI nói "Scope unknown for this episode, all rules apply". Dùng chung `subcore.translate.scope`, không viết lại ở TS. |
| W5 | Nên | Trường `Solo / audience self-pronoun` là trường mới của glossary (schema, hash luật, prompt), ngoài SPEC; mâu thuẫn với lý do loại cột tự xưng mặc định ở D-06. | Bỏ khỏi P20, ghi backlog; cần FR riêng nếu làm. |
| W6 | Nên | `Auto-mirror` đảo hai vế đúng với cặp đối xứng (`em–anh` ↔ `anh–em`), sai với cặp không đối xứng có thật trong dữ liệu (`ta–cậu` / `tôi–ngài` của Spangler–Francis). | Chỉ điền gợi ý khi ô chiều kia trống, không ghi đè; hiện rõ là gợi ý. |
| W7 | Ghi chú | `MovieGlossary` không có Triage: đúng, `services/suggestions.py` không có nhánh phim. | Không đổi. |
| W8 | Ghi chú | (Câu hỏi 3 và 4 của checklist P20 §6) Link cũ: `#/series/:id/characters` → `#/series/:id/glossary?view=list&kind=characters`, `#/series/:id/suggestions` → `?view=triage`; hộp vàng Bootstrap `Review suggestions` trỏ thẳng `?view=triage`. Khi sang Triage với nháp bẩn: hỏi `Save / Discard / Stay` (không tự lưu, vì Save có thể làm N tập stale và cần thấy dòng impact). | Ghi vào FR P20. |

## Việc tiếp theo

1. Người dùng duyệt D-07 và các sửa v0.25 (P19d), cùng lựa chọn W1 và W2 cho P20.
2. Khi code P19d: test cho D1–D8 (fake Bazarr, fake provider), nhất là "rút rồi mở lại thì policy xin lại đúng các tập đã rút".
