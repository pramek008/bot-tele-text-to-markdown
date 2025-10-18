import os
import re
import logging
import tempfile
import requests
from datetime import datetime, timedelta
from typing import Optional
import pandas as pd
from pathlib import Path
from telegram import Update, Document
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Konfigurasi
PDF_SERVICE_URL = os.getenv('PDF_SERVICE_URL', 'http://markdown-pdf-service:8080/convert')
DATA_DIR = os.getenv('DATA_DIR', './data')
EXCEL_LOG_FILE = os.path.join(DATA_DIR, 'user_generations.xlsx')
BACKUP_DIR = os.path.join(DATA_DIR, 'backups')
FREE_DAILY_QUOTA = int(os.getenv('FREE_DAILY_QUOTA', '15'))
HOURLY_RATE_LIMIT = int(os.getenv('HOURLY_RATE_LIMIT', '3'))
AUTO_BACKUP_ENABLED = os.getenv('AUTO_BACKUP_ENABLED', 'true').lower() == 'true'
BACKUP_INTERVAL_HOURS = int(os.getenv('BACKUP_INTERVAL_HOURS', '24'))

# User states
user_states = {}
user_markdown = {}
message_types = {}
user_quota = {}  # {user_id: {'daily_count': int, 'hourly_count': int, 'last_reset': datetime, 'hourly_reset': datetime, 'is_premium': bool}}

# ==================== EXCEL LOGGING ====================

def init_excel_log():
    """Inisialisasi file Excel untuk logging"""
    # Buat directory jika belum ada
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
    Path(BACKUP_DIR).mkdir(parents=True, exist_ok=True)
    
    if not Path(EXCEL_LOG_FILE).exists():
        df = pd.DataFrame(columns=[
            'timestamp', 'user_id', 'username', 'first_name', 'last_name',
            'input_type', 'input_length', 'success', 'error_message', 'is_premium'
        ])
        df.to_excel(EXCEL_LOG_FILE, index=False)
        logger.info(f"✅ Excel log file created: {os.path.abspath(EXCEL_LOG_FILE)}")
    else:
        logger.info(f"📊 Excel log file exists: {os.path.abspath(EXCEL_LOG_FILE)}")

def log_generation(user_id: int, username: str, first_name: str, last_name: str,
                   input_type: str, input_length: int, success: bool, 
                   error_message: str = '', is_premium: bool = False):
    """Log generasi PDF ke Excel"""
    try:
        df = pd.read_excel(EXCEL_LOG_FILE)
        new_row = {
            'timestamp': datetime.now(),
            'user_id': user_id,
            'username': username or '',
            'first_name': first_name or '',
            'last_name': last_name or '',
            'input_type': input_type,
            'input_length': input_length,
            'success': success,
            'error_message': error_message,
            'is_premium': is_premium
        }
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        df.to_excel(EXCEL_LOG_FILE, index=False)
        
        total_records = len(df)
        logger.info(f"✅ Logged generation for user {user_id} (Total records: {total_records})")
        
        # Auto backup jika enabled
        if AUTO_BACKUP_ENABLED:
            check_and_backup()
            
    except Exception as e:
        logger.error(f"❌ Error logging to Excel: {e}")

def backup_excel():
    """Backup file Excel dengan timestamp"""
    try:
        if not Path(EXCEL_LOG_FILE).exists():
            logger.warning("No Excel file to backup")
            return None
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_filename = f"user_generations_backup_{timestamp}.xlsx"
        backup_path = os.path.join(BACKUP_DIR, backup_filename)
        
        # Copy file
        import shutil
        shutil.copy2(EXCEL_LOG_FILE, backup_path)
        
        logger.info(f"✅ Backup created: {backup_path}")
        return backup_path
    except Exception as e:
        logger.error(f"❌ Error creating backup: {e}")
        return None

def check_and_backup():
    """Cek apakah perlu backup otomatis"""
    backup_files = sorted(Path(BACKUP_DIR).glob('user_generations_backup_*.xlsx'))
    
    if not backup_files:
        # Belum ada backup, buat backup pertama
        backup_excel()
        return
    
    # Cek backup terakhir
    latest_backup = backup_files[-1]
    backup_time_str = latest_backup.stem.split('_')[-2] + latest_backup.stem.split('_')[-1]
    
    try:
        backup_time = datetime.strptime(backup_time_str, '%Y%m%d%H%M%S')
        hours_since_backup = (datetime.now() - backup_time).total_seconds() / 3600
        
        if hours_since_backup >= BACKUP_INTERVAL_HOURS:
            logger.info(f"⏰ Last backup was {hours_since_backup:.1f} hours ago, creating new backup...")
            backup_excel()
    except Exception as e:
        logger.error(f"Error parsing backup time: {e}")

def get_excel_stats() -> dict:
    """Dapatkan statistik dari Excel log"""
    try:
        if not Path(EXCEL_LOG_FILE).exists():
            return {
                'total_records': 0,
                'total_users': 0,
                'successful_conversions': 0,
                'failed_conversions': 0,
                'premium_users': 0
            }
        
        df = pd.read_excel(EXCEL_LOG_FILE)
        
        return {
            'total_records': len(df),
            'total_users': df['user_id'].nunique(),
            'successful_conversions': len(df[df['success'] == True]),
            'failed_conversions': len(df[df['success'] == False]),
            'premium_users': df[df['is_premium'] == True]['user_id'].nunique(),
            'file_size_mb': os.path.getsize(EXCEL_LOG_FILE) / (1024 * 1024)
        }
    except Exception as e:
        logger.error(f"Error getting Excel stats: {e}")
        return {}

# ==================== QUOTA & RATE LIMITING ====================

def init_user_quota(user_id: int):
    """Inisialisasi quota user"""
    if user_id not in user_quota:
        user_quota[user_id] = {
            'daily_count': 0,
            'hourly_count': 0,
            'last_reset': datetime.now(),
            'hourly_reset': datetime.now(),
            'is_premium': False
        }

def reset_quota_if_needed(user_id: int):
    """Reset quota jika sudah lewat periode"""
    init_user_quota(user_id)
    now = datetime.now()
    
    # Reset daily quota (tengah malam)
    if now.date() > user_quota[user_id]['last_reset'].date():
        user_quota[user_id]['daily_count'] = 0
        user_quota[user_id]['last_reset'] = now
        logger.info(f"Daily quota reset for user {user_id}")
    
    # Reset hourly quota
    if now >= user_quota[user_id]['hourly_reset'] + timedelta(hours=1):
        user_quota[user_id]['hourly_count'] = 0
        user_quota[user_id]['hourly_reset'] = now
        logger.info(f"Hourly quota reset for user {user_id}")

def check_quota(user_id: int) -> tuple[bool, str]:
    """
    Cek apakah user masih punya quota
    Returns: (can_proceed, message)
    """
    reset_quota_if_needed(user_id)
    
    # Premium user unlimited
    if user_quota[user_id]['is_premium']:
        return True, ""
    
    # Cek hourly limit
    if user_quota[user_id]['hourly_count'] >= HOURLY_RATE_LIMIT:
        wait_time = user_quota[user_id]['hourly_reset'] + timedelta(hours=1) - datetime.now()
        minutes = int(wait_time.total_seconds() / 60)
        return False, f"⏰ Rate limit tercapai! Tunggu {minutes} menit lagi.\n\n💎 Upgrade ke Premium untuk unlimited access!"
    
    # Cek daily quota
    if user_quota[user_id]['daily_count'] >= FREE_DAILY_QUOTA:
        return False, f"📊 Quota harian habis ({FREE_DAILY_QUOTA}/{FREE_DAILY_QUOTA})!\n\n💎 Upgrade ke Premium untuk unlimited quota!"
    
    return True, ""

def increment_quota(user_id: int):
    """Increment quota setelah generate"""
    user_quota[user_id]['daily_count'] += 1
    user_quota[user_id]['hourly_count'] += 1

def get_quota_status(user_id: int) -> str:
    """Get status quota user"""
    reset_quota_if_needed(user_id)
    
    if user_quota[user_id]['is_premium']:
        return "💎 Status: Premium (Unlimited)\n✨ Tidak ada batasan quota!"
    
    daily_remaining = FREE_DAILY_QUOTA - user_quota[user_id]['daily_count']
    hourly_remaining = HOURLY_RATE_LIMIT - user_quota[user_id]['hourly_count']
    
    return (
        f"📊 Quota Status:\n"
        f"├─ Harian: {user_quota[user_id]['daily_count']}/{FREE_DAILY_QUOTA} (sisa {daily_remaining})\n"
        f"├─ Per Jam: {user_quota[user_id]['hourly_count']}/{HOURLY_RATE_LIMIT} (sisa {hourly_remaining})\n"
        f"└─ Status: Free User\n\n"
        f"💡 Tip: Gunakan /premium untuk upgrade!"
    )

# ==================== PAYMENT (PSEUDO) ====================

async def process_payment_pseudo(user_id: int, payment_method: str, amount: float) -> tuple[bool, str]:
    """
    Fungsi pseudo untuk proses pembayaran
    Di production, ini akan integrate dengan payment gateway
    """
    # Simulasi payment processing
    logger.info(f"Processing payment for user {user_id}: {payment_method} - ${amount}")
    
    # Untuk testing, selalu return success
    # Di production, ini akan hit payment gateway API
    success = True
    transaction_id = f"TRX-{user_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    
    if success:
        # Activate premium
        init_user_quota(user_id)
        user_quota[user_id]['is_premium'] = True
        return True, f"✅ Pembayaran berhasil!\n🎫 Transaction ID: {transaction_id}"
    else:
        return False, "❌ Pembayaran gagal. Silakan coba lagi."

# ==================== PDF SERVICE API ====================

def fetch_markdown_from_url(url: str) -> Optional[str]:
    """Fetch markdown dari URL (GitHub, raw file, etc)"""
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.text
    except Exception as e:
        logger.error(f"Error fetching URL: {e}")
        return None

def convert_markdown_to_pdf_via_api(markdown_content: str, output_path: str) -> tuple[bool, str]:
    """
    Kirim markdown ke PDF service dan save hasilnya
    Returns: (success, error_message)
    """
    try:
        logger.info(f"Sending markdown to PDF service: {PDF_SERVICE_URL}")
        
        response = requests.post(
            PDF_SERVICE_URL,
            headers={'Content-Type': 'text/plain'},
            data=markdown_content.encode('utf-8'),
            timeout=30
        )
        
        response.raise_for_status()
        
        # Save PDF
        with open(output_path, 'wb') as f:
            f.write(response.content)
        
        logger.info(f"PDF saved successfully: {output_path}")
        return True, ""
        
    except requests.exceptions.Timeout:
        return False, "Timeout: PDF service tidak merespon"
    except requests.exceptions.ConnectionError:
        return False, "Connection Error: Tidak dapat terhubung ke PDF service"
    except requests.exceptions.HTTPError as e:
        return False, f"HTTP Error: {e.response.status_code}"
    except Exception as e:
        logger.error(f"Error converting to PDF: {e}", exc_info=True)
        return False, str(e)

# ==================== TELEGRAM HANDLERS ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk command /start"""
    user_id = update.effective_user.id
    user_states[user_id] = 'waiting_input'
    user_markdown[user_id] = []
    init_user_quota(user_id)
    
    quota_status = get_quota_status(user_id)
    
    await update.message.reply_text(
        "🎨 Selamat datang di Markdown to PDF Bot!\n\n"
        "📥 Cara Pakai:\n"
        "├─ Kirim teks Markdown langsung\n"
        "├─ Kirim file .md atau .txt\n"
        "├─ Kirim link GitHub (raw markdown)\n"
        "└─ Gunakan /convert untuk buat PDF\n\n"
        "⚡ Commands:\n"
        "├─ /status - Cek markdown & quota\n"
        "├─ /quota - Lihat quota detail\n"
        "├─ /premium - Upgrade ke Premium\n"
        "└─ /cancel - Batalkan proses\n\n"
        f"{quota_status}"
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk command /cancel"""
    user_id = update.effective_user.id
    if user_id in user_states:
        del user_states[user_id]
    if user_id in user_markdown:
        del user_markdown[user_id]
    
    await update.message.reply_text(
        "❌ Proses dibatalkan. Gunakan /start untuk memulai lagi."
    )

async def quota_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk command /quota"""
    user_id = update.effective_user.id
    status = get_quota_status(user_id)
    await update.message.reply_text(status)

async def premium_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk command /premium"""
    user_id = update.effective_user.id
    init_user_quota(user_id)
    
    if user_quota[user_id]['is_premium']:
        await update.message.reply_text(
            "💎 Anda sudah Premium!\n\n"
            "✨ Benefit Premium:\n"
            "├─ Unlimited daily quota\n"
            "├─ No hourly rate limit\n"
            "├─ Priority processing\n"
            "└─ Advanced features access"
        )
        return
    
    await update.message.reply_text(
        "💎 Upgrade ke Premium!\n\n"
        "✨ Benefits:\n"
        "├─ Unlimited daily quota\n"
        "├─ No hourly rate limit\n"
        "├─ Priority processing\n"
        "└─ Advanced features access\n\n"
        "💰 Harga: Rp 10.000/bulan\n\n"
        "🔐 Untuk aktivasi Premium, gunakan:\n"
        "/activate_premium <payment_method>\n\n"
        "Contoh: /activate_premium credit_card\n\n"
        "⚠️ Catatan: Ini fitur pseudo untuk demo"
    )

async def activate_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk aktivasi premium (pseudo)"""
    user_id = update.effective_user.id
    
    if not context.args:
        await update.message.reply_text(
            "❌ Format salah!\n\n"
            "Gunakan: /activate_premium <payment_method>\n"
            "Contoh: /activate_premium credit_card \n\n"
            "⚠️ Catatan: Ini fitur pseudo untuk *demo* aja"
        )
        return
    
    # payment_method = context.args[0]
    
    # Process payment (pseudo)
    # success, message = await process_payment_pseudo(user_id, payment_method, 9.99)
    
    # if success:
    #     await update.message.reply_text(
    #         f"{message}\n\n"
    #         "🎉 Premium berhasil diaktifkan!\n"
    #         "✨ Sekarang Anda memiliki akses unlimited!"
    #     )
    # else:
    #     await update.message.reply_text(message)

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk command /stats - statistik Excel (admin only)"""
    # Simple admin check - bisa diganti dengan list admin user_id
    ADMIN_IDS = [int(x) for x in os.getenv('ADMIN_USER_IDS', '896847229').split(',') if x]
    
    if ADMIN_IDS and update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Command ini hanya untuk admin.")
        return
    
    stats = get_excel_stats()
    
    await update.message.reply_text(
        f"📊 **Excel Database Statistics**\n\n"
        f"📁 File: `{os.path.basename(EXCEL_LOG_FILE)}`\n"
        f"📍 Path: `{os.path.abspath(EXCEL_LOG_FILE)}`\n"
        f"💾 Size: {stats.get('file_size_mb', 0):.2f} MB\n\n"
        f"📈 Records:\n"
        f"├─ Total: {stats.get('total_records', 0)}\n"
        f"├─ Successful: {stats.get('successful_conversions', 0)}\n"
        f"└─ Failed: {stats.get('failed_conversions', 0)}\n\n"
        f"👥 Users:\n"
        f"├─ Total: {stats.get('total_users', 0)}\n"
        f"└─ Premium: {stats.get('premium_users', 0)}\n\n"
        f"💾 Backup Dir: `{os.path.abspath(BACKUP_DIR)}`",
        parse_mode='Markdown'
    )

def escape_markdown_v2(text: str) -> str:
    # Escape semua karakter spesial di MarkdownV2
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text or "")

async def my_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    first_name_clean = escape_markdown_v2(user.first_name)
    last_name_clean = escape_markdown_v2(user.last_name)
    username_clean = escape_markdown_v2(user.username)
    
    user_info = (
        "👤 *Info Akun Anda:*\n\n"
        f"🆔 *User ID:* `{user.id}`\n"
        f"📛 *Nama:* {first_name_clean} {last_name_clean}\n"
        f"👤 *Username:* @{username_clean}\n"
        f"📞 *Language:* {escape_markdown_v2(user.language_code or 'tidak diketahui')}\n\n"
        "*Salin User ID ini untuk keperluan admin:*\n"
        f"`{user.id}`"
    )
    
    await update.message.reply_text(user_info, parse_mode='MarkdownV2')


async def admin_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk command /backup - manual backup (admin only)"""
    ADMIN_IDS = [int(x) for x in os.getenv('ADMIN_USER_IDS', '').split(',') if x]
    
    if ADMIN_IDS and update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Command ini hanya untuk admin.")
        return
    
    msg = await update.message.reply_text("⏳ Creating backup...")
    
    backup_path = backup_excel()
    
    if backup_path:
        # Kirim file backup
        with open(backup_path, 'rb') as f:
            await update.message.reply_document(
                document=f,
                filename=os.path.basename(backup_path),
                caption=f"✅ Backup berhasil dibuat!\n📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
        await msg.delete()
    else:
        await msg.edit_text("❌ Gagal membuat backup.")

async def convert_to_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk command /convert"""
    user_id = update.effective_user.id
    user = update.effective_user
    
    # Cek state
    if user_id not in user_states or user_id not in user_markdown:
        await update.message.reply_text(
            "Tidak ada markdown untuk dikonversi. Gunakan /start untuk memulai."
        )
        return
    
    if not user_markdown[user_id]:
        await update.message.reply_text(
            "Anda belum mengirim markdown apapun. Kirim teks/file markdown terlebih dahulu."
        )
        return
    
    # Cek quota
    can_proceed, quota_message = check_quota(user_id)
    if not can_proceed:
        await update.message.reply_text(quota_message)
        return
    
    # Gabungkan semua markdown
    full_markdown = "\n\n".join(user_markdown[user_id])
    
    # Loading message
    loading_msg = await update.message.reply_text("⏳ Mengirim ke PDF service...")
    
    pdf_path = None
    success = False
    error_message = ""
    
    try:
        # Buat temp file untuk PDF
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as pdf_file:
            pdf_path = pdf_file.name
        
        # Convert via API
        success, error_message = convert_markdown_to_pdf_via_api(full_markdown, pdf_path)
        
        if success:
            # Kirim PDF
            with open(pdf_path, 'rb') as pdf:
                await update.message.reply_document(
                    document=pdf,
                    filename='markdown_converted.pdf',
                    caption=f"✅ Konversi berhasil!\n📄 Input: {len(user_markdown[user_id])} pesan"
                )
            
            # Increment quota
            increment_quota(user_id)
            
            await loading_msg.delete()
            
            # Show remaining quota
            quota_info = get_quota_status(user_id)
            await update.message.reply_text(
                f"✨ Selesai!\n\n{quota_info}\n\nGunakan /start untuk konversi lagi."
            )
            
            # Reset state
            del user_states[user_id]
            del user_markdown[user_id]
        else:
            await loading_msg.edit_text(
                f"❌ Gagal konversi ke PDF:\n{error_message}\n\n"
                "Silakan coba lagi dengan /start"
            )
        
    except Exception as e:
        logger.error(f"Error in convert_to_pdf: {e}", exc_info=True)
        error_message = str(e)
        await update.message.reply_text(
            f"❌ Terjadi kesalahan:\n{error_message}\n\nGunakan /start untuk mencoba lagi."
        )
    
    finally:
        # Log generation
        log_generation(
            user_id=user_id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            input_type=message_types.get(user_id, 'unknown'),
            input_length=len(full_markdown),
            success=success,
            error_message=error_message,
            is_premium=user_quota.get(user_id, {}).get('is_premium', False)
        )
        
        # Cleanup temp file
        if pdf_path and os.path.exists(pdf_path):
            try:
                os.unlink(pdf_path)
            except Exception as e:
                logger.error(f"Error deleting temp file: {e}")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk teks markdown"""
    user_id = update.effective_user.id
    
    if user_id not in user_states or user_states[user_id] != 'waiting_input':
        welcome_guide = (
            "👋 **Halo! Mari mulai konversi Markdown ke PDF**\n\n"
            
            "📖 **Panduan Singkat:**\n"
            "1. **Ketik** /start untuk memulai\n"
            "2. **Kirim** konten Markdown dengan cara:\n"
            "   • 📝 **Teks langsung** - ketik markdown\n"
            "   • 📎 **File** - upload file .md/.txt\n"
            "   • 🔗 **URL** - link GitHub/GitLab\n"
            "3. **Konversi** dengan /convert\n\n"
            
            "⚡ **Contoh penggunaan:**\n"
            "/start → kirim teks markdown → /convert\n\n"
            
            "🎯 **Fitur unggulan:**\n"
            "• ✅ Support GitHub/GitLab URLs\n"
            "• 📊 Multiple konten dalam 1 PDF\n"
            "• 🎨 Formatting lengkap\n"
            "• 🔒 Privasi terjamin\n\n"
            
            "**Ketik /start sekarang untuk memulai!** 🚀"
        )
        
        await update.message.reply_text(welcome_guide, parse_mode='Markdown')
        return
    
    text = update.message.text
    
    # Cek apakah ini URL
    if text.startswith('http://') or text.startswith('https://'):
        loading_msg = await update.message.reply_text(
            "🔗 **Mendeteksi URL...**\n"
            "⏳ Mengambil konten markdown..."
        )
        
        # Convert URL ke raw URL jika diperlukan
        raw_url = convert_to_raw_url(text)
        
        markdown_content = fetch_markdown_from_url(raw_url)
        
        message_types[user_id] = 'url'
        
        if markdown_content:
            user_markdown[user_id].append(markdown_content)
            await loading_msg.edit_text(
                "✅ **Berhasil mengambil konten dari URL!**\n\n"
                f"📊 **Detail:**\n"
                f"• 📏 Panjang: {len(markdown_content):,} karakter\n"
                f"• 📑 Baris: {markdown_content.count(chr(10)) + 1}\n"
                f"• 💾 Ukuran: {len(markdown_content.encode('utf-8')) / 1024:.1f} KB\n\n"
                "**Langkah selanjutnya:**\n"
                "• Kirim lebih banyak konten, atau\n"
                "• Gunakan /convert untuk buat PDF\n"
                "• Cek /status untuk melihat semua konten",
                parse_mode='Markdown'
            )
        else:
            await loading_msg.edit_text(
                "❌ **Gagal mengambil konten dari URL**\n\n"
                "**Penyebab mungkin:**\n"
                "• URL tidak valid/tidak bisa diakses\n"
                "• Bukan konten markdown\n"
                "• Butuh authentication\n"
                "• File terlalu besar\n\n"
                "**Tips:**\n"
                "• Pastikan URL publik dan bisa diakses\n"
                "• Untuk GitHub, gunakan format:\n"
                "  `https://github.com/user/repo/blob/main/file.md`\n"
                "• Atau kirim teks markdown langsung"
            )
    else:
        # Teks biasa
        if user_id not in user_markdown:
            user_markdown[user_id] = []
        
        user_markdown[user_id].append(text)
        message_types[user_id] = 'text'
        total = len(user_markdown[user_id])
        
        # Hitung statistik
        total_chars = sum(len(content) for content in user_markdown[user_id])
        total_lines = sum(content.count(chr(10)) + 1 for content in user_markdown[user_id])
        
        response_message = (
            f"✅ **Konten ke-{total} berhasil disimpan!**\n\n"
            f"📊 **Statistik terkini:**\n"
            f"• 📝 Jumlah pesan: {total}\n"
            f"• 📏 Total karakter: {total_chars:,}\n"
            f"• 📑 Total baris: {total_lines}\n"
            f"• 💾 Total ukuran: {total_chars / 1024:.1f} KB\n\n"
            "**Apa selanjutnya?**\n"
            "• ➕ Kirim lebih banyak konten\n"
            "• 📄 Gunakan `/convert` untuk buat PDF\n"
            "• 👀 Gunakan `/status` untuk review\n"
            "• 🗑️  Gunakan `/cancel` untuk reset\n\n"
            "**Tips:** Bisa kirim file .md, URL GitHub, atau teks markdown langsung!"
        )
        
        await update.message.reply_text(
            response_message,
            reply_to_message_id=update.message.message_id
        )


def convert_to_raw_url(url: str) -> str:
    """
    Convert berbagai URL GitHub ke raw URL
    
    Args:
        url: URL yang akan di-convert
        
    Returns:
        URL raw untuk mengakses konten langsung
    """
    from urllib.parse import urlparse
    
    parsed = urlparse(url)
    
    # GitHub blob URL -> raw URL
    if parsed.hostname == 'github.com' and '/blob/' in parsed.path:
        return url.replace('github.com', 'raw.githubusercontent.com').replace('/blob/', '/')
    
    # GitHub gist URL
    if parsed.hostname == 'gist.github.com':
        # Hapus trailing slash jika ada dan tambahkan /raw
        gist_url = url.rstrip('/')
        return f'{gist_url}/raw'
    
    # GitLab blob URL -> raw URL
    if parsed.hostname == 'gitlab.com' and '/blob/' in parsed.path:
        return url.replace('/blob/', '/raw/')
    
    # Bitbucket URL -> raw URL
    if parsed.hostname == 'bitbucket.org' and '/src/' in parsed.path:
        # Ganti /src/ dengan /raw/ dan tambahkan parameter ?at=default jika perlu
        path_parts = parsed.path.split('/')
        if len(path_parts) > 4:
            # Format: /workspace/repo/src/branch/path -> /workspace/repo/raw/branch/path
            src_index = path_parts.index('src')
            if src_index != -1 and len(path_parts) > src_index + 1:
                path_parts[src_index] = 'raw'
                new_path = '/'.join(path_parts)
                return f'{parsed.scheme}://{parsed.hostname}{new_path}'
    
    return url

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk file .md atau .txt"""
    user_id = update.effective_user.id
    
    if user_id not in user_states or user_states[user_id] != 'waiting_input':
        await update.message.reply_text(
            "Gunakan /start untuk memulai konversi Markdown ke PDF."
        )
        return
    
    document: Document = update.message.document
    file_name = document.file_name.lower()
    
    # Cek ekstensi file
    if not (file_name.endswith('.md') or file_name.endswith('.txt')):
        await update.message.reply_text(
            "❌ Hanya file .md atau .txt yang didukung!"
        )
        return
    
    loading = await update.message.reply_text("⏳ Memproses file...")
    
    try:
        # Download file
        file = await context.bot.get_file(document.file_id)
        
        # Read content
        with tempfile.NamedTemporaryFile(delete=False, suffix='.txt') as temp_file:
            await file.download_to_drive(temp_file.name)
            
            with open(temp_file.name, 'r', encoding='utf-8') as f:
                content = f.read()
            
            os.unlink(temp_file.name)
        
        # Simpan markdown
        if user_id not in user_markdown:
            user_markdown[user_id] = []
        
        user_markdown[user_id].append(content)
        message_types[user_id] = 'file'
        await loading.edit_text(
            f"✅ File '{document.file_name}' berhasil diproses!\n"
            f"📏 Panjang: {len(content)} karakter\n\n"
            f"Gunakan /convert untuk buat PDF."
        )
        
    except Exception as e:
        logger.error(f"Error processing document: {e}", exc_info=True)
        await loading.edit_text(f"❌ Gagal memproses file: {str(e)}")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk command /status"""
    user_id = update.effective_user.id
    
    if user_id not in user_markdown or not user_markdown[user_id]:
        await update.message.reply_text(
            "Belum ada markdown yang dikirim. Gunakan /start untuk memulai."
        )
        return
    
    total_messages = len(user_markdown[user_id])
    total_chars = sum(len(msg) for msg in user_markdown[user_id])
    
    preview = user_markdown[user_id][0][:150]
    if len(user_markdown[user_id][0]) > 150:
        preview += "..."
    
    quota_info = get_quota_status(user_id)
    
    await update.message.reply_text(
        f"📊 Status Markdown:\n"
        f"├─ Total input: {total_messages}\n"
        f"└─ Total karakter: {total_chars}\n\n"
        f"📄 Preview:\n{preview}\n\n"
        f"{quota_info}\n\n"
        f"Gunakan /convert untuk buat PDF atau /cancel untuk batal."
    )

def main():
    """Main function"""
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN tidak ditemukan!")
        return
    
    # Init Excel log
    init_excel_log()
    
    # Buat aplikasi
    application = Application.builder().token(token).build()
    
    # Tambahkan handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("convert", convert_to_pdf))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("quota", quota_status))
    application.add_handler(CommandHandler("premium", premium_info))
    application.add_handler(CommandHandler("activate_premium", activate_premium))
    application.add_handler(CommandHandler("stats", admin_stats))
    application.add_handler(CommandHandler("backup", admin_backup))
    application.add_handler(CommandHandler("myid", my_id))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    # Jalankan bot
    logger.info("=" * 60)
    logger.info("🤖 Bot started successfully!")
    logger.info("=" * 60)
    logger.info(f"📊 Excel Log: {os.path.abspath(EXCEL_LOG_FILE)}")
    logger.info(f"💾 Backup Dir: {os.path.abspath(BACKUP_DIR)}")
    logger.info(f"🔧 PDF Service: {PDF_SERVICE_URL}")
    logger.info(f"📈 Daily Quota: {FREE_DAILY_QUOTA}")
    logger.info(f"⏱️  Hourly Limit: {HOURLY_RATE_LIMIT}")
    logger.info(f"💾 Auto Backup: {'ON' if AUTO_BACKUP_ENABLED else 'OFF'} (every {BACKUP_INTERVAL_HOURS}h)")
    logger.info("=" * 60)
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
