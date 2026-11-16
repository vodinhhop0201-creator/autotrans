# Auto Translator - Discord bot

Bot Discord tự động dịch tin nhắn theo ngôn ngữ mà mỗi người dùng đã chọn
(English, Tiếng Việt, ไทย, Filipino, Bahasa Indonesia).

## Cài đặt
1. Cài thư viện: `pip install -r requirements.txt`
2. Sao chép `.env.example` thành `.env` rồi điền token và ID kênh.
3. Bật **Message Content Intent** và **Server Members Intent** trong Discord Developer Portal.
4. Chạy: `python bot.py`
5. Trong kênh chọn ngôn ngữ, gõ `/setup_languages` (chỉ admin).

## Lưu ý
- Không đưa file `.env` lên GitHub (đã có trong `.gitignore`).
- Bot cần Manage Roles và role của bot phải nằm cao hơn các role `Lang-...`.
