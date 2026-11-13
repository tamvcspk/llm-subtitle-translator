# SPEC — LLM Subtitle Translator for Bazarr (tên tạm)

> Trạng thái: **Draft v0.5** · 2026-11-14
> Thay thế bản SRS ban đầu. Những chỗ khác SRS gốc được đánh dấu **[Đổi]** kèm lý do.
> v0.2: chốt các câu hỏi mở của v0.1 (mục 7), thêm FR-11 (duyệt, dịch lại, correction).
> v0.3: chốt 2 câu hỏi mở của v0.2 — thông báo để v1.1; Basic Auth bật ngay từ đầu (NFR-5).
> v0.5: NFR-5 — thêm form đăng nhập và đổi mật khẩu trong Settings cho tài khoản duy nhất, giữ Basic Auth cho script; thêm tiêu chí nghiệm thu 9 và quyết định #8. (Số v0.4 được ARCHITECTURE dùng trước đó; hai tài liệu cùng v0.5 từ đây.)

---

## 1. Mục tiêu

Tự động dịch phụ đề phim/series trong thư viện Bazarr sang ngôn ngữ đích (ưu tiên **tiếng Việt**) bằng LLM, với chất lượng gần người dịch ở 3 điểm mà máy dịch thường sai:

1. **Nhất quán thuật ngữ & tên riêng** xuyên suốt series.
2. **Xưng hô đúng quan hệ nhân vật** tại *đúng thời điểm trong cốt truyện*, kể cả khi quan hệ thay đổi giữa các tập.
3. **Không vỡ file** — timecode, số cue, thẻ định dạng giữ nguyên tuyệt đối.

**Ngôn ngữ đích** là tham số (`target_lang`, mã ISO 639-1), mặc định `vi`. v1 chỉ có bộ quy tắc riêng (xưng hô, giọng văn) cho tiếng Việt; ngôn ngữ khác vẫn chạy được với quy tắc chung nhưng không được kiểm thử chất lượng.

### Non-goals (v1)

- Không thay thế subtitle provider của Bazarr; không tự tải/trích phụ đề (Bazarr đã làm).
- Không transcribe từ audio.
- Không dịch ASS/SSA giữ style (v1 chỉ SRT; VTT ở v1.1).
- Không liên kết bối cảnh giữa các phim trong một franchise (movie là đơn vị độc lập).
- Không multi-user, không quản lý user, không phân quyền, không OAuth/SSO (homelab, mạng tin cậy) — chỉ một tài khoản, vào bằng form đăng nhập hoặc Basic Auth (NFR-5). Đổi tên đăng nhập qua console và xác thực hai bước: để sau v1 nếu cần.
- Không gửi thông báo (ntfy/Apprise/Telegram) khi có mục `pending` hoặc job fail — để **v1.1**. Console vẫn hiện badge `pending` và danh sách job lỗi (FR-11.1.1).

---

## 2. Thuật ngữ

| Thuật ngữ | Nghĩa |
|---|---|
| **Unit** | Một đơn vị nội dung: 1 tập series hoặc 1 movie. Định danh bằng `(series_key, season, episode)` hoặc `movie_key`. |
| **Story order** | Thứ tự trong cốt truyện: `(season, episode)` **đúng như Bazarr trả về** (kể cả anime — không dùng absolute numbering). Season 0 (specials) nằm **ngoài chuỗi** (xem FR-5.7). |
| **Source subtitle** | File SRT ngôn ngữ nguồn của một unit (vd `*.en.srt`). |
| **Episode facts** | Dữ kiện trích từ *source subtitle* của **một** unit: nhân vật, sự kiện quan hệ (có timecode), thuật ngữ, tóm tắt. Không phụ thuộc ngôn ngữ đích. |
| **Story state @N** | Trạng thái câu chuyện *tại đầu* unit N = gộp (fold) facts của mọi unit đứng trước N theo story order. |
| **Glossary** | Quy tắc do **người** viết (hoặc người duyệt) cho một series + ngôn ngữ đích: thuật ngữ, tên riêng, quy tắc xưng hô. Luôn thắng mọi thứ do máy suy ra. |
| **Context bundle** | Toàn bộ ngữ cảnh đưa vào prompt khi dịch unit N. Có `context_hash`. |
| **Context complete** | Mọi unit đứng trước N (có trong thư viện) đều đã có facts. |
| **Stale translation** | Bản dịch đã xuất mà `context_hash` hiện tại khác `context_hash` lúc dịch. |

---

## 3. Bối cảnh & ràng buộc

- Chạy trên homelab (Docker), cạnh Bazarr; worker và Bazarr thấy **cùng đường dẫn media**.
- LLM chính: Gemini (free tier ~15 RPM / giới hạn request theo ngày) — quota là **tài nguyên khan hiếm nhất**, mọi thiết kế phải tính số lời gọi.
- Bazarr xử lý cả season pack cùng lúc ⇒ **thứ tự job đến không theo story order** là trường hợp bình thường, không phải ngoại lệ.
- SRT **không có tên người nói** ⇒ mọi suy luận về nhân vật/quan hệ đều có sai số; con người phải sửa được.

---

## 4. Yêu cầu chức năng

### FR-1 Ingestion

- FR-1.1 Nhận job từ Bazarr custom post-processing (cơ chế thả file vào queue, kế thừa từ `gemini-srt-translator-bazarr`) và từ HTTP API (thủ công).
  **[Đổi]** SRS gốc yêu cầu webhook FastAPI. Giữ cơ chế thả file làm kênh chính vì: script trong container Bazarr không cần mạng/phụ thuộc, và job không mất khi worker đang tắt. HTTP API là kênh phụ.
- FR-1.2 Parse SRT thành danh sách cue `{index, start, end, text}`; chấp nhận BOM, CRLF, cue rỗng, index không liên tục.
- FR-1.3 Nhận diện unit: lấy `series_key` (TVDB/TMDB id), season, episode qua Bazarr API; movie qua TMDB/IMDb id.

### FR-2 Dịch nguyên file

- FR-2.1 Mặc định gửi **toàn bộ cue** của unit trong một lời gọi.
- FR-2.2 **[Đổi]** Nếu ước tính output vượt ~80% giới hạn output token của model, chia thành nhiều *phần dịch* tại ranh giới cảnh (khoảng lặng lớn nhất giữa các cue). **Mỗi phần vẫn nhận toàn bộ source của unit làm ngữ cảnh**, chỉ yêu cầu dịch một dải cue. ⇒ Giữ được ngữ cảnh toàn cục (tinh thần của SRS) mà không vướng giới hạn output.
  *Lý do:* context window lớn nhưng output bị giới hạn (vd 65k token); phim dài dịch sang tiếng Việt có thể vượt.

### FR-3 Toàn vẹn định dạng

- FR-3.1 Timecode và index **không bao giờ gửi cho LLM**. LLM nhận/trả mảng JSON `{i, text}` theo schema bắt buộc (structured output).
- FR-3.2 Validate output: đủ đúng tập `i`, không thừa, không rỗng (trừ khi nguồn rỗng), thẻ `<i> <b> <u> <font>` cân bằng và giữ nguyên số lượng, số dòng trong cue ≤ 2 (cảnh báo, không chặn).
- FR-3.3 **[Đổi]** Khi thiếu/hỏng một số cue: chỉ gửi lại **các cue lỗi** (kèm ngữ cảnh) thay vì dịch lại cả file. Tối đa 2 vòng sửa; sau đó fallback chia nhỏ (FR-2.2); sau nữa thì fail job.
  *Lý do:* SRS gốc retry cả file — tốn quota gấp đôi cho một lỗi nhỏ.
- FR-3.4 File đích được ghi atomically (ghi file tạm → rename).

### FR-4 Glossary (do người quản lý)

- FR-4.1 Mỗi series (và mỗi movie, tùy chọn) có một file glossary cho mỗi ngôn ngữ đích, định dạng YAML, sửa tay được.
- FR-4.2 Nội dung:
  - `terms`: `source → target`, có thể kèm ghi chú, cờ `locked`.
  - `characters`: tên gốc → tên dịch, giới tính, mô tả ngắn.
  - `address_rules`: quy tắc xưng hô theo **cặp nhân vật**, có thể giới hạn hiệu lực theo khoảng tập/timecode (vd `A→B: "anh–em" từ S01E06 00:32:10`).
  - `style`: giọng văn chung (trang trọng/suồng sã, giữ hay dịch honorific…).
- FR-4.3 Glossary được đưa vào system prompt ở mục **riêng**, ưu tiên cao hơn mọi ngữ cảnh do máy suy ra.
- FR-4.4 Mục do máy đề xuất (FR-6, FR-7) vào trạng thái `pending`; chỉ áp dụng khi được duyệt, trừ khi bật `auto_accept` cho loại đó. Mặc định:
  - `address_rules`: **không** tự áp dụng.
  - `terms`: tự áp dụng khi research có **≥ 2 nguồn độc lập** cho cùng bản dịch; còn lại `pending`.
  - Mục tự áp dụng vẫn hiện trong lịch sử duyệt và hoàn tác được.

### FR-5 Story memory (không phụ thuộc thứ tự dịch)

Đây là yêu cầu cốt lõi. Ví dụ bắt buộc phải đúng: *A và B là người yêu ở tập 1, thành kẻ thù ở tập 6; Bazarr giao tập 6 trước tập 1.*

- FR-5.1 **Facts trích từ source subtitle, không từ bản dịch.** Mỗi unit có facts riêng, chỉ phụ thuộc file source của chính nó (cache theo SHA-256 của file + phiên bản prompt trích xuất).
- FR-5.2 Context bundle cho unit N gồm:
  - Story state @N = fold facts của **mọi unit trước N** theo story order — **không bao giờ** dùng facts của unit sau N.
  - Facts của **chính unit N**, kèm timecode các thay đổi quan hệ trong tập (để đổi xưng hô đúng chỗ giữa tập).
  - Glossary (FR-4) và metadata (TMDB overview).
- FR-5.3 **Prerequisite:** trước khi dịch unit N, hệ thống đảm bảo facts của các unit 1..N-1 đã tồn tại; unit nào có source subtitle mà chưa có facts thì trích trước (job trích xuất, rẻ hơn job dịch nhiều).
- FR-5.4 **Gap:** nếu unit đứng trước chưa có source subtitle trong thư viện, chờ tối đa `gap_wait` (mặc định 30 phút); hết thời gian thì dịch với `context_complete = false` và ghi nhận các unit bị thiếu.
- FR-5.5 **Staleness:** khi facts của unit k thay đổi (mới trích, source đổi, prompt trích xuất đổi) hoặc glossary đổi, mọi bản dịch của unit > k có `context_hash` khác hash hiện tại bị đánh dấu `stale`.
- FR-5.6 Stale **chỉ được đánh dấu**, không bao giờ tự dịch lại. Chính sách theo series: `flag` (mặc định — hiện trên console) | `ignore`. Dịch lại luôn do người kích hoạt (FR-11).
- FR-5.7 Season 0 (specials): dùng story state tại vị trí do người cấu hình (`after: S02E05`), mặc định = state sau tập cuối cùng của season trước đó; facts của specials **không** đi vào chuỗi chính.
- FR-5.8 Story state phải gọn: giữ tóm tắt đầy đủ của K tập gần nhất (mặc định 3), các tập cũ hơn được nén thành tóm tắt arc; tổng budget mặc định 8k token. Bản nén được cache theo `(series, up_to_unit, facts_hash)`.

### FR-6 Đề xuất xưng hô & thuật ngữ (máy gợi ý, người duyệt)

- FR-6.1 Từ facts (giới tính, tuổi tương đối, vai vế, mức độ thân mật) sinh đề xuất `address_rules` cho ngôn ngữ đích → glossary `pending`.
- FR-6.2 Tên riêng/thuật ngữ xuất hiện ≥ 2 tập mà chưa có trong glossary → đề xuất `terms` `pending`.

### FR-7 Tra cứu thuật ngữ (search grounding)

- FR-7.1 **[Đổi]** Không bật search tool trong lời gọi dịch. Thay vào đó có bước **research** riêng, chạy **một lần cho mỗi series** (và khi có thuật ngữ mới), dùng provider có web search để tìm bản dịch phổ biến của tên riêng/thuật ngữ, kèm URL nguồn.
  *Lý do:* (a) gộp search vào lời gọi dịch xung đột với structured output trên một số model; (b) tra cứu mỗi tập là lặp lại và tốn quota; (c) kết quả cần được lưu và duyệt.
- FR-7.2 Kết quả vào glossary `pending` (FR-4.4).
- FR-7.3 Provider không có web search ⇒ bỏ qua bước này, không lỗi.

### FR-8 Tích hợp Bazarr

- FR-8.1 Chỉ nhận job từ provider/ngôn ngữ nguồn được cấu hình; bỏ qua nếu file đích đã tồn tại và **không phải do hệ thống tạo**.
- FR-8.2 Sau khi ghi file, yêu cầu Bazarr scan lại unit tương ứng.
- FR-8.3 Liệt kê được các unit của series kèm source subtitle hiện có (phục vụ FR-5.3).

### FR-9 Provider LLM

- FR-9.1 Gemini là provider mặc định. Phải có ít nhất một provider thứ hai tương thích OpenAI API (OpenRouter, Ollama, LM Studio…).
- FR-9.2 Mỗi provider khai báo năng lực: `max_input_tokens`, `max_output_tokens`, `structured_output`, `web_search`, giới hạn RPM/TPM/RPD.
- FR-9.3 Model được chọn **theo tác vụ**: `translate`, `extract`, `summarize`, `research` (vd extract dùng model rẻ hơn).
- FR-9.4 Hỗ trợ nhiều API key cho một provider (xoay vòng khi chạm quota).
- FR-9.5 **[Đổi]** Hai tầng model cho tác vụ `translate`: model **mặc định** nhẹ (`gemini-3.1-flash-lite`) dịch mọi job; model **mạnh** (`gemini-3.5-flash`, tuỳ chọn) chỉ nhận job mà model mặc định không làm được — đầu ra vẫn sai sau repair và chia nhỏ, provider sập quá các lần thử lại, hoặc hết quota ngày. Mỗi job leo thang tối đa một lần. Tầng thứ hai chỉ được giữ nếu số liệu (`model_layers`) cho thấy nó cứu được đáng kể số job; nếu không thì bỏ (xoá model mạnh trong Settings).
- FR-9.6 Chỉ dịch ngôn ngữ còn **thiếu**: bỏ job (không gọi model) khi đã có phụ đề cùng video ở ngôn ngữ đích dưới bất kỳ tên nào, hoặc khi Bazarr không còn liệt kê ngôn ngữ đó trong `missing_subtitles` của mục.

### FR-10 Console & API

- FR-10.1 Kế thừa console hiện có (queue, logs, settings, backup).
- FR-10.2 Bổ sung: xem/sửa glossary, duyệt mục `pending`, xem story state @N của một unit, danh sách bản dịch `stale` / `context_complete=false`, nút dịch lại (chi tiết ở FR-11).
- FR-10.3 Mọi thao tác của console đều có HTTP API tương ứng (để gọi từ script, Home Assistant…).

### FR-11 Duyệt, dịch lại và correction

Người duyệt là **admin duy nhất** (chủ homelab). Mọi thao tác nằm trong **console sẵn có của worker** — không làm UI riêng (lý do ở ARCHITECTURE ADR-7). Console phải dùng được trên điện thoại, vì tình huống thường gặp là đang xem phim thì thấy câu sai.

**11.1 Hộp duyệt (Review inbox)**
- FR-11.1.1 Một trang liệt kê mọi mục `pending` (FR-4.4), nhóm theo series; badge số lượng trên thanh điều hướng.
- FR-11.1.2 Mỗi mục: Chấp nhận · Sửa rồi chấp nhận · Từ chối. Có chấp nhận hàng loạt trong một series.
- FR-11.1.3 Mỗi mục hiển thị bằng chứng: câu thoại gốc (cue + timecode) hoặc URL nguồn research, và **số bản dịch sẽ thành stale** nếu chấp nhận.
- FR-11.1.4 Chấp nhận = ghi vào glossary YAML (FR-4). Từ chối = ghi nhớ để không đề xuất lại cùng nội dung.

**11.2 Correction**
- FR-11.2.1 Loại correction:

  | Loại | Ví dụ | Có gọi LLM? |
  |---|---|---|
  | `term` | "Kyber crystal" → "tinh thể Kyber" | Khi dịch lại |
  | `address` | Alice→Bob: "em–anh" (từ cue 412 / 00:32:10) | Khi dịch lại |
  | `note` | "Bob đang nói mỉa, giữ giọng châm chọc" | Khi dịch lại |
  | `cue_edit` | Sửa trực tiếp text của một/vài cue | **Không** — áp dụng ngay |

- FR-11.2.2 Phạm vi (scope): `episode` (chỉ tập này) · `forward` (từ tập này trở đi) · `series` (cả series). `cue_edit` luôn là `episode`.
- FR-11.2.3 Correction phạm vi `forward`/`series` được ghi vào glossary YAML (có `from`/`until` theo tập) — glossary là nguồn sự thật duy nhất cho quy tắc cấp series. Correction phạm vi `episode` lưu trong DB, gắn với unit.
- FR-11.2.4 `cue_edit` là **khoá**: các lần dịch lại sau giữ nguyên các cue đó và đưa chúng vào prompt như bản dịch mẫu.
- FR-11.2.5 Tìm cue để sửa: theo timecode (nhập `00:23:10` như trên trình phát → nhảy tới cue gần nhất) hoặc tìm theo text gốc/dịch; hiển thị song song gốc–dịch.

**11.3 Dịch lại (retranslate)**
- FR-11.3.1 Chỉ kích hoạt bằng tay, từ trang tập (hoặc API); không có dịch lại tự động hay hàng loạt cả series.
- FR-11.3.2 Form dịch lại: danh sách correction đính kèm (thêm mới ngay trong form) + phạm vi: cả tập · dải cue · dải thời gian. Dịch theo dải vẫn gửi toàn bộ source làm ngữ cảnh (như FR-2.2) nhưng chỉ yêu cầu dịch các cue trong dải ⇒ rẻ.
- FR-11.3.3 Kết quả **được ghi đè ngay** lên file đích (Bazarr được scan lại), bản cũ lưu trong `/data/backups/` (ngoài thư mục media). Trang tập có: xem diff bản cũ–mới, **Hoàn tác** về bất kỳ phiên bản trước.
- FR-11.3.4 Job dịch lại được ưu tiên cao hơn job tự động trong queue.
- FR-11.3.5 Dịch lại dùng context bundle hiện tại (facts + glossary mới nhất) ⇒ cũng là cách xử lý bản dịch `stale`.

---

## 5. Yêu cầu phi chức năng

| ID | Yêu cầu |
|---|---|
| NFR-1 Quota | Rate limiter theo provider/model (RPM, TPM, RPD). Lỗi 429/503: exponential backoff có jitter, tôn trọng `retry-after` nếu có. Hết quota ngày: tạm dừng queue tới lúc reset, không đánh fail job. |
| NFR-2 Timeout | HTTP timeout mỗi lời gọi LLM ≥ 300 s (dùng streaming nếu provider hỗ trợ); watchdog mỗi job 30 phút. Không còn subprocess treo vô hạn. |
| NFR-3 Idempotent | Chạy lại một job cho cùng input + context_hash không tạo lời gọi LLM mới nếu đã có kết quả. Facts và bản nén được cache theo hash. |
| NFR-4 An toàn dữ liệu | Không bao giờ ghi đè file phụ đề không do hệ thống tạo. Dịch lại thì backup bản cũ. |
| NFR-5 Bảo mật | API key chỉ qua env/secret hoặc config đã che; không log, không trả qua API; lọc pattern key khỏi log/lỗi. **Xác thực bật mặc định** cho mọi đường dẫn HTTP (console tĩnh + `/api/*`); chỉ có **một tài khoản**. **[Đổi]** (v0.5, thay cho "chỉ Basic Auth, mật khẩu chỉ từ env") — hai cách vào, cùng một tài khoản: (a) **form đăng nhập** cho trình duyệt (cookie phiên); (b) **Basic Auth** cho script/Home Assistant (FR-10.3). *Lý do:* trình duyệt nhớ Basic Auth nhưng không có cách đăng xuất hay đổi mật khẩu, và mật khẩu chỉ nằm trong env thì phải sửa compose rồi khởi động lại container mỗi lần đổi. Tài khoản khởi tạo lấy từ env `CONSOLE_USER`/`CONSOLE_PASSWORD` ở lần chạy đầu; sau đó người dùng **đổi mật khẩu trong Settings** (cần nhập mật khẩu hiện tại, tối thiểu 8 ký tự) và mật khẩu đã lưu (băm `scrypt`, không lưu thô) thắng env. Quên mật khẩu: đặt `CONSOLE_PASSWORD_RESET=1` cùng `CONSOLE_USER`/`CONSOLE_PASSWORD` rồi khởi động lại để ghi đè tài khoản đã lưu (log cảnh báo bỏ cờ này đi). Chưa có tài khoản đã lưu và thiếu env ⇒ worker vẫn xử lý queue nhưng HTTP trả `503` kèm hướng dẫn (không có trang "đặt mật khẩu lần đầu" mở cho cả LAN), trừ khi tắt **tường minh** bằng `CONSOLE_AUTH=off` (log cảnh báo mỗi lần khởi động). Mật khẩu và token phiên so sánh bằng `hmac.compare_digest`; không log mật khẩu, cookie hay header `Authorization`; mật khẩu không nằm trong `config.json`, không trả qua API, không sửa qua `/api/settings`. Cookie phiên `HttpOnly`, `SameSite=Strict` (thêm `Secure` khi sau proxy HTTPS), hết hạn sau 7 ngày không dùng, mất hiệu lực khi đổi mật khẩu; thao tác ghi bằng cookie còn bị kiểm `Origin`/`Host`. Đăng nhập sai liên tiếp 5 lần từ một địa chỉ ⇒ khoá 5 phút (cả form lẫn Basic Auth). Bazarr không gọi HTTP của worker (chỉ thả file vào queue) nên auth không ảnh hưởng ingestion. Vẫn khuyến nghị chỉ expose trong LAN / sau reverse proxy có TLS (Basic Auth gửi mật khẩu dạng base64, form gửi dạng thô). |
| NFR-6 Cô lập dữ liệu | Chỉ gửi lên LLM: text phụ đề, tên phim/series, metadata TMDB, glossary. Không gửi đường dẫn file, tên máy, key khác. |
| NFR-7 Quan sát | Mỗi job ghi: số lời gọi, token vào/ra, model, thời gian, `context_hash`, `context_complete`. Console hiển thị tổng token theo ngày. |
| NFR-8 Kiểm thử | Test không gọi mạng (fake provider). Bắt buộc có test tình huống FR-5 (tập 6 trước tập 1). |
| NFR-9 Triển khai | Một Docker image multi-arch (amd64/arm64), một container; state trong một volume. |

---

## 6. Tiêu chí nghiệm thu chính

1. **Out-of-order:** series giả 6 tập (A–B người yêu ở tập 1, kẻ thù từ tập 6). Đưa tập 6 vào queue trước, rồi tập 1.
   - Prompt dịch tập 1 **không** chứa bất kỳ fact nào từ tập 2–6.
   - Prompt dịch tập 6 chứa facts tập 1–5 (đã được trích như prerequisite).
2. **Gap:** xoá source tập 3 → dịch tập 4 sau `gap_wait` có `context_complete=false`; thêm lại tập 3 → bản dịch tập 4–6 thành `stale`.
3. **Format:** 50 file SRT thực tế, 100% file đầu ra có cùng số cue, cùng timecode byte-by-byte, thẻ cân bằng.
4. **Repair:** giả lập model trả thiếu 3 cue → chỉ 1 lời gọi sửa với đúng 3 cue đó.
5. **Glossary thắng:** term `locked` luôn xuất hiện đúng trong output (kiểm bằng string match trên cue chứa term nguồn).
6. **Quota:** giả lập 429 liên tục → không fail job, không vượt RPM cấu hình; giả lập hết quota ngày → queue tạm dừng.
7. **Correction:** thêm `cue_edit` cho cue 10 rồi dịch lại cả tập → cue 10 giữ nguyên byte-by-byte; thêm `term` scope `forward` ở tập 3 → prompt tập 2 không chứa term đó, prompt tập 4 có.
8. **Hoàn tác:** dịch lại → hoàn tác → file đích trùng sha256 với bản trước khi dịch lại.
9. **Đăng nhập:** chưa đăng nhập → trình duyệt được chuyển tới form, `/api/*` trả `401`; đăng nhập đúng → cookie phiên dùng được console, đăng xuất → `401`; đổi mật khẩu trong Settings (nhập đúng mật khẩu hiện tại) → phiên cũ mất hiệu lực, mật khẩu env cũ không còn vào được, Basic Auth dùng mật khẩu mới; `CONSOLE_PASSWORD_RESET=1` ghi đè mật khẩu đã lưu bằng env; sai 5 lần liên tiếp → bị khoá; mật khẩu, cookie, header `Authorization` không có trong log và không trả qua API.

---

## 7. Quyết định đã chốt (từ câu hỏi mở v0.1)

| # | Câu hỏi | Quyết định |
|---|---|---|
| 1 | Ngôn ngữ đích ở v1 | Chỉ tiếng Việt được hỗ trợ đầy đủ; `target_lang` là tham số, mặc định `vi`. |
| 2 | Tự áp dụng đề xuất của máy | Đồng ý: tắt cho `address_rules`, bật cho `terms` có ≥ 2 nguồn khớp (FR-4.4). |
| 3 | Dịch lại hàng loạt khi đổi model/prompt | Không. Chỉ dịch lại thủ công từng tập, có thể kèm correction (FR-11). |
| 4 | Thứ tự tập anime | Dùng season/episode của Bazarr. |
| 5 | Ai duyệt, dịch lại thế nào, thêm correction thế nào, UI ở đâu | Admin duy nhất, trong console sẵn có (FR-11, ADR-7). |

| 6 | Thông báo (ntfy/Apprise/Telegram) khi có mục `pending` mới hoặc job fail | Để **v1.1**, kèm link tới trang duyệt. v1 chỉ có badge/danh sách trên console. |
| 7 | Basic Auth cho console | **Bật ngay từ đầu**, mặc định bắt buộc; chỉ tắt tường minh bằng `CONSOLE_AUTH=off` (NFR-5). Làm ở phase P0b, trước P1. Mở rộng bởi #8. |
| 8 | Form đăng nhập thay vì chỉ Basic Auth; đổi mật khẩu; quên mật khẩu; Basic Auth cho script | **Một tài khoản**, hai cách vào: form đăng nhập (cookie phiên) cho trình duyệt, Basic Auth cho script. Tài khoản khởi tạo từ env `CONSOLE_USER`/`CONSOLE_PASSWORD`; người dùng đổi mật khẩu trong Settings và mật khẩu đã lưu thắng env. Quên mật khẩu: `CONSOLE_PASSWORD_RESET=1` + env, khởi động lại. Giữ Basic Auth song song (FR-10.3). Làm ở phase P0c. |

## 8. Câu hỏi mở

Chưa có.
