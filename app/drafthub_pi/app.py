from __future__ import annotations

import argparse
import json
import logging
import mmap
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pygame

from . import __version__


WIDTH = 480
HEIGHT = 480
FPS = 10
LOGGER = logging.getLogger("drafthub_pi")
FRAMEBUFFER_PATH = Path("/dev/fb0")
FRAMEBUFFER_SYSFS_PATH = Path("/sys/class/graphics/fb0")
LOGO_PATH = Path(__file__).with_name("assets") / "drafthub-logo.png"
LOGO_CROP = pygame.Rect(96, 324, 824, 392)
LOGO_SIZE = (156, 40)
DEFAULT_FRAMEBUFFER_VIEWPORT = "480x480+0+0"
DEFAULT_MEDIA_DIR = Path("/var/lib/drafthub/media")
DEFAULT_UPLOAD_PORT = 8080

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
    "warning": (244, 185, 66),
    "danger": (222, 105, 115),
}


@dataclass(frozen=True)
class NavItem:
    key: str
    label: str


NAV_ITEMS = (
    NavItem("connection", "Connect"),
    NavItem("media", "Media"),
    NavItem("playlist", "Playlist"),
    NavItem("manage", "Manage"),
)


class DraftHubApp:
    def __init__(self, windowed: bool = False) -> None:
        pygame.font.init()
        if windowed:
            pygame.display.init()
            self.screen = pygame.display.set_mode((WIDTH, HEIGHT))
            self.canvas = self.screen
            self.framebuffer = None
            LOGGER.info(
                "SDL video driver=%s output_size=%sx%s windowed=True",
                pygame.display.get_driver(),
                self.screen.get_width(),
                self.screen.get_height(),
            )
        else:
            self.framebuffer = FramebufferPresenter(FRAMEBUFFER_PATH, FRAMEBUFFER_SYSFS_PATH)
            self.screen = self.framebuffer.surface
            self.canvas = pygame.Surface((WIDTH, HEIGHT))
        if windowed:
            pygame.display.set_caption("DraftHub")
            pygame.mouse.set_visible(True)
        self.clock = pygame.time.Clock()
        self.windowed = windowed
        self.running = True
        self.active_view = "connection"
        self.video_size = parse_size(os.environ.get("DRAFTHUB_VIDEO_SIZE", f"{WIDTH}x{HEIGHT}"))
        self.font_small = pygame.font.Font(None, 22)
        self.font_body = pygame.font.Font(None, 26)
        self.font_heading = pygame.font.Font(None, 32)
        self.font_large = pygame.font.Font(None, 44)
        self.logo = self.load_logo()
        self.build_label = get_build_label()
        self.ip_address = ""
        self.ip_checked_at = 0.0
        self.nav_rects: dict[str, pygame.Rect] = {}
        self.upload_server = UploadServer(DEFAULT_MEDIA_DIR, DEFAULT_UPLOAD_PORT)
        self.upload_server.start()

    def run(self) -> None:
        while self.running:
            playback_path = self.upload_server.take_playback_request()
            if playback_path is not None:
                if self.framebuffer is None:
                    LOGGER.error("HTTP playback is available only in framebuffer mode")
                    continue
                try:
                    if playback_path.suffix.lower() == ".mp4":
                        FfmpegMp4Player(playback_path, *self.video_size, 30).run(
                            self.framebuffer,
                            self.upload_server.playback_stop,
                        )
                    else:
                        RawVidPlayer(playback_path, *self.video_size, 30).run(
                            self.framebuffer,
                            self.upload_server.playback_stop,
                        )
                except Exception:
                    LOGGER.exception("Playback failed for %s", playback_path)
                continue
            self.handle_events()
            self.draw()
            self.present_canvas()
            self.clock.tick(FPS)
        LOGGER.info("Application event loop stopped")
        if self.framebuffer is not None:
            self.framebuffer.close()
        self.upload_server.close()
        pygame.quit()

    def present_canvas(self) -> None:
        if self.canvas is self.screen:
            pygame.display.flip()
            return
        self.framebuffer.present(self.canvas)

    def handle_events(self) -> None:
        if not self.windowed:
            return
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                LOGGER.info("Received pygame.QUIT")
                self.running = False
            elif event.type == pygame.KEYDOWN and event.key in (pygame.K_ESCAPE, pygame.K_q):
                LOGGER.info("Received exit key: %s", pygame.key.name(event.key))
                self.running = False
            elif event.type == pygame.MOUSEBUTTONUP:
                self.handle_tap(self.to_canvas_position(event.pos))
            elif event.type == pygame.FINGERUP:
                self.handle_tap((int(event.x * WIDTH), int(event.y * HEIGHT)))

    def handle_tap(self, position: tuple[int, int]) -> None:
        for key, rect in self.nav_rects.items():
            if rect.collidepoint(position):
                self.active_view = key
                return

    def draw(self) -> None:
        self.canvas.fill(COLORS["background"])
        self.draw_header()
        if self.active_view == "connection":
            self.draw_connection()
        elif self.active_view == "media":
            self.draw_media()
        elif self.active_view == "playlist":
            self.draw_playlist()
        else:
            self.draw_manage()
        self.draw_navigation()

    def draw_header(self) -> None:
        pygame.draw.circle(self.canvas, COLORS["panel_alt"], (WIDTH // 2, 0), 122)
        if self.logo is not None:
            self.canvas.blit(self.logo, ((WIDTH - self.logo.get_width()) // 2, 7))
        else:
            self.draw_text("DraftHub", (WIDTH // 2, 14), self.font_heading, align="center")
        now = datetime.now().strftime("%H:%M")
        self.draw_text(now, (WIDTH // 2, 46), self.font_small, COLORS["muted"], align="center")

    @staticmethod
    def load_logo() -> pygame.Surface | None:
        try:
            source = pygame.image.load(LOGO_PATH)
            cropped = source.subsurface(LOGO_CROP)
            return pygame.transform.smoothscale(cropped, LOGO_SIZE)
        except (FileNotFoundError, pygame.error, ValueError):
            LOGGER.exception("Unable to load logo asset %s", LOGO_PATH)
            return None

    def draw_connection(self) -> None:
        self.refresh_network_address()
        manager_url = f"http://{self.ip_address}:8080" if self.ip_address else "Waiting for network"
        self.draw_section_title("Connection", "Network status")
        self.draw_panel(pygame.Rect(58, 112, 364, 128), "NETWORK", "Connected", COLORS["success"])
        self.draw_label_value("SSID", os.environ.get("DRAFTHUB_WIFI_SSID", "DraftHub WiFi"), 140)
        self.draw_label_value("IP", self.ip_address or "Detecting...", 166)
        self.draw_label_value("Manager", manager_url, 192)
        self.draw_label_value("Build", self.build_label, 218)

        self.draw_panel(pygame.Rect(58, 252, 364, 80), "DEVICE HOTSPOT", "Ready", COLORS["accent"])
        self.draw_label_value("Access point", os.environ.get("DRAFTHUB_AP_SSID", "DraftHub-Setup"), 278)
        self.draw_label_value("Setup", "http://192.168.4.1", 306)
        self.draw_button("WiFi settings", pygame.Rect(126, 342, 132, 32), COLORS["accent_dark"])
        self.draw_button("Refresh", pygame.Rect(268, 342, 86, 32), COLORS["panel_alt"])

    def refresh_network_address(self) -> None:
        now = time.monotonic()
        if self.ip_address and now - self.ip_checked_at < 15:
            return
        self.ip_address = get_lan_ip_address()
        self.ip_checked_at = now

    def draw_media(self) -> None:
        self.draw_section_title("Media", "Playback preview")
        preview = pygame.Rect(70, 116, 156, 156)
        pygame.draw.rect(self.canvas, COLORS["panel"], preview, border_radius=8)
        pygame.draw.rect(self.canvas, COLORS["line"], preview, width=2, border_radius=8)
        self.draw_text("NO MEDIA", preview.center, self.font_body, COLORS["muted"], align="center")

        self.draw_text("NOW PLAYING", (242, 122), self.font_small, COLORS["accent"])
        self.draw_text("Ready", (242, 150), self.font_heading, COLORS["success"])
        self.draw_text("Waiting for", (242, 190), self.font_body)
        self.draw_text("playlist", (242, 216), self.font_body)
        self.draw_text("0 / 0 items", (242, 246), self.font_small, COLORS["muted"])

        self.draw_panel(pygame.Rect(76, 288, 328, 56), "LIBRARY", "0 files", COLORS["muted"])
        self.draw_text("Upload from the web manager", (240, 320), self.font_small, COLORS["muted"], align="center")
        self.draw_button("Start playback", pygame.Rect(168, 354, 144, 32), COLORS["accent_dark"])

    def draw_playlist(self) -> None:
        self.draw_section_title("Playlist", "Playback order")
        self.draw_panel(pygame.Rect(58, 112, 364, 172), "ACTIVE PLAYLIST", "0 items", COLORS["muted"])
        self.draw_text("No playlist items yet", (240, 158), self.font_body, COLORS["muted"], align="center")
        self.draw_text("Add files in Manage", (240, 186), self.font_small, COLORS["muted"], align="center")
        self.draw_playlist_row(1, "Playlist is empty", "Add files in Manage", 214, selected=True)
        self.draw_button("Item -", pygame.Rect(72, 302, 76, 32), COLORS["panel_alt"])
        self.draw_button("Item +", pygame.Rect(156, 302, 76, 32), COLORS["panel_alt"])
        self.draw_button("Up", pygame.Rect(240, 302, 76, 32), COLORS["panel_alt"])
        self.draw_button("Down", pygame.Rect(324, 302, 76, 32), COLORS["panel_alt"])
        self.draw_button("Save playlist", pygame.Rect(168, 344, 144, 32), COLORS["accent_dark"])

    def draw_manage(self) -> None:
        self.draw_section_title("Manage media", "Choose active files")
        self.draw_panel(pygame.Rect(58, 112, 364, 172), "ON-DEVICE MEDIA", "0 files", COLORS["muted"])
        self.draw_text("No media uploaded yet", (240, 158), self.font_body, COLORS["muted"], align="center")
        self.draw_text("Use the web manager to add files", (240, 186), self.font_small, COLORS["muted"], align="center")
        self.draw_media_row("[ ]", "Library is empty", "Upload a .vid file", 214)
        self.draw_button("Previous", pygame.Rect(72, 302, 76, 32), COLORS["panel_alt"])
        self.draw_button("Next", pygame.Rect(156, 302, 76, 32), COLORS["panel_alt"])
        self.draw_button("Add", pygame.Rect(240, 302, 76, 32), COLORS["accent_dark"])
        self.draw_button("Delete", pygame.Rect(324, 302, 76, 32), COLORS["danger"])

    def draw_section_title(self, title: str, subtitle: str) -> None:
        self.draw_text(title, (WIDTH // 2, 70), self.font_heading, align="center")
        self.draw_text(subtitle, (WIDTH // 2, 96), self.font_small, COLORS["muted"], align="center")

    def draw_panel(
        self,
        rect: pygame.Rect,
        title: str,
        status: str,
        status_color: tuple[int, int, int],
    ) -> None:
        pygame.draw.rect(self.canvas, COLORS["panel"], rect, border_radius=8)
        pygame.draw.rect(self.canvas, COLORS["line"], rect, width=1, border_radius=8)
        self.draw_text(title, (rect.x + 12, rect.y + 10), self.font_small, COLORS["accent"])
        self.draw_text(status, (rect.right - 12, rect.y + 10), self.font_small, status_color, align="right")

    def draw_label_value(self, label: str, value: str, y: int) -> None:
        self.draw_text(label, (72, y), self.font_small, COLORS["muted"])
        self.draw_text(value, (408, y), self.font_small, align="right")

    def draw_status_chip(
        self,
        label: str,
        position: tuple[int, int],
        color: tuple[int, int, int],
    ) -> None:
        rect = pygame.Rect(position[0], position[1], 72, 24)
        pygame.draw.rect(self.canvas, COLORS["panel"], rect, border_radius=12)
        pygame.draw.circle(self.canvas, color, (rect.x + 13, rect.centery), 4)
        self.draw_text(label, (rect.x + 24, rect.y + 5), self.font_small, color)

    def draw_button(self, label: str, rect: pygame.Rect, color: tuple[int, int, int]) -> None:
        pygame.draw.rect(self.canvas, color, rect, border_radius=6)
        pygame.draw.rect(self.canvas, COLORS["line"], rect, width=1, border_radius=6)
        self.draw_text(label, rect.center, self.font_small, align="center")

    def draw_playlist_row(self, index: int, title: str, detail: str, y: int, selected: bool = False) -> None:
        rect = pygame.Rect(72, y, 336, 56)
        pygame.draw.rect(self.canvas, COLORS["panel_alt"] if selected else COLORS["panel"], rect, border_radius=5)
        if selected:
            pygame.draw.rect(self.canvas, COLORS["accent"], pygame.Rect(rect.x, rect.y, 4, rect.height))
        self.draw_text(str(index), (84, y + 17), self.font_body, COLORS["accent"])
        self.draw_text(title, (112, y + 9), self.font_body)
        self.draw_text(detail, (112, y + 31), self.font_small, COLORS["muted"])

    def draw_media_row(self, marker: str, title: str, detail: str, y: int) -> None:
        rect = pygame.Rect(72, y, 336, 56)
        pygame.draw.rect(self.canvas, COLORS["panel_alt"], rect, border_radius=5)
        self.draw_text(marker, (84, y + 17), self.font_body, COLORS["accent"])
        self.draw_text(title, (122, y + 9), self.font_body)
        self.draw_text(detail, (122, y + 31), self.font_small, COLORS["muted"])

    def draw_navigation(self) -> None:
        nav_top = HEIGHT - 92
        nav_left = 94
        nav_width = 292
        pygame.draw.rect(self.canvas, COLORS["panel_alt"], pygame.Rect(nav_left, nav_top, nav_width, 36), border_radius=12)
        item_width = nav_width // len(NAV_ITEMS)
        self.nav_rects.clear()
        for index, item in enumerate(NAV_ITEMS):
            rect = pygame.Rect(nav_left + index * item_width, nav_top, item_width, 36)
            self.nav_rects[item.key] = rect
            if item.key == self.active_view:
                pygame.draw.rect(self.canvas, COLORS["accent_dark"], rect)
                pygame.draw.rect(self.canvas, COLORS["accent"], pygame.Rect(rect.x, rect.y, rect.width, 4))
            self.draw_text(item.label, rect.center, self.font_small, align="center")

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
        self.canvas.blit(surface, rect)

    def to_canvas_position(self, position: tuple[int, int]) -> tuple[int, int]:
        return (
            position[0] * WIDTH // self.screen.get_width(),
            position[1] * HEIGHT // self.screen.get_height(),
        )

class FramebufferPresenter:
    def __init__(self, device_path: Path, sysfs_path: Path) -> None:
        width, height = (
            int(value) for value in (sysfs_path / "virtual_size").read_text().strip().split(",")
        )
        bits_per_pixel = int((sysfs_path / "bits_per_pixel").read_text().strip())
        if bits_per_pixel not in (16, 32):
            raise RuntimeError(
                f"Expected a 16-bit or 32-bit framebuffer, got {bits_per_pixel} bits per pixel"
            )

        self.bits_per_pixel = bits_per_pixel
        self.bytes_per_pixel = bits_per_pixel // 8
        if bits_per_pixel == 16:
            self.surface = pygame.Surface(
                (width, height),
                depth=16,
                masks=(0xF800, 0x07E0, 0x001F, 0),
            )
        else:
            self.surface = pygame.Surface((width, height), depth=32)
        self.rgb565_surface_cache: pygame.Surface | None = None
        self.rgb565_surface_cache_size: tuple[int, int] | None = None
        self.output_size = (width, height)
        self.viewport = self.parse_viewport(
            os.environ.get("DRAFTHUB_FRAMEBUFFER_VIEWPORT", DEFAULT_FRAMEBUFFER_VIEWPORT)
        )
        self.video_viewport = self.parse_viewport(
            os.environ.get("DRAFTHUB_VIDEO_VIEWPORT", f"{width}x{height}+0+0")
        )
        self.clear_rect = self.output_size_rect if os.environ.get("DRAFTHUB_FRAMEBUFFER_CLEAR") == "full" else self.viewport
        stride_path = sysfs_path / "stride"
        self.stride = (
            int(stride_path.read_text().strip()) if stride_path.exists() else self.surface.get_pitch()
        )
        self.device = device_path.open("r+b", buffering=0)
        self.buffer = mmap.mmap(self.device.fileno(), self.stride * height)
        LOGGER.info(
            "Framebuffer device=%s output_size=%sx%s viewport=%sx%s+%s+%s video_viewport=%sx%s+%s+%s clear=%sx%s+%s+%s bits_per_pixel=%s bytes_per_pixel=%s pitch=%s stride=%s",
            device_path,
            width,
            height,
            self.viewport.width,
            self.viewport.height,
            self.viewport.x,
            self.viewport.y,
            self.video_viewport.width,
            self.video_viewport.height,
            self.video_viewport.x,
            self.video_viewport.y,
            self.clear_rect.width,
            self.clear_rect.height,
            self.clear_rect.x,
            self.clear_rect.y,
            bits_per_pixel,
            self.bytes_per_pixel,
            self.surface.get_pitch(),
            self.stride,
        )

    def present(self, canvas: pygame.Surface, viewport: pygame.Rect | None = None) -> None:
        target_viewport = viewport or self.viewport
        source_width, source_height = canvas.get_size()
        scale = min(target_viewport.width / source_width, target_viewport.height / source_height)
        target_size = (int(source_width * scale), int(source_height * scale))
        target_x = target_viewport.x + (target_viewport.width - target_size[0]) // 2
        target_y = target_viewport.y + (target_viewport.height - target_size[1]) // 2
        self.surface.fill(COLORS["background"], self.clear_rect)
        scaled_canvas = canvas if canvas.get_size() == target_size else pygame.transform.scale(canvas, target_size)
        self.surface.blit(scaled_canvas, (target_x, target_y))
        self.write_rect(self.clear_rect)

    @property
    def output_size_rect(self) -> pygame.Rect:
        return pygame.Rect(0, 0, *self.output_size)

    def write_rect(self, rect: pygame.Rect) -> None:
        pixels = memoryview(self.surface.get_buffer())
        pitch = self.surface.get_pitch()
        row_bytes = rect.width * self.bytes_per_pixel
        if rect.x == 0 and row_bytes == pitch and self.stride == pitch:
            source_offset = rect.y * pitch
            self.buffer.seek(rect.y * self.stride)
            self.buffer.write(pixels[source_offset : source_offset + rect.height * pitch])
            return
        for row in range(rect.height):
            source_offset = (rect.y + row) * pitch + rect.x * self.bytes_per_pixel
            self.buffer.seek(
                (rect.y + row) * self.stride + rect.x * self.bytes_per_pixel
            )
            self.buffer.write(pixels[source_offset : source_offset + row_bytes])

    def present_rgb565(self, frame: bytes, width: int, height: int) -> None:
        if (
            self.bits_per_pixel == 16
            and width == self.video_viewport.width
            and height == self.video_viewport.height
            and self.video_viewport.x == 0
            and self.video_viewport.y == 0
            and self.stride == width * 2
        ):
            self.buffer.seek(0)
            self.buffer.write(frame)
            return

        frame_surface = self.get_rgb565_surface(width, height)
        frame_surface.get_buffer().write(frame)
        if width == self.video_viewport.width and height == self.video_viewport.height:
            self.surface.blit(frame_surface, self.video_viewport.topleft)
            self.write_rect(self.video_viewport)
            return
        self.present(frame_surface, self.video_viewport)

    def get_rgb565_surface(self, width: int, height: int) -> pygame.Surface:
        size = (width, height)
        if self.rgb565_surface_cache is None or self.rgb565_surface_cache_size != size:
            self.rgb565_surface_cache = pygame.Surface(
                size,
                depth=16,
                masks=(0xF800, 0x07E0, 0x001F, 0),
            )
            self.rgb565_surface_cache_size = size
        return self.rgb565_surface_cache

    def parse_viewport(self, value: str) -> pygame.Rect:
        match = re.fullmatch(r"(\d+)x(\d+)\+(\d+)\+(\d+)", value)
        if not match:
            raise ValueError(
                "DRAFTHUB_FRAMEBUFFER_VIEWPORT must use WIDTHxHEIGHT+X+Y, for example 480x480+0+0"
            )
        width, height, x, y = (int(part) for part in match.groups())
        output_width, output_height = self.output_size
        if width <= 0 or height <= 0 or x + width > output_width or y + height > output_height:
            raise ValueError(
                f"Framebuffer viewport {value} does not fit inside {output_width}x{output_height}"
            )
        return pygame.Rect(x, y, width, height)

    def close(self) -> None:
        self.buffer.close()
        self.device.close()


class UploadServer:
    SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
    MEDIA_SUFFIXES = (".vid", ".rgb565", ".mp4")
    PLAYABLE_SUFFIXES = (".vid", ".mp4")

    def __init__(self, media_dir: Path, port: int) -> None:
        self.media_dir = media_dir
        self.port = port
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.playback_lock = threading.Lock()
        self.playback_request: Path | None = None
        self.playback_stop = threading.Event()

    def start(self) -> None:
        try:
            self.media_dir.mkdir(parents=True, exist_ok=True)
            self.server = ThreadingHTTPServer(("0.0.0.0", self.port), self.make_handler())
        except OSError:
            LOGGER.exception("Unable to start upload server on port %s", self.port)
            return
        self.thread = threading.Thread(target=self.server.serve_forever, name="upload-server", daemon=True)
        self.thread.start()
        LOGGER.info("Upload server listening on http://0.0.0.0:%s media_dir=%s", self.port, self.media_dir)

    def close(self) -> None:
        if self.server is None:
            return
        self.server.shutdown()
        self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=2)

    def make_handler(self) -> type[BaseHTTPRequestHandler]:
        upload_server = self

        class Handler(BaseHTTPRequestHandler):
            def do_OPTIONS(self) -> None:
                self.send_response(204)
                self.send_cors_headers()
                self.end_headers()

            def do_GET(self) -> None:
                if urllib.parse.urlparse(self.path).path != "/media-index":
                    self.send_error(404)
                    return
                files = [
                    {"name": path.name, "size": path.stat().st_size}
                    for path in sorted(upload_server.media_dir.iterdir())
                    if path.is_file() and path.name.lower().endswith(upload_server.MEDIA_SUFFIXES)
                ]
                payload = json.dumps({"files": files}).encode("utf-8")
                self.send_response(200)
                self.send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self) -> None:
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/play":
                    self.handle_play(parsed.query)
                    return
                if parsed.path == "/stop":
                    upload_server.playback_stop.set()
                    self.send_text(200, "Playback stopped")
                    return
                if parsed.path != "/upload":
                    self.send_error(404)
                    return
                query = urllib.parse.parse_qs(parsed.query)
                upload_name = query.get("name", [""])[0]
                try:
                    target_name, compressed = upload_server.validate_name(upload_name)
                    content_length = int(self.headers.get("Content-Length", "0"))
                    if content_length <= 0:
                        raise ValueError("Upload body is empty")
                    upload_server.receive_upload(self.rfile, target_name, content_length, compressed)
                except (OSError, ValueError) as exc:
                    LOGGER.exception("Upload failed for %s", upload_name)
                    self.send_error(400, str(exc))
                    return
                self.send_text(200, f"Uploaded {target_name}")

            def handle_play(self, query_string: str) -> None:
                query = urllib.parse.parse_qs(query_string)
                media_name = query.get("name", [""])[0]
                try:
                    target_name, compressed = upload_server.validate_name(media_name)
                    if compressed or not target_name.lower().endswith(upload_server.PLAYABLE_SUFFIXES):
                        raise ValueError("Playback supports uploaded .vid and .mp4 files")
                    upload_server.request_playback(target_name)
                except (OSError, ValueError) as exc:
                    self.send_error(400, str(exc))
                    return
                self.send_text(200, f"Starting {target_name}")

            def send_text(self, status: int, text: str) -> None:
                message = text.encode("utf-8")
                self.send_response(status)
                self.send_cors_headers()
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(message)))
                self.end_headers()
                self.wfile.write(message)

            def send_cors_headers(self) -> None:
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")

            def log_message(self, format: str, *args: object) -> None:
                LOGGER.info("HTTP %s - %s", self.address_string(), format % args)

        return Handler

    def validate_name(self, upload_name: str) -> tuple[str, bool]:
        if not self.SAFE_NAME.fullmatch(upload_name):
            raise ValueError("Bad file name")
        compressed = upload_name.lower().endswith(".zlib")
        target_name = upload_name[:-5] if compressed else upload_name
        if not target_name.lower().endswith(self.MEDIA_SUFFIXES):
            raise ValueError("Supported uploads: .vid, .rgb565, .mp4, and .zlib transport copies")
        return target_name, compressed

    def receive_upload(self, source, target_name: str, content_length: int, compressed: bool) -> None:
        target_path = self.media_dir / target_name
        part_path = target_path.with_name(target_path.name + ".part")
        remaining = content_length
        decompressor = zlib.decompressobj() if compressed else None
        try:
            with part_path.open("wb") as output:
                while remaining:
                    chunk = source.read(min(64 * 1024, remaining))
                    if not chunk:
                        raise OSError("Upload ended early")
                    remaining -= len(chunk)
                    output.write(decompressor.decompress(chunk) if decompressor is not None else chunk)
                if decompressor is not None:
                    output.write(decompressor.flush())
            os.replace(part_path, target_path)
            LOGGER.info("Uploaded media path=%s transport_bytes=%s", target_path, content_length)
        except Exception:
            part_path.unlink(missing_ok=True)
            raise

    def request_playback(self, target_name: str) -> None:
        target_path = self.media_dir / target_name
        if not target_path.is_file():
            raise ValueError(f"Media file not found: {target_name}")
        with self.playback_lock:
            self.playback_stop.set()
            self.playback_request = target_path

    def take_playback_request(self) -> Path | None:
        with self.playback_lock:
            playback_path = self.playback_request
            self.playback_request = None
            if playback_path is not None:
                self.playback_stop.clear()
            return playback_path


class RawVidPlayer:
    def __init__(self, path: Path, width: int, height: int, fps: int) -> None:
        self.path = path
        self.width = width
        self.height = height
        self.fps = fps
        self.frame_bytes = width * height * 2
        self.file_size = path.stat().st_size
        self.frame_count, remainder = divmod(self.file_size, self.frame_bytes)
        if remainder:
            raise ValueError(
                f"{path} has {self.file_size} bytes, which is not aligned to "
                f"{width}x{height} RGB565LE frames ({self.frame_bytes} bytes each)"
            )
        if not self.frame_count:
            raise ValueError(f"{path} does not contain any complete RGB565LE frames")

    def run(self, presenter: FramebufferPresenter, stop_event: threading.Event | None = None) -> None:
        LOGGER.info(
            "Playing VID path=%s size=%sx%s fps=%s frames=%s loop=True",
            self.path,
            self.width,
            self.height,
            self.fps,
            self.frame_count,
        )
        frame_period = 1 / self.fps
        frame_count = 0
        measured_from = time.monotonic()
        while stop_event is None or not stop_event.is_set():
            with self.path.open("rb") as vid_file:
                for _frame_index in range(self.frame_count):
                    if stop_event is not None and stop_event.is_set():
                        LOGGER.info("VID playback stopped")
                        return
                    started = time.monotonic()
                    frame = vid_file.read(self.frame_bytes)
                    if len(frame) != self.frame_bytes:
                        raise OSError(f"Short read while playing {self.path}")
                    presenter.present_rgb565(frame, self.width, self.height)
                    frame_count += 1
                    now = time.monotonic()
                    if now - measured_from >= 5:
                        LOGGER.info("VID display fps=%.1f", frame_count / (now - measured_from))
                        frame_count = 0
                        measured_from = now
                    remaining = frame_period - (time.monotonic() - started)
                    if remaining > 0:
                        time.sleep(remaining)


class FfmpegMp4Player:
    def __init__(self, path: Path, width: int, height: int, fps: int) -> None:
        self.path = path
        self.width = width
        self.height = height
        self.fps = fps
        self.frame_bytes = width * height * 2
        self.ffmpeg = self.resolve_ffmpeg()

    @staticmethod
    def resolve_ffmpeg() -> str:
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise RuntimeError("ffmpeg is required for MP4 playback")
        return ffmpeg

    def run(self, presenter: FramebufferPresenter, stop_event: threading.Event | None = None) -> None:
        video_filter = (
            f"fps={self.fps},"
            f"scale={self.width}:{self.height}:force_original_aspect_ratio=increase,"
            f"crop={self.width}:{self.height},setsar=1"
        )
        command = [
            self.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-stream_loop",
            "-1",
            "-i",
            str(self.path),
            "-an",
            "-vf",
            video_filter,
            "-pix_fmt",
            "rgb565le",
            "-f",
            "rawvideo",
            "-",
        ]
        LOGGER.info(
            "Playing MP4 path=%s size=%sx%s fps=%s loop=True ffmpeg=%s",
            self.path,
            self.width,
            self.height,
            self.fps,
            self.ffmpeg,
        )
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=self.frame_bytes * 2,
        )
        assert process.stdout is not None
        frame_period = 1 / self.fps
        frame_count = 0
        measured_from = time.monotonic()
        try:
            while stop_event is None or not stop_event.is_set():
                started = time.monotonic()
                frame = process.stdout.read(self.frame_bytes)
                if len(frame) != self.frame_bytes:
                    stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
                    raise RuntimeError(f"ffmpeg ended before a complete frame was available: {stderr.strip()}")
                presenter.present_rgb565(frame, self.width, self.height)
                frame_count += 1
                now = time.monotonic()
                if now - measured_from >= 5:
                    LOGGER.info("MP4 display fps=%.1f", frame_count / (now - measured_from))
                    frame_count = 0
                    measured_from = now
                remaining = frame_period - (time.monotonic() - started)
                if remaining > 0:
                    time.sleep(remaining)
        finally:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            LOGGER.info("MP4 playback stopped")


def parse_size(value: str) -> tuple[int, int]:
    try:
        width, height = (int(part) for part in value.lower().split("x", maxsplit=1))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("size must use WIDTHxHEIGHT, for example 240x240") from exc
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("width and height must both be positive")
    return width, height


def get_lan_ip_address() -> str:
    override = os.environ.get("DRAFTHUB_IP_ADDRESS", "").strip()
    if override:
        return override

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            address = probe.getsockname()[0]
            if address and not address.startswith("127."):
                return address
    except OSError:
        pass

    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET, socket.SOCK_DGRAM):
            address = info[4][0]
            if address and not address.startswith("127."):
                return address
    except OSError:
        pass

    return ""


def get_build_label() -> str:
    version = os.environ.get("DRAFTHUB_VERSION", __version__).strip() or __version__
    if not version.startswith("v"):
        version = f"v{version}"

    commit = os.environ.get("DRAFTHUB_BUILD", "").strip()
    if not commit:
        repo_root = Path(__file__).resolve().parents[2]
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
            )
            commit = result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            commit = ""

    return f"{version} {commit}" if commit else version


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DraftHub touchscreen application")
    parser.add_argument("--windowed", action="store_true", help="Run in a 480x480 desktop window")
    parser.add_argument("--play-vid", type=Path, help="Loop a raw RGB565LE .vid file for playback testing")
    parser.add_argument("--play-mp4", type=Path, help="Loop an MP4 file through ffmpeg for playback testing")
    parser.add_argument("--vid-size", type=parse_size, default=(480, 480), help="VID frame size (default: 480x480)")
    parser.add_argument("--vid-fps", type=int, default=30, help="VID playback rate (default: 30)")
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args()
    if args.play_vid:
        if args.vid_fps <= 0:
            raise ValueError("--vid-fps must be positive")
        pygame.font.init()
        presenter = FramebufferPresenter(FRAMEBUFFER_PATH, FRAMEBUFFER_SYSFS_PATH)
        try:
            RawVidPlayer(args.play_vid, *args.vid_size, args.vid_fps).run(presenter)
        except KeyboardInterrupt:
            LOGGER.info("VID playback stopped")
        finally:
            presenter.close()
        return 0
    if args.play_mp4:
        if args.vid_fps <= 0:
            raise ValueError("--vid-fps must be positive")
        pygame.font.init()
        presenter = FramebufferPresenter(FRAMEBUFFER_PATH, FRAMEBUFFER_SYSFS_PATH)
        try:
            FfmpegMp4Player(args.play_mp4, *args.vid_size, args.vid_fps).run(presenter)
        except KeyboardInterrupt:
            LOGGER.info("MP4 playback stopped")
        finally:
            presenter.close()
        return 0
    DraftHubApp(windowed=args.windowed).run()
    return 0
