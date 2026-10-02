import asyncio
import json
import os
import sys
import urllib.parse
import urllib.request
import yt_dlp
from highrise import BaseBot, Position, User, AnchorPosition
from highrise.models import SessionMetadata
from highrise.__main__ import main, BotDefinition

CONFIG_FILE = "bot_config.json"

class AdvanceHighriseBot(BaseBot):
    def __init__(self):
        super().__init__()
        # ==========================
        # 1. ACCESS CONTROL & ADMINS
        # ==========================
       # Change this:
        # self.super_admins = {"your_highrise_username".lower()}

        # To your actual Highrise username (in lowercase):
        self.super_admins = {"JustManish".lower()}
        self.admin_passphrase = os.environ.get("ADMIN_PASSPHRASE", "mySecretAdminPass123")

        # Bot self-state
        self.bot_id = None
        self.default_bot_spot = "bot"

        # Default Teleportation Coordinates
        self.saved_spots = {
            "dj": Position(10.5, 0.0, 10.5, "FrontRight"),
            "vip": Position(5.0, 2.0, 8.0, "FrontLeft"),
            "stage": Position(12.0, 1.5, 12.0, "FrontRight"),
            "bar": Position(3.0, 0.0, 4.0, "FrontLeft"),
            "jail": Position(0.0, 0.0, 0.0, "FrontRight"),
            "bot": Position(18.0, 1.0, 14.5, "FrontLeft")
        }
        self.user_positions = {}

        # Load persisted spots and admins from disk
        self._load_config()

        # ==========================
        # 2. CLOUD QUEUE ENGINE
        # ==========================
        self.music_queue = []
        self.current_track = None
        self.is_playing = False
        self.volume = 80
        self.skip_votes = set()
        self.required_skips = 3
        self.playback_task = None

        self.ydl_opts = {
            'format': 'bestaudio/best',
            'noplaylist': True,
            'quiet': True,
            'default_search': 'scsearch1:',
            'extract_flat': False
        }

    # ==========================
    # CONFIG PERSISTENCE
    # ==========================

    def _save_config(self) -> None:
        """Saves dynamic spots, admins, and default bot location to disk."""
        try:
            data = {
                "super_admins": list(self.super_admins),
                "default_bot_spot": self.default_bot_spot,
                "saved_spots": {
                    name: {
                        "x": pos.x,
                        "y": pos.y,
                        "z": pos.z,
                        "facing": pos.facing
                    } for name, pos in self.saved_spots.items()
                }
            }
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            print(f"Error saving config: {e}")

    def _load_config(self) -> None:
        """Loads saved spots and admins if the config file exists."""
        if not os.path.exists(CONFIG_FILE):
            return
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "super_admins" in data:
                    self.super_admins.update([a.lower() for a in data["super_admins"]])
                if "default_bot_spot" in data:
                    self.default_bot_spot = data["default_bot_spot"]
                if "saved_spots" in data:
                    for name, coords in data["saved_spots"].items():
                        self.saved_spots[name] = Position(
                            coords["x"], coords["y"], coords["z"], coords.get("facing", "FrontRight")
                        )
        except Exception as e:
            print(f"Error loading config: {e}")

    # ==========================
    # COORDINATE RESOLVER
    # ==========================

    async def _get_user_position(self, user_id: str) -> Position | None:
        """Retrieves user coordinates from cache or live room queries."""
        if user_id in self.user_positions:
            return self.user_positions[user_id]

        try:
            response = await self.highrise.get_room_users()
            room_users = response.content if hasattr(response, "content") else response
            for item in room_users:
                u, pos = item[0], item[1]
                if u.id == user_id:
                    if isinstance(pos, Position):
                        self.user_positions[user_id] = pos
                        return pos
                    elif isinstance(pos, AnchorPosition):
                        return None
        except Exception as e:
            print(f"Error retrieving position: {e}")
        return None

    # ==========================
    # LIFECYCLE EVENTS
    # ==========================

    async def on_start(self, session_metadata: SessionMetadata) -> None:
        self.bot_id = session_metadata.user_id
        room_name = session_metadata.room_info.room_name if session_metadata.room_info else "Highrise Room"
        print(f"Bot connected to room: {room_name} (Bot ID: {self.bot_id})")

        if self.default_bot_spot in self.saved_spots:
            try:
                await self.highrise.teleport(self.bot_id, self.saved_spots[self.default_bot_spot])
                print(f"Bot placed at initial spot: '{self.default_bot_spot}'")
            except Exception as e:
                print(f"Failed to place bot on start: {e}")

        await self.highrise.chat("⚡ Highrise Advanced Controller Online. Type !help for commands.")

    async def on_user_join(self, user: User, position: Position | AnchorPosition) -> None:
        if isinstance(position, Position):
            self.user_positions[user.id] = position
        await self.highrise.chat(f"Welcome @{user.username}! Use !spots to warp or !play <song> to queue music.")

    async def on_user_leave(self, user: User) -> None:
        self.user_positions.pop(user.id, None)
        self.skip_votes.discard(user.id)

    async def on_user_move(self, user: User, pos: Position | AnchorPosition) -> None:
        if isinstance(pos, Position):
            self.user_positions[user.id] = pos

    # ==========================
    # CHAT & COMMAND DISPATCHER
    # ==========================

    async def on_chat(self, user: User, message: str) -> None:
        msg = message.strip()
        if not msg.startswith("!"):
            return

        parts = msg.split()
        cmd = parts[0].lower()
        args = parts[1:]

        is_admin = user.username.lower() in self.super_admins

        # Public Commands
        if cmd == "!help":
            await self.cmd_help(user, is_admin)
        elif cmd in ["!bio", "!info", "!about"]:
            await self.cmd_bio(user)
        elif cmd in ["!tp", "!goto"]:
            await self.cmd_teleport(user, args)
        elif cmd == "!spots":
            await self.cmd_list_spots()
        elif cmd == "!admins":
            await self.cmd_list_admins()
        elif cmd in ["!play", "!request"]:
            await self.cmd_request_song(user, args)
        elif cmd == "!skip":
            await self.cmd_skip_song(user, is_admin)
        elif cmd in ["!np", "!song"]:
            await self.cmd_now_playing()
        elif cmd in ["!q", "!queue"]:
            await self.cmd_view_queue()

        # Admin Verification
        elif cmd == "!claimadmin":
            await self.cmd_claim_admin(user, args)

        # Admin Management & Controls
        elif cmd in ["!addadmin", "!op"] and is_admin:
            await self.cmd_add_admin(user, args)
        elif cmd in ["!deladmin", "!deop"] and is_admin:
            await self.cmd_del_admin(user, args)
        elif cmd == "!bring" and is_admin:
            await self.cmd_bring(user, args)
        elif cmd == "!send" and is_admin:
            await self.cmd_send(user, args)
        elif cmd == "!setspot" and is_admin:
            await self.cmd_set_spot(user, args)
        elif cmd == "!delspot" and is_admin:
            await self.cmd_del_spot(user, args)
        elif cmd == "!bot" and is_admin:
            await self.cmd_bot_placement(user, args)
        elif cmd == "!volume" and is_admin:
            await self.cmd_set_volume(user, args)

    # ==========================
    # CLOUD DJ QUEUE & METADATA
    # ==========================

    def _resolve_spotify_or_query(self, query: str) -> str:
        if "open.spotify.com/track" in query:
            try:
                encoded = urllib.parse.quote(query)
                req = urllib.request.Request(
                    f"https://open.spotify.com/oembed?url={encoded}",
                    headers={'User-Agent': 'Mozilla/5.0'}
                )
                with urllib.request.urlopen(req, timeout=5) as response:
                    if response.status == 200:
                        data = json.loads(response.read().decode('utf-8'))
                        return data.get("title", query)
            except Exception as e:
                print(f"Spotify resolver error: {e}")
        return query

    def _fetch_track_info(self, query: str):
        is_direct_url = query.startswith("http://") or query.startswith("https://")
        target = query if is_direct_url else f"scsearch1:{query}"

        with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
            info = ydl.extract_info(target, download=False)
            if 'entries' in info and len(info['entries']) > 0:
                entry = info['entries'][0]
                return entry.get('title', query), entry.get('duration', 180)
            return info.get('title', query), info.get('duration', 180)

    async def _play_next_track(self) -> None:
        if self.playback_task and not self.playback_task.done():
            self.playback_task.cancel()

        if not self.music_queue:
            self.current_track = None
            self.is_playing = False
            await self.highrise.chat("⏹️ DJ Queue is empty. Use !play <song> to queue music.")
            return

        self.current_track = self.music_queue.pop(0)
        self.is_playing = True
        self.skip_votes.clear()

        query = self.current_track["query"]
        await self.highrise.chat(f"🔍 Fetching track: '{query}'...")

        loop = asyncio.get_running_loop()
        try:
            real_title, duration = await loop.run_in_executor(None, self._fetch_track_info, query)
            self.current_track["title"] = real_title
            self.current_track["duration"] = duration

            mins, secs = divmod(duration or 180, 60)
            duration_str = f"{int(mins)}:{int(secs):02d}"

            await self.highrise.chat(f"🎶 Now Playing: '{real_title}' ({duration_str}) [Req by @{self.current_track['requested_by']}]")
            self.playback_task = asyncio.create_task(self._track_timer(duration or 180))
        except Exception as e:
            await self.highrise.chat(f"❌ Could not load '{query}': {e}")
            await self._play_next_track()

    async def _track_timer(self, duration: int) -> None:
        try:
            await asyncio.sleep(duration)
            await self._play_next_track()
        except asyncio.CancelledError:
            pass

    async def cmd_request_song(self, user: User, args: list) -> None:
        if not args:
            await self.highrise.chat(f"@{user.username} Usage: !play <song name or Spotify link>")
            return

        raw_query = " ".join(args).strip()
        loop = asyncio.get_running_loop()
        resolved_title = await loop.run_in_executor(None, self._resolve_spotify_or_query, raw_query)

        track_data = {
            "query": resolved_title,
            "title": resolved_title,
            "requested_by": user.username
        }
        self.music_queue.append(track_data)

        if not self.is_playing:
            await self._play_next_track()
        else:
            pos = len(self.music_queue)
            await self.highrise.chat(f"🎵 Added to Queue #{pos}: '{resolved_title}' (Requested by @{user.username})")

    async def cmd_skip_song(self, user: User, is_admin: bool) -> None:
        if not self.is_playing:
            await self.highrise.chat("No track currently playing.")
            return

        if is_admin:
            await self.highrise.chat(f"⏭️ Admin @{user.username} forced skip.")
            await self._play_next_track()
            return

        self.skip_votes.add(user.id)
        votes = len(self.skip_votes)
        if votes >= self.required_skips:
            await self.highrise.chat(f"⏭️ Vote skip passed ({votes}/{self.required_skips}). Skipping...")
            await self._play_next_track()
        else:
            await self.highrise.chat(f"🗳️ Skip vote added: ({votes}/{self.required_skips}) votes required.")

    async def cmd_now_playing(self) -> None:
        if self.is_playing and self.current_track:
            await self.highrise.chat(f"🔊 Now Playing: '{self.current_track['title']}' [Req by @{self.current_track['requested_by']}] | Vol: {self.volume}%")
        else:
            await self.highrise.chat("No track currently playing. Use !play <song> to queue one.")

    async def cmd_view_queue(self) -> None:
        if not self.music_queue:
            await self.highrise.chat("📭 The DJ queue is currently empty.")
            return

        lines = [f"{idx+1}. {item['title']} (@{item['requested_by']})" for idx, item in enumerate(self.music_queue[:5])]
        remaining = len(self.music_queue) - 5
        summary = " | ".join(lines)
        if remaining > 0:
            summary += f" ...and {remaining} more."
        await self.highrise.chat(f"📋 Upcoming: {summary}")

    async def cmd_set_volume(self, user: User, args: list) -> None:
        if not args or not args[0].isdigit():
            await self.highrise.chat("Usage: !volume <0-100>")
            return
        self.volume = max(0, min(100, int(args[0])))
        await self.highrise.chat(f"🎚️ DJ Master Volume set to {self.volume}%.")

    # ==========================
    # DYNAMIC ADMIN SYSTEM
    # ==========================

    async def cmd_claim_admin(self, user: User, args: list) -> None:
        if not args:
            await self.highrise.chat(f"@{user.username} Usage: !claimadmin <passphrase>")
            return

        if args[0] == self.admin_passphrase:
            self.super_admins.add(user.username.lower())
            self._save_config()
            await self.highrise.chat(f"👑 @{user.username} has verified credentials and is now a Super Admin!")
        else:
            await self.highrise.chat(f"@{user.username} Invalid passphrase.")

    async def cmd_add_admin(self, user: User, args: list) -> None:
        if not args:
            await self.highrise.chat(f"@{user.username} Usage: !addadmin @username")
            return
        target = args[0].lower().replace("@", "")
        self.super_admins.add(target)
        self._save_config()
        await self.highrise.chat(f"✅ @{user.username} promoted @{target} to Admin!")

    async def cmd_del_admin(self, user: User, args: list) -> None:
        if not args:
            await self.highrise.chat(f"@{user.username} Usage: !deladmin @username")
            return
        target = args[0].lower().replace("@", "")
        self.super_admins.discard(target)
        self._save_config()
        await self.highrise.chat(f"❌ Demoted @{target} from Admin.")

    async def cmd_list_admins(self) -> None:
        admin_list = ", ".join([f"@{a}" for a in self.super_admins])
        await self.highrise.chat(f"🛡️ Current Admins: {admin_list}")

    # ==========================
    # BOT SELF-PLACEMENT SYSTEM
    # ==========================

    async def cmd_bot_placement(self, user: User, args: list) -> None:
        if not args:
            await self.highrise.chat(f"@{user.username} Usage: !bot tp <spot> | !bot walk <spot> | !bot come | !bot coords <x> <y> <z> [facing]")
            return

        subcmd = args[0].lower()

        if subcmd == "tp":
            if len(args) < 2:
                await self.highrise.chat("Usage: !bot tp <spot>")
                return
            spot_name = args[1].lower()
            if spot_name in self.saved_spots:
                await self.highrise.teleport(self.bot_id, self.saved_spots[spot_name])
                await self.highrise.chat(f"🤖 Bot warped to '{spot_name}'.")
            else:
                await self.highrise.chat(f"Spot '{spot_name}' not found. Use !spots.")

        elif subcmd == "walk":
            if len(args) < 2:
                await self.highrise.chat("Usage: !bot walk <spot>")
                return
            spot_name = args[1].lower()
            if spot_name in self.saved_spots:
                await self.highrise.walk_to(self.saved_spots[spot_name])
                await self.highrise.chat(f"🤖 Bot walking to '{spot_name}'...")
            else:
                await self.highrise.chat(f"Spot '{spot_name}' not found.")

        elif subcmd == "come":
            admin_pos = await self._get_user_position(user.id)
            if admin_pos:
                dest = Position(admin_pos.x + 0.5, admin_pos.y, admin_pos.z, admin_pos.facing)
                await self.highrise.teleport(self.bot_id, dest)
                await self.highrise.chat(f"🤖 Bot moved to @{user.username}.")
            else:
                await self.highrise.chat("Could not detect your coordinates. Stand on the floor and retry.")

        elif subcmd in ["coords", "pos"]:
            if len(args) < 4:
                await self.highrise.chat("Usage: !bot coords <x> <y> <z> [facing]")
                return
            try:
                x, y, z = float(args[1]), float(args[2]), float(args[3])
                facing = args[4] if len(args) > 4 else "FrontRight"
                dest = Position(x, y, z, facing)
                await self.highrise.teleport(self.bot_id, dest)
                await self.highrise.chat(f"🤖 Bot moved to ({x:.1f}, {y:.1f}, {z:.1f}).")
            except ValueError:
                await self.highrise.chat("Coordinates x, y, and z must be numbers.")

        elif subcmd == "default":
            if len(args) < 2 or args[1].lower() not in self.saved_spots:
                await self.highrise.chat("Usage: !bot default <saved_spot>")
                return
            self.default_bot_spot = args[1].lower()
            self._save_config()
            await self.highrise.chat(f"✅ Bot startup spot set to '{self.default_bot_spot}'.")

    # ==========================
    # USER TELEPORTATION FUNCTIONS
    # ==========================

    async def cmd_teleport(self, user: User, args: list) -> None:
        if not args:
            await self.highrise.chat(f"@{user.username} Usage: !tp <spot_name> or !tp @username")
            return

        target_name = args[0].lower().replace("@", "")

        if target_name in self.saved_spots:
            await self.highrise.teleport(user.id, self.saved_spots[target_name])
            await self.highrise.chat(f"⚡ @{user.username} warped to '{target_name}'.")
            return

        response = await self.highrise.get_room_users()
        room_users = response.content if hasattr(response, "content") else response
        target_user = next((u for u, _ in room_users if u.username.lower() == target_name), None)

        if target_user:
            pos = await self._get_user_position(target_user.id)
            if pos:
                dest = Position(pos.x + 0.5, pos.y, pos.z, pos.facing)
                await self.highrise.teleport(user.id, dest)
                await self.highrise.chat(f"⚡ @{user.username} warped to @{target_user.username}.")
                return

        await self.highrise.chat(f"Destination or user '{target_name}' not found.")

    async def cmd_bring(self, user: User, args: list) -> None:
        if not args:
            await self.highrise.chat(f"@{user.username} Usage: !bring @username")
            return

        target_username = args[0].lower().replace("@", "")
        caller_pos = await self._get_user_position(user.id)
        if not caller_pos:
            await self.highrise.chat("Could not detect your coordinates. Stand on the floor and try again.")
            return

        response = await self.highrise.get_room_users()
        room_users = response.content if hasattr(response, "content") else response
        target_user = next((u for u, _ in room_users if u.username.lower() == target_username), None)

        if target_user:
            dest = Position(caller_pos.x + 0.5, caller_pos.y, caller_pos.z, caller_pos.facing)
            await self.highrise.teleport(target_user.id, dest)
            await self.highrise.chat(f"⚡ Brought @{target_user.username} to your position.")
        else:
            await self.highrise.chat(f"Target @{target_username} not found in room.")

    async def cmd_send(self, user: User, args: list) -> None:
        if len(args) < 2:
            await self.highrise.chat(f"@{user.username} Usage: !send @username <spot_name>")
            return

        target_username = args[0].lower().replace("@", "")
        spot_name = args[1].lower()

        if spot_name not in self.saved_spots:
            await self.highrise.chat(f"Spot '{spot_name}' does not exist. Use !spots.")
            return

        response = await self.highrise.get_room_users()
        room_users = response.content if hasattr(response, "content") else response
        target_user = next((u for u, _ in room_users if u.username.lower() == target_username), None)

        if target_user:
            await self.highrise.teleport(target_user.id, self.saved_spots[spot_name])
            await self.highrise.chat(f"⚡ Sent @{target_user.username} to '{spot_name}'.")
        else:
            await self.highrise.chat(f"User @{target_username} not found.")

    async def cmd_set_spot(self, user: User, args: list) -> None:
        if not args:
            await self.highrise.chat(f"@{user.username} Usage: !setspot <name>")
            return

        spot_name = args[0].lower()
        pos = await self._get_user_position(user.id)

        if pos:
            self.saved_spots[spot_name] = pos
            self._save_config()
            await self.highrise.chat(f"✅ Saved spot '{spot_name}' at ({pos.x:.1f}, {pos.y:.1f}, {pos.z:.1f}).")
        else:
            await self.highrise.chat("Unable to get coordinates. If you are sitting on furniture, stand up on the floor and try again.")

    async def cmd_del_spot(self, user: User, args: list) -> None:
        if not args or args[0].lower() not in self.saved_spots:
            await self.highrise.chat("Spot not found.")
            return
        del self.saved_spots[args[0].lower()]
        self._save_config()
        await self.highrise.chat(f"🗑️ Deleted spot '{args[0].lower()}'.")

    async def cmd_list_spots(self) -> None:
        spots_str = ", ".join(self.saved_spots.keys())
        await self.highrise.chat(f"📍 Active Teleport Spots: {spots_str}")

    # ==========================
    # HELP & BIO MENUS
    # ==========================

    async def cmd_bio(self, user: User) -> None:
        bio_lines = [
            f"🤖 [Bot Directory for @{user.username}]",
            "🎵 MUSIC: !play <song/link> | !skip | !np | !q",
            "📍 TELEPORT: !tp <spot/@user> | !spots",
            "👑 ACCESS: !claimadmin <passphrase> | !admins",
            "🛡️ ADMIN: !bot <tp/walk/come/coords/default> | !bring | !send | !setspot | !delspot | !addadmin | !deladmin | !volume"
        ]
        for line in bio_lines:
            await self.highrise.chat(line)
            await asyncio.sleep(0.35)

    async def cmd_help(self, user: User, is_admin: bool) -> None:
        general_commands = "!tp <spot/@user>, !spots, !admins, !play <song>, !skip, !np, !q, !bio"
        admin_commands = " | Admin: !bot <tp/walk/come/coords/default>, !bring @user, !send @user <spot>, !setspot <name>, !delspot <name>, !addadmin @user, !deladmin @user, !volume <0-100>"
        await self.highrise.chat(f"Commands: {general_commands}" + (admin_commands if is_admin else ""))

# ==========================
# 24/7 AUTO-RECONNECT RUNNER
# ==========================

async def run_bot_loop():
    room_id = os.environ.get("ROOM_ID")
    api_token = os.environ.get("API_TOKEN")

    # Command-line arguments fallback: python bot.py <room_id> <api_token>
    if not room_id and len(sys.argv) > 1:
        room_id = sys.argv[1]
    if not api_token and len(sys.argv) > 2:
        api_token = sys.argv[2]

    if not room_id or not api_token:
        print("ERROR: Missing ROOM_ID or API_TOKEN. Set them in environment variables or pass as CLI arguments.")
        return

    while True:
        try:
            print("Connecting to Highrise...")
            definitions = [BotDefinition(AdvanceHighriseBot(), room_id, api_token)]
            await main(definitions)
        except Exception as e:
            print(f"Room session closed or network dropped: {e}")
            print("Waiting 10 seconds before attempting reconnection...")
            await asyncio.sleep(10)

if __name__ == "__main__":
    try:
        asyncio.run(run_bot_loop())
    except KeyboardInterrupt:
        print("Bot process stopped manually.")
