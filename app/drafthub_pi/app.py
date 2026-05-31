from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pygame


WIDTH = 480
HEIGHT = 480
FPS = 30

COLORS = {
    "background": (8, 28, 50),
    "panel": (15, 54, 91),
    "panel_alt": (12, 43, 72),
    "line": (54, 108, 150),
    "accent": (52, 198, 235),
    "accent_dark": (18, 122, 158),
    "text": (238, 246, 251),
    "muted": (164, 194, 214),
    "success": (93, 210, 145),
}


@dataclass(frozen=True)
class NavItem:
    key: str
    label: str


NAV_ITEMS = (
    NavItem("playing", "Playing"),
    NavItem("library", "Library"),
    NavItem("settings", "Settings"),
)


class DraftHubApp:
    def __init__(self, windowed: bool = False) -> None:
        pygame.init()
        pygame.font.init()
        flags = 0 if windowed else pygame.FULLSCREEN
        self.screen = pygame.display.set_mode((WIDTH, HEIGHT), flags)
        pygame.display.set_caption("DraftHub")
        pygame.mouse.set_visible(windowed)
        self.clock = pygame.time.Clock()
        self.running = True
        self.active_view = "playing"
        self.font_small = pygame.font.Font(None, 22)
        self.font_body = pygame.font.Font(None, 26)
        self.font_heading = pygame.font.Font(None, 36)
        self.font_large = pygame.font.Font(None, 52)
        self.nav_rects: dict[str, pygame.Rect] = {}

    def run(self) -> None:
        while self.running:
            self.handle_events()
            self.draw()
            pygame.display.flip()
            self.clock.tick(FPS)
        pygame.quit()

    def handle_events(self) -> None:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.KEYDOWN and event.key in (pygame.K_ESCAPE, pygame.K_q):
                self.running = False
            elif event.type == pygame.MOUSEBUTTONUP:
                self.handle_tap(event.pos)
            elif event.type == pygame.FINGERUP:
                self.handle_tap((int(event.x * WIDTH), int(event.y * HEIGHT)))

    def handle_tap(self, position: tuple[int, int]) -> None:
        for key, rect in self.nav_rects.items():
            if rect.collidepoint(position):
                self.active_view = key
                return

    def draw(self) -> None:
        self.screen.fill(COLORS["background"])
        self.draw_header()
        if self.active_view == "playing":
            self.draw_playing()
        elif self.active_view == "library":
            self.draw_library()
        else:
            self.draw_settings()
        self.draw_navigation()

    def draw_header(self) -> None:
        pygame.draw.rect(self.screen, COLORS["panel_alt"], pygame.Rect(0, 0, WIDTH, 68))
        self.draw_text("DraftHub", (20, 16), self.font_heading)
        now = datetime.now().strftime("%H:%M")
        self.draw_text(now, (WIDTH - 20, 20), self.font_body, COLORS["muted"], align="right")

    def draw_playing(self) -> None:
        self.draw_text("Now Playing", (20, 92), self.font_heading)
        media_rect = pygame.Rect(20, 140, 250, 250)
        pygame.draw.rect(self.screen, COLORS["panel"], media_rect, border_radius=6)
        pygame.draw.rect(self.screen, COLORS["line"], media_rect, width=2, border_radius=6)
        self.draw_text("No media", media_rect.center, self.font_heading, COLORS["muted"], align="center")

        self.draw_text("Ready", (300, 156), self.font_heading, COLORS["success"])
        self.draw_text("No playlist loaded", (300, 204), self.font_body, COLORS["muted"])
        self.draw_text("0 / 0 items", (300, 238), self.font_body, COLORS["muted"])

    def draw_library(self) -> None:
        self.draw_text("Library", (20, 92), self.font_heading)
        self.draw_text("No media uploaded yet.", (20, 150), self.font_body, COLORS["muted"])
        self.draw_text("DraftHub uploads will appear here.", (20, 184), self.font_small, COLORS["muted"])

    def draw_settings(self) -> None:
        self.draw_text("Settings", (20, 92), self.font_heading)
        rows = (
            ("Display", "480 x 480"),
            ("Playback", "Ready"),
            ("Network", self.network_status()),
            ("Storage", str(Path.cwd())),
        )
        y = 148
        for label, value in rows:
            pygame.draw.line(self.screen, COLORS["line"], (20, y + 38), (460, y + 38), width=1)
            self.draw_text(label, (20, y), self.font_body)
            self.draw_text(value, (460, y), self.font_body, COLORS["muted"], align="right")
            y += 58

    def draw_navigation(self) -> None:
        nav_top = HEIGHT - 66
        pygame.draw.rect(self.screen, COLORS["panel_alt"], pygame.Rect(0, nav_top, WIDTH, 66))
        item_width = WIDTH // len(NAV_ITEMS)
        self.nav_rects.clear()
        for index, item in enumerate(NAV_ITEMS):
            rect = pygame.Rect(index * item_width, nav_top, item_width, 66)
            self.nav_rects[item.key] = rect
            if item.key == self.active_view:
                pygame.draw.rect(self.screen, COLORS["accent_dark"], rect)
                pygame.draw.rect(self.screen, COLORS["accent"], pygame.Rect(rect.x, rect.y, rect.width, 4))
            self.draw_text(item.label, rect.center, self.font_body, align="center")

    def draw_text(
        self,
        text: str,
        position: tuple[int, int],
        font: pygame.font.Font,
        color: tuple[int, int, int] = COLORS["text"],
        align: str = "left",
    ) -> None:
        surface = font.render(text, True, color)
        rect = surface.get_rect()
        if align == "center":
            rect.center = position
        elif align == "right":
            rect.topright = position
        else:
            rect.topleft = position
        self.screen.blit(surface, rect)

    @staticmethod
    def network_status() -> str:
        return os.environ.get("DRAFTHUB_NETWORK_STATUS", "Checking...")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DraftHub Pi touchscreen application")
    parser.add_argument("--windowed", action="store_true", help="Run in a 480x480 desktop window")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    DraftHubApp(windowed=args.windowed).run()
    return 0

