# PLAN — LLM Subtitle Translator for Bazarr

> Lộ trình triển khai [docs/design/SPEC.md](docs/design/SPEC.md) (cái gì) theo [docs/design/ARCHITECTURE.md](docs/design/ARCHITECTURE.md) (như thế nào).
> File này là **nguồn sự thật duy nhất** cho phase, phạm vi, trạng thái. Đổi yêu cầu → sửa SPEC/ARCHITECTURE; đổi thứ tự/tiến độ → sửa file này.
> Cập nhật: 2026-11-11

Trạng thái: `[x]` xong · `[~]` đang làm / chờ xác nhận · `[ ]` chưa làm.
"Tiêu chí N" = tiêu chí nghiệm thu SPEC §6.

## Tổng quan

| Phase | Nội dung | Kết quả kiểm chứng | Trạng thái |
|---|---|---|---|
| **P0** | Fork, uv workspace, CI xanh, chưa đổi hành vi | Test upstream vẫn pass | `[~]` CI + image xanh; còn kiểm tra Dependabot |
| **P0b** | Basic Auth bắt buộc cho console + API (NFR-5) | Thiếu/sai credential → 401; thiếu env → 503; `CONSOLE_AUTH=off` → mở; mật khẩu không có trong log | `[x]` CI GitHub xanh (f9b8605) |
| **P1** | `subcore`: srt, provider gemini + fake, translator (planner/prompt/validate/repair), glossary YAML, CLI | Tiêu chí 3, 4, 5 (qua CLI) | `[~]` CI xanh; còn thử thêm Gemini thật + 50 file thật |
| **P2** | Worker dùng `subcore` thay `gst`; SQLite; rate limiter | Chạy thật với Bazarr, bỏ phụ thuộc gst; tiêu chí 6, 8 | `[ ]` |
| **P3** | Console: Library, Episode, correction, dịch lại theo dải, lịch sử/hoàn tác | Tiêu chí 7 | `[ ]` |
| **P4** | Memory: extractor, fold, scheduler prerequisite/gap/stale; tab Context | Tiêu chí 1, 2 | `[ ]` |
| **P5** | Đề xuất + research grounding + Review inbox; provider `openai_compat` | FR-6, FR-7, FR-9.1, FR-11.1 | `[ ]` |

**Vì sao thứ tự này:** P1 dùng được ngay bằng CLI trước khi động vào worker — cách nhanh nhất để đánh giá chất lượng dịch với glossary thật. P3 đứng trước memory vì correction + dịch lại là "lưới an toàn" chất lượng cần có trước khi tự động hoá thêm. P0b đứng đầu vì console hiện tại đã ghi được (settings, backup/restore, retry) và SPEC chốt Basic Auth "ngay từ đầu".

**Mỗi phase xong khi:** test của tiêu chí nghiệm thu pass bằng fake provider (không gọi mạng), CI xanh trên GitHub, docs liên quan đã cập nhật (`docs/API.md`, `docs/CONFIGURATION.md`, `CONTEXT.md`, `AGENTS.md`), có mục `CHANGELOG.md`, và trạng thái ở file này được đánh dấu. Quy trình chi tiết: skill `implement-phase`.

---

## P0 — uv workspace `[~]`

- [x] Root `pyproject.toml` là virtual workspace root; members `apps/worker` (`llm-subtitle-worker`, `package = false`) và `packages/subcore` (`src/` layout, `uv_build`).
- [x] Chuyển `worker.py`, `gst_worker/`, `static/`, `tests/` vào `apps/worker/` (giữ lịch sử git).
- [x] `uv.lock` lock lại bằng uv 0.11.29, không đổi phiên bản dependency nào.
- [x] Dockerfile chỉ cài `llm-subtitle-worker`, giữ layout runtime `/app/worker.py`, `/app/gst_worker`, `/app/static`.
- [x] CI: test worker và subcore chạy riêng; compileall thêm `packages/subcore/src`; workflow image theo dõi `apps/worker/**`.
- [x] Scripts (`start-local.ps1`, `update_upstream.py`), `.dockerignore`, docs cập nhật theo layout mới.
- [x] Fork housekeeping: image mặc định `ghcr.io/tamvcspk/llm-subtitle-translator:latest`; Dependabot gán cho `tamvcspk`, múi giờ `Asia/Ho_Chi_Minh`.
- [x] Skills: `ci-check`, `implement-phase`, `upstream-sync`, `design-docs`.
- [x] Các bước CI chạy local pass (worker 125 test, subcore 1 test, compileall, `gst --help`).
- [x] Workflow **CI** trên GitHub xanh cho commit P0 (check "Python 3.14" trên f9b8605).
- [x] Workflow **Docker image** build + push thành công (check "Build and publish image" trên f9b8605).
- [ ] Dependabot chạy được với uv workspace (xem lần chạy kế tiếp).

## P0b — Basic Auth `[x]`

SPEC NFR-5, quyết định #7; ARCHITECTURE ADR-7 "Xác thực".

- [x] Middleware trong HTTP handler của worker, chạy **trước** mọi route (static + `/api/*`), không có đường dẫn ngoại lệ.
- [x] Đọc `CONSOLE_USER`, `CONSOLE_PASSWORD`, `CONSOLE_AUTH` từ env lúc khởi động; không lưu vào `config.json`, không trả qua API, không sửa qua console.
- [x] Thiếu credential (và không có `CONSOLE_AUTH=off`) → worker vẫn xử lý queue, HTTP trả `503` kèm hướng dẫn; `CONSOLE_AUTH=off` → log cảnh báo mỗi lần khởi động.
- [x] So sánh bằng `hmac.compare_digest`; header `WWW-Authenticate: Basic realm=…`; không log mật khẩu / header `Authorization`.
- [x] `docker-compose.yml`, `.env.example`, `docs/CONFIGURATION.md`, `docs/API.md`, README cập nhật.
- [x] Test: không header → 401; sai → 401; đúng → 200; thiếu env → 503; `off` → 200; log không chứa mật khẩu (`tests/test_console_auth.py`).
- [x] Workflow **CI** trên GitHub xanh cho commit P0b (f9b8605).

## P1 — Engine `subcore` + CLI `[~]`

Tiêu chí 3, 4, 5. ADR-1, ADR-3; ARCHITECTURE §3.3.

- [x] `subcore.srt`: parse/serialize (BOM, CRLF, cue rỗng, index không liên tục — FR-1.2), validate thẻ `<i> <b> <u> <font>` (FR-3.2), ghi atomic (FR-3.4).
- [x] `subcore.providers`: `Provider` protocol, `Capabilities`, `LLMRequest/LLMResponse`, exception có kiểu (`RateLimited`, `QuotaExhausted`, `Unavailable`, `BadOutput`); adapter `gemini` (google-genai, structured output) và `fake`.
- [x] `subcore.translate`: planner chia phần tại khoảng lặng (FR-2.2), prompt builder theo thứ tự ưu tiên §3.3, validate + repair chỉ cue lỗi tối đa 2 vòng rồi chia nhỏ (FR-3.3). Timecode/index không bao giờ gửi cho LLM (FR-3.1).
- [x] Mượn từ gst (MIT) phần core rules, JSON schema `{i, text}`, `json-repair`; cập nhật `NOTICE`.
- [x] `subcore.glossary`: YAML load/merge/validate (FR-4.1–4.3), term `locked`, `address_rules` có hiệu lực theo tập/timecode.
- [x] `subcore.cli`: `subcore translate file.srt --glossary g.yaml [--target vi]`.
- [x] Test: tiêu chí 3 (50 file SRT **tổng hợp** sinh theo seed), 4 (thiếu 3 cue → đúng 1 lời gọi repair với 3 cue đó), 5 (term `locked` luôn đúng).
- [~] Chạy thử CLI với Gemini thật (2026-11-11): Malcolm in the Middle S01E01 (531 cue, glossary 6 nhân vật + 5 address_rules + 1 term locked) → 1 lời gọi, 531/531 cue, timecode giữ nguyên, term locked đúng, chất lượng đọc được. Phát hiện: `gemini-2.5-flash` đã đóng với key mới (đổi mặc định sang `gemini-3.5-flash`); `gemini-3.7/3.8-flash` timeout; `gemini-3.5-flash` hay trả 503 "high demand" — CLI chưa retry (dành cho P2: backoff). Còn thiếu: thử thêm 1–2 series/ngôn ngữ khác.
- [x] Phát hiện model gán sai văn bản cho cue (lệch số cue) bằng anchor + tương quan độ dài, vùng lệch đi qua repair (`translate/alignment.py`, `tests/test_alignment.py`). Phát hiện khi thử Brooklyn Nine-Nine S01E01 bằng `gemini-3.1-flash-lite` (2026-11-12); trên 4 bản dịch tốt (flash + lite) không báo nhầm.
- [ ] Tiêu chí 3 trên 50 file SRT **thực tế** (cần chọn bộ file có thể giữ ngoài repo hoặc commit được); hiện mới có corpus tổng hợp.
- [x] Workflow **CI** trên GitHub xanh cho commit P1 (f9b8605).

## P2 — Worker dùng `subcore`, SQLite `[ ]`

Tiêu chí 6, 8. ADR-4, ADR-5; ARCHITECTURE §4, NFR-1/2/3/7.

- [ ] Store SQLite `/data/app.db`: bảng `jobs`, `translations` (version, backup, sha256, trigger), `llm_calls`; migration khởi tạo.
- [ ] Importer: `/queue/incoming/*.json` → bảng `jobs` (giữ giao thức `gst_enqueue.sh`).
- [ ] Worker gọi `subcore` trong process thay cho subprocess `gst`; bỏ phân loại lỗi bằng chuỗi stdout.
- [ ] Rate limiter RPM/TPM/RPD theo provider/model; backoff có jitter, tôn trọng `retry-after`; hết quota ngày → pause queue tới reset (NFR-1).
- [ ] Timeout ≥ 300 s mỗi lời gọi, watchdog 30 phút mỗi job (NFR-2); idempotent theo input + `context_hash` (NFR-3).
- [ ] "File do hệ thống tạo" = có trong `translations` với sha256 khớp; file bị sửa tay → backup như phiên bản ngoài (FR-8.1, NFR-4).
- [ ] Dịch lại ghi đè + backup `/data/backups/`, hoàn tác (tiêu chí 8).
- [ ] Bỏ dependency `gemini-srt-translator` và bước CI `gst --help`; cập nhật `docs/ARCHITECTURE.md` (kiến trúc hiện tại).
- [ ] Chạy thật với Bazarr (khi đã có môi trường triển khai cho fork).

## P3 — Console: Library, Episode, correction, dịch lại `[ ]`

Tiêu chí 7. FR-10, FR-11.2, FR-11.3; ADR-7; API §5.

- [ ] Trang **Library**: series → tập, trạng thái `current/stale/incomplete`, lọc.
- [ ] Trang **Episode**: bảng gốc–dịch song song, nhảy theo timecode, tìm text (FR-11.2.5); mobile-usable.
- [ ] Correction `term/address/note/cue_edit`, scope `episode/forward/series`; `forward/series` ghi vào glossary YAML, `episode` vào bảng `corrections`; `cue_edit` là khoá (FR-11.2.4).
- [ ] Dịch lại theo cả tập / dải cue / dải thời gian, ưu tiên cao hơn job tự động (FR-11.3).
- [ ] Tab **Lịch sử**: phiên bản, diff, hoàn tác.
- [ ] Trang **Glossary**: sửa theo series, lưu YAML atomic, hiện số bản dịch sẽ stale.
- [ ] Endpoint API §5 tương ứng + `docs/API.md`.

## P4 — Story memory `[ ]`

Tiêu chí 1, 2. FR-5; ADR-6; ARCHITECTURE §3.1, §3.2, §3.4.

- [ ] Extractor facts theo unit (structured output, cache theo sha256 + `prompt_version`).
- [ ] Fold @N xác định; compress khi vượt budget (cache `summaries_cache`); `context_hash`.
- [ ] Scheduler: story order, prerequisite extract, `gap_wait`, `context_complete`, `settle_window`, đánh dấu `stale` (không tự dịch lại).
- [ ] Season 0 neo theo cấu hình (FR-5.7); Bazarr API liệt kê unit + source subtitle (FR-8.3).
- [ ] Tab **Context** trên trang Episode.
- [ ] Đo chất lượng trích quan hệ trên 2–3 series quen thuộc trước khi bật mặc định (rủi ro §8).

## P5 — Đề xuất, research, Review inbox, provider thứ hai `[ ]`

FR-6, FR-7, FR-9.1, FR-11.1.

- [ ] Đề xuất `address_rules` và `terms` → bảng `suggestions` (`pending`), auto-accept theo FR-4.4.
- [ ] Research grounding một lần mỗi series; URL nguồn lấy từ grounding metadata.
- [ ] **Review inbox**: nhóm theo series, badge trên nav, chấp nhận/sửa/từ chối, chấp nhận hàng loạt, bằng chứng + số bản dịch sẽ stale.
- [ ] Provider `openai_compat` (OpenRouter, Ollama, LM Studio…); model theo tác vụ (FR-9.3); nhiều API key (FR-9.4).

---

## Sau v1 (backlog)

- **v1.1:** thông báo ntfy/Apprise/Telegram khi có mục `pending` mới hoặc job fail, kèm link trang duyệt (SPEC quyết định #6); hỗ trợ VTT.
- Ngoài v1: plugin Jellyfin/Plex "báo câu sai"; ASS/SSA giữ style; liên kết bối cảnh franchise; multi-user.
