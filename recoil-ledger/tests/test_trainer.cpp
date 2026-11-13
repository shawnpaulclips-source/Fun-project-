#define DOCTEST_CONFIG_IMPLEMENT_WITH_MAIN
#include <doctest/doctest.h>

#include <cmath>
#include <filesystem>
#include <random>

#include "Model.h"

using namespace rl;

namespace {

// Synthetic "true" pull: steep vertical early, easing off, slight right drift.
void TruePull(double t, double& x, double& y) {
    y = 0.22 * t - 0.00004 * t * t;
    x = 0.015 * t + 3.0 * std::sin(t / 400.0);
}

RawSpray MakeSpray(std::mt19937& rng, double durMs, double tremor, double latencyMs, double trackAmp) {
    std::normal_distribution<double> n(0, tremor);
    RawSpray s;
    s.loadoutKey = "r4c|none|none|1.0x";
    double px = 0, py = 0, fracX = 0, fracY = 0;
    const double phase = std::uniform_real_distribution<double>(0, 6.28)(rng);
    for (double t = 0; t <= durMs; t += 1.0) {
        double x, y;
        TruePull(std::max(0.0, t - latencyMs), x, y);
        x += trackAmp * std::sin(t / 150.0 + phase);  // aim tracking that isn't recoil
        x += n(rng);  // hand tremor jitters position around intent, it doesn't random-walk
        y += n(rng);
        const double dx = x - px + fracX, dy = y - py + fracY;
        const double ix = std::round(dx), iy = std::round(dy);  // mice report integers
        fracX = dx - ix;
        fracY = dy - iy;
        px = x;
        py = y;
        s.ticks.push_back({t, static_cast<float>(ix), static_cast<float>(iy)});
    }
    return s;
}

double RmsVsTruth(const Learned& L, double upTo) {
    double e = 0;
    int c = 0;
    for (double t = 0; t <= upTo; t += kGridMs) {
        float lx, ly;
        L.At(t, lx, ly);
        double x, y;
        TruePull(t, x, y);
        e += (lx - x) * (lx - x) + (ly - y) * (ly - y);
        ++c;
    }
    return std::sqrt(e / c);
}

}  // namespace

TEST_CASE("resample produces cumulative grid") {
    RawSpray s;
    for (int i = 0; i <= 100; ++i) s.ticks.push_back({double(i), 1.f, 2.f});
    Trace t = Resample(s);
    REQUIRE(t.size() == 21);
    CHECK(t.y.back() == doctest::Approx(202.f));
    CHECK(t.x[10] == doctest::Approx(51.f));
}

TEST_CASE("one spray is already usable") {
    std::mt19937 rng(1);
    Trace t = Resample(MakeSpray(rng, 2500, 0.6, 0, 0));
    Learned L = Train({t});
    REQUIRE(L.Valid());
    CHECK(RmsVsTruth(L, 2400) < 3.0);
    CHECK(L.confidence > 10.f);
}

TEST_CASE("more training converges and confidence rises") {
    std::mt19937 rng(7);
    std::vector<Trace> samples;
    double rms1 = 0, rms20 = 0;
    float c1 = 0, c20 = 0;
    for (int i = 0; i < 20; ++i) {
        std::uniform_real_distribution<double> lat(0, 12);
        samples.push_back(Resample(MakeSpray(rng, 2400 + 200 * (i % 3), 1.2, lat(rng), 6.0)));
        Learned L = Train(samples);
        if (i == 0) { rms1 = RmsVsTruth(L, 2300); c1 = L.confidence; }
        if (i == 19) { rms20 = RmsVsTruth(L, 2300); c20 = L.confidence; }
    }
    MESSAGE("rms after 1: " << rms1 << "  after 20: " << rms20 << "  conf " << c1 << " -> " << c20);
    CHECK(rms20 < rms1);
    CHECK(rms20 < 4.0);
    CHECK(c20 > c1);
}

TEST_CASE("outlier spray is rejected") {
    std::mt19937 rng(3);
    std::vector<Trace> samples;
    for (int i = 0; i < 6; ++i) samples.push_back(Resample(MakeSpray(rng, 2400, 0.8, 0, 0)));
    Trace bad = samples.back();
    for (size_t i = 0; i < bad.size(); ++i) bad.x[i] += 0.12f * i;  // flicked off target
    samples.push_back(bad);
    Learned L = Train(samples);
    CHECK(L.used == 6);
    CHECK(RmsVsTruth(L, 2300) < 2.5);
}

TEST_CASE("profile round-trips through JSON and store") {
    std::mt19937 rng(9);
    auto dir = std::filesystem::temp_directory_path() / "rl_test_store";
    std::filesystem::remove_all(dir);
    {
        Store st(dir);
        Profile& p = st.Get("r4c|none|none|1.0x");
        p.sensAtCapture = 12.f;
        p.stance[1].Add(Resample(MakeSpray(rng, 2200, 0.5, 0, 0)));
        p.dirty = true;
        REQUIRE(st.Save(p.key));
        CHECK_FALSE(p.dirty);
    }
    Store st2(dir);
    REQUIRE(st2.Has("r4c|none|none|1.0x"));
    Profile& q = st2.Get("r4c|none|none|1.0x");
    CHECK(q.sensAtCapture == doctest::Approx(12.f));
    CHECK(q.stance[1].samples.size() == 1);
    CHECK(q.stance[1].learned.Valid());
    CHECK(q.stance[0].samples.empty());
    std::filesystem::remove_all(dir);
}

TEST_CASE("compiled falls back across stances") {
    Compiled c;
    std::mt19937 rng(2);
    c.stance[0] = Train({Resample(MakeSpray(rng, 2100, 0.5, 0, 0))});
    CHECK(c.For(Stance::LeanLeft) == &c.stance[0]);
    c.stance[1] = Train({Resample(MakeSpray(rng, 2100, 0.5, 0, 0))});
    CHECK(c.For(Stance::LeanLeft) == &c.stance[1]);
}
