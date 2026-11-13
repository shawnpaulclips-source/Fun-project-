#pragma once
#include <array>
#include <cstdint>
#include <filesystem>
#include <map>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace rl {

enum class Stance : int { Center = 0, LeanLeft = 1, LeanRight = 2 };
constexpr int kStanceCount = 3;
const char* StanceName(Stance s);
const char* StanceTag(Stance s);

struct Weapon {
    std::string id, name, cls;
    int rpm = 0;
    std::vector<std::string> barrels, grips, sights, operators;
};

struct WeaponDb {
    std::vector<Weapon> weapons;
    std::vector<std::string> operators;
    static WeaponDb Load(const std::filesystem::path& file);
    const Weapon* Find(const std::string& id) const;
};

struct Loadout {
    std::string weapon, barrel, grip, sight;
    std::string Key() const;  // weapon|barrel|grip|sight
    bool operator==(const Loadout&) const = default;
};

// Raw capture: one tick per ~1 ms, raw mouse counts from physical devices only.
struct RawTick { double tMs; float dx, dy; };

struct RawSpray {
    std::string loadoutKey;
    Stance stance = Stance::Center;
    std::vector<RawTick> ticks;
    double DurationMs() const { return ticks.empty() ? 0.0 : ticks.back().tMs; }
};

// Cumulative displacement on a fixed grid.
struct Trace {
    std::vector<float> x, y;
    size_t size() const { return y.size(); }
};

constexpr double kGridMs = 5.0;

Trace Resample(const RawSpray& s);

struct Learned {
    Trace curve;   // weighted mean cumulative path
    Trace sigma;   // per-point stddev of cumulative path
    int used = 0;  // samples surviving outlier rejection
    int total = 0;
    float confidence = 0.f;  // 0..100
    bool Valid() const { return curve.size() >= 2; }
    // Cumulative position at time t (ms), linearly interpolated, clamped at the end.
    void At(double tMs, float& x, float& y) const;
    void SigmaAt(double tMs, float& sx, float& sy) const;
    double LengthMs() const { return curve.size() ? (curve.size() - 1) * kGridMs : 0.0; }
};

Learned Train(const std::vector<Trace>& samples);

struct StanceProfile {
    std::vector<Trace> samples;  // oldest first, capped
    Learned learned;
    void Add(Trace t);
    void Rebuild() { learned = Train(samples); }
};

struct Profile {
    std::string key;
    float sensAtCapture = 10.f;
    int64_t updated = 0;
    bool dirty = false;
    std::array<StanceProfile, kStanceCount> stance;
    int TotalSamples() const;
};

constexpr size_t kMaxSamplesPerStance = 48;

class Store {
public:
    explicit Store(std::filesystem::path dir);
    Profile& Get(const std::string& key);  // loads from disk on first access
    bool Save(const std::string& key);
    bool Has(const std::string& key) const;
    const std::filesystem::path& Dir() const { return dir_; }
private:
    std::filesystem::path FileFor(const std::string& key) const;
    std::filesystem::path dir_;
    std::map<std::string, Profile> cache_;
};

std::string SerializeProfile(const Profile& p);
std::optional<Profile> DeserializeProfile(const std::string& json);

// Read-only snapshot handed to the 1 kHz engine.
struct Compiled {
    std::string key;
    float sensAtCapture = 10.f;
    std::array<Learned, kStanceCount> stance;
    const Learned* For(Stance s) const;  // falls back to center, then any trained stance
};

}  // namespace rl
