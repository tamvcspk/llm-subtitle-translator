# SPEC — LLM Subtitle Translator for Bazarr (tên tạm)

> Trạng thái: **Draft v0.8** · 2026-11-14
> Thay thế bản SRS ban đầu. Những chỗ khác SRS gốc được đánh dấu **[Đổi]** kèm lý do.
> v0.2: chốt các câu hỏi mở của v0.1 (mục 7), thêm FR-11 (duyệt, dịch lại, correction).
> v0.3: chốt 2 câu hỏi mở của v0.2 — thông báo để v1.1; Basic Auth bật ngay từ đầu (NFR-5).
> v0.5: NFR-5 — thêm form đăng nhập và đổi mật khẩu trong Settings cho tài khoản duy nhất, giữ Basic Auth cho script; thêm tiêu chí nghiệm thu 9 và quyết định #8. (Số v0.4 được ARCHITECTURE dùng trước đó; hai tài liệu cùng v0.5 từ đây.)
> v0.6: thêm kiến trúc đích cho worker — ranh giới và port (ADR-8, mục 9), NFR-10, tiêu chí nghiệm thu 10, quyết định #9, ba thuật ngữ (Port, Adapter, Use case). Chỉ SPEC đổi: ARCHITECTURE vẫn v0.5 và chỉ nhận nội dung này khi PLAN P0d xong.
> v0.7: thêm FR-12 (chế độ nhận job `hold` và duyệt job), tiêu chí nghiệm thu 11, quyết định #10, thuật ngữ "Held job"; làm cùng phase P0d, sau phần ranh giới (ADR-8).
> v0.8: thêm FR-13 (dọn file: file job đã xong, bản sao phiên bản và glossary, file tạm mồ côi), tiêu chí nghiệm thu 12, quyết định #11; làm cùng phase P0d.

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
- Chế độ `hold` (FR-12) chỉ có một công tắc toàn cục; chọn `auto`/`hold` theo từng series để **v1.1** nếu cần.

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
| **Port** | Interface (`Protocol`) do core của worker định nghĩa cho thứ nó cần từ bên ngoài: lưu trữ, hàng đợi, Bazarr, TMDB, provider. Trả kiểu có tên, không trả `dict` (ADR-8). |
| **Adapter** | Cài đặt cụ thể của một port (SQLite, hàng đợi file, Bazarr HTTP…), hoặc điểm vào gọi vào core (HTTP handler, CLI, `gst_enqueue.sh`). |
| **Use case** | Một hành động của hệ thống ở tầng ứng dụng (enqueue, retranslate, revert, edit cue, cancel…). HTTP và CLI cùng gọi một use case, không chứa nghiệp vụ riêng. |
| **Held job** | Job do script Bazarr tạo khi `admission_mode = hold`: đã nhận, chưa gọi model, chờ admin duyệt (FR-12). |

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

### FR-12 Chế độ nhận job và duyệt job

Bazarr có thể báo hàng trăm mục thiếu phụ đề và chạy script cho từng mục; ở chế độ `auto` (hiện tại) mọi job đó được dịch ngay. FR-12 cho phép Bazarr vẫn đẩy job vào, nhưng **admin chọn job nào được dịch**. Người duyệt là admin duy nhất, thao tác trong console (cùng lý do ADR-7).

- FR-12.1 Cài đặt toàn cục `admission_mode`: `auto` (mặc định, hành vi hiện tại, không đổi) | `hold`.
- FR-12.2 Ở `hold`, **job do script Bazarr tạo** vào trạng thái `held`: worker nhận và lưu job nhưng không lấy ra chạy, không gọi model, không tốn quota. Job do **chính admin** tạo (Enqueue/Scan trên console, `/api/enqueue`, dịch lại, sửa cue, hoàn tác) **không** bị giữ, vì thao tác đó đã là sự chấp thuận.
- FR-12.3 Chế độ do **worker** áp dụng khi nhận job; script và định dạng file job không đổi (FR-1.1: script không cần mạng hay phụ thuộc, nên không đọc được cài đặt của worker).
- FR-12.4 Hộp duyệt job trên console (một tab của Queue): job `held` nhóm theo series, mỗi job hiện unit, ngôn ngữ đích, ngôn ngữ nguồn, số cue, thời điểm đến; badge số job `held` trên thanh điều hướng (như FR-11.1.1); dùng được trên điện thoại.
- FR-12.5 Thao tác: **Duyệt** (một job, nhiều job đã chọn, cả series, tất cả) → job thành `pending` như job thường · **Từ chối** → job không chạy và **không bị đưa lại** khi script hoặc scan gặp lại cùng mục (khôi phục được) · Duyệt hàng loạt hiện số job sẽ chạy và phải xác nhận.
- FR-12.6 Duyệt không bỏ qua các kiểm tra khác: khi duyệt, job vẫn qua FR-9.6 (chỉ dịch ngôn ngữ còn thiếu) và NFR-4 (không ghi đè); nếu trong lúc chờ phụ đề đích đã có thì job kết thúc `skipped`, không gọi model.
- FR-12.7 `GST_ONLY_PATHS` vẫn áp dụng cho job ở `hold` (ngoài thư mục → không được nhận). `GST_MAX_PENDING` không tính job `held`; duyệt không bị giới hạn bởi nó (thao tác của admin), nên bảo vệ ở đây là bước xác nhận của FR-12.5.
- FR-12.8 Đổi `admission_mode` **không** làm đổi job đang có: chuyển `auto` → `hold` không giữ job đang `pending`; chuyển `hold` → `auto` không thả job `held` (phải duyệt tường minh). Job `held` sống qua khởi động lại worker.
- FR-12.9 Mọi thao tác có HTTP API tương ứng (FR-10.3); các use case dùng chung cho console và CLI (ADR-8).

### FR-13 Dọn file

Worker chạy lâu thì tự tạo ngày càng nhiều file. Hiện (2026-11-14) chỉ backup cấu hình (30 ngày) và log (xoay vòng) có giới hạn; trong code không thấy cơ chế dọn cho file job đã xong, bản sao phiên bản bản dịch hay bản sao glossary.

- FR-13.1 Đối tượng và quy tắc mặc định. Chỉ dọn trong `QUEUE_DIR` và `DATA_DIR`:

  | Đối tượng | Quy tắc | Cài đặt (mặc định) |
  |---|---|---|
  | File job ở `done/` (kèm `.error`) | xoá khi quá hạn | `retention_done_days` (30) |
  | File job ở `failed/` (kèm `.error`) | xoá khi quá hạn | `retention_failed_days` (90) |
  | Bản sao phiên bản bản dịch (`backups/subtitles/`) | mỗi file đích giữ phiên bản `current`, phiên bản đầu tiên (bản gốc hoặc `external`) và N phiên bản mới nhất; xoá bản sao của phần còn lại | `retention_versions_keep` (5) |
  | Bản sao glossary (`backups/glossary/`) | mỗi series giữ N bản mới nhất | `retention_glossary_keep` (20) |
  | File tạm mồ côi do worker tạo (`.*.tmp`) trong queue và `DATA_DIR` | xoá khi cũ hơn 1 ngày | — |

  Backup cấu hình `.zip` và log giữ nguyên cơ chế hiện có. Đặt một cài đặt `retention_*` về `0` thì **tắt** nhóm đó (giữ mãi).
- FR-13.2 **Không bao giờ dọn:** file job `pending`/`processing`/`deferred`/`held`; dấu từ chối (FR-12.5); phiên bản `current`; **mọi file trong thư mục media** (NFR-4) — file tạm mồ côi cạnh media chỉ được liệt kê cảnh báo trên console, không xoá; facts, summary, snapshot (cache tốn quota, NFR-3) và nội dung SQLite.
- FR-13.3 Xoá bản sao phiên bản không xoá lịch sử: dòng phiên bản còn, đánh dấu `pruned`; Hoàn tác về phiên bản `pruned` bị từ chối với lý do rõ ràng (không có bản sao), diff không có. Hoàn tác về phiên bản còn bản sao vẫn đúng từng byte (tiêu chí 8).
- FR-13.4 Chạy tự động mỗi ngày cùng bảo trì backup, và có nút **Dọn ngay** trong Settings. Luôn có **xem trước (dry run)**: số file và dung lượng sẽ xoá theo từng nhóm; "Dọn ngay" hiện xem trước rồi mới xác nhận.
- FR-13.5 Mỗi lần dọn ghi log tóm tắt (nhóm, số file, dung lượng; không ghi nội dung). Có HTTP API (`dry_run`) tương ứng (FR-10.3); use case dùng chung cho console và CLI (ADR-8).
- FR-13.6 Thống kê `model_layers` được tính từ file job `done/`/`failed/` nên bị giới hạn bởi cửa sổ `retention_done_days`/`retention_failed_days`; console và API ghi rõ điều đó. v1 không chuyển thống kê này sang SQLite.

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
| NFR-10 Ranh giới | Core của worker (use case + domain) không import `sqlite3`, adapter lưu trữ, `urllib`/HTTP client hay module hạ tầng của hàng đợi; nó chỉ biết port và kiểu có tên (ADR-8, mục 9). Ranh giới được kiểm bằng test import (tiêu chí 10), không bằng quy ước. `subcore` vẫn không biết Bazarr, queue, HTTP (ADR-2). |

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
10. **Ranh giới (ADR-8):** (a) test import quét mã nguồn của core worker: không có `sqlite3`, adapter lưu trữ hay HTTP client nào ngoài danh sách adapter; danh sách ngoại lệ tạm thời chỉ được thu hẹp, và rỗng khi P0d xong; (b) mỗi port có test hợp đồng chạy trên adapter thật **và** trên fake trong bộ nhớ, cùng một bộ kiểm; (c) không port nào trả `dict` hay `sqlite3.Row`; (d) khi kết thúc phần ranh giới của P0d (trước FR-12): toàn bộ test hiện có vẫn pass, `schema_version` của SQLite, định dạng queue JSON, `gst_enqueue.sh` và HTTP API không đổi; FR-12 chỉ được thêm API và trạng thái queue sau mốc này.
11. **Hold và duyệt job (FR-12):** với `admission_mode = hold` và fake provider: (a) script thả job → job `held`, không có lời gọi provider nào, không có file đích; (b) duyệt một job → chỉ job đó chạy, các job `held` khác vẫn `held`; (c) duyệt theo series chỉ chạy job của series đó; (d) từ chối rồi chạy lại script/scan cho cùng mục → không có job mới; khôi phục → job quay lại `held`; (e) duyệt job mà phụ đề đích đã xuất hiện → `skipped`, không ghi đè, không gọi model; (f) đổi `hold` → `auto` không thả job `held`, đổi `auto` → `hold` không giữ job đang `pending`; (g) Enqueue/dịch lại do admin tạo không bị giữ; (h) job ngoài `GST_ONLY_PATHS` không được nhận kể cả ở `hold`; (i) khởi động lại worker giữ nguyên job `held`; (j) `auto` (mặc định) cho kết quả y như trước FR-12, kể cả khi dùng bản `gst_enqueue.sh` cũ.
12. **Dọn file (FR-13):** (a) file job `done/` quá hạn bị xoá, chưa quá hạn thì giữ; `pending`, `processing`, `deferred`, `held` và dấu từ chối không bao giờ bị xoá dù cũ đến đâu; (b) với một file đích có nhiều phiên bản, chỉ `current`, phiên bản đầu và N phiên bản mới nhất còn bản sao; Hoàn tác về phiên bản còn bản sao cho đúng sha256, về phiên bản đã dọn bị từ chối và lịch sử vẫn hiện nó là `pruned`; (c) dry run không xoá gì và số file/dung lượng khớp với lần dọn thật ngay sau đó; (d) trên một cây thư mục media giả có phụ đề và file `.tmp` mồ côi, không file nào trong media bị sửa hay xoá, file tạm mồ côi chỉ được liệt kê; (e) cài đặt `0` tắt đúng nhóm đó; (f) file tạm mồ côi cũ hơn 1 ngày trong queue và `DATA_DIR` bị dọn, file tạm mới thì không.

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
| 9 | Refactor worker sang kiến trúc cổng–adapter (hexagon): DB là phụ thuộc cụ thể hay nằm sau interface | **Nằm sau port**, có chọn lọc (ADR-8, mục 9): `Store` tách thành port hẹp trả kiểu có tên; tầng use case dùng chung cho HTTP và CLI; `subcore` giữ nguyên. Hàng đợi vẫn là cây thư mục JSON, chỉ bọc bằng port `JobQueue` (bảng `jobs` vẫn hoãn). Port trả kiểu có tên ngay từ đầu, không có giai đoạn trả `dict`. Ưu tiên cao: làm ở phase P0d, trước P5. |
| 10 | Chế độ "manual": để Bazarr đẩy job vào nhưng admin chọn job nào được dịch | **Chế độ `hold`** (FR-12): `admission_mode = auto \| hold`, toàn cục, mặc định `auto`. Job từ script Bazarr thành `held` chờ duyệt (duyệt một/nhiều/theo series/tất cả, từ chối nhớ để không đưa lại); thao tác của admin không bị giữ. Worker áp dụng chế độ, script và giao thức file không đổi. Làm cùng phase P0d, **sau** phần ranh giới vì cần tầng use case và port `JobQueue`. |
| 11 | Dọn file tự sinh (file job đã xong, bản sao phiên bản/glossary, file tạm) | **Có dọn tự động** (FR-13) theo cửa sổ giữ cấu hình được (`retention_*`), kèm xem trước và nút Dọn ngay; không bao giờ đụng thư mục media, job chưa kết thúc, job `held`/từ chối, phiên bản `current` hay cache facts. Xoá bản sao phiên bản giữ lại dòng lịch sử (`pruned`). Làm cùng phase P0d (phần 3), độc lập với `hold`. |

## 8. Câu hỏi mở

Chưa có.

---

## 9. Kiến trúc đích: ranh giới và port (ADR-8)

> Trạng thái: **đang làm** theo [PLAN.md](../../PLAN.md) P0d. Đây là kiến trúc đích của `apps/worker`; [ARCHITECTURE.md](ARCHITECTURE.md) chưa đổi vì nó mô tả cái đã làm. Khi P0d xong, ADR-8 và sơ đồ chuyển sang ARCHITECTURE (§1, §2) và mục này chỉ còn một con trỏ.

### ADR-8 · Core của worker không biết hạ tầng; DB nằm sau port

**Hiện trạng** (đọc từ code, 2026-11-14):

- `Store` (`store.py`, SQLite) là một class hơn 30 method trộn sáu mối quan tâm: tài khoản, log lời gọi LLM, phiên bản bản dịch, catalog (series/unit/output/correction), facts/summary/snapshot của memory, cache. Có khoảng 85 chỗ tham chiếu trong 6 module (`units`, `memory`, `library`, `versions`, `translation_attempt`, `auth`) cộng với test.
- Method của `Store` trả `dict[str, Any]`, nên người gọi biết tên cột. Bọc bằng một Protocol mà vẫn trả `dict` thì chưa tách được khỏi schema.
- SQLite đã đóng gói tốt: ngoài `store.py` không có `sqlite3` hay `.execute`. Đây là chỗ cắt rẻ nhất.
- Use case `units.py` import thẳng `queue.enqueue_retranslation` và `llm.build_provider` (hạ tầng).
- Có 5 cơ chế lưu trữ song song: SQLite, cây thư mục JSON của hàng đợi (kèm `provider-pause.json`), `config.json`/`targets.json`, glossary YAML, file `.srt` kèm backups.
- CLI `subcore` chỉ chạm engine; HTTP là đường duy nhất tới hàng đợi, library và dịch lại.
- `subcore` đã đúng hướng: `Provider` là port, core thuần, không biết Bazarr/queue/HTTP.

**Quyết định:** kiến trúc cổng–adapter có chọn lọc cho `apps/worker`; `subcore` giữ nguyên.

1. **Ba lớp.** Adapter vào (HTTP handler, `gst_enqueue.sh`, CLI) → **use case** + domain thuần (`rules`, `queue_policy`, story memory, quy tắc phiên bản) → **port** (`Protocol`) ← adapter ra (SQLite, hàng đợi file, Bazarr, TMDB, `subcore.providers`).
2. **Port hẹp theo nơi dùng, không có một `Store` Protocol chung** (bảng dưới). Mỗi nơi chỉ thấy phần nó cần, nên test dựng fake nhỏ.
3. **Chỉ tạo port ở ranh giới thật:** lưu trữ bền, hàng đợi, dịch vụ ngoài, provider. Filesystem (`pathlib`, ghi atomic) không bọc port; test dùng thư mục tạm.
4. **Port trả kiểu có tên** (dataclass bất biến), không trả `dict`/`sqlite3.Row`. Tên cột chỉ tồn tại trong adapter SQLite.
5. **Port nằm ở core, adapter nằm riêng**; core không import adapter. Việc nối dây chỉ ở `RuntimeContext` (composition root).
6. **Hai đường vào, một tầng use case.** HTTP và CLI gọi cùng use case; handler và CLI không chứa nghiệp vụ. CLI `subcore` giữ nguyên (adapter vào của engine). Nếu thêm CLI cho worker: khi worker đang chạy, thao tác **ghi** đi qua HTTP API, không mở SQLite trực tiếp (ADR-4: một writer); lệnh chỉ đọc có thể đọc trực tiếp.
7. **Hàng đợi vẫn là cây thư mục JSON** (giao thức `gst_enqueue.sh` không đổi), chỉ được bọc bằng port `JobQueue`. Bảng `jobs` vẫn hoãn (PLAN P2); khi làm thì chỉ thêm adapter, không sửa core.

| Port | Phụ trách | Adapter hôm nay |
|---|---|---|
| `AuthRepo` | tài khoản duy nhất, bí mật phiên | SQLite (`auth`) |
| `UsageLog` | lời gọi LLM, tổng token (NFR-7) | SQLite (`llm_calls`) |
| `VersionRepo` | phiên bản bản dịch, cache theo `context_hash` (NFR-3) | SQLite (`translations`) |
| `CatalogRepo` | series, unit, output, correction, cấu hình memory theo series | SQLite |
| `MemoryRepo` | facts, summary nén, snapshot context | SQLite |
| `JobQueue` | nhận job, claim, hoãn, thử lại, huỷ, tạm dừng theo quota | cây thư mục JSON (`queue.py`) |
| `MediaLibrary` | liệt kê tập và source subtitle, `missing_subtitles`, yêu cầu scan lại | Bazarr |
| `Metadata` | tên và mô tả phim/series | TMDB |
| `ProviderFactory` | dựng provider theo model và key | `subcore.providers` |

Danh sách này là dự kiến: khi làm, một port có thể gộp hoặc tách nếu đọc code thấy ranh giới khác.

**Ràng buộc khi làm:**

- Hành vi không đổi: schema SQLite, định dạng queue JSON, `gst_enqueue.sh` và HTTP API giữ nguyên; không có migration.
- Từng port một, mỗi port một commit, CI xanh sau từng bước; đi từ nhỏ đến lớn (`AuthRepo` trước, `CatalogRepo` sau cùng).
- Test ranh giới (tiêu chí 10a) vào **trước**, với danh sách ngoại lệ là các vi phạm hiện có; mỗi bước thu hẹp danh sách cho tới khi rỗng.
- Mỗi port có test hợp đồng chạy trên adapter thật và trên fake trong bộ nhớ (tiêu chí 10b).

**Đánh đổi chấp nhận:** nhiều file hơn và thêm một lớp gián tiếp; với một người dùng và một SQLite, phần lớn port chỉ có một adapter thật, lợi ích chính là test không cần hạ tầng và cửa để đổi storage sau này. Nếu một port nào (ví dụ `Metadata`) không bù được chi phí khi làm, được phép gộp vào port khác.

**Không làm (trong ADR-8):** chuyển hàng đợi vào SQLite; đổi schema; bọc filesystem bằng port; thêm DB khác (Postgres chỉ là cửa để ngỏ, theo ADR-4); đổi `subcore`; thêm yêu cầu chức năng trong chính phần ranh giới (FR-12 làm riêng, sau phần ranh giới, cùng phase P0d).
