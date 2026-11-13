#include "App.h"

#include <algorithm>
#include <cctype>
#include <cstdio>
#include <cmath>
#include <fstream>
#include <sstream>

#include <imgui.h>
#include <imgui_internal.h>
#include <nlohmann/json.hpp>

namespace rl {
namespace {

// Palette
const ImVec4 kBg       = ImVec4(0.078f, 0.086f, 0.102f, 1.f);  // #14161A
const ImVec4 kPanel    = ImVec4(0.106f, 0.118f, 0.137f, 1.f);
const ImVec4 kPanelHi  = ImVec4(0.141f, 0.157f, 0.184f, 1.f);
const ImVec4 kLine     = ImVec4(0.200f, 0.216f, 0.247f, 1.f);
const ImVec4 kText     = ImVec4(0.925f, 0.933f, 0.949f, 1.f);
const ImVec4 kDim      = ImVec4(0.604f, 0.631f, 0.678f, 1.f);
const ImVec4 kAmber    = ImVec4(0.949f, 0.663f, 0.231f, 1.f);  // #F2A93B
const ImVec4 kTeal     = ImVec4(0.310f, 0.702f, 0.663f, 1.f);  // #4FB3A9
const ImVec4 kViolet   = ImVec4(0.592f, 0.490f, 0.867f, 1.f);
const ImVec4 kRed      = ImVec4(0.902f, 0.341f, 0.318f, 1.f);

const ImVec4 kStanceCol[kStanceCount] = {kAmber, kTeal, kViolet};

ImU32 Col(const ImVec4& c, float a = 1.f) { return ImGui::ColorConvertFloat4ToU32(ImVec4(c.x, c.y, c.z, c.w * a)); }
ImVec4 Mix(const ImVec4& a, const ImVec4& b, float t) {
    return ImVec4(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t, a.w + (b.w - a.w) * t);
}
unsigned Rgb(const ImVec4& c) {
    return (static_cast<unsigned>(c.x * 255) << 16) | (static_cast<unsigned>(c.y * 255) << 8) | static_cast<unsigned>(c.z * 255);
}
float Approach(float cur, float target, float rate, float dt) { return cur + (target - cur) * (1.f - std::exp(-rate * dt)); }

std::string Pretty(const std::string& id) {
    std::string s = id;
    for (char& c : s) if (c == '_') c = ' ';
    if (!s.empty()) s[0] = static_cast<char>(std::toupper(static_cast<unsigned char>(s[0])));
    return s;
}

void ApplyTheme() {
    ImGuiStyle& s = ImGui::GetStyle();
    s.WindowRounding = 0;
    s.ChildRounding = 8;
    s.FrameRounding = 6;
    s.PopupRounding = 6;
    s.GrabRounding = 6;
    s.ScrollbarRounding = 6;
    s.TabRounding = 6;
    s.WindowPadding = ImVec2(18, 18);
    s.FramePadding = ImVec2(10, 7);
    s.ItemSpacing = ImVec2(10, 9);
    s.ItemInnerSpacing = ImVec2(8, 6);
    s.ScrollbarSize = 10;
    s.WindowBorderSize = 0;
    s.ChildBorderSize = 1;
    s.FrameBorderSize = 0;
    ImVec4* c = s.Colors;
    c[ImGuiCol_WindowBg] = kBg;
    c[ImGuiCol_ChildBg] = kPanel;
    c[ImGuiCol_PopupBg] = kPanelHi;
    c[ImGuiCol_Border] = kLine;
    c[ImGuiCol_Text] = kText;
    c[ImGuiCol_TextDisabled] = kDim;
    c[ImGuiCol_FrameBg] = kPanelHi;
    c[ImGuiCol_FrameBgHovered] = Mix(kPanelHi, kAmber, 0.12f);
    c[ImGuiCol_FrameBgActive] = Mix(kPanelHi, kAmber, 0.22f);
    c[ImGuiCol_SliderGrab] = kAmber;
    c[ImGuiCol_SliderGrabActive] = Mix(kAmber, kText, 0.3f);
    c[ImGuiCol_CheckMark] = kAmber;
    c[ImGuiCol_Header] = Mix(kPanelHi, kAmber, 0.18f);
    c[ImGuiCol_HeaderHovered] = Mix(kPanelHi, kAmber, 0.10f);
    c[ImGuiCol_HeaderActive] = Mix(kPanelHi, kAmber, 0.28f);
    c[ImGuiCol_Button] = kPanelHi;
    c[ImGuiCol_ButtonHovered] = Mix(kPanelHi, kAmber, 0.15f);
    c[ImGuiCol_ButtonActive] = Mix(kPanelHi, kAmber, 0.30f);
    c[ImGuiCol_ScrollbarBg] = ImVec4(0, 0, 0, 0);
    c[ImGuiCol_ScrollbarGrab] = kLine;
    c[ImGuiCol_ScrollbarGrabHovered] = kDim;
    c[ImGuiCol_NavHighlight] = kAmber;
    c[ImGuiCol_Separator] = kLine;
}

// Button with eased hover/press and a visible focus ring. Returns pressed.
bool FancyButton(const char* label, ImVec2 size, const ImVec4& accent, bool lit, ImFont* font = nullptr) {
    ImGuiWindow* win = ImGui::GetCurrentWindow();
    const ImGuiID id = win->GetID(label);
    ImGuiStorage* st = ImGui::GetStateStorage();
    float hv = st->GetFloat(id, 0.f);
    float lt = st->GetFloat(id + 1, lit ? 1.f : 0.f);

    const ImVec2 p = ImGui::GetCursorScreenPos();
    const bool pressed = ImGui::InvisibleButton(label, size);
    const bool hovered = ImGui::IsItemHovered(), held = ImGui::IsItemActive(), focused = ImGui::IsItemFocused();
    const float dt = ImGui::GetIO().DeltaTime;
    hv = Approach(hv, hovered ? 1.f : 0.f, 14.f, dt);
    lt = Approach(lt, lit ? 1.f : 0.f, 10.f, dt);
    st->SetFloat(id, hv);
    st->SetFloat(id + 1, lt);

    const float press = held ? 1.f : 0.f;
    const ImVec2 inset(press * 1.5f, press * 1.5f);
    const ImVec2 a(p.x + inset.x, p.y + inset.y), b(p.x + size.x - inset.x, p.y + size.y - inset.y);
    ImDrawList* dl = win->DrawList;
    ImVec4 fill = Mix(kPanelHi, accent, 0.10f * hv + 0.78f * lt);
    dl->AddRectFilled(a, b, Col(fill), 7.f);
    dl->AddRect(a, b, Col(Mix(kLine, accent, std::max(hv, lt))), 7.f, 0, 1.2f);
    if (focused) dl->AddRect(ImVec2(a.x - 3, a.y - 3), ImVec2(b.x + 3, b.y + 3), Col(accent, 0.6f), 9.f, 0, 1.5f);

    if (font) ImGui::PushFont(font);
    const char* end = ImGui::FindRenderedTextEnd(label);
    const ImVec2 ts = ImGui::CalcTextSize(label, end);
    const ImVec4 tc = Mix(kText, kBg, lt * 0.92f);
    dl->AddText(ImVec2((a.x + b.x - ts.x) * 0.5f, (a.y + b.y - ts.y) * 0.5f), Col(tc), label, end);
    if (font) ImGui::PopFont();
    return pressed;
}

// Pill chip used for attachments.
bool Chip(const char* label, bool selected, const ImVec4& accent) {
    const ImVec2 ts = ImGui::CalcTextSize(label, ImGui::FindRenderedTextEnd(label));
    return FancyButton(label, ImVec2(ts.x + 22, ts.y + 12), accent, selected);
}

void Ring(ImDrawList* dl, ImVec2 c, float r, float thick, float frac, const ImVec4& col) {
    dl->PathArcTo(c, r, 0, IM_PI * 2, 48);
    dl->PathStroke(Col(kLine), 0, thick);
    if (frac <= 0.001f) return;
    const float a0 = -IM_PI * 0.5f;
    dl->PathArcTo(c, r, a0, a0 + IM_PI * 2 * std::clamp(frac, 0.f, 1.f), 64);
    dl->PathStroke(Col(col), 0, thick);
}

// Original procedural silhouettes per weapon class — no game assets.
void DrawSilhouette(ImDrawList* dl, ImVec2 o, float s, const std::string& cls, bool scoped, float kick, float flash) {
    const ImU32 body = Col(Mix(kPanelHi, kText, 0.55f));
    const ImU32 dark = Col(Mix(kPanelHi, kText, 0.30f));
    o.x -= kick * 10.f * s;
    o.y -= kick * 3.f * s;
    auto R = [&](float x0, float y0, float x1, float y1, ImU32 c, float r = 2.f) {
        dl->AddRectFilled(ImVec2(o.x + x0 * s, o.y + y0 * s), ImVec2(o.x + x1 * s, o.y + y1 * s), c, r * s);
    };
    auto Q = [&](ImVec2 a, ImVec2 b, ImVec2 c2, ImVec2 d, ImU32 c) {
        auto P = [&](ImVec2 v) { return ImVec2(o.x + v.x * s, o.y + v.y * s); };
        dl->AddQuadFilled(P(a), P(b), P(c2), P(d), c);
    };
    float muzzleX = 0, muzzleY = 0;
    if (cls == "SMG" || cls == "MP") {
        const float L = cls == "MP" ? 0.8f : 1.f;
        R(30, 20, 30 + 110 * L, 40, body, 4);
        R(30 + 110 * L, 26, 30 + 140 * L, 33, dark);
        Q({70, 40}, {86, 40}, {80, 78}, {64, 78}, dark);
        Q({44, 40}, {58, 40}, {54, 66}, {40, 66}, body);
        if (cls == "SMG") R(0, 22, 32, 34, dark, 3);
        muzzleX = 30 + 140 * L; muzzleY = 29.5f;
    } else if (cls == "SG") {
        R(40, 22, 200, 36, body, 3);
        R(200, 25, 240, 32, dark);
        R(120, 36, 190, 44, dark, 3);
        Q({60, 36}, {74, 36}, {64, 66}, {50, 66}, body);
        Q({0, 26}, {40, 22}, {40, 36}, {4, 46}, dark);
        muzzleX = 240; muzzleY = 28.5f;
    } else if (cls == "LMG") {
        R(40, 18, 190, 40, body, 4);
        R(190, 25, 250, 32, dark);
        R(84, 40, 128, 70, dark, 6);
        Q({140, 40}, {152, 40}, {146, 66}, {134, 66}, body);
        Q({0, 22}, {40, 18}, {40, 40}, {4, 50}, dark);
        R(220, 32, 224, 58, dark, 1);
        muzzleX = 250; muzzleY = 28.5f;
    } else if (cls == "DMR") {
        R(40, 20, 180, 38, body, 3);
        R(180, 25, 250, 31, dark);
        Q({100, 38}, {118, 38}, {112, 68}, {96, 68}, dark);
        Q({70, 38}, {84, 38}, {78, 64}, {64, 64}, body);
        Q({0, 24}, {40, 20}, {40, 38}, {6, 50}, dark);
        muzzleX = 250; muzzleY = 28;
    } else {  // AR
        R(40, 20, 170, 38, body, 3);
        R(170, 25, 220, 32, dark);
        Q({100, 38}, {116, 38}, {108, 74}, {92, 74}, dark);
        Q({66, 38}, {80, 38}, {74, 64}, {60, 64}, body);
        Q({0, 24}, {40, 20}, {40, 38}, {6, 50}, dark);
        muzzleX = 220; muzzleY = 28.5f;
    }
    if (scoped) {
        R(70, 6, 130, 16, body, 5);
        R(92, 15, 108, 21, dark);
    } else {
        R(90, 12, 106, 20, dark, 2);
    }
    if (flash > 0.02f) {
        const ImVec2 c(o.x + muzzleX * s + 8 * s, o.y + muzzleY * s);
        const float r = (8.f + 10.f * flash) * s;
        dl->AddCircleFilled(c, r, Col(kAmber, 0.35f * flash), 20);
        dl->AddTriangleFilled(ImVec2(c.x - 2 * s, c.y - r * 0.6f), ImVec2(c.x + r * 1.8f, c.y), ImVec2(c.x - 2 * s, c.y + r * 0.6f),
                              Col(Mix(kAmber, kText, 0.5f), 0.9f * flash));
    }
}

void PlotTrace(ImDrawList* dl, const std::vector<float>& xs, const std::vector<float>& ys, ImVec2 origin, float scale,
               ImU32 col, float thick) {
    if (xs.size() < 2) return;
    const size_t step = std::max<size_t>(1, xs.size() / 400);
    std::vector<ImVec2> pts;
    pts.reserve(xs.size() / step + 2);
    for (size_t i = 0; i < xs.size(); i += step) pts.emplace_back(origin.x + xs[i] * scale, origin.y + ys[i] * scale);
    pts.emplace_back(origin.x + xs.back() * scale, origin.y + ys.back() * scale);
    dl->AddPolyline(pts.data(), static_cast<int>(pts.size()), col, 0, thick);
}

}  // namespace

App::App(std::filesystem::path exeDir, std::filesystem::path dataDir)
    : exeDir_(std::move(exeDir)), dataDir_(std::move(dataDir)) {}

App::~App() = default;

void App::Init(void* hInstance) {
    ApplyTheme();
    db_ = WeaponDb::Load(exeDir_ / "data" / "weapons.json");
    store_ = std::make_unique<Store>(dataDir_ / "profiles");
    overlay_.Create(hInstance);
    if (loadTexture) logo_.id = loadTexture(exeDir_ / "assets" / "logo.png", logo_.w, logo_.h);
    LoadSettings();
    if (!db_.weapons.empty()) {
        const Weapon* w = db_.Find(loadout_.weapon);
        if (!w) SelectWeapon(db_.weapons.front());
        else Recompile();
    }
    engine_.Start();
}

void App::Shutdown() {
    engine_.Stop();
    SaveSettings();
    overlay_.Destroy();
    if (freeTexture) {
        for (auto& [k, t] : sprites_) freeTexture(t.id);
        freeTexture(logo_.id);
    }
    sprites_.clear();
}

// Drop-in art: <exe>/assets/weapons/<weapon id>.png, side view, muzzle pointing right, transparent background.
const App::Tex& App::Sprite(const std::string& id) {
    auto it = sprites_.find(id);
    if (it != sprites_.end()) return it->second;
    Tex t;
    if (loadTexture) t.id = loadTexture(exeDir_ / "assets" / "weapons" / (id + ".png"), t.w, t.h);
    return sprites_.emplace(id, t).first->second;
}

void App::Toast(const std::string& a, const std::string& b, unsigned rgb) { overlay_.Show(a, b, rgb); }

void App::SelectWeapon(const Weapon& w) {
    auto pick = [](const std::vector<std::string>& v, const std::string& cur) {
        if (std::find(v.begin(), v.end(), cur) != v.end()) return cur;
        return v.empty() ? std::string("none") : v.front();
    };
    loadout_.weapon = w.id;
    loadout_.barrel = pick(w.barrels, loadout_.barrel);
    loadout_.grip = pick(w.grips, loadout_.grip);
    loadout_.sight = pick(w.sights, loadout_.sight);
    swap_ = 0.f;
    Recompile();
}

void App::Recompile() {
    Profile& p = store_->Get(loadout_.Key());
    auto c = std::make_shared<Compiled>();
    c->key = p.key;
    c->sensAtCapture = p.sensAtCapture;
    for (int s = 0; s < kStanceCount; ++s) c->stance[s] = p.stance[s].learned;
    engine_.SetCompiled(std::move(c));
}

void App::Ingest() {
    auto sprays = engine_.DrainSprays();
    for (auto& sp : sprays) {
        Profile& p = store_->Get(sp.loadoutKey);
        if (p.TotalSamples() == 0) p.sensAtCapture = engine_.settings.currentSens.load();
        Trace t = Resample(sp);
        // Re-express in capture sensitivity so mixed-sens sessions still average correctly.
        const float k = engine_.settings.currentSens.load() / std::max(0.1f, p.sensAtCapture);
        if (std::fabs(k - 1.f) > 1e-3f)
            for (size_t i = 0; i < t.size(); ++i) { t.x[i] *= k; t.y[i] *= k; }
        p.stance[static_cast<int>(sp.stance)].Add(std::move(t));
        p.dirty = true;
        const auto& L = p.stance[static_cast<int>(sp.stance)].learned;
        char buf[96];
        snprintf(buf, sizeof(buf), "%s  ·  %d sprays  ·  %.0f%% conf", StanceName(sp.stance),
                 static_cast<int>(p.stance[static_cast<int>(sp.stance)].samples.size()), L.confidence);
        Toast("Spray learned", buf, Rgb(kStanceCol[static_cast<int>(sp.stance)]));
        if (sp.loadoutKey == loadout_.Key()) Recompile();
    }
}

void App::LoadSettings() {
    std::ifstream in(dataDir_ / "settings.json");
    if (!in) return;
    auto j = nlohmann::json::parse(in, nullptr, false);
    if (j.is_discarded()) return;
    auto& s = engine_.settings;
    s.strength = j.value("strength", 1.0f);
    s.humanize = j.value("humanize", 0.35f);
    s.currentSens = j.value("sens", 10.f);
    s.adsToggle = j.value("ads_toggle", false);
    s.requireAds = j.value("require_ads", true);
    s.leanLeftVk = j.value("lean_left_vk", static_cast<int>('Q'));
    s.leanRightVk = j.value("lean_right_vk", static_cast<int>('E'));
    loadout_.weapon = j.value("weapon", "");
    loadout_.barrel = j.value("barrel", "none");
    loadout_.grip = j.value("grip", "none");
    loadout_.sight = j.value("sight", "1.0x");
}

void App::SaveSettings() {
    auto& s = engine_.settings;
    nlohmann::json j{{"strength", s.strength.load()}, {"humanize", s.humanize.load()}, {"sens", s.currentSens.load()},
                     {"ads_toggle", s.adsToggle.load()}, {"require_ads", s.requireAds.load()},
                     {"lean_left_vk", s.leanLeftVk.load()}, {"lean_right_vk", s.leanRightVk.load()},
                     {"weapon", loadout_.weapon}, {"barrel", loadout_.barrel}, {"grip", loadout_.grip}, {"sight", loadout_.sight}};
    std::ofstream(dataDir_ / "settings.json") << j.dump(2);
}

void App::Frame(float dt) {
    Ingest();
    overlay_.Tick();

    const int mode = engine_.status.mode.load();
    if (mode != lastMode_) {
        static const char* names[] = {"Idle", "Training", "Armed"};
        const ImVec4 cols[] = {kDim, kTeal, kAmber};
        const Weapon* w = db_.Find(loadout_.weapon);
        Toast(names[mode], (w ? w->name : std::string("?")) + "  " + loadout_.sight, Rgb(cols[mode]));
        lastMode_ = mode;
    }
    const uint32_t rej = engine_.status.rejected.load();
    if (rej != lastRejected_) {
        lastRejected_ = rej;
        status_ = "Spray discarded: hold fire 2s+ in one stance";
        statusT_ = 3.f;
    }

    // Weapon kick spring, driven by the engine's live spray.
    const bool firing = engine_.status.trigger.load() && mode != 0;
    const uint32_t pulse = engine_.status.shotPulse.load();
    const Weapon* w = db_.Find(loadout_.weapon);
    const float shotMs = w && w->rpm > 0 ? 60000.f / w->rpm : 100.f;
    if (firing && pulse != lastPulse_) {
        static float shotClock = 0;
        shotClock += dt * 1000.f;
        if (shotClock >= shotMs) { shotClock = std::fmod(shotClock, shotMs); kickVel_ += 9.f; flash_ = 1.f; }
    }
    lastPulse_ = pulse;
    kickVel_ += (-220.f * kick_ - 22.f * kickVel_) * dt;
    kick_ += kickVel_ * dt;
    flash_ = Approach(flash_, 0.f, 22.f, dt);
    swap_ = Approach(swap_, 1.f, 9.f, dt);
    for (int i = 0; i < 3; ++i) modeBlend_[i] = Approach(modeBlend_[i], i == mode ? 1.f : 0.f, 10.f, dt);
    statusT_ = std::max(0.f, statusT_ - dt);

    const ImGuiViewport* vp = ImGui::GetMainViewport();
    ImGui::SetNextWindowPos(vp->WorkPos);
    ImGui::SetNextWindowSize(vp->WorkSize);
    ImGui::Begin("##root", nullptr,
                 ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_NoMove | ImGuiWindowFlags_NoBringToFrontOnFocus |
                     ImGuiWindowFlags_NoSavedSettings);

    // Header strip
    if (logo_.id) {
        const float sz = ImGui::GetTextLineHeight() * 1.9f;
        ImGui::Image(reinterpret_cast<ImTextureID>(logo_.id), ImVec2(sz, sz));
        ImGui::SameLine(0, 12);
        ImGui::SetCursorPosY(ImGui::GetCursorPosY() + sz * 0.12f);
    }
    ImGui::PushFont(fontHead);
    ImGui::TextColored(kAmber, "RECOIL");
    ImGui::SameLine(0, 2);
    ImGui::TextColored(kDim, "//");
    ImGui::SameLine(0, 2);
    ImGui::TextUnformatted("LEDGER");
    ImGui::PopFont();
    ImGui::SameLine();
    {
        const ImVec4 mc = Mix(Mix(kDim, kTeal, modeBlend_[1]), kAmber, modeBlend_[2]);
        static const char* names[] = {"IDLE", "TRAINING", "ARMED"};
        ImGui::SetCursorPosX(ImGui::GetWindowWidth() - 340);
        ImGui::PushFont(fontMono);
        ImGui::TextColored(kDim, "F6 arm  F7 train  F8 kill");
        ImGui::SameLine();
        ImGui::GetWindowDrawList()->AddCircleFilled(ImVec2(ImGui::GetCursorScreenPos().x + 5, ImGui::GetCursorScreenPos().y + ImGui::GetTextLineHeight() * 0.5f), 4.f, Col(mc));
        ImGui::SetCursorPosX(ImGui::GetCursorPosX() + 14);
        ImGui::TextColored(mc, "%s", names[mode]);
        ImGui::PopFont();
    }
    ImGui::Spacing();

    const float H = ImGui::GetContentRegionAvail().y;
    const float W = ImGui::GetContentRegionAvail().x;
    ImGui::BeginChild("##loadout", ImVec2(W * 0.26f, H), ImGuiChildFlags_Borders);
    DrawLoadout(dt);
    ImGui::EndChild();
    ImGui::SameLine();
    ImGui::BeginChild("##center", ImVec2(W * 0.47f, H), ImGuiChildFlags_Borders);
    DrawCenter(dt);
    ImGui::EndChild();
    ImGui::SameLine();
    ImGui::BeginChild("##controls", ImVec2(0, H), ImGuiChildFlags_Borders);
    DrawControls(dt);
    ImGui::EndChild();

    ImGui::End();
}

void App::DrawLoadout(float) {
    ImGui::PushFont(fontHead);
    ImGui::TextUnformatted("Loadout");
    ImGui::PopFont();

    ImGui::SetNextItemWidth(-1);
    ImGui::InputTextWithHint("##search", "Search weapon or operator", search_, sizeof(search_));
    ImGui::SetNextItemWidth(-1);
    if (ImGui::BeginCombo("##op", opFilter_.empty() ? "All operators" : opFilter_.c_str())) {
        if (ImGui::Selectable("All operators", opFilter_.empty())) opFilter_.clear();
        for (auto& o : db_.operators)
            if (ImGui::Selectable(o.c_str(), o == opFilter_)) opFilter_ = o;
        ImGui::EndCombo();
    }

    const float listH = ImGui::GetContentRegionAvail().y - 230;
    ImGui::BeginChild("##weapons", ImVec2(0, std::max(120.f, listH)), ImGuiChildFlags_None);
    std::string q = search_;
    std::transform(q.begin(), q.end(), q.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    for (auto& wpn : db_.weapons) {
        if (!opFilter_.empty() && std::find(wpn.operators.begin(), wpn.operators.end(), opFilter_) == wpn.operators.end())
            continue;
        if (!q.empty()) {
            std::string hay = wpn.name;
            for (auto& o : wpn.operators) hay += " " + o;
            std::transform(hay.begin(), hay.end(), hay.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
            if (hay.find(q) == std::string::npos) continue;
        }
        const bool sel = wpn.id == loadout_.weapon;
        ImGui::PushID(wpn.id.c_str());
        const ImVec2 p = ImGui::GetCursorScreenPos();
        if (ImGui::Selectable("##w", sel, 0, ImVec2(0, 34))) SelectWeapon(wpn);
        ImDrawList* dl = ImGui::GetWindowDrawList();
        if (sel) dl->AddRectFilled(p, ImVec2(p.x + 3, p.y + 34), Col(kAmber), 2);
        dl->AddText(ImVec2(p.x + 12, p.y + 3), Col(kText), wpn.name.c_str());
        std::string ops;
        for (size_t i = 0; i < wpn.operators.size() && i < 3; ++i) ops += (i ? ", " : "") + wpn.operators[i];
        dl->AddText(fontMono, fontMono->FontSize * 0.85f, ImVec2(p.x + 12, p.y + 19), Col(kDim), ops.c_str());
        const ImVec2 cs = ImGui::CalcTextSize(wpn.cls.c_str());
        const float rx = p.x + ImGui::GetContentRegionAvail().x - cs.x - 8;
        dl->AddRect(ImVec2(rx - 6, p.y + 8), ImVec2(rx + cs.x + 6, p.y + 26), Col(kLine), 4);
        dl->AddText(ImVec2(rx, p.y + 9), Col(kDim), wpn.cls.c_str());
        ImGui::PopID();
    }
    ImGui::EndChild();

    const Weapon* w = db_.Find(loadout_.weapon);
    if (!w) return;
    auto row = [&](const char* title, const std::vector<std::string>& opts, std::string& cur) {
        ImGui::TextColored(kDim, "%s", title);
        float x = 0;
        const float avail = ImGui::GetContentRegionAvail().x;
        for (auto& o : opts) {
            const std::string label = Pretty(o) + "##" + title + o;
            const float wdt = ImGui::CalcTextSize(Pretty(o).c_str()).x + 22;
            if (x > 0 && x + wdt > avail) x = 0;
            else if (x > 0) ImGui::SameLine(0, 6);
            if (Chip(label.c_str(), o == cur, kAmber) && o != cur) { cur = o; Recompile(); }
            x += wdt + 6;
        }
    };
    ImGui::Separator();
    row("Barrel", w->barrels, loadout_.barrel);
    row("Grip", w->grips, loadout_.grip);
    row("Sight", w->sights, loadout_.sight);
}

void App::DrawCenter(float) {
    const Weapon* w = db_.Find(loadout_.weapon);
    Profile& p = store_->Get(loadout_.Key());
    ImDrawList* dl = ImGui::GetWindowDrawList();

    // Title + silhouette with swap slide-in
    const float slide = (1.f - swap_) * 40.f;
    ImGui::SetCursorPosX(ImGui::GetCursorPosX() + slide);
    ImGui::PushStyleVar(ImGuiStyleVar_Alpha, swap_);
    ImGui::PushFont(fontHead);
    ImGui::TextUnformatted(w ? w->name.c_str() : "No weapon");
    ImGui::PopFont();
    ImGui::PushFont(fontMono);
    ImGui::TextColored(kDim, "%s · %s · %s", Pretty(loadout_.barrel).c_str(), Pretty(loadout_.grip).c_str(), loadout_.sight.c_str());
    ImGui::PopFont();
    {
        const ImVec2 o = ImGui::GetCursorScreenPos();
        const float s = std::min(1.6f, (ImGui::GetContentRegionAvail().x - 40) / 260.f);
        const bool scoped = loadout_.sight != "1.0x";
        const Tex& spr = w ? Sprite(w->id) : logo_;
        if (w && spr.id && spr.w > 0) {
            const float boxW = 260 * s, boxH = 84 * s;
            const float k = std::min(boxW / spr.w, boxH / spr.h);
            const float iw = spr.w * k, ih = spr.h * k;
            const ImVec2 a(o.x + 20 + slide - kick_ * 10.f * s, o.y + 6 + (boxH - ih) * 0.5f - kick_ * 3.f * s);
            dl->AddImage(reinterpret_cast<ImTextureID>(spr.id), a, ImVec2(a.x + iw, a.y + ih), ImVec2(0, 0), ImVec2(1, 1),
                         Col(ImVec4(1, 1, 1, swap_)));
            if (flash_ > 0.02f) {
                const ImVec2 c(a.x + iw + 8 * s, a.y + ih * 0.35f);
                dl->AddCircleFilled(c, (8.f + 10.f * flash_) * s, Col(kAmber, 0.35f * flash_), 20);
            }
        } else {
            DrawSilhouette(dl, ImVec2(o.x + 20 + slide, o.y + 6), s, w ? w->cls : "AR", scoped, kick_, flash_);
        }
        ImGui::Dummy(ImVec2(0, 84 * s));
    }
    ImGui::PopStyleVar();

    // Stance filter tabs
    const char* tabs[] = {"All", "Center", "Lean Q", "Lean E"};
    for (int i = 0; i < 4; ++i) {
        if (i) ImGui::SameLine(0, 6);
        const ImVec4 acc = i == 0 ? kText : kStanceCol[i - 1];
        if (Chip(tabs[i], viewStance_ == i - 1, acc)) viewStance_ = i - 1;
    }

    // Spray graph
    const ImVec2 g0 = ImGui::GetCursorScreenPos();
    const ImVec2 gs = ImGui::GetContentRegionAvail();
    const ImVec2 g1(g0.x + gs.x, g0.y + gs.y);
    dl->AddRectFilled(g0, g1, Col(kBg), 8);
    for (int i = 1; i < 8; ++i) {
        const float x = g0.x + gs.x * i / 8.f, y = g0.y + gs.y * i / 8.f;
        dl->AddLine(ImVec2(x, g0.y), ImVec2(x, g1.y), Col(kLine, 0.35f));
        dl->AddLine(ImVec2(g0.x, y), ImVec2(g1.x, y), Col(kLine, 0.35f));
    }

    std::vector<float> lx, ly;
    int liveStance = 0;
    engine_.LiveTrace(lx, ly, liveStance);

    // Fit bounds over everything visible.
    float minX = -20, maxX = 20, maxY = 60;
    auto grow = [&](const std::vector<float>& xs, const std::vector<float>& ys) {
        for (size_t i = 0; i < xs.size(); ++i) { minX = std::min(minX, xs[i]); maxX = std::max(maxX, xs[i]); maxY = std::max(maxY, ys[i]); }
    };
    for (int s = 0; s < kStanceCount; ++s) {
        if (viewStance_ >= 0 && viewStance_ != s) continue;
        for (auto& t : p.stance[s].samples) grow(t.x, t.y);
    }
    grow(lx, ly);
    const float span = std::max({maxX - minX, maxY, 1.f});
    const float scale = std::min(gs.x * 0.8f / std::max(maxX - minX, 1.f), gs.y * 0.85f / span);
    const ImVec2 origin(g0.x + gs.x * 0.5f - (minX + maxX) * 0.5f * scale, g0.y + gs.y * 0.07f);
    dl->PushClipRect(g0, g1, true);
    dl->AddCircle(origin, 5, Col(kText, 0.5f), 16, 1.5f);

    for (int s = 0; s < kStanceCount; ++s) {
        if (viewStance_ >= 0 && viewStance_ != s) continue;
        const auto& sp = p.stance[s];
        for (auto& t : sp.samples) PlotTrace(dl, t.x, t.y, origin, scale, Col(kStanceCol[s], 0.13f), 1.f);
        const Learned& L = sp.learned;
        if (L.Valid()) {
            std::vector<float> hiX(L.curve.x), loX(L.curve.x);
            for (size_t i = 0; i < hiX.size(); ++i) { hiX[i] += L.sigma.x[i]; loX[i] -= L.sigma.x[i]; }
            PlotTrace(dl, hiX, L.curve.y, origin, scale, Col(kStanceCol[s], 0.25f), 1.f);
            PlotTrace(dl, loX, L.curve.y, origin, scale, Col(kStanceCol[s], 0.25f), 1.f);
            PlotTrace(dl, L.curve.x, L.curve.y, origin, scale, Col(kStanceCol[s]), 2.6f);
        }
    }
    if (!lx.empty()) PlotTrace(dl, lx, ly, origin, scale, Col(kText, 0.95f), 2.f);
    dl->PopClipRect();

    ImGui::PushFont(fontMono);
    dl->AddText(ImVec2(g0.x + 12, g1.y - 22), Col(kDim), "pull path · raw counts · y = down");
    ImGui::PopFont();
    ImGui::Dummy(gs);
}

void App::DrawControls(float dt) {
    Profile& p = store_->Get(loadout_.Key());
    auto& s = engine_.settings;
    const Mode mode = engine_.GetMode();
    ImDrawList* dl = ImGui::GetWindowDrawList();
    const float bw = ImGui::GetContentRegionAvail().x;

    ImGui::PushFont(fontHead);
    ImGui::TextUnformatted("Control");
    ImGui::PopFont();

    if (FancyButton(mode == Mode::Training ? "STOP TRAINING  ·  F7" : "TRAIN  ·  F7", ImVec2(bw, 44), kTeal,
                    mode == Mode::Training, fontBody))
        engine_.SetMode(mode == Mode::Training ? Mode::Idle : Mode::Training);
    if (FancyButton(mode == Mode::Armed ? "DISARM  ·  F6" : "ARM RECOIL CONTROL  ·  F6", ImVec2(bw, 44), kAmber,
                    mode == Mode::Armed, fontBody))
        engine_.SetMode(mode == Mode::Armed ? Mode::Idle : Mode::Armed);

    // Hold meter: fills over the 2s training gate.
    {
        const float hold = engine_.status.holdMs.load();
        const float gate = static_cast<float>(s.minTrainHoldMs.load());
        const ImVec2 c = ImGui::GetCursorScreenPos();
        const float h = 8;
        dl->AddRectFilled(c, ImVec2(c.x + bw, c.y + h), Col(kPanelHi), 4);
        const float f = mode == Mode::Training ? std::min(1.f, hold / gate) : 0.f;
        const ImVec4 fc = f >= 1.f ? kTeal : Mix(kDim, kTeal, f);
        if (f > 0) dl->AddRectFilled(c, ImVec2(c.x + bw * f, c.y + h), Col(fc), 4);
        ImGui::Dummy(ImVec2(bw, h));
        ImGui::PushFont(fontMono);
        const int st = engine_.status.stance.load();
        ImGui::TextColored(kDim, "hold %.1fs  ·  %s%s", hold / 1000.f, StanceName(static_cast<Stance>(st)),
                           engine_.status.ads.load() ? "  ·  ADS" : "");
        if (mode == Mode::Training)
            ImGui::TextColored(kTeal, "ADS + hold fire 2s+  ·  hold Q/E whole spray for leans");
        else if (mode == Mode::Armed && !engine_.status.trigger.load())
            ImGui::TextColored(kAmber, "ADS + fire to engage  ·  Q/E switches curve live");
        else
            ImGui::TextColored(kDim, " ");
        ImGui::PopFont();
    }

    ImGui::Separator();

    // Confidence rings per stance
    const float cw = bw / 3.f;
    const ImVec2 base = ImGui::GetCursorScreenPos();
    const int liveSt = engine_.status.stance.load();
    for (int i = 0; i < kStanceCount; ++i) {
        const auto& L = p.stance[i].learned;
        const ImVec2 c(base.x + cw * i + cw * 0.5f, base.y + 40);
        ImGuiStorage* st = ImGui::GetStateStorage();
        const ImGuiID id = ImGui::GetID(i + 1000);
        float shown = Approach(st->GetFloat(id, 0.f), L.confidence / 100.f, 6.f, dt);
        st->SetFloat(id, shown);
        const ImVec4 rc = L.confidence < 60 ? Mix(kAmber, kStanceCol[i], 0.3f) : kStanceCol[i];
        Ring(dl, c, 30, liveSt == i ? 6.f : 4.f, shown, rc);
        char pct[16];
        snprintf(pct, sizeof(pct), "%.0f", L.confidence);
        ImGui::PushFont(fontHead);
        const ImVec2 ts = ImGui::CalcTextSize(pct);
        dl->AddText(ImVec2(c.x - ts.x * 0.5f, c.y - ts.y * 0.5f), Col(kText), pct);
        ImGui::PopFont();
        const char* lab = i == 0 ? "CENTER" : i == 1 ? "LEAN Q" : "LEAN E";
        const ImVec2 ls = ImGui::CalcTextSize(lab);
        dl->AddText(ImVec2(c.x - ls.x * 0.5f, c.y + 38), Col(kStanceCol[i]), lab);
        char n[24];
        snprintf(n, sizeof(n), "%d sprays", static_cast<int>(p.stance[i].samples.size()));
        const ImVec2 ns = ImGui::CalcTextSize(n);
        dl->AddText(ImVec2(c.x - ns.x * 0.5f, c.y + 56), Col(kDim), n);
    }
    ImGui::Dummy(ImVec2(bw, 120));

    // Save
    saveGlow_ = Approach(saveGlow_, p.dirty ? 1.f : 0.f, 6.f, dt);
    const float pulse = p.dirty ? 0.5f + 0.5f * std::sin(static_cast<float>(ImGui::GetTime()) * 4.f) : 0.f;
    if (saveGlow_ > 0.01f) {
        const ImVec2 c = ImGui::GetCursorScreenPos();
        dl->AddRect(ImVec2(c.x - 3, c.y - 3), ImVec2(c.x + bw + 3, c.y + 47), Col(kAmber, 0.35f * saveGlow_ * pulse), 9, 0, 2);
    }
    if (FancyButton(p.dirty ? "SAVE  ·  unsaved sprays" : "SAVED", ImVec2(bw, 44), kAmber, false, fontBody)) {
        if (store_->Save(p.key)) {
            const Weapon* w = db_.Find(loadout_.weapon);
            Toast("Saved", (w ? w->name : p.key) + "  " + loadout_.sight, Rgb(kTeal));
            status_ = "Saved " + p.key;
        } else {
            status_ = "Save failed - check " + store_->Dir().string();
        }
        statusT_ = 3.f;
    }
    {
        const float hw = (bw - 10) * 0.5f;
        const int target = viewStance_ < 0 ? -1 : viewStance_;
        if (FancyButton(target < 0 ? "Undo last (center)" : "Undo last spray", ImVec2(hw, 34), kDim, false)) {
            auto& sp = p.stance[target < 0 ? 0 : target];
            if (!sp.samples.empty()) {
                sp.samples.pop_back();
                sp.Rebuild();
                p.dirty = true;
            }
            Recompile();
        }
        ImGui::SameLine(0, 10);
        static float confirmT = 0;
        confirmT = std::max(0.f, confirmT - dt);
        if (FancyButton(confirmT > 0 ? "Click again" : "Reset view stance", ImVec2(hw, 34), kRed, confirmT > 0)) {
            if (confirmT > 0) {
                for (int i = 0; i < kStanceCount; ++i)
                    if (target < 0 || target == i) { p.stance[i].samples.clear(); p.stance[i].Rebuild(); }
                p.dirty = true;
                Recompile();
                confirmT = 0;
            } else {
                confirmT = 2.f;
            }
        }
    }

    ImGui::Separator();
    ImGui::PushFont(fontHead);
    ImGui::TextUnformatted("Tuning");
    ImGui::PopFont();
    ImGui::SetNextItemWidth(-1);
    float strength = s.strength.load() * 100.f;
    if (ImGui::SliderFloat("##str", &strength, 0.f, 150.f, "Strength %.0f%%")) s.strength = strength / 100.f;
    ImGui::SetNextItemWidth(-1);
    float hum = s.humanize.load() * 100.f;
    if (ImGui::SliderFloat("##hum", &hum, 0.f, 100.f, "Humanize %.0f%%")) s.humanize = hum / 100.f;
    ImGui::SetNextItemWidth(-1);
    float sens = s.currentSens.load();
    if (ImGui::DragFloat("##sens", &sens, 0.1f, 1.f, 100.f, "In-game sens %.1f")) s.currentSens = sens;
    if (p.TotalSamples()) {
        ImGui::PushFont(fontMono);
        ImGui::TextColored(kDim, "trained at sens %.1f · scale %.2fx", p.sensAtCapture, p.sensAtCapture / std::max(0.1f, sens));
        ImGui::PopFont();
    }
    bool adsT = s.adsToggle.load(), reqAds = s.requireAds.load();
    if (ImGui::Checkbox("ADS is toggle", &adsT)) s.adsToggle = adsT;
    ImGui::SameLine();
    if (ImGui::Checkbox("Require ADS", &reqAds)) s.requireAds = reqAds;

    auto keyPick = [&](const char* label, std::atomic<int>& vk) {
        char cur[2] = {static_cast<char>(vk.load()), 0};
        ImGui::SetNextItemWidth(60);
        if (ImGui::InputText(label, cur, sizeof(cur), ImGuiInputTextFlags_CharsUppercase | ImGuiInputTextFlags_AutoSelectAll) &&
            cur[0] >= 'A' && cur[0] <= 'Z')
            vk = cur[0];
    };
    keyPick("Lean left", s.leanLeftVk);
    ImGui::SameLine();
    keyPick("Lean right", s.leanRightVk);

    if (statusT_ > 0) {
        ImGui::PushStyleVar(ImGuiStyleVar_Alpha, std::min(1.f, statusT_));
        ImGui::TextColored(kDim, "%s", status_.c_str());
        ImGui::PopStyleVar();
    }
}

}  // namespace rl
