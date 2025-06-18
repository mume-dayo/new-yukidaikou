
import discord
from discord.ext import commands
from discord import app_commands
import json
import os
from typing import Optional
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

# 環境変数から許可ユーザーIDを取得（カンマ区切りの文字列）
allowed_user_ids_str = os.getenv("ALLOWED_USER_IDS", "")
allowed_user_ids = [int(uid.strip()) for uid in allowed_user_ids_str.split(",") if uid.strip().isdigit()]

DATA_FILE = "ticket_data.json"

def load_data():
    if not os.path.exists(DATA_FILE):
        return {"items": [], "open_message": {}}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

class OpenMessageModal(discord.ui.Modal, title="オープンメッセージを設定"):
    def __init__(self, callback):
        super().__init__()
        self.callback_func = callback
        self.add_item(discord.ui.TextInput(label="タイトル", custom_id="title", required=True))
        self.add_item(discord.ui.TextInput(label="説明", custom_id="description", style=discord.TextStyle.paragraph, required=True))

    async def on_submit(self, interaction: discord.Interaction):
        title = self.children[0].value
        description = self.children[1].value
        await self.callback_func(interaction, title, description)

class TicketSelect(discord.ui.Select):
    def __init__(self, options, items, staff_role):
        self.items = items
        self.staff_role = staff_role
        super().__init__(placeholder="ご要件を選択してください", options=options, custom_id="ticket_select")

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id not in allowed_user_ids:
            await interaction.response.send_message("この操作を行う権限がありません。", ephemeral=True)
            return

        selected_value = self.values[0]
        item = next((i for i in self.items if i["value"] == selected_value), None)
        if not item:
            await interaction.response.send_message("エラー：項目が見つかりませんでした。", ephemeral=True)
            return

        category = interaction.guild.get_channel(item["category"])
        if not category or not isinstance(category, discord.CategoryChannel):
            await interaction.response.send_message("カテゴリが存在しないか無効です。", ephemeral=True)
            return

        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            self.staff_role: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }

        channel_name = f"🎫｜{interaction.user.name}"
        ticket_channel = await interaction.guild.create_text_channel(name=channel_name, category=category, overwrites=overwrites)

        data = load_data()
        open_msg = data.get("open_message", {})

        # メンション
        await ticket_channel.send(f"{interaction.user.mention} {self.staff_role.mention}")

        # 埋め込みメッセージ
        embed = discord.Embed(
            title="内容: " + item["label"],
            description=open_msg.get("description", "オープンメッセージが設定されていません。"),
            color=discord.Color.green()
        )
        await ticket_channel.send(embed=embed)

        # 削除ボタン
        await ticket_channel.send(view=DeleteTicketButton())

        # セレクトメニューをリセット
        new_view = TicketView(self.items, self.staff_role)
        await interaction.message.edit(view=new_view)

        await interaction.response.send_message(f"{ticket_channel.mention} チャンネルを作成しました。", ephemeral=True)

class DeleteTicketButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="チケットを削除", style=discord.ButtonStyle.danger, custom_id="delete_ticket_btn")
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.channel.delete()

class TicketView(discord.ui.View):
    def __init__(self, items, staff_role: discord.Role):
        super().__init__(timeout=None)
        options = [
            discord.SelectOption(
                label=item["label"],
                value=item["value"],
                emoji=item["emoji"],
                description=item["description"]
            ) for item in items
        ]
        self.add_item(TicketSelect(options, items, staff_role))

class Ticket(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="ticket_add", description="チケット項目を追加")
    async def ticket_add(self, interaction: discord.Interaction, label: str, description: str, category: discord.CategoryChannel, emoji: str):
        data = load_data()
        data["items"].append({
            "label": label,
            "value": label,
            "description": description,
            "category": category.id,
            "emoji": emoji
        })
        save_data(data)
        await interaction.response.send_message(f"項目「{label}」を追加しました。", ephemeral=True)

    @app_commands.command(name="ticket_setting", description="チケット設定（削除・オープンメッセージ）")
    async def ticket_setting(self, interaction: discord.Interaction):
        data = load_data()
        if not data["items"]:
            await interaction.response.send_message("項目が登録されていません。", ephemeral=True)
            return

        view = discord.ui.View()

        class DeleteSelect(discord.ui.Select):
            def __init__(self):
                options = [
                    discord.SelectOption(label=item["label"], value=item["value"]) for item in data["items"]
                ]
                super().__init__(placeholder="削除する項目を選択", options=options, custom_id="delete_ticket")

            async def callback(self, select_interaction: discord.Interaction):
                selected_value = self.values[0]
                data["items"] = [i for i in data["items"] if i["value"] != selected_value]
                save_data(data)
                await select_interaction.response.send_message(f"項目「{selected_value}」を削除しました。", ephemeral=True)

        class OpenMsgButton(discord.ui.Button):
            def __init__(self):
                super().__init__(label="オープンメッセージを設定", style=discord.ButtonStyle.primary)

            async def callback(self, button_interaction: discord.Interaction):
                await button_interaction.response.send_modal(OpenMessageModal(callback=self.set_open_message))

            async def set_open_message(self, modal_interaction, title, description):
                data["open_message"] = {"title": title, "description": description}
                save_data(data)
                await modal_interaction.response.send_message("オープンメッセージを保存しました。", ephemeral=True)

        view.add_item(DeleteSelect())
        view.add_item(OpenMsgButton())
        await interaction.response.send_message("設定を選択してください：", view=view, ephemeral=True)

    @app_commands.command(name="ticket_send", description="チケット作成パネルを送信")
    async def ticket_send(self, interaction: discord.Interaction, title: str, description: str, staff_role: discord.Role, image: Optional[discord.Attachment] = None):
        data = load_data()
        items = data.get("items", [])
        if not items:
            await interaction.response.send_message("先に `/ticket_add` で項目を追加してください。", ephemeral=True)
            return

        embed = discord.Embed(title=title, description=description, color=discord.Color.blue())
        if image:
            embed.set_image(url=image.url)

        view = TicketView(items, staff_role)
        await interaction.channel.send(embed=embed, view=view)
        await interaction.response.send_message("チケットパネルを送信しました。", ephemeral=True)

# HTTP サーバー（Render用）
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'Bot is running!')

def run_server():
    port = int(os.environ.get('PORT', 8000))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    server.serve_forever()

# ボットの設定
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'{bot.user} としてログインしました!')
    try:
        synced = await bot.tree.sync()
        print(f'{len(synced)} コマンドを同期しました')
    except Exception as e:
        print(f'コマンドの同期に失敗しました: {e}')

async def setup_bot():
    await bot.add_cog(Ticket(bot))

# ボットを起動
if __name__ == "__main__":
    import asyncio

    # HTTPサーバーをバックグラウンドで開始（Render用）
    server_thread = Thread(target=run_server)
    server_thread.daemon = True
    server_thread.start()

    # ボットの実行
    try:
        # 環境変数からトークンを取得
        bot_token = os.getenv("DISCORD_BOT_TOKEN")
        if bot_token:
            asyncio.run(setup_bot())
            bot.run(bot_token)
        else:
            print("DISCORD_BOT_TOKEN環境変数にボットトークンを設定してください。")
            print("許可ユーザーIDを設定する場合は、ALLOWED_USER_IDS環境変数にカンマ区切りで設定してください。")
    except Exception as e:
        print(f"エラーが発生しました: {e}")
