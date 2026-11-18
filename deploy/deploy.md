# Triển khai Huayu Coach lên VPS (Ubuntu 24.04 + Docker + Caddy)

Hướng dẫn từng bước để đưa ứng dụng từ máy cá nhân lên một VPS và mở cho sinh viên dùng thử. Các lệnh dùng được nguyên văn, chỉ cần thay `huayu.example.com`, `deploy` và địa chỉ IP bằng của bạn.

## 0. Đọc trước: cái gì đã được kiểm thử, cái gì chưa

Máy phát triển (Mac) **không có Docker, Caddy, ufw hay systemd**, nên **chưa ai chạy thử** bộ triển khai này trên một máy chủ thật. Mọi tệp được viết theo cách đơn giản, thông dụng để chạy được ngay lần đầu, nhưng hãy coi lần triển khai đầu tiên là lần kiểm thử đầu tiên. Làm thử trên một VPS rẻ hoặc tên miền phụ trước khi mời sinh viên.

| Phần | Trạng thái |
|---|---|
| Cú pháp `backup.sh`, `restore.sh` (`bash -n`) | Đã kiểm tra trên Mac (test tự động `tests/test_marketing.py`) |
| `compose.yml` đọc được bằng YAML, đúng cấu trúc (app không mở cổng, caddy mở 80/443, volume, giới hạn bộ nhớ, xoay vòng nhật ký) | Đã kiểm tra trên Mac |
| `deploy/backup_db.py` và `deploy/restore_db.py` (sao lưu / khôi phục SQLite đang ở chế độ WAL khi có tiến trình đang ghi liên tục; từ chối tệp rỗng, hỏng, cắt cụt, hoặc không phải cơ sở dữ liệu của Huayu) | Đã chạy thật trên Mac với SQLite thật |
| Logic của `backup.sh` và `restore.sh` (giữ đúng N bản, đường dẫn có dấu cách, từ chối tệp xấu **trước khi** dừng app, tự bật lại app khi lỗi) | Đã chạy trên Mac với một lệnh `docker` **giả lập**, chưa chạy với Docker thật |
| Các đường dẫn `COPY` trong Dockerfile đều tồn tại; lệnh `python -m app.cli prepare` và `create-admin` có thật | Đã kiểm tra trên Mac |
| `docker build`, `docker compose up`, HEALTHCHECK, quyền ghi vào volume `/data`, giới hạn bộ nhớ, volume `external` | **CHƯA KIỂM THỬ** |
| Caddy: cú pháp `Caddyfile`, timeout, giới hạn 3 MB, tự lấy chứng chỉ HTTPS, chuyển hướng http sang https, header | **CHƯA KIỂM THỬ** (cú pháp lấy từ tài liệu Caddy 2; trên VPS chạy `docker compose exec caddy caddy validate --config /etc/caddy/Caddyfile`) |
| `ufw`, tạo người dùng, SSH, cron sao lưu | **CHƯA KIỂM THỬ** |
| Cách cài không dùng Docker (`huayu-coach.service`, Caddy trên máy) | **CHƯA KIỂM THỬ** (chưa có Linux/systemd để thử; ứng dụng đã được thử trên Python 3.13, `requires-python` là 3.12 trở lên) |

Mỗi bước dưới đây mà chưa từng chạy thật đều có nhãn **[CHƯA KIỂM THỬ]**. Nếu một bước lỗi, hãy sửa tài liệu này ngay sau khi bạn tìm ra cách đúng.

## 1. Chuẩn bị

- Một VPS Ubuntu 24.04 có địa chỉ IPv4 công khai (cỡ máy: xem ngay bên dưới).
- Một tên miền (hoặc tên miền phụ), ví dụ `huayu.example.com`, và quyền sửa DNS. **Nên dùng một tên miền phụ riêng cho ứng dụng**, đừng dùng tên miền gốc (`example.com`): ở chế độ prod ứng dụng gửi `Strict-Transport-Security` (HSTS, một năm), buộc trình duyệt chỉ dùng HTTPS với đúng tên miền đó; trên một tên miền phụ riêng, việc đó không đụng tới website khác của bạn.
- Thư mục dự án trên Mac đã qua test: `cd huayu-coach && .venv/bin/python -m pytest`.

### Cỡ VPS

**Khuyến nghị: 2 GB RAM, 1 vCPU, đĩa từ 20 GB.** 1 GB RAM chạy được nhưng sát: bắt buộc thêm swap (bên dưới) và đừng kỳ vọng mượt khi nhiều người đăng nhập cùng lúc.

Cơ sở của con số này (đo trên Mac, Python 3.13, **chưa đo trên VPS và chưa đo tải thật**): một tiến trình ứng dụng đã nạp xong chiếm khoảng 100 MB; 30 yêu cầu băm mật khẩu cùng lúc đẩy nó lên khoảng 155 MB, vì Argon2 dùng 19 MiB mỗi lượt và ứng dụng chỉ cho tối đa 4 lượt chạy đồng thời mỗi worker (`app/security.py`). Có 2 worker, cộng Caddy (vài chục MB), hệ điều hành và Docker: **ước chừng** 700 MB lúc cao điểm. Đĩa: mỗi ảnh Docker vài trăm MB, bạn giữ 2 đến 3 bản (mục 11), cộng bản sao lưu và nhật ký.

`compose.yml` đặt trần bộ nhớ **768 MB cho `app` và 256 MB cho `caddy`**. Đây chỉ là dây an toàn để một tiến trình chạy mất kiểm soát không làm sập cả máy (khi chạm trần, Docker dừng container đó và `restart: unless-stopped` bật lại); nó không thay cho việc chọn VPS đủ RAM. Trên VPS lớn hơn, hãy tăng `mem_limit` trong `compose.yml`.

Thêm swap 2 GB (một lần; một số loại VPS ảo hóa kiểu OpenVZ không cho phép):

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h                              # dòng Swap phải ra 2.0Gi
```

## 2. Tạo người dùng và khóa SSH **[CHƯA KIỂM THỬ]**

**Mục tiêu:** đăng nhập bằng khóa SSH rồi mới tắt mật khẩu và tắt đăng nhập root. Thứ tự dưới đây được chọn để một người chỉ có mật khẩu root **không tự khóa mình ra ngoài**. Quy tắc duy nhất: **giữ nguyên cửa sổ root đang mở cho đến khi xong bước 6.**

1. **Trên Mac**: có khóa SSH chưa? Nếu lệnh dưới in "No such file", tạo khóa (nên đặt cụm mật khẩu):

   ```bash
   ls ~/.ssh/id_ed25519.pub || ssh-keygen -t ed25519
   ```

2. **Trên VPS** (đăng nhập `root` bằng mật khẩu lần đầu, giữ cửa sổ này mở):

   ```bash
   adduser deploy                       # đặt mật khẩu mạnh, các câu hỏi còn lại cứ Enter
   usermod -aG sudo deploy
   ```

3. **Trên Mac**: cài khóa cho người dùng `deploy`. Lệnh này dùng mật khẩu của `deploy` vừa đặt, không phụ thuộc vào việc `/root/.ssh` có tồn tại hay không:

   ```bash
   ssh-copy-id deploy@IP_CUA_VPS
   ```

4. **Chứng minh đăng nhập bằng khóa chạy được, trong một cửa sổ terminal MỚI** (cửa sổ root vẫn mở). Lệnh này cấm dùng mật khẩu nên nếu khóa chưa đúng, nó thất bại ngay:

   ```bash
   ssh -o PreferredAuthentications=publickey -o PasswordAuthentication=no deploy@IP_CUA_VPS true && echo KHOA_SSH_OK
   ssh -t deploy@IP_CUA_VPS sudo -v    # nhập mật khẩu của deploy: phải qua được, vì root sắp bị tắt
   ```

   **Chỉ khi thấy `KHOA_SSH_OK` và `sudo -v` qua được** mới làm bước 5. Nếu không, dừng ở đây: đăng nhập bằng mật khẩu vẫn dùng tiếp được, chỉ kém an toàn hơn.

5. **Trên VPS** (người dùng `deploy`), tắt mật khẩu và đăng nhập root. Kiểm tra cú pháp trước khi nạp lại:

   ```bash
   printf 'PermitRootLogin no\nPasswordAuthentication no\n' | sudo tee /etc/ssh/sshd_config.d/10-hardening.conf
   sudo sshd -t                         # không in gì = cú pháp đúng
   sudo systemctl reload ssh
   sudo sshd -T | grep -Ei 'passwordauthentication|permitrootlogin'     # phải ra: permitrootlogin no / passwordauthentication no
   ```

   (Tên tệp bắt đầu bằng `10-` để được đọc trước `50-cloud-init.conf` của nhiều nhà cung cấp VPS; OpenSSH lấy giá trị đầu tiên nó gặp. Lệnh `sshd -T` cho thấy giá trị thực sự có hiệu lực.)

6. Mở thêm một cửa sổ nữa và thử lại đăng nhập bằng khóa (`ssh deploy@IP_CUA_VPS`). Rồi mới đóng cửa sổ root cũ.

**Nếu vẫn bị khóa ngoài:** vào bảng điều khiển web của nhà cung cấp VPS (console/VNC), đăng nhập `root` bằng mật khẩu (console không bị `sshd_config` chặn), xóa `/etc/ssh/sshd_config.d/10-hardening.conf` rồi `systemctl reload ssh`.

Tùy chọn nhưng nên làm: `sudo apt update && sudo apt -y install unattended-upgrades` (tự cài bản vá bảo mật).

## 3. Tường lửa ufw **[CHƯA KIỂM THỬ]**

**Thứ tự quan trọng: cho phép SSH TRƯỚC khi bật ufw**, nếu không ufw chặn luôn phiên SSH của bạn. Giữ phiên hiện tại mở và nếu nhà cung cấp dùng cổng SSH khác 22 thì cho phép đúng cổng đó (`sudo ufw allow 2222/tcp`).

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 443/udp      # HTTP/3, có thể bỏ
sudo ufw show added         # xem lại: phải có OpenSSH (hoặc cổng SSH của bạn)
sudo ufw enable
sudo ufw status
```

Sau đó mở một cửa sổ terminal mới và kiểm tra vẫn SSH vào được.

Lưu ý: Docker tự mở cổng bằng iptables và **bỏ qua ufw** đối với cổng nó công bố. Ở đây chỉ Caddy công bố cổng 80/443. Ứng dụng (cổng 8000) không được công bố nên không ai từ internet chạm tới được. Đừng thêm `ports:` cho dịch vụ `app`.

## 4. Cài Docker **[CHƯA KIỂM THỬ]**

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker deploy
exit        # đăng xuất rồi vào lại để nhóm docker có hiệu lực
docker compose version
```

## 5. Trỏ tên miền về VPS **[CHƯA KIỂM THỬ]**

Tạo bản ghi DNS **A**: `huayu.example.com` trỏ tới IPv4 của VPS. Kiểm tra từ máy bạn: `dig +short huayu.example.com` phải ra đúng IP. Caddy chỉ xin được chứng chỉ khi DNS đã đúng và cổng 80/443 mở.

**Đừng tạo bản ghi AAAA (IPv6) trong đợt thử.** Giới hạn tốc độ đăng ký, đăng nhập và quên mật khẩu tính theo địa chỉ IP của người truy cập. Theo tài liệu của Docker, khi một cổng được công bố ra cả IPv6 mà mạng của container chỉ có IPv4 (đúng với cấu hình này), kết nối IPv6 có thể đi qua một tiến trình trung gian và ứng dụng sẽ thấy **mọi người dùng IPv6 là cùng một địa chỉ**: cả nhóm cùng dùng chung một hạn mức và có thể khóa nhau. Điều này chưa được chạy thử ở đây. Không có bản ghi AAAA thì không ai kết nối bằng IPv6, và người dùng mạng chỉ có IPv6 (di động) thường vẫn vào được nhờ cơ chế chuyển đổi NAT64/464XLAT của nhà mạng. Chỉ bật IPv6 sau khi bạn tự kiểm chứng ứng dụng thấy địa chỉ thật của người truy cập.

Một lớp học dùng chung một Wi-Fi cũng chung một địa chỉ IP công cộng, nên cùng chia sẻ hạn mức đăng ký theo IP (xem `app/routers/auth.py`). Nếu cả lớp đăng ký cùng lúc mà bị báo "Thao tác quá nhanh" (lỗi 429), cho các em đăng ký rải ra, hoặc `docker compose restart app` để xóa bộ đếm (bộ đếm nằm trong bộ nhớ và mất khi khởi động lại).

## 6. Đưa mã nguồn lên VPS **[CHƯA KIỂM THỬ]**

Mã nguồn nằm trong kho GitHub `https://github.com/huytue3107/huayu-coach` (nhánh `main`). Nếu kho ở chế độ **riêng tư** (bạn có thể đổi ở Settings của kho trên GitHub), người lạ, và cả VPS của bạn, không `git clone https://...` được nếu không có quyền. Có hai cách chạy được; chọn **một** cách và dùng nó cho cả các lần cập nhật sau (mục 11), đừng trộn hai cách trên cùng một thư mục.

**Cả hai cách đều không đưa tệp `.env` lên VPS**: `.env` không nằm trong git (`.gitignore` và `.dockerignore` loại nó; kho chỉ có `.env.example`) và lệnh `rsync` dưới đây cũng loại nó. Bạn tự tạo `.env` trên VPS ở mục 7.

### Cách A: `rsync` từ Mac (không cần cấp quyền gì cho VPS)

Chạy trong thư mục dự án trên Mac:

```bash
rsync -av --delete --exclude .git --exclude .venv --exclude data --exclude .env --exclude '.pytest_cache' --exclude '__pycache__' \
  ./ deploy@IP_CUA_VPS:~/huayu-coach/
```

(`--exclude .env` và `--exclude data` để không ghi đè cấu hình và dữ liệu thật trên VPS; `--exclude .git` để không chép lịch sử git lên VPS.) `--delete` xóa trên VPS mọi tệp không còn trên Mac, nên **bản mã cũ không còn trên VPS sau lệnh này**: mục 11 nói cách giữ lại bản cũ trước khi chạy lại lệnh này. Cách này lấy đúng những gì đang có trong thư mục trên Mac, kể cả thay đổi chưa commit.

### Cách B: khóa triển khai (deploy key) chỉ đọc, rồi `git clone` trên VPS

Khóa triển khai là một khóa SSH chỉ có quyền **đọc một kho duy nhất**. Làm trên VPS bằng người dùng `deploy`:

1. Tạo khóa (không đặt cụm mật khẩu, để `git pull` chạy được không cần người gõ; rủi ro thấp vì khóa chỉ đọc một kho) và in khóa công khai ra:

   ```bash
   ssh-keygen -t ed25519 -N "" -C "huayu-coach-vps"
   cat ~/.ssh/id_ed25519.pub
   ```

   Nếu `ssh-keygen` hỏi có ghi đè tệp `id_ed25519` đã có không, trả lời `n` (VPS đã có khóa; dùng luôn khóa đó, lệnh `cat` vẫn đúng).

2. Trên GitHub, bằng trình duyệt, đăng nhập tài khoản chủ kho: mở kho `huytue3107/huayu-coach`, vào **Settings** > **Deploy keys** > **Add deploy key**. Đặt tên (ví dụ `vps`), dán nguyên dòng khóa công khai ở bước 1, **không** đánh dấu "Allow write access" (để khóa chỉ đọc), bấm **Add key**.
3. Thử kết nối (trên VPS): `ssh -T git@github.com`. Lần đầu, ssh hỏi có tin máy chủ `github.com` không (`Are you sure you want to continue connecting`): đối chiếu dấu vân tay hiện ra với danh sách trong tài liệu của GitHub ("GitHub's SSH key fingerprints") rồi mới gõ `yes`. Kết quả đúng là một dòng chào có chữ `successfully authenticated` (GitHub không cấp shell, nên lệnh này không mở phiên nào; đó là bình thường).
4. Lấy mã:

   ```bash
   cd ~
   git clone git@github.com:huytue3107/huayu-coach.git
   cd huayu-coach
   ```

Lưu ý cho cách B: (a) VPS chỉ nhận được những gì đã **commit và `git push`** lên GitHub, thay đổi chưa đẩy lên trên Mac sẽ không tới VPS. (b) Muốn biết VPS đang chạy bản nào: `git log -1 --oneline`. (c) Cập nhật sau này là `git pull` (mục 11, bước 4). (d) Nếu nghi VPS bị lộ, xóa khóa ở **Settings** > **Deploy keys** của kho. (e) Cách B chưa được chạy thử; các bước GitHub ở trên dựa trên giao diện và tài liệu của GitHub, không phải trên lần thử thật.

## 7. Tạo tệp `.env` **[CHƯA KIỂM THỬ]**

Trên VPS:

```bash
cd ~/huayu-coach
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(48))"     # in ra một khóa dài, dùng cho HUAYU_SECRET_KEY
nano .env
chmod 600 .env
```

Điền tối thiểu các dòng sau (các biến khác xem bảng trong `README.md`). **Chú thích chỉ viết ở dòng riêng, không viết sau giá trị**:

```ini
# Chỉ tên miền: KHÔNG có https://, KHÔNG có dấu / ở cuối
DOMAIN=huayu.example.com
HUAYU_SECRET_KEY=dán-khóa-vừa-tạo-vào-đây
HUAYU_SUPPORT_EMAIL=ban@example.com
# Khuyên dùng: chỉ người có mã mới đăng ký được
HUAYU_INVITE_CODE=mã-mời-bí-mật-cho-đợt-thử
# Đơn vị vận hành, hiện trên /privacy và /terms (để trống thì trang còn ô vàng giữ chỗ). Giá trị có dấu cách đặt trong nháy kép.
HUAYU_OPERATOR_NAME="Tên đơn vị vận hành"
HUAYU_OPERATOR_ADDRESS="Địa chỉ đơn vị vận hành"
HUAYU_OPERATOR_TAX_ID=ma-so-thue
# Miễn phí trong giai đoạn thử nghiệm
HUAYU_BILLING_ENABLED=false
# Xem cảnh báo bên dưới
HUAYU_ALLOW_UNREVIEWED_QUESTIONS=true
# Email (bắt buộc cấu hình trước khi mời người dùng). Cổng 587 đi với starttls; cổng 465 đi với ssl.
HUAYU_SMTP_HOST=smtp.example.com
HUAYU_SMTP_PORT=587
HUAYU_SMTP_SECURITY=starttls
HUAYU_SMTP_USER=ten-dang-nhap
HUAYU_SMTP_PASSWORD='mat-khau-smtp'
HUAYU_SMTP_FROM=ban@example.com
```

Những điểm dễ sai:

- **Nháy đơn cho giá trị có `$` hoặc `#`** (mật khẩu SMTP, mã mời). Docker Compose mở rộng `$...` trong giá trị không có nháy hoặc có nháy kép, nên mật khẩu như `abc$def` sẽ bị đổi âm thầm; trong nháy đơn thì giữ nguyên. Một giá trị có ` #` (dấu cách rồi dấu #) cũng bị cắt cụt nếu không có nháy.
- `DOMAIN` chỉ là tên miền. Nếu bạn ghi `DOMAIN=https://huayu.example.com`, địa chỉ công khai thành `https://https://...`, ở chế độ prod cấu hình đó bị từ chối và `app` **không khởi động** (`docker compose logs app` ghi `Invalid configuration: HUAYU_BASE_URL must look like https://your.domain`), container cứ khởi động lại. Sửa `.env` rồi `docker compose up -d` (mục 8 có bảng lỗi).
- `HUAYU_SECRET_KEY` phải dài từ 32 ký tự. Nếu thiếu hoặc ngắn, ứng dụng **từ chối khởi động** (vòng lặp khởi động lại, xem `docker compose logs app`). **Đừng đổi khóa này sau khi đã có người dùng**: mọi người sẽ bị đăng xuất. Bản sao lưu cơ sở dữ liệu **không chứa** `.env`: hãy lưu một bản `.env` ở nơi an toàn (ví dụ trình quản lý mật khẩu).
- `compose.yml` tự đặt `HUAYU_ENV=prod`, `HUAYU_DATA_DIR=/data`, `HUAYU_TRUSTED_PROXY=true` và `HUAYU_BASE_URL=https://` + `DOMAIN`, nên dòng `HUAYU_ENV=dev` còn sót từ `.env.example` không làm bản chạy thật rơi vào chế độ dev.
- **Câu hỏi chưa được giáo viên duyệt:** trong chế độ prod, mặc định ứng dụng **chỉ phục vụ câu hỏi đã được duyệt** (`approved`). Bộ 50 câu hỏi nạp sẵn đều đang ở trạng thái `pending_review` hoặc `needs_edit`, nên nếu không làm gì, sinh viên sẽ **không có câu nào để làm**. Chọn một trong hai: (a) nhờ giáo viên duyệt câu hỏi ở `/admin/questions` trước khi mời sinh viên; hoặc (b) đặt `HUAYU_ALLOW_UNREVIEWED_QUESTIONS=true` cho giai đoạn thử nghiệm. Trang chủ tự nói đúng tình trạng này cho sinh viên ("một số câu chưa được rà soát").
- **Email:** nếu không cấu hình SMTP (`HUAYU_SMTP_HOST`, ...), thư đặt lại mật khẩu **không được gửi đi**, chỉ được ghi thành tệp trong `/data/outbox` trên VPS (chứa địa chỉ email và liên kết đặt lại, và **không tự bị xóa**, xem mục 10). Sinh viên quên mật khẩu sẽ không tự lấy lại được. Cổng và kiểu bảo mật phải đi đôi: `587` + `starttls` (đa số nhà cung cấp), `465` + `ssl`; `none` chỉ cho máy chuyển thư trên cùng máy hoặc mạng riêng. Cặp sai thì thư không đi và chỉ có dòng `smtp send failed` trong `docker compose logs app`, còn sinh viên vẫn thấy thông báo "nếu email có tài khoản thì chúng tôi đã gửi liên kết" (cố ý, để không lộ email nào có tài khoản). Vì vậy **phải thử gửi thật** (mục 15).
- Để AI viết lời giải thích ở chế độ tắt (`HUAYU_LLM_PROVIDER=none`) trừ khi bạn đã đọc kỹ đoạn "AI" trong trang `/privacy`.

## 8. Khởi động **[CHƯA KIỂM THỬ]**

```bash
cd ~/huayu-coach
docker volume create huayu-data      # MỘT LẦN duy nhất: volume chứa cơ sở dữ liệu, tạo tay để lệnh down -v không xóa được nó
docker compose up -d --build
docker compose ps                    # cả app và caddy phải ở trạng thái running; app sẽ thành "healthy" sau ~30 giây
docker compose logs -f app           # Ctrl+C để thoát; phải thấy dòng "database ready" rồi uvicorn khởi động
docker compose exec app python -m app.cli check     # xem dòng env=prod base_url=https://huayu.example.com (MỘT https:// thôi) smtp=yes
curl -sS https://huayu.example.com/healthz     # kết quả đúng: {"ok":true}
```

Mỗi lần khởi động, ứng dụng chạy `python -m app.cli prepare` (áp dụng migration cơ sở dữ liệu và nạp dữ liệu tham chiếu, an toàn khi chạy lại) rồi mới mở web.

Nhật ký của `app` **không có dòng cho từng yêu cầu** (đã tắt `access log` có chủ ý, vì liên kết đặt lại mật khẩu `/reset/<mã>` chứa một mã dùng được trong đường dẫn): bạn chỉ thấy dòng khởi động và lỗi. Muốn biết có người dùng hay không, dùng dịch vụ giám sát (mục 14) chứ không dùng nhật ký.

Nếu lỗi:

| Triệu chứng | Nguyên nhân thường gặp |
|---|---|
| `app` cứ khởi động lại | `docker compose logs app`: thường là `HUAYU_SECRET_KEY` ngắn hoặc thiếu |
| `external volume "huayu-data" not found` | Chưa chạy `docker volume create huayu-data` |
| Trình duyệt báo chứng chỉ lỗi / không vào được | DNS chưa trỏ đúng, cổng 80/443 bị chặn (ufw hoặc tường lửa của nhà cung cấp VPS); xem `docker compose logs caddy` |
| 502 Bad Gateway | `app` chưa healthy hoặc bị lỗi; xem `docker compose ps` và log của `app` |
| `DOMAIN` báo "Set DOMAIN=..." | Thiếu `DOMAIN=` trong `.env` |
| `app` cứ khởi động lại và log có `HUAYU_BASE_URL must look like https://your.domain` | `DOMAIN` có ghi `https://` (thành `https://https://...`): ứng dụng từ chối khởi động. Sửa `.env` rồi `docker compose up -d` |
| Caddy báo lỗi đọc `Caddyfile` | `docker compose exec caddy caddy validate --config /etc/caddy/Caddyfile` cho biết dòng lỗi |

## 9. Tạo quản trị viên đầu tiên **[CHƯA KIỂM THỬ]**

```bash
docker compose exec app python -m app.cli create-admin --email ban@example.com
```

Lệnh sẽ hỏi mật khẩu (ít nhất 8 ký tự) hai lần. Đăng nhập tại `https://huayu.example.com/login` rồi vào `/admin`. Không đưa mật khẩu vào dòng lệnh (nó nằm lại trong lịch sử lệnh).

## 10. Sao lưu, lưu giữ dữ liệu và bảo vệ volume **[CHƯA KIỂM THỬ]**

### 10.1 Sao lưu hằng ngày

Mỗi ngày một bản, giữ **14 tệp** mới nhất (`KEEP=14`, tức **14 ngày** nếu cron chạy mỗi ngày), lưu ở `~/huayu-backups` (chỉ người dùng `deploy` đọc được; đường dẫn có dấu cách cũng được):

```bash
cd ~/huayu-coach
./deploy/backup.sh                   # chạy thử một lần, phải thấy "backup written"
crontab -e                           # thêm các dòng sau (mỗi ngày 03:15)
```

```cron
MAILTO=ban@example.com
BACKUP_PING_URL=https://hc-ping.com/UUID-CUA-BAN
15 3 * * * cd /home/deploy/huayu-coach && ./deploy/backup.sh >> /home/deploy/huayu-backups/backup.log
```

- `backup.sh` thoát với mã khác 0 khi lỗi (container không chạy, hết đĩa, tệp ra không phải SQLite). Lỗi được in ra stderr; dòng cron trên chỉ chuyển stdout vào `backup.log`, nên cron sẽ gửi thư (`MAILTO`) **chỉ khi có lỗi**. Lưu ý: `MAILTO` chỉ hoạt động nếu VPS có sẵn chương trình gửi thư (MTA); VPS mới thường **không có**, nên đừng chỉ dựa vào nó.
- **Cách báo lỗi đáng tin hơn (tùy chọn): `BACKUP_PING_URL`.** Tạo một "check" miễn phí kiểu [healthchecks.io](https://healthchecks.io) (chu kỳ 1 ngày, thời gian chờ thêm 2 giờ), dán địa chỉ ping vào dòng `BACKUP_PING_URL=` ở trên. `backup.sh` gọi địa chỉ đó sau mỗi bản sao lưu thành công và gọi `<địa chỉ>/fail` khi lỗi. Nếu tín hiệu ngừng đến (cron chết, VPS tắt, hết đĩa), dịch vụ gửi email cho bạn. Việc ping lỗi không làm hỏng bản sao lưu. Biến không đặt thì không ping.
- **Thử khôi phục ít nhất một lần** trên VPS thử trước khi có dữ liệu thật (mục 10.5). `restore.sh` không có chế độ "thử vào thư mục khác": nó **thay cơ sở dữ liệu thật**, nên đừng chạy nó để "thử" trên máy đang có sinh viên. Cách thử không đụng tới dữ liệu thật (chỉ kiểm tra bản sao lưu) nằm ở mục 10.5.

### 10.2 Bản sao lưu có gì và không có gì

Có: **toàn bộ cơ sở dữ liệu**, gồm dữ liệu cá nhân của sinh viên, nên tệp chỉ đọc được bởi bạn. Không có: `.env` (khóa ký và mật khẩu SMTP), chứng chỉ của Caddy (xin lại được), thư mục `/data/outbox`. Hãy lưu `.env` riêng ở nơi an toàn.

### 10.3 Dữ liệu tồn tại bao lâu (chính xác)

Trang `/privacy` hứa bản sao lưu được thay sau 14 ngày và nhật ký chỉ giữ khi còn cần. Bảng dưới là thực tế của bộ triển khai này; **các điều kiện ghi trong bảng là việc bạn phải làm**, nếu không lời hứa trên `/privacy` sẽ không đúng.

| Nơi | Giữ bao lâu |
|---|---|
| Cơ sở dữ liệu đang chạy | Khi người dùng xóa tài khoản (ở Cài đặt, hoặc bạn chạy `python -m app.cli delete-user` theo yêu cầu qua email), mọi dòng của họ bị xóa ngay khỏi cơ sở dữ liệu (chỉ còn các dòng nhật ký đã ẩn danh hóa, trong đó có một dòng account_deleted, và các bản ghi thanh toán đã tách khỏi tài khoản). SQLite mặc định **không ghi đè ngay các byte đã giải phóng** trong tệp trên đĩa; chúng bị ghi đè dần khi có dữ liệu mới. |
| Bản sao lưu trên VPS (`~/huayu-backups/huayu-*.db.gz`) | `backup.sh` giữ `KEEP` **tệp** mới nhất, mặc định 14. Với cron hằng ngày, **một tài khoản đã xóa còn trong các bản sao lưu làm trước lúc xóa tối đa 14 ngày** sau lúc xóa. Tệp cũ chỉ bị xóa khi một bản mới được tạo **thành công**: nếu cron ngừng chạy, bản cũ ở lại lâu hơn (đó là lý do cần báo lỗi ở mục 10.1). Bản sao lưu thủ công cũng tính vào 14 tệp, nên chỉ làm bản cũ rơi ra sớm hơn. Nếu bạn đổi `KEEP`, sửa câu "14" trong `app/locales/*/marketing.json` (khóa `privacy.s2_p2` và `privacy.s7_p3`). |
| Bản chép ngoài VPS | **Do bạn chịu trách nhiệm**; `backup.sh` không động tới chúng. Xem 10.4. |
| `huayu.db.before-restore-*` trong volume dữ liệu | **Mãi mãi cho đến khi bạn xóa tay.** Đó là bản đầy đủ của cơ sở dữ liệu trước lần khôi phục. Giữ nó cho đến khi bạn đối chiếu xong tài khoản bị khôi phục lại (10.5) rồi xóa. |
| `/data/outbox/*.txt` | **Mãi mãi cho đến khi bạn xóa tay.** Xóa tài khoản (ở Cài đặt hoặc bằng `delete-user`) xóa các tệp của đúng địa chỉ email đó; tệp của các địa chỉ khác ở lại. Chỉ có khi chưa cấu hình SMTP; đã cấu hình SMTP thì thư mục này không nhận thư mới. Có tệp cũ thì xóa: `docker compose exec app sh -c 'rm -f /data/outbox/*.txt'`. |
| Nhật ký Docker (`app`, `caddy`) | Không có dòng cho từng yêu cầu và không có địa chỉ IP của người truy cập (access log của app và của Caddy đều tắt); chỉ có dòng khởi động và thông báo lỗi. Xoay vòng **theo dung lượng**: tối đa 5 tệp x 10 MB = 50 MB mỗi dịch vụ, **không theo thời gian**; nhật ký mất khi container bị tạo lại (mỗi lần `docker compose up -d --build`). Nhật ký lỗi có thể chứa traceback: đừng dán chúng lên nơi công cộng. |

Khi VPS bị hỏng hoặc bạn bỏ VPS, **xóa VPS ở nhà cung cấp cũng là xóa dữ liệu**; đừng để lại ảnh đĩa/snapshot.

### 10.4 Bản chép ngoài VPS và vòng đời (việc của bạn)

Chép bản sao lưu ra ngoài VPS (máy của bạn, kho lưu trữ đã mã hóa); nếu VPS mất thì đó là bản duy nhất. Chúng chứa **toàn bộ dữ liệu cá nhân**: đặt trên ổ đĩa đã mã hóa (FileVault trên Mac) và **đừng để thư mục này nằm trong thư mục tự đồng bộ** (iCloud, Drive, Dropbox) trừ khi bạn cũng kiểm soát thời hạn ở đó. Gợi ý vòng đời **khớp với 14 ngày trên VPS**, chạy hằng ngày trên máy của bạn:

```bash
rsync -av deploy@IP_CUA_VPS:~/huayu-backups/ ~/huayu-backups-offsite/
find ~/huayu-backups-offsite -name 'huayu-*.db.gz' -mtime +14 -delete
```

Dòng `find` xóa theo **tuổi tệp** (`rsync -a` giữ nguyên giờ tạo gốc). Không nên dùng `rsync --delete` để phản chiếu: nếu thư mục sao lưu trên VPS vô tình trống, bản chép của bạn cũng bị xóa theo. Nếu bạn lưu lên kho đám mây (S3 và tương tự), đặt quy tắc vòng đời "hết hạn sau 14 ngày". Muốn giữ lâu hơn (ví dụ bản hằng tuần), bạn phải nói điều đó trong trang `/privacy`.

### 10.5 Khôi phục và tài khoản đã xóa

```bash
./deploy/restore.sh ~/huayu-backups/huayu-AAAAMMDD-HHMMSS.db.gz
```

Lệnh từ chối ngay (khi app **còn chạy**) tệp thiếu, rỗng, không phải `.gz`, hoặc không chứa cơ sở dữ liệu SQLite. Sau khi bạn gõ `RESTORE`, nó dừng `app`, kiểm tra lại bên trong container (tính toàn vẹn, có các bảng của Huayu), giữ bản cũ cạnh tệp mới dưới tên `huayu.db.before-restore-...` trong volume dữ liệu, thay cơ sở dữ liệu rồi bật lại `app` và chờ nó khỏe. **Dù khôi phục lỗi ở bước nào, lệnh luôn bật lại `app` trước khi thoát** (nếu tệp bị từ chối, cơ sở dữ liệu hiện tại giữ nguyên).

**Khôi phục làm sống lại các tài khoản đã bị xóa sau thời điểm sao lưu** (mật khẩu cũ của họ lại dùng được), và mất mọi thứ sinh viên làm sau thời điểm đó. Sinh viên tự xóa tài khoản ở Cài đặt, bạn không nhận được danh sách, nhưng bản `huayu.db.before-restore-*` chính là trạng thái ngay trước khi khôi phục, nên lệnh sau liệt kê các tài khoản vừa sống lại (có trong cơ sở dữ liệu đã khôi phục nhưng không có trong bản trước đó). **Chạy nó trước khi xóa bản `before-restore`:**

```bash
docker compose exec -T app python - <<'PY'
import glob, sqlite3
aside = sorted(glob.glob("/data/huayu.db.before-restore-*"))[-1]
alive_before = {row[0] for row in sqlite3.connect(aside).execute("SELECT public_id FROM users")}
for public_id, email in sqlite3.connect("/data/huayu.db").execute("SELECT public_id, email FROM users ORDER BY id"):
    if public_id not in alive_before:
        print(public_id, email)
PY
```

Với mỗi dòng in ra, người đó đã yêu cầu xóa dữ liệu nhưng nay tài khoản đang tồn tại lại. Bạn xóa lại giúp họ bằng lệnh quản trị `delete-user` (cùng đường xử lý với nút xóa trong Cài đặt: xóa mọi dữ liệu học của người đó, giữ lại các bản ghi thanh toán đã tách khỏi tài khoản):

```bash
docker compose exec app python -m app.cli delete-user --email EMAIL      # hỏi gõ DELETE (chữ hoa) để xác nhận; thêm --yes để bỏ câu hỏi
```

Lệnh từ chối xóa quản trị viên duy nhất (tạo quản trị viên khác trước). Nên báo cho người đó biết tài khoản đã được xóa lại. Lệnh liệt kê ở trên chỉ so với bản `before-restore` **mới nhất**, nên chạy ngay sau mỗi lần khôi phục.

Sau khi mọi thứ ổn, xóa bản `before-restore` (chứa dữ liệu cá nhân): `docker compose exec app ls -la /data` để xem, rồi `docker compose exec app sh -c 'rm -f /data/huayu.db.before-restore-*'`.

**Thử khôi phục mà không đụng tới dữ liệu thật.** `restore.sh` không có tùy chọn khôi phục vào một thư mục thử: nó luôn dừng `app` và thay cơ sở dữ liệu thật trong volume `huayu-data`. Vì vậy chỉ có hai cách thử thật sự:

1. **Trên một VPS thử (hoặc trước khi có dữ liệu thật):** chạy đúng lệnh `./deploy/restore.sh ...` ở đầu mục này. Đây là cách duy nhất thử cả quy trình Docker (dừng `app`, thay cơ sở dữ liệu, bật lại, chờ khỏe).
2. **Trên chính VPS đang chạy, chỉ để kiểm tra rằng bản sao lưu khôi phục được:** `deploy/restore_db.py` chỉ cần `python3` có sẵn trên VPS. Nó đọc bản sao lưu đã giải nén từ đầu vào chuẩn và ghi vào thư mục trong biến `HUAYU_DATA_DIR`. Nó **không có tùy chọn `--help`** và biến này mặc định là `/data` (nơi dữ liệu thật nằm trong container), nên **luôn đặt `HUAYU_DATA_DIR` sang một thư mục thử** như dưới đây:

   ```bash
   cd ~/huayu-coach
   mkdir -m 700 ~/restore-test
   gunzip -c ~/huayu-backups/huayu-AAAAMMDD-HHMMSS.db.gz | HUAYU_DATA_DIR=$HOME/restore-test python3 deploy/restore_db.py
   # kết quả đúng: restored /home/deploy/restore-test/huayu.db (migration revision ...)
   python3 -c "import os, sqlite3; print(sqlite3.connect(os.path.expanduser('~/restore-test/huayu.db')).execute('SELECT COUNT(*) FROM users').fetchone())"
   rm -r ~/restore-test      # thư mục này chứa dữ liệu cá nhân: xóa ngay khi xong
   ```

   Cách này kiểm tra tệp sao lưu (toàn vẹn SQLite, có các bảng của Huayu, có số phiên bản migration) và cho bạn đếm số dòng, nhưng **không chạy ứng dụng và không thử Docker**. Nó đã được chạy trên máy phát triển (Mac) với một bản sao lưu do `backup_db.py` tạo; **chưa chạy trên VPS**.

### 10.6 Không bao giờ chạy trên máy thật

> **Cơ sở dữ liệu của sinh viên nằm trong volume Docker `huayu-data`**, không nằm trong thư mục `~/huayu-coach`. Trên VPS nó ở `/var/lib/docker/volumes/huayu-data/_data` (xem `docker volume inspect huayu-data`). Các lệnh sau **xóa hoặc tách rời nó** và biến cả dữ liệu thành "cơ sở dữ liệu mới trống":
>
> - `docker compose down -v` (`-v` xóa volume; volume này khai báo `external` nên Compose theo tài liệu **không xóa** nó, nhưng đừng thử)
> - `docker volume rm huayu-data`, `docker volume prune`, `docker system prune --volumes`: `prune` xóa volume **không còn container nào dùng**, mà sau `docker compose down` container đã bị xóa. Từ Docker 23 `volume prune` mặc định chỉ xóa volume vô danh, nhưng có thêm `-a` hoặc trên bản cũ hơn thì xóa cả volume có tên. Đừng chạy các lệnh `prune` có liên quan tới volume.
> - đổi tên thư mục dự án mà **không** có `name:` trong `compose.yml` (Compose đặt tên volume theo tên thư mục). `compose.yml` đã đặt `name: huayu-coach` và volume tên cố định `huayu-data`, nên việc đổi tên thư mục không còn gây lỗi này, nhưng đừng đụng tới hai dòng đó.
>
> Chỉ một thứ bảo vệ bạn thật sự: **bản sao lưu đã được thử khôi phục và đã chép ra ngoài VPS** (10.1 đến 10.5). Caddy giữ chứng chỉ ở volume riêng `caddy-data`; mất nó chỉ cần xin lại, nhưng Let's Encrypt giới hạn số lần cấp lại mỗi tuần.

## 11. Cập nhật phiên bản mới **[CHƯA KIỂM THỬ]**

Mỗi lần cập nhật làm đủ các bước sau **theo thứ tự**; mục 12 (quay lui) chỉ dùng được nếu bước 2 và 3 đã làm.

1. Trên Mac: chạy test (`.venv/bin/python -m pytest`). Xem `migrations/versions/` có tệp mới không: nếu có, bản này **đổi cấu trúc cơ sở dữ liệu**, quay lui sẽ phải khôi phục dữ liệu (mục 12, trường hợp B).
2. Trên VPS, **sao lưu trước**: `cd ~/huayu-coach && ./deploy/backup.sh`. Dòng "backup written: ..." cho biết tên tệp: đó là **bản "trước khi cập nhật"**, ghi nó lại.
3. **Giữ image Docker đang chạy** bằng một thẻ riêng (trước khi build image mới, vì `docker compose up --build` chuyển thẻ `latest` sang image mới), và giữ luôn mã nguồn đang chạy:

   ```bash
   docker tag huayu-coach:latest huayu-coach:before-$(date +%Y%m%d-%H%M)
   docker images huayu-coach            # phải thấy cả thẻ latest lẫn thẻ before-...
   mkdir -p ~/releases && tar czf ~/releases/huayu-code-$(date +%Y%m%d-%H%M).tgz --exclude=.env --exclude=.venv --exclude=data -C ~ huayu-coach
   ```

4. Đưa mã mới lên theo cách bạn đã chọn ở mục 6: cách A thì chạy lại lệnh `rsync` trên Mac; cách B thì `cd ~/huayu-coach && git pull` trên VPS (nhớ là Mac phải đã `git push`).
5. `docker compose up -d --build` (migration tự chạy khi khởi động; vài giây ngừng phục vụ).
6. Kiểm tra `curl -sS https://huayu.example.com/healthz`, `docker compose logs --tail 50 app`, `docker compose exec app python -m app.cli check`, rồi đăng nhập thử.
7. **Đừng dọn ngay.** Sau vài ngày chạy ổn, xóa bớt thẻ cũ, chỉ giữ 2 đến 3 thẻ `before-...` gần nhất: `docker rmi huayu-coach:before-AAAAMMDD-HHMM`; xóa bớt `~/releases/*.tgz` cũ. `docker image prune -f` chỉ xóa ảnh **không có thẻ**, nên không động tới các thẻ `before-...`; còn `docker image prune -a` thì **xóa mất chúng**: đừng chạy.

## 12. Quay lui (rollback) **[CHƯA KIỂM THỬ]**

Migration chỉ đi tới (không có đường quay lại). Mã cũ chạy trên cơ sở dữ liệu đã lên phiên bản mới sẽ **không khởi động** (`prepare` báo `Can't locate revision`). Vì vậy quay lui có hai trường hợp.

**Trường hợp A: bản mới không đổi cấu trúc cơ sở dữ liệu** (không có tệp mới trong `migrations/versions/`): chỉ cần đổi lại image.

```bash
docker images huayu-coach            # chọn thẻ before-AAAAMMDD-HHMM của bản cần quay về
docker tag huayu-coach:before-AAAAMMDD-HHMM huayu-coach:latest
docker compose up -d --no-build --force-recreate app
docker compose logs --tail 50 app && curl -sS https://huayu.example.com/healthz
```

**Trường hợp B: bản mới đã đổi cấu trúc cơ sở dữ liệu, hoặc bạn không chắc, hoặc migration báo lỗi / `app` cứ khởi động lại.** Một migration lỗi có thể để cơ sở dữ liệu ở trạng thái dở dang: đừng sửa tay, hãy khôi phục bản sao lưu bước 2 của mục 11. **Thứ tự:** trước hết đổi ảnh về bản cũ, rồi mới khôi phục (để `restore.sh` bật lại đúng mã cũ):

```bash
docker tag huayu-coach:before-AAAAMMDD-HHMM huayu-coach:latest
./deploy/restore.sh ~/huayu-backups/huayu-AAAAMMDD-HHMMSS.db.gz     # bản "trước khi cập nhật" của mục 11 bước 2
docker compose logs --tail 50 app && curl -sS https://huayu.example.com/healthz
```

Ghi chú:

- `restore.sh` chạy được ngay cả khi `app` bản mới đang khởi động lại liên tục: nó dùng một container tạm chỉ chạy `restore_db.py` (không chạy migration). Lệnh `docker compose up` bình thường có thể báo `dependency failed to start ... unhealthy` khi mã cũ gặp cơ sở dữ liệu mới: đó là bình thường trước khi khôi phục.
- Dữ liệu sinh viên nhập **sau** bản sao lưu sẽ mất, và tài khoản bị xóa sau đó sống lại (mục 10.5): nên cập nhật vào lúc ít người dùng.
- Sau khi quay lui, thư mục mã trên VPS vẫn là bản mới. **Đừng chạy `docker compose up -d --build`** cho đến khi bạn đã sửa lỗi và đưa mã đúng lên, nếu không bản lỗi được dựng lại. Cần đối chiếu mã cũ thì giải nén `~/releases/huayu-code-*.tgz` vào một thư mục tạm.
- Nếu bạn chưa làm bước 2 hoặc 3 của mục 11, **không có đường quay lui**: chỉ còn cách sửa tiến (đưa bản sửa lỗi lên).

## 13. Bật thu phí (khi hết giai đoạn thử nghiệm)

Hiện chưa có cổng thanh toán: thanh toán là thủ công (sinh viên chuyển khoản, quản trị viên ghi nhận, hệ thống gia hạn quyền sử dụng).

1. Giá: các gói mặc định (199 và 799 NT$) chỉ là **giá giữ chỗ** nằm trong `DEFAULT_PLANS` của `app/services/billing.py`. Đặt giá thật ở **Quản trị → Gói và thanh toán** (`/admin/billing`): sửa ô **Giá (NT$)** của từng gói rồi lưu.
2. **Trước khi bật:** khi thu phí được bật, tài khoản nào chưa có gói hoặc đợt dùng thử còn hạn sẽ bị đưa sang trang **Gói của bạn** (`/billing`) và mất quyền học. Hãy tặng thêm ngày dùng cho các tài khoản đang học trong khu quản trị, và thử trước bằng một tài khoản thử.
3. Sửa `.env` (chú thích ở dòng riêng; nháy kép cho giá trị có dấu cách và ngoặc, hoặc nháy đơn nếu có `$`):

   ```ini
   HUAYU_BILLING_ENABLED=true
   HUAYU_TRIAL_DAYS=14
   HUAYU_PAYMENT_INSTRUCTIONS="Chuyển khoản tới ngân hàng ..., số tài khoản ..., chủ tài khoản ..."
   ```

   Chỉ viết thông tin tài khoản nhận tiền. Trang Gói của bạn (`/billing`) tự thêm dòng dặn người học ghi **mã người học** của họ (dạng `L...`) vào nội dung chuyển khoản; khi nhận tiền, bạn tìm người đó theo mã trong Quản trị > Người học rồi bấm Kích hoạt gói. Nếu `HUAYU_PAYMENT_INSTRUCTIONS` để trống, người học chỉ thấy "Thông tin thanh toán chưa được cập nhật".

4. `docker compose up -d` (tạo lại container với cấu hình mới; dữ liệu giữ nguyên).
5. Kiểm tra trang `/pricing` và `/terms`: trang tự chuyển từ "miễn phí trong giai đoạn thử nghiệm" sang "dùng thử {số ngày} ngày".
6. Phần "Hoàn tiền và rút lui" trong `/terms` còn là chỗ trống màu vàng: chủ sở hữu phải quy định chính sách (sửa khóa `legal.refund_placeholder` trong `app/locales/{vi,zh-TW,en}/marketing.json`, rồi `docker compose up -d --build`) và luật sư phải xem trước khi thu tiền thật. Việc đăng ký kinh doanh, hóa đơn và cổng thanh toán ở Đài Loan xem `README.md`.

## 14. Giám sát và cảnh báo **[CHƯA KIỂM THỬ]**

Bộ triển khai không tự báo cho bạn khi có sự cố; bạn cần ít nhất ba thứ miễn phí:

1. **Giám sát từ bên ngoài.** Dùng một dịch vụ kiểm tra thời gian hoạt động miễn phí (UptimeRobot, Better Stack, ...) gọi `https://huayu.example.com/healthz` mỗi vài phút và báo cho bạn qua email hoặc ứng dụng nhắn tin. Khuyên dùng kiểu **GET có kiểm từ khóa**: từ khóa `"ok":true`, vì trang lỗi của Caddy hay của nhà cung cấp không thể trả đúng từ khóa đó. Phiên bản hiện tại trả 200 cho cả `GET` và `HEAD` ở `/healthz`. Lưu ý `/healthz` chỉ chạy `SELECT 1`: **nó vẫn báo "ok" khi đĩa đầy hoặc volume chỉ đọc**, lúc đó mọi thao tác ghi đều lỗi 500. Vì vậy thêm bước dưới.
2. **Đĩa và bộ nhớ.** Xem `df -h /` và `free -h` mỗi tuần, hoặc bật cảnh báo dung lượng đĩa ở trang quản lý của nhà cung cấp VPS. Đĩa đầy làm cả `app` lẫn Caddy ngừng hoạt động.
3. **Sao lưu.** `BACKUP_PING_URL` (mục 10.1): dịch vụ báo khi bản sao lưu **không đến**, kể cả khi cron chết im lặng.

`app` bị treo (không trả lời) thì Docker chỉ đánh dấu `unhealthy` (xem `docker compose ps`) chứ **không tự khởi động lại**, và Caddy vẫn chuyển yêu cầu tới nó. Khi giám sát báo lỗi, chạy `docker compose restart app` rồi xem `docker compose logs --tail 100 app`.

## 15. Kiểm tra trước khi mời sinh viên đầu tiên

- [ ] `https://DOMAIN/healthz` trả `{"ok":true}`; `curl -s -D - -o /dev/null https://DOMAIN/ | grep -i strict-transport` có dòng HSTS.
- [ ] `docker compose exec app python -m app.cli check` in `base_url=https://DOMAIN` (một `https://`), `smtp=yes`, `env=prod`.
- [ ] Đã tạo quản trị viên, đăng nhập được `/admin`.
- [ ] Tự đăng ký một tài khoản học viên thử ở cả 3 ngôn ngữ: làm bài xếp trình độ, một bài hằng ngày, "Mình bị kẹt" (nhận một phần, nhận hết, không nhận), nút Nghỉ, xuất dữ liệu, xóa tài khoản.
- [ ] **Thư đặt lại mật khẩu thật sự đến được hộp thư**: bấm "Quên mật khẩu" bằng email thật của bạn và chờ thư. Ứng dụng luôn báo "nếu email có tài khoản thì đã gửi liên kết" dù thư có đi hay không, nên chỉ thử bằng hộp thư thật mới biết. Không thấy thư: `docker compose logs --tail 100 app` tìm `smtp send failed` (kiểm tra cặp cổng/`HUAYU_SMTP_SECURITY`, mục 7).
- [ ] Đã quyết định về câu hỏi: giáo viên đã duyệt, hoặc cố ý bật `HUAYU_ALLOW_UNREVIEWED_QUESTIONS=true`.
- [ ] `HUAYU_SUPPORT_EMAIL` đã đặt (trang quyền riêng tư dẫn sinh viên liên hệ qua địa chỉ này; nếu để trống, dòng liên hệ bị ẩn).
- [ ] `/privacy` và `/terms`: đã điền các ô vàng. Tên đơn vị vận hành, địa chỉ và mã số thuế điền bằng `HUAYU_OPERATOR_NAME`, `HUAYU_OPERATOR_ADDRESS`, `HUAYU_OPERATOR_TAX_ID` trong `.env` (rồi `docker compose up -d`). Chính sách hoàn tiền và tòa án (chỉ có trên `/terms`) **chưa điền được bằng `.env`**: sửa các khóa `legal.refund_placeholder` và `legal.court_placeholder` trong `app/locales/{vi,zh-TW,en}/marketing.json` rồi `docker compose up -d --build`. Email liên hệ lấy từ `HUAYU_SUPPORT_EMAIL`. **Luật sư đã xem** cả hai trang. Sau khi luật sư duyệt, xóa dòng "Bản nháp" (dòng `<p class="notice" role="note">...</p>` ngay dưới thẻ `<article ...>`) khỏi `app/templates/marketing/legal.html`, rồi dựng lại ảnh.
- [ ] Sao lưu chạy được, cron đã cài, `BACKUP_PING_URL` hoạt động (đã thử tắt cron một ngày xem có thư báo), đã thử khôi phục (`restore.sh` trên VPS thử hoặc trước khi có dữ liệu thật, hoặc ít nhất `restore_db.py` vào thư mục thử, mục 10.5; **không** thử `restore.sh` trên máy đang có sinh viên), đã chép bản sao lưu ra ngoài VPS **kèm vòng đời 14 ngày** (mục 10.4), đã cất `.env` ở nơi an toàn.
- [ ] Đã đọc mục 10.6 ("Không bao giờ chạy").
- [ ] SSH: đã tắt mật khẩu **sau khi** thử khóa (mục 2); ufw đã bật và SSH vẫn vào được.
- [ ] Không có bản ghi DNS AAAA (mục 5).
- [ ] Có swap nếu VPS chỉ có 1 GB RAM (mục 1).
- [ ] Đặt `HUAYU_INVITE_CODE` (đợt thử kín) hoặc đã chủ ý để mở đăng ký.
- [ ] Có dịch vụ giám sát bên ngoài gọi `/healthz` và gửi cảnh báo cho bạn (mục 14).
- [ ] Đã nói với sinh viên đây là bản thử nghiệm và cách liên hệ khi gặp lỗi.

## 16. Cách cài không dùng Docker (thay thế) **[CHƯA KIỂM THỬ]**

Chỉ dùng nếu bạn không muốn dùng Docker. Gồm: Python + uv, một người dùng hệ thống riêng, unit systemd và Caddy cài bằng apt. Các mục 1 đến 3 (cỡ VPS, SSH, ufw), 5 (DNS) và 9 đến 15 vẫn áp dụng, chỉ khác lệnh Docker (bên dưới). Nhật ký xem bằng `sudo journalctl -u huayu-coach`; journald có giới hạn dung lượng riêng của hệ thống (mặc định theo dung lượng đĩa, xem `journald.conf`).

Trên VPS (người dùng `deploy`):

```bash
sudo apt update && sudo apt -y install python3 curl rsync caddy     # nếu apt không có caddy: https://caddyserver.com/docs/install
curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh
sudo useradd --system --home-dir /opt/huayu-coach --shell /usr/sbin/nologin huayu
sudo mkdir -p /opt/huayu-coach
```

Trên Mac, trong thư mục dự án (đưa vào thư mục **của `deploy`**, không ghi thẳng vào `/opt`, vì `/opt/huayu-coach` sẽ thuộc về root):

```bash
rsync -av --delete --exclude .venv --exclude data --exclude .env --exclude __pycache__ ./ deploy@IP_CUA_VPS:~/huayu-release/
```

Trên VPS:

```bash
sudo rsync -a --delete --exclude .venv ~/huayu-release/ /opt/huayu-coach/
cd /opt/huayu-coach && sudo uv sync --frozen --no-dev --python /usr/bin/python3.12
sudo chown -R root:huayu /opt/huayu-coach && sudo chmod -R g+rX,o-rwx /opt/huayu-coach
sudo nano /etc/huayu-coach.env      # nội dung mẫu ngay bên dưới
sudo chown root:root /etc/huayu-coach.env && sudo chmod 600 /etc/huayu-coach.env
sudo cp /opt/huayu-coach/deploy/huayu-coach.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now huayu-coach
sudo cp /opt/huayu-coach/deploy/Caddyfile.systemd /etc/caddy/Caddyfile      # rồi sửa tên miền trong tệp
sudo caddy validate --config /etc/caddy/Caddyfile && sudo systemctl reload caddy    # lỗi ở timeouts/request_body: Caddy trong kho Ubuntu có thể cũ, cài từ kho của Caddy (liên kết ở trên)
sudo journalctl -u huayu-coach -f
```

**Tệp `/etc/huayu-coach.env` dùng cú pháp systemd, không giống `.env` của Docker.** Mẫu (không có chú thích sau giá trị, không có `export`, không có `DOMAIN`):

```ini
# Mỗi dòng NAME=giá_trị. Chú thích CHỈ ở dòng riêng như dòng này.
HUAYU_SECRET_KEY=dán-khóa-vừa-tạo-vào-đây
HUAYU_BASE_URL=https://huayu.example.com
HUAYU_SUPPORT_EMAIL=ban@example.com
HUAYU_OPERATOR_NAME="Tên đơn vị vận hành"
HUAYU_OPERATOR_ADDRESS="Địa chỉ đơn vị vận hành"
HUAYU_OPERATOR_TAX_ID=ma-so-thue
HUAYU_INVITE_CODE=mã-mời-bí-mật-cho-đợt-thử
HUAYU_BILLING_ENABLED=false
HUAYU_ALLOW_UNREVIEWED_QUESTIONS=true
HUAYU_SMTP_HOST=smtp.example.com
HUAYU_SMTP_PORT=587
HUAYU_SMTP_SECURITY=starttls
HUAYU_SMTP_USER=ten-dang-nhap
HUAYU_SMTP_PASSWORD='mat-khau-smtp'
HUAYU_SMTP_FROM=ban@example.com
HUAYU_PAYMENT_INSTRUCTIONS="Chuyển khoản tới ngân hàng ..., số tài khoản ..., chủ tài khoản ..."
```

Khác với `.env` của Docker:

- Docker chấp nhận chú thích sau giá trị (` # ...`); **systemd thì không**: phần chú thích trở thành một phần của giá trị (một biến đúng/sai khi đó hỏng, mã mời chứa cả chú thích).
- Không có `DOMAIN`: Compose tự ghép `HUAYU_BASE_URL` từ nó, ở đây bạn ghi `HUAYU_BASE_URL=https://...` trực tiếp (đúng một `https://`).
- Giá trị có dấu cách, ngoặc, `$` hay `#`: đặt trong nháy, đúng cả với systemd lẫn Docker.
- **Không** đặt `HUAYU_ENV`, `HUAYU_DATA_DIR`, `HUAYU_TRUSTED_PROXY` trong tệp này. systemd cho phép tệp `EnvironmentFile` ghi đè `Environment=`, nên unit đặt ba giá trị này ngay trên dòng lệnh (qua `env`), nơi tệp không ghi đè được. Một dòng `HUAYU_ENV=dev` còn sót từ `.env.example` vì thế không làm được gì.
- Quyền `600` chủ sở hữu `root`: systemd đọc tệp này bằng quyền root trước khi chuyển sang người dùng `huayu`.

**Các cờ bảo vệ trong unit và SQLite WAL** (đã xem xét theo tài liệu systemd, chưa chạy): cơ sở dữ liệu và hai tệp `-wal`, `-shm` của nó nằm trong `/var/lib/huayu-coach`; `StateDirectory=` tạo thư mục đó cho người dùng `huayu` và theo `systemd.exec(5)` được loại khỏi `ProtectSystem=strict`, nên không cần `ReadWritePaths=` (nếu bạn đổi `HUAYU_DATA_DIR` sang chỗ khác thì phải thêm `ReadWritePaths=` cho chỗ đó). `PrivateTmp=true` chỉ cho `/tmp` riêng để SQLite ghi tệp tạm; WAL không dùng `/tmp`. `PrivateDevices=true` vẫn có `/dev/urandom`. `MemoryMax=768M` là trần bộ nhớ tương đương `mem_limit` của Docker.

Kiểm tra cấu hình và tạo quản trị viên (dùng cùng cách đọc cấu hình mà dịch vụ dùng, thay vì nạp tệp vào bash, vốn lỗi với dấu ngoặc):

```bash
sudo systemd-run --pty --wait --collect -p User=huayu -p WorkingDirectory=/opt/huayu-coach -p EnvironmentFile=/etc/huayu-coach.env \
  /usr/bin/env HUAYU_ENV=prod HUAYU_DATA_DIR=/var/lib/huayu-coach HUAYU_TRUSTED_PROXY=true \
  /opt/huayu-coach/.venv/bin/python -m app.cli check
# create-admin: thay "check" bằng "create-admin --email ban@example.com" (hỏi mật khẩu hai lần)
# delete-user (xóa tài khoản theo yêu cầu): thay "check" bằng "delete-user --email EMAIL" (hỏi gõ DELETE để xác nhận)
```

**Sao lưu** (cùng `backup.sh`, chế độ `host`; chạy bằng root để nó chuyển sang người dùng `huayu`, nhờ vậy không tạo tệp SQLite thuộc nhầm chủ; bản sao lưu nằm ở `/root/huayu-backups`, mặc định quyền chỉ root, giữ 14 tệp):

```bash
sudo BACKUP_MODE=host /opt/huayu-coach/deploy/backup.sh     # chạy thử
sudo crontab -e                                             # rồi thêm (BACKUP_PING_URL tùy chọn, như mục 10.1):
```

```cron
BACKUP_MODE=host
BACKUP_PING_URL=https://hc-ping.com/UUID-CUA-BAN
15 3 * * * /opt/huayu-coach/deploy/backup.sh >> /root/huayu-backups/backup.log
```

**Khôi phục** (dừng dịch vụ, nạp bản sao lưu bằng `restore_db.py` với cùng các kiểm tra như bản Docker, bật lại; nếu `restore_db.py` từ chối tệp thì cơ sở dữ liệu giữ nguyên, **và bạn vẫn phải bật lại dịch vụ**):

```bash
sudo systemctl stop huayu-coach
gunzip -c /root/huayu-backups/huayu-AAAAMMDD-HHMMSS.db.gz | sudo -u huayu env HUAYU_DATA_DIR=/var/lib/huayu-coach python3 /opt/huayu-coach/deploy/restore_db.py
sudo systemctl start huayu-coach
```

(Cũng như bản Docker, khôi phục làm sống lại tài khoản đã xóa sau thời điểm sao lưu; mục 10.5 áp dụng, chỉ đổi `/data` thành `/var/lib/huayu-coach`, chạy bằng `sudo -u huayu python3 -`; để xóa lại các tài khoản đó dùng lệnh `delete-user` qua `systemd-run` như ở phần "Kiểm tra cấu hình và tạo quản trị viên" ngay trên. Thử khôi phục mà không đụng dữ liệu thật: `restore_db.py` với `HUAYU_DATA_DIR` trỏ vào thư mục thử, như mục 10.5.)

**Cập nhật và quay lui** (không có "image" để đổi thẻ, nên giữ nguyên một bản chép của thư mục mã, kèm môi trường ảo):

```bash
sudo BACKUP_MODE=host /opt/huayu-coach/deploy/backup.sh               # 1. sao lưu trước
sudo rsync -a --delete /opt/huayu-coach/ /opt/huayu-coach.prev/       # 2. giữ bản đang chạy
# 3. đưa mã mới lên ~/huayu-release/ như trên rồi:
sudo rsync -a --delete --exclude .venv ~/huayu-release/ /opt/huayu-coach/
cd /opt/huayu-coach && sudo uv sync --frozen --no-dev --python /usr/bin/python3.12
sudo chown -R root:huayu /opt/huayu-coach && sudo chmod -R g+rX,o-rwx /opt/huayu-coach
sudo systemctl restart huayu-coach && sudo journalctl -u huayu-coach -n 50
```

Quay lui: `sudo systemctl stop huayu-coach`, `sudo rsync -a --delete /opt/huayu-coach.prev/ /opt/huayu-coach/`; nếu bản mới đã đổi cấu trúc cơ sở dữ liệu (có tệp mới trong `migrations/versions/`) thì khôi phục bản sao lưu bước 1 bằng lệnh ở trên (cùng thứ tự: mã cũ trước, rồi dữ liệu); cuối cùng `sudo systemctl start huayu-coach`. Dữ liệu nằm ở `/var/lib/huayu-coach`; mục 10.6 (đừng xóa volume) trở thành: **đừng xóa `/var/lib/huayu-coach`** và đừng chạy `systemctl clean huayu-coach` với tham số `--what=state`.
