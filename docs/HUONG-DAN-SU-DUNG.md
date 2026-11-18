# Hướng dẫn sử dụng Huayu Coach (華語教練)

Tài liệu này dành cho **chủ sản phẩm / người vận hành** và cho cả ba nhóm người dùng của ứng dụng: học viên, giáo viên, quản trị viên.
Mọi tên nút và đường dẫn trong hướng dẫn lấy từ giao diện tiếng Việt thật của ứng dụng.

> **Độ tin cậy của tài liệu.** Các bước đã được đối chiếu với mã nguồn và chạy thử trên máy phát triển (có dữ liệu mẫu). Những phần **chưa từng được thử với dịch vụ thật** được đánh dấu **[CHƯA KIỂM THỬ]** ngay tại chỗ: gửi email qua SMTP thật, AI thật, đồng hồ tập trung trong trình duyệt thật, và mọi lệnh trên VPS (Docker, Caddy, ufw, SSH, cron, GitHub). Hãy thử các phần đó trước khi mời người dùng thật.

**Mục lục**

1. [Ứng dụng này làm gì](#1-ứng-dụng-này-làm-gì)
2. [Chạy thử trên máy cá nhân](#2-chạy-thử-trên-máy-cá-nhân)
3. [Dành cho học viên](#3-dành-cho-học-viên)
4. [Dành cho giáo viên: duyệt câu hỏi](#4-dành-cho-giáo-viên-duyệt-câu-hỏi)
5. [Dành cho quản trị viên](#5-dành-cho-quản-trị-viên)
6. [Cấu hình bằng tệp `.env`](#6-cấu-hình-bằng-tệp-env)
7. [Các lệnh quản trị](#7-các-lệnh-quản-trị)
8. [Đưa lên VPS](#8-đưa-lên-vps)
9. [Vận hành hằng ngày](#9-vận-hành-hằng-ngày)
10. [Danh sách kiểm tra trước khi mời sinh viên đầu tiên](#10-danh-sách-kiểm-tra-trước-khi-mời-sinh-viên-đầu-tiên)
11. [Xử lý sự cố thường gặp](#11-xử-lý-sự-cố-thường-gặp)
12. [Giới hạn hiện tại](#12-giới-hạn-hiện-tại)

---

## 1. Ứng dụng này làm gì

Huayu Coach giúp sinh viên quốc tế tại Đài Loan (ưu tiên người Việt) học tiếng Hoa ở mức A1–A2 mà không bị áp lực.

Ý tưởng cốt lõi: ứng dụng không nhắc thúc và không đoán xem bạn có đang bỏ học hay không. Khi người học thấy mình không học tiếp được, họ tự bấm **Mình bị kẹt** (trên menu, hoặc ở thẻ “Hôm nay chưa có hứng?” trong trang Hôm nay); lúc đó ứng dụng **hỏi vì sao** (4 lý do: quá khó, không có thời gian, cần nghỉ, hay xao nhãng). Sau đó một bộ quy tắc đề xuất **tối đa 3 điều chỉnh** kèm căn cứ, và người học quyết định: nhận một phần, nhận tất cả, hoặc giữ nguyên. **Kế hoạch học chỉ thay đổi khi người học xác nhận.**

Nói thật về công nghệ: **bộ quy tắc quyết định mọi con số**. AI là tùy chọn (mặc định tắt), chỉ viết lại phần giải thích và chỉ khi học viên đồng ý riêng; nó không thể thêm hay đổi một con số nào.

Có ba loại tài khoản (trong tài liệu này “học viên”, “người học” và “sinh viên” đều chỉ người dùng cuối):

| Vai trò | Làm được gì |
|---|---|
| **Học viên** (learner; trong khu quản trị gọi là **Người học**) | Học, báo "mình bị kẹt", quản lý kế hoạch, tải hoặc xóa dữ liệu của mình |
| **Giáo viên** (teacher) | Duyệt, sửa, nhập và xuất câu hỏi. Không thấy phần người học và tiền |
| **Quản trị viên** (admin) | Mọi thứ của giáo viên, cộng với người học, gói và thanh toán, nhật ký thao tác |

---

## 2. Chạy thử trên máy cá nhân

### 2.1 Cài công cụ (một lần)

Chỉ cần **uv**. Không cần Docker, máy chủ cơ sở dữ liệu hay khóa API.

```bash
brew install uv          # macOS. Cách khác: https://docs.astral.sh/uv/getting-started/installation/
```

### 2.2 Lấy mã nguồn

```bash
git clone https://github.com/huytue3107/huayu-coach.git
cd huayu-coach
```

Kho mã có thể ở chế độ **riêng tư** hoặc **công khai** (xem ở **Settings** của kho trên GitHub; bạn là người quyết định). Nếu kho đang riêng tư, bạn cần một tài khoản GitHub đã được mời vào kho: cài `brew install gh`, chạy `gh auth login`, rồi clone như trên hoặc dùng `gh repo clone huytue3107/huayu-coach`. **[CHƯA KIỂM THỬ]**: việc clone kho riêng tư chưa được thử trong lúc soạn tài liệu này.

### 2.3 Khởi động

```bash
./start.sh demo      # có sẵn 3 tài khoản mẫu và dữ liệu học để xem thử mọi màn hình
./start.sh           # bản sạch, không có tài khoản nào
```

Lệnh này tự làm: tạo `.env` từ `.env.example` nếu chưa có, cài thư viện, tạo cơ sở dữ liệu, nạp 50 câu hỏi mẫu, rồi chạy web tại **http://localhost:8000**. Dừng bằng `Ctrl+C`. Cửa sổ này đang chạy ứng dụng, nên hãy mở thêm một cửa sổ dòng lệnh khác (trong cùng thư mục dự án) cho các lệnh khác như `create-admin`.

- **Đổi cổng:** `PORT=9000 ./start.sh`. `start.sh` tự đặt `HUAYU_BASE_URL=http://localhost:9000` để liên kết đặt lại mật khẩu dùng đúng cổng. Nếu bạn muốn một địa chỉ khác, đặt biến ngay trong lệnh, ví dụ `HUAYU_BASE_URL=http://localhost:9000 ./start.sh`. Khi chạy bằng `start.sh`, dòng `HUAYU_BASE_URL` trong tệp `.env` luôn bị bỏ qua, vì `start.sh` tự đặt biến này mỗi lần chạy; chỉ giá trị gõ ngay trong lệnh mới đổi được.
- **Chỉ máy bạn mở được:** ứng dụng chỉ lắng nghe trên `127.0.0.1`, nên điện thoại hay máy khác trong cùng Wi-Fi không vào được.
- **Bản sạch (`./start.sh`) chưa có tài khoản nào, kể cả quản trị viên:** tạo bằng `uv run python -m app.cli create-admin --email ban@example.com` (xem [mục 5.5](#55-tạo-quản-trị-viên-đầu-tiên)) hoặc dùng `./start.sh demo`.

Tài khoản mẫu (mật khẩu chung `demo-password-1`, **chỉ dùng khi phát triển**):

| Email | Vai trò |
|---|---|
| `learner@huayu.local` | Học viên đăng ký cách đây 9 ngày, có 6 ngày học (4 ngày đã xong, 2 ngày gần nhất còn dở, nghỉ 3 ngày ở giữa) và 42 câu trả lời, ngữ pháp còn yếu. «Mình bị kẹt» cho đề xuất thật với cả 4 lý do; với «Mình thấy quá khó» có thêm mục ôn lại chủ đề 在 |
| `teacher@huayu.local` | Giáo viên |
| `admin@huayu.local` | Quản trị viên |

Dữ liệu nằm trong thư mục `data/` (tệp `huayu.db`). Muốn làm lại từ đầu: dừng ứng dụng (`Ctrl+C`), rồi chạy trong thư mục dự án:

```bash
rm -rf data/huayu.db* data/secret_key.txt data/outbox      # xóa cơ sở dữ liệu, khóa ký và các thư đã ghi; giữ lại data/.gitkeep
./start.sh demo
```

> Chạy thử trên máy không gửi được email thật. Khi chưa cấu hình SMTP, thư "quên mật khẩu" được ghi thành tệp trong `data/outbox/` (xem [mục 11](#11-xử-lý-sự-cố-thường-gặp)).

---

## 3. Dành cho học viên

Giao diện có 3 ngôn ngữ: Tiếng Việt, 繁體中文, English. Đổi bằng biểu tượng 🌐 ở đầu trang; lựa chọn được ghi nhớ cho tài khoản.

### 3.1 Đăng ký và thiết lập lần đầu

1. Bấm **Đăng ký** (trên menu hoặc ở trang chủ), nhập email và mật khẩu rồi bấm **Tạo tài khoản**.
   - Mật khẩu dài từ 8 đến 128 ký tự, không được trùng email và không được là mật khẩu quá phổ biến (ví dụ `12345678`, `password123`, `huayu123`); nếu vướng, trang báo “Mật khẩu này quá phổ biến và dễ đoán”.
   - Nếu người vận hành đã đặt **mã mời**, trang có thêm ô **Mã mời** (bắt buộc, phân biệt chữ hoa/chữ thường).
   - Nếu người vận hành đã đóng đăng ký, trang chỉ hiện “Hiện chưa mở đăng ký” và không có biểu mẫu.
2. Màn hình thiết lập là **một trang** gồm 3 phần:
   - **1. Ngôn ngữ của bạn**: bấm một nút ngôn ngữ thì trang tải lại ngay bằng ngôn ngữ đó, nên hãy chọn ngôn ngữ **trước** khi điền phần dưới (bài học tiếng Hoa luôn giữ nguyên tiếng Hoa).
   - **2. Về bạn**: tên gọi (không bắt buộc) và thời gian học mỗi ngày: 10, 15, 20, 30 hoặc 45 phút (mặc định chọn sẵn 20). Trang này chỉ hiện số phút; số bài tập và số câu ngữ pháp mới đi kèm bạn sẽ thấy ở trang Hôm nay và trang Kế hoạch:

     | Thời gian mỗi ngày | Số bài tập | Câu ngữ pháp mới (mục tiêu) |
     |---|---|---|
     | 10 phút | 4 | 1 |
     | 15 phút | 5 | 2 |
     | 20 phút | 6 | 3 |
     | 30 phút | 8 | 5 |
     | 45 phút | 10 | 6 |

   - **3. Dữ liệu của bạn, quyền của bạn**: ô đầu **bắt buộc** (cho phép lưu dữ liệu học để chạy kế hoạch). Ô thứ hai **không bắt buộc** và mặc định tắt: chỉ liên quan tới AI, xem [mục 6.4](#64-bật-ai-tùy-chọn).
3. Cuối trang bấm **Bắt đầu học**.

### 3.2 Bài kiểm tra đầu vào

Một bài ngắn (tối đa 10 câu) xác định điểm xuất phát A1 hay A2. Đây **không phải kỳ thi**, đoán tốt nhất cũng được. Kết quả hiển thị số câu đúng và trình độ; trang nói rõ nếu bài có ít câu nên chỉ là ước lượng.

Chỉ được xếp A2 khi đúng từ 80% trở lên và đúng hết các câu A2 (cần ít nhất 2 câu A2); với bộ 50 câu mẫu hiện nay mọi người đều ở A1. Nếu chưa có câu kiểm tra nào được phục vụ, trang hiện “Chưa có câu hỏi kiểm tra” và nút **Tiếp tục với trình độ A1**.

### 3.3 Trang «Hôm nay» và bài học hằng ngày

Trang **Hôm nay** cho biết số phút, số bài tập và số câu ngữ pháp mới của hôm nay. Bấm **Bắt đầu** (chưa làm), **Tiếp tục** (làm dở) hoặc **Xem tóm tắt hôm nay** (đã xong):

- Mỗi lần hiện một câu, bốn đáp án được **xáo trộn**. **Bấm vào một đáp án là nộp ngay** (không có nút xác nhận và không đổi lại được).
- Sau đó bạn thấy “Chính xác!” hoặc “Lần này chưa đúng”, đáp án đúng và mục **Vì sao** (nếu câu có lời giải thích), rồi bấm **Câu tiếp theo** (câu cuối là **Hoàn thành**).
- Làm xong, ứng dụng ghi nhận bài hôm nay đã hoàn thành. Không có chuỗi ngày, không có điểm phạt.
- Nếu chưa có câu nào phục vụ được cho bạn, trang chỉ ghi “Hiện chưa có bài tập nào cho bạn. Bạn quay lại sau nhé.”

Ví dụ 30 phút = 8 bài tập và khoảng 5 câu ngữ pháp mới (giao diện ghi “Điểm ngữ pháp mới: tối đa 5”). Số câu ngữ pháp mới là **mục tiêu của kế hoạch chứ không phải mức trần cứng**: khi ngân hàng câu hỏi còn nhỏ, bài hôm nay có thể có nhiều câu ngữ pháp chưa làm hơn để đủ số bài tập.

Nếu kế hoạch của bạn có dòng “Chia thành N buổi ngắn hơn (khoảng M phút mỗi buổi)” hoặc “Các khối tập trung M phút” (do bạn chấp nhận một gợi ý), trang Hôm nay hiện dòng đó, và trang làm bài có thêm đồng hồ tập trung đơn giản (“Khối tập trung: M phút”, nút “Bắt đầu đếm giờ” và “Đặt lại”). **[CHƯA KIỂM THỬ]**: nhãn và bố cục của đồng hồ đã được kiểm tra, nhưng phần đếm ngược (JavaScript) chưa được thử trong trình duyệt thật; hãy thử đồng hồ một lần trước khi hướng dẫn người dùng.

### 3.4 Khi bạn bị kẹt: «Mình bị kẹt»

Đây là tính năng chính. Mở **Mình bị kẹt** trên menu (hoặc đường liên kết ngay trong trang học):

1. Chọn một trong bốn lý do: **Mình thấy quá khó**, **Mình không có thời gian** (khi chọn lý do này mới hiện ô “Hôm nay bạn có khoảng bao nhiêu phút? (không bắt buộc)”, nhập số nguyên từ 5 đến 240), **Mình cần nghỉ ngơi**, **Mình hay bị xao nhãng**.
2. Có thể viết thêm ghi chú (không bắt buộc, tối đa 500 ký tự; dài hơn sẽ bị từ chối). Ghi chú là **riêng tư**: người quản trị không thấy, không dùng để tính gợi ý và không bao giờ gửi cho AI (xem lưu ý ở [mục 6.4](#64-bật-ai-tùy-chọn)).
3. Bấm **Xem các lựa chọn của mình**. Mỗi người báo được tối đa 20 lần trong 24 giờ.

Bạn sang trang **đề xuất**. Ở đó:

- Khi đề xuất còn đang chờ bạn quyết định, đầu trang có dòng: *"Chưa có gì thay đổi cả. Kế hoạch của bạn chỉ thay đổi khi bạn xác nhận."*
- Mỗi gợi ý là một thẻ có ô đánh dấu (**không đánh dấu sẵn**), mô tả điều chỉnh bằng số cũ → số mới, và mục **“Vì sao chúng mình gợi ý điều này”** với căn cứ thật: lý do bạn chọn, số liệu kế hoạch hiện tại, số câu ngữ pháp bạn trả lời đúng trong 14 ngày qua (nói bằng số câu, ví dụ “đúng 2 trên 8 câu”, không dùng phần trăm; nếu có ít hơn 5 câu, ứng dụng nói thẳng là quá ít để nhận xét và không đoán bừa), chủ đề bạn làm sai từ 2 câu trở lên, và việc hoàn thành bài của 3 ngày gần đây (khi có ít nhất 2 ngày).
- Ba nút: **Chấp nhận những mục đã chọn**, **Chấp nhận tất cả**, **Giữ nguyên kế hoạch hiện tại**.
- Muốn tự đặt số: liên kết **Muốn tự đặt các con số? Chỉnh kế hoạch của bạn**.

Các loại điều chỉnh có thể gặp: **Bớt điểm ngữ pháp mới**, **Bớt số bài tập**, **Rút ngắn thời gian học mỗi ngày**, **Chia nhỏ buổi học** (chia cùng thời gian thành 2 buổi ngắn), **Học theo từng đợt ngắn** (đợt tập trung ngắn, ví dụ 10 phút), **Nghỉ ngơi một chút** (1 ngày) và **Ôn lại một chủ đề còn khó**. Các đề xuất không bao giờ làm tăng số phút, số bài tập hay số điểm ngữ pháp mới mỗi ngày, và không có đề xuất nào được áp dụng khi bạn chưa chọn.

Sau khi quyết định bạn thấy trang kết quả: điều gì đã đổi (số cũ → số mới, phiên bản kế hoạch), điều gì được bỏ qua, và một phiếu phản hồi ngắn gồm 3 câu chấm từ 1 đến 5 (“Gợi ý có rõ ràng không?” 1 = Không rõ, 5 = Rất rõ; “Gợi ý có hữu ích không?” 1 = Không hữu ích, 5 = Rất hữu ích; “Bạn có thấy áp lực không?” 1 = Hoàn toàn không, 5 = Rất nhiều) cùng một ô nhận xét tối đa 500 ký tự; bấm **Gửi phản hồi**. Phiếu không bắt buộc, mỗi đề xuất chỉ gửi được một lần và phải chấm đủ cả 3 câu.

### 3.5 Nghỉ ngơi

Nút cam **Nghỉ** luôn hiện ở đầu trang khi bạn đã đăng nhập. Trang **Nghỉ ngơi một chút** cho chọn **1 ngày**, **3 ngày**, **7 ngày** hoặc **Cho đến khi mình quay lại**, rồi bấm **Bắt đầu nghỉ**. “1 ngày” nghĩa là nghỉ hết hôm nay, ngày học tiếp theo là ngày mai (trang ghi rõ ngày). Khi đang nghỉ, trang Hôm nay không giao bài và có nút **Mình sẵn sàng học lại**; trang Nghỉ ngơi một chút có nút **Mình quay lại rồi: học tiếp ngay**. Cả hai đều dừng quãng nghỉ ngay, kế hoạch giữ nguyên như lúc bạn đi. Chức năng nghỉ **không bao giờ bị chặn**, kể cả khi gói dùng thử hết hạn.

### 3.6 Kế hoạch học

Mục **Kế hoạch** cho xem kế hoạch hiện tại, số phiên bản, 30 thay đổi gần nhất kèm ai khởi xướng (“Bạn tự thay đổi”, “Một gợi ý bạn đã chấp nhận”, “Một gợi ý bạn đã chấp nhận một phần”, “Kế hoạch ban đầu của bạn”) và cho **tự chỉnh**: phút mỗi ngày (10 đến 90), điểm ngữ pháp mới (1 đến 10), số bài tập (3 đến 20), rồi bấm **Lưu kế hoạch của mình**. Điểm ngữ pháp mới không được nhiều hơn số bài tập. Nếu các con số y hệt kế hoạch hiện tại, trang báo không có gì được lưu. Đây là kế hoạch của bạn nên bạn muốn tăng hay giảm đều được. Khi bạn tự sửa, các đề xuất đang chờ cũ bị đóng lại vì đã tính trên số liệu cũ.

### 3.7 Tiến độ và bài tự kiểm tra

- **Tiến độ**: 7 ngày gần nhất (tính cả hôm nay). Số câu trả lời và độ chính xác theo kỹ năng (tính cả câu của bài kiểm tra đầu vào hoặc bài tự kiểm tra nếu bạn làm trong 7 ngày đó; chỉ hiện phần trăm khi có ít nhất 5 câu), số bài hằng ngày đã hoàn thành, các lần bạn báo bị kẹt, thay đổi kế hoạch. Khung “7 ngày qua của bạn” ở trang Hôm nay chỉ tính bài hằng ngày nên số có thể nhỏ hơn.
- **Bài tự kiểm tra sau 7 ngày** mở vào ngày thứ 7 sau khi bạn làm bài kiểm tra đầu vào (trang Tiến độ ghi ngày mở; trước đó trang bài tự kiểm tra ghi “Chưa đến lúc”). Khi đã mở, vào bằng thẻ **Làm bài tự kiểm tra** ở trang Hôm nay hoặc liên kết **Bài tự kiểm tra đã sẵn sàng. Làm bài ngay** ở trang Tiến độ. Kết quả so sánh số câu đúng **Lúc bắt đầu** và **Bây giờ** (số câu của hai bài có thể khác nhau, ví dụ 4/8 và 3/9). Nếu chưa có câu hỏi giai đoạn “Bài kiểm tra cuối” nào được phục vụ, trang báo “Chưa có câu hỏi tự kiểm tra”. Đây là bài tự kiểm tra ngắn, **không phải kết quả chính thức**.

### 3.8 Cài đặt, quyền riêng tư và xóa tài khoản

Mục **Cài đặt**:

- Đổi ngôn ngữ, tên gọi, múi giờ. **Múi giờ** mặc định của tài khoản mới là Đài Bắc (Asia/Taipei) và phần thiết lập ban đầu không hỏi múi giờ. Ngày học, bài của hôm nay, quãng nghỉ “1 ngày” và ngày mở bài tự kiểm tra đều tính theo múi giờ này và đổi ngày lúc nửa đêm. Học viên ở nơi khác (ví dụ Việt Nam, chậm hơn Đài Bắc 1 giờ) nên chọn lại ở **Cài đặt → Múi giờ** (5 lựa chọn: Đài Loan (Đài Bắc), Việt Nam (TP. Hồ Chí Minh), Thái Lan (Bangkok), Nhật Bản (Tokyo), UTC). Giờ hiển thị trong khu quản trị tính theo biến `HUAYU_TIMEZONE` (mặc định Asia/Taipei).
- **Đổi mật khẩu** (cần mật khẩu hiện tại). Các thiết bị khác bị đăng xuất, thiết bị hiện tại vẫn đăng nhập.
- Bật hoặc tắt đồng ý gửi số liệu ẩn danh cho AI (chỉ có tác dụng nếu người vận hành bật AI).
- **Tải dữ liệu của mình**: một tệp JSON chứa mọi thứ ứng dụng lưu về bạn (không có mật khẩu), kể cả những gì nhân viên đã ghi chú về bạn.
- **Xóa tài khoản**: nhập mật khẩu, gõ `DELETE` (không phân biệt chữ hoa/chữ thường) rồi bấm **Xóa tất cả**. Thao tác **xóa vĩnh viễn** tài khoản và toàn bộ dữ liệu học, không hoàn tác được. Chỉ còn lại các bản ghi thanh toán (giữ cho kế toán, không còn liên kết với bạn và đã xóa ghi chú) và một dòng tối thiểu “account_deleted” trong Nhật ký thao tác; các dòng nhật ký cũ về bạn bị gỡ ghi chú và mã người học. Quản trị viên duy nhất không thể tự xóa tài khoản.

**Quên mật khẩu**: ở trang đăng nhập bấm **Quên mật khẩu?**, nhập email, bấm **Gửi liên kết đặt lại**. Với một email đúng định dạng, ứng dụng luôn trả lời như nhau dù email có tồn tại hay không. Liên kết trong thư có hiệu lực **60 phút** và chỉ dùng được một lần; sau khi đặt mật khẩu mới, mọi thiết bị bị đăng xuất và bạn đăng nhập lại bằng mật khẩu mới. **[CHƯA KIỂM THỬ]** việc gửi thư qua SMTP thật: chỉ chế độ ghi thư thành tệp (khi chưa có SMTP) đã được thử.

---

## 4. Dành cho giáo viên: duyệt câu hỏi

Đăng nhập bằng tài khoản giáo viên (hoặc quản trị viên) rồi vào **Quản trị → Câu hỏi**. Giáo viên chỉ thấy **Tổng quan** (phần ngân hàng câu hỏi) và **Câu hỏi**; các mục Người học, Gói và thanh toán, Nhật ký thao tác trả về “Không có quyền truy cập”.

**Tạo tài khoản giáo viên.** Không có màn hình tạo giáo viên riêng. Người đó đăng ký như một học viên bình thường (có mã mời nếu bạn đã đặt; không cần làm phần thiết lập học), rồi quản trị viên vào **Quản trị → Người học**, tìm email của họ, mở hồ sơ, ở khung **Vai trò** chọn **Giáo viên** và bấm **Lưu vai trò**. Vai trò có hiệu lực ngay, kể cả khi họ đang đăng nhập, và từ đó họ vào thẳng khu **Quản trị**. Muốn thêm quản trị viên thì làm tương tự và chọn **Quản trị viên**.

### 4.1 Nguyên tắc phục vụ câu hỏi

| Trạng thái | Học viên có thấy không |
|---|---|
| **Đã duyệt** | Được phục vụ, nhưng chỉ đúng giai đoạn và trình độ của học viên: bài kiểm tra đầu vào, bài hằng ngày và bài tự kiểm tra mỗi loại dùng câu thuộc giai đoạn của nó; học viên A1 chỉ nhận câu A1 |
| **Chờ duyệt** | Chỉ thấy khi cài đặt `HUAYU_ALLOW_UNREVIEWED_QUESTIONS=true` (mặc định bật khi phát triển, **tắt khi chạy thật**) |
| **Cần sửa** | **Không bao giờ** thấy |

Đổi `HUAYU_ALLOW_UNREVIEWED_QUESTIONS` trong `.env` chỉ có tác dụng sau khi **khởi động lại** ứng dụng.

Vì vậy khi chạy thật, **chưa duyệt câu nào thì học viên không có bài tập**. Bộ 50 câu mẫu đều là bản nháp của sinh viên, chưa có câu nào được giáo viên duyệt, và 6 câu đã được đánh dấu **Cần sửa** vì có hơn một đáp án hợp lý.

**Duyệt đủ cả ba giai đoạn.** Ở chế độ chạy thật, học viên chỉ thấy câu đã duyệt đúng giai đoạn của nó:

- **Bài kiểm tra đầu vào** (`pretest`): nếu chưa duyệt câu nào, học viên được xếp A1 ngay (nút “Tiếp tục với trình độ A1”) và muốn lên A2 cần ít nhất 2 câu A2 được duyệt.
- **Luyện tập hằng ngày** (`daily`): cần đủ câu **A1** cho học viên A1, nếu không trang Hôm nay ghi “Hiện chưa có bài tập nào cho bạn”.
- **Bài kiểm tra cuối** (`posttest`): nếu chưa duyệt câu nào, sau 7 ngày trang Tiến độ vẫn hiện “Bài tự kiểm tra đã sẵn sàng. Làm bài ngay” nhưng bấm vào chỉ thấy “Chưa có câu hỏi tự kiểm tra”.

Dùng bộ lọc **Giai đoạn**, **Trình độ** và **Trạng thái duyệt** ở Quản trị → Câu hỏi để kiểm tra từng nhóm trước khi mời học viên.

### 4.2 Duyệt từng câu

1. Dùng bộ lọc (tìm theo mã, câu hỏi hoặc chủ đề; giai đoạn; kỹ năng; trình độ; trạng thái duyệt; chủ đề) rồi bấm **Lọc**; **Xóa lọc** để bỏ.
2. Mở một câu để xem và sửa: câu hỏi tiếng Hoa, bốn đáp án (không được trùng nhau), đáp án đúng, giải thích (vi / zh-TW / en), chủ đề, trình độ, kỹ năng, giai đoạn và **ghi chú duyệt** cho người sau. Sửa xong bấm **Lưu** ở cuối biểu mẫu.
3. Đặt trạng thái bằng khung **Duyệt câu hỏi** ở đầu trang: **Duyệt** (thành *Đã duyệt*), **Cần sửa**, hoặc **Đưa về chờ duyệt**. Khung này là một biểu mẫu riêng: bấm nút trạng thái **không lưu** những chỗ bạn đang sửa dở, nên hãy **Lưu trước rồi mới đặt trạng thái**. Sửa một câu đang *Cần sửa* không tự đổi trạng thái: bạn phải bấm Duyệt hoặc Đưa về chờ duyệt. Ở danh sách mỗi dòng có hai nút nhanh **Duyệt** và **Cần sửa**; **Đưa về chờ duyệt** chỉ có trong trang sửa. Ứng dụng ghi người đặt trạng thái và thời điểm.
4. **Sửa nội dung của một câu đã duyệt** (câu hỏi, đáp án, đáp án đúng, giải thích, chủ đề, trình độ, kỹ năng, giai đoạn) sẽ tự đưa nó về **Chờ duyệt** để duyệt lại (ứng dụng không bắt buộc phải là người khác duyệt). Chỉ sửa **ghi chú duyệt** thì trạng thái giữ nguyên.

Nút **Thêm câu hỏi** mở một biểu mẫu trống: điền câu hỏi, bốn đáp án, chọn đáp án đúng rồi bấm **Lưu** để tạo câu mới. Mã của câu mới là `Q` + số lớn nhất đang có cộng 1 (ví dụ sau Q051 là Q052), và câu luôn ở trạng thái **Chờ duyệt**.

### 4.3 Nhập và xuất hàng loạt

Ở trang Câu hỏi, mục **Nhập và xuất**:

- **Tải xuống tất cả câu hỏi (JSON)** cho tệp JSON (gồm cả câu *Cần sửa*) có cùng cấu trúc với `seeds/questions.json`, thêm trường `explanation_en`. Khi nhập lại một tệp như vậy, `review_status` trong tệp bị bỏ qua: câu mới luôn vào ở trạng thái **Chờ duyệt**.
- **Nhập tệp** nhận tệp JSON là một danh sách tối đa **500 câu** và dung lượng tối đa **1 MB**. Mã đã tồn tại bị bỏ qua; trang kết quả liệt kê từng mục không hợp lệ (tối đa 50 mục đầu) kèm lý do.

Tệp phải là một **danh sách** (đặt trong dấu `[ ]`); mỗi phần tử là một câu hỏi. Ví dụ tệp có một câu:

```json
[
  {
    "id": "Q051",
    "stage": "daily",
    "level": "A1",
    "skill": "grammar",
    "topic": "在",
    "question_zh": "我___教室上課。",
    "options": ["在", "是", "有", "要"],
    "correct_index": 0,
    "explanation_vi": "「在」 chỉ nơi chốn: 在教室 = ở trong lớp."
  }
]
```

Nếu tệp chỉ chứa một đối tượng `{ ... }` không có `[ ]`, trang báo “Tệp phải chứa một danh sách câu hỏi. Chưa có gì được nhập.”

Mỗi phần tử **bắt buộc** có: `id` (1 đến 12 ký tự gồm chữ, số, `_` hoặc `-`), `stage`, `level`, `skill`, `question_zh`, `options`, `correct_index`. Các trường `topic`, `explanation_vi`, `explanation_zh`, `explanation_en` và `review_note` là tùy chọn (nên có `explanation_vi` để học viên thấy mục “Vì sao”). `stage` là `pretest` / `daily` / `posttest`; `level` là `A1` / `A2`; `skill` là `vocab` / `grammar` / `reading`; `options` đúng 4 đáp án khác nhau; `correct_index` từ 0 đến 3. Độ dài tối đa: câu hỏi 500 ký tự, mỗi đáp án 200, chủ đề 30, mỗi lời giải thích 1000. Tệp lớn hơn khoảng 1 MB bị từ chối (đôi khi chỉ hiện chữ `too_large`). **Không viết chữ cái đáp án (A, B…) trong giải thích**, vì thứ tự đáp án bị xáo trộn khi hiển thị.

---

## 5. Dành cho quản trị viên

Đăng nhập rồi bấm **Quản trị** trên menu. Thanh phụ có 5 mục: **Tổng quan**, **Câu hỏi**, **Người học**, **Gói và thanh toán**, **Nhật ký thao tác**.

### 5.1 Tổng quan

Mọi con số tính từ hành động thật của người học, **kèm số mẫu `n` đằng sau mỗi điểm trung bình**; chưa có dữ liệu thì ghi "Chưa có dữ liệu", không ghi 0%.

- Số người học, số người hoạt động 7 ngày qua.
- **Kết quả thử nghiệm** (đúng các mục tiêu trong bài thuyết trình): “Đề xuất dễ hiểu” và “Đề xuất hữu ích” (trung bình 1 đến 5, mục tiêu từ 4), “Áp lực cảm nhận” (càng thấp càng tốt), “Đề xuất đã được đánh giá và người học nhận ít nhất một phần” (chỉ tính trong các đề xuất đã có phiếu đánh giá) và “Người học quay lại sau khi dừng học (đã học lại / tất cả)” (tính theo từng lần báo kẹt; “đã học lại” nghĩa là người học hoàn thành trọn một bài hằng ngày trong 7 ngày sau khi báo).
- Vì sao người học dừng lại (theo 4 lý do), kết quả các đề xuất, số gói theo trạng thái, tình trạng ngân hàng câu hỏi.

### 5.2 Người học

Tìm theo mã hoặc email. Mở một người học để xem hồ sơ, kế hoạch, lịch sử gói và thanh toán, các lần báo kẹt (chỉ thấy **lý do và trạng thái**, không bao giờ thấy ghi chú riêng tư). Các thao tác:

- **Tặng ngày** (1 đến 365 ngày dùng miễn phí, kèm ghi chú).
- **Kích hoạt gói**: dùng **sau khi nhận chuyển khoản**. Chọn gói, nhập số tiền nếu khác giá niêm yết, ghi chú (ví dụ mã giao dịch). Ứng dụng ghi một khoản thanh toán và cộng thời hạn. Cách tìm đúng người học từ một khoản chuyển khoản: xem [mục 6.3](#63-bật-thu-phí).
- **Lưu vai trò**: chọn **Người học**, **Giáo viên** hoặc **Quản trị viên** rồi bấm Lưu vai trò; có hiệu lực ngay cả khi người đó đang đăng nhập. Không tự đổi vai trò của chính mình; không hạ cấp quản trị viên cuối cùng.
- **Kết thúc mọi phiên**: đăng xuất người đó khỏi mọi thiết bị. Lệnh này **không đổi mật khẩu**: người đó vẫn đăng nhập lại được bằng mật khẩu cũ. Nếu nghi mật khẩu bị lộ, hãy yêu cầu họ đặt lại bằng **Quên mật khẩu?** hoặc đổi mật khẩu trong Cài đặt (đặt lại bằng liên kết đăng xuất mọi thiết bị; đổi trong Cài đặt đăng xuất các thiết bị khác).
- **Cho làm lại bài kiểm tra**: xóa trình độ, dấu “đã làm xong bài kiểm tra đầu vào” **và cả các câu trả lời bài kiểm tra đầu vào trước đó** của người này, để họ làm lại **từ đầu cả bài**. Lịch sử luyện tập hằng ngày được giữ nguyên. Số câu trả lời đã xóa được ghi trong Nhật ký thao tác (`placement_reset`). Mốc 7 ngày của bài tự kiểm tra cũng tính lại từ lần làm bài mới (nếu họ chưa làm bài tự kiểm tra). Dùng khi họ bị xếp A1 trong lúc chưa có câu kiểm tra nào được phục vụ (sau khi bạn đã duyệt câu giai đoạn “Bài kiểm tra đầu vào”), hoặc khi cần cho làm lại bài.

**Tặng ngày và Kích hoạt gói không hoàn tác được.** Không có nút hủy, hoàn tiền hay rút bớt ngày. Mỗi lần bấm tạo thêm một dòng gói (và một khoản thanh toán nếu là Kích hoạt gói) và được **cộng dồn** vào ngày hết hạn hiện tại của học viên (ví dụ tặng 1, 2 rồi 365 ngày là cộng thêm 368 ngày). Hãy kiểm tra số ngày, gói và số tiền trước khi bấm; nhập nhầm chỉ sửa được bằng cách can thiệp thẳng vào cơ sở dữ liệu trên máy chủ. Hai thao tác này chỉ dùng được cho tài khoản **Người học**, và chỉ ảnh hưởng quyền truy cập khi bạn đã bật `HUAYU_BILLING_ENABLED=true` (nếu chưa bật, mọi người đang miễn phí). Một thao tác y hệt (cùng người, cùng gói và số tiền, hoặc cùng số ngày) trong vòng 1 phút bị từ chối để tránh ghi hai lần.

Không có chức năng xem mật khẩu hay đăng nhập thay người khác.

### 5.3 Gói và thanh toán

Xem và sửa các gói (tên theo từng ngôn ngữ, giá NT$, số ngày, ô **Mở cho người học**), **Thêm gói**, xem 50 thanh toán gần đây và tổng tiền theo tháng. **Giá có sẵn (NT$199 mỗi tháng, NT$799 mỗi học kỳ) chỉ là giá giữ chỗ; hãy đặt giá thật của bạn trước khi mời người dùng.** Các gói đang “Mở cho người học” được hiện công khai ở trang `/pricing` (kể cả khi thu phí đang tắt); muốn ẩn một gói, bỏ chọn “Mở cho người học”. Gói không xóa được, và mã gói không đổi được sau khi tạo.

### 5.4 Nhật ký thao tác

Mọi thao tác quản trị được ghi lại, mới nhất ở trên cùng (50 dòng mỗi trang): giờ, **ai** (mã người học dạng `L…`; tra email ở mục Người học), **thao tác** (mã tiếng Anh: `question_review` = duyệt/đổi trạng thái, `question_edited` = sửa câu hỏi, `question_created` = thêm câu hỏi, `questions_imported` = nhập tệp, `access_granted` = tặng ngày, `subscription_activated` = kích hoạt gói, `role_changed` = đổi vai trò, `sessions_ended` = kết thúc mọi phiên, `placement_reset` = cho làm lại bài kiểm tra, `plan_created` / `plan_saved` = thêm/sửa gói, `account_deleted` = tài khoản bị xóa), **đối tượng** và **chi tiết**. Ghi chú bạn nhập khi tặng ngày hoặc kích hoạt gói cũng nằm trong nhật ký và có trong tệp dữ liệu học viên tải về, nên đừng ghi thông tin nhạy cảm.

### 5.5 Tạo quản trị viên đầu tiên

Quản trị viên đầu tiên **không** đăng ký qua web mà tạo bằng lệnh (trên máy cá nhân chạy trong thư mục dự án; trên VPS xem [mục 8](#8-đưa-lên-vps), bước 9):

```bash
uv run python -m app.cli create-admin --email ban@example.com    # địa chỉ MỚI: hỏi mật khẩu hai lần
```

Lệnh hoạt động theo hai trường hợp:

| Địa chỉ email | Lệnh làm gì |
|---|---|
| **Chưa có tài khoản** | Tạo tài khoản quản trị viên mới và **cần mật khẩu** (lệnh hỏi hai lần, hoặc đọc biến `HUAYU_ADMIN_PASSWORD`) |
| **Đã có tài khoản** | **Chỉ nâng quyền** thành quản trị viên; **mật khẩu và các phiên đăng nhập hiện có giữ nguyên** |

Muốn đặt mật khẩu mới cho một tài khoản đã có (ví dụ quản trị viên quên mật khẩu), thêm `--reset-password`:

```bash
uv run python -m app.cli create-admin --email ban@example.com --reset-password    # hỏi mật khẩu mới hai lần
```

Khi đó mật khẩu cũ không còn dùng được và người đó bị đăng xuất khỏi mọi thiết bị. Lệnh còn nhận `--password MAT-KHAU`, nhưng đừng dùng: mật khẩu sẽ nằm lại trong lịch sử lệnh của máy. Lưu ý: với tài khoản đã có mà **không** thêm `--reset-password`, cả `--password` lẫn biến `HUAYU_ADMIN_PASSWORD` đều bị bỏ qua; lệnh chỉ nâng quyền và in `(password unchanged)`. Muốn đổi mật khẩu thì luôn thêm `--reset-password`. Muốn nâng quyền một người mà không dùng dòng lệnh: **Quản trị → Người học →** mở người đó **→ Lưu vai trò**.

---

## 6. Cấu hình bằng tệp `.env`

Sao chép `.env.example` thành `.env` rồi sửa. Hầu hết biến trong `.env` bắt đầu bằng `HUAYU_`; ngoại lệ duy nhất là `DOMAIN` (chỉ dùng trong `.env` của Docker Compose). `PORT` (cho `start.sh`) và `BACKUP_DIR`, `KEEP`, `BACKUP_PING_URL`, `BACKUP_MODE` (cho `deploy/backup.sh`) **không** đặt trong `.env`: đó là biến môi trường, gõ ngay trước lệnh (ví dụ `PORT=9000 ./start.sh`) hoặc ghi ở đầu tệp crontab (xem mục 10.1 của `deploy.md`). Đặt chúng trong `.env` thì không có tác dụng. Khi chạy bằng Docker, `compose.yml` tự đặt `HUAYU_ENV=prod`, `HUAYU_DATA_DIR`, `HUAYU_TRUSTED_PROXY` và `HUAYU_BASE_URL=https://` + `DOMAIN`, nên các dòng này trong `.env` (kể cả `HUAYU_ENV=dev` còn sót từ `.env.example`) bị bỏ qua. Bảng đầy đủ nằm trong `README.md`; sau đây là những biến bạn sẽ dùng thường xuyên. Đổi `.env` chỉ có tác dụng sau khi khởi động lại ứng dụng (máy cá nhân: `Ctrl+C` rồi `./start.sh`; Docker: `docker compose up -d`).

| Biến | Ý nghĩa |
|---|---|
| `HUAYU_ENV` | `dev` (máy cá nhân) hoặc `prod` (chạy thật). `prod` bắt buộc có khóa bí mật dài và địa chỉ https |
| `HUAYU_SECRET_KEY` | Khóa ký phiên đăng nhập, **tối thiểu 32 ký tự**, bắt buộc khi `prod`. Tạo: `python3 -c "import secrets;print(secrets.token_urlsafe(48))"`. Đổi khóa là đăng xuất mọi người |
| `HUAYU_BASE_URL` | Địa chỉ công khai, ví dụ `https://huayu.example.com` (dùng trong thư đặt lại mật khẩu) |
| `HUAYU_INVITE_CODE` | Nếu đặt, chỉ ai có mã mới đăng ký được (khuyên dùng cho đợt thử) |
| `HUAYU_REGISTRATION_OPEN` | `false` để đóng đăng ký |
| `HUAYU_SUPPORT_EMAIL` | Email liên hệ hiện trên trang quyền riêng tư (**nên đặt**: đây là nơi người dùng gửi yêu cầu về dữ liệu) |
| `HUAYU_OPERATOR_NAME`, `HUAYU_OPERATOR_ADDRESS`, `HUAYU_OPERATOR_TAX_ID` | Tên, địa chỉ và mã số thuế (統一編號) của đơn vị vận hành, hiện trên **/privacy** và **/terms** (xem [mục 6.5](#65-thông-tin-đơn-vị-vận-hành-trang-privacy-và-terms)). Giá trị có dấu cách đặt trong nháy kép |
| `HUAYU_ALLOW_UNREVIEWED_QUESTIONS` | `true` để phục vụ cả câu chưa duyệt (xem [mục 4.1](#41-nguyên-tắc-phục-vụ-câu-hỏi)) |
| `HUAYU_BILLING_ENABLED`, `HUAYU_TRIAL_DAYS`, `HUAYU_PAYMENT_INSTRUCTIONS` | Thu phí (xem 6.3) |
| `HUAYU_SMTP_*` | Gửi email (xem 6.2) |
| `HUAYU_LLM_*` | AI tùy chọn (xem 6.4) |

### 6.1 Chế độ mời

Đặt `HUAYU_INVITE_CODE=mã-bí-mật` rồi phát mã cho người được mời. Trang đăng ký sẽ có thêm ô **Mã mời**.

### 6.2 Gửi email (đặt lại mật khẩu)

```ini
HUAYU_SMTP_HOST=smtp.example.com
HUAYU_SMTP_PORT=587
HUAYU_SMTP_SECURITY=starttls
HUAYU_SMTP_USER=ten-dang-nhap
HUAYU_SMTP_PASSWORD='mat-khau'
HUAYU_SMTP_FROM=ban@example.com
```

Cổng 587 đi với `starttls`; cổng 465 đi với `ssl`; `none` chỉ cho máy chủ thư nội bộ đáng tin. Ứng dụng **kiểm tra chứng chỉ TLS** của máy chủ thư. Nếu không cấu hình SMTP, thư **không được gửi đi** mà chỉ được ghi thành tệp trong `data/outbox/`. Hãy cấu hình trước khi mời người dùng thật. Dấu nháy đơn quanh mật khẩu là cần thiết khi mật khẩu có ký tự `$` hoặc `#`.

Nếu cặp cổng và `HUAYU_SMTP_SECURITY` sai, hoặc sai mật khẩu, thư không đi (ứng dụng chờ tối đa 15 giây rồi bỏ), nhưng người dùng **vẫn thấy thông báo "đã gửi"** (cố ý, để không lộ email nào có tài khoản); dấu vết duy nhất là dòng `smtp send failed` trong nhật ký. **[CHƯA KIỂM THỬ]** với SMTP thật: hãy bấm **Quên mật khẩu?** bằng một email thật của bạn và chờ thư đến trước khi mời người dùng.

### 6.3 Bật thu phí

Mặc định `HUAYU_BILLING_ENABLED=false`: mọi người dùng **miễn phí** ("giai đoạn thử nghiệm"). Dù thu phí đang tắt, khi ai đó đăng ký ứng dụng **vẫn ghi sẵn** một đợt dùng thử (`HUAYU_TRIAL_DAYS` ngày tại thời điểm đăng ký, mặc định 14); đợt này chỉ có tác dụng khi bạn bật thu phí, và đổi `HUAYU_TRIAL_DAYS` sau đó không làm đổi đợt đã ghi. Khi bật `HUAYU_BILLING_ENABLED=true`:

- Hết hạn mà chưa có gói: mọi trang học (Hôm nay, Tiến độ, Kế hoạch, Mình bị kẹt, các bài kiểm tra) chuyển sang trang **Gói của bạn** (**/billing**: xem gói, giá và hướng dẫn thanh toán bạn viết trong `HUAYU_PAYMENT_INSTRUCTIONS`, dòng “Thời gian dùng miễn phí đã hết”). Vẫn luôn dùng được: **Nghỉ** (và nút quay lại học), **Cài đặt**, **Tải dữ liệu của mình**, **Xóa tài khoản** và chính trang **/billing**. Quản trị viên và giáo viên không bao giờ bị chặn.
- **Viết hướng dẫn thanh toán** trước khi bật: điền `HUAYU_PAYMENT_INSTRUCTIONS` (số tài khoản, ngân hàng); nếu để trống, người học chỉ thấy "Thông tin thanh toán chưa được cập nhật".
- **Từ khoản chuyển khoản đến đúng học viên:** trang **/billing** tự dặn người học ghi **mã người học** của họ (dạng `L…`) vào nội dung chuyển khoản. Khi nhận được tiền, gõ mã đó (hoặc email) vào ô tìm ở **Quản trị → Người học**, mở hồ sơ rồi bấm **Kích hoạt gói**. Học viên học lại được ngay.

**Cảnh báo:** thời gian dùng thử của người đã đăng ký từ lâu có thể đã hết ngay khi bạn bật thu phí. Hãy dùng **Tặng ngày** cho những người đang thử trước khi bật. Chưa có cổng thanh toán tự động.

### 6.4 Bật AI (tùy chọn)

Mặc định `HUAYU_LLM_PROVIDER=none` và ứng dụng chạy đầy đủ không cần AI. Muốn bật:

```ini
# openai hoặc anthropic
HUAYU_LLM_PROVIDER=openai
HUAYU_LLM_MODEL=ten-mo-hinh
HUAYU_LLM_API_KEY=khoa-api
```

Phải điền đủ cả ba dòng: thiếu `HUAYU_LLM_MODEL` hoặc `HUAYU_LLM_API_KEY` thì ứng dụng từ chối khởi động. Chú thích chỉ viết ở dòng riêng, không viết sau giá trị.

Chỉ khi **cả hai**: bạn bật AI **và** học viên tích ô đồng ý riêng, ứng dụng mới gửi cho nhà cung cấp một gói dữ liệu có cấu trúc (mã lý do, các con số của kế hoạch, các điều chỉnh đề xuất, số câu đúng/sai, số phút rảnh nếu có và tên chủ đề ngữ pháp hay sai như 在). **Không bao giờ gửi** ghi chú tự do, tên hay email. Câu trả lời của AI bị loại nếu chứa một con số (dãy chữ số liền nhau, ví dụ 15) không có trong dữ liệu đã gửi (riêng 1, 2, 3 luôn được chấp nhận) hoặc dài quá 900 ký tự; kiểm tra này chỉ nhìn các con số viết bằng chữ số nên số viết bằng chữ không bị chặn. Mọi lỗi đều quay lại lời giải thích mặc định. Khi bật AI, bước "Xem các lựa chọn của mình" có thể chậm tới `HUAYU_LLM_TIMEOUT_S` giây (mặc định 8). Hãy cập nhật trang quyền riêng tư cho khớp nếu bạn bật tính năng này. **[CHƯA KIỂM THỬ]**: các điều trên đã được kiểm tra trong mã và bằng bản giả lập, chưa với một nhà cung cấp AI thật; nếu bật AI, hãy tự thử một lần.

### 6.5 Thông tin đơn vị vận hành (trang /privacy và /terms)

Hai trang **/privacy** và **/terms** có các ô vàng giữ chỗ cho những thứ chỉ chủ sở hữu biết. Một phần điền được bằng `.env`, phần còn lại vẫn phải sửa trong tệp dịch:

| Ô vàng | Cách điền |
|---|---|
| Tên đơn vị vận hành, địa chỉ, mã số thuế (統一編號) | Trong `.env`: `HUAYU_OPERATOR_NAME`, `HUAYU_OPERATOR_ADDRESS`, `HUAYU_OPERATOR_TAX_ID`, rồi khởi động lại (Docker: `docker compose up -d`). Hiện ở khối “Ai vận hành dịch vụ này” của cả hai trang; để trống thì ô vàng còn nguyên |
| Email liên hệ | Trong `.env`: `HUAYU_SUPPORT_EMAIL` |
| Chính sách hoàn tiền và rút lui (chỉ có trên **/terms**) | **Chưa điền được bằng `.env`**: sửa khóa `legal.refund_placeholder` trong ba tệp `app/locales/vi/marketing.json`, `app/locales/zh-TW/marketing.json`, `app/locales/en/marketing.json`, rồi dựng lại ảnh (`docker compose up -d --build`) |
| Tòa án và cách giải quyết tranh chấp (chỉ có trên **/terms**) | Như trên, với khóa `legal.court_placeholder` |
| Dòng “Bản nháp: cần luật sư xem lại…” (cả hai trang) | Sau khi **luật sư đã xem**, xóa dòng `<p class="notice" role="note">...</p>` (ngay dưới thẻ `<article ...>`) trong tệp `app/templates/marketing/legal.html`, rồi dựng lại ảnh |

---

## 7. Các lệnh quản trị

Chạy từ thư mục dự án. Máy cá nhân: thêm `uv run` phía trước, ví dụ `uv run python -m app.cli check`. Docker: `docker compose exec app python -m app.cli check`.

| Lệnh | Việc làm |
|---|---|
| `python -m app.cli prepare` | Áp dụng migration cơ sở dữ liệu và nạp dữ liệu tham chiếu. An toàn khi chạy lại. Khi `HUAYU_ENV=prod` (Docker luôn là prod) còn in cảnh báo nếu thiếu email hỗ trợ, thiếu SMTP, hoặc chưa có câu hỏi nào được duyệt trong khi không cho phục vụ câu chưa duyệt; ở `dev` không in cảnh báo nào |
| `python -m app.cli create-admin --email X` | Địa chỉ **mới**: tạo quản trị viên (hỏi mật khẩu hai lần; hoặc dùng biến `HUAYU_ADMIN_PASSWORD`). Địa chỉ **đã có tài khoản**: chỉ nâng quyền, **giữ nguyên mật khẩu và phiên đăng nhập**. Thêm `--reset-password` để đặt mật khẩu mới cho tài khoản đã có (người đó bị đăng xuất khỏi mọi thiết bị). Có tùy chọn `--password` nhưng mật khẩu nằm lại trong lịch sử lệnh, đừng dùng |
| `python -m app.cli delete-user --email X` | Xóa tài khoản theo yêu cầu (ví dụ sinh viên gửi email). Cùng đường xử lý với nút xóa trong ứng dụng (cũng xóa các thư của địa chỉ đó trong `outbox`; giữ lại bản ghi thanh toán đã tách khỏi tài khoản); từ chối xóa quản trị viên duy nhất; hỏi gõ `DELETE` chữ hoa (hoặc thêm `--yes`) |
| `python -m app.cli demo` | Dữ liệu mẫu, chỉ cho phát triển |
| `python -m app.cli check` | In cấu hình đang dùng (ẩn bí mật), kiểm tra cơ sở dữ liệu và in cùng các cảnh báo như `prepare` (chỉ ở `prod`). Chạy sau khi ứng dụng đã khởi động ít nhất một lần, để cơ sở dữ liệu đã được `prepare` |

---

## 8. Đưa lên VPS

Hướng dẫn từng bước đầy đủ nằm ở **[`deploy/deploy.md`](../deploy/deploy.md)** (Ubuntu 24.04, Docker Compose, Caddy với HTTPS tự động). Tóm tắt thứ tự:

1. **Chuẩn bị**: một VPS (khuyên dùng 2 GB RAM), một tên miền, email SMTP. Nên dùng một **tên miền phụ riêng** (ví dụ `hoc.example.com`), đừng dùng tên miền gốc: ở chế độ chạy thật ứng dụng gửi tiêu đề HSTS (một năm) buộc trình duyệt chỉ dùng HTTPS với đúng tên miền đó, và một tên miền phụ riêng không ảnh hưởng các website khác của bạn.
2. **Tạo người dùng và khóa SSH**; thử đăng nhập bằng khóa ở phiên thứ hai **trước khi** tắt mật khẩu.
3. **Tường lửa `ufw`**: cho phép SSH trước rồi mới bật; mở 80 và 443.
4. **Cài Docker**.
5. **Trỏ tên miền** về VPS: chỉ tạo bản ghi **A** (IPv4) và **đừng tạo bản ghi AAAA (IPv6)** trong đợt thử. Giới hạn tốc độ đăng ký, đăng nhập, quên mật khẩu tính theo địa chỉ IP, và với IPv6 qua Docker ứng dụng có thể thấy mọi người dùng là cùng một địa chỉ, khiến cả nhóm chung một hạn mức (chưa được chạy thử; chi tiết ở mục 5 của `deploy.md`).
6. **Đưa mã nguồn lên VPS**: có hai cách (chọn một), chi tiết ở mục 6 của `deploy.md`. Cách A: `rsync` từ máy của bạn. Cách B: tạo một khóa SSH trên VPS và thêm khóa công khai vào kho GitHub ở **Settings > Deploy keys** (chỉ đọc), rồi `git clone git@github.com:huytue3107/huayu-coach.git`. Nếu kho riêng tư, `git clone` bằng địa chỉ `https://` trên VPS sẽ không chạy (chỉ cần khóa deploy như trên). Tệp `.env` **không nằm trong git** (và `rsync` cũng loại nó): bạn tự tạo nó ở bước 7.
7. **Tạo `.env`**: `cp .env.example .env`, rồi `chmod 600 .env`. Tối thiểu: `DOMAIN` (chỉ tên miền, ví dụ `huayu.example.com`: **không** có `https://`, không có dấu `/` hay đường dẫn), `HUAYU_SECRET_KEY`, `HUAYU_SUPPORT_EMAIL`, `HUAYU_INVITE_CODE`, các biến SMTP, và **một quyết định về câu hỏi**: hoặc nhờ giáo viên duyệt câu hỏi trước khi mời sinh viên, hoặc đặt `HUAYU_ALLOW_UNREVIEWED_QUESTIONS=true` cho đợt thử. Nếu không làm một trong hai, ở chế độ chạy thật sinh viên sẽ không có bài tập nào. Nên điền luôn `HUAYU_OPERATOR_NAME`, `HUAYU_OPERATOR_ADDRESS`, `HUAYU_OPERATOR_TAX_ID` (xem [mục 6.5](#65-thông-tin-đơn-vị-vận-hành-trang-privacy-và-terms)).
8. **Khởi động**:
   ```bash
   docker volume create huayu-data
   docker compose up -d --build
   curl -sS https://TEN-MIEN-CUA-BAN/healthz      # phải ra {"ok":true}
   ```
9. **Tạo quản trị viên đầu tiên**: `docker compose exec app python -m app.cli create-admin --email ban@example.com` (xem [mục 5.5](#55-tạo-quản-trị-viên-đầu-tiên)).
10. **Cài sao lưu hằng ngày** (cron + `deploy/backup.sh`) và một trình giám sát gọi `/healthz` (xem [mục 9](#9-vận-hành-hằng-ngày)).

> ⚠️ **Bộ triển khai này chưa từng được chạy thử** vì máy phát triển không có Docker. Các lệnh Docker trong hướng dẫn này đã được đối chiếu với `compose.yml`, `Dockerfile` và các tập lệnh trong `deploy/` nhưng **chưa được chạy với Docker thật**, và các bước Caddy, `ufw`, SSH, cron, khóa triển khai GitHub cũng chưa được thử. Hãy đọc kỹ các mục đánh dấu **[CHƯA KIỂM THỬ]** trong `deploy.md`, và thử trên một VPS rẻ trước khi mời sinh viên.

---

## 9. Vận hành hằng ngày

> **[CHƯA KIỂM THỬ]** Các lệnh Docker, cron, `backup.sh` và `restore.sh` trong mục này chưa được chạy với Docker thật: hai tập lệnh mới chỉ được thử với một lệnh `docker` giả lập, còn `restore_db.py` đã chạy thật trên máy phát triển nhưng chưa chạy trên VPS (xem bảng ở mục 0 của `deploy.md`). Hãy thử cả quy trình trên một VPS rẻ trước khi mời sinh viên.

**Sao lưu.** `deploy/backup.sh` tạo bản sao lưu nhất quán của cơ sở dữ liệu SQLite, nén lại và giữ 14 bản gần nhất. **Cài chạy mỗi ngày** (trên VPS, người dùng `deploy`; chi tiết ở mục 10.1 của `deploy.md`): chạy thử `cd ~/huayu-coach && ./deploy/backup.sh` một lần (phải thấy dòng `backup written: ...`), rồi gõ `crontab -e` và thêm các dòng sau (mỗi ngày lúc 03:15):

```cron
MAILTO=ban@example.com
BACKUP_PING_URL=https://hc-ping.com/UUID-CUA-BAN
15 3 * * * cd /home/deploy/huayu-coach && ./deploy/backup.sh >> /home/deploy/huayu-backups/backup.log
```

`BACKUP_PING_URL` đặt trong crontab như trên, **không** đặt trong `.env`; bỏ dòng đó nếu bạn chưa dùng dịch vụ kiểu healthchecks.io.

**Không bao giờ chạy trên máy thật:** `docker compose down -v`, `docker volume rm huayu-data`, `docker volume prune`, `docker system prune --volumes`. Cơ sở dữ liệu của sinh viên nằm trong volume Docker `huayu-data`, không nằm trong thư mục `~/huayu-coach`; các lệnh này xóa hoặc tách rời nó. Chỉ một bản sao lưu đã thử khôi phục và đã chép ra ngoài VPS mới bảo vệ được bạn (chi tiết: mục 10.6 của `deploy.md`).

- **Báo lỗi sao lưu.** `backup.sh` thoát với mã khác 0 khi lỗi, nhưng `MAILTO` của cron chỉ gửi được thư nếu VPS có sẵn chương trình gửi thư (VPS mới thường không có). Cách đáng tin hơn là đặt `BACKUP_PING_URL=` (một "check" miễn phí kiểu healthchecks.io, chu kỳ 1 ngày): dịch vụ sẽ gửi email cho bạn khi tín hiệu ngừng đến.
- **Chép ra ngoài VPS là việc của bạn.** Bản sao lưu chứa toàn bộ dữ liệu cá nhân: đặt trên ổ đĩa mã hóa và xóa bản chép quá 14 ngày (lệnh `find ... -mtime +14 -delete` có trong mục 10.4 của `deploy.md`) để khớp với lời hứa trên trang /privacy.
- **Cất bản sao của `.env`.** Tệp `.env` (khóa ký `HUAYU_SECRET_KEY`, mật khẩu SMTP, mã mời) không nằm trong git và **không nằm trong bản sao lưu cơ sở dữ liệu**. Hãy lưu một bản ở nơi an toàn, ví dụ trình quản lý mật khẩu. Mất hoặc đổi `HUAYU_SECRET_KEY` thì mọi người dùng bị đăng xuất. Chạy `chmod 600 .env` trên VPS.

**Khôi phục.** `./deploy/restore.sh ~/huayu-backups/huayu-AAAAMMDD-HHMMSS.db.gz`. Lệnh từ chối ngay tệp thiếu, rỗng, không phải `.gz` hoặc không chứa cơ sở dữ liệu SQLite; sau đó bắt bạn gõ `RESTORE`, dừng ứng dụng, kiểm tra lại bên trong container (toàn vẹn, có các bảng của Huayu; nếu không đúng thì từ chối và bật lại ứng dụng), rồi **thay cơ sở dữ liệu thật**, giữ bản cũ thành `huayu.db.before-restore-...` trong volume dữ liệu (chứa dữ liệu cá nhân: xóa tay khi đã chắc chắn) và luôn bật lại ứng dụng. Mọi thứ sinh viên làm sau thời điểm sao lưu sẽ mất. Khôi phục có thể làm sống lại tài khoản đã xóa sau thời điểm đó: xóa lại bằng `docker compose exec app python -m app.cli delete-user --email EMAIL` (cách liệt kê các tài khoản vừa sống lại có trong mục 10.5 của `deploy.md`).

**Thử khôi phục mà không đụng dữ liệu thật.** `restore.sh` **không** có chế độ khôi phục vào thư mục thử, nên đừng chạy nó để "thử" trên máy đang có sinh viên. Có hai cách thật sự:

- **Trên một VPS thử** (hoặc trước khi có dữ liệu thật): chạy chính `./deploy/restore.sh ...`. Đây là cách duy nhất thử cả quy trình Docker.
- **Chỉ kiểm tra bản sao lưu đọc được, trên chính VPS đang chạy:** dùng `deploy/restore_db.py` với biến `HUAYU_DATA_DIR` trỏ vào một thư mục thử (không có tùy chọn `--help`; biến này mặc định là `/data`, nên luôn đặt nó):

  ```bash
  cd ~/huayu-coach
  mkdir -m 700 ~/restore-test
  gunzip -c ~/huayu-backups/huayu-AAAAMMDD-HHMMSS.db.gz | HUAYU_DATA_DIR=$HOME/restore-test python3 deploy/restore_db.py
  rm -r ~/restore-test      # chứa dữ liệu cá nhân: xóa ngay khi xong
  ```

  Dòng kết quả đúng bắt đầu bằng `restored ...`. Cách này chỉ kiểm tra tệp sao lưu (toàn vẹn SQLite, có các bảng của Huayu), không chạy ứng dụng và không thử Docker; nó đã được chạy trên máy phát triển, **chưa chạy trên VPS** (chi tiết và lệnh đếm số dòng: mục 10.5 của `deploy.md`).

**Cập nhật phiên bản mới** (làm đủ, theo thứ tự):

1. `./deploy/backup.sh` và ghi lại tên tệp ở dòng "backup written: ..." (bản "trước khi cập nhật").
2. Giữ image đang chạy: `docker tag huayu-coach:latest huayu-coach:before-$(date +%Y%m%d-%H%M)` (làm **trước** khi build).
3. Đưa mã mới lên (cách A: `rsync`; cách B: `git pull`, xem bước 6 mục 8) rồi `docker compose up -d --build`. Migration tự chạy khi khởi động; ứng dụng ngừng phục vụ vài giây.
4. Kiểm tra `/healthz`, `docker compose logs --tail 50 app`, đăng nhập thử.

**Quay lui** (mục 12 của `deploy.md`): migration chỉ đi tới. Nếu bản mới không thêm tệp nào trong `migrations/versions/` thì chỉ cần đổi lại image cũ. Nếu có, mã cũ không chạy được trên cơ sở dữ liệu mới: bạn phải đổi ảnh về bản cũ rồi `restore.sh` bản sao lưu ở bước 1 và **mất dữ liệu sinh viên nhập sau đó**. Nếu bạn bỏ bước 1 hoặc 2 thì không có đường quay lui. Vì vậy hãy cập nhật vào lúc ít người dùng.

**Xử lý yêu cầu của sinh viên.**

- *Xuất dữ liệu*: họ tự bấm **Tải dữ liệu của mình** trong Cài đặt.
- *Xóa dữ liệu*: họ tự bấm **Xóa tài khoản**, hoặc bạn chạy `docker compose exec app python -m app.cli delete-user --email EMAIL` khi họ yêu cầu qua email. Dữ liệu có thể còn trong các bản sao lưu cho tới khi bản đó bị xóa (tối đa 14 ngày với cấu hình mặc định).
- *Quên mật khẩu*: họ tự dùng **Quên mật khẩu?**. Nếu nghi ngờ lộ tài khoản, nhớ rằng **Kết thúc mọi phiên** không đổi mật khẩu (xem [mục 5.2](#52-người-học)).

**Theo dõi.** Trang **Quản trị → Tổng quan** cho biết người học có quay lại sau khi dừng không và họ chấm các đề xuất ra sao. Ứng dụng cố ý **không ghi nhật ký từng yêu cầu** (liên kết đặt lại mật khẩu chứa mã bí mật trong đường dẫn); để xem lỗi dùng `docker compose logs app`. Trình giám sát nên gọi `https://TEN-MIEN/healthz` kiểu GET có kiểm từ khóa `"ok":true`. Lưu ý `/healthz` vẫn báo ok khi đĩa đầy (lúc đó mọi thao tác ghi đều lỗi 500), nên hãy xem `df -h /` và `free -h` mỗi tuần hoặc bật cảnh báo đĩa ở nhà cung cấp VPS. Nếu `app` bị treo, Docker chỉ đánh dấu `unhealthy` chứ không tự khởi động lại: chạy `docker compose restart app` rồi xem `docker compose logs --tail 100 app`.

**Câu hỏi mới được duyệt.** Câu vừa bấm **Duyệt** có thể được phục vụ cho bài kiểm tra đầu vào và cho các bài hằng ngày được tạo sau đó. Bài của hôm nay đã tạo sẵn cho một học viên được giữ nguyên, nên câu mới thường tới học viên từ ngày hôm sau.

---

## 10. Danh sách kiểm tra trước khi mời sinh viên đầu tiên

- [ ] Chạy thử toàn bộ trên một VPS rẻ và mở trang qua HTTPS (chứng chỉ hợp lệ).
- [ ] `docker compose exec app python -m app.cli check` **không còn cảnh báo**: đã đặt `HUAYU_SUPPORT_EMAIL`, đã cấu hình SMTP (thử **Quên mật khẩu?** bằng email thật), và có câu hỏi được duyệt (hoặc cố ý bật `HUAYU_ALLOW_UNREVIEWED_QUESTIONS=true` cho đợt thử và đã nói rõ với người tham gia).
- [ ] Đã đặt `HUAYU_INVITE_CODE`.
- [ ] Câu hỏi: hoặc giáo viên đã duyệt đủ **cả ba giai đoạn** (bài kiểm tra đầu vào, hằng ngày A1, bài kiểm tra cuối; xem [mục 4.1](#41-nguyên-tắc-phục-vụ-câu-hỏi)): ít nhất các câu dùng trong bài kiểm tra đầu vào và vài chục câu hằng ngày, đã sửa 6 câu **Cần sửa**; hoặc bạn cố ý bật `HUAYU_ALLOW_UNREVIEWED_QUESTIONS=true` cho đợt thử và đã nói rõ với người tham gia (câu **Cần sửa** vẫn không bao giờ được phục vụ).
- [ ] Đã đọc mục 10.6 của `deploy.md` (“Không bao giờ chạy trên máy thật”) và biết cơ sở dữ liệu nằm trong volume `huayu-data`.
- [ ] Trang **/privacy** và **/terms**: đã điền thông tin đơn vị vận hành bằng `HUAYU_OPERATOR_NAME`, `HUAYU_OPERATOR_ADDRESS`, `HUAYU_OPERATOR_TAX_ID`; đã sửa trong các tệp `marketing.json` hai ô vàng còn lại (chính sách hoàn tiền và tòa án, chỉ có trên /terms) rồi dựng lại ảnh; và **luật sư đã xem** (hiện chỉ là bản nháp). Chi tiết: [mục 6.5](#65-thông-tin-đơn-vị-vận-hành-trang-privacy-và-terms).
- [ ] Đã quyết giá thật trong **Gói và thanh toán** (các gói đang mở hiện công khai ở /pricing), và viết `HUAYU_PAYMENT_INSTRUCTIONS` nếu bật thu phí.
- [ ] Đã tạo ít nhất **hai** quản trị viên (không thể xóa hoặc hạ quyền quản trị viên cuối cùng).
- [ ] Sao lưu hằng ngày chạy được, có báo lỗi (`BACKUP_PING_URL`), đã chép ra ngoài VPS, đã cất một bản `.env` ở nơi an toàn, và **đã thử khôi phục** bằng `./deploy/restore.sh` trên một VPS thử (hoặc trước khi có dữ liệu thật), hoặc ít nhất kiểm tra bản sao lưu bằng `restore_db.py` vào thư mục thử (xem [mục 9](#9-vận-hành-hằng-ngày)). **Đừng thử `restore.sh` trên máy đang có sinh viên**: lệnh này dừng ứng dụng và thay cơ sở dữ liệu thật (bản cũ được giữ lại thành `huayu.db.before-restore-...` trong volume dữ liệu).
- [ ] Có trình giám sát gọi `/healthz` và cảnh báo khi hỏng.
- [ ] Đã thống nhất với nhóm tác giả, giáo viên hướng dẫn và nhà trường về quyền đối với dự án thi gốc trước khi thương mại hóa.

---

## 11. Xử lý sự cố thường gặp

| Hiện tượng | Nguyên nhân và cách xử lý |
|---|---|
| `./start.sh` báo thiếu `uv` | Cài `brew install uv`, mở lại cửa sổ dòng lệnh |
| Cổng 8000 đã bị dùng | `PORT=9000 ./start.sh` (liên kết đặt lại mật khẩu tự theo cổng mới) |
| Quên mật khẩu mà không có thư | (a) **Chưa cấu hình SMTP**: thư chỉ được ghi thành tệp. Máy cá nhân: mở tệp mới nhất trong `data/outbox/`. Docker: xem hai lệnh ngay dưới bảng. Tệp chứa liên kết đặt lại mật khẩu của người đó nên chỉ dùng khi thử; khi chạy thật hãy cấu hình SMTP. Liên kết có hiệu lực 60 phút, dùng một lần. (b) **Đã cấu hình SMTP nhưng thư vẫn không đến**: kiểm tra cặp cổng và `HUAYU_SMTP_SECURITY` (587 + `starttls`, 465 + `ssl`) và mật khẩu; người dùng vẫn thấy "đã gửi" (cố ý), lỗi chỉ nằm trong `docker compose logs app`, tìm dòng `smtp send failed` |
| Học viên thấy “Hiện chưa có bài tập nào cho bạn” | Không có câu hằng ngày nào phục vụ được cho trình độ của họ: học viên A1 chỉ nhận câu giai đoạn **Luyện tập hằng ngày**, trình độ **A1**, đã duyệt (hoặc đang chờ duyệt nếu `HUAYU_ALLOW_UNREVIEWED_QUESTIONS=true`); câu *Cần sửa* không bao giờ được phục vụ. Duyệt thêm câu hằng ngày A1 (Quản trị → Câu hỏi, lọc Giai đoạn và Trình độ) hoặc bật biến trên rồi **khởi động lại** ứng dụng |
| Học viên bị xếp A1 mãi, không làm lại được bài kiểm tra | (a) Họ làm bài lúc chưa có câu kiểm tra nào: duyệt câu giai đoạn **Bài kiểm tra đầu vào**, rồi dùng **Người học →** mở người đó **→ Cho làm lại bài kiểm tra**. Nút này xóa cả các câu trả lời bài kiểm tra đầu vào cũ nên họ làm lại cả bài; bài hằng ngày giữ nguyên. (b) Muốn lên A2 cần ít nhất 2 câu A2 giai đoạn kiểm tra đầu vào được phục vụ, đúng từ 80% và đúng hết các câu A2; với bộ 50 câu mẫu chưa ai lên A2 được (xem [mục 12](#12-giới-hạn-hiện-tại)) |
| Ai cũng bị đăng xuất sau khi cập nhật | `HUAYU_SECRET_KEY` đã thay đổi. Giữ cố định một khóa |
| Ứng dụng trong Docker cứ khởi động lại | `docker compose logs app`: thường do `HUAYU_SECRET_KEY` ngắn hơn 32 ký tự, `DOMAIN` ghi sai (có `https://`), hoặc bật AI mà thiếu `HUAYU_LLM_MODEL` / `HUAYU_LLM_API_KEY` |
| Báo “Thao tác quá nhanh” (429) | Giới hạn tốc độ theo địa chỉ IP (chống đoán mật khẩu và đăng ký ồ ạt), đếm trong bộ nhớ của ứng dụng và **tính cả lần đăng nhập đúng**. Hạn mức chính: đăng ký 60 lần/giờ và Quên mật khẩu 10 lần/giờ cho mỗi IP; đăng nhập 240 lần/10 phút mỗi IP, 8 lần/10 phút cho mỗi cặp (IP, email) và 25 lần/15 phút cho mỗi tài khoản (tính từ mọi IP). Cả lớp dùng chung một Wi-Fi nhà trường chỉ có **một** IP nên dùng chung hạn mức đăng ký, và thời gian chờ có thể tới khoảng 1 giờ. Cách xử lý nhanh: `docker compose restart app` (xóa bộ đếm); lần sau cho sinh viên đăng ký rải ra |
| Học viên hết thời gian dùng thử khi mới bật thu phí | Dùng **Tặng ngày** cho họ |
| Giao diện trông lạ sau khi cập nhật | Tải lại cứng trình duyệt; tệp CSS luôn được kiểm tra lại, nên thường tự khỏi |

Đọc thư trong `outbox` khi chạy Docker (không cần cấu hình thêm). **[CHƯA KIỂM THỬ]** (cần Docker thật):

```bash
docker compose exec app ls -t /data/outbox                      # tệp đầu tiên là mới nhất
docker compose exec app cat /data/outbox/TEN-TEP.txt            # thay TEN-TEP.txt bằng tên tệp từ lệnh trên; trong đó có liên kết đặt lại mật khẩu
```

---

## 12. Giới hạn hiện tại

Để bạn không hứa quá với người dùng:

- **Nội dung học còn rất ít**: 50 câu mẫu chưa duyệt. Kho câu hằng ngày A1 chỉ có khoảng 20 đến 22 câu, nên học viên A1 bắt đầu gặp câu lặp từ ngày thứ 3 đến thứ 6 tùy mức học mỗi ngày (30 hoặc 45 phút: ngày 3; 20 phút: ngày 4; 15 phút: ngày 5; 10 phút: ngày 6); câu làm sai sẽ quay lại sớm hơn. Chưa có giải thích tiếng Anh.
- Hiện **không thể** đạt trình độ A2 qua bài kiểm tra đầu vào: bài chỉ có 1 câu A2 dùng được (Q007; câu A2 còn lại, Q009, đang “Cần sửa”), trong khi quy tắc cần ít nhất 2 câu A2, đúng cả hai, và tổng số câu đúng từ 80%. Chỉ khi sửa và duyệt Q009 (hoặc thêm câu A2 cho bài kiểm tra đầu vào) thì mới có người đạt A2.
- **Chưa có cổng thanh toán tự động**; chưa có nhắc học qua email hay thông báo đẩy; chưa có ứng dụng gốc cho điện thoại (nhưng có thể "thêm vào màn hình chính").
- Chưa có chế độ ngoại tuyến (không có service worker): mất mạng là không học được, dù đã "thêm vào màn hình chính". Chỉ chạy được trên một máy chủ (SQLite). Chưa có xác thực hai lớp, đăng nhập bằng bên thứ ba, luyện nói và phát âm, trình độ trên A2, hay kiểm thử xâm nhập.
- Giới hạn tốc độ nằm trong bộ nhớ của từng tiến trình và mất khi khởi động lại; bản Docker chạy 2 worker nên mỗi worker đếm riêng (hạn mức thực tế gần gấp đôi).
- Đăng xuất chỉ xóa cookie trên thiết bị đó: cookie đã bị sao chép vẫn dùng được tới 30 ngày kể từ lúc đăng nhập, cho đến khi người dùng đổi mật khẩu hoặc quản trị viên bấm **Kết thúc mọi phiên**. Chưa xác minh email khi đăng ký.
- Trang quyền riêng tư và điều khoản là **bản nháp** cần luật sư xem.
