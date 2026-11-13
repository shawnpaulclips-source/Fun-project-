#include "Overlay.h"

#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>

#include <algorithm>

namespace rl {
namespace {
constexpr int kW = 300, kH = 64;
double NowMs() { return GetTickCount64(); }
std::wstring Widen(const std::string& s) {
    if (s.empty()) return {};
    int n = MultiByteToWideChar(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), nullptr, 0);
    std::wstring w(n, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), w.data(), n);
    return w;
}
}  // namespace

bool Overlay::Create(void* hInstance) {
    WNDCLASSEXW wc{sizeof(wc)};
    wc.lpfnWndProc = DefWindowProcW;
    wc.hInstance = static_cast<HINSTANCE>(hInstance);
    wc.lpszClassName = L"RecoilLedgerToast";
    RegisterClassExW(&wc);
    HWND h = CreateWindowExW(WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
                             wc.lpszClassName, L"", WS_POPUP, 24, 24, kW, kH, nullptr, nullptr, wc.hInstance, nullptr);
    hwnd_ = h;
    return h != nullptr;
}

void Overlay::Destroy() {
    if (hwnd_) DestroyWindow(static_cast<HWND>(hwnd_));
    hwnd_ = nullptr;
}

void Overlay::Show(const std::string& a, const std::string& b, unsigned accentRgb) {
    l1_ = a;
    l2_ = b;
    accent_ = accentRgb;
    shownAt_ = NowMs();
    alpha_ = 235;
    Paint();
    ShowWindow(static_cast<HWND>(hwnd_), SW_SHOWNOACTIVATE);
}

void Overlay::Tick() {
    if (!hwnd_ || alpha_ <= 0) return;
    const double age = NowMs() - shownAt_;
    int target = age < 1600 ? 235 : static_cast<int>(235 * std::max(0.0, 1.0 - (age - 1600) / 400.0));
    if (target != alpha_) {
        alpha_ = target;
        if (alpha_ <= 0) { ShowWindow(static_cast<HWND>(hwnd_), SW_HIDE); return; }
        Paint();
    }
}

void Overlay::Paint() {
    HWND h = static_cast<HWND>(hwnd_);
    HDC screen = GetDC(nullptr);
    HDC dc = CreateCompatibleDC(screen);
    BITMAPINFO bi{};
    bi.bmiHeader = {sizeof(BITMAPINFOHEADER), kW, -kH, 1, 32, BI_RGB};
    void* bits = nullptr;
    HBITMAP bmp = CreateDIBSection(dc, &bi, DIB_RGB_COLORS, &bits, nullptr, 0);
    HGDIOBJ old = SelectObject(dc, bmp);

    RECT r{0, 0, kW, kH};
    HBRUSH bg = CreateSolidBrush(RGB(0x14, 0x16, 0x1A));
    FillRect(dc, &r, bg);
    DeleteObject(bg);
    RECT bar{0, 0, 4, kH};
    HBRUSH ac = CreateSolidBrush(RGB((accent_ >> 16) & 0xFF, (accent_ >> 8) & 0xFF, accent_ & 0xFF));
    FillRect(dc, &bar, ac);
    DeleteObject(ac);

    SetBkMode(dc, TRANSPARENT);
    HFONT f1 = CreateFontW(20, 0, 0, 0, FW_SEMIBOLD, 0, 0, 0, DEFAULT_CHARSET, 0, 0, CLEARTYPE_QUALITY, 0, L"Segoe UI");
    HFONT f2 = CreateFontW(15, 0, 0, 0, FW_NORMAL, 0, 0, 0, DEFAULT_CHARSET, 0, 0, CLEARTYPE_QUALITY, 0, L"Consolas");
    SelectObject(dc, f1);
    SetTextColor(dc, RGB(0xEC, 0xEE, 0xF2));
    std::wstring w1 = Widen(l1_), w2 = Widen(l2_);
    TextOutW(dc, 16, 10, w1.c_str(), static_cast<int>(w1.size()));
    SelectObject(dc, f2);
    SetTextColor(dc, RGB(0x9A, 0xA1, 0xAD));
    TextOutW(dc, 16, 36, w2.c_str(), static_cast<int>(w2.size()));
    DeleteObject(f1);
    DeleteObject(f2);

    // GDI leaves alpha at 0; force opaque so per-pixel alpha + SourceConstantAlpha behave.
    auto* px = static_cast<unsigned*>(bits);
    for (int i = 0; i < kW * kH; ++i) px[i] |= 0xFF000000u;

    POINT src{0, 0}, dst{24, 24};
    SIZE sz{kW, kH};
    BLENDFUNCTION bf{AC_SRC_OVER, 0, static_cast<BYTE>(alpha_), AC_SRC_ALPHA};
    UpdateLayeredWindow(h, screen, &dst, &sz, dc, &src, 0, &bf, ULW_ALPHA);

    SelectObject(dc, old);
    DeleteObject(bmp);
    DeleteDC(dc);
    ReleaseDC(nullptr, screen);
}

}  // namespace rl
