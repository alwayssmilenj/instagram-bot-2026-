"""Interactive multiplayer and single-player GamesEngine with fine-grained thread-safe locks and auto-eviction.

Provides rich group-chat and DM game sessions (Tic-Tac-Toe, Connect Four, Blackjack, Trivia, Tarot, Roast Battle)
with per-session RLock isolation, zero-deadlock concurrent turns, idle timeout auto-eviction, and XP reward integration.
"""
from __future__ import annotations

import enum
import logging
import random
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from lib.trivia_service import TriviaQuestion, TriviaService

LOGGER = logging.getLogger("knightbot.games")


@dataclass
class Card:
    """Standard playing card representation."""
    rank: str
    suit: str

    def value(self) -> int:
        if self.rank in ("J", "Q", "K", "10"):
            return 10
        if self.rank == "A":
            return 11
        return int(self.rank)

    def display(self) -> str:
        return f"{self.rank}{self.suit}"

    def __str__(self) -> str:
        symbols = {"S": "♠️", "H": "♥️", "D": "♦️", "C": "♣️"}
        return f"{self.rank}{symbols.get(self.suit, self.suit)}"


class GameType(str, enum.Enum):
    TRIVIA = "trivia"
    TICTACTOE = "ttt"
    CONNECT4 = "c4"
    BLACKJACK = "blackjack"
    GUESS_NUMBER = "guess_number"
    WORDLE = "wordle"


class GameStatus(str, enum.Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    WON = "won"
    DRAW = "draw"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


# =============================================================================
# 1. TIC-TAC-TOE GAME ENGINE
# =============================================================================

class TicTacToeGame:
    """Thread-safe 2-player or AI Tic-Tac-Toe game with RLock."""

    def __init__(
        self,
        thread_id: str = "",
        player_x: str = "@Player1",
        player_o: str = "",
        is_ai: bool = False,
        timeout_seconds: float = 300.0,
        p1_id: str = "",
        p1_name: str = "",
        p2_id: str = "",
        p2_name: str = "",
        is_vs_ai: bool | None = None,
    ) -> None:
        self.thread_id = str(thread_id)
        px = p1_name or player_x
        po = p2_name or player_o
        self.player_x = px if px.startswith("@") else f"@{px}"
        self.player_o = (po if po.startswith("@") else f"@{po}") if po else ""
        self.p1_id = p1_id
        self.p2_id = p2_id
        self.is_ai = is_ai if is_vs_ai is None else bool(is_vs_ai)
        self.timeout_seconds = timeout_seconds
        self.status = "active"
        self.turn = "X"
        self.board: list[str] = [" "] * 9
        self.winner: str | None = None
        self.last_activity: float = time.time()
        self.updated_at: float = time.time()
        self.created_at: float = time.time()
        self.lock = threading.RLock()
        self.xp_reward = 35

    def is_expired(self, timeout_seconds: float = 300.0) -> bool:
        with self.lock:
            if self.status != "active":
                return False
            return (time.time() - self.last_activity) > timeout_seconds

    def _check_winner(self) -> str | None:
        lines = [
            (0, 1, 2), (3, 4, 5), (6, 7, 8),
            (0, 3, 6), (1, 4, 7), (2, 5, 8),
            (0, 4, 8), (2, 4, 6),
        ]
        for a, b, c in lines:
            if self.board[a] == self.board[b] == self.board[c] and self.board[a] in ("X", "O"):
                return self.board[a]
        if all(cell in ("X", "O") for cell in self.board):
            return "DRAW"
        return None

    def render_board(self, footer: str = "") -> str:
        with self.lock:
            symbols = {"X": "❌", "O": "⭕", " ": "⬜"}
            num_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣"]
            b = [
                symbols["X"] if cell == "X" else symbols["O"] if cell == "O" else num_emojis[i]
                for i, cell in enumerate(self.board)
            ]
            grid = (
                f"   {b[0]} │ {b[1]} │ {b[2]}\n"
                f"  ───┼───┼───\n"
                f"   {b[3]} │ {b[4]} │ {b[5]}\n"
                f"  ───┼───┼───\n"
                f"   {b[6]} │ {b[7]} │ {b[8]}"
            )
            p_o = self.player_o or "Waiting for opponent (⭕)..."
            text = (
                f"⚔️ **TIC-TAC-TOE ARENA** ⚔️\n\n"
                f"❌ Player X: {self.player_x}\n"
                f"⭕ Player O: {p_o}\n\n"
                f"{grid}\n\n"
                f"👉 Turn: **{'❌ ' + self.player_x if self.turn == 'X' else '⭕ ' + p_o}**\n"
                f"💡 Send `.ttt <1-9>` to make your move!"
            )
            if footer:
                text += f"\n\n{footer}"
            return text

    def make_move(self, position: int, username: str, database: Any = None) -> tuple[bool, str]:
        with self.lock:
            if self.status != "active":
                return False, "This game is no longer active."

            if position < 1 or position > 9:
                return False, "Invalid move! Choose a number between 1 and 9."

            pos = position - 1
            if self.board[pos] != " ":
                return False, "That position is already occupied! Choose an empty spot."

            clean_user = username.lower().lstrip("@")
            x_clean = self.player_x.lower().lstrip("@")
            o_clean = self.player_o.lower().lstrip("@") if self.player_o else ""

            if self.turn == "X":
                if clean_user != x_clean and x_clean:
                    return False, f"It is not your turn! Waiting for {self.player_x} (❌)."
            else:
                if not self.player_o:
                    if clean_user == x_clean:
                        return False, "You cannot play against yourself! Wait for an opponent."
                    self.player_o = f"@{username.lstrip('@')}"
                    o_clean = clean_user
                elif clean_user != o_clean:
                    return False, f"It is not your turn! Waiting for {self.player_o} (⭕)."

            self.last_activity = time.time()
            self.updated_at = time.time()
            self.board[pos] = self.turn
            result = self._check_winner()
            render = self.render_board()

            if result == "DRAW":
                self.status = "draw"
                return True, f"🤝 **IT'S A DRAW!** Well played by both players.\n\n{render}"
            elif result in ("X", "O"):
                self.status = "won"
                self.winner = self.player_x if result == "X" else self.player_o
                symbol = "❌" if result == "X" else "⭕"

                if database and hasattr(database, "update_user_stats"):
                    try:
                        database.update_user_stats(self.winner.lstrip("@"), self.winner.lstrip("@"), xp_delta=self.xp_reward)
                    except Exception as err:
                        LOGGER.debug("Error awarding TTT XP: %s", err)

                return True, f"🎉 **VICTORY!** {symbol} {self.winner} completed 3-in-a-row and won! (+{self.xp_reward} XP)\n\n{render}"
            else:
                self.turn = "O" if self.turn == "X" else "X"
                return True, f"⚔️ Move placed at position {position}!\n\n{render}"

    def play_turn(self, position: int, user_id: str, username: str) -> tuple[bool, str]:
        return self.make_move(position, username)


# =============================================================================
# 2. CONNECT FOUR GAME ENGINE
# =============================================================================

class ConnectFourGame:
    """Thread-safe 2-player Connect Four game with RLock."""

    COLS = 7
    ROWS = 6

    def __init__(
        self,
        thread_id: str = "",
        player_red: str = "@Player1",
        player_yellow: str = "",
        is_ai: bool = False,
        timeout_seconds: float = 300.0,
        p1_id: str = "",
        p1_name: str = "",
        p2_id: str = "",
        p2_name: str = "",
        is_vs_ai: bool | None = None,
    ) -> None:
        self.thread_id = str(thread_id)
        pr = p1_name or player_red
        py = p2_name or player_yellow
        self.player_red = pr if pr.startswith("@") else f"@{pr}"
        self.player_yellow = (py if py.startswith("@") else f"@{py}") if py else ""
        self.p1_id = p1_id
        self.p2_id = p2_id
        self.is_ai = is_ai if is_vs_ai is None else bool(is_vs_ai)
        self.timeout_seconds = timeout_seconds
        self.status = "active"
        self.turn = "R"  # "R" (Red) or "Y" (Yellow)
        self.grid: list[list[str]] = [[" " for _ in range(self.COLS)] for _ in range(self.ROWS)]
        self.winner: str | None = None
        self.last_activity: float = time.time()
        self.updated_at: float = time.time()
        self.created_at: float = time.time()
        self.lock = threading.RLock()
        self.xp_reward = 40

    def is_expired(self, timeout_seconds: float = 300.0) -> bool:
        with self.lock:
            if self.status != "active":
                return False
            return (time.time() - self.last_activity) > timeout_seconds

    def _render_grid(self) -> str:
        symbols = {"R": "🔴", "Y": "🟡", " ": "⚪"}
        header = " 1️⃣  2️⃣  3️⃣  4️⃣  5️⃣  6️⃣  7️⃣"
        rows = [" ".join(symbols[cell] for cell in row) for row in self.grid]
        return header + "\n" + "\n".join(rows)

    def render_board(self, footer: str = "") -> str:
        with self.lock:
            grid_text = self._render_grid()
            p_y = self.player_yellow or "Waiting for opponent (🟡)..."
            text = (
                f"🔴 **CONNECT FOUR ARENA** 🟡\n\n"
                f"🔴 Player Red: {self.player_red}\n"
                f"🟡 Player Yellow: {p_y}\n\n"
                f"{grid_text}\n\n"
                f"👉 Turn: **{'🔴 ' + self.player_red if self.turn == 'R' else '🟡 ' + p_y}**\n"
                f"💡 Send `.c4 <1-7>` to drop a piece into a column!"
            )
            if footer:
                text += f"\n\n{footer}"
            return text

    def _check_win(self, disc: str) -> bool:
        # Horizontal
        for r in range(self.ROWS):
            for c in range(self.COLS - 3):
                if all(self.grid[r][c + i] == disc for i in range(4)):
                    return True
        # Vertical
        for r in range(self.ROWS - 3):
            for c in range(self.COLS):
                if all(self.grid[r + i][c] == disc for i in range(4)):
                    return True
        # Diagonal /
        for r in range(3, self.ROWS):
            for c in range(self.COLS - 3):
                if all(self.grid[r - i][c + i] == disc for i in range(4)):
                    return True
        # Diagonal \
        for r in range(self.ROWS - 3):
            for c in range(self.COLS - 3):
                if all(self.grid[r + i][c + i] == disc for i in range(4)):
                    return True
        return False

    def _is_full(self) -> bool:
        return all(self.grid[0][c] != " " for c in range(self.COLS))

    def make_move(self, col: int, username: str, database: Any = None) -> tuple[bool, str]:
        with self.lock:
            if self.status != "active":
                return False, "This game is no longer active."

            if col < 1 or col > 7:
                return False, "Invalid move! Choose a column between 1 and 7."

            col_idx = col - 1
            if self.grid[0][col_idx] != " ":
                return False, f"Column {col} is full! Choose another column."

            clean_user = username.lower().lstrip("@")
            r_clean = self.player_red.lower().lstrip("@")
            y_clean = self.player_yellow.lower().lstrip("@") if self.player_yellow else ""

            if self.turn == "R":
                if clean_user != r_clean and r_clean:
                    return False, f"It is not your turn! Waiting for {self.player_red} (🔴)."
            else:
                if not self.player_yellow:
                    if clean_user == r_clean:
                        return False, "You cannot play against yourself! Wait for an opponent."
                    self.player_yellow = f"@{username.lstrip('@')}"
                    y_clean = clean_user
                elif clean_user != y_clean:
                    return False, f"It is not your turn! Waiting for {self.player_yellow} (🟡)."

            self.last_activity = time.time()
            self.updated_at = time.time()
            # Gravity drop
            for r in range(self.ROWS - 1, -1, -1):
                if self.grid[r][col_idx] == " ":
                    self.grid[r][col_idx] = self.turn
                    break

            render = self.render_board()

            if self._check_win(self.turn):
                self.status = "won"
                self.winner = self.player_red if self.turn == "R" else self.player_yellow
                disc_sym = "🔴" if self.turn == "R" else "🟡"

                if database and hasattr(database, "update_user_stats"):
                    try:
                        database.update_user_stats(self.winner.lstrip("@"), self.winner.lstrip("@"), xp_delta=self.xp_reward)
                    except Exception as err:
                        LOGGER.debug("Error awarding C4 XP: %s", err)

                return True, f"🎉 **VICTORY!** {disc_sym} {self.winner} connected 4-in-a-row! (+{self.xp_reward} XP)\n\n{render}"
            elif self._is_full():
                self.status = "draw"
                return True, f"🤝 **IT'S A DRAW!** Board is full.\n\n{render}"
            else:
                self.turn = "Y" if self.turn == "R" else "R"
                return True, f"🔴 Dropped in col {col}!\n\n{render}"

    def play_turn(self, col: int, user_id: str, username: str) -> tuple[bool, str]:
        return self.make_move(col, username)


# =============================================================================
# 3. BLACKJACK GAME ENGINE
# =============================================================================

class BlackjackGame:
    """Thread-safe single-player Casino Blackjack session."""

    SUITS = ["S", "H", "D", "C"]
    RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]

    def __init__(
        self,
        thread_id: str = "",
        player_id: str = "",
        player_username: str = "Player",
        bet: float = 50.0,
        timeout_seconds: float = 180.0,
        user_id: str = "",
        username: str = "",
    ) -> None:
        self.thread_id = str(thread_id)
        self.player_id = str(player_id or user_id)
        self.user_id = self.player_id
        uname = player_username if player_username != "Player" else (username or "Player")
        self.player_username = uname.lstrip("@")
        self.username = self.player_username
        self.bet = float(bet)
        self.timeout_seconds = timeout_seconds
        self.status = "active"
        self.state = "PLAYING"
        self.result: str | None = None
        self.payout: float = 0.0
        self.net_profit: float = 0.0
        self.xp_earned: int = 0
        self.doubled: bool = False
        self.is_doubled: bool = False
        self.last_activity: float = time.time()
        self.updated_at: float = time.time()
        self.created_at: float = time.time()
        self.lock = threading.RLock()

        self.deck: list[Card] = [Card(r, s) for s in self.SUITS for r in self.RANKS]
        random.shuffle(self.deck)
        self.player_hand: list[Card] = [self._draw(), self._draw()]
        self.dealer_hand: list[Card] = [self._draw(), self._draw()]

    def is_expired(self, timeout_seconds: float = 180.0) -> bool:
        with self.lock:
            if self.status != "active":
                return False
            return (time.time() - self.last_activity) > timeout_seconds

    def _draw(self) -> Card:
        if not self.deck:
            self.deck = [Card(r, s) for s in self.SUITS for r in self.RANKS]
            random.shuffle(self.deck)
        return self.deck.pop()

    @classmethod
    def calculate_hand(cls, cards: list[Card]) -> tuple[int, bool]:
        """Return (score, is_soft)."""
        score = 0
        aces = 0
        for c in cards:
            r = c.rank
            if r in ("J", "Q", "K"):
                score += 10
            elif r == "A":
                score += 11
                aces += 1
            else:
                score += int(r)

        while score > 21 and aces > 0:
            score -= 10
            aces -= 1

        is_soft = (aces > 0 and score <= 21)
        return score, is_soft

    @classmethod
    def is_blackjack(cls, hand: list[Card]) -> bool:
        score, _ = cls.calculate_hand(hand)
        return score == 21 and len(hand) == 2

    def prompt(self) -> str:
        with self.lock:
            p_score, _ = self.calculate_hand(self.player_hand)
            p_str = " ".join(str(c) for c in self.player_hand)
            d_first = str(self.dealer_hand[0])

            if self.is_blackjack(self.player_hand):
                self.status = "completed"
                self.result = "blackjack"
                self.payout = self.bet * 2.5
                self.net_profit = self.bet * 1.5
                return (
                    f"🎰 **BLACKJACK! NATURAL 21!** 🔥\n\n"
                    f"👤 Your Hand: [{p_str}] (Value: 21)\n"
                    f"🤵 Dealer: [{d_first} 🂠]\n\n"
                    f"🏆 **YOU WIN!** Natural 21 Payout (+{self.net_profit:.0f} XP)"
                )

            return (
                f"🃏 **BLACKJACK (21)** 🎰\n\n"
                f"👤 Your Hand: [{p_str}] (Value: {p_score})\n"
                f"🤵 Dealer: [{d_first} 🂠]\n"
                f"💰 Bet: {self.bet:.0f} XP\n\n"
                f"👉 Commands: `.hit` (draw card) | `.stand` (stay) | `.double` (double down)"
            )

    def render(self) -> str:
        return self.prompt()

    def hit(self) -> tuple[bool, str]:
        with self.lock:
            if self.status != "active":
                return False, "No active Blackjack hand."
            self.last_activity = time.time()
            self.updated_at = time.time()
            card = self._draw()
            self.player_hand.append(card)
            score, _ = self.calculate_hand(self.player_hand)
            p_str = " ".join(str(c) for c in self.player_hand)

            if score > 21:
                self.status = "completed"
                self.result = "bust"
                self.payout = 0.0
                self.net_profit = -self.bet
                return True, (
                    f"💥 **BUSTED!** Your score exceeded 21.\n\n"
                    f"👤 Your Hand: [{p_str}] (Value: {score})\n"
                    f"💀 You lost {self.bet:.0f} XP. Better luck next time!"
                )
            elif score == 21:
                return self.stand()
            else:
                d_first = str(self.dealer_hand[0])
                return True, (
                    f"🃏 Hit: [{card}]\n"
                    f"👤 Your Hand: [{p_str}] (Value: {score})\n"
                    f"🤵 Dealer: [{d_first} 🂠]\n"
                    f"👉 Send `.hit` or `.stand`"
                )

    def double(self) -> tuple[bool, str]:
        with self.lock:
            if self.status != "active":
                return False, "No active Blackjack hand."
            self.doubled = True
            self.is_doubled = True
            self.bet *= 2
            card = self._draw()
            self.player_hand.append(card)
            return self.stand()

    def double_down(self) -> tuple[bool, str]:
        return self.double()

    def stand(self, database: Any = None) -> tuple[bool, str]:
        with self.lock:
            if self.status != "active":
                return False, "No active Blackjack hand."
            self.status = "completed"
            self.updated_at = time.time()
            p_score, _ = self.calculate_hand(self.player_hand)
            p_str = " ".join(str(c) for c in self.player_hand)

            if p_score > 21:
                self.result = "bust"
                self.net_profit = -self.bet
                return True, f"💥 **BUSTED!** [{p_str}] = {p_score} (> 21). Lost {self.bet:.0f} XP."

            # Dealer plays: hits until 17 or higher
            while self.calculate_hand(self.dealer_hand)[0] < 17:
                self.dealer_hand.append(self._draw())

            d_score, _ = self.calculate_hand(self.dealer_hand)
            d_str = " ".join(str(c) for c in self.dealer_hand)

            if d_score > 21:
                self.result = "win"
                self.payout = self.bet * 2
                self.net_profit = self.bet
                verdict = f"🎉 **DEALER BUSTS ({d_score})! YOU WIN!** (+{self.net_profit:.0f} XP)"
            elif p_score > d_score:
                self.result = "win"
                self.payout = self.bet * 2
                self.net_profit = self.bet
                verdict = f"🏆 **YOU WIN!** ({p_score} vs {d_score}) (+{self.net_profit:.0f} XP)"
            elif p_score < d_score:
                self.result = "lose"
                self.payout = 0.0
                self.net_profit = -self.bet
                verdict = f"💀 **DEALER WINS!** ({d_score} vs {p_score}) Lost {self.bet:.0f} XP."
            else:
                self.result = "push"
                self.payout = self.bet
                self.net_profit = 0.0
                verdict = f"🤝 **PUSH (TIE)!** Both scored {p_score}. Bet returned."

            if database and hasattr(database, "update_user_stats") and self.net_profit > 0:
                try:
                    database.update_user_stats(self.player_id, self.player_username, xp_delta=int(self.net_profit))
                except Exception as err:
                    LOGGER.debug("Error awarding Blackjack XP: %s", err)

            return True, (
                f"🎰 **BLACKJACK SHOWDOWN**\n\n"
                f"👤 Your Hand: [{p_str}] (Score: {p_score})\n"
                f"🤵 Dealer: [{d_str}] (Score: {d_score})\n\n"
                f"{verdict}"
            )


# =============================================================================
# 4. TAROT & ROAST ENGINES
# =============================================================================

@dataclass
class TarotCard:
    name: str
    number: int
    meaning: str
    upright_keywords: str
    reversed_keywords: str
    element: str
    emoji: str
    upright: str = ""
    reversed: str = ""


class TarotEngine:
    """Mystic 22 Major Arcana Tarot divination engine."""

    CARDS_DATA = [
        ("The Fool", 0, "New beginnings, boundless potential, taking leaps of faith", "Innocence, Adventure", "Recklessness, Fear", "Air", "🃏"),
        ("The Magician", 1, "Manifestation, resourcefulness, channeling cosmic power", "Creation, Skill", "Trickery, Blocked energy", "Air", "🪄"),
        ("The High Priestess", 2, "Intuition, sacred mysteries, spiritual wisdom", "Insight, Stillness", "Secrets, Disconnection", "Water", "🌙"),
        ("The Empress", 3, "Abundance, nurturing growth, divine creativity", "Flourishing, Beauty", "Smothering, Creative block", "Earth", "🌸"),
        ("The Emperor", 4, "Authority, structure, protective sovereignty", "Discipline, Leadership", "Tyranny, Rigidity", "Fire", "👑"),
        ("The Hierophant", 5, "Spiritual tradition, wisdom mentorship, higher principles", "Guidance, Heritage", "Rebellion, Dogma", "Earth", "📜"),
        ("The Lovers", 6, "Soulmate harmony, authentic alignment, sacred choices", "Unity, Deep chemistry", "Conflict, Disharmony", "Air", "💖"),
        ("The Chariot", 7, "Overcoming obstacles, triumph through determination", "Victory, Drive", "Lack of direction, Friction", "Water", "🏎️"),
        ("Strength", 8, "Inner courage, patience, compassion mastering force", "Endurance, Grace", "Self-doubt, Raw anger", "Fire", "🦁"),
        ("The Hermit", 9, "Introspection, inner light, searching deeper truths", "Soul-searching, Clarity", "Isolation, Loneliness", "Earth", "🕯️"),
        ("Wheel of Fortune", 10, "Karmic destiny, turning cycles, unexpected elevation", "Good fortune, Change", "Bad luck, Resistance", "Fire", "🎡"),
        ("Justice", 11, "Truth, cosmic cause & effect, balanced integrity", "Fairness, Honesty", "Injustice, Dishonesty", "Air", "⚖️"),
        ("The Hanged Man", 12, "Surrender, enlightened pause, shifting perspective", "Letting go, Epiphany", "Stalling, Martyrdom", "Water", "⏳"),
        ("Death", 13, "Profound transformation, shedding old skin, rebirth", "Endings, Renewal", "Fear of change, Stagnation", "Water", "🦋"),
        ("Temperance", 14, "Spiritual alchemy, divine balance, moderation", "Harmony, Patience", "Imbalance, Excess", "Fire", "🕊️"),
        ("The Devil", 15, "Shedding toxic attachments, breaking illusions", "Liberation, Awareness", "Addiction, Trap", "Earth", "😈"),
        ("The Tower", 16, "Sudden breakthrough, shattering illusions, raw truth", "Awakening, Revelation", "Averting disaster, Fear", "Fire", "⚡"),
        ("The Star", 17, "Radiant hope, celestial inspiration, peace of soul", "Blessings, Faith", "Despair, Lost faith", "Air", "🌟"),
        ("The Moon", 18, "Intuition guiding through illusion, subconscious depths", "Dreams, Psychic vision", "Confusion, Fear of unknown", "Water", "🌕"),
        ("The Sun", 19, "Radiant warmth, joyful victory, glorious vitality", "Success, Illumination", "Temporary cloud, Burnout", "Fire", "☀️"),
        ("Judgement", 20, "Spiritual rebirth, answering your calling, reckoning", "Redemption, Clarity", "Self-doubt, Harsh critique", "Fire", "🎺"),
        ("The World", 21, "Cosmic wholeness, mission accomplished, higher evolution", "Completion, Triumph", "Delayed finish, Incompletion", "Earth", "🌍"),
    ]

    def __init__(self) -> None:
        self.deck: list[TarotCard] = [
            TarotCard(name=d[0], number=d[1], meaning=d[2], upright_keywords=d[3], reversed_keywords=d[4], element=d[5], emoji=d[6], upright=d[2], reversed=d[4])
            for d in self.CARDS_DATA
        ]

    def draw_card(self, question: str = "") -> dict[str, Any]:
        card = random.choice(self.deck)
        is_reversed = random.random() < 0.25
        return {
            "card": card,
            "is_reversed": is_reversed,
            "question": question or "your path and destiny",
        }

    def format_single_reading(self, draw: dict[str, Any], username: str) -> str:
        card: TarotCard = draw["card"]
        is_rev = draw["is_reversed"]
        q = draw["question"]
        orientation = " (Reversed) 🔄" if is_rev else " (Upright) ✨"
        keywords = card.reversed_keywords if is_rev else card.upright_keywords

        return (
            f"🔮 **MYSTIC TAROT READING** 🔮\n\n"
            f"👤 Seeker: @{username.lstrip('@')}\n"
            f"❓ Inquiry: *\"{q}\"*\n\n"
            f"🎴 **Drawn Card**: {card.emoji} **{card.name}**{orientation}\n"
            f"🏷️ Keywords: *{keywords}*\n"
            f"✨ **Mystic Guidance**: {card.meaning}\n"
            f"🕯️ Element: {card.element} | Arcanum: #{card.number}"
        )

    def single_card_reading(self, username: str = "") -> str:
        draw = self.draw_card()
        return self.format_single_reading(draw, username or "Seeker")

    def draw_three_cards(self, topic: str = "") -> list[dict[str, Any]]:
        sample = random.sample(self.deck, 3)
        positions = ["The Past", "The Present", "The Future"]
        return [
            {"card": sample[i], "is_reversed": random.random() < 0.25, "position": positions[i], "topic": topic}
            for i in range(3)
        ]

    def format_three_card_spread(self, spread: list[dict[str, Any]], username: str) -> str:
        lines = [f"🔮 **3-CARD DESTINY SPREAD** for @{username.lstrip('@')} 🔮\n"]
        for item in spread:
            card: TarotCard = item["card"]
            pos = item["position"]
            is_rev = item["is_reversed"]
            orient = " (Reversed)" if is_rev else " (Upright)"
            kw = card.reversed_keywords if is_rev else card.upright_keywords
            lines.append(f"• **{pos}**: {card.emoji} **{card.name}**{orient} — *{kw}*")
        lines.append("\n🕯️ *Remember: The cards illuminate trajectories; your willpower writes destiny.*")
        return "\n".join(lines)

    def three_card_spread(self, username: str = "") -> str:
        spread = self.draw_three_cards()
        return self.format_three_card_spread(spread, username or "Seeker")

    def celtic_cross_spread(self, username: str = "") -> str:
        return self.three_card_spread(username)


class RoastBattleEngine:
    """Interactive multi-round roast showdown engine."""

    ROAST_VAULT = [
        "is living proof that even auto-correct gives up sometimes 📴",
        "has the emotional range of an unbuttered piece of toast 🍞",
        "brings the exact same energy as an unskippable YouTube ad 📺",
        "is built like an NPC with only three dialogue choices 🤖",
        "could get lost in a one-room apartment with Google Maps 🧭",
        "has aura points in the deep negative numbers rn 📉",
        "talks in 4K but delivers in 144p resolution 💀",
        "has the reaction speed of dial-up internet from 1996 📞",
    ]

    def battle(self, user1: str, user2: str) -> str:
        u1 = f"@{user1.lstrip('@')}"
        u2 = f"@{user2.lstrip('@')}"
        r1 = random.choice(self.ROAST_VAULT)
        r2 = random.choice([r for r in self.ROAST_VAULT if r != r1] or self.ROAST_VAULT)
        winner = random.choice([u1, u2])

        return (
            f"🔥 **AI ROAST BATTLE ARENA** 🔥\n\n"
            f"⚔️ {u1} vs {u2}\n\n"
            f"🥊 **ROUND 1**: {u1} {r1}\n"
            f"🥊 **ROUND 2**: {u2} {r2}\n\n"
            f"🏆 **JUDGE'S VERDICT**: {winner} delivered the ultimate emotional damage! 💀💥"
        )


# =============================================================================
# 5. TRIVIA SESSION
# =============================================================================

@dataclass
class TriviaGameSession:
    """Multiplayer interactive trivia session."""

    game_id: str
    thread_id: str
    starter_id: str
    starter_name: str
    question: TriviaQuestion
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    timeout_seconds: float = 120.0
    status: str = "active"
    winner_id: str | None = None
    winner_name: str | None = None
    xp_reward: int = 25
    attempts_by_user: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.lock = threading.RLock()
        self.xp_reward = self.question.xp_reward

    def is_expired(self, timeout_seconds: float = 120.0) -> bool:
        with self.lock:
            if self.status != "active":
                return False
            return (time.time() - self.last_activity) > timeout_seconds

    def prompt(self) -> str:
        with self.lock:
            letters = ["A", "B", "C", "D"]
            options_text = "\n".join(f"  {letters[i]}️⃣ {opt}" for i, opt in enumerate(self.question.options))
            return (
                f"🧠 **TRIVIA ARENA** [{self.question.category.upper()}]\n\n"
                f"❓ {self.question.question}\n\n"
                f"{options_text}\n\n"
                f"💡 *Reply with A, B, C, or D to answer! (+{self.xp_reward} XP)*\n"
                f"⏳ *Time limit: {int(self.timeout_seconds)}s*"
            )

    def handle_turn(
        self,
        sender_id: str,
        username: str,
        text: str,
        database: Any = None,
    ) -> str | None:
        with self.lock:
            if self.status != "active":
                return None

            clean_text = text.strip()
            if not clean_text:
                return None

            clean_upper = clean_text.upper().rstrip(".,!?")
            clean_lower = clean_text.lower()
            correct_opt = self.question.correct_option.upper()
            correct_ans_lower = self.question.correct_answer.lower()

            is_valid_choice = clean_upper in ("A", "B", "C", "D")
            is_valid_text = len(clean_text) >= 3 and (clean_lower in correct_ans_lower or correct_ans_lower in clean_lower)

            if not is_valid_choice and not is_valid_text:
                return None

            self.last_activity = time.time()
            self.attempts_by_user[sender_id] = self.attempts_by_user.get(sender_id, 0) + 1

            is_correct = False
            if clean_upper == correct_opt:
                is_correct = True
            elif clean_lower == correct_ans_lower or (len(correct_ans_lower) > 3 and correct_ans_lower in clean_lower):
                is_correct = True

            if is_correct:
                self.status = "completed"
                self.winner_id = sender_id
                self.winner_name = username

                if database and hasattr(database, "update_user_stats"):
                    try:
                        database.update_user_stats(sender_id, username, xp_delta=self.xp_reward)
                    except Exception as err:
                        LOGGER.debug("Failed awarding trivia XP: %s", err)

                return (
                    f"🎉 **CORRECT!** @{username.lstrip('@')} answered correctly! (+{self.xp_reward} XP)\n"
                    f"💡 Correct Answer: **{self.question.correct_option} ({self.question.correct_answer})**\n"
                    f"✨ {self.question.explanation}"
                )
            else:
                return f"❌ Incorrect answer, @{username.lstrip('@')}! Try again or let others guess."


# =============================================================================
# 6. MASTER GAME MANAGER & ENGINE
# =============================================================================

class GameManager:
    """Fine-grained thread-safe session store with automatic idle eviction."""

    def __init__(self, default_timeout: float = 300.0) -> None:
        self.default_timeout = default_timeout
        self._sessions: dict[str, Any] = {}
        self._lock = threading.RLock()

    def set_game(self, thread_id: str, game_type: str, game: Any) -> None:
        key = f"{thread_id}:{game_type}"
        with self._lock:
            self._sessions[key] = game

    def get_game(self, thread_id: str, game_type: str | None = None) -> Any | None:
        key = f"{thread_id}:{game_type}" if game_type else thread_id
        with self._lock:
            game = self._sessions.get(key)
            if game is not None:
                if hasattr(game, "is_expired") and game.is_expired():
                    del self._sessions[key]
                    return None
                return game

            for k, g in list(self._sessions.items()):
                if k.startswith(f"{thread_id}:"):
                    if hasattr(g, "is_expired") and g.is_expired():
                        del self._sessions[k]
                        continue
                    return g
        return None

    def remove_game(self, thread_id: str, game_type: str | None = None) -> Any | None:
        key = f"{thread_id}:{game_type}" if game_type else thread_id
        with self._lock:
            game = self._sessions.pop(key, None)
            if game is not None:
                return game
            for k, g in list(self._sessions.items()):
                if k.startswith(f"{thread_id}:"):
                    return self._sessions.pop(k, None)
        return None

    def cleanup_expired(self, timeout_seconds: float = 300.0) -> int:
        now = time.time()
        evicted = 0
        with self._lock:
            for k, g in list(self._sessions.items()):
                last = getattr(g, "last_activity", getattr(g, "updated_at", 0.0))
                is_exp = g.is_expired(timeout_seconds) if hasattr(g, "is_expired") else (now - last > timeout_seconds)
                if is_exp:
                    del self._sessions[k]
                    evicted += 1
        return evicted


class GamesEngine(GameManager):
    """Integrated Master Games Engine implementing all chat games, casino logic, and session pooling."""

    def __init__(
        self,
        database: Any = None,
        ai_service: Any = None,
        default_idle_timeout: float = 300.0,
    ) -> None:
        super().__init__(default_timeout=default_idle_timeout)
        self.database = database
        self.ai_service = ai_service
        self.tarot = TarotEngine()
        self.tarot_engine = self.tarot
        self.roast = RoastBattleEngine()
        self.roast_arena = self.roast
        self.trivia_service = TriviaService(ai_service)
        self.total_games_played = 0

    def start_trivia(
        self,
        thread_id: str,
        category: str = "",
        starter_id: str = "",
        starter_name: str = "",
    ) -> str:
        q = self.trivia_service.get_random_question(category)
        session = TriviaGameSession(
            game_id=f"trivia_{uuid.uuid4().hex[:8]}",
            thread_id=thread_id,
            starter_id=starter_id,
            starter_name=starter_name or "Player",
            question=q,
            timeout_seconds=120.0,
        )
        self.set_game(thread_id, "trivia", session)
        self.total_games_played += 1
        return session.prompt()

    def handle_ttt(
        self,
        thread_id: str,
        username: str = "Player",
        user_id: str = "",
        args: list[str] | None = None,
    ) -> str:
        clean_user = str(username).lstrip("@")
        args_list = args or []
        subcmd = args_list[0].lower() if args_list else ""

        if subcmd in ("end", "cancel", "stop"):
            g = self.remove_game(thread_id, "ttt")
            return f"🛑 Tic-Tac-Toe match ended by @{clean_user}." if g else "ℹ️ No active Tic-Tac-Toe match in this chat."

        existing: TicTacToeGame = self.get_game(thread_id, "ttt")

        if subcmd.isdigit() and 1 <= int(subcmd) <= 9:
            if not existing or existing.status != "active":
                return "ℹ️ No active Tic-Tac-Toe match! Send `.ttt` to start one."
            ok, msg = existing.make_move(int(subcmd), clean_user, self.database)
            if existing.status != "active":
                self.remove_game(thread_id, "ttt")
            return msg

        if existing and existing.status == "active":
            return f"⚠️ A Tic-Tac-Toe match is already in progress!\n\n{existing.render_board()}"

        opp = args_list[0] if (args_list and args_list[0].startswith("@")) else ""
        game = TicTacToeGame(thread_id=thread_id, player_x=f"@{clean_user}", player_o=opp)
        self.set_game(thread_id, "ttt", game)
        self.total_games_played += 1
        return game.render_board()

    def handle_c4(
        self,
        thread_id: str,
        username: str = "Player",
        user_id: str = "",
        args: list[str] | None = None,
    ) -> str:
        clean_user = str(username).lstrip("@")
        args_list = args or []
        subcmd = args_list[0].lower() if args_list else ""

        if subcmd in ("end", "cancel", "stop"):
            g = self.remove_game(thread_id, "c4")
            return f"🛑 Connect Four match ended by @{clean_user}." if g else "ℹ️ No active Connect Four match in this chat."

        existing: ConnectFourGame = self.get_game(thread_id, "c4")

        if subcmd.isdigit() and 1 <= int(subcmd) <= 7:
            if not existing or existing.status != "active":
                return "ℹ️ No active Connect Four match! Send `.c4` to start one."
            ok, msg = existing.make_move(int(subcmd), clean_user, self.database)
            if existing.status != "active":
                self.remove_game(thread_id, "c4")
            return msg

        if existing and existing.status == "active":
            return f"⚠️ A Connect Four match is already in progress!\n\n{existing.render_board()}"

        opp = args_list[0] if (args_list and args_list[0].startswith("@")) else ""
        game = ConnectFourGame(thread_id=thread_id, player_red=f"@{clean_user}", player_yellow=opp)
        self.set_game(thread_id, "c4", game)
        self.total_games_played += 1
        return game.render_board()

    def handle_blackjack(
        self,
        thread_id: str,
        username: str = "Player",
        user_id: str = "",
        action: str = "",
        args: list[str] | None = None,
    ) -> str:
        clean_user = str(username).lstrip("@")
        game_key = f"{thread_id}_{user_id}"
        existing: BlackjackGame = self.get_game(game_key, "blackjack")
        args_list = args or []

        if action in ("blackjack", "bj"):
            bet = 50.0
            if args_list and args_list[0].isdigit():
                bet = max(10.0, min(1000.0, float(args_list[0])))

            if existing and existing.status == "active":
                return f"⚠️ You already have an active Blackjack hand!\n\n{existing.prompt()}"

            game = BlackjackGame(thread_id=thread_id, player_id=user_id, player_username=clean_user, bet=bet)
            self.set_game(game_key, "blackjack", game)
            self.total_games_played += 1
            res = game.prompt()
            if game.status != "active":
                self.remove_game(game_key, "blackjack")
            return res

        if not existing or existing.status != "active":
            return "ℹ️ No active Blackjack hand. Start one with `.bj [bet]` (e.g. `.bj 50`)"

        if action in ("hit", "h"):
            ok, msg = existing.hit()
        elif action in ("stand", "s", "stay"):
            ok, msg = existing.stand(self.database)
        elif action in ("double", "doubledown", "dd"):
            ok, msg = existing.double()
        else:
            msg = existing.prompt()

        if existing.status != "active":
            self.remove_game(game_key, "blackjack")

        return msg

    def handle_tarot(self, username_or_args: Any = "Seeker", args: list[str] | None = None) -> str:
        if isinstance(username_or_args, str) and not isinstance(args, list):
            username = username_or_args
            args_list: list[str] = []
        elif isinstance(username_or_args, str) and isinstance(args, list):
            username = username_or_args
            args_list = args
        elif isinstance(username_or_args, list):
            username = "Seeker"
            args_list = username_or_args
        else:
            username = "Seeker"
            args_list = args or []

        q = " ".join(args_list).strip() if args_list else ""
        if len(args_list) >= 3 or "spread" in q.lower() or "three" in q.lower() or "3" in q.lower():
            spread = self.tarot.draw_three_cards(q)
            return self.tarot.format_three_card_spread(spread, username)
        single = self.tarot.draw_card(q)
        return self.tarot.format_single_reading(single, username)

    def handle_roast(
        self,
        thread_id: str = "",
        user_id: str = "",
        username: str = "",
        args: list[str] | None = None,
    ) -> str:
        args_list = args or []
        u1 = username.lstrip("@") or "Player"
        if not args_list:
            return self.roast.battle(u1, "Ineffa")
        if args_list[0].startswith("@"):
            u2 = args_list[0].lstrip("@")
            u3 = args_list[1].lstrip("@") if len(args_list) > 1 else u1
            return self.roast.battle(u2, u3)
        return self.roast.battle(u1, " ".join(args_list))

    def handle_roast_battle(
        self,
        username: str = "",
        args: list[str] | None = None,
        thread_id: str = "",
        user_id: str = "",
    ) -> str:
        return self.handle_roast(thread_id=thread_id, user_id=user_id, username=username, args=args)

    def handle_input(
        self,
        thread_id: str,
        sender_id: str,
        username: str,
        text: str,
    ) -> str | None:
        clean_user = username.lstrip("@")
        # Check active trivia
        trivia: TriviaGameSession = self.get_game(thread_id, "trivia")
        if trivia and trivia.status == "active":
            ans = trivia.handle_turn(sender_id, clean_user, text, self.database)
            if trivia.status != "active":
                self.remove_game(thread_id, "trivia")
            return ans

        # Check active TTT if text is a number
        if text.strip().isdigit():
            val = int(text.strip())
            ttt: TicTacToeGame = self.get_game(thread_id, "ttt")
            if ttt and ttt.status == "active" and 1 <= val <= 9:
                ok, msg = ttt.make_move(val, clean_user, self.database)
                if ttt.status != "active":
                    self.remove_game(thread_id, "ttt")
                return msg

            c4: ConnectFourGame = self.get_game(thread_id, "c4")
            if c4 and c4.status == "active" and 1 <= val <= 7:
                ok, msg = c4.make_move(val, clean_user, self.database)
                if c4.status != "active":
                    self.remove_game(thread_id, "c4")
                return msg

        return None

    def evict_idle_games(self, max_idle_seconds: float | None = None) -> list[str]:
        timeout = max_idle_seconds or self.default_timeout
        cleaned = self.cleanup_expired(timeout)
        return [f"evicted_{cleaned}"] if cleaned > 0 else []


Connect4Game = ConnectFourGame
RoastBattleArena = RoastBattleEngine
GamesManager = GamesEngine
GAMES_ENGINE = GamesEngine()
