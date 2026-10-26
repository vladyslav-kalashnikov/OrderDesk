"""Вбудований антидетект-профіль для кожного акаунта (альтернатива AdsPower).

Генерує та зберігає унікальний апаратний відбиток (Fingerprint) для кожного акаунта:
- Захист від витоку реального IP через WebRTC на рівні рушія Chromium (C++ прапорці без JS-слідів для reCAPTCHA).
- Унікальний відбиток WebGL (Vendor / Renderer відповідно до ОС) з маскуванням під [native code].
- Унікальний детермінований шум Canvas 2D та AudioContext з повним маскуванням Function.prototype.toString.
- Індивідуальні параметри екрана, вікна, кількості ядер процесора (hardwareConcurrency) та пам'яті (deviceMemory).
- Повна сумісність із Google reCAPTCHA v3 та Cloudflare Turnstile.
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

# Аргументи запуску Chromium: блокують витік реального IP через WebRTC на рівні C++ (невидимо для reCAPTCHA)
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
    """Формує стелс JS-скрипт, сумісний із Google reCAPTCHA v3 (усі функції мають вигляд [native code])."""
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
        "languages": fp.languages,
        "locale": fp.locale,
        "proxyIp": proxy_ip or "",
    })

    return f"""
(() => {{
    const CFG = {cfg_json};

    // Маскування під [native code], щоб Google reCAPTCHA v3 не бачила слідів перевизначення функцій
    const nativeStrings = new WeakMap();
    const origToString = Function.prototype.toString;
    const maskAsNative = (fn, name) => {{
        try {{
            if (name) Object.defineProperty(fn, 'name', {{ value: name, configurable: true }});
            nativeStrings.set(fn, `function ${{name || ''}}() {{ [native code] }}`);
        }} catch (e) {{}}
        return fn;
    }};
    const customToString = function toString() {{
        if (nativeStrings.has(this)) return nativeStrings.get(this);
        return origToString.apply(this, arguments);
    }};
    maskAsNative(customToString, 'toString');
    Object.defineProperty(Function.prototype, 'toString', {{
        value: customToString,
        configurable: true,
        writable: true
    }});

    const defineNativeGetter = (proto, prop, val) => {{
        try {{
            const getter = maskAsNative(function() {{ return val; }}, `get ${{prop}}`);
            Object.defineProperty(proto, prop, {{
                get: getter,
                configurable: true,
                enumerable: true
            }});
        }} catch (e) {{}}
    }};

    // 1. Приховування прапорця webdriver
    defineNativeGetter(Navigator.prototype, 'webdriver', false);

    // 2. Апаратні характеристики (CPU, RAM, мови)
    defineNativeGetter(Navigator.prototype, 'hardwareConcurrency', CFG.hw);
    defineNativeGetter(Navigator.prototype, 'deviceMemory', CFG.mem);
    defineNativeGetter(Navigator.prototype, 'language', CFG.locale);
    defineNativeGetter(Navigator.prototype, 'languages', Object.freeze([...CFG.languages]));

    // 3. Розміри екрана
    if (typeof Screen !== 'undefined') {{
        defineNativeGetter(Screen.prototype, 'width', CFG.sw);
        defineNativeGetter(Screen.prototype, 'height', CFG.sh);
        defineNativeGetter(Screen.prototype, 'availWidth', CFG.aw);
        defineNativeGetter(Screen.prototype, 'availHeight', CFG.ah);
        defineNativeGetter(Screen.prototype, 'colorDepth', 24);
        defineNativeGetter(Screen.prototype, 'pixelDepth', 24);
    }}

    // 4. Підміна WebGL Vendor та Renderer (з маскуванням під [native code])
    const patchWebGL = (proto) => {{
        if (!proto || !proto.getParameter) return;
        const origGetParameter = proto.getParameter;
        const wrappedGetParameter = maskAsNative(function getParameter(param) {{
            if (param === 37445) return CFG.vendor;
            if (param === 37446) return CFG.renderer;
            return origGetParameter.apply(this, arguments);
        }}, 'getParameter');
        Object.defineProperty(proto, 'getParameter', {{
            value: wrappedGetParameter,
            configurable: true,
            writable: true
        }});
    }};
    if (typeof WebGLRenderingContext !== 'undefined') patchWebGL(WebGLRenderingContext.prototype);
    if (typeof WebGL2RenderingContext !== 'undefined') patchWebGL(WebGL2RenderingContext.prototype);

    // 5. Унікальний детермінований шум Canvas 2D (з маскуванням під [native code])
    if (typeof CanvasRenderingContext2D !== 'undefined' && typeof HTMLCanvasElement !== 'undefined') {{
        const origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
        const origPutImageData = CanvasRenderingContext2D.prototype.putImageData;
        const origToDataURL = HTMLCanvasElement.prototype.toDataURL;

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

        const wrappedGetImageData = maskAsNative(function getImageData() {{
            const res = origGetImageData.apply(this, arguments);
            return applyNoise(res);
        }}, 'getImageData');

        const wrappedToDataURL = maskAsNative(function toDataURL() {{
            const target = cloneWithNoise(this);
            return origToDataURL.apply(target, arguments);
        }}, 'toDataURL');

        Object.defineProperty(CanvasRenderingContext2D.prototype, 'getImageData', {{
            value: wrappedGetImageData,
            configurable: true,
            writable: true
        }});
        Object.defineProperty(HTMLCanvasElement.prototype, 'toDataURL', {{
            value: wrappedToDataURL,
            configurable: true,
            writable: true
        }});
    }}

    // 6. Унікальний мікро-шум AudioContext (з маскуванням під [native code])
    if (typeof AudioBuffer !== 'undefined' && AudioBuffer.prototype.getChannelData) {{
        const origGetChannelData = AudioBuffer.prototype.getChannelData;
        const wrappedGetChannelData = maskAsNative(function getChannelData() {{
            const data = origGetChannelData.apply(this, arguments);
            try {{
                if (data && data.length > 0) {{
                    const step = Math.max(1, Math.floor(data.length / 32));
                    for (let i = 0; i < data.length; i += step) {{
                        data[i] = data[i] + CFG.audioSeed * ((i % 2 === 0) ? 1 : -1);
                    }}
                }}
            }} catch (e) {{}}
            return data;
        }}, 'getChannelData');
        Object.defineProperty(AudioBuffer.prototype, 'getChannelData', {{
            value: wrappedGetChannelData,
            configurable: true,
            writable: true
        }});
    }}
}})();
"""
