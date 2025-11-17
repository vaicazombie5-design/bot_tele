import logging
import os
import traceback
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
import aiohttp
import asyncio
import qrcode
import io
import re
import uuid
from config import TELEGRAM_BOT_TOKEN, ACCESSTRADE_TOKEN, BOT_INSTANCE_ID

# 🆔 Unique bot instance identifier
BOT_INSTANCE_ID = BOT_INSTANCE_ID or str(uuid.uuid4())[:8]

# ⚠️ Tokens được load từ config.py
TOKEN = TELEGRAM_BOT_TOKEN
ACCESS_TOKEN = ACCESSTRADE_TOKEN

# Cấu hình logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# 🕒 Lưu trữ ID các message đã xử lý để tránh xử lý trùng lặp
processed_messages = set()  # Lưu trữ ID các message đã xử lý

# 💾 Cache campaign ID để tránh gọi API nhiều lần
shopee_campaign_id_cache = None

# 🔍 Mở rộng link rút gọn dạng shp.ee, vn.shp.ee hoặc s.shopee.vn (async)
async def expand_url(short_url):
    """Unshorten link bằng cách follow redirects - phiên bản đơn giản và nhanh"""
    logging.info(f"🔗 [{BOT_INSTANCE_ID}] Đang expand: {short_url}")
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1'
        }
        
        # Timeout ngắn để không làm chậm bot
        timeout = aiohttp.ClientTimeout(total=8, connect=4)
        connector = aiohttp.TCPConnector(ssl=False)
        
        async with aiohttp.ClientSession(timeout=timeout, headers=headers, connector=connector) as session:
            async with session.get(short_url, allow_redirects=True, max_redirects=15) as response:
                final_url = str(response.url)
                
                # Kiểm tra xem đã redirect sang shopee.vn chưa
                if "shopee.vn" in final_url:
                    logging.info(f"✅ [{BOT_INSTANCE_ID}] Expand thành công: {final_url[:80]}...")
                    return final_url
                else:
                    logging.warning(f"⚠️ [{BOT_INSTANCE_ID}] URL không phải Shopee: {final_url[:80]}...")
                    return None
                    
    except asyncio.TimeoutError:
        logging.warning(f"⏱️ [{BOT_INSTANCE_ID}] Timeout expand: {short_url}")
        return None
    except Exception as e:
        logging.error(f"❌ [{BOT_INSTANCE_ID}] Lỗi expand: {type(e).__name__}: {e}")
        return None

# 🔗 Rút gọn link qua AccessTrade (async)
async def shorten_affiliate_link(original_url, platform="shopee"):
    """Rút gọn link affiliate cho Shopee hoặc Lazada"""
    print(f"🔗 Đang rút gọn {platform} link: {original_url}")
    
    if platform == "shopee":
        campaign_id = await get_shopee_campaign_id()
    elif platform == "lazada":
        campaign_id = await get_lazada_campaign_id()
    else:
        print(f"❌ Platform không hỗ trợ: {platform}")
        return None
    
    if not campaign_id:
        print(f"❌ Không tìm thấy campaign_id cho {platform}")
        return None
    
    print(f"✅ Campaign ID cho {platform}: {campaign_id}")

    url = "https://api.accesstrade.vn/v1/product_link/create"
    headers = {
        "Authorization": f"Token {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "campaign_id": campaign_id,
        "urls": [original_url]
    }

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.post(url, headers=headers, json=data) as response:
                response_text = await response.text()
                print(f"📊 API Response Status: {response.status}")
                print(f"📊 API Response: {response_text}")
                
                if response.status == 200:
                    json_data = await response.json()
                    if json_data.get("success"):
                        short_link = json_data["data"]["success_link"][0]["short_link"]
                        print(f"✅ Rút gọn thành công: {short_link}")
                        return short_link
                    else:
                        print(f"❌ API trả về success=false: {json_data}")
                else:
                    print(f"❌ API error {response.status}: {response_text}")
    except Exception as e:
        print(f"❌ Lỗi gọi API: {e}")
    return None

# 🔗 Wrapper cho Shopee (để tương thích ngược)
async def shorten_shopee_link(original_url):
    return await shorten_affiliate_link(original_url, "shopee")

# 🔗 Wrapper cho Lazada
async def shorten_lazada_link(original_url):
    return await shorten_affiliate_link(original_url, "lazada")

# 💾 Cache cho campaign IDs
lazada_campaign_id_cache = None

# 📦 Lấy campaign ID của Shopee (async + cache)
async def get_shopee_campaign_id():
    global shopee_campaign_id_cache
    
    # Trả về từ cache nếu đã có
    if shopee_campaign_id_cache:
        return shopee_campaign_id_cache
    
    url = "https://api.accesstrade.vn/v1/campaigns?approval=successful"
    headers = {"Authorization": f"Token {ACCESS_TOKEN}"}
    
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    json_data = await response.json()
                    for campaign in json_data["data"]:
                        if campaign["merchant"] == "shopee":
                            shopee_campaign_id_cache = campaign["id"]  # Cache lại
                            return campaign["id"]
    except:
        pass
    return None

# 📦 Lấy campaign ID của Lazada (async + cache)
async def get_lazada_campaign_id():
    global lazada_campaign_id_cache
    
    # Trả về từ cache nếu đã có
    if lazada_campaign_id_cache:
        print(f"📦 Sử dụng cached Lazada campaign ID: {lazada_campaign_id_cache}")
        return lazada_campaign_id_cache
    
    url = "https://api.accesstrade.vn/v1/campaigns?approval=successful"
    headers = {"Authorization": f"Token {ACCESS_TOKEN}"}
    
    try:
        # SSL verification - enable for production, disable for development
        ssl_verify = os.getenv('SSL_VERIFY', 'true').lower() == 'true'
        connector = aiohttp.TCPConnector(ssl=ssl_verify)
        timeout = aiohttp.ClientTimeout(total=10, connect=5)
        
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            print("🌐 Đang gọi API campaigns...")
            async with session.get(url, headers=headers) as response:
                response_text = await response.text()
                print(f"📊 Campaign API Status: {response.status}")
                
                if response.status == 200:
                    json_data = await response.json()
                    merchants = [c['merchant'] for c in json_data['data']]
                    print(f"📋 Tìm thấy {len(merchants)} campaigns: {merchants}")
                    
                    for campaign in json_data["data"]:
                        merchant_name = campaign["merchant"]
                        if merchant_name in ["lazadacps", "lazada"]:  # Try both names
                            lazada_campaign_id_cache = campaign["id"]
                            print(f"✅ Tìm thấy Lazada campaign ID: {campaign['id']} (merchant: {merchant_name})")
                            return campaign["id"]
                    
                    print("❌ Không tìm thấy campaign Lazada/lazadacps trong danh sách")
                else:
                    print(f"❌ Lỗi API campaigns {response.status}: {response_text}")
                    
    except aiohttp.ClientError as e:
        print(f"❌ Lỗi kết nối API campaigns: {e}")
    except Exception as e:
        print(f"❌ Lỗi không xác định API campaigns: {e}")
    
    return None

# 🖼️ Tạo mã QR
def generate_qr_code(url):
    qr = qrcode.make(url)
    buffer = io.BytesIO()
    qr.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer

# ✅ Lệnh bắt đầu
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Gửi thông điệp chào mừng khi nhận lệnh /start."""
    welcome_text = "🤖 **Chào mừng đến với Bot QR & Affiliate Telegram!**\n\n"
    welcome_text += f"🆔 Bot Instance: {BOT_INSTANCE_ID}\n\n"
    welcome_text += "🎮 **Lệnh khả dụng:**\n"
    welcome_text += "• `/rutgon <link>` - Rút gọn link thủ công\n"
    welcome_text += "• `/status` - Kiểm tra trạng thái bot\n"
    welcome_text += "• **Gửi bất kỳ gì** - Tạo QR code\n\n"
    welcome_text += "🛒 **Tính năng đặc biệt:**\n"
    welcome_text += "• **Link Shopee/Lazada** → Rút gọn affiliate + QR\n"
    welcome_text += "• **Link khác/Text** → Tạo QR trực tiếp\n\n"
    welcome_text += "💡 Chỉ cần gửi bất kỳ nội dung gì, bot sẽ tạo QR code cho bạn!"
    
    await update.message.reply_text(welcome_text, parse_mode='Markdown', reply_to_message_id=update.message.message_id)
    print(f'✅ [{BOT_INSTANCE_ID}] Bot đã được khởi động bởi user: {update.effective_user.first_name}')

# ✅ Lệnh thủ công: /rutgon <link>
async def rutgon(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Xử lý lệnh /rutgon để rút gọn link thủ công."""
    if not context.args:
        await update.message.reply_text("❌ Vui lòng cung cấp link!\nVí dụ: `/rutgon https://shopee.vn/...`", parse_mode='Markdown', reply_to_message_id=update.message.message_id)
        return
    
    link = ' '.join(context.args)
    await process_link(update, link)

# 📊 Lệnh kiểm tra trạng thái bot
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Hiển thị trạng thái hoạt động của bot."""
    status_text = "🤖 **Trạng thái Bot QR & Affiliate**\n\n"
    status_text += f"✅ **Bot đang hoạt động**: Online\n"
    status_text += f"🆔 **Instance ID**: {BOT_INSTANCE_ID}\n"
    status_text += f"🔗 **Affiliate links**: ✅ Hoạt động\n"
    status_text += f"🎯 **Tạo QR code**: ✅ Hoạt động\n"
    
    status_text += f"\n🛒 **Hỗ trợ platforms:**\n"
    status_text += f"• **Shopee** - Rút gọn affiliate + QR\n"
    status_text += f"• **Lazada** - Rút gọn affiliate + QR\n"
    status_text += f"• **Link khác/Text** - Tạo QR trực tiếp\n"
    
    status_text += f"\n🎮 **Lệnh khả dụng:**\n"
    status_text += f"• `/rutgon <link>` - Rút gọn link thủ công\n"
    status_text += f"• `/status` - Kiểm tra trạng thái bot\n"
    status_text += f"• **Gửi bất kỳ gì** - Tạo QR code\n"
    
    await update.message.reply_text(status_text, parse_mode='Markdown', reply_to_message_id=update.message.message_id)

# 📩 Tự động xử lý mọi tin nhắn
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Xử lý mọi tin nhắn - tạo QR hoặc rút gọn affiliate link."""
    message = update.message
    
    if not message or not message.text:
        return

    # Cleanup processed_messages để tránh tốn bộ nhớ (giữ lại 1000 message gần nhất)
    if len(processed_messages) > 1000:
        # Xóa 500 message cũ nhất (giả sử ID nhỏ hơn là cũ hơn)
        old_messages = sorted(processed_messages)[:500]
        for old_id in old_messages:
            processed_messages.discard(old_id)
        print(f"🧹 [{BOT_INSTANCE_ID}] Đã dọn dẹp {len(old_messages)} message cũ")

    # Kiểm tra xem message đã được xử lý chưa
    if message.message_id in processed_messages:
        print(f"⚠️ [{BOT_INSTANCE_ID}] Message {message.message_id} đã được xử lý, bỏ qua")
        return
    
    # Đánh dấu message đã xử lý
    processed_messages.add(message.message_id)
    print(f"📨 [{BOT_INSTANCE_ID}] Nhận tin nhắn mới {message.message_id} từ {message.from_user.first_name}: {message.text}")
    
    # Tìm link Shopee/Lazada trong tin nhắn
    shopee_pattern = r'(https?://(?:shopee\.vn|shp\.ee|vn\.shp\.ee|s\.shopee\.vn)/\S+)'
    lazada_pattern = r'(https?://(?:lazada\.vn|www\.lazada\.vn|lzd\.co|m\.lazada\.vn|s\.lazada\.vn)/\S+)'
    
    shopee_matches = re.findall(shopee_pattern, message.text)
    lazada_matches = re.findall(lazada_pattern, message.text)
    
    print(f"🔍 [{BOT_INSTANCE_ID}] Tìm thấy Shopee: {len(shopee_matches)}, Lazada: {len(lazada_matches)}")
    
    # Ưu tiên xử lý affiliate links trước
    if shopee_matches:
        link = shopee_matches[0]  # Lấy link đầu tiên
        print(f"🛒 [{BOT_INSTANCE_ID}] Xử lý Shopee affiliate: {link}")
        await process_affiliate_link(update, link, "shopee")
    elif lazada_matches:
        link = lazada_matches[0]  # Lấy link đầu tiên
        print(f"🛒 [{BOT_INSTANCE_ID}] Xử lý Lazada affiliate: {link}")
        await process_affiliate_link(update, link, "lazada")
    else:
        # Không phải affiliate link → Tạo QR cho bất kỳ nội dung gì
        print(f"🎯 [{BOT_INSTANCE_ID}] Tạo QR cho nội dung: {message.text}")
        await create_qr_for_content(update, message.text)

# 🛒 Xử lý affiliate link (Shopee/Lazada)
async def process_affiliate_link(update: Update, link: str, platform: str) -> None:
    """Xử lý affiliate link: mở rộng, rút gọn và tạo QR code."""
    print(f"🔧 [{BOT_INSTANCE_ID}] process_affiliate_link được gọi với {platform}: {link}")
    
    # Gửi thông báo đang xử lý
    processing_message = await update.message.reply_text(f"🛒 [{BOT_INSTANCE_ID}] Đang xử lý {platform.title()} link...")

    # CHỈ unshorten link s.shopee.vn (vn.shp.ee và shp.ee gửi trực tiếp cho API)
    unshortened_link = None
    
    if platform == "shopee" and "s.shopee.vn" in link:
        print(f"🔗 [{BOT_INSTANCE_ID}] Đang unshorten s.shopee.vn: {link}")
        expanded = await expand_url(link)
        print(f"📊 [{BOT_INSTANCE_ID}] Kết quả expand: {expanded}")
        
        if not expanded:
            error_msg = f"❌ Không thể unshorten link!\n\nLink gốc: {link}\n\nVui lòng thử lại hoặc kiểm tra link có hợp lệ không."
            await processing_message.edit_text(error_msg)
            return
        
        if "shopee.vn" not in expanded:
            error_msg = f"❌ Link sau khi unshorten không phải Shopee!\n\nLink gốc: {link}\nLink sau unshorten: {expanded}"
            print(f"⚠️ [{BOT_INSTANCE_ID}] {error_msg}")
            await processing_message.edit_text(error_msg)
            return
        
        unshortened_link = expanded
        link = expanded
        print(f"✅ [{BOT_INSTANCE_ID}] Link đã unshorten thành công: {unshortened_link}")
    elif platform == "shopee" and ("vn.shp.ee" in link or "shp.ee" in link):
        # Link vn.shp.ee hoặc shp.ee → gửi trực tiếp cho API AccessTrade
        print(f"📤 [{BOT_INSTANCE_ID}] Link {link} sẽ được gửi trực tiếp cho API AccessTrade (không cần unshorten)")
    elif platform == "lazada" and ("lzd.co" in link or "s.lazada.vn" in link):
        expanded = await expand_url(link)
        if not expanded or "lazada.vn" not in expanded:
            await processing_message.edit_text("❌ Không thể mở rộng link rút gọn hoặc không phải Lazada!")
            return
        link = expanded

    # Kiểm tra tính hợp lệ (cho phép link rút gọn Shopee như vn.shp.ee, shp.ee)
    if platform == "shopee":
        valid_shopee_domains = ["shopee.vn", "vn.shp.ee", "shp.ee", "s.shopee.vn"]
        if not any(domain in link for domain in valid_shopee_domains):
            await processing_message.edit_text("❌ Link Shopee không hợp lệ!")
            return
    elif platform == "lazada" and not any(domain in link for domain in ["lazada.vn", "www.lazada.vn", "lzd.co", "s.lazada.vn"]):
        await processing_message.edit_text("❌ Link Lazada không hợp lệ!")
        return

    # Rút gọn link affiliate
    short_link = await shorten_affiliate_link(link, platform)
    if not short_link:
        # Nếu không rút gọn được → Tạo QR cho link gốc và thông báo
        print(f"⚠️ Không rút gọn được {platform}, tạo QR cho link gốc")
        await processing_message.edit_text(f"⚠️ Không thể rút gọn {platform.title()} link. Tạo QR cho link gốc...")
        
        # Tạo QR cho link gốc
        loop = asyncio.get_event_loop()
        qr_image = await loop.run_in_executor(None, generate_qr_code, link)
        
        result_text = f"⚠️ QR của {platform.title()} link gốc:\n{link}"
        
        try:
            await update.message.reply_photo(
                photo=InputFile(qr_image, filename="qrcode.png"),
                caption=result_text,
                parse_mode='Markdown',
                reply_to_message_id=update.message.message_id
            )
            await processing_message.delete()
            return
        except Exception as e:
            print(f"❌ Lỗi tạo QR cho link gốc: {e}")
            await processing_message.edit_text(f"❌ Không thể tạo QR cho {platform.title()} link.")
            return

    # Tạo QR code (chạy trong executor để không block)
    loop = asyncio.get_event_loop()
    qr_image = await loop.run_in_executor(None, generate_qr_code, short_link)
    
    # Gửi kết quả với QR code
    # Hiển thị cả link đã unshorten (nếu có) và link affiliate
    if unshortened_link:
        result_text = f"🔗 **Link đã unshorten:**\n{unshortened_link}\n\n"
        result_text += f"✅ **Link affiliate (ăn hoa hồng):**\n{short_link}"
    else:
        result_text = f"✅ QR của {platform.title()} link:\n{short_link}"
    
    try:
        await update.message.reply_photo(
            photo=InputFile(qr_image, filename="qrcode.png"),
            caption=result_text,
            parse_mode='Markdown',
            reply_to_message_id=update.message.message_id
        )
        print(f"📤 [{BOT_INSTANCE_ID}] Gửi kết quả QR cho {platform} link: {short_link}")
        
        # Xóa thông báo "đang xử lý"
        await processing_message.delete()
        
    except Exception as e:
        print(f"❌ [{BOT_INSTANCE_ID}] Lỗi gửi QR code: {e}")
        if unshortened_link:
            error_text = f"🔗 **Link đã unshorten:**\n{unshortened_link}\n\n"
            error_text += f"✅ **Link affiliate:**\n{short_link}\n\n❌ Không thể tạo QR code."
        else:
            error_text = f"✅ {platform.title()} link đã rút gọn:\n{short_link}\n\n❌ Không thể tạo QR code."
        await processing_message.edit_text(error_text, parse_mode='Markdown')

# 🎯 Tạo QR cho nội dung bất kỳ
async def create_qr_for_content(update: Update, content: str) -> None:
    """Tạo QR code cho bất kỳ nội dung gì."""
    print(f"🎯 [{BOT_INSTANCE_ID}] Tạo QR cho nội dung: {content}")
    
    # Gửi thông báo đang tạo QR
    processing_message = await update.message.reply_text(f"🎯 [{BOT_INSTANCE_ID}] Đang tạo QR code...")
    
    try:
        # Tạo QR code (chạy trong executor để không block)
        loop = asyncio.get_event_loop()
        qr_image = await loop.run_in_executor(None, generate_qr_code, content)
        
        # Gửi kết quả với QR code
        # Kiểm tra xem có phải là link không
        if content.startswith(('http://', 'https://')):
            result_text = f"✅ QR của link:\n{content}"
        else:
            result_text = f"✅ QR của nội dung:\n`{content}`"
        
        await update.message.reply_photo(
            photo=InputFile(qr_image, filename="qrcode.png"),
            caption=result_text,
            parse_mode='Markdown',
            reply_to_message_id=update.message.message_id
        )
        print(f"📤 [{BOT_INSTANCE_ID}] Gửi QR code cho nội dung")
        
        # Xóa thông báo "đang xử lý"
        await processing_message.delete()
        
    except Exception as e:
        print(f"❌ [{BOT_INSTANCE_ID}] Lỗi tạo QR code: {e}")
        # Kiểm tra xem có phải là link không để format phù hợp
        if content.startswith(('http://', 'https://')):
            await processing_message.edit_text(f"❌ Không thể tạo QR code cho link:\n{content}", parse_mode='Markdown')
        else:
            await processing_message.edit_text(f"❌ Không thể tạo QR code cho nội dung:\n`{content}`", parse_mode='Markdown')

# 🔁 Wrapper cho process_link (để tương thích ngược)
async def process_link(update: Update, link: str) -> None:
    """Wrapper để tương thích với lệnh /rutgon."""
    # Kiểm tra xem có phải Shopee/Lazada không
    if "shopee.vn" in link or "shp.ee" in link or "vn.shp.ee" in link or "s.shopee.vn" in link:
        await process_affiliate_link(update, link, "shopee")
    elif any(domain in link for domain in ["lazada.vn", "www.lazada.vn", "lzd.co", "m.lazada.vn", "s.lazada.vn"]):
        await process_affiliate_link(update, link, "lazada")
    else:
        # Link khác → tạo QR trực tiếp
        await create_qr_for_content(update, link)

# 🟢 Hàm main để khởi chạy bot
def main() -> None:
    """Khởi chạy bot Telegram."""
    print(f'🚀 [{BOT_INSTANCE_ID}] Đang khởi động bot Telegram...')
    
    # Tạo Application
    application = Application.builder().token(TOKEN).build()

    # Đăng ký các handler
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("rutgon", rutgon))
    application.add_handler(CommandHandler("status", status))
    
    # Handler cho tin nhắn thường (không phải lệnh)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print(f'✅ [{BOT_INSTANCE_ID}] Bot Telegram đã sẵn sàng!')
    
    # Chạy bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
