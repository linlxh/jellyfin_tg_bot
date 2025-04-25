import aiosqlite
import asyncio
import logging
import sqlite3
import secrets
import string
from datetime import datetime, timedelta
import requests
from telegram import Update, BotCommand
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import nest_asyncio

# Configuration
TOKEN = '7491740941:AAEx2hNxSuVLdgeHYJo9g7u5i1eKR2_AcGA'
JELLYFIN_URL = 'http://143.110.155.41:8097'
ADMIN_API_KEY = 'd01f07969a404dec82e53ffc72147253'
ADMIN_IDS = {1236259428}
DB_PATH = 'bot_data.db'

# 初始化日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 初始化数据库
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cursor = conn.cursor()
lock = asyncio.Lock()

# 创建数据表及索引
cursor.executescript('''
CREATE TABLE IF NOT EXISTS invites (
    code TEXT PRIMARY KEY,
    type TEXT CHECK(type IN ('1d', '1m', '1y', 'perm')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    password TEXT NOT NULL,
    registered_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    tg_id INTEGER UNIQUE,
    expires_at DATETIME
);

CREATE INDEX IF NOT EXISTS idx_expires ON users (expires_at);
CREATE INDEX IF NOT EXISTS idx_tg_id ON users (tg_id);
''')
conn.commit()

async def generate_invite_code(code_type: str) -> str:
    """生成邀请码"""
    if code_type not in {'1d', '1m', '1y', 'perm'}:
        raise ValueError("Invalid invite type")
    
    alphabet = string.ascii_uppercase + string.digits
    code = ''.join(secrets.choice(alphabet) for _ in range(10))
    
    async with lock:
        cursor.execute("INSERT INTO invites (code, type) VALUES (?, ?)", (code, code_type))
        conn.commit()
    
    return code

async def validate_invite(code: str) -> tuple[bool, datetime | None]:
    """验证邀请码"""
    async with lock:
        cursor.execute("SELECT type FROM invites WHERE code = ?", (code,))
        row = cursor.fetchone()
    
    if not row:
        return False, None
    
    code_type = row[0]
    now = datetime.utcnow()
    
    if code_type == 'perm':
        return True, None
    else:
        delta_map = {
            '1d': timedelta(days=1),
            '1m': timedelta(days=30),
            '1y': timedelta(days=365)
        }
        return True, now + delta_map[code_type]

async def remove_invite(code: str):
    """删除邀请码"""
    async with lock:
        cursor.execute("DELETE FROM invites WHERE code = ?", (code,))
        conn.commit()

async def register_jellyfin_user(username: str, password: str) -> bool:
    """注册Jellyfin用户"""
    headers = {
        'X-Emby-Token': ADMIN_API_KEY,
        'Content-Type': 'application/json'
    }
    payload = {
        "Name": username,
        "Password": password,
        "Policy": {
            "IsAdministrator": False,
            "IsDisabled": False,
            "EnableContentDownloading": True,
            "EnableAllFolders": True
        }
    }
    
    try:
        response = requests.post(
            f"{JELLYFIN_URL}/Users/New",
            json=payload,
            headers=headers,
            timeout=10
        )
        return response.status_code == 200
    except Exception as e:
        logger.error(f"Jellyfin注册失败: {str(e)}")
        return False

def get_jellyfin_user_id(username: str) -> str | None:
    """获取Jellyfin用户ID"""
    headers = {'X-Emby-Token': ADMIN_API_KEY}
    try:
        response = requests.get(f"{JELLYFIN_URL}/Users", headers=headers)
        for user in response.json():
            if user['Name'] == username:
                return user['Id']
        return None
    except Exception as e:
        logger.error(f"获取用户ID失败: {str(e)}")
        return None

async def auto_delete_expired_accounts():
    """自动删除过期账号"""
    while True:
        try:
            now = datetime.utcnow()
            async with lock:
                cursor.execute("""
                    SELECT username 
                    FROM users 
                    WHERE expires_at < ? AND expires_at IS NOT NULL
                """, (now,))
                expired_accounts = cursor.fetchall()
            
            deleted_count = 0
            for (username,) in expired_accounts:
                # 删除Jellyfin账号
                if user_id := get_jellyfin_user_id(username):
                    headers = {'X-Emby-Token': ADMIN_API_KEY}
                    requests.delete(
                        f"{JELLYFIN_URL}/Users/{user_id}",
                        headers=headers,
                        timeout=5
                    )
                
                # 删除本地记录
                async with lock:
                    cursor.execute("DELETE FROM users WHERE username = ?", (username,))
                    conn.commit()
                
                deleted_count += 1
                logger.info(f"已自动删除过期账号: {username}")

            await asyncio.sleep(3600)  # 每小时检查一次
            
            if deleted_count > 0:
                logger.info(f"本次清理完成，删除账号数: {deleted_count}")

        except Exception as e:
            logger.error(f"自动删除任务异常: {str(e)}")
            await asyncio.sleep(300)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """启动命令"""
    await update.message.reply_text(
        "🎉 欢迎使用Jellyfin账号机器人\n\n"
        "🔑 注册账号：/register <邀请码> <用户名> <密码>\n"
        "🔍 查询信息：/query_credentials\n"
        "⏳ 账号到期后会自动删除"
    )

async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """用户注册"""
    try:
        user_id = update.effective_user.id
        code, username, password = context.args

        # 检查用户是否已注册
        async with lock:
            cursor.execute("SELECT username FROM users WHERE tg_id = ?", (user_id,))
            if existing := cursor.fetchone():
                await update.message.reply_text(
                    f"⚠️ 您已注册过账号：{existing[0]}\n"
                    "每个用户只能拥有一个账号\n"
                    "使用 /query_credentials 查看详情"
                )
                return

        # 验证邀请码
        valid, expire_time = await validate_invite(code)
        if not valid:
            await update.message.reply_text("❌ 无效或已使用的邀请码")
            return

        # 检查用户名
        if len(password) < 6:
            await update.message.reply_text("⚠️ 密码至少需要6个字符")
            return

        # 注册Jellyfin账号
        if not await register_jellyfin_user(username, password):
            await update.message.reply_text("🔧 注册失败，请联系管理员")
            return

        # 保存记录
        expire_str = expire_time.isoformat() if expire_time else None
        async with lock:
            cursor.execute("""
                INSERT INTO users (username, password, tg_id, expires_at)
                VALUES (?, ?, ?, ?)
            """, (username, password, user_id, expire_str))
            conn.commit()
        
        await remove_invite(code)

        # 构造回复信息
        msg = [
            f"✅ 注册成功！",
            f"👤 用户名：{username}",
            f"🔒 密码：{password}",
            f"🌐 访问地址：{JELLYFIN_URL}"
        ]
        if expire_time:
            msg.append(f"⏰ 到期时间：{expire_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")
        await update.message.reply_text("\n".join(msg))

    except ValueError:
        await update.message.reply_text("📝 格式错误，正确格式：/register <邀请码> <用户名> <密码>")
    except Exception as e:
        logger.error(f"注册异常：{str(e)}")
        await update.message.reply_text("⚠️ 系统错误，请联系管理员")

async def query_credentials(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查询账号信息"""
    user_id = update.effective_user.id
    async with lock:
        cursor.execute("""
            SELECT username, password, expires_at 
            FROM users 
            WHERE tg_id = ?
        """, (user_id,))
        rows = cursor.fetchall()
    
    if not rows:
        await update.message.reply_text("❌ 您尚未注册任何账号")
        return
    
    messages = ["📋 您的账号信息："]
    for username, password, expire_str in rows:
        if expire_str:
            expire_time = datetime.fromisoformat(expire_str)
            expire_info = f"⏳ 到期时间：{expire_time.strftime('%Y-%m-%d %H:%M:%S')} UTC"
        else:
            expire_info = "⏳ 永久有效"
        
        messages.append(
            f"🔑 用户名：{username}\n"
            f"🔒 密码：{password}\n"
            f"{expire_info}\n"
            f"—————————————"
        )
    
    await update.message.reply_text("\n".join(messages))

async def admin_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """管理员查看账号"""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ 权限不足")
        return
    
    async with lock:
        cursor.execute("""
            SELECT username, registered_at, expires_at 
            FROM users 
            ORDER BY registered_at DESC
        """)
        rows = cursor.fetchall()
    
    if not rows:
        await update.message.reply_text("📭 当前没有注册用户")
        return
    
    report = ["📊 用户列表（最近注册优先）"]
    for username, reg_time_str, expire_str in rows:
        reg_time = datetime.fromisoformat(reg_time_str).strftime('%Y-%m-%d')
        
        if expire_str:
            expire_time = datetime.fromisoformat(expire_str)
            expire_info = expire_time.strftime('%Y-%m-%d')
            status = "✅ 有效" if expire_time > datetime.utcnow() else "❌ 已过期"
        else:
            expire_info = "永久"
            status = "✅ 有效"
        
        report.append(
            f"👤 {username.ljust(15)} "
            f"📅 注册：{reg_time} "
            f"⏳ 到期：{expire_info} "
            f"{status}"
        )
    
    await update.message.reply_text("\n".join(report))

async def generate_invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """生成邀请码"""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ 权限不足")
        return

    try:
        invite_type = context.args[0].lower()
        count = int(context.args[1])
        
        if invite_type not in {'1d', '1m', '1y', 'perm'}:
            await update.message.reply_text("❌ 类型错误，可选：1d/1m/1y/perm")
            return
            
        if not 1 <= count <= 50:
            await update.message.reply_text("⚠️ 数量需在1-50之间")
            return

        codes = [await generate_invite_code(invite_type) for _ in range(count)]
        
        # 分批次发送避免消息过长
        for i in range(0, len(codes), 5):
            chunk = codes[i:i+5]
            await update.message.reply_text(
                "🆔 新邀请码生成成功\n" + 
                "\n".join([f"• `{code}` ({invite_type})" for code in chunk])
            )

    except (ValueError, IndexError):
        await update.message.reply_text("📝 格式：/generate_invite <类型> <数量>")
    except Exception as e:
        logger.error(f"邀请码生成失败：{str(e)}")
        await update.message.reply_text("⚠️ 生成失败，请查看日志")

async def delete_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """删除账号"""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ 权限不足")
        return

    try:
        username = context.args[0]
        if user_id := get_jellyfin_user_id(username):
            headers = {'X-Emby-Token': ADMIN_API_KEY}
            requests.delete(f"{JELLYFIN_URL}/Users/{user_id}", headers=headers)
        
        async with lock:
            cursor.execute("DELETE FROM users WHERE username = ?", (username,))
            conn.commit()
        
        await update.message.reply_text(f"✅ 用户 {username} 已删除")

    except IndexError:
        await update.message.reply_text("📝 格式：/delete_account <用户名>")
    except Exception as e:
        logger.error(f"删除账号失败：{str(e)}")
        await update.message.reply_text("⚠️ 删除失败，请检查用户名是否正确")

async def main():
    """主程序"""
    app = ApplicationBuilder().token(TOKEN).build()
    
    # 启动自动清理任务
    asyncio.create_task(auto_delete_expired_accounts())
    
    # 注册命令
    handlers = [
        CommandHandler("start", start),
        CommandHandler("register", register),
        CommandHandler("query_credentials", query_credentials),
        CommandHandler("admin_accounts", admin_accounts),
        CommandHandler("generate_invite", generate_invite),
        CommandHandler("delete_account", delete_account)
    ]
    for handler in handlers:
        app.add_handler(handler)
    
    # 设置菜单命令
    await app.bot.set_my_commands([
        BotCommand("start", "显示帮助信息"),
        BotCommand("register", "注册新账号"),
        BotCommand("query_credentials", "查询账号信息"),
        BotCommand("admin_accounts", "查看所有用户（管理员）"),
        BotCommand("generate_invite", "生成邀请码（管理员）"),
        BotCommand("delete_account", "删除账号（管理员）")
    ])
    
    logger.info("机器人已启动")
    await app.run_polling()

if __name__ == "__main__":
    nest_asyncio.apply()
    asyncio.run(main())