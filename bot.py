import asyncio

from highrise import BaseBot, Position, User, AnchorPosition
from highrise.models import SessionMetadata

class AdvanceHighriseBot(BaseBot):
    def __init__(self):
        super().__init__()
        # 1. Access Control: Add Highrise user IDs or usernames here
        self.super_admins = ["YOUR_HIGHRISE_USERNAME"]
        
        # 2. Predefined Teleportation Coordinates (x, y, z, facing)
        self.saved_spots = {
            "dj": Position(10.5, 0.0, 10.5, "FrontRight"),
            "vip": Position(5.0, 2.0, 8.0, "FrontLeft"),
            "stage": Position(12.0, 1.5, 12.0, "FrontRight"),
            "bar": Position(3.0, 0.0, 4.0, "FrontLeft"),
            "jail": Position(0.0, 0.0, 0.0, "FrontRight")
        }

        # User coordinates cache for relative features
        self.user_positions = {}

        # 3. Music Controller State
        self.music_queue = []
        self.current_track = None
        self.is_playing = False
        self.volume = 80
        self.skip_votes = set()
        self.required_skips = 3

    # ==========================
    # LIFECYCLE EVENTS
    # ==========================

    async def on_start(self, session_metadata: SessionMetadata) -> None:
        print(f"Bot connected to room: {session_metadata.room_info.room_name}")
        await self.highrise.chat("⚡ Highrise Advanced Controller Online. Type !help for commands.")

    async def on_user_join(self, user: User, position: Position | AnchorPosition) -> None:
        if isinstance(position, Position):
            self.user_positions[user.id] = position
        await self.highrise.chat(f"Welcome @{user.username}! Use !spots to see teleport zones, or !q to view the DJ queue.")

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

        is_admin = user.username.lower() in [a.lower() for a in self.super_admins]

        # Route Commands
        if cmd == "!help":
            await self.cmd_help(user, is_admin)
        elif cmd in ["!tp", "!goto"]:
            await self.cmd_teleport(user, args)
        elif cmd == "!bring" and is_admin:
            await self.cmd_bring(user, args)
        elif cmd == "!send" and is_admin:
            await self.cmd_send(user, args)
        elif cmd == "!setspot" and is_admin:
            await self.cmd_set_spot(user, args)
        elif cmd == "!delspot" and is_admin:
            await self.cmd_del_spot(user, args)
        elif cmd == "!spots":
            await self.cmd_list_spots()
        elif cmd in ["!play", "!request"]:
            await self.cmd_request_song(user, args)
        elif cmd == "!skip":
            await self.cmd_skip_song(user, is_admin)
        elif cmd in ["!np", "!song"]:
            await self.cmd_now_playing()
        elif cmd in ["!q", "!queue"]:
            await self.cmd_view_queue()
        elif cmd == "!volume" and is_admin:
            await self.cmd_set_volume(user, args)

    # ==========================
    # TELEPORTATION FUNCTIONS
    # ==========================

    async def cmd_teleport(self, user: User, args: list) -> None:
        """Teleports caller to a saved spot or to another user."""
        if not args:
            await self.highrise.chat(f"@{user.username} Usage: !tp <spot_name> or !tp @username")
            return

        target_name = args[0].lower().replace("@", "")

        # Target is a predefined named spot
        if target_name in self.saved_spots:
            target_pos = self.saved_spots[target_name]
            await self.highrise.teleport(user.id, target_pos)
            await self.highrise.chat(f"⚡ @{user.username} warped to '{target_name}'.")
            return

        # Target is a user in the room
        room_users = (await self.highrise.get_room_users()).content
        target_user = next((u for u, _ in room_users if u.username.lower() == target_name), None)

        if target_user and target_user.id in self.user_positions:
            pos = self.user_positions[target_user.id]
            # Offset slightly so avatars do not clip into each other
            dest = Position(pos.x + 0.5, pos.y, pos.z, pos.facing)
            await self.highrise.teleport(user.id, dest)
            await self.highrise.chat(f"⚡ @{user.username} warped to @{target_user.username}.")
        else:
            await self.highrise.chat(f"@{user.username} Destination or user '{target_name}' not found.")

    async def cmd_bring(self, user: User, args: list) -> None:
        """Admin only: Pulls target user to admin's current location."""
        if not args:
            await self.highrise.chat(f"@{user.username} Usage: !bring @username")
            return

        target_username = args[0].lower().replace("@", "")
        caller_pos = self.user_positions.get(user.id)
        if not caller_pos:
            await self.highrise.chat("Could not detect your coordinates. Move slightly and try again.")
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
        """Admin only: Warps a target user to a specific spot."""
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
        """Admin only: Dynamically saves current position as a named spot."""
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
        """Admin only: Removes a saved spot."""
        if not args or args[0].lower() not in self.saved_spots:
            await self.highrise.chat("Spot not found.")
            return
        del self.saved_spots[args[0].lower()]
        await self.highrise.chat(f"🗑️ Deleted spot '{args[0].lower()}'.")

    async def cmd_list_spots(self) -> None:
        spots_str = ", ".join(self.saved_spots.keys())
        await self.highrise.chat(f"📍 Active Teleport Spots: {spots_str}")

    # ==========================
    # MUSIC CONTROLLER FUNCTIONS
    # ==========================

    async def cmd_request_song(self, user: User, args: list) -> None:
        """Enqueues a song query or direct URL."""
        if not args:
            await self.highrise.chat(f"@{user.username} Usage: !play <song title or artist>")
            return

        query = " ".join(args)
        track = {"title": query, "requested_by": user.username}
        self.music_queue.append(track)

        if not self.is_playing:
            await self._play_next_track()
        else:
            pos = len(self.music_queue)
            await self.highrise.chat(f"🎵 Added to Queue #{pos}: '{query}' (Requested by @{user.username})")

    async def _play_next_track(self) -> None:
        if not self.music_queue:
            self.current_track = None
            self.is_playing = False
            await self.highrise.chat("⏹️ Music queue is empty. Use !play <song> to queue a track.")
            return

        self.current_track = self.music_queue.pop(0)
        self.is_playing = True
        self.skip_votes.clear()
        
        # In an integrated setup, trigger your audio worker / media player API here
        await self.highrise.chat(f"🎶 Now Playing: '{self.current_track['title']}' [Req by @{self.current_track['requested_by']}]")

    async def cmd_skip_song(self, user: User, is_admin: bool) -> None:
        """Skips song via admin override or democratic crowd vote."""
        if not self.is_playing:
            await self.highrise.chat("No track is currently playing.")
            return

        if is_admin:
            await self.highrise.chat(f"⏭️️ Admin @{user.username} forced skip.")
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
            await self.highrise.chat(f"🔊 Current Track: '{self.current_track['title']}' | Vol: {self.volume}%")
        else:
            await self.highrise.chat("No track currently playing.")

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
        await self.highrise.chat(f"🎚️ Master Volume set to {self.volume}%.")

    # ==========================
    # HELP MENU
    # ==========================

    async def cmd_help(self, user: User, is_admin: bool) -> None:
        general_commands = "!tp <spot/@user>, !spots, !play <song>, !skip, !np, !q"
        admin_commands = " | Admin: !bring @user, !send @user <spot>, !setspot <name>, !delspot <name>, !volume <0-100>"
        await self.highrise.chat(f"Commands: {general_commands}" + (admin_commands if is_admin else ""))