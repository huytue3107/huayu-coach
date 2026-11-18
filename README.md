# 華語教練 Huayu Coach

- [Tiếng Việt](#tiếng-việt)
- [English](#english)

---

## Tiếng Việt

**Hướng dẫn sử dụng từng bước cho người vận hành, học viên, giáo viên và quản trị viên: [docs/HUONG-DAN-SU-DUNG.md](docs/HUONG-DAN-SU-DUNG.md).**

### Đây là gì

**Huayu Coach** (華語教練) là ứng dụng web dùng được trên điện thoại, giúp sinh viên quốc tế tại Đài Loan (ưu tiên người Việt) học tiếng Hoa ở mức A1–A2 mà không bị áp lực. Ý tưởng cốt lõi, lấy từ một dự án thi đấu đại học (đội 霸氣隊): **khi người học dừng lại, ứng dụng hỏi vì sao** (4 lý do: quá khó, không có thời gian, cần nghỉ, dễ bị phân tâm). Một bộ quy tắc đề xuất **tối đa ba điều chỉnh** kèm căn cứ, và **người học quyết định** (nhận một vài điều, nhận hết, hoặc không nhận). Không có gì thay đổi nếu họ chưa xác nhận.

Nói thật về cách hoạt động: **quy tắc quyết định mọi con số**. AI là tùy chọn, chỉ viết lại phần giải thích và chỉ khi người học đồng ý riêng; nó không thể thêm hay đổi điều chỉnh nào. Đừng quảng cáo "AI chẩn đoán" hay hứa kết quả thi.

Công nghệ: FastAPI, SQLAlchemy 2, SQLite (chế độ WAL), trang dựng sẵn từ máy chủ bằng Jinja2, giao diện 3 ngôn ngữ (Tiếng Việt, 繁體中文, English), có manifest PWA để thêm vào màn hình chính (chưa có chế độ ngoại tuyến).

### Chạy trên máy cá nhân

Chỉ cần cài [uv](https://docs.astral.sh/uv/). Không cần Docker, máy chủ cơ sở dữ liệu hay khóa API.

```bash
cd huayu-coach
./start.sh            # tạo .env từ .env.example nếu chưa có, cài thư viện, chuẩn bị cơ sở dữ liệu, chạy web tại http://localhost:8000
./start.sh demo       # như trên, kèm tài khoản mẫu: learner@huayu.local, teacher@huayu.local, admin@huayu.local
```

Mật khẩu của các tài khoản mẫu là `demo-password-1` (chỉ dùng khi phát triển; lệnh `demo` từ chối chạy khi `HUAYU_ENV=prod`). Các lệnh quản trị (chạy bằng `uv run`):

```bash
uv run python -m app.cli prepare                         # áp dụng migration + nạp dữ liệu tham chiếu (chạy lại an toàn)
uv run python -m app.cli create-admin --email ban@example.com   # tạo quản trị viên mới (hỏi mật khẩu hai lần); nếu email đã có tài khoản thì chỉ nâng quyền, giữ nguyên mật khẩu (thêm --reset-password để đặt mật khẩu mới)
uv run python -m app.cli delete-user --email sv@example.com   # xóa tài khoản khi sinh viên yêu cầu qua email (cùng đường xử lý với nút xóa trong ứng dụng)
uv run python -m app.cli demo                            # dữ liệu mẫu, chỉ dành cho phát triển
uv run python -m app.cli check                           # in cấu hình đang dùng (ẩn bí mật) và kiểm tra cơ sở dữ liệu
```

Muốn đổi cổng: `PORT=9000 ./start.sh`.

### Đưa lên VPS và bán cho sinh viên

Xem **[deploy/deploy.md](deploy/deploy.md)**: từng bước cho Ubuntu 24.04 với Docker Compose và Caddy (HTTPS tự động): cỡ VPS và swap, khóa SSH (thử khóa **trước** khi tắt mật khẩu), sao lưu có báo lỗi, thời gian dữ liệu thật sự tồn tại (kể cả tài khoản đã xóa trong các bản sao lưu), cập nhật và quay lui có giữ ảnh cũ, giám sát, bật thu phí và danh sách kiểm tra trước khi mời sinh viên đầu tiên. **Bộ triển khai chưa từng được chạy thử trên máy chủ thật** (máy phát triển không có Docker, Caddy, ufw hay systemd); tài liệu nêu rõ bước nào đã và chưa kiểm thử.

### Cấu trúc thư mục

| Đường dẫn | Nội dung |
|---|---|
| `app/main.py` | Tạo ứng dụng, tự gắn mọi tệp trong `app/routers/` |
| `app/routers/` | Các trang: `marketing` (trang chủ, giá, quyền riêng tư, điều khoản, manifest, robots), `auth`, `account`, `learn`, `coach`, `admin`, `billing_web`, `core` (`/healthz`, `/lang/{code}`) |
| `app/services/` | Nghiệp vụ (kế hoạch, coach, học tập, thanh toán, đồng ý, AI, email, dữ liệu mẫu) |
| `app/domain/` | Logic thuần, không đụng cơ sở dữ liệu: các hành động được phép, chẩn đoán theo quy tắc, trình độ, lịch học |
| `app/models.py` | Toàn bộ lược đồ cơ sở dữ liệu |
| `app/templates/`, `app/static/` | Giao diện (mỗi phần một thư mục mẫu và một tệp CSS) |
| `app/locales/{vi,zh-TW,en}/` | Bản dịch, mỗi ngôn ngữ cùng bộ khóa (có test kiểm tra) |
| `app/web.py`, `app/security.py`, `app/i18n.py`, `app/config.py`, `app/db.py` | Nền tảng dùng chung |
| `migrations/`, `alembic.ini` | Migration cơ sở dữ liệu (Alembic) |
| `seeds/questions.json` | 50 câu hỏi mẫu, chưa có câu nào được giáo viên duyệt |
| `tests/` | Bộ test (pytest) |
| `deploy/` | Caddyfile, sao lưu và khôi phục, unit systemd, hướng dẫn `deploy.md`, trình tạo biểu tượng PWA |
| `Dockerfile`, `compose.yml`, `.dockerignore` | Bộ triển khai Docker |
| `start.sh` | Khởi động trên máy cá nhân |

### Biến môi trường

Cấu hình đọc từ biến môi trường hoặc tệp `.env` (mẫu: `.env.example`). Mọi biến trong bảng dưới đây có tiền tố `HUAYU_` (bảng khớp với `app/config.py`); vài biến khác không có tiền tố (`DOMAIN`, `PORT`, `BACKUP_*`) được nêu ngay sau bảng.

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `HUAYU_ENV` | `dev` | `dev`, `prod` hoặc `test`. `prod` bật cookie bảo mật, HSTS và các kiểm tra khởi động |
| `HUAYU_SECRET_KEY` | (trống) | Khóa ký cookie. **Bắt buộc, từ 32 ký tự, khi `prod`**. Ở dev tự sinh và lưu trong `data/secret_key.txt` |
| `HUAYU_DATA_DIR` | `./data` | Nơi lưu cơ sở dữ liệu `huayu.db` và thư mục `outbox` |
| `HUAYU_DATABASE_URL` | (trống) | Ghi đè đường dẫn cơ sở dữ liệu (mặc định là SQLite trong thư mục dữ liệu) |
| `HUAYU_BASE_URL` | `http://localhost:8000` | Địa chỉ công khai; phải bắt đầu bằng `https://` khi `prod` |
| `HUAYU_DEFAULT_LOCALE` | `vi` | Ngôn ngữ mặc định: `vi`, `zh-TW` hoặc `en` |
| `HUAYU_TIMEZONE` | `Asia/Taipei` | Múi giờ mặc định |
| `HUAYU_TRUSTED_PROXY` | `false` | `true` khi chạy sau Caddy/nginx để đọc địa chỉ IP thật cho giới hạn tốc độ |
| `HUAYU_REGISTRATION_OPEN` | `true` | `false` đóng đăng ký |
| `HUAYU_INVITE_CODE` | (trống) | Nếu đặt, đăng ký cần mã này (đợt thử kín) |
| `HUAYU_MIN_PASSWORD_LENGTH` | `8` | Độ dài mật khẩu tối thiểu |
| `HUAYU_ALLOW_UNREVIEWED_QUESTIONS` | `true` ở dev/test, `false` ở prod | Có phục vụ câu hỏi chưa được giáo viên duyệt hay không |
| `HUAYU_BILLING_ENABLED` | `false` | `false` = mọi người dùng miễn phí (giai đoạn thử nghiệm) |
| `HUAYU_TRIAL_DAYS` | `14` | Số ngày dùng thử khi bật thu phí |
| `HUAYU_PAYMENT_INSTRUCTIONS` | (trống) | Hướng dẫn thanh toán thủ công hiện cho người học |
| `HUAYU_SUPPORT_EMAIL` | (trống) | Địa chỉ hỗ trợ; hiện ở chân trang và trang quyền riêng tư. Nếu trống, dòng liên hệ bị ẩn |
| `HUAYU_OPERATOR_NAME` | (trống) | Tên đơn vị vận hành, hiện trên trang quyền riêng tư và điều khoản. Trống thì còn ô giữ chỗ màu vàng |
| `HUAYU_OPERATOR_ADDRESS` | (trống) | Địa chỉ đơn vị vận hành (như trên) |
| `HUAYU_OPERATOR_TAX_ID` | (trống) | Mã số thuế 統一編號 (như trên) |
| `HUAYU_LLM_PROVIDER` | `none` | `none`, `openai` hoặc `anthropic`. AI chỉ viết lại lời giải thích |
| `HUAYU_LLM_MODEL` | (trống) | Tên mô hình (bắt buộc khi bật AI) |
| `HUAYU_LLM_API_KEY` | (trống) | Khóa API (bắt buộc khi bật AI) |
| `HUAYU_LLM_BASE_URL` | (trống) | Địa chỉ tương thích OpenAI khác, nếu dùng |
| `HUAYU_LLM_TIMEOUT_S` | `8.0` | Thời gian chờ AI, tính bằng giây; hết giờ thì dùng văn bản mẫu |
| `HUAYU_SMTP_HOST` | (trống) | Máy chủ SMTP. Nếu trống, thư được ghi vào `data/outbox/` và **không được gửi đi** |
| `HUAYU_SMTP_PORT` | `587` | Cổng SMTP. Phải đi đôi với `HUAYU_SMTP_SECURITY`: 587 với `starttls`, 465 với `ssl` |
| `HUAYU_SMTP_SECURITY` | `starttls` | `starttls`, `ssl` (TLS ngay từ đầu, cổng 465) hoặc `none` (chỉ cho máy chuyển thư cục bộ/mạng riêng) |
| `HUAYU_SMTP_USER` | (trống) | Tài khoản SMTP |
| `HUAYU_SMTP_PASSWORD` | (trống) | Mật khẩu SMTP |
| `HUAYU_SMTP_FROM` | (trống) | Địa chỉ người gửi |

Các biến khác: `DOMAIN` (tên miền, **không** có `https://` hay dấu `/`, chỉ dùng trong `.env` của Docker Compose), `HUAYU_ADMIN_PASSWORD` (mật khẩu cho `create-admin` không tương tác), `HUAYU_ROUTERS` (chỉ để cô lập test, đừng đặt khi chạy thật), `PORT` (cho `start.sh`), và cho `deploy/backup.sh`: `BACKUP_DIR`, `KEEP` (số **tệp** giữ lại, mặc định 14, tức 14 ngày nếu sao lưu mỗi ngày), `BACKUP_PING_URL` (tùy chọn, báo kết quả cho dịch vụ kiểu healthchecks.io), `BACKUP_MODE` (`docker` hoặc `host` cho cách cài không dùng Docker; chế độ `host` còn đọc `HUAYU_DATA_DIR` và `HUAYU_USER`). Định dạng `.env`: mỗi dòng `NAME=giá_trị`, chú thích chỉ ở dòng riêng, nháy đơn cho giá trị có `$` hoặc `#` (xem `.env.example`).

### Chạy test

```bash
.venv/bin/python -m pytest                       # toàn bộ
.venv/bin/python -m pytest tests/test_marketing.py   # chỉ trang công khai và bộ triển khai
```

Không thêm `-q` (đã có sẵn trong cấu hình). Test của trang công khai còn kiểm tra: không lộ khóa dịch thô ở cả 3 ngôn ngữ, trang quyền riêng tư nêu đủ các loại dữ liệu, biểu tượng PWA là PNG hợp lệ, README nêu đủ mọi biến môi trường.

### Mô hình bảo mật (tóm tắt)

- Mật khẩu băm bằng Argon2id. Phiên đăng nhập là cookie ký (HttpOnly, SameSite=Lax, thêm Secure ở prod, tối đa 30 ngày); tăng `session_version` sẽ đăng xuất mọi thiết bị.
- Mọi thao tác thay đổi dữ liệu dùng POST có token CSRF (cookie ký, so khớp với trường biểu mẫu). GET không bao giờ đổi trạng thái.
- Tiêu đề bảo mật: CSP `script-src 'self'` (không có script nội dòng), chống nhúng khung, `nosniff`, Referrer-Policy, HSTS ở prod; trang chứa dữ liệu cá nhân gửi `Cache-Control: no-store`. Không có trang tài liệu API công khai.
- Phân quyền theo vai trò (người học, giáo viên, quản trị); người học chỉ đọc được dòng dữ liệu của chính mình. Thao tác quản trị được ghi vào nhật ký kiểm tra.
- Giới hạn tốc độ đăng nhập và các thao tác nhạy cảm, tính theo IP, lưu trong bộ nhớ **của từng tiến trình** (chạy 2 worker nghĩa là hạn mức tính riêng cho mỗi worker, và mất khi khởi động lại).
- SQLite chế độ WAL, ghi bằng `BEGIN IMMEDIATE`; xóa tài khoản xóa theo dây chuyền mọi dữ liệu của người học (chỉ giữ các dòng nhật ký đã bị gỡ mã người học và ghi chú, kèm một dòng account_deleted, và các bản ghi thanh toán đã tách khỏi tài khoản).
- AI (nếu bật): chỉ gửi mã lý do và các dữ kiện có cấu trúc, **không bao giờ** gửi tên, email hay ghi chú tự do; cần sự đồng ý riêng; câu trả lời chứa con số không có trong dữ liệu bị loại; mọi lỗi đều quay về văn bản mẫu.
- Bản triển khai: container chạy bằng người dùng không có đặc quyền, cổng ứng dụng không công bố ra ngoài, chỉ Caddy nhận 80/443. Nhật ký truy cập của ứng dụng tắt (liên kết đặt lại mật khẩu chứa một mã dùng được trong đường dẫn), Caddy cắt yêu cầu treo (timeout đọc) và từ chối thân yêu cầu trên 3 MB, nhật ký Docker xoay vòng (tối đa 50 MB mỗi dịch vụ), có trần bộ nhớ cho container, volume dữ liệu được bảo vệ khỏi `docker compose down -v`.
- Chưa có: xác thực hai lớp, đăng nhập bằng bên thứ ba, quét lỗ hổng phụ thuộc tự động, kiểm thử xâm nhập.

### Những gì CHƯA được xây

- **Cổng thanh toán** (thẻ, chuyển khoản tự động, hóa đơn điện tử). Hiện chỉ có thanh toán thủ công: quản trị viên ghi nhận rồi gia hạn. Khi có cổng, gọi lại `services.billing.activate()` từ webhook.
- **Email nhắc học.** Chưa có thông báo đẩy hay email nhắc (và có chủ ý: sản phẩm hứa "không thúc ép"). Email hiện chỉ dùng để đặt lại mật khẩu.
- **Công cụ soạn nội dung cho giáo viên** (soạn bài, âm thanh, quản lý phiên bản). Chỉ có các màn hình duyệt/nhập câu hỏi trong khu quản trị.
- **Ứng dụng di động bản địa.** Chỉ có web, thêm được vào màn hình chính nhờ manifest. Chưa có service worker nên **không dùng được khi mất mạng**, không có thông báo đẩy.
- Chưa có: luyện nói và phát âm, trình độ trên A2, nhiều máy chủ (SQLite chạy trên một máy), CI tự động.

### Việc kinh doanh và pháp lý chủ sở hữu phải tự xử lý

Phần mềm không giải quyết được các việc dưới đây; hãy hỏi kế toán hoặc luật sư tại Đài Loan.

1. **Đăng ký kinh doanh** (công ty hoặc hành hiệu) trước khi thu tiền, cùng nghĩa vụ thuế và hóa đơn. Nếu chủ sở hữu là sinh viên hoặc người nước ngoài, kiểm tra quy định về thị thực, giấy phép làm việc và việc có thu nhập tại Đài Loan (phòng quốc tế của trường hoặc luật sư).
2. **Cổng thanh toán tại Đài Loan.** Các cổng phổ biến (ví dụ ECPay, NewebPay, TapPay) thường yêu cầu doanh nghiệp đã đăng ký và hồ sơ riêng. Chưa tích hợp cổng nào.
3. **Luật sư xem chính sách quyền riêng tư và điều khoản** (`/privacy`, `/terms`). Hai trang đang ở dạng **bản nháp** và còn các ô vàng chủ sở hữu phải điền: tên đơn vị vận hành, địa chỉ, mã số thuế (統一編號), chính sách hoàn tiền và rút lui, tòa án và cách giải quyết tranh chấp. Cần xem lại: nghĩa vụ theo Luật Bảo vệ Dữ liệu cá nhân (個人資料保護法), chuyển dữ liệu ra ngoài Đài Loan khi bật AI, thông báo khi rò rỉ dữ liệu, độ tuổi tối thiểu, luật bảo vệ người tiêu dùng cho bán hàng trực tuyến. Mỗi khi cơ sở dữ liệu có thêm loại dữ liệu mới, phải cập nhật trang quyền riêng tư (test sẽ báo).
4. **Giáo viên duyệt ngân hàng câu hỏi.** 50 câu hiện có đều chưa được duyệt (44 chờ duyệt, 6 cần sửa vì có đáp án gây tranh cãi). Ở chế độ prod, mặc định chỉ phục vụ câu đã duyệt. Cần giáo viên xem từng câu, đồng thời xác nhận nguồn gốc và bản quyền của nội dung.
5. **Quyền của đội thi gốc và nhà trường.** Ý tưởng và dữ liệu ban đầu đến từ một dự án thi đấu đại học (đội 霸氣隊). Trước khi thương mại hóa, cần thỏa thuận bằng văn bản với các thành viên và kiểm tra quy chế cuộc thi, quy định sở hữu trí tuệ của nhà trường, người hướng dẫn hoặc nhà tài trợ (nếu có).
6. **Dữ liệu thử nghiệm dùng cho nghiên cứu.** Nếu số liệu của giai đoạn thử nghiệm (ví dụ điểm đánh giá của người học) được dùng cho báo cáo, bài thi hoặc nghiên cứu, cần có sự đồng ý riêng và thông tin rõ ràng cho người học; trang quyền riêng tư hiện chỉ mô tả việc vận hành dịch vụ.
7. **Tên miền, email gửi đi** (cấu hình SPF/DKIM để thư đặt lại mật khẩu không vào thư rác), hộp thư hỗ trợ, và kiểm tra tên thương hiệu "華語教練" trước khi quảng bá rộng.

---

## English

**Step-by-step user guide for the operator, learners, teachers and admins (written in Vietnamese): [docs/HUONG-DAN-SU-DUNG.md](docs/HUONG-DAN-SU-DUNG.md).**

### What it is

**Huayu Coach** (華語教練) is a mobile-first web app that helps international students in Taiwan (Vietnamese first) learn Mandarin at A1–A2 without pressure. Its core idea comes from a university competition project (team 霸氣隊): **when a learner stops, the app asks why** (4 reasons: too hard, no time, need rest, distracted). A rule engine proposes **at most three plan adjustments** with evidence, and **the learner decides** (accept some, all or none). Nothing changes until they confirm.

Honesty matters: **the rules decide every number.** The optional AI only re-words the explanation, only with the learner's separate consent, and cannot add or change an adjustment. Do not market it as "AI diagnosis" and do not promise exam results.

Stack: FastAPI, SQLAlchemy 2, SQLite (WAL), server-rendered Jinja2 pages, interface in Vietnamese, Traditional Chinese (Taiwan) and English, a PWA manifest so it can be added to the home screen (no offline mode yet).

### Run it on your own computer

You only need [uv](https://docs.astral.sh/uv/). No Docker, no database server, no API key.

```bash
cd huayu-coach
./start.sh            # creates .env from .env.example if missing, installs dependencies, prepares the database, serves http://localhost:8000
./start.sh demo       # the same, plus sample accounts: learner@huayu.local, teacher@huayu.local, admin@huayu.local
```

The sample accounts use the password `demo-password-1` (development only; `demo` refuses to run when `HUAYU_ENV=prod`). Management commands (run them with `uv run`):

```bash
uv run python -m app.cli prepare                         # apply migrations + load reference data (safe to repeat)
uv run python -m app.cli create-admin --email you@example.com   # create an admin (asks for a password twice); an existing account is only promoted and keeps its password (add --reset-password to set a new one)
uv run python -m app.cli delete-user --email student@example.com   # erase an account on request (same code path as the in-app delete button)
uv run python -m app.cli demo                            # sample data, development only
uv run python -m app.cli check                           # print the effective configuration (secrets hidden), check the database
```

Different port: `PORT=9000 ./start.sh`.

### Put it on a VPS and sell it to students

See **[deploy/deploy.md](deploy/deploy.md)** (written in Vietnamese): step by step for Ubuntu 24.04 with Docker Compose and Caddy (automatic HTTPS): VPS size and swap, SSH keys (prove key login **before** turning passwords off), backups with failure alerts, how long data really survives (including deleted accounts inside backups), updates and rollback that keep the old image, monitoring, turning billing on, and a checklist before inviting the first students. **The deploy kit has never been run on a real server** (the development Mac has no Docker, Caddy, ufw or systemd). The guide says which steps were tested and which were not.

### Repository map

| Path | Contents |
|---|---|
| `app/main.py` | App factory; mounts every module in `app/routers/` automatically |
| `app/routers/` | Pages: `marketing` (landing, pricing, privacy, terms, manifest, robots), `auth`, `account`, `learn`, `coach`, `admin`, `billing_web`, `core` (`/healthz`, `/lang/{code}`) |
| `app/services/` | Business rules (plans, coach, learning, billing, consent, LLM, mail, seed data) |
| `app/domain/` | Pure logic with no database: allowed actions, rule-based diagnosis, levels, scheduler |
| `app/models.py` | The whole database schema |
| `app/templates/`, `app/static/` | The UI (one template folder and one CSS file per area) |
| `app/locales/{vi,zh-TW,en}/` | Translations; every language has the same keys (tests enforce it) |
| `app/web.py`, `app/security.py`, `app/i18n.py`, `app/config.py`, `app/db.py` | Shared foundation |
| `migrations/`, `alembic.ini` | Database migrations (Alembic) |
| `seeds/questions.json` | 50 sample questions, none approved by a teacher yet |
| `tests/` | Test suite (pytest) |
| `deploy/` | Caddyfile, backup and restore, systemd unit, the `deploy.md` guide, PWA icon generator |
| `Dockerfile`, `compose.yml`, `.dockerignore` | The Docker deploy kit |
| `start.sh` | Local start |

### Environment variables

Settings come from environment variables or a `.env` file (template: `.env.example`). Every variable in this table is prefixed `HUAYU_` (the table matches `app/config.py`); the few that are not (`DOMAIN`, `PORT`, `BACKUP_*`) are listed right after it.

| Variable | Default | Meaning |
|---|---|---|
| `HUAYU_ENV` | `dev` | `dev`, `prod` or `test`. `prod` turns on secure cookies, HSTS and start-up checks |
| `HUAYU_SECRET_KEY` | (empty) | Cookie signing key. **Required, at least 32 characters, in `prod`.** Generated into `data/secret_key.txt` in dev |
| `HUAYU_DATA_DIR` | `./data` | Where the `huayu.db` database and the `outbox` folder live |
| `HUAYU_DATABASE_URL` | (empty) | Override the database URL (default: SQLite in the data directory) |
| `HUAYU_BASE_URL` | `http://localhost:8000` | Public address; must start with `https://` in `prod` |
| `HUAYU_DEFAULT_LOCALE` | `vi` | Default language: `vi`, `zh-TW` or `en` |
| `HUAYU_TIMEZONE` | `Asia/Taipei` | Default time zone |
| `HUAYU_TRUSTED_PROXY` | `false` | `true` behind Caddy/nginx, so the real client IP is used for rate limiting |
| `HUAYU_REGISTRATION_OPEN` | `true` | `false` closes sign-up |
| `HUAYU_INVITE_CODE` | (empty) | If set, sign-up needs this code (closed beta) |
| `HUAYU_MIN_PASSWORD_LENGTH` | `8` | Minimum password length |
| `HUAYU_ALLOW_UNREVIEWED_QUESTIONS` | `true` in dev/test, `false` in prod | Whether questions not yet approved by a teacher are served |
| `HUAYU_BILLING_ENABLED` | `false` | `false` = everyone is free (the pilot) |
| `HUAYU_TRIAL_DAYS` | `14` | Free trial length when billing is on |
| `HUAYU_PAYMENT_INSTRUCTIONS` | (empty) | Manual payment instructions shown to learners |
| `HUAYU_SUPPORT_EMAIL` | (empty) | Support address shown in the footer and on the privacy page. Hidden when empty |
| `HUAYU_OPERATOR_NAME` | (empty) | Operator name shown on the privacy and terms pages. Empty keeps the visible yellow placeholder |
| `HUAYU_OPERATOR_ADDRESS` | (empty) | Operator address (same) |
| `HUAYU_OPERATOR_TAX_ID` | (empty) | Tax ID 統一編號 (same) |
| `HUAYU_LLM_PROVIDER` | `none` | `none`, `openai` or `anthropic`. The AI only re-words explanations |
| `HUAYU_LLM_MODEL` | (empty) | Model name (required when the AI is on) |
| `HUAYU_LLM_API_KEY` | (empty) | API key (required when the AI is on) |
| `HUAYU_LLM_BASE_URL` | (empty) | Another OpenAI-compatible endpoint, if you use one |
| `HUAYU_LLM_TIMEOUT_S` | `8.0` | AI timeout in seconds; on timeout the template text is shown |
| `HUAYU_SMTP_HOST` | (empty) | SMTP server. If empty, mail is written to `data/outbox/` and **not sent** |
| `HUAYU_SMTP_PORT` | `587` | SMTP port. Must match `HUAYU_SMTP_SECURITY`: 587 with `starttls`, 465 with `ssl` |
| `HUAYU_SMTP_SECURITY` | `starttls` | `starttls`, `ssl` (implicit TLS, port 465) or `none` (only for a relay on the same machine or a private network) |
| `HUAYU_SMTP_USER` | (empty) | SMTP account |
| `HUAYU_SMTP_PASSWORD` | (empty) | SMTP password |
| `HUAYU_SMTP_FROM` | (empty) | Sender address |

Other variables: `DOMAIN` (the domain, with **no** `https://` and no `/`, only in the Docker Compose `.env`), `HUAYU_ADMIN_PASSWORD` (password for non-interactive `create-admin`), `HUAYU_ROUTERS` (test isolation only, never set it in production), `PORT` (for `start.sh`), and for `deploy/backup.sh`: `BACKUP_DIR`, `KEEP` (how many backup **files** to keep, default 14, i.e. 14 days with a daily backup), `BACKUP_PING_URL` (optional, reports the result to a healthchecks.io-style service), `BACKUP_MODE` (`docker` or `host` for the no-Docker install; `host` also reads `HUAYU_DATA_DIR` and `HUAYU_USER`). `.env` format: one `NAME=value` per line, comments on their own line, single quotes around a value with `$` or `#` (see `.env.example`).

### Run the tests

```bash
.venv/bin/python -m pytest                       # everything
.venv/bin/python -m pytest tests/test_marketing.py   # only the public site and the deploy kit
```

Do not add `-q` (it is already configured). The public-site tests also check that no raw translation key shows in any of the 3 languages, that the privacy page names every kind of stored data, that the PWA icons are valid PNGs, and that this README lists every environment variable.

### Security model (summary)

- Passwords are hashed with Argon2id. The login session is a signed cookie (HttpOnly, SameSite=Lax, plus Secure in prod, up to 30 days); bumping `session_version` logs out every device.
- Every state-changing request is a POST with a CSRF token (signed cookie matched against the form field). GET never changes state.
- Headers: CSP `script-src 'self'` (no inline scripts), no framing, `nosniff`, Referrer-Policy, HSTS in prod; pages with personal data are sent `Cache-Control: no-store`. No public API docs.
- Role-based access (learner, teacher, admin); a learner can only read their own rows. Admin actions are written to an audit log.
- Rate limits on login and sensitive actions, per IP, kept in memory **per process** (with 2 workers each worker has its own budget, and it resets on restart).
- SQLite in WAL mode with `BEGIN IMMEDIATE` for writes; deleting an account cascades through everything the learner produced (only anonymised audit lines (the learner code and staff notes are removed, plus one account_deleted line) and payment records detached from the account remain).
- AI (if on): only the reason code and structured facts are sent, **never** name, e-mail or the free-text note; it needs separate consent; a reply with a number that is not in the data is rejected; every failure falls back to template text.
- Deployment: the container runs as an unprivileged user, the app port is not published, and only Caddy receives 80/443. The app's access log is off (the password-reset link carries a live token in its path), Caddy cuts stalled requests (read timeouts) and refuses request bodies over 3 MB, Docker logs rotate (at most 50 MB per service), containers have a memory ceiling, and the data volume is protected from `docker compose down -v`.
- Not there: two-factor login, third-party sign-in, automated dependency scanning, a penetration test.

### What is NOT built

- **A payment gateway** (card, automatic bank transfer, e-invoices). Only manual payments exist: an admin records them and extends access. A gateway can later call `services.billing.activate()` from its webhook.
- **E-mail reminders.** No reminder e-mails or push notifications (deliberately: the product promises "not pressure"). E-mail is only used for password resets.
- **Content authoring tools for teachers** (lesson writing, audio, versioning). Only the question review and import screens in the admin area exist.
- **A native mobile app.** Web only; it can be added to the home screen through the manifest. There is no service worker, so it **does not work offline**, and there are no push notifications.
- Also missing: speaking and pronunciation practice, levels above A2, more than one server (SQLite runs on one machine), automated CI.

### Business and legal items the owner must handle

The software cannot solve these; ask an accountant or a lawyer in Taiwan.

1. **Business registration** (company or sole proprietorship) before taking money, with tax and invoicing duties. If the owner is a student or a foreigner, check visa, work-permit and income rules in Taiwan (the school's international office or a lawyer).
2. **A payment gateway in Taiwan.** Common gateways (for example ECPay, NewebPay, TapPay) usually require a registered business and their own onboarding. None is integrated.
3. **A lawyer must review the privacy policy and the terms** (`/privacy`, `/terms`). Both pages are **drafts** with yellow blanks the owner must fill in: operator name, address, tax ID (統一編號), refund and withdrawal policy, court and dispute process. Points to review: duties under the Personal Data Protection Act (個人資料保護法), sending data outside Taiwan when the AI is on, breach notification, a minimum age, and consumer protection law for online sales. Whenever the database gains a new kind of data, the privacy page must be updated (a test will remind you).
4. **Teacher review of the question bank.** None of the 50 questions is approved yet (44 pending, 6 flagged `needs_edit` because a second answer is defensible). In prod only approved questions are served by default. A teacher has to check every question, and the origin and copyright of the content must be confirmed.
5. **The rights of the original team and the school.** The idea and the first data come from a university competition project (team 霸氣隊). Before commercialising, agree in writing with the team members and check the competition rules, the school's intellectual-property rules, and any supervisor or sponsor.
6. **Pilot data used for research.** If pilot figures (for example learner ratings) are used in a report, a contest entry or research, you need separate consent and clear information for learners; the privacy page only describes running the service.
7. **Domain and outgoing e-mail** (SPF/DKIM so password-reset mail does not land in spam), a support mailbox, and a check of the 華語教練 name before wide promotion.
