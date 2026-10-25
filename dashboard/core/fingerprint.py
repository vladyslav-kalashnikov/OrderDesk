"""Вбудований антидетект-профіль для кожного акаунта (альтернатива AdsPower).

Генерує та зберігає унікальний апаратний відбиток (Fingerprint) для кожного акаунта:
- Захист від витоку реального IP через WebRTC (блокування non-proxied UDP + підміна IP у SDP/ICE на IP проксі).
- Унікальний відбиток WebGL (Vendor / Renderer відповідно до ОС).
- Унікальний детермінований шум Canvas 2D, AudioContext та DOMRect (ClientRects).
- Індивідуальні параметри екрана, вікна, кількості ядер процесора (hardwareConcurrency) та пам'яті (deviceMemory).
- Маскування ознак автоматизації (navigator.webdriver, Chrome runtime, локаль uk-UA, часовий пояс Europe/Kyiv).
"""
from __future__ import annotations

import hashlib
import json
import platform
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import paths

# Аргументи запуску Chromium для блокування витоків WebRTC та приховування автоматизації
CHROME_STEALTH_ARGS: list[str] = [
    "--proxy-bypass-list=<-loopback>",
    "--disable-blink-features=AutomationControlled",
    "--webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--enforce-webrtc-ip-permission-check",
    "--force-webrtc-ip-handling-policy",
]

# Реалістичні конфігурації екранів (viewport_w, viewport_h, screen_w, screen_h, scale)
_SCREEN_PROFILES: list[tuple[int, int, int, int, int]] = [
    (1440, 810, 1440, 900, 2),
    (1512, 850, 1512, 982, 2),
    (1536, 864, 1536, 864, 1),
    (1600, 900, 1600, 900, 1),
    (1680, 945, 1680, 1050, 2),
    (1728, 972, 1728, 1117, 2),
    (1920, 1080, 1920, 1080, 1),
    (1920, 1080, 2560, 1440, 1),
]

# Реалістичні GPU під macOS та Windows/Linux, щоб не було конфлікту з системним User-Agent
_MAC_GPUS: list[tuple[str, str]] = [
    ("Google Inc. (Apple)", "ANGLE (Apple, ANGLE Metal Renderer: Apple M1, Unspecified Version)"),
    ("Google Inc. (Apple)", "ANGLE (Apple, ANGLE Metal Renderer: Apple M1 Pro, Unspecified Version)"),
    ("Google Inc. (Apple)", "ANGLE (Apple, ANGLE Metal Renderer: Apple M1 Max, Unspecified Version)"),
    ("Google Inc. (Apple)", "ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)"),
    ("Google Inc. (Apple)", "ANGLE (Apple, ANGLE Metal Renderer: Apple M2 Pro, Unspecified Version)"),
    ("Google Inc. (Apple)", "ANGLE (Apple, ANGLE Metal Renderer: Apple M3, Unspecified Version)"),
    ("Google Inc. (Apple)", "ANGLE (Apple, ANGLE Metal Renderer: Apple M3 Pro, Unspecified Version)"),
    ("Google Inc. (Intel Inc.)", "ANGLE (Intel Inc., Intel(R) Iris(TM) Plus Graphics 655, OpenGL 4.1)"),
    ("Google Inc. (ATI Technologies Inc.)", "ANGLE (ATI Technologies Inc., AMD Radeon Pro 5500M OpenGL Engine, OpenGL 4.1)"),
]

_WIN_GPUS: list[tuple[str, str]] = [
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 4060 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 SUPER Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3070 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon RX 6600 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)"),
]

_CPU_CORES: list[int] = [4, 8, 8, 10, 12, 16]
_MEM_SIZES: list[int] = [8, 8, 16, 16, 32]


@dataclass
class AccountFingerprint:
    account_id: int
    viewport_width: int
    viewport_height: int
    screen_width: int
    screen_height: int
    avail_width: int
    avail_height: int
    device_scale_factor: int
    hardware_concurrency: int
    device_memory: int
    webgl_vendor: str
    webgl_renderer: str
    canvas_seed: int
    audio_seed: float
    rects_seed: float
    locale: str = "uk-UA"
    timezone_id: str = "Europe/Kyiv"
    languages: list[str] = field(default_factory=lambda: ["uk-UA", "uk", "en-US", "en"])

    def to_context_kwargs(self) -> dict[str, Any]:
        """Параметри для browser.new_context(**kwargs)."""
        return {
            "viewport": {"width": self.viewport_width, "height": self.viewport_height},
            "screen": {"width": self.screen_width, "height": self.screen_height},
            "locale": self.locale,
            "timezone_id": self.timezone_id,
        }


def fingerprint_path(account_id: int) -> Path:
    return paths.sessions_dir() / f"fingerprint_{account_id}.json"


def delete_fingerprint(account_id: int) -> None:
    """Видаляє збережений відбиток акаунта (при скиданні сесії або зміні проксі)."""
    try:
        fingerprint_path(account_id).unlink(missing_ok=True)
    except Exception:
        pass


def generate_fingerprint(account_id: int, seed_extra: str = "") -> AccountFingerprint:
    """Генерує реалістичний апаратний профіль для акаунта."""
    raw = f"orderdesk-fp-{account_id}-{seed_extra}".encode("utf-8")
    seed_int = int(hashlib.sha256(raw).hexdigest()[:16], 16)
    rng = random.Random(seed_int)

    vw, vh, sw, sh, scale = rng.choice(_SCREEN_PROFILES)
    # Невеликий індивідуальний відступ панелі завдань / док-панелі
    dock_offset = rng.choice([25, 30, 40, 48, 60])
    avail_w = sw
    avail_h = max(vh, sh - dock_offset)

    gpus = _MAC_GPUS if platform.system() == "Darwin" else _WIN_GPUS
    vendor, renderer = rng.choice(gpus)

    cores = rng.choice(_CPU_CORES)
    memory = rng.choice(_MEM_SIZES)

    canvas_seed = rng.randint(10_000, 999_999)
    audio_seed = round(rng.uniform(1.1e-7, 9.8e-7), 10)
    rects_seed = round(rng.uniform(1.1e-6, 8.9e-6), 9)

    return AccountFingerprint(
        account_id=account_id,
        viewport_width=vw,
        viewport_height=vh,
        screen_width=sw,
        screen_height=sh,
        avail_width=avail_w,
        avail_height=avail_h,
        device_scale_factor=scale,
        hardware_concurrency=cores,
        device_memory=memory,
        webgl_vendor=vendor,
        webgl_renderer=renderer,
        canvas_seed=canvas_seed,
        audio_seed=audio_seed,
        rects_seed=rects_seed,
    )


def load_or_create_fingerprint(account_id: int, seed_extra: str = "") -> AccountFingerprint:
    """Завантажує збережений відбиток акаунта з диска або створює новий."""
    path = fingerprint_path(account_id)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return AccountFingerprint(**data)
        except Exception:
            pass

    fp = generate_fingerprint(account_id, seed_extra=seed_extra)
    try:
        paths.ensure_dirs()
        path.write_text(json.dumps(asdict(fp), ensure_ascii=False, indent=2), encoding="utf-8")
        path.chmod(0o600)
    except Exception:
        pass
    return fp


def build_init_script(fp: AccountFingerprint, proxy_ip: str | None = None) -> str:
    """Формує JS-скрипт, який виконується до завантаження будь-якого сайту в контексті акаунта."""
    cfg_json = json.dumps({
        "hw": fp.hardware_concurrency,
        "mem": fp.device_memory,
        "sw": fp.screen_width,
        "sh": fp.screen_height,
        "aw": fp.avail_width,
        "ah": fp.avail_height,
        "dpr": fp.device_scale_factor,
        "vendor": fp.webgl_vendor,
        "renderer": fp.webgl_renderer,
        "canvasSeed": fp.canvas_seed,
        "audioSeed": fp.audio_seed,
        "rectsSeed": fp.rects_seed,
        "languages": fp.languages,
        "locale": fp.locale,
        "proxyIp": proxy_ip or "",
    })

    return f"""
(() => {{
    const CFG = {cfg_json};

    // 1. Приховування webdriver
    try {{
        Object.defineProperty(Navigator.prototype, 'webdriver', {{
            get: () => false,
            configurable: true,
            enumerable: true
        }});
    }} catch (e) {{}}

    // 2. Апаратні характеристики (CPU, RAM, мови)
    const defineGetter = (obj, prop, val) => {{
        try {{
            Object.defineProperty(obj, prop, {{
                get: () => val,
                configurable: true,
                enumerable: true
            }});
        }} catch (e) {{}}
    }};

    defineGetter(Navigator.prototype, 'hardwareConcurrency', CFG.hw);
    defineGetter(Navigator.prototype, 'deviceMemory', CFG.mem);
    defineGetter(Navigator.prototype, 'language', CFG.locale);
    defineGetter(Navigator.prototype, 'languages', Object.freeze([...CFG.languages]));

    // 3. Розміри екрана
    if (typeof Screen !== 'undefined') {{
        defineGetter(Screen.prototype, 'width', CFG.sw);
        defineGetter(Screen.prototype, 'height', CFG.sh);
        defineGetter(Screen.prototype, 'availWidth', CFG.aw);
        defineGetter(Screen.prototype, 'availHeight', CFG.ah);
        defineGetter(Screen.prototype, 'colorDepth', 24);
        defineGetter(Screen.prototype, 'pixelDepth', 24);
    }}

    // 4. Підміна WebGL Vendor та Renderer
    const patchWebGL = (proto) => {{
        if (!proto || !proto.getParameter) return;
        const origGetParameter = proto.getParameter;
        Object.defineProperty(proto, 'getParameter', {{
            value: function(param) {{
                if (param === 37445) return CFG.vendor;   // UNMASKED_VENDOR_WEBGL
                if (param === 37446) return CFG.renderer; // UNMASKED_RENDERER_WEBGL
                return origGetParameter.apply(this, arguments);
            }},
            configurable: true,
            writable: true
        }});
    }};
    if (typeof WebGLRenderingContext !== 'undefined') patchWebGL(WebGLRenderingContext.prototype);
    if (typeof WebGL2RenderingContext !== 'undefined') patchWebGL(WebGL2RenderingContext.prototype);

    // 5. Унікальний детермінований шум Canvas 2D (як у AdsPower)
    if (typeof CanvasRenderingContext2D !== 'undefined' && typeof HTMLCanvasElement !== 'undefined') {{
        const origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
        const origPutImageData = CanvasRenderingContext2D.prototype.putImageData;
        const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
        const origToBlob = HTMLCanvasElement.prototype.toBlob;

        const applyNoise = (imgData) => {{
            try {{
                const d = imgData.data;
                const len = d.length;
                if (len >= 64) {{
                    const step = (CFG.canvasSeed % 17) + 13;
                    const delta = (CFG.canvasSeed % 2 === 0) ? 1 : -1;
                    for (let i = 0; i < Math.min(len, 400); i += step * 4) {{
                        const v = d[i];
                        d[i] = (v > 0 && v < 255) ? (v + delta) : (v === 0 ? 1 : 254);
                    }}
                }}
            }} catch (e) {{}}
            return imgData;
        }};

        const cloneWithNoise = (canvas) => {{
            try {{
                if (!canvas || !canvas.width || !canvas.height) return canvas;
                const clone = document.createElement('canvas');
                clone.width = canvas.width;
                clone.height = canvas.height;
                const ctx = clone.getContext('2d');
                if (!ctx) return canvas;
                ctx.drawImage(canvas, 0, 0);
                const imgData = origGetImageData.call(ctx, 0, 0, clone.width, clone.height);
                applyNoise(imgData);
                origPutImageData.call(ctx, imgData, 0, 0);
                return clone;
            }} catch (e) {{
                return canvas;
            }}
        }};

        Object.defineProperty(CanvasRenderingContext2D.prototype, 'getImageData', {{
            value: function() {{
                const res = origGetImageData.apply(this, arguments);
                return applyNoise(res);
            }},
            configurable: true,
            writable: true
        }});

        Object.defineProperty(HTMLCanvasElement.prototype, 'toDataURL', {{
            value: function() {{
                const target = cloneWithNoise(this);
                return origToDataURL.apply(target, arguments);
            }},
            configurable: true,
            writable: true
        }});

        if (origToBlob) {{
            Object.defineProperty(HTMLCanvasElement.prototype, 'toBlob', {{
                value: function() {{
                    const target = cloneWithNoise(this);
                    return origToBlob.apply(target, arguments);
                }},
                configurable: true,
                writable: true
            }});
        }}
    }}

    // 6. Унікальний мікро-шум AudioContext
    if (typeof AudioBuffer !== 'undefined' && AudioBuffer.prototype.getChannelData) {{
        const origGetChannelData = AudioBuffer.prototype.getChannelData;
        Object.defineProperty(AudioBuffer.prototype.getChannelData, '__patched', {{ value: true }});
        AudioBuffer.prototype.getChannelData = function() {{
            const data = origGetChannelData.apply(this, arguments);
            try {{
                if (data && data.length > 0 && !data.__fpNoiseApplied) {{
                    Object.defineProperty(data, '__fpNoiseApplied', {{ value: true }});
                    const step = Math.max(1, Math.floor(data.length / 32));
                    for (let i = 0; i < data.length; i += step) {{
                        data[i] = data[i] + CFG.audioSeed * ((i % 2 === 0) ? 1 : -1);
                    }}
                }}
            }} catch (e) {{}}
            return data;
        }};
    }}

    // 7. Мікро-зміщення ClientRects
    if (typeof Element !== 'undefined' && Element.prototype.getBoundingClientRect) {{
        const origGetBoundingClientRect = Element.prototype.getBoundingClientRect;
        Element.prototype.getBoundingClientRect = function() {{
            const rect = origGetBoundingClientRect.apply(this, arguments);
            try {{
                if (rect && (rect.width > 0 || rect.height > 0) && typeof DOMRect !== 'undefined') {{
                    const n = CFG.rectsSeed;
                    return new DOMRect(rect.x + n, rect.y + n, rect.width + n, rect.height + n);
                }}
            }} catch (e) {{}}
            return rect;
        }};
    }}

    // 8. Захист WebRTC від витоку локального та реального IP
    const sanitizeSdp = (sdp) => {{
        if (!sdp || typeof sdp !== 'string') return sdp;
        if (CFG.proxyIp) {{
            // Підміняємо IPv4-адреси в c=IN IP4 та a=candidate на IP проксі
            return sdp
                .replace(/(c=IN IP4\\s+)([0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+)/g, '$1' + CFG.proxyIp)
                .replace(/(a=candidate:[^\\r\\n]*?\\s+)([0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+)(\\s+)/g, '$1' + CFG.proxyIp + '$3');
        }}
        // Якщо IP проксі ще невідомий — видаляємо локальні/приватні та srflx кандидати
        return sdp.replace(/a=candidate:.*(192\\.168\\.|10\\.|172\\.(1[6-9]|2[0-9]|3[0-1])\\.|typ srflx).*\\r?\\n/g, '');
    }};

    const wrapRTC = (name) => {{
        const OrigPeerConnection = window[name];
        if (!OrigPeerConnection) return;
        const WrappedRTC = function(config, constraints) {{
            const safeConfig = Object.assign({{}}, config || {{}});
            // Вимикаємо сторонні STUN-сервери, якщо немає потреби, щоб уникнути прямого UDP-витоку
            if (!CFG.proxyIp) {{
                safeConfig.iceServers = [];
            }}
            const pc = new OrigPeerConnection(safeConfig, constraints);
            const origAddEventListener = pc.addEventListener.bind(pc);
            pc.addEventListener = function(type, listener, options) {{
                if (type === 'icecandidate' && typeof listener === 'function') {{
                    const wrappedListener = function(event) {{
                        if (event && event.candidate && event.candidate.candidate) {{
                            if (!CFG.proxyIp && (
                                event.candidate.candidate.includes('typ srflx') ||
                                /192\\.168\\.|10\\.|172\\.(1[6-9]|2[0-9]|3[0-1])\\./.test(event.candidate.candidate)
                            )) {{
                                return;
                            }}
                        }}
                        return listener.apply(this, arguments);
                    }};
                    return origAddEventListener(type, wrappedListener, options);
                }}
                return origAddEventListener(type, listener, options);
            }};
            return pc;
        }};
        WrappedRTC.prototype = OrigPeerConnection.prototype;
        Object.defineProperty(window, name, {{
            value: WrappedRTC,
            configurable: true,
            writable: true
        }});
    }};
    wrapRTC('RTCPeerConnection');
    wrapRTC('webkitRTCPeerConnection');
}})();
"""
