#include "Overlay.h"

#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>

#include <algorithm>
#include <cmath>

namespace rl {
namespace {

constexpr int kW = 340, kH = 78, kMargin = 24, kRadius = 12;
constexpr double kInMs = 320, kHoldMs = 1900, kOutMs = 260;

double NowMs() {
    static LARGE_INTEGER f = [] { LARGE_INTEGER x; QueryPerformanceFrequency(&x); return x; }();
    LARGE_INTEGER c;
    QueryPerformanceCounter(&c);
    return c.QuadPart * 1000.0 / f.QuadPart;
}

std::wstring Widen(const std::string& s) {
    if (s.empty()) return {};
    int n = MultiByteToWideChar(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), nullptr, 0);
    std::wstring w(n, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), w.data(), n);
    return w;
}

double EaseOutBack(double t) {
    const double c1 = 1.70158, c3 = c1 + 1;
    return 1 + c3 * std::pow(t - 1, 3) + c1 * std::pow(t - 1, 2);
}
double EaseInCubic(double t) { return t * t * t; }

// Signed distance to a rounded rect, for anti-aliased corners.
float RoundedCoverage(int x, int y, int w, int h, float r) {
    const float px = x + 0.5f, py = y + 0.5f;
    const float cx = std::clamp(px, r, w - r), cy = std::clamp(py, r, h - r);
    const float d = std::hypot(px - cx, py - cy) - r;
    return std::clamp(0.5f - d, 0.f, 1.f);
}

}  // namespace

bool Overlay::Create(void* hInstance) {
    WNDCLASSEXW wc{sizeof(wc)};
    wc.lpfnWndProc = DefWindowProcW;
    wc.hInstance = static_cast<HINSTANCE>(hInstance);
    wc.lpszClassName = L"RecoilLedgerToast";
    RegisterClassExW(&wc);
    HWND h = CreateWindowExW(WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
                             wc.lpszClassName, L"", WS_POPUP, kMargin, kMargin, kW, kH, nullptr, nullptr, wc.hInstance, nullptr);
    hwnd_ = h;
    return h != nullptr;
}

void Overlay::Destroy() {
    if (hwnd_) DestroyWindow(static_cast<HWND>(hwnd_));
    hwnd_ = nullptr;
}

void Overlay::Show(const std::string& a, const std::string& b, unsigned accentRgb) {
    if (!hwnd_) return;
    const double now = NowMs();
    const bool visible = phase_ != Phase::Hidden;
    l1_ = a;
    l2_ = b;
    accent_ = accentRgb;
    if (visible && phase_ != Phase::Out) {
        // Already on screen: swap content in place with an accent pulse, no re-slide.
        phase_ = Phase::Hold;
        phaseStart_ = now;
        pulseAt_ = now;
    } else {
        phase_ = Phase::In;
        phaseStart_ = now;
        pulseAt_ = -1e9;
        ShowWindow(static_cast<HWND>(hwnd_), SW_SHOWNOACTIVATE);
    }
    Tick();
}

void Overlay::Tick() {
    if (!hwnd_ || phase_ == Phase::Hidden) return;
    const double now = NowMs();
    double t = now - phaseStart_;
    float slide = 1.f, alpha = 1.f, life = 1.f;
    switch (phase_) {
        case Phase::In:
            if (t >= kInMs) { phase_ = Phase::Hold; phaseStart_ = now; t = 0; }
            else {
                const double k = t / kInMs;
                slide = static_cast<float>(EaseOutBack(k));
                alpha = static_cast<float>(std::min(1.0, k * 2.2));
            }
            break;
        case Phase::Hold:
            if (t >= kHoldMs) { phase_ = Phase::Out; phaseStart_ = now; t = 0; }
            else life = static_cast<float>(1.0 - t / kHoldMs);
            break;
        case Phase::Out:
            if (t >= kOutMs) {
                phase_ = Phase::Hidden;
                ShowWindow(static_cast<HWND>(hwnd_), SW_HIDE);
                return;
            }
            {
                const double k = EaseInCubic(t / kOutMs);
                slide = static_cast<float>(1.0 - k);
                alpha = static_cast<float>(1.0 - k);
                life = 0.f;
            }
            break;
        default: break;
    }
    if (phase_ == Phase::Hold) life = static_cast<float>(1.0 - (now - phaseStart_) / kHoldMs);
    const float pulse = static_cast<float>(std::max(0.0, 1.0 - (now - pulseAt_) / 450.0));
    Paint(slide, alpha, std::clamp(life, 0.f, 1.f), pulse);
}

void Overlay::Paint(float slide, float alpha, float life, float pulse) {
    HWND h = static_cast<HWND>(hwnd_);
    HDC screen = GetDC(nullptr);
    HDC dc = CreateCompatibleDC(screen);
    BITMAPINFO bi{};
    bi.bmiHeader = {sizeof(BITMAPINFOHEADER), kW, -kH, 1, 32, BI_RGB};
    void* bits = nullptr;
    HBITMAP bmp = CreateDIBSection(dc, &bi, DIB_RGB_COLORS, &bits, nullptr, 0);
    HGDIOBJ old = SelectObject(dc, bmp);
    auto* px = static_cast<unsigned*>(bits);

    const unsigned ar = (accent_ >> 16) & 0xFF, ag = (accent_ >> 8) & 0xFF, ab = accent_ & 0xFF;
    auto mix = [](unsigned a, unsigned b, float t) { return static_cast<unsigned>(a + (static_cast<float>(b) - a) * t); };

    // Background with a faint accent wash on the left; brightens on pulse.
    for (int y = 0; y < kH; ++y)
        for (int x = 0; x < kW; ++x) {
            const float wash = std::max(0.f, 1.f - x / 160.f) * (0.07f + 0.18f * pulse);
            const unsigned r = mix(0x16, ar, wash), g = mix(0x18, ag, wash), b = mix(0x1D, ab, wash);
            px[y * kW + x] = (r << 16) | (g << 8) | b;
        }

    SetBkMode(dc, TRANSPARENT);
    HFONT f1 = CreateFontW(21, 0, 0, 0, FW_SEMIBOLD, 0, 0, 0, DEFAULT_CHARSET, 0, 0, CLEARTYPE_QUALITY, 0, L"Segoe UI");
    HFONT f2 = CreateFontW(15, 0, 0, 0, FW_NORMAL, 0, 0, 0, DEFAULT_CHARSET, 0, 0, CLEARTYPE_QUALITY, 0, L"Consolas");
    HGDIOBJ oldFont = SelectObject(dc, f1);
    SetTextColor(dc, RGB(0xEC, 0xEE, 0xF2));
    const std::wstring w1 = Widen(l1_), w2 = Widen(l2_);
    RECT r1{58, 13, kW - 14, 40}, r2{58, 40, kW - 14, 60};
    DrawTextW(dc, w1.c_str(), static_cast<int>(w1.size()), &r1, DT_SINGLELINE | DT_END_ELLIPSIS | DT_NOPREFIX);
    SelectObject(dc, f2);
    SetTextColor(dc, RGB(0x9A, 0xA1, 0xAD));
    DrawTextW(dc, w2.c_str(), static_cast<int>(w2.size()), &r2, DT_SINGLELINE | DT_END_ELLIPSIS | DT_NOPREFIX);
    SelectObject(dc, oldFont);
    DeleteObject(f1);
    DeleteObject(f2);
    GdiFlush();

    // Accent glyph: ring + dot, mirrors the app mark.
    const float gx = 32, gy = kH * 0.5f - 3, gr = 12;
    for (int y = 0; y < kH; ++y)
        for (int x = 0; x < 54; ++x) {
            const float d = std::hypot(x + 0.5f - gx, y + 0.5f - gy);
            const float ring = std::clamp(1.5f - std::fabs(d - gr), 0.f, 1.f);
            const float dot = std::clamp(4.5f - d, 0.f, 1.f) * (0.7f + 0.3f * pulse);
            const float a = std::max(ring, dot);
            if (a <= 0) continue;
            unsigned& p = px[y * kW + x];
            const unsigned r = mix((p >> 16) & 0xFF, ar, a), g = mix((p >> 8) & 0xFF, ag, a), b = mix(p & 0xFF, ab, a);
            p = (r << 16) | (g << 8) | b;
        }

    // Life bar drains along the bottom.
    const int barW = static_cast<int>((kW - 2 * kRadius) * life);
    for (int y = kH - 4; y < kH - 2; ++y)
        for (int x = kRadius; x < kRadius + barW; ++x) px[y * kW + x] = (ar << 16) | (ag << 8) | ab;

    // Alpha: rounded mask × fade, premultiplied for ULW_ALPHA.
    for (int y = 0; y < kH; ++y)
        for (int x = 0; x < kW; ++x) {
            const float a = RoundedCoverage(x, y, kW, kH, static_cast<float>(kRadius)) * 0.96f;
            const unsigned A = static_cast<unsigned>(a * 255.f + 0.5f);
            unsigned& p = px[y * kW + x];
            const unsigned r = ((p >> 16) & 0xFF) * A / 255, g = ((p >> 8) & 0xFF) * A / 255, b = (p & 0xFF) * A / 255;
            p = (A << 24) | (r << 16) | (g << 8) | b;
        }

    const int offX = static_cast<int>((1.f - slide) * -(kW + kMargin));
    POINT src{0, 0}, dst{kMargin + offX, kMargin};
    SIZE sz{kW, kH};
    BLENDFUNCTION bf{AC_SRC_OVER, 0, static_cast<BYTE>(std::clamp(alpha, 0.f, 1.f) * 255.f), AC_SRC_ALPHA};
    UpdateLayeredWindow(h, screen, &dst, &sz, dc, &src, 0, &bf, ULW_ALPHA);

    SelectObject(dc, old);
    DeleteObject(bmp);
    DeleteDC(dc);
    ReleaseDC(nullptr, screen);
}

}  // namespace rl
