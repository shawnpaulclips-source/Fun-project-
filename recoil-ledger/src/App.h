#pragma once
#include <filesystem>
#include <map>
#include <memory>
#include <string>

#include "Engine.h"
#include "Model.h"
#include "Overlay.h"

struct ImFont;

namespace rl {

class App {
public:
    App(std::filesystem::path exeDir, std::filesystem::path dataDir);
    ~App();
    void Init(void* hInstance);
    void Frame(float dt);  // ImGui calls; must be inside NewFrame/Render
    void Shutdown();
    Engine& GetEngine() { return engine_; }

    ImFont* fontBody = nullptr;
    ImFont* fontHead = nullptr;
    ImFont* fontMono = nullptr;

    // Set by the host before Init. Returns an ImTextureID-compatible handle.
    void* (*loadTexture)(const std::filesystem::path&, int& w, int& h) = nullptr;
    void (*freeTexture)(void*) = nullptr;

private:
    void SelectWeapon(const Weapon& w);
    void Recompile();
    void Ingest();
    void LoadSettings();
    void SaveSettings();
    struct Tex { void* id = nullptr; int w = 0, h = 0; };
    const Tex& Sprite(const std::string& weaponId);
    void Toast(const std::string& a, const std::string& b, unsigned rgb);

    void DrawLoadout(float dt);
    void DrawCenter(float dt);
    void DrawControls(float dt);

    std::filesystem::path exeDir_, dataDir_;
    WeaponDb db_;
    std::unique_ptr<Store> store_;
    Engine engine_;
    Overlay overlay_;

    Loadout loadout_;
    std::map<std::string, Tex> sprites_;
    Tex logo_;
    std::string opFilter_;
    char search_[64] = {};
    int viewStance_ = -1;  // -1 = all
    int lastMode_ = 0;
    uint32_t lastCommitted_ = 0, lastRejected_ = 0;

    // animation state
    float kick_ = 0, kickVel_ = 0;
    uint32_t lastPulse_ = 0;
    float swap_ = 1.f;       // 0→1 when weapon changes
    float flash_ = 0.f;
    float saveGlow_ = 0.f;
    float modeBlend_[3] = {1, 0, 0};
    std::string status_;
    float statusT_ = 0;
};

}  // namespace rl
