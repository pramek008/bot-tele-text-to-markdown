import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import tempfile
import markdown
from playwright.async_api import async_playwright
from pygments.formatters import HtmlFormatter

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# User states - menyimpan markdown text yang sedang dikumpulkan
user_states = {}
user_markdown = {}

# HTML Template untuk PDF dengan syntax highlighting
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
        }}
        h1, h2, h3, h4, h5, h6 {{
            color: #2c3e50;
            margin-top: 24px;
            margin-bottom: 16px;
            page-break-after: avoid;
        }}
        h1 {{ font-size: 2em; border-bottom: 2px solid #eee; padding-bottom: 10px; }}
        h2 {{ font-size: 1.5em; border-bottom: 1px solid #eee; padding-bottom: 8px; }}
        
        /* Inline code */
        code {{
            background-color: #f4f4f4;
            padding: 2px 6px;
            border-radius: 3px;
            font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
            font-size: 0.9em;
            color: #c7254e;
        }}
        
        /* Code blocks - no border, smooth background for page breaks */
        pre {{
            background-color: #f8f8f8;
            padding: 16px;
            overflow-x: auto;
            font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
            font-size: 0.9em;
            line-height: 1.5;
            page-break-inside: auto;
            margin: 16px 0;
        }}
        pre code {{
            background-color: transparent;
            padding: 0;
            color: inherit;
            border-radius: 0;
        }}
        
        /* Pygments syntax highlighting */
        {pygments_css}
        
        blockquote {{
            border-left: 4px solid #ddd;
            padding-left: 16px;
            color: #666;
            margin: 16px 0;
            page-break-inside: avoid;
        }}
        ul, ol {{
            padding-left: 30px;
        }}
        li {{
            margin: 8px 0;
        }}
        a {{
            color: #3498db;
            text-decoration: none;
        }}
        a:hover {{
            text-decoration: underline;
        }}
        table {{
            border-collapse: collapse;
            width: 100%;
            margin: 16px 0;
            page-break-inside: avoid;
        }}
        th, td {{
            border: 1px solid #ddd;
            padding: 12px;
            text-align: left;
        }}
        th {{
            background-color: #f4f4f4;
            font-weight: bold;
        }}
        img {{
            max-width: 100%;
            height: auto;
        }}
        
        /* Improve page breaks */
        p {{
            page-break-inside: avoid;
        }}
    </style>
</head>
<body>
    {content}
</body>
</html>
"""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk command /start"""
    user_id = update.effective_user.id
    user_states[user_id] = 'waiting_markdown'
    user_markdown[user_id] = []
    
    await update.message.reply_text(
        "Selamat datang di Markdown to PDF Bot!\n\n"
        "Kirimkan teks Markdown Anda (bisa dalam beberapa pesan).\n"
        "Ketik /convert untuk mengkonversi ke PDF.\n"
        "Ketik /cancel untuk membatalkan.\n\n"
        "Contoh Markdown:\n"
        """
        # Judul Utama\n
        ## Sub Judul\n\n
        Ini adalah **teks tebal** dan *teks miring*.\n\n
        - Item 1\n
        - Item 2\n\n
        Contoh Code Block:\n
        ```python
        def hello_world():
            print("Hello, world!")
        ```\n
        Gunakan /status untuk melihat status markdown yang sudah dikirim.
        """
        ,parse_mode='Markdown'
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

async def convert_to_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk command /convert - konversi markdown ke PDF dengan syntax highlighting"""
    user_id = update.effective_user.id
    
    if user_id not in user_states or user_id not in user_markdown:
        await update.message.reply_text(
            "Tidak ada markdown untuk dikonversi. Gunakan /start untuk memulai."
        )
        return
    
    if not user_markdown[user_id]:
        await update.message.reply_text(
            "Anda belum mengirim markdown apapun. Kirim teks markdown terlebih dahulu."
        )
        return
    
    # Gabungkan semua pesan markdown
    full_markdown = "\n\n".join(user_markdown[user_id])
    
    # Kirim pesan loading
    loading_msg = await update.message.reply_text("⏳ Sedang memproses markdown...")
    
    pdf_path = None
    
    try:
        # Generate CSS untuk syntax highlighting
        formatter = HtmlFormatter(style='default')
        pygments_css = formatter.get_style_defs('.codehilite')
        
        # Convert markdown ke HTML dengan syntax highlighting
        html_content = markdown.markdown(
            full_markdown,
            extensions=[
                'fenced_code',
                'tables',
                'nl2br',
                'codehilite'
            ],
            extension_configs={
                'codehilite': {
                    'css_class': 'codehilite',
                    'linenums': False,
                    'guess_lang': True
                }
            }
        )
        
        # Buat HTML lengkap dengan styling dan syntax highlighting
        full_html = HTML_TEMPLATE.format(
            content=html_content,
            pygments_css=pygments_css
        )
        
        # Buat file temporary untuk PDF
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as pdf_file:
            pdf_path = pdf_file.name
        
        logger.info(f"Converting to PDF: {pdf_path}")
        
        # Gunakan Playwright untuk convert HTML ke PDF
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            
            # Set HTML content
            await page.set_content(full_html)
            
            # Generate PDF
            await page.pdf(
                path=pdf_path,
                format='A4',
                margin={
                    'top': '20mm',
                    'right': '20mm',
                    'bottom': '20mm',
                    'left': '20mm'
                },
                print_background=True
            )
            
            await browser.close()
        
        logger.info("PDF generated successfully")
        
        # Kirim file PDF
        with open(pdf_path, 'rb') as pdf:
            await update.message.reply_document(
                document=pdf,
                filename='markdown_converted.pdf',
                caption=f"✅ Konversi berhasil!\n📄 Total {len(user_markdown[user_id])} pesan dikonversi."
            )
        
        # Hapus pesan loading
        await loading_msg.delete()
        
        # Reset state
        del user_states[user_id]
        del user_markdown[user_id]
        
        await update.message.reply_text(
            "✨ Selesai! Gunakan /start untuk konversi lagi."
        )
        
    except Exception as e:
        logger.error(f"Error converting markdown: {e}", exc_info=True)
        await update.message.reply_text(
            f"❌ Terjadi kesalahan saat konversi:\n{str(e)}\n\n"
            "Gunakan /start untuk mencoba lagi."
        )
        if user_id in user_states:
            del user_states[user_id]
        if user_id in user_markdown:
            del user_markdown[user_id]
    
    finally:
        # Hapus file temporary
        if pdf_path and os.path.exists(pdf_path):
            try:
                os.unlink(pdf_path)
                logger.info(f"Cleaned up temp file: {pdf_path}")
            except Exception as e:
                logger.error(f"Error deleting temp PDF file: {e}")

async def handle_markdown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk menerima markdown text"""
    user_id = update.effective_user.id
    
    if user_id not in user_states or user_states[user_id] != 'waiting_markdown':
        await update.message.reply_text(
            "Gunakan /start untuk memulai konversi Markdown ke PDF."
        )
        return
    
    markdown_text = update.message.text
    
    # Simpan markdown text
    if user_id not in user_markdown:
        user_markdown[user_id] = []
    
    user_markdown[user_id].append(markdown_text)
    
    # Konfirmasi penerimaan
    total_messages = len(user_markdown[user_id])
    await update.message.reply_text(
        f"✅ Pesan ke-{total_messages} diterima!\n\n"
        f"Kirim lebih banyak markdown atau gunakan /convert untuk membuat PDF.",
        reply_to_message_id=update.message.message_id
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk command /status - cek status markdown yang sudah dikirim"""
    user_id = update.effective_user.id
    
    if user_id not in user_markdown or not user_markdown[user_id]:
        await update.message.reply_text(
            "Belum ada markdown yang dikirim. Gunakan /start untuk memulai."
        )
        return
    
    total_messages = len(user_markdown[user_id])
    total_chars = sum(len(msg) for msg in user_markdown[user_id])
    
    preview = user_markdown[user_id][0][:100]
    if len(user_markdown[user_id][0]) > 100:
        preview += "..."
    
    await update.message.reply_text(
        f"📊 Status Markdown:\n\n"
        f"📝 Total pesan: {total_messages}\n"
        f"📏 Total karakter: {total_chars}\n\n"
        f"Preview pesan pertama:\n{preview}\n\n"
        f"Gunakan /convert untuk membuat PDF atau /cancel untuk membatalkan."
    )

def main():
    """Main function"""
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN tidak ditemukan!")
        return
    
    # Buat aplikasi
    application = Application.builder().token(token).build()
    
    # Tambahkan handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("convert", convert_to_pdf))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_markdown))
    
    # Jalankan bot
    logger.info("Bot started...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()