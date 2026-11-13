#include "Model.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <fstream>
#include <numeric>
#include <sstream>

#include <nlohmann/json.hpp>

namespace rl {
using json = nlohmann::json;

const char* StanceName(Stance s) {
    switch (s) {
        case Stance::LeanLeft: return "Lean Left";
        case Stance::LeanRight: return "Lean Right";
        default: return "Center";
    }
}
const char* StanceTag(Stance s) {
    switch (s) {
        case Stance::LeanLeft: return "Q";
        case Stance::LeanRight: return "E";
        default: return "C";
    }
}

WeaponDb WeaponDb::Load(const std::filesystem::path& file) {
    WeaponDb db;
    std::ifstream in(file);
    if (!in) return db;
    json j = json::parse(in, nullptr, false);
    if (j.is_discarded()) return db;
    const json defs = j.value("defaults", json::object());
    auto list = [&](const json& w, const char* k) {
        const json& src = w.contains(k) ? w[k] : defs.value(k, json::array());
        std::vector<std::string> out;
        for (auto& v : src) out.push_back(v.get<std::string>());
        return out;
    };
    for (auto& w : j["weapons"]) {
        Weapon x;
        x.id = w.value("id", "");
        x.name = w.value("name", x.id);
        x.cls = w.value("class", "AR");
        x.rpm = w.value("rpm", 0);
        x.barrels = list(w, "barrels");
        x.grips = list(w, "grips");
        x.sights = list(w, "sights");
        x.operators = list(w, "operators");
        for (auto& o : x.operators)
            if (std::find(db.operators.begin(), db.operators.end(), o) == db.operators.end())
                db.operators.push_back(o);
        db.weapons.push_back(std::move(x));
    }
    std::sort(db.operators.begin(), db.operators.end());
    return db;
}

const Weapon* WeaponDb::Find(const std::string& id) const {
    for (auto& w : weapons)
        if (w.id == id) return &w;
    return nullptr;
}

std::string Loadout::Key() const { return weapon + "|" + barrel + "|" + grip + "|" + sight; }

// ---------------------------------------------------------------- training

Trace Resample(const RawSpray& s) {
    Trace t;
    if (s.ticks.empty()) return t;
    const double end = s.DurationMs();
    const size_t n = static_cast<size_t>(end / kGridMs) + 1;
    t.x.resize(n);
    t.y.resize(n);
    double cx = 0, cy = 0;
    size_t i = 0;
    for (size_t g = 0; g < n; ++g) {
        const double gt = g * kGridMs;
        while (i < s.ticks.size() && s.ticks[i].tMs <= gt) {
            cx += s.ticks[i].dx;
            cy += s.ticks[i].dy;
            ++i;
        }
        t.x[g] = static_cast<float>(cx);
        t.y[g] = static_cast<float>(cy);
    }
    return t;
}

namespace {

// Centered moving average applied to increments, so drift is preserved but tick jitter is removed.
void SmoothCumulative(std::vector<float>& c, int radius) {
    if (c.size() < 3 || radius <= 0) return;
    std::vector<float> d(c.size());
    d[0] = c[0];
    for (size_t i = 1; i < c.size(); ++i) d[i] = c[i] - c[i - 1];
    std::vector<float> s(d.size());
    for (size_t i = 0; i < d.size(); ++i) {
        const size_t a = i >= static_cast<size_t>(radius) ? i - radius : 0;
        const size_t b = std::min(d.size() - 1, i + radius);
        float acc = 0;
        for (size_t k = a; k <= b; ++k) acc += d[k];
        s[i] = acc / static_cast<float>(b - a + 1);
    }
    float run = 0;
    for (size_t i = 0; i < c.size(); ++i) { run += s[i]; c[i] = run; }
}

float Median(std::vector<float> v) {
    if (v.empty()) return 0.f;
    const size_t m = v.size() / 2;
    std::nth_element(v.begin(), v.begin() + m, v.end());
    float hi = v[m];
    if (v.size() % 2) return hi;
    float lo = *std::max_element(v.begin(), v.begin() + m);
    return 0.5f * (lo + hi);
}

// Shift (in grid steps) that best aligns `s` to `ref` over their overlap — absorbs trigger latency jitter.
int BestShift(const Trace& s, const Trace& ref, int maxShift) {
    int best = 0;
    double bestErr = 1e300;
    for (int k = -maxShift; k <= maxShift; ++k) {
        double err = 0;
        int cnt = 0;
        for (size_t i = 0; i < ref.size(); ++i) {
            const long j = static_cast<long>(i) + k;
            if (j < 0 || j >= static_cast<long>(s.size())) continue;
            const double ex = s.x[j] - ref.x[i], ey = s.y[j] - ref.y[i];
            err += ex * ex + ey * ey;
            ++cnt;
        }
        if (cnt < static_cast<int>(ref.size()) / 2) continue;
        err /= cnt;
        if (err < bestErr) { bestErr = err; best = k; }
    }
    return best;
}

Trace Shifted(const Trace& s, int k) {
    if (k == 0) return s;
    Trace o;
    const size_t n = s.size();
    o.x.resize(n);
    o.y.resize(n);
    for (size_t i = 0; i < n; ++i) {
        long j = static_cast<long>(i) + k;
        j = std::clamp<long>(j, 0, static_cast<long>(n) - 1);
        // Keep origin at zero: subtract the value at the new start.
        o.x[i] = s.x[j];
        o.y[i] = s.y[j];
    }
    const float x0 = o.x[0], y0 = o.y[0];
    if (k > 0)
        for (size_t i = 0; i < n; ++i) { o.x[i] -= x0; o.y[i] -= y0; }
    return o;
}

}  // namespace

Learned Train(const std::vector<Trace>& in) {
    Learned L;
    L.total = static_cast<int>(in.size());
    std::vector<Trace> samples;
    for (auto& t : in)
        if (t.size() >= 2) samples.push_back(t);
    if (samples.empty()) return L;

    // Reference = longest sample; align everyone to it (±20 ms).
    {
        size_t ref = 0;
        for (size_t i = 1; i < samples.size(); ++i)
            if (samples[i].size() > samples[ref].size()) ref = i;
        const int maxShift = static_cast<int>(20.0 / kGridMs);
        for (size_t i = 0; i < samples.size(); ++i)
            if (i != ref) samples[i] = Shifted(samples[i], BestShift(samples[i], samples[ref], maxShift));
    }

    size_t len = 0;
    for (auto& s : samples) len = std::max(len, s.size());

    // Outlier rejection against per-point median (needs 3+ samples to mean anything).
    std::vector<bool> keep(samples.size(), true);
    if (samples.size() >= 3) {
        Trace med;
        med.x.resize(len);
        med.y.resize(len);
        for (size_t g = 0; g < len; ++g) {
            std::vector<float> xs, ys;
            for (auto& s : samples)
                if (g < s.size()) { xs.push_back(s.x[g]); ys.push_back(s.y[g]); }
            med.x[g] = Median(xs);
            med.y[g] = Median(ys);
        }
        std::vector<float> rms(samples.size());
        for (size_t i = 0; i < samples.size(); ++i) {
            double e = 0;
            for (size_t g = 0; g < samples[i].size(); ++g) {
                const double ex = samples[i].x[g] - med.x[g], ey = samples[i].y[g] - med.y[g];
                e += ex * ex + ey * ey;
            }
            rms[i] = static_cast<float>(std::sqrt(e / samples[i].size()));
        }
        const float m = Median(rms);
        std::vector<float> dev(rms.size());
        for (size_t i = 0; i < rms.size(); ++i) dev[i] = std::fabs(rms[i] - m);
        const float mad = std::max(Median(dev), 0.5f);
        for (size_t i = 0; i < rms.size(); ++i) keep[i] = rms[i] <= m + 2.5f * mad * 1.4826f;
    }

    // Recency weights: newest sample weighs most.
    const size_t n = samples.size();
    std::vector<double> w(n);
    for (size_t i = 0; i < n; ++i) w[i] = keep[i] ? std::pow(0.9, static_cast<double>(n - 1 - i)) : 0.0;

    L.curve.x.assign(len, 0);
    L.curve.y.assign(len, 0);
    L.sigma.x.assign(len, 0);
    L.sigma.y.assign(len, 0);
    size_t effLen = 0;
    for (size_t g = 0; g < len; ++g) {
        double sw = 0, mx = 0, my = 0;
        for (size_t i = 0; i < n; ++i)
            if (w[i] > 0 && g < samples[i].size()) { sw += w[i]; mx += w[i] * samples[i].x[g]; my += w[i] * samples[i].y[g]; }
        if (sw <= 0) break;
        mx /= sw;
        my /= sw;
        double vx = 0, vy = 0;
        for (size_t i = 0; i < n; ++i)
            if (w[i] > 0 && g < samples[i].size()) {
                vx += w[i] * (samples[i].x[g] - mx) * (samples[i].x[g] - mx);
                vy += w[i] * (samples[i].y[g] - my) * (samples[i].y[g] - my);
            }
        L.curve.x[g] = static_cast<float>(mx);
        L.curve.y[g] = static_cast<float>(my);
        L.sigma.x[g] = static_cast<float>(std::sqrt(vx / sw));
        L.sigma.y[g] = static_cast<float>(std::sqrt(vy / sw));
        effLen = g + 1;
    }
    L.curve.x.resize(effLen);
    L.curve.y.resize(effLen);
    L.sigma.x.resize(effLen);
    L.sigma.y.resize(effLen);

    // Single sample: smooth harder since there's nothing to average hand tremor against.
    int used = 0;
    for (bool k : keep) used += k;
    L.used = used;
    SmoothCumulative(L.curve.x, used <= 1 ? 6 : used <= 3 ? 4 : 2);
    SmoothCumulative(L.curve.y, used <= 1 ? 4 : used <= 3 ? 3 : 2);

    // Confidence: volume term × consistency term.
    double sig = 0, mag = 0;
    for (size_t g = 0; g < effLen; ++g) {
        sig += std::hypot(L.sigma.x[g], L.sigma.y[g]);
        mag += std::hypot(L.curve.x[g], L.curve.y[g]);
    }
    const double relNoise = effLen ? sig / std::max(mag, 1.0) : 1.0;
    const double volume = 1.0 - std::exp(-used / 4.0);
    const double consistency = used <= 1 ? 0.55 : std::clamp(1.0 - relNoise * 2.0, 0.15, 1.0);
    L.confidence = static_cast<float>(std::clamp(100.0 * (0.25 + 0.75 * volume) * consistency, 0.0, 100.0));
    return L;
}

void Learned::At(double tMs, float& x, float& y) const {
    if (curve.size() == 0) { x = y = 0; return; }
    const double f = std::clamp(tMs / kGridMs, 0.0, static_cast<double>(curve.size() - 1));
    const size_t i = static_cast<size_t>(f);
    const size_t j = std::min(i + 1, curve.size() - 1);
    const float a = static_cast<float>(f - i);
    x = curve.x[i] + (curve.x[j] - curve.x[i]) * a;
    y = curve.y[i] + (curve.y[j] - curve.y[i]) * a;
}

void Learned::SigmaAt(double tMs, float& sx, float& sy) const {
    if (sigma.size() == 0) { sx = sy = 0; return; }
    const size_t i = std::min(static_cast<size_t>(std::max(0.0, tMs / kGridMs)), sigma.size() - 1);
    sx = sigma.x[i];
    sy = sigma.y[i];
}

void StanceProfile::Add(Trace t) {
    samples.push_back(std::move(t));
    if (samples.size() > kMaxSamplesPerStance) samples.erase(samples.begin());
    Rebuild();
}

int Profile::TotalSamples() const {
    int n = 0;
    for (auto& s : stance) n += static_cast<int>(s.samples.size());
    return n;
}

const Learned* Compiled::For(Stance s) const {
    const Learned& want = stance[static_cast<int>(s)];
    if (want.Valid()) return &want;
    if (stance[0].Valid()) return &stance[0];
    for (auto& l : stance)
        if (l.Valid()) return &l;
    return nullptr;
}

// ---------------------------------------------------------------- persistence

std::string SerializeProfile(const Profile& p) {
    json j;
    j["version"] = 1;
    j["key"] = p.key;
    j["sens_at_capture"] = p.sensAtCapture;
    j["updated"] = p.updated;
    json st = json::object();
    for (int s = 0; s < kStanceCount; ++s) {
        json arr = json::array();
        for (auto& t : p.stance[s].samples) arr.push_back({{"x", t.x}, {"y", t.y}});
        st[StanceTag(static_cast<Stance>(s))] = arr;
    }
    j["samples"] = st;
    return j.dump();
}

std::optional<Profile> DeserializeProfile(const std::string& text) {
    json j = json::parse(text, nullptr, false);
    if (j.is_discarded() || !j.contains("key")) return std::nullopt;
    Profile p;
    p.key = j["key"].get<std::string>();
    p.sensAtCapture = j.value("sens_at_capture", 10.f);
    p.updated = j.value("updated", int64_t{0});
    const json st = j.value("samples", json::object());
    for (int s = 0; s < kStanceCount; ++s) {
        const char* tag = StanceTag(static_cast<Stance>(s));
        if (!st.contains(tag)) continue;
        for (auto& e : st[tag]) {
            Trace t;
            t.x = e["x"].get<std::vector<float>>();
            t.y = e["y"].get<std::vector<float>>();
            if (t.x.size() == t.y.size() && t.size() >= 2) p.stance[s].samples.push_back(std::move(t));
        }
        p.stance[s].Rebuild();
    }
    return p;
}

Store::Store(std::filesystem::path dir) : dir_(std::move(dir)) {
    std::error_code ec;
    std::filesystem::create_directories(dir_, ec);
}

std::filesystem::path Store::FileFor(const std::string& key) const {
    std::string f = key;
    for (char& c : f)
        if (c == '|' || c == '/' || c == '\\' || c == ':' || c == '*' || c == '?' || c == '"' || c == '<' || c == '>') c = '_';
    return dir_ / (f + ".json");
}

bool Store::Has(const std::string& key) const {
    return cache_.count(key) || std::filesystem::exists(FileFor(key));
}

Profile& Store::Get(const std::string& key) {
    auto it = cache_.find(key);
    if (it != cache_.end()) return it->second;
    Profile p;
    p.key = key;
    std::ifstream in(FileFor(key), std::ios::binary);
    if (in) {
        std::stringstream ss;
        ss << in.rdbuf();
        if (auto loaded = DeserializeProfile(ss.str())) p = std::move(*loaded);
        p.key = key;
    }
    return cache_.emplace(key, std::move(p)).first->second;
}

bool Store::Save(const std::string& key) {
    auto it = cache_.find(key);
    if (it == cache_.end()) return false;
    Profile& p = it->second;
    p.updated = std::chrono::duration_cast<std::chrono::seconds>(
                    std::chrono::system_clock::now().time_since_epoch()).count();
    const auto file = FileFor(key);
    const auto tmp = file.string() + ".tmp";
    {
        std::ofstream out(tmp, std::ios::binary | std::ios::trunc);
        if (!out) return false;
        out << SerializeProfile(p);
        if (!out) return false;
    }
    std::error_code ec;
    std::filesystem::rename(tmp, file, ec);
    if (ec) return false;
    p.dirty = false;
    return true;
}

}  // namespace rl
