import asyncio
import json
import urllib.parse
import urllib.request
import vlc
import yt_dlp
from highrise import BaseBot, Position, User, AnchorPosition
from highrise.models import SessionMetadata

class AdvanceHighriseBot(BaseBot):
    def __init__(self):
        super().__init__()
        # ==========================
        # 1. ACCESS CONTROL & ADMINS
        # ==========================
        self.super_admins = {"your_highrise_username".lower()}
        self.admin_passphrase = "mySecretAdminPass123"

        # Bot self-state
        self.bot_id = None
        self.default_bot_spot = "dj"

        # Predefined Teleportation Coordinates
        self.saved_spots = {
            "dj": Position(10.5, 0.0, 10.5, "FrontRight"),
            "vip": Position(5.0, 2.0, 8.0, "FrontLeft"),
            "stage": Position(12.0, 1.5, 12.0, "FrontRight"),
            "bar": Position(3.0, 0.0, 4.0, "FrontLeft"),
            "jail": Position(0.0, 0.0, 0.0, "FrontRight")
        }
        self.user_positions = {}

        # ==========================
        # 2. AUDIO & MEDIA ENGINE
        # ==========================
        self.vlc_instance = vlc.Instance("--no-xlib")
        self.player = self.vlc_instance.media_player_new()

        # 24/7 Background station when queue is idle
        self.idle_radio_url = "https://stream.zeno.fm/f3wvbbqmdg8uv"
        self.is_idle_radio = False

        self.music_queue = []
        self.current_track = None
        self.is_playing = False
        self.volume = 80
        self.skip_votes = set()
        self.required_skips = 3

        # Cloud-safe: prioritize SoundCloud to bypass YouTube datacenter IP throttling
        self.ydl_opts = {
            'format': 'bestaudio/best',
            'noplaylist': True,
            'quiet': True,
            'default_search': 'scsearch1:',
            'extract_flat': False
        }

    # ==========================
    # LIFECYCLE EVENTS
    # ==========================

    async def on_start(self, session_metadata: SessionMetadata) -> None:
        self.bot_id = session_metadata.user_id
        print(f"Bot connected to room: {session_metadata.room_info.room_name} (ID: {self.bot_id})")

        # Auto-place bot at its default home spot on boot
        if self.default_bot_spot in self.saved_spots:
            try:
                await self.highrise.teleport(self.bot_id, self.saved_spots[self.default_bot_spot])
                print(f"Bot spawned at initial spot: '{self.default_bot_spot}'")
            except Exception as e:
                print(f"Failed to place bot on start: {e}")

        # Start background monitor for auto-progressing finished tracks
        asyncio.create_task(self._track_monitor())

        # Start 24/7 background ambient stream
        await self._start_idle_radio()
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

        # Admin Self-Verification
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
    # MUSIC PLAYBACK & STREAMING
    # ==========================

    async def _track_monitor(self) -> None:
        """Asynchronous monitor that advances queue automatically when a song finishes."""
        while True:
            try:
                # If a user song is playing and has naturally finished
                if self.is_playing and not self.is_idle_radio:
                    state = self.player.get_state()
                    if state == vlc.State.Ended:
                        await self._play_next_track()
            except Exception as e:
                print(f"Track monitor exception: {e}")
            await asyncio.sleep(1)

    def _resolve_spotify_or_query(self, query: str) -> str:
        """Decodes Spotify links to track titles without needing API credentials."""
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
                print(f"Spotify resolver fallback: {e}")
        return query

    def _extract_audio_stream(self, query: str):
        """Extracts direct playable stream URL using yt-dlp (SoundCloud default)."""
        is_direct_url = query.startswith("http://") or query.startswith("https://")
        target = query if is_direct_url else f"scsearch1:{query}"

        with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
            info = ydl.extract_info(target, download=False)
            if 'entries' in info and len(info['entries']) > 0:
                entry = info['entries'][0]
                return entry['url'], entry.get('title', query)
            return info['url'], info.get('title', query)

    async def _start_idle_radio(self) -> None:
        """Starts background 24/7 radio when queue is idle."""
        try:
            self.player.stop()
            media = self.vlc_instance.media_new(self.idle_radio_url)
            self.player.set_media(media)
            self.player.audio_set_volume(self.volume)
            self.player.play()
            self.is_playing = True
            self.is_idle_radio = True
            self.current_track = {"title": "24/7 Lo-Fi Chill Radio", "requested_by": "Room Radio"}
        except Exception as e:
            print(f"Error starting idle radio: {e}")

    async def _play_next_track(self) -> None:
        """Loads and streams the next track in queue or falls back to idle radio."""
        if self.player.is_playing():
            self.player.stop()

        if not self.music_queue:
            self.current_track = None
            await self._start_idle_radio()
            await self.highrise.chat("📻 Queue empty. Switched to 24/7 Lo-Fi background radio.")
            return

        self.current_track = self.music_queue.pop(0)
        self.is_playing = True
        self.is_idle_radio = False
        self.skip_votes.clear()

        query = self.current_track["query"]
        await self.highrise.chat(f"🔍 Loading track: '{query}'...")

        loop = asyncio.get_event_loop()
        try:
            stream_url, real_title = await loop.run_in_executor(None, self._extract_audio_stream, query)
            media = self.vlc_instance.media_new(stream_url)
            self.player.set_media(media)
            self.player.audio_set_volume(self.volume)
            self.player.play()

            self.current_track["title"] = real_title
            await self.highrise.chat(f"🎶 Now Playing: '{real_title}' [Req by @{self.current_track['requested_by']}]")
        except Exception as e:
            await self.highrise.chat(f"❌ Failed to play '{query}': {e}")
            await self._play_next_track()

    async def cmd_request_song(self, user: User, args: list) -> None:
        if not args:
            await self.highrise.chat(f"@{user.username} Usage: !play <song name, artist, or Spotify link>")
            return

        raw_query = " ".join(args).strip()
        loop = asyncio.get_event_loop()
        resolved_title = await loop.run_in_executor(None, self._resolve_spotify_or_query, raw_query)

        track_data = {
            "query": resolved_title,
            "title": resolved_title,
            "requested_by": user.username
        }
        self.music_queue.append(track_data)

        # If currently idle on the radio or stopped, play immediately
        if not self.is_playing or self.is_idle_radio:
            await self._play_next_track()
        else:
            pos = len(self.music_queue)
            await self.highrise.chat(f"🎵 Added to Queue #{pos}: '{resolved_title}' (Requested by @{user.username})")

    async def cmd_skip_song(self, user: User, is_admin: bool) -> None:
        if not self.is_playing:
            await self.highrise.chat("No track currently playing.")
            return

        if self.is_idle_radio:
            await self.highrise.chat("Currently streaming live background radio. Use !play to queue a song.")
            return

        if is_admin:
            await self.highrise.chat(f"⏭️ Admin @{user.username} forced skip.")
            self.player.stop()
            await self._play_next_track()
            return

        self.skip_votes.add(user.id)
        votes = len(self.skip_votes)
        if votes >= self.required_skips:
            await self.highrise.chat(f"⏭️ Vote skip passed ({votes}/{self.required_skips}). Skipping...")
            self.player.stop()
            await self._play_next_track()
        else:
            await self.highrise.chat(f"🗳️ Skip vote added: ({votes}/{self.required_skips}) votes required.")

    async def cmd_now_playing(self) -> None:
        if self.is_playing and self.current_track:
            mode = "Radio Stream" if self.is_idle_radio else f"Req by @{self.current_track['requested_by']}"
            await self.highrise.chat(f"🔊 Now Playing: '{self.current_track['title']}' [{mode}] | Vol: {self.volume}%")
        else:
            await self.highrise.chat("No track currently playing.")

    async def cmd_view_queue(self) -> None:
        if not self.music_queue:
            status = "Playing 24/7 background Lo-Fi" if self.is_idle_radio else "Idle"
            await self.highrise.chat(f"📭 Queue is empty. ({status})")
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
        self.player.audio_set_volume(self.volume)
        await self.highrise.chat(f"🎚️ Master Volume set to {self.volume}%.")

    # ==========================
    # DYNAMIC ADMIN SYSTEM
    # ==========================

    async def cmd_claim_admin(self, user: User, args: list) -> None:
        if not args:
            await self.highrise.chat(f"@{user.username} Usage: !claimadmin <passphrase>")
            return

        if args[0] == self.admin_passphrase:
            self.super_admins.add(user.username.lower())
            await self.highrise.chat(f"👑 @{user.username} has been granted Super Admin status!")
        else:
            await self.highrise.chat(f"@{user.username} Invalid passphrase.")

    async def cmd_add_admin(self, user: User, args: list) -> None:
        if not args:
            await self.highrise.chat(f"@{user.username} Usage: !addadmin @username")
            return
        target = args[0].lower().replace("@", "")
        self.super_admins.add(target)
        await self.highrise.chat(f"✅ @{user.username} promoted @{target} to Admin!")

    async def cmd_del_admin(self, user: User, args: list) -> None:
        if not args:
            await self.highrise.chat(f"@{user.username} Usage: !deladmin @username")
            return
        target = args[0].lower().replace("@", "")
        self.super_admins.discard(target)
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
            admin_pos = self.user_positions.get(user.id)
            if admin_pos:
                dest = Position(admin_pos.x + 0.5, admin_pos.y, admin_pos.z, admin_pos.facing)
                await self.highrise.teleport(self.bot_id, dest)
                await self.highrise.chat(f"🤖 Bot moved to @{user.username}.")
            else:
                await self.highrise.chat("Could not detect your coordinates. Move slightly and retry.")

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

        room_users = (await self.highrise.get_room_users()).content
        target_user = next((u for u, _ in room_users if u.username.lower() == target_name), None)

        if target_user and target_user.id in self.user_positions:
            pos = self.user_positions[target_user.id]
            dest = Position(pos.x + 0.5, pos.y, pos.z, pos.facing)
            await self.highrise.teleport(user.id, dest)
            await self.highrise.chat(f"⚡ @{user.username} warped to @{target_user.username}.")
        else:
            await self.highrise.chat(f"Destination or user '{target_name}' not found.")

    async def cmd_bring(self, user: User, args: list) -> None:
        if not args:
            await self.highrise.chat(f"@{user.username} Usage: !bring @username")
            return

        target_username = args[0].lower().replace("@", "")
        caller_pos = self.user_positions.get(user.id)
        if not caller_pos:
            await self.highrise.chat("Could not detect your coordinates. Move slightly and retry.")
            return

        room_users = (await self.highrise.get_room_users()).content
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

        room_users = (await self.highrise.get_room_users()).content
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
        pos = self.user_positions.get(user.id)
        if pos:
            self.saved_spots[spot_name] = pos
            await self.highrise.chat(f"✅ Saved spot '{spot_name}' at ({pos.x:.1f}, {pos.y:.1f}, {pos.z:.1f}).")
        else:
            await self.highrise.chat("Unable to get current position. Please move and try again.")

    async def cmd_del_spot(self, user: User, args: list) -> None:
        if not args or args[0].lower() not in self.saved_spots:
            await self.highrise.chat("Spot not found.")
            return
        del self.saved_spots[args[0].lower()]
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
            "🛡️ ADMIN: !bot <tp/walk/come/coords> | !bring | !send | !setspot | !delspot | !addadmin | !deladmin | !volume"
        ]
        for line in bio_lines:
            await self.highrise.chat(line)
            await asyncio.sleep(0.35)

    async def cmd_help(self, user: User, is_admin: bool) -> None:
        general_commands = "!tp <spot/@user>, !spots, !admins, !play <song>, !skip, !np, !q, !bio"
        admin_commands = " | Admin: !bot <tp/walk/come/coords>, !bring @user, !send @user <spot>, !setspot <name>, !delspot <name>, !addadmin @user, !deladmin @user, !volume <0-100>"
        await self.highrise.chat(f"Commands: {general_commands}" + (admin_commands if is_admin else ""))
