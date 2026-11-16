import asyncio
import os

import aiohttp
import discord
from deep_translator import GoogleTranslator
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# ID các kênh bật dịch tự động, cách nhau bằng dấu phẩy (xem file .env)
AUTO_CHANNELS = {
    int(x) for x in os.getenv("AUTO_TRANSLATE_CHANNELS", "").split(",") if x.strip()
}

# key: (nhãn nút, mã ngôn ngữ của Google Translate, tên role, cờ)
LANGS = {
    "en":  {"label": "English",         "code": "en", "role": "Lang-English",    "flag": "🇬🇧"},
    "vi":  {"label": "Tiếng Việt",      "code": "vi", "role": "Lang-Vietnamese", "flag": "🇻🇳"},
    "th":  {"label": "ไทย",             "code": "th", "role": "Lang-Thai",       "flag": "🇹🇭"},
    "fil": {"label": "Filipino",        "code": "tl", "role": "Lang-Filipino",   "flag": "🇵🇭"},
    "id":  {"label": "Bahasa Indonesia", "code": "id", "role": "Lang-Indonesian", "flag": "🇮🇩"},
}
ROLE_NAMES = {info["role"] for info in LANGS.values()}


# ---------------------------------------------------------------- Dịch thuật
def _translate_sync(text: str, source: str, target: str) -> str:
    return GoogleTranslator(source=source, target=target).translate(text[:4500])


_cache: dict = {}
_sem = asyncio.Semaphore(2)  # tối đa 2 yêu cầu dịch cùng lúc để tránh bị Google chặn


async def _google_free(text: str, source: str, target: str):
    """Gọi trực tiếp endpoint dịch miễn phí của Google, trả về (bản dịch, ngôn ngữ gốc)."""
    url = "https://translate.googleapis.com/translate_a/single"
    params = {"client": "gtx", "sl": source, "tl": target, "dt": "t", "q": text}
    async with aiohttp.ClientSession() as session:
        async with session.get(
            url, params=params, timeout=aiohttp.ClientTimeout(total=15)
        ) as r:
            if r.status != 200:
                raise RuntimeError(f"HTTP {r.status}")
            data = await r.json(content_type=None)
    translated = "".join(seg[0] for seg in data[0] if seg and seg[0])
    detected = data[2] if len(data) > 2 and data[2] else source
    return translated, detected


async def translate(text: str, source: str, target: str):
    """Trả về (bản dịch, ngôn ngữ gốc nhận diện được), hoặc (None, None) nếu lỗi."""
    text = text[:4500]
    key = (text, source, target)
    if key in _cache:
        return _cache[key]

    async with _sem:
        for attempt in range(3):  # thử lại với thời gian chờ tăng dần
            try:
                result = await _google_free(text, source, target)
                _cache[key] = result
                if len(_cache) > 500:
                    _cache.pop(next(iter(_cache)))
                return result
            except Exception as e:  # noqa: BLE001
                print(f"  -> Lỗi dịch sang {target} (lần {attempt + 1}/3): {e}")
                await asyncio.sleep(1.5 * (attempt + 1))

        # Dự phòng: dùng thư viện deep-translator
        try:
            out = await asyncio.to_thread(_translate_sync, text, source, target)
            return out, source
        except Exception as e:  # noqa: BLE001
            print(f"  -> Dự phòng cũng lỗi ({target}): {e}")
    return None, None


def get_user_lang(member: discord.Member):
    """Trả về key ngôn ngữ (en, vi, ...) theo role của thành viên, hoặc None."""
    for key, info in LANGS.items():
        if any(r.name == info["role"] for r in member.roles):
            return key
    return None


# ------------------------------------------------------- Nút chọn ngôn ngữ
class LanguageButton(discord.ui.Button):
    def __init__(self, key: str):
        super().__init__(
            label=LANGS[key]["label"],
            style=discord.ButtonStyle.primary,
            custom_id=f"lang:{key}",  # custom_id cố định để nút sống sau khi restart
        )
        self.key = key

    async def callback(self, interaction: discord.Interaction):
        guild, member = interaction.guild, interaction.user
        role_name = LANGS[self.key]["role"]
        role = discord.utils.get(guild.roles, name=role_name)
        if role is None:
            role = await guild.create_role(name=role_name, reason="Language role")

        if role in member.roles:  # bấm lại -> gỡ role
            await member.remove_roles(role)
            msg = f"Đã gỡ ngôn ngữ **{LANGS[self.key]['label']}**."
        else:  # chỉ giữ 1 ngôn ngữ tại một thời điểm
            others = [r for r in member.roles if r.name in ROLE_NAMES and r != role]
            if others:
                await member.remove_roles(*others)
            await member.add_roles(role)
            msg = f"Đã chọn ngôn ngữ **{LANGS[self.key]['label']}** {LANGS[self.key]['flag']}"
        await interaction.response.send_message(msg, ephemeral=True)


class LanguageView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        for key in LANGS:
            self.add_item(LanguageButton(key))


# ---------------------------------------------------------------------- Bot
class TranslateBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True  # bật trong Developer Portal
        intents.members = True          # bật trong Developer Portal
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        self.add_view(LanguageView())  # đăng ký lại nút sau mỗi lần khởi động
        await self.tree.sync()

    async def on_ready(self):
        print(f"Đã đăng nhập: {self.user} ({self.user.id})")

    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        print(f"[msg] kênh={message.channel.id} | {message.author} | {message.content!r}")
        if not message.content:
            print("  -> Nội dung rỗng: hãy bật Message Content Intent trong Developer Portal")
            return
        if message.channel.id not in AUTO_CHANNELS:
            print("  -> Kênh này KHÔNG có trong AUTO_TRANSLATE_CHANNELS (.env)")
            return

        # Tự nhận diện ngôn ngữ của tin nhắn (người gửi viết ngôn ngữ nào cũng được)
        src_code = "auto"

        # Dịch sang ngôn ngữ mà người đọc (thành viên có role ngôn ngữ) đang dùng
        targets = []
        for key, info in LANGS.items():
            role = discord.utils.get(message.guild.roles, name=info["role"])
            if role and role.members:
                targets.append(key)
        if not targets:
            print("  -> Chưa có ai chọn ngôn ngữ: hãy bấm 1 nút trong bảng chọn ngôn ngữ")
            return

        results = await asyncio.gather(
            *(translate(message.content, src_code, LANGS[k]["code"]) for k in targets)
        )
        # Bỏ những bản dịch giống hệt tin gốc (tức tin gốc đã đúng ngôn ngữ đó)
        lines = []
        for k, (text, detected) in zip(targets, results):
            if not text:  # dịch lỗi -> bỏ qua, không hiện lỗi vào kênh
                continue
            if detected and detected.split("-")[0] == LANGS[k]["code"]:
                continue  # tin gốc đã đúng ngôn ngữ này
            if text.strip().lower() == message.content.strip().lower():
                continue
            lines.append(f"{LANGS[k]['flag']} **{LANGS[k]['label']}**: {text}")
        print(f"  -> Dịch sang: {targets} | kết quả: {results}")
        if lines:
            embed = discord.Embed(description="\n".join(lines), color=0x5865F2)
            await message.reply(embed=embed, mention_author=False)


bot = TranslateBot()


# ------------------------------------------------------------------ Lệnh
@bot.tree.command(name="setup_languages", description="Đăng bảng chọn ngôn ngữ vào kênh này")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def setup_languages(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🌐 Language / Ngôn ngữ / ภาษา / Bahasa / Wika",
        description=(
            "Please select your language below.\n"
            "Vui lòng chọn ngôn ngữ của bạn bên dưới.\n"
            "โปรดเลือกภาษาของคุณด้านล่าง\n"
            "Silakan pilih bahasamu di bawah ini.\n"
            "Piliin ang iyong wika sa ibaba.\n\n"
            "💡 Press the same button again to remove your language role.\n"
            "💡 Nhấn lại đúng nút đã chọn để gỡ vai trò ngôn ngữ.\n"
            "💡 กดปุ่มเดิมที่เลือกไว้อีกครั้งเพื่อลบบทบาทภาษา\n"
            "💡 Tekan tombol yang sama sekali lagi untuk menghapus peran bahasamu.\n"
            "💡 Pindutin muli ang parehong button para alisin ang iyong language role."
        ),
        color=0x3BA7E0,
    )
    await interaction.channel.send(embed=embed, view=LanguageView())
    await interaction.response.send_message("Đã đăng bảng chọn ngôn ngữ.", ephemeral=True)


@bot.tree.context_menu(name="Translate")
@app_commands.guild_only()
async def translate_message(interaction: discord.Interaction, message: discord.Message):
    """Chuột phải tin nhắn -> Apps -> Translate: dịch riêng cho người bấm (ephemeral)."""
    if not message.content:
        await interaction.response.send_message("Tin nhắn không có nội dung.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)

    target_key = get_user_lang(interaction.user) or "en"
    src_key = get_user_lang(message.author) if isinstance(message.author, discord.Member) else None
    src_code = LANGS[src_key]["code"] if src_key else "auto"

    text, _ = await translate(message.content, src_code, LANGS[target_key]["code"])
    if not text:
        await interaction.followup.send("Không dịch được lúc này, hãy thử lại sau.", ephemeral=True)
        return
    await interaction.followup.send(
        f"{LANGS[target_key]['flag']} **{LANGS[target_key]['label']}**\n{text}", ephemeral=True
    )


if __name__ == "__main__":
    bot.run(TOKEN)