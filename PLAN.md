# PLAN — LLM Subtitle Translator for Bazarr

> Lộ trình triển khai [docs/design/SPEC.md](docs/design/SPEC.md) (cái gì) theo [docs/design/ARCHITECTURE.md](docs/design/ARCHITECTURE.md) (như thế nào).
> File này là **nguồn sự thật duy nhất** cho phase, phạm vi, trạng thái. Đổi yêu cầu → sửa SPEC/ARCHITECTURE; đổi thứ tự/tiến độ → sửa file này.
> Cập nhật: 2026-11-14

Trạng thái: `[x]` xong · `[~]` đang làm / chờ xác nhận · `[ ]` chưa làm.
"Tiêu chí N" = tiêu chí nghiệm thu SPEC §6.

## Tổng quan

| Phase | Nội dung | Kết quả kiểm chứng | Trạng thái |
|---|---|---|---|
| **P0** | Fork, uv workspace, CI xanh, chưa đổi hành vi | Test upstream vẫn pass | `[~]` CI + image xanh; còn kiểm tra Dependabot |
| **P0b** | Basic Auth bắt buộc cho console + API (NFR-5) | Thiếu/sai credential → 401; thiếu env → 503; `CONSOLE_AUTH=off` → mở; mật khẩu không có trong log | `[x]` CI GitHub xanh (f9b8605) |
| **P0c** | Form đăng nhập + đổi mật khẩu trong Settings cho tài khoản duy nhất; Basic Auth giữ cho script (NFR-5, tiêu chí 9) | Tiêu chí 9: chưa đăng nhập → chuyển tới form / `401`; đăng nhập, đăng xuất, đổi mật khẩu vô hiệu phiên cũ và mật khẩu env cũ; `CONSOLE_PASSWORD_RESET=1` ghi đè; sai 5 lần → khoá; không rò mật khẩu/cookie vào log, API | `[x]` |
| **P0d** | **Ưu tiên cao.** Ba phần: (1) ranh giới và port cho core worker (ADR-8, SPEC mục 9): `Store` tách thành port hẹp trả kiểu có tên, tầng use case dùng chung cho HTTP/CLI, `JobQueue`/`MediaLibrary`/`Metadata`/`ProviderFactory` thành port; (2) chế độ `hold` và duyệt job (FR-12) xây trên phần 1; (3) dọn file tự sinh (FR-13) | Tiêu chí 10 (phần 1): test import không còn ngoại lệ; test hợp đồng mỗi port chạy trên adapter thật và fake; toàn bộ test cũ pass; `schema_version`, queue JSON, `gst_enqueue.sh`, HTTP API không đổi. Tiêu chí 11 (phần 2): hold/duyệt/từ chối/khôi phục đúng, `auto` y như cũ. Tiêu chí 12 (phần 3): dọn đúng nhóm, không đụng media/job chưa xong/`held`/phiên bản `current` | `[ ]` Đã ghi ADR-8, FR-12, FR-13 (SPEC v0.8). Phần 1 xong về code và docs (9 port, tầng use case, `KNOWN_VIOLATIONS` rỗng, ADR-8 chuyển sang ARCHITECTURE v0.9); CI GitHub xanh (bac2098); phần 2 (hold) và phần 3 (dọn file) chưa làm |
| **P1** | `subcore`: srt, provider gemini + fake, translator (planner/prompt/validate/repair), glossary YAML, CLI | Tiêu chí 3, 4, 5 (qua CLI) | `[x]` CI xanh; thử Gemini thật 4 file; 50 file thật hoãn (xem dưới) |
| **P2** | Worker dùng `subcore` thay `gst`; SQLite; rate limiter | Chạy thật với Bazarr, bỏ phụ thuộc gst; tiêu chí 6, 8 | `[~]` Code + test xong (xem dưới); còn bảng `jobs`/importer và chạy thật với Bazarr |
| **P3** | Console: Library, Episode, correction, dịch lại theo dải, lịch sử/hoàn tác | Tiêu chí 7 | `[~]` Code + test xong, CI xanh (e5babb1); còn thử giao diện trên trình duyệt thật |
| **P4** | Memory: extractor, fold, scheduler prerequisite/gap/stale; tab Context | Tiêu chí 1, 2 | `[~]` Code + test xong (tiêu chí 1, 2 bằng fake provider); đã đo chất lượng trên 3 series, CI GitHub xanh (263c048); còn thử giao diện trên trình duyệt thật |
| **P5** | Đề xuất + research grounding + Review inbox; provider `openai_compat` | FR-6, FR-7, FR-9.1, FR-11.1 | `[ ]` |

**Vì sao thứ tự này:** P1 dùng được ngay bằng CLI trước khi động vào worker — cách nhanh nhất để đánh giá chất lượng dịch với glossary thật. P3 đứng trước memory vì correction + dịch lại là "lưới an toàn" chất lượng cần có trước khi tự động hoá thêm. P0b đứng đầu vì console hiện tại đã ghi được (settings, backup/restore, retry) và SPEC chốt Basic Auth "ngay từ đầu". P0d (đánh số theo nhóm hạ tầng P0x, nhưng làm **sau** P4 và **trước** P5): P5 thêm bảng `suggestions` và provider mới, nếu chưa có port thì chúng nằm tiếp vào `Store` hơn 30 method; mọi tính năng mới sau đó cũng cần đi qua tầng use case để có cả HTTP lẫn CLI. P0d không chặn việc "Chạy thật với Bazarr" của P2 (hai việc độc lập; chạy thật vẫn phải có `GST_ONLY_PATHS`/`GST_MAX_PENDING`). Chế độ `hold` (FR-12) nằm cùng P0d nhưng làm **sau** phần ranh giới: "duyệt job" là một use case phục vụ cả console lẫn CLI và chạm vòng đời hàng đợi, nên cần tầng use case và port `JobQueue` có trước. Khi `hold` xong, cách nối Bazarr an toàn nhất là `hold` kèm `GST_ONLY_PATHS`; hai rào cũ vẫn giữ.

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

## P0c — Form đăng nhập `[x]`

SPEC NFR-5, quyết định #8, tiêu chí 9; ARCHITECTURE ADR-7 "Xác thực", §4 (`auth`), §5, §6, §8. Làm sau khi P4 xong thử giao diện (không chặn P5).

- [x] **POC** (chưa commit): form `/login`, cookie phiên trong bộ nhớ, đăng xuất, `GET /api/auth/me`, khoá 5 lần/5 phút chung với Basic Auth, kiểm `Origin` — vẫn dùng tài khoản từ env, chưa có bảng `auth`, đổi mật khẩu, `CONSOLE_PASSWORD_RESET` (`gst_worker/auth.py`, `static/login.html`, `tests/test_console_login.py`).
- [x] Bảng `auth` (schema 4): một dòng, mật khẩu băm `scrypt` + salt; tạo từ `CONSOLE_USER`/`CONSOLE_PASSWORD` ở lần chạy đầu, bỏ qua env khi đã có (trừ `CONSOLE_PASSWORD_RESET=1`: ghi đè, log cảnh báo bỏ cờ). Bí mật phiên là cột `session_secret` (schema 5, thêm sau schema 4 vì bảng `auth` đã được push); token ký HMAC, sống qua restart, bí mật xoay mỗi lần ghi tài khoản (tạo, đổi mật khẩu, reset). Đăng xuất đưa id token vào danh sách chặn trong bộ nhớ (mất khi restart — chấp nhận, ghi trong CONFIGURATION).
- [ ] Middleware: cookie phiên `gst_session` (`HttpOnly`, `SameSite=Strict`, `Secure` sau HTTPS, hết hạn 7 ngày không dùng) **hoặc** Basic Auth; ngoại lệ chỉ `GET /login` + tài nguyên của nó + `POST /api/auth/login`; chưa đăng nhập: trang HTML → `/login`, `/api/*` → `401` JSON không kèm `WWW-Authenticate` (trừ Basic Auth không cookie, không xin HTML); thao tác ghi bằng cookie kiểm `Origin`/`Host`.
- [x] Khoá 5 phút sau 5 lần sai liên tiếp từ một địa chỉ (chung cho form và Basic Auth); thiếu env và chưa có tài khoản → `503` như P0b; `CONSOLE_AUTH=off` vẫn tắt được.
- [x] Endpoint `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me`, `POST /api/auth/password` (mật khẩu hiện tại + mới 8–256 ký tự; phiên cũ mất hiệu lực, người đổi được cấp phiên mới; sai mật khẩu hiện tại = `403` và tính vào khoá).
- [x] Console: trang `/login` (không CDN, dùng được trên điện thoại), nút Đăng xuất trên thanh bên, mục "Console password" trong Settings (form riêng, không qua `/api/settings`). Đã thử form `/login` bằng trình duyệt thật trên homeserver (2026-11-14): hoạt động tốt. Các trang khác (Library, Settings đổi mật khẩu, điện thoại) chưa nằm trong lần thử này.
- [x] Test (`apps/worker/tests/test_console_login.py`; `LoginServerTests` chạy lại với tài khoản trong DB ở `StoredAccountServerTests`): từng ý của tiêu chí 9, kể cả duyệt mọi route của router để chắc chắn không có đường dẫn nào ngoài danh sách ngoại lệ mở được khi chưa đăng nhập; log không chứa mật khẩu/cookie.
- [x] Docs khi làm xong: `docs/API.md`, `docs/CONFIGURATION.md` (`CONSOLE_PASSWORD_RESET`, đổi mật khẩu), `AGENTS.md` (invariant xác thực không còn "chỉ từ env"), `README.md`, `.env.example`, `docker-compose.yml`, `CHANGELOG.md`.
- [x] Workflow **CI** trên GitHub xanh cho commit P0c (f3c914c; image build xanh, thử bằng curl trên container local: đăng nhập, đổi mật khẩu, phiên sống qua restart, reset, khoá). Đã deploy thử trên homeserver (sha-f3c914c) và đăng nhập bằng form thành công; container đã dừng sau khi thử (2026-11-14).

## P0d — Ranh giới và port cho core worker; hold và duyệt job; dọn file `[ ]`

SPEC mục 9 (ADR-8), NFR-10, tiêu chí 10, quyết định #9; FR-12, tiêu chí 11, quyết định #10; FR-13, tiêu chí 12, quyết định #11. **Ưu tiên cao:** làm trước P5. Ba phần: phần 1 không đổi hành vi và làm trước; phần 2 (hold) và phần 3 (dọn file) thêm tính năng, độc lập nhau, đều sau mốc CI xanh của phần 1. Mỗi bước một commit, CI xanh sau từng bước. Danh sách port là dự kiến (SPEC mục 9), kiểm lại số chỗ gọi khi làm từng port.

### Phần 1 — Ranh giới và port (hành vi không đổi)

- [x] Ghi ADR-8, NFR-10, tiêu chí 10, quyết định #9 vào SPEC (v0.6, 2026-11-14).
- [x] **Test ranh giới trước tiên** (quét AST mã nguồn core worker): không `sqlite3`, adapter lưu trữ, HTTP client, `urllib`. Bắt đầu với danh sách ngoại lệ chứa các vi phạm hiện có (ví dụ `units.py` import `queue` và `llm`); mỗi bước dưới đây thu hẹp danh sách, kết thúc khi rỗng. Đã có `apps/worker/tests/test_core_boundary.py`: mọi module `gst_worker` là core trừ adapter (`store`, `queue`, `http`, `bazarr`, `tmdb`, `gemini`, `llm`, `backups`, `connection_tests`) và `runtime` (composition root); 9 ngoại lệ hiện có (`console`, `library` ×2, `memory`, `translation_attempt` ×2, `units` ×2, `versions`); thêm vi phạm mới hoặc để sót ngoại lệ đã hết đều làm test đỏ. `urllib.parse`/`http.cookies` (xử lý chuỗi thuần) vẫn được phép. `KNOWN_VIOLATIONS` đã rỗng. `settings` và `web` (HTTP handler) nay cũng là adapter, và `worker.py` được quét riêng: nó là composition root nên được import adapter, nhưng không được import socket, SQLite hay `http.server` trực tiếp.
- [x] Kiểu có tên (dataclass bất biến) thay `dict` cho dữ liệu đi qua port (làm cùng từng port): tài khoản, phiên bản, series, unit, output, correction, facts, summary. Tên cột chỉ còn trong adapter SQLite.
- [x] `ports.py` (Protocol, nằm ở core) và adapter SQLite tách khỏi core; `Store` là adapter cài năm port lưu trữ. **Quyết định (cần người dùng xác nhận):** không tách `Store` thành một file mỗi port: không còn người gọi nào phụ thuộc vào `Store` (chỉ `worker.py` biết nó), năm port dùng chung một kết nối và một chuỗi migration, nên việc tách chỉ là dọn dẹp; hoãn tới khi có adapter thứ hai (ghi ở ARCHITECTURE ADR-8, "Đánh đổi").
- [x] Port theo thứ tự nhỏ đến lớn (`AuthRepo` → `CatalogRepo`; bốn port cuối gộp chung một commit vì cùng sửa `ports.py`, `worker.py` và test). Chi tiết: `AuthRepo` (`gst_worker/ports.py`: `Account` bất biến, hash ẩn khỏi `repr`; `Store` cài port, trả `Account` thay `dict`; `ConsoleAuth` chỉ biết port; khuôn: port trong `ports.py`, fake + bộ test hợp đồng chạy trên cả SQLite lẫn fake ở `tests/test_auth_repo_contract.py`, test 10c quét kiểu trả về trong `ports.py`; khi một `Store` cài nhiều port, tên method mang tiền tố port). `UsageLog` (`LlmCall`, `TokenTotals`; `tests/test_usage_log_contract.py`; `TranslationAttempt` nhận `usage: UsageLog` tường minh thay vì lấy `versions.store`), `VersionRepo` (`TranslationVersion` bất biến với `missing_units` là tuple và `context_complete` là bool thay cho `missing_units_json`/số nguyên; `versions.py`, `library.py`, `units.py`, `memory.is_stale` dùng thuộc tính; `tests/test_version_repo_contract.py`; `versions.py` và `translation_attempt.py` hết import `store`, còn 7 ngoại lệ). `MemoryRepo` (`StoredFacts` mang `Facts` của `subcore` thay cho cột `json`; `facts_row` đổi tên `get_facts`; `save_facts` nhận `Facts`, hash do adapter tính; `tests/test_memory_repo_contract.py`; test 10c nay cho phép `dict[int, KiểuCóTên]` làm chỉ mục, vẫn cấm `dict`/`Any`/`Row` trần; `memory.py` còn import `store` vì `StoryMemory` còn dùng series/unit). `CatalogRepo` (`Series`, `Unit`, `Output`, `Correction` bất biến thay cho `dict`; `Store` trả kiểu có tên, `payload` của correction vẫn là dict vì là nội dung; `Library(catalog, …)`, `StoryMemory(facts, units, …)` và `UnitService(…, usage=)` nhận port hẹp; JSON của `/api/units/<id>/corrections` giữ nguyên qua `_correction_json`; `StoryMemory.snapshot` thay cho việc `units.py` đọc snapshot thẳng từ `Store`; `tests/test_catalog_repo_contract.py` chạy trên SQLite và fake; `library.py` và `memory.py` hết import `store`, còn 5 ngoại lệ trong test ranh giới: `console`→`queue`, `library`→`http`, `translation_attempt`/`units`→`llm`, `units`→`queue`). Đã xong cả năm port lưu trữ (`AuthRepo`, `UsageLog`, `VersionRepo`, `MemoryRepo`, `CatalogRepo`); `Store` vẫn cài hết, chưa tách file.
- [x] `JobQueue` bọc `queue.py`; `ProviderFactory` bọc `llm.build_provider`; `MediaLibrary` bọc `bazarr.py`; `Metadata` bọc `tmdb.py`. Core không còn import trực tiếp các module này. **Xong:** `ProviderFactory` (`ports.py`: Protocol + `MissingApiKey` thuộc core, `llm.py` chỉ re-export; `TranslationAttempt` và `UnitService` nhận `provider_factory` từ `worker.py`, không còn mặc định `build_provider`; `DEFAULT_ATTEMPT` chuyển sang `worker.py`). `MediaLibrary` (`describe` → `MediaDetails`, `series_episodes` → `EpisodeListing`; adapter `BazarrMedia` đọc settings sống nên đổi URL/key trong console có hiệu lực ngay; `enrich_job_identity(job, media)` và `StoryMemory(media=)` thay cho closure `bazarr_catalog` và lazy import `http`; `tests/test_media_library_contract.py`; `refresh`/`target_still_missing`/`wanted` chưa qua port, còn ở `bazarr.py` cho tới khi `JobQueue` và tầng use case làm). `JobQueue` (port hẹp ở `ports.py`: `enqueue`, `enqueue_retranslation`, `retry`, `cancel`, `delete`; adapter `queue.FileJobQueue` bọc các hàm `queue.py` trên cây thư mục JSON, định dạng không đổi; `ConsoleActions(jobs)` và `UnitService(…, jobs, …)` nhận port, `console.py`/`units.py` hết import `queue`; phía tiêu thụ job (claim, hoãn, quota, recover) vẫn là lớp `queue.JobQueue` của adapter, cùng tên với port nhưng khác module; `tests/test_job_queue_contract.py`). **`KNOWN_VIOLATIONS` của test ranh giới đã rỗng:** core không còn import hạ tầng. **Xong (bổ sung):** `Metadata` (`TmdbMetadata` trong `tmdb.py`, `ports.py`; `process_job` nhận `media`/`metadata` từ composition root, `tests/test_metadata_contract.py`). `MediaLibrary.refresh` và `target_still_missing` (`BazarrMedia`; `tests/test_media_library_contract.py`); `process_job` và `UnitService` không còn gọi hàm Bazarr/TMDB trực tiếp. `TranslationVersions.store` đổi thành `repo`: `library.py` và `units.py` không còn đi vòng qua thuộc tính đó để lấy `Store`. Định dạng queue JSON và `gst_enqueue.sh` không đổi.
- [x] Tầng use case: `ConsoleActions`/`UnitService` là use case (enqueue, retranslate, revert, edit cue, cancel…); việc dựng job của `/api/enqueue` và `/api/enqueue-scan` chuyển từ handler vào `ConsoleActions.enqueue_subtitle`/`enqueue_found` (có test). Handler HTTP chuyển từ `worker.py` sang `gst_worker/web.py` và bộ nạp settings sang `gst_worker/settings.py` (cả hai là adapter, `worker.py` vẫn re-export); `worker.py` chỉ còn là composition root và test ranh giới kiểm nó không import socket/SQLite/`http.server` trực tiếp. `RuntimeContext` vẫn là nơi duy nhất nối dây. **Cố ý để lại:** các route quản trị không thuộc miền (cài đặt, trạng thái, backup, log, test kết nối, scan/wanted chỉ đọc) vẫn gọi thẳng module từ handler; `process_job` và `QueueWorker` vẫn ở `worker.py` vì `process_job` dùng `skip_reason` của adapter `queue` (đưa nó vào core cần thêm một port phía tiêu thụ, để làm cùng phần 2 khi vòng đời `held`/`rejected` đổi phía tiêu thụ).
- [x] Test hợp đồng cho từng port, một bộ kiểm chạy trên adapter thật (SQLite/thư mục tạm) **và** fake trong bộ nhớ: `AuthRepo`, `UsageLog`, `VersionRepo`, `MemoryRepo`, `CatalogRepo`, `ProviderFactory`, `MediaLibrary`, `Metadata`, `JobQueue`.
- [x] Giữ nguyên: schema SQLite, định dạng queue JSON, `gst_enqueue.sh`, HTTP API, `subcore`; toàn bộ test hiện có pass (chỉ sửa chỗ khởi tạo và chỗ patch `worker.mimetypes`, nay là `gst_worker.web.mimetypes`).
- [x] Chuyển ADR-8 và sơ đồ từ SPEC mục 9 sang ARCHITECTURE (§1, §2; cả hai v0.9), SPEC mục 9 còn một con trỏ; cập nhật `docs/ARCHITECTURE.md` (mô tả code hiện tại), phần "Seams to respect" trong `AGENTS.md`, `CONTEXT.md` (Port, Adapter, Use case), `CHANGELOG.md`.
- [x] Workflow **CI** trên GitHub xanh cho phần 1 (bac2098; mốc để bắt đầu phần 2).

### Phần 2 — Chế độ `hold` và duyệt job (FR-12)

Chỉ bắt đầu sau mốc CI xanh của phần 1.

- [x] Vòng đời hàng đợi thêm `held` và `rejected` (thể hiện cụ thể do `JobQueue` quyết định, ghi lại trong ARCHITECTURE và `CONTEXT.md`); khôi phục khi khởi động worker **không** thả job `held` (FR-12.8); từ chối nhớ theo job id để script/scan không đưa lại, khôi phục được (FR-12.5).
- [x] Cài đặt `admission_mode` (`auto` mặc định | `hold`) trong settings; worker áp dụng khi nhận job từ script, job do admin tạo (Enqueue, Scan, dịch lại, sửa cue, hoàn tác) không bị giữ (FR-12.2, 12.3). `gst_enqueue.sh` và định dạng file job không đổi; kiểm bằng test chạy với bản script hiện tại.
- [x] Use case: liệt kê `held`, duyệt (job / nhiều job / series / tất cả), từ chối, khôi phục. Duyệt vẫn qua FR-9.6 và NFR-4, và không bị giới hạn bởi `GST_MAX_PENDING`; `GST_ONLY_PATHS` vẫn chặn ở lúc nhận (FR-12.6, 12.7).
- [x] HTTP API cho các use case trên (FR-12.9) + `docs/API.md`.
- [x] Console: tab **Held** (chưa thử trên trình duyệt thật; chỉ có test cấu trúc) trong Queue, nhóm theo series, chọn nhiều, badge trên thanh điều hướng, xác nhận khi duyệt hàng loạt có số job; dùng được trên điện thoại (FR-12.4, 12.5).
- [x] Test tiêu chí 11 (a–j; `apps/worker/tests/test_admission_hold.py` + hợp đồng port trong `test_job_queue_contract.py`) bằng fake provider, gồm kiểm `auto` y như cũ với bản `gst_enqueue.sh` cũ.
- [x] Docs: `docs/CONFIGURATION.md` (`admission_mode`), `docs/API.md`, `docs/BAZARR.md` (mục "Manual Wanted Workflow"), `CONTEXT.md` (Held job, vòng đời), `AGENTS.md` (held không gọi model; duyệt không bỏ qua kiểm tra thiếu ngôn ngữ), `CHANGELOG.md`; chuyển phần thiết kế FR-12 vào ARCHITECTURE.
- [x] Thử trên Bazarr thật: `hold` + `GST_ONLY_PATHS` một series, xem job `held` xuất hiện, duyệt một job và đọc bản dịch. Xong 2026-11-14 (Malcolm in the Middle S07E01, en → vi, 518 cue, `gemini-3.1-flash-lite`): script chạy trong container Bazarr, job vào `held`, duyệt, bản dịch đọc tốt và Bazarr nhận ra phụ đề `vi`. Lần thử lộ hai lỗi, đã sửa (a3c4164): file đầu ra bị chmod 0600 và lỗi timeout khi báo Bazarr quét lại làm hỏng job đã dịch xong. Duyệt trong lần thử này đi bằng hàm `approve_jobs`, chưa bấm nút trong console.
- [ ] (Tuỳ chọn, sau cùng) CLI cho worker làm adapter vào thứ hai của tầng use case: lệnh ghi (duyệt, từ chối, enqueue, dịch lại) đi qua HTTP API khi worker đang chạy (ADR-4, một writer), lệnh đọc có thể đọc trực tiếp.
- [ ] Workflow **CI** trên GitHub xanh cho commit P0d phần 2.

### Phần 3 — Dọn file (FR-13)

Chỉ bắt đầu sau mốc CI xanh của phần 1; độc lập với phần 2. Riêng quy tắc "không dọn `held` và dấu từ chối" chỉ kiểm được khi phần 2 xong (trước đó các trạng thái này chưa tồn tại).

- [ ] Use case dọn file với `dry_run`: quét `done/`, `failed/` (kèm `.error`), `backups/subtitles/`, `backups/glossary/`, file tạm mồ côi trong queue và `DATA_DIR`; trả số file và dung lượng theo nhóm (FR-13.1, 13.4).
- [ ] Phiên bản bản dịch: đánh dấu `pruned` trong lịch sử khi xoá bản sao; giữ `current`, phiên bản đầu và N mới nhất; Hoàn tác về phiên bản `pruned` bị từ chối với lý do; tab Lịch sử hiện trạng thái `pruned` (FR-13.3).
- [ ] Ranh giới an toàn: không đụng thư mục media (file tạm mồ côi cạnh media chỉ được liệt kê), job `pending`/`processing`/`deferred`/`held`, dấu từ chối, facts/summary/snapshot, SQLite (FR-13.2). Đi qua port `JobQueue` và `VersionRepo` của phần 1, không đụng file trực tiếp từ use case.
- [ ] Cài đặt `retention_done_days`, `retention_failed_days`, `retention_versions_keep`, `retention_glossary_keep` (`0` tắt nhóm); chạy mỗi ngày cùng bảo trì backup (`BackupMaintenance`) và nút **Dọn ngay** trong Settings có xem trước rồi xác nhận (FR-13.4); log tóm tắt, không log nội dung (FR-13.5).
- [ ] `model_layers` ghi rõ cửa sổ thống kê theo `retention_*` trên API và console (FR-13.6). **Lưu ý:** việc "đánh giá tầng thứ hai có đáng giữ không" ở P2 đọc `model_layers`, nên làm xong đánh giá đó (hoặc ghi lại số liệu) trước khi bật dọn `done/` với cửa sổ ngắn.
- [ ] HTTP API (`dry_run`) + `docs/API.md`.
- [ ] Test tiêu chí 12 (a–f), gồm cây thư mục media giả có phụ đề và `.tmp` mồ côi để chắc không file nào bị đụng.
- [ ] Docs: `docs/CONFIGURATION.md` (`retention_*`), `docs/API.md`, `CONTEXT.md` nếu có thuật ngữ mới, `AGENTS.md` (dọn file không bao giờ đụng media), `CHANGELOG.md`; chuyển phần thiết kế FR-13 vào ARCHITECTURE.
- [ ] Workflow **CI** trên GitHub xanh cho commit P0d phần 3 (chỉ đánh dấu `[x]` cho cả phase P0d sau bước này, khi cả ba phần xong).

## P1 — Engine `subcore` + CLI `[x]`

Tiêu chí 3, 4, 5. ADR-1, ADR-3; ARCHITECTURE §3.3.

- [x] `subcore.srt`: parse/serialize (BOM, CRLF, cue rỗng, index không liên tục — FR-1.2), validate thẻ `<i> <b> <u> <font>` (FR-3.2), ghi atomic (FR-3.4).
- [x] `subcore.providers`: `Provider` protocol, `Capabilities`, `LLMRequest/LLMResponse`, exception có kiểu (`RateLimited`, `QuotaExhausted`, `Unavailable`, `BadOutput`); adapter `gemini` (google-genai, structured output) và `fake`.
- [x] `subcore.translate`: planner chia phần tại khoảng lặng (FR-2.2), prompt builder theo thứ tự ưu tiên §3.3, validate + repair chỉ cue lỗi tối đa 2 vòng rồi chia nhỏ (FR-3.3). Timecode/index không bao giờ gửi cho LLM (FR-3.1).
- [x] Mượn từ gst (MIT) phần core rules, JSON schema `{i, text}`, `json-repair`; cập nhật `NOTICE`.
- [x] `subcore.glossary`: YAML load/merge/validate (FR-4.1–4.3), term `locked`, `address_rules` có hiệu lực theo tập/timecode.
- [x] `subcore.cli`: `subcore translate file.srt --glossary g.yaml [--target vi]`.
- [x] Test: tiêu chí 3 (50 file SRT **tổng hợp** sinh theo seed), 4 (thiếu 3 cue → đúng 1 lời gọi repair với 3 cue đó), 5 (term `locked` luôn đúng).
- [x] Chạy thử CLI với Gemini thật (2026-11-11/12, 4 file: Malcolm S01E01, King of Queens S01E01, Brooklyn Nine-Nine S01E01, Modern Family S06E19): Malcolm in the Middle S01E01 (531 cue, glossary 6 nhân vật + 5 address_rules + 1 term locked) → 1 lời gọi, 531/531 cue, timecode giữ nguyên, term locked đúng, chất lượng đọc được. Phát hiện: `gemini-2.5-flash` đã đóng với key mới (đổi mặc định sang `gemini-3.5-flash`); `gemini-3.7/3.8-flash` timeout; `gemini-3.5-flash` hay trả 503 "high demand" — CLI chưa retry (dành cho P2: backoff). Hạn mức free tier `gemini-3.5-flash` là 20 request/ngày nên không chạy được nhiều hơn.
- [x] Phát hiện model gán sai văn bản cho cue (lệch số cue) bằng anchor + tương quan độ dài, vùng lệch đi qua repair (`translate/alignment.py`, `tests/test_alignment.py`). Phát hiện khi thử Brooklyn Nine-Nine S01E01 bằng `gemini-3.1-flash-lite` (2026-11-12); trên 4 bản dịch tốt (flash + lite) không báo nhầm.
- [x] Tiêu chí 3 trên 50 file SRT: chấp nhận corpus **tổng hợp** (50 file sinh theo seed, fake provider) cộng 4 file thật ở trên. Chạy 50 file thật bị chặn bởi free tier; làm lại khi có key trả phí hoặc sau P2 (rate limiter + backoff) nếu muốn đo thêm.
- [x] Workflow **CI** trên GitHub xanh cho commit P1 (f9b8605).

## P2 — Worker dùng `subcore`, SQLite `[ ]`

Tiêu chí 6, 8. ADR-4, ADR-5; ARCHITECTURE §4, NFR-1/2/3/7.

- [x] Store SQLite `app.db` (`DATA_DIR`, mặc định `STATE_DIR`; migration theo `user_version`): bảng `translations` (version, bản sao, sha256, trigger, `context_hash`) và `llm_calls` (`gst_worker/store.py`). `translations` khoá theo `output_path` cho đến khi có `units` (P3/P4).
- [ ] Bảng `jobs` + importer `/queue/incoming/*.json` → `jobs`. **Hoãn có chủ ý:** hàng đợi vẫn là cây thư mục JSON (`pending/processing/...`) mà `gst_enqueue.sh`, `JobQueue`, console và ~40 test đang dùng; chuyển sang SQLite đụng cả script Bazarr lẫn console nên làm riêng. `units` đã có từ P3 nhưng hàng đợi vẫn là cây thư mục JSON (job dịch lại là một file trong `pending/` có `priority`); bảng `jobs` vẫn hoãn: scheduler P4 chạy prerequisite inline và gap là trạng thái `deferred` của hàng đợi thư mục, nên chưa cần.
- [x] Worker gọi `subcore` trong process thay cho subprocess `gst`; lỗi nhà cung cấp là exception có kiểu (`QuotaExhausted` → `DailyQuotaExceededError`, `RateLimited`/`Unavailable` → `ProviderUnavailableError`), không còn phân loại bằng chuỗi stdout (`translation_attempt.py`). Mất: resume từ `.partial`/`.progress` (một lần dịch lỗi chạy lại từ đầu).
- [x] Rate limiter RPM/TPM/RPD theo API key + backoff có jitter, tôn trọng `retry-after` (`subcore.providers.limiter`); hết quota ngày → queue pause như cũ (NFR-1). Nhiều key: chuyển key khi hết quota (FR-9.4). Giới hạn qua `LLM_RPM/TPM/RPD`.
- [x] Timeout 300 s mỗi lời gọi (đã có ở adapter Gemini), watchdog 30 phút mỗi job (`JOB_TIMEOUT_SECONDS`, kiểm tra trước mỗi lời gọi); idempotent theo sha256 nguồn + `context_hash` (model, temperature, prompt version, mô tả TMDB) — chạy lại không gọi LLM (NFR-2, NFR-3).
- [x] "File do hệ thống tạo" = có trong `translations` với sha256 khớp; job tự động không bao giờ ghi đè file có sẵn; file bị sửa tay hoặc file lạ → lưu thành phiên bản `external` trước khi dịch lại (FR-8.1, NFR-4).
- [x] Dịch lại ghi đè + bản sao trong `DATA_DIR/backups/subtitles/`, hoàn tác từng byte (tiêu chí 8) — mức thư viện (`versions.py`) + `trigger: retranslate` trong job. Endpoint và nút trên console thuộc P3.
- [x] Bỏ dependency `gemini-srt-translator`, bước CI `gst --help` (thay bằng `subcore --help`), `scripts/update_upstream.py`; Dependabot theo dõi `google-genai`; cập nhật `docs/ARCHITECTURE.md`.
- [x] Chỉ dịch ngôn ngữ **còn thiếu** (FR-9.6): có phụ đề cùng video ở ngôn ngữ đích dưới tên nào cũng được (`Name.vi.hi.srt`, `.ass`, ...; track forced không tính) hoặc Bazarr không còn liệt kê ngôn ngữ trong `missing_subtitles` của mục → bỏ job, không gọi model (`existing_target_subtitle`, `target_still_missing`, `skipped-not-wanted`). Script Bazarr có bản sao các kiểm tra này. Đã xác nhận trên Bazarr thật (chỉ đọc, 2026-11-12) rằng `/api/episodes` trả `missing_subtitles[].code2`; Bazarr thật đang có 929 tập thiếu `vi`.
- [x] Rào chắn để thử với Bazarr thật mà **không dịch cả thư viện** (`GST_ONLY_PATHS`, `GST_MAX_PENDING`, đọc ở cả worker lẫn `gst_enqueue.sh`; bao cả nút "enqueue scan" của console). Test: `apps/worker/tests/test_missing_only.py`.
- [ ] Chạy thật với Bazarr (khi đã có môi trường triển khai cho fork). **Điều kiện bắt buộc:** đặt `GST_ONLY_PATHS` vào đúng một thư mục thử (một series) và `GST_MAX_PENDING` ≤ 3 trong cả container Bazarr và worker; dùng key Gemini free tier; xem hàng đợi trước khi để chạy không giám sát; chỉ nới rào sau khi bản dịch đầu đọc được.
- [x] Hai tầng model (FR-9.5): mặc định `gemini-3.1-flash-lite`, model mạnh `gemini-3.5-flash` chỉ cho job khó (đầu ra vẫn sai sau repair + chia nhỏ → `HardJobError`, leo thang đúng một lần), sập provider, hết quota. Lý do thử: trong thử nghiệm Brooklyn Nine-Nine (xem P1) `3.1-flash-lite` đã gán sai văn bản cho cue mà bản `flash` thì không.
- [ ] **Đánh giá tầng thứ hai có đáng giữ không** (quyết định bỏ hay giữ FR-9.5): sau khi dịch ≥ 20 tập thật bằng key có hạn mức đủ, đọc `/api/status` → `model_layers`. Bỏ tầng (xoá model mạnh trong Settings, đổi `DEFAULT_STRONG_MODEL` thành rỗng) nếu **dưới 1 job trong 10 phải leo thang** (model nhẹ gần như không gặp job khó, tầng thừa) hoặc **model mạnh cứu được dưới một nửa số job leo thang** (đắt hơn mà không hơn). Giữ nếu ngược lại. Chưa có số liệu thật nên chưa kết luận.
- [x] Dọn các ô Settings không còn tác dụng (`gst_batch_size`, `gst_top_p`, ...) — xong ở P3.
- [ ] Workflow **CI** trên GitHub xanh cho commit P2 (chỉ đánh dấu `[x]` cho phase sau bước này).

## P3 — Console: Library, Episode, correction, dịch lại `[~]`

Tiêu chí 7. FR-10, FR-11.2, FR-11.3; ADR-7; API §5.

- [x] Trang **Library**: series → tập, trạng thái `current/stale/incomplete/untranslated`, lọc (`views/library.js`, `library.py`). `stale` hiện = luật (glossary + correction) khác lúc dịch; `incomplete` = dịch khi thiếu tập trước (`context_complete`, P4). Phim liệt kê riêng.
- [x] Trang **Episode**: bảng gốc–dịch song song, nhảy theo timecode, tìm text (FR-11.2.5); layout xếp chồng trên điện thoại (`views/episode.js`).
- [x] Correction `term/address/note/cue_edit`, scope `episode/forward/series`; `forward/series` ghi vào glossary YAML (term nay có `from`/`until`), `episode` vào bảng `corrections`; `cue_edit` là khoá và là mẫu trong prompt (FR-11.2.4). `note` chỉ scope `episode`.
- [x] Dịch lại theo cả tập / dải cue / dải thời gian, ưu tiên cao hơn job tự động (FR-11.3); `subcore.translate(only=, keep=)`.
- [x] Tab **Lịch sử**: phiên bản, diff theo cue, hoàn tác.
- [x] Trang **Glossary**: sửa YAML theo series và ngôn ngữ, kiểm tra (dry run), lưu atomic có backup bản cũ, hiện số bản dịch sẽ stale.
- [x] Endpoint API §5 (trừ `/context`, thuộc P4) + `docs/API.md`.
- [x] Test tiêu chí 7 (`apps/worker/tests/test_corrections.py`, `packages/subcore/tests/test_corrections.py`) và tiêu chí 8 qua service.
- [x] Dọn các ô Settings không còn tác dụng (12 khóa `gst_*`); đổi tên mục "Library scan" thành "Media scan".
- [ ] Thử giao diện trên trình duyệt thật (desktop + điện thoại): chưa làm được trong phiên này, chỉ có kiểm cú pháp JS, cấu trúc HTML và test HTTP.
- [x] Workflow **CI** trên GitHub xanh cho commit P3 (e5babb1: "Python 3.14" và "Build and publish image" đều thành công). Đã chạy image đó trên homeserver bằng container `--rm` (tmpfs, fake provider): auth, Library, cue edit, diff, stale theo term `forward`, glossary validate/dry-run/lưu, hoàn tác, xếp hàng dịch lại. Dịch lại chưa chạy tới cùng (không có key Gemini, cửa sổ settle 600 s).

---

## P4 — Story memory `[~]`

Tiêu chí 1, 2. FR-5; ADR-6; ARCHITECTURE §3.1, §3.2, §3.4 (các điểm lệch có chủ ý ghi ở "Đã làm ở P4" trong `docs/design/ARCHITECTURE.md` §3.4).

- [x] Extractor facts theo unit (structured output, cache theo sha256 + `prompt_version`): `subcore.memory.facts`, bảng `facts`.
- [x] Fold @N xác định; compress khi vượt budget (cache `summaries_cache`); vân tay `memory_hash` thay cho `context_hash` khi xét stale (`subcore.memory.fold`, `compress`).
- [x] Prerequisite extract inline, `gap_wait` (job `deferred`, `context-gap`), `context_complete`, `missing_units`, đánh dấu `stale` (không tự dịch lại): `gst_worker/memory.py`. `settle_window` đã có sẵn ở hàng đợi.
- [x] Season 0 neo theo cấu hình (`series.specials_after`, FR-5.7); Bazarr API liệt kê tập + source subtitle (`list_series_episodes`, FR-8.3).
- [x] Tab **Context** trên trang Episode (cái đã dùng / sẽ dùng bây giờ, bật/tắt memory theo series, neo season 0, "Read facts now"); mục **Story memory** trong Settings.
- [x] Test tiêu chí 1 và 2 bằng fake provider (`apps/worker/tests/test_story_memory.py`, `packages/subcore/tests/test_memory.py`).
- [x] Đo chất lượng trích quan hệ trên 3 series (2026-11-13, `gemini-3.1-flash-lite`, 6 tập đầu mỗi series: How I Met Your Mother S01, Modern Family S03, Brooklyn Nine-Nine S01; ~10–13k token vào / ~1k ra mỗi tập). Công cụ: `uv run subcore memory ep01.srt ep02.srt … --title X` (cần `GEMINI_API_KEY`). **Đúng:** nhân vật, giới tính, các quan hệ chính và thời điểm đổi (Ted–Robin yêu→bạn tập 2, Ted–Natalie hẹn hò→kẻ thù tập 4, Robin–Mike chia tay tập 6, Barney–Leslie họ hàng; Phil–Claire vợ chồng, Gloria–Manny mẹ–con, Jay–Manny cha dượng–con riêng, Phil–Jay rể–bố vợ; Holt–Peralta sếp–cấp dưới), timecode khớp cảnh. **Lỗi thấy được và đã sửa:** (1) vị hôn phu/thê bị gán `family` → prompt `x2`; (2) "Ted"/"Ted Mosby" thành hai người → fold gộp tên viết tắt khi không mơ hồ; (3) hai nhân vật cùng tập chung biệt danh "Podolski" (cháu và ông) bị gộp, gán nhầm quan hệ → nhân vật cùng một tập không bao giờ bị gộp với nhau; (4) `family` không phân biệt cha mẹ/anh chị em/thông gia, thứ tiếng Việt cần → sự kiện có thêm `detail` ("mother and son", "son-in-law and father-in-law"), prompt `x3`; (5) một đọc nghi ngờ (độ tin < 0.6) lật quan hệ đã rõ (≥ 0.9) → bỏ qua; (6) CLI chết khi gặp 503 → nay có backoff. **Còn lại, chấp nhận:** khung tạm thời có thể thay quan hệ bền (Peralta–Holt thành "rival, đánh cược" ở tập 6, mất "sếp–cấp dưới"; hierarchy đôi khi đổi theo một khoảnh khắc thân mật), người yêu cũ ghi `stranger`, `closeness` của cặp đang yêu hơi thấp, `age_hint` dao động giữa các tập. Vì vậy memory chỉ là gợi ý cho giọng và xưng hô; glossary và correction luôn thắng. `memory_enabled` vẫn **tắt mặc định**: chưa dịch thử tập nào có bật memory để so với bản không memory.
- [x] So sánh dịch có/không memory (2026-11-13, Modern Family S03E06 → vi, `gemini-3.1-flash-lite`, temperature 0, engine `subcore` gọi thẳng, không qua worker/Bazarr; facts của S03E01–05 + tập 6; 1 lời gọi dịch mỗi bản; prompt vào 13,1k → 15,0k token). Kết quả: **có lợi nhưng chưa hoàn hảo**. Lợi: cha nói với con gái đổi "tôi" → "bố" (cue 2, 9, 20), "con"/"mẹ" tăng (con 98→117, mẹ 11→32, "tôi" cứng 98→39); nhóm bạn Claire–Mitchell–Cam–Joanne xưng "tớ–cậu" nhất quán thay vì lẫn "mình/tôi/em" và "các người" (cue 160–164); một câu giữa bạn bè không còn bị gọi nhầm là "con" (cue 105–108). Hại: ít nhất một câu Manny nói với mẹ bị đổi sang "em" thay vì "con" (cue 61–62); một số câu Gloria/Jay vẫn xưng sai chiều, vì phụ đề không có tên người nói nên cả hai bản đều đoán sai người nói ở vài chỗ; "tớ" cho Claire (~40 tuổi) hơi trẻ. Khuyến nghị: **giữ `memory_enabled` tắt mặc định**, bật theo từng series có quan hệ cố định (Context → On) và sửa bằng correction khi sai; chưa có lý do để bật chung vì tốn thêm một lời gọi trích facts cho mỗi tập trước đó ở lần đầu.
- [x] Thử giao diện (tab Context, Settings, Library, Glossary) trên trình duyệt thật, desktop và điện thoại (2026-11-13/14, container `--rm` trên homeserver với dữ liệu giả và fake provider). Tìm ra và sửa: chữ "null" trong breadcrumb (lỗi từ P3), section Context hiện `[object HTMLDetailsElement]`, lỗi kết nối Bazarr hiện nguyên `<urlopen error …>` (nay là một câu rõ ràng); thêm log lý do từ chối đăng nhập (không chứa mật khẩu). Lưu ý: bản thử gắn code đã vá lên image `072c10a`, nên image GitHub vẫn chứa các lỗi này cho đến khi push và build lại.
- [x] Workflow **CI** trên GitHub xanh cho P4: 072c10a đỏ vì test `test_continuous_429_stays_within_configured_rpm` (làm tròn số thực ~1e-13 s ở mép cửa sổ 60 s, không phải lỗi của limiter hay P4; đã sửa test, kiểm trên 3000 seed), 263c048 xanh. "Docker image" xanh cho 072c10a.

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
