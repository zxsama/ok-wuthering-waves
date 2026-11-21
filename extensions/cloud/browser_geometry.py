"""Visible cloud-player coordinates in Playwright's viewport CSS pixels."""

from dataclasses import dataclass
from typing import Any


# The Welink player uses a video for WebRTC and an alternative canvas for
# enhanced rendering. Its other canvases and the landing-page videos are UI.
_GAME_RECT = """() => {
    const viewport = [0, 0, innerWidth, innerHeight];
    for (const selector of ['canvas#sr-canvas', '#gmsdk-video-element', '.gmsdk-video-player']) {
        const element = document.querySelector(selector);
        if (!element || !element.checkVisibility({opacityProperty: true, visibilityProperty: true})) continue;
        const box = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        let {left, top, width, height} = box;
        let nativeW = element.videoWidth || element.width;
        let nativeH = element.videoHeight || element.height;
        if (nativeW && nativeH && ['contain', 'scale-down'].includes(style.objectFit)) {
            // The SDK can turn a portrait-encoded stream through 90 degrees.
            const transform = new DOMMatrixReadOnly(style.transform === 'none' ? '' : style.transform);
            if (Math.abs(transform.b) > Math.abs(transform.a)) [nativeW, nativeH] = [nativeH, nativeW];
            const scale = Math.min(width / nativeW, height / nativeH, style.objectFit === 'scale-down' ? 1 : Infinity);
            // Welink centers the contained stream within its player element.
            left += (width - nativeW * scale) / 2;
            top += (height - nativeH * scale) / 2;
            width = nativeW * scale;
            height = nativeH * scale;
        }
        const x = Math.max(0, Math.round(left));
        const y = Math.max(0, Math.round(top));
        const right = Math.min(innerWidth, Math.round(left + width));
        const bottom = Math.min(innerHeight, Math.round(top + height));
        if (right > x && bottom > y) return [x, y, right - x, bottom - y];
    }
    return viewport;
}"""


@dataclass(frozen=True, slots=True)
class BrowserRect:
    x: int
    y: int
    width: int
    height: int

    @classmethod
    def from_page(cls, page: Any) -> "BrowserRect":
        return cls(*page.evaluate(_GAME_RECT))

    def map_point(self, x: float, y: float, frame_size: tuple[int, int]) -> tuple[int, int]:
        width, height = frame_size
        return (
            self.x + min(max(round(x * self.width / width), 0), self.width - 1),
            self.y + min(max(round(y * self.height / height), 0), self.height - 1),
        )
