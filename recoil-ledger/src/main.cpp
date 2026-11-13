#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <d3d11.h>
#include <dwmapi.h>
#include <shlobj.h>

#include <filesystem>

#include <imgui.h>
#include <imgui_impl_dx11.h>
#include <imgui_impl_win32.h>

#include "App.h"

#define STB_IMAGE_IMPLEMENTATION
#define STBI_ONLY_PNG
#include <stb_image.h>

#include <cstdio>
#include <cwchar>

extern IMGUI_IMPL_API LRESULT ImGui_ImplWin32_WndProcHandler(HWND, UINT, WPARAM, LPARAM);

namespace {

ID3D11Device* gDevice = nullptr;
ID3D11DeviceContext* gCtx = nullptr;
IDXGISwapChain* gSwap = nullptr;
ID3D11RenderTargetView* gRtv = nullptr;
UINT gResizeW = 0, gResizeH = 0;
rl::App* gApp = nullptr;

void CreateRtv() {
    ID3D11Texture2D* back = nullptr;
    gSwap->GetBuffer(0, IID_PPV_ARGS(&back));
    gDevice->CreateRenderTargetView(back, nullptr, &gRtv);
    back->Release();
}
void ReleaseRtv() { if (gRtv) { gRtv->Release(); gRtv = nullptr; } }

bool CreateDevice(HWND hwnd) {
    DXGI_SWAP_CHAIN_DESC sd{};
    sd.BufferCount = 2;
    sd.BufferDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    sd.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    sd.OutputWindow = hwnd;
    sd.SampleDesc.Count = 1;
    sd.Windowed = TRUE;
    sd.SwapEffect = DXGI_SWAP_EFFECT_FLIP_DISCARD;
    const D3D_FEATURE_LEVEL levels[] = {D3D_FEATURE_LEVEL_11_0, D3D_FEATURE_LEVEL_10_0};
    D3D_FEATURE_LEVEL got;
    HRESULT hr = D3D11CreateDeviceAndSwapChain(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0, levels, 2, D3D11_SDK_VERSION,
                                               &sd, &gSwap, &gDevice, &got, &gCtx);
    if (hr == DXGI_ERROR_UNSUPPORTED)
        hr = D3D11CreateDeviceAndSwapChain(nullptr, D3D_DRIVER_TYPE_WARP, nullptr, 0, levels, 2, D3D11_SDK_VERSION, &sd,
                                           &gSwap, &gDevice, &got, &gCtx);
    if (FAILED(hr)) return false;
    CreateRtv();
    return true;
}

void DestroyDevice() {
    ReleaseRtv();
    if (gSwap) gSwap->Release();
    if (gCtx) gCtx->Release();
    if (gDevice) gDevice->Release();
}

// Loads a PNG into a shader resource view for ImGui. Returns null if the file is missing or bad.
void* LoadTexture(const std::filesystem::path& file, int& w, int& h) {
    FILE* f = _wfopen(file.c_str(), L"rb");
    if (!f) return nullptr;
    int comp = 0;
    unsigned char* px = stbi_load_from_file(f, &w, &h, &comp, 4);
    fclose(f);
    if (!px) return nullptr;
    D3D11_TEXTURE2D_DESC td{};
    td.Width = w;
    td.Height = h;
    td.MipLevels = 1;
    td.ArraySize = 1;
    td.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    td.SampleDesc.Count = 1;
    td.Usage = D3D11_USAGE_DEFAULT;
    td.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    D3D11_SUBRESOURCE_DATA sd{px, static_cast<UINT>(w * 4), 0};
    ID3D11Texture2D* tex = nullptr;
    ID3D11ShaderResourceView* srv = nullptr;
    if (SUCCEEDED(gDevice->CreateTexture2D(&td, &sd, &tex))) {
        gDevice->CreateShaderResourceView(tex, nullptr, &srv);
        tex->Release();
    }
    stbi_image_free(px);
    return srv;
}

void FreeTexture(void* t) {
    if (t) static_cast<ID3D11ShaderResourceView*>(t)->Release();
}

LRESULT CALLBACK WndProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    if (ImGui_ImplWin32_WndProcHandler(hwnd, msg, wp, lp)) return 1;
    switch (msg) {
        case WM_SIZE:
            if (wp != SIZE_MINIMIZED) { gResizeW = LOWORD(lp); gResizeH = HIWORD(lp); }
            return 0;
        case WM_GETMINMAXINFO: {
            auto* mm = reinterpret_cast<MINMAXINFO*>(lp);
            mm->ptMinTrackSize = {1180, 720};
            return 0;
        }
        case WM_SYSCOMMAND:
            if ((wp & 0xfff0) == SC_KEYMENU) return 0;
            break;
        case WM_DESTROY:
            PostQuitMessage(0);
            return 0;
    }
    return DefWindowProcW(hwnd, msg, wp, lp);
}

std::filesystem::path ExeDir() {
    wchar_t buf[MAX_PATH];
    GetModuleFileNameW(nullptr, buf, MAX_PATH);
    return std::filesystem::path(buf).parent_path();
}

std::filesystem::path DataDir() {
    PWSTR p = nullptr;
    std::filesystem::path out;
    if (SUCCEEDED(SHGetKnownFolderPath(FOLDERID_RoamingAppData, 0, nullptr, &p))) out = std::filesystem::path(p) / "RecoilLedger";
    CoTaskMemFree(p);
    if (out.empty()) out = ExeDir() / "userdata";
    std::filesystem::create_directories(out);
    return out;
}

LONG WINAPI CrashFilter(EXCEPTION_POINTERS* ep) {
    wchar_t msg[256];
    swprintf(msg, 256, L"Recoil Ledger crashed (code 0x%08X at %p).\n\nSend this to whoever built it.",
             ep->ExceptionRecord->ExceptionCode, ep->ExceptionRecord->ExceptionAddress);
    MessageBoxW(nullptr, msg, L"Recoil Ledger", MB_ICONERROR);
    return EXCEPTION_EXECUTE_HANDLER;
}

}  // namespace

int WINAPI wWinMain(HINSTANCE hInst, HINSTANCE, PWSTR, int) {
    SetUnhandledExceptionFilter(CrashFilter);
    if (!std::filesystem::exists(ExeDir() / "data" / "weapons.json"))
        MessageBoxW(nullptr, L"data\\weapons.json is missing next to the exe. Run build.bat again, or copy the data folder.",
                    L"Recoil Ledger", MB_ICONWARNING);
    ImGui_ImplWin32_EnableDpiAwareness();
    const float dpi = ImGui_ImplWin32_GetDpiScaleForMonitor(MonitorFromPoint({0, 0}, MONITOR_DEFAULTTOPRIMARY));

    WNDCLASSEXW wc{sizeof(wc), CS_CLASSDC, WndProc, 0, 0, hInst, nullptr, LoadCursor(nullptr, IDC_ARROW), nullptr, nullptr,
                   L"RecoilLedger", nullptr};
    wc.hIcon = LoadIconW(hInst, MAKEINTRESOURCEW(1));
    wc.hIconSm = wc.hIcon;
    RegisterClassExW(&wc);
    HWND hwnd = CreateWindowW(wc.lpszClassName, L"Recoil Ledger", WS_OVERLAPPEDWINDOW, 100, 80, int(1360 * dpi),
                              int(820 * dpi), nullptr, nullptr, hInst, nullptr);
    BOOL dark = TRUE;
    DwmSetWindowAttribute(hwnd, 20 /*DWMWA_USE_IMMERSIVE_DARK_MODE*/, &dark, sizeof(dark));

    if (!CreateDevice(hwnd)) {
        DestroyDevice();
        MessageBoxW(nullptr, L"D3D11 device creation failed.", L"Recoil Ledger", MB_ICONERROR);
        return 1;
    }
    ShowWindow(hwnd, SW_SHOWDEFAULT);
    UpdateWindow(hwnd);

    IMGUI_CHECKVERSION();
    ImGui::CreateContext();
    ImGuiIO& io = ImGui::GetIO();
    io.IniFilename = nullptr;
    io.ConfigFlags |= ImGuiConfigFlags_NavEnableKeyboard;
    ImGui_ImplWin32_Init(hwnd);
    ImGui_ImplDX11_Init(gDevice, gCtx);

    rl::App app(ExeDir(), DataDir());
    app.loadTexture = LoadTexture;
    app.freeTexture = FreeTexture;
    gApp = &app;

    auto loadFont = [&](const char* file, float px) -> ImFont* {
        const std::string path = std::string("C:\\Windows\\Fonts\\") + file;
        if (std::filesystem::exists(path)) return io.Fonts->AddFontFromFileTTF(path.c_str(), px * dpi);
        ImFontConfig cfg;
        cfg.SizePixels = px * dpi;
        return io.Fonts->AddFontDefault(&cfg);
    };
    app.fontBody = loadFont("segoeui.ttf", 17.f);
    app.fontHead = loadFont("seguisb.ttf", 22.f);
    app.fontMono = loadFont("consola.ttf", 14.f);
    app.Init(hInst);  // applies theme
    ImGui::GetStyle().ScaleAllSizes(dpi);

    LARGE_INTEGER freq, last, now;
    QueryPerformanceFrequency(&freq);
    QueryPerformanceCounter(&last);
    bool running = true;
    while (running) {
        MSG msg;
        while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
            if (msg.message == WM_QUIT) running = false;
        }
        if (!running) break;
        if (gResizeW) {
            ReleaseRtv();
            gSwap->ResizeBuffers(0, gResizeW, gResizeH, DXGI_FORMAT_UNKNOWN, 0);
            gResizeW = gResizeH = 0;
            CreateRtv();
        }
        QueryPerformanceCounter(&now);
        const float dt = float(double(now.QuadPart - last.QuadPart) / freq.QuadPart);
        last = now;

        ImGui_ImplDX11_NewFrame();
        ImGui_ImplWin32_NewFrame();
        ImGui::NewFrame();
        ImGui::PushFont(app.fontBody);
        app.Frame(dt);
        ImGui::PopFont();
        ImGui::Render();
        const float clear[4] = {0.078f, 0.086f, 0.102f, 1.f};
        gCtx->OMSetRenderTargets(1, &gRtv, nullptr);
        gCtx->ClearRenderTargetView(gRtv, clear);
        ImGui_ImplDX11_RenderDrawData(ImGui::GetDrawData());
        gSwap->Present(1, 0);
    }

    app.Shutdown();
    gApp = nullptr;
    ImGui_ImplDX11_Shutdown();
    ImGui_ImplWin32_Shutdown();
    ImGui::DestroyContext();
    DestroyDevice();
    DestroyWindow(hwnd);
    UnregisterClassW(wc.lpszClassName, hInst);
    return 0;
}
